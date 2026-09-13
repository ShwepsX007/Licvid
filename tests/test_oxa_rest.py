"""
0xArchive: опрос REST как рабочий путь к ликвидациям Hyperliquid.

Замер на боевом сервере 14.09.2026 показал, что живой WS-канал liquidations
не отдаёт ничего (за 1800 с — 3 кадра subscribed и 72 pong, ни одного кадра
данных), тогда как REST вернул 1118 ликвидаций за сутки по BTC+ETH с самой
свежей «0 секунд назад». Поэтому ликвидации берутся опросом REST.

Поля реального ответа (ровно те, что напечатал боевой зонд):
    closed_pnl, coin, direction, liquidated_user, liquidator_user,
    mark_price, price, side, size, symbol, timestamp, trade_id, tx_hash

Важно: поля is_liquidation в REST нет вовсе — оно есть только в WS-канале
trades. Парсер без флага all_rows такие строки отбрасывает, и этот тест
проверяет обе ветки, потому что именно на этом первая версия молча
возвращала пустой список.

Также проверяются: сторона из direction (Long/Short), дедупликация по
trade_id при перекрывающихся окнах опроса, распределение монет между
несколькими ключами, учёт запросов и прогноз расхода кредитов, поведение на
HTTP-ошибке.

Сеть наружу не нужна.  Запуск:  python3 tests/test_oxa_rest.py
"""

import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import market_feed
from market_feed import MarketFeed, oxa_is_liquidation, parse_oxa_liquidations

PORT = 8851

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


