"""🗺 Точки карты посещений: уборка старых и пробник слоёв у живого гостя.

Карта в админке копит точки за выбранный период. За 30 дней они превращаются
в кашу, и по карте уже не видно, кто приходит сейчас, поэтому рядом с картой
есть кнопка «убрать старые точки»: она ставит отметку времени (настройка
``geo_dots_after``), и карта показывает только гостей после неё.

Проверяем:

* отметку ставит и снимает только админ (``/api/admin/geo/dots/clear`` и
  ``/api/admin/geo/dots/reset``), гостю и обычному пользователю — нет;
* ``GET /api/admin/geo`` прячет точки до отметки, но статистику, страны и
  источники оставляет целыми: уборка не стирает данные и обратима;
* ``keep_sec`` оставляет на карте тех, кто на сайте прямо сейчас;
* живому гостю в карточке видно остаток пробного доступа к слоям, а кнопка
  «дать ещё» сбрасывает его таймер (``/api/admin/layers/reset``).

Запуск:  python3 -m pytest tests/test_geo_dots.py -q
"""

from __future__ import annotations

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
import web_account  # noqa: E402
import web_geo  # noqa: E402
import web_layers  # noqa: E402
from accounts import COOKIE_SID, COOKIE_VID, Store  # noqa: E402

ADMIN_EMAIL = "boss@liqscope.online"


