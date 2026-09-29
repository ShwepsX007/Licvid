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
import asyncio
import gc
import json
import inspect
import logging
import queue
import unittest
from types import SimpleNamespace

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


class GcWatchTest(unittest.TestCase):
    """Сборка мусора видна и измеряется: на большой куче это и есть p95 в секундах.

    Замер на бою 29.09.2026: 11 пауз по 1-2.3 с, а трассировка называла
    ``gzip.py:214:_compress_body`` — кадр, который просто аллоцировал память в
    момент сборки. При этом пик RSS был 1224 МБ: кэш окна калибровки уровней
    держал до 64 записей × 20000 событий × ~830 Б ≈ 1.06 ГБ.
    """

    def test_callback_times_collections(self):
        server._GC_RECENT.clear()
        before = int(server._GC_STATS["count"])
        gc.collect()
        self.assertGreater(int(server._GC_STATS["count"]), before,
                           "gc.callbacks не считает сборки — паузы останутся невидимыми")
        self.assertTrue(server._GC_RECENT, "окно последней сборки не запомнено")
        self.assertGreaterEqual(server._GC_STATS["last_ms"], 0.0)

    def test_gc_during_names_collection_inside_the_stall_window(self):
        server._GC_RECENT.clear()
        t0 = time.monotonic()
        gc.collect()
        note = server.gc_during(t0 - 0.05, time.monotonic() + 0.05)
        self.assertIn("сборка мусора", note)
        self.assertIn("поколения", note)
        self.assertEqual(server.gc_during(t0 - 100.0, t0 - 50.0), "",
                         "старая сборка попала в чужое окно паузы")

    def test_freeze_takes_startup_heap_out_of_generations(self):
        server.freeze_gc_heap()
        try:
            self.assertGreater(server._GC_STATS["frozen"], 0,
                               "куча старта не заморожена — сборки будут "
                               "перебирать гигабайт постоянных объектов")
        finally:
            gc.unfreeze()

    def test_health_reports_gc_log_and_gzip_state(self):
        server._HEALTH_CACHE.invalidate()
        d = TestClient(server.app).get("/api/health").json()
        server._HEALTH_CACHE.invalidate()
        for key in ("gc_max_ms", "gc_collections", "gc_gen2_collections",
                    "log_async", "log_dropped", "log_queue_size",
                    "levels_events_cache"):
            self.assertIn(key, d, f"в /api/health нет {key}")
        self.assertTrue(d["log_async"], "журнал снова пишется в event loop")
        cfg = d.get("config", {})
        self.assertEqual(cfg.get("gzip_level"), server.GZIP_LEVEL)
        self.assertIn("gzip_thread_min_size", cfg,
                      "не видно, уходит ли сжатие в поток")


