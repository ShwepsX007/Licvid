"""Монитор качества данных: матрица «монета × поток» для эксплуатации.

Что проверяем:
* классификация свежести: ok / stale (с возрастом) / down по порогам канала;
* снимок собирается из счётчиков процесса (тики, цена, свечи, CVD, OI,
  ликвидации) и ранжирует монеты «больные первыми»;
* строка проблем для журнала называет монету и канал;
* ручка /api/data-quality отдаёт снимок, страница /data-quality — HTML;
* пороги приходят в ответе (страница рисует по ним легенду).

Запуск:  python3 tests/test_data_quality.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = os.path.join(HERE, "data", "tmp_dq")
os.makedirs(TMP, exist_ok=True)
os.environ.setdefault("LIQSCOPE_ACCOUNTS_DB", os.path.join(TMP, "accounts.db"))
os.environ.setdefault("LIQSCOPE_HISTORY_FILE", os.path.join(TMP, "liq.jsonl"))
os.environ.setdefault("LIQSCOPE_DIGEST_FILE", os.path.join(TMP, "digests.json"))
os.environ.setdefault("LIQSCOPE_MAIL_DIR", os.path.join(TMP, "mail"))
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
os.environ.setdefault("LIQSCOPE_DEMO", "0")
os.environ.setdefault("LIQSCOPE_RATE_LIMIT", "0")

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class Classify(unittest.TestCase):
    def test_ok_stale_down_by_thresholds(self):
        now = 1000000.0
        cl = server._dq_classify
        # пороги (ok 120, down 600): свежий / отстающий / пропавший
        self.assertEqual(cl(now - 10, now, (120, 600))["state"], "ok")
        self.assertEqual(cl(now - 200, now, (120, 600))["state"], "stale")
        self.assertEqual(cl(now - 200, now, (120, 600))["age_sec"], 200)
        self.assertEqual(cl(now - 999, now, (120, 600))["state"], "down")
        self.assertEqual(cl(None, now, (120, 600))["state"], "down")
        self.assertIsNone(cl(None, now, (120, 600))["age_sec"])

    def test_thresholds_cover_all_checks(self):
        for name in server.DQ_CHECKS:
            self.assertIn(name, server.DQ_THRESHOLDS)
            ok, down = server.DQ_THRESHOLDS[name]
            self.assertGreater(down, ok, f"{name}: порог down должен быть больше ok")


class Snapshot(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for name in ("LAST_TICK_TS", "LAST_PRICE_TS", "LAST_KLINE_TS", "CVD_ACC",
                     "LIQUIDATIONS"):
            self._saved[name] = getattr(server, name)
        self._saved_feed = server.feed
        server.LAST_TICK_TS = {}
        server.LAST_PRICE_TS = {}
        server.LAST_KLINE_TS = {}
        server.CVD_ACC = {}
        server.LIQUIDATIONS = []
        server.feed = None
        self.addCleanup(self._restore)

    def _restore(self):
        for name, val in self._saved.items():
            setattr(server, name, val)
        server.feed = self._saved_feed
        server._DQ_CACHE.invalidate()

    def test_matrix_and_ordering(self):
        now = time.time()
        # BTC: всё свежее
        server.LAST_TICK_TS["BTC_USDT"] = now - 5
        server.LAST_PRICE_TS["BTC_USDT"] = now - 5
        server.LAST_KLINE_TS["BTC_USDT"] = now - 30
        server.CVD_ACC["BTC_USDT|1"] = {int(now // 60) * 60: 1.0}
        server.LIQUIDATIONS.append({"symbol": "BTC_USDT", "timestamp": now - 60})
        # ETH: цена и OI отвалились, остальное свежее
        server.LAST_TICK_TS["ETH_USDT"] = now - 5
        server.LAST_PRICE_TS["ETH_USDT"] = now - 3600
        server.LAST_KLINE_TS["ETH_USDT"] = now - 30
        server.LIQUIDATIONS.append({"symbol": "ETH_USDT", "timestamp": now - 60})
        # OI fresh только у BTC — имитируем OiHistory
        class FakeOI:
            def symbols(self):
                return ["BTC_USDT"]

            def latest(self, sym):
                return (now - 60, 123.0) if sym == "BTC_USDT" else None

        real_oi = server.OI
        server.OI = FakeOI()
        try:
            snap = server.data_quality_snapshot(now)
        finally:
            server.OI = real_oi
        by_sym = {r["symbol"]: r for r in snap["symbols"]}
        self.assertIn("BTC_USDT", by_sym)
        self.assertIn("ETH_USDT", by_sym)
        btc = by_sym["BTC_USDT"]["checks"]
        eth = by_sym["ETH_USDT"]["checks"]
        for name in server.DQ_CHECKS:
            self.assertEqual(btc[name]["state"], "ok",
                             f"BTC/{name} должен быть ok")
        self.assertEqual(eth["price"]["state"], "down", "цена ETH давно не обновлялась")
        self.assertEqual(eth["oi"]["state"], "down", "OI по ETH не приходит")
        self.assertEqual(eth["trades"]["state"], "ok")
        # больные первыми: ETH выше BTC в списке
        self.assertLess(snap["symbols"].index(
            next(r for r in snap["symbols"] if r["symbol"] == "ETH_USDT")),
            snap["symbols"].index(
                next(r for r in snap["symbols"] if r["symbol"] == "BTC_USDT")))
        self.assertEqual(snap["summary"]["symbols"], 2)
        self.assertEqual(snap["summary"]["ok"], 1)
        self.assertGreaterEqual(snap["summary"]["down"] + snap["summary"]["warn"], 1)
        # пороги отдаются странице
        self.assertIn("trades", snap["thresholds"])
        self.assertIn("ok_sec", snap["thresholds"]["trades"])

    def test_stale_channel_reported_with_age(self):
        now = time.time()
        server.LAST_TICK_TS["SOL_USDT"] = now - 400       # порог trades: ok 180, down 1800
        snap = server.data_quality_snapshot(now)
        row = next(r for r in snap["symbols"] if r["symbol"] == "SOL_USDT")
        self.assertEqual(row["checks"]["trades"]["state"], "stale")
        self.assertGreater(row["checks"]["trades"]["age_sec"], 300)
        text = server.data_quality_issues_text(snap)
        self.assertIn("SOL_USDT", text)
        self.assertIn("trades", text)

    def test_healthy_snapshot_has_no_issues_text(self):
        now = time.time()
        for name in ("BTC_USDT",):
            server.LAST_TICK_TS[name] = now - 1
            server.LAST_PRICE_TS[name] = now - 1
            server.LAST_KLINE_TS[name] = now - 1
            server.CVD_ACC[name + "|1"] = {int(now // 60) * 60: 1.0}
            server.LIQUIDATIONS.append({"symbol": name, "timestamp": now - 1})
        snap = server.data_quality_snapshot(now)
        # у монеты нет только OI — это down, строка непуста; проверяем,
        # что свежие каналы в неё НЕ попадают
        text = server.data_quality_issues_text(snap)
        self.assertNotIn("trades", text)
        self.assertNotIn("price", text)
        self.assertIn("oi", text)

    def test_cvd_freshness_splits_symbol_from_tf(self):
        now = time.time()
        server.CVD_ACC["BTC_USDT|5"] = {int(now // 300) * 300 - 600: 1.0}
        server.CVD_ACC["ETH_USDT|1"] = {int(now // 60) * 60: 2.0}
        fresh = server._dq_cvd_freshness()
        self.assertIn("BTC_USDT", fresh)
        self.assertIn("ETH_USDT", fresh)
        self.assertNotIn("BTC_USDT|5", fresh)


class ApiAndPage(unittest.TestCase):
    def test_endpoint_and_page(self):
        with TestClient(server.app) as c:
            r = c.get("/api/data-quality")
            self.assertEqual(r.status_code, 200)
            d = r.json()
            for key in ("ts", "summary", "thresholds", "sources", "symbols"):
                self.assertIn(key, d)
            self.assertIsInstance(d["symbols"], list)
            if d["symbols"]:
                row = d["symbols"][0]
                self.assertIn("checks", row)
                for name in server.DQ_CHECKS:
                    self.assertIn(name, row["checks"])
                    self.assertIn(row["checks"][name]["state"],
                                  ("ok", "stale", "down"))
            p = c.get("/data-quality")
            self.assertEqual(p.status_code, 200)
            self.assertIn("text/html", p.headers.get("content-type", ""))
            self.assertIn("Монитор качества", p.text)


if __name__ == "__main__":
    unittest.main()
