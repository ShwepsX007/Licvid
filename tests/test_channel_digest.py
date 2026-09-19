"""Сводка в канал: лидеры, биржи, OI/CVD, разные тексты."""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from channel_digest import (  # noqa: E402
    CAPTION_LIMIT, VARIANT_COUNT, _dwidth, _headlines, _mono, collect_digest,
    format_headline, list_images, money, pick_image, post_has_hours,
    render_post, short_money,
)


def _ev(sym, usd, side, exch, ts, n=1):
    return {"symbol": sym, "usd": usd, "side": side,
            "exchange": exch, "timestamp": ts, "id": f"{sym}-{n}"}


def _board(total=4_640_000, count=5):
    """Часовой стенд: ровно те данные, из которых собирается пост.

    Часы идут от старого к новому — как их отдаёт build_snapshot; сам пост
    разворачивает ряд и показывает свежий час первым.
    """
    base = 986_400                            # 13:00 МСК (10:00 UTC)
    pcts = (-4.2, 3.1, -1.8, 0.5)              # ход OI по часам, %
    shares = (4.2, 2.7, -3.1, -0.3)            # CVD как доля объёма рынка, %
    hours = [(base - (3 - i) * 3600, pcts[i], shares[i]) for i in range(4)]
    top = []
    for i, (h, _pct, _share) in enumerate(hours):
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
    out_hours = []
    for i, (h, pct, share) in enumerate(hours):
        out_hours.append({
            "h": h, "tz": 3 * 3600, "total": 1_200_000 - i * 100_000, "count": 2,
            "longs": 700_000.0, "shorts": 500_000.0, "bias": "long",
            "coins": [{"symbol": "BTC_USDT", "usd": 2_400_000 - i * 100_000,
                       "flow": None, "pct": 31.0 - i * 4.0},
                      {"symbol": "ETH_USDT", "usd": 1_100_000 - i * 50_000,
                       "flow": None, "pct": -12.0 + i},
                      {"symbol": "SOL_USDT", "usd": 250_000, "flow": None,
                       "pct": 6.0 + i}],
            # сколько событий дала монета и её вклад в CVD: из этого пост
            # берёт «лидера часа» и «лидера окна»
            "cnt": {"BTC_USDT": 120 - i * 10, "ETH_USDT": 40 + i * 40,
                    "SOL_USDT": 15},
            "cvd": {"BTC_USDT": -12_500_000.0, "ETH_USDT": 3_200_000.0,
                    "SOL_USDT": -5_000_000.0},
            "cvd_sum": -14_300_000.0,
            "oi": {"value": 1.2e9, "pct": pct},
            "liq_pct": None if i == 0 else 8.0 - i,
            "count_pct": None, "vol_usd": 20_000_000.0,
            "cvd_net": 900_000.0 - i * 300_000, "cvd_share": share,
        })
    leaders = {
        "vol": {"symbol": "BTC_USDT", "usd": 9_000_000.0, "count": 420,
                "share": 61.9},
        "count": {"symbol": "ETH_USDT", "count": 166, "usd": 4_100_000.0,
                  "share": 55.9},
        "cvd": {"symbol": "ETH_USDT", "net": 50_000_000.0, "usd": 4_100_000.0,
                "count": 166},
        "oi": {"symbol": "SOL_USDT", "usd": 120_000_000.0, "pct": 2.4},
    }
    return {
        "tz": 3, "span_hours": 4, "total_usd": total, "count": count,
        "leaders": leaders,
        "prev_total": 4_000_000, "diff_pct": 16.0,
        "cvd_4h": 2_400_000.0, "cvd_4h_share": 3.6, "vol_4h": 66_000_000.0,
        "hours": out_hours, "top_hours": top,
        "oi_hours": [{"h": h, "pct": pct, "value": 1.2e9}
                     for h, pct, _sh in hours],
        "oi_now_usd": 1_200_000_000, "oi_4h_pct": -1.2,
    }


