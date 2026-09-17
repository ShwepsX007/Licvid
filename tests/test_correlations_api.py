"""API сервиса «Корреляции валют» в кабинете.

Проверяем, что кабинет умеет: спросить картину корреляций за окно и метрику,
запомнить выбор пользователя, отдать готовый разбор (тепловая карта, связи,
направления ликвидаций, перекос CVD, рост и падение OI) и не пустить
неподтверждённый адрес.

Запуск: /tmp/venv/bin/python tests/test_correlations_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import web_account  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402
from correlations import build  # noqa: E402

HOUR = 3600
NOW = 1789669572.0


def fake_picture(window="24h", metric="liq") -> dict:
    """Готовая картина: так её отдаёт сервер по истории."""
    res = build([], now=NOW, window=window, metric=metric)
    res.update({
        "symbols": ["BTC_USDT", "ETH_USDT"],
        "coins": {
            "BTC_USDT": {"symbol": "BTC_USDT", "liq_usd": 500000.0,
                         "liq_long": 400000.0, "liq_short": 100000.0, "count": 5,
                         "cvd": -120000.0, "vol": 4000000.0, "cvd_share": -3.0,
                         "price": 61000.0, "oi_delta": 900000.0, "oi_pct": 0.4},
            "ETH_USDT": {"symbol": "ETH_USDT", "liq_usd": 300000.0,
                         "liq_long": 50000.0, "liq_short": 250000.0, "count": 4,
                         "cvd": 80000.0, "vol": 2000000.0, "cvd_share": 4.0,
                         "price": 2510.0, "oi_delta": -400000.0, "oi_pct": -0.2},
        },
        "matrices": {"liq": {"BTC_USDT": {"BTC_USDT": 1.0, "ETH_USDT": 0.91},
                             "ETH_USDT": {"BTC_USDT": 0.91, "ETH_USDT": 1.0}},
                     "cvd": {"BTC_USDT": {"BTC_USDT": 1.0, "ETH_USDT": -0.77},
                             "ETH_USDT": {"BTC_USDT": -0.77, "ETH_USDT": 1.0}}},
        "pairs": {"liq": [{"a": "BTC_USDT", "b": "ETH_USDT", "r": 0.91}],
                  "cvd": [{"a": "BTC_USDT", "b": "ETH_USDT", "r": -0.77}]},
        "flows": {"liquidated_long": ["BTC_USDT"], "liquidated_short": ["ETH_USDT"],
                  "cvd_sellers": ["BTC_USDT"], "cvd_buyers": ["ETH_USDT"],
                  "oi_up": ["BTC_USDT"], "oi_down": ["ETH_USDT"]},
    })
    return res


class CorrelationsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        user = self.store.create_email_user("vasya@example.com", password_hash="x")["user"]
        self.user_id = int(user["id"])
        self.store.mark_email_verified(self.user_id, promote_admin=False)
        self.token = self.store.create_session(self.user_id)
        fresh = self.store.create_email_user("novy@example.com", password_hash="x")["user"]
        self.fresh_id = int(fresh["id"])
        self.fresh_token = self.store.create_session(self.fresh_id)

        self.calls: list = []

        def picture(window="24h", metric="liq"):
            self.calls.append((window, metric))
            return fake_picture(window, metric)

        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        web_account.ctx.correlations_fn = picture

        self.app = FastAPI()
        web_account.register_account_routes(self.app)
        self.client = TestClient(self.app)
        self.client.cookies.set(COOKIE_SID, self.token)
        self.fresh = TestClient(self.app)
        self.fresh.cookies.set(COOKIE_SID, self.fresh_token)
        self.anon = TestClient(self.app)

    def tearDown(self):
        web_account.ctx.store = None
        web_account.ctx.correlations_fn = staticmethod(lambda window="24h", metric="liq": {})
        self.tmp.cleanup()

    # --- доступ ------------------------------------------------------------
    def test_requires_auth(self):
        self.assertEqual(self.anon.get("/api/account/correlations").status_code, 401)

    def test_unverified_email_locked(self):
        r = self.fresh.get("/api/account/correlations")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("error"), "email_unverified")

    # --- картина -----------------------------------------------------------
    def test_picture_has_everything(self):
        r = self.client.get("/api/account/correlations")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["window"], "24h")
        self.assertEqual(d["metric"], "liq")
        self.assertEqual(d["symbols"], ["BTC_USDT", "ETH_USDT"])
        self.assertAlmostEqual(d["matrices"]["liq"]["BTC_USDT"]["ETH_USDT"], 0.91)
        self.assertEqual(d["flows"]["liquidated_short"], ["ETH_USDT"])
        self.assertEqual(d["flows"]["cvd_sellers"], ["BTC_USDT"])
        self.assertEqual(d["flows"]["oi_up"], ["BTC_USDT"])
        self.assertFalse(d["subscribed"])
        self.assertTrue(d["windows"] and d["metrics"])
        self.assertEqual(self.calls[-1], ("24h", "liq"))

    def test_window_and_metric_from_query(self):
        self.client.get("/api/account/correlations",
                        params={"window": "7d", "metric": "cvd"})
        self.assertEqual(self.calls[-1], ("7d", "cvd"))

    def test_bad_window_falls_back(self):
        self.client.get("/api/account/correlations", params={"window": "100500"})
        win, _metric = self.calls[-1]
        self.assertIn(win, {"1h", "4h", "12h", "24h", "3d", "7d"})

    def test_server_error_does_not_break_page(self):
        def boom(window="24h", metric="liq"):
            raise RuntimeError("история недоступна")
        web_account.ctx.correlations_fn = boom
        r = self.client.get("/api/account/correlations")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["symbols"], [])

    # --- сохранение выбора --------------------------------------------------
    def test_save_choice(self):
        r = self.client.post("/api/account/correlations",
                             json={"window": "4h", "metric": "oi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["config"], {"window": "4h", "metric": "oi"})
        self.assertTrue(r.json()["subscribed"])
        row = self.store.get_user_service(self.user_id, "correlations")
        self.assertEqual(row["config"]["metric"], "oi")
        self.assertTrue(row["enabled"])
        # следующий запрос по умолчанию берёт сохранённое окно
        self.client.get("/api/account/correlations")
        self.assertEqual(self.calls[-1], ("4h", "oi"))

    def test_saved_choice_is_per_user(self):
        self.client.post("/api/account/correlations", json={"window": "3d"})
        other = Store(os.path.join(self.tmp.name, "b.db"), secret="s")
        self.assertIsNone(other.get_user_service(self.user_id, "correlations"))

    def test_service_is_live_in_list(self):
        r = self.client.get("/api/account/services")
        row = next(s for s in r.json()["services"] if s["slug"] == "correlations")
        self.assertFalse(row["coming_soon"])
        self.assertIn("CVD", row["description"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
