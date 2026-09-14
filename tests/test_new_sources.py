"""
Сквозной прогон трёх новых источников ликвидаций на локальных псевдо-биржах.

Псевдо-биржи повторяют документированное поведение настоящих:

  dYdX v4    wss://indexer.dydx.trade/v4/ws
             {"type":"subscribe","channel":"v4_trades","id":"BTC-USD"} ->
             {"type":"subscribed",...}, дальше data-кадры; на подписку отдаётся
             СРЕЗ последних сделок (в т.ч. старые ликвидации).
             У сделки type: Limit | Liquidated | Deleveraged.

  Kraken     wss://futures.kraken.com/ws/v1
             {"event":"subscribe","feed":"trade","product_ids":["PF_XBTUSD"]} ->
             {"event":"subscribed",...}, дальше trade_snapshot и trade.
             У сделки type: fill | liquidation | termination | block.

  Bitfinex   wss://api-pub.bitfinex.com/ws/2
             {"event":"info"} на входе, {"event":"subscribe","channel":"status",
             "key":"liq:global"} -> {"event":"subscribed","chanId":N,...},
             дальше кадры [chanId, [["pos", ...]]] и [chanId, "hb"].

Проверяем:
  1. ликвидация доходит до колбэка и имеет полный набор полей;
  2. подписочный срез (старые события) в ленту НЕ попадает;
  3. обычные сделки ликвидациями не считаются;
  4. у Bitfinex чужой chanId, "hb" и не-перп символы отсеиваются;
  5. health показывает внутренности канала (сколько сделок видели, сколько
     подписок подтверждено) — по ним видно «жив, но ликвидаций нет»;
  6. сквозной прогон через server.py: все три источника в /api/health,
     а их события — в /api/liquidations.

Сеть наружу не нужна.  Запуск:  python3 tests/test_new_sources.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import market_feed
from market_feed import MarketFeed

DYDX_PORT = 8821
KRAKEN_PORT = 8822
BITFINEX_PORT = 8823
SERVER_PORT = 8824

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


async def noop_price(*a):
    pass


def iso(sec_ago: float) -> str:
    return (datetime.fromtimestamp(time.time() - sec_ago, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


def ms(sec_ago: float) -> int:
    return int((time.time() - sec_ago) * 1000)


# ---------------------------------------------------------------------------
# Псевдо-dYdX v4
# ---------------------------------------------------------------------------
class FakeDydx:
    def __init__(self, liq_every=0.3, snapshot=True, reject=None):
        self.liq_every = liq_every
        self.snapshot = snapshot
        self.reject = reject            # тикер, на который отвечаем ошибкой
        self.subs = []
        self.sockets = set()

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.add(ws)
        await ws.send_json({"type": "connected", "connection_id": "test",
                            "message_id": 0})
        tape = asyncio.create_task(self._tape(ws))
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    p = json.loads(msg.data)
                except Exception:
                    continue
                if p.get("type") == "subscribe" and p.get("channel") == "v4_trades":
                    self.subs.append(p.get("id"))
                    if p.get("id") == self.reject:
                        await ws.send_json({"type": "error",
                                            "message": "Unknown ticker",
                                            "id": p.get("id")})
                        continue
                    await ws.send_json({"type": "subscribed",
                                        "channel": "v4_trades",
                                        "id": p.get("id"), "contents": {}})
        finally:
            tape.cancel()
            self.sockets.discard(ws)
        return ws

    def _trade(self, ticker, ttype, sec_ago, side="SELL"):
        return {"id": f"{ticker}-{ttype}-{sec_ago}", "createdAt": iso(sec_ago),
                "side": side, "price": "60000", "size": "0.5", "type": ttype}

    async def _tape(self, ws):
        # срез истории при подписке: свежие и ВЧЕРАШНИЕ ликвидации
        if self.snapshot:
            await ws.send_json({"type": "data", "channel": "v4_trades",
                                "id": "BTC-USD", "contents": {"trades": [
                                    self._trade("BTC-USD", "LIMIT", 1),
                                    self._trade("BTC-USD", "LIQUIDATED", 90000),
                                ]}})
        while not ws.closed:
            await asyncio.sleep(self.liq_every)
            if ws.closed:
                return
            await ws.send_json({"type": "data", "channel": "v4_trades",
                                "id": "BTC-USD", "contents": {"trades": [
                                    self._trade("BTC-USD", "LIMIT", 0.1, "BUY"),
                                    self._trade("BTC-USD", "LIQUIDATED", 0.2),
                                ]}})


# ---------------------------------------------------------------------------
# Псевдо-Kraken Futures
# ---------------------------------------------------------------------------
class FakeKraken:
    def __init__(self, liq_every=0.3, snapshot=True, reject=None):
        self.liq_every = liq_every
        self.snapshot = snapshot
        self.reject = reject            # продукт, на который отвечаем ошибкой
        self.subs = []
        self.pings = 0
        self.seq = 100

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        tape = asyncio.create_task(self._tape(ws))
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    p = json.loads(msg.data)
                except Exception:
                    continue
                if p.get("event") == "ping":
                    self.pings += 1
                    continue
                if p.get("event") == "subscribe" and p.get("feed") == "trade":
                    for pid in p.get("product_ids") or []:
                        self.subs.append(pid)
                        if pid == self.reject:
                            await ws.send_json({"event": "error",
                                                "error": "Invalid product id",
                                                "product_ids": [pid]})
                            continue
                        await ws.send_json({"event": "subscribed", "feed": "trade",
                                            "product_ids": [pid]})
        finally:
            tape.cancel()
        return ws

    def _trade(self, pid, ttype, sec_ago, side="sell"):
        self.seq += 1
        return {"feed": "trade", "product_id": pid, "uid": f"{pid}-{self.seq}",
                "side": side, "type": ttype, "seq": self.seq,
                "time": ms(sec_ago), "qty": 100, "price": 34800}

    async def _tape(self, ws):
        if self.snapshot:
            await ws.send_json({"feed": "trade_snapshot", "product_id": "PF_XBTUSD",
                                "trades": [self._trade("PF_XBTUSD", "fill", 1),
                                           self._trade("PF_XBTUSD", "liquidation", 90000)]})
        while not ws.closed:
            await asyncio.sleep(self.liq_every)
            if ws.closed:
                return
            await ws.send_json(self._trade("PF_XBTUSD", "fill", 0.1, "buy"))
            await ws.send_json(self._trade("PF_XBTUSD", "liquidation", 0.2))
            await ws.send_json(self._trade("PF_XBTUSD", "termination", 0.3, "buy"))


# ---------------------------------------------------------------------------
# Псевдо-Bitfinex
# ---------------------------------------------------------------------------
class FakeBitfinex:
    CHAN = 91684

    def __init__(self, liq_every=0.3, snapshot=True):
        self.liq_every = liq_every
        self.snapshot = snapshot
        self.subs = []
        self.pings = 0
        self.n = 0

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"event": "info", "version": 2, "serverId": 1})
        tape = asyncio.create_task(self._tape(ws))
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    p = json.loads(msg.data)
                except Exception:
                    continue
                if p.get("event") == "ping":
                    self.pings += 1
                    await ws.send_json({"event": "pong", "cid": p.get("cid")})
                    continue
                if p.get("event") == "subscribe" and p.get("key") == "liq:global":
                    self.subs.append(p.get("key"))
                    await ws.send_json({"event": "subscribed", "channel": "status",
                                        "chanId": self.CHAN, "key": "liq:global"})
                    # настоящая биржа отдаёт срез последних ликвидаций уже
                    # ПОСЛЕ подтверждения подписки
                    if self.snapshot:
                        await ws.send_json([self.CHAN, [
                            self._pos("tBTCF0:USTF0", 1.0, 1),
                            self._pos("tBTCF0:USTF0", 1.0, 90000),
                            self._pos("tBSVUSD", -2.6, 1, 112.27)]])
        finally:
            tape.cancel()
        return ws

    def _pos(self, symbol, amount, sec_ago, price=60000.0):
        self.n += 1
        return ["pos", 1000 + self.n, ms(sec_ago), None, symbol, amount,
                price * 0.99, None, 1, 1, None, price]

    async def _tape(self, ws):
        while not ws.closed:
            await asyncio.sleep(self.liq_every)
            if ws.closed:
                return
            await ws.send_json([self.CHAN, [self._pos("tBTCF0:USTF0", 2.5, 0.2)]])
            await ws.send_json([self.CHAN, [self._pos("tETHF0:USTF0", -1.0, 0.3, 3000.0)]])
            await ws.send_json([self.CHAN, "hb"])


# ---------------------------------------------------------------------------
# Сценарии
# ---------------------------------------------------------------------------
def make_feed(liqs, symbols=("BTC_USDT", "ETH_USDT", "PEPE_USDT")):
    async def on_liq(ev):
        liqs.append(ev)

    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed.symbols = list(symbols)
    return feed


# BTC/ETH/PEPE у биржи есть, LSK и ZEC — нет: на них сценарий 0 и проверяет
# фильтр подписок
DYDX_MARKETS = {"markets": {"BTC-USD": {}, "ETH-USD": {}, "PEPE-USD": {}}}
KRAKEN_INSTRUMENTS = {"instruments": [{"symbol": "PF_XBTUSD"},
                                       {"symbol": "PF_ETHUSD"},
                                       {"symbol": "PF_PEPEUSD"}]}


def add_rest(app, port):
    """REST-заглушки со списками рынков на том же порту, что и WS."""
    async def dydx_markets(request):
        return web.json_response(DYDX_MARKETS)

    async def kraken_instruments(request):
        return web.json_response(KRAKEN_INSTRUMENTS)

    app.router.add_get("/v4/perpetualMarkets", dydx_markets)
    app.router.add_get("/instruments", kraken_instruments)


async def scenario_markets():
    """Подписка только на рынки, которые у биржи реально есть."""
    print("0) фильтр подписок по списку рынков биржи")
    # свои порты: основные псевдо-биржи уже подняты в main()
    dport, kport = DYDX_PORT + 20, KRAKEN_PORT + 20
    market_feed.DYDX_WS = f"http://127.0.0.1:{dport}/ws"
    market_feed.DYDX_REST = f"http://127.0.0.1:{dport}"
    market_feed.DYDX_SUB_GAP = 0.01
    market_feed.KRAKEN_WS = f"http://127.0.0.1:{kport}/ws"
    market_feed.KRAKEN_FUT_REST = f"http://127.0.0.1:{kport}"
    market_feed.KRAKEN_PING_SEC = 5

    fd, fk = FakeDydx(), FakeKraken()
    runners = []
    for fake, port in ((fd, dport), (fk, kport)):
        app = web.Application()
        app.router.add_get("/ws", fake.handle)
        add_rest(app, port)
        r = web.AppRunner(app)
        await r.setup()
        await web.TCPSite(r, "127.0.0.1", port).start()
        runners.append(r)
    try:
        liqs = []
        feed = make_feed(liqs, ("BTC_USDT", "ETH_USDT", "LSK_USDT", "ZEC_USDT"))
        async with aiohttp.ClientSession() as session:
            feed._session = session
            td = asyncio.create_task(feed._dydx_liquidations())
            tk = asyncio.create_task(feed._kraken_liquidations())
            try:
                check("dYdX: подписаны только существующие",
                      await wait_until(lambda: sorted(fd.subs)
                                       == ["BTC-USD", "ETH-USD"]), fd.subs)
                check("kraken: подписаны только существующие",
                      await wait_until(lambda: sorted(fk.subs)
                                       == ["PF_ETHUSD", "PF_XBTUSD"]), fk.subs)
                check("dYdX: всего подписок = числу существующих",
                      await wait_until(
                          lambda: feed.status["dydx"].extra.get(
                              "dydx_subs_total") == 2),
                      feed.status["dydx"].extra)
                check("dYdX: подтверждены все",
                      await wait_until(
                          lambda: feed.status["dydx"].extra.get(
                              "dydx_subs_acked") == 2),
                      feed.status["dydx"].extra)
            finally:
                for t in (td, tk):
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):   # noqa: B014
                        pass
    finally:
        for r in runners:
            await r.cleanup()
        # дальше сценарии идут на основных псевдо-биржах (там тоже есть REST)
        market_feed.DYDX_WS = f"http://127.0.0.1:{DYDX_PORT}/ws"
        market_feed.DYDX_REST = f"http://127.0.0.1:{DYDX_PORT}"
        market_feed.KRAKEN_WS = f"http://127.0.0.1:{KRAKEN_PORT}/ws"
        market_feed.KRAKEN_FUT_REST = f"http://127.0.0.1:{KRAKEN_PORT}"


async def scenario_dydx(fake):
    print("1) dYdX v4 — v4_trades с типом Liquidated")
    market_feed.DYDX_WS = f"http://127.0.0.1:{DYDX_PORT}/ws"
    market_feed.DYDX_REST = f"http://127.0.0.1:{DYDX_PORT}"
    market_feed.DYDX_SUB_GAP = 0.01
    liqs = []
    feed = make_feed(liqs)
    async with aiohttp.ClientSession() as session:
        feed._session = session
        task = asyncio.create_task(feed._dydx_liquidations())
        try:
            check("подписки ушли на все наши монеты",
                  await wait_until(lambda: len(fake.subs) >= 3), fake.subs)
            check("тикеры в формате dYdX (BTC-USD)", "BTC-USD" in fake.subs, fake.subs)
            check("ликвидация дошла до колбэка",
                  await wait_until(lambda: len(liqs) > 0), len(liqs))
            ev = liqs[0] if liqs else {}
            check("событие полное (символ/сторона/цена/сумма)",
                  ev.get("symbol") == "BTC_USDT" and ev.get("side") == "LONG"
                  and ev.get("price", 0) > 0 and ev.get("usd", 0) > 0, ev)
            check("SELL у тейкера = вынесли LONG", ev.get("side") == "LONG", ev)
            check("вчерашний срез в ленту не попал",
                  all(time.time() - e["timestamp"] < 600 for e in liqs),
                  [round(time.time() - e["timestamp"]) for e in liqs])
            extra = feed.status["dydx"].extra
            check("health: подписки подтверждены",
                  extra.get("dydx_subs_acked", 0) >= 3, extra)
            check("health: видно число просмотренных сделок",
                  extra.get("dydx_trades_seen", 0) >= 4, extra)
            check("health: срез истории учтён как stale",
                  extra.get("dydx_skipped_stale", 0) >= 1, extra)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):   # noqa: B014
                pass


async def scenario_kraken(fake):
    print("2) Kraken Futures — фид trade с типом liquidation/termination")
    market_feed.KRAKEN_WS = f"http://127.0.0.1:{KRAKEN_PORT}/ws"
    market_feed.KRAKEN_FUT_REST = f"http://127.0.0.1:{KRAKEN_PORT}"
    market_feed.KRAKEN_PING_SEC = 0.3
    liqs = []
    feed = make_feed(liqs)
    async with aiohttp.ClientSession() as session:
        feed._session = session
        task = asyncio.create_task(feed._kraken_liquidations())
        try:
            check("подписки ушли (PF_XBTUSD и др.)",
                  await wait_until(lambda: "PF_XBTUSD" in fake.subs), fake.subs)
            check("ликвидация дошла до колбэка",
                  await wait_until(lambda: len(liqs) > 0), len(liqs))
            ev = liqs[0] if liqs else {}
            check("событие полное", ev.get("symbol") == "BTC_USDT"
                  and ev.get("price", 0) > 0 and ev.get("usd", 0) > 0, ev)
            check("sell у тейкера = вынесли LONG", ev.get("side") == "LONG", ev)
            check("termination тоже считаем (сторона SHORT при buy)",
                  await wait_until(lambda: any(e["side"] == "SHORT" for e in liqs)),
                  [e["side"] for e in liqs])
            check("обычные fill-сделки в ленту не попали",
                  all(e["price"] == 34800 for e in liqs) and len(liqs) >= 2, len(liqs))
            check("вчерашний снапшот отсеян",
                  all(time.time() - e["timestamp"] < 600 for e in liqs))
            extra = feed.status["kraken"].extra
            check("health: подписки подтверждены",
                  extra.get("kraken_subs_acked", 0) >= 3, extra)
            check("health: сделки считаются",
                  extra.get("kraken_trades_seen", 0) >= 4, extra)
            check("пинг по таймеру уходит", fake.pings >= 1, fake.pings)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):   # noqa: B014
                pass


async def scenario_bitfinex(fake):
    print("3) Bitfinex — status/liq:global")
    market_feed.BITFINEX_WS = f"http://127.0.0.1:{BITFINEX_PORT}/ws"
    market_feed.BITFINEX_PING_SEC = 0.3
    liqs = []
    feed = make_feed(liqs)
    async with aiohttp.ClientSession() as session:
        feed._session = session
        task = asyncio.create_task(feed._bitfinex_liquidations())
        try:
            check("одна подписка на всю биржу",
                  await wait_until(lambda: fake.subs == ["liq:global"]), fake.subs)
            check("ликвидация дошла до колбэка",
                  await wait_until(lambda: len(liqs) > 0), len(liqs))
            ev = liqs[0] if liqs else {}
            check("событие полное", ev.get("symbol") == "BTC_USDT"
                  and ev.get("price", 0) > 0 and ev.get("usd", 0) > 0, ev)
            check("цена — цена ликвидации, объём — модуль размера",
                  abs(ev.get("price", 0) - 60000.0) < 1e-6
                  and ev.get("qty", 0) > 0, ev)
            check("отрицательный размер = вынесли SHORT",
                  await wait_until(lambda: any(e["side"] == "SHORT" for e in liqs)),
                  [e["side"] for e in liqs])
            extra = feed.status["bitfinex"].extra
            check("health: chanId получен", extra.get("bitfinex_chan_id") == fake.CHAN,
                  extra)
            check("health: спот-символы отсеяны и посчитаны",
                  extra.get("bitfinex_skipped_other", 0) >= 1, extra)
            check("health: срез истории учтён как stale",
                  extra.get("bitfinex_skipped_stale", 0) >= 1, extra)
            check("health: стороны видны в статистике",
                  extra.get("bitfinex_liq_long", 0) >= 1
                  and extra.get("bitfinex_liq_short", 0) >= 1, extra)
            check("пинг/понг ходят", fake.pings >= 1
                  and extra.get("bitfinex_pongs", 0) >= 1, (fake.pings, extra))
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):   # noqa: B014
                pass


async def scenario_rejections():
    """Отказ биржи должен быть виден: иначе «нет ликвидаций» неотличимо от
    «подписка не прошла»."""
    print("5) отказы подписки видны в health")
    market_feed.DYDX_WS = f"http://127.0.0.1:{DYDX_PORT}/ws"
    market_feed.DYDX_SUB_GAP = 0.01
    market_feed.KRAKEN_WS = f"http://127.0.0.1:{KRAKEN_PORT}/ws"
    market_feed.KRAKEN_PING_SEC = 5

    fd = FakeDydx(reject="BTC-USD")
    fk = FakeKraken(reject="PF_XBTUSD")
    runners = []
    for fake, port in ((fd, DYDX_PORT + 10), (fk, KRAKEN_PORT + 10)):
        app = web.Application()
        app.router.add_get("/ws", fake.handle)
        r = web.AppRunner(app)
        await r.setup()
        await web.TCPSite(r, "127.0.0.1", port).start()
        runners.append(r)
    prev_dydx, prev_kraken = market_feed.DYDX_WS, market_feed.KRAKEN_WS
    market_feed.DYDX_WS = f"http://127.0.0.1:{DYDX_PORT + 10}/ws"
    market_feed.KRAKEN_WS = f"http://127.0.0.1:{KRAKEN_PORT + 10}/ws"
    try:
        liqs = []
        feed = make_feed(liqs)
        async with aiohttp.ClientSession() as session:
            feed._session = session
            td = asyncio.create_task(feed._dydx_liquidations())
            tk = asyncio.create_task(feed._kraken_liquidations())
            try:
                check("dYdX: отказ посчитан",
                      await wait_until(lambda: feed.status["dydx"].extra.get(
                          "dydx_errors", 0) >= 1), feed.status["dydx"].extra)
                check("dYdX: причина в last_error",
                      "Unknown ticker" in (feed.status["dydx"].last_error or ""),
                      feed.status["dydx"].last_error)
                check("dYdX: отказ не попал в подтверждённые",
                      "BTC-USD" not in [feed.status["dydx"].extra.get(
                          "dydx_subs_acked")], feed.status["dydx"].extra)
                check("kraken: отказ посчитан",
                      await wait_until(lambda: feed.status["kraken"].extra.get(
                          "kraken_errors", 0) >= 1), feed.status["kraken"].extra)
                check("kraken: причина в last_error",
                      "Invalid product id" in (feed.status["kraken"].last_error
                                               or ""),
                      feed.status["kraken"].last_error)
                check("dYdX: типы кадров видны",
                      "error" in feed.status["dydx"].extra.get(
                          "dydx_frame_kinds", {}),
                      feed.status["dydx"].extra.get("dydx_frame_kinds"))
                check("kraken: типы кадров видны",
                      "error" in feed.status["kraken"].extra.get(
                          "kraken_frame_kinds", {}),
                      feed.status["kraken"].extra.get("kraken_frame_kinds"))
            finally:
                for t in (td, tk):
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):   # noqa: B014
                        pass
    finally:
        # возвращаем адреса основным псевдо-биржам: дальше сквозной прогон
        market_feed.DYDX_WS, market_feed.KRAKEN_WS = prev_dydx, prev_kraken
        for r in runners:
            await r.cleanup()


async def scenario_server(fake_d, fake_k, fake_b):
    print("6) сквозной прогон через server.py")
    os.environ["LIQSCOPE_DEMO"] = "0"
    os.environ["PORT"] = str(SERVER_PORT)
    os.environ["LIQSCOPE_DYDX_WS"] = f"http://127.0.0.1:{DYDX_PORT}/ws"
    os.environ["LIQSCOPE_KRAKEN_WS"] = f"http://127.0.0.1:{KRAKEN_PORT}/ws"
    os.environ["LIQSCOPE_BITFINEX_WS"] = f"http://127.0.0.1:{BITFINEX_PORT}/ws"
    os.environ["LIQSCOPE_DYDX_SUB_GAP_MS"] = "10"
    os.environ["LIQSCOPE_EXCHANGES"] = "dydx,kraken,bitfinex"
    import importlib
    import server as srv
    importlib.reload(srv)

    import uvicorn
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=SERVER_PORT,
                            log_level="warning")
    httpd = uvicorn.Server(config)
    task = asyncio.create_task(httpd.serve())
    try:
        await wait_until(lambda: httpd.started, 15)
        base = f"http://127.0.0.1:{SERVER_PORT}"
        async with aiohttp.ClientSession() as s:
            got = await wait_until(lambda: True, 1.0)   # даём ленте поработать
            async with s.get(base + "/api/health") as r:
                health = await r.json()
            src = health.get("sources", {})
            for name in ("dydx", "kraken", "bitfinex"):
                check(f"health: {name} подключён",
                      src.get(name, {}).get("connected") is True,
                      src.get(name))
            await asyncio.sleep(2.0)
            async with s.get(base + "/api/liquidations?limit=300") as r:
                data = await r.json()
            events = data.get("liquidations", [])
            by_ex = {}
            for e in events:
                by_ex[e["exchange"]] = by_ex.get(e["exchange"], 0) + 1
            check("в /api/liquidations есть dydx", by_ex.get("dydx", 0) > 0, by_ex)
            check("в /api/liquidations есть kraken", by_ex.get("kraken", 0) > 0, by_ex)
            check("в /api/liquidations есть bitfinex",
                  by_ex.get("bitfinex", 0) > 0, by_ex)
            sample = next((e for e in events if e["exchange"] == "dydx"), {})
            check("событие с биржи полное",
                  sample.get("symbol") and sample.get("usd", 0) > 0
                  and sample.get("position") in ("LONG", "SHORT"), sample)
            async with s.ws_connect(base + "/ws") as ws:
                raw = await asyncio.wait_for(ws.receive(), 10)
                msg = json.loads(raw.data)
                check("в приветствии WS — список бирж с новыми",
                      set(["dydx", "kraken", "bitfinex"]) <= set(msg.get("exchanges", [])),
                      msg.get("exchanges"))
    finally:
        httpd.should_exit = True
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):       # noqa: B014
            pass


async def main():
    fake_d = FakeDydx()
    fake_k = FakeKraken()
    fake_b = FakeBitfinex()

    app = web.Application()
    app.router.add_get("/ws", fake_d.handle)
    add_rest(app, DYDX_PORT)
    r1 = web.AppRunner(app)
    await r1.setup()
    await web.TCPSite(r1, "127.0.0.1", DYDX_PORT).start()

    app2 = web.Application()
    app2.router.add_get("/ws", fake_k.handle)
    add_rest(app2, KRAKEN_PORT)
    r2 = web.AppRunner(app2)
    await r2.setup()
    await web.TCPSite(r2, "127.0.0.1", KRAKEN_PORT).start()

    app3 = web.Application()
    app3.router.add_get("/ws", fake_b.handle)
    r3 = web.AppRunner(app3)
    await r3.setup()
    await web.TCPSite(r3, "127.0.0.1", BITFINEX_PORT).start()

    try:
        await scenario_markets()
        await scenario_dydx(fake_d)
        await scenario_kraken(fake_k)
        await scenario_bitfinex(fake_b)
        await scenario_rejections()
        await scenario_server(fake_d, fake_k, fake_b)
    finally:
        await r1.cleanup()
        await r2.cleanup()
        await r3.cleanup()


if __name__ == "__main__":
    print("новые источники ликвидаций: dYdX v4, Kraken Futures, Bitfinex")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
