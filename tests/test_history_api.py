"""Месячная история через API: /api/history отдаёт месяц, а не сутки.

Проверяем, что сервер действительно хранит и умеет отдать:
    * сырые события ликвидаций (bucket=raw);
    * часовые и дневные свёртки с CVD и объёмом (bucket=hour|day);
    * ряды для графиков сайта (bucket=series);
    * срок хранения по умолчанию — 31 сутки, старые файлы удаляются.

Запуск: /tmp/venv/bin/python tests/test_history_api.py
"""
from __future__ import annotations

import calendar
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="liqhist_api_")
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq_history.jsonl")
os.environ["LIQSCOPE_HISTORY_TTL_HOURS"] = "744"          # 31 сутки
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_BOT_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from history import day_key  # noqa: E402

HOUR = 3600
# «Сейчас» считаем от настоящих часов, а не от фиксированной даты: окно истории
# и TTL отмеряются на сервере от текущего времени, поэтому жёсткая дата рано или
# поздно уезжает за границу хранения и тест падает на здоровом коде. Берём
# полдень сегодняшнего дня (или вчерашнего, если полдень ещё не наступил), чтобы
# сдвиги на часы не перескакивали через сутки.
_NOW = time.time()
_DAY0 = float(calendar.timegm(tuple(time.gmtime(_NOW)[:3]) + (0, 0, 0, 0, 0)))
NOW = _DAY0 + 12 * HOUR if _DAY0 + 12 * HOUR <= _NOW else _DAY0 - 12 * HOUR
OLD_TS = NOW - 40 * 24 * HOUR          # за пределами TTL
RECENT_TS = NOW - 3 * HOUR


def ev(n: int, ts: float, symbol: str = "BTC_USDT", usd: float = 100000.0,
       side: str = "SELL", exch: str = "binance") -> dict:
    return {"id": f"ev{n}", "symbol": symbol, "exchange": exch, "side": side,
            "position": "LONG" if side == "SELL" else "SHORT",
            "price": 50000.0, "qty": usd / 50000.0, "usd": usd,
            "timestamp": float(ts)}


class HistoryApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)          # без lifespan: фид не поднимаем
        # свежие события: разные монеты, стороны и часы
        server.HIST.add(ev(1, RECENT_TS, "BTC_USDT", 250000, "SELL", "binance"))
        server.HIST.add(ev(2, RECENT_TS + 60, "ETH_USDT", 150000, "BUY", "bybit"))
        server.HIST.add(ev(3, RECENT_TS + HOUR, "BTC_USDT", 80000, "BUY", "okx"))
        server.HIST.add_flow("BTC_USDT", RECENT_TS, cvd=-400000, vol=2000000)
        server.HIST.add_flow("ETH_USDT", RECENT_TS, cvd=120000, vol=1500000)
        # древнее — должно быть удалено уборкой
        server.HIST.add(ev(9, OLD_TS, "SOL_USDT", 999999))
        server.HIST.flush(force=True)
        server.HIST.cleanup(NOW)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    # --- сырые события -----------------------------------------------------
    def test_raw_events_in_range(self):
        r = self.client.get("/api/history", params={
            "since": RECENT_TS - 60, "until": NOW, "bucket": "raw"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["bucket"], "raw")
        self.assertEqual(data["ttl_hours"], 744)
        ids = [e["id"] for e in data["liquidations"]]
        self.assertIn("ev2", ids)
        self.assertNotIn("ev9", ids)          # событие удалённого дня

    def test_raw_filter_by_symbol_and_size(self):
        r = self.client.get("/api/history", params={
            "since": RECENT_TS - 60, "until": NOW, "bucket": "raw",
            "symbol": "ETH_USDT"})
        syms = {e["symbol"] for e in r.json()["liquidations"]}
        self.assertEqual(syms, {"ETH_USDT"})
        r = self.client.get("/api/history", params={
            "since": RECENT_TS - 60, "until": NOW, "bucket": "raw",
            "min_usd": 200000})
        self.assertTrue(all(e["usd"] >= 200000 for e in r.json()["liquidations"]))

    def test_raw_default_limit_and_explicit_limit(self):
        requested = []

        def fake_query(since, until, symbol, min_usd, limit, newest_first=True):
            requested.append(limit)
            return []

        with patch.object(server.HIST, "query", side_effect=fake_query):
            self.assertEqual(self.client.get("/api/history?bucket=raw").status_code, 200)
            self.assertEqual(self.client.get(
                "/api/history?bucket=raw&limit=1500").status_code, 200)
            self.assertEqual(self.client.get(
                "/api/history?bucket=raw&limit=999999").status_code, 200)
        self.assertEqual(requested, [1000, 1500, 4000])

    def test_heavy_routes_bypass_fastapi_jsonable_encoder(self):
        import fastapi.routing
        if server._orjson is None:
            self.skipTest("orjson is optional outside production")
        with patch.object(fastapi.routing, "jsonable_encoder",
                          side_effect=AssertionError("encoder called")):
            for route in ("/api/history?bucket=raw", "/api/history?bucket=hour",
                          "/api/liquidations?limit=5", "/api/klines",
                          "/api/oi"):
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.headers["content-type"].startswith(
                        "application/json"))
                    self.assertIsInstance(response.json(), dict)

    def test_liquidations_cap_preserves_total_and_latest_items(self):
        saved = list(server.LIQUIDATIONS)
        try:
            server.LIQUIDATIONS.clear()
            server.LIQUIDATIONS.extend(
                ev(i + 1000, NOW - (1101 - i), "BTC_USDT")
                for i in range(1101))
            # Изолируем явный лимит от архива: здесь проверяем только RAM-хвост.
            with patch.object(server.HIST, "query", return_value=[]):
                r = self.client.get("/api/liquidations?limit=2000")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertEqual(data["total"], 1101)
            self.assertEqual(len(data["liquidations"]), 1101)
            # default 300 и явный limit=2000 не должны сливаться в max=1000.
            default = self.client.get("/api/liquidations").json()
            self.assertEqual(default["total"], 1101)
            self.assertEqual(len(default["liquidations"]), 300)
            self.assertEqual(data["liquidations"][-1]["id"], "ev2100")
        finally:
            server.LIQUIDATIONS.clear()
            server.LIQUIDATIONS.extend(saved)

    def test_oi_integer_bucket_keys_remain_json_compatible(self):
        class FakeOI:
            async def ensure_symbol(self, symbol):
                return None

            def payload(self, symbol):
                return {"symbol": symbol, "total_usd": 100.0,
                        "_series": {12345: {"binance": 100.0}}}

        with patch.object(server, "feed", SimpleNamespace(oi=FakeOI())):
            response = self.client.get("/api/oi?symbol=BTC_USDT")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["_series"]["12345"]["binance"], 100.0)

    def test_default_hours_is_a_day(self):
        r = self.client.get("/api/history", params={"bucket": "hour"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertAlmostEqual(data["until"] - data["since"], 24 * HOUR, delta=5)

    # --- свёртки -----------------------------------------------------------
    def test_hour_buckets(self):
        r = self.client.get("/api/history", params={
            "since": RECENT_TS - 60, "until": NOW, "bucket": "hour"})
        rows = r.json()["hours"]
        self.assertTrue(rows)
        total = sum(x["usd"] for x in rows)
        self.assertAlmostEqual(total, 480000.0, places=2)     # 250+150+80 тыс
        self.assertAlmostEqual(sum(x["count"] for x in rows), 3, places=0)
        names = set()
        for row in rows:
            names.update(row["by_symbol"].keys())
        self.assertEqual(names, {"BTC_USDT", "ETH_USDT"})
        self.assertIn("bybit", {k.lower() for row in rows for k in row["by_exchange"]})

    def test_hour_buckets_by_symbol(self):
        r = self.client.get("/api/history", params={
            "since": RECENT_TS - 60, "until": NOW, "bucket": "hour",
            "symbol": "BTC_USDT"})
        rows = r.json()["hours"]
        self.assertAlmostEqual(sum(x["usd"] for x in rows), 330000.0, places=2)

    def test_day_buckets_carry_cvd_and_volume(self):
        r = self.client.get("/api/history", params={
            "since": RECENT_TS - 12 * HOUR, "until": NOW, "bucket": "day"})
        days = r.json()["days"]
        self.assertEqual(len(days), 1)
        day = days[0]
        self.assertEqual(day["day"], day_key(NOW))   # сутки, в которых мы живём
        self.assertAlmostEqual(day["usd"], 480000.0, places=2)
        # CVD рынка: -400к + 120к
        self.assertAlmostEqual(day["cvd"], -280000.0, places=2)
        self.assertAlmostEqual(day["vol"], 3500000.0, places=2)
        self.assertAlmostEqual(day["cvd_share"], -8.0, places=1)
        self.assertEqual(day["max_symbol"], "BTC_USDT")

    def test_series_points(self):
        r = self.client.get("/api/history", params={
            "since": RECENT_TS - 12 * HOUR, "until": NOW, "bucket": "series",
            "step_hours": 3})
        data = r.json()
        self.assertEqual(data["bucket"], "series")
        self.assertTrue(data["points"])
        self.assertIn("BTC_USDT", data["symbols"])
        last = data["points"][-1]
        self.assertIn("liq_usd", last)
        self.assertIn("cvd", last)
        self.assertIn("vol", last)
        self.assertIn("BTC_USDT", last["sym"])

    def test_month_window_kept(self):
        """Месяц данных не обрезается: граница — TTL, а не сутки."""
        # 25 суток назад: и заведомо дальше суточного окна, и заведомо внутри
        # TTL (31 сутки) — событие обязано вернуться
        month_ago = NOW - 25 * 24 * HOUR
        path = server.HIST.shard_path(day_key(month_ago))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev(20, month_ago, "SOL_USDT", 70000)) + "\n")
        r = self.client.get("/api/history", params={
            "bucket": "raw", "hours": 30 * 24})
        ids = [e["id"] for e in r.json()["liquidations"]]
        self.assertIn("ev20", ids)

    def test_history_in_health(self):
        r = self.client.get("/api/health")
        cfg = r.json()["config"]
        self.assertEqual(cfg["history_ttl_hours"], 744)
        self.assertTrue(cfg["history"]["first_day"])
        self.assertEqual(cfg["oi_history_keep_min"], 744 * 60)
        # видно, сколько событий поднялось с диска на старте: если ноль —
        # дневной дайджест соберётся пустым
        self.assertIn("history_restore", cfg)
        self.assertIn("events", cfg["history_restore"])
        self.assertGreaterEqual(cfg["history"]["days_on_disk"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
