"""Мониторинг и лимиты: /api/health, /api/metrics, RateLimitMiddleware.

Проверяем то, что держит многопользовательский режим:
* здоровье отдаёт RSS, отклик SQLite, WAL и кап сокетов;
* метрики считают запросы, статусы, p95 и сообщения сокетов;
* мутирующие POST режутся на 61-м запросе (429 + Retry-After);
* поддельный X-Forwarded-For от недоверенного клиента игнорируется;
* /ws пускает 5 подключений в минуту и закрывает 6-е кодом 1008;
* мониторинг (/api/health, /api/metrics) под лимит не попадает.
* поддельный liqscope_sid не открывает льготный бакет (те же 5 WS и 60 POST).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="ops_")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq_history.jsonl")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_SECRET"] = "test-secret-not-the-published-default"
os.environ["LIQSCOPE_ADMIN_IDS"] = "1001"
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_BOT_TOKEN"] = ""
# как в tools/run_tests.sh: тесты видят свежие ответы, а не кэш
os.environ["LIQSCOPE_API_CACHE"] = "0"

import server  # noqa: E402
import web_cache  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402


class HealthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)   # без lifespan: фид не поднимаем

    def test_health_has_ops_fields(self):
        d = self.client.get("/api/health").json()
        self.assertEqual(d["status"], "ok")
        self.assertIn("clients", d)
        self.assertEqual(d["ws_max_clients"], server.WS_MAX_CLIENTS)
        self.assertGreaterEqual(d["rss_bytes"], 0)
        self.assertGreaterEqual(d["wal_bytes"], 0)
        # база на месте — SELECT 1 проходит за миллисекунды
        self.assertGreaterEqual(d["sqlite_ms"], 0)
        self.assertGreaterEqual(d["uptime_sec"], 0)


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)

    def test_metrics_shape(self):
        d = self.client.get("/api/metrics").json()
        for key in ("ws_clients_total", "ws_max_clients",
                    "ws_connects_per_sec", "ws_messages_per_sec",
                    "http_requests_per_sec",
                    "http_requests_per_sec_by_status",
                    "http_latency_p95_ms",
                    "sqlite_wal_size_bytes", "sqlite_ms",
                    "symbols_count", "custom_symbols_count",
                    "rss_bytes", "uptime_sec"):
            self.assertIn(key, d, key)
        self.assertIsInstance(d["http_requests_per_sec_by_status"], dict)

    def test_counters_move(self):
        c = self.client
        c.get("/api/health")
        c.get("/api/health")
        c.get("/no-such-page")   # 404 тоже считается
        d = c.get("/api/metrics").json()
        by_status = d["http_requests_per_sec_by_status"]
        self.assertIn("200", by_status)
        self.assertIn("404", by_status)
        self.assertGreater(d["http_requests_per_sec"], 0)
        self.assertIsNotNone(d["http_latency_p95_ms"])
        self.assertGreaterEqual(d["http_latency_p95_ms"], 0)

    def test_monitoring_never_rate_limited(self):
        # даже после флуда мутирующих ручек мониторинг отвечает 200:
        # его дёргают UptimeRobot и скраперы, им 429 нельзя
        c = self.client
        for _ in range(5):
            self.assertEqual(c.get("/api/health").status_code, 200)
            self.assertEqual(c.get("/api/metrics").status_code, 200)


class RateLimitTest(unittest.TestCase):
    """60 мутирующих POST за 10 минут с IP, 61-й — 429.

    Бьём в несуществующую /api/-ручку: безвредно (404), своего лимита
    у неё нет, а middleware считает запрос до роутинга — как и положено
    считать сканеры.
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)

    def setUp(self):
        # бакеты глобальные на процесс — изолируем тесты друг от друга
        server._MUT_RATE.reset("mut:ip:testclient")

    def tearDown(self):
        server._MUT_RATE.reset("mut:ip:testclient")

    def test_mutating_limit_and_spoof_ignored(self):
        c = self.client
        codes = []
        # половина — с поддельным XFF: TestClient идёт напрямую
        # (недоверенный источник), так что все 60 падают в один ключ
        for i in range(30):
            r = c.post("/api/ops-probe-missing",
                       headers={"X-Forwarded-For": f"10.99.0.{i}"})
            codes.append(r.status_code)
        for _ in range(30):
            codes.append(c.post("/api/ops-probe-missing").status_code)
        self.assertTrue(all(x == 404 for x in codes),
                        f"первые 60 — мимо лимита: {sorted(set(codes))}")
        r = c.post("/api/ops-probe-missing")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json(), {"ok": False, "error": "rate"})
        self.assertEqual(r.headers.get("retry-after"), "60")

    def test_fake_cookie_gets_anon_bucket(self):
        # поддельный sid — те же 60 POST с IP и 429 на 61-м, а не 600
        c = self.client
        for _ in range(60):
            r = c.post("/api/ops-probe-missing",
                       cookies={"liqscope_sid": "fake123"})
            self.assertEqual(r.status_code, 404)
        r = c.post("/api/ops-probe-missing",
                   cookies={"liqscope_sid": "fake123"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers.get("retry-after"), "60")

    def test_get_is_not_limited(self):
        c = self.client
        for _ in range(10):
            self.assertIn(c.get("/api/stats").status_code, (200, 500))


class WsLimitTest(unittest.TestCase):
    def setUp(self):
        server._WS_RATE.reset("ws:ip:testclient")

    def tearDown(self):
        server._WS_RATE.reset("ws:ip:testclient")

    def test_ws_connect_rate(self):
        c = TestClient(server.app)
        ok = 0
        denied = []
        for _ in range(7):
            try:
                with c.websocket_connect("/ws") as ws:
                    ws.receive_text()   # init прилетел — соединение живое
                    ok += 1
            except WebSocketDisconnect as e:
                denied.append(e.code)
        self.assertEqual(ok, 5, "первые 5 подключений проходят")
        self.assertEqual(denied, [1008, 1008],
                         "6-е и 7-е закрыты лимитом (1008)")
        self.assertEqual(len(server.hub.clients), 0,
                         "все тестовые клиенты отключились")

    def test_fake_cookie_ws_stays_anon(self):
        c = TestClient(server.app)
        ok = 0
        denied = []
        jar = {"cookie": "liqscope_sid=fake123"}
        for _ in range(7):
            try:
                with c.websocket_connect("/ws", headers=jar) as ws:
                    ws.receive_text()   # init прилетел — соединение живое
                    ok += 1
            except WebSocketDisconnect as e:
                denied.append(e.code)
        self.assertEqual(ok, 5, "фейковый sid: первые 5 проходят")
        self.assertEqual(denied, [1008, 1008],
                         "6-е и 7-е закрыты лимитом (1008)")
        self.assertEqual(len(server.hub.clients), 0,
                         "все тестовые клиенты отключились")


class CacheTest(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("LIQSCOPE_API_CACHE")
        os.environ["LIQSCOPE_API_CACHE"] = "1"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("LIQSCOPE_API_CACHE", None)
        else:
            os.environ["LIQSCOPE_API_CACHE"] = self._prev

    def test_ttl(self):
        cache = web_cache.TTLCache(ttl=0.05, maxsize=4)
        self.assertIsNone(cache.get("k"))
        cache.set("k", {"v": 1})
        self.assertEqual(cache.get("k"), {"v": 1})
        time.sleep(0.06)
        self.assertIsNone(cache.get("k"))

    def test_env_disables(self):
        cache = web_cache.TTLCache(ttl=60.0)
        prev = os.environ.get("LIQSCOPE_API_CACHE")
        try:
            os.environ["LIQSCOPE_API_CACHE"] = "0"
            cache.set("k", 1)
            self.assertIsNone(cache.get("k"))
            os.environ["LIQSCOPE_API_CACHE"] = "1"
            cache.set("k", 1)
            self.assertEqual(cache.get("k"), 1)
        finally:
            if prev is None:
                os.environ.pop("LIQSCOPE_API_CACHE", None)
            else:
                os.environ["LIQSCOPE_API_CACHE"] = prev

    def test_invalidate(self):
        cache = web_cache.TTLCache(ttl=60.0)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate("a")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        cache.invalidate()
        self.assertIsNone(cache.get("b"))


class GzipCostTest(unittest.TestCase):
    """Сжатие ответов: дешёвый уровень, но сжатие по-прежнему работает.

    Замер на бою 29.09.2026 (100 rps мимо nginx) показал в окне паузы стек
    ``gzip.py:_compress_body <- … <- server.py:send_wrap``: starlette по
    умолчанию жмёт на уровне 9 — самом медленном, и на одном воркере это
    умножается на RPS. Основной gzip в бою делает nginx (``gzip_proxied any``,
    ``gzip_comp_level 5``), приложению остаются прямые заходы.
    """

    def test_level_is_cheap_and_configurable(self):
        self.assertEqual(server.GZIP_LEVEL, 1,
                         "уровень сжатия снова 9 — воркер будет жать ответы "
                         "вместо того, чтобы обслуживать запросы")
        self.assertGreaterEqual(server.GZIP_MIN_SIZE, 0)

    def test_middleware_got_the_level(self):
        gz = [m for m in server.app.user_middleware
              if "GZip" in getattr(m.cls, "__name__", "")]
        self.assertEqual(len(gz), 1, "GZipMiddleware не подключён")
        self.assertEqual(gz[0].kwargs.get("compresslevel"), server.GZIP_LEVEL)
        self.assertEqual(gz[0].kwargs.get("minimum_size"), server.GZIP_MIN_SIZE)

    def test_responses_are_still_compressed(self):
        c = TestClient(server.app)
        r = c.get("/api/health", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        # TestClient сам распаковывает тело: проверяем, что ответ целый
        body = r.json()
        self.assertEqual(body.get("status"), "ok")
        self.assertTrue(body.get("config", {}).get("fast_json"),
                        "orjson в ответах пропал")

    def test_level_1_is_far_cheaper_than_9(self):
        import gzip
        import json
        body = json.dumps({"rows": [{"symbol": f"S{i}", "clusters": [
            {"p": 61000 + j, "v": 100 + j} for j in range(40)]}
            for i in range(40)]}).encode()
        def cost(level, n=60):
            t0 = time.perf_counter()
            for _ in range(n):
                gzip.compress(body, compresslevel=level)
            return (time.perf_counter() - t0) / n * 1000
        c1, c9 = cost(1), cost(9)
        self.assertLess(c1, c9, f"уровень 1 ({c1:.2f} мс) не дешевле 9 ({c9:.2f} мс)")
        size1 = len(gzip.compress(body, compresslevel=1))
        size9 = len(gzip.compress(body, compresslevel=9))
        self.assertLess(size1, size9 * 1.25,
                        "уровень 1 даёт заметно больший ответ — трафик вырастет")


if __name__ == "__main__":
    unittest.main(verbosity=2)
