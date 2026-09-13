#!/usr/bin/env python3
"""
Зонд источников ликвидаций: меряем канал живьём, прежде чем писать слушатель.

Зачем. Hyperliquid был удалён из проекта именно потому, что документация и
сторонние описания обещали метку ликвидации в публичных `trades`, а замер на
бою дал 18 144 сделки и ноль меток. Поэтому каждый кандидат сначала меряется.

Источники (все публичные, без ключей):

  kraken    wss://futures.kraken.com/ws/v1
            подписка {"event":"subscribe","feed":"trade","product_ids":["PF_XBTUSD"]}
            у сделки поле type: fill | liquidation | termination | block
            (docs.kraken.com/exchange/api-reference/futures-websocket/trade)

  bitfinex  wss://api-pub.bitfinex.com/ws/2
            подписка {"event":"subscribe","channel":"status","key":"liq:global"}
            кадр [chanId, [[ "pos", posId, timeMs, null, symbol, amount,
                             basePrice, null, isMatch, isMarketSold, null,
                             liqPrice ]]]
            (docs.bitfinex.com/reference/ws-public-status)

  dydx      wss://indexer.dydx.trade/v4/ws
            подписка {"type":"subscribe","channel":"v4_trades","id":"BTC-USD"}
            у сделки поле type: Limit | Liquidated | Deleveraged
            (docs.dydx.xyz/types/trade_type)

Запуск на сервере (из песочницы биржи недоступны):
    python3 tools/liq_probe.py kraken 600
    python3 tools/liq_probe.py bitfinex 1800
    python3 tools/liq_probe.py dydx 600 BTC-USD ETH-USD SOL-USD
    python3 tools/liq_probe.py --selftest        # разбор кадров всех трёх, без сети

Код выхода: 0 — метки ликвидаций есть, 3 — данных мало или меток нет,
4 — не удалось подключиться.
"""

import asyncio
import json
import sys
import time

import aiohttp

KRAKEN_WS = "wss://futures.kraken.com/ws/v1"
BITFINEX_WS = "wss://api-pub.bitfinex.com/ws/2"
DYDX_WS = "wss://indexer.dydx.trade/v4/ws"

DEFAULTS = {
    "kraken": ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "PF_XRPUSD", "PF_DOGEUSD",
               "PF_BCHUSD", "PF_LTCUSD", "PF_LINKUSD", "PF_ADAUSD", "PF_AVAXUSD"],
    "bitfinex": ["liq:global"],
    "dydx": ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
             "BNB-USD", "ADA-USD", "LINK-USD", "AVAX-USD", "SUI-USD"],
}

UA = "LiqScope-probe/1.0"


# ---------------------------------------------------------------------------
#  Разбор кадров: по функции на источник. Зонд и будущий слушатель должны
#  считать одинаково, поэтому логика живёт здесь, а не в цикле чтения.
# ---------------------------------------------------------------------------

def count_kraken(payload: dict, stats: dict) -> None:
    """Kraken Futures: feed `trade` / `trade_snapshot`, type=liquidation."""
    if not isinstance(payload, dict):
        return
    feed = str(payload.get("feed") or "")
    if payload.get("event") in ("subscribed", "subscribed_failed"):
        stats["subs_acked"] += payload.get("event") == "subscribed"
        if payload.get("event") == "subscribed_failed":
            stats["subs_failed"] += 1
        return
    if feed == "trade_snapshot":
        rows = payload.get("trades") or []
    elif feed == "trade":
        rows = [payload]
    else:
        return
    product = str(payload.get("product_id") or "?")
    for r in rows:
        if not isinstance(r, dict):
            continue
        stats["trades"] += 1
        ttype = str(r.get("type") or "(пусто)")
        stats["types"][ttype] = stats["types"].get(ttype, 0) + 1
        if ttype in ("liquidation", "termination"):
            stats["liq"] += 1
            stats["by_ticker"][product] = stats["by_ticker"].get(product, 0) + 1
            if len(stats["samples"]) < 12:
                stats["samples"].append(r)


def count_bitfinex(payload, stats: dict, liq_chans: set) -> None:
    """Bitfinex: канал status с ключом liq:global, кадр — массив позиций."""
    if isinstance(payload, dict):
        if payload.get("event") == "subscribed" and \
                str(payload.get("key") or "") == "liq:global":
            stats["subs_acked"] += 1
            liq_chans.add(payload.get("chanId"))
        return
    if not isinstance(payload, list) or len(payload) < 2:
        return
    if payload[0] not in liq_chans:
        return
    body = payload[1]
    if not isinstance(body, list):
        return                      # "hb" и прочие служебные кадры
    rows = body if body and isinstance(body[0], list) else [body]
    for r in rows:
        if not isinstance(r, list) or not r or r[0] != "pos":
            continue
        stats["trades"] += 1
        stats["liq"] += 1
        symbol = str(r[4]) if len(r) > 4 else "?"
        stats["by_ticker"][symbol] = stats["by_ticker"].get(symbol, 0) + 1
        stats["types"]["pos"] = stats["types"].get("pos", 0) + 1
        if len(stats["samples"]) < 12:
            stats["samples"].append(r)


