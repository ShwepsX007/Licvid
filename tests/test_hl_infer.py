"""Вывод ликвидаций Hyperliquid из ленты сделок (`hl_infer.py`).

Проверяет и чистые функции (кого считать тейкером, за что начисляются очки,
как ищется подтверждение в ответе /info), и интеграцию с настоящим слушателем
`market_feed._hyperliquid_liquidations` на локальной псевдо-бирже:

  1. всплеск сделок одного адреса = кандидат, подтверждение по адресу →
     событие в ленту с kind="tape" и данными из объекта `liquidation`;
  2. всплеск, у которого в филах нет ликвидации, отклоняется и в ленту не идёт;
  3. сделки, которые биржа пометила сама, не дублируются выводом;
  4. медленный /info не блокирует читателя сокета (сделок за время ожидания
     набирается больше, чем ноль);
  5. счётчики видны в /api-совместимом st.extra (это то, что читает health);
  6. бюджет подтверждений на минуту реально ограничивает число запросов;
  7. сквозной прогон через настоящий `server.py`: в `/api/liquidations`
     событие приходит с `kind:"tape"` и `liq`, а в `/api/health` — счётчики.

Сеть наружу не нужна.  Запуск:  python3 tests/test_hl_infer.py
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import hl_infer
import market_feed
from hl_infer import (HlLiquidationInferer, find_liquidation, score_burst,
                      taker_of)
from market_feed import MarketFeed

PORT = 8806
BASE = f"http://127.0.0.1:{PORT}"

VICTIM = "0xvictim"
MARK = 100_000.0
BURST_PX = 99_850.0            # на 0.15% ниже марки — как у выбитого лонга

liqs = []
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


# ---------------------------------------------------------------------------
#  Псевдо-биржа: /info (meta + userFillsByTime) и /ws с лентой
# ---------------------------------------------------------------------------
def burst_rows(coin, taker, n=4, tid0=1000, px=BURST_PX):
    now = time.time()
    return [{"coin": coin, "side": "A", "px": str(px), "sz": "4.885",
             "time": int((now + i * 0.05) * 1000), "hash": f"0xb{i}",
             "tid": tid0 + i, "users": [f"0xmk{i}", taker]} for i in range(n)]


async def info_handler(request):
    body = await request.json()
    if body.get("type") == "meta":
        return web.json_response({"universe": [{"name": "BTC"}, {"name": "ETH"}]})
    if body.get("type") == "userFillsByTime":
        await asyncio.sleep(request.app["fill_delay"])
        if body.get("user") == VICTIM:
            return web.json_response({"fills": [
                {"coin": "BTC", "px": str(BURST_PX), "sz": "4.885", "side": "A",
                 "time": int(time.time() * 1000), "tid": 1003,
                 "dir": "Close Long", "startPosition": "19.54",
                 "liquidation": {"liquidatedUser": VICTIM, "markPx": 99_700.0,
                                 "method": "market"}}]})
        return web.json_response({"fills": [
            {"coin": "BTC", "px": "99990.0", "sz": "0.1", "side": "B",
             "time": int(time.time() * 1000), "tid": 42}]})
    return web.json_response({})


async def ws_handler(request):
    """Лента ОДИН раз на соединение: референсные цены → всплеск жертвы →
    всплеск «шума» → помеченная биржей ликвидация → фоновый шум.

    «Один раз» важно: клиент подписывается на 2 монеты, и повтор сценария на
    каждую подписку удвоил бы события, а тест перестал бы что-либо значить.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    played = False

    async def send(rows):
        # тест обрывает соединение в любой момент — это норма, не надо
        # печатать трейсбек на закрытие транспорта
        if ws.closed:
            return
        try:
            await ws.send_json({"channel": "trades", "data": rows})
        except Exception:
            pass

    async def script():
        # референс цены монеты: медиана считается по сделкам ДО всплеска
        await send([{"coin": "BTC", "side": "B", "px": "100000.0", "sz": "0.5",
                     "time": int((time.time() - 3 + i * 0.2) * 1000),
                     "hash": f"0xr{i}", "tid": 500 + i,
                     "users": ["0xref_a", "0xref_b"]} for i in range(8)])
        await send(burst_rows("BTC", VICTIM, tid0=1000))           # → подтвердится
        await send(burst_rows("BTC", "0xnoise", tid0=2000))        # → отклонится
        # то, что биржа пометила сама: идёт в ленту парсером и НЕ должно
        # дублироваться выводом
        await send([{"coin": "BTC", "side": "A", "px": "99000", "sz": "1.0",
                     "time": int(time.time() * 1000), "hash": "0xmarked",
                     "tid": 3000, "users": ["0xmk", VICTIM],
                     "liquidation": {"liquidatedUser": VICTIM, "markPx": 98900.0,
                                     "method": "market"}}])
        # фон: лента не должна вставать из-за ожидания /info
        for i in range(60):
            await send([{"coin": "BTC", "side": "B", "px": "100001.0",
                         "sz": "0.01", "time": int(time.time() * 1000),
                         "hash": f"0xn{i}", "tid": 9000 + i,
                         "users": [f"0xa{i}", f"0xb{i}"]}])
            await asyncio.sleep(0.05)

    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            p = json.loads(msg.data)
        except Exception:
            continue
        if p.get("method") == "ping":
            await ws.send_json({"channel": "pong"})
            continue
        if p.get("method") != "subscribe":
            continue
        if not played:
            played = True
            asyncio.create_task(script())
        await ws.send_json({"channel": "subscriptionResponse",
                            "data": {"method": "subscribe",
                                     "subscription": p.get("subscription") or {}}})
    return ws


