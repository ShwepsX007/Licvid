#!/usr/bin/env python3
"""
Зонд dYdX v4: приходят ли в публичном канале v4_trades ликвидации.

Зачем. У Hyperliquid документация и сторонние описания обещали объект
`liquidation` в публичных `trades`, а замер на бою дал 18 144 сделки и ноль
меток — источник пришлось удалить. Поэтому прежде чем добавлять dYdX,
проверяем его так же: не по доке, а по живому трафику.

Что говорит документация (docs.dydx.xyz):
  * WS:        wss://indexer.dydx.trade/v4/ws
  * подписка:  {"type": "subscribe", "channel": "v4_trades", "id": "BTC-USD"}
  * ответ:     {"type": "subscribed"|"data", "channel": "v4_trades",
                "id": ..., "contents": {"trades": [TradeUpdate, ...]}}
  * TradeUpdate: id, createdAt, side (BUY|SELL), price, size,
                 type — enum `Limit` | `Liquidated` | `Deleveraged`
  * ping раз в 30 с от сервера, на pong даётся 10 с; лимит подписок —
    2 в секунду на (соединение + канал + id)

Запуск на сервере (из песочницы биржа недоступна):
    python3 tools/dydx_liq_probe.py 600            # 10 минут, топ-тикеры
    python3 tools/dydx_liq_probe.py 1800 BTC-USD ETH-USD SOL-USD
    python3 tools/dydx_liq_probe.py --selftest     # разбор кадра без сети

Код выхода: 0 — метки ликвидаций есть, 3 — сделок много, а меток нет,
4 — не удалось подключиться/подписаться.
"""

import asyncio
import json
import sys
import time

import aiohttp

WS_URL = "wss://indexer.dydx.trade/v4/ws"
DEFAULT_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
                   "BNB-USD", "ADA-USD", "LINK-USD", "AVAX-USD", "SUI-USD"]
SUB_GAP = 0.55          # лимит — 2 подписки/с, держимся с запасом
PING_EVERY = 25.0       # сервер шлёт ping-кадр раз в 30 с, но и сами не молчим

# Значения поля type, которые считаем ликвидацией (docs.dydx.xyz/types/trade_type)
LIQ_TYPES = {"liquidated", "deleveraged"}


def count_frame(payload: dict, stats: dict) -> None:
    """Разбор одного кадра v4_trades: счётчики сделок и меток.

    Единственная функция, которая знает устройство сообщения, — зонд и будущий
    парсер терминала должны считать одинаково.
    """
    if not isinstance(payload, dict):
        return
    kind = str(payload.get("type") or "")
    if kind in ("subscribed", "connected", "unsubscribed"):
        stats["subs_acked"] = stats.get("subs_acked", 0) + (kind == "subscribed")
        return
    contents = payload.get("contents")
    if not isinstance(contents, dict):
        return
    trades = contents.get("trades")
    if not isinstance(trades, list):
        return
    ticker = str(payload.get("id") or "?")
    for t in trades:
        if not isinstance(t, dict):
            continue
        stats["trades"] = stats.get("trades", 0) + 1
        ttype = str(t.get("type") or "").lower()
        stats["types"][ttype or "(пусто)"] = stats["types"].get(ttype or "(пусто)", 0) + 1
        if ttype in LIQ_TYPES:
            stats["liq"] = stats.get("liq", 0) + 1
            stats["by_ticker"][ticker] = stats["by_ticker"].get(ticker, 0) + 1
            if len(stats["samples"]) < 12:
                stats["samples"].append(t)


