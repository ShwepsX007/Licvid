"""Тесты сервиса «Корреляции валют» (correlations.py).

Сервис отвечает на вопросы: у каких пар совпадали ликвидации,
объём, CVD и рост OI; где выносило лонги, а где шорты; где перекос CVD на
продавцов, а где на покупателей; где OI растёт, а где падает. Считаем по
часовым свёрткам месячной истории и снимкам OI.

Запуск: /tmp/venv/bin/python tests/test_correlations.py
"""
import calendar
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from correlations import (  # noqa: E402
    ALERT_THRESHOLDS, DEFAULT_ALERT_OPP, DEFAULT_ALERT_SAME, DEFAULT_METRIC,
    DEFAULT_WINDOW, MIN_ALERT_GAP_SEC, MIN_POINTS, WINDOWS, alert_gap_sec,
    build, evaluate_alerts, find_pairs, format_alert_html, format_alerts_config,
    format_text, metric_title, normalize_alerts, oi_hourly, pearson, window_key,
    window_label, window_minutes,
)

HOUR = 3600
NOW = float(calendar.timegm((2026, 9, 17, 18, 30, 0, 0, 0, 0)))


def cell(h, **syms):
    """Часовая свёртка: cell(h, BTC_USDT=dict(usd=1e5, vol=2e6, n=3, cvd=-1e5))."""
    out = {"liq_usd": 0.0, "liq_count": 0, "liq_long": 0.0, "liq_short": 0.0,
           "cvd": 0.0, "vol": 0.0, "sym": {}, "exch": {}}
    for sym, val in syms.items():
        row = {"usd": 0.0, "n": 0, "long": 0.0, "short": 0.0, "cvd": 0.0, "vol": 0.0}
        row.update(val)
        out["sym"][sym] = row
        out["liq_usd"] += row["usd"]
        out["liq_count"] += row["n"]
        out["liq_long"] += row["long"]
        out["liq_short"] += row["short"]
        out["cvd"] += row["cvd"]
        out["vol"] += row["vol"]
    return out


class TestWindows(unittest.TestCase):
    def test_known_windows(self):
        self.assertEqual(window_minutes("1h"), 60)
        self.assertEqual(window_minutes("24h"), 1440)
        self.assertEqual(window_minutes("7d"), 10080)
        self.assertEqual(window_minutes("мусор"), window_minutes(DEFAULT_WINDOW))

    def test_labels(self):
        self.assertEqual(window_label("1h"), "1 ч")
        self.assertEqual(window_label("24h"), "1 дн")
        self.assertEqual(window_label("3d"), "3 дн")

    def test_key_by_minutes(self):
        self.assertEqual(window_key(1440), "24h")
        self.assertEqual(window_key(240), "4h")
        self.assertEqual(window_key("плохо"), DEFAULT_WINDOW)
        self.assertTrue(WINDOWS)

    def test_metric_title(self):
        self.assertEqual(metric_title("cvd"), "CVD")
        self.assertEqual(metric_title("нет"), "Ликвидации")


class TestPearson(unittest.TestCase):
    def test_perfect_positive(self):
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0, places=3)

    def test_perfect_negative(self):
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [8, 6, 4, 2]), -1.0, places=3)

    def test_flat_series_is_undefined(self):
        self.assertIsNone(pearson([5, 5, 5], [1, 2, 3]))

    def test_too_short(self):
        self.assertIsNone(pearson([1], [2]))
        self.assertIsNone(pearson([], []))

    def test_rounding(self):
        r = pearson([1, 2, 3, 4, 5], [2, 1, 4, 3, 5])
        self.assertEqual(r, round(r, 3))


class TestOiHourly(unittest.TestCase):
    def test_averages_within_hour(self):
        h = int(NOW // HOUR * HOUR)
        rows = oi_hourly([(h + 60, 100.0), (h + 120, 300.0), (h + 3600 + 5, 500.0)])
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0][1], 200.0, places=2)
        self.assertAlmostEqual(rows[1][1], 500.0, places=2)

    def test_bad_values_skipped(self):
        self.assertEqual(oi_hourly([(0, 100), (NOW, 0), (NOW, "x")]), [])


