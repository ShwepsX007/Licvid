"""Сквозной сценарий по HTTP: регистрация → письмо → подтверждение → вход.

Поднимаем настоящие маршруты FastAPI (web_account.register_account_routes)
на TestClient: письма складываются в папку, токен достаём из ссылки в письме,
куки сессии проверяем по защищённым эндпоинтам.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import hashlib  # noqa: E402
import hmac  # noqa: E402
import time as _time  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import web_account  # noqa: E402
from accounts import Store  # noqa: E402
from mailer import Mailer, FileTransport  # noqa: E402

LINK_RE = re.compile(r"https://liqscope\.online(\S+)")


class EmailFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mail_dir = os.path.join(self.tmp.name, "mail")
        os.makedirs(self.mail_dir, exist_ok=True)
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=["boss@liqscope.online"])
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        web_account.ctx.cookie_secure = False
        web_account.ctx.require_email_verification = True
        web_account.ctx.mailer = Mailer(
            FileTransport(self.mail_dir), sender="LiqScope <no-reply@liqscope.online>",
            public_url="https://liqscope.online", enabled=True)
        for lim in (web_account._MAIL_RATE, web_account._MAIL_RATE_EMAIL,
                    web_account._LOGIN_RATE, web_account._CAPTCHA_RATE):
            lim._hits.clear()
        app = FastAPI()
        web_account.register_account_routes(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ----- помощники -------------------------------------------------------
    def letters(self):
        out = []
        for name in sorted(os.listdir(self.mail_dir)):
            with open(os.path.join(self.mail_dir, name), encoding="utf-8") as f:
                out.append(f.read())
        return out

    def last_link(self, path_prefix="") -> str:
        letters = self.letters()
        self.assertTrue(letters, "письма не отправлялись")
        links = [m.group(1) for m in LINK_RE.finditer(letters[-1])]
        if path_prefix:
            links = [l for l in links if l.startswith(path_prefix)]
        self.assertTrue(links, "в письме нет ссылки " + path_prefix)
        return links[0]

    def captcha(self, answer=None) -> dict:
        """Свежая задача-капча: считаем пример как живой человек."""
        data = self.client.get("/api/auth/captcha").json()
        self.assertTrue(data["ok"], data)
        a, b = [int(x) for x in str(data["question"]).split("+")]
        return {"captcha": data["token"],
                "answer": (a + b) if answer is None else answer}

    def register(self, email="bob@mail.ru", password="Good-Pass-2026", name="Боб",
                 answer=None, **extra):
        body = {"email": email, "password": password, "name": name, "language": "ru"}
        body.update(self.captcha(answer=answer))
        body.update(extra)
        return self.client.post("/api/auth/email/register", json=body)

    def widget_sign(self, data: dict, token="0:x") -> dict:
        """Подпись Telegram Login Widget, как её считает Telegram."""
        parts = [f"{k}={data[k]}" for k in sorted(data) if data[k] is not None]
        secret = hashlib.sha256(token.encode()).digest()
        digest = hmac.new(secret, "\n".join(parts).encode(), hashlib.sha256).hexdigest()
        out = dict(data)
        out["hash"] = digest
        return out

    def me(self):
        return self.client.get("/api/auth/me").json()

    # ----- регистрация -----------------------------------------------------
    def test_register_requires_good_email_and_password(self):
        r = self.client.post("/api/auth/email/register", json={"email": "мусор", "password": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "bad_email")
        r2 = self.client.post("/api/auth/email/register",
                              json={"email": "bob@mail.ru", "password": "12345678"})
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json()["error"], "weak")
        self.assertEqual(self.letters(), [])

    def test_register_sends_letter_and_keeps_user_locked(self):
        r = self.register()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["sent"])
        self.assertTrue(body["verify_required"])
        letters = self.letters()
        self.assertEqual(len(letters), 1)
        self.assertIn("bob@mail.ru", letters[0])
        self.assertIn("/verify?token=", letters[0])
        # пользователь есть, но не подтверждён и без сессии
        u = self.store.get_user_by_email("bob@mail.ru")
        self.assertFalse(u["email_verified"])
        self.assertIsNone(self.me()["user"])

    def test_verify_link_opens_session_and_cabinet(self):
        self.register()
        link = self.last_link("/verify")
        r = self.client.get(link, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/cabinet")
        self.assertTrue(r.cookies.get("liqscope_sid") or
                        any("liqscope_sid" in c for c in self.client.cookies))
        me = self.me()
        self.assertTrue(me["user"])
        self.assertEqual(me["user"]["email"], "bob@mail.ru")
        self.assertTrue(me["user"]["email_verified"])
        self.assertTrue(me["verified"])
        # ссылка одноразовая
        again = self.client.get(link, follow_redirects=False)
        self.assertEqual(again.status_code, 303)
        self.assertIn("verify=used", again.headers["location"])

    def test_expired_verify_link_tells_user(self):
        self.register()
        self.store._db.execute("UPDATE email_tokens SET expires_at=0")
        self.store._db.commit()
        r = self.client.get(self.last_link("/verify"), follow_redirects=False)
        self.assertIn("verify=expired", r.headers["location"])

    def test_second_registration_on_verified_email_tells_to_sign_in(self):
        self.register()
        self.client.get(self.last_link("/verify"))
        self.client.post("/api/auth/logout")
        r = self.register(password="Other-Pass-2026")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["exists"])
        self.assertFalse(body["sent"])
        # письмо на чужой аккаунт не уходит, пароль не перезаписывается
        self.assertEqual(len(self.letters()), 1)
        self.assertIn("уже зарегистрирована", body["hint"])
        login = self.client.post("/api/auth/email/login",
                                 json={"email": "bob@mail.ru", "password": "Other-Pass-2026"})
        self.assertEqual(login.status_code, 401)
        ok = self.client.post("/api/auth/email/login",
                              json={"email": "bob@mail.ru", "password": "Good-Pass-2026"})
        self.assertEqual(ok.status_code, 200)

    # ----- вход по паролю --------------------------------------------------
    def test_login_blocked_until_email_verified(self):
        self.register()
        r = self.client.post("/api/auth/email/login",
                             json={"email": "bob@mail.ru", "password": "Good-Pass-2026"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "email_unverified")
        self.assertTrue(r.json()["sent"])          # письмо отправили повторно
        self.assertEqual(len(self.letters()), 2)
        self.assertIsNone(self.me()["user"])

    def test_login_wrong_password_and_unknown_email(self):
        self.register()
        self.client.get(self.last_link("/verify"))
        self.client.post("/api/auth/logout")
        bad = self.client.post("/api/auth/email/login",
                               json={"email": "bob@mail.ru", "password": "Wrong-Pass-2026"})
        self.assertEqual(bad.status_code, 401)
        self.assertIsNone(self.me()["user"])
        ghost = self.client.post("/api/auth/email/login",
                                 json={"email": "ghost@mail.ru", "password": "Good-Pass-2026"})
        self.assertEqual(ghost.status_code, 401)

    def test_login_after_verification_works(self):
        self.register()
        self.client.get(self.last_link("/verify"))
        self.client.post("/api/auth/logout")
        r = self.client.post("/api/auth/email/login",
                             json={"email": "BOB@mail.ru", "password": "Good-Pass-2026"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["email"], "bob@mail.ru")
        self.assertTrue(self.me()["user"])

    # ----- вход по ссылке из письма ---------------------------------------
    def test_passwordless_login_link(self):
        self.register()
        self.client.get(self.last_link("/verify"))
        self.client.post("/api/auth/logout")
        r = self.client.post("/api/auth/email/link", json={"email": "bob@mail.ru"})
        self.assertTrue(r.json()["ok"])
        link = self.last_link("/email-login")
        got = self.client.get(link, follow_redirects=False)
        self.assertEqual(got.status_code, 303)
        self.assertEqual(got.headers["location"], "/cabinet")
        self.assertTrue(self.me()["user"])

    def test_login_link_unknown_email_is_silent(self):
        r = self.client.post("/api/auth/email/link", json={"email": "ghost@mail.ru"})
        self.assertTrue(r.json()["ok"])         # ответ одинаковый
        self.assertEqual(self.letters(), [])    # но письма нет
        self.assertIsNone(self.me()["user"])

    def test_login_link_for_unverified_account_does_not_sign_in(self):
        self.register()
        self.client.post("/api/auth/email/link", json={"email": "bob@mail.ru"})
        link = self.last_link("/email-login")
        r = self.client.get(link, follow_redirects=False)
        self.assertEqual(r.headers["location"], "/login?verify=first")
        self.assertIsNone(self.me()["user"])

    # ----- сброс пароля ----------------------------------------------------
    def test_password_reset_full_cycle(self):
        self.register()
        self.client.get(self.last_link("/verify"))
        self.client.post("/api/auth/logout")
        r = self.client.post("/api/auth/email/reset", json={"email": "bob@mail.ru"})
        self.assertTrue(r.json()["ok"])
        link = self.last_link("/reset")
        token = link.split("token=")[1]
        info = self.client.get("/api/auth/email/token?token=" + token).json()
        self.assertTrue(info["ok"])
        self.assertEqual(info["email"], "bob@mail.ru")
        self.assertEqual(self.client.get("/api/auth/email/token?token=nope").json()["error"],
                         "unknown")
        bad = self.client.post("/api/auth/email/set-password",
                               json={"token": token, "password": "123"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/auth/email/set-password",
                              json={"token": token, "password": "Fresh-Pass-2026",
                                    "email": "bob@mail.ru"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["ok"])
        self.assertTrue(self.me()["user"])          # сессия после смены пароля
        self.client.post("/api/auth/logout")
        old = self.client.post("/api/auth/email/login",
                               json={"email": "bob@mail.ru", "password": "Good-Pass-2026"})
        self.assertEqual(old.status_code, 401)
        new = self.client.post("/api/auth/email/login",
                               json={"email": "bob@mail.ru", "password": "Fresh-Pass-2026"})
        self.assertEqual(new.status_code, 200)
        # той же ссылкой второй раз — нельзя
        again = self.client.post("/api/auth/email/set-password",
                                 json={"token": token, "password": "Third-Pass-2026"})
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()["error"], "used")

    # ----- шлюз подтверждения ---------------------------------------------
    def test_services_closed_without_verification(self):
        self.register()
        self.client.post("/api/auth/email/login",
                         json={"email": "bob@mail.ru", "password": "Good-Pass-2026"})
        self.assertIsNone(self.me()["user"])       # сессии нет вовсе
        # но если сессию выдать напрямую — сервисы всё равно закрыты
        u = self.store.get_user_by_email("bob@mail.ru")
        token = self.store.create_session(u["id"], "", "")
        self.client.cookies.set("liqscope_sid", token)
        me = self.me()
        self.assertTrue(me["user"])
        self.assertFalse(me["verified"])
        r = self.client.get("/api/account/services")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "email_unverified")
        self.client.post("/api/auth/logout")

    def test_resend_verify_letter(self):
        self.register()
        r = self.client.post("/api/auth/email/resend", json={"email": "bob@mail.ru"})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(len(self.letters()), 2)
        bad = self.client.post("/api/auth/email/resend", json={"email": "мусор"})
        self.assertEqual(bad.status_code, 400)

    def test_rate_limit_on_letters(self):
        for _ in range(3):
            web_account._MAIL_RATE_EMAIL._hits.clear()
            self.register(email="spam@mail.ru")
        r = self.register(email="spam@mail.ru")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["error"], "rate")

    # ----- админ по почте и состояние почты -------------------------------
    def test_admin_overview_reports_mail_state(self):
        self.register(email="boss@liqscope.online", password="Admin-Pass-2026")
        self.client.get(self.last_link("/verify"))
        me = self.me()
        self.assertTrue(me["user"]["is_admin"])
        ov = self.client.get("/api/admin/overview").json()
        self.assertTrue(ov["mail"]["enabled"])
        self.assertIn("smtp", ov["mail"])
        # заодно видно воронку пробного доступа к слоям (web_layers)
        self.assertIn("layers_trials", ov)
        self.assertEqual(ov["layers_trials"]["total"], 0)

    # ----- Telegram: привязка через бота ----------------------------------
    def test_telegram_link_endpoints(self):
        self.register()
        self.client.get(self.last_link("/verify"))
        # без токена бота привязка недоступна
        web_account.ctx.bot = None
        r = self.client.post("/api/auth/telegram/link")
        self.assertEqual(r.status_code, 503)

        class FakeBot:
            token = "0:x"
            username = "LiqScopeBot"

        web_account.ctx.bot = FakeBot()
        r = self.client.post("/api/auth/telegram/link")
        self.assertTrue(r.json()["ok"])
        nonce = r.json()["nonce"]
        self.assertIn("start=link_" + nonce, r.json()["bot_link"])
        st = self.client.get("/api/auth/telegram/link/status?nonce=" + nonce).json()
        self.assertTrue(st["pending"])
        # бот подтверждает привязку
        self.store.confirm_tg_link(nonce, {"id": 4242, "username": "bob_tg"})
        done = self.client.get("/api/auth/telegram/link/status?nonce=" + nonce).json()
        self.assertTrue(done["ok"])
        self.assertEqual(done["user"]["tg_id"], 4242)
        me = self.me()
        self.assertTrue(me["user"]["tg_linked"])
        self.assertEqual(me["user"]["username"], "bob_tg")
        # отвязка: аккаунт остаётся
        off = self.client.post("/api/auth/telegram/unlink").json()
        self.assertTrue(off["ok"])
        self.assertFalse(self.me()["user"]["tg_linked"])
        self.assertEqual(self.me()["user"]["email"], "bob@mail.ru")

    # ----- капча на регистрацию -------------------------------------------
    def test_captcha_required_to_register(self):
        r = self.client.post("/api/auth/email/register",
                             json={"email": "bob@mail.ru", "password": "Good-Pass-2026"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "captcha_missing")
        cap = self.client.get("/api/auth/captcha").json()
        self.assertNotIn("answer", cap)          # ответ считает только сервер
        self.assertTrue(cap["question"])
        self.assertEqual(self.letters(), [])
        self.assertIsNone(self.store.get_user_by_email("bob@mail.ru"))

    def test_captcha_wrong_answer_then_three_tries(self):
        cap = self.captcha()
        for _ in range(2):
            r = self.register(answer=cap["answer"] + 1, captcha=cap["captcha"])
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json()["error"], "captcha_wrong")
            self.assertTrue(r.json()["captcha"]["token"])      # дали новый пример
        # третья ошибка сжигает задачу
        r3 = self.register(answer=cap["answer"] + 1, captcha=cap["captcha"])
        self.assertEqual(r3.json()["error"], "captcha_used")
        # решённая задача больше не работает (одноразовая)
        solved = self.captcha()
        self.assertEqual(self.register(captcha=solved["captcha"],
                                       answer=solved["answer"]).status_code, 200)
        again = self.register(email="kate@mail.ru", captcha=solved["captcha"],
                              answer=solved["answer"])
        self.assertEqual(again.json()["error"], "captcha_used")
        self.assertEqual(len(self.letters()), 1)

    # ----- один человек — один аккаунт ------------------------------------
    def test_register_from_telegram_session_attaches_email(self):
        tg = self.store.upsert_telegram_user({"id": 777, "username": "bob_tg",
                                              "first_name": "Боб"})
        sid = self.store.create_session(tg["id"], "", "")
        self.client.cookies.set("liqscope_sid", sid)
        r = self.register()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("attached"))
        me = self.me()
        self.assertEqual(me["user"]["id"], tg["id"])        # тот же аккаунт
        self.assertEqual(me["user"]["email"], "bob@mail.ru")
        self.assertEqual(me["user"]["tg_id"], 777)
        self.assertFalse(me["user"]["email_verified"])
        # дубля нет: тот же адрес указывает на тот же аккаунт
        self.assertEqual(self.store.get_user_by_email("bob@mail.ru")["id"], tg["id"])
        email = self.store.get_user_by_email("bob@mail.ru")
        self.assertEqual(self.store.get_user_by_tg(777)["id"], email["id"])

    def test_second_address_while_signed_in_is_refused(self):
        """Вошедшему со своим адресом не подсовываем второй аккаунт."""
        self.register()
        self.client.get(self.last_link("/verify"))
        r = self.register(email="kate@mail.ru", password="Kate-Pass-2026")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "signed_in")
        self.assertIsNone(self.store.get_user_by_email("kate@mail.ru"))
        # а повторная регистрация своего же адреса — это просто «уже есть»
        same = self.register()
        self.assertEqual(same.status_code, 200)
        self.assertTrue(same.json().get("exists"))
        self.assertFalse(same.json().get("sent"))
        self.assertEqual(len(self.letters()), 1)

    def test_widget_login_keeps_session_account(self):
        class FakeBot:
            token = "0:x"
            username = "LiqScopeBot"

        web_account.ctx.bot = FakeBot()
        self.register()
        self.client.get(self.last_link("/verify"))
        me = self.me()
        uid = me["user"]["id"]
        data = self.widget_sign({"id": 8888, "first_name": "Боб", "username": "bob",
                                 "auth_date": int(_time.time())})
        r = self.client.post("/api/auth/telegram/widget", json=data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["id"], uid)        # сессия и аккаунт те же
        self.assertEqual(r.json()["user"]["tg_id"], 8888)
        self.assertEqual(r.json()["user"]["email"], "bob@mail.ru")
        self.assertEqual(self.me()["user"]["tg_id"], 8888)

    def test_widget_merges_telegram_only_account(self):
        class FakeBot:
            token = "0:x"
            username = "LiqScopeBot"

        web_account.ctx.bot = FakeBot()
        self.register()
        self.client.get(self.last_link("/verify"))
        uid = self.me()["user"]["id"]
        # человек писал боту раньше — есть «телеграмный» аккаунт без почты
        old = self.store.upsert_telegram_user({"id": 5150, "username": "old",
                                               "first_name": "Старый"})
        data = self.widget_sign({"id": 5150, "first_name": "Старый", "username": "old",
                                 "auth_date": int(_time.time())})
        r = self.client.post("/api/auth/telegram/widget", json=data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["id"], uid)        # остались на своём
        self.assertEqual(r.json()["user"]["email"], "bob@mail.ru")
        # вторая половина человека исчезла
        self.assertIsNone(self.store.get_user(old["id"]))
        self.assertEqual(self.store.get_user_by_tg(5150)["id"], uid)

    def test_attach_letter_links_telegram_to_email_account(self):
        self.register()
        self.client.get(self.last_link("/verify"))
        uid = self.me()["user"]["id"]
        self.client.post("/api/auth/logout")
        self.store.upsert_telegram_user({"id": 6060, "username": "bot_bob"})
        token = self.store.new_tg_attach(uid, {"tg_id": 6060, "username": "bot_bob",
                                               "first_name": "Боб"})
        r = self.client.get("/attach?token=" + token, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/cabinet?tg=attached")
        me = self.me()
        self.assertEqual(me["user"]["id"], uid)
        self.assertEqual(me["user"]["tg_id"], 6060)
        self.assertEqual(me["user"]["email"], "bob@mail.ru")
        # ссылка одноразовая
        again = self.client.get("/attach?token=" + token, follow_redirects=False)
        self.assertIn("mail=used", again.headers["location"])

    def test_me_reports_mail_disabled(self):
        web_account.ctx.mailer = Mailer(None, enabled=False)
        me = self.me()
        self.assertFalse(me["mail_enabled"])
        r = self.register()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["sent"])   # письма нет, но аккаунт создан

    def test_soft_mode_allows_login_without_verification(self):
        web_account.ctx.require_email_verification = False
        self.register()
        r = self.client.post("/api/auth/email/login",
                             json={"email": "bob@mail.ru", "password": "Good-Pass-2026"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.me()["user"])
        self.assertEqual(self.client.get("/api/account/services").status_code, 200)


if __name__ == "__main__":
    unittest.main()
