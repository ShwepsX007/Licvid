"""Профиль объёма по времени и цене: цена входа для модели уровней.

От цены входа зависит всё: сдвиг VWAP на процент сдвигает и уровень
ликвидации на процент. Свеча отвечает «где была цена», а нужен «где прошёл
объём», поэтому модуль копит оборот, VWAP и перекос тейкера по 5-минутным
слотам — ровно по тем же слотам, что и ряд открытого интереса.

Проверяем:
    * слоты 5 минут, как у OI (иначе пара «дельта OI ↔ цена входа» разъедется);
    * VWAP считается по объёму (цена × объём), а не средним цен;
    * тики и свечи укладываются в один слот и не спорят между собой;
    * перекос тейкера (buy/sell) отдаётся как доля покупок 0…1;
    * окно хранения и потолок слотов не дают профилю расти бесконечно;
    * диск: сохранение, чтение и отбрасывание устаревшего при загрузке.

Запуск: python3 -m pytest tests/test_volume_profile.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from volume_profile import BUCKET_SEC, VolumeProfile, bucket_of  # noqa: E402


class BucketTest(unittest.TestCase):
    def test_five_minute_slots_match_oi(self):
        self.assertEqual(bucket_of(0), 0)
        self.assertEqual(bucket_of(299), 0)
        self.assertEqual(bucket_of(300), 300)
        self.assertEqual(bucket_of(1_700_000_123), 1_700_000_100)
        self.assertEqual(BUCKET_SEC, 300)


class AccumulateTest(unittest.TestCase):
    def setUp(self):
        self.vp = VolumeProfile(path="")
        self.t = bucket_of(1_700_000_000)     # выровнено по 5-минутной сетке

    def test_vwap_is_volume_weighted(self):
        # 1 единица по 100 и 3 единицы по 200 → VWAP = (100 + 600) / 4 = 175
        self.vp.add_trade("BTC_USDT", self.t, 100.0, 1.0, "BUY")
        self.vp.add_trade("BTC_USDT", self.t + 10, 200.0, 3.0, "SELL")
        self.assertAlmostEqual(self.vp.bucket_vwap("BTC_USDT", self.t), 175.0, places=9)
        self.assertAlmostEqual(self.vp.vwap("BTC_USDT", self.t, self.t + 300), 175.0)

    def test_side_ratio_is_taker_share(self):
        self.vp.add_trade("BTC_USDT", self.t, 100.0, 60.0, "BUY")
        self.vp.add_trade("BTC_USDT", self.t, 100.0, 40.0, "SELL")
        self.assertAlmostEqual(self.vp.side_ratio("BTC_USDT", self.t, self.t + 60),
                               0.6, places=9)

    def test_side_ratio_is_none_without_side(self):
        """Объём без стороны — не повод выдумать перекос."""
        self.vp.add_trade("BTC_USDT", self.t, 100.0, 60.0)
        self.assertIsNone(self.vp.side_ratio("BTC_USDT", self.t, self.t + 60))

    def test_garbage_trades_are_ignored(self):
        for price, usd in ((0, 10), (-1, 10), ("x", 10), (100, 0), (100, -5), (100, "y")):
            self.vp.add_trade("BTC_USDT", self.t, price, usd, "BUY")
        self.assertIsNone(self.vp.bucket_vwap("BTC_USDT", self.t))
        self.assertEqual(self.vp.stats()["buckets"], 0)

    def test_candle_goes_to_the_same_slot_as_ticks(self):
        self.vp.add_trade("BTC_USDT", self.t, 100.0, 10.0, "BUY")
        self.vp.add_candle("BTC_USDT", self.t + 60, high=120.0, low=80.0,
                           close=110.0, volume_usd=90.0)
        cell = self.vp.cell("BTC_USDT", self.t)
        self.assertAlmostEqual(cell["vol"], 100.0, places=6)
        # типичная цена свечи (120+80+110)/3 = 103.333…, VWAP сдвигается к ней
        expected = (100.0 * 10 + (120 + 80 + 110) / 3.0 * 90) / 100.0
        self.assertAlmostEqual(self.vp.bucket_vwap("BTC_USDT", self.t), expected, places=6)
        self.assertEqual(cell["lo"], 80.0)
        self.assertEqual(cell["hi"], 120.0)

    def test_range_limits(self):
        for i in range(10):
            self.vp.add_trade("BTC_USDT", self.t + i * BUCKET_SEC, 100.0 + i, 10.0, "BUY")
        part = self.vp.vwap("BTC_USDT", self.t + 3 * BUCKET_SEC, self.t + 5 * BUCKET_SEC)
        self.assertAlmostEqual(part, (103 + 104 + 105) / 3.0, places=6)
        self.assertIsNone(self.vp.vwap("BTC_USDT", self.t + 999 * BUCKET_SEC))

    def test_coverage_and_series(self):
        self.vp.add_trade("BTC_USDT", self.t, 100.0, 10.0, "BUY")
        self.vp.add_trade("BTC_USDT", self.t + BUCKET_SEC, 110.0, 20.0, "SELL")
        cov = self.vp.coverage("BTC_USDT")
        self.assertEqual(cov["buckets"], 2)
        self.assertEqual(cov["vol_usd"], 30.0)
        rows = self.vp.series("BTC_USDT")
        self.assertEqual([r["t"] for r in rows], [self.t, self.t + BUCKET_SEC])
        self.assertAlmostEqual(rows[1]["vwap"], 110.0, places=6)


class TrimTest(unittest.TestCase):
    def test_old_slots_are_dropped(self):
        vp = VolumeProfile(path="", keep_hours=1)
        now = time.time()
        vp.add_trade("BTC_USDT", now - 3 * 3600, 100.0, 10.0, "BUY")
        vp.add_trade("BTC_USDT", now, 100.0, 10.0, "BUY")
        self.assertEqual(vp.trim(now), 1)
        self.assertEqual(vp.coverage("BTC_USDT")["buckets"], 1)

    def test_hard_cap_on_buckets(self):
        import volume_profile as vp_mod
        old = vp_mod.MAX_BUCKETS
        vp_mod.MAX_BUCKETS = 5
        try:
            vp = VolumeProfile(path="")
            for i in range(20):
                vp.add_trade("BTC_USDT", 1_700_000_000 + i * BUCKET_SEC, 100.0, 1.0, "BUY")
            self.assertEqual(vp.stats()["buckets"], 5)
        finally:
            vp_mod.MAX_BUCKETS = old


class DiskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="volprof_")
        self.path = os.path.join(self.tmp, "volume_profile.json")
        self.t = time.time() - 120

    def test_roundtrip(self):
        vp = VolumeProfile(path=self.path)
        vp.add_trade("BTC_USDT", self.t, 100.0, 10.0, "BUY")
        vp.add_trade("BTC_USDT", self.t, 200.0, 30.0, "SELL")
        self.assertTrue(vp.save(force=True))
        other = VolumeProfile(path=self.path)
        self.assertEqual(other.load(), 1)
        self.assertAlmostEqual(other.bucket_vwap("BTC_USDT", self.t), 175.0, places=6)
        self.assertAlmostEqual(other.side_ratio("BTC_USDT", self.t, self.t + 300),
                               0.25, places=6)

    def test_stale_slots_do_not_come_back(self):
        vp = VolumeProfile(path=self.path, keep_hours=1)
        vp.add_trade("BTC_USDT", time.time() - 10 * 3600, 100.0, 10.0, "BUY")
        vp.save(force=True)
        other = VolumeProfile(path=self.path, keep_hours=1)
        self.assertEqual(other.load(), 0)

    def test_empty_state_does_not_wipe_the_file(self):
        """Пустой профиль не пишется поверх накопленного (см. диск-защиту)."""
        vp = VolumeProfile(path=self.path)
        vp.add_trade("BTC_USDT", self.t, 100.0, 10.0, "BUY")
        self.assertTrue(vp.save(force=True))
        with open(self.path, encoding="utf-8") as f:
            before = f.read()
        blank = VolumeProfile(path=self.path)     # пустая память — как у сервера
        self.assertFalse(blank.save(force=True))
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(VolumeProfile(path=self.path).load(), 1)

    def test_missing_file_is_not_an_error(self):
        vp = VolumeProfile(path=os.path.join(self.tmp, "нет-такого.json"))
        self.assertEqual(vp.load(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
