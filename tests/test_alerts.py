"""Алерты: окно, порог, мин. удар, конфиг."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from alerts import (  # noqa: E402
    THRESHOLD_PRESETS_FLOW, canon_symbol, cooldown_sec, cvd_by_symbol, evaluate,
    flow_rows, format_alert_html, liq_by_symbol, live_snapshot, money,
    normalize_config, oi_by_symbol, oi_points, oi_window_key, presets,
    should_fire, sparkline, symbol_of, threshold_presets, window_label,
)


def _liq(sym, usd, ts, side="SELL"):
    return {"symbol": sym, "usd": usd, "timestamp": ts, "side": side,
            "exchange": "binance"}


class AlertsTest(unittest.TestCase):
    def test_canon_and_labels(self):
        self.assertEqual(canon_symbol("btc"), "BTC")
        self.assertEqual(canon_symbol("btcusdt"), "BTC_USDT")
        self.assertEqual(canon_symbol("eth-usdt"), "ETH_USDT")
        self.assertEqual(canon_symbol("all"), "ALL")
        self.assertEqual(window_label(5), "5м")
        self.assertEqual(window_label(60), "1ч")
        self.assertEqual(window_label(240), "4ч")
        self.assertEqual(oi_window_key(5), "m5")
        self.assertEqual(oi_window_key(7), "m5")
        self.assertEqual(oi_window_key(60), "h1")
        self.assertIn("K", money(12_500))

    def test_window_presets_are_five(self):
        """Кнопок окна агрегации ровно пять — и в боте, и в кабинете."""
        from alerts import WINDOW_PRESETS, presets
        self.assertEqual(len(WINDOW_PRESETS), 5)
        self.assertEqual(list(WINDOW_PRESETS), [1, 15, 30, 60, 240])
        self.assertNotIn(5, WINDOW_PRESETS)          # «5м» убрана
        self.assertEqual(presets()["windows"], list(WINDOW_PRESETS))

    def test_normalize_io_alias_and_flat_threshold(self):
        c = normalize_config({
            "watch": ["IO", "liquidations"],
            "symbol": "btc-usdt",
            "window": 15,
            "threshold": 250000,
            "min_event": 10000,
            "enabled": True,
        })
        self.assertEqual(c["watch"], ["oi", "liq"])
        self.assertEqual(c["symbol"], "BTC_USDT")
        self.assertEqual(c["window_min"], 15)
        self.assertEqual(c["threshold"]["liq"], 250000)
        self.assertEqual(c["threshold"]["oi"], 250000)
        self.assertEqual(c["min_event"]["cvd"], 10000)
        self.assertTrue(c["enabled"])

    def test_liq_min_event_and_window(self):
        now = 1_000_000.0
        events = [
            _liq("BTC_USDT", 40_000, now - 10),
            _liq("BTC_USDT", 80_000, now - 20, "BUY"),
            _liq("BTC_USDT", 90_000, now - 400),   # старше 5м
            _liq("ETH_USDT", 70_000, now - 15),
        ]
        rows = liq_by_symbol(events, 300, min_usd=50_000, now=now, symbol="ALL")
        self.assertIn("ETH_USDT", rows)
        self.assertEqual(rows["BTC_USDT"]["usd"], 80_000)  # 40k отсечён
        self.assertEqual(rows["BTC_USDT"]["shorts"], 80_000)
        only = liq_by_symbol(events, 300, 0, now, "BTC_USDT")
        self.assertEqual(set(only), {"BTC_USDT"})
        self.assertEqual(only["BTC_USDT"]["usd"], 120_000)

    def test_cvd_skips_small_buckets(self):
        now = 1_000_000.0
        acc = {
            "BTC_USDT|1": {
                int(now - 10): 80_000,
                int(now - 40): -5_000,
                int(now - 400): 9_000_000,
            }
        }
        rows = cvd_by_symbol(acc, 300, min_bucket=10_000, now=now, symbol="ALL")
        self.assertAlmostEqual(rows["BTC_USDT"]["usd"], 80_000)

    def test_oi_change_and_evaluate(self):
        now = 1_000_000.0
        market = {
            "now": now,
            "events": [
                _liq("BTC_USDT", 600_000, now - 30),
                _liq("ETH_USDT", 40_000, now - 20),
            ],
            "cvd": {"SOL_USDT|1": {int(now - 5): -800_000}},
            "oi": {"BTC_USDT": {"changes": {"m5": {"usd": 1_200_000, "pct": 1.2}},
                                "total_usd": 4e8}},
        }
        cfg = {
            "enabled": True,
            "watch": ["liq", "cvd", "oi"],
            "symbol": "ALL",
            "window_min": 5,
            "threshold": {"liq": 500_000, "cvd": 500_000, "oi": 1_000_000},
            "min_event": 0,
        }
        hits = evaluate(cfg, market)
        metrics = {h["metric"] for h in hits}
        self.assertEqual(metrics, {"liq", "cvd", "oi"})
        liq_hit = next(h for h in hits if h["metric"] == "liq")
        self.assertEqual(liq_hit["symbol"], "BTC_USDT")
        self.assertGreaterEqual(liq_hit["value"], 500_000)
        text = format_alert_html(liq_hit)
        self.assertIn("Алерт", text)
        self.assertIn("BTC", text)
        self.assertIn("посмотреть в терминале", text)
        self.assertIn("https://liqscope.online/terminal", text)

    def test_below_threshold_no_hit(self):
        now = 100.0
        hits = evaluate(
            {"watch": ["liq"], "threshold": 1_000_000, "window_min": 5},
            {"now": now, "events": [_liq("BTC_USDT", 10_000, now - 1)]},
        )
        self.assertEqual(hits, [])

    def test_cooldown(self):
        self.assertEqual(cooldown_sec(5), 300)
        self.assertTrue(should_fire(None, 1000, 5))
        self.assertFalse(should_fire(900, 1000, 5))
        self.assertTrue(should_fire(100, 1000, 5))

    def test_oi_by_symbol_filters_min(self):
        oi = {
            "BTC_USDT": {"changes": {"h1": {"usd": 50_000, "pct": 0.1}}},
            "ETH_USDT": {"changes": {"h1": {"usd": -900_000, "pct": -0.4}}},
        }
        rows = oi_by_symbol(oi, 60, min_usd=100_000, symbol="ALL")
        self.assertEqual(set(rows), {"ETH_USDT"})
        self.assertLess(rows["ETH_USDT"]["usd"], 0)

    def test_watch_can_be_one_or_empty(self):
        c = normalize_config({"watch": ["cvd"], "enabled": True})
        self.assertEqual(c["watch"], ["cvd"])
        empty = normalize_config({"watch": [], "enabled": True})
        self.assertEqual(empty["watch"], [])
        missing = normalize_config({"enabled": True})
        self.assertEqual(missing["watch"], ["liq"])

    def test_flow_thresholds_are_larger(self):
        self.assertEqual(threshold_presets("cvd"), list(THRESHOLD_PRESETS_FLOW))
        self.assertEqual(threshold_presets("oi"), list(THRESHOLD_PRESETS_FLOW))
        self.assertIn(100_000_000, presets()["thresholds_flow"])
        self.assertNotIn(100_000_000, threshold_presets("liq"))

    def test_sparkline_bins_window(self):
        now = 1000.0
        pts = {910: 10, 950: 20, 990: 40}
        s = sparkline(pts, now, 120, n=4)
        self.assertEqual(len(s), 4)
        self.assertGreater(sum(s), 0)

    def test_live_snapshot_has_sparks(self):
        from alerts import live_snapshot
        now = 1_000_000.0
        snap = live_snapshot(
            {"watch": ["liq"], "window_min": 5, "min_event": 0},
            {"now": now, "events": [_liq("BTC_USDT", 80_000, now - 10)],
             "cvd": {}, "oi": {}},
        )
        self.assertEqual(len(snap["liq"]["spark"]), 24)
        self.assertGreater(sum(snap["liq"]["spark"]), 0)
        self.assertEqual(len(snap["cvd"]["spark"]), 24)


class PerMetricWindowTest(unittest.TestCase):
    """Окно агрегации у каждой метрики своё."""

    def test_defaults_are_per_metric(self):
        from alerts import DEFAULT_WINDOWS
        c = normalize_config({})
        self.assertEqual(c["windows"], {"liq": 5, "cvd": 15, "oi": 60})
        self.assertEqual(c["windows"], dict(DEFAULT_WINDOWS))

    def test_legacy_single_window_spreads_over_metrics(self):
        """Старая настройка с одним числом по-прежнему читается."""
        c = normalize_config({"window_min": 30, "watch": ["liq", "cvd"]})
        self.assertEqual(c["windows"], {"liq": 30, "cvd": 30, "oi": 30})
        self.assertEqual(c["window_min"], 30)
        c2 = normalize_config({"window": 15})
        self.assertEqual(c2["windows"]["cvd"], 15)

    def test_partial_windows_keep_other_defaults(self):
        c = normalize_config({"windows": {"cvd": 240}})
        self.assertEqual(c["windows"], {"liq": 5, "cvd": 240, "oi": 60})

    def test_windows_are_clamped(self):
        from alerts import window_minutes, window_of
        c = normalize_config({"windows": {"liq": 0, "cvd": 99999, "oi": 7}})
        self.assertEqual(c["windows"], {"liq": 1, "cvd": 1440, "oi": 7})
        self.assertEqual(window_of({"windows": {"oi": 90}}, "oi"), 90)
        self.assertEqual(window_of({"window_min": 12}, "oi"), 12)
        self.assertEqual(window_minutes("30"), 30)

    def test_evaluate_uses_each_window(self):
        now = 1_000_000.0
        cfg = {"watch": ["liq", "cvd"], "enabled": True,
               "windows": {"liq": 5, "cvd": 15},
               "threshold": {"liq": 100, "cvd": 100}, "min_event": 0,
               "symbol": "ALL"}
        market = {"now": now,
                  "events": [_liq("BTC_USDT", 500, now - 10 * 60)],   # старше 5м
                  "cvd": {"BTC_USDT|1": {int(now - 10 * 60): 900.0}},
                  "oi": {}}
        hits = evaluate(cfg, market)
        by_metric = {h["metric"]: h for h in hits}
        self.assertNotIn("liq", by_metric)          # в 5м окно не попало
        self.assertIn("cvd", by_metric)             # а в 15м попало
        self.assertEqual(by_metric["cvd"]["window_min"], 15)


class WindowRestartTest(unittest.TestCase):
    """После сигнала окно метрики начинается заново.

    Это главное требование: в следующее сообщение попадают только данные
    новее прошлого сигнала, старые не повторяются.
    """

    def test_events_before_signal_are_dropped(self):
        now = 1_000_000.0
        events = [_liq("BTC_USDT", 600_000, now - 240),
                  _liq("BTC_USDT", 300_000, now - 30)]
        rows = liq_by_symbol(events, 300, 0, now, "ALL", since=now - 120)
        self.assertEqual(rows["BTC_USDT"]["usd"], 300_000)

    def test_evaluate_marks_restart_and_span(self):
        now = 1_000_000.0
        cfg = {"watch": ["liq"], "enabled": True, "window_min": 5,
               "threshold": {"liq": 100_000}, "min_event": 0, "symbol": "ALL"}
        market = {"now": now,
                  "events": [_liq("BTC_USDT", 400_000, now - 30),
                             _liq("BTC_USDT", 400_000, now - 200)],
                  "cvd": {}, "oi": {}}
        full = evaluate(cfg, market)[0]
        self.assertEqual(full["value"], 800_000)
        self.assertEqual(full["span_min"], 5)
        self.assertFalse(full["reset"])
        # сигнал ушёл 100 секунд назад: старое событие (200 с) уже не считается
        fresh = evaluate(cfg, market, since={"liq": now - 100})[0]
        self.assertEqual(fresh["value"], 400_000)
        self.assertEqual(fresh["span_min"], 2)
        self.assertTrue(fresh["reset"])
        text = format_alert_html(fresh)
        self.assertIn("за 2м · окно 5м", text)
        self.assertIn("окно после сигнала начато заново", text)

    def test_no_new_data_no_second_message(self):
        """Ровно случай из жалобы: через минуту после сигнала — тишина."""
        now = 1_000_000.0
        cfg = {"watch": ["liq"], "enabled": True, "windows": {"liq": 5},
               "threshold": {"liq": 500_000}, "min_event": 0, "symbol": "ALL"}
        old = [_liq("BTC_USDT", 900_000, now - 60),
               _liq("ETH_USDT", 700_000, now - 30)]
        market = {"now": now, "events": old, "cvd": {}, "oi": {}}
        self.assertTrue(evaluate(cfg, market))                  # сигнал есть
        # прошло 60 секунд, новых событий нет — окно чистое
        market2 = {"now": now + 60, "events": list(old), "cvd": {}, "oi": {}}
        self.assertEqual(evaluate(cfg, market2, since={"liq": now}), [])
        # пришло новое событие — сообщение только про него
        market3 = {"now": now + 60, "events": old + [_liq("BTC_USDT", 800_000, now + 20)],
                   "cvd": {}, "oi": {}}
        hits = evaluate(cfg, market3, since={"liq": now})
        self.assertEqual([(h["symbol"], h["value"]) for h in hits],
                         [("BTC_USDT", 800_000)])

    def test_cvd_buckets_before_signal_dropped(self):
        now = 1_000_000.0
        acc = {"BTC_USDT|1": {int(now - 240): 900_000.0, int(now - 30): 400_000.0}}
        rows = cvd_by_symbol(acc, 900, 0, now, "ALL", since=now - 120)
        self.assertEqual(rows["BTC_USDT"]["usd"], 400_000)

    def test_oi_is_rebased_on_level_series(self):
        """У OI окна посчитаны трекером — пересчитываем от прошлого сигнала."""
        from alerts import oi_from_series
        now = 1_000_000.0
        series = {int(now - 3600): 40_000_000.0, int(now - 600): 41_000_000.0,
                  int(now): 42_000_000.0}
        part = oi_from_series(series, now - 300, now)
        self.assertEqual(part["usd"], 1_000_000.0)
        self.assertAlmostEqual(part["pct"], 2.439, places=2)
        cfg = {"watch": ["oi"], "enabled": True, "windows": {"oi": 60},
               "threshold": {"oi": 100_000}, "min_event": 0, "symbol": "ALL"}
        market = {"now": now, "events": [], "cvd": {},
                  "oi": {"BTC_USDT": {"changes": {"h1": {"usd": 2_000_000.0, "pct": 5.0}},
                                      "total_usd": 42_000_000.0, "_series": series}}}
        self.assertEqual(evaluate(cfg, market)[0]["value"], 2_000_000.0)
        fresh = evaluate(cfg, market, since={"oi": now - 300})[0]
        self.assertEqual(fresh["value"], 1_000_000.0)
        self.assertTrue(fresh["rebased"])

    def test_should_fire_has_minute_pause(self):
        from alerts import MIN_GAP_SEC
        self.assertEqual(MIN_GAP_SEC, 60)
        now = 1000.0
        self.assertFalse(should_fire(now - 30, now, MIN_GAP_SEC / 60.0))
        self.assertTrue(should_fire(now - 61, now, MIN_GAP_SEC / 60.0))

    def test_top_per_metric_keeps_leader_and_peers(self):
        from alerts import top_per_metric
        hits = [
            {"metric": "liq", "symbol": "ETH_USDT", "value": 1_500_000, "abs": 1.5e6},
            {"metric": "liq", "symbol": "BTC_USDT", "value": 900_000, "abs": 9e5},
            {"metric": "cvd", "symbol": "SOL_USDT", "value": -700_000, "abs": 7e5},
        ]
        out = top_per_metric(hits)
        self.assertEqual([h["metric"] for h in out], ["liq", "cvd"])
        self.assertEqual(out[0]["symbol"], "ETH_USDT")
        self.assertEqual([p["symbol"] for p in out[0]["peers"]], ["BTC_USDT"])
        self.assertEqual(out[1]["peers"], [])
        text = format_alert_html(out[0])
        self.assertIn("ещё в волне: BTC $900.0K", text)

    def test_live_snapshot_shows_restarted_window(self):
        from alerts import live_snapshot
        now = 1_000_000.0
        cfg = {"watch": ["liq"], "windows": {"liq": 5}, "min_event": 0}
        market = {"now": now, "events": [_liq("BTC_USDT", 500_000, now - 20)],
                  "cvd": {}, "oi": {}}
        snap = live_snapshot(cfg, market, since={"liq": now - 60})
        self.assertEqual(snap["liq"]["window_min"], 5)
        self.assertEqual(snap["liq"]["span_min"], 1)
        self.assertEqual(snap["windows"]["liq"], 5)


class AlertDeliveryTest(unittest.TestCase):
    """Серверная доставка: пауза, окно заново, одна карточка на метрику."""

    @classmethod
    def setUpClass(cls):
        os.environ["LIQSCOPE_ACCOUNTS_DB"] = ""
        import server
        cls.srv = server

    def test_due_hits_restart_window_after_send(self):
        from alerts import format_alert_html
        srv = self.srv
        now = 1_000_000.0
        cfg = {"watch": ["liq"], "enabled": True, "windows": {"liq": 5},
               "threshold": {"liq": 500_000}, "min_event": 0, "symbol": "ALL"}
        fired = {"liq": None}

        def last_fire(metric, symbol):
            return fired.get(metric)

        market = {"now": now,
                  "events": [_liq("BTC_USDT", 900_000, now - 60),
                             _liq("ETH_USDT", 600_000, now - 30)],
                  "cvd": {}, "oi": {}}
        first = srv.alerts_due(cfg, market, last_fire, now)
        self.assertEqual(len(first), 1)                  # одна карточка на метрику
        self.assertEqual(first[0]["symbol"], "BTC_USDT")
        self.assertIn("ещё в волне: ETH $600.0K", format_alert_html(first[0]))
        fired["liq"] = now                               # сигнал ушёл
        # следующий тик через 8 секунд: пауза, повторно не шлём
        self.assertEqual(srv.alerts_due(cfg, dict(market, now=now + 8),
                                        last_fire, now + 8), [])
        # через минуту с новым объёмом — новое сообщение только про новое
        market2 = {"now": now + 60,
                   "events": [_liq("BTC_USDT", 900_000, now - 60),
                              _liq("BTC_USDT", 700_000, now + 20)],
                   "cvd": {}, "oi": {}}
        second = srv.alerts_due(cfg, market2, last_fire, now + 60)
        self.assertEqual([(h["symbol"], h["value"]) for h in second],
                         [("BTC_USDT", 700_000)])
        text = format_alert_html(second[0])
        self.assertIn("$700.0K", text)                    # только новое
        self.assertNotIn("$900.0K", text.split("порог")[0])
        self.assertIn("за 1м · окно 5м", text)

    def test_oi_row_carries_level_series(self):
        """Ряд уровней OI нужен движку, чтобы перезапустить окно метрики."""
        class _Tracker:
            def payload(self, sym):
                return {"changes": {"h1": {"usd": 5.0, "pct": 1.0}}, "total_usd": 10.0}

            def series(self, sym):
                return {1: 1.0, 2: 2.0}

        class _Broken:
            def payload(self, sym):
                return {"changes": {}}

            def series(self, sym):
                raise RuntimeError("нет ряда")

        row = self.srv._oi_row(_Tracker(), "BTC_USDT")
        self.assertEqual(row["_series"], {1: 1.0, 2: 2.0})
        self.assertEqual(row["changes"]["h1"]["usd"], 5.0)
        self.assertEqual(self.srv._oi_row(_Broken(), "BTC_USDT")["_series"], {})

    def test_store_remembers_last_signal_per_metric_and_coin(self):
        """Якорь окна: последний сигнал метрики (и по монете, и по всем)."""
        import tempfile
        from accounts import Store
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(os.path.join(tmp, "a.db"), secret="s")
            uid = store.upsert_telegram_user({"id": 7, "first_name": "T"})["id"]
            store.add_alert_event(uid, {"metric": "liq", "symbol": "BTC_USDT",
                                        "value": 1.0, "threshold": 1.0,
                                        "window_min": 5, "span_min": 2, "count": 3})
            store.add_alert_event(uid, {"metric": "cvd", "symbol": "SOL_USDT",
                                        "value": -1.0, "threshold": 1.0,
                                        "window_min": 15, "count": 1})
            self.assertTrue(store.last_alert_ts(uid, "liq", "BTC_USDT"))
            self.assertIsNone(store.last_alert_ts(uid, "liq", "ETH_USDT"))
            self.assertEqual(store.last_alert_any(uid, "liq"),
                             store.last_alert_ts(uid, "liq", "BTC_USDT"))
            self.assertIsNone(store.last_alert_any(uid, "oi"))
            rows = store.list_alert_events(uid)
            self.assertIsInstance(rows[0]["detail"], dict)   # разобрано из JSON
            self.assertEqual(rows[0]["detail"]["count"], 1)
            liq_row = [r for r in rows if r["metric"] == "liq"][0]
            self.assertEqual(liq_row["detail"]["span_min"], 2)
            self.assertEqual(liq_row["detail"]["count"], 3)
            store.close()


class CoinsPerMetricTest(unittest.TestCase):
    """Монета — своя у каждой метрики: ликвидации могут смотреть BTC, CVD — ETH.

    Требование: в сервисе алертов по объёму у каждой переменной (liq/cvd/oi)
    своя монета, и сигналы идут независимо друг от друга.
    """

    def test_normalize_keeps_coins_per_metric(self):
        cfg = normalize_config({"watch": ["liq", "cvd", "oi"],
                                "coins": {"liq": "btc-usdt", "cvd": "eth-usdt"}})
        self.assertEqual(symbol_of(cfg, "liq"), "BTC_USDT")
        self.assertEqual(symbol_of(cfg, "cvd"), "ETH_USDT")
        self.assertEqual(symbol_of(cfg, "oi"), "ALL")
        self.assertEqual(cfg["coins"]["oi"], "ALL")

    def test_legacy_single_symbol_spreads_over_metrics(self):
        cfg = normalize_config({"symbol": "sol-usdt", "watch": ["liq", "cvd"]})
        self.assertEqual(symbol_of(cfg, "liq"), "SOL_USDT")
        self.assertEqual(symbol_of(cfg, "cvd"), "SOL_USDT")
        self.assertEqual(symbol_of(cfg, "oi"), "SOL_USDT")

    def test_evaluate_uses_own_coin_per_metric(self):
        now = 1_000_000.0
        market = {
            "now": now,
            "events": [
                _liq("BTC_USDT", 700_000, now - 30),
                _liq("ETH_USDT", 700_000, now - 20),
            ],
            "cvd": {"BTC_USDT|1": {int(now - 5): 800_000},
                    "ETH_USDT|1": {int(now - 5): -900_000}},
            "oi": {},
        }
        cfg = normalize_config({
            "enabled": True, "watch": ["liq", "cvd"],
            "coins": {"liq": "BTC_USDT", "cvd": "ETH_USDT"},
            "threshold": {"liq": 500_000, "cvd": 500_000},
            "windows": {"liq": 5, "cvd": 15},
        })
        hits = evaluate(cfg, market)
        got = {(h["metric"], h["symbol"]) for h in hits}
        self.assertIn(("liq", "BTC_USDT"), got)
        self.assertIn(("cvd", "ETH_USDT"), got)
        self.assertNotIn(("liq", "ETH_USDT"), got)
        self.assertNotIn(("cvd", "BTC_USDT"), got)

    def test_signals_are_independent(self):
        """Монета ликвидаций не влияет на CVD: только своя метрика в сигнале."""
        now = 2_000_000.0
        market = {
            "now": now,
            "events": [_liq("BTC_USDT", 900_000, now - 10)],
            "cvd": {"SOL_USDT|1": {int(now - 5): 400_000}},
            "oi": {},
        }
        cfg = normalize_config({
            "enabled": True, "watch": ["liq", "cvd"],
            "coins": {"liq": "BTC_USDT", "cvd": "ETH_USDT"},
            "threshold": {"liq": 500_000, "cvd": 300_000},
        })
        hits = evaluate(cfg, market)
        metrics = {h["metric"] for h in hits}
        self.assertEqual(metrics, {"liq"})          # ETH не двигался — CVD молчит

    def test_live_snapshot_reports_each_coin(self):
        now = 3_000_000.0
        market = {"now": now,
                  "events": [_liq("BTC_USDT", 900_000, now - 10)],
                  "cvd": {}, "oi": {}}
        cfg = normalize_config({"enabled": True, "watch": ["liq"],
                                "coins": {"liq": "BTC_USDT"}})
        live = live_snapshot(cfg, market)
        self.assertEqual(live["coins"]["liq"], "BTC_USDT")
        self.assertEqual(live["liq"]["coin"], "BTC_USDT")
        self.assertEqual(live["liq"]["symbol"], "BTC_USDT")


class LiveFlowTest(unittest.TestCase):
    """Лента и микрографик должны быть живыми, а не только по сигналам.

    Раньше в сервисе «Алерты по объёму» ленты стояли с «пока тихо» (сигналы
    редкие), а микрографик OI вообще был пустым: у трекера нет ряда для
    графика. Проверяем, что поток собирается по всем трём метрикам, а из ряда
    OI получается гребёнка.
    """

    def setUp(self):
        self.now = 4_000_000.0
        self.market = {
            "now": self.now,
            "events": [_liq("BTC_USDT", 900_000, self.now - 5),
                       _liq("SOL_USDT", 300_000, self.now - 40),
                       _liq("BTC_USDT", 100, self.now - 60)],
            "cvd": {"BTC_USDT|1": {int(self.now) - 60: -120_000.0,
                                   int(self.now) - 120: 40_000.0},
                    "SOL_USDT|1": {int(self.now): 15_000.0},
                    "BTC_USDT|5": {int(self.now): 999_999.0}},
            "oi": {"BTC_USDT": {
                "total_usd": 4e8, "_series": {
                    int(self.now) - 600: 400_000_000.0,
                    int(self.now) - 300: 401_000_000.0,
                    int(self.now): 400_200_000.0},
                "changes": {"h1": {"usd": 200_000.0, "pct": 0.05}}}},
        }

    def cfg(self, **over):
        raw = {"enabled": True, "watch": ["liq", "cvd", "oi"],
               "coins": {"liq": "ALL", "cvd": "ALL", "oi": "ALL"}}
        raw.update(over)
        return normalize_config(raw)

    def test_flow_has_rows_for_every_metric(self):
        live = live_snapshot(self.cfg(), self.market)
        for metric in ("liq", "cvd", "oi"):
            rows = live[metric]["flow"]
            self.assertTrue(rows, metric)
            self.assertTrue(all("ts" in r and "value" in r for r in rows), metric)

    def test_flow_is_newest_first_and_limited(self):
        rows = flow_rows("liq", self.market, self.cfg(), self.now, limit=2)
        self.assertEqual(len(rows), 2)
        self.assertGreaterEqual(rows[0]["ts"], rows[1]["ts"])
        self.assertEqual(rows[0]["symbol"], "BTC_USDT")

    def test_flow_respects_coin_and_min_event(self):
        cfg = self.cfg(coins={"liq": "SOL_USDT", "cvd": "ALL", "oi": "ALL"})
        rows = flow_rows("liq", self.market, cfg, self.now)
        self.assertEqual([r["symbol"] for r in rows], ["SOL_USDT"])
        cfg2 = self.cfg(min_event={"liq": 500_000, "cvd": 0, "oi": 0})
        rows2 = flow_rows("liq", self.market, cfg2, self.now)
        self.assertEqual([r["symbol"] for r in rows2], ["BTC_USDT"])

    def test_cvd_flow_uses_minute_buckets(self):
        """Поток CVD — минутные дельты, крупный таймфрейм окна не дублируем."""
        rows = flow_rows("cvd", self.market, self.cfg(), self.now)
        self.assertEqual(len(rows), 3)          # m5-бакет 999_999 не попал
        self.assertEqual(rows[0]["symbol"], "SOL_USDT")   # свежайший бакет
        btc = [r for r in rows if r["symbol"] == "BTC_USDT"]
        self.assertEqual(len(btc), 2)
        self.assertAlmostEqual(btc[0]["value"], -120_000.0)

    def test_oi_flow_and_spark_come_from_series(self):
        live = live_snapshot(self.cfg(), self.market)
        rows = live["oi"]["flow"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "BTC_USDT")
        self.assertAlmostEqual(rows[0]["value"], -800_000.0)
        self.assertEqual(rows[0]["bucket_min"], 5)
        self.assertTrue(live["oi"]["spark"], "гребешка OI не должна быть пустой")
        self.assertTrue(any(live["oi"]["spark"]), "гребешка OI из нулей")
        self.assertEqual(len(oi_points(self.market["oi"], "ALL")), 2)

    def test_oi_live_polls_beat_five_minute_buckets(self):
        """Живые опросы важнее бакетов: по ним график и лента двигаются.

        Бакеты пишутся раз в 5 минут, поэтому между ними микрографик OI стоял
        картинкой. Кольцо живых опросов приходит каждые десятки секунд —
        берём его, а к бакетам возвращаемся, только если живого ряда нет.
        """
        from alerts import oi_levels, oi_points
        # живые опросы идут внутри окна метрики (по умолчанию OI — час)
        t0 = self.now - 900
        payload = {"_series": {t0 - 300: 100.0, t0: 200.0},
                   "_live": {t0: 200.0, t0 + 15: 210.0, t0 + 30: 205.0}}
        levels, live = oi_levels(payload)
        self.assertTrue(live)
        self.assertEqual(sorted(levels), [t0, t0 + 15, t0 + 30])
        # шаг живой ленты — в секундах, а не «за 0м»
        market = {"now": self.now,
                  "events": [], "cvd": {},
                  "oi": {"BTC_USDT": dict(payload, ts=self.now)}}
        rows = flow_rows("oi", market, self.cfg(), self.now)
        self.assertTrue(rows)
        self.assertIn("bucket_sec", rows[0])
        self.assertNotIn("bucket_min", rows[0])
        self.assertEqual(rows[0]["bucket_sec"], 15)
        # без живого ряда — прежние бакеты, шаг в минутах
        levels2, live2 = oi_levels({"_series": {300: 100.0, 600: 200.0}})
        self.assertFalse(live2)
        self.assertEqual(sorted(levels2), [300.0, 600.0])
        market2 = {"now": self.now, "events": [], "cvd": {},
                   "oi": {"BTC_USDT": {"_series": {int(self.now) - 600: 1e8,
                                                   int(self.now) - 300: 1.01e8}}}}
        rows2 = flow_rows("oi", market2, self.cfg(), self.now)
        self.assertTrue(rows2)
        self.assertIn("bucket_min", rows2[0])
        self.assertEqual(oi_points(market["oi"], "ALL"),
                         {t0 + 15: 10.0, t0 + 30: -5.0})

    def test_spark_accumulates_so_empty_buckets_do_not_break_the_line(self):
        """График метрики — накопительная кривая окна, а не суммы по шагам.

        CVD и OI приходят бакетами реже шага графика: в пошаговом виде линия
        обрывалась нулями и «не шла». Накопительная кривая заполняет окно и
        сравнивается с итогом метрики, который кабинет дописывает сам.
        """
        from alerts import sparkline
        pts = {self.now - 100: 5.0, self.now - 20: 7.0}
        step = sparkline(pts, self.now, 600, n=6)
        cum = sparkline(pts, self.now, 600, n=6, cum=True)
        self.assertEqual(len(step), len(cum))
        # пустые шаги у накопительной кривой не оставляют провалов: нулей
        # столько же, сколько шагов до первого события, и ни одного после
        self.assertLessEqual(cum.count(0.0), step.count(0.0))
        self.assertEqual(cum[-1], 12.0)                     # итог окна
        running = 0.0
        for i, v in enumerate(step):
            running += v
            self.assertAlmostEqual(cum[i], round(running, 2))
        live = live_snapshot(self.cfg(), self.market)
        for metric in ("liq", "cvd", "oi"):
            spark = live[metric]["spark"]
            self.assertEqual(len(spark), 24, metric)
            self.assertTrue(any(spark), metric)
            # последняя точка накопительной кривой — это итог окна метрики:
            # у ликвидаций он сходится с суммой строк окна (у CVD график
            # считает все бакеты, а лента — только минутные, так и задумано)
            self.assertNotEqual(spark[-1], 0.0, metric)
            if metric == "liq":
                self.assertAlmostEqual(spark[-1], live[metric]["total"], places=1)

    def test_spark_of_each_metric_is_filled(self):
        live = live_snapshot(self.cfg(), self.market)
        for metric in ("liq", "cvd", "oi"):
            spark = live[metric]["spark"]
            self.assertEqual(len(spark), 24, metric)
            self.assertTrue(any(spark), metric)


if __name__ == "__main__":
    unittest.main()
