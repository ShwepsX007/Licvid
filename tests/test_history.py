"""Тесты месячной истории рынка (history.HistoryStore).

Раньше история жила сутки: событие в памяти + один JSONL, который при
разрастании переписывался целиком. Теперь события лежат по дням, рядом —
часовые свёртки (ликвидации по монетам и биржам, CVD, объём), TTL — месяц.
Здесь проверяем и хранение, и агрегаты, которыми будут пользоваться сервисы
(корреляции, сторож пампов, месячные графики на сайте).

Запуск: /tmp/venv/bin/python tests/test_history.py
"""
import calendar
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from history import HistoryStore, aggregate_hours, day_key, hour_start, next_day  # noqa: E402

# 17.09.2026, 10:17:30 UTC — середина дня, чтобы сдвиги на часы не перескакивали
# через границу суток (дни истории считаются по UTC)
NOW = float(calendar.timegm((2026, 9, 17, 10, 17, 30, 0, 0, 0)))
HOUR = 3600


def ev(symbol="BTC_USDT", usd=100000, side="SELL", exch="binance",
       ts=None, n=0):
    return {"id": f"e{n}", "symbol": symbol, "exchange": exch, "side": side,
            "position": "LONG" if side == "SELL" else "SHORT",
            "price": 50000.0, "qty": usd / 50000.0, "usd": float(usd),
            "timestamp": float(NOW if ts is None else ts)}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="liqhist_")
        self.path = os.path.join(self.dir, "liq_history.jsonl")
        self.store = HistoryStore(self.path, ttl_hours=24 * 31)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestDays(StoreCase):
    def test_day_and_hour_keys(self):
        self.assertEqual(day_key(0), "1970-01-01")
        self.assertEqual(day_key(NOW), "2026-09-17")
        self.assertEqual(hour_start(NOW) % HOUR, 0)
        self.assertEqual(next_day("2026-09-17"), "2026-09-18")
        self.assertEqual(next_day("2026-12-31"), "2027-01-01")
        self.assertEqual(next_day("мусор"), "мусор")   # не падаем

    def test_events_go_to_daily_shard(self):
        for i in range(3):
            self.store.add(ev(usd=1000 + i, n=i))
        path = self.store.shard_path(day_key(NOW))
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(len([ln for ln in f if ln.strip()]), 3)

    def test_read_back_events(self):
        self.store.add(ev(symbol="BTC_USDT", usd=50000, n=1))
        self.store.add(ev(symbol="ETH_USDT", usd=70000, n=2))
        rows = self.store.query(NOW - 60, NOW + 60)
        self.assertEqual(len(rows), 2)
        only = self.store.query(NOW - 60, NOW + 60, symbol="ETH_USDT")
        self.assertEqual([r["symbol"] for r in only], ["ETH_USDT"])
        big = self.store.query(NOW - 60, NOW + 60, min_usd=60000)
        self.assertEqual([r["usd"] for r in big], [70000.0])

    def test_query_outside_window_empty(self):
        self.store.add(ev(n=1))
        self.assertEqual(self.store.query(NOW + 10, NOW + 20), [])

    def test_events_are_newest_first_by_default(self):
        self.store.add(ev(ts=NOW - 100, n=1))
        self.store.add(ev(ts=NOW, n=2))
        rows = self.store.query(NOW - 200, NOW + 1)
        self.assertEqual(rows[0]["id"], "e2")
        rows = self.store.query(NOW - 200, NOW + 1, newest_first=False)
        self.assertEqual(rows[0]["id"], "e1")

    def test_broken_lines_skipped(self):
        self.store.add(ev(n=1))
        with open(self.store.shard_path(day_key(NOW)), "a", encoding="utf-8") as f:
            f.write("{битая строка\n")
            f.write("\n")
            f.write('{"no_id": 1}\n')
        self.assertEqual(len(self.store.query(NOW - 60, NOW + 60)), 1)


