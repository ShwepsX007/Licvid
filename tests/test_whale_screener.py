"""Offline Alchemy WebSocket protocol and screener regression tests.

Run: .venv/bin/python tests/test_whale_screener.py
No Alchemy credentials or internet connection required.
"""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whale_screener import TRANSFER_TOPIC, TOKENS, WhaleScreener, alchemy_key, load_wallets

A = "0x" + "1" * 40
B = "0x" + "2" * 40
TX = "0x" + "a" * 64


def transfer(chain, symbol, amount, target=A, hash_=TX, index="0x0", removed=False):
    address, (_, decimals) = next((k, v) for k, v in TOKENS[chain].items() if v[0] == symbol)
    topic = lambda addr: "0x" + "0" * 24 + addr[2:]
    return {"address": address, "topics": [TRANSFER_TOPIC, topic(B), topic(target)],
            "data": "0x" + format(amount * 10 ** decimals, "064x"),
            "transactionHash": hash_, "logIndex": index, "removed": removed}


class ParsingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []
        self.s = WhaleScreener("dummy", lambda pair: {"ETH_USDT": 2500, "BNB_USDT": 500,
                                                       "BTC_USDT": 50000}.get(pair),
                               self.sent.append, min_usd=50_000)
        # A known exchange address is represented by its on-disk label too.
        self.s.wallets[A] = "Exchange"

    async def test_transfers_amount_direction_chain_and_dedup(self):
        async def broadcast(value):
            self.sent.append(value)
        self.s.broadcast = broadcast
        await self.s.handle_log("ETH", transfer("ETH", "USDT", 49_999))
        self.assertEqual(self.s.history(0), [])
        await self.s.handle_log("ETH", transfer("ETH", "USDT", 100_000))
        await self.s.handle_log("ETH", transfer("ETH", "USDT", 100_000))
        await self.s.handle_log("ETH", transfer("ETH", "USDT", 100_000, index="0x1", removed=True))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["direction"], "inflow")
        self.assertEqual(self.sent[0]["to_label"], "Exchange")
        await self.s.handle_log("BNB", transfer("BNB", "USDT", 100_000, hash_=TX))
        self.assertEqual(len(self.s.history(100_000, "BNB")), 1)
        self.assertEqual(self.s.history(200_000), [])
        await self.s.handle_log("ETH", transfer("ETH", "USDT", 100_000, target=B, index="0x2"))
        self.assertEqual(self.sent[-1]["direction"], "transfer")
        self.assertEqual(self.sent[0]["type"], "whale_tx")

    async def test_native_and_unpriced_assets(self):
        async def broadcast(value):
            self.sent.append(value)
        self.s.broadcast = broadcast
        tx = {"hash": TX, "from": A, "to": B, "value": hex(40 * 10**18), "input": "0x"}
        await self.s.handle_native("ETH", tx)
        self.assertEqual(self.sent[-1]["usd"], 100_000)
        self.assertEqual(self.sent[-1]["direction"], "outflow")
        await self.s.handle_native("BNB", {**tx, "hash": "0x" + "b" * 64})
        self.assertEqual(len(self.sent), 1)  # $20K, below threshold
        await self.s.handle_native("ETH", {**tx, "hash": "0x" + "c" * 64, "input": "0x1234"})
        self.assertEqual(len(self.sent), 1)  # contract call is not a native payment
        self.s.price_fn = lambda pair: None
        await self.s.handle_native("ETH", {**tx, "hash": "0x" + "d" * 64})
        await self.s.handle_log("ETH", transfer("ETH", "WBTC", 10, hash_="0x" + "f" * 64))
        self.assertEqual(len(self.sent), 1)  # no fabricated USD for ETH/BTC

    async def test_hyperliquid_native_fills_are_whales_and_deduplicated(self):
        async def broadcast(value):
            self.sent.append(value)
        self.s.broadcast = broadcast
        now = 1_780_000_000.0
        row = {"coin": "BTC", "side": "B", "px": "50000", "sz": "1.2",
               "time": int(now * 1000), "hash": TX, "tid": 123,
               "users": [A, B]}
        self.assertFalse(await self.s.handle_hyperliquid_trade(
            {**row, "hash": "0x" + "z" * 64}, now=now))
        self.assertFalse(await self.s.handle_hyperliquid_trade(
            {**row, "sz": "0.5"}, now=now))  # $25K < $50K threshold
        self.assertFalse(await self.s.handle_hyperliquid_trade(
            {**row, "time": int((now - 121) * 1000)}, now=now))  # stale snapshot
        self.assertTrue(await self.s.handle_hyperliquid_trade(row, now=now))
        self.assertFalse(await self.s.handle_hyperliquid_trade(row, now=now))
        event = self.sent[0]
        self.assertEqual(event["type"], "whale_tx")
        self.assertEqual(event["chain"], "HYPERLIQUID")
        self.assertEqual(event["symbol"], "BTC")
        self.assertEqual(event["usd"], 60_000)
        self.assertEqual(event["direction"], "trade")
        self.assertEqual(event["side"], "BUY")
        self.assertEqual(event["from"], A)
        self.assertEqual(event["to"], B)
        self.assertEqual(event["from_label"], "Exchange")
        history = self.s.history(50_000, "HYPERLIQUID")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["hash"], TX)

    async def test_hyperliquid_trade_message_shapes(self):
        row = {"side": "A", "px": "1", "sz": "2", "time": 1}
        flat = {"channel": "trades", "data": [{"coin": "BTC", **row}]}
        grouped = {"channel": "trades", "data": {"coin": "ETH", "trades": [row]}}
        self.assertEqual(self.s._hl_trade_rows(flat)[0]["coin"], "BTC")
        self.assertEqual(self.s._hl_trade_rows(grouped)[0]["coin"], "ETH")
        self.assertEqual(self.s._hl_trade_rows({**flat, "isSnapshot": True}), [])

    async def test_one_key_from_solana_endpoint(self):
        # A Solana URL provides a credential, not a Solana event subscription.
        url = "https://solana-mainnet.g.alchemy.com/v2/dummy-example-key"
        s = WhaleScreener(url, lambda _: None, self.sent.append)
        self.assertEqual(alchemy_key(url), "dummy-example-key")
        self.assertEqual(s.endpoints["ETH"],
                         "wss://eth-mainnet.g.alchemy.com/v2/dummy-example-key")
        self.assertEqual(s.endpoints["BNB"],
                         "wss://bnb-mainnet.g.alchemy.com/v2/dummy-example-key")
        for bad in ("https://other.example/v2/secret", "https://eth-mainnet.g.alchemy.com/v2/",
                    "https://eth-mainnet.g.alchemy.com/v2/secret?token=evil"):
            with self.assertRaises(ValueError) as caught:
                alchemy_key(bad)
            self.assertNotIn("secret", str(caught.exception))

    async def test_separate_bnb_key(self):
        s = WhaleScreener("eth-key", lambda _: None, self.sent.append,
                          bnb_api_key="bnb-key")
        self.assertEqual(s.endpoints["ETH"], "wss://eth-mainnet.g.alchemy.com/v2/eth-key")
        self.assertEqual(s.endpoints["BNB"], "wss://bnb-mainnet.g.alchemy.com/v2/bnb-key")
        common = WhaleScreener("common-key", lambda _: None, self.sent.append)
        self.assertTrue(common.endpoints["BNB"].endswith("/common-key"))

    async def test_bounded_history_and_wallets(self):
        self.assertEqual(len(load_wallets(Path(__file__).resolve().parents[1] / "data/cex_wallets.json")), 8)
        async def broadcast(value):
            pass
        self.s.broadcast = broadcast
        for i in range(120):
            await self.s.handle_log("ETH", transfer("ETH", "USDC", 100_000,
                hash_="0x" + format(i, "064x")))
        self.assertEqual(len(self.s.events), 100)
        self.assertEqual(len(self.s.history(0, limit=50)), 50)
        self.assertEqual(len(self.s.seen_set["ETH"]), 120)


class WebsocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_after_socket_close(self):
        connections = 0
        received = asyncio.Event()

        async def broadcast(value):
            received.set()

        async def handler(request):
            nonlocal connections
            connections += 1
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                req = json.loads(msg.data)
                await ws.send_json({"id": req["id"], "result": req["params"][0]})
                if req["id"] == 2:
                    if connections == 1:
                        await ws.close()
                    else:
                        await ws.send_json({"method": "eth_subscription", "params": {
                            "subscription": "logs", "result": transfer("ETH", "USDT", 100_000)}})
            return ws

        app = web.Application()
        app.router.add_get("/ETH", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        s = WhaleScreener("dummy", lambda _: None, broadcast,
                          endpoints={"ETH": f"http://127.0.0.1:{port}/ETH"})
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(s._run_chain("ETH", session))
            try:
                await asyncio.wait_for(received.wait(), 7)
                self.assertGreaterEqual(connections, 2)
                self.assertEqual(len(s.history()), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await runner.cleanup()

    async def test_hyperliquid_native_api_stream_without_alchemy(self):
        sent = []
        arrived = asyncio.Event()
        requests = []
        subscriptions = []

        async def broadcast(value):
            sent.append(value)
            arrived.set()

        async def info(request):
            body = await request.json()
            requests.append((request.path, body))
            self.assertEqual(body, {"type": "meta"})
            return web.json_response({"universe": [
                {"name": "BTC"}, {"name": "ETH"}, {"name": "kPEPE"},
                {"name": "delisted-coin", "isDelisted": True}]})

        async def ws_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for frame in ws:
                body = json.loads(frame.data)
                if body.get("method") == "ping":
                    await ws.send_json({"channel": "pong"})
                    continue
                if body.get("method") != "subscribe":
                    continue
                subscription = body["subscription"]
                subscriptions.append(subscription)
                await ws.send_json({"channel": "subscriptionResponse", "data": {
                    "method": "subscribe", "subscription": subscription}})
                if subscription["coin"] == "BTC":
                    await ws.send_json({"channel": "trades", "data": [{
                        "coin": "BTC", "side": "A", "px": "65000", "sz": "1",
                        "time": int(time.time() * 1000), "hash": TX, "tid": 456,
                        "users": [A, B]}]})
            return ws

        app = web.Application()
        app.router.add_post("/info", info)
        app.router.add_get("/ws", ws_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        s = WhaleScreener("dummy", lambda _: None, broadcast, min_usd=50_000,
                          endpoints={"ETH": "wss://eth-mainnet.g.alchemy.com/v2/dummy"},
                          hl_ws=base + "/ws", hl_rest=base, hl_sub_gap=0.001)
        task = asyncio.create_task(s.run_hyperliquid())
        try:
            await asyncio.wait_for(arrived.wait(), timeout=5)
            for _ in range(50):
                if s.native_status()["subscriptions_acked"] == 3:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["chain"], "HYPERLIQUID")
            self.assertEqual(sent[0]["side"], "SELL")
            self.assertEqual(s.native_status()["provider"], "native_api")
            self.assertTrue(s.native_status()["connected"])
            self.assertEqual(s.native_status()["subscriptions_acked"], 3)
            self.assertEqual(s.native_status()["market_count"], 3)
            self.assertEqual(set(s.endpoints), {"ETH"})
            self.assertEqual([path for path, _ in requests], ["/info"])
            self.assertEqual({sub["type"] for sub in subscriptions}, {"trades"})
            self.assertEqual({sub["coin"] for sub in subscriptions}, {"BTC", "ETH", "kPEPE"})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.cleanup()

    async def test_two_networks_and_bnb_fallback(self):
        sent = []
        arrived = asyncio.Event()
        active = set()
        subscriptions = {}

        async def broadcast(value):
            sent.append(value)
            if len(sent) >= 3:
                arrived.set()

        async def handler(request):
            chain = request.match_info["chain"]
            active.add(chain)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            try:
                async for msg in ws:
                    req = json.loads(msg.data)
                    if req.get("method") == "eth_subscribe":
                        sub = req["params"][0]
                        subscriptions.setdefault(chain, []).append(req)
                        if sub == "alchemy_minedTransactions" and chain == "BNB":
                            await ws.send_json({"id": req["id"], "error": {"code": -32601}})
                            continue
                        subid = chain + ":" + sub
                        await ws.send_json({"id": req["id"], "result": subid})
                        if sub == ("newHeads" if chain == "BNB" else "alchemy_minedTransactions"):
                            log = transfer(chain, "USDT", 100_000, hash_="0x" +
                                           ("b" if chain == "BNB" else "a") * 64)
                            await ws.send_json({"method": "eth_subscription", "params": {
                                "subscription": chain + ":logs", "result": log}})
                            if chain == "ETH":
                                tx = {"hash": "0x" + "c" * 64, "from": A, "to": B,
                                      "value": hex(40 * 10**18), "input": "0x"}
                                await ws.send_json({"method": "eth_subscription", "params": {
                                    "subscription": subid, "result": {"removed": False, "transaction": tx}}})
                            else:
                                await ws.send_json({"method": "eth_subscription", "params": {
                                    "subscription": subid, "result": {"number": "0x123"}}})
                    elif req.get("method") == "eth_getBlockByNumber":
                        self.assertEqual(req["params"], ["0x123", True])
                        await ws.send_json({"id": req["id"], "result": {"transactions": [
                            {"hash": "0x" + "d" * 64, "from": A, "to": B,
                             "value": hex(200 * 10**18), "input": "0x"}]}})
            finally:
                active.discard(chain)
            return ws

        app = web.Application()
        app.router.add_get("/{chain}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        s = WhaleScreener("dummy", lambda p: {"ETH_USDT": 2500, "BNB_USDT": 500}.get(p),
                          broadcast, endpoints={c: f"http://127.0.0.1:{port}/{c}" for c in ("ETH", "BNB")})
        s.wallets[A] = "Exchange"
        task = asyncio.create_task(s.run())
        try:
            await asyncio.wait_for(arrived.wait(), timeout=4)
            self.assertEqual(len(active), 2)
            self.assertEqual(len(sent), 3)
            self.assertEqual({r["chain"] for r in sent}, {"ETH", "BNB"})
            self.assertIn("newHeads", [r["params"][0] for r in subscriptions["BNB"]])
            self.assertEqual(len(s.history()), 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
