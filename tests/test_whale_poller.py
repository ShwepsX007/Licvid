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
from whale_poller import (ALCHEMY_MIN_REQUEST_INTERVAL_SEC, DEFAULT_POLL_INTERVAL_SEC,
                          EVM_POLL_STAGGER_OFFSETS_SEC, MAX_BLOCK_RANGE, NATIVE_NETWORKS,
                          POLL_INTERVAL_OPTIONS_SEC, POLL_JITTER_SEC, SOLANA_POLL_STAGGER_SEC,
                          ENDPOINTS, WhalePoller,
                          BudgetExhausted, PollError, QuotaExhausted, RateLimited,
                          bounded_block_range, iter_block_ranges, to_hex_block)
from whale_screener import WhaleScreener, TOKENS, TRANSFER_TOPIC


class PollingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._alchemy_rate_patch = patch("whale_poller.ALCHEMY_MIN_REQUEST_INTERVAL_SEC", 0.0)
        self._alchemy_rate_patch.start()

    def tearDown(self):
        self._alchemy_rate_patch.stop()

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

    def test_block_ranges_are_inclusive_and_capped_at_five_blocks(self):
        self.assertEqual(MAX_BLOCK_RANGE, 5)
        self.assertEqual(bounded_block_range(100, 150), (100, 104))
        self.assertEqual(bounded_block_range(None, 150), (146, 150))
        self.assertEqual(bounded_block_range(0, 3), (0, 3))
        self.assertEqual(list(iter_block_ranges(100, 110)),
                         [(100, 104), (105, 109), (110, 110)])
        self.assertTrue(all(end - start <= 4 for start, end in iter_block_ranges(0, 250)))

    def test_poll_interval_defaults_environment_and_admin_choices(self):
        self.assertEqual(DEFAULT_POLL_INTERVAL_SEC, 60)
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("fake", lambda _: None, lambda _: None)
            try:
                with patch.dict("os.environ", {}, clear=True):
                    default_poller = WhalePoller(
                        "fake", screen, state_file=Path(tmp) / "default.json",
                        endpoints={"BASE": "http://127.0.0.1/rpc"})
                self.assertEqual(default_poller.status()["poll_interval_sec"], 60)
                with patch.dict("os.environ", {"LIQSCOPE_WHALE_POLL_INTERVAL_SEC": "45"}):
                    invalid_poller = WhalePoller(
                        "fake", screen, state_file=Path(tmp) / "invalid.json",
                        endpoints={"BASE": "http://127.0.0.1/rpc"})
                self.assertEqual(invalid_poller.status()["poll_interval_sec"], 60)
                with patch.dict("os.environ", {"LIQSCOPE_WHALE_POLL_INTERVAL_SEC": "120"}):
                    poller = WhalePoller(
                        "fake", screen, state_file=Path(tmp) / "selected.json",
                        endpoints={"BASE": "http://127.0.0.1/rpc"})
                self.assertEqual(poller.status()["poll_interval_sec"], 120)
                for interval_sec in POLL_INTERVAL_OPTIONS_SEC:
                    poller.configure(mode="realtime", poll_interval_sec=interval_sec,
                                     monthly_cu=1_000_000)
                    self.assertEqual(poller.interval, interval_sec)
                    self.assertEqual(poller._solana_poll_interval(), 30)
                    poller.configure(mode="economy", poll_interval_sec=interval_sec,
                                     monthly_cu=1_000_000)
                    self.assertEqual(poller.interval, interval_sec)
                    self.assertEqual(poller._solana_poll_interval(), 60)
                with self.assertRaises(ValueError):
                    poller.configure(mode="realtime", poll_interval_sec=45,
                                     monthly_cu=1_000_000)
            finally:
                screen.close()

    def test_staggered_start_offsets_match_provider_schedule(self):
        self.assertEqual(EVM_POLL_STAGGER_OFFSETS_SEC, {
            "ETH": 0.0, "BNB": 8.0, "POLYGON": 16.0,
            "ARBITRUM": 24.0, "BASE": 32.0,
        })
        self.assertEqual(SOLANA_POLL_STAGGER_SEC, 40.0)
        self.assertEqual(POLL_JITTER_SEC, 2.0)
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("fake", lambda _: None, lambda _: None)
            poller = WhalePoller("fake", screen, state_file=Path(tmp) / "state.json")
            try:
                with patch("whale_poller.random.uniform", return_value=0.0):
                    self.assertEqual(poller._stagger_delay("SOLANA"), 40.0)
                    self.assertEqual(poller._solana_poll_interval(), 30.0)
                    self.assertEqual(poller._solana_jittered_interval(), 30.0)
                    poller.mode = "economy"
                    self.assertEqual(poller._solana_poll_interval(), 60.0)
                    self.assertEqual(poller._solana_jittered_interval(), 60.0)
                    poller.mode = "realtime"
                    self.assertEqual(poller._initial_evm_poll_schedule(100.0), {
                        "ETH": 100.0, "BNB": 108.0, "POLYGON": 116.0,
                        "ARBITRUM": 124.0, "BASE": 132.0,
                    })
            finally:
                screen.close()

    async def test_run_polls_evm_networks_sequentially(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("test-key", lambda _: None, lambda _: None)
            poller = WhalePoller("test-key", screen, state_file=Path(tmp) / "state.json",
                endpoints={"ETH": "http://127.0.0.1/rpc", "BNB": "http://127.0.0.1/rpc"})
            active = 0
            max_active = 0
            seen = []

            class FakeSession:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None

            async def fake_poll_chain(_session, chain):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                seen.append(chain)
                await asyncio.sleep(0)
                active -= 1
                if len(seen) == 2:
                    raise asyncio.CancelledError

            poller.poll_chain = fake_poll_chain
            try:
                with (
                    patch("whale_poller.EVM_POLL_STAGGER_OFFSETS_SEC",
                          {"ETH": 0.0, "BNB": 0.0}),
                    patch("whale_poller.random.uniform", return_value=0.0),
                    patch("whale_poller.aiohttp.ClientSession", return_value=FakeSession()),
                ):
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(poller.run(), timeout=1.0)
                self.assertEqual(seen, ["ETH", "BNB"])
                self.assertEqual(max_active, 1)
            finally:
                screen.close()

    async def test_legacy_whale_screener_uses_the_shared_alchemy_limiter(self):
        class FakeLimiter:
            def __init__(self):
                self.calls = 0

            async def wait_for_slot(self):
                self.calls += 1

        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.replies = [
                    {"id": 1, "result": "logs-sub"},
                    {"id": 2, "result": "native-sub"},
                ]

            async def send_json(self, payload):
                self.sent.append(payload)

            async def receive_json(self):
                return self.replies.pop(0)

        limiter = FakeLimiter()
        screen = WhaleScreener("fake", lambda _: None, lambda _: None)
        try:
            ws = FakeWebSocket()
            with patch("whale_poller._ALCHEMY_REQUEST_LIMITER", limiter):
                subscriptions = await screen._subscribe(ws, "ETH")
                await screen._notification("ETH", {
                    "params": {"subscription": "heads-sub", "result": {"number": "0x2a"}}
                }, {"heads-sub": "heads"}, ws)
            self.assertEqual(subscriptions, {"logs-sub": "logs", "native-sub": "native"})
            self.assertEqual([item["method"] for item in ws.sent], [
                "eth_subscribe", "eth_subscribe", "eth_getBlockByNumber",
            ])
            self.assertEqual(limiter.calls, 3)
        finally:
            screen.close()

    async def test_global_alchemy_limiter_spaces_evm_and_solana_requests(self):
        requests = []

        async def handler(request):
            payload = await request.json()
            requests.append((payload["method"], time.monotonic()))
            result = "0x2a" if payload["method"] == "eth_blockNumber" else "ok"
            return web.json_response({"jsonrpc": "2.0", "id": 1, "result": result})

        app = web.Application()
        app.router.add_post("/rpc", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/rpc"
        with tempfile.TemporaryDirectory() as tmp:
            screen_one = WhaleScreener("fake", lambda _: None, lambda _: None)
            screen_two = WhaleScreener("fake", lambda _: None, lambda _: None)
            poller_one = WhalePoller("test-alchemy-key-one", screen_one,
                state_file=Path(tmp) / "one.json", endpoints={"BASE": url})
            poller_two = WhalePoller("test-alchemy-key-two", screen_two,
                state_file=Path(tmp) / "two.json", endpoints={"BASE": url})
            try:
                with (
                    patch("whale_poller.ALCHEMY_MIN_REQUEST_INTERVAL_SEC", 0.4),
                    patch("whale_poller.SOLANA_HTTP_BASE", url),
                ):
                    async with ClientSession() as session:
                        await asyncio.gather(
                            poller_one._rpc(session, "BASE", "eth_blockNumber", []),
                            poller_two._solana_rpc(session, "getHealth", []),
                        )
                self.assertEqual({method for method, _when in requests},
                                 {"eth_blockNumber", "getHealth"})
                times = sorted(when for _method, when in requests)
                self.assertGreaterEqual(times[1] - times[0], 0.38)
                self.assertEqual(ALCHEMY_MIN_REQUEST_INTERVAL_SEC, 0.4)
            finally:
                screen_one.close()
                screen_two.close()
                await runner.cleanup()

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
                        self.assertEqual(first_state["retry_in_sec"], 60)
                        self.assertEqual(first.exception.retry_after, 60)
                        self.assertTrue(first_state["key_active"])
                        self.assertEqual(poller.key_status()[0]["state"], "active")
                        self.assertNotIn("legacy", poller.key_errors)
                        self.assertNotIn("исчерпаны", first_state["error"].lower())

                        poller.chain_cooldown[("BASE", "legacy")] = time.time() - 1
                        with self.assertRaises(RateLimited) as second:
                            await poller._rpc(session, "BASE", "eth_blockNumber", [])
                        self.assertEqual(second.exception.retry_after, 120)
                        self.assertEqual(calls, 2)

                        for expected_delay in (180, 240, 300, 300):
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

    async def test_rate_limit_cooldown_is_isolated_to_affected_network(self):
        calls = []

        async def handler(request):
            payload = await request.json()
            calls.append(payload["method"])
            return web.json_response({"jsonrpc": "2.0", "id": 1, "result": "0x2a"})

        app = web.Application()
        app.router.add_post("/rpc", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("fake", lambda _: None, lambda _: None)
            poller = WhalePoller("test-key", screen, state_file=Path(tmp) / "state.json",
                endpoints={"ETH": f"http://127.0.0.1:{port}/rpc",
                           "BASE": f"http://127.0.0.1:{port}/rpc"})
            try:
                poller._note_rate_limit("BASE", "legacy", "HTTP 429: rate limit")
                async with ClientSession() as session:
                    self.assertEqual(await poller._rpc(
                        session, "ETH", "eth_blockNumber", []), "0x2a")
                    with self.assertRaises(RateLimited):
                        await poller._rpc(session, "BASE", "eth_blockNumber", [])
                self.assertEqual(calls, ["eth_blockNumber"])
                states = poller.status()["network_status"]
                self.assertEqual(states["BASE"]["status"], "rate_limited")
                self.assertEqual(states["ETH"]["status"], "online")
                self.assertTrue(states["BASE"]["key_active"])
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
                # Ключ с выбранной локальной квотой — не «мёртвый» и сеть он не
                # ставит на паузу, но и трогать его до конца месяца не нужно:
                # state=exhausted и честная причина в админке.
                row = poller.key_status()[0]
                self.assertEqual(row["state"], "exhausted")
                self.assertIn("исчерпана", row["reason"])
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
                self.assertEqual(len(requests), 6)
                expected = [(16, 20), (21, 25), (26, 30),
                            (31, 35), (36, 40), (41, 42)]
                ranges = []
                for request in requests:
                    query = request["params"][0]
                    self.assertEqual(request["method"], "alchemy_getAssetTransfers")
                    ranges.append((int(query["fromBlock"], 16), int(query["toBlock"], 16)))
                    self.assertEqual(query["category"], ["external", "erc20"])
                    self.assertEqual(query["contractAddresses"], list(TOKENS["BASE"]))
                    self.assertEqual(int(query["maxCount"], 16), 1000)
                self.assertEqual(ranges, expected)
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
                self.assertEqual(len(requests), 12)
                ranges = [(int(request["params"][0]["fromBlock"], 16),
                           int(request["params"][0]["toBlock"], 16))
                          for request in requests]
                expected = [(16, 20), (21, 25), (26, 30),
                            (31, 35), (36, 40), (41, 42)]
                self.assertEqual(ranges, [window for window in expected for _ in range(2)])
                for request in requests:
                    query = request["params"][0]
                    self.assertEqual(request["method"], "alchemy_getAssetTransfers")
                    self.assertEqual(query["category"], ["external"])
                    self.assertLessEqual(int(query["toBlock"], 16) -
                                         int(query["fromBlock"], 16), 4)
            finally:
                screen.close()
                await runner.cleanup()

    async def test_poll_chain_catches_up_with_sequential_five_block_chunks(self):
        requests = []
        head = 129

        async def handler(request):
            payload = await request.json()
            requests.append(payload)
            method = payload["method"]
            if method == "eth_blockNumber":
                result = hex(head)
            elif method == "eth_getLogs":
                result = []
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
        with tempfile.TemporaryDirectory() as tmp:
            screen = WhaleScreener("fake", lambda _: None, lambda _: None)
            wallet = "0x" + "1" * 40
            screen.wallets_by_chain["BASE"] = {wallet: "Exchange"}
            poller = WhalePoller("test-api-key", screen,
                state_file=Path(tmp) / "state.json",
                endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"})
            poller.state["cursors"]["BASE"] = 99
            poller.state["history_cursors"]["BASE"] = head
            try:
                sleep_calls = []

                async def record_sleep(delay):
                    sleep_calls.append(delay)

                with patch("whale_poller.asyncio.sleep", new=record_sleep):
                    async with ClientSession() as session:
                        await poller.poll_chain(session, "BASE")
                expected = [(100, 104), (105, 109), (110, 114),
                            (115, 119), (120, 124), (125, 129)]
                log_ranges = [(int(req["params"][0]["fromBlock"], 16),
                               int(req["params"][0]["toBlock"], 16))
                              for req in requests if req["method"] == "eth_getLogs"]
                transfer_ranges = [(int(req["params"][0]["fromBlock"], 16),
                                    int(req["params"][0]["toBlock"], 16))
                                   for req in requests
                                   if req["method"] == "alchemy_getAssetTransfers"]
                self.assertEqual(log_ranges, [chunk for chunk in expected for _ in range(2)])
                self.assertEqual(transfer_ranges, [chunk for chunk in expected for _ in range(2)])
                self.assertEqual(sleep_calls, [0.2] * (len(expected) - 1))
                self.assertEqual(poller.state["cu"], 2_170)
                self.assertEqual(poller.state["cursors"]["BASE"], head)
            finally:
                screen.close()
                await runner.cleanup()

    async def test_catchup_lag_over_thirty_resets_both_cursors_to_latest_five(self):
        requests = []
        head = 1000

        async def handler(request):
            payload = await request.json()
            requests.append(payload)
            if payload["method"] == "eth_blockNumber":
                result = hex(head)
            elif payload["method"] == "eth_getLogs":
                result = []
            elif payload["method"] == "alchemy_getAssetTransfers":
                result = {"transfers": []}
            else:
                raise AssertionError(payload["method"])
            return web.json_response({"jsonrpc": "2.0", "id": 1, "result": result})

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
            poller.state["cursors"]["BASE"] = 900
            poller.state["history_cursors"]["BASE"] = 800
            try:
                with self.assertLogs("whale_poller", level="WARNING") as captured:
                    async with ClientSession() as session:
                        await poller.poll_chain(session, "BASE")
                self.assertEqual(poller.state["cursors"]["BASE"], head)
                self.assertEqual(poller.state["history_cursors"]["BASE"], head)
                warning = "\n".join(captured.output)
                self.assertEqual(warning.count("Отставание > 30 блоков"), 2)
                self.assertIn("Безопасный сброс на latest - 5.", warning)
                range_requests = [req for req in requests if req["method"] in
                                  ("eth_getLogs", "alchemy_getAssetTransfers")]
                self.assertTrue(range_requests)
                for req in range_requests:
                    query = req["params"][0]
                    self.assertEqual(int(query["fromBlock"], 16), head - 4)
                    self.assertEqual(int(query["toBlock"], 16), head)
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
