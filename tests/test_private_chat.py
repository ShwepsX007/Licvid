"""🔒 Приватные диалоги: комната на пару, приглашение, непрочитанное, TG-напоминание.

Покрывает Store-слой (accounts.py) и маршруты (private_chat.py) через
FastAPI TestClient — по образцу test_ads.py. Рит-лимиты в тестах гасятся
обнулением пауз в модуле.

Запуск:  python3 tests/test_private_chat.py
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
import private_chat as pc  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402


def _no_limits():
    terminal_chat_mod.RATE_SAME_SEC = 0.0
    terminal_chat_mod.RATE_LIMIT = 10_000
    pc.RATE_SAME_SEC = 0.0
    pc.RATE_LIMIT = 10_000
    pc.INVITE_LIMIT = 10_000


class FakeBot:
    """Мини-бот для напоминаний: помнит, что и куда отправил."""

    def __init__(self):
        self.sent = []
        self.running = True
        self.enabled = True

    async def send(self, chat_id, text, markup=None, parse="HTML", raw=False):
        self.sent.append({"chat_id": int(chat_id), "text": text})
        return len(self.sent)

    def site_link_kb(self, label="открыть чат", path="/cabinet"):
        return {"inline_keyboard": [[{"text": label, "url": "https://x" + path}]]}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=["boss@x.io"])
        self.alice = self.store.create_email_user("alice@x.io", password_hash="x")["user"]
        self.bob = self.store.create_email_user("bob@x.io", password_hash="x")["user"]
        self.carol = self.store.create_email_user("carol@x.io", password_hash="x")["user"]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_room_open_unique_per_pair(self):
        r1 = self.store.private_room_open(self.alice["id"], self.bob["id"])
        self.assertTrue(r1["ok"])
        self.assertTrue(r1["created"])
        r2 = self.store.private_room_open(self.bob["id"], self.alice["id"])
        self.assertFalse(r2["created"])
        self.assertEqual(r1["room"]["id"], r2["room"]["id"])

    def test_decline_then_reinvite_reopens(self):
        rid = self.store.private_room_open(self.alice["id"], self.bob["id"])["room"]["id"]
        self.store.private_room_set_status(rid, "declined")
        r = self.store.private_room_open(self.carol["id"], self.alice["id"])
        self.assertTrue(r["created"])
        r2 = self.store.private_room_open(self.bob["id"], self.alice["id"])
        self.assertEqual(r2["room"]["status"], "pending")
        self.assertEqual(r2["room"]["invited_by"], int(self.bob["id"]))

    def test_no_self_room(self):
        r = self.store.private_room_open(self.alice["id"], self.alice["id"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "self")

    def test_messages_unread_and_mark_read(self):
        room = self.store.private_room_open(self.alice["id"], self.bob["id"])["room"]
        rid = room["id"]
        self.store.private_room_set_status(rid, "active")
        m1 = self.store.private_add_message(rid, self.alice["id"], "привет")
        self.assertTrue(m1["ok"])
        self.store.private_add_message(rid, self.alice["id"], "ты тут?")
        unread = self.store.private_unread_for(self.bob["id"])
        self.assertEqual(unread.get(rid), 2)
        self.assertEqual(self.store.private_notify_counts(self.bob["id"])["unread"], 2)
        # прочитал — счётчики обнулились
        self.store.private_mark_read(rid, self.bob["id"], m1["id"] + 1)
        self.assertEqual(self.store.private_notify_counts(self.bob["id"])["unread"], 0)
        # у отправителя своих сообщений «непрочитанных» нет
        self.assertEqual(self.store.private_notify_counts(self.alice["id"])["unread"], 0)

    def test_nick_change_does_not_break_room(self):
        room = self.store.private_room_open(self.alice["id"], self.bob["id"])["room"]
        rid = room["id"]
        self.store.private_add_message(rid, self.alice["id"], "до смены ника")
        self.store.set_user_name(self.alice["id"], "НовыйНик")
        rooms = self.store.private_rooms_for(self.bob["id"])
        by_id = {r["id"]: r for r in rooms}
        self.assertIn(rid, by_id)
        self.assertEqual(by_id[rid]["peer"]["name"], "НовыйНик")
        msgs = self.store.private_room_messages(rid)
        self.assertEqual(msgs[0]["name"], "НовыйНик")  # имя всегда актуальное
        self.assertEqual(msgs[0]["user_id"], int(self.alice["id"]))  # якорь — id

    def test_participants_and_find(self):
        self.store.add_chat_message(self.alice["id"], "Alice", "всем привет")
        parts = self.store.chat_participants()
        ids = {p["id"] for p in parts}
        self.assertIn(int(self.alice["id"]), ids)
        found = self.store.find_chat_users("ali", exclude_id=self.bob["id"])
        self.assertEqual([u["id"] for u in found], [int(self.alice["id"])])
        self.assertEqual(self.store.find_chat_users("a"), [])  # короче 2 символов

    def test_reminders_due_flow(self):
        room = self.store.private_room_open(self.alice["id"], self.bob["id"])["room"]
        rid = room["id"]
        self.store.private_room_set_status(rid, "active")
        m = self.store.private_add_message(rid, self.alice["id"], "ответь, пожалуйста")
        due = self.store.private_rooms_due(time.time() - 3600)  # ещё не созрело
        self.assertEqual(due, [])
        due = self.store.private_rooms_due(time.time() + 1)     # «созрело»
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["member"], int(self.bob["id"]))
        self.assertEqual(due[0]["message_id"], m["id"])
        # отметили — больше не должно быть
        self.store.private_reminder_mark(rid, self.bob["id"], m["id"])
        self.assertEqual(self.store.private_rooms_due(time.time() + 1), [])
        # боб ответил — цикл напоминаний сбросился; элис снова пишет → сноваdue
        self.store.private_add_message(rid, self.bob["id"], "да, я тут")
        m2 = self.store.private_add_message(rid, self.alice["id"], "ок")
        due = self.store.private_rooms_due(time.time() + 1)
        self.assertEqual([d["message_id"] for d in due], [m2["id"]])


class RoutesTest(unittest.TestCase):
    def setUp(self):
        _no_limits()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=["boss@x.io"])
        self.alice = self.store.create_email_user("alice@x.io", password_hash="x")["user"]
        self.bob = self.store.create_email_user("bob@x.io", password_hash="x")["user"]
        self.bot = FakeBot()
        import web_account
        self.web_account = web_account
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        terminal_chat_mod.ctx.store = self.store
        terminal_chat_mod.ctx.hub = None
        pc.ctx.store = self.store
        pc.ctx.bot = self.bot
        pc.ctx.hub = None
        pc.ctx.public_url = "https://x.io"
        app = FastAPI()
        terminal_chat_mod.register_chat_routes(app)
        pc.register_private_chat_routes(app)
        self.app = app
        self.guest = TestClient(app)
        self.alice_c = self._client(self.alice["id"])
        self.bob_c = self._client(self.bob["id"])

    def _client(self, uid):
        c = TestClient(self.app)
        c.cookies.set(COOKIE_SID, self.store.create_session(int(uid)))
        return c

    def tearDown(self):
        self.web_account.ctx.store = None
        terminal_chat_mod.ctx.store = None
        pc.ctx.store = None
        pc.ctx.bot = None
        self.tmp.cleanup()

    def test_guests_rejected(self):
        self.assertEqual(self.guest.post("/api/chat/dm/invite",
                                         json={"peer_id": self.bob["id"]}).status_code, 401)
        self.assertEqual(self.guest.get("/api/chat/dm/rooms").status_code, 401)

    def test_invite_accept_message_roundtrip(self):
        r = self.alice_c.post("/api/chat/dm/invite", json={"peer_id": self.bob["id"]})
        self.assertTrue(r.json()["ok"], r.text)
        room = r.json()["room"]
        self.assertEqual(room["status"], "pending")
        self.assertEqual(room["peer"]["name"], "bob")
        # боб видит входящее приглашение и счётчик
        rooms = self.bob_c.get("/api/chat/dm/rooms").json()["rooms"]
        self.assertEqual(rooms[0]["id"], room["id"])
        n = self.bob_c.get("/api/chat/dm/notify").json()
        self.assertEqual(n["invites"], 1)
        # инициатор принимать не может
        self.assertEqual(self.alice_c.post(f"/api/chat/dm/{room['id']}/accept",
                                           json={"accept": True}).status_code, 403)
        # боб принимает
        self.assertTrue(self.bob_c.post(f"/api/chat/dm/{room['id']}/accept",
                                        json={"accept": True}).json()["ok"])
        # переписка туда-сюда
        m = self.alice_c.post(f"/api/chat/dm/{room['id']}/messages",
                              json={"text": "привет, боб"})
        self.assertTrue(m.json()["ok"], m.text)
        msgs = self.bob_c.get(f"/api/chat/dm/{room['id']}/messages").json()["messages"]
        self.assertEqual(msgs[0]["text"], "привет, боб")
        self.assertEqual(msgs[0]["mine"], False)
        # открытие истории = «прочитано»
        self.assertEqual(self.bob_c.get("/api/chat/dm/notify").json()["unread"], 0)
        r2 = self.bob_c.post(f"/api/chat/dm/{room['id']}/messages",
                             json={"text": "привет, элис"})
        self.assertTrue(r2.json()["ok"], r2.text)
        # третий участник не видит чужую комнату
        carol = self.store.create_email_user("carol@x.io", password_hash="x")["user"]
        carol_c = self._client(carol["id"])
        self.assertEqual(carol_c.get(f"/api/chat/dm/{room['id']}/messages").status_code, 404)
        self.assertEqual(carol_c.post(f"/api/chat/dm/{room['id']}/messages",
                                      json={"text": "хак"}).status_code, 404)

    def test_answer_activates_pending_room(self):
        room = self.alice_c.post("/api/chat/dm/invite",
                                 json={"peer_id": self.bob["id"]}).json()["room"]
        r = self.bob_c.post(f"/api/chat/dm/{room['id']}/messages",
                            json={"text": "ну привет раз позвала"})
        self.assertTrue(r.json()["ok"], r.text)
        got = self.alice_c.get(f"/api/chat/dm/{room['id']}/messages").json()
        self.assertEqual(got["room"]["status"], "active")

    def test_declined_room_closed_until_reinvite(self):
        room = self.alice_c.post("/api/chat/dm/invite",
                                 json={"peer_id": self.bob["id"]}).json()["room"]
        self.assertTrue(self.bob_c.post(f"/api/chat/dm/{room['id']}/accept",
                                        json={"accept": False}).json()["ok"])
        self.assertEqual(self.alice_c.post(f"/api/chat/dm/{room['id']}/messages",
                                           json={"text": "алло"}).status_code, 403)
        # повторное приглашение открывает диалог заново
        again = self.alice_c.post("/api/chat/dm/invite",
                                  json={"peer_id": self.bob["id"]}).json()
        self.assertTrue(again["ok"])
        self.assertEqual(again["room"]["status"], "pending")

    def test_invite_by_name_and_self(self):
        r = self.alice_c.post("/api/chat/dm/invite", json={"name": "bob@x.io"})
        self.assertTrue(r.json()["ok"], r.text)
        r2 = self.alice_c.post("/api/chat/dm/invite", json={"peer_id": self.alice["id"]})
        self.assertEqual(r2.status_code, 400)

    def test_users_endpoint_for_logged_in(self):
        self.store.add_chat_message(self.bob["id"], "bob", "пишу в общий")
        j = self.alice_c.get("/api/chat/users?q=").json()
        self.assertTrue(j["ok"])
        ids = [u["id"] for u in j["users"]]
        self.assertIn(int(self.bob["id"]), ids)
        self.assertNotIn(int(self.alice["id"]), ids)

    def test_presence_online_endpoints(self):
        self.assertEqual(self.alice_c.post("/api/terminal/chat/ping").status_code, 200)
        online = self.bob_c.get("/api/terminal/chat/online").json()
        self.assertEqual(online["count"], 1)          # элис онлайн, боба не считаем (он не пинговал)
        self.assertIn(int(self.alice["id"]), online["ids"])
        parts = self.guest.get("/api/terminal/chat/participants").json()
        self.assertTrue(parts["ok"])

    def test_scan_reminders_sends_once_skips_online(self):
        room = self.alice_c.post("/api/chat/dm/invite",
                                 json={"peer_id": self.bob["id"]}).json()["room"]
        self.bob_c.post(f"/api/chat/dm/{room['id']}/accept", json={"accept": True})
        # привяжем бобу Telegram — без него напоминаний нет
        with self.store._lock:  # noqa: SLF001
            self.store._db.execute("UPDATE users SET tg_id=555 WHERE id=?",
                                   (int(self.bob["id"]),))
            self.store._db.commit()
        self.alice_c.post(f"/api/chat/dm/{room['id']}/messages", json={"text": "ку"})
        # задержка 0 минут — сообщение сразу «созревает»
        self.store.set_setting(pc.DELAY_SETTING, "0")
        self.assertEqual(asyncio.run(pc.scan_reminders()), 0)  # 0 = выключено
        self.store.set_setting(pc.DELAY_SETTING, "0.0001")     # ~6 мс
        # боб «на сайте» — не трогаем
        terminal_chat_mod.chat_presence_touch(int(self.bob["id"]))
        self.assertEqual(asyncio.run(pc.scan_reminders()), 0)
        terminal_chat_mod._PRESENCE.clear()
        time.sleep(0.02)
        self.assertEqual(asyncio.run(pc.scan_reminders()), 1)
        self.assertIn("ку", self.bot.sent[0]["text"])
        # повторный проход: напоминали один раз — больше не надо
        self.assertEqual(asyncio.run(pc.scan_reminders()), 0)

    def test_admin_settings_delay_validation(self):
        # маршрут /api/admin/settings живёт в web_account — проверяем только
        # разбор значения, чтобы UI и сервер не разъехались
        self.store.set_setting(pc.DELAY_SETTING, "42")
        self.assertAlmostEqual(pc.delay_min(), 42.0)
        self.store.set_setting(pc.DELAY_SETTING, "0")
        self.assertEqual(pc.delay_min(), 0.0)
        self.store.set_setting(pc.DELAY_SETTING, "bad")
        self.assertAlmostEqual(pc.delay_min(), pc.DELAY_DEFAULT_MIN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