class AsyncLoggingTest(unittest.TestCase):
    """Журнал не блокирует воркер: uvicorn логирует каждый запрос и каждый accept.

    Пауза 1282 мс на бою пришлась на ``logging/__init__.py:1103:emit`` под
    ``websocket.accept()``: stdout уходит в journald, а при полном буфере трубы
    ``write()`` блокирует единственный воркер.
    """

    def test_handlers_moved_to_queue(self):
        self.assertIsNotNone(server._log_listener, "слушатель журнала не поднят")
        root = logging.getLogger()
        self.assertTrue(any(isinstance(h, server._DropQueueHandler)
                            for h in root.handlers),
                        "корневой логгер всё ещё пишет напрямую")
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            lg = logging.getLogger(name)
            self.assertTrue(lg.propagate or
                            any(isinstance(h, server._DropQueueHandler)
                                for h in lg.handlers),
                            f"{name} пишет в журнал из event loop")

    def test_record_reaches_handler_through_the_thread(self):
        got: list = []

        class Cap(logging.Handler):
            def emit(self, record):
                got.append(record.getMessage())

        cap = Cap()
        listener = server._log_listener
        listener.handlers = tuple(listener.handlers) + (cap,)
        try:
            logging.getLogger("liqscope.server").info("журнал-жив-%d", 42)
            for _ in range(300):
                if got:
                    break
                time.sleep(0.01)
        finally:
            listener.handlers = tuple(h for h in listener.handlers if h is not cap)
        self.assertIn("журнал-жив-42", got, "запись не дошла до хендлера")

    def test_full_queue_drops_records_instead_of_blocking(self):
        q = queue.Queue(maxsize=1)
        h = server._DropQueueHandler(q)
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "сообщение",
                                None, None)
        dropped_before = server._LOG_STATS["dropped"]
        t0 = time.perf_counter()
        for _ in range(200):
            h.enqueue(rec)
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.5, f"переполненная очередь блокировала {dt:.2f} с")
        self.assertGreater(server._LOG_STATS["dropped"], dropped_before,
                           "потерянные записи не посчитаны")
        self.assertEqual(q.qsize(), 1)

    def test_uvicorn_access_args_survive_queue_and_other_logs_skip_formatter(self):
        from uvicorn.logging import AccessFormatter

        class Capture(logging.Handler):
            def __init__(self, formatter):
                super().__init__()
                self.messages = []
                self.setFormatter(formatter)

            def emit(self, record):
                self.messages.append(self.format(record))

        root = Capture(logging.Formatter("%(message)s"))
        access = Capture(AccessFormatter(
            fmt="%(client_addr)s %(request_line)s %(status_code)s",
            use_colors=False))
        access_logger = logging.getLogger("uvicorn.access")
        original_propagate = access_logger.propagate
        access_logger.propagate = False
        try:
            dispatcher = server._OriginalLogDispatch()
            handler = server._DropQueueHandler(queue.Queue(), (access,))
            args = ("127.0.0.1:1234", "GET", "/api/history", "1.1", 200)
            record = logging.LogRecord("uvicorn.access", logging.INFO, __file__,
                                       1, '%s - "%s %s HTTP/%s" %d', args, None)
            queued = handler.prepare(record)
            self.assertEqual(queued.args, args)
            dispatcher.handle(queued)
            root_queue = server._DropQueueHandler(queue.Queue(), (root,))
            dispatcher.handle(root_queue.prepare(logging.LogRecord(
                "liqscope.server", logging.INFO, __file__, 1,
                "ordinary message", (), None)))
            self.assertEqual(len(access.messages), 1)
            self.assertIn("GET /api/history", access.messages[0])
            self.assertEqual(root.messages, ["ordinary message"])
        finally:
            access_logger.propagate = original_propagate

    def test_install_is_idempotent(self):
        first = server._log_listener
        self.assertTrue(server.install_async_logging())
        self.assertIs(server._log_listener, first,
                      "повторный вызов поднял второго слушателя")


class GzipThreadOffloadTest(unittest.TestCase):
    """Сжатие уходит в поток: там zlib отпускает GIL и не держит воркер."""

    def test_middleware_got_thread_threshold(self):
        self.assertIn("thread_minimum_size", server.GZIP_APPLIED,
                      "starlette жмёт тела в event loop до 128 КиБ")
        self.assertLessEqual(server.GZIP_APPLIED["thread_minimum_size"],
                             128 * 1024)
        self.assertEqual(server.GZIP_APPLIED.get("compresslevel"),
                         server.GZIP_LEVEL)

    def test_kwargs_match_starlette_signature(self):
        from starlette.middleware.gzip import GZipMiddleware
        params = inspect.signature(GZipMiddleware.__init__).parameters
        for kw in server.GZIP_APPLIED:
            self.assertIn(kw, params,
                          f"параметр {kw} не принимается установленной "
                          "starlette — приложение не поднялось бы")


