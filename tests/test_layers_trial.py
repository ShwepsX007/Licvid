"""Пробный доступ к слоям: 30 минут гостю, потом предложение регистрации.

Проверяем серверную часть шлюза — ту, которую нельзя обойти перезагрузкой
страницы:

* ``Store.layer_trial`` заводит строку на гостя один раз и не сбрасывает её
  при повторных заходах;
* время вышло → ``allowed: false``, и так же отвечает ручка
  ``GET /api/layers/trial``;
* зарегистрированному пробник не нужен: ``left_sec: None``;
* ``LIQSCOPE_LAYERS_TRIAL_MIN=0`` выключает ограничение целиком;
* сводка ``layer_trial_stats`` считает, сколько гостей упёрлось в стену.

Запуск:  python3 -m pytest tests/test_layers_trial.py -q
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

import web_account  # noqa: E402
import web_layers  # noqa: E402
from accounts import COOKIE_SID, COOKIE_VID, LAYERS_TRIAL_SEC, Store  # noqa: E402

LIMIT = 1800


class TrialStoreTest(unittest.TestCase):
    """Хранилище: строка на гостя, остаток времени, сводка."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        self.user = self.store.create_email_user("vasya@example.com",
                                                 password_hash="x")["user"]

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_first_call_starts_the_clock(self) -> None:
        res = self.store.layer_trial("v:guest-1", limit_sec=LIMIT)
        self.assertTrue(res["tracked"])
        self.assertTrue(res["allowed"])
        self.assertFalse(res["expired"])
        self.assertAlmostEqual(res["left"], LIMIT, delta=2)
        self.assertEqual(res["hits"], 1)

    def test_second_call_does_not_restart_the_clock(self) -> None:
        """Перезагрузка страницы пробник не начинает заново."""
        first = self.store.layer_trial("v:guest-1", limit_sec=LIMIT, now=1000.0)
        again = self.store.layer_trial("v:guest-1", limit_sec=LIMIT, now=1000.0 + 900)
        self.assertEqual(first["started"], again["started"])
        self.assertAlmostEqual(again["left"], 900.0, delta=0.1)
        self.assertEqual(again["hits"], 2)
        self.assertTrue(again["allowed"])

    def test_clock_runs_out(self) -> None:
        self.store.layer_trial("v:guest-2", limit_sec=LIMIT, now=1000.0)
        late = self.store.layer_trial("v:guest-2", limit_sec=LIMIT,
                                      now=1000.0 + LIMIT + 5)
        self.assertFalse(late["allowed"])
        self.assertTrue(late["expired"])
        self.assertEqual(late["left"], 0.0)

    def test_registered_user_has_no_limit(self) -> None:
        res = self.store.layer_trial("v:guest-3", user_id=self.user["id"], limit_sec=LIMIT)
        self.assertFalse(res["tracked"])
        self.assertFalse(res["guest"])
        self.assertTrue(res["allowed"])
        self.assertIsNone(res["left"])
        self.assertEqual(self.store.layer_trial_stats(LIMIT)["total"], 0,
                         "зарегистрированных в таблицу испытаний не пишем")

    def test_no_guest_key_is_not_counted(self) -> None:
        """Нет ни cookie, ни адреса — считать не по чему, гостя не запираем."""
        res = self.store.layer_trial("", limit_sec=LIMIT)
        self.assertFalse(res["tracked"])
        self.assertTrue(res["allowed"])
        self.assertIsNone(res["left"])

    def test_stats_split_active_and_expired(self) -> None:
        now = time.time()
        self.store.layer_trial("v:fresh", limit_sec=LIMIT, now=now - 60)
        self.store.layer_trial("v:old", limit_sec=LIMIT, now=now - LIMIT - 60)
        stats = self.store.layer_trial_stats(now=now, limit_sec=LIMIT)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["active"], 1)
        self.assertEqual(stats["expired"], 1)

    def test_default_limit_is_thirty_minutes(self) -> None:
        self.assertEqual(LAYERS_TRIAL_SEC, 30 * 60)