class TestBuild(unittest.TestCase):
    def make_cells(self, hours=13):
        """BTC и ETH живут синхронно, SOL — в противофазе.

        Часы идут до текущего часа включительно и покрывают окно 12 ч целиком:
        иначе пустые часы в начале окна ломают линейную связь рядов.
        """
        cells = []
        for i in range(hours):
            h = int(NOW // HOUR * HOUR) - (hours - 1 - i) * HOUR
            btc = 100000 + i * 50000
            eth = 80000 + i * 40000
            sol = 200000 - i * 15000
            cells.append((h, cell(
                h,
                BTC_USDT={"usd": btc, "n": 3, "long": btc * 0.7, "short": btc * 0.3,
                          "cvd": -btc * 0.2, "vol": btc * 8},
                ETH_USDT={"usd": eth, "n": 2, "long": eth * 0.2, "short": eth * 0.8,
                          "cvd": eth * 0.3, "vol": eth * 9},
                SOL_USDT={"usd": sol, "n": 1, "long": sol, "short": 0.0,
                          "cvd": -sol * 0.9, "vol": sol * 6},
            )))
        return cells

    def test_defaults(self):
        res = build(self.make_cells(), now=NOW)
        self.assertEqual(res["window"], "24h")
        self.assertEqual(res["metric"], DEFAULT_METRIC)
        self.assertEqual(res["window_label"], "1 дн")
        self.assertTrue(res["windows"] and res["metrics"])

    def test_liq_matrix_finds_twins(self):
        res = build(self.make_cells(), now=NOW, window="12h")
        m = res["matrices"]["liq"]
        self.assertAlmostEqual(m["BTC_USDT"]["ETH_USDT"], 1.0, places=2)
        self.assertAlmostEqual(m["BTC_USDT"]["SOL_USDT"], -1.0, places=2)
        self.assertEqual(m["BTC_USDT"]["BTC_USDT"], 1.0)

    def test_best_pairs_sorted(self):
        res = build(self.make_cells(), now=NOW, window="12h")
        pairs = res["pairs"]["liq"]
        self.assertTrue(pairs)
        self.assertAlmostEqual(abs(pairs[0]["r"]), 1.0, places=2)
        self.assertEqual({pairs[0]["a"], pairs[0]["b"]},
                         {"BTC_USDT", "ETH_USDT"})

    def test_coins_carry_flows(self):
        res = build(self.make_cells(), now=NOW, window="12h")
        c = res["coins"]["ETH_USDT"]
        self.assertGreater(c["liq_short"], c["liq_long"])     # у ETH выносило шорты
        self.assertGreater(c["cvd"], 0)
        self.assertGreater(c["cvd_share"], 0)
        b = res["coins"]["BTC_USDT"]
        self.assertGreater(b["liq_long"], b["liq_short"])     # у BTC — лонги
        self.assertLess(b["cvd"], 0)

    def test_flows_lists(self):
        res = build(self.make_cells(), now=NOW, window="12h")
        f = res["flows"]
        self.assertEqual(f["liquidated_long"][0], "BTC_USDT")
        self.assertEqual(f["liquidated_short"][0], "ETH_USDT")
        self.assertEqual(f["cvd_sellers"][0], "SOL_USDT")
        self.assertEqual(f["cvd_buyers"][0], "ETH_USDT")

    def test_oi_series_gives_direction(self):
        h0 = int(NOW // HOUR * HOUR) - 5 * HOUR
        oi = {
            # OI BTC растёт, ETH падает
            "BTC_USDT": [(h0 + i * HOUR, 1e9 + i * 5e7) for i in range(6)],
            "ETH_USDT": [(h0 + i * HOUR, 3e9 - i * 4e7) for i in range(6)],
            "SOL_USDT": [(h0 + i * HOUR, 8e8) for i in range(6)],
        }
        res = build(self.make_cells(), oi_series=oi, now=NOW, window="12h")
        self.assertEqual(res["flows"]["oi_up"][0], "BTC_USDT")
        self.assertEqual(res["flows"]["oi_down"][0], "ETH_USDT")
        btc = res["coins"]["BTC_USDT"]
        self.assertGreater(btc["oi_delta"], 0)
        self.assertIsNotNone(btc["oi_pct"])
        self.assertIsNone(res["coins"]["DOGE_USDT"]["oi_delta"]
                          if "DOGE_USDT" in res["coins"] else None)
        # у SOL OI ровный: изменение небольшое, связь не выдумываем
        m = res["matrices"]["oi"]
        self.assertIsNone(m["SOL_USDT"]["BTC_USDT"])

    def test_metric_matrix_differs(self):
        res = build(self.make_cells(), now=NOW, window="12h")
        self.assertIn("cvd", res["matrices"])
        self.assertIn("vol", res["matrices"])
        self.assertAlmostEqual(res["matrices"]["vol"]["BTC_USDT"]["ETH_USDT"],
                               1.0, places=2)

    def test_min_points_guard(self):
        cells = self.make_cells(hours=3)          # точек мало (< MIN_POINTS)
        res = build(cells, now=NOW, window="12h")
        self.assertIsNone(res["matrices"]["liq"]["BTC_USDT"]["ETH_USDT"])
        self.assertGreaterEqual(res["min_points"], MIN_POINTS)

    def test_empty_data(self):
        res = build([], now=NOW)
        self.assertEqual(res["symbols"], [])
        self.assertEqual(res["coins"], {})
        self.assertIsNone(res["matrices"]["liq"].get("BTC_USDT"))


class TestText(unittest.TestCase):
    def test_ru_text(self):
        res = build(TestBuild().make_cells(), now=NOW, window="24h")
        text = format_text(res, "ru", site="https://liqscope.online/cabinet")
        self.assertIn("Корреляции валют", text)
        self.assertIn("Шли вместе", text)
        self.assertIn("BTC, ETH", text)
        self.assertIn("Где выносило шорты", text)
        self.assertIn("CVD на сторону продавцов", text)
        self.assertIn("OI растёт", text)
        self.assertIn("liqscope.online", text)
        self.assertIn("лишних", "лишних")   # текст не пустой и без мусора
        self.assertNotIn("None", text)
        self.assertNotIn("nan", text.lower())

    def test_en_text(self):
        res = build(TestBuild().make_cells(), now=NOW)
        text = format_text(res, "en")
        self.assertIn("Coin correlations", text)
        self.assertIn("Where shorts burned", text)
        self.assertNotIn("Корреляции", text)

    def test_text_without_data(self):
        res = build([], now=NOW)
        text = format_text(res, "ru")
        self.assertIn("Устойчивых связей пока нет", text)


class TestAlerts(unittest.TestCase):
    """Алерты по корреляции: у каждой переменной своё окно и свои пороги.

    Требование пользователя: настройки — по каждой метрике отдельно. «В
    противофазе» — порог по минусу (окно 1 ч и −0.5: ждём расхождение монет),
    «в одну сторону» — по плюсу (0.5 значит «коэффициент 0.5 и выше»).
    """

    def test_defaults_are_off_and_per_metric(self):
        cfg = normalize_alerts({})
        self.assertEqual(set(cfg), {"liq", "vol", "cvd", "oi"})
        for row in cfg.values():
            self.assertFalse(row["enabled"])
            self.assertEqual(row["window"], DEFAULT_WINDOW)
            self.assertEqual(row["opp"], DEFAULT_ALERT_OPP)
            self.assertEqual(row["same"], DEFAULT_ALERT_SAME)

    def test_per_metric_settings_are_kept(self):
        cfg = normalize_alerts({"alerts": {
            "liq": {"enabled": True, "window": "1h", "opp": -0.5},
            "cvd": {"on": True, "window": 240, "same": 0.5},
        }})
        self.assertTrue(cfg["liq"]["enabled"])
        self.assertEqual(cfg["liq"]["window"], "1h")
        self.assertEqual(cfg["liq"]["opp"], -0.5)
        self.assertTrue(cfg["cvd"]["enabled"])
        self.assertEqual(cfg["cvd"]["window"], "4h")
        self.assertEqual(cfg["cvd"]["same"], 0.5)
        self.assertFalse(cfg["vol"]["enabled"])          # остальные не тронуты

    def test_thresholds_are_clamped(self):
        cfg = normalize_alerts({"liq": {"enabled": True, "opp": -5, "same": 9}})
        self.assertEqual(cfg["liq"]["opp"], -1.0)
        self.assertEqual(cfg["liq"]["same"], 1.0)

    def test_find_pairs_includes_threshold(self):
        """0.5 значит «выше порога или равен ему» — включающий порог."""
        matrix = {"A": {"A": 1.0, "B": 0.5, "C": -0.5, "D": 0.49}}
        same = find_pairs(matrix, -0.9, 0.5)
        self.assertEqual([p["a"] for p in same], ["A"])
        self.assertEqual([p["b"] for p in same], ["B"])
        self.assertEqual(same[0]["kind"], "same")
        opp = find_pairs(matrix, -0.5, 0.9)
        self.assertEqual([p["b"] for p in opp], ["C"])
        self.assertEqual(opp[0]["kind"], "opp")

    def test_evaluate_alerts_gives_one_card_per_metric(self):
        cells = TestBuild().make_cells()
        pic = build(cells, now=NOW, window="12h")
        cfg = normalize_alerts({"alerts": {
            "liq": {"enabled": True, "window": "12h", "opp": -0.5, "same": 0.9},
            "cvd": {"enabled": False},
        }})
        hits = evaluate_alerts(cfg, {"12h": pic})
        self.assertEqual([h["metric"] for h in hits], ["liq"])
        hit = hits[0]
        self.assertEqual(hit["kind"], "same")
        self.assertIn("|", hit["symbol"])
        self.assertEqual(hit["threshold"], 0.9)
        self.assertEqual(hit["window_label"], "12 ч")
        # «ещё …» — только связи того же знака, что и лидер
        for peer in hit["peers"]:
            self.assertEqual(peer["kind"], "same")
        text = format_alert_html(hit, "https://liqscope.online")
        self.assertIn("Алерт · корреляции", text)
        self.assertIn("в одну сторону", text)
        self.assertIn("тепловая карта", text)

    def test_antiphase_hit_carries_pair_and_threshold(self):
        """Пример пользователя: окно 1 ч и −0.5 — ждём противофазу монет."""
        pic = {"matrices": {"liq": {"BTC_USDT": {"BTC_USDT": 1.0, "SOL_USDT": -0.72},
                                    "SOL_USDT": {"BTC_USDT": -0.72, "SOL_USDT": 1.0}}},
               "symbols": ["BTC_USDT", "SOL_USDT"], "hours": 12}
        cfg = normalize_alerts({"liq": {"enabled": True, "window": "1h",
                                        "opp": -0.5, "same": 0.5}})
        hits = evaluate_alerts(cfg, {"1h": pic})
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit["kind"], "opp")
        self.assertEqual(hit["symbol"], "BTC_USDT|SOL_USDT")
        self.assertEqual(hit["value"], -0.72)
        self.assertEqual(hit["threshold"], -0.5)
        self.assertEqual(hit["window_label"], "1 ч")
        text = format_alert_html(hit, "https://liqscope.online")
        self.assertIn("в противофазе", text)
        self.assertIn("-0.72", text)
        self.assertIn("cabinet#correlations", text)

    def test_limits_and_gap(self):
        self.assertEqual(alert_gap_sec(60), max(MIN_ALERT_GAP_SEC, 900.0))
        self.assertEqual(alert_gap_sec(1440), 6 * 3600.0)
        self.assertTrue(ALERT_THRESHOLDS and 0.5 in ALERT_THRESHOLDS)

    def test_config_text_lists_every_metric(self):
        cfg = normalize_alerts({"liq": {"enabled": True, "window": "1h",
                                        "opp": -0.5, "same": 0.5}})
        text = format_alerts_config(cfg)
        self.assertIn("Алерты по корреляции", text)
        for title in ("Ликвидации", "Объём", "CVD", "OI"):
            self.assertIn(title, text)
        self.assertIn("выключен", text)
        self.assertIn("включён", text)
        self.assertIn("-0.50", text)
        self.assertIn("+0.50", text)


class TestCorrDelivery(unittest.TestCase):
    """Серверная доставка алертов по корреляции: окна, пауза, лимит карточек."""

    @classmethod
    def setUpClass(cls):
        os.environ["LIQSCOPE_ACCOUNTS_DB"] = ""
        import server
        cls.srv = server

    def test_pictures_only_for_enabled_windows(self):
        srv = self.srv
        asked: list = []
        original = srv.correlations_snapshot

        def fake(window="24h", metric="liq"):
            asked.append((window, metric))
            return {"matrices": {}, "symbols": [], "hours": 0}

        srv.correlations_snapshot = fake
        try:
            pics = srv.corr_alert_pictures({"liq": {"enabled": True, "window": "1h"},
                                            "cvd": {"enabled": True, "window": "1h"},
                                            "vol": {"enabled": False, "window": "7d"}})
        finally:
            srv.correlations_snapshot = original
        self.assertEqual(set(pics), {"1h"})            # одно окно — один расчёт
        self.assertEqual(len(asked), 1)

    def test_due_respects_gap_and_limit(self):
        srv = self.srv
        pic = {"matrices": {"liq": {
            "A": {"A": 1.0, "B": -0.9, "C": -0.8, "D": -0.7},
            "B": {"A": -0.9, "B": 1.0, "C": -0.85, "D": -0.75},
            "C": {"A": -0.8, "B": -0.85, "C": 1.0, "D": -0.65},
            "D": {"A": -0.7, "B": -0.75, "C": -0.65, "D": 1.0}}},
            "symbols": ["A", "B", "C", "D"], "hours": 12}
        cfg = normalize_alerts({"liq": {"enabled": True, "window": "1h",
                                        "opp": -0.5, "same": 0.5}})
        fired: dict = {}

        def last_fire(metric, symbol):
            return fired.get((metric, symbol))

        hits = srv.corr_alerts_due(cfg, {"1h": pic}, last_fire, NOW)
        self.assertEqual(len(hits), 1)                 # одна карточка на метрику
        hit = hits[0]
        self.assertEqual(hit["symbol"], "A|B")
        fired[("liq", hit["symbol"])] = NOW
        self.assertEqual(srv.corr_alerts_due(cfg, {"1h": pic}, last_fire, NOW + 60), [])
        later = srv.corr_alerts_due(cfg, {"1h": pic}, last_fire, NOW + 3600)
        self.assertEqual(len(later), 1)                # окно прошло — сигнал снова


if __name__ == "__main__":
    unittest.main(verbosity=2)
