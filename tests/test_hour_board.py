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
    CAPTION_LIMIT, post_has_hours, render_post, render_top7,
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
    def _flows(self):
        """Часовые потоки по монетам: {монета: {час: {cvd, vol, has_cvd}}}."""
        out = {}
        last = int(self.now // 3600 * 3600)
        for j, sym in enumerate(("BTC_USDT", "ETH_USDT")):
            out[sym] = {last - h * 3600: {"cvd": 1_000_000.0 - h * 100_000,
                                          "vol": 50_000_000.0 + j * 1_000_000,
                                          "has_cvd": True}
                        for h in range(5)}
        return out

    def _full(self, flows=None):
        # История OI пишется только хронологически: срезы каждого часа идут
        # от старого к новому, по два на час (начало и конец) — так у часа
        # есть и уровень, и процент к его началу.
        series = [1.00, 1.02, 1.01, 1.03, 1.01, 1.04, 1.02, 1.05]   # млн $
        hours = list(range(7, -1, -1))
        for idx, h in enumerate(hours):
            for i in range(12):
                self.liq(h + 0.2 + i * 0.05, 50_000 * (i + 1),
                         sym="BTC_USDT" if i % 2 else "ETH_USDT",
                         ex="gate" if i % 3 == 0 else "binance")
                self.board.add_cvd("BTC_USDT" if i % 2 else "ETH_USDT",
                                   self.now - h * 3600 - i * 60, -30_000)
            hs = int(self.now // 3600 * 3600) - h * 3600
            nxt = series[min(idx + 1, len(series) - 1)]
            for sym, k in (("BTC_USDT", 1.0), ("ETH_USDT", 0.92)):
                self.oi.add(sym, series[idx] * 1_000_000 * k, hs + 60)
                self.oi.add(sym, nxt * 1_000_000 * k, min(hs + 3500, self.now))
        return build_snapshot(self.board, self.oi, now=self.now, flows=flows)

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

    def test_post_shows_hours_without_frame(self):
        """Пост: четыре часа, у каждого OI и CVD — простым текстом, без <pre>."""
        board = self._full(flows=self._flows())
        snap = {"window_h": 4, "count": 30, "total_usd": board["total_usd"],
                "longs_usd": 6_000_000, "shorts_usd": 5_700_000,
                "top_coins": [], "exchanges": {"gate": 3_000_000}, "board": board}
        ru = render_post(snap, 0)
        self.assertIn("к прошлым 4ч", ru)              # сравнение окон
        self.assertEqual(ru.count("🕘 <b>"), 4)        # все четыре часа
        self.assertEqual(ru.count("📊 OI"), 4)         # OI по каждому часу
        self.assertIn("% объёма", ru)                  # CVD — доля объёма рынка
        self.assertIn("🌊 CVD за 4ч", ru)
        self.assertNotIn("<pre>", ru)                  # рамочного окна нет
        self.assertNotIn("</code>", ru)
        self.assertIn("Gate", ru)                      # партнёрская строка жива
        self.assertLessEqual(len(ru), CAPTION_LIMIT)   # один пост, не два
        self.assertTrue(post_has_hours(ru))
        en = render_post(snap, 0, lang="en")
        self.assertIn("of volume", en)
        self.assertEqual(en.count("🕘 <b>"), 4)
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
        уходили без таблицы часов. Частота постов задаёт длину блока (N/4 часа),
        поэтому снимок обязан нести и её: иначе пост вернётся к «часу на блок».
        """
        src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
        body = src[src.index("async def build_channel_digest"):]
        body = body[:body.index("\n\nasync def ", 1)]
        self.assertIn("build_snapshot(SLOTS, OI", body)
        self.assertIn("group=interval", body)
        self.assertIn("post_interval_hours", body)
        self.assertIn("slot_flows", body)
        self.assertIn("group=interval", body)
        self.assertIn('snap["board"]', body)

    def test_block_groups_fold_slots(self):
        """Частота постов задаёт блок: 15 минут × N слотов на один блок."""
        from hour_board import SLOT_SEC, HourBoard, build_snapshot
        now = 986_400 + 53 * 60          # 13:53 МСК, внутри четверти часа
        board = HourBoard(slot_sec=SLOT_SEC, keep_hours=64)
        for i in range(16):              # 16 четвертей = 4 часа истории
            for k in range(i + 1):
                board.add_liq({"symbol": "BTC_USDT", "usd": 100_000.0,
                               "side": "SELL", "exchange": "binance",
                               "timestamp": now - i * SLOT_SEC - k})
        snap = build_snapshot(board, OiHistory(), now=now, span=4, group=4)
        self.assertEqual(snap["block_sec"], 4 * SLOT_SEC)     # блок — час
        self.assertEqual(len(snap["hours"]), 4)               # четыре блока
        self.assertEqual(snap["group"], 4)
        self.assertEqual(snap["slot_sec"], SLOT_SEC)
        self.assertEqual(snap["window_sec"], 4 * 3600)
        self.assertGreater(snap["total_usd"], 0)
        # блок из четырёх четвертей больше одной четверти
        self.assertGreater(snap["hours"][0]["total"], 100_000.0)
        # часовая группировка: последний блок — тот же час, что и слот now
        from hour_board import slot_start
        self.assertEqual(snap["hours"][-1]["h"] + snap["block_sec"],
                         slot_start(now, board.tz, 3600) + 3600)
        # одиночные слоты (частота раз в час) дают блоки по 15 минут
        snap1 = build_snapshot(board, OiHistory(), now=now, span=4, group=1)
        self.assertEqual(snap1["block_sec"], SLOT_SEC)
        self.assertEqual(len(snap1["hours"]), 4)
        self.assertTrue(snap1["hours"][-1]["live"])           # текущая четверть идёт
        self.assertFalse(snap1["hours"][0]["live"])

    def test_slot_flows_fold_into_blocks(self):
        """CVD и объём блоков складываются из четвертей: доля считается честно."""
        from hour_board import SLOT_SEC, HourBoard, OiHistory, build_snapshot
        now = 986_400 + 53 * 60
        board = HourBoard(slot_sec=SLOT_SEC, keep_hours=64)
        for i in range(12):
            board.add_liq({"symbol": "BTC_USDT", "usd": 10_000.0, "side": "BUY",
                           "exchange": "okx", "timestamp": now - i * SLOT_SEC})
        flows = {}
        for i in range(12):
            slots = flows.setdefault("BTC_USDT", {})
            slots[int((now - i * SLOT_SEC) // SLOT_SEC * SLOT_SEC)] = {
                "cvd": 1_000.0, "vol": 100_000.0, "has_cvd": True,
            }
        snap = build_snapshot(board, OiHistory(), now=now, span=4, group=3,
                              flows=flows)
        self.assertEqual(snap["block_sec"], 3 * SLOT_SEC)     # 45 минут
        self.assertAlmostEqual(snap["cvd_4h"], 12 * 1_000.0, places=3)
        self.assertAlmostEqual(snap["vol_4h"], 12 * 100_000.0, places=3)
        self.assertAlmostEqual(snap["cvd_4h_share"], 1.0, places=6)
        self.assertAlmostEqual(snap["hours"][-1]["cvd_share"], 1.0, places=6)

    def test_liqs_word_declines(self):
        from channel_digest import liqs_word
        self.assertEqual(liqs_word(1), "ликвидация")
        self.assertEqual(liqs_word(3), "ликвидации")
        self.assertEqual(liqs_word(40), "ликвидаций")
        self.assertEqual(liqs_word(1, "en"), "fill")
        self.assertEqual(liqs_word(40, "en"), "fills")

    def test_oi_line_has_no_dashes_without_history(self):
        """Пока OI-история не набралась, часы идут без OI и без прочерков."""
        board = {"span_hours": 4, "total_usd": 1e6, "count": 5, "prev_total": 0,
                 "diff_pct": None, "oi_hours": [], "hours": [
                     {"h": 1789578000, "total": 1e6, "count": 5, "longs": 9e5,
                      "shorts": 1e5, "side_sum": 9e5, "bias": "long",
                      "coins": [{"symbol": "BTC_USDT", "usd": 1e6, "flow": 1.0}],
                      "cvd_sum": 1.0, "liq_pct": None,
                      "oi": {"value": None, "pct": None}}]}
        text = render_post({"window_h": 4, "board": board, "total_usd": 1e6})
        self.assertNotIn("📊 OI", text)          # OI нет — строки нет вовсе
        self.assertIsNone(re.search(r"\d{2}:00 —", text))   # часов-прочерков нет
        self.assertIn("🕘", text)                # час всё равно показан
        self.assertIn("$1.00M", text)
        # появился уровень — появилась строка, процент без истории не выдуман
        board["hours"][0]["oi"] = {"value": 1.2e9, "pct": None}
        text2 = render_post({"window_h": 4, "board": board, "total_usd": 1e6})
        self.assertIn("📊 OI $1.20B", text2)
        self.assertIsNone(re.search(r"\d{2}:00 —", text2))

    def test_post_is_not_empty_even_without_data(self):
        """Пустой снимок не должен превращаться в пост из одной ссылки."""
        text = render_post({"window_h": 4})
        self.assertIn("LiqScope", text)
        self.assertTrue(len(text) > 60)


if __name__ == "__main__":
    unittest.main()