class TestRollups(StoreCase):
    def test_hour_cell_totals(self):
        self.store.add(ev(usd=100000, side="SELL", exch="binance", ts=NOW, n=1))
        self.store.add(ev(usd=40000, side="BUY", exch="bybit", ts=NOW + 30, n=2))
        cells = self.store.hours_range(NOW - 60, NOW + 60)
        self.assertEqual(len(cells), 1)
        h, cell = cells[0]
        self.assertEqual(int(cell["liq_count"]), 2)
        self.assertAlmostEqual(cell["liq_long"], 100000, places=2)
        self.assertAlmostEqual(cell["liq_short"], 40000, places=2)
        self.assertAlmostEqual(cell["exch"]["binance"]["usd"], 100000, places=2)
        self.assertEqual(cell["max_symbol"], "BTC_USDT")

    def test_flow_into_hour_cell(self):
        self.store.add_flow("BTC_USDT", NOW, cvd=250000, vol=1000000)
        self.store.add_flow("BTC_USDT", NOW + 10, cvd=-50000)
        cell = self.store.hours_range(NOW - 60, NOW + 60)[0][1]
        self.assertAlmostEqual(cell["cvd"], 200000, places=2)
        self.assertAlmostEqual(cell["vol"], 1000000, places=2)
        self.assertAlmostEqual(cell["sym"]["BTC_USDT"]["cvd"], 200000, places=2)

    def test_totals_and_days(self):
        for i in range(4):
            self.store.add(ev(usd=10000 * (i + 1), ts=NOW + i * 3600, n=i))
        self.store.add_flow("ETH_USDT", NOW, cvd=-300000, vol=2000000)
        agg = self.store.totals(NOW - 3600, NOW + 5 * 3600)
        self.assertEqual(agg["count"], 4)
        self.assertAlmostEqual(agg["usd"], 100000, places=2)   # 10+20+30+40 тыс
        self.assertAlmostEqual(agg["cvd"], -300000, places=2)
        self.assertEqual(agg["by_exchange"]["binance"], 100000)
        self.assertEqual(agg["max_symbol"], "BTC_USDT")
        days = self.store.days(NOW - 3600, NOW + 5 * 3600)
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0]["day"], day_key(NOW))

    def test_totals_by_symbol(self):
        self.store.add(ev(symbol="BTC_USDT", usd=90000, n=1))
        self.store.add(ev(symbol="ETH_USDT", usd=10000, n=2))
        agg = self.store.totals(NOW - 60, NOW + 60, symbol="ETH_USDT")
        self.assertEqual(agg["count"], 1)
        self.assertAlmostEqual(agg["usd"], 10000, places=2)

    def test_series_steps(self):
        for i in range(3):
            self.store.add(ev(usd=1000, ts=NOW + i * HOUR, n=i))
            self.store.add_flow("BTC_USDT", NOW + i * HOUR, vol=500.0)
        data = self.store.series(NOW - 60, NOW + 3 * HOUR, step_hours=2)
        self.assertEqual(data["step_hours"], 2)
        self.assertEqual(len(data["points"]), 2)          # 3 часа в шагах по 2
        self.assertAlmostEqual(data["points"][0]["vol"], 1000.0, places=2)


class TestPersistence(StoreCase):
    def test_rollups_survive_restart(self):
        self.store.add(ev(usd=12345, ts=NOW, n=1))
        self.store.add_flow("BTC_USDT", NOW, cvd=1000, vol=2000)
        self.assertEqual(self.store.flush(), 1)
        again = HistoryStore(self.path, ttl_hours=24 * 31)
        cells = again.hours_range(NOW - 60, NOW + 60)
        self.assertEqual(len(cells), 1)
        self.assertEqual(int(cells[0][1]["liq_count"]), 1)
        self.assertAlmostEqual(cells[0][1]["vol"], 2000.0, places=2)

    def test_cleanup_removes_old_days(self):
        old_ts = NOW - 40 * 24 * HOUR
        self.store.add(ev(ts=old_ts, n=1))
        self.store.add(ev(ts=NOW, n=2))
        self.assertTrue(os.path.exists(self.store.shard_path(day_key(old_ts))))
        removed = self.store.cleanup(NOW)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(os.path.exists(self.store.shard_path(day_key(old_ts))))
        self.assertTrue(os.path.exists(self.store.shard_path(day_key(NOW))))

    def test_month_kept(self):
        """31 сутки — тот самый «минимум месяц» из задания."""
        month_ago = NOW - 30 * 24 * HOUR
        self.store.add(ev(ts=month_ago, n=1))
        self.assertEqual(self.store.cleanup(NOW), 0)
        rows = self.store.query(month_ago - 60, NOW)
        self.assertEqual(len(rows), 1)

    def test_stats(self):
        self.store.add(ev(n=1))
        st = self.store.stats()
        self.assertEqual(st["days_on_disk"], 1)
        self.assertEqual(st["first_day"], day_key(NOW))
        self.assertGreater(st["bytes"], 0)
        self.assertEqual(st["ttl_hours"], 24 * 31)

    def test_disabled_store_is_silent(self):
        st = HistoryStore("", ttl_hours=24)
        self.assertFalse(st.add(ev(n=1)))
        st.add_flow("BTC_USDT", NOW, cvd=1)
        self.assertEqual(st.query(NOW - 60, NOW + 60), [])
        self.assertEqual(st.cleanup(), 0)

    def test_bad_events_ignored(self):
        self.assertFalse(self.store.add({"symbol": "BTC_USDT", "usd": 1}))
        self.assertFalse(self.store.add(ev(ts=0, n=1)))
        self.assertEqual(self.store.query(0, NOW + 60), [])


class TestAggregateHelper(unittest.TestCase):
    def test_aggregate_empty(self):
        agg = aggregate_hours([])
        self.assertEqual(agg["count"], 0)
        self.assertEqual(agg["usd"], 0.0)
        self.assertIsNone(agg["cvd_share"])

    def test_cvd_share(self):
        cells = [(hour_start(NOW), {"liq_usd": 0, "liq_count": 0, "liq_long": 0,
                                    "liq_short": 0, "cvd": 250.0, "vol": 1000.0,
                                    "sym": {}, "exch": {}})]
        agg = aggregate_hours(cells)
        self.assertAlmostEqual(agg["cvd_share"], 25.0, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
