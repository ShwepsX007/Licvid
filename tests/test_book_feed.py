"""📖 Стакан: детектор стен, жизненный цикл, шейрды и маршруты.

Запуск:  python3 tests/test_book_feed.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import book_feed as bf  # noqa: E402

MID = 63000.0
STEP = MID * bf.BUCKET_REL


def _rows(n, sign, size=1000.0, extra=()):
    out = [(MID + sign * i * STEP, size) for i in range(1, n + 1)]
    out.extend(extra)
    return out


def _depths(bid_extra=(), ask_extra=(), bybit=False):
    d = {"binance": (_rows(80, -1, extra=bid_extra), _rows(80, +1, extra=ask_extra))}
    if bybit:
        d["bybit"] = (_rows(30, -1, size=600.0), _rows(30, +1, size=600.0))
    return d


class AggTests(unittest.TestCase):
    def test_aggregate_buckets_and_mid(self):
        agg = bf.aggregate_depths(_depths())
        self.assertIsNotNone(agg)
        self.assertTrue(agg["bids"], "bids empty")
        self.assertTrue(agg["asks"], "asks empty")
        self.assertLess(agg["bids"][0]["hi"], agg["mid"] + STEP)  # рядом с mid
        # цены идут от mid прочь: bids по убыванию, asks по возрастанию
        self.assertGreater(agg["bids"][0]["lo"], agg["bids"][-1]["hi"])
        self.assertLess(agg["asks"][0]["lo"], agg["asks"][-1]["lo"])

    def test_no_depth_no_agg(self):
        self.assertIsNone(bf.aggregate_depths({}))

    def test_detect_walls_sides_and_merge(self):
        # две bid-стены рядом (соседние корзины) сливаются, ask — отдельно
        agg = bf.aggregate_depths(_depths(
            bid_extra=[(MID - 12 * STEP, 400000.0), (MID - 13 * STEP, 300000.0)],
            ask_extra=[(MID + 20 * STEP, 500000.0)]))
        walls = bf.detect_walls(agg, 150000)
        self.assertEqual(len(walls), 2, walls)
        bid = next(w for w in walls if w["side"] == "bid")
        ask = next(w for w in walls if w["side"] == "ask")
        self.assertGreaterEqual(bid["usdt"], 690000)
        self.assertEqual(bid["levels"], 2)
        self.assertEqual(ask["usdt"], 501000)
        self.assertGreater(ask["px"], agg["mid"])
        self.assertLess(bid["px"], agg["mid"])

    def test_btc_thin_tick_book_still_finds_walls(self):
        """BTC: тик $0.1 при цене 60k → сотни уровней попадают в 2–3 корзины.
        Медиана таких корзин — сама стена; относительный порог 6×медианы не
        должен глушить стену, что и происходило (пустые кластеры на BTC)."""
        mid = 63000.0
        bids = [(mid - 0.1 * i, 3000.0) for i in range(1, 400)]     # ≈ $1.2M в ±$40
        asks = [(mid + 0.1 * i, 3000.0) for i in range(1, 400)]
        # плюс явная стена на одном уровне
        bids[50] = (bids[50][0], 400000.0)
        agg = bf.aggregate_depths({"binance": (bids, asks)})
        self.assertLessEqual(len(agg["bids"]), 4, "стакан обязан лечь в пару корзин")
        walls = bf.detect_walls(agg, 150000)
        self.assertTrue(any(w["side"] == "bid" and w["usdt"] >= 400000 for w in walls),
                        f"стена BTC потеряна: {walls}")

    def test_parse_depth_not_truncated(self):
        """Глубину режет окно ±2% в агрегаторе, а не счётчик уровней парсера:
        иначе у BTC 150 уровней = $15 и половина окна пустая."""
        raw = {"bids": [[str(60000 - i * 0.1), "1"] for i in range(1200)],
               "asks": [[str(60000 + i * 0.1), "1"] for i in range(1200)]}
        bids, asks = bf.parse_depth("binance", raw, {})
        self.assertEqual(len(bids), 1200)
        self.assertEqual(len(asks), 1200)

    def test_depth_urls_within_exchange_limits(self):
        """Глубина под лимиты бирж: Binance только допустимые значения и ≤500
        (вес 10, не 20), Gate — параметр limit ≤300 (не «last»), Bybit/OKX 200.
        Иначе IP улетает в 429/418 и фид глохнет — «стен нет ни на одной монете»."""
        b = bf.depth_url("binance", "BTC_USDT")
        self.assertIn("limit=500", b)
        g = bf.depth_url("gate", "SOL_USDT")
        self.assertIn("limit=300", g)
        self.assertNotIn("last=", g)
        self.assertIn("limit=200", bf.depth_url("bybit", "SOL_USDT"))
        self.assertIn("sz=200", bf.depth_url("okx", "SOL_USDT"))

    def test_binance_limit_snaps_to_allowed(self):
        old = bf.DEPTH_PER_EXCH["binance"]
        try:
            bf.DEPTH_PER_EXCH["binance"] = 300          # недопустимое → вниз до 100
            self.assertIn("limit=100", bf.depth_url("binance", "BTC_USDT"))
        finally:
            bf.DEPTH_PER_EXCH["binance"] = old

    def test_poll_defaults_respect_rate_limits(self):
        self.assertGreaterEqual(bf.POLL_SEC, 3.0)
        self.assertLessEqual(bf.MAX_SYMBOLS, 10)

    def test_small_levels_not_walls(self):
        agg = bf.aggregate_depths(_depths(bid_extra=[(MID - 10 * STEP, 40000.0)]))
        self.assertEqual(bf.detect_walls(agg, 150000), [])

    def test_exchanges_union(self):
        agg = bf.aggregate_depths(_depths(bid_extra=[(MID - 10 * STEP, 400000.0)],
                                           bybit=True))
        walls = bf.detect_walls(agg, 150000)
        self.assertEqual(walls[0]["exchs"], ["binance", "bybit"])

    def test_parse_binance_shape(self):
        data = {"bids": [["63000", "2.0"], ["62999", "1.0"]],
                "asks": [["63001", "1.5"]]}
        bids, asks = bf.parse_depth("binance", data, {})
        self.assertAlmostEqual(bids[0][1], 126000.0)
        self.assertAlmostEqual(asks[0][1], 94501.5)

    def test_parse_okx_needs_ctval(self):
        data = {"data": [{"bids": [["63000", "10", "0", "3"]],
                          "asks": [["63001", "5", "0", "1"]]}]}
        self.assertIsNone(bf.parse_depth("okx", data, {}))
        bids, asks = bf.parse_depth("okx", data, {"okx": {"ct_val": 0.01}})
        self.assertAlmostEqual(bids[0][1], 6300.0)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.feed = bf.BookFeed(self.dir, min_usd=150000, gone_after=2, ttl_days=3)
        self.sym = "BTC_USDT"
        self.now = time.time()

    def _merge_depths(self, t, bid_extra=(), ask_extra=()):
        agg = bf.aggregate_depths(_depths(bid_extra=bid_extra, ask_extra=ask_extra))
        self.feed._merge(self.sym, bf.detect_walls(agg, 150000), t)

    def test_open_grow_close(self):
        t = self.now
        self._merge_depths(t, bid_extra=[(MID - 12 * STEP, 500000.0)])
        self.assertEqual(len(self.feed.walls[self.sym]), 1)
        wall = next(iter(self.feed.walls[self.sym].values()))
        self.assertTrue(wall["live"])
        # рост — тот же id, растёт peak, полоса не «переезжает»
        self._merge_depths(t + 2, bid_extra=[(MID - 12 * STEP, 700000.0)])
        cur = next(iter(self.feed.walls[self.sym].values()))
        self.assertEqual(cur["id"], wall["id"])
        self.assertGreaterEqual(cur["peak"], 700000)
        # одиночный пропуск — жива; нет связи — пропуск не копится
        self._merge_depths(t + 4)
        self.assertTrue(next(iter(self.feed.walls[self.sym].values()))["live"])
        self.feed._merge(self.sym, [], t + 6, no_data=True)
        self.assertTrue(next(iter(self.feed.walls[self.sym].values()))["live"])
        self._merge_depths(t + 8)
        cur = next(iter(self.feed.walls[self.sym].values()))
        self.assertFalse(cur["live"])
        self.assertAlmostEqual(cur["closed"], t + 2, places=1)
        # история: закрытая полоса с длительностью
        hist = self.feed.history(self.sym, hours=1)
        self.assertEqual(hist["count"], 1)
        self.assertGreaterEqual(hist["walls"][0]["dur_s"], 1)
        # живой снимок — пусто
        self.assertEqual(self.feed.snapshot(self.sym)["walls"], [])

    def test_rate_limit_backoff_and_status(self):
        import asyncio

        class _Sess:
            pass

        f = bf.BookFeed(None)
        f._session = _Sess()

        async def boom(session, url, timeout=5.0):
            raise RuntimeError("HTTP 429 for " + url)

        orig = bf._get_json
        bf._get_json = boom
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(f._fetch_depth("binance", "BTC_USDT", {}))
            self.assertGreater(f._backoff.get("binance", 0), time.time() + 30)
            # пока пауза — к бирже не ходим вообще (быстрый отказ)
            with self.assertRaises(RuntimeError) as cm:
                asyncio.run(f._fetch_depth("binance", "BTC_USDT", {}))
            self.assertIn("backoff", str(cm.exception))
        finally:
            bf._get_json = orig
        st = f.status_summary()
        self.assertGreater(st["binance"]["backoff_s"], 0)
        self.assertIn("HTTP 429", st["binance"]["error"])
        self.assertIn("live_walls", st)
        self.assertIn("persist_ok", st)

    def test_history_hours_capped_and_wide(self):
        f = bf.BookFeed(None)
        now = time.time()
        agg = bf.aggregate_depths(_depths(bid_extra=[(MID - 12 * STEP, 400000.0)]))
        f._merge("BTC_USDT", bf.detect_walls(agg, 150000), now - 30 * 3600)
        f._merge("BTC_USDT", [], now - 30 * 3600 + 20)   # miss…
        for _ in range(bf.GONE_AFTER):
            f._merge("BTC_USDT", [], now - 30 * 3600 + 30)
        # стена закрылась 30 часов назад: в 6-часовом окне её нет, в 48-часовом есть
        self.assertEqual(f.history("BTC_USDT", 6)["count"], 0)
        self.assertGreater(f.history("BTC_USDT", 48)["count"], 0)
        # окно не шире TTL хранения
        self.assertLessEqual(f.history("BTC_USDT", 10000)["hours"], bf.HISTORY_TTL_DAYS * 24)

    def test_persist_roundtrip(self):
        t = self.now
        self._merge_depths(t, bid_extra=[(MID - 12 * STEP, 500000.0)])
        self._merge_depths(t + 2, bid_extra=[(MID - 12 * STEP, 800000.0)])
        self._merge_depths(t + 4)
        self._merge_depths(t + 6)                       # закрытие
        orig = next(iter(self.feed.walls[self.sym].values()))
        files = [f for f in os.listdir(self.dir) if f.endswith(".jsonl")]
        self.assertTrue(files)
        twin = bf.BookFeed(self.dir, min_usd=150000, gone_after=2, ttl_days=3)
        rep = twin.history(self.sym, hours=1)["walls"]
        self.assertEqual(len(rep), 1)
        w = rep[0]
        self.assertEqual(w["id"], orig["id"])
        self.assertEqual(w["side"], "bid")
        self.assertAlmostEqual(w["peak"], orig["peak"], places=1)
        self.assertAlmostEqual(w["closed"], orig["closed"], places=1)
        self.assertFalse(w["live"])
        self.assertGreater(twin._next_id, orig["id"])   # идемпотентность id после рестарта

    def test_new_open_walls_filters(self):
        t = self.now
        self.feed._merge("ETH_USDT", [{"side": "bid", "lo": 3000.0, "hi": 3001.0,
                                       "px": 3000.5, "usdt": 400000.0, "levels": 1,
                                       "exchs": ["binance"]}], t + 1)
        got = self.feed.new_open_walls(["ETH_USDT"], 150000, "both", t)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["sym"], "ETH_USDT")
        self.assertEqual(self.feed.new_open_walls(["BTC_USDT"], 150000, "both", t), [])
        self.assertEqual(self.feed.new_open_walls(["ETH_USDT"], 500000, "both", t), [])
        self.assertEqual(self.feed.new_open_walls(["ETH_USDT"], 150000, "ask", t), [])
        self.assertEqual(self.feed.new_open_walls(["ETH_USDT"], 150000, "both", t + 2), [])

    def test_wanted_symbols_viewers_ttl(self):
        feed = bf.BookFeed(None)
        feed._viewers["BTC_USDT"] = time.time()
        feed._viewers["OLD_USDT"] = time.time() - 240
        feed.sub_symbols_fn = lambda: ["ETH_USDT"]
        self.assertEqual(feed.wanted_symbols(), ["BTC_USDT", "ETH_USDT"])

    def test_normalize_cfg(self):
        cfg = bf.normalize_book_cfg({"symbols": ["eth-usdt", "btcusdt", 123],
                                     "min_usd": 1, "side": "x", "notify": False})
        self.assertEqual(cfg["symbols"], ["ETH_USDT", "BTCUSDT", "123"])
        self.assertEqual(cfg["min_usd"], 50000)
        self.assertEqual(cfg["side"], "both")
        self.assertFalse(cfg["notify"])
        self.assertTrue(cfg["enabled"])
        empty = bf.normalize_book_cfg({})
        self.assertEqual(empty["symbols"], ["BTC_USDT", "ETH_USDT"])

    def test_format_html(self):
        w = {"side": "bid", "sym": "BTC_USDT", "lo": 62776.0, "hi": 62798.0,
             "usdt": 1200000.0, "exchs": ["binance", "okx"]}
        ru = bf.format_wall_html(w, "ru")
        self.assertIn("Стена", ru)
        self.assertIn("$1.20M", ru)
        self.assertIn("binance, okx", ru)
        self.assertIn("Wall", bf.format_wall_html(w, "en"))

    def test_demo_tick_makes_walls(self):
        feed = bf.BookFeed(None, min_usd=150000, gone_after=3, demo=True)
        feed._viewers["BTC_USDT"] = time.time()
        t0 = time.time()
        for k in range(10):
            feed.demo_tick(now=t0 + k * 2)
        self.assertTrue(feed.walls["BTC_USDT"])
        snap = feed.snapshot("BTC_USDT")
        self.assertTrue(snap["walls"])
        self.assertTrue(all(w["live"] for w in snap["walls"]))


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.feed = bf.BookFeed(None, min_usd=150000, gone_after=1)
        self.now = time.time()
        self.sym = "BTC_USDT"

    def _wall(self, side, opened, closed, peak, final=None, ):
        wid = self.feed._next_id
        self.feed._next_id += 1
        self.feed.walls.setdefault(self.sym, {})[wid] = {
            "id": wid, "sym": self.sym, "side": side, "lo": 1.0, "hi": 2.0, "px": 1.5,
            "usdt": final if final is not None else peak, "peak": peak, "levels": 1,
            "exchs": ["binance"], "opened": opened, "last_seen": closed or self.now,
            "closed": closed, "miss": 0, "live": closed is None}

    def test_metrics_hourly_pressure_life(self):
        # 1) живые стены: bid 200k, ask 100k → перекос +0.333
        self._wall("bid", self.now - 10, None, 200000.0)
        self._wall("ask", self.now - 10, None, 100000.0)
        # 2) закрытая: жила 2ч, peak 480k — размажется по ~2 часовым ячейкам
        self._wall("bid", self.now - 3 * 3600, self.now - 1 * 3600, 480000.0)
        # 3) спуф: 60 секунд
        self._wall("ask", self.now - 70, self.now - 10, 300000.0)
        # 4) сдутая: 1000k → 400k на последнем наблюдении
        self._wall("bid", self.now - 600, self.now - 300, 1000000.0, final=400000.0)
        m = self.feed.metrics(self.sym, hours=24)
        self.assertTrue(m["ok"])
        self.assertEqual(m["live"]["count"], 2)
        self.assertAlmostEqual(m["pressure"]["imbalance"], 1 / 3, places=2)
        # почасовая сумма = peak всех попавших в окно стен (2.4M + живые пики)
        tot = sum(c["bid_usdt"] + c["ask_usdt"] for c in m["hourly"])
        self.assertAlmostEqual(tot, 200000 + 100000 + 480000 + 300000 + 1000000, delta=0.5)
        self.assertEqual(len(m["hourly"]), 24)
        self.assertEqual(m["life"]["closed_n"], 3)
        self.assertAlmostEqual(m["life"]["spoof_pct"], 100 / 3, places=0)
        self.assertAlmostEqual(m["life"]["eaten_pct"], 100 / 3, places=0)
        self.assertEqual(m["life"]["median_s"], 300.0)     # жизни закрытых: 60, 300, 7200
        self.assertIsNone(self.feed.metrics("NOPE_USDT")["live"]["max"])

    def test_metrics_route(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        bf.register_book_routes(app, lambda: self.feed)
        c = TestClient(app)
        r = c.get("/api/book/metrics", params={"symbol": self.sym, "hours": 24})
        self.assertEqual(r.status_code, 200)
        self.assertIn("hourly", r.json())
        self.assertIn(self.sym, self.feed._viewers)


class RouteTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.feed = bf.BookFeed(None, min_usd=150000)
        self.feed._viewers["BTC_USDT"] = time.time()
        now = time.time()
        self.feed._merge("BTC_USDT", [{"side": "bid", "lo": 62700.0, "hi": 62750.0,
                                       "px": 62725.0, "usdt": 500000.0, "levels": 2,
                                       "exchs": ["binance"]}], now)
        self.app = FastAPI()
        bf.register_book_routes(self.app, lambda: self.feed)
        self.client = TestClient(self.app)

    def test_snapshot_marks_viewer_and_returns_walls(self):
        r = self.client.get("/api/book/snapshot", params={"symbol": "ETH_USDT"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("ETH_USDT", self.feed._viewers)
        r2 = self.client.get("/api/book/snapshot", params={"symbol": "BTC_USDT"})
        self.assertEqual(r2.status_code, 200)
        body = r2.json()
        self.assertEqual(len(body["walls"]), 1)
        self.assertAlmostEqual(body["walls"][0]["usdt"], 500000.0)

    def test_history_and_status(self):
        r = self.client.get("/api/book/walls", params={"symbol": "BTC_USDT", "hours": 1})
        self.assertTrue(r.json()["count"])
        s = self.client.get("/api/book/status").json()
        self.assertEqual(s["wall_ids"], 1)

    def test_feed_off_is_503(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        bf.register_book_routes(app, lambda: None)
        c = TestClient(app)
        self.assertEqual(c.get("/api/book/snapshot").status_code, 503)


if __name__ == "__main__":
    print("=== tests/test_book_feed.py ===")
    result = unittest.main(exit=False, verbosity=2).result
    print("=== %d passed, %d failed ===" % (result.testsRun - len(result.failures) - len(result.errors),
                                            len(result.failures) + len(result.errors)))
    sys.exit(1 if (result.failures or result.errors) else 0)
