"""🌍 География посетителей: страна по IP, источник перехода, время на сайте.

Что проверяем:

* страна достаётся из заголовка CDN, иначе из кэша, иначе — у внешнего
  сервиса (в тестах он подменён: сеть в проверке не нужна);
* «неизвестно» тоже кэшируется, но недолго, чтобы сервис-молчун не глушил
  определение страны навсегда;
* чужой сервис не спрашивается больше одного раза на один адрес;
* источник перехода разбирается по реферату и метке ``utm_source``, а переход
  внутри сайта источником не считается;
* ``geo_stats`` собирает точки, страны, источники и длительность визита из
  визитов и «сердцебиений», не путая роботов с гостями;
* ручки: ``/api/visit/ping`` принимает только своих (по cookie) и не считает
  роботов, ``/api/admin/geo`` — только для админа.

Запуск:  python3 -m pytest tests/test_geoip.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import geoip  # noqa: E402
import web_geo  # noqa: E402
import web_account  # noqa: E402
from accounts import COOKIE_SID, COOKIE_VID, Store, hash_ip  # noqa: E402

ADMIN_EMAIL = "boss@liqscope.online"


class GeoStoreTest(unittest.TestCase):
    """Кэш стран, присутствие гостей и сводка по окну."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        self.user = self.store.create_email_user("vasya@example.com",
                                                 password_hash="x")["user"]

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def visit(self, vid: str, cc: str = "", path: str = "/terminal", *,
              kind: str = "direct", source: str = "", ago: float = 0.0,
              bot: bool = False, user_id=None) -> int:
        return self.store.record_visit(
            path, vid, user_id, hash_ip("s", vid or "x"), ua="Mozilla/5.0",
            bot=bot, country=cc, country_name=geoip.country_name(cc),
            country_src="edge" if cc else "", source=source, source_kind=kind)

    # --- кэш ---------------------------------------------------------------
    def test_country_cache_roundtrip(self) -> None:
        iph = hash_ip("s", "8.8.8.8")
        self.assertIsNone(self.store.geo_cached(iph, 3600))
        self.store.geo_remember(iph, "fi", "Finland", "country.is")
        row = self.store.geo_cached(iph, 3600)
        self.assertEqual(row["cc"], "FI")
        self.assertEqual(row["name"], "Finland")
        self.assertEqual(row["src"], "country.is")

    def test_country_cache_expires_and_remembers_unknown(self) -> None:
        iph = hash_ip("s", "1.1.1.1")
        self.store.geo_remember(iph, "", "", "miss")
        self.assertEqual(self.store.geo_cached(iph, 3600)["cc"], "",
                         "«неизвестно» тоже ответ — не спрашиваем повторно")
        self.assertIsNone(self.store.geo_cached(iph, 0.0),
                          "нулевой TTL — кэш не считается свежим")

    def test_visit_country_is_filled_after_the_fact(self) -> None:
        visit_id = self.visit("v1", "")
        self.store.set_visit_country(visit_id, "de", "Germany", "ipwho.is")
        stats = self.store.geo_stats(24)
        self.assertEqual(stats["points"][0]["country"], "DE")
        self.assertEqual(stats["totals"]["countries"], 1)

    # --- присутствие -------------------------------------------------------
    def test_presence_keeps_first_arrival_and_extends_time(self) -> None:
        first = self.store.touch_presence("v1", country="FI", country_name="Finland",
                                          source="google.com", source_kind="search",
                                          path="/terminal")
        self.assertEqual(first["country"], "FI")
        again = self.store.touch_presence("v2", country="DE")
        self.assertEqual(again["country"], "DE")
        # источник второго гостя не перебивает первый: у presence своя строка
        row = self.store.touch_presence("v1", country="SE", country_name="Sweden",
                                        path="/cabinet")
        self.assertEqual(row["country"], "FI", "страна первого захода важнее")
        self.assertEqual(row["path"], "/cabinet", "путь обновляется")
        self.assertGreaterEqual(row["sec"], 0.0)
        self.assertEqual(len(self.store.geo_online(300)), 2)

    def test_presence_country_filled_later(self) -> None:
        self.store.touch_presence("v1", path="/")
        self.store.set_presence_country("v1", "es", "Spain")
        self.assertEqual(self.store.geo_online(300)[0]["country"], "ES")
        self.store.set_presence_country("v1", "de")     # не перебиваем
        self.assertEqual(self.store.geo_online(300)[0]["country"], "ES")

    def test_online_window_filters_old_heartbeats(self) -> None:
        self.store.touch_presence("v1")
        self.assertEqual(len(self.store.geo_online(300)), 1)
        with self.store._lock:                       # гость замолчал 10 секунд назад
            self.store._db.execute("UPDATE presence SET ts=ts-10 WHERE vid='v1'")
            self.store._db.commit()
        self.assertEqual(len(self.store.geo_online(5)), 0, "окно в 5 секунд уже пусто")
        self.assertEqual(len(self.store.geo_online(30)), 1)

    # --- сводка ------------------------------------------------------------
    def test_stats_count_countries_sources_and_time(self) -> None:
        now = time.time()
        # финн из поиска: две страницы, пришёл 4 минуты назад (v1)
        self.visit("v1", "FI", "/", kind="search", source="google.com")
        self.visit("v2", "FI", "/terminal", kind="internal")
        with self.store._lock:                       # сдвигаем время визита назад
            self.store._db.execute("UPDATE visits SET ts=? WHERE vid='v1'",
                                   (now - 240.0,))
            self.store._db.commit()
        self.store.touch_presence("v1", country="FI", source="google.com",
                                  source_kind="search", path="/terminal")
        # немец напрямую, только зашёл
        self.visit("v3", "DE", "/", kind="direct")
        self.store.touch_presence("v3", country="DE", source_kind="direct", path="/")
        # робот и гость без cookie: в гости не идут, но видны в счётчиках
        self.visit("", "US", "/", bot=True)
        self.visit("", "US", "/")
        stats = self.store.geo_stats(24)
        self.assertEqual(stats["totals"]["visitors"], 3)
        self.assertEqual(stats["totals"]["views"], 3, "просмотры гостей")
        self.assertEqual(stats["totals"]["anon_views"], 1)
        self.assertEqual(stats["totals"]["bots"], 1)
        # онлайн считается по «сердцебиениям», а не по визитам: v2 пришёл
        # (визит есть), но признаков жизни не подавал
        self.assertEqual(stats["totals"]["online"], 2)
        self.assertEqual(stats["totals"]["long_60"], 1)
        by_cc = {c["country"]: c for c in stats["countries"]}
        self.assertEqual(by_cc["FI"]["visitors"], 2)
        self.assertEqual(by_cc["FI"]["views"], 2)
        self.assertEqual(by_cc["DE"]["visitors"], 1)
        self.assertEqual({c["country"] for c in stats["countries"]}, {"FI", "DE"},
                         "робот в US в страны не попадает")
        self.assertGreater(by_cc["FI"]["avg_sec"], 100.0,
                           "240 секунд у v1 в среднем с нулём у v2")
        srcs = {(s["source"], s["kind"]) for s in stats["sources"]}
        self.assertIn(("google.com", "search"), srcs)
        self.assertIn(("", "direct"), srcs)
        self.assertNotIn(("", "internal"), srcs, "переход внутри сайта — не источник")
        self.assertEqual(len(stats["long"]), 1)
        self.assertEqual(stats["long"][0]["vid"], "v1")

    def test_country_code_and_name_stay_together(self) -> None:
        """Код и имя страны — из одной строки визита, иначе «FI» подпишется Spain.

        Гость с одним vid успел зайти из разных стран (VPN, тест): страну
        берём у первого известного визита, и имя обязано быть оттуда же.
        """
        self.visit("v1", "FI")
        self.visit("v1", "DE")
        self.visit("v1", "ES")
        point = self.store.geo_stats(24)["points"][0]
        self.assertEqual(point["country"], "FI")
        self.assertEqual(point["country_name"], "Finland")
        country = self.store.geo_stats(24)["countries"][0]
        self.assertEqual((country["country"], country["name"]), ("FI", "Finland"))

    def test_stats_window_ignores_old_visits(self) -> None:
        self.visit("v1", "FI")
        with self.store._lock:
            self.store._db.execute("UPDATE visits SET ts=ts-100000 WHERE vid='v1'")
            self.store._db.commit()
        self.assertEqual(self.store.geo_stats(24)["totals"]["visitors"], 0)
        self.assertEqual(self.store.geo_stats(48)["totals"]["visitors"], 1)
        self.assertEqual(self.store.geo_stats(24)["totals"]["views"], 0)

    def test_stats_points_are_limited(self) -> None:
        for i in range(12):
            self.visit("v%d" % i, "FI")
        stats = self.store.geo_stats(24, dots=5)
        self.assertEqual(len(stats["points"]), 5)
        self.assertEqual(stats["totals"]["visitors"], 12)

    def test_online_guest_without_visits_is_still_shown(self) -> None:
        """Страница открылась, визит ещё пишется фоном — гость уже виден."""
        self.store.touch_presence("v9", country="FR", country_name="France",
                                  path="/terminal")
        stats = self.store.geo_stats(24)
        self.assertEqual(stats["totals"]["online"], 1)
        self.assertEqual(stats["points"][0]["country"], "FR")
        self.assertEqual(stats["points"][0]["views"], 0)


