"""Тесты сторожа пампов и дампов (pump_scan.py).

Сервис «Сторож монет» теперь следит не за ликвидациями, а за резкими
движениями всех монет Gate: порог в % за выбранный период свечей (1м, 5м…),
количество свечей, текущая формирующаяся свеча учитывается. Здесь проверяем
математику: минутную историю, расчёт изменения, фильтры режима и оборота,
кулдаун сигналов и текст сообщения со ссылкой на Gate.

Запуск: /tmp/venv/bin/python tests/test_pump_scan.py
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pump_scan import (  # noqa: E402
    CANDLE_PRESETS, DEFAULT_CANDLES, DEFAULT_PERIOD, DEFAULT_THRESHOLD,
    MODES, PERIODS, THRESHOLDS, PumpScanner, filter_new, format_settings_text,
    format_signal_html, normalize, period_label, period_minutes,
)

# ровно на границе минуты: история цен — поминутная, и сдвиги в тесте
# должны попадать в разные минуты предсказуемо (1789596000 % 60 == 0)
T0 = 1_789_596_000.0


def feed_prices(scanner: PumpScanner, symbol: str, prices, start=T0, step=60):
    """Скормить сканеру ряд минутных цен (по одной минуте на значение)."""
    for i, p in enumerate(prices):
        scanner.add_prices({symbol: p}, ts=start + i * step)


class TestSettings(unittest.TestCase):
    def test_defaults(self):
        cfg = normalize({})
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["mode"], "both")
        self.assertEqual(cfg["period"], DEFAULT_PERIOD)
        self.assertEqual(cfg["candles"], DEFAULT_CANDLES)
        self.assertEqual(cfg["threshold"], DEFAULT_THRESHOLD)

    def test_sane_bounds(self):
        cfg = normalize({"threshold": "мало", "candles": 999, "cooldown_min": -5,
                         "min_vol": -1, "mode": "шум", "period": "нет"})
        self.assertEqual(cfg["mode"], "both")
        self.assertEqual(cfg["period"], DEFAULT_PERIOD)
        self.assertLessEqual(cfg["candles"], 60)
        self.assertGreaterEqual(cfg["candles"], 1)
        self.assertGreaterEqual(cfg["threshold"], 0.5)
        self.assertGreaterEqual(cfg["cooldown_min"], 1)
        self.assertEqual(cfg["min_vol"], 0.0)

    def test_period_helpers(self):
        self.assertEqual(period_minutes("5m"), 5)
        self.assertEqual(period_minutes("1h"), 60)
        self.assertEqual(period_minutes("нет"), period_minutes(DEFAULT_PERIOD))
        self.assertEqual(period_label("нет"), DEFAULT_PERIOD)
        self.assertTrue(PERIODS and THRESHOLDS and CANDLE_PRESETS and MODES)

    def test_settings_text(self):
        text = format_settings_text(normalize({"candles": 3, "period": "5m",
                                               "threshold": 30, "mode": "both"}))
        self.assertIn("30%", text)
        self.assertIn("3 × 5m", text)
        self.assertIn("пампы и дампы", text)
        self.assertIn("15 мин", text)


class TestScanner(unittest.TestCase):
    def setUp(self):
        self.s = PumpScanner()

    def test_minute_history_keeps_updating_within_minute(self):
        self.s.add_prices({"BTC_USDT": 100.0}, ts=T0)
        self.s.add_prices({"BTC_USDT": 101.0}, ts=T0 + 20)
        self.s.add_prices({"BTC_USDT": 102.0}, ts=T0 + 40)
        self.assertEqual(self.s.known(), 1)
        # внутри минуты цена уточняется, а не копится
        self.assertEqual(self.s.price_now("BTC_USDT"), 102.0)
        self.s.add_prices({"BTC_USDT": 105.0}, ts=T0 + 60)
        self.assertEqual(self.s.change_pct("BTC_USDT", 1), (105 - 102) / 102 * 100)

    def test_change_over_span(self):
        feed_prices(self.s, "SOL_USDT", [100, 100, 100, 130])
        self.assertAlmostEqual(self.s.change_pct("SOL_USDT", 3), 30.0, places=4)
        self.assertAlmostEqual(self.s.change_pct("SOL_USDT", 1), 30.0, places=4)

    def test_change_needs_history(self):
        feed_prices(self.s, "NEW_USDT", [100, 110])
        self.assertIsNone(self.s.change_pct("NEW_USDT", 5))

    def test_scan_finds_pump_and_dump(self):
        feed_prices(self.s, "AAA_USDT", [100, 100, 100, 100, 100, 100, 145])
        feed_prices(self.s, "BBB_USDT", [200, 200, 200, 200, 200, 200, 120])
        feed_prices(self.s, "CCC_USDT", [50, 50, 50, 50, 50, 50, 51])
        hits = self.s.scan({"threshold": 10, "candles": 3, "period": "1m"})
        kinds = {h["symbol"]: h["kind"] for h in hits}
        self.assertEqual(kinds.get("AAA_USDT"), "pump")
        self.assertEqual(kinds.get("BBB_USDT"), "dump")
        self.assertNotIn("CCC_USDT", kinds)              # ниже порога
        top = hits[0]
        self.assertAlmostEqual(top["change_pct"], 45.0, places=3)
        self.assertEqual(top["span_min"], 3)
        self.assertTrue(top["price_from"])

    def test_mode_filter(self):
        feed_prices(self.s, "AAA_USDT", [100, 100, 100, 100, 160])
        feed_prices(self.s, "BBB_USDT", [200, 200, 200, 200, 100])
        only_pump = self.s.scan({"threshold": 5, "candles": 1, "period": "1m",
                                 "mode": "pump"})
        self.assertEqual([h["symbol"] for h in only_pump], ["AAA_USDT"])
        only_dump = self.s.scan({"threshold": 5, "candles": 1, "period": "1m",
                                 "mode": "dump"})
        self.assertEqual([h["symbol"] for h in only_dump], ["BBB_USDT"])
        both = self.s.scan({"threshold": 5, "candles": 1, "period": "1m",
                            "mode": "both"})
        self.assertEqual(len(both), 2)

    def test_disabled_returns_nothing(self):
        feed_prices(self.s, "AAA_USDT", [100, 100, 100, 300])
        self.assertEqual(self.s.scan({"enabled": False, "threshold": 1}), [])

    def test_min_volume_filter(self):
        feed_prices(self.s, "AAA_USDT", [100, 100, 100])
        self.s.add_prices({"AAA_USDT": 300.0}, ts=T0 + 180,
                          volume={"AAA_USDT": 1000.0})
        cfg = {"threshold": 5, "candles": 1, "period": "1m", "min_vol": 1_000_000}
        self.assertEqual(self.s.scan(cfg), [])
        cfg["min_vol"] = 500
        self.assertEqual(len(self.s.scan(cfg)), 1)

    def test_movers_sorted_by_absolute_change(self):
        feed_prices(self.s, "AAA_USDT", [100, 100, 130])
        feed_prices(self.s, "BBB_USDT", [100, 100, 60])
        feed_prices(self.s, "CCC_USDT", [100, 100, 101])
        rows = self.s.movers({"period": "1m", "candles": 1}, limit=10)
        self.assertEqual([r["symbol"] for r in rows], ["BBB_USDT", "AAA_USDT",
                                                       "CCC_USDT"])
        self.assertEqual(rows[0]["change_pct"], -40.0)

    def test_period_and_candles_scale_the_window(self):
        # 5-минутные свечи: две свечи — это 10 минут истории
        prices = [100] * 10 + [150]
        for i, p in enumerate(prices):
            self.s.add_prices({"AAA_USDT": p}, ts=T0 + i * 60)
        cfg = {"period": "5m", "candles": 2, "threshold": 10}
        self.assertEqual(self.s.span_minutes(cfg), 10)
        hits = self.s.scan(cfg)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0]["change_pct"], 50.0, places=3)


class TestCooldown(unittest.TestCase):
    def test_first_signal_passes_then_pause(self):
        fired = {}
        hits = [{"symbol": "AAA_USDT", "kind": "pump"}]
        self.assertEqual(len(filter_new(hits, fired, T0, 10)), 1)
        self.assertEqual(len(filter_new(hits, fired, T0 + 60, 10)), 0)
        self.assertEqual(len(filter_new(hits, fired, T0 + 11 * 60, 10)), 1)

    def test_modes_have_their_own_pause(self):
        fired = {}
        filter_new([{"symbol": "AAA_USDT", "kind": "pump"}], fired, T0, 10)
        again = filter_new([{"symbol": "AAA_USDT", "kind": "dump"}], fired, T0 + 60, 10)
        self.assertEqual(len(again), 1)

    def test_other_coin_is_not_delayed(self):
        fired = {}
        filter_new([{"symbol": "AAA_USDT", "kind": "pump"}], fired, T0, 10)
        fresh = filter_new([{"symbol": "BBB_USDT", "kind": "pump"}], fired, T0 + 5, 10)
        self.assertEqual(len(fresh), 1)


class TestSignalText(unittest.TestCase):
    hit = {
        "symbol": "SOL_USDT", "kind": "dump", "change_pct": -31.42,
        "price": 152.4, "price_from": 222.1, "volume24h": 12_500_000.0,
        "span_min": 15, "candles": 3, "period": "5m",
    }

    def test_ru_text(self):
        text = format_signal_html(self.hit, "ru")
        self.assertIn("Дамп", text)
        self.assertIn("SOL", text)
        self.assertIn("-31.42%", text)
        self.assertIn("3 × 5m", text)
        self.assertIn("15 мин", text)
        self.assertIn("текущая свеча учтена", text)
        self.assertIn("$152.40", text)
        self.assertIn("$222.10", text)
        self.assertIn("12.50 млн", text)

    def test_text_is_pretty_like_alerts(self):
        """Оформление сигнала — как у алертов по объёму.

        Шапка «иконка · монета», крупные числа в разметке Telegram и общий
        подвал LiqScope: сообщения бота должны выглядеть одинаково.
        """
        from alerts import footer_html
        text = format_signal_html(self.hit, "ru")
        head = text.split("\n")[0]
        self.assertTrue(head.startswith("🩸 <b>Дамп · SOL</b>"), head)
        self.assertIn("<b>-31.42%</b>", text)
        self.assertIn("<code>$152.40</code>", text)
        self.assertIn("<code>$222.10</code>", text)
        self.assertIn("<code>$12.50 млн</code>", text)
        self.assertIn(footer_html(), text)

    def test_en_text(self):
        text = format_signal_html(self.hit, "en")
        self.assertIn("Dump", text)
        self.assertIn("open candle counted", text)
        self.assertNotIn("Дамп", text)
        # суммы в английском — без русских суффиксов
        self.assertIn("$12.50M", text)
        self.assertNotIn("млн", text)
        self.assertIn("a live liquidation stream", text)

    def test_pump_text(self):
        pump = dict(self.hit, kind="pump", change_pct=44.0)
        self.assertIn("Памп", format_signal_html(pump, "ru"))

    def test_missing_numbers_do_not_break(self):
        text = format_signal_html({"symbol": "X_USDT", "kind": "pump"}, "ru")
        self.assertIn("X", text)
        self.assertNotIn("nan", text.lower())
        self.assertNotIn("None", text)

    def test_old_price_formatting(self):
        text = format_signal_html({"symbol": "PEPE_USDT", "kind": "pump",
                                   "change_pct": 55.0, "price": 0.0000123,
                                   "price_from": 0.000008}, "ru")
        self.assertIn("$0.000012", text)


class TestScannerPrecision(unittest.TestCase):
    def test_unknown_symbol(self):
        s = PumpScanner()
        self.assertIsNone(s.price_now("NOPE_USDT"))
        self.assertIsNone(s.change_pct("NOPE_USDT", 5))
        self.assertEqual(s.movers({}), [])

    def test_bad_prices_skipped(self):
        s = PumpScanner()
        s.add_prices({"AAA_USDT": 0, "BBB_USDT": "мусор", "": 100}, ts=T0)
        self.assertEqual(s.known(), 0)

    def test_memory_is_capped(self):
        s = PumpScanner(keep_min=30)
        feed_prices(s, "AAA_USDT", list(range(1, 200)), start=T0)
        self.assertLessEqual(len(s._prices["AAA_USDT"]), 30)

    def test_last_update(self):
        s = PumpScanner()
        self.assertEqual(s.last_update(), 0.0)
        s.add_prices({"AAA_USDT": 5.0}, ts=time.time())
        self.assertGreater(s.last_update(), 0)


    def test_longest_window_fits_default_keep(self):
        """Самое длинное окно настроек (4ч × 10 свечей) должно уместиться в
        минутную историю сторожа, иначе такой режим молча никогда не сработает."""
        from pump_scan import DEFAULT_KEEP_MIN, PERIODS, period_minutes
        hours = max(period_minutes(k) * 10 for k, _m in PERIODS) / 60.0
        self.assertGreater(DEFAULT_KEEP_MIN / 60.0, hours,
                           f"история {DEFAULT_KEEP_MIN / 60.0} ч меньше окна {hours} ч")


if __name__ == "__main__":
    unittest.main(verbosity=2)
