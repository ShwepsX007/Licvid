"""
Сквозной прогон потока ликвидаций Hyperliquid на локальной псевдо-бирже.

Псевдо-биржа повторяет документированное поведение настоящего HL:
  * на subscribe отдаёт СРЕЗ последних сделок по монете (в том числе старые
    ликвидации) и только потом subscriptionResponse;
  * дальше льёт живую ленту trades, в которой ликвидации помечены объектом;
  * на {"method":"ping"} отвечает {"channel":"pong"};
  * рвёт соединение, если от клиента тишина дольше N секунд (у настоящего HL
    этот порог ~60 с — именно поэтому пинг обязан уходить по таймеру);
  * может «терять» подтверждение подписки (проверка повторов).

Что проверяем:
  1. поток начинается сразу: ликвидация доходит до колбэка за секунды,
     статус up() — сразу после рукопожатия, а не после всех подписок;
  2. срез истории (isSnapshot и старые сделки) в ленту НЕ попадает —
     иначе каждое переподключение заново показывало бы вчерашние ликвидации;
  3. пинг по таймеру держит соединение, которое биржа рвёт за молчание;
  4. неподтверждённая подписка повторяется и встаёт (видно в health);
  5. после разрыва супервайзер переподключается и поток продолжается;
  6. сквозной прогон через настоящий server.py: /api/health показывает
     hyperliquid подключённым с подтверждёнными подписками, а
     /api/liquidations — живые ликвидации с биржи.

Сеть наружу не нужна.  Запуск:  python3 tests/test_hl_stream.py
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

PORT = 8807
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


async def noop_price(*a):
    pass


# ---------------------------------------------------------------------------
# Псевдо-Hyperliquid
# ---------------------------------------------------------------------------
class FakeHL:
    COINS = ("BTC", "ETH")

    def __init__(self, idle_kill=None, close_after=None, ignore_subs=None,
                 snapshot=True, liq_every=0.4, coins=None, reject_coins=(),
                 strict_unknown=False):
        self.idle_kill = idle_kill      # рвём сокет после N с тишины клиента
        self.close_after = close_after  # рвём сокет через N с жизни
        self.ignore_subs = dict(ignore_subs or {})   # монета -> сколько раз промолчать
        self.snapshot = snapshot        # отдавать срез истории на подписку
        self.liq_every = liq_every      # период живых ликвидаций
        self.COINS = tuple(coins) if coins else self.COINS
        self.reject_coins = set(reject_coins)   # ответ ошибкой + разрыв
        self.strict_unknown = strict_unknown    # как HL: неизвестная монета = обрыв
        self.errors = 0
        self.connections = 0
        self.pings = 0
        self.subs = {}                  # монета -> сколько раз просили подписку
        self.acks = 0
        self.last_client_msg = 0.0

    # -- REST: universe -----------------------------------------------------
    async def info(self, request):
        return web.json_response({"universe": [{"name": c} for c in self.COINS]})

    # -- WS -----------------------------------------------------------------
    async def ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self.last_client_msg = time.monotonic()
        opened = time.monotonic()
        coins = set()

        async def killer():
            while not ws.closed:
                await asyncio.sleep(0.1)
                now = time.monotonic()
                if self.idle_kill and now - self.last_client_msg > self.idle_kill:
                    await ws.close(code=1001)
                    return
                if self.close_after and now - opened > self.close_after:
                    await ws.close(code=1006)
                    return

        async def tape():
            """Живая лента: обычные сделки + ликвидация раз в liq_every."""
            next_liq = time.monotonic() + self.liq_every
            n = 0
            while not ws.closed:
                await asyncio.sleep(0.05)
                if not coins:
                    continue
                n += 1
                coin = sorted(coins)[n % len(coins)]
                row = {"coin": coin, "side": "B", "px": "50000", "sz": "0.01",
                       "time": int(time.time() * 1000), "hash": f"h{n}"}
                if time.monotonic() >= next_liq:
                    next_liq = time.monotonic() + self.liq_every
                    row = {"coin": coin, "side": "A", "px": "49990", "sz": "0.5",
                           "time": int(time.time() * 1000), "hash": f"L{n}",
                           "liquidation": {"liquidatedUser": "0xu",
                                           "markPx": 49990.0, "method": "market"}}
                try:
                    await ws.send_json({"channel": "trades", "data": [row]})
                except Exception:
                    return

        k = asyncio.create_task(killer())
        t = asyncio.create_task(tape())
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                self.last_client_msg = time.monotonic()
                try:
                    p = json.loads(msg.data)
                except Exception:
                    continue
                if p.get("method") == "ping":
                    self.pings += 1
                    await ws.send_json({"channel": "pong"})
                    continue
                if p.get("method") != "subscribe":
                    continue
                sub = p.get("subscription") or {}
                coin = sub.get("coin")
                self.subs[coin] = self.subs.get(coin, 0) + 1
                bad = coin in self.reject_coins or (
                    self.strict_unknown and coin not in self.COINS)
                if bad:
                    # так делает HL: ошибка в ответе и закрытие соединения
                    self.errors += 1
                    await ws.send_json({
                        "channel": "subscriptionResponse",
                        "data": {"method": "subscribe", "subscription": sub,
                                 "error": f"no such coin: {coin}"}})
                    await ws.close(code=1000)
                    return ws
                if self.ignore_subs.get(coin, 0) > 0:
                    self.ignore_subs[coin] -= 1     # биржа «потеряла» подписку
                    continue
                coins.add(coin)
                if self.snapshot and self.subs[coin] == 1:
                    # срез последних сделок: старая ликвидация + isSnapshot
                    old = int((time.time() - 600) * 1000)
                    await ws.send_json({"channel": "trades", "isSnapshot": True,
                                        "data": [
                                            {"coin": coin, "side": "A",
                                             "px": "10", "sz": "1", "time": old,
                                             "hash": "snap-old",
                                             "liquidation": {"liquidatedUser": "0xold"}},
                                            {"coin": coin, "side": "B",
                                             "px": "10", "sz": "1", "time": old,
                                             "hash": "snap-plain"}]})
                    # и старая ликвидация БЕЗ метки среза (фильтр по возрасту)
                    await ws.send_json({"channel": "trades", "data": [
                        {"coin": coin, "side": "A", "px": "11", "sz": "2",
                         "time": old, "hash": "plain-old",
                         "liquidation": {"liquidatedUser": "0xold2"}}]})
                self.acks += 1
                await ws.send_json({"channel": "subscriptionResponse",
                                    "data": {"method": "subscribe",
                                             "subscription": sub}})
        finally:
            k.cancel()
            t.cancel()
        return ws


async def start_fake(fake: FakeHL) -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/info", fake.info)
    app.router.add_get("/ws", fake.ws)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()
    return runner


def make_feed(liqs) -> MarketFeed:
    async def on_liq(ev):
        liqs.append(ev)

    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    return feed


def point_at_fake():
    market_feed.HL_WS = f"{BASE}/ws"
    market_feed.HL_REST = BASE


async def wait_until(pred, timeout=10.0, step=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


# ---------------------------------------------------------------------------
# 1) поток стартует сразу + срез истории отсеивается
# ---------------------------------------------------------------------------
async def scenario_fast_start():
    print("1) поток стартует сразу, срез истории в ленту не попадает")
    market_feed.HL_PING_INTERVAL = 0.5
    market_feed.HL_FIRST_PING = 0.5
    fake = FakeHL()
    runner = await start_fake(fake)
    liqs = []
    feed = make_feed(liqs)
    point_at_fake()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        got = await wait_until(lambda: len(liqs) >= 2, 10)
        st = feed.status["hyperliquid"]
        check("ликвидации пошли (>=2 за первые секунды)", got, len(liqs))
        check("статус up()", st.connected is True, st.as_dict())
        check("события посчитаны в статусе", st.events >= 2, st.events)
        await wait_until(lambda: fake.pings >= 2, 5)   # пинг по таймеру
        extra = st.as_dict()
        check("в health видно подписки (2 отправлены / 2 подтверждены)",
              extra.get("hl_subs_sent") == 2 and extra.get("hl_subs_acked") == 2,
              {k: v for k, v in extra.items() if k.startswith("hl_")})
        check("в health видны пинги", extra.get("hl_pings", 0) >= 1,
              extra.get("hl_pings"))
        check("срез истории отсеян (hl_skipped_stale > 0)",
              extra.get("hl_skipped_stale", 0) > 0, extra.get("hl_skipped_stale"))
        # старые ликвидации из среза не должны были долететь
        check("старых ликвидаций из среза в ленте нет",
              all(e["price"] > 100 for e in liqs),
              [(e["symbol"], e["price"]) for e in liqs])
        check("символы/стороны живых ликвидаций верные",
              all(e["symbol"] in ("BTC_USDT", "ETH_USDT") and e["side"] == "LONG"
                  and e["exchange"] == "hyperliquid" for e in liqs), liqs[:3])
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await runner.cleanup()
        market_feed.HL_PING_INTERVAL = 20.0
        market_feed.HL_FIRST_PING = 10.0


# ---------------------------------------------------------------------------
# 2) пинг держит соединение, которое биржа рвёт за молчание
# ---------------------------------------------------------------------------
async def scenario_ping_keeps_alive():
    print("2) биржа рвёт за молчание — пинг по таймеру держит соединение")
    market_feed.HL_PING_INTERVAL = 0.4
    market_feed.HL_FIRST_PING = 0.4
    fake = FakeHL(idle_kill=1.2)   # рвёт, если от клиента тишина > 1.2с
    runner = await start_fake(fake)
    liqs = []
    feed = make_feed(liqs)
    point_at_fake()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        await asyncio.sleep(4.0)   # ~10 окон молчания
        check("соединение не разорвано (пинги держат)",
              not task.done() and feed.status["hyperliquid"].connected,
              {"task_done": task.done(),
               "err": feed.status["hyperliquid"].last_error})
        check("пингов ушло >= 5", fake.pings >= 5, fake.pings)
        # последние pong могут не успеть до отмены задачи — допускаем хвост
        pongs = feed.status["hyperliquid"].extra.get("hl_pongs", 0)
        check("биржа отвечает pong на пинги (хвост до 2 — гонка отмены)",
              pongs >= fake.pings - 2, f"{pongs}/{fake.pings}")
        check("одно соединение на весь срок (без переподключений)",
              fake.connections == 1, fake.connections)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await runner.cleanup()
        market_feed.HL_PING_INTERVAL = 20.0
        market_feed.HL_FIRST_PING = 10.0


# ---------------------------------------------------------------------------
# 3) неподтверждённая подписка повторяется
# ---------------------------------------------------------------------------
async def scenario_sub_retry():
    print("3) биржа «потеряла» подписку — повторяем и встаём")
    market_feed.HL_SUB_ACK_WAIT = 0.3
    market_feed.HL_SUB_GAP = 0.02
    fake = FakeHL(ignore_subs={"ETH": 1})
    runner = await start_fake(fake)
    liqs = []
    feed = make_feed(liqs)
    point_at_fake()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        ready = await wait_until(
            lambda: feed.status["hyperliquid"].extra.get("hl_subs_acked") == 2, 8)
        check("обе монеты в итоге подтверждены", ready,
              feed.status["hyperliquid"].extra)
        check("по ETH подписку пришлось повторить",
              fake.subs.get("ETH", 0) >= 2, fake.subs)
        check("BTC подтверждён с первой попытки",
              fake.subs.get("BTC", 0) == 1, fake.subs)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await runner.cleanup()
        market_feed.HL_SUB_ACK_WAIT = 4.0
        market_feed.HL_SUB_GAP = 0.08


# ---------------------------------------------------------------------------
# 4) разрыв → супервайзер переподключается → поток продолжается
# ---------------------------------------------------------------------------
async def scenario_reconnect():
    print("4) разрыв соединения: супервайзер переподключается, поток живёт")
    market_feed.HL_SUB_GAP = 0.02
    fake = FakeHL(close_after=1.0)   # биржа рвёт каждое соединение через 1с
    runner = await start_fake(fake)
    liqs = []
    feed = make_feed(liqs)
    point_at_fake()
    sup = asyncio.create_task(
        feed._supervise("hyperliquid", feed._hyperliquid_liquidations,
                        base_delay=0.2, stable_uptime=999.0))
    try:
        again = await wait_until(lambda: fake.connections >= 2, 10)
        check("второе соединение установлено", again, fake.connections)
        flowing = await wait_until(lambda: len(liqs) >= 2, 10)
        check("ликвидации идут и после переподключения", flowing, len(liqs))
        check("в статусе видны переподключения",
              feed.status["hyperliquid"].reconnects >= 1,
              feed.status["hyperliquid"].reconnects)
    finally:
        feed._stop.set()
        sup.cancel()
        try:
            await sup
        except (asyncio.CancelledError, Exception):
            pass
        await runner.cleanup()
        market_feed.HL_SUB_GAP = 0.08


# ---------------------------------------------------------------------------
# 5) сквозной прогон через настоящий server.py
# ---------------------------------------------------------------------------
async def scenario_server_e2e():
    print("5) сквозной прогон: server.py + псевдо-HL → /api/health, "
          "/api/liquidations")
    os.environ["LIQSCOPE_EXCHANGES"] = "hyperliquid"
    os.environ["LIQSCOPE_SYMBOLS_LIMIT"] = "8"
    os.environ["LIQSCOPE_HISTORY_FILE"] = "0"
    os.environ["LIQSCOPE_HL_WS"] = f"{BASE}/ws"
    os.environ["LIQSCOPE_HL_REST"] = BASE
    os.environ["LIQSCOPE_HL_SUB_GAP_MS"] = "20"
    market_feed.HL_WS = f"{BASE}/ws"
    market_feed.HL_REST = BASE
    market_feed.HL_PING_INTERVAL = 2.0
    market_feed.HL_FIRST_PING = 1.0

    import server as srv
    import uvicorn

    fake = FakeHL(liq_every=0.3)
    runner = await start_fake(fake)
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8808,
                            log_level="warning")
    srv_runner = uvicorn.Server(config)
    task = asyncio.create_task(srv_runner.serve())
    try:
        await wait_until(lambda: srv_runner.started, 15)
        async with aiohttp.ClientSession() as s:
            async def get(path):
                async with s.get(f"http://127.0.0.1:8808{path}",
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
                    return await r.json(content_type=None)

            healthy = False
            hl = {}
            for _ in range(60):
                h = await get("/api/health")
                hl = (h.get("sources") or {}).get("hyperliquid") or {}
                if hl.get("connected") and hl.get("hl_subs_acked"):
                    healthy = True
                    break
                await asyncio.sleep(0.5)
            check("health: hyperliquid подключён", healthy, hl)
            check("health: подписки подтверждены",
                  hl.get("hl_subs_acked", 0) >= 2, hl)
            for _ in range(30):
                h = await get("/api/health")
                hl = (h.get("sources") or {}).get("hyperliquid") or {}
                if hl.get("hl_pongs", 0) >= 1:
                    break
                await asyncio.sleep(0.5)
            check("health: пинг ушёл и биржа ответила pong",
                  hl.get("hl_pings", 0) >= 1 and hl.get("hl_pongs", 0) >= 1, hl)

            rows, got = {}, []
            for _ in range(40):
                rows = await get("/api/liquidations?exchange=hyperliquid&limit=50")
                got = rows.get("liquidations") or []
                if got:
                    break
                await asyncio.sleep(0.5)
            check("в /api/liquidations есть ликвидации с hyperliquid",
                  bool(got), rows)
            if got:
                e = got[0]
                check("событие полное (символ/сторона/цена/сумма)",
                      e.get("symbol") and e.get("side") in ("SELL", "BUY")
                      and float(e.get("price") or 0) > 0
                      and float(e.get("usd") or 0) > 0, e)
    finally:
        srv_runner.should_exit = True
        try:
            await asyncio.wait_for(task, 15)
        except (asyncio.TimeoutError, Exception):
            task.cancel()
        await runner.cleanup()


# ---------------------------------------------------------------------------
# 6) регистр имени монеты: kPEPE, а не KPEPE
# ---------------------------------------------------------------------------
async def scenario_coin_case():
    print("6) дешёвые токены: подписка на kPEPE (как у биржи), а не KPEPE")
    market_feed.HL_SUB_GAP = 0.02
    fake = FakeHL(coins=("BTC", "kPEPE"), strict_unknown=True)
    runner = await start_fake(fake)
    liqs = []
    feed = make_feed(liqs)
    feed.symbols = ["BTC_USDT", "PEPE_USDT"]
    point_at_fake()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        ready = await wait_until(
            lambda: feed.status["hyperliquid"].extra.get("hl_subs_acked") == 2, 8)
        check("обе монеты подтверждены (kPEPE принят биржей)", ready,
              feed.status["hyperliquid"].extra)
        check("подписка ушла ровно на kPEPE (регистр биржи)",
              "kPEPE" in fake.subs, list(fake.subs))
        check("KPEPE в подписках нет — биржа не рвала сокет",
              "KPEPE" not in fake.subs and fake.errors == 0,
              {"subs": list(fake.subs), "errors": fake.errors})
        check("соединение живо", not task.done(), fake.connections)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await runner.cleanup()
        market_feed.HL_SUB_GAP = 0.08


# ---------------------------------------------------------------------------
# 7) битая монета отключается, поток поднимается без неё
# ---------------------------------------------------------------------------
async def scenario_bad_coin_banned():
    print("7) биржа не знает монету: отключаем её и поднимаем остальной поток")
    market_feed.HL_SUB_GAP = 0.02
    fake = FakeHL(reject_coins={"ETH"})
    runner = await start_fake(fake)
    liqs = []
    feed = make_feed(liqs)
    point_at_fake()
    sup = asyncio.create_task(
        feed._supervise("hyperliquid", feed._hyperliquid_liquidations,
                        base_delay=0.2, stable_uptime=999.0))
    try:
        ok = await wait_until(
            lambda: "ETH" in feed.hl_banned_coins
            and feed.status["hyperliquid"].extra.get("hl_subs_acked") == 1
            and feed.status["hyperliquid"].connected, 12)
        extra = feed.status["hyperliquid"].extra
        check("битая монета отключена и видна в health", ok,
              {"banned": sorted(feed.hl_banned_coins), **extra})
        check("в health список отключённых монет",
              extra.get("hl_coins_banned") == ["ETH"], extra.get("hl_coins_banned"))
        alive = await asyncio.sleep(2.0) or not sup.done()
        check("после отключения битой монеты соединение живёт", alive)
        flowing = await wait_until(lambda: len(liqs) >= 1, 8)
        check("ликвидации по остальным монетам идут", flowing, len(liqs))
    finally:
        feed._stop.set()
        sup.cancel()
        try:
            await sup
        except (asyncio.CancelledError, Exception):
            pass
        await runner.cleanup()
        market_feed.HL_SUB_GAP = 0.08


async def main():
    await scenario_fast_start()
    await scenario_ping_keeps_alive()
    await scenario_sub_retry()
    await scenario_reconnect()
    await scenario_coin_case()
    await scenario_bad_coin_banned()
    await scenario_server_e2e()


if __name__ == "__main__":
    print("поток ликвидаций Hyperliquid (локальная псевдо-биржа)")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
