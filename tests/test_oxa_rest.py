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
    print("2) опрос REST двумя ключами")
    market_feed.OXA_REST = f"http://127.0.0.1:{PORT}/v1/hyperliquid"
    market_feed.OXA_MODE = "rest"
    market_feed.OXA_POLL_SEC = 0.4
    market_feed.OXA_COINS_PER_KEY = 2
    market_feed.OXA_OVERLAP_SEC = 1
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
        check("health: прогноз кредитов есть",
              extra.get("oxa_credits_budget") == 100000, extra)
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
    finally:
        await feed.stop()
        os.environ.pop("LIQSCOPE_OXA_KEYS", None)


async def test_http_error(fake_bad):
    print("3) HTTP-ошибка видна, поток не падает")
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
        await test_poll(fake, keys)
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
