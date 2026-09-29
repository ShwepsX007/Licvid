"""Missing encryption / on-chain imports must not take down the terminal."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OptionalWhaleDependencyTests(unittest.TestCase):
    def _isolated(self, script: str) -> None:
        env = {**os.environ, "LIQSCOPE_SECRET": "test-secret-not-the-published-default",
               "LIQSCOPE_DEMO": "1"}
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=35)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])

    def test_without_cryptography_site_starts_but_vault_is_disabled(self):
        self._isolated('''
import builtins
real_import = builtins.__import__
def without_cryptography(name, *args, **kwargs):
    if name == "cryptography" or name.startswith("cryptography."):
        raise ImportError("simulated missing cryptography")
    return real_import(name, *args, **kwargs)
builtins.__import__ = without_cryptography
from fastapi.testclient import TestClient
import alchemy_keys
import server
import tempfile
from pathlib import Path
from unittest.mock import patch
assert not alchemy_keys.CRYPTO_AVAILABLE
with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp) / "keys.enc"
    vault.write_bytes(b"existing-encrypted-data")
    try:
        alchemy_keys.AlchemyKeyStore("test-secret-not-the-published-default",
                                    path=vault, env_key="fake-environment-key")
    except alchemy_keys.KeyStoreError:
        pass
    else:
        raise AssertionError("missing cryptography must disable key management")
    assert vault.read_bytes() == b"existing-encrypted-data"
assert server.WHALE_POLLER_AVAILABLE
with TestClient(server.app) as client:
    assert client.get("/").status_code == 200
    res = client.get("/api/screener/whales")
    assert res.status_code == 200 and not res.json()["enabled"]
    assert "cryptography" in res.json()["unavailable_reason"]
    assert server.whale_poller is None and server.alchemy_key_store is None
    with patch.object(server, "current_user", lambda request: {"id": 1, "is_admin": True}):
        cfg = client.get("/api/admin/screener/config")
        assert "cryptography" in cfg.json()["vault_error"]
        assert client.post("/api/admin/screener/keys", json={"key": "dummy-test-key"}).status_code == 503
''')

    def test_invalid_whale_configuration_does_not_stop_terminal(self):
        self._isolated('''
from fastapi.testclient import TestClient
from unittest.mock import patch
import server
with patch.object(server, "WhaleScreener", side_effect=FileNotFoundError("missing test wallet file")):
    with TestClient(server.app) as client:
        assert client.get("/terminal").status_code == 200
        result = client.get("/api/screener/whales").json()
        assert not result["enabled"] and "cex_wallets.json" in result["unavailable_reason"]
        assert server.whale_poller is None and server.alchemy_key_store is None
''')

    def test_without_whale_poller_import_site_starts(self):
        self._isolated('''
import builtins
real_import = builtins.__import__
def without_whale_poller(name, *args, **kwargs):
    if name == "whale_poller":
        raise ImportError("simulated missing optional poller")
    return real_import(name, *args, **kwargs)
builtins.__import__ = without_whale_poller
from fastapi.testclient import TestClient
import server
assert not server.WHALE_POLLER_AVAILABLE
with TestClient(server.app) as client:
    assert client.get("/terminal").status_code == 200
    result = client.get("/api/screener/whales").json()
    assert not result["enabled"] and "Ончейн-модуль" in result["unavailable_reason"]
    assert server.whale_poller is None
''')


if __name__ == "__main__":
    unittest.main()