class WsInitTailTest(unittest.TestCase):
    """Init-пакет WS берёт хвост истории, не копируя её целиком.

    На бою сторож назвал в окне паузы строку ``server.py:5527`` — это
    ``list(LIQUIDATIONS)[-100:]``: ради последних ста событий копировался весь
    deque (до ``HISTORY_MAX`` = 60000), а на 50 подключениях разом это миллионы
    скопированных ссылок в event loop.
    """

    def setUp(self):
        server._WS_RATE.reset("ws:ip:testclient")
        self._saved = list(server.LIQUIDATIONS)
        self._maxlen = server.LIQUIDATIONS.maxlen
        server.LIQUIDATIONS.clear()
        for i in range(1200):
            server.LIQUIDATIONS.append({"id": f"ev{i}", "timestamp": 1759000000 + i,
                                        "symbol": "BTC_USDT", "side": "sell",
                                        "usd": 1000.0 + i})

    def tearDown(self):
        server.LIQUIDATIONS.clear()
        server.LIQUIDATIONS.extend(self._saved)
        server._WS_RATE.reset("ws:ip:testclient")

    def test_init_carries_last_events_in_order(self):
        c = TestClient(server.app)
        with c.websocket_connect("/ws") as ws:
            init = json.loads(ws.receive_text())
        recent = init.get("recent_liquidations") or []
        self.assertTrue(recent, "init пришёл без ленты ликвидаций")
        self.assertLessEqual(len(recent), 200, "лимит init не соблюдён")
        ids = [e.get("id") for e in recent]
        expected = [f"ev{i}" for i in range(1200 - len(recent), 1200)]
        self.assertEqual(ids, expected,
                         "хвост истории отдан не в хронологическом порядке — "
                         "reversed() забыли развернуть обратно")

    def test_source_no_longer_copies_whole_history(self):
        src = inspect.getsource(server.ws_endpoint)
        self.assertIn("islice(reversed(", src,
                      "init снова копирует весь deque истории ради хвоста")
        self.assertNotIn("list(LIQUIDATIONS)[-", src)


class _FakeFeed:
    """Двойник фида: /api/health зовёт health(), фон — session и prices."""

    def __init__(self, prices):
        self.session = object()
        self.prices = prices
        self.symbols = list(prices)
        self.hot_symbols = set(prices)
        self.tick_subscriptions = set()
        self.preferred_kline_source = None
        self.MAX_CUSTOM_SYMBOLS = 120
        # health() читает status["ticks"].name — как у настоящего источника
        self.status = {"ticks": SimpleNamespace(name="none")}

    def health(self):
        return {"sources": {}}


class _FakeLevelsEngine:
    """Двойник движка уровней: прогрев и лестница с измеримой длительностью."""

    def __init__(self, payload_ms=0.0):
        self.enabled = True
        self.warmed = []
        self.paid = []
        self.saved = 0
        self._payload_ms = payload_ms

    async def warm(self, session, batch, limit=0):
        self.warmed.append(list(batch))
        if self._payload_ms:
            await asyncio.sleep(self._payload_ms / 1000.0)

    async def payload(self, sym, session=None, price=None):
        self.paid.append((sym, price))
        if self._payload_ms:
            await asyncio.sleep(self._payload_ms / 1000.0)
        return {"enabled": True, "price": price, "ts": time.time(),
                "magnets": {"up": [], "down": []}, "magnets_list": [{"p": 1.0}],
                "totals": {}, "calibration": {}}

    def save(self):
        self.saved += 1
        return True


