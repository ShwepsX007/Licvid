"""Пользователи, сессии, Telegram-подпись, вход по nonce."""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from accounts import Store, verify_telegram_widget, public_user, hash_ip  # noqa: E402


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "a.db")
        self.store = Store(self.path, secret="test-secret", admin_ids=[1001])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_register_and_admin_flag(self):
        u = self.store.upsert_telegram_user({
            "id": 1001, "username": "boss", "first_name": "Ada", "language_code": "ru",
        })
        self.assertTrue(u["is_admin"])
        self.assertEqual(u["display_name"], "Ada")
        u2 = self.store.upsert_telegram_user({
            "id": 2002, "username": "bob", "first_name": "Bob",
        })
        self.assertFalse(u2["is_admin"])
        c = self.store.user_counts()
        self.assertEqual(c["total"], 2)
        self.assertEqual(c["admins"], 1)

    def test_session_roundtrip(self):
        u = self.store.upsert_telegram_user({"id": 9, "first_name": "X"})
        tok = self.store.create_session(u["id"])
        got = self.store.user_by_session(tok)
        self.assertEqual(got["tg_id"], 9)
        self.store.drop_session(tok)
        self.assertIsNone(self.store.user_by_session(tok))

    def test_banned_session_rejected(self):
        u = self.store.upsert_telegram_user({"id": 11, "first_name": "Ban"})
        tok = self.store.create_session(u["id"])
        self.store.set_banned(u["id"], True)
        self.assertIsNone(self.store.user_by_session(tok))

    def test_nonce_login(self):
        u = self.store.upsert_telegram_user({"id": 42, "first_name": "N"})
        nonce = self.store.new_nonce()
        st = self.store.nonce_status(nonce)
        self.assertTrue(st.get("pending"))
        self.assertTrue(self.store.confirm_nonce(nonce, u["id"]))
        st2 = self.store.nonce_status(nonce)
        self.assertTrue(st2["ok"])
        self.assertEqual(st2["user_id"], u["id"])
        self.assertFalse(self.store.confirm_nonce(nonce, u["id"]))  # повторно нельзя

    def test_nonce_unknown(self):
        st = self.store.nonce_status("nope")
        self.assertEqual(st.get("error"), "unknown")

    def test_visits_and_services(self):
        self.store.record_visit("/", "vid1", None, "abcd")
        self.store.record_visit("/terminal", "vid1", None, "abcd")
        self.store.record_visit("/cabinet", "vid2", 1, "efgh")
        v = self.store.visit_stats(7)
        self.assertGreaterEqual(v["today_views"], 3)
        self.assertGreaterEqual(v["today_uniques"], 2)
        svcs = self.store.list_services()
        slugs = {s["slug"] for s in svcs}
        self.assertIn("alerts", slugs)
        self.assertIn("correlations", slugs)
        u = self.store.upsert_telegram_user({"id": 7, "first_name": "S"})
        r = self.store.toggle_user_service(u["id"], "alerts", True)
        self.assertTrue(r["ok"])
        self.assertIn("alerts", self.store.user_service_slugs(u["id"]))

    def test_telegram_widget_hash(self):
        token = "123:abc"
        data = {
            "id": 1,
            "first_name": "Ada",
            "auth_date": int(time.time()),
            "username": "ada",
        }
        parts = [f"{k}={data[k]}" for k in sorted(data)]
        secret = hashlib.sha256(token.encode()).digest()
        data["hash"] = hmac.new(secret, "\n".join(parts).encode(), hashlib.sha256).hexdigest()
        self.assertTrue(verify_telegram_widget(data, token))
        data["hash"] = "00" * 32
        self.assertFalse(verify_telegram_widget(data, token))

    def test_digest_heads_and_photos(self):
        heads = self.store.list_digest_heads()
        self.assertGreaterEqual(len(heads), 20)
        r = self.store.add_digest_head("☕ Мой заход за {h}ч")
        self.assertTrue(r["ok"])
        texts = [h["text"] for h in self.store.list_digest_heads()]
        self.assertIn("☕ Мой заход за {h}ч", texts)
        self.assertTrue(self.store.delete_digest_head(r["id"]))
        texts2 = [h["text"] for h in self.store.list_digest_heads()]
        self.assertNotIn("☕ Мой заход за {h}ч", texts2)
        photos = self.store.list_digest_photos()
        self.assertGreaterEqual(len(photos), 1)
        src = photos[0]["path"]
        self.assertTrue(os.path.isfile(src))
        with open(src, "rb") as f:
            blob = f.read()
        add = self.store.add_digest_photo(blob, filename="copy.jpg")
        self.assertTrue(add["ok"], add)
        self.assertTrue(os.path.isfile(add["path"]))
        pid = add["id"]
        n_before = len(self.store.list_digest_photos())
        self.assertTrue(self.store.delete_digest_photo(pid))
        self.assertEqual(len(self.store.list_digest_photos()), n_before - 1)
        self.assertFalse(os.path.isfile(add["path"]))
        # bundled file stays on disk after row delete
        bundled = photos[0]
        self.assertTrue(self.store.delete_digest_photo(bundled["id"]))
        self.assertTrue(os.path.isfile(bundled["path"]))

    def test_ip_hash_stable(self):
        a = hash_ip("s", "1.2.3.4")
        b = hash_ip("s", "1.2.3.4")
        c = hash_ip("s", "1.2.3.5")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
