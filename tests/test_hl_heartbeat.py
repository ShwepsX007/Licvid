"""
Heartbeat Hyperliquid: ping должен уходить по таймеру даже когда поток
сделок непрерывный (receive-таймаут никогда не срабатывает).

Раньше ping отправлялся только в ветке asyncio.TimeoutError цикла чтения —
на активных монетах он не отправлялся вовсе, и биржа закрывала «тихое»
соединение через ~60 секунд. Этот тест держит ленту занятой и проверяет,
что пинг всё равно уходит.

Сеть наружу не нужна.  Запуск:  python3 tests/test_hl_heartbeat.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import market_feed
from market_feed import MarketFeed

PORT = 8803
BASE = f"http://127.0.0.1:{PORT}"

pings = []          # сколько раз пришёл {"method":"ping"}
subs = []           # монеты, на которые подписались


async def on_liq(ev):
    pass


async def noop_price(*a):
    pass


async def fake_hl(request):
    """Псевдо-HL: подтверждает подписки и БЕСПРЕРЫВНО льёт сделки,
    чтобы receive() у клиента никогда не уходил в таймаут."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async def stream():
        n = 0
        while not ws.closed:
            await asyncio.sleep(0.05)
            n += 1
            await ws.send_json({"channel": "trades", "data": [
                {"coin": "BTC", "side": "B", "px": "50000", "sz": "0.01",
                 "time": n, "hash": f"h{n}"}]})

    streamer = asyncio.create_task(stream())
    try:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                p = json.loads(msg.data)
            except Exception:
                continue
            if p.get("method") == "ping":
                pings.append(p)
                await ws.send_json({"channel": "pong"})
                continue
            if p.get("method") == "subscribe":
                sub = p.get("subscription") or {}
                subs.append(sub.get("coin"))
                await ws.send_json({"channel": "subscriptionResponse",
                                    "data": {"method": "subscribe",
                                             "subscription": sub}})
    finally:
        streamer.cancel()
    return ws


def make_feed():
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed._session = aiohttp.ClientSession()
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    feed._hyperliquid_load_universe = (
        lambda: asyncio.sleep(0, result=["BTC", "ETH"]))
    return feed


async def main():
    # короткий интервал, чтобы не ждать 50 секунд в тесте
    market_feed.HL_PING_INTERVAL = 0.3
    market_feed.HL_WS = f"{BASE}/hl"

    app = web.Application()
    app.router.add_get("/hl", fake_hl)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()

    feed = make_feed()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        # даём ленте покрутиться: за это время должно прийти несколько пингов
        await asyncio.sleep(1.5)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await feed._session.close()
        await runner.cleanup()

    ok = True
    detail = ""
    if not subs:
        ok = False
        detail = "подписок не было вообще"
    if not pings:
        ok = False
        detail += f" пинг не ушёл (подписок={len(subs)})"
    print(f"подписок: {len(subs)} ({', '.join(subs)}), пингов: {len(pings)}")
    if ok:
        print("ИТОГ: heartbeat работает на непрерывной ленте — ОК")
    else:
        print(f"ИТОГ: ОШИБКА — {detail}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
