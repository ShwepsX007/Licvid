"""
0xArchive как источник ликвидаций Hyperliquid (по API-ключу).

У самого HL публичного канала ликвидаций нет, а 0xArchive их публикует.
Особенности, которые здесь проверяются:

  * ключей можно задать НЕСКОЛЬКО (LIQSCOPE_OXA_KEYS=k1,k2,...) — бесплатный
    тариф даёт 10 подписок на ключ, поэтому список монет режется между
    ключами, у каждого своё соединение;
  * подписка построчная: {"op":"subscribe","channel":"liquidations",
    "symbol":"BTC"};
  * событие — строка заполнения с is_liquidation: true, поля coin/px/sz/
    side/time; тейкер A → вынесли LONG;
  * обычные заполнения (is_liquidation: false) в ленту не идут;
  * считается КАЖДЫЙ кадр, а не только type=data: «ноль ликвидаций» должен
    означать тишину, а не незнакомую форму кадра — незнакомые типы видны в
    health (oxa_frame_kinds) и в предупреждении лога;
  * в ленте событие помечено биржей hyperliquid: ликвидация произошла там,
    0xArchive — только транспорт;
  * без ключей источник не стартует и пишет причину в health.

Сеть наружу не нужна.  Запуск:  python3 tests/test_oxa.py
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
from market_feed import (MarketFeed, oxa_keys, parse_oxa_liquidations)

PORT = 8841

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


async def wait_until(cond, timeout=8.0, step=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def ms(sec_ago=0.0):
    return int((time.time() - sec_ago) * 1000)


def fill(coin, px, sz, side, sec_ago=0.2, liq=True):
    return {"coin": coin, "px": str(px), "sz": str(sz), "side": side,
            "time": ms(sec_ago), "is_liquidation": liq,
            "user_address": "0xabc", "hash": "0xdead"}


# ---------------------------------------------------------------------------
class FakeOxa:
    """Псевдо-0xArchive: принимает несколько соединений с разными ключами."""

    def __init__(self, good_keys=(), liq_every=0.3, junk_frames=False):
        self.good_keys = set(good_keys)
        self.liq_every = liq_every
        self.junk_frames = junk_frames
        self.per_key = {}          # ключ -> список подписанных монет
        self.pings = {}

    async def handle(self, request):
        auth = request.headers.get("Authorization") or ""
        key = auth.replace("Bearer", "").strip()
        if key not in self.good_keys:
            return web.Response(status=401, text="unauthorized")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.per_key.setdefault(key, [])
        self.pings.setdefault(key, 0)
        await ws.send_json({"type": "open"})
        task = None
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                p = json.loads(msg.data)
                if p.get("op") == "ping":
                    self.pings[key] += 1
                    await ws.send_json({"type": "pong"})
                    continue
                if p.get("op") == "subscribe" and p.get("channel") == "liquidations":
                    self.per_key[key].append(p.get("symbol"))
                    await ws.send_json({"type": "subscribed",
                                        "channel": "liquidations",
                                        "symbol": p.get("symbol")})
                    if task is None:
                        task = asyncio.create_task(self._tape(ws, p.get("symbol")))
        finally:
            if task:
                task.cancel()
        return ws

    async def _tape(self, ws, coin):
        while not ws.closed:
            await ws.send_json({"type": "data", "channel": "liquidations",
                                "symbol": coin, "data": [
                                    fill(coin, 60000, 0.5, "A"),
                                    fill(coin, 59900, 0.25, "B"),
                                    # обычное заполнение — не ликвидация
                                    fill(coin, 60050, 1.0, "B", liq=False),
                                ]})
            if self.junk_frames:
                await ws.send_json({"type": "something_new", "hello": 1})
                await ws.send_str("не json вообще")   # именно битый кадр
            await asyncio.sleep(self.liq_every)


def make_feed(liqs, symbols=("BTC_USDT", "ETH_USDT", "SOL_USDT", "PEPE_USDT")):
    async def on_liq(ev):
        liqs.append(ev)

    async def on_price(*a):
        pass

    feed = MarketFeed(on_liquidation=on_liq, on_price=on_price, exchanges=[])
    feed.symbols = list(symbols)
    feed.hot_symbols = set(symbols)

    async def _fake_universe(session=None):
        # universe в песочнице недоступен — подставляем, как в прочих тестах
        return {"BTC", "ETH", "SOL", "kPEPE"}
    feed._hyperliquid_load_universe = _fake_universe
    return feed


# ---------------------------------------------------------------------------
def test_keys():
    print("1) разбор ключей")
    for var in ("LIQSCOPE_OXA_KEYS", "OXARCHIVE_API_KEY"):
        os.environ.pop(var, None)
    check("без ключей — пусто", oxa_keys() == [])
    os.environ["LIQSCOPE_OXA_KEYS"] = "0xa_one, 0xa_two ,0xa_one,, 0xa_three"
    check("несколько ключей, пробелы и дубликаты убраны",
          oxa_keys() == ["0xa_one", "0xa_two", "0xa_three"], oxa_keys())
    os.environ["LIQSCOPE_OXA_KEYS"] = "0xa_one;0xa_two"
    check("разделитель «;» тоже работает",
          oxa_keys() == ["0xa_one", "0xa_two"], oxa_keys())
    os.environ.pop("LIQSCOPE_OXA_KEYS")
    os.environ["OXARCHIVE_API_KEY"] = "0xa_single"
    check("один ключ из OXARCHIVE_API_KEY", oxa_keys() == ["0xa_single"])
    os.environ["LIQSCOPE_OXA_KEYS"] = "0xa_multi"
    check("LIQSCOPE_OXA_KEYS важнее", oxa_keys() == ["0xa_multi"])
    os.environ.pop("LIQSCOPE_OXA_KEYS")
    os.environ.pop("OXARCHIVE_API_KEY")


def test_parser():
    print("2) парсер кадров")
    cmap = {"BTC": "BTC_USDT", "kPEPE": "PEPE_USDT"}
    stats = {}
    frame = {"type": "data", "channel": "liquidations", "symbol": "BTC",
             "data": [fill("BTC", 60000, 0.5, "A"),
                      fill("BTC", 59900, 0.25, "B"),
                      fill("BTC", 60050, 1.0, "B", liq=False)]}
    res = parse_oxa_liquidations(frame, cmap, now=time.time(), max_age=300,
                                 stats=stats)
    check("только is_liquidation=true", len(res) == 2, res)
    check("тейкер A → вынесли LONG", res[0]["side"] == "LONG")
    check("тейкер B → вынесли SHORT", res[1]["side"] == "SHORT")
    check("символ из карты монет", res[0]["symbol"] == "BTC_USDT")
    check("мс → секунды", abs(res[0]["ts"] - ms(0.2) / 1000.0) < 0.01)
    check("сумма в USD", abs(res[0]["usd"] - 30000.0) < 1e-6)
    check("строки ликвидаций посчитаны", stats.get("liq_rows") == 2, stats)
    check("дешёвый токен с префиксом k",
          parse_oxa_liquidations(
              {"type": "data", "symbol": "kPEPE",
               "data": [fill("kPEPE", 0.00001, 1000, "A")]},
              cmap) [0]["symbol"] == "PEPE_USDT")
    st2 = {}
    parse_oxa_liquidations({"type": "data", "symbol": "ZZZ",
                            "data": [fill("ZZZ", 1, 1, "A")]}, cmap, stats=st2)
    check("чужая монета отсеяна и посчитана", st2.get("skipped_other") == 1, st2)
    check("вчерашнее отсеяно",
          parse_oxa_liquidations({"type": "data", "symbol": "BTC",
                                  "data": [fill("BTC", 1, 1, "A", 99999)]},
                                 cmap, now=time.time(), max_age=300) == [])
    check("кадр без данных игнорируется",
          parse_oxa_liquidations({"type": "subscribed"}, cmap) == [])
    check("не словарь игнорируется", parse_oxa_liquidations([1, 2], cmap) == [])
    check("нулевая цена отброшена",
          parse_oxa_liquidations({"type": "data", "symbol": "BTC",
                                  "data": [fill("BTC", 0, 1, "A")]}, cmap) == [])


async def test_stream(fake, keys):
    print("3) два ключа — два соединения, монеты пополам")
    market_feed.OXA_WS = f"http://127.0.0.1:{PORT}/ws"
    market_feed.OXA_PING_SEC = 0.3
    os.environ["LIQSCOPE_OXA_KEYS"] = ",".join(keys)
    liqs = []
    feed = make_feed(liqs)
    async with aiohttp.ClientSession() as session:
        feed._session = session
        task = asyncio.create_task(feed._oxa_liquidations())
        try:
            check("поднялось два соединения",
                  await wait_until(lambda: len(fake.per_key) == 2),
                  list(fake.per_key))
            all_subs = [c for v in fake.per_key.values() for c in v]
            check("каждому ключу достались свои монеты",
                  all(len(v) >= 1 for v in fake.per_key.values()),
                  {k[-4:]: v for k, v in fake.per_key.items()})
            check("все наши монеты подписаны",
                  sorted(all_subs) == sorted(["BTC", "ETH", "SOL", "kPEPE"]),
                  all_subs)
            check("ликвидации дошли до ленты",
                  await wait_until(lambda: len(liqs) >= 2), len(liqs))
            ev = liqs[0] if liqs else {}
            check("в ленте биржа — hyperliquid",
                  ev.get("exchange") == "hyperliquid", ev)
            check("событие полное", ev.get("symbol") == "BTC_USDT"
                  and ev.get("price", 0) > 0 and ev.get("usd", 0) > 0, ev)
            check("обе стороны встречаются",
                  await wait_until(lambda: {e["side"] for e in liqs}
                                   == {"LONG", "SHORT"}),
                  {e["side"] for e in liqs})
            extra = feed.status["oxa"].extra
            check("health: подписки подтверждены",
                  extra.get("oxa_subs_acked", 0) >= 4, extra)
            check("health: ключей два", extra.get("oxa_keys") == 2, extra)
            check("health: ликвидации считаются",
                  extra.get("oxa_liquidations", 0) >= 2, extra)
            check("health: обычные заполнения не в счётчике ликвидаций",
                  extra.get("oxa_liquidations", 0)
                  < extra.get("oxa_frames", 0) * 3, extra)
            # пинг уходит по таймеру — даём ему хотя бы пару интервалов
            await asyncio.sleep(0.8)
            check("пинг уходит с каждого ключа",
                  all(v >= 1 for v in fake.pings.values()), fake.pings)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):      # noqa: B014
                pass
    os.environ.pop("LIQSCOPE_OXA_KEYS", None)


async def test_junk_frames():
    print("4) незнакомые кадры видно, а не проглатываются")
    fake = FakeOxa(good_keys={"0xa_j1"}, junk_frames=True)
    app = web.Application()
    app.router.add_get("/ws", fake.handle)
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "127.0.0.1", PORT + 1).start()
    market_feed.OXA_WS = f"http://127.0.0.1:{PORT + 1}/ws"
    os.environ["LIQSCOPE_OXA_KEYS"] = "0xa_j1"
    try:
        liqs = []
        feed = make_feed(liqs)
        async with aiohttp.ClientSession() as session:
            feed._session = session
            task = asyncio.create_task(feed._oxa_liquidations())
            try:
                check("поток идёт",
                      await wait_until(lambda: len(liqs) >= 1), len(liqs))
                # ждём, пока пройдут ОБА мусорных кадра (незнакомый тип и
                # битый JSON), иначе проверка снимается раньше кадра
                got = await wait_until(
                    lambda: feed.status["oxa"].extra.get(
                        "oxa_frame_kinds", {}).get("<не-json>", 0) >= 1
                    and feed.status["oxa"].extra.get(
                        "oxa_frame_kinds", {}).get("something_new", 0) >= 1)
                check("оба мусорных кадра дошли", got,
                      feed.status["oxa"].extra.get("oxa_frame_kinds"))
                kinds = feed.status["oxa"].extra.get("oxa_frame_kinds", {})
                check("незнакомый тип кадра виден в health",
                      kinds.get("something_new", 0) >= 1, kinds)
                check("битый кадр посчитан отдельно",
                      kinds.get("<не-json>", 0) >= 1, kinds)
                check("данные при этом разбираются",
                      feed.status["oxa"].extra.get("oxa_liquidations", 0) >= 1,
                      feed.status["oxa"].extra)
            finally:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: B014
                    pass
    finally:
        await r.cleanup()
        os.environ.pop("LIQSCOPE_OXA_KEYS", None)


async def test_no_keys():
    print("5) без ключей источник не стартует")
    os.environ.pop("LIQSCOPE_OXA_KEYS", None)
    os.environ.pop("OXARCHIVE_API_KEY", None)
    liqs = []
    feed = make_feed(liqs)
    async with aiohttp.ClientSession() as session:
        feed._session = session
        await feed._oxa_liquidations()
    check("ликвидаций нет", liqs == [])
    check("в health написана причина",
          "LIQSCOPE_OXA_KEYS" in (feed.status["oxa"].last_error or ""),
          feed.status["oxa"].last_error)
    check("источник не подключён",
          feed.status["oxa"].connected is False)


async def test_bad_key():
    print("6) неверный ключ — отказ, а не тишина")
    fake = FakeOxa(good_keys={"0xa_good"})
    app = web.Application()
    app.router.add_get("/ws", fake.handle)
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "127.0.0.1", PORT + 2).start()
    market_feed.OXA_WS = f"http://127.0.0.1:{PORT + 2}/ws"
    os.environ["LIQSCOPE_OXA_KEYS"] = "0xa_bad"
    try:
        feed = make_feed(liqs := [])
        async with aiohttp.ClientSession() as session:
            feed._session = session
            try:
                await asyncio.wait_for(feed._oxa_liquidations(), 5)
            except Exception as e:
                check("подключение с плохим ключом падает заметно",
                      "401" in str(e) or "Unauthorized" in str(e), str(e))
            else:
                check("подключение с плохим ключом падает заметно", False,
                      "соединение открылось")
    finally:
        await r.cleanup()
        os.environ.pop("LIQSCOPE_OXA_KEYS", None)


async def main():
    keys = ["0xa_key_one", "0xa_key_two"]
    fake = FakeOxa(good_keys=set(keys))
    app = web.Application()
    app.router.add_get("/ws", fake.handle)
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "127.0.0.1", PORT).start()
    try:
        test_keys()
        test_parser()
        await test_stream(fake, keys)
    finally:
        await r.cleanup()
    await test_junk_frames()
    await test_no_keys()
    await test_bad_key()


if __name__ == "__main__":
    print("0xArchive: ликвидации Hyperliquid по нескольким ключам")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
