"""Сводка в канал: лидеры, биржи, OI/CVD, разные тексты."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from channel_digest import (  # noqa: E402
    CAPTION_LIMIT, VARIANT_COUNT, _headlines, collect_digest,
    format_headline, list_images, money, pick_image, render_post,
)


def _ev(sym, usd, side, exch, ts, n=1):
    return {"symbol": sym, "usd": usd, "side": side,
            "exchange": exch, "timestamp": ts, "id": f"{sym}-{n}"}


class DigestTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_000_000.0
        self.events = [
            _ev("BTC_USDT", 2_400_000, "SELL", "binance", self.now - 60, 1),
            _ev("BTC_USDT", 800_000, "BUY", "bybit", self.now - 120, 2),
            _ev("ETH_USDT", 1_100_000, "SELL", "okx", self.now - 200, 3),
            _ev("SOL_USDT", 250_000, "BUY", "binance", self.now - 300, 4),
            _ev("DOGE_USDT", 90_000, "SELL", "gate", self.now - 400, 5),
            # слишком старое — не в окне 4ч
            _ev("XRP_USDT", 9_000_000, "SELL", "binance", self.now - 20_000, 6),
        ]
        self.oi = {
            "BTC_USDT": {"total_usd": 12e9,
                         "changes": {"h4": {"usd": -180_000_000, "pct": -1.45}}},
            "ETH_USDT": {"total_usd": 4e9,
                         "changes": {"h4": {"usd": 40_000_000, "pct": 1.02}}},
        }
        self.cvd = {"BTC_USDT": -12_500_000, "ETH_USDT": 3_200_000}

    def test_collect_window_and_leaders(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        self.assertEqual(snap["count"], 5)
        self.assertAlmostEqual(snap["total_usd"], 4_640_000)
        self.assertEqual(snap["top_coins"][0]["symbol"], "BTC_USDT")
        self.assertIn("binance", snap["exchanges"])
        self.assertNotIn("XRP_USDT", [c["symbol"] for c in snap["top_coins"]])
        self.assertEqual(snap["biggest"]["usd"], 2_400_000)

    def test_variants_differ_and_contain_facts(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        texts = [render_post(snap, i) for i in range(VARIANT_COUNT)]
        self.assertEqual(len(_headlines(4)), VARIANT_COUNT)
        self.assertGreaterEqual(VARIANT_COUNT, 20)
        self.assertEqual(len(set(texts)), VARIANT_COUNT)
        for t in texts:
            self.assertIn("BTC", t)
            self.assertIn("Binance", t)
            self.assertIn("LiqScope", t)
            self.assertIn("https://liqscope.online", t)
            self.assertIn("t.me/LiqScopeBot", t)
            self.assertLessEqual(len(t), CAPTION_LIMIT)
            self.assertIn("CVD", t)
            self.assertIn("OI", t)
            self.assertIn("<code>", t)
            self.assertTrue(any(ch in t for ch in "💥🔴🟢🐋📊🌊🏛🔥⚡🏆🌙🌡⏱❓📋📉📈"))

    def test_blogger_heads_are_alive(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        heads = []
        for i in range(VARIANT_COUNT):
            line = render_post(snap, i).split("\n", 1)[0]
            heads.append(line)
            self.assertNotIn("LiqScope ·", line)
            self.assertNotIn("LIQ //", line)
            self.assertTrue(any(ch in line for ch in ".?—!"), line)
            self.assertIn("<b>", line)
        self.assertEqual(len(set(heads)), VARIANT_COUNT)

    def test_caption_fits_with_many_coins(self):
        extra = [
            _ev(f"C{i}_USDT", 80_000 + i * 1000, "SELL" if i % 2 == 0 else "BUY",
                "binance", self.now - 10, 10 + i)
            for i in range(16)
        ]
        snap = collect_digest(self.events + extra, now=self.now,
                              oi=self.oi, cvd=self.cvd)
        for i in range(VARIANT_COUNT):
            t = render_post(snap, i)
            self.assertLessEqual(len(t), CAPTION_LIMIT, f"v{i} len={len(t)}")
            self.assertIn("LiqScope", t)

    def test_money_and_empty(self):
        self.assertEqual(money(1_250_000), "$1.25M")
        self.assertEqual(money(-900), "−$900")
        snap = collect_digest([], now=self.now)
        t = render_post(snap, 0)
        self.assertIn("лидеров нет", t)
        self.assertLessEqual(len(t), CAPTION_LIMIT)

    def test_custom_headlines_used(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        custom = [format_headline("☕ Моя шапка за {h}ч. Цифры ниже.", 4)]
        t = render_post(snap, 0, headlines=custom)
        self.assertIn("Моя шапка", t)
        self.assertIn("BTC", t)
        self.assertLessEqual(len(t), CAPTION_LIMIT)
        self.assertIn("4ч", t)

    def test_images_exist(self):
        imgs = list_images()
        self.assertGreaterEqual(len(imgs), 6, imgs)
        self.assertTrue(os.path.isfile(pick_image(0)))
        self.assertNotEqual(pick_image(0), pick_image(1))


if __name__ == "__main__":
    unittest.main()
