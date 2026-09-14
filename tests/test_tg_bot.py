"""Меню бота: кнопки меняются на месте, везде «Назад»."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# tg_bot тянет aiohttp только для HTTP; в офлайн-тесте подменяем модуль.
if "aiohttp" not in sys.modules:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        import types
        fake = types.ModuleType("aiohttp")

        class _TO:
            def __init__(self, total=None):
                self.total = total

        class _CS:
            def __init__(self, *a, **k):
                pass

            async def close(self):
                return None

        fake.ClientTimeout = _TO
        fake.ClientSession = _CS
        sys.modules["aiohttp"] = fake

from accounts import Store  # noqa: E402
from tg_bot import TelegramBot  # noqa: E402
HAVE = True


def _btns(markup: dict) -> list:
    rows = (markup or {}).get("inline_keyboard") or []
    return [b.get("text") for row in rows for b in row]


def _datas(markup: dict) -> list:
    rows = (markup or {}).get("inline_keyboard") or []
    return [b.get("callback_data") for row in rows for b in row]


@unittest.skipIf(not HAVE, "aiohttp/accounts")
class BotMenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"),
                           secret="s", admin_ids=[1001])
        self.bot = TelegramBot("0:x", self.store)
        self.user = self.store.upsert_telegram_user({
            "id": 2002, "username": "bob", "first_name": "Bob",
        })
        self.admin = self.store.upsert_telegram_user({
            "id": 1001, "username": "boss", "first_name": "Ada",
        })

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_main_menu_has_no_back(self):
        kb = self.bot._menu(self.user)
        self.assertNotIn("← Назад", _btns(kb))
        self.assertIn("cabinet", _datas(kb))

    def test_leaf_screens_have_back_to_menu(self):
        for data in ("cabinet", "stats", "health", "liq", "terminal"):
            _text, kb = self.bot._screen(self.user, data)
            self.assertEqual(_btns(kb), ["← Назад"], data)
            self.assertEqual(_datas(kb), ["nav:home"], data)

    def test_services_and_admin_have_back(self):
        _t, skb = self.bot._screen(self.user, "services")
        self.assertIn("← Назад", _btns(skb))
        self.assertIn("nav:home", _datas(skb))
        _t, akb = self.bot._screen(self.admin, "admin")
        self.assertIn("← Назад", _btns(akb))
        self.assertIn("nav:home", _datas(akb))
        self.assertIn("a:health", _datas(akb))

    def test_admin_leaves_back_to_admin(self):
        for data in ("users", "visits", "broadcast", "a:health"):
            _t, kb = self.bot._screen(self.admin, data)
            self.assertEqual(_datas(kb), ["nav:admin"], data)

    def test_back_returns_main_keyboard(self):
        for data in ("nav:home", "menu", "back"):
            _t, kb = self.bot._screen(self.user, data)
            self.assertIn("cabinet", _datas(kb), data)
            self.assertNotIn("← Назад", _btns(kb), data)

    def test_callback_edits_same_message(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        cb = {
            "id": "cb1",
            "from": {"id": 2002, "username": "bob", "first_name": "Bob"},
            "data": "stats",
            "message": {"message_id": 77, "chat": {"id": 2002}},
        }
        asyncio.run(self.bot._on_callback(cb))
        methods = [m for m, _ in calls]
        self.assertIn("answerCallbackQuery", methods)
        self.assertIn("editMessageText", methods)
        self.assertNotIn("sendMessage", methods)
        self.assertLess(methods.index("answerCallbackQuery"),
                        methods.index("editMessageText"))
        ans = [p for m, p in calls if m == "answerCallbackQuery"][0]
        self.assertNotIn("text", ans)
        edit = [p for m, p in calls if m == "editMessageText"][0]
        self.assertEqual(edit["message_id"], 77)
        self.assertEqual(edit["chat_id"], 2002)
        self.assertIn("← Назад", _btns(edit.get("reply_markup")))

    def test_back_callback_edits_to_main(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        cb = {
            "id": "cb2",
            "from": {"id": 2002, "username": "bob", "first_name": "Bob"},
            "data": "nav:home",
            "message": {"message_id": 88, "chat": {"id": 2002}},
        }
        asyncio.run(self.bot._on_callback(cb))
        edit = [p for m, p in calls if m == "editMessageText"][0]
        self.assertEqual(edit["message_id"], 88)
        self.assertIn("cabinet", _datas(edit.get("reply_markup")))
        self.assertNotIn("← Назад", _btns(edit.get("reply_markup")))

    def test_not_modified_is_success_and_retries(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "editMessageText":
                t = str((payload or {}).get("text") or "")
                if t.endswith("\u200b"):
                    return {"ok": True}
                return {"ok": False, "description":
                        "Bad Request: message is not modified: specified new "
                        "message content and reply markup are exactly the same"}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        ok = asyncio.run(self.bot.edit(2002, 1, "привет", self.bot._menu(self.user)))
        self.assertTrue(ok)
        edits = [p for m, p in calls if m == "editMessageText"]
        self.assertEqual(len(edits), 2)
        self.assertTrue(str(edits[1]["text"]).endswith("\u200b"))


if __name__ == "__main__":
    unittest.main()
