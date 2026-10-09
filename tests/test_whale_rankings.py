"""Рейтинги китов: топы окна с деньгами и скоростью.

Что проверяем:
* rank_events собирает топы событий/кошельков/токенов/сетей из потока;
* usd_per_min отличает «$2M за 5 минут» от «$2M растянутых на сутки»;
* нетто (inflow − outflow) считается по каждой группе;
* ручка /api/screener/whales/rankings отдаёт структуру с enrichment
  (usd_vs_vol24h для токенов, которые торгуются как перпы);
* окно и порог валидируются.

Запуск:  python3 tests/test_whale_rankings.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="whale_rank_")
os.environ.setdefault("LIQSCOPE_ACCOUNTS_DB", os.path.join(TMP, "accounts.db"))
os.environ.setdefault("LIQSCOPE_HISTORY_FILE", os.path.join(TMP, "liq.jsonl"))
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
os.environ.setdefault("LIQSCOPE_DEMO", "0")

from whale_screener import WhaleHistoryStore, rank_events  # noqa: E402


def ev(chain, usd, direction, ts, symbol="USDT", frm="0xaaa", to="0xbbb"):
    return {"chain": chain, "hash": "0x" + format(abs(hash((chain, usd, ts, frm, direction))) % 10**76, "064x"),
            "log_index": 1,
            "timestamp": ts, "usd": usd, "direction": direction,
            "symbol": symbol, "amount": usd / 100.0,
            "from": frm, "to": to}


class RankEvents(unittest.TestCase):
    def test_tops_and_speed(self):
        now = time.time()
        rows = [
            # быстрый кит: $2M за 5 минут (один кошелёк, три события)
            ev("ETH", 900_000, "inflow", now - 300, frm="0xfast", to="0xex1"),
            ev("ETH", 700_000, "inflow", now - 120, frm="0xfast", to="0xex1"),
            ev("ETH", 400_000, "outflow", now - 60, frm="0xex2", to="0xfast"),
            # медленный: $2M растянутые на сутки
            ev("BTC", 1_000_000, "inflow", now - 86_000, frm="0xslow",
               to="0xex1", symbol="BTC"),
            ev("BTC", 1_000_000, "inflow", now - 100, frm="0xslow",
               to="0xex1", symbol="BTC"),
        ]
        data = rank_events(rows, now - 172800, now, min_usd=0, top=10)
        self.assertEqual(data["totals"]["n"], 5)
        self.assertAlmostEqual(data["totals"]["usd"], 4_000_000, delta=1)
        self.assertAlmostEqual(data["totals"]["inflow_usd"], 3_600_000, delta=1)
        self.assertAlmostEqual(data["totals"]["outflow_usd"], 400_000, delta=1)
        wallets = {w["address"]: w for w in data["wallets"]}
        self.assertIn("0xslow", wallets)
        self.assertIn("0xfast", wallets)
        # скорость: быстрый кит при той же сумме несёт деньги в разы быстрее
        self.assertGreater(wallets["0xfast"]["usd_per_min"] * 10,
                           wallets["0xslow"]["usd_per_min"])
        # нетто у медленного — чистый inflow
        self.assertAlmostEqual(wallets["0xslow"]["net_usd"], 2_000_000, delta=1)
        # топ-событие — самое крупное
        self.assertEqual(max(float(e["usd"]) for e in data["events"]),
                         1_000_000)
        # сети
        nets = {n["key"]: n for n in data["networks"]}
        self.assertAlmostEqual(nets["ETH"]["usd"], 2_000_000, delta=1)
        self.assertAlmostEqual(nets["BTC"]["usd"], 2_000_000, delta=1)
        # токены
        toks = {t["key"]: t for t in data["tokens"]}
        self.assertEqual(set(toks), {"USDT", "BTC"})

    def test_min_usd_filters_groups(self):
        now = time.time()
        rows = [ev("ETH", 10_000, "inflow", now - 60),
                ev("ETH", 1_000_000, "inflow", now - 30)]
        data = rank_events(rows, now - 3600, now, min_usd=50_000, top=10)
        self.assertEqual(data["totals"]["n"], 1)
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["usd"], 1_000_000)

    def test_store_rankings_via_sqlite(self):
        path = os.path.join(TMP, "whale_rank.sqlite")
        store = WhaleHistoryStore(path)
        self.addCleanup(store.close)
        now = time.time()
        for i in range(5):
            row = ev("ETH", 100_000 + i * 1000, "inflow",
                     now - 600 + i * 60, symbol="USDT")
            row["hash"] = "0x" + format(i + 1, "064x")
            store.append(row)
        data = store.rankings(now - 3600, now, min_usd=0, top=10)
        self.assertEqual(data["totals"]["n"], 5)
        self.assertTrue(data["wallets"])
        self.assertTrue(data["tokens"])


class RankingsEndpoint(unittest.TestCase):
    def test_endpoint_private_and_shape(self):
        import server
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        member = {"id": 2, "is_admin": False}
        now = time.time()

        class FakeScreener:
            def rankings(self, since, until, min_usd=50_000.0, top=10):
                from whale_screener import rank_events
                rows = [ev("ETH", 900_000, "inflow", until - 60, symbol="USDT")]
                return rank_events(rows, since, until, min_usd=min_usd, top=top)

        with TestClient(server.app) as c:
            # скринер — закрытая часть: гость получает 401
            self.assertEqual(
                c.get("/api/screener/whales/rankings").status_code, 401)
            real = server.whale_screener
            server.whale_screener = FakeScreener()
            try:
                with patch.object(server, "current_user",
                                  lambda _request: member):
                    r = c.get("/api/screener/whales/rankings?window_hours=24&min_usd=1000")
                    self.assertEqual(r.status_code, 200)
                    d = r.json()
                    for key in ("available", "totals", "events", "inflow",
                                "outflow", "wallets", "tokens", "networks",
                                "window_hours"):
                        self.assertIn(key, d)
                    self.assertEqual(d["totals"]["n"], 1)
                    self.assertIsInstance(d["tokens"], list)
                    for t in d["tokens"]:
                        self.assertIn("usd_per_min", t)
                        self.assertIn("usd_vs_vol24h", t)
                    self.assertEqual(d["window_hours"], 24.0)
                    bad = c.get("/api/screener/whales/rankings?window_hours=9999")
                    self.assertEqual(bad.status_code, 422)
            finally:
                server.whale_screener = real


if __name__ == "__main__":
    unittest.main()