def count_dydx(payload: dict, stats: dict) -> None:
    """dYdX v4: канал v4_trades, type=Liquidated/Deleveraged."""
    if not isinstance(payload, dict):
        return
    kind = str(payload.get("type") or "")
    if kind in ("subscribed", "connected", "unsubscribed"):
        stats["subs_acked"] += kind == "subscribed"
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
        stats["trades"] += 1
        ttype = str(t.get("type") or "(пусто)").lower()
        stats["types"][ttype] = stats["types"].get(ttype, 0) + 1
        if ttype in ("liquidated", "deleveraged"):
            stats["liq"] += 1
            stats["by_ticker"][ticker] = stats["by_ticker"].get(ticker, 0) + 1
            if len(stats["samples"]) < 12:
                stats["samples"].append(t)


def new_stats() -> dict:
    return {"trades": 0, "liq": 0, "types": {}, "by_ticker": {},
            "samples": [], "subs_acked": 0, "subs_failed": 0}


# ---------------------------------------------------------------------------
#  Подписки
# ---------------------------------------------------------------------------

async def subscribe(ws, source: str, targets: list) -> None:
    for t in targets:
        if source == "kraken":
            # по одному продукту: на незнакомый id Kraken отвечает ошибкой
            # на весь запрос, а нам нужно увидеть, кто именно не подписан
            await ws.send_json({"event": "subscribe", "feed": "trade",
                                "product_ids": [t]})
        elif source == "bitfinex":
            await ws.send_json({"event": "subscribe", "channel": "status",
                                "key": t})
        else:
            await ws.send_json({"type": "subscribe", "channel": "v4_trades",
                                "id": t})
        await asyncio.sleep(0.6)


# ---------------------------------------------------------------------------
#  Прогон
# ---------------------------------------------------------------------------

async def probe(source: str, seconds: float, targets: list) -> int:
    url = {"kraken": KRAKEN_WS, "bitfinex": BITFINEX_WS, "dydx": DYDX_WS}[source]
    stats = new_stats()
    liq_chans: set = set()
    print(f"источник: {source}   подписок: {len(targets)} -> {', '.join(targets)}")
    print(f"WS: {url}   длительность: {seconds:.0f}с\n")
    t_end = time.monotonic() + seconds
    frames = 0
    last_report = time.monotonic()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                    url, heartbeat=None, max_msg_size=0,
                    headers={"User-Agent": UA}) as ws:
                sub = asyncio.create_task(subscribe(ws, source, targets))
                while time.monotonic() < t_end:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Kraken просит пинг хотя бы раз в 60 с — шлём сами
                        try:
                            await ws.ping()
                        except Exception:                              # noqa: BLE001
                            pass
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSED,
                                    aiohttp.WSMsgType.ERROR,
                                    aiohttp.WSMsgType.CLOSE):
                        print(f"соединение закрыто: {msg.type}")
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    frames += 1
                    try:
                        payload = json.loads(msg.data)
                    except Exception:                                  # noqa: BLE001
                        continue
                    if source == "kraken":
                        count_kraken(payload, stats)
                    elif source == "bitfinex":
                        count_bitfinex(payload, stats, liq_chans)
                    else:
                        count_dydx(payload, stats)
                    if time.monotonic() - last_report >= 60:
                        last_report = time.monotonic()
                        print(f"  ... {max(0, t_end - time.monotonic()):.0f}с осталось: "
                              f"сделок {stats['trades']}, ликвидаций {stats['liq']}, "
                              f"подписок {stats['subs_acked']}/{len(targets)}")
                sub.cancel()
    except Exception as e:                                             # noqa: BLE001
        print(f"не удалось подключиться: {type(e).__name__}: {e}")
        return 4

    print(f"\n=== ИТОГ {source} за {seconds:.0f}с")
    print(f"  кадров: {frames}, сделок/записей: {stats['trades']}, "
          f"ликвидаций: {stats['liq']}")
    print(f"  подписок подтверждено: {stats['subs_acked']}/{len(targets)}"
          + (f", отказов: {stats['subs_failed']}" if stats["subs_failed"] else ""))
    if stats["types"]:
        tops = sorted(stats["types"].items(), key=lambda kv: -kv[1])
        print("  значения типа: " + ", ".join(f"{k} {v}" for k, v in tops))
    if stats["by_ticker"]:
        tops = sorted(stats["by_ticker"].items(), key=lambda kv: -kv[1])[:10]
        print("  по инструментам: " + ", ".join(f"{k} {v}" for k, v in tops))
    for s in stats["samples"][:5]:
        print("  образец: " + json.dumps(s, ensure_ascii=False)[:200])

    if stats["liq"] > 0:
        print("\nВЫВОД: канал ОТДАЁТ ликвидации — можно подключать.")
        return 0
    if stats["trades"] >= 200:
        print("\nВЫВОД: сделок много, а ликвидаций нет — как было с "
              "Hyperliquid. Повторите замер в волатильный час.")
        return 3
    print("\nВЫВОД: данных слишком мало, чтобы судить — повторите с большим "
          "временем или в волатильный час.")
    return 3


