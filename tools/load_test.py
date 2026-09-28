#!/usr/bin/env python3
"""Стресс-тест LiqScope: сокеты + HTTP-нагрузка.

    python3 tools/load_test.py [--url http://127.0.0.1:8000] [--ws 50]
                               [--rps 100] [--seconds 60]

Что делает:
  * держит N WebSocket-клиентов (пинг раз в 5 с), считает обрывы;
  * долбит /api/stats заданным RPS, меряет p50/p95/max и не-200;
  * снимает /api/metrics до и после (RSS, клиенты, скорость сообщений).

Цели: p95 < 200 мс, 0 обрывов WS, RSS < 500 МБ.

ВАЖНО: тест с одного IP похож на флуд, поэтому собственное ограничение
сервера (RateLimitMiddleware: 5 WS/минуту, 60 мутирующих POST/10 минут)
его придушит. На время прогона выключайте лимит на стенде::

    LIQSCOPE_RATE_LIMIT=0  # в окружении тестируемого сервера

GET /api/stats под лимит не попадает, душить будет только WS-коннекты.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

try:
    import aiohttp
except ImportError:
    sys.exit("нужен aiohttp: pip install aiohttp (есть в requirements.txt)")

TARGET_P95_MS = 200.0
TARGET_DROPS = 0
TARGET_RSS_MB = 500.0


async def fetch_metrics(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        async with session.get(url + "/api/metrics",
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                return await r.json()
    except Exception as e:  # noqa: BLE001 — стенд может быть без /api/metrics
        print(f"  ! /api/metrics недоступен: {e}")
    return {}


async def ws_client(idx: int, url: str, stop_at: float, stats: dict) -> None:
    """Один клиент: висит на сокете, раз в 5 с шлёт пинг."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                    url + "/ws",
                    timeout=aiohttp.ClientTimeout(total=30)) as ws:
                stats["connected"] += 1
                await ws.receive(timeout=30)   # init
                stats["init_ok"] += 1
                next_ping = time.monotonic() + 5.0
                while time.monotonic() < stop_at:
                    timeout = max(0.1, min(5.0, next_ping - time.monotonic()))
                    try:
                        msg = await ws.receive(timeout=timeout)
                        if msg.type in (aiohttp.WSMsgType.CLOSED,
                                        aiohttp.WSMsgType.ERROR,
                                        aiohttp.WSMsgType.CLOSE):
                            stats["drops"] += 1
                            stats.setdefault("drop_samples", []).append(
                                f"#{idx}: {msg.type}")
                            return
                        stats["frames"] += 1
                    except asyncio.TimeoutError:
                        pass
                    if time.monotonic() >= next_ping:
                        try:
                            await ws.send_json({"action": "ping"})
                        except Exception:  # noqa: BLE001
                            stats["drops"] += 1
                            return
                        next_ping = time.monotonic() + 5.0
                await ws.close()
                stats["clean"] += 1
    except Exception as e:  # noqa: BLE001 — считаем, а не падаем
        stats["denied"] += 1
        samples = stats.setdefault("deny_samples", [])
        if len(samples) < 5:
            samples.append(f"#{idx}: {type(e).__name__}: {e}")