async def wait_until(cond, timeout=10.0, step=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def liq_row(coin, price, size, direction, trade_id, sec_ago=1.0, side="A"):
    """Строка ровно в форме боевого REST-ответа."""
    return {"closed_pnl": "-120.5", "coin": coin, "direction": direction,
            "liquidated_user": "0xaaa", "liquidator_user": "0xbbb",
            "mark_price": str(price + 100), "price": str(price),
            "side": side, "size": str(size), "symbol": coin,
            "timestamp": int((time.time() - sec_ago) * 1000),
            "trade_id": trade_id, "tx_hash": "0xdead" + trade_id}


# ---------------------------------------------------------------------------
class FakeOxaRest:
    """Псевдо-REST 0xArchive: /v1/hyperliquid/liquidations/{coin}."""

    def __init__(self, good_keys=(), rows=None, fail_coins=()):
        self.good_keys = set(good_keys)
        self.rows = rows or {}              # coin -> список строк
        self.fail_coins = set(fail_coins)
        self.hits = {}                      # (ключ, монета) -> число запросов
        self.windows = []                   # какие окна запрашивали

    async def handle(self, request):
        key = request.headers.get("X-API-Key") or ""
        coin = request.match_info.get("coin", "")
        if key not in self.good_keys:
            return web.json_response({"error": "unauthorized"}, status=401)
        self.hits[(key[-6:], coin)] = self.hits.get((key[-6:], coin), 0) + 1
        if coin in self.fail_coins:
            return web.json_response({"error": "boom"}, status=500)
        self.windows.append((coin, request.query.get("start"),
                             request.query.get("end")))
        rows = self.rows.get(coin, [])
        return web.json_response({"data": rows,
                                  "meta": {"next_cursor": None}})


def make_feed(liqs, symbols=("BTC_USDT", "ETH_USDT", "SOL_USDT", "PEPE_USDT")):
    async def on_liq(ev):
        liqs.append(ev)

    async def on_price(*a):
        pass

    feed = MarketFeed(on_liquidation=on_liq, on_price=on_price, exchanges=[])
    feed.symbols = list(symbols)

    async def _fake_universe(session=None):
        return {"BTC", "ETH", "SOL", "kPEPE"}
    feed._hyperliquid_load_universe = _fake_universe
    return feed


# ---------------------------------------------------------------------------
def test_parser_on_real_shape():
    print("1) парсер на реальных полях REST")
    cmap = {"BTC": "BTC_USDT", "kPEPE": "PEPE_USDT"}
    row = liq_row("BTC", 59900, 0.5, "Long", "t1")
    body = {"data": [row], "meta": {"next_cursor": None}}

    check("без all_rows REST-строка отбрасывается (там нет is_liquidation)",
          parse_oxa_liquidations(body, cmap) == [])
    res = parse_oxa_liquidations(body, cmap, all_rows=True)
    check("с all_rows REST-строка разбирается", len(res) == 1, res)
    ev = res[0] if res else {}
    check("цена из price", ev.get("price") == 59900.0, ev)
    check("объём из size", ev.get("qty") == 0.5, ev)
    check("сумма в USD", abs(ev.get("usd", 0) - 29950.0) < 1e-6, ev)
    check("мс -> секунды", abs(ev.get("ts", 0) - row["timestamp"] / 1000) < 1e-6, ev)
    check("direction=Long -> LONG", ev.get("side") == "LONG", ev)
    check("trade_id сохранён для дедупликации", ev.get("id") == "t1", ev)
    check("direction=Short -> SHORT",
          parse_oxa_liquidations(
              {"data": [liq_row("BTC", 1, 1, "Short", "t2")]}, cmap,
              all_rows=True)[0]["side"] == "SHORT")
    check("без direction резерв по стороне тейкера A -> LONG",
          parse_oxa_liquidations(
              {"data": [dict(liq_row("BTC", 1, 1, "", "t3"), side="A")]},
              cmap, all_rows=True)[0]["side"] == "LONG")
    check("дешёвый токен kPEPE",
          parse_oxa_liquidations(
              {"data": [liq_row("kPEPE", 0.00001, 1000, "Long", "t4")]},
              cmap, all_rows=True)[0]["symbol"] == "PEPE_USDT")
    check("строка без полей ликвидации не проходит even с all_rows",
          oxa_is_liquidation({"coin": "BTC", "price": "1"}, True) is False)
    check("WS-строка с is_liquidation проходит без all_rows",
          oxa_is_liquidation({"is_liquidation": True}, False) is True)
    check("старая строка отсеивается",
          parse_oxa_liquidations(
              {"data": [liq_row("BTC", 1, 1, "Long", "t5", sec_ago=99999)]},
              cmap, now=time.time(), max_age=300, all_rows=True) == [])


async def test_poll(fake, keys):
    print("3) опрос REST двумя ключами")
    market_feed.OXA_REST = f"http://127.0.0.1:{PORT}/v1/hyperliquid"
    market_feed.OXA_MODE = "rest"
    market_feed.OXA_POLL_SEC = 0.4
    market_feed.OXA_COINS_PER_KEY = 2
    # при опросе раз в 0,4 с реальный бюджет дал бы 0 монет и кламп урезал бы
    # per_key до 1 — здесь проверяем сам опрос, поэтому бюджет поднимаем
    market_feed.OXA_FREE_CREDITS = 10 ** 9
    # Перекрытие ОБЯЗАНО быть меньше шага между монетами (0,4/2 = 0,2 с),
    # иначе дыры от общего курсора не видно: в бою шаг 60 с при перекрытии
    # 30 с, то есть перекрытие вдвое меньше шага.
    market_feed.OXA_OVERLAP_SEC = 0.05
    market_feed.OXA_FIRST_WINDOW_SEC = 60
    os.environ["LIQSCOPE_OXA_KEYS"] = ",".join(keys)

    liqs = []
    feed = make_feed(liqs)
    await feed.start()
    try:
        check("ликвидации дошли до ленты",
              await wait_until(lambda: len(liqs) >= 2), len(liqs))
        ev = liqs[0] if liqs else {}
        check("в ленте биржа — hyperliquid",
              ev.get("exchange") == "hyperliquid", ev)
        check("событие полное", ev.get("symbol") and ev.get("usd", 0) > 0
              and ev.get("side") in ("LONG", "SHORT"), ev)
        extra = feed.status["oxa"].extra
        check("health: режим rest", extra.get("oxa_mode") == "rest", extra)
        check("health: запросы считаются",
              extra.get("oxa_requests", 0) >= 2, extra)
        check("health: ликвидации считаются",
              extra.get("oxa_liquidations", 0) >= 2, extra)
        check("health: бюджет кредитов показан на все ключи",
              extra.get("oxa_credits_budget")
              == market_feed.OXA_FREE_CREDITS * 2, extra)
        # прогноз считается только набрав полный цикл опроса — на первых
        # секундах его ещё нет, и это честно
        check("health: прогноз на старте ещё не выдаётся",
              extra.get("oxa_fits_budget") is None, extra)
        check("обе монеты каждого ключа опрошены",
              await wait_until(lambda: len(fake.hits) >= 4),
              dict(fake.hits))
        check("монеты поделены между ключами",
              len({k for k, _ in fake.hits}) == 2,
              {k for k, _ in fake.hits})
        # дедупликация: строки те же, окна перекрываются
        check("повторы отсеиваются по trade_id",
              await wait_until(lambda: feed.status["oxa"].extra.get(
                  "oxa_dupes", 0) >= 1),
              feed.status["oxa"].extra)
        n_liq = len(liqs)
        await asyncio.sleep(1.2)
        check("лента не растёт от повторов", len(liqs) == n_liq,
              (n_liq, len(liqs)))
        check("окно опроса передаётся в миллисекундах",
              all(str(w[1]).isdigit() and len(str(w[1])) >= 12
                  for w in fake.windows[:3]), fake.windows[:3])
        # У каждой монеты окна обязаны идти встык с перекрытием. Общий курсор
        # на весь ключ давал дыры: монеты опрашиваются вразнобой, поэтому
        # после опроса второй монеты курсор уезжал вперёд и следующая монета
        # начинала запрос уже оттуда — терялись целые интервалы.
        gaps = []
        for coin in {w[0] for w in fake.windows}:
            ws = sorted((int(a), int(b)) for c, a, b in fake.windows
                        if c == coin and a and b)
            for (a1, b1), (a2, b2) in zip(ws, ws[1:]):
                if a2 > b1:
                    gaps.append((coin, b1, a2, a2 - b1))
        check("в окнах опроса нет дыр ни у одной монеты", gaps == [], gaps)
    finally:
        await feed.stop()
        os.environ.pop("LIQSCOPE_OXA_KEYS", None)


def test_budget():
    """Бюджет кредитов: сколько монет влезает и урезается ли настройка."""
    print("2) бюджет кредитов и кламп")
    month = market_feed.OXA_MONTH_SEC
    check("месяц — 30 суток", month == 30 * 24 * 3600, month)
    # Free: 50 000 кредитов/мес на ключ, 1 запрос = минимум 1 кредит
    check("5 монет раз в 60 с — это 216 000 кредитов",
          market_feed.oxa_month_credits(5, 60) == 216000,
          market_feed.oxa_month_credits(5, 60))
    check("дефолт (2 монеты, 120 с) влезает в 50 000",
          market_feed.oxa_month_credits(
              market_feed.OXA_COINS_PER_KEY, market_feed.OXA_POLL_SEC)
          <= market_feed.OXA_FREE_CREDITS,
          market_feed.oxa_month_credits(market_feed.OXA_COINS_PER_KEY,
                                        market_feed.OXA_POLL_SEC))
    check("раз в 60 с — влезает 1 монета",
          market_feed.oxa_coins_for_budget(60) == 1,
          market_feed.oxa_coins_for_budget(60))
    check("раз в 300 с — влезает 5 монет",
          market_feed.oxa_coins_for_budget(300) == 5,
          market_feed.oxa_coins_for_budget(300))
    check("раз в 30 с — не влезает ничего",
          market_feed.oxa_coins_for_budget(30) == 0,
          market_feed.oxa_coins_for_budget(30))
    check("нулевой опрос не роняет расчёт",
          market_feed.oxa_coins_for_budget(0) == 0
          and market_feed.oxa_month_credits(3, 0) == 0.0)
    # при каком интервале желаемое число монет влезло бы в бюджет
    check("5 монет влезут при опросе раз в 259 с",
          int(round(month * 5 / market_feed.OXA_FREE_CREDITS)) == 259,
          int(round(month * 5 / market_feed.OXA_FREE_CREDITS)))

    # Окно свежести обязано быть не короче шага опроса: иначе при опросе
    # раз в 300 с и реже отбрасывается ВСЁ, в том числе интервалы, которые
    # советует кламп по бюджету (518 с на 10 монет).
    check("при редком опросе окно шире шага",
          all(market_feed.oxa_rest_max_age(p, 30) >= p
              for p in (60, 300, 518, 600, 900)),
          [(p, market_feed.oxa_rest_max_age(p, 30)) for p in (300, 518, 900)])
    check("при частом опросе окно остаётся 300 с",
          market_feed.oxa_rest_max_age(60, 30) == market_feed.OXA_LIQ_FRESH_SEC,
          market_feed.oxa_rest_max_age(60, 30))
    for poll in (300, 518, 600):
        # событие пришло между двумя опросами: возраст = шаг опроса
        row = liq_row("BTC", 100, 1, "Long", f"old{poll}", sec_ago=poll)
        got = parse_oxa_liquidations(
            {"data": [row]}, {"BTC": "BTC_USDT"}, now=time.time(),
            max_age=market_feed.oxa_rest_max_age(poll, 30), all_rows=True)
        check(f"при опросе раз в {poll} с событие не теряется", bool(got), got)


async def test_clamp(fake, keys):
    """Настройка сверх бюджета урезается, и это видно в health."""
    print("4) настройка сверх бюджета урезается")
    market_feed.OXA_REST = f"http://127.0.0.1:{PORT}/v1/hyperliquid"
    market_feed.OXA_MODE = "rest"
    market_feed.OXA_POLL_SEC = 60.0        # реальный интервал
    market_feed.OXA_FREE_CREDITS = 50000   # реальный бюджет Free
    market_feed.OXA_COINS_PER_KEY = 5      # просим 5 -> влезает 1
    market_feed.OXA_OVERLAP_SEC = 1
    market_feed.OXA_FIRST_WINDOW_SEC = 60
    os.environ["LIQSCOPE_OXA_KEYS"] = keys[0]
    fake.hits.clear()

    liqs = []
    feed = make_feed(liqs)
    await feed.start()
    try:
        check("источник поднялся",
              await wait_until(lambda: feed.status["oxa"].connected),
              feed.status["oxa"].last_error)
        extra = feed.status["oxa"].extra
        check("health: настройку урезали с 5",
              extra.get("oxa_coins_clamped_from") == 5, extra)
        check("health: плановый расход влезает в бюджет",
              extra.get("oxa_credits_planned", 10 ** 9)
              <= market_feed.OXA_FREE_CREDITS, extra)
        check("health: плановый расход посчитан",
              extra.get("oxa_credits_planned") == 43200, extra)
        check("опрашивается ровно одна монета",
              await wait_until(lambda: len({c for _, c in fake.hits}) >= 1)
              and len({c for _, c in fake.hits}) == 1,
              {c for _, c in fake.hits})
    finally:
        await feed.stop()
        os.environ.pop("LIQSCOPE_OXA_KEYS", None)


class FormatHandler(logging.Handler):
    """Обработчик, который именно ФОРМАТИРУЕТ запись, как в бою.

    В server.py стоит logging.basicConfig(level=INFO), поэтому каждая запись
    проходит через Formatter -> record.getMessage() -> "msg % args". В тестах
    обработчиков нет, а lastResort срабатывает только от WARNING, поэтому
    log.info не форматируется вовсе и битый %d молча проходит. Боевой лог при
    этом падает с TypeError: %d format: a real number is required, not NoneType.
    """

    def __init__(self):
        super().__init__(logging.INFO)
        self.setFormatter(logging.Formatter("%(message)s"))
        self.lines = []
        self.errors = []

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception as e:                      # noqa: BLE001
            self.errors.append(f"{type(record.msg).__name__}: {type(e).__name__}: {e}")


async def test_log_formats(fake, keys):
    """Минутная строка лога обязана форматироваться при любом состоянии."""
    print("6) лог форматируется без ошибок")
    market_feed.OXA_REST = f"http://127.0.0.1:{PORT}/v1/hyperliquid"
    market_feed.OXA_MODE = "rest"
    market_feed.OXA_POLL_SEC = 0.4
    market_feed.OXA_COINS_PER_KEY = 2
    market_feed.OXA_FREE_CREDITS = 10 ** 9
    market_feed.OXA_OVERLAP_SEC = 1
    market_feed.OXA_FIRST_WINDOW_SEC = 60
    market_feed.NEW_SOURCE_LOG_SEC = 0.0     # чтобы report() писал каждый раз
    os.environ["LIQSCOPE_OXA_KEYS"] = ",".join(keys)
    fake.hits.clear()

    handler = FormatHandler()
    feed_log = logging.getLogger("liqscope.feed")
    feed_log.addHandler(handler)
    old_level = feed_log.level
    feed_log.setLevel(logging.INFO)

    liqs = []
    async def on_liq(ev): liqs.append(ev)
    async def on_price(*a): pass
    feed = MarketFeed(on_liquidation=on_liq, on_price=on_price, exchanges=[])
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    async def _uni(session=None): return {"BTC", "ETH"}
    feed._hyperliquid_load_universe = _uni
    await feed.start()
    try:
        await wait_until(lambda: feed.status["oxa"].extra.get("oxa_requests", 0) >= 2)
        await asyncio.sleep(0.2)
    finally:
        await feed.stop()
        feed_log.removeHandler(handler)
        feed_log.setLevel(old_level)
        os.environ.pop("LIQSCOPE_OXA_KEYS", None)

    check("лог не падал при форматировании", handler.errors == [], handler.errors)
    check("минутная строка oxa/rest действительно писалась",
          any("[oxa/rest]" in ln for ln in handler.lines), handler.lines[-3:])
    # прогноз кредитов на старте ещё None — строка обязана это пережить
    check("в строке нет None вместо числа",
          all("None" not in ln for ln in handler.lines if "[oxa" in ln),
          [ln for ln in handler.lines if "[oxa" in ln][-2:])


async def test_http_error(fake_bad):
    print("5) HTTP-ошибка видна, поток не падает")
    market_feed.OXA_REST = f"http://127.0.0.1:{PORT + 1}/v1/hyperliquid"
    market_feed.OXA_POLL_SEC = 0.3
    market_feed.OXA_COINS_PER_KEY = 4
    os.environ["LIQSCOPE_OXA_KEYS"] = "0xa_err"
    liqs = []
    feed = make_feed(liqs, ("BTC_USDT",))
    await feed.start()
    try:
        check("ошибка попала в last_error",
              await wait_until(lambda: "500" in
                               (feed.status["oxa"].last_error or "")),
              feed.status["oxa"].last_error)
        check("ошибки считаются",
              feed.status["oxa"].extra.get("oxa_errors", 0) >= 1,
              feed.status["oxa"].extra)
        check("источник остаётся поднятым (не упал)",
              feed.status["oxa"].connected is True)
    finally:
        await feed.stop()
        os.environ.pop("LIQSCOPE_OXA_KEYS", None)


async def main():
    keys = ["0xa_rest_one", "0xa_rest_two"]
    rows = {
        "BTC": [liq_row("BTC", 59900, 0.5, "Long", "btc-1"),
                liq_row("BTC", 59800, 0.25, "Short", "btc-2", side="B")],
        "ETH": [liq_row("ETH", 2990, 3.0, "Short", "eth-1", side="B")],
        "SOL": [liq_row("SOL", 140, 10.0, "Long", "sol-1")],
        "kPEPE": [liq_row("kPEPE", 0.00001, 100000, "Long", "pepe-1")],
    }
    fake = FakeOxaRest(good_keys=set(keys), rows=rows)
    app = web.Application()
    app.router.add_get("/v1/hyperliquid/liquidations/{coin}", fake.handle)
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "127.0.0.1", PORT).start()

    fake_bad = FakeOxaRest(good_keys={"0xa_err"}, fail_coins={"BTC"})
    app2 = web.Application()
    app2.router.add_get("/v1/hyperliquid/liquidations/{coin}", fake_bad.handle)
    r2 = web.AppRunner(app2)
    await r2.setup()
    await web.TCPSite(r2, "127.0.0.1", PORT + 1).start()

    try:
        test_parser_on_real_shape()
        test_budget()
        await test_poll(fake, keys)
        await test_clamp(fake, keys)
        await test_log_formats(fake, keys)
        await test_http_error(fake_bad)
    finally:
        await r.cleanup()
        await r2.cleanup()


if __name__ == "__main__":
    print("0xArchive: опрос REST — рабочий путь к ликвидациям Hyperliquid")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
