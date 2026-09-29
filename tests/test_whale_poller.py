"""Offline Alchemy JSON-RPC budget / cursor / exchange-filter tests.
Run: .venv/bin/python tests/test_whale_poller.py
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from aiohttp import web, ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whale_poller import ENDPOINTS, WhalePoller, BudgetExhausted, PollError, to_hex_block
from whale_screener import WhaleScreener, TOKENS, TRANSFER_TOPIC


class PollingTests(unittest.IsolatedAsyncioTestCase):
    def test_block_params_are_normalized_to_hex(self):
        self.assertEqual(to_hex_block(0), "0x0")
        self.assertEqual(to_hex_block(42), "0x2a")
        self.assertEqual(to_hex_block("42"), "0x2a")
        self.assertEqual(to_hex_block("0x2a"), "0x2a")
        self.assertEqual(to_hex_block("latest"), "latest")
        self.assertEqual(to_hex_block("not-a-block"), "latest")
        self.assertEqual(to_hex_block(None), "latest")
        self.assertEqual(set(ENDPOINTS), {"ETH", "BNB", "POLYGON", "ARBITRUM", "BASE"})

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
            screener.wallets = {wallet: "Exchange"}
            poller = WhalePoller("fake", screener, state_file=state,
                                 endpoints={"BASE": f"http://127.0.0.1:{port}/rpc"},
                                 monthly_cu=1000)
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
                    self.assertEqual(poller.state["cu"], 10+10+240+120)
                    restored = WhalePoller("fake", screener, state_file=state,
                                           endpoints=poller.endpoints, monthly_cu=380)
                    self.assertEqual(restored.state["cu"], 380)
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
                screener.wallets = {wallet: "Exchange"}
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
