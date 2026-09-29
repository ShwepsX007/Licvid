"""Admin-only API: credentials never returned; hot-add without restart.
Run: LIQSCOPE_SECRET=test-secret-not-the-published-default .venv/bin/python tests/test_screener_admin_keys.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import server
from alchemy_keys import AlchemyKeyStore
from whale_poller import WhalePoller
from whale_screener import WhaleScreener


class AdminKeysTests(unittest.TestCase):
    def test_auth_add_list_remove_and_no_secret_in_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AlchemyKeyStore(os.environ["LIQSCOPE_SECRET"], path=Path(tmp)/"keys.enc")
            screen = WhaleScreener("placeholder", lambda _: None, lambda _: None)
            poller = WhalePoller("", screen, key_store=store,
                                 state_file=Path(tmp)/"poll.json")
            with patch.object(server, "alchemy_key_store", store), \
                 patch.object(server, "whale_poller", poller), \
                 patch.object(server, "whale_screener", screen):
                client = TestClient(server.app)
                key = "not-a-real-key-testfixture"
                self.assertEqual(client.post("/api/admin/screener/keys",
                    json={"key": key}).status_code, 403)
                with patch.object(server, "current_user", lambda request: {"id": 1, "is_admin": True}):
                    res = client.post("/api/admin/screener/keys", json={"key": key})
                    self.assertEqual(res.status_code, 200, res.text)
                    self.assertNotIn(key, res.text)
                    self.assertTrue(poller.wakeup.is_set())
                    identifier = res.json()["key"]["id"]
                    config = client.get("/api/admin/screener/config")
                    self.assertEqual(config.status_code, 200)
                    self.assertNotIn(key, config.text)
                    self.assertEqual(len(config.json()["keys"]), 1)
                    whale_response = client.get("/api/screener/whales?chain=HYPERLIQUID")
                    self.assertEqual(whale_response.json()["enabled"], True)
                    self.assertEqual(whale_response.json()["native_supported"], ["HYPERLIQUID"])
                    self.assertEqual(whale_response.json()["native"]["provider"], "native_api")
                    self.assertNotIn(key, whale_response.text)
                    duplicate = client.post("/api/admin/screener/keys", json={"key": key})
                    self.assertEqual(duplicate.status_code, 422)
                    self.assertNotIn(key, duplicate.text)
                    self.assertTrue(client.delete("/api/admin/screener/keys/"+identifier).json()["ok"])
                    self.assertEqual(client.get("/api/screener/whales").json()["enabled"], False)


if __name__ == "__main__":
    unittest.main()
