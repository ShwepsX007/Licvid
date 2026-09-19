"""Сводки по часам: архив постов канала и раздел сайта.

Проверяем то, что легко сломать незаметно: разбор подписи канала в безопасный
HTML (её присылает Telegram-бот, а не человек), выбор языка поста, индекс дней
для календаря, отдачу фото и то, что страница раздела строится на том же
материале, что ушёл в канал.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import hourly_posts  # noqa: E402
from hourly_posts import (  # noqa: E402
    DEFAULT_KEEP, PostStore, day_index, photo_url, pick_text, plain_text,
    post_id, public_post, tg_html, texts_of,
)

NOW = 1_770_000_000.0            # 2026-02-01 ~ (фиксированное время для тестов)

RU_POST = ("<b>🌙 Ночная смена на ленте</b>\n\n"
           "💥 <b>$215.1K</b> · 7 ликвидаций\n\n"
           "🌐 <a href=\"https://liqscope.online/hourly?a=1&amp;b=2\">liqscope.online</a>")
EN_POST = ("<b>🌙 Night shift on the tape</b>\n\n"
           "💥 <b>$215.1K</b> · 7 fills\n\n"
           "🌐 <a href=\"https://liqscope.online/hourly\">liqscope.online</a>")


def make_rec(ts: float = NOW, rid: str = "", texts=None, day: str = "") -> dict:
    rid = rid or post_id(ts)
    return {
        "id": rid, "ts": ts, "day": day or time.strftime(
            "%Y-%m-%d", time.gmtime(ts + hourly_posts.tz_seconds())),
        "window_h": 4, "interval_h": 4, "total_usd": 1234567.0, "liq_count": 42,
        "texts": texts if texts is not None else {"ru": RU_POST, "en": EN_POST},
        "sent": {"ru": True, "en": True},
    }


class TgHtmlTest(unittest.TestCase):
    """Подпись канала → безопасный HTML страницы."""

    def test_keeps_channel_markup(self):
        out = tg_html(RU_POST)
        self.assertIn("<b>", out)
        self.assertIn("</b>", out)
        self.assertIn("💥", out)

    def test_escape_keeps_ampersands_readable(self):
        """В канале текст уже экранирован: на сайте он не должен стать «&amp;amp;»."""
        out = tg_html("R&amp;D · <b>итог</b>")
        self.assertIn("R&amp;D", out)
        self.assertNotIn("&amp;amp;", out)

    def test_link_gets_safe_attributes(self):
        out = tg_html('<a href="https://x.io/a?b=1&amp;c=2">сайт</a>')
        self.assertIn('<a href="https://x.io/a?b=1&amp;c=2"', out)
        self.assertIn('rel="noopener nofollow"', out)
        self.assertIn('target="_blank"', out)

    def test_scripts_and_unknown_tags_are_escaped(self):
        out = tg_html('<script>alert(1)</script> <img src=x onerror=alert(1)>')
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertIn("&lt;script&gt;", out)

    def test_unpaired_link_end_is_left_as_text(self):
        """Висячий </a> не ломает разметку страницы."""
        out = tg_html("просто текст </a> и ещё")
        self.assertNotIn("</a>", out)
        self.assertIn("&lt;/a&gt;", out)

    def test_br_and_code_survive(self):
        out = tg_html("<code>OI</code><br>дальше")
        self.assertIn("<code>OI</code>", out)
        self.assertIn("<br>", out)


class PlainTextTest(unittest.TestCase):
    def test_strips_markup_and_squeezes_blank_lines(self):
        out = plain_text("<b>Итог</b> <a href=\"https://x.io\">сайт</a>\n\n\n$1M")
        self.assertEqual(out, "Итог сайт\n\n$1M")

    def test_unescapes_entities(self):
        self.assertIn("R&D", plain_text("R&amp;D"))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "posts.json")
        self.store = PostStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_keeps_posts_newest_first_and_survives_reload(self):
        for i in range(3):
            self.store.add(make_rec(NOW - i * 4 * 3600))
        ids = [r["id"] for r in self.store.list()]
        self.assertEqual(ids, sorted(ids, reverse=True))
        again = PostStore(self.path)
        self.assertEqual([r["id"] for r in again.list()], ids)

    def test_same_id_replaces_instead_of_duplicating(self):
        rec = make_rec(NOW)
        self.store.add(rec)
        rec["texts"] = {"ru": "обновлено"}
        self.store.add(rec)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.list()[0]["texts"]["ru"], "обновлено")

    def test_keep_limits_the_archive(self):
        store = PostStore(self.path, keep=3)
        for i in range(6):
            store.add(make_rec(NOW - i * 3600))
        self.assertEqual(len(store.list()), 3)

    def test_day_filter_and_index(self):
        """Индекс дней — то, по чему рисуется календарь: дата, число постов."""
        day1, day2 = "2026-02-01", "2026-01-31"
        self.store.add(make_rec(NOW, day=day1, texts={"ru": "раз"}))
        self.store.add(make_rec(NOW - 3600, day=day1, texts={"ru": "два"}))
        self.store.add(make_rec(NOW - 86400, day=day2, texts={"ru": "три"}))
        self.assertEqual(len(self.store.by_day(day1)), 2)
        self.assertEqual(len(self.store.by_day(day2)), 1)
        days = self.store.days()
        self.assertEqual([d["day"] for d in days], [day1, day2])
        self.assertEqual(days[0]["n"], 2)
        self.assertAlmostEqual(days[0]["total_usd"], 1234567.0 * 2, places=1)
        self.assertEqual(self.store.stats()["count"], 3)

    def test_broken_file_does_not_break_the_archive(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{не json")
        store = PostStore(self.path)
        self.assertEqual(store.items, [])
        self.assertTrue(store.error)

    def test_post_id_has_day_and_time(self):
        rid = post_id(NOW)
        self.assertRegex(rid, r"^\d{4}-\d{2}-\d{2}-\d{4}$")
        self.assertEqual(len(rid), len("2026-02-01-0000"))


class LanguageTest(unittest.TestCase):
    """Тексты поста: русский для ru, английский (канал-источник) для остальных."""

    def test_ru_site_gets_russian_text(self):
        self.assertIn("Ночная смена", pick_text(make_rec(), "ru"))

    def test_other_languages_get_english_text(self):
        for lang in ("en", "zh", "hi", "es"):
            self.assertIn("Night shift", pick_text(make_rec(), lang), lang)

    def test_falls_back_to_what_exists(self):
        only_en = make_rec(texts={"en": EN_POST})
        self.assertIn("Night shift", pick_text(only_en, "ru"))
        only_ru = make_rec(texts={"ru": RU_POST})
        self.assertIn("Ночная смена", pick_text(only_ru, "es"))

    def test_texts_of_normalises_language_keys(self):
        rec = make_rec(texts={"ru-RU": RU_POST, "EN": EN_POST, "de": "нет"})
        self.assertEqual(sorted(texts_of(rec).keys()), ["en", "ru"])

    def test_public_post_carries_both_versions(self):
        out = public_post(make_rec(), "ru")
        self.assertEqual(out["langs"], ["ru", "en"])
        self.assertIn("Ночная смена", out["html"])
        self.assertIn("Night shift", out["texts"]["en"])
        self.assertEqual(out["id"], post_id(NOW))

    def test_public_post_without_text_for_calendar(self):
        """Для превью ссылки текст не нужен — и не должен тянуться."""
        out = public_post(make_rec(), "ru", with_text=False)
        self.assertNotIn("html", out)
        self.assertNotIn("text", out)


class PublicPhotoTest(unittest.TestCase):
    def test_photo_has_url_only_when_the_file_exists(self):
        rec = make_rec()
        self.assertNotIn("photo", public_post(rec, "ru"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cover.jpg")
            with open(path, "wb") as fh:
                fh.write(b"\xff\xd8\xff")
            rec["photo"] = {"path": path, "name": "cover.jpg", "source": "admin"}
            out = public_post(rec, "ru")
            self.assertEqual(out["photo"]["url"], photo_url(rec["id"]))
            self.assertEqual(out["photo"]["name"], "cover.jpg")

    def test_day_index_row(self):
        row = day_index({"day": "2026-02-01", "n": 4, "total_usd": 5000}, "ru")
        self.assertEqual(row["n"], 4)
        self.assertEqual(row["day"], "2026-02-01")
        self.assertTrue(row["label"])


class HourlyApiTest(unittest.TestCase):
    """Раздел отдаёт то же, что ушло в канал: посты, дни и фото."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import api_hourly

        self.tmp = tempfile.TemporaryDirectory()
        self.media = tempfile.TemporaryDirectory()
        self.store = PostStore(os.path.join(self.tmp.name, "posts.json"))
        self.day1, self.day2 = "2026-02-01", "2026-01-31"
        cover = os.path.join(self.media.name, "cover.png")
        with open(cover, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        rec = make_rec(NOW, day=self.day1)
        rec["photo"] = {"path": cover, "name": "cover.png", "source": "admin"}
        self.store.add(rec)
        self.store.add(make_rec(NOW - 4 * 3600, day=self.day1, texts={"ru": "второй"}))
        self.store.add(make_rec(NOW - 86400, day=self.day2, texts={"ru": "вчера"}))

        api_hourly.ctx.store = self.store
        api_hourly.ctx.public_url = "https://liqscope.online"
        api_hourly.ctx.page_ok = True
        api_hourly.ctx.channel_url_fn = lambda: "https://t.me/+test"
        app = FastAPI()
        api_hourly.register_hourly_routes(app)
        self.client = TestClient(app)
        self.api = api_hourly

    def tearDown(self):
        self.api.ctx.store = PostStore("")
        self.api.ctx.collect_fn = None
        self.api.ctx.channel_url_fn = None
        self.tmp.cleanup()
        self.media.cleanup()

    def test_list_returns_fresh_posts_and_day_index(self):
        res = self.client.get("/api/hourly").json()
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 3)
        self.assertEqual([d["day"] for d in res["days"]], [self.day1, self.day2])
        self.assertEqual(res["days"][0]["n"], 2)
        self.assertEqual(res["tz_hours"], 3.0)
        self.assertEqual(res["channel_url"], "https://t.me/+test")
        self.assertEqual(res["interval_h"], 4)
        self.assertEqual(res["items"][0]["id"], post_id(NOW))

    def test_day_filter_returns_only_that_day(self):
        res = self.client.get("/api/hourly?day=" + self.day2).json()
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["day"], self.day2)

    def test_lang_switches_the_post_text(self):
        ru = self.client.get("/api/hourly?lang=ru").json()["items"][0]
        en = self.client.get("/api/hourly?lang=de").json()["items"][0]
        self.assertIn("Ночная смена", ru["html"])
        self.assertIn("Night shift", en["html"])

    def test_photo_route_serves_the_channel_photo(self):
        pid = post_id(NOW)
        res = self.client.get("/api/hourly/photo/" + pid)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/png")
        self.assertIn("max-age", res.headers.get("cache-control", ""))
        # у поста без фото — честный 404, а не пустая картинка
        other = self.client.get("/api/hourly").json()["items"][1]["id"]
        self.assertEqual(self.client.get("/api/hourly/photo/" + other).status_code, 404)

    def test_page_is_served_with_the_latest_photo_as_preview(self):
        res = self.client.get("/hourly")
        self.assertEqual(res.status_code, 200)
        html = res.text
        self.assertIn("https://liqscope.online/api/hourly/photo/" + post_id(NOW), html)
        self.assertIn('"@type": "CollectionPage"', html)
        # размеры общей обложки сняты: у фото дня они свои
        self.assertNotIn('property="og:image:width"', html)

    def test_status_reports_the_archive(self):
        res = self.client.get("/api/hourly/status").json()
        self.assertTrue(res["ok"])
        self.assertEqual(res["stats"]["count"], 3)
        self.assertEqual(res["store_error"], "")

    def test_collect_requires_an_admin(self):
        self.assertEqual(self.client.post("/api/hourly/collect").status_code, 401)
        self.api.ctx.collect_fn = lambda: make_rec(NOW + 10, day=self.day1)
        res = self.client.post("/api/hourly/collect")
        # без сессии админа — по-прежнему отказ, архив не пополняется
        self.assertEqual(res.status_code, 401)
        self.assertEqual(len(self.store.list()), 3)

    def test_keep_is_reported(self):
        res = self.client.get("/api/hourly").json()
        self.assertEqual(res["keep"], DEFAULT_KEEP)


class SitemapTest(unittest.TestCase):
    def test_hourly_is_in_the_sitemap(self):
        import seo_pages
        body = seo_pages.sitemap_xml().body.decode("utf-8")
        self.assertIn("https://liqscope.online/hourly", body)

    def test_jsonld_kind_hourly(self):
        import seo_pages
        block = seo_pages.jsonld("hourly", "ru", image="https://x/y.png")
        self.assertIn("CollectionPage", block)
        self.assertIn("https://x/y.png", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
