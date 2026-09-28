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
его придушит. На время прогона выключайте лимит **в окружении сервера**
(не в команде запуска этого скрипта — он-то и есть клиент)::

    sudo systemctl edit licvid        # [Service] + Environment=LIQSCOPE_RATE_LIMIT=0
    sudo systemctl daemon-reload && sudo systemctl restart licvid
    # …прогон… затем убрать строку и снова restart

GET /api/stats под лимит не попадает, душить будет только WS-коннекты.

Ещё две вещи, которые легко принять за деградацию:

* ``--url http://127.0.0.1:8000`` бьёт **мимо nginx**, то есть мимо микрокэша
  (``/api/stats`` кэшируется на 5 с, и в бою в приложение уходит ~0.2 rps
  вместо 100). Это замер худшего случая; картину боя даёт
  ``--url https://ваш-домен``.
* Прогон сразу после ``systemctl restart licvid`` меряет прогрев, а не
  установившийся режим: ~20 с единственный воркер занят каталогом бирж,
  подписками и восстановлением истории, поэтому p95 будет секундами.
  Подождите 2-3 минуты (``curl -sf localhost:8000/api/health`` + пауза).

RSS в ``/api/metrics`` — текущий (``VmRSS``), а пик с момента старта лежит
в ``rss_peak_bytes``: порог 500 МБ проверяется по текущему.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from typing import Optional

try:
    import aiohttp
except ImportError:
    sys.exit("нужен aiohttp: pip install aiohttp (есть в requirements.txt)")

TARGET_P95_MS = 200.0
SLOW_MS = 500.0          # что считаем «залипшим» запросом для разбора пауз воркера
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
                     stop_at: float, lat: list, errors: list,
                     slow: Optional[list] = None, t_start: float = 0.0) -> None:
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
                ms = (time.perf_counter() - t0) * 1000.0
                lat.append(ms)
                if r.status != 200:
                    errors.append(r.status)
                # Медленные запросы — с отметкой времени от старта прогона:
                # если они приходят пачками раз в N секунд, воркер чем-то
                # занят периодически (рассылка кадра, прогрев свечей, диск),
                # а не «просто медленный».
                if slow is not None and ms >= SLOW_MS:
                    slow.append((round(t0 - t_start, 1), round(ms, 1)))
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
    if "127.0.0.1" in url or "localhost" in url:
        print("  (замер мимо nginx: микрокэш API_CACHE не участвует — это "
              "худший случай; картина боя — --url https://ваш-домен)")
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
    slow: list = []          # (секунда от старта, латентность мс) — залипшие запросы

    ws_tasks = [asyncio.ensure_future(ws_client(i, url, stop_at, stats))
                for i in range(args.ws)]
    # коннектам даём 10 с форы, затем — HTTP-нагрузка поверх живых сокетов
    await asyncio.sleep(min(10, args.seconds / 6))
    t_start = time.perf_counter()
    async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=0)) as session:
        await http_flood(session, url, args.rps, stop_at, lat, errors,
                         slow=slow, t_start=t_start)
    # --ws 0 (замер чистого HTTP-пути через nginx) — задач нет, wait() с пустым
    # набором бросал ValueError: Set of coroutines/Futures is empty
    if ws_tasks:
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
    peak1 = (after.get("rss_peak_bytes") or 0) / 1024 / 1024
    print(f"RSS: {rss0:.1f} -> {rss1:.1f} МБ (текущий; пик с момента старта "
          f"{peak1:.1f} МБ)")
    print(f"сервер: ws {after.get('ws_clients_total', '?')} клиентов, "
          f"{after.get('ws_messages_per_sec', '?')} сообщ/с, "
          f"{after.get('http_requests_per_sec', '?')} запр/с, "
          f"p95 {after.get('http_latency_p95_ms', '?')} мс")

    lag = after.get("loop_lag_max_ms")
    if lag is not None:
        print(f"паузы воркера: максимум {float(lag):.0f} мс, последняя "
              f"{float(after.get('loop_lag_last_ms') or 0):.0f} мс, всего "
              f"{int(after.get('loop_stalls') or 0)} пауз выше порога")
        srv_p95 = after.get("http_latency_p95_ms")
        if p95 >= 200 and srv_p95 is not None and p95 > 5 * float(srv_p95):
            print(f"  -> разрыв клиент/сервер: обработчик p95 {float(srv_p95):.1f} мс, "
                  f"а снаружи {p95:.0f} мс. Запросы СТОЯЛИ В ОЧЕРЕДИ event loop, "
                  "пока единственный воркер был занят (рассылка кадра, разбор "
                  "пачки свечей, диск). Моменты пауз — в journalctl рядом с [loop].")
        tr = after.get("loop_trace_last")
        if tr:
            print(f"  -> чем был занят воркер (LIQSCOPE_LOOP_TRACE=1): {tr}")
            print(f"     стек держался {float(after.get('loop_trace_held_ms') or 0):.0f} мс, "
                  f"таких пауз {int(after.get('loop_trace_stalls') or 0)}")
        elif after.get("loop_stalls"):
            print("  -> кто именно держал воркер, не записано: включите на сервере "
                  "LIQSCOPE_LOOP_TRACE=1 (стек) или LIQSCOPE_ASYNCIO_DEBUG=1 "
                  "(имя задачи) в drop-in и повторите прогон")
    if slow:
        print(f"\nзалипшие запросы (> {SLOW_MS:.0f} мс): {len(slow)} шт. "
              f"из {len(lat)}")
        for off, ms in slow[:12]:
            print(f"  +{off:>6.1f} с   {ms:>8.0f} мс")
        if len(slow) > 12:
            print(f"  … и ещё {len(slow) - 12}")
        offs = [o for o, _ in slow]
        gaps = sorted(round(b - a, 1) for a, b in zip(offs, offs[1:]))
        if len(gaps) >= 2:
            print(f"  интервалы между залипаниями: медиана "
                  f"{gaps[len(gaps) // 2]:.1f} с, мин {gaps[0]:.1f} с — ровная "
                  "периодичность указывает на фоновую задачу (15 с — прогрев "
                  "свечей kline_refresher), рваная — на очередь под нагрузкой")

    fails = []
    if stats["denied"]:
        fails.append(f"отклонено WS: {stats['denied']} — лимит включён НА СЕРВЕРЕ: "
                     "sudo systemctl edit licvid → Environment=LIQSCOPE_RATE_LIMIT=0, "
                     "daemon-reload + restart, затем повторить (после прогона убрать)")
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
