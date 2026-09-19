"""Кластеры ликвидаций из сохранённой истории: плашки за всё время графика.

Регрессия из терминала: кластеры собирались только в открытом окне браузера —
2000 последних событий из памяти сервера плюс сутки в IndexedDB. Всё, что
случилось при закрытом терминале, на графике не появлялось вовсе, хотя история
ликвидаций пишется на диск всегда (дневные шарды HistoryStore, месяц хранения).

Проверяем:
    * уровни считаются так же, как их считал клиент (шесть уровней внутри
      свечи, округление как Math.round, а не банковское);
    * поток событий с диска фильтруется по монете и окну времени;
    * /api/liq_clusters отдаёт кластеры по свечам за сохранённую историю;
    * событие, которое есть и на диске, и в памяти, считается один раз, а
      свежее событие из памяти добавляется сверху;
    * порог min_usd и список бирж фильтруют так же, как фильтр ленты.

Запуск: python3 -m pytest tests/test_liq_clusters.py
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

TMP = tempfile.mkdtemp(prefix="liqclusters_")
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq_history.jsonl")
os.environ["LIQSCOPE_HISTORY_TTL_HOURS"] = "744"
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_BOT_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from history import HistoryStore  # noqa: E402

TF = 5
STEP = TF * 60


def ev(n: int, ts: float, symbol: str = "BTC_USDT", usd: float = 100_000.0,
       side: str = "SELL", exch: str = "binance", price: float = 50_000.0,
       sym: str = "") -> dict:
    return {"id": f"cl{n}", "symbol": sym or symbol, "exchange": exch, "side": side,
            "position": "LONG" if side == "SELL" else "SHORT",
            "price": price, "qty": usd / max(price, 1.0), "usd": usd,
            "timestamp": float(ts)}


def candle(t: float, lo: float = 100.0, hi: float = 110.0) -> dict:
    return {"time": int(t), "open": lo, "high": hi, "low": lo, "close": hi,
            "volume": 1.0}


#: время, выровненное по сетке пятиминутных свечей (иначе событие уедет
#: в предыдущий бакет — так же, как это делает клиент)
T = 999_900


class LevelMathTest(unittest.TestCase):
    """Раскладка события по уровням внутри свечи — как на клиенте."""

    def setUp(self):
        self.bars = server._liq_cluster_bars([candle(T)], TF)

    def level_of(self, price: float, acc: dict) -> int:
        evd = ev(1, T, usd=1000.0, price=price)
        server._liq_cluster_add(self.bars, TF, evd, acc)
        return list(acc[T]["levels"])[0]

    def test_low_and_high_edges(self):
        self.assertEqual(self.level_of(100.0, {}), 0)
        self.assertEqual(self.level_of(110.0, {}), 5)

    def test_half_rounds_up_like_js(self):
        # 101.0 — это ровно середина первого шага: Math.round(0.5) = 1.
        # Python-овское round() дало бы 0, и плашка встала бы на уровень ниже.
        self.assertEqual(self.level_of(101.0, {}), 1)
        self.assertEqual(self.level_of(105.0, {}), 3)     # round(2.5) -> 3
        self.assertEqual(self.level_of(103.0, {}), 2)     # round(1.5) -> 2

    def test_price_outside_candle_is_clamped(self):
        self.assertEqual(self.level_of(90.0, {}), 0)
        self.assertEqual(self.level_of(140.0, {}), 5)

    def test_flat_candle_goes_to_first_level(self):
        bars = server._liq_cluster_bars([candle(T, 100.0, 100.0)], TF)
        acc: dict = {}
        server._liq_cluster_add(bars, TF, ev(1, T, price=100.0), acc)
        self.assertEqual(list(acc[T]["levels"]), [0])

    def test_event_of_other_candle_is_ignored(self):
        acc: dict = {}
        server._liq_cluster_add(self.bars, TF, ev(1, T - STEP, price=100.0), acc)
        self.assertEqual(acc, {})

    def test_totals_sides_and_exchanges(self):
        acc: dict = {}
        server._liq_cluster_add(self.bars, TF,
                                ev(1, T, usd=1000.0, price=100.0), acc)
        server._liq_cluster_add(self.bars, TF,
                                ev(2, T, usd=2000.0, price=101.0, side="BUY"),
                                acc)
        server._liq_cluster_add(self.bars, TF,
                                ev(3, T, usd=500.0, price=100.0,
                                   exch="okx"), acc)
        self.assertEqual(acc[T]["ts"], T)
        long_lvl = acc[T]["levels"][0]
        self.assertEqual(long_lvl[0], 1500.0)          # SELL = вынесли лонг
        self.assertEqual(long_lvl[2], 2)
        self.assertEqual(long_lvl[1], 0.0)
        self.assertEqual(long_lvl[5]["BINANCE"], [1, 1000.0])
        self.assertEqual(long_lvl[5]["OKX"], [1, 500.0])
        short_lvl = acc[T]["levels"][1]
        self.assertEqual(short_lvl[1], 2000.0)         # BUY = вынесли шорт
        self.assertEqual(short_lvl[3], 1)

    def test_newest_timestamp_is_kept(self):
        acc: dict = {}
        for ts in (T, T + 120, T + 60):
            server._liq_cluster_add(self.bars, TF, ev(1, ts, price=100.0), acc)
        self.assertEqual(acc[T]["ts"], T + 120)


class IterEventsTest(unittest.TestCase):
    """Поток сырых событий с диска: шарды читаются построчно."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="liqiter_", dir=TMP)
        self.store = HistoryStore(os.path.join(self.dir, "liq_history.jsonl"))
        self.t0 = 1_700_000_000.0

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_streams_only_window_and_symbol(self):
        for n, (ts, sym) in enumerate([(self.t0, "BTC_USDT"),
                                       (self.t0 + 60, "ETH_USDT"),
                                       (self.t0 + 600, "BTC_USDT")]):
            self.store.add(ev(n, ts, sym))
        rows = list(self.store.iter_events(self.t0 - 10, self.t0 + 300, "BTC_USDT"))
        self.assertEqual([r["id"] for r in rows], ["cl0"])
        rows = list(self.store.iter_events(self.t0 - 10, self.t0 + 900))
        self.assertEqual(len(rows), 3)

    def test_broken_line_does_not_break_stream(self):
        path = self.store.shard_path(__import__("history").day_key(self.t0))
        self.store.add(ev(1, self.t0))
        with open(path, "a", encoding="utf-8") as f:
            f.write("{это не json}\n\n[]\n")
        self.store.add(ev(2, self.t0 + 60))
        rows = list(self.store.iter_events(self.t0 - 10, self.t0 + 120, "BTC_USDT"))
        self.assertEqual([r["id"] for r in rows], ["cl1", "cl2"])

    def test_no_history_file_is_not_an_error(self):
        self.assertEqual(list(HistoryStore("").iter_events(0.0)), [])

    def test_other_symbol_is_filtered_before_parsing(self):
        # чужая монета лежит в том же шарде и не должна мешать
        self.store.add(ev(1, self.t0, "ETH_USDT"))
        self.store.add(ev(2, self.t0 + 30, "BTC_USDT"))
        rows = list(self.store.iter_events(self.t0 - 10, self.t0 + 60, "BTC_USDT"))
        self.assertEqual([r["id"] for r in rows], ["cl2"])