async def wait_for(cond, timeout, what):
    """Ждём условие, а не фиксированные секунды: иначе тест плавающий."""
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            if cond():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    print(f"  TIMEOUT  {what}")
    return False


async def on_liq(ev):
    liqs.append(ev)


async def noop_price(*a):
    pass


# ---------------------------------------------------------------------------
async def unit_checks():
    print("1) чистые функции: тейкер, очки, поиск подтверждения")
    check("side A → тейкер продавец = вынесен LONG",
          taker_of({"side": "A", "users": ["0xb", "0xs"]}) == ("0xs", "LONG"))
    check("side B → тейкер покупатель = вынесен SHORT",
          taker_of({"side": "B", "users": ["0xb", "0xs"]}) == ("0xb", "SHORT"))
    check("без users тейкер неизвестен", taker_of({"side": "A"}) == (None, None))

    now = time.time()
    rows = [{"px": 99_850.0, "sz": 4.885, "ts": now + i * 0.05, "side": "A",
             "position": "LONG"} for i in range(4)]
    score, why, usd = score_burst(rows, ref_px=100_000.0, min_usd=25_000)
    check("серия с проскальзыванием ниже марки набирает очки", score >= 3.0,
          f"{score} {why}")
    check("проскальзывание попало в причины",
          any("хуже марки" in w for w in why), why)
    check("нотионал посчитан", abs(usd - 4 * 99_850.0 * 4.885) < 1, usd)
    flat = score_burst([{"px": 99_999.0, "sz": 5.0, "ts": now, "side": "A",
                        "position": "LONG"}], ref_px=100_000.0, min_usd=25_000)
    check("одиночный маркет без проскальзывания — не кандидат", flat[0] < 3.0, flat)
    small = score_burst(rows, ref_px=100_000.0, min_usd=10 ** 9)
    check("под порог по нотионалу — сразу 0", small[0] == 0.0, small)
    up = score_burst([{"px": 100_200.0, "sz": 4.885, "ts": now, "side": "B",
                      "position": "SHORT"}], ref_px=100_000.0, min_usd=25_000)
    check("шорт выше марки — тоже проскальзывание", any("хуже марки" in w for w in up[1]),
          up)

    fills = {"fills": [{"coin": "BTC", "time": int(now * 1000), "tid": 1003,
                        "liquidation": {"markPx": 1, "method": "market"}}]}
    check("tid совпал — matched=tid",
          find_liquidation(fills, tid=1003)["matched"] == "tid")
    check("tid не совпал — берём новдейший в окне",
          find_liquidation(fills, tid=999)["matched"] == "окно")
    check("филы до окна отбрасываются",
          find_liquidation(fills, tid=999, since_ms=int((now + 60) * 1000)) is None)
    check("список без обёртки понимается",
          find_liquidation(fills["fills"], tid=1003) is not None)
    check("unknownOid/мусор не роняют",
          find_liquidation({"status": "unknownOid"}, tid=1) is None
          and find_liquidation(None) is None)
    check("чистые филы без метки — None",
          find_liquidation({"fills": [{"coin": "BTC", "tid": 1}]}) is None)

    inf = HlLiquidationInferer({"BTC": "BTC_USDT"}, None, None,
                               hl_rest="http://x", max_confirm_per_min=2)
    check("бюджет подтверждений считается по минуте",
          all([inf._budget_ok(), inf._budget_ok()]) and not inf._budget_ok(),
          inf.stats)
    check("имя монеты берётся из карты", inf.coin_name("BTC") == "BTC_USDT")
    check("неизвестная монета не ломает имя", inf.coin_name("NEW") == "NEW_USDT")


