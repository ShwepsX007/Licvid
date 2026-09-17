"""Тесты минутного потока по всем монетам (flow_feed.FlowFeed).

Что проверяем: лента «ВСЕ» в CVD/OI раньше строилась по свечам графика, из-за
чего при переключении на «все» оставалась прежняя монета. Теперь строки
собирает FlowFeed — по каждой монете и каждой минуте ликвидации, объём, CVD и
OI. Здесь проверяется арифметика: окна, сортировка, сводка «кто куда».

Запуск: /tmp/venv/bin/python tests/test_flow_feed.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_feed import FlowFeed, minute_of   # noqa: E402


NOW = 1_789_596_850.0          # 17.09.2026, середина минуты


class TestMinuteOf(unittest.TestCase):
    def test_rounds_down(self):
        self.assertEqual(minute_of(NOW), int(NOW // 60 * 60))
        self.assertEqual(minute_of(0), 0)
        self.assertEqual(minute_of("bad"), 0)


class TestFlowFeed(unittest.TestCase):
    def test_liquidations_split_by_side(self):
        f = FlowFeed()
        # SELL = вынесли лонг, BUY = вынесли шорт (термины ордера биржи)
        f.add_liq({"symbol": "BTC_USDT", "side": "SELL", "usd": 120000,
                   "timestamp": NOW})
        f.add_liq({"symbol": "BTC_USDT", "side": "BUY", "usd": 80000,
                   "timestamp": NOW})
        row = f.rows("liq", 5, NOW)[0]
        self.assertEqual(row["symbol"], "BTC_USDT")
        self.assertEqual(row["liq_count"], 2)
        self.assertAlmostEqual(row["liq_long"], 120000, places=2)
        self.assertAlmostEqual(row["liq_short"], 80000, places=2)

    def test_bad_events_ignored(self):
        f = FlowFeed()
        f.add_liq({"symbol": "", "usd": 100, "timestamp": NOW})
        f.add_liq({"symbol": "BTC_USDT", "usd": 0, "timestamp": NOW})
        f.add_liq({"symbol": "BTC_USDT", "usd": "xxx", "timestamp": NOW})
        f.add_liq({"symbol": "BTC_USDT", "usd": 100, "timestamp": 0})
        self.assertEqual(f.known(), 0)
        self.assertEqual(f.rows("liq", 5, NOW), [])

    def test_cvd_signed_and_volume(self):
        f = FlowFeed()
        f.add_trade("ETH_USDT", NOW, 500000)
        f.add_trade("ETH_USDT", NOW, -200000)
        f.add_volume("ETH_USDT", NOW, 1_000_000)
        row = f.rows("cvd", 5, NOW)[0]
        self.assertAlmostEqual(row["cvd"], 300000, places=2)
        self.assertAlmostEqual(row["cvd_share"], 30.0, places=1)
        self.assertAlmostEqual(row["vol"], 1_000_000, places=2)

    def test_oi_delta_inside_minute(self):
        f = FlowFeed()
        f.add_oi("SOL_USDT", NOW, 100_000_000)
        f.add_oi("SOL_USDT", NOW, 104_000_000)
        row = f.rows("oi", 5, NOW)[0]
        # изменение считаем внутри окна: последнее минус первое
        self.assertAlmostEqual(row["oi_delta"], 4_000_000, places=2)
        self.assertAlmostEqual(row["oi_usd"], 104_000_000, places=2)

    def test_rows_only_for_wanted_kind(self):
        f = FlowFeed()
        f.add_liq({"symbol": "BTC_USDT", "side": "SELL", "usd": 5e6,
                   "timestamp": NOW})
        f.add_trade("ETH_USDT", NOW, 250000)
        self.assertEqual([r["symbol"] for r in f.rows("cvd", 5, NOW)], ["ETH_USDT"])
        self.assertEqual([r["symbol"] for r in f.rows("liq", 5, NOW)], ["BTC_USDT"])
        # у BTC CVD нет — выдумывать ноль нельзя
        self.assertEqual(f.rows("oi", 5, NOW), [])

    def test_window_cuts_old_minutes(self):
        f = FlowFeed(keep_min=60)
        f.add_trade("BTC_USDT", NOW - 40 * 60, 1e9)      # давно
        f.add_trade("BTC_USDT", NOW - 60, 250000)        # в окне 5 минут
        row = f.rows("cvd", 5, NOW)[0]
        self.assertAlmostEqual(row["cvd"], 250000, places=2)

    def test_sorted_by_size_and_symbols_all(self):
        f = FlowFeed()
        f.add_trade("BTC_USDT", NOW, 100000)
        f.add_trade("ETH_USDT", NOW, -900000)
        f.add_trade("SOL_USDT", NOW, 400000)
        rows = f.rows("cvd", 5, NOW)
        self.assertEqual([r["symbol"] for r in rows],
                         ["ETH_USDT", "SOL_USDT", "BTC_USDT"])
        self.assertEqual(sorted(f.symbols()), ["BTC_USDT", "ETH_USDT", "SOL_USDT"])

    def test_keep_min_trims_memory(self):
        f = FlowFeed(keep_min=10)
        for i in range(30):
            f.add_trade("BTC_USDT", NOW - i * 60, 1000)
        self.assertLessEqual(len(f._by_sym["BTC_USDT"]), 15)

    def test_price_and_price_in_row(self):
        f = FlowFeed()
        f.set_price("BTC_USDT", 61000)
        f.add_trade("BTC_USDT", NOW, 1000)
        self.assertEqual(f.rows("cvd", 5, NOW)[0]["price"], 61000)

    def test_summary_directions(self):
        f = FlowFeed()
        # лонги сносило по BTC, шорты — по ETH
        f.add_liq({"symbol": "BTC_USDT", "side": "SELL", "usd": 9e6,
                   "timestamp": NOW})
        f.add_liq({"symbol": "ETH_USDT", "side": "BUY", "usd": 7e6,
                   "timestamp": NOW})
        # продавцы: SOL, покупатели: BNB
        f.add_trade("SOL_USDT", NOW, -5e6)
        f.add_trade("BNB_USDT", NOW, 3e6)
        # OI растёт у XRP, падает у DOGE
        f.add_oi("XRP_USDT", NOW, 1e8)
        f.add_oi("XRP_USDT", NOW, 1.2e8)
        f.add_oi("DOGE_USDT", NOW, 2e8)
        f.add_oi("DOGE_USDT", NOW, 1.5e8)
        s = f.summary(60, NOW)
        self.assertEqual(s["liquidated_long"], ["BTC_USDT"])
        self.assertEqual(s["liquidated_short"], ["ETH_USDT"])
        self.assertEqual(s["cvd_sellers"], ["SOL_USDT"])
        self.assertEqual(s["cvd_buyers"], ["BNB_USDT"])
        self.assertEqual(s["oi_up"], ["XRP_USDT"])
        self.assertEqual(s["oi_down"], ["DOGE_USDT"])
        self.assertAlmostEqual(s["liq_usd"], 1.6e7, places=0)

    def test_symbol_series(self):
        m = minute_of(NOW)
        f = FlowFeed()
        f.add_trade("BTC_USDT", m - 120, -5)
        f.add_trade("BTC_USDT", m, 10)
        f.add_oi("BTC_USDT", m - 60, 1e8)              # база прошлой минуты
        f.add_oi("BTC_USDT", m + 5, 1.05e8)            # внутри минуты: база
        f.add_oi("BTC_USDT", m + 20, 1.1e8)            # итог минуты
        series = f.symbol_series("BTC_USDT", 5, NOW)
        self.assertEqual(len(series), 3)
        self.assertIsNone(series[0]["oi_delta"])       # нет базы — прочерк
        self.assertAlmostEqual(series[1]["oi_delta"], 0.0, places=0)
        # внутри минуты: итог минуты минус первое значение минуты (1.05e8)
        self.assertAlmostEqual(series[2]["oi_delta"], 5e6, places=0)
        self.assertAlmostEqual(series[2]["oi"], 1.1e8, places=0)

    def test_limit(self):
        f = FlowFeed()
        for i in range(30):
            f.add_trade(f"C{i}_USDT", NOW, 1000 + i)
        self.assertEqual(len(f.rows("cvd", 5, NOW, limit=5)), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
