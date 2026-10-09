"""Offline encrypted-key storage and rotating budget tests."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from aiohttp import ClientSession, web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alchemy_keys import AlchemyKeyStore, KeyStoreError
from whale_poller import WhalePoller, BudgetExhausted
from whale_screener import WhaleScreener, alchemy_key

SECRET = "test-secret-not-the-published-default"


class KeyStoreTests(unittest.TestCase):
    def test_encrypted_file_no_secret_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.enc"
            store = AlchemyKeyStore(SECRET, path=path)
            a = store.add("  https://eth-mainnet.g.alchemy.com/v2/fake-secret-A/  ")
            b = store.add(" fake-secret-B ")
            self.assertEqual(len(store.public()), 2)
            self.assertNotIn("fake-secret-A", path.read_text())
            self.assertNotIn("fake-secret-B", path.read_text())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(store.keys(), AlchemyKeyStore(SECRET, path=path).keys())
            self.assertNotIn("fake-secret", json.dumps(store.public()))
            with self.assertRaises(KeyStoreError):
                AlchemyKeyStore("another-secret-that-is-long-enough", path=path)
            with self.assertRaises(ValueError):
                store.add("fake-secret-B")
            self.assertTrue(store.remove(a["id"]))
            self.assertFalse(store.remove(a["id"]))
            self.assertEqual(store.keys(), [(b["id"], "fake-secret-B")])

    def test_env_is_fallback_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.enc"
            store = AlchemyKeyStore(SECRET, path=path, env_key="env-secret")
            self.assertEqual(store.keys(), [("env", "env-secret")])
            admin = store.add("admin-secret")
            # Ключ из окружения остаётся в пуле: «дополнительный» ключ,
            # добавленный в админке, не имеет права выключать основной —
            # иначе сбор данных вставал целиком, пока новый ключ не годился.
            self.assertEqual(store.keys(),
                             [(admin["id"], "admin-secret"), ("env", "env-secret")])
            self.assertEqual([row["source"] for row in store.public()],
                             ["admin", "environment"])
            self.assertNotIn("env-secret", path.read_text())   # секрет не на диске
            with self.assertRaises(ValueError):
                store.add("env-secret")     # дубликат того же ключа
            self.assertTrue(store.remove(admin["id"]))
            self.assertEqual(store.keys(), [("env", "env-secret")])


    def test_alchemy_key_accepts_bare_and_trimmed_v2_urls(self):
        self.assertEqual(alchemy_key("  fake-secret  "), "fake-secret")
        self.assertEqual(alchemy_key(
            "  https://eth-mainnet.g.alchemy.com/v2/key-with.dots_123/  "),
            "key-with.dots_123")
        self.assertEqual(alchemy_key(
            "wss://base-mainnet.g.alchemy.com/v2/another-key"), "another-key")
        for value in (
                "https://evil.example/v2/fake-key",
                "https://eth-mainnet.g.alchemy.com/v2/fake-key/extra",
                "https://eth-mainnet.g.alchemy.com/v2/fake-key?secret=x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                alchemy_key(value)


class RotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_hot_add_wakes_idle_poller(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AlchemyKeyStore(SECRET, path=Path(tmp) / "keys.enc")
            calls = asyncio.Event()
            async def handler(request):
                calls.set()
                return web.json_response({"result": "0x10"})
            app = web.Application()
            app.router.add_post("/rpc", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            screen = WhaleScreener("fake", lambda _: None, lambda _: None)
            poller = WhalePoller("", screen, key_store=store,
                                 endpoints={"ETH": f"http://127.0.0.1:{port}/rpc"},
                                 state_file=Path(tmp)/"cursor.json")
            task = asyncio.create_task(poller.run())
            try:
                await asyncio.sleep(.02)
                self.assertFalse(calls.is_set())
                self.assertEqual(poller.status()["phase"], "no_key")
                store.add("fixture-valid-key")
                poller.wakeup.set()
                await asyncio.wait_for(calls.wait(), 2)
                for _ in range(10):
                    if "ETH" in poller.last_success:
                        break
                    await asyncio.sleep(.01)
                self.assertIn("ETH", poller.last_success)
                self.assertEqual(poller.state["cursors"]["ETH"], 16)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await runner.cleanup()

    async def test_production_rpc_uses_chain_host_and_clean_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("unused", lambda _: None, lambda _: None)
            poller = WhalePoller(
                " https://eth-mainnet.g.alchemy.com/v2/clean-key-123/ ", screen,
                endpoints={"BASE": "https://base-mainnet.g.alchemy.com/v2/"},
                state_file=Path(tmp) / "cursor.json")
            calls = []
            class FakeResponse:
                status = 200
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None
                async def json(self):
                    return {"jsonrpc": "2.0", "id": 1, "result": "0x2a"}
            class FakeSession:
                def post(self, url, **kwargs):
                    calls.append((url, kwargs["json"]))
                    return FakeResponse()
            result = await poller._rpc(FakeSession(), "BASE", "eth_blockNumber", [])
            self.assertEqual(result, "0x2a")
            self.assertEqual(calls[0][0],
                "https://base-mainnet.g.alchemy.com/v2/clean-key-123")
            self.assertEqual(calls[0][1]["method"], "eth_blockNumber")
            screen.close()

    async def test_switch_429_and_soft_per_key_budget_global_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.enc"
            keys = AlchemyKeyStore(SECRET, path=path)
            first = keys.add("fake-secret-A")
            second = keys.add("fake-secret-B")
            used = []
            async def handler(request):
                # Only test fixtures, never a real secret in external requests.
                used.append(request.headers.get("X-Test-Key"))
                # Trigger provider 429 on first local key; second succeeds.
                if request.headers.get("X-Test-Key") == "fake-secret-A":
                    return web.Response(status=429)
                return web.json_response({"result": "0x10"})
            app = web.Application()
            app.router.add_post("/rpc", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            s = WhaleScreener("fake", lambda _: None, lambda _: None)
            p = WhalePoller("", s, key_store=keys, state_file=Path(tmp)/"state.json",
                            endpoints={"ETH": f"http://127.0.0.1:{port}/rpc"},
                            monthly_cu=2000)
            # Simulate HTTP authentication per key without echoing it in URLs.
            # A minimal wrapper session tags requests for the local fake server.
            class TaggedSession:
                def __init__(self, real):
                    self.real = real
                    self.calls = 0
                def post(self, url, **kwargs):
                    self.calls += 1
                    key = "fake-secret-A" if self.calls == 1 else "fake-secret-B"
                    kwargs.setdefault("headers", {})["X-Test-Key"] = key
                    return self.real.post(url, **kwargs)
            try:
                async with ClientSession() as session:
                    value = await p._rpc(TaggedSession(session), "ETH", "eth_blockNumber", [])
                self.assertEqual(value, "0x10")
                self.assertEqual(used, ["fake-secret-A", "fake-secret-B"])
                self.assertEqual(p.state["active_key"], second["id"])
                self.assertNotIn(first["id"], p.key_errors)
                network = p.status()["network_status"]["ETH"]
                self.assertEqual(network["status"], "online")
                self.assertEqual(network["warning_status"], "rate_limited")
                self.assertGreater(network["retry_in_sec"], 0)
                self.assertTrue(network["key_active"])
                self.assertIn("HTTP 429", network["error"])
                self.assertNotEqual(p.key_status()[0]["state"], "cooldown")
                self.assertEqual(p.state["cu"], 20)
                # Квота считается на каждый ключ: пока у второго есть запас,
                # исчерпанный первый не останавливает сбор целиком.
                self.assertEqual(p.key_cu_allowance(), p.monthly_cu)
                self.assertEqual(p.cu_ceiling(), p.monthly_cu * 2)
                p.state["key_usage"][first["id"]] = p.monthly_cu   # ключ A исчерпан
                with self.assertRaises(BudgetExhausted):
                    p._reserve("eth_blockNumber", first["id"])
                # пул не встал: запрос уходит на второй ключ, у которого запас есть

                class SecondKeyOnly(TaggedSession):
                    """Следующий запрос пула обязан уйти на второй ключ."""

                    def post(self, url, **kwargs):
                        kwargs.setdefault("headers", {})["X-Test-Key"] = "fake-secret-B"
                        return self.real.post(url, **kwargs)

                async with ClientSession() as session:
                    again = await p._rpc(SecondKeyOnly(session), "ETH",
                                        "eth_blockNumber", [])
                self.assertEqual(again, "0x10")
                self.assertEqual(used[-1], "fake-secret-B")
                self.assertEqual(p.state["active_key"], second["id"])
                p.monthly_cu = 20
                with self.assertRaises(BudgetExhausted):
                    p._reserve("eth_blockNumber", second["id"])
            finally:
                await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
