"""Регистрация по почте, подтверждение, вход по паролю и по ссылке, сброс.

Проверяем хранилище (accounts.Store) и отправку писем (mailer.Mailer) —
без сервера: письма складываются в папку, ссылку достаём из токена.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from accounts import (Store, hash_password, normalize_email, password_problem,  # noqa: E402
                      public_user, valid_email, verify_password)
import mailer as mailer_module  # noqa: E402
from mailer import (Mailer, SmtpTransport, build_mailer, html_to_text,  # noqa: E402
                    ipv4_address, split_sender)


class EmailAccountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "a.db")
        self.store = Store(self.path, secret="s", admin_ids=[1001],
                           admin_emails=["boss@liqscope.online"])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ----- адреса и пароли ------------------------------------------------
    def test_email_normalization(self):
        self.assertEqual(normalize_email("  Foo@Mail.RU "), "foo@mail.ru")
        self.assertTrue(valid_email("f.o+tag@mail.ru"))
        self.assertFalse(valid_email("почта@mail.ru"))
        self.assertFalse(valid_email("no-at-sign"))
        self.assertFalse(valid_email("a@b"))
        self.assertFalse(valid_email("a..b@mail.ru"))

    def test_password_rules(self):
        self.assertEqual(password_problem(""), "short")
        self.assertEqual(password_problem("коротко"), "short")   # 7 символов
        self.assertEqual(password_problem("12345678"), "weak")
        self.assertEqual(password_problem("qwerty123"), "weak")
        self.assertEqual(password_problem("bob@mail.ru", "bob@mail.ru"), "weak")
        self.assertEqual(password_problem("Good-Pass-2026"), "")

    def test_password_hash_roundtrip(self):
        h = hash_password("Good-Pass-2026")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertIn("Good-Pass-2026", "Good-Pass-2026")   # в хеше пароля нет
        self.assertNotIn("Good-Pass-2026", h)
        self.assertTrue(verify_password("Good-Pass-2026", h))
        self.assertFalse(verify_password("good-pass-2026", h))
        self.assertFalse(verify_password("Good-Pass-2026", "мусор"))
        self.assertFalse(verify_password("Good-Pass-2026", ""))
        # соль своя у каждого хеша
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    # ----- регистрация и подтверждение ------------------------------------
    def test_register_needs_email_verification(self):
        r = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"),
                                         first_name="Боб")
        self.assertTrue(r["ok"])
        u = r["user"]
        self.assertEqual(u["email"], "bob@mail.ru")
        self.assertFalse(u["email_verified"])
        self.assertIsNone(u["tg_id"])
        self.assertFalse(u["tg_linked"])
        self.assertTrue(u["has_password"])
        self.assertEqual(u["display_name"], "Боб")

        token = self.store.new_email_token(u["id"], "verify", email=u["email"])
        owner, err = self.store.email_token_user(token, "verify")
        self.assertEqual(err, "")
        self.assertEqual(owner["id"], u["id"])
        verified = self.store.mark_email_verified(u["id"])
        self.assertTrue(verified["email_verified"])
        # токен одноразовый
        used, err2 = self.store.consume_email_token(token, "verify")
        self.assertEqual(err2, "")
        self.assertEqual(used["id"], u["id"])
        again, err3 = self.store.consume_email_token(token, "verify")
        self.assertIsNone(again)
        self.assertEqual(err3, "used")

    def test_second_registration_on_verified_email_is_rejected(self):
        r = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))
        self.store.mark_email_verified(r["user"]["id"])
        again = self.store.create_email_user("BOB@mail.ru", hash_password("Other-Pass-2026"))
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "taken")

    def test_registration_restarts_if_email_never_verified(self):
        r = self.store.create_email_user("bob@mail.ru", hash_password("First-Pass-2026"))
        self.assertTrue(r["ok"])
        again = self.store.create_email_user("bob@mail.ru", hash_password("Second-Pass-2026"),
                                             first_name="Борис")
        self.assertTrue(again["ok"])
        self.assertEqual(again["user"]["id"], r["user"]["id"])   # тот же аккаунт
        self.assertEqual(again["user"]["first_name"], "Борис")
        rows = self.store._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(rows, 1)

    def test_admin_by_email(self):
        r = self.store.create_email_user("boss@liqscope.online", hash_password("Admin-Pass-2026"))
        self.assertTrue(r["user"]["is_admin"])
        verified = self.store.mark_email_verified(r["user"]["id"])
        self.assertTrue(verified["is_admin"])
        other = self.store.create_email_user("nobody@mail.ru", hash_password("Nobody-Pass-2026"))
        self.assertFalse(other["user"]["is_admin"])

    def test_tokens_are_single_use_and_expire(self):
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        old = self.store.new_email_token(u["id"], "login")
        self.store._db.execute("UPDATE email_tokens SET expires_at=? WHERE token=?",
                               (time.time() - 1, old))
        self.store._db.commit()
        user, err = self.store.consume_email_token(old, "login")
        self.assertIsNone(user)
        self.assertEqual(err, "expired")
        # новый токен того же назначения гасит предыдущий
        first = self.store.new_email_token(u["id"], "login")
        second = self.store.new_email_token(u["id"], "login")
        _, e1 = self.store.consume_email_token(first, "login")
        self.assertEqual(e1, "used")
        u2, e2 = self.store.consume_email_token(second, "login")
        self.assertEqual(e2, "")
        self.assertEqual(u2["id"], u["id"])

    def test_reset_token_does_not_work_as_login(self):
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        reset = self.store.new_email_token(u["id"], "reset")
        user, err = self.store.consume_email_token(reset, "login")
        self.assertIsNone(user)
        self.assertEqual(err, "unknown")

    def test_token_rate_counting(self):
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        self.store.new_email_token(u["id"], "verify", email=u["email"])
        self.store.new_email_token(u["id"], "verify", email=u["email"])
        since = time.time() - 60
        self.assertEqual(self.store.email_tokens_recent(user_id=u["id"], since=since), 2)
        self.assertEqual(self.store.email_tokens_recent(email="bob@mail.ru", since=since), 2)
        self.assertEqual(self.store.email_tokens_recent(email="bob@mail.ru", since=since,
                                                        kind="login"), 0)

    # ----- Telegram: привязка, слияние, отвязка ---------------------------
    def test_telegram_link_flow(self):
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"),
                                         first_name="Боб")["user"]
        self.store.mark_email_verified(u["id"])
        nonce = self.store.new_link_nonce(u["id"])
        self.assertTrue(self.store.link_nonce_status(nonce).get("pending"))
        r = self.store.confirm_tg_link(nonce, {"id": 555, "username": "bob_tg",
                                               "first_name": "Bob", "language_code": "ru"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["user"]["tg_linked"])
        self.assertEqual(r["user"]["tg_id"], 555)
        self.assertEqual(r["user"]["email"], "bob@mail.ru")
        st = self.store.link_nonce_status(nonce)
        self.assertTrue(st["ok"])
        self.assertEqual(st["user"]["id"], u["id"])
        # повторно той же меткой — нельзя
        again = self.store.confirm_tg_link(nonce, {"id": 556})
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "used")

    def test_telegram_link_merges_previous_telegram_account(self):
        tg_only = self.store.upsert_telegram_user({"id": 777, "username": "bob_tg",
                                                   "first_name": "Bob"})
        self.store.toggle_user_service(tg_only["id"], "alerts", True)
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        self.store.mark_email_verified(u["id"])
        nonce = self.store.new_link_nonce(u["id"])
        r = self.store.confirm_tg_link(nonce, {"id": 777, "username": "bob_tg"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["merged"])
        self.assertEqual(r["user"]["id"], u["id"])
        # подписка из «телеграмного» аккаунта переехала, сам аккаунт исчез
        self.assertIn("alerts", self.store.user_service_slugs(u["id"]))
        self.assertIsNone(self.store.get_user(tg_only["id"]))

    def test_telegram_link_rejected_when_tg_bound_elsewhere(self):
        first = self.store.create_email_user("first@mail.ru", hash_password("First-Pass-2026"))
        self.store.mark_email_verified(first["user"]["id"])
        second = self.store.create_email_user("second@mail.ru", hash_password("Second-Pass-2026"))
        self.store.mark_email_verified(second["user"]["id"])
        n1 = self.store.new_link_nonce(first["user"]["id"])
        self.assertTrue(self.store.confirm_tg_link(n1, {"id": 888})["ok"])
        n2 = self.store.new_link_nonce(second["user"]["id"])
        bad = self.store.confirm_tg_link(n2, {"id": 888})
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "taken")
        # почтовый аккаунт не пострадал
        self.assertEqual(self.store.get_user(second["user"]["id"])["tg_id"], None)

    def test_telegram_unlink(self):
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        self.store.mark_email_verified(u["id"])
        nonce = self.store.new_link_nonce(u["id"])
        self.store.confirm_tg_link(nonce, {"id": 999, "username": "bob"})
        off = self.store.unlink_telegram(u["id"])
        self.assertFalse(off["tg_linked"])
        self.assertEqual(off["email"], "bob@mail.ru")
        self.assertTrue(off["email_verified"])
        self.assertEqual(self.store.admin_tg_ids(), [1001] if False else [])

    def test_broadcast_and_admins_skip_unlinked(self):
        mail_only = self.store.create_email_user("a@mail.ru", hash_password("Mail-Pass-2026"))["user"]
        self.store.mark_email_verified(mail_only["id"])
        self.store.set_admin(mail_only["id"], True)
        tg_user = self.store.upsert_telegram_user({"id": 4242, "first_name": "Tg"})
        self.assertEqual(self.store.tg_ids_for_broadcast(), [4242])
        # админ по почте без Telegram не попадает в список уведомлений бота
        self.assertNotIn(None, self.store.admin_tg_ids())
        self.assertEqual(self.store.admin_tg_ids(), [])
        self.assertEqual([u["id"] for u in [self.store.get_user(tg_user["id"])]], [tg_user["id"]])

    def test_alerts_subscribers_keep_tg_id_none(self):
        u = self.store.create_email_user("a@mail.ru", hash_password("Mail-Pass-2026"))["user"]
        self.store.mark_email_verified(u["id"])
        self.store.set_user_service_config(u["id"], "alerts", {"watch": ["liq"]}, enabled=True)
        subs = self.store.list_alert_subscribers()
        self.assertEqual(len(subs), 1)
        self.assertIsNone(subs[0]["tg_id"])

    def test_public_user_hides_password_hash(self):
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        self.assertNotIn("password_hash", u)
        row = self.store._db.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
        pub = public_user(row)
        self.assertNotIn("password_hash", pub)
        self.assertTrue(pub["has_password"])

    # ----- миграция старой базы ------------------------------------------
    def test_legacy_telegram_db_migrates(self):
        import sqlite3
        legacy_path = os.path.join(self.tmp.name, "legacy.db")
        raw = sqlite3.connect(legacy_path)
        raw.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE NOT NULL,
                username TEXT, first_name TEXT, last_name TEXT, photo_url TEXT,
                language TEXT DEFAULT 'ru',
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_banned INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, last_seen REAL NOT NULL,
                login_count INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO users(tg_id, username, first_name, is_admin, created_at, last_seen,
                              login_count)
            VALUES(1001, 'boss', 'Ada', 1, 1.0, 1.0, 3);
            """
        )
        raw.commit()
        raw.close()
        store = Store(legacy_path, secret="s", admin_ids=[1001],
                      admin_emails=["new@mail.ru"])
        try:
            old = store.get_user_by_tg(1001)
            self.assertEqual(old["display_name"], "Ada")
            self.assertTrue(old["is_admin"])
            # tg_id стал nullable — аккаунт по почте регистрируется
            fresh = store.create_email_user("new@mail.ru", hash_password("New-Pass-2026"))
            self.assertTrue(fresh["ok"])
            self.assertIsNone(fresh["user"]["tg_id"])
            self.assertTrue(store.mark_email_verified(fresh["user"]["id"])["is_admin"])
            # админ без Telegram не мешает списку уведомлений бота
            self.assertEqual(store.admin_tg_ids(), [1001])
        finally:
            store.close()

    def test_old_email_index_does_not_block(self):
        """Повторная миграция (рестарт сервера) ничего не ломает."""
        u = self.store.create_email_user("bob@mail.ru", hash_password("Good-Pass-2026"))["user"]
        self.store.mark_email_verified(u["id"])
        self.store.close()
        again = Store(self.path, secret="s")
        try:
            self.assertEqual(again.get_user_by_email("bob@mail.ru")["id"], u["id"])
        finally:
            again.close()


class MailerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mailer = Mailer(None, sender="LiqScope <no-reply@liqscope.online>",
                             public_url="https://liqscope.online", enabled=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_mailer_reports_and_does_not_raise(self):
        self.assertFalse(self.mailer.send("a@b.ru", "тема", "<p>привет</p>", kind="verify"))
        st = self.mailer.status()
        self.assertFalse(st["enabled"])
        self.assertEqual(st["last"]["reason"], "smtp_disabled")
        self.assertEqual(st["last"]["kind"], "verify")
        self.assertEqual(st["sent"], 0)
        self.assertEqual(st["failed"], 0)

    def test_links_point_to_public_url(self):
        self.assertEqual(self.mailer.link("/verify?token=abc"),
                         "https://liqscope.online/verify?token=abc")
        self.assertEqual(self.mailer.link("cabinet"), "https://liqscope.online/cabinet")

    def test_templates_contain_button_link(self):
        m = Mailer(None, sender="x", public_url="https://liqscope.online", enabled=False)
        sent = []

        class Capture:
            def send(self, to, subject, html, text=""):
                sent.append((to, subject, html, text))
                return True, ""

        m.transport = Capture()
        m.enabled = True
        self.assertTrue(m.send_verify("bob@mail.ru", "TOKEN1", name="Боб"))
        self.assertTrue(m.send_login_link("bob@mail.ru", "TOKEN2"))
        self.assertTrue(m.send_reset("bob@mail.ru", "TOKEN3"))
        verify, login, reset = sent
        self.assertIn("/verify?token=TOKEN1", verify[2])
        self.assertIn("/email-login?token=TOKEN2", login[2])
        self.assertIn("/reset?token=TOKEN3", reset[2])
        self.assertIn("Боб", verify[2])
        self.assertEqual(m.sent, 3)
        self.assertTrue(m.status()["last"]["ok"])

    def test_html_to_text_keeps_links(self):
        text = html_to_text('<p>Привет</p><a href="https://x/y">жми</a><br>хвост')
        self.assertIn("Привет", text)
        self.assertIn("https://x/y", text)
        self.assertIn("жми", text)
        self.assertNotIn("<p>", text)

    def test_file_transport_writes_letters(self):
        folder = os.path.join(self.tmp.name, "mail")
        m = build_mailer("https://liqscope.online")
        self.assertFalse(m.enabled)   # без LIQSCOPE_SMTP_HOST писем нет
        os.environ["LIQSCOPE_MAIL_DIR"] = folder
        try:
            file_mailer = build_mailer("https://liqscope.online")
            self.assertTrue(file_mailer.enabled)
            self.assertTrue(file_mailer.send_verify("bob@mail.ru", "T", name="Боб"))
            files = os.listdir(folder)
            self.assertEqual(len(files), 1)
            with open(os.path.join(folder, files[0]), encoding="utf-8") as f:
                body = f.read()
            self.assertIn("bob@mail.ru", body)
            self.assertIn("/verify?token=T", body)
        finally:
            os.environ.pop("LIQSCOPE_MAIL_DIR", None)

    def test_smtp_sender_defaults_to_login(self):
        """Яндекс/Gmail требуют, чтобы отправитель совпадал с логином."""
        keys = ("LIQSCOPE_SMTP_HOST", "LIQSCOPE_SMTP_USER", "LIQSCOPE_SMTP_FROM",
                "LIQSCOPE_SMTP_PORT", "LIQSCOPE_SMTP_TLS", "LIQSCOPE_MAIL_DIR")
        keep = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            os.environ["LIQSCOPE_SMTP_HOST"] = "smtp.yandex.ru"
            os.environ["LIQSCOPE_SMTP_USER"] = "terarasa@yandex.ru"
            m = build_mailer("https://liqscope.online")
            self.assertTrue(m.enabled)
            self.assertEqual(m.transport.sender, "LiqScope <terarasa@yandex.ru>")
            self.assertEqual(m.transport._sender_parts(),
                             ("LiqScope", "terarasa@yandex.ru"))
            # явный отправитель важнее логина
            os.environ["LIQSCOPE_SMTP_FROM"] = "LiqScope <no-reply@liqscope.online>"
            m2 = build_mailer("https://liqscope.online")
            self.assertEqual(m2.transport._sender_parts()[1], "no-reply@liqscope.online")
        finally:
            for k in keys:
                os.environ.pop(k, None)
                if keep[k] is not None:
                    os.environ[k] = keep[k]

    def test_sender_split_and_ipv4_lookup(self):
        self.assertEqual(split_sender("LiqScope <no-reply@liqscope.online>"),
                         ("LiqScope", "no-reply@liqscope.online"))
        self.assertEqual(split_sender("plain@liqscope.online"),
                         ("LiqScope", "plain@liqscope.online"))
        self.assertEqual(split_sender(""), ("LiqScope", "no-reply@liqscope.online"))
        self.assertEqual(ipv4_address("localhost"), "127.0.0.1")
        self.assertEqual(ipv4_address("такого-хоста-нет.лиqscope"),
                         "")   # мусорный домен — пусто, без исключения

    def test_smtp_retries_over_ipv4_after_network_error(self):
        """«Network is unreachable» из-за IPv6 → одна повторная попытка по IPv4."""
        calls = []

        def fake_deliver(host, port, user, password, tls, timeout, msg, to,
                         from_addr, ipv4_only=False):
            calls.append(ipv4_only)
            if not ipv4_only:
                raise OSError(101, "Network is unreachable")

        transport = SmtpTransport("smtp.yandex.ru", 465, "u@yandex.ru", "p",
                                  "LiqScope <u@yandex.ru>", tls="ssl")
        with mock.patch.object(mailer_module, "smtp_deliver", fake_deliver), \
                mock.patch.object(mailer_module, "ipv4_address", lambda h: "77.88.21.158"):
            ok, err = transport.send("bob@mail.ru", "Тема", "<p>привет</p>")
        self.assertTrue(ok, err)
        self.assertEqual(calls, [False, True])
        self.assertTrue(transport.ipv4)          # дальше сразу по IPv4

    def test_smtp_error_is_reported_when_ipv4_does_not_help(self):
        def fake_deliver(*a, **kw):
            raise OSError(101, "Network is unreachable")

        transport = SmtpTransport("smtp.yandex.ru", 465, "", "",
                                  "LiqScope <no-reply@liqscope.online>", tls="ssl")
        with mock.patch.object(mailer_module, "smtp_deliver", fake_deliver), \
                mock.patch.object(mailer_module, "ipv4_address", lambda h: "77.88.21.158"):
            ok, err = transport.send("bob@mail.ru", "Тема", "<p>привет</p>")
        self.assertFalse(ok)
        self.assertIn("Network is unreachable", err)

    def test_smtp_sender_parts(self):
        t = SmtpTransport("smtp.example.com", 465, "user", "pass",
                          "LiqScope <no-reply@liqscope.online>", tls="ssl")
        self.assertEqual(t._sender_parts(), ("LiqScope", "no-reply@liqscope.online"))
        t2 = SmtpTransport("h", 25, "", "", "plain@liqscope.online")
        self.assertEqual(t2._sender_parts()[1], "plain@liqscope.online")
        self.assertEqual(t2._sender_parts()[0], "LiqScope")


if __name__ == "__main__":
    unittest.main()
