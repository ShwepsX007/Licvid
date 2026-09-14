"""Меню бота: новое сообщение + удаление старого, везде «Назад»."""
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

    def test_callback_replaces_message(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 101}}
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
        self.assertIn("sendMessage", methods)
        self.assertIn("deleteMessage", methods)
        self.assertNotIn("editMessageText", methods)
        self.assertLess(methods.index("answerCallbackQuery"),
                        methods.index("sendMessage"))
        ans = [p for m, p in calls if m == "answerCallbackQuery"][0]
        self.assertNotIn("text", ans)
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertEqual(sent["chat_id"], 2002)
        self.assertIn("← Назад", _btns(sent.get("reply_markup")))
        deleted = [p for m, p in calls if m == "deleteMessage"][0]
        self.assertEqual(deleted["message_id"], 77)

    def test_back_callback_sends_main_and_drops_old(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 202}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        cb = {
            "id": "cb2",
            "from": {"id": 2002, "username": "bob", "first_name": "Bob"},
            "data": "nav:home",
            "message": {"message_id": 88, "chat": {"id": 2002}},
        }
        asyncio.run(self.bot._on_callback(cb))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("cabinet", _datas(sent.get("reply_markup")))
        self.assertNotIn("← Назад", _btns(sent.get("reply_markup")))
        deleted = [p for m, p in calls if m == "deleteMessage"][0]
        self.assertEqual(deleted["message_id"], 88)

    def test_start_drops_previous_menu(self):
        calls = []
        n = {"id": 10}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        user = self.user
        asyncio.run(self.bot._cmd_start(2002, user, "/start"))
        self.assertEqual(self.bot._menu_msg[2002], 11)
        asyncio.run(self.bot._cmd_start(2002, user, "/start"))
        self.assertEqual(self.bot._menu_msg[2002], 12)
        deleted = [p["message_id"] for m, p in calls if m == "deleteMessage"]
        self.assertEqual(deleted, [11])
        self.assertNotIn("editMessageText", [m for m, _ in calls])

    def test_menu_has_channel_url(self):
        kb = self.bot._menu(self.user)
        urls = []
        for row in kb["inline_keyboard"]:
            for b in row:
                if b.get("url"):
                    urls.append(b["url"])
        self.assertTrue(any("t.me/" in u for u in urls))

    def test_start_requires_channel_when_id_set(self):
        self.bot._channel_id_cfg = "-100111"
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "getChatMember":
                return {"ok": True, "result": {"status": "left"}}
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 5}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        asyncio.run(self.bot._cmd_start(2002, self.user, "/start"))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("Подписаться", str(sent.get("reply_markup")))
        self.assertNotIn("cabinet", _datas(sent.get("reply_markup")))

    def test_check_callback_opens_menu_when_member(self):
        self.bot._channel_id_cfg = "-100111"
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "getChatMember":
                return {"ok": True, "result": {"status": "member"}}
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 9}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        cb = {
            "id": "cbx",
            "from": {"id": 2002, "username": "bob", "first_name": "Bob"},
            "data": "ch:check",
            "message": {"message_id": 3, "chat": {"id": 2002}},
        }
        asyncio.run(self.bot._on_callback(cb))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("cabinet", _datas(sent.get("reply_markup")))

    def test_digest_without_channel_id_is_false(self):
        self.bot._channel_id_cfg = ""
        ok = asyncio.run(self.bot.post_channel_digest())
        self.assertFalse(ok)
        self.assertIn("Перешлите", self.bot._digest_err)
        self.assertIn("Перешлите", self.bot._digest_result_text(False))

    def test_my_chat_member_stores_channel_id(self):
        asyncio.run(self.bot._on_my_chat_member({
            "chat": {"id": -100555, "type": "channel", "title": "Liq"},
            "new_chat_member": {"status": "administrator",
                                "user": {"id": 1, "is_bot": True}},
        }))
        self.assertEqual(self.store.get_setting("channel_id"), "-100555")

    def test_my_chat_member_accepts_supergroup(self):
        asyncio.run(self.bot._on_my_chat_member({
            "chat": {"id": -100777, "type": "supergroup", "title": "Grp"},
            "new_chat_member": {"status": "administrator",
                                "user": {"id": 1, "is_bot": True}},
        }))
        self.assertEqual(self.store.get_setting("channel_id"), "-100777")

    def test_digest_sends_one_message(self):
        self.bot._channel_id_cfg = "-100111"
        calls = []

        async def fake_photo(*_a, **_k):
            calls.append("photo")
            return 11

        async def fake_send(*_a, **_k):
            calls.append("text")
            return 22

        self.bot.send_photo = fake_photo  # type: ignore
        self.bot.send = fake_send  # type: ignore
        ok = asyncio.run(self.bot.post_channel_digest())
        self.assertTrue(ok)
        self.assertEqual(calls, ["photo"])

    def test_digest_long_caption_still_one_message(self):
        self.bot._channel_id_cfg = "-100111"
        import channel_digest
        orig = channel_digest.render_post
        channel_digest.render_post = lambda *_a, **_k: "x" * 2000
        calls = []

        async def fake_photo(*_a, **_k):
            calls.append("photo")
            return 11

        async def fake_send(*_a, **_k):
            calls.append("text")
            return 22

        self.bot.send_photo = fake_photo  # type: ignore
        self.bot.send = fake_send  # type: ignore
        try:
            ok = asyncio.run(self.bot.post_channel_digest())
        finally:
            channel_digest.render_post = orig
        self.assertTrue(ok)
        self.assertEqual(calls, ["text"])

    def test_digest_shows_telegram_error(self):
        self.bot._channel_id_cfg = "-100111"

        async def fake(method, payload=None):
            return {"ok": False, "description": "Forbidden: bot is not a member of the channel chat"}

        self.bot._call = fake  # type: ignore
        ok = asyncio.run(self.bot.post_channel_digest())
        self.assertFalse(ok)
        self.assertIn("not a member", self.bot._digest_err)
        self.assertIn("not a member", self.bot._digest_result_text(False))
        self.assertNotIn("должен быть админом", self.bot._digest_result_text(False))

    def test_admin_forward_binds_channel(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 44}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        upd = {
            "update_id": 1,
            "message": {
                "message_id": 9,
                "chat": {"id": 1001},
                "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
                "text": "ликвидации",
                "forward_from_chat": {
                    "id": -100888, "type": "channel", "title": "LiqScope",
                },
            },
        }
        asyncio.run(self.bot._on_update(upd))
        self.assertEqual(self.store.get_setting("channel_id"), "-100888")
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("привязан", sent["text"].lower())

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
