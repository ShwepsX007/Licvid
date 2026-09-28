"""Раздача WS-кадров и паузы единственного воркера.

Что проверяем:
* ``Hub.broadcast`` сериализует кадр ОДИН раз на всех получателей — при 50
  зрителях прежняя схема (json.dumps внутри ``send`` каждого клиента) жгла
  50× того же CPU на единственном воркере и давала очереди на сотни мс;
* кадр уходит в **личную очередь** зрителя и пишется его же задачей: один
  забитый сокет больше не держит event loop (и все HTTP-запросы) до таймаута
  отправки — именно это давало p95 в секундах при серверном p95 в 6 мс;
* порядок кадров у каждого клиента сохранён (свой писатель, FIFO), а отставший
  сверх предела зритель отключается вместо того, чтобы есть память воркера;
* глубина очередей видна в мониторинге (``_ws_queue_stats``);
* мёртвый/медленный клиент помечается и выпадает из хаба, остальные получают
  кадр;
* сторож пауз event loop (``loop_lag_watchdog``) замечает, когда воркер
  надолго занят, и считает такие паузы;
* ``/api/health`` и ``/api/metrics`` отдают ``loop_lag_max_ms``/``loop_stalls``:
  клиентский p95 в секундах при серверном p95 в единицах мс означает очередь
  цикла, и это должно быть видно в мониторинге.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="fanout_")
os.environ.setdefault("LIQSCOPE_ACCOUNTS_DB", os.path.join(TMP, "accounts.db"))
os.environ.setdefault("LIQSCOPE_HISTORY_FILE", os.path.join(TMP, "liq.jsonl"))
os.environ.setdefault("LIQSCOPE_DIGEST_FILE", os.path.join(TMP, "digests.json"))
os.environ.setdefault("LIQSCOPE_MAIL_DIR", os.path.join(TMP, "mail"))
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
os.environ.setdefault("LIQSCOPE_DEMO", "0")
# лимиты соединений отключены: в бою их держит nginx, здесь они мешают тесту
os.environ.setdefault("LIQSCOPE_RATE_LIMIT", "0")
os.environ.setdefault("LIQSCOPE_WS_LIMIT", "0")
os.environ.setdefault("LIQSCOPE_WS_BURST", "0")

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeWS:
    """Двойник WebSocket: считает кадры и хранит их в порядке отправки."""

    def __init__(self, fail=False, slow=0.0):
        self.sent: list = []
        self.closed = False
        self._fail = fail
        self._slow = slow

    async def send_text(self, text):
        if self._slow:
            await asyncio.sleep(self._slow)
        if self._fail:
            raise OSError("client gone")
        self.sent.append(text)


def _client(**kw):
    return server.Client(FakeWS(**kw))


def _broadcast(hub, msg, predicate=None, drain=True):
    """Рассылка + дождаться, пока очереди клиентов уйдут в сокеты.

    Кадры теперь кладутся в личную очередь зрителя и пишутся его же задачей,
    поэтому проверка содержимого сокета — после drain.
    """
    async def run():
        await hub.broadcast(msg, predicate)
        if drain:
            await asyncio.gather(*[c.flush(2.0) for c in list(hub.clients)],
                                 return_exceptions=True)
    asyncio.run(run())


class BroadcastFanOut(unittest.TestCase):
    """Сериализация один раз на всех + порядок кадров."""

    def setUp(self):
        self.hub = server.Hub()
        self._real = server.json_dumps_text
        self.calls = 0

        def counting(obj, **kw):
            self.calls += 1
            return self._real(obj, **kw)

        server.json_dumps_text = counting
        self.addCleanup(lambda: setattr(server, "json_dumps_text", self._real))

    def test_frame_serialized_once_for_all_clients(self):
        n = 50
        self.hub.clients = {_client() for _ in range(n)}
        msg = {"type": "klines", "symbol": "BTCUSDT",
               "candles": [[i, i, i, i, i] for i in range(200)]}

        _broadcast(self.hub, msg)

        self.assertEqual(self.calls, 1,
                         f"кадр сериализован {self.calls} раз на {n} клиентов "
                         "— рассылка жжёт CPU пропорционально зрителям")
        for c in list(self.hub.clients):
            self.assertEqual(len(c.ws.sent), 1)
            self.assertIn('"BTCUSDT"', c.ws.sent[0])

    def test_single_client_also_serialized_once(self):
        self.hub.clients = {_client()}
        _broadcast(self.hub, {"type": "stats", "rows": [1, 2, 3]})
        self.assertEqual(self.calls, 1)

    def test_order_preserved_per_client(self):
        self.hub.clients = {_client(), _client()}

        async def run():
            for i in range(5):
                await self.hub.broadcast({"type": "tick", "n": i})
            await asyncio.gather(*[c.flush(2.0) for c in list(self.hub.clients)])

        asyncio.run(run())
        for c in list(self.hub.clients):
            got = [json.loads(f)["n"] for f in c.ws.sent]
            self.assertEqual(got, [0, 1, 2, 3, 4],
                             "клиент получил кадры вразнобой")

    def test_predicate_filters_targets(self):
        a, b = _client(), _client()
        b.symbol = "ETHUSDT"
        self.hub.clients = {a, b}
        _broadcast(self.hub, {"type": "liq", "symbol": "ETHUSDT"},
                   predicate=lambda c: c.symbol == "ETHUSDT")
        self.assertEqual(a.ws.sent, [])
        self.assertEqual(len(b.ws.sent), 1)

    def test_no_targets_no_serialization(self):
        _broadcast(self.hub, {"type": "stats"})
        self.assertEqual(self.calls, 0)

    def test_dead_client_pruned_others_served(self):
        """Упавший сокет помечается мёртвым в писателе и выпадает из хаба.

        С очередью рассылка больше не ждёт сокет: обрыв виден писателю, а хаб
        убирает клиента на следующем же кадре (``alive=False`` → ``offer``
        отказывает). Живые зрители получают кадр сразу, не дожидаясь смерти
        соседа.
        """
        bad = _client(fail=True)
        good = _client()
        self.hub.clients = {bad, good}

        async def run():
            await self.hub.broadcast({"type": "klines", "symbol": "BTCUSDT"})
            await asyncio.gather(bad.flush(1.0), good.flush(1.0),
                                 return_exceptions=True)
            await self.hub.broadcast({"type": "klines", "symbol": "BTCUSDT"})
            await good.flush(1.0)

        asyncio.run(run())
        self.assertFalse(bad.alive, "упавший сокет не помечен мёртвым")
        self.assertNotIn(bad, self.hub.clients, "мёртвый клиент остался в хабе")
        self.assertEqual(len(good.ws.sent), 2, "живой клиент не получил кадры")

    def test_broken_client_does_not_stop_fanout(self):
        """Один сломанный сокет не должен обрывать рассылку остальным.

        На единственном воркере исключение в send одного клиента стоило бы
        свечей и ликвидаций всем зрителям: рассылка изолирует отказ и
        выкидывает виновника.
        """
        class Boom:
            alive = True
            ws = FakeWS()

            async def send(self, msg):        # старая сигнатура — упадёт на text=
                raise TypeError("send() got an unexpected keyword 'text'")

        boom, good = Boom(), _client()
        self.hub.clients = {boom, good}
        _broadcast(self.hub, {"type": "klines", "symbol": "BTCUSDT"})
        self.assertEqual(len(good.ws.sent), 1,
                         "из-за одного сломанного клиента кадр не дошёл до живых")
        self.assertNotIn(boom, self.hub.clients, "сломавшийся клиент остался в хабе")

    def test_send_serializes_only_when_no_frame_given(self):
        c = _client()
        asyncio.run(c.send({"type": "init", "ok": True}))
        self.assertEqual(self.calls, 1)
        asyncio.run(c.send({"type": "init", "ok": True}, text='{"ready":true}'))
        self.assertEqual(self.calls, 1, "готовый кадр сериализован повторно")
        self.assertEqual(c.ws.sent[-1], '{"ready":true}')


class OutboundQueue(unittest.TestCase):
    """Личная очередь кадров: медленный зритель не держит воркер и остальных."""

    def setUp(self):
        self.hub = server.Hub()

    def test_slow_client_does_not_delay_others(self):
        """Главный симптом боя: один забитый сокет делал p95 секундами.

        Рассылка ждала ``send_text`` каждого зрителя по очереди, и клиент с
        полным TCP-буфером держал event loop до ``SEND_TIMEOUT`` (5 с) — всё
        это время HTTP-запросы стояли в очереди цикла при серверном p95 в
        единицах миллисекунд.
        """
        stuck = _client(slow=2.0)          # писатель этого клиента вязнет
        fast = [_client() for _ in range(20)]
        self.hub.clients = {stuck, *fast}

        async def run():
            t0 = time.perf_counter()
            await self.hub.broadcast({"type": "liq", "symbol": "BTCUSDT"})
            fanout = (time.perf_counter() - t0) * 1000.0
            await asyncio.gather(*[c.flush(1.0) for c in fast])
            delivered = (time.perf_counter() - t0) * 1000.0
            return fanout, delivered

        fanout, delivered = asyncio.run(run())

        self.assertLess(fanout, 200.0,
                        f"рассылка ждала сокет клиента: {fanout:.0f} мс")
        self.assertLess(delivered, 1000.0,
                        f"живые клиенты получили кадр поздно: {delivered:.0f} мс")
        for c in fast:
            self.assertEqual(len(c.ws.sent), 1, "кадр не дошёл до живого зрителя")
        self.assertEqual(stuck.ws.sent, [], "залипший клиент уже всё получил — "
                                            "значит, рассылка его дожидалась")

    def test_overdue_client_dropped_on_queue_overflow(self):
        c = _client(slow=5.0)
        self.hub.clients = {c}

        async def run():
            for i in range(server.Client.OUT_MAX_FRAMES + 5):
                await self.hub.broadcast({"type": "tick", "n": i})
            return list(self.hub.clients)

        left = asyncio.run(run())
        self.assertNotIn(c, left, "безнадёжно отставший зритель остался в хабе")
        self.assertFalse(c.alive)
        self.assertGreater(c.dropped_frames, 0)

    def test_queue_holds_frames_until_writer_catches_up(self):
        c = _client(slow=0.05)
        self.hub.clients = {c}

        async def run():
            for i in range(3):
                await self.hub.broadcast({"type": "tick", "n": i})
            self.assertGreater(len(c.out) + len(c.ws.sent), 0)
            ok = await c.flush(3.0)
            return ok

        self.assertTrue(asyncio.run(run()), "очередь не опустела")
        got = [json.loads(f)["n"] for f in c.ws.sent]
        self.assertEqual(got, [0, 1, 2], "порядок кадров у клиента нарушен")

    def test_dead_client_refuses_frames(self):
        c = _client()
        c.alive = False
        self.assertFalse(c.offer('{"type":"tick"}'))
        self.assertEqual(len(c.out), 0)

    def test_bytes_cap_counts_payload(self):
        c = _client(slow=5.0)
        big = "x" * (server.Client.OUT_MAX_BYTES // 2)
        self.assertTrue(c.offer(big))
        self.assertTrue(c.offer(big))
        self.assertFalse(c.offer(big), "очередь по байтам не ограничена")
        self.assertEqual(c.out_bytes, 0, "после сброса байты не обнулены")

    def test_remove_stops_writer(self):
        c = _client()
        self.hub.clients = {c}

        async def run():
            c.start_pump()
            task = c._pump_task
            await self.hub.remove(c)
            return task

        task = asyncio.run(run())
        self.assertTrue(task is None or task.done(),
                        "писатель очереди продолжает жить после remove")

    def test_queue_depth_visible_in_metrics(self):
        c = _client(slow=5.0)
        self.hub.clients = {c}
        for i in range(15):
            c.offer(json.dumps({"type": "tick", "n": i}))
        st = server._ws_queue_stats(self.hub.clients)
        self.assertEqual(st["ws_send_queue_frames"], 15)
        self.assertEqual(st["ws_send_queue_worst_frames"], 15)
        self.assertEqual(st["ws_slow_clients"], 1,
                         "отстающий клиент не виден в мониторинге")
        self.assertGreater(st["ws_send_queue_bytes"], 0)


class LoopLagWatchdog(unittest.TestCase):
    """Сторож пауз цикла: воркер занят — пауза видна в метриках."""

    def setUp(self):
        self._lag = dict(server._LOOP_LAG)
        self._warn = server.LOOP_LAG_WARN_MS
        self.addCleanup(self._restore)

    def _restore(self):
        server._LOOP_LAG.clear()
        server._LOOP_LAG.update(self._lag)
        server.LOOP_LAG_WARN_MS = self._warn

    def test_stall_detected_and_counted(self):
        server._LOOP_LAG.clear()
        server._LOOP_LAG.update({"max_ms": 0.0, "at": 0.0, "stalls": 0.0,
                                 "last_ms": 0.0, "logged_at": 0.0})
        server.LOOP_LAG_WARN_MS = 50.0

        async def run():
            wd = asyncio.create_task(server.loop_lag_watchdog())
            hog = asyncio.create_task(self._block_loop())
            await asyncio.sleep(1.0)
            hog.cancel()
            wd.cancel()
            for t in (wd, hog):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        async def _run():
            await run()

        asyncio.run(_run())

        self.assertGreaterEqual(server._LOOP_LAG["stalls"], 1,
                                "пауза воркера не засчитана")
        self.assertGreaterEqual(server._LOOP_LAG["max_ms"], 50.0)
        self.assertGreater(server._LOOP_LAG["at"], 0)

    @staticmethod
    async def _block_loop():
        # синхронная работа внутри цикла — ровно то, что роняет p95 клиентам
        for _ in range(6):
            time.sleep(0.12)
            await asyncio.sleep(0)

    def test_idle_loop_reports_small_lag(self):
        server._LOOP_LAG.clear()
        server._LOOP_LAG.update({"max_ms": 0.0, "at": 0.0, "stalls": 0.0,
                                 "last_ms": 0.0, "logged_at": 0.0})
        server.LOOP_LAG_WARN_MS = 5000.0

        async def run():
            wd = asyncio.create_task(server.loop_lag_watchdog())
            await asyncio.sleep(0.7)
            wd.cancel()
            try:
                await wd
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        self.assertEqual(server._LOOP_LAG["stalls"], 0,
                         "свободный цикл посчитан залипшим")


def _busy_work():
    """Синхронная работа, которую сэмплер стеков обязан назвать по имени."""
    time.sleep(0.8)


class LoopTrace(unittest.TestCase):
    """Сэмплер стеков: сторож говорит «занят 857 мс», трассировка — КЕМ."""

    def setUp(self):
        self._saved = dict(server._LOOP_TRACE)
        server._LOOP_TRACE.update({"held_ms": 0.0, "stalls": 0, "last": "",
                                   "stop": False, "thread": None})
        self.addCleanup(self._restore)

    def _restore(self):
        server._LOOP_TRACE["stop"] = True
        server._LOOP_TRACE.clear()
        server._LOOP_TRACE.update(self._saved)

    def test_trace_names_the_blocking_frame(self):
        import threading
        t = threading.Thread(target=server._loop_trace_thread,
                             args=(threading.get_ident(),), daemon=True)
        t.start()
        try:
            _busy_work()
        finally:
            server._LOOP_TRACE["stop"] = True
            t.join(timeout=2.0)
        self.assertGreaterEqual(server._LOOP_TRACE["stalls"], 1,
                                "сэмплер не заметил синхронную блокировку")
        self.assertIn("_busy_work", server._LOOP_TRACE["last"],
                      f"стек не назвал виновника: {server._LOOP_TRACE['last']!r}")
        self.assertGreaterEqual(server._LOOP_TRACE["held_ms"], 400.0)

    def test_idle_loop_stack_is_not_busy(self):
        idle = ["selectors.py:468:select", "base_events.py:1910:_run_once",
                "runners.py:118:run"]
        busy = ["server.py:2340:pump_loop", "base_events.py:1910:_run_once"]
        self.assertTrue(server._stack_is_idle(idle),
                        "ожидание epoll принято за занятость — лог утонет")
        self.assertFalse(server._stack_is_idle(busy))

    def test_real_idle_stack_under_uvicorn_is_idle(self):
        """Боевой простой: под механикой цикла лежит обвязка uvicorn/click.

        Регрессия на ложные срабатывания: правило «весь стек — asyncio»
        считало такой стек занятостью и писало в журнал каждые 300 мс при
        совершенно свободном воркере.
        """
        sig = ["selectors.py:468:select", "base_events.py:1910:_run_once",
               "base_events.py:603:run_forever",
               "base_events.py:631:run_until_complete", "runners.py:118:run",
               "_compat.py:30:asyncio_run", "server.py:86:run",
               "main.py:632:run", "main.py:448:main", "core.py:910:invoke",
               "core.py:1552:main", "__main__.py:4:<module>",
               "<frozen runpy>:88:_run_code"]
        self.assertTrue(server._stack_is_idle(sig),
                        "обвязка uvicorn под циклом принята за занятость")

    def test_uvloop_idle_stack_is_idle(self):
        """uvloop (его ставит uvicorn[standard]) прячет механику цикла в Cython.

        В python-стеке простоя самый внутренний кадр — ``runners.py:run``, а не
        ``selectors.select``: прежнее правило считало это занятостью и писало в
        журнал каждые 150-300 мс при свободном воркере.
        """
        sig = ["runners.py:118:run", "_compat.py:30:asyncio_run",
               "server.py:86:run", "main.py:632:run", "main.py:448:main",
               "core.py:910:invoke", "core.py:1552:main",
               "__main__.py:4:<module>", "<frozen runpy>:88:_run_code"]
        self.assertTrue(server._stack_is_idle(sig),
                        "простой под uvloop принят за занятость")
        self.assertEqual(server._stack_work(sig), [])

    def test_uvloop_busy_stack_names_app_frames(self):
        sig = ["pump_scan.py:135:add_prices", "server.py:2331:pump_loop",
               "runners.py:118:run", "_compat.py:30:asyncio_run",
               "main.py:632:run", "core.py:910:invoke"]
        self.assertFalse(server._stack_is_idle(sig))
        self.assertEqual(server._stack_work(sig),
                         ["pump_scan.py:135:add_prices", "server.py:2331:pump_loop"],
                         "в отчет должна попасть работа, а не обвязка uvicorn")

    def test_stack_without_boundary_counts_as_work(self):
        sig = ["server.py:120:handler", "market_feed.py:40:parse"]
        self.assertEqual(server._stack_work(sig), sig)
        self.assertFalse(server._stack_is_idle(sig))

    def test_busy_stack_under_uvicorn_is_busy(self):
        sig = ["pump_scan.py:135:add_prices", "server.py:2331:pump_loop",
               "base_events.py:1910:_run_once", "runners.py:118:run",
               "main.py:632:run", "core.py:910:invoke"]
        self.assertFalse(server._stack_is_idle(sig),
                         "настоящая работа признана простоем — виновника не увидим")

    def test_stack_signature_is_innermost_first(self):
        def outer():
            return inner()

        def inner():
            return server._stack_signature(sys._getframe())

        sig = outer()
        self.assertIn("inner", sig[0], "первым должен быть кадр, где застряли")
        self.assertTrue(any("outer" in f for f in sig[1:]), "кто позвал — не видно")
        self.assertLessEqual(len(sig), server.LOOP_TRACE_FRAMES)

    def test_start_is_noop_when_disabled(self):
        saved = server.LOOP_TRACE
        server.LOOP_TRACE = False
        self.addCleanup(lambda: setattr(server, "LOOP_TRACE", saved))
        server.loop_trace_start()
        self.assertIsNone(server._LOOP_TRACE["thread"],
                          "трассировка запустилась без LIQSCOPE_LOOP_TRACE=1")
        server.loop_trace_stop()          # не падает без потока


class AsyncioDebugFlag(unittest.TestCase):
    """PYTHONASYNCIODEBUG под uvicorn не срабатывает — включаем из приложения."""

    def test_flag_enables_loop_debug_and_threshold(self):
        saved = server.ASYNCIO_DEBUG, server.SLOW_CALLBACK_SEC
        server.ASYNCIO_DEBUG, server.SLOW_CALLBACK_SEC = True, 0.25
        self.addCleanup(lambda: (setattr(server, "ASYNCIO_DEBUG", saved[0]),
                                 setattr(server, "SLOW_CALLBACK_SEC", saved[1])))

        async def run():
            server._enable_asyncio_debug()
            loop = asyncio.get_running_loop()
            return loop.get_debug(), loop.slow_callback_duration

        dbg, dur = asyncio.run(run())
        self.assertTrue(dbg, "debug-режим asyncio не включился")
        self.assertEqual(dur, 0.25)

    def test_disabled_by_default(self):
        saved = server.ASYNCIO_DEBUG
        server.ASYNCIO_DEBUG = False
        self.addCleanup(lambda: setattr(server, "ASYNCIO_DEBUG", saved))

        async def run():
            server._enable_asyncio_debug()
            return asyncio.get_running_loop().get_debug()

        self.assertFalse(asyncio.run(run()))


class LagExposedInMonitoring(unittest.TestCase):
    """Паузы воркера видны в /api/health и /api/metrics."""

    def test_health_and_metrics_carry_loop_lag(self):
        saved = dict(server._LOOP_LAG)
        server._LOOP_LAG.update({"max_ms": 1234.5, "at": time.time(),
                                 "stalls": 7.0, "last_ms": 12.0,
                                 "logged_at": 0.0})
        self.addCleanup(lambda: (server._LOOP_LAG.clear(),
                                 server._LOOP_LAG.update(saved)))
        c = TestClient(server.app)
        for path in ("/api/health", "/api/metrics"):
            with self.subTest(path=path):
                self.assertEqual(c.get(path).status_code, 200)
                d = c.get(path).json()
                self.assertEqual(d.get("loop_lag_max_ms"), 1234.5,
                                 f"{path}: нет loop_lag_max_ms")
                self.assertEqual(d.get("loop_stalls"), 7, f"{path}: нет loop_stalls")
                self.assertIn("loop_lag_last_ms", d, f"{path}: нет last_ms")
                self.assertIn("loop_trace", d, f"{path}: нет флага трассировки")
                self.assertIn("loop_trace_last", d, f"{path}: нет стека виновника")
                self.assertIsInstance(d.get("loop_trace_last"), str)


if __name__ == "__main__":
    unittest.main()
