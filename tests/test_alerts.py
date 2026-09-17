"""Алерты: окно, порог, мин. удар, конфиг."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from alerts import (  # noqa: E402
    THRESHOLD_PRESETS_FLOW, canon_symbol, cooldown_sec, cvd_by_symbol, evaluate,
    format_alert_html, liq_by_symbol, money, normalize_config, oi_by_symbol,
    oi_window_key, presets, should_fire, sparkline, threshold_presets,
    window_label,
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

    def test_window_presets_are_five(self):
        """Кнопок окна агрегации ровно пять — и в боте, и в кабинете."""
        from alerts import WINDOW_PRESETS, presets
        self.assertEqual(len(WINDOW_PRESETS), 5)
        self.assertEqual(list(WINDOW_PRESETS), [1, 15, 30, 60, 240])
        self.assertNotIn(5, WINDOW_PRESETS)          # «5м» убрана
        self.assertEqual(presets()["windows"], list(WINDOW_PRESETS))

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
        self.assertIn("посмотреть в терминале", text)
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

    def test_watch_can_be_one_or_empty(self):
        c = normalize_config({"watch": ["cvd"], "enabled": True})
        self.assertEqual(c["watch"], ["cvd"])
        empty = normalize_config({"watch": [], "enabled": True})
        self.assertEqual(empty["watch"], [])
        missing = normalize_config({"enabled": True})
        self.assertEqual(missing["watch"], ["liq"])

    def test_flow_thresholds_are_larger(self):
        self.assertEqual(threshold_presets("cvd"), list(THRESHOLD_PRESETS_FLOW))
        self.assertEqual(threshold_presets("oi"), list(THRESHOLD_PRESETS_FLOW))
        self.assertIn(100_000_000, presets()["thresholds_flow"])
        self.assertNotIn(100_000_000, threshold_presets("liq"))

    def test_sparkline_bins_window(self):
        now = 1000.0
        pts = {910: 10, 950: 20, 990: 40}
        s = sparkline(pts, now, 120, n=4)
        self.assertEqual(len(s), 4)
        self.assertGreater(sum(s), 0)

    def test_live_snapshot_has_sparks(self):
        from alerts import live_snapshot
        now = 1_000_000.0
        snap = live_snapshot(
            {"watch": ["liq"], "window_min": 5, "min_event": 0},
            {"now": now, "events": [_liq("BTC_USDT", 80_000, now - 10)],
             "cvd": {}, "oi": {}},
        )
        self.assertEqual(len(snap["liq"]["spark"]), 24)
        self.assertGreater(sum(snap["liq"]["spark"]), 0)
        self.assertEqual(len(snap["cvd"]["spark"]), 24)


if __name__ == "__main__":
    unittest.main()
