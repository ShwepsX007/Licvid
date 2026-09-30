"""Offline Alchemy JSON-RPC budget / cursor / exchange-filter tests.
Run: .venv/bin/python tests/test_whale_poller.py
"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from aiohttp import web, ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whale_poller import (ENDPOINTS, NATIVE_NETWORKS, WhalePoller, BudgetExhausted,
                          PollError, QuotaExhausted, RateLimited, to_hex_block)
from whale_screener import WhaleScreener, TOKENS, TRANSFER_TOPIC


class PollingTests(unittest.IsolatedAsyncioTestCase):
    def test_hyperliquid_is_only_a_native_source(self):
        self.assertIn("HYPERLIQUID", NATIVE_NETWORKS)
        self.assertNotIn("HYPERLIQUID", ENDPOINTS)
        screener = WhaleScreener("fake", lambda _: None, lambda _: None)
        with self.assertRaisesRegex(ValueError, "native API"):
            WhalePoller("fake", screener,
                        endpoints={"HYPERLIQUID": "https://api.hyperliquid.xyz/v2/key"})
        with self.assertRaisesRegex(ValueError, "native API"):
            WhaleScreener("fake", lambda _: None, lambda _: None,
                          endpoints={"HYPERLIQUID": "wss://api.hyperliquid.xyz/ws"})

    def test_block_params_are_normalized_to_hex(self):
        self.assertEqual(to_hex_block(0), "0x0")
        self.assertEqual(to_hex_block(42), "0x2a")
        self.assertEqual(to_hex_block("42"), "0x2a")
        self.assertEqual(to_hex_block("0x2a"), "0x2a")
        self.assertEqual(to_hex_block("latest"), "latest")
        self.assertEqual(to_hex_block("not-a-block"), "latest")
        self.assertEqual(to_hex_block(None), "latest")
        self.assertEqual(set(ENDPOINTS), {"ETH", "BNB", "POLYGON", "ARBITRUM", "BASE"})
        self.assertEqual(set(NATIVE_NETWORKS), {"HYPERLIQUID"})
        self.assertNotIn("HYPERLIQUID", ENDPOINTS)  # never call Alchemy for Hyperliquid

    async def test_cursors_dedup_and_budget_survive_restart(self):
        wallet = "0x" + "1" * 40
        sender = "0x" + "2" * 40
        txhash = "0x" + "a" * 64
        token = next(k for k, v in TOKENS["BASE"].items() if v[0] == "USDC")
        log = {"address": token, "transactionHash": txhash, "logIndex": "0x0",
               "topics": [TRANSFER_TOPIC, "0x" + "0"*24 + sender[2:],
                          "0x" + "0"*24 + wallet[2:]],
               "data": "0x" + format(200000 * 10**6, "064x")}
        requests = []
        head = 4
        async def handler(request):
            req = await request.json()
            requests.append(req)
            method = req["method"]
            if method == "eth_blockNumber":
                result = hex(head)
            elif method == "eth_getLogs":
                outsider = {**log, "transactionHash": "0x" + "b"*64,
                            "topics": [TRANSFER_TOPIC, "0x" + "0"*24 + sender[2:],
                                       "0x" + "0"*24 + ("3"*40)]}
                result = [log, outsider]  # dedup CEX tx; reject unrelated log locally
            elif method == "alchemy_getAssetTransfers":
                result = {"transfers": []}
            else:
                raise AssertionError(method)
            return web.json_response({"jsonrpc": "2.0", "id": 1, "result": result})
        app = web.Application()
        app.router.add_post("/rpc", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        sent = []
        async def broadcast(msg):
            sent.append(msg)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            screener = WhaleScreener("fake", lambda _: None, broadcast)
            screener.wallets_by_chain = {"BASE": {wallet: "Exchange"}}
            screener.wallets = {wallet: "Exchange"}
            screener._registry_wallet_keys = set()
            poller = WhalePoller("fake", screener, state_file=state,
                                 endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"},
                                 monthly_cu=1000)
            self.assertEqual(poller.status()["native_supported"], ["HYPERLIQUID", "SOLANA", "TRON"])
            async with ClientSession() as session:
                try:
                    await poller.poll_chain(session, "BASE")
                    self.assertEqual(poller.state["cursors"]["BASE"], 4)
                    self.assertEqual(sent, [])  # first poll starts from head
                    head = 5
                    await poller.poll_chain(session, "BASE")
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(sent[0]["direction"], "inflow")
                    self.assertEqual(poller.state["cursors"]["BASE"], 5)
                    block_queries = [req for req in requests if req["method"] in
                                     ("eth_getLogs", "alchemy_getAssetTransfers")]
                    self.assertTrue(block_queries)
                    for req in block_queries:
                        block_range = req["params"][0]
                        self.assertEqual(block_range["fromBlock"], "0x5")
                        self.assertEqual(block_range["toBlock"], "0x5")
                    self.assertEqual(poller.state["cu"], 10 + 10 + 240 + 120 + 120)
                    restored = WhalePoller("fake", screener, state_file=state,
                                           endpoints=poller.endpoints, monthly_cu=620)
                    self.assertEqual(restored.state["cu"], 500)
                    restored.monthly_cu = 20
                    with self.assertRaises(BudgetExhausted):
                        restored._reserve("eth_blockNumber")
                    self.assertEqual(restored.state["cursors"]["BASE"], 5)
                finally:
                    await runner.cleanup()

    async def test_rejected_rpc_resets_cursor_to_latest_and_recovers(self):
        for rejected_as in ("http", "jsonrpc"):
            with self.subTest(rejected_as=rejected_as), tempfile.TemporaryDirectory() as tmp:
                wallet = "0x" + "1" * 40
                mode = rejected_as
                head = 5
                requests = []

                async def handler(request):
                    req = await request.json()
                    requests.append(req)
                    method = req["method"]
                    if method == "eth_blockNumber":
                        result = hex(head)
                        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                                  "result": result})
                    if method == "alchemy_getAssetTransfers" and mode == "http":
                        return web.Response(status=400, text="bad block range")
                    if method == "alchemy_getAssetTransfers" and mode == "jsonrpc":
                        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                                  "error": {"code": -32602,
                                                            "message": "invalid params"}})
                    if method == "alchemy_getAssetTransfers":
                        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                                  "result": {"transfers": []}})
                    if method == "eth_getLogs":
                        return web.json_response({"jsonrpc": "2.0", "id": 1, "result": []})
                    raise AssertionError(method)

                app = web.Application()
                app.router.add_post("/rpc", handler)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                port = site._server.sockets[0].getsockname()[1]
                state_file = Path(tmp) / "cursor.json"
                screener = WhaleScreener("fake", lambda _: None, lambda _: None)
                screener.wallets_by_chain = {"BASE": {wallet: "Exchange"}}
                screener.wallets = {wallet: "Exchange"}
                screener._registry_wallet_keys = set()
                poller = WhalePoller("fake", screener, state_file=state_file,
                                     endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"},
                                     monthly_cu=1000)
                poller.state["cursors"]["BASE"] = 3
                poller._save()
                try:
                    async with ClientSession() as session:
                        with self.assertRaises(PollError):
                            await poller.poll_chain(session, "BASE")
                        self.assertEqual(poller.state["cursors"]["BASE"], "latest")
                        saved = json.loads(state_file.read_text(encoding="utf-8"))
                        self.assertEqual(saved["cursors"]["BASE"], "latest")

                        # On the next successful head query, latest is replaced by
                        # the live head; no stale/invalid range is retried.
                        mode = "ok"
                        await poller.poll_chain(session, "BASE")
                        self.assertEqual(poller.state["cursors"]["BASE"], head)
                        head = 6
                        await poller.poll_chain(session, "BASE")
                        self.assertEqual(poller.state["cursors"]["BASE"], head)
                        ranges = [req["params"][0] for req in requests
                                  if req["method"] == "alchemy_getAssetTransfers"]
                        self.assertTrue(all(r["fromBlock"].startswith("0x") and
                                            r["toBlock"].startswith("0x") for r in ranges))
                finally:
                    await runner.cleanup()

    async def test_http_400_logs_full_body_without_an_accidental_api_key(self):
        secret = "test-alchemy-secret-400"
        body = f"invalid params for alchemy_getAssetTransfers; echoed key={secret}; details=bad fromBlock"
        with tempfile.TemporaryDirectory() as tmp:
            async def handler(_request):
                return web.Response(status=400, text=body)
            app = web.Application()
            app.router.add_post("/rpc", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            screen = WhaleScreener(secret, lambda _: None, lambda _: None)
            poller = WhalePoller(secret, screen,
                state_file=Path(tmp) / "state.json",
                endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"})
            try:
                async with ClientSession() as session:
                    with self.assertLogs("whale_poller", level="ERROR") as captured:
                        with self.assertRaisesRegex(PollError, "HTTP 400"):
                            await poller._rpc(session, "BASE", "alchemy_getAssetTransfers", [{}])
                logged = "\n".join(captured.output)
                self.assertIn("details=bad fromBlock", logged)
                self.assertIn("echoed key=[redacted]", logged)
                self.assertNotIn(secret, logged)
            finally:
                screen.close()
                await runner.cleanup()

    def test_provider_failure_classification_is_network_scoped(self):
        classify = WhalePoller._provider_failure_kind
        cases = (
            (429, "too many requests", None, "rate_limited"),
            (200, "JSON-RPC code=429: rate limit", "429", "rate_limited"),
            (403, "forbidden", None, "auth_error"),
            (200, "invalid API key", 401, "auth_error"),
            (402, "quota exceeded", None, "quota_exhausted"),
            (200, "monthly quota exhausted", None, "quota_exhausted"),
            (503, "upstream unavailable", None, "network_error"),
        )
        for http_status, detail, rpc_code, expected in cases:
            with self.subTest(http_status=http_status, rpc_code=rpc_code):
                self.assertEqual(classify(http_status, detail, rpc_code), expected)

    async def test_http_429_uses_backoff_and_keeps_key_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = 0

            async def handler(_request):
                nonlocal calls
                calls += 1
                if calls < 7:
                    return web.Response(status=429, text='{"error":"rate limit exceeded"}')
                return web.json_response({"jsonrpc": "2.0", "id": 1, "result": "0x10"})

            app = web.Application()
            app.router.add_post("/rpc", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            screen = WhaleScreener("test-alchemy-key", lambda _: None, lambda _: None)
            poller = WhalePoller("test-alchemy-key", screen,
                state_file=Path(tmp) / "state.json",
                endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"})
            try:
                with patch("whale_poller.random.uniform", return_value=1.0):
                    async with ClientSession() as session:
                        with self.assertRaises(RateLimited) as first:
                            await poller._rpc(session, "BASE", "eth_blockNumber", [])
                        first_state = poller.status()["network_status"]["BASE"]
                        self.assertEqual(first_state["status"], "rate_limited")
                        self.assertEqual(first_state["http_status"], 429)
                        self.assertIn("rate limit exceeded", first_state["error"])
                        self.assertEqual(first_state["retry_in_sec"], 15)
                        self.assertEqual(first.exception.retry_after, 15)
                        self.assertTrue(first_state["key_active"])
                        self.assertEqual(poller.key_status()[0]["state"], "active")
                        self.assertNotIn("legacy", poller.key_errors)
                        self.assertNotIn("исчерпаны", first_state["error"].lower())

                        poller.chain_cooldown[("BASE", "legacy")] = time.time() - 1
                        with self.assertRaises(RateLimited) as second:
                            await poller._rpc(session, "BASE", "eth_blockNumber", [])
                        self.assertEqual(second.exception.retry_after, 30)
                        self.assertEqual(calls, 2)

                        for expected_delay in (60, 120, 240, 300):
                            poller.chain_cooldown[("BASE", "legacy")] = time.time() - 1
                            with self.assertRaises(RateLimited) as repeated:
                                await poller._rpc(session, "BASE", "eth_blockNumber", [])
                            self.assertEqual(repeated.exception.retry_after, expected_delay)

                        poller.chain_cooldown[("BASE", "legacy")] = time.time() - 1
                        result = await poller._rpc(session, "BASE", "eth_blockNumber", [])
                        self.assertEqual(result, "0x10")
                        self.assertEqual(calls, 7)
                        self.assertEqual(poller.status()["network_status"]["BASE"]["status"], "online")
                        self.assertEqual(poller.key_status()[0]["state"], "active")
                        self.assertNotIn("legacy", poller.key_errors)
            finally:
                screen.close()
                await runner.cleanup()

    async def test_json_rpc_429_is_rate_limited_and_key_stays_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def handler(_request):
                return web.json_response({"jsonrpc": "2.0", "id": 1,
                    "error": {"code": "429", "message": "Too many requests"}})
            app = web.Application()
            app.router.add_post("/rpc", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            screen = WhaleScreener("test-alchemy-key", lambda _: None, lambda _: None)
            poller = WhalePoller("test-alchemy-key", screen,
                state_file=Path(tmp) / "state.json",
                endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"})
            try:
                with patch("whale_poller.random.uniform", return_value=1.0):
                    async with ClientSession() as session:
                        with self.assertRaises(RateLimited):
                            await poller._rpc(session, "BASE", "eth_blockNumber", [])
                status = poller.status()["network_status"]["BASE"]
                self.assertEqual(status["status"], "rate_limited")
                self.assertEqual(status["http_status"], 200)
                self.assertEqual(status["rpc_code"], "429")
                self.assertIn("Too many requests", status["error"])
                self.assertTrue(status["key_active"])
                self.assertEqual(poller.key_status()[0]["state"], "active")
                self.assertNotIn("legacy", poller.key_errors)
            finally:
                screen.close()
                await runner.cleanup()

    async def test_local_key_cu_cap_is_quota_not_dead_key_or_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("test-alchemy-key", lambda _: None, lambda _: None)
            poller = WhalePoller("test-alchemy-key", screen,
                state_file=Path(tmp) / "state.json", monthly_cu=1000,
                endpoints={"ETH": "http://127.0.0.1:1/rpc"})
            poller.state["month"] = time.strftime("%Y-%m", time.gmtime())
            poller.state.setdefault("key_usage", {})["legacy"] = 1000

            class NeverCalledSession:
                def post(self, *_args, **_kwargs):
                    raise AssertionError("CU-capped key must not make an RPC request")

            try:
                with self.assertRaises(QuotaExhausted):
                    await poller._rpc(NeverCalledSession(), "ETH", "eth_blockNumber", [])
                status = poller.status()["network_status"]["ETH"]
                self.assertEqual(status["status"], "quota_exhausted")
                self.assertTrue(status["key_active"])
                self.assertIsNone(status["http_status"])
                self.assertEqual(poller.key_status()[0]["state"], "ready")
            finally:
                screen.close()

    async def test_asset_transfer_history_uses_valid_combined_params(self):
        requests = []
        async def handler(request):
            requests.append(await request.json())
            return web.json_response({"jsonrpc": "2.0", "id": 1,
                                      "result": {"transfers": []}})
        app = web.Application()
        app.router.add_post("/rpc", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("fake", lambda _: None, lambda _: None)
            poller = WhalePoller("test-api-key", screen,
                state_file=Path(tmp) / "state.json",
                endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"})
            try:
                async with ClientSession() as session:
                    await poller._asset_transfer_history(session, "BASE", 16, 42)
                self.assertEqual(len(requests), 1)
                query = requests[0]["params"][0]
                self.assertEqual(requests[0]["method"], "alchemy_getAssetTransfers")
                self.assertEqual(query["fromBlock"], "0x10")
                self.assertEqual(query["toBlock"], "0x2a")
                self.assertEqual(query["category"], ["external", "erc20"])
                self.assertEqual(query["contractAddresses"], list(TOKENS["BASE"]))
                self.assertEqual(int(query["maxCount"], 16), 1000)
            finally:
                screen.close()
                await runner.cleanup()

    async def test_native_transfers_remain_external_only(self):
        requests = []
        async def handler(request):
            requests.append(await request.json())
            return web.json_response({"jsonrpc": "2.0", "id": 1,
                                      "result": {"transfers": []}})
        app = web.Application()
        app.router.add_post("/rpc", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("fake", lambda _: None, lambda _: None)
            screen.wallets_by_chain["BASE"] = {"0x" + "1" * 40: "Exchange"}
            poller = WhalePoller("test-api-key", screen,
                state_file=Path(tmp) / "state.json",
                endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"})
            try:
                async with ClientSession() as session:
                    await poller._native(session, "BASE", 16, 42)
                self.assertEqual(len(requests), 2)
                for request in requests:
                    query = request["params"][0]
                    self.assertEqual(request["method"], "alchemy_getAssetTransfers")
                    self.assertEqual(query["category"], ["external"])
                    self.assertEqual(query["fromBlock"], "0x10")
                    self.assertEqual(query["toBlock"], "0x2a")
            finally:
                screen.close()
                await runner.cleanup()

    async def test_no_advance_on_failed_rpc(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = WhaleScreener("fake", lambda _: None, lambda _: None)
            p = WhalePoller("fake", s, state_file=Path(tmp)/"cursor.json",
                            endpoints={"BASE": "http://127.0.0.1:1/rpc"}, monthly_cu=10)
            p.state["cursors"]["BASE"] = 3
            async with ClientSession() as session:
                with self.assertRaises(Exception):
                    await p.poll_chain(session, "BASE")
            self.assertEqual(p.state["cursors"]["BASE"], 3)


if __name__ == "__main__":
    unittest.main()
