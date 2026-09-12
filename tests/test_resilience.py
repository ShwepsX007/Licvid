"""
Живучесть Hyperliquid-потока и смежной обвязки.

Закрывает три дыры, из-за которых боевой слушатель «отваливался», хотя зонд
tools/check_hyperliquid.py --bisect проходил 23/23:

  1. Зомби-сокет: TCP тихо потерян (CF-эдж/NAT/маршрут) — receive() вечно
     таймаутит, send(ping) пишет в буфер без ошибок, слушатель висит на
     мёртвом соединении вечно. Теперь сторож в heartbeat закрывает соединение,
     если входящих нет дольше HL_STALE_AFTER, и ConnectionError содержит
     причину. Проверяем и обратное: тихое, но ЖИВОЕ соединение (pong ходит,
     ленты нет) сторож не убивает.

  2. Backoff супервайзера: короткоживущий «успешный» коннект больше не
     сбрасывает паузу к минимуму (иначе биржа, рвущая соединения пачкой,
     попадает в цикл долбёжки и не поднимается); честный сброс — только
     после соединения, прожившего >= stable_uptime.

  3. Зависший WS-клиент терминала: Client.send ограничен по времени и
     помечает клиента мёртвым вместо бесконечного drain(), подвешивавшего
     читателей бирж.

  4. Сквозной прогон зонда в режиме --soak (нормальная биржа → код 0,
     замолчавшая биржа → код 4).

Сеть наружу не нужна.  Запуск:  python3 tests/test_resilience.py
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
import server as srv
from market_feed import MarketFeed

PORT = 8805
BASE = f"http://127.0.0.1:{PORT}"

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


async def on_liq(ev):
    pass


async def noop_price(*a):
    pass


async def fake_universe():
    return ["BTC", "ETH"]


# --- псевдо-биржа -----------------------------------------------------------

class FakeHL:
    """Аккумулирует счётчики pings/pongs/subs для проверок."""

    def __init__(self, answer_pongs: int):
        self.answer_pongs = answer_pongs   # сколько первых ping ответить pong
        self.pings = 0
        self.pongs = 0
        self.subs = 0

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        answered = 0
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                p = json.loads(msg.data)
            except Exception:
                continue
            if p.get("method") == "ping":
                self.pings += 1
                if answered < self.answer_pongs:
                    answered += 1
                    self.pongs += 1
                    await ws.send_json({"channel": "pong"})
                continue
            if p.get("method") == "subscribe":
                self.subs += 1
                await ws.send_json({
                    "channel": "subscriptionResponse",
                    "data": {"method": "subscribe",
                             "subscription": p.get("subscription") or {}}})
        return ws


def make_feed(session) -> MarketFeed:
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed._session = session
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    feed._hyperliquid_load_universe = fake_universe
    return feed


# Разные маршруты-псевдобиржи: у каждого сценария свой характер «биржи».
scenario1_zombie = FakeHL(answer_pongs=1)   # pong на первый ping, дальше молчит
scenario2_alive = FakeHL(answer_pongs=10**9)  # тихая, но живая: pong всегда
soak_alive = FakeHL(answer_pongs=10**9)
soak_zombie = FakeHL(answer_pongs=1)


async def run_listener(feed, timeout_s):
    """Запустить слушатель, вернуть (исключение, секунды до исхода)."""
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    t0 = time.monotonic()
    err = None
    try:
        await asyncio.wait_for(task, timeout_s)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014
            pass
    except Exception as e:  # noqa: BLE001
        err = e
    return err, time.monotonic() - t0


# --- 1) сторож убивает зомби-сокет ------------------------------------------

async def scenario_watchdog_kills_zombie():
    print("1) зомби-сокет (pong перестал приходить) — сторож рвёт соединение")
    market_feed.HL_WS = f"{BASE}/zombie"
    market_feed.HL_PING_INTERVAL = 0.3
    market_feed.HL_STALE_AFTER = 1.0
    fake = scenario1_zombie            # маршрут /zombie ведёт на этот инстанс
    async with aiohttp.ClientSession() as session:
        feed = make_feed(session)
        err, elapsed = await run_listener(feed, 10)
    check("слушатель завершился с громкой ошибкой",
          isinstance(err, ConnectionError), repr(err))
    check("в тексте причины — «входящих нет»",
          err is not None and "входящих нет" in str(err), str(err)[:160])
    check("пришлось отправить несколько пингов (≥3)",
          fake.pings >= 3, fake.pings)
    check("первый pong всё же был (убийство — из-за молчания ПОСЛЕ него)",
          fake.pongs == 1, fake.pongs)
    check("сработал быстро (быстрее таймаута ожидания)",
          elapsed < 9.5, f"{elapsed:.1f}с")


# --- 2) тихое, но живое соединение сторож НЕ убивает ------------------------

async def scenario_watchdog_keeps_quiet_alive():
    print("2) тихое соединение с рабочим pong — сторож не срабатывает")
    market_feed.HL_WS = f"{BASE}/alive"
    market_feed.HL_PING_INTERVAL = 0.3
    market_feed.HL_STALE_AFTER = 1.0
    fake = scenario2_alive             # маршрут /alive ведёт на этот инстанс
    async with aiohttp.ClientSession() as session:
        feed = make_feed(session)
        task = asyncio.create_task(feed._hyperliquid_liquidations())
        try:
            await asyncio.sleep(1.5)   # > HL_STALE_AFTER, несколько пингов
            alive = not task.done() and feed.status["hyperliquid"].connected
            check("соединение живо после окна молчания ленты", alive)
            check("пинги реально ходили", fake.pings >= 3, fake.pings)
            check("pong приходил на каждый пинг",
                  fake.pongs == fake.pings, f"{fake.pongs}/{fake.pings}")
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass


# --- 3) backoff супервайзера -------------------------------------------------

async def scenario_supervise_backoff():
    print("3) backoff: короткие «успехи» не сбрасывают паузу, долгий — сбрасывает")
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    plan = [
        ("ok", 0.05),    # короткоживущий успех — раньше сбрасывал паузу
        ("ok", 0.05),
        ("ok", 0.05),
        ("ok", 0.45),    # долгий успех (>= stable_uptime) — честный сброс
        ("fail", 0.0),   # после него цикл завершается (ставит _stop)
    ]
    calls = {"n": 0}

    async def factory():
        i = calls["n"]
        calls["n"] += 1
        mode, life = plan[i]
        await asyncio.sleep(life)
        if mode == "fail" or i + 1 >= len(plan):
            feed._stop.set()
        if mode == "fail":
            raise RuntimeError(f"boom#{i}")

    sleeps = []

    class Shim:
        """Перехват asyncio.sleep внутри market_feed._supervise."""

        def __getattr__(self, name):
            return getattr(asyncio, name)

        @staticmethod
        async def sleep(d):
            sleeps.append(d)

    orig = market_feed.asyncio
    market_feed.asyncio = Shim()
    try:
        await asyncio.wait_for(
            feed._supervise("hyperliquid", factory,
                            base_delay=0.1, stable_uptime=0.3), 10)
    finally:
        market_feed.asyncio = orig

    check("паузы копятся на короткоживущих успехах (0.1 → 0.16 → 0.256)",
          len(sleeps) >= 3 and sleeps[1] > sleeps[0] and sleeps[2] > sleeps[1],
          sleeps[:3])
    check("долгоживущий успех сбросил паузу к base",
          len(sleeps) >= 4 and abs(sleeps[3] - 0.1) < 1e-9, sleeps[:4])
    check("после сброса рост начинается заново",
          len(sleeps) >= 5 and sleeps[4] > sleeps[3], sleeps[:5])

    # initial_delay: пауза перед первой попыткой, в ретраи не подмешивается
    feed._stop.clear()
    sleeps.clear()
    calls["n"] = 0

    async def factory_once():
        feed._stop.set()
        raise RuntimeError("once")

    market_feed.asyncio = Shim()
    try:
        await asyncio.wait_for(
            feed._supervise("hyperliquid", factory_once,
                            base_delay=0.1, initial_delay=0.5), 10)
    finally:
        market_feed.asyncio = orig
    check("initial_delay ждёт до первой попытки (0.5, потом 0.1)",
          len(sleeps) >= 2 and abs(sleeps[0] - 0.5) < 1e-9
          and abs(sleeps[1] - 0.1) < 1e-9, sleeps[:2])
    feed._stop.clear()


# --- 4) таймаут отправки зависшему клиенту -----------------------------------

class SlowWS:
    def __init__(self, lag: float):
        self.lag = lag

    async def send_json(self, msg):
        await asyncio.sleep(self.lag)


async def scenario_client_send_timeout():
    print("4) зависший WS-клиент: send ограничен по времени, клиент мёртв")
    old = srv.Client.SEND_TIMEOUT
    srv.Client.SEND_TIMEOUT = 0.15
    try:
        c = srv.Client(SlowWS(2.0))
        t0 = time.monotonic()
        sent = await c.send({"t": 1})
        dt = time.monotonic() - t0
        check("send вернул False для зависшего клиента", sent is False)
        check("клиент помечен мёртвым", c.alive is False)
        check("не ждали lag отправщика, вернулись по таймауту",
              dt < 1.0, f"{dt:.2f}с")
        c2 = srv.Client(SlowWS(0.01))
        sent2 = await c2.send({"t": 1})
        check("живой клиент проходит как раньше",
              sent2 is True and c2.alive is True)
    finally:
        srv.Client.SEND_TIMEOUT = old


# --- 5) сквозной прогон зонда --soak -----------------------------------------

async def scenario_soak_probe():
    print("5) зонд --soak: живая биржа → 0, замолчавшая → 4")
    import tools.check_hyperliquid as probe
    probe.HL_REST = BASE
    probe.HL_WS = f"{BASE}/hl"
    probe.SOAK_PING = 0.3
    probe.SOAK_STALE = 0.9

    rc = await probe.soak(BASE, 1.6)
    check("живая биржа: soak пережил весь срок (код 0)", rc == 0, rc)
    check("живая биржа: пинги/понги сходятся",
          soak_alive.pings >= 2 and soak_alive.pongs == soak_alive.pings,
          f"{soak_alive.pongs}/{soak_alive.pings}")

    probe.HL_WS = f"{BASE}/hl-dead"
    rc = await probe.soak(BASE, 30)
    check("замолчавшая биржа: soak завершился с кодом 4", rc == 4, rc)
    check("замолчавшая биржа: замечены пинги без ответа",
          soak_zombie.pings >= 3 and soak_zombie.pongs == 1,
          f"pings={soak_zombie.pings} pongs={soak_zombie.pongs}")


async def main():
    app = web.Application()

    async def api_symbols(request):
        return web.json_response({"symbols": ["BTC_USDT", "ETH_USDT"]})

    async def info_meta(request):
        return web.json_response({"universe": [{"name": "BTC"},
                                               {"name": "ETH"}]})

    app.router.add_get("/api/symbols", api_symbols)
    app.router.add_post("/info", info_meta)
    app.router.add_get("/zombie", scenario1_zombie.handle)
    app.router.add_get("/alive", scenario2_alive.handle)
    app.router.add_get("/hl", soak_alive.handle)
    app.router.add_get("/hl-dead", soak_zombie.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        await scenario_watchdog_kills_zombie()
        await scenario_watchdog_keeps_quiet_alive()
        await scenario_supervise_backoff()
        await scenario_client_send_timeout()
        await scenario_soak_probe()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    print("живучесть hyperliquid и обвязки (локальная псевдо-биржа)")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
