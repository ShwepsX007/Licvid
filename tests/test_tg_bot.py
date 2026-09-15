"""Меню бота: правка того же сообщения, панель внизу."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
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


def _strip_anchors(text: str) -> str:
    """Убирает <a href="...">…</a> целиком — остаётся только видимый текст."""
    import re
    return re.sub(r"<a\s+href=\"[^\"]*\">.*?</a>", "", text, flags=re.S)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    async def json(self, content_type=None):
        return self._p

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    """Заглушка aiohttp-сессии: отдаёт заранее заданные ответы по порядку."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.posts = 0

    def post(self, url, json=None, timeout=None):
        self.posts += 1
        return _Resp(self.seq.pop(0))


async def _always_true(*a, **k):
    return True


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
        kb = self.bot._reply_kb(self.user)
        texts = [b.get("text") for row in kb["keyboard"] for b in row]
        self.assertNotIn("← Назад", texts)
        self.assertTrue(any("Кабинет" in t for t in texts))
        self.assertTrue(kb.get("is_persistent"))
        self.assertNotIn("cabinet", _datas(kb))

    def test_leaf_screens_use_reply_panel(self):
        for data in ("cabinet", "stats", "health", "liq"):
            text, kb = self.bot._screen(self.user, data)
            rows = kb.get("keyboard") or []
            texts = [b.get("text") for row in rows for b in row]
            self.assertTrue(kb.get("is_persistent"), data)
            self.assertTrue(any("Кабинет" in t for t in texts), data)
            self.assertNotIn("← Назад", texts)
            self.assertFalse(kb.get("inline_keyboard"), data)

    def test_terminal_screen_opens_site_directly(self):
        """⚡ Терминал — сразу на /terminal: URL-кнопка, без лишнего звука."""
        text, kb = self.bot._screen(self.user, "terminal")
        rows = kb.get("inline_keyboard") or []
        urls = [b.get("url") for row in rows for b in row if b.get("url")]
        self.assertIn("https://liqscope.online/terminal", urls)
        # текст прячет ссылку в слово, голого URL вне <a> нет
        self.assertIn('<a href="https://liqscope.online/terminal"', text)
        self.assertNotIn("https://", _strip_anchors(text))

    def test_screens_hide_urls_in_clickable_words(self):
        """Все ссылки в экранных уведомлениях спрятаны в кликабельные слова;
        в конце каждого — красиво оформленная ссылка на сайт."""
        screens = ("cabinet", "stats", "health", "liq", "terminal",
                   "services", "nav:home", "help")
        for data in screens:
            text, _kb = self.bot._screen(self.user, data)
            self.assertNotIn("https://", _strip_anchors(text), data)
            self.assertIn('<a href="https://liqscope.online"', text, data)
        # админские экраны тоже с футером
        for data in ("admin", "users", "visits"):
            text, _kb = self.bot._screen(self.admin, data)
            self.assertNotIn("https://", _strip_anchors(text), data)
            self.assertIn('<a href="https://liqscope.online"', text, data)

    def test_alerts_screen_from_services(self):
        text, kb = self.bot._screen(self.user, "svc:alerts")
        self.assertIn("Алерты", text)
        self.assertIn("al:on", _datas(kb))
        self.assertIn("al:m:liq", _datas(kb))
        self.assertIn("services", _datas(kb))
        cfg = self.bot._alert_cfg(self.user)
        cfg["enabled"] = True
        cfg["watch"] = ["liq"]
        cfg["window_min"] = 15
        self.bot._alert_save(self.user, cfg)
        text2, _kb2 = self.bot._screen(self.user, "svc:alerts")
        self.assertIn("включён", text2)

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
            texts = [b.get("text") for row in (kb.get("keyboard") or []) for b in row]
            self.assertTrue(any("Кабинет" in t for t in texts), data)
            self.assertTrue(kb.get("is_persistent"), data)

    def test_callback_edits_same_message(self):
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
        self.assertIn("editMessageText", methods)
        self.assertNotIn("sendMessage", methods)
        self.assertNotIn("deleteMessage", methods)
        self.assertLess(methods.index("answerCallbackQuery"),
                        methods.index("editMessageText"))
        ans = [p for m, p in calls if m == "answerCallbackQuery"][0]
        self.assertNotIn("text", ans)
        edited = [p for m, p in calls if m == "editMessageText"][0]
        self.assertEqual(edited["chat_id"], 2002)
        self.assertEqual(edited["message_id"], 77)
        self.assertIn("Рынок", edited["text"])
        self.assertEqual(self.bot._menu_msg[2002], 77)

    def test_back_callback_edits_home(self):
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
        self.assertNotIn("sendMessage", [m for m, _ in calls])
        self.assertNotIn("deleteMessage", [m for m, _ in calls])
        edited = [p for m, p in calls if m == "editMessageText"][0]
        self.assertEqual(edited["message_id"], 88)
        self.assertIn("кнопки внизу", edited["text"])
        self.assertEqual((edited.get("reply_markup") or {}).get("inline_keyboard"), [])

    def test_start_edits_second_time(self):
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
        self.assertEqual(self.bot._menu_msg[2002], 11)
        self.assertEqual([m for m, _ in calls if m == "sendMessage"], ["sendMessage"])
        self.assertIn("editMessageText", [m for m, _ in calls])
        self.assertNotIn("deleteMessage", [m for m, _ in calls])
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertTrue(sent.get("disable_notification"))

    def test_menu_has_channel_url(self):
        kb = self.bot._reply_kb(self.user)
        texts = [b.get("text") for row in kb["keyboard"] for b in row]
        self.assertTrue(any("Канал" in t for t in texts))
        self.assertEqual(self.bot._reply_cmd("👤 Кабинет"), "cabinet")
        self.assertEqual(self.bot._reply_cmd("🔔 Алерты"), "al")
        self.assertEqual(self.bot.bot_url(), "https://t.me/LiqScopeBot")

    def test_public_url_defaults_to_domain(self):
        from tg_bot import normalize_public_url
        self.assertEqual(normalize_public_url(""), "https://liqscope.online")
        self.assertEqual(normalize_public_url("http://liqscope.online:8000"),
                         "https://liqscope.online")
        self.assertEqual(normalize_public_url("http://78.17.66.215:8000/"),
                         "https://liqscope.online")
        self.assertEqual(self.bot.site_url("/cabinet"),
                         "https://liqscope.online/cabinet")
        self.assertEqual(self.bot.terminal_url(),
                         "https://liqscope.online/terminal")

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

    def test_channel_check_unavailable_does_not_lock_users(self):
        """getChatMember падает (бот не админ канала) — меню не запирается."""
        self.bot._channel_id_cfg = "-100111"
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "getChatMember":
                return {"ok": False,
                        "description": "Bad Request: chat not found"}
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 6}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        asyncio.run(self.bot._cmd_start(2002, self.user, "/start"))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        # вместо экрана «Сначала канал» — обычное приветствие с меню
        self.assertNotIn("Подписаться", str(sent.get("reply_markup")))
        self.assertIn("Привет", str(sent.get("text")))

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
        self.assertNotIn("sendMessage", [m for m, _ in calls])
        edited = [p for m, p in calls if m == "editMessageText"][0]
        self.assertEqual(edited["message_id"], 3)
        self.assertIn("LiqScope", edited["text"])

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
        captured = {}

        async def fake_photo(*_a, **_k):
            calls.append("photo")
            captured["caption"] = _k.get("caption") or (_a[2] if len(_a) > 2 else "")
            captured["markup"] = _k.get("markup") if "markup" in _k else (
                _a[3] if len(_a) > 3 else None)
            return 11

        async def fake_send(*_a, **_k):
            calls.append("text")
            return 22

        self.bot.send_photo = fake_photo  # type: ignore
        self.bot.send = fake_send  # type: ignore
        ok = asyncio.run(self.bot.post_channel_digest())
        self.assertTrue(ok)
        self.assertEqual(calls, ["photo"])
        cap = captured.get("caption") or ""
        self.assertIn("https://liqscope.online", cap)
        self.assertIn("t.me/LiqScopeBot", cap)
        markup = captured.get("markup") or {}
        btns = [b for row in (markup.get("inline_keyboard") or []) for b in row]
        texts = [b.get("text") for b in btns]
        urls = [b.get("url") for b in btns]
        self.assertIn("liqscope", texts)
        self.assertIn("https://liqscope.online", urls)
        self.assertTrue(any("t.me/" in (u or "") for u in urls))

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

    def test_admin_kb_has_templates(self):
        self.assertIn("a:tpl", _datas(self.bot._admin_kb()))

    def test_admin_adds_head_via_bot(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 55}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        n0 = len(self.store.list_digest_heads())
        self.bot._wait_tpl[1001] = "head"
        asyncio.run(self.bot._on_update({
            "update_id": 2,
            "message": {
                "message_id": 10,
                "chat": {"id": 1001},
                "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
                "text": "☕ Новый заход за {h}ч. Цифры ниже.",
            },
        }))
        texts = [h["text"] for h in self.store.list_digest_heads()]
        self.assertEqual(len(texts), n0 + 1)
        self.assertTrue(any("Новый заход" in t for t in texts))
        self.assertNotIn(1001, self.bot._wait_tpl)
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("добавлена", sent["text"].lower())

    def test_tpl_delete_head_callback(self):
        hid = self.store.add_digest_head("удали меня")["id"]
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 70}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        cb = {
            "id": "cbt",
            "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
            "data": f"a:th:{hid}",
            "message": {"message_id": 4, "chat": {"id": 1001}},
        }
        asyncio.run(self.bot._on_callback(cb))
        texts = [h["text"] for h in self.store.list_digest_heads()]
        self.assertNotIn("удали меня", texts)

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

    def test_liq_tape_is_big_rich_and_fits(self):
        """Лента: максимум событий, время, биржи, суммы; влезает в лимит."""
        import time as _time
        now = _time.time()
        events = []
        for i in range(300):
            events.append({
                "symbol": ("BTC_USDT" if i % 3 == 0 else
                           "ETH_USDT" if i % 3 == 1 else "SOL_USDT"),
                "exchange": ("binance" if i % 2 == 0 else "bybit"),
                "side": "SELL" if i % 4 else "BUY",
                "usd": 1000 + i * 137.5,
                "timestamp": now - i * 7,
            })
        self.bot.liqs_fn = lambda: list(events)
        text = self.bot._liq_text()
        self.assertIn("Лента ликвидаций", text)
        self.assertIn("Binance", text)
        self.assertIn("Bybit", text)
        self.assertIn("BTC/USDT", text)
        # времени и событий много: больше 40 строк ленты, а не 8
        self.assertGreater(text.count("BTC/USDT") + text.count("ETH/USDT")
                           + text.count("SOL/USDT"), 40)
        self.assertRegex(text, r"\d{2}:\d{2}:\d{2}")
        # всё сообщение вместе с HTML укладывается в лимит отправки
        self.assertLessEqual(len(text), 3900)
        # ссылка в конце спрятана в слово
        self.assertIn('<a href="https://liqscope.online"', text)
        self.assertNotIn("https://", _strip_anchors(text))

    def test_liq_tape_empty(self):
        self.bot.liqs_fn = lambda: []
        text = self.bot._liq_text()
        self.assertIn("Лента", text)
        self.assertIn('<a href="https://liqscope.online"', text)

    def test_poll_conflict_notifies_admins(self):
        """409 Conflict: уведомление админам сразу, затем не чаще раза в ~30 мин."""
        sent = []

        async def fake_send(chat_id, text, markup=None, parse="HTML", silent=False):
            sent.append((chat_id, text))
            return len(sent)

        self.bot.send = fake_send  # type: ignore
        self.bot.running = True
        res409 = {"ok": False, "error_code": 409,
                  "description": "Conflict: terminated by other getUpdates request"}
        sleep1 = asyncio.run(self.bot._poll_fail_step(res409))
        self.assertEqual(sleep1, 0.5)          # конфликт: быстрые повторы
        self.assertEqual([c for c, _t in sent], [1001])  # только админ
        self.assertIn("Конфликт", sent[0][1])
        self.assertIn("призрачного", sent[0][1])  # диагноз про старую копию
        self.assertTrue(self.bot._conflict_mode)
        # сразу повторно не спамим
        asyncio.run(self.bot._poll_fail_step(res409))
        self.assertEqual(len(sent), 1)
        self.assertTrue(self.bot.running)
        # через 30 минут напоминание приходит снова
        self.bot._conflict_warned_at -= 2000
        asyncio.run(self.bot._poll_fail_step(res409))
        self.assertEqual(len(sent), 2)

    def test_private_command_echo_deleted(self):
        """Эхо команды/кнопки из лички вычищается — в чате живёт только меню."""
        calls = []

        async def noop_route(upd):
            pass

        async def fake_call(method, payload=None):
            calls.append((method, payload or {}))
            return {"ok": True}

        self.bot._route_message = noop_route  # type: ignore
        self.bot._call = fake_call  # type: ignore
        asyncio.run(self.bot._on_update({
            "message": {"message_id": 77,
                        "chat": {"id": 1001, "type": "private"},
                        "from": {"id": 1001},
                        "text": "Плиты"},
        }))
        self.assertEqual(calls, [("deleteMessage",
                                  {"chat_id": 1001, "message_id": 77})])

    def test_poll_conflict_webhook_reason(self):
        async def fake_send(chat_id, text, markup=None, parse="HTML", silent=False):
            fake_send.text = text
            return 1

        self.bot.send = fake_send  # type: ignore
        res = {"ok": False, "error_code": 409,
               "description": "Conflict: can't use getUpdates while webhook is active"}
        asyncio.run(self.bot._poll_fail_step(res))
        self.assertIn("вебхук", getattr(fake_send, "text", ""))

    def test_poll_unauthorized_stops_bot(self):
        self.bot.running = True
        res = {"ok": False, "error_code": 401, "description": "Unauthorized"}
        sleep_s = asyncio.run(self.bot._poll_fail_step(res))
        self.assertEqual(sleep_s, 0.0)
        self.assertFalse(self.bot.running)

    def test_poll_ordinary_error_keeps_polling(self):
        self.bot.running = True
        sleep_s = asyncio.run(self.bot._poll_fail_step(None))
        self.assertEqual(sleep_s, 2.0)
        self.assertTrue(self.bot.running)
        self.assertEqual(self.bot._poll_fails, 1)

    def test_admin_tg_ids(self):
        self.assertEqual(self.store.admin_tg_ids(), [1001])
        self.store.set_banned(self.admin["id"], True)
        self.assertEqual(self.store.admin_tg_ids(), [])

    def test_flood_wait_detects_429(self):
        self.assertEqual(TelegramBot._flood_wait({"ok": True}), 0.0)
        self.assertEqual(TelegramBot._flood_wait(
            {"ok": False, "error_code": 400, "description": "Bad Request"}), 0.0)
        self.assertEqual(TelegramBot._flood_wait(
            {"ok": False, "error_code": 429,
             "description": "Too Many Requests: retry after 7",
             "parameters": {"retry_after": 7}}), 7.0)
        # слишком длинный бан не ждём — иначе встанет опрос
        self.assertEqual(TelegramBot._flood_wait(
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 600}}), 20.0)

    def test_send_retry_on_bad_entities_strips_tags(self):
        """Telegram не принял разметку — повтор уходит без тегов, а не с ними."""
        bot = self.bot
        bot._session = _Session([
            {"ok": False, "error_code": 400,
             "description": "Bad Request: can't parse entities: Unexpected end tag"},
            {"ok": True, "result": {"message_id": 9}},
        ])
        res = asyncio.run(bot.send(2002, "<pre><code>• Binance  $78.46M</code></pre>"))
        self.assertEqual(res, 9)
        self.assertEqual(bot._session.posts, 2)

    def test_strip_html_helper(self):
        from tg_bot import _strip_html
        self.assertEqual(_strip_html("<b>BTC</b> &amp; ETH"), "BTC & ETH")
        self.assertEqual(_strip_html(""), "")

    def test_call_waits_and_retries_on_429(self):
        """429 больше не теряет нажатие: ждём retry_after и повторяем."""
        bot = self.bot
        bot._session = _Session([
            {"ok": False, "error_code": 429,
             "description": "Too Many Requests: retry after 1"},
            {"ok": True, "result": {"message_id": 5}},
        ])
        slept = []
        orig = asyncio.sleep

        async def fake_sleep(sec):
            slept.append(sec)

        asyncio.sleep = fake_sleep  # type: ignore
        try:
            res = asyncio.run(bot._call("sendMessage", {"chat_id": 1, "text": "x"}))
        finally:
            asyncio.sleep = orig  # type: ignore
        self.assertTrue(res and res.get("ok"))
        self.assertEqual(bot._session.posts, 2)
        self.assertEqual(slept, [1.0])

    def test_edit_retry_failure_is_not_reported_as_success(self):
        """Правка, которую Telegram отверг, не должна считаться успешной."""
        bot = self.bot
        bot._session = _Session([
            {"ok": False, "error_code": 400,
             "description": "Bad Request: message is not modified"},
            {"ok": False, "error_code": 400,
             "description": "Bad Request: message to edit not found"},
        ])
        ok = asyncio.run(bot.edit(2002, 77, "текст", bot._menu(self.user)))
        self.assertFalse(ok)
        self.assertEqual(bot._session.posts, 2)  # была попытка повтора

    def test_broken_screen_tells_user_instead_of_silence(self):
        """Раньше исключение экрана глоталось: кнопка «просто не работала»."""
        sent = []

        async def fake_send(chat_id, text, markup=None, parse="HTML", silent=False):
            sent.append(text)
            return 1

        self.bot.send = fake_send  # type: ignore
        self.bot._ensure_channel = _always_true  # type: ignore
        self.bot._screen = lambda user, data: (_ for _ in ()).throw(  # type: ignore
            RuntimeError("boom"))
        asyncio.run(self.bot._route_message({
            "message": {"message_id": 5, "chat": {"id": 2002, "type": "private"},
                        "from": {"id": 2002}, "text": "📊 Статистика"}}))
        self.assertTrue(sent and "не открылся" in sent[0])

    def test_callback_error_tells_user(self):
        sent = []

        async def fake_send(chat_id, text, markup=None, parse="HTML", silent=False):
            sent.append(text)
            return 1

        async def fake_answer(cb_id, text=""):
            return None

        self.bot.send = fake_send  # type: ignore
        self.bot.answer_cb = fake_answer  # type: ignore
        self.bot._ensure_channel = _always_true  # type: ignore
        self.bot._screen = lambda user, data: (_ for _ in ()).throw(  # type: ignore
            RuntimeError("boom"))
        asyncio.run(self.bot._on_callback({
            "id": "cb1", "data": "stats", "from": {"id": 2002},
            "message": {"message_id": 77, "chat": {"id": 2002, "type": "private"}}}))
        self.assertTrue(sent and "не открылся" in sent[0])

    def test_liq_button_survives_dead_store_reads(self):
        """Лента — единственный экран без обращений к Store: он живёт всегда."""
        text, _kb = self.bot._screen(self.user, "liq")
        self.assertIn("Лента", text)

    def test_watchdog_sees_dead_and_stalled_poll(self):
        """Сторож ловит и мёртвую задачу, и зависший опрос."""
        self.bot._last_ok_at = time.time()
        self.assertTrue(self.bot._watchdog_problem())  # задачи опроса нет
        self.bot._task = object()  # type: ignore  # «живая» заглушка

        class _T:  # задача, которая уже завершилась
            def done(self):
                return True

            def exception(self):
                return RuntimeError("boom")

        self.bot._task = _T()  # type: ignore
        self.assertIn("остановилась", self.bot._watchdog_problem())

        class _Alive:
            def done(self):
                return False

        self.bot._task = _Alive()  # type: ignore
        self.bot._last_ok_at = time.time()
        self.assertEqual(self.bot._watchdog_problem(), "")

        import tg_bot as tg
        self.bot._last_ok_at = time.time() - (tg.POLL_STALE_SEC + 60)
        self.assertIn("getUpdates", self.bot._watchdog_problem())

    def test_watchdog_restart_notifies_and_recreates_task(self):
        sent = []

        async def fake_send(chat_id, text, markup=None, parse="HTML", silent=False):
            sent.append((chat_id, text))
            return 1

        async def fake_poll():
            await asyncio.sleep(3600)

        async def scenario():
            self.bot._poll = fake_poll  # type: ignore
            self.bot.send = fake_send  # type: ignore
            self.bot.running = True
            self.bot._conflict_mode = True
            self.bot._last_ok_at = time.time() - 10_000
            self.bot._task = None
            problem = self.bot._watchdog_problem()
            await self.bot._watchdog_restart(problem)
            task = self.bot._task
            self.assertTrue(task is not None and not task.done())
            self.assertFalse(self.bot._conflict_mode)
            self.assertEqual(self.bot._watchdog_restarts, 1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        self.assertEqual([c for c, _t in sent], [1001])
        self.assertIn("перезапущен", sent[0][1])
        self.assertIn("journalctl", sent[0][1])

    def test_poll_status_exposes_liveness(self):
        """running — флаг; по нему нельзя понять, живой ли опрос."""
        st = self.bot.poll_status()
        self.assertFalse(st["task_alive"])
        self.assertIsNone(st["poll_ok_sec"])
        self.assertEqual(st["send_fails"], 0)
        self.bot._last_ok_at = time.time() - 5
        self.bot._last_update_at = time.time() - 7
        st = self.bot.poll_status()
        self.assertGreaterEqual(st["poll_ok_sec"], 4)
        self.assertGreaterEqual(st["last_update_sec"], 6)

    def test_health_and_poll_line_show_stalled_poll(self):
        import tg_bot as tg
        self.bot.health_fn = lambda: {"sources": {}}
        self.bot.ws_clients_fn = lambda: 0

        class _Alive:
            def done(self):
                return False

        self.bot.running = True
        self.bot._task = _Alive()  # type: ignore
        self.bot._last_ok_at = time.time() - (tg.POLL_STALE_SEC + 120)
        self.assertIn("завис", self.bot.poll_line())
        self.assertIn("завис", self.bot.poll_text())
        text = self.bot._health_text()
        self.assertIn("Бот:", text)

    def test_call_429_sets_retry_pause(self):
        """429: Telegram сам говорит, сколько ждать — иначе бот продлевает бан."""
        self.assertEqual(TelegramBot._retry_after(
            {"parameters": {"retry_after": 17}}), 17.0)
        self.assertEqual(TelegramBot._retry_after(
            {"description": "Too Many Requests: retry after 9"}), 9.0)
        self.assertGreaterEqual(TelegramBot._retry_after({}), 1.0)

    def test_start_restores_saved_offset(self):
        async def fake_call(method, payload=None):
            if method == "getMe":
                return {"ok": True, "result": {"id": 42, "username": "liq_bot"}}
            if method == "getUpdates":
                await asyncio.sleep(3600)
                return {"ok": True, "result": []}
            return {"ok": True}

        async def scenario():
            self.bot._call = fake_call  # type: ignore
            self.bot._channel_loop = lambda: asyncio.sleep(3600)  # type: ignore
            self.store.set_setting("bot_offset:42", "555")
            await self.bot.start()
            self.assertTrue(self.bot.running)
            self.assertEqual(self.bot._offset, 555)
            st = self.bot.poll_status()
            self.assertTrue(st["task_alive"])
            self.assertIsNotNone(st["poll_ok_sec"])
            await self.bot.stop()

        asyncio.run(scenario())
        self.assertFalse(self.bot.running)


if __name__ == "__main__":
    unittest.main()
