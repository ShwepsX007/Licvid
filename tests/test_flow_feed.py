"""Тесты минутного потока по всем монетам (flow_feed.FlowFeed).

Что проверяем: лента «ВСЕ» в CVD/OI раньше строилась по свечам графика, из-за
чего при переключении на «все» оставалась прежняя монета. Теперь строки
собирает FlowFeed — по каждой монете и каждой минуте ликвидации, объём, CVD и
OI. Здесь проверяется арифметика: окна, сортировка, сводка «кто куда».

Запуск: /tmp/venv/bin/python tests/test_flow_feed.py
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_feed import MINUTE, FlowFeed, minute_of   # noqa: E402


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

    def test_by_slot_folds_minutes(self):
        """Минутки → слоты постов: объём и CVD складываются, границы честные."""
        f = FlowFeed()
        slot = 900                                  # 15 минут, как в постах
        base = int(NOW // slot * slot)
        now = base + slot + 300                     # заглядываем на два слота
        f.add_trade("BTC_USDT", base + 60, 100.0)
        f.add_trade("BTC_USDT", base + 120, -40.0)
        f.add_volume("BTC_USDT", base + 120, 500.0)
        f.add_trade("BTC_USDT", base + slot + 60, 30.0)
        f.add_volume("BTC_USDT", base + slot + 60, 700.0)
        f.add_trade("ETH_USDT", base + 60, -5.0)
        out = f.by_slot(slot, 2, now)
        self.assertEqual(sorted(out), ["BTC_USDT", "ETH_USDT"])
        btc = out["BTC_USDT"]
        self.assertEqual(sorted(btc), [base, base + slot])
        self.assertAlmostEqual(btc[base]["cvd"], 60.0, places=6)
        self.assertAlmostEqual(btc[base]["vol"], 500.0, places=6)
        self.assertTrue(btc[base]["has_cvd"])
        self.assertAlmostEqual(btc[base + slot]["cvd"], 30.0, places=6)
        self.assertAlmostEqual(btc[base + slot]["vol"], 700.0, places=6)

    def test_by_slot_without_cvd_keeps_volume(self):
        """Объём без тейкер-делты не пропадает: долю CVD по слоту не считаем."""
        f = FlowFeed()
        slot = 900
        base = int(NOW // slot * slot)
        f.add_volume("BTC_USDT", base + 60, 400.0)
        out = f.by_slot(slot, 1, NOW)
        self.assertAlmostEqual(out["BTC_USDT"][base]["vol"], 400.0, places=6)
        self.assertFalse(out["BTC_USDT"][base]["has_cvd"])
        self.assertEqual(out["BTC_USDT"][base]["cvd"], 0.0)

    def test_by_slot_skips_old_minutes(self):
        """За окном слотов минут нет: в блок поста не подмешивается прошлый день."""
        f = FlowFeed()
        slot = 900
        base = int(NOW // slot * slot)
        f.add_trade("BTC_USDT", base - 5 * slot, 999.0)
        f.add_trade("BTC_USDT", base + 60, 10.0)
        out = f.by_slot(slot, 2, NOW)
        self.assertEqual(sorted(out["BTC_USDT"]), [base])

    def test_limit(self):
        f = FlowFeed()
        for i in range(30):
            f.add_trade(f"C{i}_USDT", NOW, 1000 + i)
        self.assertEqual(len(f.rows("cvd", 5, NOW, limit=5)), 5)




class WindowRangeTest(unittest.TestCase):
    """Окно агрегата обходится диапазоном минут, а не сортировкой всех ключей.

    ``flow_snapshot`` зовёт ``rows()`` трижды (cvd, oi, liq) плюс ``summary`` —
    ещё три прохода: шесть обходов на каждую монету на каждом WS-подключении и
    каждой рассылке ленты «ВСЕ». Раньше каждый обход сортировал ВСЕ минуты
    монеты (``keep_min`` = 120) ради пяти нужных. Ключи выровнены по минутам,
    поэтому достаточно пройтись по диапазону — порядок тот же, суммы и
    «последняя минута» не меняются.
    """

    def _ref_window(self, f, sym, window_min, now):
        """Прежняя реализация: sorted() по всем минутам и отбрасывание лишних."""
        rows = f._by_sym.get(sym) or {}
        first = minute_of(now) - (max(1, window_min) - 1) * MINUTE
        agg = {"liq_long": 0.0, "liq_short": 0.0, "liq_count": 0, "vol": 0.0,
               "cvd": 0.0, "has_cvd": False, "oi": None, "oi_from": None,
               "minutes": 0, "last": 0}
        for m in sorted(rows):
            if m < first:
                continue
            c = rows[m]
            agg["minutes"] += 1
            agg["last"] = m
            agg["liq_long"] += c.get("liq_long") or 0.0
            agg["liq_short"] += c.get("liq_short") or 0.0
            agg["liq_count"] += int(c.get("liq_count") or 0)
            agg["vol"] += c.get("vol") or 0.0
            if c.get("has_cvd"):
                agg["cvd"] += c.get("cvd") or 0.0
                agg["has_cvd"] = True
            if c.get("oi") is not None:
                if agg["oi_from"] is None:
                    agg["oi_from"] = c.get("oi_from")
                agg["oi"] = c.get("oi")
        if not agg["minutes"]:
            return None
        return agg

    def _fill(self, f, nmin, base=NOW):
        for i in range(nmin):
            ts = base - (nmin - 1 - i) * MINUTE + 7
            f.add_liq({"symbol": "BTC_USDT", "timestamp": ts, "side": "SELL",
                       "usd": 1000.0 + i, "price": 100.0 + i})
            f.add_trade("BTC_USDT", ts, 5000.0 + i)
            f.add_trade("BTC_USDT", ts, -(4000.0 + i))
            f.add_volume("BTC_USDT", ts, 9000.0 + i)
            f.add_oi("BTC_USDT", ts, 900_000.0 + i * 10)
        return f

    def test_window_matches_sorted_walk(self):
        """Диапазон даёт ровно тот же агрегат, что прежняя сортировка."""
        f = self._fill(FlowFeed(), 120)
        for window in (1, 2, 5, 15, 60, 120, 121, 500):
            self.assertEqual(f._window("BTC_USDT", window, NOW),
                             self._ref_window(f, "BTC_USDT", window, NOW),
                             f"окно {window} мин")

    def test_window_with_gaps_and_sparse_minutes(self):
        """Дырки в минутах: отсутствующие ключи просто пропускаются."""
        f = FlowFeed()
        for k in (0, 3, 17, 40, 119):                    # редкие минуты
            ts = NOW - k * MINUTE + 5
            f.add_liq({"symbol": "ETH_USDT", "timestamp": ts, "side": "BUY",
                       "usd": 100.0 + k, "price": 50.0})
            f.add_oi("ETH_USDT", ts, 10_000.0 + k)
        for window in (1, 5, 18, 60, 120):
            self.assertEqual(f._window("ETH_USDT", window, NOW),
                             self._ref_window(f, "ETH_USDT", window, NOW),
                             f"окно {window} мин с дырками")
        # oi_from — из ПЕРВОЙ минуты окна с OI, last — из последней
        agg = f._window("ETH_USDT", 120, NOW)
        self.assertEqual(agg["minutes"], 5)
        self.assertEqual(agg["last"], minute_of(NOW - 0 * MINUTE + 5))
        self.assertEqual(agg["oi_from"], 10_000.0 + 119)

    def test_window_sees_minutes_ahead_of_local_clock(self):
        """Биржевые часы впереди наших: такие минуты не теряются."""
        f = FlowFeed()
        self._fill(f, 10)
        for k in (1, 2, 3):                               # будущее относительно NOW
            ts = NOW + k * MINUTE
            f.add_liq({"symbol": "SOL_USDT", "timestamp": ts, "side": "SELL",
                       "usd": 10.0 * k, "price": 20.0})
        for window in (5, 60):
            self.assertEqual(f._window("SOL_USDT", window, NOW),
                             self._ref_window(f, "SOL_USDT", window, NOW),
                             f"окно {window} мин с будущими минутами")

    def test_absurd_future_minute_does_not_widen_the_walk(self):
        """Метка на годы вперёд не растягивает обход до миллиона шагов."""
        f = FlowFeed()
        self._fill(f, 5)
        far = NOW + 400 * 24 * 3600
        f.add_trade("DOGE_USDT", far, 1.0)
        f.add_trade("DOGE_USDT", NOW, 2.0)
        t0 = time.perf_counter()
        for _ in range(200):
            f._window("DOGE_USDT", 5, NOW)
        spent = time.perf_counter() - t0
        self.assertLess(spent, 1.0, f"200 окон обошлись в {spent*1000:.0f} мс")
        # данные текущей минуты на месте
        self.assertGreater(f._window("DOGE_USDT", 5, NOW)["minutes"], 0)

    def test_top_minute_follows_pruning(self):
        """Обрезка старых минут не сбивает верхнюю границу окна."""
        f = FlowFeed(keep_min=10)
        self._fill(f, 40)
        self.assertLessEqual(len(f._by_sym["BTC_USDT"]), 10 + 5)
        self.assertEqual(f._window("BTC_USDT", 5, NOW),
                         self._ref_window(f, "BTC_USDT", 5, NOW))
        self.assertEqual(f._top["BTC_USDT"], minute_of(NOW - 0 * MINUTE + 7))

    def test_unknown_symbol_and_empty_feed(self):
        f = FlowFeed()
        self.assertIsNone(f._window("NOPE_USDT", 5, NOW))
        self.assertEqual(f.rows("cvd", 5, NOW), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
