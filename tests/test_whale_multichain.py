"""Offline coverage for multichain whale ingestion and wallet registry CRUD."""
import asyncio
import json
import tempfile
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import ClientSession, web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cex_wallets_updater import (
    CEXWalletRegistry, EVM_CHAINS, SourceFetchError, WalletSourceError, _read_json,
    fetch_defillama_cex_wallets, normalize_chain, parse_defillama_cex_config,
    valid_address,
)
from tron_address import tron_to_base58, tron_to_hex
from whale_poller import MINED_TRANSACTION_CHAINS, SPL_TOKEN_PROGRAM, WhalePoller, RateLimited
from whale_screener import SOLANA_TOKENS, TRON_TOKENS, WhaleScreener


class CEXWalletRegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_static_defillama_config_parses_supported_chains_only(self):
        source = '''
        const configs = {
          'binance': {
            ethereum: { owners: ['0x1111111111111111111111111111111111111111'] },
            bsc: { owners: ['0x2222222222222222222222222222222222222222'] },
            polygon: { owners: ['0x3333333333333333333333333333333333333333'] },
            arbitrum: { owners: ['0x4444444444444444444444444444444444444444'] },
            base: { owners: ['0x5555555555555555555555555555555555555555'] },
            solana: { owners: ['Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'] },
            tron: { owners: ['TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'] },
            bitcoin: 'binance-btc-owner'
          },
          unverified_exchange: {
            ethereum: { owners: ['0x9999999999999999999999999999999999999999'] }
          }
        };
        '''
        metadata = {"cexs": [{"slug": "binance", "name": "Binance"}]}
        rows = parse_defillama_cex_config(source, metadata)
        self.assertEqual({row["chain"] for row in rows},
                         {"ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA", "TRON"})
        self.assertTrue(all(row["name"] == "Binance" for row in rows))
        self.assertNotIn("0x9999999999999999999999999999999999999999",
                         {row["address"] for row in rows})
        self.assertEqual(normalize_chain("binanceSmartChain"), "BNB")
        self.assertEqual(normalize_chain("arbitrum-one"), "ARBITRUM")
        self.assertTrue(valid_address("SOLANA", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"))
        self.assertTrue(valid_address("TRON", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"))
        self.assertFalse(valid_address("TRON", "T" + "1" * 33))

    def test_legacy_evm_scope_is_preserved_without_non_evm_labels(self):
        # These are format-only test candidates: Base58/32-byte validity does
        # not prove exchange ownership, so none is asserted as a CEX fact.
        valid = [
            "5tzFkiKsc22KEChR37aTBD323ApAo28nJZ34352fgd5e",
            "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
            "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm",
            "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE",
            "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2",
            "42brAgAVNzMBP7aaktPvAmBSPEkehnFQejiZc53EpJFd",
        ]
        for address in valid:
            self.assertTrue(valid_address("SOLANA", address), address)
        invalid = [
            "2OJ13z19qaA8f8T3u1L5qT3xJ7Y3P51S1v5",  # forbidden base58 O/0
            "H8sMJSC38A243qT8up9Bv21L5tX2M3Y3P51S1v5",  # short decoded key
            "2AQdpLPUdVe12Bk18KX1MS2bY7peE4G23M13T41L5v5",
            "AC57B932p4A8f3T8up9Bv21L5tX2M3Y3P51S1v5",
            "6f9U9L523M13T41L5v53T8up9Bv21L5tX2M3Y3P51S1v5",
        ]
        for address in invalid:
            self.assertFalse(valid_address("SOLANA", address), address)

        registry = CEXWalletRegistry(Path(__file__).resolve().parents[1] /
                                     "data" / "cex_wallets.json")
        records = registry.records()
        self.assertNotIn("5tzFkiKsc22KEChR37aTBD323ApAo28nJZ34352fgd5e",
                         {row["address"] for row in records})
        self.assertEqual(sum(row["chain"] == "SOLANA" for row in records), 0)
        self.assertEqual(sum(row["chain"] == "TRON" for row in records), 0)
        self.assertEqual(sum(row["chain"] == "ETH" for row in records), 8)
        self.assertTrue(all(sum(row["chain"] == chain for row in records) == 8
                            for chain in EVM_CHAINS),
                        "legacy EVM labels retain their existing EVM-only scope")

    async def test_manual_addresses_survive_refresh_update_delete_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cex_wallets.json"
            registry = CEXWalletRegistry(path)
            eth = registry.add_manual("ETH", "0x" + "1" * 40, "Manual exchange")
            tron = registry.add_manual("TRON", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "Manual TRON")
            auto = {"chain": "BASE", "address": "0x" + "2" * 40,
                    "name": "Auto CEX", "source": "defillama"}
            with patch("cex_wallets_updater.fetch_defillama_cex_wallets",
                       return_value=[auto]):
                result = await registry.refresh()
            self.assertTrue(result["ok"])
            self.assertTrue(path.with_suffix(".json.bak").exists())
            rows = registry.records()
            self.assertEqual({row["source"] for row in rows}, {"manual", "defillama"})
            self.assertTrue(any(row["id"] == eth["id"] for row in rows))
            self.assertTrue(any(row["id"] == tron["id"] for row in rows))
            changed = registry.update_manual(eth["id"], "ETH", "0x" + "3" * 40, "Renamed")
            self.assertEqual(changed["name"], "Renamed")
            self.assertNotEqual(changed["id"], eth["id"])
            self.assertTrue(registry.remove_manual(tron["id"]))
            restored = CEXWalletRegistry(path)
            self.assertEqual([(row["chain"], row["name"]) for row in restored.records()],
                             [("BASE", "Auto CEX"), ("ETH", "Renamed")])

    async def test_http_404_and_500_failures_preserve_file_and_manual_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cex_wallets.json"
            registry = CEXWalletRegistry(path)
            manual = registry.add_manual("ETH", "0x" + "1" * 40, "Manual survives")
            automatic = {"chain": "BASE", "address": "0x" + "2" * 40,
                         "name": "Known auto", "source": "defillama"}
            with patch("cex_wallets_updater.fetch_defillama_cex_wallets",
                       return_value=[automatic]):
                await registry.refresh()
            original = path.read_bytes()
            with patch("cex_wallets_updater.fetch_defillama_cex_wallets", return_value=[]):
                with self.assertRaises(WalletSourceError):
                    await registry.refresh()
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(any(row["id"] == manual["id"] and row["source"] == "manual"
                                for row in registry.records()))
            for status in (404, 500):
                with self.subTest(status=status):
                    error = SourceFetchError(
                        "https://api.llama.fi/cexs", "HTTP error", status=status,
                        body=f"provider failure {status}; diagnostic body")
                    with patch("cex_wallets_updater.fetch_defillama_cex_wallets",
                               side_effect=error):
                        with self.assertRaises(SourceFetchError):
                            await registry.refresh()
                    self.assertEqual(path.read_bytes(), original)
                    self.assertTrue(any(row["id"] == manual["id"] and
                                        row["source"] == "manual" for row in registry.records()))
                    self.assertTrue(any(row["name"] == "Known auto" for row in registry.records()))
                    self.assertIn("https://api.llama.fi/cexs", registry.last_error)
                    self.assertIn(f"HTTP {status}", registry.last_error)
                    self.assertIn(f"provider failure {status}", registry.last_error)

    async def test_defillama_metadata_falls_back_to_protocols_and_adapter_config(self):
        owner = "0x" + "1" * 40
        seen = []

        async def cex_missing(request):
            seen.append((request.path, request.headers.get("User-Agent", "")))
            return web.Response(status=404, text="CEX endpoint unavailable")

        async def protocols(request):
            seen.append((request.path, request.headers.get("User-Agent", "")))
            return web.json_response([
                {"slug": "binance", "name": "Binance", "category": "CEX"},
                {"slug": "example-defi", "name": "Not an exchange", "category": "Dexes"},
            ])

        async def adapter(request):
            seen.append((request.path, request.headers.get("User-Agent", "")))
            config = "const configs = { binance: { ethereum: { owners: ['" + owner + "'] } } };"
            return web.Response(text=config)

        app = web.Application()
        app.router.add_get("/cexs", cex_missing)
        app.router.add_get("/protocols", protocols)
        app.router.add_get("/adapter.js", adapter)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        try:
            with patch("cex_wallets_updater.DEFILLAMA_CEX_API", base + "/cexs"), \
                 patch("cex_wallets_updater.DEFILLAMA_PROTOCOLS_API", base + "/protocols"), \
                 patch("cex_wallets_updater.DEFILLAMA_CEX_CONFIG", base + "/adapter.js"):
                async with ClientSession() as session:
                    rows = await fetch_defillama_cex_wallets(session)
            self.assertEqual([(row["chain"], row["address"], row["name"]) for row in rows],
                             [("ETH", owner, "Binance")])
            self.assertEqual([item[0] for item in seen], ["/cexs", "/protocols", "/adapter.js"])
            self.assertTrue(all("LiqScope-CEX-Wallets/" in item[1] for item in seen))
        finally:
            await runner.cleanup()

    async def test_source_errors_redact_secrets_in_url_and_body(self):
        secret = "provider-key-should-not-leak"
        error = SourceFetchError(
            f"https://pro-api.llama.fi/{secret}/cexs?api_key={secret}", "HTTP error",
            status=403, body=f"api_key={secret}; response denied")
        self.assertNotIn(secret, str(error))
        self.assertIn("/[redacted]/cexs", str(error))
        self.assertIn("HTTP 403", str(error))
        self.assertIn("[redacted]", str(error))

    async def test_source_errors_report_url_status_body_and_decode_reason(self):
        requests = {"missing": 0, "server": 0, "invalid": 0}

        async def missing(_request):
            requests["missing"] += 1
            return web.Response(status=404, text="endpoint not found")

        async def server_error(_request):
            requests["server"] += 1
            return web.Response(status=500, text="upstream temporarily unavailable")

        async def invalid_json(_request):
            requests["invalid"] += 1
            return web.Response(status=200, text="<html>not json</html>")

        app = web.Application()
        app.router.add_get("/missing", missing)
        app.router.add_get("/server-error", server_error)
        app.router.add_get("/invalid-json", invalid_json)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as session:
                for path, expected in (("/missing", ("HTTP 404", "endpoint not found")),
                                       ("/server-error", ("HTTP 500", "upstream temporarily unavailable")),
                                       ("/invalid-json", ("JSON decode error", "not json"))):
                    url = f"http://127.0.0.1:{port}{path}"
                    with self.assertRaises(SourceFetchError) as caught:
                        await _read_json(session, url)
                    self.assertIn(url, str(caught.exception))
                    for text in expected:
                        self.assertIn(text, str(caught.exception))
            self.assertEqual(requests["missing"], 1)
            self.assertEqual(requests["server"], 3)
            self.assertEqual(requests["invalid"], 1)
        finally:
            await runner.cleanup()


class MultichainTransferTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def broadcast(_message):
        return None

    def make_screener(self, tmp):
        screen = WhaleScreener(
            "unused", lambda pair: {"SOL_USDT": 200.0, "ETH_USDT": 2_000.0,
                                    "TRX_USDT": 1.0}.get(pair),
            self.broadcast, min_usd=0,
            history_path=Path(tmp) / "whales.sqlite3")
        return screen

    async def test_tron_base58check_trx_and_trc20_decimal_amounts(self):
        owner = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        recipient = tron_to_base58("41" + "1" * 40)
        self.assertEqual(tron_to_base58(tron_to_hex(owner)), owner)
        self.assertEqual(tron_to_hex(owner), "a614f803b6fd780986a42c78ec9c7f77e6ded13c")
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            screen.wallets_by_chain["TRON"] = {owner: "Exchange"}
            poller = WhalePoller("unused", screen, state_file=Path(tmp) / "poller.json")
            block = {"block_header": {"raw_data": {"number": 123,
                      "timestamp": int(time.time() * 1000)}},
                     "transactions": [{"txID": "a" * 64,
                       "raw_data": {"contract": [{"type": "TransferContract",
                         "parameter": {"value": {"owner_address": tron_to_hex(owner),
                           "to_address": tron_to_hex(recipient), "amount": 3_250_000}}}]}}]}
            await poller._process_tron_block(block)
            self.assertEqual(screen.events[-1]["chain"], "TRON")
            self.assertEqual(screen.events[-1]["symbol"], "TRX")
            self.assertAlmostEqual(screen.events[-1]["amount"], 3.25)
            self.assertAlmostEqual(screen.events[-1]["usd"], 3.25)
            self.assertEqual(screen.events[-1]["direction"], "outflow")

            contract, (symbol, decimals) = next(iter(TRON_TOKENS.items()))
            request = {}
            async def fake_events(_session, url, params=None):
                request.update(url=url, params=params or {})
                return {"data": [{"event_index": 7, "block_number": 124,
                    "block_timestamp": int(time.time() * 1000),
                    "transaction_id": "b" * 64,
                    "result": {"from": recipient, "to": owner, "value": "25000001"}}],
                    "meta": {}}
            poller._trongrid_get = fake_events
            screen.wallets_by_chain["TRON"] = {}  # event discovery is not wallet-gated
            await poller._poll_tron_contract(None, contract, symbol, decimals,
                                             int(time.time() * 1000))
            self.assertEqual(request["url"],
                f"https://api.trongrid.io/v1/contracts/{contract}/events")
            self.assertEqual(request["params"]["event_name"], "Transfer")
            self.assertEqual(request["params"]["limit"], 50)
            self.assertEqual(request["params"]["only_confirmed"], "true")
            event = screen.events[-1]
            self.assertEqual(event["symbol"], "USDT")
            self.assertEqual(event["log_index"], "trc20:7")
            self.assertAlmostEqual(event["amount"], 25.000001)
            self.assertAlmostEqual(event["usd"], 25.0, places=2)
            self.assertEqual(event["direction"], "transfer")
            screen.close()

    async def test_trongrid_api_key_header_is_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            poller = WhalePoller("unused", screen,
                                 state_file=Path(tmp) / "poller.json")
            requests = []

            class FakeResponse:
                status = 200
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None
                async def json(self):
                    return {"data": []}

            class FakeSession:
                def get(self, url, *, params, headers, timeout):
                    requests.append((url, params, headers))
                    return FakeResponse()

            poller._trongrid_key = lambda: "test-tron-key"
            await poller._trongrid_get(FakeSession(), "https://api.trongrid.io/events")
            poller._trongrid_key = lambda: ""
            await poller._trongrid_get(FakeSession(), "https://api.trongrid.io/events")
            self.assertEqual(requests[0][2], {"TRON-PRO-API-KEY": "test-tron-key"})
            self.assertEqual(requests[1][2], {})
            screen.close()

    async def test_solana_owner_polling_uses_standard_json_rpc_methods(self):
        owner = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        external_owner = "3" * 32
        source_account, destination_account = "1" * 32, "2" * 32
        signature = "5" * 64
        usdc = next(mint for mint, (symbol, _) in SOLANA_TOKENS.items() if symbol == "USDC")
        raw_amount = 150_000 * 10**6
        instruction_hint = 200_000 * 10**6
        token_account = destination_account

        def balance(index, wallet, amount):
            return {"accountIndex": index, "owner": wallet, "mint": usdc,
                    "uiTokenAmount": {"amount": str(amount), "decimals": 6}}

        tx = {"blockTime": int(time.time()),
              "transaction": {"signatures": [signature], "message": {
                  "accountKeys": [source_account, destination_account],
                  "instructions": [{"program": "spl-token", "programId": SPL_TOKEN_PROGRAM,
                      "parsed": {"type": "transferChecked", "info": {
                          "source": source_account, "destination": destination_account,
                          "mint": usdc, "tokenAmount": {"amount": str(instruction_hint),
                                                               "decimals": 6}}}}]}},
              "meta": {"preTokenBalances": [balance(0, external_owner, raw_amount),
                                             balance(1, owner, 0)],
                       "postTokenBalances": [balance(0, external_owner, 0),
                                              balance(1, owner, raw_amount)]}}
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            screen.wallets_by_chain["SOLANA"] = {owner: "Binance"}
            poller = WhalePoller("test-alchemy-key-123", screen,
                                 state_file=Path(tmp) / "poller.json")

            class FakeResponse:
                status = 200
                def __init__(self, result):
                    self.result = result
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None
                async def json(self):
                    return {"jsonrpc": "2.0", "id": 1, "result": self.result}

            class FakeSession:
                def __init__(self):
                    self.requests = []
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None
                def post(self, url, *, json, timeout):
                    self.requests.append((url, json, timeout))
                    method = json["method"]
                    params = json["params"]
                    if method == "getHealth":
                        result = "ok"
                    elif method == "getTokenAccountsByOwner":
                        mint = params[1]["mint"]
                        result = {"value": ([{"pubkey": token_account,
                            "account": {"data": {"parsed": {"info": {"mint": usdc}}}}}]
                            if mint == usdc else [])}
                    elif method == "getSignaturesForAddress":
                        result = ([{"signature": signature, "err": None,
                                    "blockTime": tx["blockTime"]}]
                                   if params[0] in {owner, token_account} else [])
                    elif method == "getTransaction":
                        result = tx
                    else:
                        raise AssertionError(f"unexpected Solana RPC method: {method}")
                    return FakeResponse(result)

            fake_session = FakeSession()
            with patch("whale_poller.aiohttp.ClientSession", return_value=fake_session):
                task = asyncio.create_task(poller.run_solana())
                try:
                    for _ in range(200):
                        if screen.events and poller.solana_status["connected"]:
                            break
                        await asyncio.sleep(.01)
                    self.assertTrue(poller.solana_status["connected"])
                    self.assertEqual(poller.solana_status["state"], "online")
                    self.assertEqual(poller.solana_status["mode"], "cex_poll")
                    self.assertEqual(poller.solana_status["token_accounts"], 1)
                    requests = [payload for _url, payload, _timeout in fake_session.requests]
                    methods = [payload["method"] for payload in requests]
                    self.assertEqual(methods.count("getHealth"), 1)
                    self.assertEqual(methods.count("getTokenAccountsByOwner"), 2)
                    self.assertIn("getSignaturesForAddress", methods)
                    self.assertEqual(methods.count("getTransaction"), 1)
                    self.assertEqual(poller.solana_status["request_budget"], 80)
                    self.assertEqual(poller.solana_status["requests_last_cycle"], 3)
                    self.assertLessEqual(poller.solana_status["requests_last_cycle"],
                                         poller.solana_status["request_budget"])
                    self.assertEqual(poller._solana_poll_interval(), 30)
                    self.assertIn(signature, poller._solana_processed_signatures)
                    self.assertEqual(set(methods), {"getHealth", "getTokenAccountsByOwner",
                                                    "getSignaturesForAddress", "getTransaction"})
                    token_queries = [payload["params"] for payload in requests
                                     if payload["method"] == "getTokenAccountsByOwner"]
                    self.assertEqual({params[1]["mint"] for params in token_queries},
                                     set(SOLANA_TOKENS))
                    self.assertTrue(all(params[2]["encoding"] == "jsonParsed"
                                        for params in token_queries))
                    signature_queries = [payload["params"] for payload in requests
                                         if payload["method"] == "getSignaturesForAddress"]
                    self.assertEqual({params[0] for params in signature_queries},
                                     {owner, token_account})
                    self.assertTrue(all(params[1]["limit"] == 10
                                        for params in signature_queries))
                    tx_query = next(payload["params"] for payload in requests
                                    if payload["method"] == "getTransaction")
                    self.assertEqual(tx_query[0], signature)
                    self.assertEqual(tx_query[1]["encoding"], "jsonParsed")
                    self.assertEqual(tx_query[1]["maxSupportedTransactionVersion"], 0)
                    event = screen.events[-1]
                    self.assertEqual(event["symbol"], "USDC")
                    self.assertEqual(event["amount"], 150_000.0)
                    self.assertEqual(event["usd"], 150_000.0)
                    self.assertEqual(event["from"], external_owner)
                    self.assertEqual(event["to"], owner)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            screen.close()

    async def test_solana_balance_parser_processes_large_spl_transfer(self):
        usdc = next(mint for mint, (symbol, _) in SOLANA_TOKENS.items() if symbol == "USDC")
        source_owner, destination_owner = "3" * 32, "4" * 32
        source_account, destination_account = "1" * 32, "2" * 32
        signature = "5" * 64
        raw_amount = 100_000 * 10**6

        def balance(index, owner, amount):
            return {"accountIndex": index, "owner": owner, "mint": usdc,
                    "uiTokenAmount": {"amount": str(amount), "decimals": 6}}

        tx = {"blockTime": int(time.time()),
              "transaction": {"signatures": [signature], "message": {
                  "accountKeys": ["payer", source_account, destination_account],
                  "instructions": [{"program": "spl-token", "programId": SPL_TOKEN_PROGRAM,
                      "parsed": {"type": "transferChecked", "info": {
                          "source": source_account, "destination": destination_account,
                          "mint": usdc, "tokenAmount": {"amount": str(150_000 * 10**6),
                                                               "decimals": 6}}}}]}},
              "meta": {"preTokenBalances": [balance(1, source_owner, raw_amount),
                                             balance(2, destination_owner, 0)],
                       "postTokenBalances": [balance(1, source_owner, 0),
                                              balance(2, destination_owner, raw_amount)]}}
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            screen.wallets_by_chain["SOLANA"] = {}
            poller = WhalePoller("test-alchemy-key-123", screen,
                                 state_file=Path(tmp) / "poller.json")
            count = await poller._solana_process_network_token_tx(tx)
            self.assertEqual(count, 1)
            event = screen.events[-1]
            self.assertEqual(event["symbol"], "USDC")
            self.assertEqual(event["amount"], 100_000.0)
            self.assertEqual(event["usd"], 100_000.0)
            self.assertEqual(event["from"], source_owner)
            self.assertEqual(event["to"], destination_owner)
            screen.close()

    async def test_solana_empty_registry_waits_after_confirmed_get_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            screen.wallets_by_chain["SOLANA"] = {}
            poller = WhalePoller("test-alchemy-key-123", screen,
                                 state_file=Path(tmp) / "poller.json")
            calls = []

            class FakeResponse:
                status = 200
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None
                async def json(self):
                    return {"jsonrpc": "2.0", "id": 1, "result": "ok"}

            class FakeSession:
                def post(self, url, *, json, timeout):
                    calls.append((url, json, timeout))
                    return FakeResponse()

            await poller._solana_wait_for_filters(FakeSession())
            self.assertEqual(poller.solana_status["state"], "online")
            self.assertEqual(poller.solana_status["mode"], "waiting_for_filters")
            self.assertTrue(poller.solana_status["connected"])
            self.assertTrue(poller.solana_status["waiting_for_filters"])
            self.assertEqual(poller.solana_status["processing_state"], "waiting_for_filters")
            self.assertEqual(poller.solana_status["active_wallets"], 0)
            self.assertEqual(calls[0][1]["method"], "getHealth")
            self.assertEqual(poller.solana_status["error"], "")
            screen.close()

    async def test_solana_retry_delay_never_exceeds_mode_poll_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            poller = WhalePoller("test-alchemy-key-123", screen,
                                 state_file=Path(tmp) / "poller.json")
            self.assertEqual(poller._solana_retry_delay(15), 30)
            self.assertEqual(poller._solana_poll_interval(), 30)
            poller.mode = "economy"
            self.assertEqual(poller._solana_retry_delay(15), 60)
            self.assertEqual(poller._solana_retry_delay(90), 90)
            self.assertEqual(poller._solana_retry_delay(500), 300)
            screen.close()

    async def test_solana_429_is_rate_limited_not_a_key_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            poller = WhalePoller("test-alchemy-key-123", screen,
                                 state_file=Path(tmp) / "poller.json")
            requests = []

            class FakeResponse:
                def __init__(self, status, result=None, body=""):
                    self.status, self.result, self.body = status, result, body
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None
                async def text(self, **_kwargs):
                    return self.body
                async def json(self):
                    return {"jsonrpc": "2.0", "id": 1, "result": self.result}

            class FakeSession:
                def post(self, url, *, json, timeout):
                    requests.append((url, json, timeout))
                    if len(requests) == 1:
                        return FakeResponse(429, body="Too many requests; retry later")
                    return FakeResponse(200, result="ok")

            with patch("whale_poller.random.uniform", return_value=1.0):
                with self.assertRaises(RateLimited) as limited:
                    await poller._solana_rpc(FakeSession(), "getHealth", [])
                self.assertEqual(limited.exception.retry_after, 15)
                status = poller.status()
                self.assertEqual(status["solana"]["state"], "rate_limited")
                self.assertEqual(status["network_status"]["SOLANA"]["status"], "rate_limited")
                self.assertIn("Too many requests", status["solana"]["error"])
                self.assertTrue(status["network_status"]["SOLANA"]["key_active"])
                self.assertNotIn("legacy", poller.key_errors)
                self.assertEqual(poller.key_status()[0]["state"], "active")
                with self.assertRaises(RateLimited):
                    await poller._solana_rpc(FakeSession(), "getHealth", [])
                self.assertEqual(len(requests), 1)
                poller.chain_cooldown[("SOLANA", "legacy")] = time.time() - 1
                self.assertEqual(await poller._solana_rpc(
                    FakeSession(), "getHealth", []), "ok")
                self.assertEqual(poller.status()["network_status"]["SOLANA"]["status"], "online")
                self.assertEqual(poller.key_status()[0]["state"], "active")
            screen.close()

    async def test_solana_rpc_uses_alchemy_solana_endpoint_and_native_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            poller = WhalePoller("test-alchemy-key-123", screen,
                                 state_file=Path(tmp) / "poller.json")
            requests = []

            class FakeResponse:
                status = 200
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    return None
                async def json(self):
                    return {"jsonrpc": "2.0", "id": 1,
                            "result": [{"signature": "sig", "err": None}]}

            class FakeSession:
                def post(self, url, *, json, timeout):
                    requests.append((url, json, timeout))
                    return FakeResponse()

            result = await poller._solana_rpc(
                FakeSession(), "getSignaturesForAddress", ["wallet", {"limit": 20}])
            self.assertEqual(result, [{"signature": "sig", "err": None}])
            self.assertEqual(requests[0][0],
                "https://solana-mainnet.g.alchemy.com/v2/test-alchemy-key-123")
            self.assertEqual(requests[0][1]["method"], "getSignaturesForAddress")
            self.assertEqual(requests[0][1]["params"][0], "wallet")
            screen.close()

    async def test_solana_native_lamports_and_spl_token_decimals(self):
        owner, other = "1" * 32, "2" * 32
        token_source, token_destination = "3" * 32, "4" * 32
        native_signature, token_signature = "5" * 64, "6" * 64
        usdc = next(mint for mint, (symbol, _) in SOLANA_TOKENS.items() if symbol == "USDC")
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            screen.wallets_by_chain["SOLANA"] = {owner: "Solana CEX"}
            poller = WhalePoller("unused", screen, state_file=Path(tmp) / "poller.json")
            native_tx = {"blockTime": int(time.time()),
                "transaction": {"signatures": [native_signature],
                    "message": {"accountKeys": [owner, other], "instructions": [
                        {"program": "system", "parsed": {"type": "transfer", "info": {
                            "source": owner, "destination": other,
                            "lamports": 600_000_000_000}}}]}},
                "meta": {"preBalances": [1_000_000_000_000, 0],
                         "postBalances": [499_999_995_000, 500_000_000_000],
                         "fee": 5_000}}
            count = await poller._solana_process_native(native_tx, owner)
            self.assertEqual(count, 1)
            self.assertEqual(screen.events[-1]["symbol"], "SOL")
            self.assertEqual(screen.events[-1]["amount"], 500.0)
            self.assertEqual(screen.events[-1]["usd"], 100_000.0)
            self.assertEqual(screen.events[-1]["direction"], "outflow")

            balance_tx = {"blockTime": int(time.time()),
                "transaction": {"signatures": ["7" * 64], "message": {
                    "accountKeys": [owner, other], "instructions": []}},
                "meta": {"preBalances": [1_000_000_000_000, 0],
                         "postBalances": [499_999_995_000, 500_000_000_000],
                         "fee": 5_000}}
            count = await poller._solana_process_native(balance_tx, owner)
            self.assertEqual(count, 1)
            self.assertEqual(screen.events[-1]["hash"], "7" * 64)
            self.assertEqual(screen.events[-1]["amount"], 500.0)
            self.assertEqual(screen.events[-1]["usd"], 100_000.0)

            def token_balance(index, address, amount):
                return {"accountIndex": index, "owner": address, "mint": usdc,
                        "uiTokenAmount": {"amount": str(amount), "decimals": 6}}
            token_tx = {"blockTime": int(time.time()),
                "transaction": {"signatures": [token_signature], "message": {
                    "accountKeys": ["payer", token_source, token_destination],
                    "instructions": [{"parsed": {"type": "transferChecked", "info": {
                        "source": token_source, "destination": token_destination}}}]}},
                "meta": {"preTokenBalances": [token_balance(1, other, 100_000_000_000),
                                              token_balance(2, owner, 0)],
                         "postTokenBalances": [token_balance(1, other, 0),
                                               token_balance(2, owner, 100_000_000_000)]}}
            count = await poller._solana_process_network_token_tx(token_tx, {owner})
            self.assertEqual(count, 1)
            event = screen.events[-1]
            self.assertEqual(event["symbol"], "USDC")
            self.assertEqual(event["amount"], 100_000.0)
            self.assertEqual(event["usd"], 100_000.0)
            self.assertEqual(event["direction"], "inflow")

            balance_only_tx = {"blockTime": int(time.time()),
                "transaction": {"signatures": ["8" * 64], "message": {
                    "accountKeys": ["payer", token_source, token_destination],
                    "instructions": []}},
                "meta": token_tx["meta"]}
            count = await poller._solana_process_network_token_tx(balance_only_tx, {owner})
            self.assertEqual(count, 1)
            self.assertEqual(screen.events[-1]["hash"], "8" * 64)
            self.assertEqual(screen.events[-1]["amount"], 100_000.0)
            self.assertEqual(screen.events[-1]["direction"], "inflow")
            screen.close()

    async def test_filtered_mined_evm_native_websocket_events(self):
        self.assertIn("ETH", MINED_TRANSACTION_CHAINS)
        owner, recipient = "0x" + "1" * 40, "0x" + "2" * 40
        with tempfile.TemporaryDirectory() as tmp:
            screen = self.make_screener(tmp)
            screen.wallets_by_chain["ETH"] = {owner: "Exchange"}
            poller = WhalePoller("unused", screen, state_file=Path(tmp) / "poller.json")
            tx = {"hash": "0x" + "a" * 64, "from": owner, "to": recipient,
                  "value": hex(10**18), "input": "0x"}
            self.assertTrue(await poller._handle_mined_native(
                "ETH", {"removed": False, "transaction": tx}))
            self.assertEqual(screen.events[-1]["symbol"], "ETH")
            self.assertEqual(screen.events[-1]["amount"], 1.0)
            self.assertEqual(screen.events[-1]["usd"], 2_000.0)
            self.assertEqual(screen.events[-1]["source"], "realtime")
            self.assertFalse(await poller._handle_mined_native(
                "ETH", {"removed": True, "transaction": {**tx, "hash": "0x" + "b" * 64}}))
            self.assertFalse(await poller._handle_mined_native(
                "ETH", {"transaction": {**tx, "hash": "0x" + "c" * 64, "input": "0xdead"}}))
            screen.close()


if __name__ == "__main__":
    unittest.main()
