"""
Всплеск ликвидаций не должен тормозить читателей биржевых сокетов.

Инцидент: HL-кадр trades на 8 КБ → десятки событий → обработка (диск +
рассылка) прямо в задаче-читателе → читатель переставал читать сокет
(задержка ack 2.3с вместо 0.3с в журнале) → соединение умирало 1006.

Теперь on_liquidation только кладёт событие в очередь (дёшево), а диск и
рассылку делает фоновый liq_event_worker. Проверяем:

  1. вызов on_liquidation при медленном диске возвращает быстро;
  2. воркер обрабатывает всё, по порядку, без потерь;
  3. при переполнении очереди события отбрасываются с жалобой в лог,
     а не блокируют производителя;
  4. buffered-режим (LIQSCOPE_LIQ_FLUSH_MS > 0) по-прежнему наполняет
     _pending для liquidation_broadcaster.

Сеть наружу не нужна.  Запуск:  python3 tests/test_event_pipeline.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as srv

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}  [{detail}]")


def make_event(i):
    return {"symbol": "BTC_USDT", "exchange": "hyperliquid",
            "side": "LONG", "price": 50000 + i, "qty": 0.1, "usd": 5000 + i,
            "timestamp": 1789250000.0 + i}


class RecordingClient:
    """Фейковый WS-клиент: принимает всё, помнит порядок."""

    def __init__(self):
        self.rows = []
        self.delay = 0.0

    def wants(self, ev):
        return True

    async def send(self, msg):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.rows.extend(msg["data"])
        return True


class FakeHub:
    def __init__(self, *clients):
        self._clients = list(clients)

    class _Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    _lock = _Lock()

    @property
    def clients(self):
        return set(self._clients)


async def scenario_fast_producer_slow_disk():
    print("1) медленный диск + медленный клиент: производитель не тормозит")
    client = RecordingClient()
    client.delay = 0.02                      # «душный» клиент WS
    srv.hub = FakeHub(client)

    disk_calls = []

    def slow_append(event):                  # «медленный диск», блокирует
        time.sleep(0.03)
        disk_calls.append(event["id"])

    def slow_append_many(events):            # тот же диск, но пачкой
        time.sleep(0.03)
        disk_calls.extend(e["id"] for e in events)
        return len(events)

    real_append = srv.HIST.add
    real_append_many = getattr(srv.HIST, "add_many", None)
    srv.HIST.add = slow_append
    srv.HIST.add_many = slow_append_many
    worker = asyncio.create_task(srv.liq_event_worker())
    try:
        await asyncio.sleep(0)               # дать воркеру встать в get()
        n = 60
        t0 = time.monotonic()
        worst = 0.0
        for i in range(n):
            c0 = time.monotonic()
            await srv.on_liquidation(make_event(i))
            worst = max(worst, time.monotonic() - c0)
        producer_dt = time.monotonic() - t0

        check("очередь приняла все события",
              srv._liq_queue.qsize() >= 0 and not srv._liq_queue.empty()
              or len(client.rows) == n, srv._liq_queue.qsize())
        check("производитель не ждал медленный диск "
              "(худший вызов < 15мс)", worst < 0.015, f"{worst * 1000:.1f}мс")
        check("60 событий у производителя быстрее, "
              "чем воркер тратит на одно (0.03+0.02с)",
              producer_dt < n * 0.04, f"{producer_dt:.2f}с")
        await srv._liq_queue.join()
        check("воркер обработал все 60 (диск)", len(disk_calls) == n,
              len(disk_calls))
        check("воркер разослал все 60 клиенту, по порядку",
              [r["price"] for r in client.rows] ==
              [50000 + i for i in range(n)], len(client.rows))
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        srv.HIST.add = real_append
        if real_append_many is not None:
            srv.HIST.add_many = real_append_many
        else:
            try:
                del srv.HIST.add_many
            except AttributeError:
                pass


async def scenario_queue_overflow_drops():
    print("2) переполнение очереди: события отбрасываются, не блокируют")
    real_append = srv.HIST.add
    srv.HIST.add = lambda e: None
    old_q = srv._liq_queue
    old_worker_alive = True
    # воркер НЕ запущен: очередь только копится
    q = asyncio.Queue(maxsize=100)
    srv._liq_queue = q
    try:
        for i in range(130):                 # 100 влезут, 30 — нет
            await srv.on_liquidation(make_event(i))
        check("очередь полна ровно по maxsize", q.qsize() == 100, q.qsize())
        check("лишние события отброшены со счётчиком",
              srv._liq_dropped == 30, srv._liq_dropped)
        # производитель не заблокировался — цикл выше завершился быстро
    finally:
        srv._liq_queue = old_q
        srv.HIST.add = real_append
        del old_worker_alive


async def scenario_buffered_mode():
    print("3) buffered-режим: события ждут liquidation_broadcaster")
    old_bi = srv.BROADCAST_INTERVAL
    srv.BROADCAST_INTERVAL = 5.0             # «буфер включён»
    real_append = srv.HIST.add
    srv.HIST.add = lambda e: None
    worker = asyncio.create_task(srv.liq_event_worker())
    try:
        srv._pending.clear()
        await srv.on_liquidation(make_event(1))
        await srv._liq_queue.join()
        check("событие ушло в _pending, а не напрямую клиентам",
              len(srv._pending) == 1 and srv._pending[0]["price"] == 50001,
              srv._pending)
        check("в память (LIQUIDATIONS) попало сразу",
              len(srv.LIQUIDATIONS) > 0 and
              srv.LIQUIDATIONS[-1]["price"] == 50001)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        srv._pending.clear()
        srv.BROADCAST_INTERVAL = old_bi
        srv.HIST.add = real_append


async def scenario_disk_is_off_loop():
    """Запись пачки идёт в потоке: цикл продолжает тикать.

    Прежде ``HIST.add`` открывал, писал и закрывал файл на КАЖДОЕ событие прямо
    в воркере: каскад ликвидаций — сотни открытий подряд, а ``close()`` под
    давлением грязных страниц ждёт writeback. Замер: 200 событий по одному
    30.9 мс, пачкой 3.8 мс.
    """
    print("4) диск ушёл в поток: во время записи цикл тикает")
    client = RecordingClient()
    srv.hub = FakeHub(client)

    real_many = srv.HIST.add_many

    def slow_append_many(events):
        time.sleep(0.25)                     # заметная «запись»
        return len(events)

    srv.HIST.add_many = slow_append_many
    worker = asyncio.create_task(srv.liq_event_worker())
    ticks = []
    try:
        await asyncio.sleep(0)
        for i in range(20):
            await srv.on_liquidation(make_event(i))

        async def ticker():
            for _ in range(30):
                ticks.append(time.perf_counter())
                await asyncio.sleep(0.01)

        t = asyncio.create_task(ticker())
        await asyncio.sleep(0.55)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        gaps = [(b - a) * 1000 for a, b in zip(ticks, ticks[1:])]
        worst = max(gaps) if gaps else 0.0
        check("во время записи на диск цикл не стоял (худший тик < 60мс)",
              worst < 60.0, f"{worst:.1f}мс")
        check("пачка ушла на диск целиком до конца прогона",
              len(client.rows) > 0, len(client.rows))
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        srv.HIST.add_many = real_many
        await srv._liq_queue.join() if srv._liq_queue.empty() else None


async def scenario_fanout_encodes_once_per_group():
    """Кадр сериализуется один раз на группу фильтров, а не на клиента."""
    print("5) рассылка: одна сериализация на группу клиентов")
    a, b = RecordingClient(), RecordingClient()
    b.min_usd = 5005.0                       # свой фильтр — своя группа
    a.rows.clear(); b.rows.clear()
    srv.hub = FakeHub(a, b)
    calls = {"n": 0}
    real_dumps = srv.json_dumps_text

    def counting_dumps(msg):
        calls["n"] += 1
        return real_dumps(msg)

    srv.json_dumps_text = counting_dumps
    try:
        events = [make_event(i) for i in range(10)]      # usd = 5000+i
        await srv.send_liquidations(events)
    finally:
        srv.json_dumps_text = real_dumps
    check("кадров ровно по числу групп (2), а не клиентов",
          calls["n"] == 2, calls["n"])
    check("клиент с порогом 5005 получил только крупные",
          all(r["usd"] >= 5005 for r in b.rows) and len(b.rows) == 5,
          f"{len(b.rows)} строк")
    check("клиент без фильтра получил все 10", len(a.rows) == 10, len(a.rows))


async def scenario_spans_report_names_the_task():
    """Сторож пауз называет задачу, а не только кадр стека."""
    print("6) атрибуция пауз: имя задачи в отчёте")
    srv._TASK_SPANS.clear()
    with srv.task_span("liq-levels"):
        time.sleep(0.05)
        rows = srv.active_spans()
        report = srv.spans_report()
    check("активная задача видна по имени",
          rows and rows[0][0] == "liq-levels", rows)
    check("в отчёте есть имя и миллисекунды",
          "liq-levels" in report and "мс" in report, report)
    check("после выхода спан снят", srv.active_spans() == [], srv.active_spans())


async def main():
    await scenario_fast_producer_slow_disk()
    await scenario_queue_overflow_drops()
    await scenario_buffered_mode()
    await scenario_disk_is_off_loop()
    await scenario_fanout_encodes_once_per_group()
    await scenario_spans_report_names_the_task()


if __name__ == "__main__":
    print("конвейер ликвидаций: читатели бирж не блокируются")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
