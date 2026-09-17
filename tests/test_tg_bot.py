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
            # tg_bot задаёт и sock_connect/sock_read — заглушка обязана их принимать
            def __init__(self, total=None, **kw):
                self.total = total
                for k, v in kw.items():
                    setattr(self, k, v)

        class _CS:
            def __init__(self, *a, **k):
                pass

            async def close(self):
                return None

        class _TCP:
            def __init__(self, *a, **k):
                pass

        fake.ClientTimeout = _TO
        fake.ClientSession = _CS
        fake.TCPConnector = _TCP
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
        # «☰ Меню» — постоянная кнопка: если панель пропала, она её вернёт
        self.assertIn("☰ Меню", texts)
        self.assertEqual(self.bot._reply_cmd("☰ Меню"), "help")
        self.assertEqual(self.bot._reply_cmd("меню"), "help")
        self.assertEqual(self.bot._reply_cmd("/menu"), "help")

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

    def test_correlations_screen_live(self):
        """Корреляции валют: экран показывает связи, направления и настройки."""
        from correlations import build
        pic = build([], window="24h", metric="liq", now=1789669572.0)
        pic.update({
            "symbols": ["BTC_USDT", "ETH_USDT"],
            "pairs": {"liq": [{"a": "BTC_USDT", "b": "ETH_USDT", "r": 0.83}],
                      "cvd": []},
            "flows": {"liquidated_long": ["BTC_USDT"],
                      "liquidated_short": ["ETH_USDT"],
                      "cvd_sellers": ["BTC_USDT"], "cvd_buyers": ["ETH_USDT"],
                      "oi_up": ["BTC_USDT"], "oi_down": ["ETH_USDT"]},
        })
        calls = []

        def fn(window="24h", metric="liq"):
            calls.append((window, metric))
            return pic

        self.bot.correlations_fn = fn
        text, kb = self.bot._screen(self.user, "svc:correlations")
        self.assertIn("Корреляции валют", text)
        self.assertIn("BTC, ETH", text)
        self.assertIn("Где выносило шорты", text)
        self.assertIn("OI падает", text)
        datas = _datas(kb)
        self.assertIn("cor:w:4h", datas)
        self.assertIn("cor:m:cvd", datas)
        self.assertIn("cor:now", datas)
        self.assertIn("services", datas)
        self.assertTrue(any(b.get("url", "").endswith("/cabinet#correlations")
                            for row in kb["inline_keyboard"] for b in row))
        self.assertEqual(calls[-1], ("24h", "liq"))

    def test_correlations_screen_without_history(self):
        self.bot.correlations_fn = lambda window="24h", metric="liq": {}
        text, _kb = self.bot._screen(self.user, "svc:correlations")
        self.assertIn("История ещё собирается", text)

    def test_correlations_choice_is_saved(self):
        import asyncio
        sent = []

        async def fake_reply(chat_id, text, markup=None, message_id=None, **kw):
            sent.append(text)
            return True

        self.bot.reply = fake_reply
        self.bot.correlations_fn = lambda window="24h", metric="liq": {}
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            self.bot._on_corr_cb(1, self.user, "cor:w:7d", None))
        cfg = self.bot._corr_cfg(self.user)
        self.assertEqual(cfg["window"], "7d")
        row = self.store.get_user_service(self.user["id"], "correlations")
        self.assertTrue(row["enabled"])
        self.assertEqual(row["config"]["window"], "7d")

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

    def test_menu_after_alerts_moves_to_bottom(self):
        """После алертов меню открывается внизу, старое сообщение убирается."""
        calls = []
        n = {"id": 100}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        self.bot._menu_msg.clear()
        self.bot._last_msg.clear()
        kb = self.bot._reply_kb(self.user)
        # первое меню — сообщение 101, оно же последнее
        asyncio.run(self.bot.show_menu(2002, "меню", kb))
        self.assertEqual(self.bot._menu_msg[2002], 101)
        calls.clear()
        asyncio.run(self.bot.show_menu(2002, "кабинет", kb))
        self.assertEqual([m for m, _ in calls], ["editMessageText"])
        # прилетели сигналы алертов: сообщения 102 и 103
        asyncio.run(self.bot.send(2002, "алерт 1", markup=self.bot.site_link_kb()))
        asyncio.run(self.bot.send(2002, "алерт 2", markup=self.bot.site_link_kb()))
        calls.clear()
        asyncio.run(self.bot.show_menu(2002, "меню снова", kb))
        methods = [m for m, _ in calls]
        self.assertIn("sendMessage", methods)        # свежий экран внизу
        self.assertNotIn("editMessageText", methods)  # уехавшее вверх не трогаем
        self.assertTrue([p for m, p in calls
                         if m == "deleteMessage" and p.get("message_id") == 101], calls)
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertTrue(sent.get("disable_notification"))   # без пуша
        # 101 — меню, 102–103 — алерты, 104 — свежее меню внизу
        self.assertEqual(self.bot._menu_msg[2002], 104)
        # меню снова последнее: следующее нажатие правит его на месте
        calls.clear()
        asyncio.run(self.bot.show_menu(2002, "и снова меню", kb))
        self.assertEqual([m for m, _ in calls], ["editMessageText"])

    def test_menu_button_after_alerts_lands_below(self):
        """Кнопка «☰ Меню» после алертов: новый экран внизу, старый удалён."""
        calls = []
        n = {"id": 200}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        self.bot._menu_msg.clear()
        self.bot._last_msg.clear()
        kb = self.bot._reply_kb(self.user)
        asyncio.run(self.bot.show_menu(2002, "меню", kb))
        old = self.bot._menu_msg[2002]
        # алерт пришёл уже после меню — иначе меню осталось бы внизу
        asyncio.run(self.bot.send(2002, "алерт", markup=self.bot.site_link_kb()))
        calls.clear()
        upd = {"update_id": 1, "message": {
            "message_id": 900, "text": "☰ Меню", "from": {"id": 2002,
                                                          "first_name": "Bob"},
            "chat": {"id": 2002, "type": "private"}}}
        asyncio.run(self.bot._on_update(upd))
        methods = [m for m, _ in calls]
        self.assertIn("sendMessage", methods)
        self.assertNotIn("editMessageText", methods)
        self.assertTrue([p for m, p in calls
                         if m == "deleteMessage" and p.get("message_id") == old], calls)
        self.assertGreater(self.bot._menu_msg[2002], old)
        self.assertFalse(self.bot._stale_menus())     # меню снова внизу

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

    def test_start_links_telegram_to_email_account(self):
        """/start link_<nonce> привязывает Telegram к аккаунту с почтой."""
        from accounts import hash_password
        acc = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"),
                                           first_name="Боб")["user"]
        self.store.mark_email_verified(acc["id"])
        nonce = self.store.new_link_nonce(acc["id"])
        calls = []
        n = {"id": 30}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        asyncio.run(self.bot._cmd_start(2002, self.user, "/start link_" + nonce))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("привязан", sent["text"])
        self.assertIn("bob@mail.ru", sent["text"])
        linked = self.store.get_user(acc["id"])
        self.assertEqual(linked["tg_id"], 2002)
        self.assertTrue(linked["email_verified"])
        self.assertTrue(self.store.link_nonce_status(nonce)["ok"])

    def test_start_link_merges_telegram_account(self):
        """Человек писал боту до привязки — профиль переезжает в аккаунт с почтой."""
        from accounts import hash_password
        acc = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        self.store.mark_email_verified(acc["id"])
        self.store.toggle_user_service(self.user["id"], "alerts", True)
        nonce = self.store.new_link_nonce(acc["id"])
        calls = []
        n = {"id": 40}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        asyncio.run(self.bot._cmd_start(2002, self.user, "/start link_" + nonce))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("перенесён", sent["text"])
        merged = self.store.get_user(acc["id"])
        self.assertEqual(merged["tg_id"], 2002)
        self.assertIn("alerts", self.store.user_service_slugs(acc["id"]))
        self.assertIsNone(self.store.get_user(self.user["id"]))

    def test_start_link_expired_tells_user(self):
        from accounts import hash_password
        acc = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        nonce = self.store.new_link_nonce(acc["id"])
        self.store._db.execute("UPDATE tg_link_nonces SET expires_at=0 WHERE nonce=?", (nonce,))
        self.store._db.commit()
        calls = []
        n = {"id": 50}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        asyncio.run(self.bot._cmd_start(2002, self.user, "/start link_" + nonce))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("устарела", sent["text"])
        self.assertIsNone(self.store.get_user(acc["id"])["tg_id"])

    def test_start_link_rejected_when_tg_taken(self):
        from accounts import hash_password
        first = self.store.create_email_user("f@mail.ru", hash_password("First-Pass-2026"))["user"]
        self.store.mark_email_verified(first["id"])
        second = self.store.create_email_user("s@mail.ru", hash_password("Second-Pass-2026"))["user"]
        self.store.mark_email_verified(second["id"])
        self.store.confirm_tg_link(self.store.new_link_nonce(first["id"]), {"id": 2002})
        nonce = self.store.new_link_nonce(second["id"])
        calls = []
        n = {"id": 60}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        asyncio.run(self.bot._cmd_start(2002, self.user, "/start link_" + nonce))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("уже привязан", sent["text"])
        self.assertIsNone(self.store.get_user(second["id"])["tg_id"])

    def test_login_nonce_still_works(self):
        """Старый вход через бота (login_<nonce>) не сломан."""
        nonce = self.store.new_nonce()
        calls = []
        n = {"id": 70}

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            if method == "sendMessage":
                n["id"] += 1
                return {"ok": True, "result": {"message_id": n["id"]}}
            return {"ok": True}

        self.bot._call = fake  # type: ignore
        asyncio.run(self.bot._cmd_start(2002, self.user, "/start login_" + nonce))
        sent = [p for m, p in calls if m == "sendMessage"][0]
        self.assertIn("Вход подтверждён", sent["text"])
        self.assertTrue(self.store.nonce_status(nonce)["ok"])

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
        self.assertIn("LiqScopeRUS", str(sent.get("reply_markup")))
        self.assertIn("LiqScopeEng", str(sent.get("reply_markup")))
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

    # --- ИИ-шапка и контроль публикации ------------------------------------
    class _FakeAI:
        """Заглушка ai_text.AiWriter: без сети, отвечает заранее заданной шапкой."""

        enabled = True

        def __init__(self, head="Рынок снова показал, кто здесь главный",
                     head_en="Futures opened the week with a $2.1B flush"):
            self.head = head
            self.head_en = head_en
            self.seen = []

        async def headline(self, snap, recent=None, variant=0, lang="ru"):
            self.seen.append((snap, list(recent or []), variant, lang))
            return self.head_en if str(lang).startswith("en") else self.head

        def status(self):
            return {"enabled": True, "calls": 1, "fails": 0,
                    "providers": [{"name": "gemini", "model": "gemini-2.5-flash-lite",
                                   "ok": True, "reason": "", "dead": False, "ms": 800}],
                    "last": {"provider": "gemini", "ok": True, "reason": "",
                             "ms": 800, "ts": 0.0}}

    def _capture_caption(self):
        captured = {}

        async def fake_photo(cid, path, caption="", markup=None, **_kw):
            captured["cid"] = cid
            captured["caption"] = caption
            captured["markup"] = markup
            return 11

        async def fake_send(chat_id, text, kb=None, **_kw):
            captured["cid"] = chat_id
            captured["caption"] = text
            captured["markup"] = kb
            return 22

        self.bot.send_photo = fake_photo  # type: ignore
        self.bot.send = fake_send  # type: ignore
        return captured

    def test_ai_head_goes_into_post(self):
        self.bot._channel_id_cfg = "-100111"
        self.bot.ai = self._FakeAI()
        cap = self._capture_caption()
        self.assertTrue(asyncio.run(self.bot.post_channel_digest()))
        self.assertIn("Рынок снова показал, кто здесь главный", cap["caption"])
        self.assertTrue(self.bot.ai.seen)                 # модель реально звали
        self.assertIn("$", cap["caption"])                # «сухие» цифры на месте

    def _capture_posts(self):
        """Все отправки в каналы: фото, текст и второе сообщение с топ-7."""
        posts = []

        async def fake_photo(cid, path, caption="", markup=None, **_kw):
            posts.append({"cid": cid, "kind": "photo", "text": caption,
                          "markup": markup})
            return 11

        async def fake_send(chat_id, text, kb=None, **_kw):
            posts.append({"cid": chat_id, "kind": "text", "text": text, "markup": kb})
            return 22

        self.bot.send_photo = fake_photo  # type: ignore
        self.bot.send = fake_send  # type: ignore
        return posts

    def test_two_channels_get_same_post_in_two_languages(self):
        """Один бот — два канала: русский текст в русский, английский в английский."""
        self.bot._channel_id_cfg = "-100111"
        self.bot._channel2_id_cfg = "-100222"
        self.bot.ai = self._FakeAI()
        self.bot.digest_fn = lambda: {          # type: ignore
            "window_h": 4, "total_usd": 12e6, "longs_usd": 7e6, "shorts_usd": 5e6,
            "count": 40, "exchanges": {"gate": 3e6, "binance": 2e6},
            "top_coins": [{"symbol": "BTC_USDT", "usd": 5e6, "longs": 4e6,
                           "shorts": 1e6, "count": 12}],
            "biggest": {"symbol": "BTC_USDT", "usd": 1e6, "exchange": "gate",
                        "side": "SELL"},
        }
        posts = self._capture_posts()
        self.assertTrue(asyncio.run(self.bot.post_channel_digest()))
        by_chat = {}
        for p in posts:
            by_chat.setdefault(p["cid"], []).append(p)
        self.assertIn("-100111", by_chat)
        self.assertIn("-100222", by_chat)
        ru = by_chat["-100111"][0]["text"]
        en = by_chat["-100222"][0]["text"]
        self.assertIn("Рынок снова показал, кто здесь главный", ru)
        self.assertIn("Futures opened the week", en)
        self.assertNotIn("Рынок снова", en)
        # цифры и подписи блоков тоже переведены
        self.assertIn("fills", en)
        self.assertIn("longs", en)
        self.assertIn("ликвидаций", ru)
        self.assertIn("лонги", ru)
        # реф-ссылка Gate есть в обоих каналах
        self.assertIn("gate.com/ru/signup/VLFCAVWMBW", ru)
        self.assertIn("gate.com/signup/VLFCAVWMBW", en)

    def test_one_post_carries_top7_inside_caption(self):
        """Один пост: топ-7 по часам живёт в той же подписи, второго нет."""
        self.bot._channel_id_cfg = "-100111"
        board = {
            "span_hours": 4, "tz": 3 * 3600, "total_usd": 12e6, "count": 40,
            "prev_total": 10e6, "diff_pct": 20.0, "oi_now_usd": 2e9, "oi_4h_pct": 3.0,
            "hours": [{"h": 1789578000 + i * 3600, "total": 3e6, "count": 10,
                       "longs": 2e6, "shorts": 1e6, "side_sum": 2e6, "bias": "long",
                       "coins": [{"symbol": "BTC_USDT", "usd": 2e6, "flow": -1e5}],
                       "cvd_sum": -1e5,
                       "oi": {"value": 2e9, "pct": 1.0}} for i in range(4)],
            "top_hours": [{"h": 1789578000 + i * 3600, "items": [
                {"symbol": "BTC_USDT", "exchange": "gate", "usd": 1e6, "side": "SELL"},
                {"symbol": "ETH_USDT", "exchange": "bybit", "usd": 5e5, "side": "BUY"},
            ]} for i in range(4)],
            "oi_hours": [{"h": 1789578000 + i * 3600, "value": 2e9, "pct": 1.0}
                         for i in range(4)],
        }

        async def digest():
            return {"window_h": 4, "total_usd": 12e6, "count": 40, "board": board,
                    "top_coins": [], "exchanges": {"gate": 3e6}}

        self.bot.digest_fn = digest          # type: ignore
        posts = self._capture_posts()
        self.assertTrue(asyncio.run(self.bot.post_channel_digest()))
        self.assertEqual(len(posts), 1, posts)            # одно сообщение, не два
        self.assertEqual(posts[0]["kind"], "photo")
        caption = posts[0]["text"]
        self.assertEqual(caption.count("🕘 <b>"), 4)      # все четыре часа
        self.assertEqual(caption.count("📊 OI"), 4)       # OI по каждому часу
        self.assertIn("CVD", caption)                     # и CVD часа
        self.assertNotIn("<pre>", caption)                # рамки с цифрами нет
        self.assertIn("к прошлым 4ч", caption)
        self.assertIn("Gate", caption)
        self.assertLessEqual(len(caption), 1024, len(caption))

    def test_subscription_accepts_either_channel(self):
        """Подписки на любой канал достаточно — русский или английский."""
        self.bot._channel_id_cfg = "-100111"
        self.bot._channel2_id_cfg = "-100222"
        asked = []

        async def fake_call(method, payload=None):
            if method == "getChatMember":
                asked.append(payload.get("chat_id"))
                ok = payload.get("chat_id") == "-100222"     # подписан только на Eng
                if ok:
                    return {"ok": True, "result": {"status": "member"}}
                return {"ok": False, "description": "Bad Request: user not found"}
            return {"ok": True}

        self.bot._call = fake_call  # type: ignore
        self.assertTrue(asyncio.run(self.bot._is_member_any(2002)))
        self.assertIn("-100222", asked)
        # кэш: второй раз не спрашиваем Telegram
        asked.clear()
        self.assertTrue(asyncio.run(self.bot._is_member_any(2002)))
        self.assertEqual(asked, [])

    def test_join_text_offers_both_channels(self):
        self.bot._channel_id_cfg = "-100111"
        self.bot._channel2_id_cfg = "-100222"
        text = self.bot._join_text()
        self.assertIn("LiqScopeRUS", text)
        self.assertIn("LiqScopeEng", text)
        self.assertIn("любой из двух каналов", text)
        kb = self.bot._kb_join()
        texts = [b.get("text") for row in kb["inline_keyboard"] for b in row]
        self.assertTrue(any("LiqScopeRUS" in t for t in texts))
        self.assertTrue(any("LiqScopeEng" in t for t in texts))
        self.assertTrue(any("Я подписался" in t for t in texts))

    def test_menu_button_opens_keyboard_screen(self):
        """Кнопка «☰ Меню» открывает экран с кнопками вместо ручного /start."""
        text, kb = self.bot._screen(self.user, "help")
        self.assertIn("Меню", text)
        # партнёрская ссылка Gate — отдельной url-кнопкой прямо в меню
        gate = [b for row in kb["inline_keyboard"] for b in row
                if str(b.get("url") or "").startswith("https://www.gate.com/")]
        self.assertTrue(gate, kb)
        self.assertIn("VLFCAVWMBW", gate[0]["url"])
        datas = _datas(kb)
        self.assertIn("nav:home", datas)                 # это и есть /start
        self.assertIn("cabinet", datas)
        self.assertIn("terminal", datas)
        self.assertIn("a:vmail", datas)
        texts = [b.get("text") for row in self.bot._reply_kb(self.user)["keyboard"]
                 for b in row]
        self.assertTrue(any("Меню" in t for t in texts), texts)

    def test_channel_binding_by_title(self):
        """Канал понятен по названию, даже если переслали в обратном порядке."""
        self.bot._channel_id_cfg = ""
        self.bot._channel2_id_cfg = ""
        self.bot.store.set_setting("channel_id", "")
        self.bot.store.set_setting("channel_id_en", "")
        self.bot._save_channel_bindings({"ru": "", "en": "", "titles": {}})
        code, cid = self.bot.remember_channel_auto("-100999", "LiqScopeEng")
        self.assertEqual((code, cid), ("en", "-100999"))
        self.bot._save_channel_bindings({"ru": "", "en": "", "titles": {}})
        code2, cid2 = self.bot.remember_channel_auto("-100888", "LiqScopeRUS")
        self.assertEqual((code2, cid2), ("ru", "-100888"))
        # без названия работает порядок: первый — русский, второй — английский
        self.bot._save_channel_bindings({"ru": "", "en": "", "titles": {}})
        self.assertEqual(self.bot.remember_channel_auto("-100777", "")[0], "ru")
        self.assertEqual(self.bot.remember_channel_auto("-100666", "")[0], "en")

    def test_swapped_channels_are_repaired_by_titles(self):
        """Перепутанные роли каналов бот видит по названию и меняет местами."""
        self.bot._channel_id_cfg = ""
        self.bot._channel2_id_cfg = ""
        # так каналы записались раньше: русская роль — на английском канале
        self.bot._save_channel_bindings({
            "ru": "-3931620564", "en": "-4481747490",
            "titles": {"ru": "LiqScopeEng", "en": "LiqScopeRUS"}})
        titles = {"-3931620564": "LiqScope ENG", "-4481747490": "LiqScope RUS"}

        async def fake_call(method, payload=None):
            if method == "getChat":
                cid = str((payload or {}).get("chat_id"))
                if cid in titles:
                    return {"ok": True, "result": {"id": cid, "title": titles[cid]}}
            return {"ok": False, "description": "Bad Request"}

        self.bot._call = fake_call  # type: ignore
        note = asyncio.run(self.bot.verify_channel_roles(force=True))
        self.assertIn("поменяли местами", note)
        self.assertEqual(self.bot.channel_chat_id(), "-4481747490")      # русский
        self.assertEqual(self.bot.channel_chat_id_en(), "-3931620564")   # английский
        # повторная проверка уже ничего не меняет
        self.assertEqual(asyncio.run(self.bot.verify_channel_roles(force=True)), "")
        self.assertEqual(self.bot.channel_chat_id(), "-4481747490")
        # в отчёте о сводке видно, куда что ушло
        routes = self.bot.channel_route_text()
        self.assertIn("-4481747490", routes)
        self.assertIn("-3931620564", routes)

    def test_swapped_env_channels_are_repaired_by_titles(self):
        """Перепутанные id из env тоже лечатся: проверка названий правит роли."""
        self.bot._save_channel_bindings({"ru": "", "en": "", "titles": {}})
        # так каналы заданы переменными окружения: ru — английский канал
        self.bot._channel_id_cfg = "-3931620564"
        self.bot._channel2_id_cfg = "-4481747490"
        titles = {"-3931620564": "LiqScope ENG", "-4481747490": "LiqScope RUS"}

        async def fake_call(method, payload=None):
            if method == "getChat":
                cid = str((payload or {}).get("chat_id"))
                if cid in titles:
                    return {"ok": True, "result": {"id": cid, "title": titles[cid]}}
            return {"ok": False, "description": "Bad Request"}

        self.bot._call = fake_call  # type: ignore
        note = asyncio.run(self.bot.verify_channel_roles(force=True))
        self.assertIn("поменяли местами", note)
        self.assertEqual(self.bot.channel_chat_id(), "-4481747490")
        self.assertEqual(self.bot.channel_chat_id_en(), "-3931620564")

    def test_menu_opens_without_channel_when_ai_missing(self):
        """Без ключей ИИ бот работает как раньше — шапка из шаблонов."""
        self.bot._channel_id_cfg = "-100111"
        cap = self._capture_caption()
        self.assertTrue(asyncio.run(self.bot.post_channel_digest()))
        self.assertIn("liqscope.online", cap["caption"])
        self.assertEqual(self.bot._ai_state.get("ok"), False)

    def test_review_toggle_in_templates_menu(self):
        calls = []

        async def fake(method, payload=None):
            calls.append((method, payload or {}))
            return {"ok": True, "result": {"message_id": 77}}

        self.bot._call = fake  # type: ignore
        self.assertFalse(self.bot._review_on())
        cb = {"id": "cb1", "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
              "data": "a:rv", "message": {"message_id": 5, "chat": {"id": 1001}}}
        asyncio.run(self.bot._on_callback(cb))
        self.assertTrue(self.bot._review_on())
        self.assertEqual(self.store.get_setting("channel_digest_review"), "1")
        text = [pl for m, pl in calls if m in ("editMessageText", "sendMessage")][0]["text"]
        self.assertIn("Контроль постов: включён", text)
        labels = [b["text"] for row in self.bot._tpl_kb()["inline_keyboard"] for b in row]
        self.assertIn("🧪 Контроль: вкл", labels)

    def test_review_mode_drafts_then_publishes(self):
        self.bot._channel_id_cfg = "-100111"
        self.bot.ai = self._FakeAI()
        self.bot._set_review(True)
        sent, photo = [], []

        async def fake_send(chat_id, text, kb=None, **_kw):
            sent.append((str(chat_id), text, kb))
            return 1

        async def fake_photo(cid, path, caption="", markup=None, **_kw):
            photo.append((str(cid), caption))
            return 11

        self.bot.send = fake_send          # черновик админу
        self.bot.send_photo = fake_photo   # публикация в канал
        self.assertTrue(asyncio.run(self.bot.post_channel_digest()))
        # в канал ничего не ушло, черновик — админу (tg_id 1001)
        self.assertEqual(photo, [])
        self.assertEqual([x[0] for x in sent], ["1001"])
        self.assertIn("Черновик сводки", sent[0][1])
        datas = [b["callback_data"] for row in sent[0][2]["inline_keyboard"] for b in row]
        self.assertEqual(datas, ["d:pub", "d:regen", "d:no"])
        self.assertEqual(self.store.get_setting("channel_digest_n", "0"), "0")

        calls = []

        async def fake_call(method, payload=None):
            calls.append((method, payload or {}))
            return {"ok": True, "result": {"message_id": 78}}

        self.bot._call = fake_call  # type: ignore
        cb = {"id": "cb2", "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
              "data": "d:pub", "message": {"message_id": 6, "chat": {"id": 1001}}}
        asyncio.run(self.bot._on_callback(cb))
        to_channel = [x for x in photo if x[0] == "-100111"] or \
                     [x for x in sent if x[0] == "-100111"]
        self.assertTrue(to_channel, "пост должен уйти в канал после нажатия")
        self.assertIn("Рынок снова показал, кто здесь главный", to_channel[0][1])
        self.assertEqual(self.store.get_setting("channel_digest_n", "0"), "1")

    def test_review_cancel_keeps_channel_clean(self):
        self.bot._channel_id_cfg = "-100111"
        self.bot._set_review(True)
        sent, photo = [], []

        async def fake_send(chat_id, text, kb=None, **_kw):
            sent.append((str(chat_id), text, kb))
            return 1

        async def fake_photo(cid, path, caption="", markup=None, **_kw):
            photo.append(str(cid))
            return 11

        self.bot.send = fake_send  # type: ignore
        self.bot.send_photo = fake_photo  # type: ignore
        asyncio.run(self.bot.post_channel_digest())

        async def fake_call(method, payload=None):
            return {"ok": True, "result": {"message_id": 79}}

        self.bot._call = fake_call  # type: ignore
        cb = {"id": "cb3", "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
              "data": "d:no", "message": {"message_id": 7, "chat": {"id": 1001}}}
        asyncio.run(self.bot._on_callback(cb))
        self.assertEqual(photo, [])
        self.assertIsNone(self.bot._draft)
        self.assertEqual(self.store.get_setting("channel_digest_n", "0"), "0")

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
        self.assertTrue(any("liqscope" in t for t in texts), texts)
        self.assertTrue(any("Gate" in t for t in texts), texts)      # реф-ссылка
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
        sent = [p for m, p in calls if m == "sendMessage"][0]
        # бот не угадывает язык: он спрашивает, какой канал перед ним
        self.assertIn("на русском или на английском", sent["text"])
        cbs = str(sent.get("reply_markup"))
        self.assertIn("ch:role:ru", cbs)
        self.assertIn("ch:role:en", cbs)
        # до ответа админа привязки нет — канал не встанет не на свою роль
        self.assertEqual(self.store.get_setting("channel_id") or "", "")

        # админ выбрал «русский»
        self.store.upsert_telegram_user({"id": 1001, "username": "boss",
                                         "first_name": "Ada"})
        self.bot._pending_channel = {"id": "-100888", "title": "LiqScopeRUS"}
        asyncio.run(self.bot._on_callback({
            "id": "cb1", "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
            "data": "ch:role:ru",
            "message": {"message_id": 44, "chat": {"id": 1001}},
        }))
        self.assertEqual(self.bot.channel_chat_id(), "-100888")
        self.assertEqual(self.store.get_setting("channel_id"), "-100888")

        # и английский канал встаёт на свою роль, не сбивая русский
        self.bot._pending_channel = {"id": "-100777", "title": "LiqScopeEng"}
        asyncio.run(self.bot._on_callback({
            "id": "cb2", "from": {"id": 1001, "username": "boss", "first_name": "Ada"},
            "data": "ch:role:en",
            "message": {"message_id": 45, "chat": {"id": 1001}},
        }))
        self.assertEqual(self.bot.channel_chat_id_en(), "-100777")
        self.assertEqual(self.bot.channel_chat_id(), "-100888")
        # один и тот же канал не может занимать обе роли
        self.bot._pending_channel = {"id": "-100777", "title": "LiqScopeEng"}
        self.bot.remember_channel_role("ru", "-100777", "LiqScopeEng")
        self.assertEqual(self.bot.channel_chat_id_en(), "")

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

    def test_health_shows_dead_listener_and_attempts(self):
        """Биржа «отвалилась и больше не пытается» должна быть видна в чате."""
        self.bot.health_fn = lambda: {"sources": {
            "gate": {"connected": False, "events": 12, "attempts": 7,
                     "last_error": "нет спецификаций", "supervisor_alive": False,
                     "respawns": 2},
            "bybit": {"connected": True, "events": 900, "attempts": 3,
                      "seconds_since_event": 5, "supervisor_alive": True,
                      "respawns": 0},
            "okx": {"connected": False, "events": 0, "attempts": 4,
                    "last_error": "timeout", "supervisor_alive": True,
                    "respawns": 1},
        }}
        self.bot.ws_clients_fn = lambda: 3
        text = self.bot._health_text()
        self.assertIn("слушатель не запущен", text)
        self.assertIn("подъёмов: 2", text)
        self.assertIn("попыток: 7", text)      # красная строка тоже со счётчиком
        self.assertIn("попыток: 3", text)      # зелёная строка — сколько раз поднимался
        self.assertIn("В эфире <b>1/3</b>", text)

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


