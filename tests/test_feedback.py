"""💬 Обратная связь: «по всем вопросам» из кабинета и ответы из админки.

Проверяем главное: пользователь пишет админу из кабинета, сообщение не
теряется, админ видит диалог в админке со счётчиком непрочитанных, отвечает —
и ответ приходит в тот же диалог в кабинете. Плюс то, что легко сломать:
доступ (гость не пишет, обычный пользователь не читает чужое), пустые и
слишком длинные сообщения, уведомление в Telegram, когда бот подключён.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import feedback  # noqa: E402
import web_account  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402

ADMIN_EMAIL = "boss@liqscope.online"


class FakeBot:
    """Мини-бот: помнит, кому и что отправил."""

    def __init__(self, admins=(1001,), enabled=True):
        self.admins_tg = {i: "админ" for i in admins}
        self._enabled = enabled
        self.sent = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _admin_tg_ids(self):
        return list(self.admins_tg)

    async def send(self, chat_id, text, markup=None, parse="HTML", raw=False):
        self.sent.append((int(chat_id), text))
        return 1


class FeedbackStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        self.user = self.store.create_email_user("vasya@example.com", password_hash="x")["user"]
        self.admin = self.store.create_email_user(ADMIN_EMAIL, password_hash="x")["user"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_thread_is_created_once_and_keeps_history(self):
        t1 = self.store.feedback_thread(self.user["id"])
        self.store.feedback_add(t1["id"], "Первый вопрос", author_id=self.user["id"])
        t2 = self.store.feedback_thread(self.user["id"])
        self.assertEqual(t1["id"], t2["id"], "на пользователя должен быть один диалог")
        self.store.feedback_add(t1["id"], "Ответ админа",
                                author_id=self.admin["id"], is_admin=True)
        msgs = self.store.feedback_messages(t1["id"])
        self.assertEqual([m["text"] for m in msgs], ["Первый вопрос", "Ответ админа"])
        self.assertFalse(msgs[0]["is_admin"])
        self.assertTrue(msgs[1]["is_admin"])

    def test_empty_message_is_rejected(self):
        thread = self.store.feedback_thread(self.user["id"])
        self.assertEqual(self.store.feedback_add(thread["id"], "   ")["error"], "empty")
        self.assertEqual(self.store.feedback_messages(thread["id"]), [])

    def test_long_message_is_truncated_not_lost(self):
        thread = self.store.feedback_thread(self.user["id"])
        got = self.store.feedback_add(thread["id"], "я" * (Store.FB_MAX_LEN + 500))
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["text"]), Store.FB_MAX_LEN)

    def test_unread_counts_only_other_side(self):
        thread = self.store.feedback_thread(self.user["id"])
        self.store.feedback_add(thread["id"], "Вопрос", author_id=self.user["id"])
        # своё сообщение админ не «не прочитал»: счётчик считает чужие реплики
        self.assertEqual(self.store.feedback_admin_unread(), 1)
        self.store.feedback_mark_read(thread["id"], "admin")
        self.assertEqual(self.store.feedback_admin_unread(), 0)
        reply = self.store.feedback_add(thread["id"], "Ответ", is_admin=True)
        self.assertEqual(self.store.feedback_unread(self.user["id"]), 1)
        self.store.feedback_mark_read(thread["id"], "user")
        self.assertEqual(self.store.feedback_unread(self.user["id"]), 0)
        self.assertTrue(reply["ok"])

    def test_admin_list_shows_who_wrote_and_what(self):
        thread = self.store.feedback_thread(self.user["id"])
        self.store.feedback_add(thread["id"], "Не работает кнопка", author_id=self.user["id"])
        rows = self.store.feedback_threads()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["user_id"], self.user["id"])
        self.assertEqual(row["last_text"], "Не работает кнопка")
        self.assertEqual(row["unread"], 1)
        self.assertEqual(row["total"], 1)
        self.assertTrue(row["name"])
        self.assertEqual(self.store.feedback_threads()[0]["last_admin"], False)

    def test_reply_goes_to_the_same_thread(self):
        thread = self.store.feedback_thread(self.user["id"])
        self.store.feedback_add(thread["id"], "Вопрос", author_id=self.user["id"])
        self.store.feedback_add(thread["id"], "Ответ", author_id=self.admin["id"], is_admin=True)
        data = self.store.feedback_for_user(self.user["id"])
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["unread"], 1, "ответ админа — непрочитанный для пользователя")

    def test_unknown_thread_is_not_writable(self):
        self.assertEqual(self.store.feedback_add(9999, "привет")["error"], "not_found")

    def test_thread_without_messages_has_no_unread(self):
        self.store.feedback_thread(self.user["id"])
        self.assertEqual(self.store.feedback_admin_unread(), 0)
        self.assertEqual(self.store.feedback_unread(self.user["id"]), 0)


class FeedbackRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=[ADMIN_EMAIL])
        self.bot = FakeBot()
        self.admin_id = int(self.store.create_email_user(ADMIN_EMAIL, password_hash="x")["user"]["id"])
        self.user_id = int(self.store.create_email_user("vasya@example.com",
                                                        password_hash="x")["user"]["id"])
        # current_user читает сессию из web_account — та же база, что у сервера
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        feedback.ctx.store = self.store
        feedback.ctx.bot = self.bot
        feedback.ctx.public_url = "https://liqscope.online"
        feedback.limiter.hits.clear()
        self.app = FastAPI()
        feedback.register_feedback_routes(self.app)
        self.guest = TestClient(self.app)
        self.user = TestClient(self.app)
        self.user.cookies.set(COOKIE_SID, self.store.create_session(self.user_id))
        self.admin = TestClient(self.app)
        self.admin.cookies.set(COOKIE_SID, self.store.create_session(self.admin_id))

    def tearDown(self):
        feedback.ctx.store = None
        feedback.ctx.bot = None
        web_account.ctx.store = None
        feedback.limiter.hits.clear()
        self.tmp.cleanup()

    def send(self, text="Здравствуйте! Не приходят алерты"):
        return self.user.post("/api/feedback", json={"text": text})

    # --- доступ ------------------------------------------------------------
    def test_guest_cannot_write_or_read(self):
        self.assertEqual(self.guest.get("/api/feedback").status_code, 401)
        self.assertEqual(self.guest.post("/api/feedback", json={"text": "x"}).status_code, 401)
        self.assertEqual(self.guest.get("/api/admin/feedback").status_code, 401)

    def test_regular_user_cannot_open_the_admin_side(self):
        self.assertEqual(self.user.get("/api/admin/feedback").status_code, 403)
        self.assertEqual(self.user.get("/api/admin/feedback/1").status_code, 403)
        self.assertEqual(self.user.post("/api/admin/feedback/1", json={"text": "x"}).status_code, 403)

    # --- путь пользователя -------------------------------------------------
    def test_first_message_creates_the_dialog(self):
        r = self.send()
        self.assertTrue(r.json()["ok"], r.text)
        d = self.user.get("/api/feedback").json()
        self.assertEqual([m["text"] for m in d["messages"]], ["Здравствуйте! Не приходят алерты"])
        self.assertTrue(d["messages"][0]["mine"])
        self.assertFalse(d["messages"][0]["admin"])
        self.assertEqual(d["unread"], 0)

    def test_dialog_starts_empty_and_says_so(self):
        d = self.user.get("/api/feedback").json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["messages"], [])
        self.assertEqual(d["unread"], 0)

    def test_empty_and_long_messages_are_checked(self):
        self.assertEqual(self.send("   ").status_code, 400)
        self.assertEqual(self.send("я" * 2500).status_code, 400)
        self.assertEqual(self.send("я" * 2500).json()["error"], "too_long")

    def test_flood_is_slowed_down(self):
        for _ in range(feedback.RATE_LIMIT):
            self.assertTrue(self.send().json()["ok"])
        r = self.send()
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["error"], "too_fast")

    def test_admin_reply_lands_in_the_user_dialog(self):
        self.send()
        threads = self.admin.get("/api/admin/feedback").json()
        self.assertEqual(threads["unread"], 1)
        tid = threads["threads"][0]["id"]
        r = self.admin.post(f"/api/admin/feedback/{tid}", json={"text": "Уже смотрим"})
        self.assertTrue(r.json()["ok"], r.text)
        d = self.user.get("/api/feedback").json()
        self.assertEqual([m["text"] for m in d["messages"]], ["Здравствуйте! Не приходят алерты",
                                                              "Уже смотрим"])
        self.assertTrue(d["messages"][1]["admin"])
        self.assertFalse(d["messages"][1]["mine"])

    def test_unread_counter_for_the_user(self):
        self.send()
        tid = self.admin.get("/api/admin/feedback").json()["threads"][0]["id"]
        self.admin.post(f"/api/admin/feedback/{tid}", json={"text": "Ответ"})
        self.assertEqual(self.user.get("/api/feedback/unread").json()["unread"], 1)
        d = self.user.get("/api/feedback")                 # открыл диалог — прочитано
        self.assertEqual(d.json()["unread"], 0, "счётчик гаснет тем же запросом")
        self.assertEqual(self.user.get("/api/feedback/unread").json()["unread"], 0)

    # --- путь админа -------------------------------------------------------
    def test_admin_sees_thread_with_user_card(self):
        self.store._db.execute("UPDATE users SET first_name='Вася', username='vasya' WHERE id=?",
                               (self.user_id,))
        self.store._db.commit()
        self.send()
        d = self.admin.get("/api/admin/feedback").json()
        row = d["threads"][0]
        self.assertEqual(row["unread"], 1)
        self.assertIn("vasya@example.com", row["email"])
        thread = self.admin.get(f"/api/admin/feedback/{row['id']}").json()
        self.assertEqual(len(thread["messages"]), 1)
        self.assertEqual(thread["unread"], 0, "открыл диалог — непрочитанных больше нет")

    def test_admin_cannot_reply_into_the_void(self):
        self.assertEqual(self.admin.get("/api/admin/feedback/777").status_code, 404)
        self.assertEqual(self.admin.post("/api/admin/feedback/777", json={"text": "привет"}).status_code,
                         404)

    def test_empty_admin_reply_is_rejected(self):
        self.send()
        tid = self.admin.get("/api/admin/feedback").json()["threads"][0]["id"]
        self.assertEqual(self.admin.post(f"/api/admin/feedback/{tid}", json={"text": "  "}).status_code,
                         400)

    # --- уведомления -------------------------------------------------------
    def test_admins_get_a_telegram_nudge(self):
        self.send("Сайт лежит")
        self.assertTrue(self.bot.sent, "админ должен получить сообщение в бота")
        chat, text = self.bot.sent[0]
        self.assertEqual(chat, 1001)
        self.assertIn("Обратная связь", text)
        self.assertIn("Сайт лежит", text)
        self.assertIn("/admin", text)

    def test_admin_reply_notifies_the_user_in_telegram(self):
        self.store._db.execute("UPDATE users SET tg_id=555, tg_linked_at=1 WHERE id=?",
                               (self.user_id,))
        self.store._db.commit()
        self.send()
        tid = self.admin.get("/api/admin/feedback").json()["threads"][0]["id"]
        self.admin.post(f"/api/admin/feedback/{tid}", json={"text": "Починили"})
        self.assertTrue(self.store.get_user(self.user_id).get("tg_id"), "tg привязан")
        to_user = [x for x in self.bot.sent if x[0] == 555]
        self.assertTrue(to_user, "пользователю должен прийти ответ в бота")
        self.assertIn("Починили", to_user[0][1])

    def test_work_does_not_depend_on_the_bot(self):
        feedback.ctx.bot = None
        r = self.send("Бот выключен, а написать надо")
        self.assertTrue(r.json()["ok"])
        d = self.user.get("/api/feedback").json()
        self.assertEqual(len(d["messages"]), 1)

    def test_broken_bot_does_not_break_the_message(self):
        class Bad:
            enabled = True

            def _admin_tg_ids(self):
                raise RuntimeError("нет админов")

        feedback.ctx.bot = Bad()
        r = self.send("Всё равно должно сохраниться")
        self.assertTrue(r.json()["ok"])
        self.assertEqual(len(self.user.get("/api/feedback").json()["messages"]), 1)


class FeedbackNotifyTest(unittest.TestCase):
    """Уведомления: проверяем, что троттлинг не даёт спамить админам."""

    class Bot:
        enabled = True

        def __init__(self):
            self.sent = []

        def _admin_tg_ids(self):
            return [7]

        async def send(self, chat_id, text, markup=None, parse="HTML", raw=False):
            self.sent.append(text)
            return 1

    def setUp(self):
        feedback._notified.clear()
        self.bot = self.Bot()
        feedback.ctx.bot = self.bot
        feedback.ctx.public_url = "https://liqscope.online"

    def tearDown(self):
        feedback._notified.clear()
        feedback.ctx.bot = None

    def test_repeat_notifications_are_throttled(self):
        user = {"id": 1, "first_name": "Вася", "username": "vasya", "email": "v@e.ru"}
        thread = {"id": 5}
        for i in range(3):
            asyncio.run(feedback.notify_admin(thread, user, f"сообщение {i}", first=False))
        self.assertEqual(len(self.bot.sent), 1, "второе и третье сообщения — в пределах паузы")

    def test_first_message_always_notifies(self):
        user = {"id": 1, "first_name": "Вася", "email": "v@e.ru"}
        asyncio.run(feedback.notify_admin({"id": 6}, user, "первое", first=False))
        asyncio.run(feedback.notify_admin({"id": 6}, user, "второе", first=True))
        self.assertEqual(len(self.bot.sent), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
