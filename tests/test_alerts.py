"""Алерты: окно, порог, мин. удар, конфиг."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from alerts import (  # noqa: E402
    canon_symbol, cooldown_sec, cvd_by_symbol, evaluate, format_alert_html,
    liq_by_symbol, money, normalize_config, oi_by_symbol, oi_window_key,
    should_fire, window_label,
)


def _liq(sym, usd, ts, side="SELL"):
    return {"symbol": sym, "usd": usd, "timestamp": ts, "side": side,
            "exchange": "binance"}


class AlertsTest(unittest.TestCase):
    def test_canon_and_labels(self):
        self.assertEqual(canon_symbol("btc"), "BTC")
        self.assertEqual(canon_symbol("btcusdt"), "BTC_USDT")
        self.assertEqual(canon_symbol("eth-usdt"), "ETH_USDT")
        self.assertEqual(canon_symbol("all"), "ALL")
        self.assertEqual(window_label(5), "5м")
        self.assertEqual(window_label(60), "1ч")
        self.assertEqual(window_label(240), "4ч")
        self.assertEqual(oi_window_key(5), "m5")
        self.assertEqual(oi_window_key(7), "m5")
        self.assertEqual(oi_window_key(60), "h1")
        self.assertIn("K", money(12_500))

    def test_normalize_io_alias_and_flat_threshold(self):
        c = normalize_config({
            "watch": ["IO", "liquidations"],
            "symbol": "btc-usdt",
            "window": 15,
            "threshold": 250000,
            "min_event": 10000,
            "enabled": True,
        })
        self.assertEqual(c["watch"], ["oi", "liq"])
        self.assertEqual(c["symbol"], "BTC_USDT")
        self.assertEqual(c["window_min"], 15)
        self.assertEqual(c["threshold"]["liq"], 250000)
        self.assertEqual(c["threshold"]["oi"], 250000)
        self.assertEqual(c["min_event"]["cvd"], 10000)
        self.assertTrue(c["enabled"])

    def test_liq_min_event_and_window(self):
        now = 1_000_000.0
        events = [
            _liq("BTC_USDT", 40_000, now - 10),
            _liq("BTC_USDT", 80_000, now - 20, "BUY"),
            _liq("BTC_USDT", 90_000, now - 400),   # старше 5м
            _liq("ETH_USDT", 70_000, now - 15),
        ]
        rows = liq_by_symbol(events, 300, min_usd=50_000, now=now, symbol="ALL")
        self.assertIn("ETH_USDT", rows)
        self.assertEqual(rows["BTC_USDT"]["usd"], 80_000)  # 40k отсечён
        self.assertEqual(rows["BTC_USDT"]["shorts"], 80_000)
        only = liq_by_symbol(events, 300, 0, now, "BTC_USDT")
        self.assertEqual(set(only), {"BTC_USDT"})
        self.assertEqual(only["BTC_USDT"]["usd"], 120_000)

    def test_cvd_skips_small_buckets(self):
        now = 1_000_000.0
        acc = {
            "BTC_USDT|1": {
                int(now - 10): 80_000,
                int(now - 40): -5_000,
                int(now - 400): 9_000_000,
            }
        }
        rows = cvd_by_symbol(acc, 300, min_bucket=10_000, now=now, symbol="ALL")
        self.assertAlmostEqual(rows["BTC_USDT"]["usd"], 80_000)

    def test_oi_change_and_evaluate(self):
        now = 1_000_000.0
        market = {
            "now": now,
            "events": [
                _liq("BTC_USDT", 600_000, now - 30),
                _liq("ETH_USDT", 40_000, now - 20),
            ],
            "cvd": {"SOL_USDT|1": {int(now - 5): -800_000}},
            "oi": {"BTC_USDT": {"changes": {"m5": {"usd": 1_200_000, "pct": 1.2}},
                                "total_usd": 4e8}},
        }
        cfg = {
            "enabled": True,
            "watch": ["liq", "cvd", "oi"],
            "symbol": "ALL",
            "window_min": 5,
            "threshold": {"liq": 500_000, "cvd": 500_000, "oi": 1_000_000},
            "min_event": 0,
        }
        hits = evaluate(cfg, market)
        metrics = {h["metric"] for h in hits}
        self.assertEqual(metrics, {"liq", "cvd", "oi"})
        liq_hit = next(h for h in hits if h["metric"] == "liq")
        self.assertEqual(liq_hit["symbol"], "BTC_USDT")
        self.assertGreaterEqual(liq_hit["value"], 500_000)
        text = format_alert_html(liq_hit)
        self.assertIn("Алерт", text)
        self.assertIn("BTC", text)
        self.assertIn("https://liqscope.online/terminal", text)

    def test_below_threshold_no_hit(self):
        now = 100.0
        hits = evaluate(
            {"watch": ["liq"], "threshold": 1_000_000, "window_min": 5},
            {"now": now, "events": [_liq("BTC_USDT", 10_000, now - 1)]},
        )
        self.assertEqual(hits, [])

    def test_cooldown(self):
        self.assertEqual(cooldown_sec(5), 300)
        self.assertTrue(should_fire(None, 1000, 5))
        self.assertFalse(should_fire(900, 1000, 5))
        self.assertTrue(should_fire(100, 1000, 5))

    def test_oi_by_symbol_filters_min(self):
        oi = {
            "BTC_USDT": {"changes": {"h1": {"usd": 50_000, "pct": 0.1}}},
            "ETH_USDT": {"changes": {"h1": {"usd": -900_000, "pct": -0.4}}},
        }
        rows = oi_by_symbol(oi, 60, min_usd=100_000, symbol="ALL")
        self.assertEqual(set(rows), {"ETH_USDT"})
        self.assertLess(rows["ETH_USDT"]["usd"], 0)


if __name__ == "__main__":
    unittest.main()
