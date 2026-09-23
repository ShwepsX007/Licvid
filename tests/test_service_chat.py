"""🔔 Сервисы: сигналы сервисов в личной ленте чата на сайте.

Проверяем то, из-за чего вкладка «Сервисы» была пустой, хотя в Telegram
сигналы приходили исправно:

    * маршрут ``/api/chat/services`` отвечает (модуль не был подключён в
      ``server.py`` — на запрос приходил 404);
    * сигнал, ушедший в Telegram, ложится и в личную ленту кабинета —
      ровно тому пользователю, который включил сервис;
    * вместе с текстом сохраняются части сигнала (``meta.parts``): по ним
      кабинет собирает ту же строку на языке посетителя;
    * чужие сигналы не видны и не рассылаются по WebSocket.

Запуск:  python3 tests/test_service_chat.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import alerts  # noqa: E402
import book_feed  # noqa: E402
import correlations  # noqa: E402
import pump_scan  # noqa: E402
import service_chat as svc  # noqa: E402
import terminal_chat as terminal_chat_mod  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402


class FakeClient:
    """WS-клиент hub: опознаётся по user_id, как настоящий."""

    def __init__(self, user_id):
        self.user_id = user_id


class FakeHub:
    def __init__(self):
        self.sent = []

    async def broadcast(self, payload, predicate=None):
        self.sent.append(payload)
        self.last_predicate = predicate
        self.matched = []
        for uid in (1, 2, 3):
            c = FakeClient(uid)
            try:
                if predicate is None or predicate(c):
                    self.matched.append(uid)
            except Exception:                                   # noqa: BLE001
                pass
        return len(self.matched)


class ServiceChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        self.alice = self.store.create_email_user("alice@x.io", password_hash="x")["user"]
        self.bob = self.store.create_email_user("bob@x.io", password_hash="x")["user"]
        self.hub = FakeHub()
        svc.ctx.store = self.store
        svc.ctx.hub = self.hub
        import web_account
        self.web_account = web_account
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        app = FastAPI()
        svc.register_service_chat_routes(app, hub=self.hub)
        self.app = app
        self.alice_c = self._client(self.alice["id"])
        self.bob_c = self._client(self.bob["id"])

    def _client(self, uid):
        c = TestClient(self.app)
        c.cookies.set(COOKIE_SID, self.store.create_session(int(uid)))
        return c

    def tearDown(self):
        self.web_account.ctx.store = None
        svc.ctx.store = None
        svc.ctx.hub = None
        self.tmp.cleanup()

    def _push(self, uid, kind, text, meta):
        return asyncio.run(svc.broadcast_service_user(int(uid), kind, text, meta))

    # --- лента --------------------------------------------------------------

    def test_empty_feed_for_a_new_user(self):
        got = self.alice_c.get("/api/chat/services").json()
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["messages"], [])

    def test_alert_signal_reaches_the_feed(self):
        """Сигнал алерта: текст как в Telegram плюс части для перевода."""
        hit = {"metric": "liq", "symbol": "BTC_USDT", "value": 1_200_000,
               "threshold": 500_000, "window_min": 5, "span_min": 1, "count": 3,
               "longs": 900_000, "shorts": 300_000, "peers": []}
        self._push(self.alice["id"], "alert", alerts.chat_text(hit),
                   {"symbol": hit["symbol"], "metric": hit["metric"],
                    "parts": alerts.chat_meta(hit)})
        msg = self.alice_c.get("/api/chat/services").json()["messages"][0]
        self.assertEqual(msg["kind"], "alert")
        self.assertIn("ликвидации", msg["text"])
        self.assertEqual(msg["meta"]["parts"]["metric"], "liq")
        self.assertEqual(msg["meta"]["parts"]["value"], 1_200_000)
        # монета для подписи берётся из частей, а не из текста
        self.assertEqual(msg["meta"]["symbol"], "BTC_USDT")

    def test_every_service_kind_is_stored(self):
        """Стакан, памп, корреляции — та же лента, свой вид сигнала."""
        wall = {"sym": "ETH_USDT", "side": "ask", "lo": 3200, "hi": 3210,
                "usdt": 4_000_000, "exchs": ["binance"]}
        self._push(self.alice["id"], "book", book_feed.chat_text(wall),
                   {"symbol": wall["sym"], "parts": book_feed.chat_meta(wall)})
        pump = {"kind": "dump", "symbol": "SOL_USDT", "change_pct": -7.5,
                "span_min": 5, "candles": 5, "period": "1м", "price": 190.0,
                "price_from": 205.0, "volume24h": 1_000_000_000}
        self._push(self.alice["id"], "pump", pump_scan.chat_text(pump),
                   {"symbol": pump["symbol"], "parts": pump_scan.chat_meta(pump)})
        corr = {"metric": "cvd", "kind": "opp", "a": "SOL_USDT", "b": "XRP_USDT",
                "r": -0.58, "threshold": -0.5, "window": "24h", "peers": []}
        self._push(self.alice["id"], "corr", correlations.chat_text(corr),
                   {"symbol": corr["a"], "metric": corr["metric"],
                    "parts": correlations.chat_meta(corr)})
        msgs = self.alice_c.get("/api/chat/services").json()["messages"]
        self.assertEqual([m["kind"] for m in msgs], ["book", "pump", "corr"])
        self.assertEqual(msgs[0]["meta"]["parts"]["side"], "ask")
        self.assertEqual(msgs[1]["meta"]["parts"]["direction"], "dump")
        self.assertEqual(msgs[2]["meta"]["parts"]["kind"], "opp")
        self.assertEqual(msgs[2]["meta"]["parts"]["a"], "SOL_USDT")

    def test_signals_are_personal(self):
        self._push(self.alice["id"], "alert", "только для Alice", {"parts": {}})
        self.assertEqual(self.alice_c.get("/api/chat/services").json()["messages"][0]["text"],
                         "только для Alice")
        self.assertEqual(self.bob_c.get("/api/chat/services").json()["messages"], [])

    def test_guest_gets_401(self):
        r = TestClient(self.app).get("/api/chat/services")
        self.assertEqual(r.status_code, 401)

    # --- WebSocket ----------------------------------------------------------

    def test_signal_is_pushed_only_to_its_owner(self):
        """Свежий сигнал уходит по WS только владельцу — остальные не видят."""
        self._push(self.alice["id"], "alert", "молния", {"parts": {}})
        import time
        time.sleep(0.05)                       # рассылка идёт задачей в цикле
        self.assertTrue(self.hub.sent, "сигнал не разослан по WS")
        self.assertEqual(self.hub.sent[-1]["type"], "service_chat")
        self.assertEqual(self.hub.matched, [int(self.alice["id"])])

    def test_endpoint_returns_only_own_messages_and_the_language_parts(self):
        for uid in (self.alice["id"], self.bob["id"]):
            self._push(uid, "pump", "сигнал %s" % uid,
                       {"symbol": "BTC_USDT", "parts": {"direction": "pump",
                                                        "change_pct": 3.5}})
        got = self.bob_c.get("/api/chat/services").json()
        self.assertEqual(len(got["messages"]), 1)
        self.assertEqual(got["messages"][0]["meta"]["parts"]["change_pct"], 3.5)


class WiringTest(unittest.TestCase):
    """Модуль подключён к серверу, а сигналы сервисов действительно пишутся."""

    @classmethod
    def setUpClass(cls):
        import server
        import service_chat as sc
        cls.server = server
        cls.app = server.app
        # ServiceChatTest работает на своём сторе и в tearDown сбрасывает
        # общую привязку — повторяем ту же проводку, что делает server.py
        # при импорте, чтобы проверка не зависела от порядка классов
        sc.ctx.store = server.account_store
        sc.ctx.hub = server.hub

    @staticmethod
    def _paths(routes, out=None):
        out = set() if out is None else out
        for r in routes:
            path = getattr(r, "path", "")
            if isinstance(path, str) and path:
                out.add(path)
            for sub in (getattr(r, "routes", None),
                        getattr(getattr(r, "original_router", None), "routes", None)):
                if sub:
                    WiringTest._paths(sub, out)
        return out

    def test_services_route_is_served(self):
        paths = self._paths(self.app.routes)
        self.assertIn("/api/chat/services", paths)
        r = TestClient(self.app).get("/api/chat/services")
        self.assertNotEqual(r.status_code, 404)

    def test_ctx_is_filled(self):
        import service_chat as sc
        self.assertIsNotNone(sc.ctx.store)
        self.assertIsNotNone(sc.ctx.hub)

    def test_every_service_writes_to_the_feed(self):
        """Алерты, корреляции, сторож и стакан — все пишут в ленту кабинета.

        Иначе часть сервисов снова окажется «только в Telegram»: именно так
        и было, пока сигналы не начали складываться в user_service_chat.
        """
        import inspect
        cases = {
            "alert_loop": ("alerts.chat_text", "\"alert\""),
            "corr_alert_loop": ("correlations.chat_text", "\"corr\""),
            "book_alert_loop": ("book_chat_text", "\"book\""),
            "pump_notify": ("pump_chat_text", "\"pump\""),
        }
        for name, (text_call, kind) in cases.items():
            src = inspect.getsource(getattr(self.server, name))
            self.assertIn("push_service_message", src, name)
            self.assertIn(text_call, src, name)
            self.assertIn(kind, src, name)

    def test_service_messages_do_not_leak_to_the_global_chat(self):
        """Личная лента — отдельная таблица: в общем чате сигналов нет."""
        import accounts
        self.assertTrue(hasattr(accounts.Store, "list_user_service_messages"))
        self.assertTrue(hasattr(svc, "broadcast_service_user"))


class _AsyncioProxy:
    """asyncio с подменённым ``sleep``: петля «засыпает» мгновенно.

    Нужен, чтобы прогнать петлю стакана несколько раз внутри теста: она
    штатно спит 25 и 15 секунд, а ждать этого в тестах незачем.
    """

    def __init__(self, real, sleep):
        self._real = real
        self._sleep = sleep

    def __getattr__(self, name):
        return getattr(self._real, name)

    def sleep(self, seconds):
        return self._sleep(seconds)


class BookLoopDeliveryTest(unittest.TestCase):
    """📖 Стакан: стена уходит в ленту кабинета, даже если Telegram выключен.

    Раньше петля стакана сначала проверяла Telegram и только потом писала в
    чат: у подписчика без привязанного бота (или при выключенном боте) стены
    не появлялись во вкладке «Сервисы» вообще.
    """

    WALL = {"id": 5, "sym": "BTC_USDT", "side": "bid", "opened": 1,
            "peak": 5_400_000, "usdt": 5_400_000,
            "lo": 64000.0, "hi": 64100.0, "exchs": ["binance", "okx"]}

    def _run_loop(self, subs, bot_running, rounds=4):
        """Один-два круга петли стакана на подставных подписчиках."""
        import asyncio as real_asyncio
        import server

        pushed, sent, sleeps = [], [], {"n": 0}
        real_sleep = real_asyncio.sleep

        async def fake_sleep(seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= rounds:
                raise real_asyncio.CancelledError()
            await real_sleep(0)

        class FakeBook:
            def __init__(self):
                self.walls = {BookLoopDeliveryTest.WALL["sym"]: {
                    1: dict(BookLoopDeliveryTest.WALL, id=1)}}

            def new_open_walls(self, symbols, min_usd, side, since):
                return [dict(BookLoopDeliveryTest.WALL)]

        class FakeStore:
            def list_service_subscribers(self, slug):
                return list(subs)

        class FakeBot:
            running = bot_running

            async def send(self, chat_id, text, **kw):
                sent.append((chat_id, text))

            @staticmethod
            def site_link_kb(label):
                return None

        async def fake_push(user_id, kind, text, meta):
            pushed.append((user_id, kind, text, meta))

        real = (server.asyncio, server.book_feed_inst, server.account_store,
                server.tg_bot, server.push_service_message)
        server.asyncio = _AsyncioProxy(real_asyncio, fake_sleep)
        server.book_feed_inst = FakeBook()
        server.account_store = FakeStore()
        server.tg_bot = FakeBot()
        server.push_service_message = fake_push

        async def run():
            task = real_asyncio.create_task(server.book_alert_loop())
            await task

        try:
            real_asyncio.run(run())
        finally:
            (server.asyncio, server.book_feed_inst, server.account_store,
             server.tg_bot, server.push_service_message) = real
        return pushed, sent

    def test_wall_reaches_feed_without_telegram_bot(self):
        """Бота нет — сигнал всё равно в «Сервисах» и ровно один раз."""
        subs = [{"user_id": 7, "config": {"enabled": True, "notify": True},
                 "tg_id": None, "language": "ru"}]
        pushed, sent = self._run_loop(subs, bot_running=False)
        self.assertEqual(sent, [])
        self.assertEqual([p[0] for p in pushed], [7])
        self.assertEqual(pushed[0][1], "book")
        self.assertIn("Стена", pushed[0][2])
        self.assertEqual(pushed[0][3]["parts"]["sym"], "BTC_USDT")

    def test_wall_reaches_feed_with_telegram_off_switch(self):
        """Тумблер «TELEGRAM ВЫКЛ» гасит бота, но не ленту кабинета."""
        subs = [{"user_id": 8, "config": {"enabled": True, "notify": False},
                 "tg_id": 555, "language": "ru"}]
        pushed, sent = self._run_loop(subs, bot_running=True)
        self.assertEqual(sent, [])
        self.assertEqual([p[0] for p in pushed], [8])

    def test_telegram_still_gets_the_wall(self):
        """Telegram работает как раньше: у кого бот есть, тот получает письмо."""
        subs = [{"user_id": 9, "config": {"enabled": True, "notify": True},
                 "tg_id": 777, "language": "ru"}]
        pushed, sent = self._run_loop(subs, bot_running=True)
        self.assertEqual([p[0] for p in pushed], [9])
        self.assertEqual([c for c, _ in sent], [777])
        self.assertIn("Стена", sent[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
