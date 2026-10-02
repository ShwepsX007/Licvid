"""Тесты входа и регистрации через Google OAuth 2.0 (Login with Google).

Проверяем:
* миграцию БД и методы Store: find_user_by_google_id, find_user_by_email,
  link_google_id, create_user_from_google, unlink_google;
* GET /api/auth/providers и GET /api/auth/google/login (503 без ключей,
  302 редирект на Google с корректными параметрами и HttpOnly state cookie);
* защиту _safe_next (отклонение //evil.com и внешних URL);
* GET /api/auth/google/callback с замоканными ответами
  oauth2.googleapis.com/token и www.googleapis.com/oauth2/v3/userinfo
  (без реальных сетевых вызовов):
  - неверный или отсутствующий state -> 400;
  - неподтверждённый email -> 400;
  - создание нового пользователя с подтверждённой почтой и выдачей сессии;
  - привязка google_id к существующему пользователю с тем же email без дубля;
  - повторный вход по google_id;
  - отсутствие утечки client_secret и токенов в логах при ошибках;
* POST /api/auth/google/unlink (защита от потери последнего способа входа).
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app_settings  # noqa: E402
import web_account  # noqa: E402
from accounts import Store, hash_password  # noqa: E402

#: Адрес согласия Google, куда уходит гость из /api/auth/google/login.
GOOGLE_AUTH_HOST = "https://accounts.google.com/o/oauth2/v2/auth"
from mailer import Mailer  # noqa: E402


class GoogleOAuthStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(
            os.path.join(self.tmp.name, "a.db"),
            secret="test-secret",
            admin_emails=["boss@liqscope.online"],
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_create_and_find_google_user(self):
        u = self.store.create_user_from_google(
            email="Alice@Gmail.com",
            google_id="gid-100500",
            name="Alice Smith",
            avatar_url="https://lh3.googleusercontent.com/a/alice",
            language="en",
        )
        self.assertEqual(u["email"], "alice@gmail.com")
        self.assertTrue(u["email_verified"])
        self.assertEqual(u["google_id"], "gid-100500")
        self.assertTrue(u["google_linked"])
        self.assertEqual(u["display_name"], "Alice Smith")
        self.assertEqual(u["avatar_url"], "https://lh3.googleusercontent.com/a/alice")
        self.assertEqual(u["photo_url"], "https://lh3.googleusercontent.com/a/alice")

        found_gid = self.store.find_user_by_google_id("gid-100500")
        self.assertIsNotNone(found_gid)
        self.assertEqual(found_gid["id"], u["id"])

        found_email = self.store.find_user_by_email("ALICE@gmail.com")
        self.assertIsNotNone(found_email)
        self.assertEqual(found_email["id"], u["id"])

    def test_link_google_to_existing_email_user_without_duplicate(self):
        made = self.store.create_email_user(
            "bob@gmail.com", hash_password("Good-Pass-2026"), first_name="Bob"
        )
        uid = made["user"]["id"]
        self.assertFalse(made["user"]["email_verified"])

        linked = self.store.link_google_id(
            uid, "gid-bob-1", avatar_url="https://lh3.googleusercontent.com/a/bob"
        )
        self.assertIsNotNone(linked)
        self.assertEqual(linked["id"], uid)
        self.assertEqual(linked["google_id"], "gid-bob-1")
        self.assertTrue(linked["google_linked"])
        self.assertTrue(linked["email_verified"])
        self.assertTrue(linked["has_password"])
        self.assertEqual(self.store.user_counts()["total"], 1)

    def test_unlink_google_protection_and_success(self):
        u = self.store.create_user_from_google(
            email="onlygoogle@gmail.com", google_id="gid-only-1", name="Only"
        )
        # Нет ни пароля, ни Telegram -> отвязка запрещена
        res = self.store.unlink_google(u["id"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "last_auth_method")
        self.assertEqual(self.store.get_user(u["id"])["google_id"], "gid-only-1")

        # Задаём пароль -> теперь отвязать можно
        self.store.set_user_password(u["id"], hash_password("Good-Pass-2026"))
        res2 = self.store.unlink_google(u["id"])
        self.assertTrue(res2["ok"])
        self.assertIsNone(res2["user"]["google_id"])
        self.assertFalse(res2["user"]["google_linked"])


class GoogleOAuthHttpFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.tmp.name, "a.db")
        self.store = Store(
            db_path, secret="test-secret", admin_emails=["boss@liqscope.online"]
        )
        self.settings = app_settings.SettingsManager(db_path)
        web_account.ctx.store = self.store
        web_account.ctx.settings = self.settings
        web_account.ctx.secret = "test-secret"
        web_account.ctx.public_url = "https://liqscope.online"
        web_account.ctx.cookie_secure = False
        web_account.ctx.require_email_verification = True
        web_account.ctx.mailer = Mailer(
            None, sender="", public_url="https://liqscope.online"
        )
        for lim in (
            web_account._MAIL_RATE,
            web_account._MAIL_RATE_EMAIL,
            web_account._LOGIN_RATE,
            web_account._CAPTCHA_RATE,
        ):
            lim._hits.clear()
        web_account._OAUTH_STATES.clear()
        web_account._OAUTH_CONSUMED.clear()

        self._env_backup = {}
        for k in (
            "LIQSCOPE_GOOGLE_CLIENT_ID",
            "LIQSCOPE_GOOGLE_CLIENT_SECRET",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "LIQSCOPE_PUBLIC_URL",
            "PUBLIC_URL",
            "SITE_URL",
        ):
            self._env_backup[k] = os.environ.pop(k, None)

        self._orig_exchange = web_account._google_exchange_code
        self._orig_userinfo = web_account._google_fetch_userinfo

        app = FastAPI()
        web_account.register_account_routes(app)
        self.client = TestClient(app)

    def tearDown(self):
        web_account._google_exchange_code = self._orig_exchange
        web_account._google_fetch_userinfo = self._orig_userinfo
        web_account.ctx.settings = None
        self.store.close()
        self.settings.close()
        self.tmp.cleanup()
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def configure_google(self):
        self.settings.set(
            "LIQSCOPE_GOOGLE_CLIENT_ID",
            "123456789-testclient.apps.googleusercontent.com",
        )
        self.settings.set(
            "LIQSCOPE_GOOGLE_CLIENT_SECRET",
            "GOCSPX-TopSecretValueNeverLog",
        )

    def mock_google(self, token_resp=None, userinfo_resp=None, token_exc=None):
        async def fake_exchange(code, client_id, client_secret, redirect_uri, timeout=10.0):
            if token_exc:
                raise token_exc
            return dict(
                token_resp
                or {
                    "access_token": "ya29.mock_access_token_secret",
                    "token_type": "Bearer",
                    "_status": 200,
                }
            )

        async def fake_userinfo(access_token, timeout=10.0):
            return dict(
                userinfo_resp
                or {
                    "sub": "google-uid-42",
                    "email": "trader@gmail.com",
                    "email_verified": True,
                    "name": "Crypto Trader",
                    "picture": "https://lh3.googleusercontent.com/a/trader",
                    "_status": 200,
                }
            )

        web_account._google_exchange_code = fake_exchange
        web_account._google_fetch_userinfo = fake_userinfo

    def test_safe_next_rejects_external_urls(self):
        self.assertEqual(web_account._safe_next("//evil.com"), "")
        self.assertEqual(web_account._safe_next("https://evil.com/path"), "")
        self.assertEqual(web_account._safe_next("javascript:alert(1)"), "")
        self.assertEqual(web_account._safe_next("/terminal"), "/terminal")
        self.assertEqual(web_account._safe_next("/cabinet?tab=alerts"), "/cabinet?tab=alerts")

    def test_login_returns_503_when_not_configured(self):
        p = self.client.get("/api/auth/providers").json()
        self.assertFalse(p["google"])
        r = self.client.get("/api/auth/google/login", follow_redirects=False)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"], "google_oauth_not_configured")

    def test_login_redirects_to_google_with_state_and_safe_next(self):
        self.configure_google()
        p = self.client.get("/api/auth/providers").json()
        self.assertTrue(p["google"])

        r = self.client.get(
            "/api/auth/google/login?next=//evil.com", follow_redirects=False
        )
        self.assertEqual(r.status_code, 302)
        loc = r.headers["location"]
        parsed = urlparse(loc)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(parsed.path, "/o/oauth2/v2/auth")
        qs = parse_qs(parsed.query)
        self.assertEqual(
            qs["client_id"][0],
            "123456789-testclient.apps.googleusercontent.com",
        )
        self.assertEqual(
            qs["redirect_uri"][0],
            "https://liqscope.online/api/auth/google/callback",
        )
        self.assertEqual(qs["response_type"][0], "code")
        self.assertEqual(qs["scope"][0], "openid email profile")
        self.assertEqual(qs["access_type"][0], "online")
        self.assertEqual(qs["prompt"][0], "select_account")
        state = qs["state"][0]
        self.assertGreaterEqual(len(state), 32)
        self.assertEqual(r.cookies.get("liqscope_oauth_state"), state)
        # //evil.com отброшен -> сохранён безопасный дефолт /cabinet
        self.assertEqual(r.cookies.get("liqscope_oauth_next", "").strip('"'), "/cabinet")

    def test_callback_rejects_invalid_or_missing_state_with_400(self):
        self.configure_google()
        self.mock_google()
        # Без cookie state
        r1 = self.client.get(
            "/api/auth/google/callback?code=abc&state=some-state",
            follow_redirects=False,
        )
        self.assertEqual(r1.status_code, 400)
        self.assertEqual(r1.json()["error"], "invalid_state")

        # Несовпадающий state
        r_start = self.client.get("/api/auth/google/login", follow_redirects=False)
        self.assertEqual(r_start.status_code, 302)
        r2 = self.client.get(
            "/api/auth/google/callback?code=abc&state=wrong-state",
            follow_redirects=False,
        )
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json()["error"], "invalid_state")

    def test_callback_creates_new_user_and_sets_session(self):
        self.configure_google()
        self.mock_google()
        r_start = self.client.get(
            "/api/auth/google/login?next=/terminal", follow_redirects=False
        )
        state = parse_qs(urlparse(r_start.headers["location"]).query)["state"][0]

        r_cb = self.client.get(
            f"/api/auth/google/callback?code=auth-code-1&state={state}",
            follow_redirects=False,
        )
        self.assertEqual(r_cb.status_code, 303)
        self.assertEqual(r_cb.headers["location"], "/terminal")
        self.assertTrue(
            r_cb.cookies.get("liqscope_sid")
            or any("liqscope_sid" in c for c in self.client.cookies)
        )

        me = self.client.get("/api/auth/me").json()
        self.assertIsNotNone(me["user"])
        self.assertEqual(me["user"]["email"], "trader@gmail.com")
        self.assertTrue(me["user"]["email_verified"])
        self.assertEqual(me["user"]["google_id"], "google-uid-42")
        self.assertTrue(me["user"]["google_linked"])
        self.assertEqual(me["user"]["display_name"], "Crypto Trader")

        # Повторное использование того же state отклоняется с 400
        self.client.cookies.set("liqscope_oauth_state", state)
        r_replay = self.client.get(
            f"/api/auth/google/callback?code=auth-code-1&state={state}",
            follow_redirects=False,
        )
        self.assertEqual(r_replay.status_code, 400)

    def test_callback_links_existing_email_user_without_duplicate(self):
        self.configure_google()
        existing = self.store.create_email_user(
            "trader@gmail.com", hash_password("Good-Pass-2026"), first_name="OldName"
        )["user"]
        self.mock_google()

        r_start = self.client.get("/api/auth/google/login", follow_redirects=False)
        state = parse_qs(urlparse(r_start.headers["location"]).query)["state"][0]
        r_cb = self.client.get(
            f"/api/auth/google/callback?code=code-link&state={state}",
            follow_redirects=False,
        )
        self.assertEqual(r_cb.status_code, 303)
        self.assertEqual(r_cb.headers["location"], "/cabinet")

        me = self.client.get("/api/auth/me").json()["user"]
        self.assertEqual(me["id"], existing["id"])
        self.assertEqual(me["google_id"], "google-uid-42")
        self.assertTrue(me["email_verified"])
        self.assertEqual(self.store.user_counts()["total"], 1)

    def test_callback_rejects_unverified_google_email(self):
        self.configure_google()
        self.mock_google(
            userinfo_resp={
                "sub": "google-uid-99",
                "email": "unverified@gmail.com",
                "email_verified": False,
                "_status": 200,
            }
        )
        r_start = self.client.get("/api/auth/google/login", follow_redirects=False)
        state = parse_qs(urlparse(r_start.headers["location"]).query)["state"][0]
        r_cb = self.client.get(
            f"/api/auth/google/callback?code=code-unv&state={state}",
            follow_redirects=False,
        )
        self.assertEqual(r_cb.status_code, 400)
        self.assertEqual(r_cb.json()["error"], "google_email_unverified")

    def test_errors_never_log_client_secret_or_tokens(self):
        self.configure_google()
        self.mock_google(
            token_resp={
                "error": "invalid_grant",
                "access_token": "",
                "_status": 400,
            }
        )
        r_start = self.client.get("/api/auth/google/login", follow_redirects=False)
        state = parse_qs(urlparse(r_start.headers["location"]).query)["state"][0]
        with self.assertLogs("liqscope.account", level=logging.WARNING) as cm:
            r_cb = self.client.get(
                f"/api/auth/google/callback?code=secret-code-xyz&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(r_cb.status_code, 400)
        joined = "\n".join(cm.output)
        self.assertNotIn("GOCSPX-TopSecretValueNeverLog", joined)
        self.assertNotIn("secret-code-xyz", joined)
        self.assertNotIn("ya29.mock_access_token_secret", joined)

    def test_unlink_endpoint_requires_other_auth_method(self):
        self.configure_google()
        self.mock_google()
        r_start = self.client.get("/api/auth/google/login", follow_redirects=False)
        state = parse_qs(urlparse(r_start.headers["location"]).query)["state"][0]
        self.client.get(
            f"/api/auth/google/callback?code=c1&state={state}",
            follow_redirects=False,
        )
        # У пользователя только Google -> 400
        r_unlink = self.client.post("/api/auth/google/unlink")
        self.assertEqual(r_unlink.status_code, 400)
        self.assertEqual(r_unlink.json()["error"], "last_auth_method")

        # Добавляем пароль -> отвязка проходит (200)
        me = self.client.get("/api/auth/me").json()["user"]
        self.store.set_user_password(me["id"], hash_password("Good-Pass-2026"))
        r_unlink2 = self.client.post("/api/auth/google/unlink")
        self.assertEqual(r_unlink2.status_code, 200)
        self.assertTrue(r_unlink2.json()["ok"])
        self.assertFalse(r_unlink2.json()["user"]["google_linked"])

    def test_admin_settings_google_keys_masking_and_check(self):
        # Создаём владельца через Google OAuth (boss@liqscope.online)
        self.configure_google()
        self.mock_google(
            userinfo_resp={
                "sub": "google-boss-1",
                "email": "boss@liqscope.online",
                "email_verified": True,
                "name": "Boss",
                "_status": 200,
            }
        )
        r_start = self.client.get("/api/auth/google/login", follow_redirects=False)
        state = parse_qs(urlparse(r_start.headers["location"]).query)["state"][0]
        self.client.get(
            f"/api/auth/google/callback?code=cboss&state={state}",
            follow_redirects=False,
        )
        # Сохранение через короткие алиасы GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
        r_save = self.client.post(
            "/api/admin/settings",
            json={
                "GOOGLE_CLIENT_ID": "999-xyz.apps.googleusercontent.com",
                "GOOGLE_CLIENT_SECRET": "GOCSPX-NewSecret987654",
            },
        )
        self.assertEqual(r_save.status_code, 200)
        self.assertNotIn("GOCSPX-NewSecret987654", r_save.text)

        r_get = self.client.get("/api/admin/settings")
        self.assertEqual(r_get.status_code, 200)
        d = r_get.json()
        self.assertEqual(
            d["google_redirect_uri"],
            "https://liqscope.online/api/auth/google/callback",
        )
        self.assertEqual(
            d["settings"]["LIQSCOPE_GOOGLE_CLIENT_ID"]["value"],
            "999-xyz.apps.googleusercontent.com",
        )
        sec_val = d["settings"]["LIQSCOPE_GOOGLE_CLIENT_SECRET"]["value"]
        self.assertIn("***", sec_val)
        self.assertNotIn("NewSecret", sec_val)

        # Тот же путь, каким ходит админка: канонические имена ключей из схемы
        r_save2 = self.client.post(
            "/api/admin/settings",
            json={
                "LIQSCOPE_GOOGLE_CLIENT_ID": "555-canon.apps.googleusercontent.com",
                "LIQSCOPE_GOOGLE_CLIENT_SECRET": "GOCSPX-CanonSecret246810",
            },
        )
        self.assertEqual(r_save2.status_code, 200)
        self.assertNotIn("GOCSPX-CanonSecret246810", r_save2.text)
        saved = r_save2.json()["saved"]
        self.assertEqual(
            saved["LIQSCOPE_GOOGLE_CLIENT_ID"],
            "555-canon.apps.googleusercontent.com",
        )
        self.assertIn("***", saved["LIQSCOPE_GOOGLE_CLIENT_SECRET"])
        # Ключи действуют сразу: вход через Google включается без перезапуска
        self.assertTrue(self.client.get("/api/auth/providers").json()["google"])
        self.assertEqual(
            self.client.get("/api/admin/settings").json()["settings"][
                "LIQSCOPE_GOOGLE_CLIENT_ID"]["source"],
            "db",
        )

        # Проверка валидного и невалидного формата Client ID
        r_ok = self.client.post("/api/admin/settings/test-google", json={})
        self.assertEqual(r_ok.status_code, 200)
        self.assertTrue(r_ok.json()["ok"])

        r_bad = self.client.post(
            "/api/admin/settings/test-google",
            json={"client_id": "invalid-client-id"},
        )
        self.assertEqual(r_bad.status_code, 400)
        self.assertFalse(r_bad.json()["ok"])

    def test_login_page_keeps_google_as_a_plain_link(self):
        """Кнопка входа — ссылка, а не JS-only кнопка.

        Гость с закэшированным (старым) account.js обработчик не получит —
        вход всё равно должен работать: сервер сам отдаст 503 без ключей и
        редирект на Google с ними.
        """
        page = self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        html = page.text
        m = re.search(r'<a\b[^>]*\bid="google-login-btn"[^>]*>', html)
        self.assertIsNotNone(m, "кнопка Google не ссылка: JS может не загрузиться")
        self.assertIn('href="/api/auth/google/login"', m.group(0))
        self.assertIn('data-i18n-aria="auth.google_btn"', m.group(0))

        r = self.client.get("/api/auth/google/login", follow_redirects=False)
        self.assertEqual(r.status_code, 503)
        self.configure_google()
        r2 = self.client.get("/api/auth/google/login", follow_redirects=False)
        self.assertEqual(r2.status_code, 302)
        self.assertTrue(r2.headers["location"].startswith(GOOGLE_AUTH_HOST))

    def test_login_page_versions_scripts_by_content(self):
        """Разметка и скрипты приходят парой с одним хешем содержимого.

        Иначе после деплоя гость получает новую страницу со старым (год
        immutable) account.js: кнопка есть, но не работает — ровно этот
        случай и проверяем.
        """
        import seo_pages  # noqa: E402 — нужен только этому тесту

        page = self.client.get("/login", headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        self.assertEqual(page.status_code, 200)
        refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', page.text)
        self.assertTrue(refs, "в странице нет ссылок на статику")
        checked = 0
        for ref in refs:
            url, _, query = ref.partition("?")
            if url.endswith((".html", "/")):
                continue
            name = url[len("/static/"):]
            ver = dict(
                part.split("=", 1) for part in query.split("&") if "=" in part
            ).get("v", "")
            if not ver:
                continue  # файла может не быть в сборке — тогда версия пустая
            self.assertEqual(
                ver, seo_pages.asset_version(name),
                f"{name}: в HTML устаревшая версия {ver}",
            )
            checked += 1
        self.assertGreaterEqual(checked, 5, "проверка почти ничего не увидела")
        # словарь страницы тоже версионируется — иначе не будет ключа кнопки
        self.assertIn("/static/i18n.pages.ru.js?v=", page.text)
        self.assertIn("/static/account.js?v=", page.text)
        self.assertIn("/static/account.css?v=", page.text)


if __name__ == "__main__":
    unittest.main()
