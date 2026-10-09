"""Сторона набранных позиций: тейкер-баланс, funding и приоритет источников.

Дельта открытого интереса сама по себе слепа: +$50M OI бывает и лонгами, и
шортами, а уровни ликвидаций у них в противоположных сторонах графика. Модуль
даёт долю покупок по 5-минуткам (ряд Binance takerlongshortRatio), историю
funding и порядок, в котором эти источники уступают друг другу.

Проверяем:
    * разбор ответов Binance: buySellRatio → доля покупок 0…1, мс → секунды,
      пятиминутные слоты;
    * funding: история, поиск ставки на момент, знак как признак перевеса;
    * приоритет источников: taker → тики профиля → funding → «пополам»;
    * кэш: ряд не перезапрашивается до истечения TTL;
    * отключённый модуль не ходит в сеть и честно отдаёт «нет данных».

Запуск: python3 -m pytest tests/test_side_feed.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import side_feed  # noqa: E402
from side_feed import (SideFeed, bucket_keys, funding_at,  # noqa: E402
                       nearest_bucket, parse_funding, parse_global_lsr,
                       parse_taker_ratio)

TAKER = [
    {"buySellRatio": "1.0", "buyVol": "100", "sellVol": "100",
     "timestamp": 1_700_000_100_000},
    {"buySellRatio": "3.0", "buyVol": "300", "sellVol": "100",
     "timestamp": 1_700_000_400_000},
]

LSR = [
    {"symbol": "BTCUSDT", "longShortRatio": "3.0", "longAccount": "0.75",
     "shortAccount": "0.25", "timestamp": 1_700_000_100_000},
]

FUNDING = [
    {"symbol": "BTCUSDT", "fundingTime": 1_700_000_000_000, "fundingRate": "0.0001"},
    {"symbol": "BTCUSDT", "fundingTime": 1_700_028_800_000, "fundingRate": "-0.0002"},
]


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        payload = self.routes.get(url)
        if payload is None:
            raise RuntimeError("нет маршрута")
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)


class ParserTest(unittest.TestCase):
    def test_taker_ratio_becomes_buy_share(self):
        rows = parse_taker_ratio(TAKER)
        first = rows[1_700_000_100]
        self.assertAlmostEqual(first["buy_share"], 0.5, places=9)
        self.assertAlmostEqual(first["ratio"], 1.0, places=9)
        second = rows[1_700_000_400]
        self.assertAlmostEqual(second["buy_share"], 0.75, places=9)

    def test_ratio_from_volumes_when_ratio_missing(self):
        rows = parse_taker_ratio([{"buyVol": "150", "sellVol": "50",
                                   "timestamp": 1_700_000_100_000}])
        self.assertAlmostEqual(rows[1_700_000_100]["buy_share"], 0.75, places=9)

    def test_broken_rows_are_skipped(self):
        rows = parse_taker_ratio([{"buySellRatio": "x", "timestamp": 1},
                                  {"buySellRatio": "1"}, None, "мусор"])
        self.assertEqual(rows, {})

    def test_funding_history_and_lookup(self):
        points = parse_funding(FUNDING)
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(funding_at(points, 1_700_010_000), 0.0001, places=9)
        self.assertAlmostEqual(funding_at(points, 1_700_100_000), -0.0002, places=9)
        self.assertIsNone(funding_at(points, 1_600_000_000))

    def test_global_lsr(self):
        rows = parse_global_lsr([{"longShortRatio": "3.0",
                                  "timestamp": 1_700_000_100_000}])
        self.assertAlmostEqual(rows[1_700_000_100]["long_share"], 0.75, places=9)


class SideAtTest(unittest.TestCase):
    def setUp(self):
        self.sf = SideFeed(ttl=600, enabled=True)
        self.t = 1_700_000_100

    def test_priority_taker_over_ticks_over_funding(self):
        self.sf._taker["BTC_USDT"] = parse_taker_ratio(TAKER)
        share, src = self.sf.side_at("BTC_USDT", 1_700_000_400, tick_share=0.1)
        self.assertEqual(src, "taker")
        self.assertAlmostEqual(share, 0.75, places=9)

    def test_ticks_used_when_taker_missing(self):
        share, src = self.sf.side_at("BTC_USDT", self.t, tick_share=0.42)
        self.assertEqual(src, "ticks")
        self.assertAlmostEqual(share, 0.42, places=9)

    def test_funding_is_the_last_resort(self):
        self.sf._funding["BTC_USDT"] = parse_funding(FUNDING)
        share, src = self.sf.side_at("BTC_USDT", 1_700_050_000)
        self.assertEqual(src, "funding")
        self.assertLess(share, 0.5)          # ставка отрицательная — перевес в шортах

    def test_nothing_known_is_half_and_half(self):
        share, src = self.sf.side_at("BTC_USDT", self.t)
        self.assertEqual(src, "none")
        self.assertAlmostEqual(share, 0.5, places=9)

    def test_ratio_at_falls_back_to_previous_bucket(self):
        self.sf._taker["BTC_USDT"] = parse_taker_ratio(TAKER)
        self.assertAlmostEqual(self.sf.ratio_at("BTC_USDT", 1_700_000_700), 0.75,
                               places=9)


class EnsureTest(unittest.TestCase):
    ROUTES = {
        "https://fapi.binance.com/futures/data/takerlongshortRatio": TAKER,
        "https://fapi.binance.com/fapi/v1/fundingRate": FUNDING,
    }

    def test_ensure_fills_and_caches(self):
        session = FakeSession(self.ROUTES)
        sf = SideFeed(ttl=600)
        snap = asyncio.run(sf.ensure(session, "BTC_USDT"))
        self.assertTrue(snap["taker"])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]["period"], "5m")
        self.assertAlmostEqual(snap["funding"], -0.0002, places=9)
        asyncio.run(sf.ensure(session, "BTC_USDT"))
        self.assertEqual(len(session.calls), 2)      # второй раз — из кэша

    def test_force_refreshes(self):
        session = FakeSession(self.ROUTES)
        sf = SideFeed(ttl=600)
        asyncio.run(sf.ensure(session, "BTC_USDT"))
        asyncio.run(sf.ensure(session, "BTC_USDT", force=True))
        self.assertEqual(len(session.calls), 4)

    def test_failure_is_remembered_and_reported(self):
        session = FakeSession({})
        sf = SideFeed(ttl=0.01)
        snap = asyncio.run(sf.ensure(session, "BTC_USDT"))
        self.assertIsNone(snap["taker"])
        self.assertIn("taker", snap["error"])
        st = sf.status()
        self.assertEqual(st["failed"], 1)

    LSR_ROUTES = {
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio": LSR,
    }

    def test_lsr_used_when_taker_is_missing(self):
        """Тейкер не отдался — сторона берётся из баланса счетов (LSR)."""
        session = FakeSession(self.LSR_ROUTES)
        sf = SideFeed(ttl=600)
        snap = asyncio.run(sf.ensure(session, "BTC_USDT"))
        self.assertIsNone(snap["taker"])
        self.assertAlmostEqual(snap["lsr"], 0.75, places=9)
        self.assertEqual(len(sf._taker), 0)
        share, src = sf.side_at("BTC_USDT", 1_700_000_100)
        self.assertEqual(src, "lsr")
        self.assertAlmostEqual(share, 0.75, places=9)

    def test_lsr_falls_back_to_venue_average(self):
        """Нет и баланса счетов — берём средний LSR по биржам (liq_api)."""
        import liq_api
        session = FakeSession({})
        sf = SideFeed(ttl=600)

        async def fake_multi(_session, _sym):
            return {"Gate.io": 1.0, "Bybit": 3.0, "average": 2.0}

        old = getattr(liq_api, "get_multi_lsr", None)
        liq_api.get_multi_lsr = fake_multi
        try:
            snap = asyncio.run(sf.ensure(session, "BTC_USDT"))
        finally:
            if old is not None:
                liq_api.get_multi_lsr = old
        self.assertAlmostEqual(snap["lsr"], 2.0 / 3.0, places=6)
        share, src = sf.side_at("BTC_USDT", time.time())
        self.assertEqual(src, "lsr")
        self.assertAlmostEqual(share, 2.0 / 3.0, places=6)

    def test_disabled_module_never_touches_network(self):
        session = FakeSession(self.ROUTES)
        sf = SideFeed(ttl=600, enabled=False)
        asyncio.run(sf.ensure(session, "BTC_USDT"))
        self.assertEqual(session.calls, [])
        st = sf.status()
        self.assertFalse(st["enabled"])


def _linear_nearest(rows, ts, tol):
    """Прежняя реализация в лоб — эталон для проверки бинарного поиска."""
    if not rows:
        return None
    try:
        want = float(ts)
    except (TypeError, ValueError):
        return None
    best, best_gap, best_key = None, None, None
    for b, row in rows.items():
        share = row.get("buy_share") if isinstance(row, dict) else row
        if share is None:
            continue
        gap = abs(float(b) + 150.0 - want)
        if best_gap is None or gap <= best_gap:
            best, best_gap, best_key = share, gap, float(b)
    if best_gap is not None and best_gap <= float(tol):
        return best
    return None


class NearestBucketSearchTest(unittest.TestCase):
    """Поиск ближайшего слота: бинарный вместо перебора, семантика та же.

    ``build_rows`` зовёт ``side_at`` на каждую точку OI (30 дней окна по
    5-минуткам — тысячи точек), а прежний ``nearest_bucket`` перебирал весь ряд
    на каждый вызов: O(N·M) и пауза воркера 652 мс на бою.
    """

    def _rows(self, n, step=300, start=1_700_000_000, holes=()):
        rows = {}
        for i in range(n):
            b = start + i * step
            if i in holes:
                rows[b] = {"buy_share": None}      # слот без доли — пропускается
            else:
                rows[b] = {"buy_share": round(0.3 + (i % 5) * 0.1, 3),
                           "ratio": 1.0 + i}
        return rows

    def test_matches_linear_scan_on_regular_series(self):
        rows = self._rows(200)
        keys = bucket_keys(rows)
        start = 1_700_000_000
        for probe in range(start - 900, start + 200 * 300 + 900, 37):
            with self.subTest(probe=probe):
                self.assertEqual(nearest_bucket(rows, probe, keys=keys),
                                 _linear_nearest(rows, probe, side_feed.TAKER_TOL_SEC))

    def test_matches_linear_scan_with_holes_and_gaps(self):
        rows = self._rows(120, holes={0, 1, 5, 6, 7, 40, 119})
        for b in list(rows)[10:20]:
            del rows[b]                            # дыры в середине ряда
        keys = bucket_keys(rows)
        start = 1_700_000_000
        for probe in range(start, start + 120 * 300, 53):
            self.assertEqual(nearest_bucket(rows, probe, keys=keys),
                             _linear_nearest(rows, probe, side_feed.TAKER_TOL_SEC),
                             f"расхождение на {probe}")

    def test_outside_tolerance_is_none(self):
        rows = self._rows(10)
        far = 1_700_000_000 + 10 * 300 + 10 ** 6
        self.assertIsNone(nearest_bucket(rows, far))
        self.assertIsNone(nearest_bucket(rows, 1_600_000_000))

    def test_ties_prefer_fresher_slot(self):
        # середина между двумя слотами: разрыв равен, берём более свежий
        rows = {1_000: {"buy_share": 0.2}, 1_300: {"buy_share": 0.9}}
        mid = 1_000 + 150 + 150          # ровно между серединами слотов
        self.assertEqual(nearest_bucket(rows, mid), 0.9)

    def test_bad_input_is_survivable(self):
        self.assertIsNone(nearest_bucket({}, 123))
        self.assertIsNone(nearest_bucket({100: {"buy_share": 0.5}}, "мусор"))
        self.assertIsNone(nearest_bucket({100: {"buy_share": None}}, 250))

    def test_no_quadratic_blowup_on_big_series(self):
        """30 дней 5-минуток: 8640 слотов × 8640 точек не должны считаться вечно."""
        rows = self._rows(8640)
        keys = bucket_keys(rows)
        probes = [1_700_000_000 + i * 300 + 120 for i in range(8640)]
        t0 = time.perf_counter()
        got = sum(1 for p in probes
                  if nearest_bucket(rows, p, keys=keys) is not None)
        dt = time.perf_counter() - t0
        self.assertEqual(got, 8640)
        self.assertLess(dt, 2.0,
                        f"8640 обращений к ряду считались {dt:.1f} с — "
                        "похоже, перебор вернулся")

    def test_sidefeed_caches_sorted_keys_and_invalidates(self):
        feed = SideFeed(enabled=True)
        rows = self._rows(50)
        feed._taker["BTC_USDT"] = rows
        k1 = feed._keys_of(feed._taker, feed._taker_keys, "BTC_USDT")
        k2 = feed._keys_of(feed._taker, feed._taker_keys, "BTC_USDT")
        self.assertIs(k1, k2, "ряд сортируется заново на каждый вызов")
        feed._taker["BTC_USDT"] = self._rows(60)     # загрузка заменила ряд
        k3 = feed._keys_of(feed._taker, feed._taker_keys, "BTC_USDT")
        self.assertIsNot(k1, k3, "кэш ключей пережил замену ряда")
        self.assertEqual(len(k3), 60)
        self.assertEqual(feed.taker_share_at("BTC_USDT", k3[10] + 150),
                         rows_value(feed._taker["BTC_USDT"], k3[10]))


def rows_value(rows, begin):
    return rows.get(int(begin), rows.get(begin, {})).get("buy_share")


if __name__ == "__main__":
    unittest.main(verbosity=2)
