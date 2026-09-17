"""API сервиса «Сторож монет» в кабинете.

Проверяем, что кабинет умеет: отдать настройки сторожа (режим, порог, период,
число свечей), показать резкие движения и уже сработавшие сигналы, сохранить
выбор пользователя и не пустить неподтверждённый адрес.

Запуск: /tmp/venv/bin/python tests/test_pump_api.py
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
from pump_scan import normalize  # noqa: E402

NOW = 1789669572.0


def fake_snapshot(config=None) -> dict:
    """Готовый снимок: так его отдаёт сервер по минутной истории цен."""
    cfg = normalize(config or {})
    return {
        "now": NOW,
        "config": cfg,
        "status": {"coins": 418, "updated": NOW - 12, "age_sec": 12.0,
                   "poll_sec": 25.0, "signals_total": 2, "lang": "ru"},
        "movers": [
            {"symbol": "SOL_USDT", "change_pct": 31.4, "price": 152.4,
             "volume24h": 12_500_000.0, "span_min": 15, "period": "5m",
             "candles": 3},
            {"symbol": "DOGE_USDT", "change_pct": -18.2, "price": 0.31,
             "volume24h": 4_000_000.0, "span_min": 15, "period": "5m",
             "candles": 3},
        ],
        "hits": [{"symbol": "SOL_USDT", "kind": "pump", "change_pct": 31.4,
                  "span_min": 15}],
        "signals": [{"symbol": "SOL_USDT", "kind": "pump", "change_pct": 31.4,
                     "ts": NOW - 120}],
        "periods": [{"key": "1m", "minutes": 1}, {"key": "5m", "minutes": 5}],
        "thresholds": [3, 5, 10, 20],
        "candles": [1, 2, 3, 5, 10],
    }


class PumpApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        user = self.store.create_email_user("vasya@example.com",
                                            password_hash="x")["user"]
        self.user_id = int(user["id"])
        self.store.mark_email_verified(self.user_id, promote_admin=False)
        self.token = self.store.create_session(self.user_id)
        fresh = self.store.create_email_user("novy@example.com",
                                             password_hash="x")["user"]
        self.fresh_id = int(fresh["id"])
        self.fresh_token = self.store.create_session(self.fresh_id)

        self.calls: list = []

        def snapshot(config=None):
            cfg = normalize(config or {})
            self.calls.append(cfg)
            return fake_snapshot(cfg)

        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        web_account.ctx.pump_snapshot_fn = snapshot

        self.app = FastAPI()
        web_account.register_account_routes(self.app)
        self.client = TestClient(self.app)
        self.client.cookies.set(COOKIE_SID, self.token)
        self.fresh = TestClient(self.app)
        self.fresh.cookies.set(COOKIE_SID, self.fresh_token)
        self.anon = TestClient(self.app)

    def tearDown(self):
        web_account.ctx.store = None
        web_account.ctx.pump_snapshot_fn = staticmethod(lambda config=None: {})
        self.tmp.cleanup()

    # --- доступ ------------------------------------------------------------
    def test_requires_auth(self):
        self.assertEqual(self.anon.get("/api/account/watchlist").status_code, 401)
        self.assertEqual(self.anon.post("/api/account/watchlist", json={}).status_code,
                         401)

    def test_unverified_email_locked(self):
        r = self.fresh.get("/api/account/watchlist")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("error"), "email_unverified")

    # --- снимок ------------------------------------------------------------
    def test_snapshot_has_everything(self):
        r = self.client.get("/api/account/watchlist")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["status"]["coins"], 418)
        self.assertEqual([m["symbol"] for m in d["movers"]],
                         ["SOL_USDT", "DOGE_USDT"])
        self.assertEqual(d["hits"][0]["kind"], "pump")
        self.assertEqual(d["signals"][0]["change_pct"], 31.4)
        self.assertFalse(d["subscribed"])
        self.assertTrue(d["periods"] and d["thresholds"] and d["candles"])
        self.assertTrue(d["modes"])
        self.assertEqual(d["config"]["period"], "5m")
        self.assertEqual(d["config"]["candles"], 3)
        self.assertEqual(self.calls[-1]["mode"], "both")

    def test_defaults_are_sane(self):
        d = self.client.get("/api/account/watchlist").json()
        cfg = d["config"]
        self.assertEqual(cfg["mode"], "both")
        self.assertEqual(cfg["period"], "5m")
        self.assertEqual(cfg["candles"], 3)
        self.assertEqual(cfg["threshold"], 10.0)
        self.assertTrue(cfg["enabled"])

    def test_saved_config_goes_to_server(self):
        self.client.post("/api/account/watchlist",
                         json={"mode": "dump", "period": "1m", "candles": 5,
                               "threshold": 20})
        self.client.get("/api/account/watchlist")
        cfg = self.calls[-1]
        self.assertEqual(cfg["mode"], "dump")
        self.assertEqual(cfg["period"], "1m")
        self.assertEqual(cfg["candles"], 5)
        self.assertEqual(cfg["threshold"], 20.0)

    def test_server_error_does_not_break_page(self):
        def boom(config=None):
            raise RuntimeError("Gate недоступен")
        web_account.ctx.pump_snapshot_fn = boom
        r = self.client.get("/api/account/watchlist")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["movers"], [])
        self.assertEqual(d["config"]["threshold"], 10.0)

    # --- сохранение выбора --------------------------------------------------
    def test_save_choice(self):
        r = self.client.post("/api/account/watchlist",
                             json={"mode": "pump", "period": "15m", "candles": 3,
                                   "threshold": 30})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["config"]["mode"], "pump")
        self.assertEqual(body["config"]["threshold"], 30.0)
        self.assertTrue(body["subscribed"])
        row = self.store.get_user_service(self.user_id, "watchlist")
        self.assertTrue(row["enabled"])
        self.assertEqual(row["config"]["period"], "15m")
        self.assertEqual(row["config"]["threshold"], 30.0)

    def test_bad_values_are_clamped(self):
        r = self.client.post("/api/account/watchlist",
                             json={"mode": "teleport", "period": "77y",
                                   "candles": 999, "threshold": -5})
        self.assertEqual(r.status_code, 200)
        cfg = r.json()["config"]
        self.assertIn(cfg["mode"], {"pump", "dump", "both"})
        self.assertNotEqual(cfg["period"], "77y")
        self.assertLessEqual(cfg["candles"], 60)
        self.assertGreater(cfg["threshold"], 0)

    def test_broken_json_falls_back_to_defaults(self):
        r = self.client.post("/api/account/watchlist", content=b"{not json",
                             headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["config"]["period"], "5m")

    def test_service_is_live_in_list(self):
        r = self.client.get("/api/account/services")
        row = next(s for s in r.json()["services"] if s["slug"] == "watchlist")
        self.assertFalse(row["coming_soon"])
        self.assertIn("Пампы и дампы", row["description"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
