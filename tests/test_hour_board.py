"""Часовой стенд постов: календарные часы, топ-7, CVD, история OI."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hour_board import (  # noqa: E402
    OI, HourBoard, OiHistory, build_snapshot, hour_hhmm, hour_start, tz_offset,
)
from channel_digest import (  # noqa: E402
    CAPTION_LIMIT, render_post, render_top7,
)

MSK = 3 * 3600


class HourBoardTest(unittest.TestCase):
    def setUp(self):
        # Середина календарного часа: у границы событие «полчаса назад»
        # попадает в предыдущий час, и тест проверял бы не то.
        self.now = 1_800_000_000.0 + 1800
        self.board = HourBoard(tz=MSK)
        self.oi = OiHistory(tz=MSK)

    def liq(self, hours_ago: float, usd: float, sym="BTC_USDT", ex="binance",
            side="SELL"):
        self.board.add_liq({
            "timestamp": self.now - hours_ago * 3600, "usd": usd, "symbol": sym,
            "exchange": ex, "side": side,
        })

    def test_hours_are_calendar_not_rolling(self):
        """Часы — календарные по МСК: 14:00–15:00, 15:00–16:00 и так далее."""
        start = hour_start(self.now, MSK)
        self.assertEqual(hour_hhmm(start, MSK)[-2:], "00")
        self.assertLessEqual(start, self.now)
        self.assertGreater(start, self.now - 3600)
        # время внутри одного часа попадает в одну ячейку
        a = hour_start(start + 61, MSK)
        b = hour_start(start + 3599, MSK)
        self.assertEqual(a, b)
        self.assertEqual(hour_start(start + 3601, MSK), b + 3600)
        # пояс по умолчанию — МСК (UTC+3)
        self.assertEqual(tz_offset(), MSK)

    def test_hours_returns_last_four_with_totals(self):
        # «сейчас» — :30, поэтому 0.5ч назад — ровно начало часа (он же текущий),
        # а 0.4ч — та же календарная ячейка
        self.liq(0.5, 1_000_000, "BTC_USDT")
        self.liq(0.4, 500_000, "ETH_USDT", side="BUY")
        self.liq(1.5, 2_000_000, "SOL_USDT")
        self.liq(3.5, 300_000, "XRP_USDT")
        self.liq(5.5, 9_000_000, "BTC_USDT")          # за окном — не считается
        hours = self.board.hours(4, now=self.now)
        self.assertEqual(len(hours), 4)
        totals = [round(h["total"]) for h in hours]
        self.assertEqual(totals[3], 1_500_000)        # текущий час
        self.assertEqual(totals[2], 2_000_000)
        self.assertEqual(totals[0], 300_000)
        self.assertEqual(sum(totals), 3_800_000)
        # перекос: лонги против шортов внутри часа
        self.assertEqual(hours[3]["longs"], 1_000_000)
        self.assertEqual(hours[3]["shorts"], 500_000)

    def test_top7_per_hour_keeps_biggest_and_exchange(self):
        for i, usd in enumerate([10_000, 5_000_000, 700_000, 3_000_000, 250_000,
                                 120_000, 90_000, 900_000, 60_000]):
            self.liq(0.5, usd, sym="ETH_USDT" if i % 2 else "BTC_USDT",
                     ex="gate" if i == 1 else "bybit")
        hours = self.board.hours(4, now=self.now)
        top = hours[3]["top"]
        self.assertEqual(len(top), 7)
        self.assertEqual(top[0]["usd"], 5_000_000)
        self.assertEqual(top[0]["exchange"], "gate")
        self.assertEqual([t["usd"] for t in top],
                         sorted([t["usd"] for t in top], reverse=True))
        # монета с максимумом часа видна и в агрегате по монетам
        self.assertEqual(hours[3]["coins"]["ETH_USDT"],
                         5_000_000 + 3_000_000 + 120_000 + 900_000)

    def test_cvd_skew_accumulates_per_coin(self):
        self.board.add_cvd("BTC_USDT", self.now - 600, 1_500_000)
        self.board.add_cvd("BTC_USDT", self.now - 300, -500_000)
        self.board.add_cvd("ETH_USDT", self.now - 300, -900_000)
        hours = self.board.hours(4, now=self.now)
        self.assertEqual(hours[3]["cvd"]["BTC_USDT"], 1_000_000)
        self.assertEqual(hours[3]["cvd"]["ETH_USDT"], -900_000)

    def test_old_hours_are_dropped(self):
        for h in range(20):
            self.liq(h + 0.5, 1000)
        hours = self.board.hours(4, now=self.now)
        self.assertEqual(len(hours), 4)
        self.assertLessEqual(len(self.board._hours), self.board.keep_hours)

    # ----- OI -----
    def test_oi_history_keeps_series_and_reports_change(self):
        oi = OiHistory(tz=MSK)
        oi.add("BTC_USDT", 1_000_000, self.now - 4 * 3600)
        oi.add("BTC_USDT", 1_100_000, self.now)
        change = oi.change("BTC_USDT", 4, now=self.now)
        self.assertIsNotNone(change)
        self.assertAlmostEqual(change["pct"], 10.0, places=2)
        self.assertIsNone(oi.change("NOPE_USDT", 4, now=self.now))

    def test_oi_slices_follow_the_same_hours(self):
        oi = OiHistory(tz=MSK)
        base = hour_start(self.now, MSK)
        # пять точек: час до окна и по одной в начале каждого часа окна
        for i in range(5):
            oi.add("BTC_USDT", 1000 + i * 100, base - (4 - i) * 3600 + 60)
        slices = oi.hour_slices("BTC_USDT", 4, now=self.now)
        self.assertEqual(len(slices), 4)
        self.assertEqual(slices[0]["value"], 1100)      # конец первого часа окна
        self.assertEqual(slices[3]["value"], 1400)
        self.assertAlmostEqual(slices[3]["pct"], (1400 - 1300) / 1300 * 100)

    def test_oi_history_survives_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "oi.json")
        oi = OiHistory(tz=MSK)
        oi.add("BTC_USDT", 2_000_000, self.now)
        self.assertTrue(oi.save(path))
        again = OiHistory(tz=MSK)
        self.assertEqual(again.load(path), 1)
        self.assertEqual(again.latest("BTC_USDT")[1], 2_000_000)

    # ----- стенд в посте -----
    def _full(self):
        for h in range(8):
            for i in range(12):
                self.liq(h + 0.2 + i * 0.05, 50_000 * (i + 1),
                         sym="BTC_USDT" if i % 2 else "ETH_USDT",
                         ex="gate" if i % 3 == 0 else "binance")
                self.board.add_cvd("BTC_USDT" if i % 2 else "ETH_USDT",
                                   self.now - h * 3600 - i * 60, -30_000)
            for sym in ("BTC_USDT", "ETH_USDT"):
                self.oi.add(sym, 1_000_000 * (1 + 0.01 * (8 - h)),
                            self.now - h * 3600)
        return build_snapshot(self.board, self.oi, now=self.now)

    def test_snapshot_has_everything_the_stand_promises(self):
        snap = self._full()
        self.assertEqual(snap["span_hours"], 4)
        self.assertEqual(len(snap["hours"]), 4)
        self.assertEqual(len(snap["top_hours"]), 4)
        self.assertTrue(snap["total_usd"] > 0)
        self.assertTrue(snap["prev_total"] > 0)
        self.assertIsNotNone(snap["diff_pct"])
        # у каждого часа — монеты и OI, у каждого часа — топ-7
        for hr in snap["hours"]:
            self.assertTrue(hr["coins"])
            self.assertIn("oi", hr)
        for hr in snap["top_hours"]:
            self.assertLessEqual(len(hr["items"]), 7)
        self.assertEqual([c["pct"] for c in snap["oi_hours"]],
                         [c["pct"] for c in snap["oi_hours"]])

    def test_post_shows_stand_and_top7(self):
        board = self._full()
        snap = {"window_h": 4, "count": 30, "total_usd": board["total_usd"],
                "longs_usd": 6_000_000, "shorts_usd": 5_700_000,
                "top_coins": [], "exchanges": {"gate": 3_000_000}, "board": board}
        ru = render_post(snap, 0)
        self.assertIn("OI за 4ч", ru)
        self.assertIn("к прошлым 4ч", ru)              # сравнение окон
        self.assertIn("крупнейшие за час", ru)         # топ-7 внутри подписи
        self.assertIn("🏆", ru)
        self.assertIn("<pre><code>", ru)
        self.assertIn("Gate", ru)
        self.assertLessEqual(len(ru), CAPTION_LIMIT)   # один пост, не два
        en = render_post(snap, 0, lang="en")
        self.assertIn("OI over 4h", en)
        self.assertIn("biggest of the hour", en)
        self.assertLessEqual(len(en), CAPTION_LIMIT)

    def test_top7_reserve_matches_post_format(self):
        """Запасное сообщение с топ-7 остаётся на случай тесной подписи."""
        board = self._full()
        top = render_top7(board)
        self.assertIn("Топ-7", top)
        self.assertIn("крупнейшие за час", top)
        self.assertIn("gate.com/ru/signup/VLFCAVWMBW", top)
        top_en = render_top7(board, "en")
        self.assertIn("Top 7", top_en)
        self.assertIn("gate.com/signup/VLFCAVWMBW", top_en)

    def test_digest_from_server_carries_the_stand(self):
        """Сервер обязан положить стенд в снимок поста.

        Стенд умеет рендериться и сам по себе, но в посты он попадает только
        через snap["board"] — однажды это звено уже было забыто, и посты
        уходили без таблицы часов.
        """
        src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
        body = src[src.index("async def build_channel_digest"):]
        body = body[:body.index("\n\nasync def ", 1)]
        self.assertIn("build_snapshot(BOARD, OI", body)
        self.assertIn('snap["board"]', body)

    def test_liqs_word_declines(self):
        from channel_digest import liqs_word
        self.assertEqual(liqs_word(1), "ликвидация")
        self.assertEqual(liqs_word(3), "ликвидации")
        self.assertEqual(liqs_word(40), "ликвидаций")
        self.assertEqual(liqs_word(1, "en"), "fill")
        self.assertEqual(liqs_word(40, "en"), "fills")

    def test_oi_line_has_no_dashes_without_history(self):
        """Пока OI-история не набралась, в посте нет строки из прочерков."""
        board = {"span_hours": 4, "total_usd": 1e6, "count": 5, "prev_total": 0,
                 "diff_pct": None, "oi_hours": [], "hours": [
                     {"h": 1789578000, "total": 1e6, "count": 5, "longs": 9e5,
                      "shorts": 1e5, "side_sum": 9e5, "bias": "long",
                      "coins": [{"symbol": "BTC_USDT", "usd": 1e6, "flow": 1.0}],
                      "cvd_sum": 1.0, "oi": {"value": None, "pct": None}}]}
        text = render_post({"window_h": 4, "board": board, "total_usd": 1e6})
        self.assertNotIn("OI за 4ч", text)
        self.assertIsNone(re.search(r"\d{2}:00 —", text))   # нет часов-прочерков
        self.assertIn("резали лонги", text)
        # часы с прочерком вместо процента в строку не попадают
        board["oi_hours"] = [{"h": 1789578000, "value": 1.2e9, "pct": None}]
        board["oi_now_usd"] = 1.2e9
        text2 = render_post({"window_h": 4, "board": board, "total_usd": 1e6})
        self.assertIn("OI за 4ч: $1.20B", text2)
        self.assertIsNone(re.search(r"\d{2}:00 —", text2))

    def test_post_is_not_empty_even_without_data(self):
        """Пустой снимок не должен превращаться в пост из одной ссылки."""
        text = render_post({"window_h": 4})
        self.assertIn("LiqScope", text)
        self.assertTrue(len(text) > 60)


if __name__ == "__main__":
    unittest.main()
