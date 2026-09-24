"""Охрана списка монет: чужой HTML не попадает в общий список.

Анонимный POST /api/symbols/add с force=true раньше клал произвольную строку
в feed.symbols, сервер рассылал её всем клиентам, а UI вставлял в innerHTML.
Проверяем три слоя: валидация пары, кап списка, force только для админа,
и что клиент экранирует подпись, даже если строка всё же пришла.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="symguard_")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq_history.jsonl")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_SECRET"] = "test-secret-not-the-published-default"
os.environ["LIQSCOPE_ADMIN_IDS"] = "1001"
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_BOT_TOKEN"] = ""

from market_feed import MarketFeed, SYMBOLS_CAP, is_safe_symbol  # noqa: E402
import market_feed  # noqa: E402
import server  # noqa: E402


def _feed() -> MarketFeed:
    feed = MarketFeed(on_liquidation=lambda e: asyncio.sleep(0),
                      on_price=lambda s, p, c: asyncio.sleep(0),
                      symbols_limit=8)

    async def binance():
        return [
            {"symbol": "BTC_USDT", "volume24h": 1e10, "price": 1.0, "change24h": 0},
            {"symbol": "ETH_USDT", "volume24h": 1e9, "price": 1.0, "change24h": 0},
            {"symbol": "<IMG SRC=X ONERROR=ALERT(1)>_USDT", "volume24h": 1e12,
             "price": 1.0, "change24h": 0},
        ]

    async def empty():
        return []

    feed._symbols_binance = binance
    feed._symbols_bybit = empty
    feed._symbols_okx = empty
    feed._symbols_gate = empty
    feed._symbols_bitget = empty
    return feed


def _request(symbol: str, force: bool = False, cookie: str = ""):
    from starlette.requests import Request
    query = f"symbol={symbol}&force={'true' if force else 'false'}"
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/symbols/add",
        "raw_path": b"/api/symbols/add",
        "query_string": query.encode(),
        "headers": headers,
        "client": ("203.0.113.8", 1234),
        "server": ("test", 80),
    }
    return Request(scope)


class SymbolGuardTest(unittest.TestCase):
    def setUp(self):
        self.feed = _feed()
        asyncio.run(self.feed.refresh_symbols())
        self._prev = server.feed
        server.feed = self.feed
        server._SYMBOL_ADD_RATE.reset("add:203.0.113.8")

    def tearDown(self):
        server.feed = self._prev

    def test_catalog_drops_html_names(self):
        self.assertIn("BTC_USDT", self.feed.symbol_index)
        self.assertNotIn("<IMG SRC=X ONERROR=ALERT(1)>_USDT", self.feed.symbol_index)
        self.assertTrue(all(is_safe_symbol(s) for s in self.feed.symbols))

    def test_xss_payload_is_rejected(self):
        res = asyncio.run(self.feed.add_symbol(
            "<img src=x onerror=alert(1)>", force=True))
        self.assertFalse(res["added"])
        self.assertEqual(res.get("error"), "invalid")
        self.assertEqual(res.get("symbol"), "")
        self.assertFalse(any("<" in s or '"' in s for s in self.feed.symbols))

    def test_quote_and_space_rejected(self):
        for raw in ('BTC"_USDT', "BTC USDT", "BTC_USDT<img>", "../ETC_USDT"):
            res = asyncio.run(self.feed.add_symbol(raw, force=True))
            self.assertFalse(res["added"], raw)
            self.assertNotIn(raw, self.feed.symbols)

    def test_known_pair_still_adds(self):
        res = asyncio.run(self.feed.add_symbol("GRAM"))
        # GRAM нет в каталоге этого теста — не добавляем без force
        self.assertFalse(res["added"])
        res = asyncio.run(self.feed.add_symbol("ETH"))
        self.assertTrue(res["added"])
        self.assertEqual(res["symbol"], "ETH_USDT")

    def test_cap_stops_growth(self):
        saved = market_feed.SYMBOLS_CAP
        market_feed.SYMBOLS_CAP = len(self.feed.symbols)
        try:
            res = asyncio.run(self.feed.add_symbol("NEWCOIN_USDT", force=True))
            self.assertEqual(res.get("error"), "full")
            self.assertNotIn("NEWCOIN_USDT", self.feed.symbols)
            # повтор уже лежащей пары кап не трогает
            again = asyncio.run(self.feed.add_symbol("BTC_USDT", force=True))
            self.assertTrue(again["added"])
        finally:
            market_feed.SYMBOLS_CAP = saved

    def test_route_rejects_anonymous_force_and_html(self):
        denied = asyncio.run(server.api_symbol_add(
            _request("<img src=x>", force=True), "<img src=x>", True))
        self.assertEqual(denied.status_code, 403)
        bad = asyncio.run(server.api_symbol_add(
            _request("<img src=x>"), "<img src=x>", False))
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(any("<" in s for s in self.feed.symbols))

    def test_route_allows_admin_force_of_a_real_pair(self):
        user = server.account_store.upsert_telegram_user({
            "id": 1001, "first_name": "Ada",
        })
        token = server.account_store.create_session(user["id"])
        cookie = f"{server.COOKIE_SID}={token}"
        ok = asyncio.run(server.api_symbol_add(
            _request("SOL_USDT", force=True, cookie=cookie),
            "SOL_USDT", True))
        body = ok if isinstance(ok, dict) else ok.body
        if isinstance(ok, dict):
            self.assertTrue(ok.get("added"), ok)
        else:
            self.assertEqual(ok.status_code, 200, body)
        self.assertIn("SOL_USDT", self.feed.symbols)

    def test_security_headers_and_body_limit(self):
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(server.SecurityHeadersMiddleware(app)(
            {"type": "http", "scheme": "http", "headers": []}, receive, send))
        headers = {k.lower(): v for k, v in sent[0]["headers"]}
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertIn(b"frame-ancestors", headers[b"content-security-policy"])
        self.assertEqual(headers[b"x-frame-options"], b"SAMEORIGIN")
        self.assertNotIn(b"strict-transport-security", headers)

        rejected = []

        async def send413(message):
            rejected.append(message)

        async def big_receive():
            return {"type": "http.request", "body": b"x" * 80, "more_body": False}

        mw = server.MaxBodyMiddleware(app, max_bytes=64)
        # минимум класса — 1024, поэтому проверяем Content-Length-отказ отдельно
        asyncio.run(server.MaxBodyMiddleware(app, max_bytes=1024)(
            {"type": "http", "method": "POST", "scheme": "http",
             "headers": [(b"content-length", b"999999")]},
            big_receive, send413))
        self.assertEqual(rejected[0]["status"], 413)

    def test_client_escapes_symbol_label(self):
        app = open(os.path.join(HERE, "static", "app.js"), encoding="utf-8").read()
        account = open(os.path.join(HERE, "static", "account.js"), encoding="utf-8").read()
        self.assertIn("escapeHtml(o.label)", app)
        self.assertIn("escapeHtml(c.symbol)", app)
        self.assertIn("escapeHtml(pretty(c.symbol))", app)
        self.assertIn("esc(sym === \"ALL\"", account)

    def test_cors_is_not_a_star(self):
        self.assertNotIn("*", server.CORS_ORIGINS)
        self.assertTrue(server.CORS_ORIGINS)
        self.assertTrue(all(o.startswith("http") for o in server.CORS_ORIGINS))

    def test_secret_is_not_the_published_default(self):
        self.assertNotEqual(server.SECRET, "liqscope-change-me")
        self.assertGreaterEqual(len(server.SECRET), 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
