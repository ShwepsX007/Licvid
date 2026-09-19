"""Дневной дайджест: сборка фактов, тексты, архив и планировщик.

Проверяем то, что легко сломать незаметно: суммы и срезы за сутки, порядок
монет по OI относительно оборота, знак настроения, сборку поста/статьи на двух
языках, замену выпуска в архиве и вечернее время публикации.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import daily_digest  # noqa: E402
from ai_text import body_problem, clean_body, fit_body  # noqa: E402
from daily_digest import (  # noqa: E402
    DEFAULT_KEEP, DigestStore, brief, collect_day, day_key, day_label, day_prompt,
    channel_link_block, fallback_narrative, headline_block, lead_of, mood_of,
    oi_block, price_txt, render_channel,
    prices_block, render_article, render_post, weekday_label,
)


def _events(now, spec):
    """spec: (символ, биржа, сторона, usd, сколько часов назад)."""
    out = []
    for sym, exch, side, usd, ago in spec:
        out.append({"timestamp": now - ago * 3600, "symbol": sym,
                    "exchange": exch, "side": side, "usd": usd})
    return out


def _flows(now, spec):
    """spec: {монета: [(cvd, объём), … по часам]}."""
    out = {}
    for sym, rows in spec.items():
        cells = {}
        for i, (cvd, vol) in enumerate(rows):
            h = int((now - (i + 1) * 3600) // 3600 * 3600)
            cells[str(h)] = {"cvd": cvd, "vol": vol, "has_cvd": True}
        out[sym] = cells
    return out


class CollectDayTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_770_000_000.0

    def test_sums_exchanges_and_biggest(self):
        events = _events(self.now, [
            ("BTC_USDT", "binance", "SELL", 1_000_000, 1),
            ("BTC_USDT", "binance", "BUY", 500_000, 2),
            ("ETH_USDT", "bybit", "SELL", 250_000, 3),
            ("SOL_USDT", "bybit", "SELL", 100_000, 20),          # в окне суток
            ("XRP_USDT", "okx", "BUY", 900_000, 26),             # старше суток — мимо
        ])
        f = collect_day(events, now=self.now, oi={}, prices={})
        self.assertEqual(f["liq_count"], 4)
        self.assertAlmostEqual(f["liq_total_usd"], 1_850_000.0)
        self.assertAlmostEqual(f["longs_usd"], 1_350_000.0)     # SELL = лонги
        self.assertAlmostEqual(f["shorts_usd"], 500_000.0)
        self.assertEqual(f["max"]["usd"], 1_000_000.0)
        self.assertEqual(f["max"]["symbol"], "BTC_USDT")
        ex = {e["name"]: e for e in f["exchanges"]}
        self.assertEqual(ex["binance"]["count"], 2)
        self.assertAlmostEqual(ex["binance"]["usd"], 1_500_000.0)
        self.assertLess(ex["bybit"]["usd"], ex["binance"]["usd"])

    def test_oi_relative_to_turnover_ordering(self):
        events = _events(self.now, [("BTC_USDT", "binance", "SELL", 10_000, 1)])
        oi = {
            "BTC_USDT": {"total_usd": 5e9, "changes": {"h24": {"pct": 2.5}}},
            "PEPE_USDT": {"total_usd": 2e8, "changes": {"h24": {"pct": -4.0}}},
            "AAVE_USDT": {"total_usd": 1e8, "changes": {"h24": {"pct": 9.0}}},
        }
        prices = {"BTC_USDT": {"price": 100000, "pct": 1.0, "vol_usd": 9e9},
                  "PEPE_USDT": {"price": 1e-5, "pct": -3.0, "vol_usd": 1e8},
                  "AAVE_USDT": {"price": 300, "pct": 7.0, "vol_usd": 5e7}}
        f = collect_day(events, now=self.now, oi=oi, prices=prices, oi_top=5)
        order = [o["symbol"] for o in f["oi_top"]]
        # OI/оборот: PEPE 2.0, AAVE 2.0, BTC 0.55 — BTC последний, мелкие впереди
        self.assertEqual(order[-1], "BTC_USDT")
        self.assertAlmostEqual(f["oi_top"][0]["ratio"], 2.0, places=6)
        # монеты без оборота в таблицу не попадают
        oi["NOVOL_USDT"] = {"total_usd": 1e12, "changes": {"h24": {"pct": 5}}}
        f2 = collect_day(events, now=self.now, oi=oi, prices=prices, oi_top=5)
        self.assertNotIn("NOVOL_USDT", [o["symbol"] for o in f2["oi_top"]])

    def test_top_prices_by_turnover_and_pct(self):
        events = _events(self.now, [("BTC_USDT", "binance", "SELL", 1e6, 1)])
        prices = {"BTC_USDT": {"price": 100000, "pct": -1.5, "vol_usd": 9e9},
                  "ETH_USDT": {"price": 3000, "pct": 2.5, "vol_usd": 5e9},
                  "SOL_USDT": {"price": 150, "pct": 4.0, "vol_usd": 1e9},
                  "XRP_USDT": {"price": 2.2, "pct": -0.2, "vol_usd": 8e8},
                  "DOGE_USDT": {"price": 0.2, "pct": 0.0, "vol_usd": 7e8},
                  "PEPE_USDT": {"price": 1e-5, "pct": 30.0, "vol_usd": 1e6}}
        f = collect_day(events, now=self.now, oi={}, prices=prices, price_top=5)
        top = [p["symbol"] for p in f["prices"]]
        self.assertEqual(len(top), 5)
        self.assertNotIn("PEPE_USDT", top)          # самый слабый по обороту мимо
        self.assertEqual(top[0], "BTC_USDT")
        # без цен таблицу заполняем монетами из ликвидаций, без процентов
        f2 = collect_day(events, now=self.now, oi={}, prices={}, price_top=5)
        self.assertTrue(f2["prices"])
        self.assertIsNone(f2["prices"][0]["pct"])

    def test_taker_flow_and_mood_direction(self):
        events = _events(self.now, [
            ("BTC_USDT", "binance", "SELL", 900_000, 1),      # снесли лонги → вниз
            ("BTC_USDT", "binance", "BUY", 100_000, 2),
        ])
        prices = {"BTC_USDT": {"price": 100000, "pct": -3.0, "vol_usd": 5e9},
                  "ETH_USDT": {"price": 3000, "pct": -2.0, "vol_usd": 4e9}}
        flows = _flows(self.now, {"BTC_USDT": [(-4e8, 4e9)] * 6})
        bear = collect_day(events, now=self.now, oi={}, prices=prices, flows=flows)
        self.assertEqual(bear["mood"]["kind"], "bear")
        self.assertLess(bear["mood"]["score"], 0)
        self.assertIsNotNone(bear["cvd_usd"])
        self.assertLess(bear["cvd_usd"], 0)

        prices_up = {"BTC_USDT": {"price": 100000, "pct": 4.0, "vol_usd": 5e9},
                     "ETH_USDT": {"price": 3000, "pct": 5.0, "vol_usd": 4e9}}
        flows_up = _flows(self.now, {"BTC_USDT": [(5e8, 4e9)] * 6})
        events_up = _events(self.now, [("BTC_USDT", "binance", "BUY", 900_000, 1)])
        bull = collect_day(events_up, now=self.now, oi={}, prices=prices_up,
                           flows=flows_up)
        self.assertIn(bull["mood"]["kind"], ("bull", "bull_soft"))
        self.assertGreater(bull["mood"]["score"], 0)

    def test_quiet_market_is_neutral(self):
        events = _events(self.now, [("BTC_USDT", "binance", "SELL", 1000, 1),
                                    ("BTC_USDT", "binance", "BUY", 1000, 2)])
        prices = {"BTC_USDT": {"price": 100000, "pct": 0.02, "vol_usd": 1e9}}
        f = collect_day(events, now=self.now, oi={}, prices=prices)
        self.assertEqual(f["mood"]["kind"], "flat")

    def test_window_can_be_shortened(self):
        events = _events(self.now, [("BTC_USDT", "binance", "SELL", 1e6, 1),
                                    ("ETH_USDT", "binance", "SELL", 1e6, 8)])
        f = collect_day(events, now=self.now, window_sec=4 * 3600, oi={}, prices={})
        self.assertEqual(f["liq_count"], 1)
        self.assertEqual(f["window_h"], 4)


class LabelsTest(unittest.TestCase):
    def test_day_key_and_labels(self):
        # 2026-09-17 01:14 МСК: в Москве уже 17-е, по UTC — ещё 16-е
        ts = 1_789_596_850.0
        key = day_key(ts, tz=3 * 3600)
        self.assertEqual(key, "2026-09-17")
        self.assertEqual(day_key(ts, tz=0), "2026-09-16")
        # 22:14 МСК 16-го — предыдущие сутки
        self.assertEqual(day_key(ts - 3 * 3600, tz=3 * 3600), "2026-09-16")
        self.assertEqual(day_key(ts - 3 * 3600, tz=3 * 3600),
                         day_key(ts, tz=3 * 3600 - 3 * 3600))
        self.assertTrue(day_label(key, "ru")[0].isdigit())
        months = ("January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December")
        self.assertTrue(any(m in day_label(key, "en") for m in months),
                        day_label(key, "en"))
        self.assertTrue(weekday_label(key, "ru"))
        self.assertTrue(weekday_label(key, "en"))
        self.assertEqual(day_label("", "ru"), "")

    def test_price_and_pct_format(self):
        self.assertEqual(price_txt(101234.5), "$101,234")
        self.assertEqual(price_txt(3000.5), "$3,000.50")
        self.assertEqual(price_txt(9999.99), "$9,999.99")
        self.assertEqual(price_txt(0.0042), "$0.0042")
        self.assertEqual(price_txt(1.234e-5), "$0.00001234")
        self.assertEqual(price_txt(None), "—")
        self.assertEqual(price_txt(0), "—")


class TextTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_770_000_000.0
        events = _events(self.now, [
            ("BTC_USDT", "binance", "SELL", 1_800_000, 1),
            ("ETH_USDT", "bybit", "SELL", 400_000, 2),
            ("SOL_USDT", "okx", "BUY", 300_000, 4),
        ])
        oi = {"BTC_USDT": {"total_usd": 4e9, "changes": {"h24": {"pct": -3.0}}},
              "SOL_USDT": {"total_usd": 2e9, "changes": {"h24": {"pct": 6.0}}}}
        prices = {"BTC_USDT": {"price": 100000, "pct": -2.4, "vol_usd": 8e9},
                  "ETH_USDT": {"price": 3000, "pct": -3.1, "vol_usd": 6e9},
                  "SOL_USDT": {"price": 150, "pct": 1.2, "vol_usd": 2e9}}
        flows = _flows(self.now, {"BTC_USDT": [(-1e8, 4e9)] * 6})
        self.facts = collect_day(events, now=self.now, oi=oi, prices=prices,
                                 flows=flows)
        self.rec = {"id": "2026-09-16", "day": "2026-09-16", "facts": self.facts,
                    "ai": {"ru": "Русский рассказ. " * 30,
                           "en": "English narrative. " * 30}}

    def test_post_ru_has_all_five_parts(self):
        post = render_post(self.rec, "ru", "https://liqscope.online")
        self.assertIn("Дневной дайджест", post)
        self.assertIn("$1.80M", post)                 # крупнейшая ликвидация
        self.assertIn("BTC", post)
        self.assertIn("Биржи", post)
        self.assertIn("Открытый интерес против оборота", post)
        self.assertIn("Топовые монеты", post)
        self.assertIn("Настроение рынка", post)
        self.assertIn("https://liqscope.online/digest", post)
        self.assertNotIn("<pre>", post)

    def test_post_en_is_english(self):
        rec = dict(self.rec)
        rec["ai"] = {"ru": self.rec["ai"]["ru"], "en": "English narrative. " * 30}
        post = render_post(rec, "en", "https://liqscope.online")
        self.assertIn("Daily digest", post)
        self.assertIn("Exchanges", post)
        self.assertIn("Open interest vs turnover", post)
        self.assertIn("Market mood", post)
        self.assertIn("English narrative.", post)
        self.assertNotIn("Биржи", post)

    def test_post_falls_back_to_template_without_ai(self):
        rec = dict(self.rec)
        rec["ai"] = {}
        post = render_post(rec, "ru")
        self.assertIn("За сутки рынок снёс", post)
        self.assertTrue(len(post) > 600)

    def test_long_text_is_squeezed(self):
        rec = dict(self.rec)
        rec["ai"] = {"ru": "Очень длинный рассказ. " * 400}
        post = render_post(rec, "ru")
        self.assertLessEqual(len(post), 4200)

    def test_article_has_sections_and_escapes(self):
        art = render_article(self.rec, "ru")
        self.assertIn("<h3>", art)
        self.assertIn("<ul>", art)
        self.assertIn("<p>", art)
        rec = dict(self.rec)
        rec["ai"] = {"ru": "текст <script>alert(1)</script> " * 20}
        art2 = render_article(rec, "ru")
        self.assertNotIn("<script>", art2)
        self.assertIn("&lt;script&gt;", art2)

    def test_prompt_names_the_numbers(self):
        for lang in ("ru", "en"):
            prompt = day_prompt(self.facts, lang)
            self.assertIn("$1.80M", prompt)
            self.assertIn("BTC", prompt)
            self.assertIn("по биржам" if lang == "ru" else "by exchange", prompt)

    def test_fallback_narrative_has_no_invented_coins(self):
        text = fallback_narrative(self.facts, "ru")
        self.assertIn("BTC", text)
        self.assertIn("Открытый интерес", text)
        self.assertNotIn("$0.00", text)

    def test_mood_without_data_is_neutral(self):
        mood = mood_of(0, 0, 0.0, 0.0, [], [])
        self.assertEqual(mood["kind"], "flat")

    def test_block_helpers(self):
        self.assertIn("Крупнейшая ликвидация дня", headline_block(self.facts, "ru"))
        self.assertIn("Biggest", headline_block(self.facts, "en"))
        block = oi_block(self.facts, "ru")
        self.assertIn("<b>BTC</b>", block)
        self.assertIn("×", block)
        self.assertTrue(block.startswith("📈"))
        self.assertIn("▲", prices_block(self.facts, "ru") +
                      prices_block(self.facts, "en"))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "digests.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_list_get_and_replace(self):
        st = DigestStore(self.path, keep=5)
        st.save({"id": "2026-09-14", "day": "2026-09-14", "facts": {"liq_count": 1}})
        st.save({"id": "2026-09-15", "day": "2026-09-15", "facts": {"liq_count": 2}})
        self.assertEqual([r["id"] for r in st.list()], ["2026-09-15", "2026-09-14"])
        st.save({"id": "2026-09-15", "day": "2026-09-15", "facts": {"liq_count": 7}})
        self.assertEqual(len(st.list()), 2)
        self.assertEqual(st.get("2026-09-15")["facts"]["liq_count"], 7)
        # архив переживает перезапуск
        st2 = DigestStore(self.path, keep=5)
        self.assertEqual(len(st2.list()), 2)
        self.assertEqual(st2.get("2026-09-14")["id"], "2026-09-14")

    def test_newest_first_even_if_file_written_out_of_order(self):
        st = DigestStore(self.path)
        for day in ("2026-09-15", "2026-09-17", "2026-09-16"):
            st.save({"id": day, "day": day, "facts": {}})
        self.assertEqual([r["id"] for r in st.list()],
                         ["2026-09-17", "2026-09-16", "2026-09-15"])
        # и после перезапуска порядок не поедет
        st2 = DigestStore(self.path)
        self.assertEqual(st2.list()[0]["id"], "2026-09-17")

    def test_keep_limit_and_broken_file(self):
        st = DigestStore(self.path, keep=2)
        for d in ("01", "02", "03"):
            st.save({"id": f"2026-09-{d}", "facts": {}})
        self.assertEqual(len(st.list()), 2)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{битый json")
        st3 = DigestStore(self.path)
        self.assertEqual(st3.list(), [])
        self.assertTrue(st3.error)          # ошибка видна, а не проглочена
        st3.save({"id": "x", "facts": {}})   # и запись после этого работает

    def test_mark_published(self):
        st = DigestStore(self.path)
        st.save({"id": "2026-09-16", "day": "2026-09-16", "facts": {}})
        st.mark_published("2026-09-16", "ru", chat="-100500", ok=True)
        st.mark_published("2026-09-16", "en", chat="-100600", ok=False)
        rec = st.get("2026-09-16")
        self.assertTrue(rec["published"]["ru"]["ok"])
        self.assertFalse(rec["published"]["en"]["ok"])
        self.assertEqual(rec["published"]["ru"]["chat"], "-100500")


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        from api_digest import DigestScheduler
        self.cls = DigestScheduler

    def test_target_is_evening_msk(self):
        s = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600)
        ts = s.target_for("2026-09-17", 0.5)
        self.assertEqual(day_key(ts, tz=3 * 3600), "2026-09-17")
        tm = time.gmtime(ts + 3 * 3600)
        self.assertEqual((tm.tm_hour, tm.tm_min), (22, 0))
        # UTC-полночь наступит позже — 19:00 UTC того же дня
        self.assertEqual(time.strftime("%H:%M", time.gmtime(ts)), "19:00")

    def test_jitter_stays_inside_window(self):
        s = self.cls(hour=22, minute=0, jitter_min=10, tz=3 * 3600)
        for rnd in (0.0, 0.25, 0.5, 0.75, 1.0):
            ts = s.target_for("2026-09-17", rnd)
            shift = abs(ts - self.cls(hour=22, minute=0, jitter_min=0,
                                      tz=3 * 3600).target_for("2026-09-17", 0.5))
            self.assertLessEqual(shift, 10 * 60 + 1)

    def test_due_only_after_evening(self):
        s = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600)
        day = "2026-09-17"
        before = s.target_for(day, 0.5) - 3600
        after = s.target_for(day, 0.5) + 60
        # target пересчитывается на первом вызове — дёргаем дважды
        self.assertIsNone(s.due(before))
        self.assertEqual(s.due(after), day)
        s.mark(day)
        self.assertIsNone(s.due(after + 60))
        # следующий день: до вечера молчим, после вечера выпускаем
        nxt = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600).target_for(
            "2026-09-18", 0.5)
        self.assertIsNone(s.due(nxt - 60))
        nxt2 = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600).target_for(
            "2026-09-18", 0.5)
        self.assertEqual(s.due(nxt2 + 60), "2026-09-18")

    def test_slightly_late_restart_still_publishes(self):
        """Сервер подняли в 23:40 — выпуск всё равно уходит, день тот же."""
        s = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600)
        day = "2026-09-17"
        ts = s.target_for(day, 0.5)
        self.assertIsNone(s.due(ts - 3600))          # ещё не вечер
        self.assertEqual(s.due(ts + 100 * 60), day)

    def test_late_restart_skips_the_day(self):
        """Вечерний выпуск пропущен надолго — задним числом не публикуем."""
        s = self.cls(hour=1, minute=0, jitter_min=0, tz=3 * 3600, late_sec=90 * 60)
        day = "2026-09-17"
        ts = s.target_for(day, 0.5)                   # 01:00 МСК
        self.assertIsNone(s.due(ts - 600))
        self.assertIsNone(s.due(ts + 5 * 3600))       # поднялись в 06:00
        self.assertEqual(s.target_day, day)           # и больше не пытаемся
        self.assertIsNone(s.due(ts + 6 * 3600))

    def test_disabled_scheduler_never_fires(self):
        s = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600, enabled=False)
        self.assertIsNone(s.due(time.time() + 10 ** 6))

    def test_status_reports_schedule(self):
        s = self.cls(hour=22, minute=30, jitter_min=5, tz=3 * 3600)
        st = s.status()
        self.assertEqual((st["hour"], st["minute"], st["jitter_min"]), (22, 30, 5))
        self.assertEqual(st["tz"], 3 * 3600)
        self.assertEqual(st["tz_hours"], 3.0)
        self.assertIn("enabled", st)

    def test_apply_moves_release_time_and_resets_target(self):
        """Время поменяли с сайта — выпуск должен уйти уже по новому часу."""
        s = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600)
        day = "2026-09-17"
        self.assertIsNone(s.due(s.target_for(day, 0.5) - 3600))
        old_target = s.target_ts
        s.apply({"hour": 20, "minute": 30, "jitter_min": 0})
        self.assertEqual((s.hour, s.minute, s.jitter_min), (20, 30, 0))
        self.assertEqual(s.target_ts, 0.0, "цель пересчитается по новому времени")
        new_target = s.target_for(day, 0.5)
        self.assertLess(new_target, old_target)
        self.assertEqual(s.due(new_target + 60), day)

    def test_apply_can_turn_scheduler_off(self):
        s = self.cls(hour=22, minute=0, jitter_min=0, tz=3 * 3600)
        s.apply({"enabled": False})
        self.assertFalse(s.enabled)
        self.assertIsNone(s.due(time.time() + 10 ** 6))
        s.apply({"enabled": True, "hour": 25, "minute": 99})
        self.assertTrue(s.enabled)
        self.assertEqual((s.hour, s.minute), (1, 39))

    def test_apply_ignores_garbage(self):
        s = self.cls(hour=22, minute=0, jitter_min=10, tz=3 * 3600)
        s.apply({"hour": "вечер", "minute": None, "jitter_min": "x", "enabled": None})
        self.assertEqual((s.hour, s.minute, s.jitter_min), (22, 0, 10))


class ChannelPostTest(unittest.TestCase):
    """Пост в канал: небольшой (влезает в подпись под фото) и со ссылкой на сайт.

    Полный разбор с рассказом живёт на сайте (`render_post` → страница
    /digest), поэтому канал получает короткую версию: шапка, лид рассказа,
    цифры дня и красивая ссылка. Так фото прикрепляется к каждому выпуску.
    """

    def setUp(self):
        self.now = 1_770_000_000.0

    def _rec(self, story_ru=None, story_en=None):
        events = _events(self.now, [
            ("BTC_USDT", "binance", "SELL", 1_800_000, 1),
            ("ETH_USDT", "bybit", "BUY", 400_000, 2),
        ])
        facts = collect_day(events, now=self.now, oi={}, prices={})
        # рассказ из разных предложений: так видно, что в пост попал только лид
        long_ru = " ".join(f"Фраза {i} про рынок и ликвидации." for i in range(1, 61))
        long_en = " ".join(f"Sentence {i} about the market." for i in range(1, 61))
        return {"id": "2026-09-16", "day": "2026-09-16", "facts": facts,
                "ai": {"ru": story_ru if story_ru is not None else long_ru,
                       "en": story_en if story_en is not None else long_en}}

    def test_long_story_post_fits_caption(self):
        """Даже с рассказом на 2000 знаков пост влезает в подпись под фото."""
        rec = self._rec()
        for lang in ("ru", "en"):
            out = render_channel(rec, lang, "https://liqscope.online")
            self.assertLessEqual(len(out), 1024, (lang, len(out)))
            self.assertIn("/digest", out, lang)
            self.assertIn("liqscope.online", out, lang)
            self.assertIn(" …", out, lang)                  # лид оборван, есть куда идти
            story = rec["ai"][lang].strip()
            self.assertNotIn(story[-200:], out, lang)        # рассказ в пост не влез
            self.assertLess(len(out), len(story), lang)

    def test_channel_post_has_numbers_and_head(self):
        story = "Рынок весь день шёл боком, но к вечеру продавцы сдали. " * 8
        rec = self._rec(story_ru=story)
        out = render_channel(rec, "ru", "https://liqscope.online")
        self.assertIn("Дневной дайджест", out)
        self.assertIn("За сутки снесено", out)
        self.assertIn("Крупнейшая:", out)
        self.assertIn("Настроение рынка", out)
        self.assertIn("Полный разбор дня — на сайте", out)
        self.assertIn("Рынок весь день шёл боком", out)       # лид берётся из рассказа
        # совсем короткий рассказ заменяется шаблоном — как и в большом посте
        tiny = render_channel(self._rec(story_ru="Коротко."), "ru", "")
        self.assertIn("За сутки снесено", tiny)
        self.assertNotIn("Коротко.", tiny)

    def test_lead_cuts_at_sentence_boundary(self):
        long = "Первое предложение про рынок. " * 40
        out = lead_of(long, 200)
        self.assertLessEqual(len(out), 200)
        self.assertTrue(out.endswith("…"), out[-30:])
        self.assertTrue(out[:-2].rstrip().endswith("."), out[-40:])
        # текст короче лимита не трогаем
        self.assertEqual(lead_of("Короткий рассказ.", 200), "Короткий рассказ.")

    def test_post_without_site_url_has_no_link_but_still_fits(self):
        rec = self._rec()
        out = render_channel(rec, "ru", "")
        self.assertNotIn("/digest", out)
        self.assertLessEqual(len(out), 1024, len(out))

    def test_link_block_points_to_digest_page(self):
        self.assertIn('href="https://liqscope.online/digest"',
                      channel_link_block("https://liqscope.online", "ru"))
        self.assertIn("liqscope.online/digest",
                      channel_link_block("https://liqscope.online", "en"))
        self.assertEqual(channel_link_block("", "ru"), "")

    def test_site_version_keeps_whole_story(self):
        rec = self._rec()
        out = render_post(rec, "ru", "https://liqscope.online")
        self.assertIn(rec["ai"]["ru"].strip(), out)
        self.assertIn("Биржи", out)                     # полные блоки остаются
        self.assertNotIn("  …", out[:len(rec["ai"]["ru"]) + 60])


class BotPublishTest(unittest.TestCase):
    """Публикация дневного дайджеста ботом: каналы, черновик, языки."""

    def setUp(self):
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            self.skipTest("aiohttp нет — бот не соберётся")
        from accounts import Store
        from tg_bot import TelegramBot
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_ids=[1001])
        self.bot = TelegramBot("0:x", self.store, public_url="liqscope.online",
                               channel_id="-100111")
        self.store.upsert_telegram_user({"id": 1001, "username": "boss",
                                         "first_name": "Ada"})
        self.bot._channel2_id_cfg = "-100222"
        self.bot.username = "LiqScopeBot"
        self.sent = []

        async def fake_send(cid, text, kb=None, parse="HTML", message_id=None,
                            **kw):
            self.sent.append({"cid": str(cid), "text": text, "kb": kb})
            return len(self.sent)

        async def fake_photo(cid, path, caption="", kb=None, **kw):
            self.sent.append({"cid": str(cid), "text": caption, "kb": kb,
                              "photo": path})
            return len(self.sent)

        async def no_roles(*a, **k):
            return ""

        self.bot.send = fake_send
        self.bot.send_photo = fake_photo
        self.bot.verify_channel_roles = no_roles

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _rec(self):
        return {"id": "2026-09-16", "day": "2026-09-16",
                "facts": {"liq_total_usd": 5_000_000, "liq_count": 42,
                          "longs_usd": 3_000_000, "shorts_usd": 2_000_000,
                          "max": {"usd": 900_000, "symbol": "BTC_USDT",
                                  "exchange": "binance", "side": "SELL",
                                  "timestamp": time.time()},
                          "exchanges": [{"name": "binance", "usd": 4e6,
                                         "count": 20}],
                          "oi_top": [{"symbol": "BTC_USDT", "oi_usd": 4e9,
                                      "vol_usd": 8e9, "ratio": 0.5, "pct": 1.0}],
                          "prices": [{"symbol": "BTC_USDT", "price": 100000,
                                      "pct": -1.5, "vol_usd": 8e9}],
                          "mood": {"kind": "flat", "score": 0.01,
                                   "label": {"ru": "⚪ нейтральный",
                                             "en": "⚪ neutral"},
                                   "reasons": {"ru": ["цифры ровные"],
                                               "en": ["numbers are even"]}}},
                "ai": {"ru": "Русский рассказ про день. " * 20,
                       "en": "English story about the day. " * 20}}

    def _upload_photo(self, kind="digest"):
        """Фото рубрики так же, как его кладёт админка."""
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        res = self.store.add_digest_photo(png, "cover.png", actor_id=1001, kind=kind)
        self.assertTrue(res.get("ok"), res)
        return res.get("path") or ""

    def test_channel_post_goes_with_photo_and_link_to_site(self):
        """В канал уходит небольшой пост: фото, цифры дня и ссылка на сайт."""
        import asyncio
        path = self._upload_photo()
        rec = self._rec()
        rec["ai"] = {"ru": "Короткий рассказ про сутки. " * 8,
                     "en": "Short story about the day. " * 8}
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru",), force=True))
        msg = [m for m in self.sent if m["cid"] == "-100111"][0]
        self.assertEqual(msg.get("photo"), path, "фото должно уйти с постом")
        self.assertLessEqual(len(msg["text"]), 1024, len(msg["text"]))
        self.assertIn("За сутки снесено", msg["text"])
        self.assertIn("Крупнейшая:", msg["text"])
        self.assertIn("/digest", msg["text"], "ссылка на полный разбор на сайте")

    def test_channel_uses_the_cover_of_the_record(self):
        """В канал уходит та же обложка, что записана за выпуском и видна на сайте.

        Фото выбирает сборка выпуска (api_digest.assign_cover) — иначе картинка
        на странице /digest и в канале могла бы разъехаться.
        """
        import asyncio
        own = self._upload_photo(kind="digest")
        rec = self._rec()
        rec["photo"] = {"path": own, "name": "cover.png", "source": "admin", "id": 1}
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru",), force=True))
        msg = [m for m in self.sent if m["cid"] == "-100111"][0]
        self.assertEqual(msg.get("photo"), own)

    def test_channel_picks_a_cover_for_old_records(self):
        """Старая запись без обложки: фото всё равно уходит — пост без картинки не выходит."""
        import asyncio
        path = self._upload_photo(kind="digest")
        rec = self._rec()
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru",), force=True))
        msg = [m for m in self.sent if m["cid"] == "-100111"][0]
        self.assertEqual(msg.get("photo"), path, "взяли фото рубрики дайджеста")

    def test_caption_goes_on_the_record_cover_that_site_shows(self):
        """Обложка записи живёт и в разметке страницы: одна картинка на два места."""
        import api_digest
        own = self._upload_photo(kind="digest")
        rec = self._rec()
        rec["photo"] = {"path": own, "name": "cover.png", "source": "admin", "id": 7}
        pub = api_digest.public_photo(rec)
        self.assertTrue(pub and pub["url"].endswith("day=" + rec["day"]), pub)
        self.assertEqual(pub["id"], 7)
        self.assertEqual(pub["name"], "cover.png")
        self.assertEqual(pub["source"], "admin")

    def test_digest_post_has_button_to_full_breakdown(self):
        """Под дневным выпуском — кнопка на полный разбор на сайте."""
        import asyncio
        self._upload_photo()
        rec = self._rec()
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru", "en"), force=True))
        for cid in ("-100111", "-100222"):
            msg = [m for m in self.sent if m["cid"] == cid][0]
            rows = (msg.get("kb") or {}).get("inline_keyboard") or []
            urls = [b.get("url") for row in rows for b in row]
            self.assertTrue(any(str(u).endswith("/digest") for u in urls), urls)
            texts = [b.get("text") for row in rows for b in row]
            self.assertTrue(any("разбор" in str(t) or "breakdown" in str(t)
                                for t in texts), texts)

    def test_caption_length_counts_emoji_like_telegram(self):
        """Эмодзи в UTF-16 занимают два знака — лимит подписи считаем так же."""
        from tg_bot import caption_len
        self.assertEqual(caption_len("абв"), 3)
        self.assertEqual(caption_len("🎯"), 2)
        self.assertEqual(caption_len(""), 0)

    def test_long_story_keeps_channel_post_small_and_full_text_on_site(self):
        """Рассказ длинный — в канале всё равно короткий пост с фото.

        Полный разбор (рассказ целиком + все блоки) остаётся на сайте: в канал
        уходит шапка, первые предложения рассказа, цифры дня и ссылка. Так пост
        влезает в подпись под фотографией, и фото уходит всегда.
        """
        import asyncio
        self._upload_photo()
        rec = self._rec()
        long_story = "Подробный рассказ про сутки. " * 60      # ~1600 знаков
        rec["ai"] = {"ru": long_story, "en": "Long story about the day. " * 60}
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru",), force=True))
        msg = [m for m in self.sent if m["cid"] == "-100111"][0]
        self.assertIn("photo", msg, "короткий пост влезает в подпись под фото")
        caption = msg["text"]
        self.assertLessEqual(len(caption), 1024, len(caption))
        self.assertNotIn(long_story.strip(), caption)           # рассказ не целиком
        self.assertIn("Подробный рассказ про сутки.", caption)  # но лид из него
        self.assertIn("…", caption, "лид обрывается по границе и ведёт на сайт")
        self.assertIn("/digest", caption)
        # сайт получает рассказ целиком, без обрезки
        site = daily_digest.render_post(rec, "ru", "https://liqscope.online")
        self.assertIn(long_story.strip(), site)
        self.assertNotIn("…", site)

    def test_channel_draft_says_photo_fits_even_with_long_story(self):
        """В черновике сразу видно: фото прикрепится (пост короткий по замыслу)."""
        import asyncio
        self.bot._set_review(True)
        rec = self._rec()
        rec["ai"] = {"ru": "Длинный рассказ про сутки. " * 80,
                     "en": "Long story about the day. " * 80}
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru",), force=True))
        draft = self.sent[0]["text"]
        self.assertIn("Подпись:", draft)
        self.assertIn("влезает в подпись под фото", draft)

    def test_publishes_to_both_channels(self):
        import asyncio
        res = asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(self._rec(), ("ru", "en"), force=True))
        self.assertTrue(res["ru"][0], res)
        self.assertTrue(res["en"][0], res)
        cids = [m["cid"] for m in self.sent]
        self.assertIn("-100111", cids)
        self.assertIn("-100222", cids)
        ru = [m for m in self.sent if m["cid"] == "-100111"][0]["text"]
        en = [m for m in self.sent if m["cid"] == "-100222"][0]["text"]
        self.assertIn("Дневной дайджест", ru)
        self.assertIn("Daily digest", en)
        self.assertNotIn("Daily digest", ru)
        # отметка о публикации — для сайта и админки
        self.assertTrue(self.bot.daily_status()["ok"])

    def test_missing_english_channel_is_reported_not_lost(self):
        import asyncio
        self.bot._channel2_id_cfg = ""
        res = asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(self._rec(), ("ru", "en")))
        self.assertTrue(res["ru"][0])
        self.assertFalse(res["en"][0])
        self.assertIn("не привязан", res["en"][1])

    def test_review_mode_sends_draft_to_admin(self):
        import asyncio
        self.bot._set_review(True)
        res = asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(self._rec(), ("ru",), force=True))
        self.assertFalse(res["ru"][0])            # в канал не ушло
        cids = [m["cid"] for m in self.sent]
        self.assertIn("1001", cids)               # черновик — админу
        self.assertNotIn("-100111", cids)
        self.assertTrue(self.bot._daily_draft)
        datas = [b.get("callback_data") for row in
                 (self.sent[0]["kb"] or {}).get("inline_keyboard", []) for b in row]
        self.assertEqual(datas, ["dd:pub", "dd:regen", "dd:no"])

    def test_draft_tells_about_photo_caption_limit(self):
        """Черновик заранее говорит, влезает ли пост в подпись под фото.

        Telegram принимает подпись не длиннее 1024 знаков: админ должен видеть
        арифметику до «Опубликовать», а не удивляться отсутствию картинки.
        """
        import asyncio
        self.bot._set_review(True)
        rec = self._rec()
        rec["ai"] = {"ru": "Рассказ про сутки. " * 20,
                     "en": "Story about the day. " * 20}
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru",), force=True))
        draft = "\n".join(m["text"] for m in self.sent)
        self.assertIn("Подпись:", draft)
        self.assertIn("знаков", draft)
        self.assertIn("/digest", draft, "видно, где смотреть полный разбор")

    def test_draft_says_photo_fits(self):
        import asyncio
        self.bot._set_review(True)
        rec = self._rec()
        rec["ai"] = {"ru": "Короткий рассказ. " * 10, "en": "Short story. " * 10}
        asyncio.get_event_loop().run_until_complete(
            self.bot.publish_daily_digest(rec, ("ru",), force=True))
        draft = self.sent[0]["text"]
        self.assertIn("влезает в подпись под фото", draft)

    def test_admin_keyboard_has_digest_button(self):
        texts = [b.get("text") for row in self.bot._admin_kb()["inline_keyboard"]
                 for b in row]
        self.assertTrue(any("Дайджест" in t for t in texts), texts)


class BodyTextTest(unittest.TestCase):
    def test_clean_body_strips_markdown_and_links(self):
        raw = "**Рынок** [ок](https://x.y) `код`\n\n\n\nВторой абзац."
        out = clean_body(raw)
        self.assertNotIn("*", out)
        self.assertNotIn("`", out)
        self.assertNotIn("https://", out)
        self.assertIn("Второй абзац.", out)
        self.assertNotIn("\n\n\n", out)

    def test_body_problem_catches_short_language_and_refusals(self):
        self.assertEqual(body_problem("", "ru"), "пусто")
        self.assertTrue(body_problem("Коротко.", "ru"))
        self.assertTrue(body_problem("English text only, nothing Russian here.",
                                    "ru"))
        self.assertTrue(body_problem("Как ИИ, я не могу это сделать. " * 6, "ru"))
        long_ru = "Рынок снёс миллион долларов за сутки, вот что произошло. " * 8
        self.assertEqual(body_problem(long_ru, "ru"), "")
        self.assertTrue(body_problem(long_ru, "en"))      # русский текст в EN-канал


class BodyLimitTest(unittest.TestCase):
    def test_fit_body_cuts_at_sentence(self):
        text = ("Первое предложение. " * 200).strip()
        out = fit_body(text, 200)
        self.assertLessEqual(len(out), 200)
        self.assertTrue(out.endswith("."))

    def test_brief_is_one_line(self):
        rec = {"facts": {"liq_total_usd": 1_200_000, "liq_count": 5,
                         "mood": {"label": {"ru": "⚪ нейтральный",
                                            "en": "⚪ neutral"}}}}
        line = brief(rec, "ru")
        self.assertIn("$1.20M", line)
        self.assertIn("нейтральный", line)
        self.assertNotIn("\n", line)
        self.assertIn("neutral", brief(rec, "en"))


class EmptyDayPublishTest(unittest.TestCase):
    """Пустой выпуск (в памяти нет событий за сутки) в канал не уходит.

    Так выглядел настоящий сбой: после перезапуска история не восстановилась,
    и в канал ушёл пост с «$0 · 0 ликвидаций» да ещё и с рассказом из одной
    фразы. Теперь сервис честно говорит админу, что проверить.
    """

    def setUp(self):
        import api_digest
        self.api = api_digest
        self.sent = []

        async def fake_publish(rec, langs, force):
            self.sent.append((rec.get("day"), list(langs), bool(force)))
            return {lang: [True, ""] for lang in langs}

        async def fake_build(now=None, window=None, save=True, ai=True):
            return {"id": "2026-09-18", "day": "2026-09-18",
                    "facts": {"liq_total_usd": 0, "liq_count": 0,
                              "mood": {"kind": "flat", "score": 0.5,
                                       "label": {"ru": "⚪ нейтральный"},
                                       "reasons": {"ru": []}}},
                    "ai": {"ru": "According to liqscope.online. …"}}

        self._old_build = api_digest.build_digest
        self._old_publish = api_digest.ctx.publish_fn
        api_digest.build_digest = fake_build
        api_digest.ctx.publish_fn = fake_publish

    def tearDown(self):
        self.api.build_digest = self._old_build
        self.api.ctx.publish_fn = self._old_publish

    def test_empty_day_is_not_published(self):
        import asyncio
        rec = asyncio.get_event_loop().run_until_complete(
            self.api.publish_digest(now=1.7e9, langs=("ru", "en"), force=True,
                                    reason="schedule"))
        self.assertEqual(self.sent, [], "в канал ничего не ушло")
        self.assertEqual(rec.get("skipped"), "no_liquidations")
        ru = rec["published"]["ru"]
        self.assertFalse(ru[0])
        self.assertIn("ни одной ликвидации", ru[1])
        self.assertIn("liquidations_in_memory", ru[1])

    def test_retry_only_when_the_digest_did_not_go_out(self):
        """Повтор выпуска — только если он не ушёл: пустой день или отказ канала.

        Черновик админу повтора не требует (его ждёт кнопка «Опубликовать»), а
        неудачная отправка требует — но с паузой, иначе сборка с ИИ крутится
        каждую минуту.
        """
        done = self.api.publish_done
        self.assertFalse(done(None))
        self.assertFalse(done({"skipped": "no_liquidations",
                               "published": {"ru": [False, "пусто"]}}))
        self.assertTrue(done({"published": {"ru": [True, ""]}}))
        self.assertTrue(done({"published": {"ru": [False, ""]}}))       # черновик
        self.assertFalse(done({"published": {
            "ru": [False, "канал не привязан"], "en": [False, "нет канала"]}}))
        self.assertFalse(done({"published": {}}))
        # вторая форма записи — отметка DigestStore.mark_published (словарь:
        # {ok, at, chat}); на ней планировщик падал с KeyError: 0, поэтому
        # день не отмечался выпущенным и выпуск собирался заново каждую минуту
        self.assertTrue(done({"published": {"ru": {"ok": True, "chat": "-100"}}}))
        self.assertFalse(done({"published": {"ru": {"ok": False, "at": 1,
                                                    "chat": ""}}}))
        self.assertFalse(done({"published": {"ru": {"ok": False, "at": 1},
                                             "en": {"ok": False, "at": 1}}}))
        self.assertGreaterEqual(self.api.RETRY_SEC, 300)

    def test_reason_explains_what_to_check(self):
        text = self.api.empty_day_reason({"liq_count": 0, "liq_total_usd": 0})
        self.assertIn("история не восстановилась", text)
        self.assertIn("/api/health", text)


class ArchiveIndexTest(unittest.TestCase):
    """Архив выпусков для страницы дайджестов: свежие + индекс всех дат.

    Список свежих выпусков короткий (в ленте они не копятся), а на каждую дату
    архива сервер отдаёт лёгкую строку — по ней рисуется календарь и видно, за
    какие дни выпуск есть. Пост и статью на эти строки не рендерим: в архиве
    сотни выпусков, и это было бы дорого.
    """

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import api_digest

        self.tmp = tempfile.TemporaryDirectory()
        self.store = DigestStore(os.path.join(self.tmp.name, "dig.json"))
        self.now = 1_770_000_000.0
        for i, day in enumerate(("2026-09-18", "2026-09-17", "2026-08-30")):
            events = _events(self.now - i * 86400,
                             [("BTC_USDT", "binance", "SELL", 900_000 - i, 1)])
            facts = collect_day(events, now=self.now - i * 86400, oi={}, prices={})
            self.store.save({"id": day, "day": day, "facts": facts,
                             "ai": {"ru": "Рассказ дня. " * 30},
                             "created": self.now - i,
                             "published": {"ru": {"ok": True}}})
        api_digest.ctx.store = self.store
        api_digest.ctx.public_url = ""
        app = FastAPI()
        api_digest.register_digest_routes(app)
        self.client = TestClient(app)

    def tearDown(self):
        import api_digest
        api_digest.ctx.store = DigestStore("")

    def test_cover_route_serves_the_issue_photo(self):
        """Обложка выпуска отдаётся странице: то же фото, что ушло в канал."""
        import api_digest
        with tempfile.TemporaryDirectory() as tmp:
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
            path = os.path.join(tmp, "cover.png")
            with open(path, "wb") as fh:
                fh.write(png)
            rec = self.store.get("2026-09-18")
            rec["photo"] = {"path": path, "name": "cover.png", "source": "admin"}
            self.store.save(rec)
            res = self.client.get("/api/digest/cover?day=2026-09-18")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers["content-type"], "image/png")
            self.assertEqual(res.content, png)
            self.assertIn("max-age", res.headers.get("cache-control", ""))
            # без обложки — честный 404, а не пустая картинка
            self.assertEqual(self.client.get("/api/digest/cover?day=2026-09-17").status_code, 404)
            # без даты отдаём обложку свежего выпуска (так строит превью страница)
            self.assertEqual(self.client.get("/api/digest/cover").status_code, 200)
            self.assertIn("/api/digest/cover?day=2026-09-18",
                          self.client.get("/api/digest/today").json()["item"]["photo"]["url"])

    def test_digest_page_preview_uses_the_cover(self):
        """Превью ссылки на страницу выпуска — фото дня, без размеров общей обложки."""
        import api_digest
        api_digest.ctx.public_url = "https://liqscope.online"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cover.jpg")
            with open(path, "wb") as fh:
                fh.write(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
            rec = self.store.get("2026-09-18")
            rec["photo"] = {"path": path, "name": "cover.jpg", "source": "admin"}
            self.store.save(rec)
            page = self.client.get("/digest?day=2026-09-18").text
        self.assertIn('property="og:image" content="https://liqscope.online'
                      '/api/digest/cover?day=2026-09-18"', page)
        self.assertIn('name="twitter:image" content="https://liqscope.online'
                      '/api/digest/cover?day=2026-09-18"', page)
        self.assertNotIn("og:image:width", page,
                         "размеры общей обложки к фото дня не подходят")
        self.assertIn("https://liqscope.online/api/digest/cover?day=2026-09-18",
                      page, "картинка выпуска должна быть и в разметке Schema.org")

    def test_days_index_covers_every_issue(self):
        d = self.client.get("/api/digest?lang=ru").json()
        self.assertEqual(d["count"], 3)
        self.assertEqual([x["day"] for x in d["days"]],
                         ["2026-09-18", "2026-09-17", "2026-08-30"])
        self.assertTrue(all("label" in x and "published" in x for x in d["days"]))
        self.assertTrue(all("post" not in x and "article" not in x
                            for x in d["days"]), "индекс не должен тянуть посты")

    def test_fresh_list_is_short_but_days_are_whole(self):
        d = self.client.get("/api/digest?lang=ru&limit=2").json()
        self.assertEqual(len(d["items"]), 2)                # в ленте всего два
        self.assertEqual(len(d["days"]), 3)                 # а даты все
        self.assertEqual(d["keep"], DEFAULT_KEEP)

    def test_day_index_labels_follow_language(self):
        import api_digest
        rec = self.store.get("2026-09-18")
        self.assertEqual(api_digest.day_index(rec, "ru")["label"], "18 сентября")
        self.assertEqual(api_digest.day_index(rec, "en")["label"], "18 September")


class SettingsApiTest(unittest.TestCase):
    """Правки расписания с сайта: /api/digest/settings и приоритет окружения."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import api_digest
        import web_account
        from accounts import COOKIE_SID, Store

        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=["boss@liqscope.online"])
        admin = self.store.create_email_user("boss@liqscope.online",
                                             password_hash="x")["user"]
        self.admin_id = int(admin["id"])
        token = self.store.create_session(self.admin_id)
        plain = self.store.create_email_user("vasya@example.com",
                                             password_hash="x")["user"]
        plain_token = self.store.create_session(int(plain["id"]))

        web_account.ctx.store = self.store
        api_digest.ctx.store = DigestStore(os.path.join(self.tmp.name, "dig.json"))
        api_digest.ctx.public_url = ""
        api_digest.ctx.env_locked = {}
        api_digest.ctx.set_setting_fn = (
            lambda key, val, actor=None:
            self.store.set_setting(str(key), str(val), actor_id=actor))
        api_digest.ctx.settings_fn = lambda: {"hour": 22, "minute": 0,
                                              "jitter_min": 10, "enabled": True}

        app = FastAPI()
        self.sched = api_digest.DigestScheduler(hour=22, minute=0, jitter_min=10,
                                                tz=3 * 3600)
        app.state.digest_scheduler = self.sched
        api_digest.register_digest_routes(app)
        self.admin = TestClient(app)
        self.admin.cookies.set(COOKIE_SID, token)
        self.plain = TestClient(app)
        self.plain.cookies.set(COOKIE_SID, plain_token)

    def tearDown(self):
        import api_digest
        import web_account
        web_account.ctx.store = None
        api_digest.ctx.env_locked = {}
        api_digest.ctx.settings_fn = None
        api_digest.ctx.set_setting_fn = None
        api_digest.ctx.store = DigestStore("")
        self.tmp.cleanup()

    def test_saves_time_and_turns_auto_off(self):
        d = self.admin.post("/api/digest/settings",
                            json={"hour": 21, "minute": 45, "jitter_min": 5,
                                  "enabled": False}).json()
        self.assertTrue(d["ok"])
        self.assertEqual(self.store.get_setting("digest_hour"), "21")
        self.assertEqual(self.store.get_setting("digest_minute"), "45")
        self.assertEqual(self.store.get_setting("digest_jitter_min"), "5")
        self.assertEqual(self.store.get_setting("digest_enabled"), "0")
        # планировщик применил новое время сразу, без перезапуска
        self.assertEqual((self.sched.hour, self.sched.minute, self.sched.jitter_min),
                         (21, 45, 5))
        self.assertFalse(self.sched.enabled)
        self.assertEqual(d["schedule"]["hour"], 21)

    def test_clamps_and_rejects_garbage(self):
        d = self.admin.post("/api/digest/settings",
                            json={"hour": 99, "minute": -5, "jitter_min": 999}).json()
        self.assertTrue(d["ok"])
        self.assertEqual((self.sched.hour, self.sched.minute, self.sched.jitter_min),
                         (23, 0, 120))
        bad = self.admin.post("/api/digest/settings", json={"hour": "вечер"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["error"], "bad_value")

    def test_env_locked_keys_win(self):
        import api_digest
        api_digest.ctx.env_locked = {"hour": "LIQSCOPE_DIGEST_HOUR"}
        d = self.admin.post("/api/digest/settings",
                            json={"hour": 3, "minute": 15}).json()
        self.assertTrue(d["ok"])
        self.assertNotIn("hour", d["saved"])
        self.assertEqual(d["saved"]["minute"], 15)
        self.assertEqual(self.sched.hour, 22, "замороженный час не меняется")
        self.assertIn("LIQSCOPE_DIGEST_HOUR", d["note"])
        self.assertEqual(self.store.get_setting("digest_hour"), "")

    def test_only_admin_may_change(self):
        self.assertEqual(self.plain.post("/api/digest/settings",
                                         json={"hour": 5}).status_code, 403)

    def test_status_shows_locked_keys(self):
        import api_digest
        api_digest.ctx.env_locked = {"enabled": "LIQSCOPE_DIGEST_SCHED"}
        st = self.admin.get("/api/digest/status").json()
        self.assertEqual(st["schedule"]["env_locked"]["enabled"],
                         "LIQSCOPE_DIGEST_SCHED")


if __name__ == "__main__":
    unittest.main(verbosity=1)