# ---------------------------------------------------------------------------
#  Самопроверка разбора кадров (сеть не нужна)
# ---------------------------------------------------------------------------

def selftest() -> int:
    checks = []

    # --- Kraken ------------------------------------------------------------
    st = new_stats()
    count_kraken({"event": "subscribed", "feed": "trade",
                  "product_ids": ["PF_XBTUSD"]}, st)
    count_kraken({"event": "subscribed_failed", "feed": "trade",
                  "product_ids": ["PF_NOPE"]}, st)
    count_kraken({"feed": "trade_snapshot", "product_id": "PF_XBTUSD",
                  "trades": [
                      {"uid": "a", "side": "sell", "type": "fill", "seq": 1,
                       "time": 1612269657781, "qty": 440, "price": 34893},
                      {"uid": "b", "side": "sell", "type": "liquidation",
                       "seq": 2, "time": 1612269658000, "qty": 100,
                       "price": 34800},
                  ]}, st)
    count_kraken({"feed": "trade", "product_id": "PF_ETHUSD", "uid": "c",
                  "side": "buy", "type": "termination", "seq": 3,
                  "time": 1612269659000, "qty": 10, "price": 3000}, st)
    count_kraken("мусор", st)
    checks += [
        ("kraken: подписка и отказ учтены",
         st["subs_acked"] == 1 and st["subs_failed"] == 1),
        ("kraken: сделок 3", st["trades"] == 3),
        ("kraken: ликвидаций 2 (liquidation + termination)", st["liq"] == 2),
        ("kraken: типы посчитаны",
         st["types"].get("fill") == 1 and st["types"].get("liquidation") == 1
         and st["types"].get("termination") == 1),
        ("kraken: инструменты", st["by_ticker"].get("PF_XBTUSD") == 1
         and st["by_ticker"].get("PF_ETHUSD") == 1),
    ]

    # --- Bitfinex ----------------------------------------------------------
    st = new_stats()
    chans: set = set()
    count_bitfinex({"event": "info", "version": 2}, st, chans)
    count_bitfinex({"event": "subscribed", "channel": "status",
                    "chanId": 91684, "key": "liq:global"}, st, chans)
    count_bitfinex({"event": "subscribed", "channel": "status",
                    "chanId": 335856, "key": "deriv:tBTCF0:USTF0"}, st, chans)
    count_bitfinex([335856, [1596124822000, None, 0.896]], st, chans)   # не наш канал
    count_bitfinex([91684, "hb"], st, chans)                            # heartbeat
    count_bitfinex([91684, [["pos", 142397657, 1574697680828, None,
                             "tBSVUSD", -2.62932, 91.583875238719, None,
                             1, 1, None, 112.27]]], st, chans)
    count_bitfinex([91684, []], st, chans)                              # пустой снимок
    checks += [
        ("bitfinex: chanId ликвидационного канала запомнен", 91684 in chans),
        ("bitfinex: чужой канал и hb не считаются", st["trades"] == 1),
        ("bitfinex: ликвидация учтена", st["liq"] == 1),
        ("bitfinex: символ вытащен из позиции 4",
         st["by_ticker"].get("tBSVUSD") == 1),
        ("bitfinex: образец сохранён", bool(st["samples"])
         and st["samples"][0][11] == 112.27),
    ]

    # --- dYdX --------------------------------------------------------------
    st = new_stats()
    count_dydx({"type": "subscribed", "channel": "v4_trades",
                "id": "BTC-USD", "contents": {}}, st)
    count_dydx({"type": "data", "channel": "v4_trades", "id": "BTC-USD",
                "contents": {"trades": [
                    {"id": "1", "side": "BUY", "price": "60000", "size": "0.1",
                     "type": "LIMIT"},
                    {"id": "2", "side": "SELL", "price": "59900", "size": "2.5",
                     "type": "LIQUIDATED"},
                    {"id": "3", "side": "BUY", "price": "59800", "size": "1.0",
                     "type": "DELEVERAGED"},
                ]}}, st)
    count_dydx("мусор", st)
    checks += [
        ("dydx: сделок 3", st["trades"] == 3),
        ("dydx: ликвидаций 2", st["liq"] == 2),
        ("dydx: типы посчитаны", st["types"].get("limit") == 1
         and st["types"].get("liquidated") == 1
         and st["types"].get("deleveraged") == 1),
    ]

    bad = 0
    for name, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        bad += not ok
    print(f"итог: {len(checks) - bad} ок, {bad} ошибок")
    return 1 if bad else 0


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:            # проверка раньше справки: иначе
        return selftest()               # `and`/`or` съедают этот случай
    if not args:
        print(__doc__)
        return 2
    source = args[0].lower()
    if source not in DEFAULTS:
        print(f"неизвестный источник {source!r}; доступны: "
              f"{', '.join(DEFAULTS)}")
        return 2
    seconds = 600.0
    targets = []
    for a in args[1:]:
        if a.replace(".", "", 1).isdigit():
            seconds = float(a)
        else:
            targets.append(a)
    targets = targets or DEFAULTS[source]
    try:
        return asyncio.run(probe(source, seconds, targets))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
