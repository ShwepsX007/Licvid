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
from whale_poller import WhalePoller, BudgetExhausted
from whale_screener import WhaleScreener, TOKENS, TRANSFER_TOPIC


class PollingTests(unittest.IsolatedAsyncioTestCase):
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
                    self.assertEqual(poller.state["cu"], 10+10+240+120)
                    restored = WhalePoller("fake", screener, state_file=state,
                                           endpoints=poller.endpoints, monthly_cu=380)
                    self.assertEqual(restored.state["cu"], 380)
                    with self.assertRaises(BudgetExhausted):
                        restored._reserve("eth_blockNumber")
                    self.assertEqual(restored.state["cursors"]["BASE"], 5)
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
