"""Тест для chat_reads per-user per-chat — Part A."""
import os
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp(prefix="liq_chat_read_")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_HISTORY_FILE"] = "0"
os.environ["LIQSCOPE_DEMO"] = "1"

import accounts as accounts_mod
import terminal_chat as terminal_chat_mod


class ChatReadStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = accounts_mod.Store(path=os.path.join(TMP, "accounts.db"), secret="test-secret")
        terminal_chat_mod.ctx.store = cls.store

    def setUp(self):
        # два пользователя — как в test_private_chat
        self.u1 = self.store.create_email_user("u1@example.com", password_hash="x")["user"]
        self.u2 = self.store.create_email_user("u2@example.com", password_hash="x")["user"]
        # задаём display_name
        try:
            self.store.set_user_name(self.u1["id"], "User1")
            self.store.set_user_name(self.u2["id"], "User2")
        except Exception:
            pass
        self.uid1 = int(self.u1["id"])
        self.uid2 = int(self.u2["id"])

    def test_mark_read_saves_and_unread(self):
        # пишем 3 сообщения в public
        m1 = self.store.add_chat_message(self.uid1, "User1", "hello 1")
        time.sleep(0.01)
        m2 = self.store.add_chat_message(self.uid2, "User2", "hello 2")
        time.sleep(0.01)
        m3 = self.store.add_chat_message(self.uid1, "User1", "hello 3")
        self.assertTrue(m1["ok"] and m2["ok"] and m3["ok"])
        id1, id2, id3 = m1["id"], m2["id"], m3["id"]

        # юзер1 не читал — unread = 3
        unread = self.store.chat_unread_counts(self.uid1)
        self.assertGreaterEqual(unread.get("public", 0), 3)

        # помечаем прочитанным до id2
        self.store.chat_mark_read(self.uid1, "public", last_id=id2, last_ts=time.time())
        lr = self.store.chat_last_read(self.uid1, "public")
        self.assertEqual(lr["last_read_id"], id2)

        # теперь unread = 1 (только id3)
        unread2 = self.store.chat_unread_counts(self.uid1)
        self.assertEqual(unread2.get("public", 0), 1)

        # помечаем до id3 — unread 0
        self.store.chat_mark_read(self.uid1, "public", last_id=id3, last_ts=time.time())
        unread3 = self.store.chat_unread_counts(self.uid1)
        self.assertEqual(unread3.get("public", 0), 0)

        # повторный markRead с меньшим id не должен уменьшить
        self.store.chat_mark_read(self.uid1, "public", last_id=id1, last_ts=time.time()-100)
        lr2 = self.store.chat_last_read(self.uid1, "public")
        self.assertEqual(lr2["last_read_id"], id3)

    def test_services_read(self):
        # сигналы per-user
        r1 = self.store.add_user_service_message(self.uid1, "alert", "sig1", {"symbol": "BTC"})
        r2 = self.store.add_user_service_message(self.uid1, "alert", "sig2", {"symbol": "ETH"})
        self.assertTrue(r1["ok"] and r2["ok"])
        # unread 2
        unread = self.store.chat_unread_counts(self.uid1)
        self.assertGreaterEqual(unread.get("services", 0), 2)
        # mark read до первого
        self.store.chat_mark_read(self.uid1, "services", last_id=r1["id"], last_ts=time.time())
        unread2 = self.store.chat_unread_counts(self.uid1)
        self.assertEqual(unread2.get("services", 0), 1)
        # mark read до второго — 0
        self.store.chat_mark_read(self.uid1, "services", last_id=r2["id"], last_ts=time.time())
        unread3 = self.store.chat_unread_counts(self.uid1)
        self.assertEqual(unread3.get("services", 0), 0)

    def test_dm_read_via_chat_reads(self):
        # создаём комнату
        room_res = self.store.private_room_open(self.uid1, self.uid2)
        self.assertTrue(room_res["ok"])
        room_id = int(room_res["room"]["id"])
        # сообщения
        self.store.private_add_message(room_id, self.uid1, "hi b")
        self.store.private_add_message(room_id, self.uid1, "how are you")
        # у второго непрочитано 2
        unread_map = self.store.private_unread_for(self.uid2)
        self.assertEqual(unread_map.get(room_id, 0), 2)
        # помечаем прочитанным
        msgs = self.store.private_room_messages(room_id, 0, 10)
        last_id = max(m["id"] for m in msgs)
        self.store.private_mark_read(room_id, self.uid2, last_id)
        self.store.chat_mark_read(self.uid2, f"dm:{room_id}", last_id=last_id, last_ts=time.time())
        unread_map2 = self.store.private_unread_for(self.uid2)
        self.assertEqual(unread_map2.get(room_id, 0), 0)

    def test_history_limit_constant(self):
        # проверяем что константа MAX_CHAT_HISTORY существует и 50
        self.assertTrue(hasattr(terminal_chat_mod, "MAX_CHAT_HISTORY"))
        self.assertEqual(terminal_chat_mod.MAX_CHAT_HISTORY, 50)

    def test_after_relogin_not_new(self):
        # симулируем: юзер прочитал до id=5, потом приходят новые id=6,7
        # после relogin сервер должен отдавать unread только 2, а не 50
        # пишем 5 сообщений
        ids = []
        for i in range(5):
            r = self.store.add_chat_message(self.uid1, "User1", f"msg {i}")
            ids.append(r["id"])
        # mark read до последнего из 5
        self.store.chat_mark_read(self.uid1, "public", last_id=ids[-1], last_ts=time.time())
        # пишем ещё 2
        r6 = self.store.add_chat_message(self.uid2, "User2", "new 6")
        r7 = self.store.add_chat_message(self.uid2, "User2", "new 7")
        unread = self.store.chat_unread_counts(self.uid1)
        self.assertEqual(unread.get("public", 0), 2)
        # list_chat_messages with after_id=last_read should return only 2
        rows = self.store.list_chat_messages(limit=50, after_id=ids[-1])
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["id"] for r in rows], [r6["id"], r7["id"]])


if __name__ == "__main__":
    unittest.main()