class TrialApiTest(unittest.TestCase):
    """Ручка /api/layers/trial: что видит страница терминала."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        self.user = self.store.create_email_user("vasya@example.com",
                                                 password_hash="x")["user"]
        app = FastAPI()
        self.old_store = web_account.ctx.store
        web_account.ctx.store = self.store
        web_layers.ctx.store = self.store
        web_layers.ctx.secret = "s"
        web_layers.register_layer_routes(app)
        self.client = TestClient(app)
        self._env = os.environ.pop("LIQSCOPE_LAYERS_TRIAL_MIN", None)

    def tearDown(self) -> None:
        web_account.ctx.store = self.old_store
        web_layers.ctx.store = None
        if self._env is not None:
            os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = self._env
        else:
            os.environ.pop("LIQSCOPE_LAYERS_TRIAL_MIN", None)
        self.store.close()
        self.tmp.cleanup()

    def _guest(self):
        c = TestClient(self.client.app)
        c.cookies.set(COOKIE_VID, "guest-abc")
        return c

    def test_guest_gets_thirty_minutes(self) -> None:
        res = self._guest().get("/api/layers/trial")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["guest"])
        self.assertTrue(data["allowed"])
        self.assertAlmostEqual(data["left_sec"], LIMIT, delta=3)
        self.assertEqual(data["limit_sec"], LIMIT)
        self.assertFalse(data["expired"])

    def test_guest_is_stopped_when_time_is_over(self) -> None:
        self.store.layer_trial("v:guest-abc", limit_sec=LIMIT,
                               now=time.time() - LIMIT - 30)
        data = self._guest().get("/api/layers/trial").json()
        self.assertFalse(data["allowed"])
        self.assertTrue(data["expired"])
        self.assertEqual(data["left_sec"], 0.0)
        self.assertGreater(data["ended_at"], 0)

    def test_time_is_shared_between_requests(self) -> None:
        first = self._guest().get("/api/layers/trial").json()
        second = self._guest().get("/api/layers/trial").json()
        self.assertAlmostEqual(first["left_sec"], second["left_sec"], delta=3)
        self.assertEqual(second["hits"], 2)

    def test_signed_in_user_has_no_limit(self) -> None:
        token = self.store.create_session(self.user["id"])
        c = TestClient(self.client.app)
        c.cookies.set(COOKIE_SID, token)
        data = c.get("/api/layers/trial").json()
        self.assertFalse(data["guest"])
        self.assertTrue(data["allowed"])
        self.assertIsNone(data["left_sec"])

    def test_cookie_less_guest_is_counted_by_address(self) -> None:
        """Отключённые cookie не дают бесконечный пробник: считаем по ip+ua."""
        first = self.client.get("/api/layers/trial",
                                headers={"User-Agent": "Mozilla/5.0 (X11; Linux)"}).json()
        self.assertTrue(first["allowed"])
        self.assertIsNotNone(first["left_sec"])
        second = self.client.get("/api/layers/trial",
                                 headers={"User-Agent": "Mozilla/5.0 (X11; Linux)"}).json()
        self.assertEqual(second["hits"], 2, "один и тот же гость — одна строка")

    def test_zero_minutes_turns_the_gate_off(self) -> None:
        os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = "0"
        data = self.client.get("/api/layers/trial").json()
        self.assertTrue(data["allowed"])
        self.assertTrue(data.get("disabled"))
        self.assertIsNone(data["left_sec"])
        self.assertEqual(web_layers.trial_limit_sec(), 0)

    def test_limit_comes_from_environment(self) -> None:
        os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = "15"
        self.assertEqual(web_layers.trial_limit_sec(), 15 * 60)
        os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = "мусор"
        self.assertEqual(web_layers.trial_limit_sec(), 30 * 60)
        os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = "99999"
        self.assertEqual(web_layers.trial_limit_sec(), 24 * 60 * 60)
        os.environ.pop("LIQSCOPE_LAYERS_TRIAL_MIN", None)
        self.assertEqual(web_layers.trial_limit_sec(), 30 * 60)

    def test_no_store_does_not_block_anybody(self) -> None:
        web_layers.ctx.store = None
        data = self.client.get("/api/layers/trial").json()
        self.assertTrue(data["allowed"])
        self.assertIsNone(data["left_sec"])



ADMIN_EMAIL = "boss@liqscope.online"


class LayersAdminApiTest(unittest.TestCase):
    """Админка сайта: лимит пробного доступа меняется без перезапуска.

    Проверяем то, ради чего настройка и делалась: админ сам решает, сколько
    минут давать гостю (в том числе 0 — совсем без ограничения), правка
    действует сразу и на тех, кто уже в терминале, а живой гость может
    получить свой таймер заново, не меняя лимит для всех.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=[ADMIN_EMAIL])
        self.admin_id = int(self.store.create_email_user(
            ADMIN_EMAIL, password_hash="x")["user"]["id"])
        self.plain_id = int(self.store.create_email_user(
            "vasya@example.com", password_hash="x")["user"]["id"])
        self.old_store = web_account.ctx.store
        web_account.ctx.store = self.store
        web_layers.ctx.store = self.store
        web_layers.ctx.secret = "s"
        app = FastAPI()
        web_layers.register_layer_routes(app)
        self.client = TestClient(app)
        self.admin = TestClient(app)
        self.admin.cookies.set(COOKIE_SID, self.store.create_session(self.admin_id))
        self.plain = TestClient(app)
        self.plain.cookies.set(COOKIE_SID, self.store.create_session(self.plain_id))
        self._env = os.environ.pop("LIQSCOPE_LAYERS_TRIAL_MIN", None)

    def tearDown(self) -> None:
        web_account.ctx.store = self.old_store
        web_layers.ctx.store = None
        if self._env is not None:
            os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = self._env
        else:
            os.environ.pop("LIQSCOPE_LAYERS_TRIAL_MIN", None)
        self.store.close()
        self.tmp.cleanup()

    def _guest(self, vid: str = "guest-abc"):
        c = TestClient(self.client.app)
        c.cookies.set(COOKIE_VID, vid)
        return c

    # --- доступ -------------------------------------------------------------
    def test_only_admin_may_read_and_change(self) -> None:
        self.assertEqual(self.client.get("/api/admin/layers/settings").status_code, 401)
        self.assertEqual(self.plain.get("/api/admin/layers/settings").status_code, 403)
        self.assertEqual(self.plain.post("/api/admin/layers/settings",
                                         json={"minutes": 5}).status_code, 403)
        self.assertEqual(self.client.post("/api/admin/layers/reset").status_code, 401)

    def test_default_is_thirty_minutes(self) -> None:
        d = self.admin.get("/api/admin/layers/settings").json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["minutes"], 30)
        self.assertEqual(d["limit_sec"], LIMIT)
        self.assertEqual(d["default_min"], 30)
        self.assertEqual(d["source"], "default")
        self.assertFalse(d["locked"])
        self.assertEqual(d["env_var"], "LIQSCOPE_LAYERS_TRIAL_MIN")
        self.assertEqual(d["stats"]["total"], 0)

    # --- правка лимита ------------------------------------------------------
    def test_admin_changes_the_limit_and_guests_get_it(self) -> None:
        d = self.admin.post("/api/admin/layers/settings", json={"minutes": 45}).json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["saved"], 45)
        self.assertEqual(d["source"], "site")
        self.assertEqual(self.store.get_setting("layers_trial_min"), "45")
        data = self._guest().get("/api/layers/trial").json()
        self.assertEqual(data["limit_sec"], 45 * 60)
        self.assertAlmostEqual(data["left_sec"], 45 * 60, delta=3)

    def test_new_limit_moves_the_clock_of_a_guest_who_is_already_inside(self) -> None:
        """Гость в терминале: подняли лимит — у него сразу больше времени."""
        guest = self._guest()
        self.store.layer_trial("v:guest-abc", limit_sec=LIMIT, now=time.time() - 900)
        before = guest.get("/api/layers/trial").json()
        self.assertAlmostEqual(before["left_sec"], 900, delta=5)
        self.admin.post("/api/admin/layers/settings", json={"minutes": 60})
        after = guest.get("/api/layers/trial").json()
        self.assertAlmostEqual(after["left_sec"], 3600 - 900, delta=6)

    def test_lowering_the_limit_can_close_the_gate(self) -> None:
        guest = self._guest()
        self.store.layer_trial("v:guest-abc", limit_sec=LIMIT, now=time.time() - 1200)
        self.admin.post("/api/admin/layers/settings", json={"minutes": 10})
        data = guest.get("/api/layers/trial").json()
        self.assertFalse(data["allowed"])
        self.assertTrue(data["expired"])
        self.assertEqual(data["limit_sec"], 600)

    def test_zero_turns_the_limit_off(self) -> None:
        d = self.admin.post("/api/admin/layers/settings", json={"minutes": 0}).json()
        self.assertEqual(d["minutes"], 0)
        data = self._guest().get("/api/layers/trial").json()
        self.assertTrue(data["allowed"])
        self.assertTrue(data.get("disabled"))
        self.assertIsNone(data["left_sec"])

    def test_value_is_clamped_and_garbage_is_rejected(self) -> None:
        d = self.admin.post("/api/admin/layers/settings", json={"minutes": 99999}).json()
        self.assertEqual(d["minutes"], 24 * 60)
        for bad in ("мусор", None, ""):
            r = self.admin.post("/api/admin/layers/settings", json={"minutes": bad})
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.admin.post("/api/admin/layers/settings",
                                         json={}).status_code, 400)

    def test_environment_wins_over_the_site(self) -> None:
        os.environ["LIQSCOPE_LAYERS_TRIAL_MIN"] = "15"
        d = self.admin.get("/api/admin/layers/settings").json()
        self.assertEqual(d["minutes"], 15)
        self.assertEqual(d["source"], "env")
        self.assertTrue(d["locked"])
        r = self.admin.post("/api/admin/layers/settings", json={"minutes": 90})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "locked")
        self.assertEqual(self.store.get_setting("layers_trial_min"), "",
                         "переменная окружения главнее — в базу ничего не пишем")

    def test_broken_setting_falls_back_to_default(self) -> None:
        """Мусор в базе не должен ломать шлюз: работаем как без настройки."""
        self.store.set_setting("layers_trial_min", "полчаса")
        self.assertEqual(web_layers.trial_limit_sec(), LIMIT)
        self.assertEqual(self.admin.get("/api/admin/layers/settings").json()["source"],
                         "default")

    # --- сброс таймера ------------------------------------------------------
    def test_reset_all_restarts_the_timers(self) -> None:
        self._guest("g1").get("/api/layers/trial")
        self._guest("g2").get("/api/layers/trial")
        d = self.admin.post("/api/admin/layers/reset", json={"all": True}).json()
        self.assertEqual(d["reset"], 2)
        self.assertEqual(self.store.layer_trial_stats(LIMIT)["total"], 0)
        self.assertAlmostEqual(self._guest("g1").get("/api/layers/trial")
                               .json()["left_sec"], LIMIT, delta=3)

    def test_reset_touches_only_the_named_guest(self) -> None:
        self._guest("g1").get("/api/layers/trial")
        self._guest("g2").get("/api/layers/trial")
        d = self.admin.post("/api/admin/layers/reset", json={"who": "v:g1"}).json()
        self.assertEqual(d["reset"], 1)
        self.assertFalse(d["all"])
        self.assertEqual(d["who"], "v:g1")
        whos = {r["who"] for r in
                [dict(x) for x in self.store._db.execute(
                    "SELECT who FROM layer_trials").fetchall()]}
        self.assertEqual(whos, {"v:g2"})

    def test_stats_and_journal_after_a_change(self) -> None:
        self._guest("g1").get("/api/layers/trial")
        self.store.layer_trial("v:old", limit_sec=LIMIT, now=time.time() - LIMIT - 60)
        d = self.admin.post("/api/admin/layers/settings", json={"minutes": 20}).json()
        self.assertEqual(d["stats"]["total"], 2)
        self.assertEqual(d["stats"]["active"], 1)
        self.assertEqual(d["stats"]["expired"], 1)
        self.assertIn("20 мин", d["note"])
        self.admin.post("/api/admin/layers/reset", json={"who": "v:g1"})
        actions = [row["action"] for row in self.store.recent_audit(20)]
        self.assertIn("setting", actions)
        self.assertIn("layers_reset", actions)


if __name__ == "__main__":
    unittest.main()
