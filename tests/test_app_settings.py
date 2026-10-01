"""Менеджер системных настроек (Менеджер настроек из админки).

Проверяем ядро динамической конфигурации из ``app_settings``:
* значение читается из БД, иначе из окружения, иначе дефолт;
* удаление переопределения возвращает поведение к окружению;
* секреты маскируются в срезе для админки;
* ``apply_mailer`` пересобирает транспорт только при изменении настроек;
* ``apply_admin_access`` обновляет списки админов без перезапуска;
* маскировка токенов в логах (аудит AUTH-01): без LIQSCOPE_DEBUG полный
  токен в журнал не попадает.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import app_settings  # noqa: E402
from app_settings import MASK, SettingsManager, mask_secret, mask_token  # noqa: E402


class SettingsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "accounts.db")
        self.mgr = SettingsManager(self.db)
        self._env_backup = {}
        for key in ("LIQSCOPE_SMTP_HOST", "LIQSCOPE_SMTP_PORT",
                    "LIQSCOPE_SMTP_PASSWORD", "LIQSCOPE_ADMIN_EMAILS",
                    "LIQSCOPE_ADMIN_IDS", "LIQSCOPE_BOT_TOKEN"):
            self._env_backup[key] = os.environ.pop(key, None)

    def tearDown(self):
        self.mgr.close()
        self.tmp.cleanup()
        for key, val in self._env_backup.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    # ----- чтение: БД → окружение → дефолт ---------------------------------
    def test_env_fallback_when_db_empty(self):
        os.environ["LIQSCOPE_SMTP_HOST"] = "smtp.env.example"
        self.assertEqual(self.mgr.get("LIQSCOPE_SMTP_HOST"), "smtp.env.example")
        self.assertEqual(self.mgr.get("LIQSCOPE_SMTP_PORT", "587"), "587")

    def test_db_overrides_env(self):
        os.environ["LIQSCOPE_SMTP_HOST"] = "smtp.env.example"
        self.mgr.set("LIQSCOPE_SMTP_HOST", "smtp.db.example")
        self.assertEqual(self.mgr.get("LIQSCOPE_SMTP_HOST"), "smtp.db.example")
        # и после перечтения менеджера БД остаётся источником правды
        other = SettingsManager(self.db)
        try:
            self.assertEqual(other.get("LIQSCOPE_SMTP_HOST"), "smtp.db.example")
        finally:
            other.close()

    def test_delete_returns_to_env(self):
        os.environ["LIQSCOPE_SMTP_HOST"] = "smtp.env.example"
        self.mgr.set("LIQSCOPE_SMTP_HOST", "smtp.db.example")
        self.mgr.delete("LIQSCOPE_SMTP_HOST")
        self.assertEqual(self.mgr.get("LIQSCOPE_SMTP_HOST"), "smtp.env.example")

    def test_unknown_key_not_stored(self):
        self.assertFalse(self.mgr.set("NOT_A_MANAGED_KEY", "x"))
        self.assertIsNone(self.mgr.db_value("NOT_A_MANAGED_KEY"))

    # ----- маскировка --------------------------------------------------------
    def test_admin_view_masks_secrets(self):
        self.mgr.set("LIQSCOPE_SMTP_PASSWORD", "super-secret-pass")
        self.mgr.set("LIQSCOPE_SMTP_HOST", "smtp.example")
        view = self.mgr.admin_view()
        self.assertEqual(view["LIQSCOPE_SMTP_PASSWORD"]["value"], MASK)
        self.assertTrue(view["LIQSCOPE_SMTP_PASSWORD"]["secret"])
        self.assertEqual(view["LIQSCOPE_SMTP_PASSWORD"]["source"], "db")
        self.assertEqual(view["LIQSCOPE_SMTP_HOST"]["value"], "smtp.example")
        self.assertFalse(view["LIQSCOPE_SMTP_HOST"]["secret"])

    def test_mask_secret_empty(self):
        self.assertEqual(mask_secret(""), "")
        self.assertEqual(mask_secret("x"), MASK)

    # ----- почта: динамическая пересборка ------------------------------------
    def test_apply_mailer_rebuilds_and_is_idempotent(self):
        from mailer import Mailer, SmtpTransport
        m = Mailer(None, sender="s", public_url="https://liqscope.online")
        self.assertFalse(m.enabled)
        self.mgr.set("LIQSCOPE_SMTP_HOST", "smtp.example")
        self.mgr.set("LIQSCOPE_SMTP_PORT", "465")
        self.mgr.set("LIQSCOPE_SMTP_TLS", "ssl")
        self.mgr.set("LIQSCOPE_SMTP_USER", "no-reply@example")
        self.mgr.set("LIQSCOPE_SMTP_PASSWORD", "pass-1")
        changed = self.mgr.apply_mailer(m, "https://liqscope.online")
        self.assertTrue(changed)
        self.assertTrue(m.enabled)
        self.assertIsInstance(m.transport, SmtpTransport)
        self.assertEqual(m.transport.host, "smtp.example")
        self.assertEqual(m.transport.port, 465)
        # повтор без изменений — транспорт не трогается
        tr = m.transport
        self.assertFalse(self.mgr.apply_mailer(m, "https://liqscope.online"))
        self.assertIs(m.transport, tr)
        # поменяли пароль — пересборка
        self.mgr.set("LIQSCOPE_SMTP_PASSWORD", "pass-2")
        self.assertTrue(self.mgr.apply_mailer(m, "https://liqscope.online"))
        self.assertEqual(m.transport.password, "pass-2")
        # стёрли хост — почта снова выключена
        self.mgr.delete("LIQSCOPE_SMTP_HOST")
        self.assertTrue(self.mgr.apply_mailer(m, "https://liqscope.online"))
        self.assertFalse(m.enabled)

    def test_smtp_config_defaults(self):
        self.mgr.set("LIQSCOPE_SMTP_HOST", "smtp.example")
        cfg = self.mgr.smtp_config()
        self.assertEqual(cfg["kind"], "smtp")
        self.assertEqual(cfg["port"], 587)
        self.assertEqual(cfg["tls"], "starttls")
        self.mgr.set("LIQSCOPE_SMTP_SSL", "1")
        cfg = self.mgr.smtp_config()
        self.assertEqual(cfg["port"], 465)
        self.assertEqual(cfg["tls"], "ssl")

    # ----- доступы ------------------------------------------------------------
    def test_apply_admin_access(self):
        class FakeStore:
            admin_ids = {1}
            admin_emails = {"old@example.com"}

        store = FakeStore()
        os.environ["LIQSCOPE_ADMIN_IDS"] = "1"
        os.environ["LIQSCOPE_ADMIN_EMAILS"] = "old@example.com"
        self.assertFalse(self.mgr.apply_admin_access(store))  # ничего не менялось
        self.mgr.set("LIQSCOPE_ADMIN_IDS", "11, 22; 33")
        self.mgr.set("LIQSCOPE_ADMIN_EMAILS", "new@Example.com")
        self.assertTrue(self.mgr.apply_admin_access(store))
        self.assertEqual(store.admin_ids, {11, 22, 33})
        self.assertEqual(store.admin_emails, {"new@example.com"})


class TokenMaskTest(unittest.TestCase):
    """AUTH-01: bearer-токены в журнале маскируются без LIQSCOPE_DEBUG."""

    def setUp(self):
        self._dbg = os.environ.pop("LIQSCOPE_DEBUG", None)

    def tearDown(self):
        if self._dbg is None:
            os.environ.pop("LIQSCOPE_DEBUG", None)
        else:
            os.environ["LIQSCOPE_DEBUG"] = self._dbg

    def test_mask_token_shape(self):
        self.assertEqual(mask_token(""), "")
        masked = mask_token("abc12345-tail-of-the-token")
        self.assertEqual(masked, "abc12345...hidden")
        self.assertNotIn("tail-of-the-token", masked)
        self.assertEqual(mask_token("short"), "...hidden")

    def test_fallback_link_masked_in_log(self):
        import logging
        import web_account

        token = "verysecrettoken1234567890"
        with self.assertLogs("liqscope.account", level="WARNING") as cap:
            web_account._send_mail_blocking("verify", "u@example.com", token)
        joined = "\n".join(cap.output)
        self.assertIn("Ссылка для ручной выдачи", joined)
        self.assertNotIn(token, joined)
        self.assertIn("...hidden", joined)

    def test_full_token_logged_only_in_debug(self):
        import web_account

        token = "verysecrettoken1234567890"
        os.environ["LIQSCOPE_DEBUG"] = "1"
        try:
            with self.assertLogs("liqscope.account", level="WARNING") as cap:
                web_account._send_mail_blocking("verify", "u@example.com", token)
        finally:
            os.environ.pop("LIQSCOPE_DEBUG", None)
        self.assertIn(token, "\n".join(cap.output))


if __name__ == "__main__":
    unittest.main()
