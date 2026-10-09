"""Кто и сколько монет может добавить в общий список терминала.

Каждая добавленная пара — это подписки WS на биржах, история свечей, ряд OI и
поток тиков, а список видят все клиенты. Ресурс общий, поэтому:

* аноним новую монету добавить не может (401, «Авторизуйтесь для добавления
  монет»), но уже лежащую в списке пару открыть может — она ничего не стоит;
* аккаунт — не больше ``USER_SYMBOL_CAP`` (5) монет; счётчик лежит в базе
  (``accounts.user_symbols``) и сверяется со списком фида: слот держит только
  та пара, которая реально занимает подписки и память, поэтому рестарт сервиса
  освобождает слоты, а не сжигает их навсегда;
* админ — без ограничений;
* глобальный кап памяти ``MAX_CUSTOM_SYMBOLS`` остаётся 120.

Отдельно проверяем, что охрана списка из tests/test_symbol_guard.py никуда не
делась: force только для админа, кривое имя пары отклоняется до всяких квот.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="symquota_")
DB_PATH = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = DB_PATH
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq_history.jsonl")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_SECRET"] = "test-secret-not-the-published-default"
os.environ["LIQSCOPE_ADMIN_IDS"] = "1001"
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_BOT_TOKEN"] = ""
os.environ["LIQSCOPE_API_CACHE"] = "0"

import accounts  # noqa: E402
import market_feed  # noqa: E402
import server  # noqa: E402
from market_feed import MarketFeed  # noqa: E402

NEW_COINS = [f"NEWCOIN{i}_USDT" for i in range(1, 9)]


def _feed() -> MarketFeed:
    """Фид с подставным каталогом: топ-4 + восемь «новых» монет."""
    feed = MarketFeed(on_liquidation=lambda ev: asyncio.sleep(0),
                      on_price=lambda s, p, c: asyncio.sleep(0),
                      symbols_limit=4)

    async def binance():
        rows = [{"symbol": s, "volume24h": 1e9, "price": 1.0, "change24h": 0.0}
                for s in ("BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT")]
        rows += [{"symbol": s, "volume24h": 1e6, "price": 2.0, "change24h": 0.0}
                 for s in NEW_COINS]
        return rows

    async def empty():
        return []

    feed._symbols_binance = binance
    feed._symbols_bybit = empty
    feed._symbols_okx = empty
    feed._symbols_gate = empty
    feed._symbols_bitget = empty
    return feed


def _request(symbol: str, force: bool = False, cookie: str = "",
             ip: str = "203.0.113.9"):
    from starlette.requests import Request
    query = f"symbol={symbol}&force={'true' if force else 'false'}"
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/symbols/add",
        "raw_path": b"/api/symbols/add", "query_string": query.encode(),
        "headers": headers, "client": (ip, 4321), "server": ("test", 80),
    }
    return Request(scope)


def _body(res):
    """Тело ответа роута: dict (200) или JSONResponse (отказ)."""
    if isinstance(res, dict):
        return res, 200
    import json as _json
    return _json.loads(res.body), res.status_code


def _add(symbol: str, cookie: str = "", force: bool = False, ip: str = "203.0.113.9"):
    res = asyncio.run(server.api_symbol_add(_request(symbol, force=force,
                                                     cookie=cookie, ip=ip),
                                            symbol, force))
    return _body(res)


class SymbolQuotaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = server.account_store
        cls.user = cls.store.upsert_telegram_user({"id": 2002, "first_name": "Bob"})
        cls.user_cookie = f"{server.COOKIE_SID}=" + cls.store.create_session(cls.user["id"])
        cls.admin = cls.store.upsert_telegram_user({"id": 1001, "first_name": "Ada"})
        cls.admin_cookie = f"{server.COOKIE_SID}=" + cls.store.create_session(cls.admin["id"])

    def setUp(self):
        self.feed = _feed()
        asyncio.run(self.feed.refresh_symbols())
        self._prev_feed = server.feed
        server.feed = self.feed
        server._SYMBOL_ADD_RATE.reset("add:203.0.113.9")
        server._SYMBOL_ADD_RATE.reset("add:203.0.113.10")
        self.store.remove_user_symbol(self.user["id"])
        self.store.remove_user_symbol(self.admin["id"])

    def tearDown(self):
        server.feed = self._prev_feed

    # --- аноним ------------------------------------------------------------
    def test_anonymous_cannot_add_new_coin(self):
        body, status = _add(NEW_COINS[0])
        self.assertEqual(status, 401)
        self.assertFalse(body["added"])
        self.assertEqual(body["error"], "auth")
        self.assertEqual(body["message"], "Авторизуйтесь для добавления монет")
        self.assertNotIn(NEW_COINS[0], self.feed.symbols)
        self.assertNotIn(NEW_COINS[0], self.feed.custom_symbols)

    def test_anonymous_can_open_coin_that_is_already_listed(self):
        """Гость не теряет график: уже лежащая в списке пара открывается."""
        body, status = _add("BTC_USDT")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["added"])
        self.assertIn("BTC_USDT", self.feed.symbols)

    def test_anonymous_force_is_still_admin_only(self):
        body, status = _add(NEW_COINS[1], force=True)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "admin")
        self.assertNotIn(NEW_COINS[1], self.feed.symbols)

    def test_anonymous_html_payload_is_rejected_before_quota(self):
        body, status = _add("<img src=x onerror=alert(1)>")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"], "invalid")
        self.assertFalse(any("<" in s for s in self.feed.symbols))

    # --- аккаунт: кап 5 ----------------------------------------------------
    def test_account_gets_five_coins_and_no_more(self):
        cap = market_feed.USER_SYMBOL_CAP
        self.assertEqual(cap, 5, "кап на аккаунт по условию задачи — 5 монет")
        for i in range(cap):
            body, status = _add(NEW_COINS[i], cookie=self.user_cookie)
            self.assertEqual(status, 200, body)
            self.assertTrue(body["added"], body)
            self.assertEqual(body["limit"], cap)
            self.assertEqual(body["used"], i + 1)
            self.assertEqual(body["left"], cap - i - 1)
            self.assertIn(NEW_COINS[i], self.feed.symbols)

        body, status = _add(NEW_COINS[5], cookie=self.user_cookie)
        self.assertEqual(status, 409, body)
        self.assertFalse(body["added"])
        self.assertEqual(body["error"], "quota")
        self.assertEqual(body["limit"], cap)
        self.assertEqual(body["left"], 0)
        self.assertNotIn(NEW_COINS[5], self.feed.symbols)
        self.assertNotIn(NEW_COINS[5], self.feed.custom_symbols)

    def test_quota_counter_lives_in_the_database(self):
        for coin in NEW_COINS[:3]:
            _add(coin, cookie=self.user_cookie)
        quota = self.store.user_symbol_quota(self.user["id"])
        self.assertEqual(quota["used"], 3)
        self.assertEqual(quota["left"], market_feed.USER_SYMBOL_CAP - 3)
        self.assertEqual(sorted(quota["symbols"]), sorted(NEW_COINS[:3]))

        # «рестарт процесса»: новый Store на той же базе видит тот же счётчик
        fresh = accounts.Store(DB_PATH, os.environ["LIQSCOPE_SECRET"],
                               admin_ids=(1001,))
        try:
            self.assertEqual(fresh.user_symbol_count(self.user["id"]), 3)
            self.assertEqual(sorted(fresh.user_symbols(self.user["id"])),
                             sorted(NEW_COINS[:3]))
        finally:
            fresh.close()

    def test_slots_free_up_when_the_coins_are_gone(self):
        """«Рестарт сервиса»: custom_symbols фида живёт в памяти и пустеет.

        Счётчик в базе переживает рестарт, но слот считается по факту: если
        пары в терминале больше нет, она не занимает ни подписок WS, ни памяти
        — значит и квоту держать не должна. Иначе «5 монет на аккаунт»
        превращались бы в пожизненные пять монет.
        """
        for coin in NEW_COINS[:3]:
            _add(coin, cookie=self.user_cookie)
        self.assertEqual(self.store.user_symbol_count(self.user["id"]), 3)

        self.feed.custom_symbols.clear()
        for coin in NEW_COINS[:3]:
            if coin in self.feed.symbols:
                self.feed.symbols.remove(coin)

        body, status = _add(NEW_COINS[3], cookie=self.user_cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["added"], body)
        self.assertEqual(body["used"], 1, "строки исчезнувших пар освобождены")
        self.assertEqual(body["left"], market_feed.USER_SYMBOL_CAP - 1)
        self.assertEqual(self.store.user_symbols(self.user["id"]), [NEW_COINS[3]])

    def test_coin_that_is_still_listed_keeps_its_slot(self):
        """Пока пара в списке, слот занят: сверка не «протечёт» в лишние места."""
        cap = market_feed.USER_SYMBOL_CAP
        for coin in NEW_COINS[:cap]:
            _add(coin, cookie=self.user_cookie)
        self.assertEqual(self.store.user_symbol_count(self.user["id"]), cap)

        # одна монета из списка пропала — её слот тут же можно занять заново
        self.feed.custom_symbols.remove(NEW_COINS[1])
        if NEW_COINS[1] in self.feed.symbols:
            self.feed.symbols.remove(NEW_COINS[1])

        body, status = _add(NEW_COINS[1], cookie=self.user_cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["used"], cap, "та же пара снова занимает свой слот")
        self.assertEqual(self.store.user_symbol_count(self.user["id"]), cap,
                         "двойного счётчика в базе нет")
        # а шестая по-прежнему не проходит
        body, status = _add(NEW_COINS[6], cookie=self.user_cookie)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"], "quota")

    def test_repeating_same_coin_is_free(self):
        _add(NEW_COINS[0], cookie=self.user_cookie)
        body, status = _add(NEW_COINS[0], cookie=self.user_cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["added"])
        self.assertEqual(body["used"], 1, "повтор своей же пары квоту не тратит")
        self.assertEqual(self.store.user_symbol_count(self.user["id"]), 1)

    def test_opening_listed_coin_does_not_spend_quota(self):
        body, status = _add("ETH_USDT", cookie=self.user_cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["added"])
        self.assertEqual(body["used"], 0)
        self.assertEqual(self.store.user_symbol_count(self.user["id"]), 0)
        self.assertEqual(body["left"], market_feed.USER_SYMBOL_CAP)

    def test_quota_is_per_account_not_global(self):
        other = self.store.upsert_telegram_user({"id": 3003, "first_name": "Cid"})
        other_cookie = f"{server.COOKIE_SID}=" + self.store.create_session(other["id"])
        for coin in NEW_COINS[:market_feed.USER_SYMBOL_CAP]:
            _add(coin, cookie=self.user_cookie)
        # второй аккаунт: чужие монеты уже в списке — открывает бесплатно,
        # а свою новую добавить может (счётчики независимы)
        body, status = _add(NEW_COINS[0], cookie=other_cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["added"])
        body, status = _add(NEW_COINS[6], cookie=other_cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["used"], 1)
        self.assertEqual(self.store.user_symbol_count(self.user["id"]),
                         market_feed.USER_SYMBOL_CAP)

    def test_fake_cookie_does_not_unlock_quota(self):
        """Левый liqscope_sid — это аноним: новая монета не добавляется."""
        body, status = _add(NEW_COINS[2], cookie=f"{server.COOKIE_SID}=fake-sid-123")
        self.assertEqual(status, 401, body)
        self.assertEqual(body["error"], "auth")
        self.assertNotIn(NEW_COINS[2], self.feed.symbols)

    # --- админ -------------------------------------------------------------
    def test_admin_has_no_quota(self):
        for coin in NEW_COINS:
            body, status = _add(coin, cookie=self.admin_cookie)
            self.assertEqual(status, 200, body)
            self.assertTrue(body["added"], body)
            self.assertIn(coin, self.feed.symbols)
        self.assertGreater(len(NEW_COINS), market_feed.USER_SYMBOL_CAP)
        self.assertEqual(self.store.user_symbol_count(self.admin["id"]), 0,
                         "админский счётчик не ведём")

    def test_admin_force_still_works(self):
        body, status = _add("FORCEME_USDT", cookie=self.admin_cookie, force=True)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["added"])
        self.assertIn("FORCEME_USDT", self.feed.symbols)

    # --- глобальный кап памяти ---------------------------------------------
    def test_global_memory_cap_is_120(self):
        self.assertEqual(market_feed.MAX_CUSTOM_SYMBOLS, 120)
        self.assertEqual(market_feed.SYMBOLS_CAP, 120)

    def test_global_memory_cap_stops_everyone(self):
        saved = market_feed.MAX_CUSTOM_SYMBOLS
        market_feed.MAX_CUSTOM_SYMBOLS = 2
        try:
            for coin in NEW_COINS[:2]:
                body, status = _add(coin, cookie=self.admin_cookie)
                self.assertTrue(body["added"], body)
            body, status = _add(NEW_COINS[2], cookie=self.admin_cookie)
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"], "full")
            self.assertNotIn(NEW_COINS[2], self.feed.custom_symbols)
        finally:
            market_feed.MAX_CUSTOM_SYMBOLS = saved

    def test_feed_owner_cap_without_http(self):
        """Второй рубеж: кап на владельца работает и внутри фида."""
        feed = self.feed

        async def run():
            ok1 = await feed.add_symbol(NEW_COINS[0], owner="u:77", owner_cap=1)
            ok2 = await feed.add_symbol(NEW_COINS[1], owner="u:77", owner_cap=1)
            admin1 = await feed.add_symbol(NEW_COINS[2], owner="admin", owner_cap=-1)
            admin2 = await feed.add_symbol(NEW_COINS[3], owner="admin", owner_cap=-1)
            other = await feed.add_symbol(NEW_COINS[4], owner="u:88", owner_cap=1)
            return ok1, ok2, admin1, admin2, other

        ok1, ok2, admin1, admin2, other = asyncio.run(run())
        self.assertTrue(ok1["added"])
        self.assertTrue(ok1["quota_charged"])
        self.assertFalse(ok2["added"])
        self.assertEqual(ok2["error"], "quota")
        self.assertEqual(ok2["limit"], 1)
        self.assertTrue(admin1["added"] and admin2["added"], "админ без счёта")
        self.assertTrue(other["added"], "чужая квота не мешает")
        self.assertEqual(feed.owner_symbol_count("u:77"), 1)
        self.assertEqual(feed.custom_owners.get(NEW_COINS[0]), "u:77")

    def test_service_call_without_owner_is_unlimited(self):
        """Прогрев/тесты без владельца: квота не проверяется (прежнее поведение)."""
        async def run():
            for coin in NEW_COINS:
                res = await self.feed.add_symbol(coin)
                self.assertTrue(res["added"], res)
                self.assertFalse(res["quota_charged"])

        asyncio.run(run())


class FastJsonTest(unittest.TestCase):
    """orjson: приложение отвечает им, если пакет установлен."""

    def test_response_class_matches_orjson_availability(self):
        from fastapi.responses import JSONResponse as StarletteJSON
        cls = getattr(server.app.router, "default_response_class", None)
        self.assertIs(cls, server._DEFAULT_RESPONSE_CLASS)
        if server.FAST_JSON:
            from fastapi.responses import ORJSONResponse
            self.assertIs(cls, ORJSONResponse)
        else:
            self.assertIs(cls, StarletteJSON)
        # явные JSONResponse в server.py — тот же класс, что и дефолтный
        self.assertIs(server.JSONResponse, server._DEFAULT_RESPONSE_CLASS)

    def test_default_response_class_really_is_the_fast_one(self):
        """Поведенческая проверка: orjson кладёт null туда, где stdlib падает.

        У starlette JSONResponse ``allow_nan=False`` — NaN это ValueError и 500.
        orjson сериализует его в null, поэтому по такому ответу видно, какой
        сериализатор реально стоит за дефолтным классом приложения.
        """
        cls = server._DEFAULT_RESPONSE_CLASS
        if server.FAST_JSON:
            self.assertIn(b"null", cls({"x": float("nan")}).body)
        else:
            with self.assertRaises(ValueError):
                cls({"x": float("nan")}).body

    def test_feed_json_helpers_roundtrip(self):
        payload = {"symbol": "BTC_USDT", "n": 1, "f": 1.5,
                   "текст": "кириллица", "list": [1, 2, 3], "none": None}
        self.assertEqual(market_feed.json_loads(market_feed.json_dumps_text(payload)),
                         payload)
        self.assertEqual(server.json_dumps_text({"a": 1}), '{"a":1}')
        # orjson строже stdlib и не понимает NaN — запасной путь дорабатывает
        # кадр стандартным парсером, данные биржи не теряются
        import math
        got = market_feed.json_loads('{"a": NaN}')
        self.assertTrue(math.isnan(got["a"]))

    def test_ws_frames_go_through_fast_json(self):
        """Client.send сериализует сам (send_text), а не зовёт json.dumps."""
        import inspect
        src = inspect.getsource(server.Client.send)
        self.assertIn("send_text", src)
        self.assertIn("json_dumps_text", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