class ClusterMapTest(unittest.TestCase):
    """Свёртка сохранённой истории + свежий хвост из памяти — без двойного счёта."""

    #: своя монета на каждый тест: шард дня общий, а события в нём накапливаются
    seq = 0

    def setUp(self):
        server.LIQ_CLUSTER_CACHE.clear()
        # своё хранилище: в общем прогоне тестов HIST достаётся другому файлу
        # (он задаёт каталог данных первым), поэтому не полагаемся на него
        self.dir = tempfile.mkdtemp(prefix="liqmap_", dir=TMP)
        self._hist, self._hist_file = server.HIST, server.HISTORY_FILE
        server.HIST = HistoryStore(os.path.join(self.dir, "liq_history.jsonl"))
        server.HISTORY_FILE = server.HIST.base_path
        ClusterMapTest.seq += 1
        self.sym = "CMAP%d_USDT" % ClusterMapTest.seq
        self.now = time.time()
        cur = int(self.now // STEP * STEP)                     # текущая свеча
        self.t0 = cur - 2 * STEP                               # две свечи назад
        self.t1 = cur - STEP
        self.cur = cur
        self.store = server.HIST
        # событие, которое успело уйти на диск (очередь записи)
        self.old = ev(1, self.t0 + 10, usd=300_000.0, price=101.0, sym=self.sym)
        self.store.add(self.old)
        self.store.flush(force=True)
        # свежее, только в памяти: диск его ещё не видел
        self.fresh_ts = max(self.now, cur + 1)
        self.fresh = ev(2, self.fresh_ts, usd=200_000.0, price=109.0, sym=self.sym)
        server.LIQUIDATIONS.clear()
        server.LIQUIDATIONS.append(self.old)     # то же событие, что на диске
        server.LIQUIDATIONS.append(self.fresh)

    def tearDown(self):
        server.LIQUIDATIONS.clear()
        server.LIQ_CLUSTER_CACHE.clear()
        server.HIST, server.HISTORY_FILE = self._hist, self._hist_file
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_counts_disk_and_memory_once(self):
        candles = [candle(self.t0), candle(self.t1), candle(self.cur)]
        rows, cut = server._liq_cluster_map(candles, TF, self.sym, 0.0)
        self.assertGreater(cut, 0)
        disk_bucket = rows[str(self.t0)]
        total_disk = sum(r[1] + r[2] for r in disk_bucket["l"])
        # 300к с диска, но НЕ 600к: то же событие лежит и в памяти
        self.assertAlmostEqual(total_disk, 300_000.0, places=2)
        self.assertAlmostEqual(disk_bucket["t"], self.t0 + 10, places=1)
        # свежее событие из памяти — в своей свече и посчитано один раз
        live = rows[str(self.cur)]
        self.assertAlmostEqual(sum(r[1] + r[2] for r in live["l"]), 200_000.0,
                               places=2)
        self.assertAlmostEqual(live["t"], self.fresh_ts, places=1)

    def test_min_usd_filters_like_the_feed(self):
        server.LIQ_CLUSTER_CACHE.clear()
        candles = [candle(self.t0), candle(self.t1), candle(self.cur)]
        rows, _cut = server._liq_cluster_map(candles, TF, self.sym, 250_000.0)
        total = sum(r[1] + r[2] for r in rows.get(str(self.t0), {"l": []})["l"])
        self.assertAlmostEqual(total, 300_000.0, places=2)
        # свежее событие на 200к порог не проходит и в плашки не попадает
        self.assertNotIn(str(self.cur), rows)
        server.LIQ_CLUSTER_CACHE.clear()
        rows, cut = server._liq_cluster_map(candles, TF, self.sym, 300_001.0)
        self.assertEqual((rows, cut), ({}, 0.0))

    def test_cache_gives_same_rows(self):
        candles = [candle(self.t0), candle(self.t1), candle(self.cur)]
        first = server._liq_cluster_map(candles, TF, self.sym, 0.0)
        second = server._liq_cluster_map(candles, TF, self.sym, 0.0)
        self.assertEqual(first[0], second[0])
        # ключ кэша: монета, ТФ, порог и список включённых бирж (None — все)
        self.assertIn((self.sym, TF, 0.0, None), server.LIQ_CLUSTER_CACHE)

    def test_exchanges_filter_like_the_feed(self):
        """Выключенная в фильтре биржа не должна оставаться в сохранённых плашках."""
        candles = [candle(self.t0), candle(self.t1), candle(self.cur)]
        rows, _cut = server._liq_cluster_map(candles, TF, self.sym, 0.0, ["binance"])
        bucket = rows[str(self.t0)]
        self.assertAlmostEqual(sum(r[1] + r[2] for r in bucket["l"]), 300_000.0,
                               places=2)
        self.assertEqual(set(bucket["l"][0][6]), {"BINANCE"})
        # свеча со свежим событием осталась: оно тоже с binance
        self.assertIn(str(self.cur), rows)
        server.LIQ_CLUSTER_CACHE.clear()
        rows, cut = server._liq_cluster_map(candles, TF, self.sym, 0.0, ["kraken"])
        self.assertEqual((rows, cut), ({}, 0.0))

    def test_other_symbol_has_no_clusters(self):
        candles = [candle(self.t0), candle(self.t1), candle(self.cur)]
        rows, cut = server._liq_cluster_map(candles, TF, "ETH_USDT", 0.0)
        self.assertEqual(rows, {})
        self.assertEqual(cut, 0.0)


class ClusterApiTest(unittest.TestCase):
    """Эндпоинт плашек: кластеры по свечам за сохранённую историю."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)          # без lifespan: фид не поднимаем
        cls.dir = tempfile.mkdtemp(prefix="liqapi_", dir=TMP)
        cls._hist, cls._hist_file = server.HIST, server.HISTORY_FILE
        server.HIST = HistoryStore(os.path.join(cls.dir, "liq_history.jsonl"))
        server.HISTORY_FILE = server.HIST.base_path
        cls.now = time.time()
        cls.t0 = int(cls.now // STEP * STEP) - 2 * STEP
        cls.sym = "CLUSTERTEST_USDT"
        server.HIST.add(ev(1, cls.t0 + 30, cls.sym, 400_000.0, "SELL", "binance",
                           price=101.0))
        server.HIST.add(ev(2, cls.t0 + 90, cls.sym, 100_000.0, "BUY", "okx",
                           price=105.0))
        server.HIST.add(ev(3, cls.t0 + STEP + 40, cls.sym, 250_000.0, "SELL",
                           "bybit", price=109.0))
        server.HIST.flush(force=True)

    @classmethod
    def tearDownClass(cls):
        server.HIST, server.HISTORY_FILE = cls._hist, cls._hist_file
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        # свечи в тесте свои: у живого сервера их не спросить (фид не поднят),
        # а геометрия свечи важна — от неё зависят уровни цены
        server.LIQ_CLUSTER_CACHE.clear()
        self._real_candles = server.get_candles
        cls = self.__class__

        async def fake(symbol, tf, force=False):
            return {"source": "test", "candles": [
                candle(cls.t0), candle(cls.t0 + STEP), candle(cls.t0 + 2 * STEP)]}

        server.get_candles = fake

    def tearDown(self):
        server.get_candles = self._real_candles
        server.LIQ_CLUSTER_CACHE.clear()

    def test_clusters_come_from_history(self):
        r = self.client.get("/api/liq_clusters", params={
            "symbol": self.sym, "timeframe": TF})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["symbol"], self.sym)
        self.assertEqual(data["timeframe"], TF)
        self.assertGreater(data["cut"], 0)
        rows = data["candles"]
        self.assertIn(str(self.t0), rows)
        levels = rows[str(self.t0)]["l"]
        self.assertEqual(len(levels), 2)                 # два уровня цены
        total = sum(x[1] + x[2] for x in levels)
        self.assertAlmostEqual(total, 500_000.0, places=2)
        self.assertAlmostEqual(rows[str(self.t0)]["t"], self.t0 + 90, places=1)
        # свежая свеча тоже посчитана (250к), хотя терминала никто не открывал
        newer = [t for t in rows if int(t) > self.t0]
        self.assertTrue(newer)
        self.assertAlmostEqual(sum(x[1] + x[2] for x in rows[newer[0]]["l"]),
                               250_000.0, places=2)

    def test_exchange_breakdown_for_the_modal(self):
        r = self.client.get("/api/liq_clusters", params={
            "symbol": self.sym, "timeframe": TF})
        levels = r.json()["candles"][str(self.t0)]["l"]
        names = set()
        for row in levels:
            names.update(row[6].keys())
        self.assertEqual(names, {"BINANCE", "OKX"})

    def test_min_usd_filter(self):
        r = self.client.get("/api/liq_clusters", params={
            "symbol": self.sym, "timeframe": TF, "min_usd": 300_000})
        rows = r.json()["candles"]
        self.assertIn(str(self.t0), rows)
        levels = rows[str(self.t0)]["l"]
        self.assertAlmostEqual(sum(x[1] + x[2] for x in levels), 400_000.0, places=2)
        # свеча, где было только 250к, из ответа уходит
        self.assertNotIn(str(self.t0 + STEP), rows)

    def test_exchanges_param(self):
        """Фильтр бирж в запросе: чужие биржи из плашек уходят."""
        r = self.client.get("/api/liq_clusters", params={
            "symbol": self.sym, "timeframe": TF, "exchanges": "okx"})
        rows = r.json()["candles"]
        self.assertIn(str(self.t0), rows)
        levels = rows[str(self.t0)]["l"]
        # 100к шорта с OKX, 400к с Binance (выключена) и 250к свежей свечи с Bybit
        self.assertAlmostEqual(sum(x[1] + x[2] for x in levels), 100_000.0, places=2)
        self.assertEqual(rows[str(self.t0)]["l"][0][6], {"OKX": [1, 100000.0]})
        self.assertNotIn(str(self.t0 + STEP), rows)
        # список через запятую — обе биржи, порядок не важен
        r2 = self.client.get("/api/liq_clusters", params={
            "symbol": self.sym, "timeframe": TF, "exchanges": " bybit , OKX "})
        rows2 = r2.json()["candles"]
        self.assertAlmostEqual(sum(x[1] + x[2] for x in rows2[str(self.t0)]["l"]),
                               100_000.0, places=2)
        self.assertAlmostEqual(
            sum(x[1] + x[2] for x in rows2[str(self.t0 + STEP)]["l"]),
            250_000.0, places=2)

    def test_month_history_is_the_ceiling(self):
        r = self.client.get("/api/liq_clusters", params={
            "symbol": self.sym, "timeframe": TF})
        self.assertGreaterEqual(r.json()["ttl_hours"], 24 * 28)


if __name__ == "__main__":
    unittest.main(verbosity=2)
