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

    def test_bots_do_not_count_as_visitors(self):
        """Служебные запросы не идут ни в переходы, ни в посетителей."""
        self.store.record_visit("/", "vid1", None, "abcd", ua="Mozilla/5.0", bot=False)
        for _ in range(5):
            self.store.record_visit("/", "", None, "abcd",
                                    ua="TelegramBot (like TwitterBot)", bot=True)
        v = self.store.visit_stats(7)
        self.assertEqual(v["today_views"], 1)
        self.assertEqual(v["today_uniques"], 1)
        self.assertEqual(v["today_bots"], 5)
        self.assertEqual(v["paths"][0]["path"], "/")

    def test_cookie_less_guest_does_not_multiply(self):
        """Один гость без cookie — один посетитель, сколько бы страниц ни открыл."""
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        vid = "same-vid"
        # middleware спрашивает у базы: vid этой связки ip+ua уже выдан?
        self.assertEqual(self.store.visit_vid("iphash", ua), "")
        for path in ("/", "/terminal", "/cabinet"):
            self.store.record_visit(path, vid, None, "iphash", ua=ua)
        self.assertEqual(self.store.visit_vid("iphash", ua), vid)
        v = self.store.visit_stats(7)
        self.assertEqual(v["today_views"], 3)
        self.assertEqual(v["today_uniques"], 1)
        # другой ip с тем же браузером — это уже другой посетитель
        self.store.record_visit("/", "vid2", None, "other", ua=ua)
        v2 = self.store.visit_stats(7)
        self.assertEqual(v2["today_uniques"], 2)
        # служебные визиты не подсказывают vid
        self.assertEqual(self.store.visit_vid("iphash", "curl/8.0"), "")

    def test_days_report_bots_separately(self):
        self.store.record_visit("/", "vid1", None, "abcd", ua="Mozilla/5.0")
        self.store.record_visit("/", "", None, "abcd", ua="Googlebot/2.1", bot=True)
        v = self.store.visit_stats(7)
        day = v["days"][-1]
        self.assertEqual(day["views"], 1)
        self.assertEqual(day["uniques"], 1)
        self.assertEqual(day["bots"], 1)

    def test_browser_and_bot_agents_are_told_apart(self):
        """Кто гость, а кто служебный запрос — отдельный разбор user-agent."""
        import web_account as W
        browser = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
                   " (KHTML, like Gecko) Version/17.0 Safari/605.1.15")
        self.assertFalse(W._is_bot_ua(browser))
        for ua in ("curl/8.4.0", "python-requests/2.31", "Googlebot/2.1",
                   "TelegramBot (like TwitterBot)", "Mozilla/5.0 (compatible; YandexBot/3.0)",
                   "WhatsApp/2.23 A", "", "axios/1.6.0"):
            self.assertTrue(W._is_bot_ua(ua), ua)

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

    def test_digest_photo_accepts_phone_size_jpeg(self):
        # раньше лимит был 4 МБ — телефонные jpg часто больше
        blob = b"\xff\xd8\xff\xe0" + b"\x00" * (5 * 1000 * 1000) + b"\xff\xd9"
        r = self.store.add_digest_photo(blob, filename="phone.jpg")
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.isfile(r["path"]))
        self.store.delete_digest_photo(r["id"])

    def test_digest_photo_rejects_too_big_and_non_image(self):
        huge = b"\xff\xd8" + b"\x00" * 12_000_001
        self.assertEqual(self.store.add_digest_photo(huge).get("error"), "too_big")
        self.assertEqual(self.store.add_digest_photo(b"not-an-image-file-at-all!!").get("error"), "not_image")
        self.assertEqual(self.store.add_digest_photo(b"").get("error"), "empty")

    def test_alerts_service_is_live_and_stores_config(self):
        slugs = {s["slug"]: s for s in self.store.list_services()}
        self.assertFalse(slugs["alerts"]["coming_soon"])
        u = self.store.upsert_telegram_user({"id": 77, "first_name": "A"})
        r = self.store.set_user_service_config(u["id"], "alerts", {
            "watch": ["liq", "io"], "symbol": "ethusdt", "window_min": 15,
            "threshold": {"liq": 100000}, "enabled": True,
        }, enabled=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["config"]["symbol"], "ETH_USDT")
        self.assertIn("oi", r["config"]["watch"])
        got = self.store.get_user_service(u["id"], "alerts")
        self.assertTrue(got["enabled"])
        self.assertEqual(got["config"]["window_min"], 15)
        subs = self.store.list_alert_subscribers()
        self.assertEqual(len(subs), 1)
        hit = {"metric": "liq", "symbol": "ETH_USDT", "value": 150000,
               "threshold": 100000, "window_min": 15, "count": 3}
        pid = self.store.add_alert_event(u["id"], hit)
        self.assertGreater(pid, 0)
        self.assertTrue(self.store.last_alert_ts(u["id"], "liq", "ETH_USDT"))
        hist = self.store.list_alert_events(u["id"])
        self.assertEqual(hist[0]["metric"], "liq")

    # ----- капча ----------------------------------------------------------
    def test_captcha_solves_once_and_expires(self):
        tok = self.store.new_captcha(12)
        ok, err = self.store.check_captcha(tok, "12")
        self.assertTrue(ok, err)
        # решённая задача одноразовая
        again, err2 = self.store.check_captcha(tok, "12")
        self.assertFalse(again)
        self.assertEqual(err2, "used")
        # чужая задача не подходит
        self.assertEqual(self.store.check_captcha("nope", 1), (False, "unknown"))
        self.assertEqual(self.store.check_captcha("", 1), (False, "unknown"))
        # просроченная
        old = self.store.new_captcha(3, ttl=-1)
        self.assertEqual(self.store.check_captcha(old, 3), (False, "expired"))

    def test_captcha_three_tries(self):
        tok = self.store.new_captcha(5)
        self.assertEqual(self.store.check_captcha(tok, "4"), (False, "wrong"))
        self.assertEqual(self.store.check_captcha(tok, " шесть "), (False, "wrong"))
        self.assertEqual(self.store.check_captcha(tok, "6"), (False, "used"))
        # после «сожжённой» задачи правильный ответ уже не помогает
        self.assertEqual(self.store.check_captcha(tok, "5"), (False, "used"))

    def test_captcha_tokens_are_unique(self):
        """Задачи не повторяются, и ответ по токену не угадывается."""
        first = self.store.new_captcha(4)
        second = self.store.new_captcha(4)
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 16)
        row = self.store._db.execute("SELECT answer FROM captchas WHERE token=?",
                                     (first,)).fetchone()
        self.assertEqual(int(row["answer"]), 4)      # ответ знает только сервер

    # ----- дубли аккаунтов ------------------------------------------------
    def test_attach_email_merges_unverified_registration(self):
        made = self.store.create_email_user("bob@mail.ru", password_hash="h")
        old_id = made["user"]["id"]
        tg = self.store.upsert_telegram_user({"id": 555, "username": "bob"})
        r = self.store.attach_email(tg["id"], "BOB@mail.ru")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["merged"])
        self.assertEqual(r["user"]["id"], tg["id"])
        self.assertFalse(r["user"]["email_verified"])
        self.assertIsNone(self.store.get_user(old_id))       # незавершённая запись ушла
        self.assertEqual(self.store.get_user_by_email("bob@mail.ru")["id"], tg["id"])

    def test_attach_email_refuses_someone_elses_verified_address(self):
        made = self.store.create_email_user("bob@mail.ru", password_hash="h")
        self.store.mark_email_verified(made["user"]["id"])
        tg = self.store.upsert_telegram_user({"id": 556, "username": "not_bob"})
        r = self.store.attach_email(tg["id"], "bob@mail.ru")
        self.assertEqual(r["error"], "taken")
        self.assertEqual(self.store.get_user(tg["id"])["email"], "")
        self.assertIsNotNone(self.store.get_user(made["user"]["id"]))

    def test_attach_email_keeps_verified_flag_on_repeat(self):
        u = self.store.create_email_user("bob@mail.ru", password_hash="h")["user"]
        self.store.mark_email_verified(u["id"])
        r = self.store.attach_email(u["id"], "bob@mail.ru")
        self.assertTrue(r["ok"])
        self.assertTrue(r["user"]["email_verified"])         # подтверждение не сбрасываем

    def test_link_tg_to_user_merges_telegram_half(self):
        made = self.store.create_email_user("bob@mail.ru", password_hash="h")
        uid = made["user"]["id"]
        self.store.mark_email_verified(uid)
        old = self.store.upsert_telegram_user({"id": 777, "username": "bob_tg",
                                               "first_name": "Боб"})
        r = self.store.link_tg_to_user(uid, {"tg_id": 777, "username": "bob_tg",
                                             "first_name": "Боб"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["merged"])
        self.assertEqual(r["user"]["id"], uid)
        self.assertEqual(r["user"]["tg_id"], 777)
        self.assertEqual(r["user"]["email"], "bob@mail.ru")
        self.assertIsNone(self.store.get_user(old["id"]))
        # повторная привязка ничего не ломает
        self.assertTrue(self.store.link_tg_to_user(uid, {"tg_id": 777})["ok"])

    def test_link_tg_to_user_keeps_email_account_of_other_person(self):
        made = self.store.create_email_user("bob@mail.ru", password_hash="h")
        self.store.mark_email_verified(made["user"]["id"])
        other = self.store.create_email_user("kate@mail.ru", password_hash="h")["user"]
        self.store.mark_email_verified(other["id"])
        r = self.store.link_tg_to_user(other["id"], {"tg_id": 777})   # tg_id занят? нет
        self.assertTrue(r["ok"])
        tg = self.store.upsert_telegram_user({"id": 888, "username": "tg_only"})
        r2 = self.store.link_tg_to_user(made["user"]["id"], {"tg_id": 888})
        self.assertTrue(r2["ok"])
        self.assertIsNone(self.store.get_user(tg["id"]))       # «телеграмную» половину слили
        # чужой аккаунт с подтверждённой почтой не отдаём: 888 занят аккаунтом
        # с подтверждённой почтой, поэтому привязка отклоняется
        with self.store._lock:
            clash = self.store._link_tg_locked(other["id"], {"tg_id": 888})
        self.assertEqual(clash["error"], "taken")
        self.assertEqual(self.store.get_user(other["id"])["tg_id"], 777)

    def test_confirm_tg_attach_binds_and_consumes(self):
        made = self.store.create_email_user("bob@mail.ru", password_hash="h")
        uid = made["user"]["id"]
        self.store.mark_email_verified(uid)
        self.store.upsert_telegram_user({"id": 909, "username": "bob_tg"})
        tok = self.store.new_tg_attach(uid, {"tg_id": 909, "username": "bob_tg"})
        info, err = self.store.tg_attach_info(tok)
        self.assertEqual(err, "")
        self.assertEqual(info["tg_id"], 909)
        r = self.store.confirm_tg_attach(tok)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["user"]["id"], uid)
        self.assertEqual(r["user"]["tg_id"], 909)
        self.assertEqual(self.store.confirm_tg_attach(tok)["error"], "used")
        self.assertEqual(self.store.tg_attach_info("nope")[1], "unknown")
        self.assertEqual(self.store.confirm_tg_attach("")["error"], "unknown")

    def test_public_user_marks_email_state(self):
        u = self.store.create_email_user("bob@mail.ru", password_hash="h")["user"]
        pub = public_user(self.store.get_user(u["id"]))
        self.assertTrue(pub["email"])
        self.assertFalse(pub["email_verified"])
        self.store.mark_email_verified(u["id"])
        self.assertTrue(self.store.get_user(u["id"])["email_verified"])

    def test_ip_hash_stable(self):
        a = hash_ip("s", "1.2.3.4")
        b = hash_ip("s", "1.2.3.4")
        c = hash_ip("s", "1.2.3.5")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
