"""🆘 Поддержка: личный тред, задержка письма в Telegram, ссылка на кабинет.

Проверяем то, ради чего модуль подключается к серверу:

    * маршруты ``/api/chat/support*`` отвечают (модуль был написан, но не
      подключён в server.py — вкладка «Поддержка» в чате получала 404, а
      напоминания админам не уходили вовсе);
    * сообщение уходит в Telegram **не сразу**, а через
      ``chat_dm_tg_delay_min`` минут — и только если админ не ответил,
      не прочитал и его нет на сайте;
    * в письме есть ссылка на кабинет и кнопка «Открыть кабинет»;
    * текст пользователя экранируется (HTML из чата не лезет в письмо).

Запуск:  python3 tests/test_support_chat.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import terminal_chat as terminal_chat_mod  # noqa: E402
import support_chat as sc  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402


def _no_limits():
    sc.RATE_LIMIT = 10_000
    sc.RATE_WINDOW = 0.0


class FakeBot:
    """Мини-бот: помнит письма и собирает кнопку-ссылку как настоящий."""

    def __init__(self, public_url="https://liqscope.online"):
        self.sent = []
        self.running = True
        self.enabled = True
        self.public_url = public_url
        self.fail = False

    async def send(self, chat_id, text, markup=None, parse="HTML", raw=False):
        if self.fail:
            return None
        self.sent.append({"chat_id": int(chat_id), "text": text, "markup": markup})
        return len(self.sent)

    def site_url(self, path=""):
        return self.public_url + (path or "")

    def site_link_kb(self, label="открыть кабинет", path="/cabinet"):
        return {"inline_keyboard": [[{"text": label, "url": self.site_url(path)}]]}


class SupportTest(unittest.TestCase):
    """Общая обвязка: своя база, свой бот, приложение только с нужными роутами."""

    def setUp(self):
        _no_limits()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=["boss@x.io"])
        self.alice = self.store.create_email_user("alice@x.io", password_hash="x")["user"]
        self.admin = self.store.create_email_user("boss@x.io", password_hash="x")["user"]
        self.assertTrue(self.admin.get("is_admin"), self.admin)
        with self.store._lock:                      # noqa: SLF001 — админу нужен Telegram
            self.store._db.execute("UPDATE users SET tg_id=1001 WHERE id=?",
                                   (int(self.admin["id"]),))
            self.store._db.commit()
        import web_account
        self.web_account = web_account
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        terminal_chat_mod.ctx.store = self.store
        terminal_chat_mod.ctx.hub = None
        sc.ctx.store = self.store
        self.bot = FakeBot()
        sc.ctx.bot = self.bot
        sc.ctx.hub = None
        sc.ctx.public_url = "https://liqscope.online"
        app = FastAPI()
        sc.register_support_chat_routes(app)
        self.app = app
        self.guest = TestClient(app)
        self.alice_c = self._client(self.alice["id"])
        self.admin_c = self._client(self.admin["id"])

    def _client(self, uid):
        c = TestClient(self.app)
        c.cookies.set(COOKIE_SID, self.store.create_session(int(uid)))
        return c

    def tearDown(self):
        self.web_account.ctx.store = None
        terminal_chat_mod.ctx.store = None
        sc.ctx.store = None
        sc.ctx.bot = None
        terminal_chat_mod._PRESENCE.clear()
        self.tmp.cleanup()

    def _delay(self, minutes: str) -> None:
        self.store.set_setting(sc.DELAY_SETTING if hasattr(sc, "DELAY_SETTING")
                               else "chat_dm_tg_delay_min", minutes)

    # --- маршруты -----------------------------------------------------------

    def test_user_message_reaches_support_thread(self):
        r = self.alice_c.post("/api/chat/support", json={"text": "не приходят алерты"})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["thread_key"], f"u:{int(self.alice['id'])}")
        got = self.alice_c.get("/api/chat/support").json()
        self.assertEqual([m["text"] for m in got["messages"]], ["не приходят алерты"])

    def test_guest_needs_a_name_and_gets_a_token(self):
        self.assertEqual(self.guest.post("/api/chat/support", json={"text": "привет"}).status_code, 400)
        r = self.guest.post("/api/chat/support", json={"text": "привет", "name": "Гостья"})
        self.assertTrue(r.json()["ok"], r.text)
        token = r.json()["guest_token"]
        self.assertTrue(token)
        back = self.guest.get("/api/chat/support?guest_token=" + token).json()
        self.assertEqual(back["messages"][0]["name"], "Гостья")
        self.assertTrue(back["messages"][0]["guest"])

    def test_admin_sees_the_thread_and_replies(self):
        self.alice_c.post("/api/chat/support", json={"text": "вопрос"})
        threads = self.admin_c.get("/api/chat/support/threads").json()["threads"]
        self.assertEqual([t["thread_key"] for t in threads], [f"u:{int(self.alice['id'])}"])
        r = self.admin_c.post("/api/chat/support",
                              json={"text": "ответ", "thread_key": f"u:{int(self.alice['id'])}"})
        self.assertTrue(r.json()["ok"], r.text)
        msgs = self.alice_c.get("/api/chat/support").json()["messages"]
        self.assertEqual([(m["text"], m["admin"]) for m in msgs],
                         [("вопрос", False), ("ответ", True)])

    def test_other_users_do_not_see_the_thread(self):
        self.alice_c.post("/api/chat/support", json={"text": "секрет"})
        other = self.store.create_email_user("carol@x.io", password_hash="x")["user"]
        c = self._client(other["id"])
        self.assertEqual(c.get("/api/chat/support").json()["messages"], [])

    # --- задержка письма в Telegram -----------------------------------------

    def _pending(self, text="письмо в поддержку"):
        self.alice_c.post("/api/chat/support", json={"text": text})

    def test_message_does_not_go_to_telegram_immediately(self):
        """Письмо ждёт задержку: сразу после сообщения бот молчит."""
        self.store.set_setting("chat_dm_tg_delay_min", "10")
        self._pending()
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 0)
        self.assertEqual(self.bot.sent, [])

    def test_reminder_comes_after_the_delay_with_a_cabinet_link(self):
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")   # ~6 мс
        self._pending("верните фильтр по биржам")
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 1)
        self.assertEqual(len(self.bot.sent), 1)
        letter = self.bot.sent[0]
        self.assertEqual(letter["chat_id"], 1001)
        self.assertIn("верните фильтр по биржам", letter["text"])
        self.assertIn("https://liqscope.online/cabinet?chat=1&tab=support", letter["text"])
        # кнопка «открыть кабинет» — быстрый переход, а не только текст ссылки
        kb = (letter["markup"] or {}).get("inline_keyboard") or []
        self.assertTrue(kb and kb[0][0]["url"].endswith("/cabinet?chat=1&tab=support"),
                        letter["markup"])
        self.assertIn("Открыть кабинет", kb[0][0]["text"])
        # повторно об этом же сообщении не напоминаем
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 0)

    def test_delay_zero_turns_reminders_off(self):
        self.store.set_setting("chat_dm_tg_delay_min", "0")
        self._pending()
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 0)
        self.assertEqual(self.bot.sent, [])

    def test_admin_reply_cancels_the_reminder(self):
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")
        self._pending()
        self.admin_c.post("/api/chat/support",
                          json={"text": "уже смотрю",
                                "thread_key": f"u:{int(self.alice['id'])}"})
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 0)
        self.assertEqual(self.bot.sent, [])

    def test_admin_reading_the_thread_cancels_the_reminder(self):
        """Админ открыл тред в чате (прочитал) — в Telegram не дёргаем."""
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")
        self._pending()
        self.admin_c.get("/api/chat/support?thread_key=u:" + str(int(self.alice["id"])))
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 0)

    def test_admin_online_in_chat_gets_no_telegram(self):
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")
        self._pending()
        terminal_chat_mod.chat_presence_touch(int(self.admin["id"]))
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 0)
        self.assertEqual(self.bot.sent, [])

    def test_guest_reminder_shows_the_nick_not_an_id(self):
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")
        self.guest.post("/api/chat/support", json={"text": "как оплатить", "name": "Гостья"})
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 1)
        text = self.bot.sent[0]["text"]
        self.assertIn("Гостья", text)
        self.assertIn("(гость)", text)
        self.assertNotIn("idNone", text)

    def test_user_nick_shows_in_the_letter(self):
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")
        with self.store._lock:                      # noqa: SLF001 — имя как в профиле
            self.store._db.execute("UPDATE users SET first_name='Алиса' WHERE id=?",
                                   (int(self.alice["id"]),))
            self.store._db.commit()
        self._pending()
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 1)
        self.assertIn("Алиса", self.bot.sent[0]["text"])

    def test_undelivered_reminder_stays_pending(self):
        """Telegram не принял письмо — не помечаем «напомнили», уйдёт на следующем круге."""
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")
        self.bot.fail = True
        self._pending("не дошло")
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 0)
        self.bot.fail = False                     # бот починился
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 1)
        self.assertEqual(len(self.bot.sent), 1)

    def test_html_in_the_message_is_escaped(self):
        self.store.set_setting("chat_dm_tg_delay_min", "0.0001")
        self._pending("<b>жирный</b> и <a href='x'>ссылка</a>")
        time.sleep(0.02)
        self.assertEqual(asyncio.run(sc.scan_support_reminders()), 1)
        text = self.bot.sent[0]["text"]
        self.assertIn("&lt;b&gt;жирный&lt;/b&gt;", text)
        self.assertNotIn("<b>жирный</b>", text)


class WiringTest(unittest.TestCase):
    """Модуль подключён к приложению и к фоновому циклу напоминаний."""

    @classmethod
    def setUpClass(cls):
        import inspect
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.setdefault("LIQSCOPE_ACCOUNTS_DB",
                              os.path.join(cls.tmp.name, "accounts.db"))
        try:
            import server
        except Exception as exc:  # noqa: BLE001 — окружение без зависимостей
            raise unittest.SkipTest(f"приложение не поднимается: {exc}")
        cls.server = server
        cls.app = server.app

    @staticmethod
    def _paths(routes, out=None):
        """Все пути приложения: включённые роутеры FastAPI прячет за обёрткой."""
        out = set() if out is None else out
        for r in routes:
            path = getattr(r, "path", "")
            if isinstance(path, str) and path:
                out.add(path)
            inner = getattr(r, "routes", None)
            wrapped = getattr(getattr(r, "original_router", None), "routes", None)
            for sub in (inner, wrapped):
                if sub:
                    WiringTest._paths(sub, out)
        return out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_support_routes_are_served(self):
        """Без подключения модуля тут был 404 — вкладка «Поддержка» не работала."""
        paths = self._paths(self.app.routes)
        self.assertIn("/api/chat/support", paths)
        self.assertIn("/api/chat/support/threads", paths)
        r = TestClient(self.app).get("/api/chat/support")
        self.assertNotEqual(r.status_code, 404)

    def test_ctx_is_filled_for_reminders(self):
        self.assertIsNotNone(sc.ctx.store)
        self.assertIsNotNone(sc.ctx.bot)
        self.assertIsNotNone(sc.ctx.hub)

    def test_notify_loop_scans_support_reminders(self):
        import inspect
        src = inspect.getsource(self.server.chat_notify_loop)
        self.assertIn("scan_support_reminders", src)
        self.assertIn("scan_reminders", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