class ClassifyTest(unittest.TestCase):
    """Источник перехода: поиск, соцсети, свои страницы, метки кампаний."""

    def test_search_and_social(self) -> None:
        self.assertEqual(geoip.classify("https://www.google.com/search?q=liq"),
                         ("google.com", "search"))
        self.assertEqual(geoip.classify("https://yandex.ru/search/"),
                         ("yandex.ru", "search"))
        self.assertEqual(geoip.classify("https://t.me/liqscopebot"),
                         ("t.me", "social"))
        self.assertEqual(geoip.classify("https://vk.com/wall-1"),
                         ("vk.com", "social"))

    def test_referral_and_direct(self) -> None:
        self.assertEqual(geoip.classify("https://example.org/post"),
                         ("example.org", "referral"))
        self.assertEqual(geoip.classify(""), ("", "direct"))
        self.assertEqual(geoip.classify("не ссылка"), ("", "direct"))

    def test_own_pages_are_not_a_source(self) -> None:
        src, kind = geoip.classify("https://liqscope.online/terminal",
                                   host="liqscope.online",
                                   site_hosts=("https://liqscope.online",))
        self.assertEqual((src, kind), ("", "internal"))

    def test_campaign_wins_over_referer(self) -> None:
        self.assertEqual(geoip.classify("https://t.me/x", utm="Newsletter"),
                         ("newsletter", "campaign"))

    def test_country_headers(self) -> None:
        self.assertEqual(geoip.header_country({"CF-IPCountry": "fi"}), "FI")
        self.assertEqual(geoip.header_country({"x-country-code": "DE"}), "DE")
        self.assertEqual(geoip.header_country({"cf-ipcountry": "T1"}), "",
                         "Tor — не страна")
        self.assertEqual(geoip.header_country({"cf-ipcountry": "XX"}), "")
        self.assertEqual(geoip.header_country({}), "")

    def test_app_and_direct(self) -> None:
        meta = geoip.visit_meta(_Req(headers={"host": "liqscope.online",
                                              "user-agent": "Mozilla/5.0 (Linux) "
                                                            "Telegram/10.2"}))
        self.assertEqual(meta["source_kind"], "app")

    def test_visit_meta_reads_utm_from_query(self) -> None:
        meta = geoip.visit_meta(_Req(headers={"referer": "https://google.com/"},
                                     query={"utm_source": "mail"}))
        self.assertEqual((meta["source"], meta["source_kind"]), ("mail", "campaign"))