def _live_board():
    """Стенд с цифрами живого канала: крупные кассы, длинные тикеры.

    На таких числах подпись почти полна, и раньше русский пост (шапка длиннее)
    терял лидеров часа, пока английский их показывал. Лидер часа по событиям
    (ETH) и по деньгам (BTC) — разные монеты, поэтому у каждого часа есть «💰»,
    а лидер окна (SOL) ни с одним из них не совпадает.
    """
    board = _board()
    for i, hr in enumerate(board["hours"]):
        hr["total"] = 44_600_000 - i * 3_100_000
        hr["count"] = 180 + i * 7
        hr["liq_pct"] = 662 - i * 40
        hr["oi"] = {"value": 43_100_000_000, "pct": 3 - i}
        hr["cnt"] = {"ETH_USDT": 114 + i, "BTC_USDT": 60 + i}
        hr["cvd"] = {"ETH_USDT": -12_500_000.0, "BTC_USDT": 3_200_000.0}
    board["total_usd"] = 61_970_000
    board["cvd_4h"] = 12_400_000.0
    board["cvd_4h_share"] = -7.4
    board["leaders"]["vol"] = {"symbol": "SOL_USDT", "usd": 61_970_000.0,
                               "count": 1100, "share": 61.9}
    board["leaders"]["count"] = {"symbol": "BTC_USDT", "count": 12146,
                                 "usd": 41_000_000.0, "share": 55.9}
    board["leaders"]["oi"] = {"symbol": "SOL_USDT", "usd": 320_000_000.0,
                              "pct": 2.4}
    return board


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
            self.assertIn("LiqScope", t)
            self.assertIn("https://liqscope.online", t)
            self.assertIn("t.me/LiqScopeBot", t)
            self.assertLessEqual(len(t), CAPTION_LIMIT)
            self.assertIn("OI", t)
            self.assertIn("🕘", t)                 # часы в подписи
            self.assertNotIn("<pre>", t)           # рамки с цифрами больше нет
            self.assertTrue(any(ch in t for ch in "💥🔴🟢🐋📊🌊🏛🔥⚡🕘🌙🌡⏱❓📋📉📈"))

    def test_one_post_carries_hours(self):
        """Один пост: все четыре часа внутри подписи, рамочного окна нет."""
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        t = render_post(snap, 0)
        self.assertEqual(t.count("💥 <b>$4.64M</b>"), 1)   # касса ровно раз
        self.assertNotIn("<pre>", t)
        self.assertNotIn("</code>", t)
        self.assertEqual(t.count("🕘 <b>"), 4)             # все четыре часа
        self.assertLess(t.index("13:00"), t.index("10:00"))  # свежий первым
        self.assertNotIn("00:00", t)         # часы в МСК, а не в эпохе
        self.assertIn("к прошлым 4ч", t)
        self.assertIn("📊 OI", t)
        self.assertTrue(post_has_hours(t))   # второй пост с топом не нужен

    def test_hours_have_liqs_oi_and_cvd(self):
        """Час — одной строкой: касса, OI и CVD долей объёма в одном ряду."""
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        t = render_post(snap, 0)
        # четыре часа: касса с эмодзи направления, OI, CVD от объёма
        # процент к предыдущему часу — у каждого часа, кроме самого первого
        with_pct = re.findall(
            r"🕘 <b>\d{2}:00</b>(?: \(идёт\))? 💥 <b>\$[\d.]+[KMB]</b> [📈📉]", t)
        self.assertEqual(len(with_pct), 3, t)
        self.assertEqual(t.count("📊 OI "), 4)          # OI часа — в его же строке
        self.assertEqual(t.count("% объёма"), 5)        # четыре часа + строка окна
        self.assertEqual(t.count("🌊 CVD"), 5)          # четыре часа + строка окна
        self.assertIn("🌊 CVD за 4ч", t)
        self.assertGreaterEqual(t.count("📈"), 3)
        self.assertGreaterEqual(t.count("📉"), 2)
        # час — ровно одна строка цифр: OI и CVD стоят в ней же, а не под ней
        for line in t.splitlines():
            if line.startswith("🕘 <b>"):
                self.assertIn("📊 OI", line, line)
                self.assertIn("🌊 CVD", line, line)

    def test_hour_leaders_survive_in_both_languages(self):
        """Лидеры часа есть и в русском посте, и в английском.

        Регрессия из живого канала: русский текст длиннее, и лестница видов
        выбирала часы БЕЗ лидеров, пока английский их показывал — в одном
        канале данные были, в другом нет. Теперь часы (с лидерами) выбираются
        первыми, а строки окна заполняют остаток.
        """
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        for lang, mark in (("ru", "🏆 Лидер часа:"), ("en", "🏆 Hour leader:")):
            t = render_post(snap, 0, lang=lang)
            self.assertEqual(t.count(mark), 4, (lang, t))
            self.assertEqual(t.count("🕘 <b>"), 4, (lang, t))
        # длинная шапка сжимает строки окна, но лидеры часа остаются в обоих
        long_head = "🧪 " + "очень длинная шапка от ИИ " * 5
        for lang in ("ru", "en"):
            t = render_post(snap, 0, lang=lang, head_override=long_head)
            self.assertEqual(t.count("🕘 <b>"), 4, (lang, t))
            self.assertIn("🏆", t, (lang, t))
            # все пять строк с 🏆: лидер окна и четыре лидера часов
            stars = [ln for ln in t.splitlines() if ln.startswith("🏆")]
            self.assertEqual(len(stars), 5, (lang, t))
            self.assertLessEqual(len(t), CAPTION_LIMIT, (lang, len(t)))
        # совсем тесная подпись: лидер часа уходит в короткий вид без слов
        # «Лидер часа», но не пропадает
        huge = "🧪 " + "шапка " * 40
        t = render_post(snap, 0, lang="ru", head_override=huge)
        self.assertNotIn("🏆 Лидер часа:", t)
        under_hours = [ln for ln in t.splitlines() if ln.startswith("🏆 <b>")]
        self.assertEqual(len(under_hours), 4, t)              # все четыре часа
        self.assertLessEqual(len(t), CAPTION_LIMIT, len(t))

    def test_hour_leaders_with_long_live_head(self):
        """Живой случай: длинная шапка и крупные цифры — лидеры часа остаются.

        Регрессия из канала: русская шапка приходит из админки или от ИИ и
        бывает на всю длину (240 знаков), английская — короткий шаблон из кода.
        На крупных цифрах русский пост не влезал ни в подробные часы, ни в
        короткие, лестница брала часы совсем без лидеров и добивала место
        строками окна — в русском канале «🏆» под часами пропадал, в
        английском оставался. Теперь у часа есть тесный вид: касса и её
        изменение строкой, под ней лидер часа с деньгами.
        """
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _live_board()
        long_head = "🧪 " + "рынок кипит " * 40          # длиннее 240 знаков
        for lang in ("ru", "en"):
            t = render_post(snap, 0, lang=lang, head_override=long_head)
            self.assertLessEqual(len(t), CAPTION_LIMIT, (lang, len(t)))
            lines = t.splitlines()
            under = [i for i, ln in enumerate(lines) if ln.startswith("🕘 <b>")]
            self.assertEqual(len(under), 4, (lang, t))
            for i in under:
                self.assertTrue(lines[i + 1].startswith("🏆 "), (lang, t))
            # лидер окна и четыре лидера часа — каждый час с деньгами (💰)
            stars = [ln for ln in lines if ln.startswith("🏆 ")]
            self.assertEqual(len(stars), 5, (lang, t))
            self.assertEqual(sum(1 for ln in stars if "💰" in ln), 4, (lang, t))
        # у русского канала при этом нет слов «Лидер часа»: подпись и так полна
        t = render_post(snap, 0, lang="ru", head_override=long_head)
        self.assertNotIn("🏆 Лидер часа:", t)
        # с обычной короткой шапкой русский пост остаётся подробным
        t = render_post(snap, 0, lang="ru")
        self.assertEqual(t.count("🏆 Лидер часа:"), 4, t)

    def test_leaders_of_window_and_hour(self):
        """Лидеры: окно by money и по событиям, час — своим лидером."""
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        t = render_post(snap, 0)
        self.assertIn("🏆 Лидирует <b>BTC</b>", t)
        self.assertIn("$9.00M", t)
        self.assertIn("420 событий", t)
        # лидер по количеству — другая монета: монета и число, слово уже выше
        self.assertIn("· 🥇 ETH 166", t)
        # каждый час подписан своим лидером (по числу событий и с суммой)
        self.assertEqual(t.count("🏆 Лидер часа:"), 4, t)
        self.assertIn("🏆 Лидер часа: <b>BTC</b> · 120 событий · $2.4M", t)
        # в одном из часов лидер по количеству — не тот, кто по деньгам
        self.assertIn("🏆 Лидер часа: <b>ETH</b> · 160 событий · $950K", t)
        # сумма монеты-лидера окна в часах не повторяется: она уже строкой выше
        self.assertNotIn("💰 BTC $2.1M", t)
        self.assertIn("🌊 CVD за 4ч", t)
        # когда шапка короткая и в подпись влезает всё — видны и перекос CVD
        # с монетой, которая его дала, и сдвиг OI со своей монетой
        short = render_post(snap, 0, head_override="🧪 Коротко")
        self.assertIn("🌊 Перекос CVD · <b>ETH</b>", short)
        self.assertIn("$50.00M", short)
        self.assertIn("📊 Сдвиг OI · <b>SOL</b>", short)
        self.assertIn("+$120M", short)
        self.assertIn("📈 2.4%", short)
        self.assertEqual(short.count("🏆 Лидер часа:"), 4, short)
        # если лидер окна по деньгам — не та монета, что в часе, её сумма
        # снова появляется в часах: без неё монета осталась бы без цифры
        board2 = _board()
        board2["leaders"]["vol"]["symbol"] = "SOL_USDT"
        snap2 = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap2["board"] = board2
        t2 = render_post(snap2, 0, head_override="🧪 Коротко")
        self.assertIn("💰 BTC $2.1M", t2)
        self.assertIn("🏆 Лидирует <b>SOL</b>", t2)
        # длинная шапка не должна сжимать часы до одной строки: лидеры часов
        # важнее сводки окна
        self.assertIn("🏆 Лидер часа:", t)

    def test_long_headline_keeps_all_hours(self):
        """Полные сроки часов не влезли — часы уходят короткими строками."""
        snap = collect_digest(self.events, now=self.now, oi=self.oi, cvd=self.cvd)
        snap["board"] = _board()
        head = "Смешанный рынок: лонги подчистили, шорты добрали. " * 7
        t = render_post(snap, 0, head_override=head)
        self.assertLessEqual(len(t), CAPTION_LIMIT)
        self.assertEqual(t.count("🕘 <b>"), 4)     # ни один час не потерялся
        self.assertIn("% объёма", t)
        self.assertNotIn("<pre>", t)
        self.assertNotIn("🔝", t)                  # короткий вид — без монет
        self.assertTrue(post_has_hours(t))

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
            self.assertIn("🕘", t)
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

    def test_interval_sets_window_and_block(self):
        """Частота постов 1…10 ч: блок анализа — четверть окна."""
        from channel_digest import (MAX_INTERVAL_H, MIN_INTERVAL_H, block_secs,
                                    clamp_interval, interval_hours, window_word)
        self.assertEqual((MIN_INTERVAL_H, MAX_INTERVAL_H), (1, 10))
        self.assertEqual(block_secs(4), 3600)          # раз в 4 ч → по часу
        self.assertEqual(block_secs(1), 900)           # раз в час → по 15 минут
        self.assertEqual(block_secs(10), 9000)         # раз в 10 ч → по 2.5 часа
        self.assertEqual(window_word(3600), "1ч")
        self.assertEqual(window_word(900), "15м")
        self.assertEqual(window_word(9000), "2ч30м")
        self.assertEqual(window_word(900, "en"), "15m")
        for bad in (0, -3, 99, None, "мусор"):
            self.assertTrue(MIN_INTERVAL_H <= clamp_interval(bad) <= MAX_INTERVAL_H)
        # в базе пусто — работает значение по умолчанию (переменная окружения)
        os.environ.pop("LIQSCOPE_POST_INTERVAL_H", None)
        self.assertEqual(interval_hours(None), 4)
        os.environ["LIQSCOPE_POST_INTERVAL_H"] = "7"
        try:
            self.assertEqual(interval_hours(None), 7)
        finally:
            os.environ.pop("LIQSCOPE_POST_INTERVAL_H", None)

    def test_interval_from_store_wins(self):
        """Настройка из админки важнее окружения — расписание меняют на ходу."""

        class Store:
            def __init__(self, value):
                self.value = value

            def get_setting(self, key):
                return self.value

        from channel_digest import INTERVAL_SETTING, interval_hours
        os.environ["LIQSCOPE_POST_INTERVAL_H"] = "4"
        try:
            self.assertEqual(interval_hours(Store("2")), 2)
            self.assertEqual(interval_hours(Store("11")), 10)   # прижали к границе
            self.assertEqual(interval_hours(Store("")), 4)      # пусто → окружение
        finally:
            os.environ.pop("LIQSCOPE_POST_INTERVAL_H", None)

    def test_quarter_hour_blocks_are_labelled(self):
        """Пост раз в час: блоки по 15 минут подписаны временем начала."""
        base = 986_400
        hours = []
        for i in range(4):
            hours.append({
                "h": base + i * 900, "tz": 3 * 3600, "block_sec": 900,
                "total": 500_000 - i * 100_000, "count": 1, "longs": 400_000.0,
                "shorts": 100_000.0, "side_sum": 400_000.0, "bias": "long",
                "coins": [{"symbol": "BTC_USDT", "usd": 500_000 - i * 100_000,
                           "flow": None, "pct": None}],
                "cvd": {}, "cvd_sum": 0.0, "liq_pct": None, "count_pct": None,
                "live": i == 3, "vol_usd": 10_000_000.0, "cvd_net": 0.0,
                "cvd_share": 1.5, "oi": {"value": 1e9, "pct": 0.5},
            })
        snap = collect_digest([], window_sec=3600, now=self.now)
        snap["board"] = {
            "tz": 3 * 3600, "window_hours": 1, "span_hours": 4, "span_blocks": 4,
            "block_sec": 900, "slot_sec": 900, "group": 1, "window_sec": 3600,
            "total_usd": 1_400_000, "count": 4, "prev_total": 1_000_000,
            "diff_pct": 40.0, "oi_now_usd": 1e9, "oi_4h_pct": 1.0,
            "cvd_4h": 100_000.0, "cvd_4h_share": 1.5, "vol_4h": 40_000_000.0,
            "hours": hours, "top_hours": [],
            "oi_hours": [{"h": h["h"], "pct": 0.5, "value": 1e9} for h in hours],
        }
        t = render_post(snap, 0)
        for hh in ("13:00", "13:15", "13:30", "13:45"):
            self.assertIn(hh, t, hh)
        self.assertEqual(t.count("🕘 <b>"), 4)
        self.assertIn("за 1ч", t)                    # окно поста — тот же час
        self.assertLessEqual(len(t), CAPTION_LIMIT)

    def test_headline_word_follows_number(self):
        """Слово после числа часов согласуется: 1 час, 2 часа, 5 часов."""
        self.assertIn("за 1 час.", format_headline("Сводка за {h} часа.", 1))
        self.assertIn("2 часа", format_headline("Сводка за {h} часа.", 2))
        self.assertIn("5 часов", format_headline("Сводка за {h} часа.", 5))
        self.assertIn("11 часов", format_headline("Сводка за {h} часа.", 11))
        self.assertIn("1 hour", format_headline("Recap for {h} hours", 1, "en"))
        self.assertIn("3 hours", format_headline("Recap for {h} hours", 3, "en"))
        # шаблон с «{h}ч» остаётся как есть — там согласовывать нечего
        self.assertIn("4ч", format_headline("Разбор за {h}ч", 4))

    def test_cover_photo_rotates(self):
        """Обложка листается: обложкой по очереди бывает каждое фото набора.

        Альбома в постах больше нет — в канал уходит ровно одна картинка,
        поэтому ротация проверяется на выборе обложки.
        """
        imgs = list_images()
        self.assertGreaterEqual(len(imgs), 2, imgs)
        first = pick_image(0, images=imgs)
        second = pick_image(1, images=imgs)
        self.assertEqual(first, imgs[0])
        self.assertEqual(second, imgs[1])            # обложка сдвинулась
        # по кругу: после последнего фото снова первое
        self.assertEqual(pick_image(len(imgs), images=imgs), imgs[0])
        # пустой набор из админки — берутся картинки из комплекта
        self.assertEqual(pick_image(0, images=[]), list_images()[0])

    def test_images_exist(self):
        imgs = list_images()
        self.assertGreaterEqual(len(imgs), 6, imgs)
        self.assertTrue(os.path.isfile(pick_image(0)))
        self.assertNotEqual(pick_image(0), pick_image(1))

    def test_cover_from_admin_goes_round_without_repeats(self):
        """Фото из админки листаются по кругу без повторов.

        Раньше обложка бралась по номеру поста: при небольшом наборе картинки
        повторялись предсказуемо. Теперь база отдаёт самое «давнее» фото, а
        порядок круга каждый раз новый.
        """
        import tempfile
        from accounts import Store
        from channel_digest import pick_active_image, pick_digest_image

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(os.path.join(tmp, "a.db"), secret="test-secret")
            batch = [(b"\xff\xd8\xff\xe0" + bytes([i]) * 64 + b"\xff\xd9",
                      f"p{i}.jpg") for i in range(4)]
            store.add_digest_photos(batch, kind="post")
            n = len(store.list_digest_photos("post"))
            picks = [pick_active_image(store, "post") for _ in range(n)]
            self.assertEqual(len(set(picks)), n, picks)
            for path in picks:
                self.assertTrue(os.path.isfile(path), path)
            # своей рубрики дайджеста нет — берём постовые, тоже по кругу
            d = [pick_digest_image(store) for _ in range(3)]
            self.assertEqual(len(set(d)), 3, d)
            # без базы остаётся комплект
            self.assertTrue(os.path.isfile(pick_active_image(None, "post")))
            store.close()


if __name__ == "__main__":
    unittest.main()
