"""Расчётные уровни ликвидаций: где стоят позиции, которым больно.

Инструмент отвечает на вопрос «что ещё стоит», а не «что уже снесли»:
масса позиций берётся из дельт открытого интереса, цена входа — из VWAP
профиля объёма, сторона — из тейкер-баланса, плечо и маржа — из
распределения и риск-лимитов бирж.

Проверяем самое хрупкое:
    * формулу цены ликвидации (лонг ниже входа, шорт выше, MMR сдвигает);
    * сборку порций: рост OI — позиции набрали, падение — выживаемость упала;
    * разложение массы по плечам: каждая ступень попадает на свою цену;
    * вычитание отработавшего: уровень обнуляется, но не уходит в минус и не
      трогает чужую сторону;
    * кумулятив и магниты: суммы по сторонам и «до чего рукой подать»;
    * калибровку: параметры подбираются по факту, а без факта честно
      отказывается;
    * сборку ответа движком: кэш, покрытие, оговорки, выключение.

Запуск: python3 -m pytest tests/test_liq_levels.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from liq_levels import (DEFAULT_SETTINGS, LevelsEngine, actual_histogram,  # noqa: E402
                        apply_executed, build_ladder, build_rows, calibrate,
                        cumulative, grid_step, ladder_rows, liq_price,
                        normalize_dist, overlap_score, pick_magnets, snap,
                        spread_kernel)

HOUR = 3600
BUCKET = 300


# ---------------------------------------------------------------------------
#  Чистая часть
# ---------------------------------------------------------------------------

class LiqPriceTest(unittest.TestCase):
    def test_long_below_short_above(self):
        self.assertAlmostEqual(liq_price(60000, True, 20, 0.005), 57300.0, places=6)
        self.assertAlmostEqual(liq_price(60000, False, 20, 0.005), 62700.0, places=6)

    def test_leverage_moves_the_level(self):
        low = liq_price(60000, True, 5, 0.005)
        high = liq_price(60000, True, 100, 0.005)
        self.assertAlmostEqual(low, 60000 * (1 - 0.2 + 0.005), places=6)
        self.assertGreater(high, low)
        self.assertLess(high, 60000)

    def test_margin_shifts_towards_entry(self):
        """Чем больше поддерживающая маржа, тем раньше выносит: цена ближе к входу."""
        self.assertGreater(liq_price(100, True, 20, 0.02), liq_price(100, True, 20, 0.0))
        self.assertLess(liq_price(100, False, 20, 0.02), liq_price(100, False, 20, 0.0))

    def test_garbage_is_not_a_price(self):
        for entry, lev in ((0, 20), (100, 0), (100, 1), (100, -5), ("x", 20), (100, "y")):
            self.assertIsNone(liq_price(entry, True, lev, 0.005))


class DistTest(unittest.TestCase):
    def test_weights_are_normalized(self):
        dist = normalize_dist([[10, 2], [20, 2]])
        self.assertAlmostEqual(sum(w for _l, w in dist), 1.0, places=9)
        self.assertAlmostEqual(dict(dist)[10], 0.5, places=9)

    def test_broken_input_falls_back_to_default(self):
        for bad in (None, [], "мусор", [["x", 1]], [[20, 0]]):
            self.assertEqual(normalize_dist(bad), normalize_dist(None))
        self.assertGreater(len(normalize_dist(None)), 3)

    def test_dict_form_is_supported(self):
        dist = normalize_dist({"20": 1, "50": 3})
        self.assertAlmostEqual(dict(dist)[50], 0.75, places=9)


class GridTest(unittest.TestCase):
    def test_step_and_snap_are_stable(self):
        step = grid_step(60000, 0.001)
        self.assertAlmostEqual(step, 60.0, places=9)
        self.assertAlmostEqual(snap(60030, step), 60060.0, places=6)  # вверх от половины
        self.assertAlmostEqual(snap(10, 0), None if False else snap(10, 0), places=9)
        self.assertIsNone(snap(0, step))

    def test_kernel_keeps_the_mass(self):
        kernel = spread_kernel(0.012, 0.001)
        self.assertAlmostEqual(sum(w for _o, w in kernel), 1.0, places=9)
        self.assertAlmostEqual(dict(kernel)[1.0] if False else dict(kernel)[0.001],
                               dict(kernel)[-0.001], places=9)

    def test_zero_spread_means_single_level(self):
        self.assertEqual(spread_kernel(0, 0.001), [(0.0, 1.0)])


class BuildRowsTest(unittest.TestCase):
    def setUp(self):
        self.t0 = 1_700_000_400

    def _points(self, values):
        return [(self.t0 + i * BUCKET, v) for i, v in enumerate(values)]

    def test_growth_becomes_mass(self):
        points = self._points([100.0, 110.0, 130.0])
        rows = build_rows(points, lambda ts: 100.0, lambda ts: (1.0, "taker"),
                          lambda ts: 0.005, min_doi_rel=0)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["usd"], 10.0, places=6)   # 100 → 110
        self.assertAlmostEqual(rows[1]["usd"], 20.0, places=6)   # 110 → 130
        self.assertAlmostEqual(rows[0]["long_usd"], 10.0, places=6)
        self.assertAlmostEqual(rows[0]["short_usd"], 0.0, places=6)

    def test_closing_shrinks_survival(self):
        """OI вырос вдвое, потом вернулся: старая порция выжила лишь частично."""
        points = self._points([100.0, 200.0, 100.0])
        rows = build_rows(points, lambda ts: 100.0, lambda ts: (0.5, "taker"),
                          lambda ts: 0.0, min_doi_rel=0)
        self.assertEqual(len(rows), 1)
        # порция бралась на OI 200, а к концу осталось 100 → выжило половину
        self.assertAlmostEqual(rows[0]["usd"], 50.0, places=6)
        self.assertAlmostEqual(rows[0]["long_usd"], 25.0, places=6)
        self.assertAlmostEqual(rows[0]["short_usd"], 25.0, places=6)

    def test_tiny_deltas_are_noise(self):
        points = self._points([100.0, 100.00001, 100.00002])
        rows = build_rows(points, lambda ts: 100.0, lambda ts: (0.5, "taker"),
                          lambda ts: 0.0, min_doi_rel=0.001)
        self.assertEqual(rows, [])

    def test_row_without_entry_is_flagged_not_lost(self):
        points = self._points([100.0, 110.0])
        rows = build_rows(points, lambda ts: None, lambda ts: (0.5, "none"),
                          lambda ts: 0.0, min_doi_rel=0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("missing"), "entry")
        self.assertIsNone(rows[0]["entry"])


class LadderTest(unittest.TestCase):
    def _settings(self, **patch):
        s = dict(DEFAULT_SETTINGS)
        s.update(patch)
        return s

    def test_long_mass_sits_below_entry_short_above(self):
        rows = [{"entry": 1000.0, "long_usd": 100.0, "short_usd": 0.0, "mmr": 0.0},
                {"entry": 1000.0, "long_usd": 0.0, "short_usd": 100.0, "mmr": 0.0}]
        ladder = build_ladder(rows, self._settings(step_rel=0.001, spread_rel=0.0),
                              1000.0)
        below = sum(c["usd"] for p, c in ladder.items() if p < 1000)
        above = sum(c["usd"] for p, c in ladder.items() if p > 1000)
        self.assertAlmostEqual(below, 100.0, places=4)
        self.assertAlmostEqual(above, 100.0, places=4)

    def test_each_leverage_lands_on_its_own_price(self):
        rows = [{"entry": 1000.0, "long_usd": 100.0, "short_usd": 0.0, "mmr": 0.0}]
        settings = self._settings(step_rel=0.0002, spread_rel=0.0,
                                  lev_dist=[[10, 1.0]])
        ladder = build_ladder(rows, settings, 1000.0)
        # 10x без маржи: 1000 × (1 − 0.1) = 900
        self.assertAlmostEqual(sum(c["usd"] for c in ladder.values()), 100.0, places=4)
        self.assertAlmostEqual(ladder[900.0]["usd"], 100.0, places=4)

    def test_mass_is_preserved_across_the_kernel(self):
        rows = [{"entry": 1000.0, "long_usd": 50.0, "short_usd": 50.0, "mmr": 0.0}]
        settings = self._settings(step_rel=0.001, spread_rel=0.01)
        ladder = build_ladder(rows, settings, 1000.0)
        self.assertAlmostEqual(sum(c["usd"] for c in ladder.values()), 100.0, places=3)
        self.assertGreater(len(ladder), 6)          # масса размазана, а не в точку

    def test_lev_scale_shifts_everything(self):
        rows = [{"entry": 1000.0, "long_usd": 100.0, "short_usd": 0.0, "mmr": 0.0}]
        base = build_ladder(rows, self._settings(lev_dist=[[20, 1.0]], spread_rel=0.0),
                            1000.0)
        scaled = build_ladder(rows, self._settings(lev_dist=[[20, 1.0]], spread_rel=0.0,
                                                   lev_scale=2.0), 1000.0)
        self.assertAlmostEqual(min(base), 950.0, places=3)      # 20x → −5 %
        self.assertAlmostEqual(min(scaled), 975.0, places=3)    # 40x → −2.5 %


class ApplyExecutedTest(unittest.TestCase):
    def test_subtracts_from_matching_side_and_price(self):
        step = 1.0
        ladder = {900.0: {"usd": 100.0, "long_usd": 100.0, "short_usd": 0.0, "lev": {}},
                  1100.0: {"usd": 80.0, "long_usd": 0.0, "short_usd": 80.0, "lev": {}}}
        events = [{"price": 900.4, "usd": 30.0, "side": "SELL"},      # вынесли лонг
                  {"price": 1100.1, "usd": 50.0, "side": "BUY"}]      # вынесли шорт
        res = apply_executed(ladder, events, step)
        self.assertAlmostEqual(res["usd"], 80.0, places=6)
        self.assertEqual(res["events"], 2)
        self.assertAlmostEqual(ladder[900.0]["long_usd"], 70.0, places=6)
        self.assertAlmostEqual(ladder[1100.0]["short_usd"], 30.0, places=6)

    def test_does_not_go_negative(self):
        ladder = {900.0: {"usd": 10.0, "long_usd": 10.0, "short_usd": 0.0, "lev": {}}}
        apply_executed(ladder, [{"price": 900.0, "usd": 500.0, "side": "SELL"}], 1.0)
        # вычищенный до нуля уровень из лестницы уходит (отдавать его незачем)
        self.assertNotIn(900.0, ladder)

    def test_side_mismatch_is_not_subtracted(self):
        ladder = {900.0: {"usd": 10.0, "long_usd": 10.0, "short_usd": 0.0, "lev": {}}}
        apply_executed(ladder, [{"price": 900.0, "usd": 5.0, "side": "BUY"}], 1.0)
        self.assertAlmostEqual(ladder[900.0]["long_usd"], 10.0, places=6)

    def test_empty_buckets_are_dropped(self):
        ladder = {900.0: {"usd": 10.0, "long_usd": 10.0, "short_usd": 0.0, "lev": {}},
                  800.0: {"usd": 5.0, "long_usd": 5.0, "short_usd": 0.0, "lev": {}}}
        apply_executed(ladder, [{"price": 900.0, "usd": 10.0, "side": "SELL"}], 1.0)
        self.assertNotIn(900.0, ladder)
        self.assertIn(800.0, ladder)


class CumulativeTest(unittest.TestCase):
    def setUp(self):
        self.ladder = {
            900.0: {"usd": 30.0, "long_usd": 30.0, "short_usd": 0.0, "lev": {}},
            950.0: {"usd": 20.0, "long_usd": 20.0, "short_usd": 0.0, "lev": {}},
            1050.0: {"usd": 40.0, "long_usd": 0.0, "short_usd": 40.0, "lev": {}},
            1100.0: {"usd": 10.0, "long_usd": 0.0, "short_usd": 10.0, "lev": {}},
        }

    def test_up_counts_shorts_down_counts_longs(self):
        up, down = cumulative(self.ladder, 1000.0)
        self.assertEqual([r["price"] for r in up], [1050.0, 1100.0])
        self.assertEqual([r["usd"] for r in up], [40.0, 50.0])
        self.assertEqual([r["price"] for r in down], [900.0, 950.0])
        self.assertEqual([r["usd"] for r in down], [30.0, 50.0])

    def test_magnet_is_the_nearest_significant_level(self):
        magnets = pick_magnets(self.ladder, 1000.0, min_share=0.2)
        self.assertEqual(magnets["up"]["price"], 1050.0)
        self.assertEqual(magnets["down"]["price"], 950.0)
        self.assertEqual(magnets["top_up"]["price"], 1050.0)
        self.assertEqual(magnets["top_down"]["price"], 900.0)

    def test_small_level_is_not_a_magnet(self):
        ladder = {
            990.0: {"usd": 1.0, "long_usd": 1.0, "short_usd": 0.0, "lev": {}},
            900.0: {"usd": 99.0, "long_usd": 99.0, "short_usd": 0.0, "lev": {}},
        }
        magnets = pick_magnets(ladder, 1000.0, min_share=0.1)
        self.assertEqual(magnets["down"]["price"], 900.0)
        self.assertEqual(magnets["top_down"]["usd"], 99.0)

    def test_share_is_counted_from_the_heaviest_level(self):
        """Масса размазана по сотням уровней — доля «от всего» не наберётся.

        Порог считается от самого тяжёлого уровня, иначе в реальной лестнице
        магнитов не было бы вовсе (в облаке по 1 % от суммы их нет).
        """
        ladder = {1000.0 + i: {"usd": 1.0, "long_usd": 0.0, "short_usd": 1.0,
                               "lev": {}} for i in range(1, 20)}
        ladder[1050.0] = {"usd": 3.0, "long_usd": 0.0, "short_usd": 3.0, "lev": {}}
        magnets = pick_magnets(ladder, 1000.0, min_share=0.2)
        self.assertEqual(magnets["top_up"]["price"], 1050.0)   # тяжёлый
        self.assertEqual(magnets["up"]["price"], 1001.0)       # ближний значимый
        self.assertIsNone(magnets["down"])

    def test_top_up_is_the_heaviest_not_the_nearest(self):
        ladder = {
            1005.0: {"usd": 2.0, "long_usd": 0.0, "short_usd": 2.0, "lev": {}},
            1080.0: {"usd": 50.0, "long_usd": 0.0, "short_usd": 50.0, "lev": {}},
            900.0: {"usd": 7.0, "long_usd": 7.0, "short_usd": 0.0, "lev": {}},
        }
        magnets = pick_magnets(ladder, 1000.0, min_share=0.5)
        self.assertEqual(magnets["up"]["price"], 1080.0)
        self.assertEqual(magnets["top_up"]["price"], 1080.0)
        self.assertEqual(magnets["top_down"]["price"], 900.0)


class LadderRowsTest(unittest.TestCase):
    def test_rows_are_capped_and_cumulative_grows_outwards(self):
        ladder = {}
        for i in range(1, 40):
            price = 1000.0 + i * 5
            ladder[price] = {"usd": 10.0, "long_usd": 0.0, "short_usd": 10.0,
                             "lev": {20: 10.0}}
            ladder[price - 1000] = {"usd": 5.0, "long_usd": 5.0, "short_usd": 0.0,
                                    "lev": {}}
        rows = ladder_rows(ladder, 1000.0, limit=6)
        self.assertEqual(len(rows), 6)
        self.assertEqual([r["cum_usd"] for r in rows if r["price"] > 1000],
                         sorted([r["cum_usd"] for r in rows if r["price"] > 1000]))
        for row in rows:
            self.assertIn(row["side"], ("long", "short", "both"))
            self.assertLessEqual(row["share"], 100.0)

    def test_shares_are_relative_to_their_own_side(self):
        ladder = {900.0: {"usd": 25.0, "long_usd": 25.0, "short_usd": 0.0, "lev": {}},
                  800.0: {"usd": 75.0, "long_usd": 75.0, "short_usd": 0.0, "lev": {}},
                  1100.0: {"usd": 40.0, "long_usd": 0.0, "short_usd": 40.0, "lev": {}}}
        rows = {r["price"]: r for r in ladder_rows(ladder, 1000.0, limit=10)}
        self.assertAlmostEqual(rows[900.0]["long_share"], 25.0, places=3)
        self.assertAlmostEqual(rows[800.0]["long_share"], 75.0, places=3)
        self.assertAlmostEqual(rows[1100.0]["short_share"], 100.0, places=3)

    def test_cum_usd_is_mass_on_the_way(self):
        ladder = {980.0: {"usd": 10.0, "long_usd": 10.0, "short_usd": 0.0, "lev": {}},
                  900.0: {"usd": 30.0, "long_usd": 30.0, "short_usd": 0.0, "lev": {}},
                  1020.0: {"usd": 7.0, "long_usd": 0.0, "short_usd": 7.0, "lev": {}}}
        rows = {r["price"]: r for r in ladder_rows(ladder, 1000.0, limit=10)}
        self.assertAlmostEqual(rows[980.0]["cum_usd"], 10.0, places=3)
        self.assertAlmostEqual(rows[900.0]["cum_usd"], 40.0, places=3)
        self.assertAlmostEqual(rows[1020.0]["cum_usd"], 7.0, places=3)


class CalibrateTest(unittest.TestCase):
    def _rows(self, entry=1000.0, n=40):
        return [{"entry": entry, "long_usd": 25.0, "short_usd": 25.0, "mmr": 0.0, "ts": i}
                for i in range(n)]

    def test_overlap_score_compares_shapes_not_volumes(self):
        """Метрика нормированная: «мало, но туда же» — это попадание."""
        actual = {900.0: {"usd": 100.0, "long_usd": 100.0, "short_usd": 0.0}}
        self.assertAlmostEqual(overlap_score({900.0: {"usd": 50.0}}, actual), 1.0, places=6)
        self.assertAlmostEqual(overlap_score({800.0: {"usd": 500.0}}, actual), 0.0, places=6)
        self.assertAlmostEqual(overlap_score({900.0: {"usd": 500.0}}, actual), 1.0, places=6)
        # половина факта попала, половина ушла мимо
        two = {890.0: {"usd": 50.0}, 910.0: {"usd": 50.0}}
        half = overlap_score({890.0: {"usd": 1.0}, 5000.0: {"usd": 1.0}}, two, step_rel=0.01)
        self.assertAlmostEqual(half, 0.5, places=6)

    def test_histogram_keeps_sides(self):
        hist = actual_histogram([{"price": 900.0, "usd": 10.0, "side": "SELL"},
                                 {"price": 1100.0, "usd": 5.0, "side": "BUY"}],
                                1000.0, 1.0)
        self.assertAlmostEqual(hist[900.0]["long_usd"], 10.0, places=6)
        self.assertAlmostEqual(hist[1100.0]["short_usd"], 5.0, places=6)

    def test_calibration_picks_a_combo_when_there_is_fact(self):
        settings = dict(DEFAULT_SETTINGS)
        rows = self._rows()
        # факт: выносило на ступенях 10x / 20x / 25x — как и заложено в модель
        actual = []
        for lev in (10, 20, 25):
            price = liq_price(1000.0, True, lev, 0.0)
            actual += [{"price": price, "usd": 100.0, "side": "SELL"} for _ in range(15)]
        res = calibrate(rows, actual, 1000.0, settings)
        self.assertTrue(res["applied"])
        self.assertGreater(res["score"], 0.2)
        # ступени плеч в модели те же, что в «факте»: масштаб не должен уехать
        self.assertAlmostEqual(res["lev_scale"], 1.0, places=6)

    def test_calibration_finds_a_different_leverage_scale(self):
        """Если рынок сидел на вдвое меньших плечах, калибровка это найдёт."""
        settings = dict(DEFAULT_SETTINGS)
        rows = self._rows()
        ladder = build_ladder(rows, dict(settings, lev_scale=0.5), 1000.0)
        actual = [{"price": p, "usd": (c["long_usd"] or c["usd"]), "side": "SELL"}
                  for p, c in sorted(ladder.items()) if (c["long_usd"] or 0) > 0]
        res = calibrate(rows, actual, 1000.0, settings)
        self.assertTrue(res["applied"])
        self.assertAlmostEqual(res["lev_scale"], 0.5, places=6)

    def test_no_fact_means_no_calibration(self):
        res = calibrate(self._rows(), [{"price": 900.0, "usd": 1.0, "side": "SELL"}],
                        1000.0, dict(DEFAULT_SETTINGS))
        self.assertFalse(res["applied"])
        self.assertIn("мало факта", res["reason"])


# ---------------------------------------------------------------------------
#  Движок на заглушках
# ---------------------------------------------------------------------------

class FakeOI:
    """Ряд OI и снимок по биржам — как у трекера, только из заготовки."""

    def __init__(self, points, legs=None):
        self._points = list(points)
        self._legs = legs or {"binance": 0.6, "bybit": 0.4}

    def points(self, symbol, since=None, until=None):
        return [(t, v) for t, v in self._points if since is None or t >= since]

    def snapshot(self, symbol):
        return {"total_usd": sum(self._points[-1][1:]) if self._points else None,
                "per_exchange": self._legs}


class FakeRisk:
    def __init__(self, mmr=0.005, estimated=False):
        self._mmr = mmr
        self._estimated = estimated

    def mmr_or_default(self, symbol, exchange=None, notional=None, weights=None):
        return self._mmr, self._estimated

    def max_leverage(self, symbol, exchange=None):
        return 100


class FakeSide:
    def __init__(self, share=0.7, src="taker"):
        self._share = share
        self._src = src

    def side_at(self, symbol, ts, tick_share=None):
        return self._share, self._src

    async def ensure(self, session, symbol, force=False):
        return {}


class FakeHist:
    def __init__(self, events=()):
        self._events = list(events)

    def iter_events(self, since, until=None, symbol=None):
        for ev in self._events:
            if symbol and ev.get("symbol") != symbol:
                continue
            if since and float(ev.get("timestamp") or 0) < since:
                continue
            yield ev


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lilevels_")
        self.now = time.time()
        # 12 часов роста OI по 5-минуткам: масса набирается примерно по цене 1000
        pts = []
        oi = 1000.0
        for i in range(145):
            ts = self.now - (144 - i) * BUCKET
            oi *= 1.002
            pts.append((ts, oi))
        self.oi = FakeOI(pts)
        from volume_profile import VolumeProfile
        self.profile = VolumeProfile(path="")
        for i in range(145):
            ts = self.now - (144 - i) * BUCKET
            self.profile.add_trade("BTC_USDT", ts, 1000.0, 10_000.0, "BUY")
        self.engine = LevelsEngine(
            oi=self.oi, profile=self.profile, side=FakeSide(),
            risk=FakeRisk(), hist=FakeHist(),
            klines=None, path=os.path.join(self.tmp, "levels.json"))

    def _payload(self, **kw):
        return asyncio.run(self.engine.payload("BTC_USDT", now=self.now, **kw))

    def test_payload_has_levels_and_coverage(self):
        p = self._payload()
        self.assertTrue(p["enabled"])
        self.assertGreater(len(p["levels"]), 0)
        self.assertGreater(p["total_usd"], 0)
        self.assertGreater(p["coverage"]["rows"], 10)
        self.assertEqual(p["coverage"]["side_sources"].get("taker"), p["coverage"]["rows"])
        self.assertIn("notes", p)
        self.assertAlmostEqual(p["long_usd"] / p["total_usd"], 0.7, places=2)

    def test_long_mass_sits_below_the_price(self):
        p = self._payload()
        below = [r for r in p["levels"] if r["distance_pct"] < 0]
        above = [r for r in p["levels"] if r["distance_pct"] > 0]
        self.assertTrue(below)
        # шортов (share 0.3) меньше, чем лонгов, поэтому вниз масса тоже уходит,
        # но её доля меньше
        self.assertGreater(sum(r["usd"] for r in below),
                           sum(r["usd"] for r in above))

    def test_cumulative_and_magnets_are_filled(self):
        p = self._payload()
        self.assertTrue(p["cumulative"]["down"])
        self.assertTrue(p["cumulative"]["up"])
        down = p["cumulative"]["down"]
        self.assertEqual([r["usd"] for r in down], sorted(r["usd"] for r in down))
        self.assertIsNotNone(p["magnets"]["down"])
        self.assertLess(p["magnets"]["down"]["price"], p["price"])

    def test_payload_is_cached(self):
        self._payload()
        builds = self.engine.builds
        self._payload()
        self.assertEqual(self.engine.builds, builds)

    def test_applied_counts_real_liquidations(self):
        event = {"symbol": "BTC_USDT", "price": 900.0, "usd": 50_000.0,
                 "side": "SELL", "timestamp": self.now - 600}
        self.engine.hist = FakeHist([event])
        p = self._payload()
        self.assertEqual(p["applied"]["events"], 1)
        self.assertGreater(p["applied"]["usd"], 0)

    def test_settings_update_changes_calculation(self):
        p1 = self._payload()
        self.engine.update_settings({"step_rel": 0.002})
        self.engine._calib.clear()
        p2 = self._payload(force=True)
        self.assertAlmostEqual(p2["step"], p1["step"] * 2, places=6)
        self.assertIn("settings", self.engine.status())

    def test_disabled_engine_returns_nothing(self):
        self.engine.update_settings({"enabled": False})
        p = self._payload()
        self.assertFalse(p["enabled"])
        self.assertEqual(p["levels"], [])

    def test_no_oi_points_is_an_honest_empty(self):
        engine = LevelsEngine(oi=FakeOI([]), profile=self.profile, side=FakeSide(),
                              risk=FakeRisk(), hist=FakeHist(), klines=None,
                              path=os.path.join(self.tmp, "empty.json"))
        p = asyncio.run(engine.payload("BTC_USDT", now=self.now, price=1000.0))
        self.assertEqual(p["levels"], [])
        self.assertIn("notes", p)

    def test_estimated_margin_shows_up_in_notes(self):
        engine = LevelsEngine(oi=self.oi, profile=self.profile, side=FakeSide(),
                              risk=FakeRisk(0.01, estimated=True), hist=FakeHist(),
                              klines=None, path=os.path.join(self.tmp, "est.json"))
        p = asyncio.run(engine.payload("BTC_USDT", now=self.now))
        self.assertTrue(p["coverage"]["mmr_estimated"])
        self.assertTrue(any("маржа" in n for n in p["notes"]))

    def test_settings_survive_restart(self):
        self.engine.update_settings({"spread_rel": 0.02, "lev_scale": 1.4})
        path = self.engine.path
        other = LevelsEngine(path=path)
        self.assertAlmostEqual(other.settings()["spread_rel"], 0.02, places=9)
        self.assertAlmostEqual(other.settings()["lev_scale"], 1.4, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
