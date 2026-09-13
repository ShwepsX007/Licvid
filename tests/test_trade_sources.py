"""
Источники тиков для CVD: dYdX v4, Kraken Futures, Bitfinex, Hyperliquid.

CVD считается из потока сделок, где у каждой сделки есть сторона ТЕЙКЕРА.
Все четыре биржи такую ленту публикуют, поэтому движок тиков умеет брать
данные с любой из них:
    LIQSCOPE_TICK_SOURCE=dydx,kraken,bitfinex,hyperliquid

Псевдо-биржи повторяют документированные кадры:
  dYdX    {"type":"data","channel":"v4_trades","id":"BTC-USD",
           "contents":{"trades":[{createdAt,side,price,size,type}]}}
  Kraken  {"feed":"trade","product_id":"PF_XBTUSD","side":"sell","price":..,
           "qty":..,"time":мс}
  Bitfinex [chanId,[seq,мс,amount,price]] + subscribed с pair/chanId
  HL      {"channel":"trades","data":{"coin":"BTC","trades":[{px,sz,side,time}]}}

Проверяем: сделки доходят до колбэка CVD, сторона тейкера определена верно,
цена/объём/время разобраны, чужие каналы и фиды отсеиваются, пинг уходит.

Сеть наружу не нужна.  Запуск:  python3 tests/test_trade_sources.py
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import market_feed
from market_feed import MarketFeed

DYDX_PORT = 8831
KRAKEN_PORT = 8832
BITFINEX_PORT = 8833
HL_PORT = 8834

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}  [{detail}]")


async def wait_until(cond, timeout=6.0, step=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def ms(sec_ago=0.0):
    return int((time.time() - sec_ago) * 1000)


def iso(sec_ago=0.0):
    from datetime import datetime, timezone
    return (datetime.fromtimestamp(time.time() - sec_ago, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


def make_feed(trades, hot=("BTC_USDT",)):
    async def on_trade(sym, price, qty, ts, side):
        trades.append((sym, price, qty, ts, side))

    async def on_liq(ev):
        pass

    async def on_price(*a):
        pass

    feed = MarketFeed(on_liquidation=on_liq, on_price=on_price,
                      on_trade=on_trade, exchanges=[])
    feed.symbols = list(hot)
    feed.hot_symbols = set(hot)
    return feed


# ---------------------------------------------------------------------------
class FakeDydxTrades:
    def __init__(self):
        self.subs = []

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        task = None
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                p = json.loads(msg.data)
                if p.get("type") == "subscribe" and p.get("channel") == "v4_trades":
                    self.subs.append(p.get("id"))
                    await ws.send_json({"type": "subscribed",
                                        "channel": "v4_trades",
                                        "id": p.get("id"), "contents": {}})
                    if task is None:
                        task = asyncio.create_task(self._tape(ws))
        finally:
            if task:
                task.cancel()
        return ws

    async def _tape(self, ws):
        while not ws.closed:
            await ws.send_json({"type": "data", "channel": "v4_trades",
                                "id": "BTC-USD", "contents": {"trades": [
                                    {"id": "a", "createdAt": iso(0.1),
                                     "side": "BUY", "price": "60000",
                                     "size": "0.25", "type": "LIMIT"},
                                    {"id": "b", "createdAt": iso(0.1),
                                     "side": "SELL", "price": "59990",
                                     "size": "0.5", "type": "LIQUIDATED"},
                                ]}})
            # чужой канал — не сделки
            await ws.send_json({"type": "data", "channel": "v4_orderbook",
                                "id": "BTC-USD", "contents": {}})
            # чужой тикер — не в нашей карте
            await ws.send_json({"type": "data", "channel": "v4_trades",
                                "id": "ZZZ-USD", "contents": {"trades": [
                                    {"id": "c", "createdAt": iso(0.1),
                                     "side": "BUY", "price": "1", "size": "1",
                                     "type": "LIMIT"}]}})
            await asyncio.sleep(0.2)


class FakeKrakenTrades:
    def __init__(self):
        self.subs = []
        self.pings = 0

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        task = None
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                p = json.loads(msg.data)
                if p.get("event") == "ping":
                    self.pings += 1
                    continue
                if p.get("event") == "subscribe" and p.get("feed") == "trade":
                    for pid in p.get("product_ids") or []:
                        self.subs.append(pid)
                        await ws.send_json({"event": "subscribed",
                                            "feed": "trade",
                                            "product_ids": [pid]})
                        if task is None:
                            task = asyncio.create_task(self._tape(ws))
        finally:
            if task:
                task.cancel()
        return ws

    async def _tape(self, ws):
        while not ws.closed:
            await ws.send_json({"feed": "trade", "product_id": "PF_XBTUSD",
                                "uid": "1", "side": "buy", "type": "fill",
                                "seq": 1, "time": ms(0.1), "qty": 10,
                                "price": 60000})
            await ws.send_json({"feed": "trade", "product_id": "PF_XBTUSD",
                                "uid": "2", "side": "sell",
                                "type": "liquidation", "seq": 2,
                                "time": ms(0.1), "qty": 5, "price": 59900})
            await ws.send_json({"feed": "ticker", "product_id": "PF_XBTUSD"})
            await asyncio.sleep(0.2)


class FakeBitfinexTrades:
    CHAN = 4242
    OTHER = 9999

    def __init__(self):
        self.subs = []
        self.pings = 0

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"event": "info", "version": 2})
        task = None
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                p = json.loads(msg.data)
                if p.get("event") == "ping":
                    self.pings += 1
                    await ws.send_json({"event": "pong", "cid": p.get("cid")})
                    continue
                if p.get("event") == "subscribe" and p.get("channel") == "trades":
                    key = p.get("key") or ""
                    self.subs.append(key)
                    await ws.send_json({"event": "subscribed", "channel": "trades",
                                        "chanId": self.CHAN, "pair": key})
                    if task is None:
                        task = asyncio.create_task(self._tape(ws))
        finally:
            if task:
                task.cancel()
        return ws

    async def _tape(self, ws):
        while not ws.closed:
            await ws.send_json([self.CHAN, [1, ms(0.1), 0.75, 60000.0]])
            await ws.send_json([self.CHAN, [2, ms(0.1), -0.5, 59950.0]])
            await ws.send_json([self.CHAN, "hb"])
            await ws.send_json([self.OTHER, [3, ms(0.1), 1.0, 1.0]])
            await asyncio.sleep(0.2)


class FakeHLTrades:
    def __init__(self):
        self.subs = []
        self.pings = 0

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        task = None
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                p = json.loads(msg.data)
                if p.get("method") == "ping":
                    self.pings += 1
                    await ws.send_json({"channel": "pong"})
                    continue
                if p.get("method") == "subscribe":
                    sub = p.get("subscription") or {}
                    if sub.get("type") == "trades":
                        self.subs.append(sub.get("coin"))
                        await ws.send_json({"channel": "subscriptionResponse",
                                            "data": sub})
                        if task is None:
                            task = asyncio.create_task(self._tape(ws))
        finally:
            if task:
                task.cancel()
        return ws

    async def _tape(self, ws):
        while not ws.closed:
            await ws.send_json({"channel": "trades", "data": {
                "coin": "BTC", "trades": [
                    {"coin": "BTC", "side": "B", "px": "60000", "sz": "0.1",
                     "time": ms(0.1), "tid": 1, "users": [], "hash": "h"},
                    {"coin": "BTC", "side": "A", "px": "59990", "sz": "0.2",
                     "time": ms(0.1), "tid": 2, "users": [], "hash": "h"},
                ]}})
            await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
async def scenario(title, url_attr, url, stream, fake, extra=(), prep=None):
    """Гоняет один поток сделок против псевдо-биржи и проверяет колбэк CVD."""
    print(title)
    setattr(market_feed, url_attr, url)
    trades = []
    feed = make_feed(trades)
    if prep:
        prep(feed)
    async with aiohttp.ClientSession() as session:
        feed._session = session
        task = asyncio.create_task(getattr(feed, stream)())
        try:
            check("сделки дошли до колбэка CVD",
                  await wait_until(lambda: len(trades) >= 2), len(trades))
            sides = {t[4] for t in trades}
            check("обе стороны тейкера определены",
                  sides == {"BUY", "SELL"}, sides)
            check("цена и объём разобраны",
                  bool(trades) and all(t[1] > 0 and t[2] > 0 for t in trades),
                  trades[:3])
            check("символ в нашем формате",
                  bool(trades) and all(t[0] == "BTC_USDT" for t in trades),
                  trades[:3])
            check("время в секундах и свежее",
                  bool(trades) and all(abs(t[3] - time.time()) < 120
                                       for t in trades), trades[:3])
            check("цена обновлена в self.prices",
                  feed.prices.get("BTC_USDT", 0) > 0, feed.prices)
            # пинги уходят по таймеру — даём ему хотя бы пару интервалов
            await asyncio.sleep(0.8)
            for cname, cfn in extra:
                check(cname, cfn(trades, feed), f"{cname} не выполнено")
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):      # noqa: B014
                pass


async def main():
    fd, fk, fb, fh = (FakeDydxTrades(), FakeKrakenTrades(),
                      FakeBitfinexTrades(), FakeHLTrades())
    runners = []
    for fake, port in ((fd, DYDX_PORT), (fk, KRAKEN_PORT),
                       (fb, BITFINEX_PORT), (fh, HL_PORT)):
        app = web.Application()
        app.router.add_get("/ws", fake.handle)
        r = web.AppRunner(app)
        await r.setup()
        await web.TCPSite(r, "127.0.0.1", port).start()
        runners.append(r)

    try:
        market_feed.DYDX_SUB_GAP = 10
        await scenario(
            "1) dYdX v4 как источник тиков", "DYDX_WS",
            f"http://127.0.0.1:{DYDX_PORT}/ws", "_dydx_trade_stream", fd,
            (("подписка на BTC-USD", lambda t, f: "BTC-USD" in fd.subs),
             ("чужой тикер не попал в CVD",
              lambda t, f: all(x[1] > 100 for x in t))))

        market_feed.KRAKEN_PING_SEC = 0.3
        await scenario(
            "2) Kraken Futures как источник тиков", "KRAKEN_WS",
            f"http://127.0.0.1:{KRAKEN_PORT}/ws", "_kraken_trade_stream", fk,
            (("подписка на PF_XBTUSD", lambda t, f: "PF_XBTUSD" in fk.subs),
             ("пинг уходит по таймеру", lambda t, f: fk.pings >= 1)))

        market_feed.BITFINEX_PING_SEC = 0.3
        await scenario(
            "3) Bitfinex как источник тиков", "BITFINEX_WS",
            f"http://127.0.0.1:{BITFINEX_PORT}/ws", "_bitfinex_trade_stream", fb,
            (("подписка на tBTCF0:USTF0",
              lambda t, f: "tBTCF0:USTF0" in fb.subs),
             ("пинг уходит по таймеру", lambda t, f: fb.pings >= 1),
             ("чужой chanId отсеян", lambda t, f: all(x[1] > 50000 for x in t)),
             ("объём всегда положительный",
              lambda t, f: all(x[2] > 0 for x in t))))

        market_feed.HL_PING_INTERVAL = 0.3

        def _stub_universe(feed):
            # universe в песочнице недоступен — подставляем локально, как в
            # tests/test_hl_stream.py
            async def _fake_universe(session=None):
                return {"BTC", "ETH"}
            feed._hyperliquid_load_universe = _fake_universe

        await scenario(
            "4) Hyperliquid как источник тиков", "HL_WS",
            f"http://127.0.0.1:{HL_PORT}/ws", "_hyperliquid_trade_stream", fh,
            (("подписка на BTC", lambda t, f: "BTC" in fh.subs),
             ("сторона A = SELL, B = BUY",
              lambda t, f: {"BUY", "SELL"} == {x[4] for x in t}),
             ("пинг уходит по таймеру", lambda t, f: fh.pings >= 1)),
            prep=_stub_universe)
    finally:
        for r in runners:
            await r.cleanup()


if __name__ == "__main__":
    print("источники тиков для CVD: dYdX v4, Kraken Futures, Bitfinex, "
          "Hyperliquid")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
