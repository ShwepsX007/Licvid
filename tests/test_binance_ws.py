"""WS Binance: новый /market против отключённого legacy-пути.

2026-04-23 Binance отключил legacy-адреса (`wss://fstream.binance.com/stream`,
`.../ws`). Худшее в них то, что они НЕ отказывают: рукопожатие проходит,
подписка принимается, данных нет. Слушатель свечей это и ловил — «Binance kline
молчит 30с — перехожу на REST», — и по лог нельзя было понять, молчит биржа или
мы не туда ходим.

Здесь проверяется:
  1. порядок кандидатов: /market всегда первым;
  2. ликвидации и свечи берутся из /market, и второй путь не дёргается;
  3. если /market недоступен (404 — тестнет, зеркало, старый прокси),
     слушатель откатывается на legacy и данные всё равно идут;
  4. «открылся и молчит» — путь МЕНЯЕТСЯ, а не уводится в REST вслепую;
  5. адрес пути и счётчики кадров видны в health (`binance_ws`,
     `binance_frames`, `binance_klines`, `binance_silent_sec`), а путь, давший
     данные, запоминается (`_binance_ws_pref`).

Сеть наружу не нужна.  Запуск:  python3 tests/test_binance_ws.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import web

import market_feed
from market_feed import MarketFeed, binance_market_url, binance_ws_urls

PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"

ok = 0
fail = 0
hits: list = []
liqs: list = []
prices: list = []


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}  [{detail}]")


async def noop_price(sym, price, candle):
    prices.append((sym, price))


async def on_liq(ev):
    liqs.append(ev)


def force_order():
    return {"stream": "!forceOrder@arr", "data": {
        "e": "forceOrder", "E": int(time.time() * 1000),
        "o": {"s": "BTCUSDT", "S": "sell", "p": "98000", "q": "0.5",
              "z": "0.5", "ap": "97900", "T": int(time.time() * 1000)}}}


def kline():
    return {"stream": "btcusdt@kline_1m", "data": {
        "e": "kline", "s": "BTCUSDT",
        "k": {"t": int(time.time() * 1000) // 60000 * 60000, "i": "1m",
              "o": "99000", "c": "99123.5", "h": "99500", "l": "98900",
              "q": "1234567"}}}


def build_app(market_mode, legacy_mode, kind):
    """Псевдо-Binance: у каждого пути своё поведение.

    mode: 'data' — кадр сразу, 'silent' — соединение есть, данных нет (ровно то,
    что делает отключённый legacy-путь), '404' — пути нет вовсе.
    """
    app = web.Application()

    async def handler(request):
        mode = market_mode if request.path.startswith("/market") else legacy_mode
        if mode == "404":
            return web.Response(status=404)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hits.append(request.path)
        if mode == "data":
            await ws.send_json(force_order() if kind == "liq" else kline())
            await asyncio.sleep(0.2)      # даём клиенту прочитать кадр
        else:
            await asyncio.sleep(30)        # молчим, как мёртвый путь
        await ws.close()
        return ws

    app.router.add_get("/market/stream", handler)
    app.router.add_get("/stream", handler)
    return app


def make_feed():
    market_feed.BINANCE_WS = f"{BASE}/stream?streams="
    market_feed.BINANCE_WS_RAW = f"{BASE}/ws"
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed.symbols = ["BTC_USDT"]
    feed._session = aiohttp.ClientSession()
    return feed


async def run(feed, coro_factory, timeout=25.0):
    task = asyncio.create_task(coro_factory())
    try:
        return await asyncio.wait_for(task, timeout)
    finally:
        feed._stop.set()
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def scenario(url_app_kwargs, kind, coro_name):
    app = build_app(**url_app_kwargs, kind=kind)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    hits.clear()
    liqs.clear()
    prices.clear()
    feed = make_feed()
    try:
        await run(feed, lambda: getattr(feed, coro_name)())
    finally:
        await feed._session.close()
        await runner.cleanup()
    return feed, list(hits)


async def unit_checks():
    print("1) кандидаты URL: /market первым")
    check("обычный путь → сначала /market, потом то, что в BINANCE_WS",
          market_feed.binance_ws_urls("combined") ==
          ("wss://fstream.binance.com/market/stream?streams=",
           "wss://fstream.binance.com/stream?streams="),
          market_feed.binance_ws_urls("combined"))
    check("raw-путь так же",
          market_feed.binance_ws_urls("raw") ==
          ("wss://fstream.binance.com/market/ws", "wss://fstream.binance.com/ws"),
          market_feed.binance_ws_urls("raw"))
    check("уже на /market — не дублируем",
          binance_market_url("wss://x.com/market/stream?streams=")
          == "wss://x.com/market/stream?streams=")
    check("адрес без пути не ломаем", binance_market_url("wss://x.com") == "wss://x.com")
    market_feed.BINANCE_WS = "http://127.0.0.1:1/stream?streams="
    check("подменённый адрес (тестнет/зеркало) тоже получает /market-вариант",
          binance_ws_urls("combined") ==
          ("http://127.0.0.1:1/market/stream?streams=",
           "http://127.0.0.1:1/stream?streams="), binance_ws_urls("combined"))


async def liq_checks():
    print("2) ликвидации: /market отвечает — legacy не трогаем")
    feed, seen = await scenario({"market_mode": "data", "legacy_mode": "data"},
                                "liq", "_binance_liquidations")
    check("дошёл до ленты ровно один кадр, и только с /market",
          seen == ["/market/stream"], seen)
    check("событие ликвидации на месте", len(liqs) == 1
          and liqs[0]["symbol"] == "BTC_USDT" and liqs[0]["side"] == "LONG", liqs)
    check("в health видно, каким путём пошли",
          "/market/" in str(feed.status["binance"].extra.get("binance_ws")),
          feed.status["binance"].extra)
    check("рабочий путь запомнен именно префиксом (иначе prefer не сработает)",
          feed._binance_ws_pref in market_feed.binance_ws_urls("combined")
          and feed._binance_ws_pref.endswith("/market/stream?streams="),
          feed._binance_ws_pref)

    print("3) /market недоступен (404) — откат на legacy, данные идут")
    feed, seen = await scenario({"market_mode": "404", "legacy_mode": "data"},
                                "liq", "_binance_liquidations")
    check("сначала попробовали /market, потом legacy",
          seen == ["/stream"], seen)
    check("событие всё равно пришло", len(liqs) == 1, liqs)


async def kline_checks():
    print("4) свечи: молчащий путь меняем, а не уходим в REST")
    market_feed.BINANCE_SILENT_SEC = 1.0
    feed, seen = await scenario({"market_mode": "data", "legacy_mode": "data"},
                                "klines", "_binance_kline_stream")
    check("цена из /market дошла", any(p[1] == 99123.5 for p in prices), prices)
    check("legacy-путь не дёргали", seen == ["/market/stream"], seen)
    ex = feed.status["prices"].extra
    check("кадры и свечи считаются отдельно",
          ex.get("binance_frames", 0) >= 1 and ex.get("binance_klines") == 1, ex)
    check("имя источника показывает путь",
          "/market/" in str(ex.get("binance_ws")), ex)

    print("5) /market открывается и молчит — переклюдаемся на живой путь")
    feed, seen = await scenario({"market_mode": "silent", "legacy_mode": "data"},
                                "klines", "_binance_kline_stream")
    check("попробовали оба пути",
          seen == ["/market/stream", "/stream"], seen)
    check("данные всё-таки получили", any(p[1] == 99123.5 for p in prices), prices)
    check("и запомнили рабочий адрес (молчащий путь больше не первый)",
          feed._binance_ws_pref.startswith(f"{BASE}/stream?streams=")
          and "/market" not in feed._binance_ws_pref, feed._binance_ws_pref)
    market_feed.BINANCE_SILENT_SEC = 30.0


async def sticky_checks():
    print("6) запомненный путь не перебирается заново (без прыжков туда-сюда)")
    app = build_app(market_mode="data", legacy_mode="data", kind="klines")
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    hits.clear()
    prices.clear()
    market_feed.BINANCE_WS = f"{BASE}/stream?streams="
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed.symbols = ["BTC_USDT"]
    feed._session = aiohttp.ClientSession()
    # как если бы предыдущее соединение увидело, что живым был legacy-путь
    feed._binance_ws_pref = f"{BASE}/stream?streams="
    try:
        await run(feed, lambda: feed._binance_kline_stream())
        check("первым пошёл запомненный путь, а не /market",
              hits == ["/stream"], hits)
        check("данные пришли с первого раза", bool(prices), prices)
    finally:
        await feed._session.close()
        await runner.cleanup()


async def main():
    await unit_checks()
    await liq_checks()
    await kline_checks()
    await sticky_checks()
    print(f"\nитог: {ok} ок, {fail} ошибок")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