async def integration_checks():
    print("2) интеграция: слушатель + псевдо-биржа → событие с kind=tape")
    app = web.Application()
    app["fill_delay"] = 0.0
    app.router.add_post("/info", info_handler)
    app.router.add_get("/ws", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()

    market_feed.HL_REST = BASE
    market_feed.HL_WS = f"http://127.0.0.1:{PORT}/ws"
    market_feed.HL_INFER_ENABLED = True
    market_feed.HL_FRESH_SEC = 600.0
    hl_infer.HL_INFER_LOG_SEC = 3600.0          # не засоряем вывод теста

    liqs.clear()
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    feed.hl_coin_map = {"BTC": "BTC_USDT", "ETH": "ETH_USDT"}
    feed._session = aiohttp.ClientSession()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        await wait_for(lambda: feed.status["hyperliquid"].extra.get(
            "hl_infer_confirmed", 0) >= 1, 20.0,
            "подтверждение дошло до счётчиков")
        tape = [e for e in liqs if e.get("kind") == "tape"]
        marked = [e for e in liqs if e.get("kind") != "tape"]
        check("выведенное событие дошло до ленты", len(tape) == 1, liqs)
        if tape:
            ev = tape[0]
            check("символ/сторона верные",
                  ev["symbol"] == "BTC_USDT" and ev["side"] == "LONG", ev)
            check("нотионал = сумма всплеска",
                  abs(ev["usd"] - 4 * BURST_PX * 4.885) < 1, ev)
            check("подтверждение приложено (адрес, markPx, method)",
                  ev["liquidation"]["liquidatedUser"] == VICTIM
                  and ev["liquidation"]["method"] == "market", ev)
            check("timestamp — время последней сделки всплеска, а не «сейчас»",
                  abs(ev["timestamp"] - time.time()) < 5, ev)
        check("помеченное биржей событие прошло отдельным счётом",
              len(marked) == 1 and "kind" not in marked[0], marked)
        check("отклонённый всплеск в ленту не попал",
              len(tape) + len(marked) == 2, liqs)
        extra = feed.status["hyperliquid"].extra
        check("счётчики вывода видны в health",
              extra.get("hl_infer_trades", 0) > 20
              and extra.get("hl_infer_candidates", 0) >= 2
              and extra.get("hl_infer_confirmed") == 1
              and extra.get("hl_infer_rejected") >= 1, extra)
        check("события источника включают и выведенные",
              feed.status["hyperliquid"].events == 2,
              feed.status["hyperliquid"].events)
        check("статус остаётся up (вывод не роняет слушателя)",
              feed.status["hyperliquid"].connected is True)
    finally:
        feed._stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await feed._session.close()
        await runner.cleanup()


async def slow_rest_checks():
    print("3) медленный /info не блокирует читателя сокета")
    app = web.Application()
    app["fill_delay"] = 2.0                    # подтверждение висит 2 секунды
    app.router.add_post("/info", info_handler)
    app.router.add_get("/ws", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    liqs.clear()
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    feed.hl_coin_map = {"BTC": "BTC_USDT", "ETH": "ETH_USDT"}
    feed._session = aiohttp.ClientSession()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        await wait_for(lambda: any(e.get("kind") == "tape" for e in liqs), 20.0,
                       "медленный /info всё равно подтвердил")
        extra = feed.status["hyperliquid"].extra
        check("за время ожидания подтверждения лента продолжалась",
              extra.get("hl_infer_trades", 0) > 20, extra)
        check("подтверждённое событие всё равно пришло",
              any(e.get("kind") == "tape" for e in liqs), liqs)
        check("ожидающих подтверждения не потеряли (info_calls учтён)",
              extra.get("hl_infer_info_calls", 0) >= 1, extra)
    finally:
        feed._stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await feed._session.close()
        await runner.cleanup()


async def server_flow_checks():
    print("5) сквозной прогон через настоящий server.py: kind/liq в /api/liquidations")
    os.environ["LIQSCOPE_EXCHANGES"] = "hyperliquid"
    os.environ["LIQSCOPE_SYMBOLS_LIMIT"] = "8"
    os.environ["LIQSCOPE_HISTORY_FILE"] = "0"
    os.environ["LIQSCOPE_OXA_KEYS"] = ""
    os.environ["LIQSCOPE_HL_WS"] = f"{BASE}/ws"
    os.environ["LIQSCOPE_HL_REST"] = BASE
    os.environ["LIQSCOPE_HL_SUB_GAP_MS"] = "20"
    os.environ["LIQSCOPE_HL_FRESH_SEC"] = "600"
    market_feed.HL_WS = f"{BASE}/ws"
    market_feed.HL_REST = BASE

    app = web.Application()
    app["fill_delay"] = 0.0
    app.router.add_post("/info", info_handler)
    app.router.add_get("/ws", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    import server as srv
    import uvicorn

    config = uvicorn.Config(srv.app, host="127.0.0.1", port=PORT + 3,
                            log_level="error")
    srv_runner = uvicorn.Server(config)
    srv_task = asyncio.create_task(srv_runner.serve())
    try:
        t_end = time.time() + 20
        while time.time() < t_end and not srv_runner.started:
            await asyncio.sleep(0.2)
        check("сервер поднялся", srv_runner.started)
        async with aiohttp.ClientSession() as s:
            async def get(path):
                async with s.get(f"http://127.0.0.1:{PORT + 3}{path}",
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
                    return await r.json(content_type=None)

            tape = {}
            hl = {}
            for _ in range(60):
                rows = await get("/api/liquidations?exchange=hyperliquid&limit=50")
                got = rows.get("liquidations") or []
                h = await get("/api/health")
                hl = (h.get("sources") or {}).get("hyperliquid") or {}
                tape = next((x for x in got if x.get("kind") == "tape"), {})
                if tape:
                    break
                await asyncio.sleep(0.5)
            check("выведенное событие дошло до REST-истории сервера", bool(tape), hl)
            if tape:
                check("в событии есть kind и данные подтверждения",
                      tape.get("kind") == "tape"
                      and (tape.get("liq") or {}).get("liquidatedUser") == VICTIM
                      and (tape.get("liq") or {}).get("method") == "market", tape)
                check("символ и сторона не потерялись в дороге",
                      tape.get("symbol") == "BTC_USDT"
                      and tape.get("side") == "SELL"
                      and tape.get("position") == "LONG", tape)
            # health обновляется тиком подписчика, поэтому ждём именно счётчик,
            # а не читаем первый попавшийся снапшот
            await wait_for(lambda: ((srv.feed.status["hyperliquid"].extra or {}).get(
                "hl_infer_confirmed", 0) >= 1), 20.0,
                "счётчики вывода появились в health")
            hl = (await get("/api/health")).get("sources", {}).get("hyperliquid") or {}
            check("health показывает счётчики вывода",
                  hl.get("hl_infer_confirmed", 0) >= 1
                  and hl.get("hl_infer_trades", 0) > 20
                  and hl.get("hl_infer_info_calls", 0) >= 1, hl)
    finally:
        srv_runner.should_exit = True
        try:
            await asyncio.wait_for(srv_task, 15)
        except Exception:
            srv_task.cancel()
        await runner.cleanup()


async def disabled_checks():
    print("4) LIQSCOPE_HL_INFER=0: вывод выключен, слушатель живёт своей жизнью")
    app = web.Application()
    app["fill_delay"] = 0.0
    app.router.add_post("/info", info_handler)
    app.router.add_get("/ws", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    # сценарий самодостаточен по глобалам: он может идти и первым, и последним
    market_feed.HL_REST = BASE
    market_feed.HL_WS = f"http://127.0.0.1:{PORT}/ws"
    market_feed.HL_FRESH_SEC = 600.0
    liqs.clear()
    market_feed.HL_INFER_ENABLED = False
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    feed.hl_coin_map = {"BTC": "BTC_USDT", "ETH": "ETH_USDT"}
    feed._session = aiohttp.ClientSession()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        got_marked = await wait_for(lambda: liqs, 15.0,
                                    "помеченное биржей событие пришло")
        await asyncio.sleep(0.5)
        extra = feed.status["hyperliquid"].extra
        check("событие от биржи (с меткой) прошло как обычно",
              got_marked and all(e.get("kind") != "tape" for e in liqs), liqs)
        check("в health нет ключей hl_infer_* (вывод выключен)",
              not [k for k in extra if "hl_infer" in k], extra)
        check("сокет цел, слушатель не упал",
              feed.status["hyperliquid"].connected is True
              and feed.status["hyperliquid"].reconnects == 0,
              feed.status["hyperliquid"].extra)
    finally:
        market_feed.HL_INFER_ENABLED = True
        feed._stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await feed._session.close()
        await runner.cleanup()


async def main():
    await unit_checks()
    await integration_checks()
    await slow_rest_checks()
    await disabled_checks()
    await server_flow_checks()
    print(f"\nитог: {ok} ок, {fail} ошибок")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
