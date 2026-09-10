"""
Интеграционный тест потикового потока на локальном «псевдо-Binance»/«псевдо-Bybit».

Проверяет три вещи:
  1. combined-стрим (?streams=..@aggTrade) отдаёт сделки и переподключается
     при смене монеты;
  2. подключение без открытых графиков не «залипает» — подписка появляется,
     как только клиент открыл график;
  3. если сокет открыт, но биржа молчит (реальный случай на проде:
     connected=true, ticks_seen=0), движок сам уходит на следующий источник.

Сеть наружу не нужна.  Запуск:  python3 tests/test_tick_flow.py
"""

import asyncio
import json
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import market_feed
from market_feed import MarketFeed

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"

received = []          # сделки, дошедшие до колбэка
subscribe_log = []     # что клиент запросил у «биржи»
combined_urls = []     # с какими стримами открывали combined-соединение
bybit_subs = []

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


async def _pump(ws, streams, wrap):
    """Шлём сделки по подписанным стримам каждые 20 мс."""
    price = 100.0
    while not ws.closed:
        await asyncio.sleep(0.02)
        for st in list(streams):
            sym = st.split("@")[0].upper()
            price = round(price + 0.1, 4)
            event = {"e": "aggTrade", "E": 1, "s": sym,
                     "p": str(price), "q": "0.5", "T": 1700000000000}
            payload = {"stream": st, "data": event} if wrap else event
            try:
                await ws.send_json(payload)
            except Exception:
                return


async def fake_binance_combined(request):
    """wss://.../stream?streams=btcusdt@aggTrade/ethusdt@aggTrade"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    q = parse_qs(urlparse(str(request.url)).query)
    streams = set((q.get("streams") or [""])[0].split("/")) - {""}
    combined_urls.append(sorted(streams))
    task = asyncio.create_task(_pump(ws, streams, wrap=True))
    try:
        async for _ in ws:
            pass
    finally:
        task.cancel()
    return ws


async def fake_binance_raw(request):
    """wss://.../ws + SUBSCRIBE/UNSUBSCRIBE"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    streams = set()
    task = asyncio.create_task(_pump(ws, streams, wrap=False))
    try:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            params = tuple(data.get("params") or [])
            subscribe_log.append((data.get("method"), params))
            if data.get("method") == "SUBSCRIBE":
                streams.update(params)
            elif data.get("method") == "UNSUBSCRIBE":
                streams.difference_update(params)
            await ws.send_json({"result": None, "id": data.get("id")})
    finally:
        task.cancel()
    return ws


async def fake_silent(request):
    """Сокет открывается, подписку принимает — и молчит (как Binance на проде)."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if data.get("id"):
                await ws.send_json({"result": None, "id": data["id"]})
    return ws


async def fake_bybit(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    topics = set()

    async def pump():
        price = 50.0
        while not ws.closed:
            await asyncio.sleep(0.02)
            for t in list(topics):
                sym = t.split(".")[1]
                price = round(price + 0.05, 4)
                await ws.send_json({"topic": t, "data": [
                    {"s": sym, "p": str(price), "v": "1.5",
                     "S": "Buy", "T": 1700000000000}]})

    task = asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            args = data.get("args") or []
            bybit_subs.append((data.get("op"), tuple(args)))
            if data.get("op") == "subscribe":
                topics.update(args)
            else:
                topics.difference_update(args)
            await ws.send_json({"op": data.get("op"), "success": True})
    finally:
        task.cancel()
    return ws


async def on_trade(symbol, price, qty, ts, side=""):
    received.append((symbol, price, qty, ts))


async def noop_liq(ev):
    pass


async def noop_price(sym, price, candle):
    pass


def make_feed():
    feed = MarketFeed(on_liquidation=noop_liq, on_price=noop_price,
                      on_trade=on_trade, exchanges=[])
    feed._session = aiohttp.ClientSession()
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    return feed


async def scenario_combined():
    print("1) combined-стрим: сделки и переподписка при смене монеты")
    market_feed.BINANCE_WS = f"{BASE}/stream?streams="
    feed = make_feed()
    feed.set_hot_symbols(["BTC_USDT"])

    task = asyncio.create_task(feed._trade_engine())
    await asyncio.sleep(0.7)

    btc = [r for r in received if r[0] == "BTC_USDT"]
    check("сделки по BTC доходят", len(btc) > 5, f"получено {len(btc)}")
    check("цена распарсена", bool(btc) and btc[0][1] > 0)
    check("объём распарсен", bool(btc) and btc[0][2] == 0.5)
    check("combined-подписка на btcusdt", ["btcusdt@aggTrade"] in combined_urls, combined_urls)
    check("цена попала в кэш фида", feed.prices.get("BTC_USDT", 0) > 0)
    check("статус ticks connected", feed.status["ticks"].connected)

    received.clear()
    feed.set_hot_symbols(["ETH_USDT"])
    await asyncio.sleep(1.2)
    check("переподписался на ETH", ["ethusdt@aggTrade"] in combined_urls, combined_urls)
    check("сделки по ETH идут",
          len([r for r in received if r[0] == "ETH_USDT"]) > 5)

    received.clear()
    await asyncio.sleep(0.4)
    check("трафик по BTC прекратился",
          not any(r[0] == "BTC_USDT" for r in received))

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await feed._session.close()


async def scenario_late_client():
    print("2) старт без открытых графиков — подписка появляется потом")
    market_feed.BINANCE_WS = f"{BASE}/stream?streams="
    received.clear()
    combined_urls.clear()
    feed = make_feed()

    task = asyncio.create_task(feed._trade_engine())
    await asyncio.sleep(0.4)
    check("без графиков соединение не держим", not combined_urls, combined_urls)
    check("тиков нет", not received)

    feed.set_hot_symbols(["BTC_USDT"])
    await asyncio.sleep(1.0)
    check("после открытия графика тики пошли",
          len([r for r in received if r[0] == "BTC_USDT"]) > 5,
          f"получено {len(received)}")

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await feed._session.close()


async def scenario_silent_source():
    print("3) биржа молчит (connected=true, ticks=0) — уходим на резерв")
    market_feed.BINANCE_WS = f"{BASE}/silent?streams="
    market_feed.BINANCE_WS_RAW = f"{BASE}/silent"
    market_feed.BYBIT_WS = f"{BASE}/bybit"
    received.clear()
    bybit_subs.clear()
    feed = make_feed()
    feed.NO_DATA_TIMEOUT = 1.0            # ускоряем для теста
    feed.set_hot_symbols(["BTC_USDT"])

    task = asyncio.create_task(feed._trade_engine())
    await asyncio.sleep(5.0)

    check("движок дошёл до Bybit", any(op == "subscribe" for op, _ in bybit_subs), bybit_subs)
    check("тики пошли с резервного источника",
          len([r for r in received if r[0] == "BTC_USDT"]) > 5,
          f"получено {len(received)}")
    check("источник в статусе — Bybit",
          "bybit" in feed.status["ticks"].name, feed.status["ticks"].name)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await feed._session.close()


async def main():
    app = web.Application()
    app.router.add_get("/stream", fake_binance_combined)
    app.router.add_get("/ws", fake_binance_raw)
    app.router.add_get("/silent", fake_silent)
    app.router.add_get("/bybit", fake_bybit)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()

    market_feed.BINANCE_WS_RAW = f"{BASE}/ws"
    market_feed.BYBIT_WS = f"{BASE}/bybit"

    await scenario_combined()
    await scenario_late_client()
    await scenario_silent_source()

    await runner.cleanup()


if __name__ == "__main__":
    print("потиковый поток (локальные псевдо-биржи)")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
