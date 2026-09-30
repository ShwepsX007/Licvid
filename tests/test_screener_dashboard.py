"""Closed screener pages/API, historical analytics and admin Alchemy CRUD."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
from fastapi.testclient import TestClient

import server
from alchemy_keys import AlchemyKeyStore
from cex_wallets_updater import CEXWalletRegistry, SourceFetchError
from whale_poller import WhalePoller
from whale_screener import WhaleScreener

ADMIN = {"id": 1, "is_admin": True}
MEMBER = {"id": 2, "is_admin": False}


def event(index: int, *, timestamp: float | None = None, usd: float | None = None,
          exchange: str = "Binance 14") -> dict:
    return {
        "chain": "ETH", "hash": "0x" + format(index + 1, "064x"),
        "log_index": str(index), "timestamp": time.time() if timestamp is None else timestamp,
        "symbol": "USDT", "amount": 100_000 + index,
        "usd": 100_000 + index if usd is None else usd,
        "from": "0x" + "1" * 40, "to": "0x" + "2" * 40,
        "from_label": exchange, "to_label": "", "direction": "outflow",
    }


class ScreenerDashboardTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_guest_page_redirect_and_all_screener_routes_are_private(self):
        for path in ("/screener", "/static/screener.html"):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/login?next=/screener")
        for path in (
            "/api/screener/whales", "/api/screener/stats",
            "/api/screener/history", "/api/screener/history.csv",
            "/api/screener/not-a-route",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.json()["error"], "auth")
        self.client.cookies.set("liqscope_sid", "expired-or-unknown")
        self.assertEqual(self.client.get("/api/screener/whales").status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/admin/alchemy/keys").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/alchemy/stats").status_code, 403)

    def test_member_can_view_but_cannot_manage_alchemy(self):
        with patch.object(server, "current_user", lambda _request: MEMBER):
            self.assertEqual(self.client.get("/screener").status_code, 200)
            self.assertEqual(self.client.get("/api/screener/stats").status_code, 200)
            self.assertEqual(self.client.get("/api/admin/alchemy/keys").status_code, 403)
            self.assertEqual(self.client.get("/api/admin/alchemy/stats").status_code, 403)
            self.assertEqual(self.client.post("/api/admin/alchemy/keys", json={"key": "test-key"}).status_code, 403)
            self.assertEqual(self.client.request("DELETE", "/api/admin/alchemy/keys", json={"id": "0123456789abcdef"}).status_code, 403)

    def test_admin_key_crud_masks_credentials_and_reports_cu_networks(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_store = AlchemyKeyStore(os.environ["LIQSCOPE_SECRET"], path=Path(tmp) / "keys.enc")
            screen = WhaleScreener("placeholder", lambda _pair: None,
                                   lambda _msg: None, history_path=Path(tmp) / "history.sqlite3")
            poller = WhalePoller("", screen, key_store=key_store,
                                 state_file=Path(tmp) / "poller.json")
            poller.network_status["ETH"].update(
                status="rate_limited", last_success=time.time() - 60,
                last_event=time.time(), retry_at=time.time() + 15,
                http_status=429, error="HTTP 429: rate limit exceeded", key_active=True)
            poller.state.setdefault("network_cu", {})["ETH"] = 120
            poller.solana_status.update(connected=True, state="online",
                                        waiting_for_filters=True, last_success=time.time())
            with patch.object(server, "alchemy_key_store", key_store), \
                 patch.object(server, "whale_screener", screen), \
                 patch.object(server, "whale_poller", poller), \
                 patch.object(server, "alchemy_vault_error", ""), \
                 patch.object(server, "current_user", lambda _request: ADMIN):
                key = "test-api-key-dashboard-secret"
                before = self.client.get("/api/admin/alchemy/keys")
                self.assertEqual(before.status_code, 200, before.text)
                self.assertEqual(before.json()["keys"], [])
                added = self.client.post("/api/admin/alchemy/keys", json={"key": key})
                self.assertEqual(added.status_code, 200, added.text)
                self.assertNotIn(key, added.text)
                public = added.json()["key"]
                self.assertEqual(public["hint"], key[:4] + "••••" + key[-4:])
                identifier = public["id"]
                self.assertTrue(poller.wakeup.is_set())
                listed = self.client.get("/api/admin/alchemy/keys").json()
                self.assertEqual(listed["keys"][0]["hint"], public["hint"])
                stats = self.client.get("/api/admin/alchemy/stats")
                self.assertEqual(stats.status_code, 200, stats.text)
                payload = stats.json()
                self.assertEqual(payload["cu"]["keys_configured"], 1)
                eth = next(row for row in payload["networks"] if row["chain"] == "ETH")
                self.assertEqual(eth["status"], "online")
                self.assertEqual(eth["warning_status"], "rate_limited")
                self.assertEqual(eth["http_status"], 429)
                self.assertTrue(eth["key_active"])
                self.assertEqual(eth["cu_used"], 120)
                self.assertIn("rate limit exceeded", eth["error"])
                solana = next(row for row in payload["networks"] if row["chain"] == "SOLANA")
                self.assertEqual(solana["status"], "online")
                self.assertTrue(solana["waiting_for_filters"])
                self.assertEqual({row["chain"] for row in payload["networks"]},
                                 {"ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "HYPERLIQUID", "SOLANA", "TRON"})
                self.assertNotIn(key, stats.text)
                deleted = self.client.request("DELETE", "/api/admin/alchemy/keys", json={"id": identifier})
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertTrue(deleted.json()["ok"])
                self.assertEqual(self.client.get("/api/admin/alchemy/keys").json()["keys"], [])
            screen.close()

    def test_admin_cex_wallet_crud_and_encrypted_trongrid_key_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = CEXWalletRegistry(Path(tmp) / "cex_wallets.json")
            trongrid_store = AlchemyKeyStore(os.environ["LIQSCOPE_SECRET"],
                                             path=Path(tmp) / "trongrid_keys.enc")
            with patch.object(server, "cex_wallet_registry", registry), \
                 patch.object(server, "trongrid_key_store", trongrid_store), \
                 patch.object(server, "trongrid_vault_error", ""), \
                 patch.object(server, "whale_screener", None), \
                 patch.object(server, "whale_poller", None), \
                 patch.object(server, "current_user", lambda _request: MEMBER):
                self.assertEqual(self.client.get("/api/admin/screener/cex-wallets").status_code, 403)
                self.assertEqual(self.client.get("/api/admin/trongrid/key").status_code, 403)
                self.assertEqual(self.client.post("/api/admin/trongrid/key", json={"key": "test-key"}).status_code, 403)
            with patch.object(server, "cex_wallet_registry", registry), \
                 patch.object(server, "trongrid_key_store", trongrid_store), \
                 patch.object(server, "trongrid_vault_error", ""), \
                 patch.object(server, "whale_screener", None), \
                 patch.object(server, "whale_poller", None), \
                 patch.object(server, "current_user", lambda _request: ADMIN):
                added = self.client.post("/api/admin/screener/cex-wallets", json={
                    "chain": "ETH", "address": "0x" + "1" * 40, "name": "Manual CEX"})
                self.assertEqual(added.status_code, 200, added.text)
                identifier = added.json()["wallet"]["id"]
                edited = self.client.put("/api/admin/screener/cex-wallets/" + identifier, json={
                    "chain": "SOLANA", "address": "1" * 32, "name": "Solana CEX"})
                self.assertEqual(edited.status_code, 200, edited.text)
                new_identifier = edited.json()["wallet"]["id"]
                listing = self.client.get("/api/admin/screener/cex-wallets?chain=SOLANA")
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["wallets"][0]["name"], "Solana CEX")
                self.assertEqual(self.client.delete("/api/admin/screener/cex-wallets/" + new_identifier).status_code, 200)
                preserved = registry.add_manual("ETH", "0x" + "4" * 40, "Preserved manual")
                before_refresh = registry.path.read_bytes()
                source_error = SourceFetchError(
                    "https://api.llama.fi/cexs", "HTTP error", status=404,
                    body="Not Found: endpoint unavailable")
                with patch("cex_wallets_updater.fetch_defillama_cex_wallets",
                           new_callable=AsyncMock, side_effect=source_error):
                    failed_refresh = self.client.post("/api/admin/screener/cex-wallets/refresh")
                self.assertEqual(failed_refresh.status_code, 502)
                self.assertIn("https://api.llama.fi/cexs", failed_refresh.json()["reason"])
                self.assertIn("HTTP 404", failed_refresh.json()["reason"])
                self.assertIn("Not Found", failed_refresh.json()["reason"])
                self.assertEqual(registry.path.read_bytes(), before_refresh)
                self.assertTrue(any(row["id"] == preserved["id"]
                                    for row in registry.records()))
                key = "trongrid-api-key-test-secret"
                key_response = self.client.post("/api/admin/trongrid/key", json={"key": key})
                self.assertEqual(key_response.status_code, 200, key_response.text)
                self.assertNotIn(key, key_response.text)
                key_id = key_response.json()["key"]["id"]
                self.assertEqual(self.client.get("/api/admin/trongrid/key").json()["keys"][0]["source"], "admin")
                self.assertEqual(self.client.delete("/api/admin/trongrid/key/" + key_id).status_code, 200)
                self.assertEqual(self.client.get("/api/admin/trongrid/key").json()["keys"], [])

    def test_seven_day_history_pagination_sorting_filtering_stats_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "history.sqlite3"
            screen = WhaleScreener("placeholder", lambda _pair: None,
                                   lambda _msg: None, history_path=history_file)
            now = time.time()
            for i in range(51):
                self.assertTrue(screen.history_store.append(event(i, timestamp=now - i * 60)))
            self.assertTrue(screen.history_store.append(event(1000, timestamp=now - 8 * 86400)))
            screen.close()
            # Reopening the feed restores recent events and prunes stale rows.
            screen = WhaleScreener("placeholder", lambda _pair: None,
                                   lambda _msg: None, history_path=history_file)
            self.assertEqual(len(screen.events), 51)
            with patch.object(server, "whale_screener", screen), \
                 patch.object(server, "whale_poller", None), \
                 patch.object(server, "alchemy_key_store", None), \
                 patch.object(server, "current_user", lambda _request: MEMBER):
                params = {"chain": "ETH", "direction": "outflow", "exchange": "Binance 14",
                          "min_usd": "100050", "page_size": "50", "sort_by": "usd", "sort_dir": "desc"}
                first = self.client.get("/api/screener/history", params=params)
                self.assertEqual(first.status_code, 200, first.text)
                payload = first.json()
                self.assertEqual(payload["total"], 1)  # only USD >= 100050
                self.assertEqual(payload["page_size"], 50)
                self.assertEqual(payload["events"][0]["usd"], 100050)
                many = self.client.get("/api/screener/history", params={**params, "min_usd": "0"}).json()
                self.assertEqual(many["total"], 51)  # old event was pruned from the seven-day view
                self.assertEqual(len(many["events"]), 50)
                second = self.client.get("/api/screener/history", params={**params, "min_usd": "0", "page": "2"}).json()
                self.assertEqual(len(second["events"]), 1)
                stats = self.client.get("/api/screener/stats").json()
                self.assertEqual(stats["total_events"], 51)
                self.assertEqual(stats["networks"]["ETH"]["events"], 51)
                self.assertEqual(len(stats["series"]), 24)
                csv_response = self.client.get("/api/screener/history.csv", params={**params, "min_usd": "0"})
                self.assertEqual(csv_response.status_code, 200)
                self.assertIn("text/csv", csv_response.headers["content-type"])
                self.assertEqual(csv_response.text.count("\n"), 52)  # header + 51 rows
                self.assertIn("https://etherscan.io/tx/", csv_response.text)
            screen.close()


if __name__ == "__main__":
    unittest.main()
