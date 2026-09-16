"""Сводка в канал: лидеры, биржи, OI/CVD, разные тексты."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from channel_digest import (  # noqa: E402
    CAPTION_LIMIT, VARIANT_COUNT, _dwidth, _headlines, _mono, collect_digest,
    format_headline, list_images, money, pick_image, render_post, short_money,
)


def _ev(sym, usd, side, exch, ts, n=1):
    return {"symbol": sym, "usd": usd, "side": side,
            "exchange": exch, "timestamp": ts, "id": f"{sym}-{n}"}


def _board(total=4_640_000, count=5):
    """Часовой стенд: ровно те данные, из которых собирается пост."""
    base = 1_000_000 - 1_000_000 % 3600      # начало часа в секундах
    hours = [(base - i * 3600, pct) for i, pct in
             enumerate((-0.42, 0.31, -0.18, 0.05))]
    top = []
    for i, (h, pct) in enumerate(hours):
        top.append({
            "h": h, "tz": 3 * 3600, "total": 1_200_000 - i * 100_000, "count": 2,
            "items": [
                {"symbol": "BTC_USDT", "usd": 2_400_000 - i * 100_000,
                 "side": "SELL", "exchange": "binance"},
                {"symbol": "ETH_USDT", "usd": 1_100_000 - i * 50_000,
                 "side": "BUY", "exchange": "okx"},
                {"symbol": "SOL_USDT", "usd": 250_000, "side": "BUY",
                 "exchange": "bybit"},
                {"symbol": "DOGE_USDT", "usd": 90_000, "side": "SELL",
                 "exchange": "gate"},
            ],
        })
    return {
        "tz": 3, "span_hours": 4, "total_usd": total, "count": count,
        "prev_total": 4_000_000, "diff_pct": 16.0,
        "hours": [{"h": h, "total": 1_200_000, "count": 2, "coins": [],
                   "bias": "long", "oi": {"value": 1.2e9, "pct": pct}}
                  for h, pct in hours],
        "top_hours": top,
        "oi_hours": [{"h": h, "pct": pct, "value": 1.2e9} for h, pct in hours],
        "oi_now_usd": 1_200_000_000, "oi_4h_pct": -1.2,
    }


class DigestTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_000_000.0
        self.events = [
            _ev("BTC_USDT", 2_400_000, "SELL", "binance", self.now - 60, 1),
            _ev("BTC_USDT", 800_000, "BUY", "bybit", self.now - 120, 2),
            _ev("ETH_USDT", 1_100_000, "SELL", "okx", self.now - 200, 3),
            _ev("SOL_USDT", 250_000, "BUY", "binance", self.now - 300, 4),
            _ev("DOGE_USDT", 90_000, "SELL", "gate", self.now - 400, 5),
            # слишком старое — не в окне 4ч
            _ev("XRP_USDT", 9_000_000, "SELL", "binance", self.now - 20_000, 6),
        ]
        self.oi = {
            "BTC_USDT": {"total_usd": 12e9,
                         "changes": {"h4": {"usd": -180_000_000, "pct": -1.45}}},
            "ETH_USDT": {"total_usd": 4e9,
                         "changes": {"h4": {"usd": 40_000_000, "pct": 1.02}}},
        }
        # CVD и OI в жизни есть не по каждой монете: в правой колонке
        # показывается тот индикатор, который по деньгам сильнее
        self.cvd = {"BTC_USDT": -12_500_000, "ETH_USDT": 3_200_000,
                    "SOL_USDT": -5_000_000}

    def test_collect_window_and_leaders(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        self.assertEqual(snap["count"], 5)
        self.assertAlmostEqual(snap["total_usd"], 4_640_000)
        self.assertEqual(snap["top_coins"][0]["symbol"], "BTC_USDT")
        self.assertIn("binance", snap["exchanges"])
        self.assertNotIn("XRP_USDT", [c["symbol"] for c in snap["top_coins"]])
        self.assertEqual(snap["biggest"]["usd"], 2_400_000)

    def test_variants_differ_and_contain_facts(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        texts = [render_post(snap, i) for i in range(VARIANT_COUNT)]
        self.assertEqual(len(_headlines(4)), VARIANT_COUNT)
        self.assertGreaterEqual(VARIANT_COUNT, 20)
        self.assertEqual(len(set(texts)), VARIANT_COUNT)
        for t in texts:
            self.assertIn("BTC", t)
            self.assertIn("Binance", t)
            self.assertIn("LiqScope", t)
            self.assertIn("https://liqscope.online", t)
            self.assertIn("t.me/LiqScopeBot", t)
            self.assertLessEqual(len(t), CAPTION_LIMIT)
            self.assertIn("OI", t)
            self.assertIn("<code>", t)
            self.assertTrue(any(ch in t for ch in "💥🔴🟢🐋📊🌊🏛🔥⚡🏆🌙🌡⏱❓📋📉📈"))

    def test_one_post_carries_top7_hours(self):
        """Один пост: цифры не дублируются, топ-7 живёт в той же подписи."""
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        t = render_post(snap, 0)
        self.assertEqual(t.count("💥 <b>$4.64M</b>"), 1)   # касса ровно раз
        self.assertIn("🏆", t)
        # все часы представлены: сколько влезло полными таблицами, остальные —
        # сжатой строкой (три удара часа), чтобы час не пропал из поста
        self.assertEqual(t.count("<b>🏆"), 4)
        self.assertGreaterEqual(t.count("</code></pre>"), 2)
        self.assertIn(" · ", t.split("<b>🏆")[-1])      # последний час — сжатый
        self.assertIn("крупнейшие за час", t)
        # часов влезает столько, сколько помещается в подпись (но не меньше трёх)
        self.assertGreaterEqual(t.count("крупнейшие за час"), 3)
        self.assertNotIn("00:00", t)         # часы в МСК, а не в эпохе
        self.assertIn("к прошлым 4ч", t)
        self.assertIn("📊 OI за 4ч", t)

    def test_no_heavy_tables_in_post(self):
        """В посте нет таблиц с колонками цифр — они спорили друг с другом."""
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        t = render_post(snap, 0)
        self.assertNotIn("Биржи", t)
        self.assertNotIn("шт.", t)
        for i in range(VARIANT_COUNT):
            self.assertNotIn("Кит", render_post(snap, i))

    def test_post_without_board_still_readable(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        t = render_post(snap, 0)
        self.assertIn("$4.64M", t)
        self.assertIn("резали лонги", t)
        self.assertLessEqual(len(t), CAPTION_LIMIT)

    def test_legacy_views_still_render(self):
        """Запасные виды: табличный стенд и старые таблицы — живы, но в пост не идут."""
        from channel_digest import build_board, _facts
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        stand = build_board(snap["board"])
        self.assertIn("СТЕНД", stand)
        self.assertIn("Всего", stand)
        self.assertIn("<pre><code>", stand)
        self.assertIn("OI за 4ч", stand)
        self.assertNotIn("СТЕНД", render_post(snap, 0))     # в пост таблица не идёт
        facts = _facts(snap, tables=True)
        for key in ("ex", "ex6", "coins", "coins8", "kpi"):
            self.assertIn(key, facts)
        self.assertNotIn("ex", _facts(snap))                # по умолчанию не считаем

    def test_money_shorter_in_top7(self):
        """В топ-7 суммы короче: знак в строке стоит строки."""
        self.assertEqual(short_money(2_400_000), "$2.4M")
        self.assertEqual(short_money(899_400), "$899K")
        self.assertEqual(short_money(1_260_000_000), "$1.3B")
        self.assertEqual(short_money(0), "$0")

    def test_emoji_do_not_break_columns(self):
        """Столбики не разъезжаются: эмодзи считается за две знакоместа."""
        rows = [
            [("1. BTC 🔴", "l"), ("$2.4M", "r"), ("Binance", "l")],
            [("2. ETH 🟢", "l"), ("$1.1M", "r"), ("OKX", "l")],
            [("3. LONG_NAME", "l"), ("$900K", "r"), ("Gate", "l")],
        ]
        block = _mono(rows)
        body = block.split("<code>")[1].split("</code>")[0]
        lines = body.splitlines()
        self.assertEqual(len(lines), 3)
        widths = {_dwidth(line.split("$")[0]) for line in
                  (l for l in lines if "$" in l)}
        self.assertEqual(len(widths), 1, lines)

    def test_blogger_heads_are_alive(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        heads = []
        for i in range(VARIANT_COUNT):
            line = render_post(snap, i).split("\n", 1)[0]
            heads.append(line)
            self.assertNotIn("LiqScope ·", line)
            self.assertNotIn("LIQ //", line)
            self.assertTrue(any(ch in line for ch in ".?—!"), line)
            self.assertIn("<b>", line)
        self.assertEqual(len(set(heads)), VARIANT_COUNT)

    def test_caption_fits_with_long_headline(self):
        """Даже с длинной шапкой ИИ пост остаётся одним сообщением."""
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        long_head = ("Смешанная картина: лонги BTC и ETH вынесли на $2.4M,"
                     " шорты SOL и DOGE получили своё, открытый интерес по"
                     " рынку просел на 1.2% за четыре часа, а суммарно лента"
                     " отдала больше четырёх с половиной миллионов долларов."
                     " Подробности по часам — ниже, в блоке с топом.")
        for i in range(VARIANT_COUNT):
            t = render_post(snap, i, head_override=long_head)
            self.assertLessEqual(len(t), CAPTION_LIMIT, f"v{i} len={len(t)}")
            self.assertIn("LiqScope", t)
            self.assertIn("🏆", t)
            self.assertTrue(t.rstrip().endswith("</a>"), t[-90:])

    def test_money_and_empty(self):
        self.assertEqual(money(1_250_000), "$1.25M")
        self.assertEqual(money(-900), "−$900")
        snap = collect_digest([], now=self.now)
        t = render_post(snap, 0)
        self.assertTrue(t.strip())          # пост не пустой даже без данных
        self.assertLessEqual(len(t), CAPTION_LIMIT)

    def test_custom_headlines_used(self):
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        custom = [format_headline("☕ Моя шапка за {h}ч. Топы ниже.", 4)]
        t = render_post(snap, 0, headlines=custom)
        self.assertIn("Моя шапка", t)
        self.assertIn("BTC", t)
        self.assertLessEqual(len(t), CAPTION_LIMIT)
        self.assertIn("4ч", t)

    def test_images_exist(self):
        imgs = list_images()
        self.assertGreaterEqual(len(imgs), 6, imgs)
        self.assertTrue(os.path.isfile(pick_image(0)))
        self.assertNotEqual(pick_image(0), pick_image(1))


if __name__ == "__main__":
    unittest.main()
