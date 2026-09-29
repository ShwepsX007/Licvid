"""Offline coverage for multichain whale ingestion and wallet registry CRUD."""
import asyncio
import tempfile
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cex_wallets_updater import (
    CEXWalletRegistry, normalize_chain, parse_defillama_cex_config,
    valid_address,
)
from tron_address import tron_to_base58, tron_to_hex
from whale_poller import MINED_TRANSACTION_CHAINS, WhalePoller
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
          other: { owners: [] }
        };
        '''
        metadata = {"cexs": [{"slug": "binance", "name": "Binance"}]}
        rows = parse_defillama_cex_config(source, metadata)
        self.assertEqual({row["chain"] for row in rows},
                         {"ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA", "TRON"})
        self.assertTrue(all(row["name"] == "Binance" for row in rows))
        self.assertEqual(normalize_chain("binanceSmartChain"), "BNB")
        self.assertEqual(normalize_chain("arbitrum-one"), "ARBITRUM")
        self.assertTrue(valid_address("SOLANA", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"))
        self.assertTrue(valid_address("TRON", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"))
        self.assertFalse(valid_address("TRON", "T" + "1" * 33))

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
            async def fake_events(_session, _url, _params=None):
                return {"data": [{"event_index": 7, "block_number": 124,
                    "block_timestamp": int(time.time() * 1000),
                    "transaction_id": "b" * 64,
                    "result": {"from": recipient, "to": owner, "value": "25000001"}}],
                    "meta": {}}
            poller._trongrid_get = fake_events
            await poller._poll_tron_contract(None, contract, symbol, decimals,
                                             int(time.time() * 1000))
            event = screen.events[-1]
            self.assertEqual(event["symbol"], "USDT")
            self.assertEqual(event["log_index"], "trc20:7")
            self.assertAlmostEqual(event["amount"], 25.000001)
            self.assertAlmostEqual(event["usd"], 25.0, places=2)
            self.assertEqual(event["direction"], "inflow")
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
                    "message": {"instructions": [{"program": "system", "parsed": {
                        "type": "transfer", "info": {"source": owner, "destination": other,
                                                           "lamports": 1_500_000_000}}}]}},
                "meta": {}}
            count = await poller._solana_process_native(native_tx, owner)
            self.assertEqual(count, 1)
            self.assertEqual(screen.events[-1]["symbol"], "SOL")
            self.assertEqual(screen.events[-1]["amount"], 1.5)
            self.assertEqual(screen.events[-1]["usd"], 300.0)
            self.assertEqual(screen.events[-1]["direction"], "outflow")

            def token_balance(index, address, amount):
                return {"accountIndex": index, "owner": address, "mint": usdc,
                        "uiTokenAmount": {"amount": str(amount), "decimals": 6}}
            token_tx = {"blockTime": int(time.time()),
                "transaction": {"signatures": [token_signature], "message": {
                    "accountKeys": ["payer", token_source, token_destination],
                    "instructions": [{"parsed": {"type": "transferChecked", "info": {
                        "source": token_source, "destination": token_destination}}}]}},
                "meta": {"preTokenBalances": [token_balance(1, other, 5_000_000),
                                              token_balance(2, owner, 0)],
                         "postTokenBalances": [token_balance(1, other, 0),
                                               token_balance(2, owner, 5_000_000)]}}
            count = await poller._solana_process_token(token_tx, owner, usdc)
            self.assertEqual(count, 1)
            event = screen.events[-1]
            self.assertEqual(event["symbol"], "USDC")
            self.assertEqual(event["amount"], 5.0)
            self.assertEqual(event["usd"], 5.0)
            self.assertEqual(event["direction"], "inflow")
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