class GeoDotsTest(unittest.TestCase):
    """Уборка точек карты и пробник слоёв у гостей в админской картине."""

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
        web_layers.ctx.store = self.store
        web_layers.ctx.secret = "s"
        self.admin_id = int(self.store.create_email_user(
            ADMIN_EMAIL, password_hash="x")["user"]["id"])
        self.user_id = int(self.store.create_email_user(
            "vasya@example.com", password_hash="x")["user"]["id"])
        self.app = FastAPI()
        web_geo.register_geo_routes(self.app)
        web_layers.register_layer_routes(self.app)
        self.guest = TestClient(self.app)
        self.guest.cookies.set(COOKIE_VID, "g1")
        self.user = TestClient(self.app)
        self.user.cookies.set(COOKIE_VID, "u1")
        self.user.cookies.set(COOKIE_SID, self.store.create_session(self.user_id))
        self.admin = TestClient(self.app)
        self.admin.cookies.set(COOKIE_VID, "a1")
        self.admin.cookies.set(COOKIE_SID, self.store.create_session(self.admin_id))
        self._env = os.environ.pop("LIQSCOPE_LAYERS_TRIAL_MIN", None)

    def tearDown(self) -> None:
        web_account.ctx.store = None
        geoip.ctx.store = None
        web_geo.ctx.store = None
        web_layers.ctx.store = None
        web_geo.limiter.hits.clear()
        if self._env is not None:
            os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = self._env
        else:
            os.environ.pop("LIQSCOPE_LAYERS_TRIAL_MIN", None)
        self.store.close()
        self.tmp.cleanup()

    # --- данные для карты ---------------------------------------------------
    def _seed(self) -> None:
        """Два гостя: свежий (он же онлайн) и старый — полсуток назад."""
        self.guest.post("/api/visit/ping", json={"path": "/terminal"},
                        headers={"CF-IPCountry": "de"})
        self.store.record_visit("/", "old1", None, geoip.hash_ip("s", "old1"),
                                country="ES", country_name="Spain",
                                source="t.me", source_kind="social")
        with self.store._lock:
            self.store._db.execute("UPDATE visits SET ts=ts-43200 WHERE vid='old1'")
            self.store._db.commit()

    # --- уборка точек -------------------------------------------------------
    def test_clear_and_reset_require_admin(self) -> None:
        self.assertEqual(self.guest.post("/api/admin/geo/dots/clear").status_code, 401)
        self.assertEqual(self.user.post("/api/admin/geo/dots/clear").status_code, 403)
        self.assertEqual(self.guest.post("/api/admin/geo/dots/reset").status_code, 401)
        self.assertEqual(self.user.post("/api/admin/geo/dots/reset").status_code, 403)

    def test_clear_hides_old_dots_and_keeps_the_statistics(self) -> None:
        self._seed()
        before = self.admin.get("/api/admin/geo?period=24h").json()
        self.assertEqual(before["dots_after"], 0.0)
        self.assertEqual(len(before["points"]), 2)
        self.assertEqual(before["dots_hidden"], 0)

        r = self.admin.post("/api/admin/geo/dots/clear")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertGreater(r.json()["dots_after"], 0)

        after = self.admin.get("/api/admin/geo?period=24h").json()
        self.assertEqual(len(after["points"]), 1, "старая точка спрятана")
        self.assertEqual(after["dots_hidden"], 1)
        self.assertEqual([p["vid"] for p in after["points"]], ["g1"])
        # статистика не тронута: страны, визиты и источники считаются как раньше
        self.assertEqual(after["totals"]["visitors"], before["totals"]["visitors"])
        self.assertEqual(set(c["country"] for c in after["countries"]),
                         set(c["country"] for c in before["countries"]))
        self.assertEqual({(s["source"], s["kind"]) for s in after["sources"]},
                         {(s["source"], s["kind"]) for s in before["sources"]})

    def test_reset_brings_every_dot_back(self) -> None:
        self._seed()
        self.admin.post("/api/admin/geo/dots/clear")
        self.assertEqual(len(self.admin.get("/api/admin/geo").json()["points"]), 1)
        r = self.admin.post("/api/admin/geo/dots/reset")
        self.assertTrue(r.json()["ok"])
        body = self.admin.get("/api/admin/geo").json()
        self.assertEqual(body["dots_after"], 0.0)
        self.assertEqual(len(body["points"]), 2)
        self.assertEqual(body["dots_hidden"], 0)

    def test_keep_sec_leaves_the_dots_of_those_online(self) -> None:
        """Кнопка по умолчанию оставляет на карте тех, кто на сайте сейчас."""
        self._seed()
        # гость без отметки времени: точка есть, но он уже не онлайн
        self.store.record_visit("/", "gone1", None, geoip.hash_ip("s", "gone1"),
                                country="FR", country_name="France")
        with self.store._lock:
            self.store._db.execute("UPDATE visits SET ts=ts-1800 WHERE vid='gone1'")
            self.store._db.commit()
        self.assertEqual(len(self.admin.get("/api/admin/geo").json()["points"]), 3)
        self.admin.post("/api/admin/geo/dots/clear", json={"keep_sec": 300})
        left = {p["vid"] for p in self.admin.get("/api/admin/geo").json()["points"]}
        self.assertEqual(left, {"g1"}, "остался только гость, который на сайте")
        # с запасом в час на карте оказываются оба недавних гостя, кроме старого
        self.admin.post("/api/admin/geo/dots/clear", json={"keep_sec": 3600})
        left = {p["vid"] for p in self.admin.get("/api/admin/geo").json()["points"]}
        self.assertEqual(left, {"g1", "gone1"})

    def test_clear_is_written_to_the_journal(self) -> None:
        self._seed()
        self.admin.post("/api/admin/geo/dots/clear")
        actions = [row["action"] for row in self.store.recent_audit(20)]
        self.assertIn("geo_dots_clear", actions)
        self.admin.post("/api/admin/geo/dots/reset")
        actions = [row["action"] for row in self.store.recent_audit(20)]
        self.assertIn("geo_dots_reset", actions)

    # --- пробник слоёв у живых гостей ---------------------------------------
    def test_online_guest_shows_the_trial_left_and_the_limit(self) -> None:
        self.guest.post("/api/visit/ping", json={"path": "/terminal"},
                        headers={"CF-IPCountry": "de"})
        self.guest.get("/api/layers/trial")            # гость открыл терминал
        body = self.admin.get("/api/admin/geo?period=24h").json()
        self.assertEqual(body["layers"]["minutes"], 30)
        self.assertTrue(body["layers"]["enabled"])
        live = body["online"][0]
        self.assertEqual(live["trial"]["who"], "v:g1")
        self.assertAlmostEqual(live["trial"]["left_sec"], 1800, delta=5)
        self.assertFalse(live["trial"]["expired"])
        self.assertEqual(live["trial"]["minutes"], 30)

    def test_guest_who_never_opened_the_terminal_has_no_trial(self) -> None:
        self.guest.post("/api/visit/ping", json={"path": "/"}, headers={})
        body = self.admin.get("/api/admin/geo?period=24h").json()
        self.assertNotIn("trial", body["online"][0])

    def test_reset_gives_the_guest_a_full_trial_again(self) -> None:
        """Кнопка «дать ещё»: таймер гостя начинается заново, лимит не меняем."""
        self.guest.get("/api/layers/trial")
        with self.store._lock:                        # гость пришёл 25 минут назад
            self.store._db.execute("UPDATE layer_trials SET started_at=started_at-1500")
            self.store._db.commit()
        left = self.guest.get("/api/layers/trial").json()["left_sec"]
        self.assertLess(left, 400)
        r = self.admin.post("/api/admin/layers/reset", json={"who": "v:g1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["reset"], 1)
        again = self.guest.get("/api/layers/trial").json()
        self.assertAlmostEqual(again["left_sec"], 1800, delta=5)
        self.assertEqual(again["hits"], 1, "строка гостя начата заново")

    def test_reset_all_restarts_every_timer(self) -> None:
        self.guest.get("/api/layers/trial")
        other = TestClient(self.app)
        other.cookies.set(COOKIE_VID, "g2")
        other.get("/api/layers/trial")
        self.assertEqual(self.store.layer_trial_stats()["total"], 2)
        r = self.admin.post("/api/admin/layers/reset", json={"all": True})
        self.assertEqual(r.json()["reset"], 2)
        self.assertEqual(self.store.layer_trial_stats()["total"], 0)

    def test_reset_needs_a_store_and_writes_the_journal(self) -> None:
        self.guest.get("/api/layers/trial")
        self.admin.post("/api/admin/layers/reset", json={"who": "v:g1"})
        self.assertIn("layers_reset",
                      [row["action"] for row in self.store.recent_audit(20)])
        web_layers.ctx.store = None
        self.assertEqual(self.admin.post("/api/admin/layers/reset").status_code, 503)

    def test_signed_in_guest_has_no_trial_left(self) -> None:
        """У вошедшего в кабинет слои без ограничений — в карточке прочерк."""
        self.user.post("/api/visit/ping", json={"path": "/cabinet"})
        body = self.admin.get("/api/admin/geo?period=24h").json()
        live = body["online"][0]
        self.assertNotIn("trial", live)

    def test_trial_is_hidden_when_the_limit_is_off(self) -> None:
        self.guest.post("/api/visit/ping", json={"path": "/terminal"})
        self.guest.get("/api/layers/trial")
        self.store.set_setting("layers_trial_min", "0")
        body = self.admin.get("/api/admin/geo?period=24h").json()
        self.assertFalse(body["layers"]["enabled"])
        self.assertNotIn("trial", body["online"][0])

    def test_expired_trial_is_marked(self) -> None:
        self.guest.post("/api/visit/ping", json={"path": "/terminal"})
        self.guest.get("/api/layers/trial")
        with self.store._lock:
            self.store._db.execute("UPDATE layer_trials SET started_at=?",
                                   (time.time() - 3600,))
            self.store._db.commit()
        live = self.admin.get("/api/admin/geo?period=24h").json()["online"][0]
        self.assertTrue(live["trial"]["expired"])
        self.assertEqual(live["trial"]["left_sec"], 0.0)


if __name__ == "__main__":
    unittest.main()
