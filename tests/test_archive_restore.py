"""Месячный архив переживает перезагрузку: стенд, факты дайджеста, лента.

Публикаций здесь нет намеренно: свёртка месяца — это цифры за час или сутки,
а не выпуск. На /digest и /hourly попадает только то, что реально сохранил
бот (см. tests/test_archive_delete.py и tests/test_hourly_posts.py).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from archive_restore import (cvd_from_cells, facts_from_cells, merge_events,
                             overlay_archive_facts, slot_flows_from_cells)
from hour_board import HOUR, HourBoard, apply_archive_span

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = 1_800_000_000


def cell(usd=1000.0, n=2, long=700.0, short=300.0, cvd=50.0, vol=5000.0,
         sym="BTC_USDT"):
    return {
        "liq_usd": usd, "liq_count": n, "liq_long": long, "liq_short": short,
        "max_usd": usd, "max_symbol": sym, "cvd": cvd, "vol": vol, "has_cvd": True,
        "sym": {sym: {"usd": usd, "n": n, "long": long, "short": short,
                      "cvd": cvd, "vol": vol}},
        "exch": {"binance": {"usd": usd, "n": n}},
    }


class FactsTest(unittest.TestCase):
    def test_cells_become_digest_facts_with_cvd(self):
        facts = facts_from_cells([(NOW, cell())])
        self.assertEqual(facts["liq_count"], 2)
        self.assertAlmostEqual(facts["liq_total_usd"], 1000.0)
        self.assertAlmostEqual(facts["cvd_usd"], 50.0)
        self.assertEqual(facts["coins"][0]["symbol"], "BTC_USDT")
        self.assertEqual(facts["source"], "archive")

    def test_overlay_keeps_a_complete_window(self):
        facts = {"liq_total_usd": 1000.0, "liq_count": 2, "cvd_usd": 50.0,
                 "max": {"usd": 1000.0}}
        # час чуть шире точного окна — не должен подменять уже полные цифры
        wider = cell(usd=1010.0, n=3)
        out = overlay_archive_facts(facts, [(NOW, wider)])
        self.assertEqual(out["liq_count"], 2)
        self.assertAlmostEqual(out["liq_total_usd"], 1000.0)

    def test_overlay_fills_an_empty_restart(self):
        out = overlay_archive_facts({"liq_total_usd": 0, "liq_count": 0},
                                    [(NOW, cell())])
        self.assertEqual(out["liq_count"], 2)
        self.assertEqual(out["totals_source"], "archive")
        self.assertAlmostEqual(out["cvd_usd"], 50.0)

    def test_cells_do_not_become_publications(self):
        """Свёртка часа — не пост: страницы публикаций её не показывают."""
        import archive_restore as mod
        self.assertFalse(hasattr(mod, "digest_records_from_cells"))
        self.assertFalse(hasattr(mod, "hourly_posts_from_cells"))

    def test_slot_flows(self):
        flows = slot_flows_from_cells([(NOW, cell())])
        self.assertIn("BTC_USDT", flows)
        slot = flows["BTC_USDT"][NOW]
        self.assertTrue(slot["has_cvd"])
        self.assertAlmostEqual(slot["cvd"], 50.0)

    def test_cvd_comes_from_cells_not_candles(self):
        self.assertAlmostEqual(cvd_from_cells([(NOW, cell())], "BTC_USDT"), 50.0)
        self.assertIsNone(cvd_from_cells([]))

    def test_merge_events_dedupes_and_keeps_unflushed(self):
        arch = [{"id": "a", "timestamp": NOW, "usd": 1}]
        mem = [{"id": "a", "timestamp": NOW, "usd": 9},
               {"id": "b", "timestamp": NOW + 1, "usd": 2}]
        rows = merge_events(mem, arch, NOW - 10, NOW + 10)
        self.assertEqual([r["id"] for r in rows], ["a", "b"])
        self.assertEqual(rows[0]["usd"], 1)


class BoardRestoreTest(unittest.TestCase):
    def test_replay_after_keep_is_raised_keeps_the_month(self):
        old = NOW - 20 * HOUR
        # баг: вставка при keep=12 выкидывает час, как только ячеек стало больше лимита
        board = HourBoard(tz=0, keep_hours=12)
        for n in range(20):
            board.add_liq({"timestamp": old + n * HOUR, "usd": 100, "side": "SELL",
                           "symbol": "BTC_USDT"})
        self.assertNotIn(int(old // HOUR * HOUR), board._hours)
        self.assertLessEqual(len(board._hours), 12)

        kept = HourBoard(tz=0, keep_hours=31 * 24)
        for n in range(20):
            kept.add_liq({"timestamp": old + n * HOUR, "usd": 100, "side": "SELL",
                          "symbol": "BTC_USDT"})
        self.assertIn(int(old // HOUR * HOUR), kept._hours)
        self.assertAlmostEqual(kept._hours[int(old // HOUR * HOUR)]["total"], 100.0)

    def test_archive_fills_a_hole_without_doubling_a_replay(self):
        board = HourBoard(tz=0, keep_hours=48)
        h = int(NOW // HOUR * HOUR)
        board.add_liq({"timestamp": h + 10, "usd": 1000, "side": "SELL",
                       "symbol": "BTC_USDT", "exchange": "binance"})
        self.assertFalse(apply_archive_span(board, h, cell(usd=1000)))
        self.assertAlmostEqual(board._hours[h]["total"], 1000.0)
        self.assertEqual(board._hours[h]["top"][0]["exchange"], "binance")

        empty = HourBoard(tz=0, keep_hours=48)
        self.assertTrue(apply_archive_span(empty, h, cell()))
        self.assertAlmostEqual(empty._hours[h]["total"], 1000.0)
        self.assertEqual(empty._hours[h]["coins"]["BTC_USDT"], 1000.0)


class TapeSourceTest(unittest.TestCase):
    def test_terminal_reads_month_shards_not_only_ram_limit(self):
        src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        fn = src.split("async function loadHistoryFor", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("/api/history?bucket=raw", fn)
        self.assertIn("ARCHIVE_HOURS", fn)
        self.assertIn("bucket=hour", fn)
        boot = src.split("function boot()", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("loadHistoryFor", boot)
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        # лимит стенда поднимается раньше, чем события вставляются в доску
        replay = server.split("for ev in loaded:", 1)[0]
        self.assertIn("BOARD.keep_hours", replay)
        self.assertLess(replay.rfind("BOARD.keep_hours"), replay.rfind("loaded = loaded[-HISTORY_MAX:]") + 5000)
        self.assertIn("SLOTS.add_liq", server.split("for ev in loaded:", 1)[1][:600])


if __name__ == "__main__":
    unittest.main()
