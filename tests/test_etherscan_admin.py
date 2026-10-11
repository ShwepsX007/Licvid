"""Админ-роуты Etherscan: /api/admin/etherscan/key* и статус в конфиге скринера.

Поднимаем настоящий server.py на TestClient (как tests/test_data_quality.py):
lifespan не запускается, коллекторы None — роуты обязаны это переживать.
Хранилище ключей подменяем на настоящее, но во временной папке: проверяем
шифрование на диске, маску в ответах и удаление.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="liq-es-admin-")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq.jsonl")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_SECRET"] = "test-secret-not-the-published-default"
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_RATE_LIMIT"] = "0"
os.environ["LIQSCOPE_ADMIN_EMAILS"] = "boss@liqscope.online"

sys.path.insert(0, HERE)

import server  # noqa: E402
from accounts import COOKIE_SID  # noqa: E402
from alchemy_keys import AlchemyKeyStore  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ADMIN_EMAIL = "boss@liqscope.online"
KEY_A = "EtherscanKeyAAAAABBBBBBBBBCCCCCCCCCC1"
KEY_B = "EtherscanKeyBBBBBCCCCCCCCCCDDDDDDDD2"


class FakeCollector:
    """Минимальный коллектор: статус + флаг пробуждения."""

    def __init__(self):
        self.wakeup_set = False

        class _Ev:
            def set(_self):
                self.wakeup_set = True

        self.wakeup = _Ev()

    def status(self):
        return {"available": True, "provider": "etherscan", "mode": "primary",
                "poll_interval_sec": 180, "daily_calls_limit": 95000,
                "calls_today": 12, "keys_configured": 1,
                "keys": [{"id": "abc", "hint": "Ethe••••CCC1", "state": "active",
                          "calls_today": 12, "cooldown_sec": 0}],
                "networks": {"ETH": {"status": "online"}}}


class EtherscanAdminRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        admin = server.account_store.create_email_user(
            ADMIN_EMAIL, password_hash="x")["user"]
        cls.admin_id = int(admin["id"])
        plain = server.account_store.create_email_user(
            "vasya@example.com", password_hash="x")["user"]
        cls.plain_id = int(plain["id"])
        cls.client = TestClient(server.app)
        cls.admin = TestClient(server.app)
        cls.admin.cookies.set(COOKIE_SID,
                              server.account_store.create_session(cls.admin_id))
        cls.plain = TestClient(server.app)
        cls.plain.cookies.set(COOKIE_SID,
                              server.account_store.create_session(cls.plain_id))

    def setUp(self):
        self._saved = (server.etherscan_key_store, server.etherscan_collector,
                       server.etherscan_vault_error)
        self.store_path = Path(tempfile.mkdtemp(prefix="liq-es-keys-")) / "keys.enc"
        server.etherscan_key_store = AlchemyKeyStore(
            "test-secret-not-the-published-default", path=self.store_path)
        server.etherscan_collector = None
        server.etherscan_vault_error = ""
        self.addCleanup(self._restore)

    def _restore(self):
        (server.etherscan_key_store, server.etherscan_collector,
         server.etherscan_vault_error) = self._saved

    # --- доступ -------------------------------------------------------------

    def test_guest_and_user_are_rejected(self):
        self.assertEqual(self.client.get("/api/admin/etherscan/key").status_code, 403)
        self.assertEqual(self.plain.get("/api/admin/etherscan/key").status_code, 403)
        self.assertEqual(
            self.plain.post("/api/admin/etherscan/key", json={"key": KEY_A}).status_code,
            403)

    # --- жизненный цикл ключей ----------------------------------------------

    def test_add_list_delete_key_roundtrip(self):
        # пусто
        got = self.admin.get("/api/admin/etherscan/key")
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["keys"], [])
        self.assertIn("status", body)

        # добавить
        added = self.admin.post("/api/admin/etherscan/key", json={"key": KEY_A})
        self.assertEqual(added.status_code, 200)
        row = added.json()["key"]
        self.assertEqual(row["source"], "admin")
        self.assertNotIn(KEY_A, str(added.json()))          # секрет не утёк
        self.assertIn("••••", row["hint"])

        # на диске зашифровано
        blob = self.store_path.read_text()
        self.assertNotIn(KEY_A, blob)

        # дубликат отклоняется
        dup = self.admin.post("/api/admin/etherscan/key", json={"key": KEY_A})
        self.assertEqual(dup.status_code, 422)

        # второй ключ, список из двух
        self.admin.post("/api/admin/etherscan/key", json={"key": KEY_B})
        listed = self.admin.get("/api/admin/etherscan/key").json()["keys"]
        self.assertEqual(len(listed), 2)

        # удаление
        removed = self.admin.delete("/api/admin/etherscan/key/" + row["id"])
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["ok"])
        self.assertEqual(
            len(self.admin.get("/api/admin/etherscan/key").json()["keys"]), 1)
        # повторное удаление — 404
        self.assertEqual(
            self.admin.delete("/api/admin/etherscan/key/" + row["id"]).status_code, 404)

    def test_add_key_wakes_collector(self):
        server.etherscan_collector = FakeCollector()
        got = self.admin.post("/api/admin/etherscan/key", json={"key": KEY_A})
        self.assertEqual(got.status_code, 200)
        self.assertTrue(server.etherscan_collector.wakeup_set)

    def test_invalid_payload_rejected(self):
        self.assertEqual(
            self.admin.post("/api/admin/etherscan/key", json={}).status_code, 422)
        self.assertEqual(
            self.admin.post("/api/admin/etherscan/key",
                            json={"key": "x" * 600}).status_code, 422)
        self.assertEqual(
            self.admin.post("/api/admin/etherscan/key",
                            json={"key": "bad key with spaces"}).status_code, 422)

    # --- статус -------------------------------------------------------------

    def test_status_in_key_route_and_screener_config(self):
        server.etherscan_collector = FakeCollector()
        body = self.admin.get("/api/admin/etherscan/key").json()
        self.assertEqual(body["status"]["mode"], "primary")
        self.assertEqual(body["status"]["calls_today"], 12)
        # состояние ключа коллектора видно в /api/admin/screener/config
        self.assertEqual(cfg_keys := self.admin.get("/api/admin/screener/config")
                         .json()["etherscan"]["keys"][0]["state"], "active")

        cfg = self.admin.get("/api/admin/screener/config").json()
        self.assertIn("etherscan", cfg)
        self.assertEqual(cfg["etherscan"]["mode"], "primary")
        self.assertEqual(cfg["etherscan"]["networks"]["ETH"]["status"], "online")

    def test_status_survives_missing_collector(self):
        server.etherscan_key_store = None
        server.etherscan_vault_error = ""
        body = self.admin.get("/api/admin/etherscan/key").json()
        self.assertFalse(body["available"])
        self.assertEqual(body["status"]["mode"], "unavailable")
        cfg = self.admin.get("/api/admin/screener/config").json()
        self.assertEqual(cfg["etherscan"]["mode"], "unavailable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
