"""
Поток Hyperliquid на локальной «псевдо-бирже».

Проверяет логику подписок с подтверждением:
  1. подписки идут по одной в отсортированном порядке, каждую биржа
     подтверждает subscriptionResponse;
  2. сделки, приходящие ДО ответа-подтверждения, обрабатываются мимоходом
     (ликвидации доходят до колбэка ещё в фазе подписок);
  3. после подписок слушатель встаёт в up() и продолжает жить;
  4. закрытие соединения превращается в громкий ConnectionError с кодом.

Сеть наружу не нужна.  Запуск:  python3 tests/test_hl_flow.py
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

PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"

sub_log = []     # монеты, на которые клиент подписался (по порядку)
liqs = []        # ликвидации, дошедшие до колбэка

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
    liqs.append(ev)


async def noop_price(*a):
    pass


async def fake_universe():
    return ["BTC", "ETH"]


async def fake_hl(request):
    """Псевдо-HL: на каждый subscribe — сначала сделка-ликвидация,
    потом subscriptionResponse (как в реальной жизни может прийти)."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            p = json.loads(msg.data)
        except Exception:
            continue
        sub = p.get("subscription") or {}
        coin = sub.get("coin")
        if p.get("method") == "subscribe" and coin:
            sub_log.append(coin)
            await ws.send_json({"channel": "trades", "data": [
                {"coin": coin, "side": "A", "px": "50000", "sz": "0.1",
                 "time": 1, "hash": "x",
                 "liquidation": {"liquidatedUser": "u"}}]})
            await ws.send_json({"channel": "subscriptionResponse",
                                "data": {"method": "subscribe",
                                         "subscription": {"type": "trades",
                                                          "coin": coin}}})
    return ws


async def fake_hl_closer(request):
    """Псевдо-HL, который рвёт соединение чистым close(1000)
    сразу после первой подписки."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            p = json.loads(msg.data)
        except Exception:
            continue
        if p.get("method") == "subscribe":
            await ws.close(code=1000)
            break
    return ws


def make_feed():
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    feed._session = aiohttp.ClientSession()
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    feed._hyperliquid_load_universe = fake_universe
    return feed


async def scenario_subscribe_flow():
    print("1) подписки по одной с подтверждением, сделки мимоходом")
    market_feed.HL_WS = f"{BASE}/hl"
    sub_log.clear()
    liqs.clear()
    feed = make_feed()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    try:
        for _ in range(100):
            if len(liqs) >= 2 and feed.status["hyperliquid"].connected:
                break
            await asyncio.sleep(0.1)
        check("обе монеты подписаны по порядку",
              sub_log == ["BTC", "ETH"], sub_log)
        check("ликвидации дошли до колбэка (2 шт)", len(liqs) == 2, len(liqs))
        check("стороны/символы верные",
              {e["symbol"] for e in liqs} == {"BTC_USDT", "ETH_USDT"}
              and all(e["side"] == "LONG" for e in liqs)
              and all(e["exchange"] == "hyperliquid" for e in liqs),
              liqs)
        check("статус up после подписок",
              feed.status["hyperliquid"].connected is True)
        check("счётчик событий = 2",
              feed.status["hyperliquid"].events == 2,
              feed.status["hyperliquid"].events)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await feed._session.close()


async def scenario_loud_close():
    print("2) чистое закрытие биржей — громкая ошибка с кодом")
    market_feed.HL_WS = f"{BASE}/closer"
    feed = make_feed()
    err = None
    try:
        await feed._hyperliquid_liquidations()
    except ConnectionError as e:
        err = e
    except Exception as e:  # noqa: BLE001
        err = e
        check("тип ошибки — ConnectionError", False, type(e).__name__)
    finally:
        await feed._session.close()
    check("разрыв поднял исключение (не тихий выход)", err is not None)
    if err is not None:
        check("в тексте есть close_code=1000", "close_code=1000" in str(err),
              str(err)[:120])


async def main():
    app = web.Application()
    app.router.add_get("/hl", fake_hl)
    app.router.add_get("/closer", fake_hl_closer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()
    try:
        await scenario_subscribe_flow()
        await scenario_loud_close()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    print("поток hyperliquid (локальная псевдо-биржа)")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