class _Req:
    """Мини-запрос: только то, что читает geoip.visit_meta."""

    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


class ProviderTest(unittest.TestCase):
    """Внешние сервисы: порядок, отказы, кэш — сеть подменена."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        geoip.ctx.store = self.store
        geoip.ctx.secret = "s"
        self.calls: list = []
        self.answer: dict = {}
        self._orig = geoip._fetch_json

        async def fake(url):
            self.calls.append(url)
            for host, payload in self.answer.items():
                if host in url:
                    return payload
            return None

        geoip._fetch_json = fake

    def tearDown(self) -> None:
        geoip._fetch_json = self._orig
        geoip.ctx.store = None
        self.store.close()
        self.tmp.cleanup()

    def run_async(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_first_provider_wins(self) -> None:
        self.answer = {"country.is": {"country": "nl"},
                       "ipwho.is": {"country_code": "DE"}}
        got = self.run_async(geoip.resolve("8.8.8.8"))
        self.assertEqual(got["cc"], "NL")
        self.assertEqual(got["src"], "country.is")
        self.assertEqual(len(self.calls), 1, "второй сервис не нужен")

    def test_falls_through_to_next_provider(self) -> None:
        self.answer = {"ipapi.co": {"country_code": "es"}}
        got = self.run_async(geoip.resolve("8.8.8.8"))
        self.assertEqual(got["cc"], "ES")
        self.assertEqual(got["src"], "ipapi.co")
        self.assertEqual(len(self.calls), 3, "первые два промолчали")

    def test_nothing_answered(self) -> None:
        self.assertEqual(self.run_async(geoip.resolve("8.8.8.8")), {})

    def test_private_address_is_not_asked(self) -> None:
        got = self.run_async(geoip.resolve("127.0.0.1"))
        self.assertEqual(got, {})
        self.assertEqual(self.calls, [])

    def test_lookup_caches_the_answer(self) -> None:
        self.answer = {"country.is": {"country": "fi"}}
        first = self.run_async(geoip.lookup("8.8.8.8"))
        self.assertEqual((first["cc"], first["src"]), ("FI", "country.is"))
        calls = len(self.calls)
        second = self.run_async(geoip.lookup("8.8.8.8"))
        self.assertEqual(second["cc"], "FI")
        self.assertEqual(second["src"], "country.is")
        self.assertEqual(len(self.calls), calls, "второй раз — из кэша")
        self.assertTrue(second.get("cached"))

    def test_empty_answer_is_remembered_as_unknown(self) -> None:
        second = self.run_async(geoip.lookup("9.9.9.9"))
        self.assertEqual(second["cc"], "")
        self.assertEqual(len(self.calls), 4, "спросили все сервисы")
        again = self.run_async(geoip.lookup("9.9.9.9"))
        self.assertEqual(again["cc"], "")
        self.assertEqual(len(self.calls), 4, "«неизвестно» держим в кэше")

    def test_off_switch_disables_network(self) -> None:
        os.environ["LIQSCOPE_GEOIP"] = "0"
        try:
            self.assertEqual(self.run_async(geoip.resolve("8.8.8.8")), {})
            self.assertEqual(self.calls, [])
        finally:
            os.environ.pop("LIQSCOPE_GEOIP", None)

    def test_custom_provider_goes_first(self) -> None:
        os.environ["LIQSCOPE_GEOIP_URL"] = "http://127.0.0.1:9/geo/{ip}"
        self.answer = {"127.0.0.1:9": {"country_code": "lv"}}
        try:
            got = self.run_async(geoip.resolve("8.8.8.8"))
            self.assertEqual(got["src"], "custom")
            self.assertEqual(got["cc"], "LV")
        finally:
            os.environ.pop("LIQSCOPE_GEOIP_URL", None)

    def test_attach_country_fills_visit_and_presence(self) -> None:
        self.answer = {"country.is": {"country": "se"}}
        visit_id = self.store.record_visit("/", "v1", None, hash_ip("s", "8.8.8.8"))
        self.store.touch_presence("v1", path="/")
        self.run_async(geoip.attach_country("8.8.8.8", hash_ip("s", "8.8.8.8"),
                                            visit_id, "v1"))
        stats = self.store.geo_stats(1)
        self.assertEqual(stats["points"][0]["country"], "SE")
        self.assertEqual(self.store.geo_online(300)[0]["country"], "SE")


class GeoRoutesTest(unittest.TestCase):
    """Ручки: сердцебиение гостя и картина для админа."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=[ADMIN_EMAIL])
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        geoip.ctx.store = self.store
        geoip.ctx.secret = "s"
        web_geo.ctx.store = self.store
        web_geo.ctx.secret = "s"
        web_geo.ctx.public_url = "https://liqscope.online"
        web_geo.limiter.hits.clear()
        self.admin_id = int(self.store.create_email_user(
            ADMIN_EMAIL, password_hash="x")["user"]["id"])
        self.user_id = int(self.store.create_email_user(
            "vasya@example.com", password_hash="x")["user"]["id"])
        self.app = FastAPI()
        web_geo.register_geo_routes(self.app)
        self.guest = TestClient(self.app)
        self.guest.cookies.set(COOKIE_VID, "g1")
        self.user = TestClient(self.app)
        self.user.cookies.set(COOKIE_VID, "u1")
        self.user.cookies.set(COOKIE_SID, self.store.create_session(self.user_id))
        self.admin = TestClient(self.app)
        self.admin.cookies.set(COOKIE_VID, "a1")
        self.admin.cookies.set(COOKIE_SID, self.store.create_session(self.admin_id))

    def tearDown(self) -> None:
        web_account.ctx.store = None
        geoip.ctx.store = None
        web_geo.ctx.store = None
        web_geo.limiter.hits.clear()
        self.store.close()
        self.tmp.cleanup()

    def test_ping_without_cookie_is_not_counted(self) -> None:
        r = TestClient(self.app).post("/api/visit/ping", json={"path": "/"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["counted"])
        self.assertEqual(self.store.geo_online(300), [])

    def test_ping_counts_guest_and_returns_country(self) -> None:
        r = self.guest.post("/api/visit/ping", json={"path": "/terminal"},
                            headers={"CF-IPCountry": "fi",
                                     "referer": "https://google.com/"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["counted"])
        self.assertEqual(body["cc"], "FI")
        self.assertEqual(body["lat"] is not None and body["lon"] is not None, True)
        self.assertEqual(body["online"], 1)
        rows = self.store.geo_online(300)
        self.assertEqual(rows[0]["vid"], "g1")
        self.assertEqual(rows[0]["path"], "/terminal")
        self.assertEqual(rows[0]["source_kind"], "search")
        self.assertEqual(rows[0]["source"], "google.com")

    def test_ping_marks_logged_in_guest(self) -> None:
        self.user.post("/api/visit/ping", json={"path": "/cabinet"})
        self.assertEqual(self.store.geo_online(300)[0]["user_id"], self.user_id)

    def test_ping_is_rate_limited(self) -> None:
        web_geo.limiter.limit = 2
        try:
            codes = [self.guest.post("/api/visit/ping", json={"path": "/"}).status_code
                     for _ in range(3)]
        finally:
            web_geo.limiter.limit = web_geo.RATE_LIMIT
        self.assertEqual(codes, [200, 200, 429])

    def test_admin_geo_requires_admin(self) -> None:
        self.assertEqual(self.guest.get("/api/admin/geo").status_code, 401)
        self.assertEqual(self.user.get("/api/admin/geo").status_code, 403)

    def test_admin_geo_returns_points_and_countries(self) -> None:
        self.guest.post("/api/visit/ping", json={"path": "/"},
                        headers={"CF-IPCountry": "de"})
        self.store.record_visit("/", "u1", self.user_id, hash_ip("s", "u1"),
                                country="ES", country_name="Spain",
                                source="t.me", source_kind="social")
        r = self.admin.get("/api/admin/geo?period=24h")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["period"], "24h")
        self.assertEqual(body["totals"]["online"], 1)
        ccs = {c["country"]: c for c in body["countries"]}
        self.assertEqual(set(ccs), {"DE", "ES"})
        self.assertEqual(ccs["ES"]["lat"] is not None, True)
        self.assertEqual(ccs["ES"]["lon"] is not None, True)
        live = body["online"][0]
        self.assertEqual(live["country"], "DE")
        self.assertTrue(live["lat"] is not None)
        srcs = {(s["source"], s["kind"]) for s in body["sources"]}
        self.assertIn(("t.me", "social"), srcs)
        self.assertIn("edge", str(body["geo"]))

    def test_period_switches_window_and_bad_value_is_ignored(self) -> None:
        self.store.record_visit("/", "old", None, hash_ip("s", "old"),
                                country="FR", country_name="France")
        with self.store._lock:
            self.store._db.execute("UPDATE visits SET ts=ts-3*86400 WHERE vid='old'")
            self.store._db.commit()
        self.assertEqual(self.admin.get("/api/admin/geo?period=24h")
                         .json()["totals"]["visitors"], 0)
        self.assertEqual(self.admin.get("/api/admin/geo?period=7d")
                         .json()["totals"]["visitors"], 1)
        self.assertEqual(self.admin.get("/api/admin/geo?period=чепуха")
                         .json()["period"], web_geo.DEFAULT_PERIOD)

    def test_period_list_is_advertised(self) -> None:
        body = self.admin.get("/api/admin/geo").json()
        self.assertEqual(sorted(body["periods"]), ["24h", "30d", "7d"])
        self.assertGreater(body["online_sec"], 0)


if __name__ == "__main__":
    unittest.main()