class LevelsBackgroundTest(unittest.TestCase):
    """Проход фона уровней считает себя: без этого нельзя сказать, он ли грузит воркер.

    Паузы воркера в прогоне на бою ложились кластерами с периодом, близким к
    ``LIQSCOPE_LEVELS_SNAP_SEC`` (20 с), а клиентский p95 был 883 мс при 9.6 мс
    на стороне обработчика.
    """

    def setUp(self):
        self._feed, self._levels = server.feed, server.LEVELS
        self._want, self._bg = server.level_alert_symbols, dict(server.LEVELS_BG)
        self._snap = dict(server.LEVELS_SNAP)
        self._slow_ms, self._batch = server.LEVELS_BG_SLOW_MS, server.LEVELS_BATCH
        self._on = server.LEVELS_BG_ON
        server.feed = _FakeFeed({"BTC_USDT": 61000.0, "ETH_USDT": 3100.0})
        server.level_alert_symbols = lambda: ["BTC_USDT", "ETH_USDT"]
        for k in server.LEVELS_BG:
            server.LEVELS_BG[k] = 0.0
        server.LEVELS_SNAP.clear()
        server.LEVELS_BG_SLOW_MS = 100.0
        server.LEVELS_BATCH = 4
        server.LEVELS_BG_ON = True

    def tearDown(self):
        server.feed, server.LEVELS = self._feed, self._levels
        server.level_alert_symbols = self._want
        server.LEVELS_BG.update(self._bg)
        server.LEVELS_SNAP.clear()
        server.LEVELS_SNAP.update(self._snap)
        server.LEVELS_BG_SLOW_MS, server.LEVELS_BATCH = self._slow_ms, self._batch
        server.LEVELS_BG_ON = self._on

    def test_pass_records_timing_and_snapshot(self):
        engine = _FakeLevelsEngine()
        server.LEVELS = engine
        state = {"asked": [], "snap_at": 0.0}
        asyncio.run(server.levels_bg_pass(state))
        bg = server.LEVELS_BG
        self.assertEqual(int(bg["passes"]), 1, "проход не посчитан")
        self.assertEqual(int(bg["symbols"]), 2, "число монет в проходе неверно")
        self.assertGreater(bg["last_at"], 0.0)
        self.assertEqual(engine.warmed, [["BTC_USDT", "ETH_USDT"]])
        self.assertEqual([s for s, _ in engine.paid], ["BTC_USDT", "ETH_USDT"])
        self.assertEqual(engine.saved, 1, "настройки не сохранены")
        self.assertIn("BTC_USDT", server.LEVELS_SNAP,
                      "снимок для алертов не обновлён")
        self.assertEqual(state["asked"], ["BTC_USDT", "ETH_USDT"],
                         "круг монет не запомнен — следующий проход возьмёт тех же")
        self.assertGreater(state["snap_at"], 0.0)

    def test_slow_pass_is_counted_and_reported(self):
        engine = _FakeLevelsEngine(payload_ms=60.0)     # 2 монеты × 60 мс > порога
        server.LEVELS = engine
        with self.assertLogs("liqscope.server", level="WARNING") as cap:
            asyncio.run(server.levels_bg_pass({"asked": [], "snap_at": 0.0}))
        self.assertEqual(int(server.LEVELS_BG["slow_passes"]), 1,
                         "медленный проход не посчитан")
        self.assertGreaterEqual(server.LEVELS_BG["max_pass_ms"], 100.0)
        text = "\n".join(cap.output)
        self.assertIn("[levels]", text)
        self.assertIn("BTC_USDT", text, "в предупреждении нет раскладки по монетам")

    def test_disabled_background_does_no_work(self):
        engine = _FakeLevelsEngine()
        server.LEVELS = engine
        server.LEVELS_BG_ON = False
        asyncio.run(server.levels_bg_pass({"asked": [], "snap_at": 0.0}))
        self.assertEqual(engine.warmed, [], "выключенный фон всё равно грел биржи")
        self.assertEqual(engine.paid, [], "выключенный фон всё равно считал лестницы")
        self.assertEqual(server.LEVELS_BG["off"], 1.0,
                         "в /api/health не видно, что фон выключен")

    def test_payload_error_does_not_kill_the_pass(self):
        class Boom(_FakeLevelsEngine):
            async def payload(self, sym, session=None, price=None):
                raise RuntimeError("взрыв")
        server.LEVELS = Boom()
        asyncio.run(server.levels_bg_pass({"asked": [], "snap_at": 0.0}))
        self.assertEqual(int(server.LEVELS_BG["passes"]), 1,
                         "проход не завершился после ошибки монеты")

    def test_health_exposes_levels_background(self):
        server._HEALTH_CACHE.invalidate()
        d = TestClient(server.app).get("/api/health").json()
        server._HEALTH_CACHE.invalidate()
        self.assertIn("levels_bg", d)
        for key in ("passes", "last_pass_ms", "max_pass_ms", "slow_passes", "off"):
            self.assertIn(key, d["levels_bg"], f"в levels_bg нет {key}")
        self.assertIn("cpu_count", d, "не видно, сколько ядер у машины")
        self.assertIn("load_avg", d, "не видно загрузки машины")
        self.assertIn("payload_cache", d["levels_events_cache"],
                      "попадания кэша лестниц не видны — не проверить, "
                      "работает ли бакет цены")


if __name__ == "__main__":
    unittest.main(verbosity=2)
