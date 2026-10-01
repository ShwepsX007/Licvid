"""API «Менеджера настроек» админки: /api/admin/settings и live-проверки.

Поднимаем настоящие маршруты web_account на TestClient. Админ — почта из
списка владельцев (как в проде через LIQSCOPE_ADMIN_EMAILS).

Проверяем:
* GET отдаёт секреты маской, а не исходным значением;
* POST сохраняет ключи в БД настроек и применяет их на лету
  (почтовый транспорт пересобирается, списки админов обновляются);
* «***»/пустое значение секрета не затирает сохранённое;
* test-smtp с недоступным сервером отдаёт 400 с текстом ошибки и не роняет
  процесс (проверка на живом отказе подключения 127.0.0.1:1);
* test-tg с невалидным токеном отдаёт 400 «Invalid Telegram Token»
  (ответ Telegram подменяем, в сеть тест не ходит).
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app_settings  # noqa: E402
import web_account  # noqa: E402
from accounts import Store  # noqa: E402
from mailer import Mailer  # noqa: E402

LINK_RE = re.compile(r"/verify\?token=([\w.\-]+)")


class AdminSettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=["boss@liqscope.online"])
        self.settings = app_settings.SettingsManager(
            os.path.join(self.tmp.name, "a.db"))
        web_account.ctx.store = self.store
        web_account.ctx.settings = self.settings
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        web_account.ctx.cookie_secure = False
        web_account.ctx.require_email_verification = True
        web_account.ctx.mailer = Mailer(None, sender="",
                                        public_url="https://liqscope.online")
        for lim in (web_account._MAIL_RATE, web_account._MAIL_RATE_EMAIL,
                    web_account._LOGIN_RATE, web_account._CAPTCHA_RATE):
            lim._hits.clear()
        app = FastAPI()
        web_account.register_account_routes(app)
        self.client = TestClient(app)

    def tearDown(self):
        web_account.ctx.settings = None
        self.store.close()
        self.settings.close()
        self.tmp.cleanup()

    # ----- вход владельцем ---------------------------------------------------
    def captcha(self) -> dict:
        data = self.client.get("/api/auth/captcha").json()
        a, b = [int(x) for x in str(data["question"]).split("+")]
        return {"captcha": data["token"], "answer": a + b}

    def login_boss(self):
        body = {"email": "boss@liqscope.online", "password": "Good-Pass-2026",
                "name": "Босс", "language": "ru"}
        body.update(self.captcha())
        r = self.client.post("/api/auth/email/register", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # подтверждаем почту: токен берём прямо из базы (письма нет — стенд)
        row = self.store._db.execute(
            "SELECT token FROM email_tokens ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertTrue(row, "токен подтверждения не создан")
        r2 = self.client.get(f"/verify?token={row['token']}",
                             follow_redirects=False)
        self.assertEqual(r2.status_code, 303)
        me = self.client.get("/api/auth/me").json()
        self.assertTrue(me["user"]["is_admin"], me)

    # ----- GET ------------------------------------------------------------
    def test_get_masks_secrets(self):
        self.login_boss()
        # настройки пишутся после входа: с ними почтовик соберёт настоящий
        # транспорт и регистрация пыталась бы слать реальное письмо
        self.settings.set("LIQSCOPE_SMTP_PASSWORD", "hunter2-secret")
        self.settings.set("LIQSCOPE_SMTP_HOST", "smtp.example")
        r = self.client.get("/api/admin/settings")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        pwd = d["settings"]["LIQSCOPE_SMTP_PASSWORD"]
        self.assertEqual(pwd["value"], "***")
        self.assertNotIn("hunter2-secret", r.text)
        self.assertEqual(d["settings"]["LIQSCOPE_SMTP_HOST"]["value"],
                         "smtp.example")
        self.assertIn("bot_welcome", d["site"])

    def test_get_requires_admin(self):
        r = self.client.get("/api/admin/settings")
        self.assertEqual(r.status_code, 401)

    # ----- POST ------------------------------------------------------------
    def test_post_saves_and_applies_smtp(self):
        self.login_boss()
        r = self.client.post("/api/admin/settings", json={
            "LIQSCOPE_SMTP_HOST": "smtp.new.example",
            "LIQSCOPE_SMTP_PORT": "465",
            "LIQSCOPE_SMTP_TLS": "ssl",
            "LIQSCOPE_SMTP_USER": "no-reply@liqscope.online",
            "LIQSCOPE_SMTP_PASSWORD": "new-pass",
            "bot_welcome": "привет",   # старые ключи продолжают работать
        })
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertIn("LIQSCOPE_SMTP_HOST", d["saved"])
        self.assertEqual(self.settings.get("LIQSCOPE_SMTP_HOST"),
                         "smtp.new.example")
        self.assertEqual(self.store.get_setting("bot_welcome"), "привет")
        # почта пересобрана на лету
        m = web_account.ctx.mailer
        self.assertTrue(m.enabled)
        self.assertEqual(m.transport.host, "smtp.new.example")
        # ответ не раскрывает пароль
        self.assertNotIn("new-pass", r.text)

    def test_post_keeps_secret_on_mask_or_empty(self):
        self.settings.set("LIQSCOPE_SMTP_PASSWORD", "kept-secret")
        self.login_boss()
        r = self.client.post("/api/admin/settings", json={
            "LIQSCOPE_SMTP_PASSWORD": "***",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.settings.get("LIQSCOPE_SMTP_PASSWORD"),
                         "kept-secret")
        r2 = self.client.post("/api/admin/settings", json={
            "LIQSCOPE_SMTP_PASSWORD": "",
        })
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(self.settings.get("LIQSCOPE_SMTP_PASSWORD"),
                         "kept-secret")

    def test_post_updates_admin_access_on_the_fly(self):
        self.login_boss()
        r = self.client.post("/api/admin/settings", json={
            "LIQSCOPE_ADMIN_IDS": "777, 888",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.store.admin_ids, {777, 888})

    def test_post_warns_about_bot_token_restart(self):
        self.login_boss()
        r = self.client.post("/api/admin/settings", json={
            "LIQSCOPE_BOT_TOKEN": "12345:NEW-TOKEN",
        })
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["restart_required"])
        self.assertTrue(any("перезапуск" in w for w in d["warnings"]), d)

    # ----- live-проверки -----------------------------------------------------
    def test_test_smtp_refused_connection_returns_400(self):
        self.login_boss()
        r = self.client.post("/api/admin/settings/test-smtp", json={
            "host": "127.0.0.1", "port": "1", "tls": "none",
            "timeout": 5,
        })
        self.assertEqual(r.status_code, 400)
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertIn("Ошибка SMTP", d["error"])
        # сервер жив и продолжает отвечать
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 200)

    def test_test_smtp_requires_host(self):
        self.login_boss()
        r = self.client.post("/api/admin/settings/test-smtp", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("не задан сервер", r.json()["error"])

    def test_test_tg_invalid_token(self):
        self.login_boss()

        async def fake_getme(token, timeout=15.0):
            return {"ok": False, "error_code": 401,
                    "description": "Unauthorized"}

        orig = web_account._tg_getme
        web_account._tg_getme = fake_getme
        try:
            r = self.client.post("/api/admin/settings/test-tg",
                                 json={"token": "123:bad"})
        finally:
            web_account._tg_getme = orig
        self.assertEqual(r.status_code, 400)
        self.assertIn("Invalid Telegram Token", r.json()["error"])

    def test_test_tg_valid_token(self):
        self.login_boss()

        async def fake_getme(token, timeout=15.0):
            return {"ok": True, "result": {"username": "liqscope_test_bot",
                                            "first_name": "LiqScope"}}

        orig = web_account._tg_getme
        web_account._tg_getme = fake_getme
        try:
            r = self.client.post("/api/admin/settings/test-tg",
                                 json={"token": "123:good"})
        finally:
            web_account._tg_getme = orig
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["username"], "liqscope_test_bot")

    def test_test_tg_requires_token(self):
        self.login_boss()
        r = self.client.post("/api/admin/settings/test-tg", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("не задан", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