class _FakeMailer:
    """Письма из бота: пишем вызовы, наружу ничего не уходит."""

    enabled = True

    def __init__(self):
        self.sent = []

    def send_verify(self, to, token, name=""):
        self.sent.append(("verify", to, token, name))
        return True

    def send_tg_attach(self, to, token, tg_name="", name=""):
        self.sent.append(("attach", to, token, tg_name))
        return True


@unittest.skipIf(not HAVE, "aiohttp/accounts")
class BotMailTest(unittest.TestCase):
    """Напоминание про почту в кабинете и привязка почты из бота."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s", admin_ids=[1001])
        self.bot = TelegramBot("0:x", self.store)
        self.mailer = _FakeMailer()
        self.bot.mailer = self.mailer
        self.user = self.store.upsert_telegram_user({
            "id": 2002, "username": "bob", "first_name": "Bob"})
        self.sent = []

        async def fake_send(chat_id, text, markup=None, parse="HTML", silent=False):
            self.sent.append(text)
            return 1

        self.bot.send = fake_send  # type: ignore
        self.bot._ensure_channel = _always_true  # type: ignore

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _type(self, text, tg_id=2002):
        self.bot._wait_email[tg_id] = time.time() + 900
        asyncio.run(self.bot._route_message({
            "message": {"message_id": 5, "chat": {"id": tg_id, "type": "private"},
                        "from": {"id": tg_id, "username": "bob", "first_name": "Bob"},
                        "text": text}}))

    def test_cabinet_asks_to_confirm_email(self):
        kb = self.bot._reply_kb(self.user)
        texts = [b.get("text") for row in kb["keyboard"] for b in row]
        self.assertIn("✉️ Подтвердить почту", texts)
        self.assertIn("не привязана", self.bot._cabinet_text(self.user))
        self.assertEqual(self.bot._reply_cmd("✉️ Подтвердить почту"), "mail")

    def test_button_hides_when_email_confirmed(self):
        r = self.store.attach_email(self.user["id"], "bob@mail.ru")
        self.assertTrue(r["ok"])
        unverified = r["user"]
        self.assertIn("не подтверждена", self.bot._cabinet_text(unverified))
        texts = [b.get("text") for row in self.bot._reply_kb(unverified)["keyboard"] for b in row]
        self.assertIn("✉️ Подтвердить почту", texts)
        ok_user = self.store.mark_email_verified(self.user["id"])
        texts = [b.get("text") for row in self.bot._reply_kb(ok_user)["keyboard"] for b in row]
        self.assertNotIn("✉️ Подтвердить почту", texts)
        self.assertIn("уже подтверждена", self.bot._mail_screen(ok_user))

    def test_mail_screen_waits_for_address(self):
        text, _kb = self.bot._screen(self.user, "mail")
        self.assertIn("Пришлите адрес", text)
        self.assertGreater(self.bot._wait_email.get(2002, 0), time.time())

    def test_mail_command_and_help_mention_it(self):
        self.assertEqual(self.bot._reply_cmd("/mail"), "")
        help_text = self.bot._help(self.user)
        self.assertIn("/mail", help_text)
        body, kb = self.bot._screen(self.user, "mail")
        self.assertIn("Пришлите адрес", body)
        self.assertTrue([b for row in kb["inline_keyboard"] for b in row])

    def test_email_input_sends_verify_letter(self):
        self._type("  bob@MAIL.ru ")
        self.assertEqual(len(self.mailer.sent), 1)
        kind, to, token, _name = self.mailer.sent[0]
        self.assertEqual((kind, to), ("verify", "bob@mail.ru"))
        self.assertTrue(token)
        me = self.store.get_user(self.user["id"])
        self.assertEqual(me["email"], "bob@mail.ru")
        self.assertFalse(me["email_verified"])          # ждём клик по ссылке
        self.assertTrue(any("Письмо ушло" in t for t in self.sent), self.sent)
        # повторный ввод сразу — письма нет, просим подождать
        self._type("bob@mail.ru")
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertTrue(any("уже отправил" in t for t in self.sent), self.sent)

    def test_bad_address_asks_again(self):
        self._type("не-адрес")
        self.assertEqual(self.mailer.sent, [])
        self.assertTrue(any("не похоже на адрес" in t for t in self.sent), self.sent)
        self.assertGreater(self.bot._wait_email.get(2002, 0), time.time())

    def test_confirmed_email_gets_attach_letter_and_merges(self):
        # человек зарегистрировался на сайте по почте и подтвердил её
        made = self.store.create_email_user("bob@mail.ru", password_hash="x")
        other_id = made["user"]["id"]
        self.store.mark_email_verified(other_id)
        self._type("bob@mail.ru")
        kind, to, token, _tg = self.mailer.sent[0]
        self.assertEqual(kind, "attach")               # без письма не связываем
        self.assertEqual(to, "bob@mail.ru")
        self.assertIsNotNone(self.store.get_user(self.user["id"]))  # пока два аккаунта
        # владелец почты нажал ссылку в письме
        r = self.store.confirm_tg_attach(token)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"]["id"], other_id)
        self.assertEqual(r["user"]["tg_id"], 2002)
        self.assertEqual(r["user"]["email_verified"], True)
        self.assertIsNone(self.store.get_user(self.user["id"]))     # дубль исчез
        self.assertTrue(any("подтверждением" in t for t in self.sent), self.sent)

    def test_email_input_needs_mail_enabled(self):
        self.bot.mailer = None
        self._type("bob@mail.ru")
        self.assertTrue(any("не настроена" in t for t in self.sent), self.sent)
        self.assertEqual(self.store.get_user(self.user["id"])["email"], "bob@mail.ru")

    def test_vcheck_reports_state(self):
        self.store.attach_email(self.user["id"], "bob@mail.ru")
        cb = {"id": "c1", "data": "a:vchk",
              "from": {"id": 2002, "username": "bob", "first_name": "Bob"},
              "message": {"message_id": 9, "chat": {"id": 2002}}}
        asyncio.run(self.bot._on_callback(cb))
        self.assertTrue(any("пока не подтверждена" in t for t in self.sent), self.sent)
        self.store.mark_email_verified(self.user["id"])
        asyncio.run(self.bot._on_callback(cb))
        self.assertTrue(any("Готово" in t for t in self.sent), self.sent)


if __name__ == "__main__":
    unittest.main()