def selftest() -> int:
    """Прогон count_frame на синтетических кадрах — без сети."""
    stats = {"trades": 0, "liq": 0, "types": {}, "by_ticker": {},
             "samples": [], "subs_acked": 0}
    count_frame({"type": "subscribed", "channel": "v4_trades", "id": "BTC-USD",
                 "contents": {}}, stats)
    count_frame({"type": "data", "channel": "v4_trades", "id": "BTC-USD",
                 "contents": {"trades": [
                     {"id": "1", "side": "BUY", "price": "60000", "size": "0.1",
                      "type": "LIMIT", "createdAt": "2026-09-13T10:00:00Z"},
                     {"id": "2", "side": "SELL", "price": "59900", "size": "2.5",
                      "type": "LIQUIDATED", "createdAt": "2026-09-13T10:00:01Z"},
                     {"id": "3", "side": "BUY", "price": "59800", "size": "1.0",
                      "type": "DELEVERAGED", "createdAt": "2026-09-13T10:00:02Z"},
                 ]}}, stats)
    count_frame({"type": "data", "id": "ETH-USD",
                 "contents": {"trades": [{"id": "4", "side": "SELL",
                                          "price": "3000", "size": "1",
                                          "type": "LIMIT"}]}}, stats)
    count_frame("мусор вместо словаря", stats)          # не должно упасть
    count_frame({"type": "data", "contents": {}}, stats)

    checks = [
        ("подписка учтена", stats["subs_acked"] == 1),
        ("сделок 4", stats["trades"] == 4),
        ("меток 2 (Liquidated + Deleveraged)", stats["liq"] == 2),
        ("типы посчитаны", stats["types"].get("limit") == 2
         and stats["types"].get("liquidated") == 1
         and stats["types"].get("deleveraged") == 1),
        ("привязка к тикеру", stats["by_ticker"].get("BTC-USD") == 2),
        ("образец сохранён", bool(stats["samples"])),
    ]
    bad = 0
    for name, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        bad += not ok
    print(f"итог: {len(checks) - bad} ок, {bad} ошибок")
    return 1 if bad else 0


async def probe(seconds: float, tickers: list) -> int:
    stats = {"trades": 0, "liq": 0, "types": {}, "by_ticker": {},
             "samples": [], "subs_acked": 0}
    print(f"тикеров: {len(tickers)} -> {', '.join(tickers)}")
    print(f"WS: {WS_URL}   длительность: {seconds:.0f}с\n")
    t_end = time.monotonic() + seconds
    frames = 0
    last_report = time.monotonic()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                    WS_URL, heartbeat=None, max_msg_size=0,
                    headers={"User-Agent": "LiqScope-probe/1.0"}) as ws:

                async def subscribe():
                    for tk in tickers:
                        await ws.send_json({"type": "subscribe",
                                            "channel": "v4_trades", "id": tk})
                        await asyncio.sleep(SUB_GAP)

                sub_task = asyncio.create_task(subscribe())
                while time.monotonic() < t_end:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=PING_EVERY)
                    except asyncio.TimeoutError:
                        continue          # серверный ping/pong живёт в aiohttp
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print(f"соединение закрыто: {msg.type}")
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    frames += 1
                    try:
                        count_frame(json.loads(msg.data), stats)
                    except Exception:
                        continue
                    if time.monotonic() - last_report >= 60:
                        last_report = time.monotonic()
                        left = max(0, t_end - time.monotonic())
                        print(f"  ... {left:.0f}с осталось: сделок {stats['trades']}, "
                              f"меток {stats['liq']}, подписок {stats['subs_acked']}"
                              f"/{len(tickers)}")
                sub_task.cancel()
    except Exception as e:                                   # noqa: BLE001
        print(f"не удалось подключиться: {type(e).__name__}: {e}")
        return 4

    print("\n=== ИТОГ за %.0fс" % seconds)
    print(f"  кадров: {frames}, сделок: {stats['trades']}, "
          f"с типом Liquidated/Deleveraged: {stats['liq']}")
    print(f"  подписок подтверждено: {stats['subs_acked']}/{len(tickers)}")
    if stats["types"]:
        tops = sorted(stats["types"].items(), key=lambda kv: -kv[1])
        print("  значения поля type: " + ", ".join(f"{k} {v}" for k, v in tops))
    if stats["by_ticker"]:
        tops = sorted(stats["by_ticker"].items(), key=lambda kv: -kv[1])[:10]
        print("  ликвидации по тикерам: " + ", ".join(f"{k} {v}" for k, v in tops))
    for s in stats["samples"][:5]:
        print("  образец: " + json.dumps(s, ensure_ascii=False)[:200])

    if stats["liq"] > 0:
        print("\nВЫВОД: публичный канал v4_trades ОТДАЁТ метку ликвидации — "
              "dYdX можно подключать.")
        return 0
    if stats["trades"] >= 200:
        print("\nВЫВОД: сделок много, а меток нет — как было с Hyperliquid. "
              "Подключать рано, повторите замер в волатильный час.")
        return 3
    print("\nВЫВОД: данных слишком мало, чтобы судить — повторите с большим "
          "временем или в волатильный час.")
    return 3


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if "--selftest" in args:
        return selftest()
    seconds = 600.0
    tickers = []
    for a in args:
        if a.replace(".", "", 1).isdigit():
            seconds = float(a)
        else:
            tickers.append(a.upper())
    tickers = tickers or DEFAULT_TICKERS
    try:
        return asyncio.run(probe(seconds, tickers))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
