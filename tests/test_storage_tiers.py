"""Ярусы хранения истории: HOT сырые дни / WARM свёртки / COLD gzip.

Что проверяем:
* минутные свёртки строятся из событий (long/short по монетам), переживают
  flush и повторное открытие хранилища;
* ряды 1m/5m/15m из минутных свёрток совпадают с честным ручным подсчётом;
* удержание по ярусам: сырой день умирает по HOT, свёртки живут до WARM;
* COLD: день старше HOT пакуется в gzip, читается прозрачно (iter_events и
  query видят события из .gz), удаляется по COLD-границе;
* ручки /api/liq/series (tf 1m..1d, ошибки 400) и /api/liq/heatmap;
* stats() показывает ярусы.

Запуск:  python3 tests/test_storage_tiers.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="tiers_")
os.environ.setdefault("LIQSCOPE_ACCOUNTS_DB", os.path.join(TMP, "accounts.db"))
os.environ.setdefault("LIQSCOPE_HISTORY_FILE", os.path.join(TMP, "liq.jsonl"))
os.environ.setdefault("LIQSCOPE_DIGEST_FILE", os.path.join(TMP, "digests.json"))
os.environ.setdefault("LIQSCOPE_MAIL_DIR", os.path.join(TMP, "mail"))
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
os.environ.setdefault("LIQSCOPE_DEMO", "0")
os.environ.setdefault("LIQSCOPE_RATE_LIMIT", "0")

from history import HistoryStore, day_key  # noqa: E402


def make_event(i, ts, symbol="BTC_USDT", usd=1000.0, side="SELL"):
    return {"id": f"e{i}", "symbol": symbol, "exchange": "binance",
            "side": side, "price": 1.0, "qty": usd, "usd": usd,
            "timestamp": ts}


class MinuteRollups(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="min_roll_")
        self.base = os.path.join(self.dir, "liq.jsonl")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_rollup_flush_reload(self):
        store = HistoryStore(self.base, warm_days=90)
        t0 = time.time() - 60
        events = [make_event(i, t0 + i * 30) for i in range(4)]
        events += [make_event(100 + i, t0 + i * 30, "ETH_USDT", 500.0, "BUY")
                   for i in range(4)]
        store.add_many(events)
        store.flush(force=True)
        cells = store._load_day_minutes(day_key(t0))
        self.assertTrue(cells, "минутные свёртки не построились")
        total_usd = sum(c["usd"] for c in cells.values())
        total_n = sum(c["n"] for c in cells.values())
        self.assertAlmostEqual(total_usd, 4 * 1000.0 + 4 * 500.0, delta=0.01)
        self.assertEqual(total_n, 8)
        # long/short в терминах выбитых: SELL = лонг
        longs = sum(c["long"] for c in cells.values())
        shorts = sum(c["short"] for c in cells.values())
        self.assertAlmostEqual(longs, 4000.0, delta=0.01)
        self.assertAlmostEqual(shorts, 2000.0, delta=0.01)
        # перезапуск: свёртки читаются с диска
        store2 = HistoryStore(self.base)
        cells2 = store2._load_day_minutes(day_key(t0))
        self.assertAlmostEqual(sum(c["usd"] for c in cells2.values()),
                               total_usd, delta=0.01)
        btc = sum(v["usd"] for c in cells2.values()
                  for s, v in (c.get("sym") or {}).items() if s == "BTC_USDT")
        self.assertAlmostEqual(btc, 4000.0, delta=0.01)

    def test_series_minutes_steps(self):
        store = HistoryStore(self.base)
        # выравниваем старт по 15-минутной сетке: границы шагов детерминированы
        t0 = (int(time.time()) // 900) * 900 - 3600
        # три монеты-минуты: 4 события в минуту, 15 минут подряд
        events = []
        for m in range(15):
            for i in range(4):
                events.append(make_event(m * 4 + i, t0 + m * 60 + i))
        store.add_many(events)
        for step, expect_points in ((1, 15), (5, 3), (15, 1)):
            data = store.series_minutes(t0 - 60, t0 + 16 * 60, step_min=step)
            self.assertEqual(len(data["points"]), expect_points,
                             f"шаг {step}m: точек {len(data['points'])}")
            usd = sum(p["usd"] for p in data["points"])
            self.assertAlmostEqual(usd, 60 * 1000.0, delta=0.01,
                                   msg=f"шаг {step}m: сумма неверна")
            n = sum(p["n"] for p in data["points"])
            self.assertEqual(n, 60)
        # фильтр по монете: верхние итоги остаются полными (как в часовых
        # series), чужие монеты не попадают в поэлементный sym-словарь
        data = store.series_minutes(t0 - 60, t0 + 16 * 60,
                                    symbols=["ETH_USDT"], step_min=1)
        self.assertEqual(data["symbols"], [])
        for p in data["points"]:
            self.assertNotIn("BTC_USDT", p["sym"])


class TierRetention(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tiers_ret_")
        self.base = os.path.join(self.dir, "liq.jsonl")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.now = time.time()

    def _mkday(self, store, day):
        ts = time.mktime(time.strptime(day, "%Y-%m-%d")) + 3600
        store.add_many([make_event(1, ts)])
        store.flush(force=True)
        return ts

    def test_hot_dies_warm_lives(self):
        store = HistoryStore(self.base, ttl_hours=24 * 31,
                             hot_days=3, warm_days=90)
        old = day_key(self.now - 10 * 86400)
        fresh = day_key(self.now - 86400)
        self._mkday(store, old)
        self._mkday(store, fresh)
        removed = store.cleanup(self.now)
        self.assertEqual(removed, 1, "старый сырой день не удалён")
        self.assertFalse(os.path.exists(store.shard_path(old)))
        self.assertTrue(os.path.exists(store.shard_path(fresh)),
                        "свежий сырой день удалён зря")
        # свёртки старого дня живут: WARM шире HOT
        self.assertTrue(os.path.exists(store.hours_path(old)),
                        "часовая свёртка старого дня умерла вместе с сырьём")
        self.assertTrue(os.path.exists(store.minutes_path(old)),
                        "минутная свёртка старого дня умерла вместе с сырьём")

    def test_warm_cutoff_removes_aggregates(self):
        store = HistoryStore(self.base, hot_days=3, warm_days=10)
        ancient = day_key(self.now - 30 * 86400)
        self._mkday(store, ancient)
        store.cleanup(self.now)
        self.assertFalse(os.path.exists(store.hours_path(ancient)))
        self.assertFalse(os.path.exists(store.minutes_path(ancient)))
        self.assertFalse(os.path.exists(store.shard_path(ancient)))

    def test_cold_gzip_keeps_readable(self):
        store = HistoryStore(self.base, hot_days=3, warm_days=90, cold_days=30)
        old = day_key(self.now - 10 * 86400)
        ts = self._mkday(store, old)
        removed = store.cleanup(self.now)
        # день не удалён, а упакован
        self.assertEqual(removed, 0)
        self.assertFalse(os.path.exists(store.shard_path(old)))
        self.assertTrue(os.path.exists(store.shard_path(old) + ".gz"))
        # чтение прозрачно: события видны и из gzip
        evs = list(store.iter_events(ts - 60, ts + 60))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["id"], "e1")
        rows = store.query(ts - 60, ts + 60)
        self.assertEqual(len(rows), 1)
        # за COLD-границей gzip удаляется
        store2 = HistoryStore(self.base, hot_days=3, warm_days=90, cold_days=5)
        removed = store2.cleanup(self.now)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(os.path.exists(store2.shard_path(old) + ".gz"))

    def test_compress_is_atomic_and_idempotent(self):
        store = HistoryStore(self.base, hot_days=31, warm_days=90)
        day = day_key(self.now - 86400)
        ts = self._mkday(store, day)
        self.assertTrue(store.compress_shard(day))
        self.assertTrue(store.compress_shard(day), "повторная упаковка падает")
        self.assertEqual(len(list(store.iter_events(ts - 60, ts + 60))), 1)
        self.assertEqual(store._compressed, 1)

    def test_stats_reports_tiers(self):
        store = HistoryStore(self.base, hot_days=7, warm_days=90, cold_days=60)
        self._mkday(store, day_key(self.now - 86400))
        st = store.stats()
        tiers = st["tiers"]
        self.assertEqual(tiers["hot_days_retention"], 7)
        self.assertEqual(tiers["warm_days_retention"], 90)
        self.assertEqual(tiers["cold_days_retention"], 60)
        self.assertEqual(tiers["hot_files"], 1)
        self.assertEqual(tiers["warm_minute_files"], 1)
        self.assertGreater(tiers["bytes_hot"], 0)
        self.assertGreater(tiers["bytes_warm"], 0)


class SeriesApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tiers_api_")
        os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(self.dir, "liq.jsonl")
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq.jsonl")
        import importlib
        import server
        importlib.reload(server)

    def test_series_and_heatmap_endpoints(self):
        import server
        t0 = (int(time.time()) // 60) * 60 - 600
        events = [make_event(i, t0 + i * 60, "BTC_USDT",
                             1000.0 if i % 2 else 2000.0,
                             "SELL" if i % 2 else "BUY") for i in range(10)]
        server.HIST.add_many(events)
        server.HIST.flush(force=True)
        from fastapi.testclient import TestClient
        with TestClient(server.app) as c:
            r = c.get("/api/liq/series?tf=1m&hours=1&symbol=BTC_USDT")
            self.assertEqual(r.status_code, 200)
            d = r.json()
            self.assertEqual(d["tf"], "1m")
            self.assertTrue(d["points"])
            usd = sum(p["usd"] for p in d["points"])
            self.assertAlmostEqual(usd, 15000.0, delta=0.01)
            lng = sum(p["long"] for p in d["points"])
            sht = sum(p["short"] for p in d["points"])
            self.assertAlmostEqual(lng, 5000.0, delta=0.01)
            self.assertAlmostEqual(sht, 10000.0, delta=0.01)
            # 15m шаг складывает пять минутных точек
            r15 = c.get("/api/liq/series?tf=15m&hours=1&symbol=BTC_USDT")
            self.assertEqual(r15.status_code, 200)
            self.assertAlmostEqual(sum(p["usd"] for p in r15.json()["points"]),
                                   15000.0, delta=0.01)
            # часовой шаг
            rh = c.get("/api/liq/series?tf=1h&hours=24")
            self.assertEqual(rh.status_code, 200)
            self.assertTrue(rh.json()["points"])
            # неверный tf — 400
            bad = c.get("/api/liq/series?tf=2m")
            self.assertEqual(bad.status_code, 400)
            # тепловая карта
            hm = c.get("/api/liq/heatmap?days=7&symbol=BTC_USDT")
            self.assertEqual(hm.status_code, 200)
            h = hm.json()
            self.assertIn("cells", h)
            self.assertGreaterEqual(h["max_usd"], 0)
            total_usd = sum(c["usd"] for row in h["cells"]
                            for c in row["hours"].values())
            self.assertAlmostEqual(total_usd, 15000.0, delta=0.01)
            total_n = sum(c["n"] for row in h["cells"]
                          for c in row["hours"].values())
            self.assertEqual(total_n, 10)


if __name__ == "__main__":
    unittest.main()