async def http_flood(session: aiohttp.ClientSession, url: str, rps: int,
                     stop_at: float, lat: list, errors: list) -> None:
    """Планировщик: держит заданный RPS своими задачами."""
    interval = 1.0 / max(1, rps)
    pending: set = set()

    async def one():
        t0 = time.perf_counter()
        try:
            async with session.get(
                    url + "/api/stats",
                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                await r.read()
                lat.append((time.perf_counter() - t0) * 1000.0)
                if r.status != 200:
                    errors.append(r.status)
        except Exception as e:  # noqa: BLE001
            errors.append(type(e).__name__)

    while time.monotonic() < stop_at:
        tick = time.perf_counter()
        task = asyncio.ensure_future(one())
        pending.add(task)
        task.add_done_callback(pending.discard)
        delay = interval - (time.perf_counter() - tick)
        if delay > 0:
            await asyncio.sleep(delay)
    if pending:
        await asyncio.wait(pending, timeout=30)


def pct(data: list, q: float) -> float:
    if not data:
        return 0.0
    ordered = sorted(data)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def main() -> int:
    ap = argparse.ArgumentParser(description="Стресс-тест LiqScope")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--ws", type=int, default=50)
    ap.add_argument("--rps", type=int, default=100)
    ap.add_argument("--seconds", type=int, default=60)
    args = ap.parse_args()
    url = args.url.rstrip("/")

    print(f"стенд: {url} | WS: {args.ws} | HTTP: {args.rps} rps x "
          f"{args.seconds}с к /api/stats")
    async with aiohttp.ClientSession() as session:
        before = await fetch_metrics(session, url)
    rss0 = (before.get("rss_bytes") or 0) / 1024 / 1024
    print(f"RSS до: {rss0:.1f} МБ, WS-клиентов до: "
          f"{before.get('ws_clients_total', '?')}")

    stop_at = time.monotonic() + args.seconds
    stats = {"connected": 0, "init_ok": 0, "frames": 0, "drops": 0,
             "denied": 0, "clean": 0}
    lat: list = []
    errors: list = []

    ws_tasks = [asyncio.ensure_future(ws_client(i, url, stop_at, stats))
                for i in range(args.ws)]
    # коннектам даём 10 с форы, затем — HTTP-нагрузка поверх живых сокетов
    await asyncio.sleep(min(10, args.seconds / 6))
    async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=0)) as session:
        await http_flood(session, url, args.rps, stop_at, lat, errors)
    await asyncio.wait(ws_tasks, timeout=30)

    async with aiohttp.ClientSession() as session:
        after = await fetch_metrics(session, url)
    rss1 = (after.get("rss_bytes") or 0) / 1024 / 1024

    p50 = pct(lat, 0.50)
    p95 = pct(lat, 0.95)
    mx = max(lat) if lat else 0.0
    print("\n--- итог ---")
    print(f"WS: подключилось {stats['connected']}/{args.ws}, "
          f"init принято {stats['init_ok']}, кадров {stats['frames']}, "
          f"обрывов {stats['drops']}, отклонено {stats['denied']}, "
          f"чисто закрылось {stats['clean']}")
    for s in stats.get("deny_samples", [])[:5]:
        print(f"  отказ: {s}")
    for s in stats.get("drop_samples", [])[:5]:
        print(f"  обрыв: {s}")
    print(f"HTTP: запросов {len(lat)}, ошибок {len(errors)}, "
          f"p50 {p50:.1f} мс, p95 {p95:.1f} мс, max {mx:.1f} мс")
    if errors[:8]:
        print(f"  примеры ошибок: {errors[:8]}")
    print(f"RSS: {rss0:.1f} -> {rss1:.1f} МБ "
          f"(метрика сервера: {after.get('rss_bytes', '?')} байт)")
    print(f"сервер: ws {after.get('ws_clients_total', '?')} клиентов, "
          f"{after.get('ws_messages_per_sec', '?')} сообщ/с, "
          f"{after.get('http_requests_per_sec', '?')} запр/с, "
          f"p95 {after.get('http_latency_p95_ms', '?')} мс")

    fails = []
    if stats["denied"]:
        fails.append(f"отклонено WS: {stats['denied']} "
                     "(выключите LIQSCOPE_RATE_LIMIT=0 на стенде и повторите)")
    if stats["drops"] > TARGET_DROPS:
        fails.append(f"обрывы WS: {stats['drops']}")
    if p95 >= TARGET_P95_MS:
        fails.append(f"p95 {p95:.1f} мс >= {TARGET_P95_MS:.0f} мс")
    if rss1 >= TARGET_RSS_MB:
        fails.append(f"RSS {rss1:.1f} МБ >= {TARGET_RSS_MB:.0f} МБ")
    if fails:
        print("\nРЕЗУЛЬТАТ: НЕ ПРОШЁЛ")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nРЕЗУЛЬТАТ: ПРОШЁЛ "
          f"(p95 {p95:.1f} мс < {TARGET_P95_MS:.0f}, "
          f"обрывов 0, RSS {rss1:.1f} МБ < {TARGET_RSS_MB:.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
