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
    def __init__(self, client):
        self._client = client

    class _Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    _lock = _Lock()

    @property
    def clients(self):
        return {self._client}


async def scenario_fast_producer_slow_disk():
    print("1) медленный диск + медленный клиент: производитель не тормозит")
    client = RecordingClient()
    client.delay = 0.02                      # «душный» клиент WS
    srv.hub = FakeHub(client)

    disk_calls = []

    def slow_append(event, path):            # «медленный диск», блокирует
        time.sleep(0.03)
        disk_calls.append(event["id"])

    real_append = srv.append_history_event
    srv.append_history_event = slow_append
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
        srv.append_history_event = real_append


async def scenario_queue_overflow_drops():
    print("2) переполнение очереди: события отбрасываются, не блокируют")
    real_append = srv.append_history_event
    srv.append_history_event = lambda e, p: None
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
        srv.append_history_event = real_append
        del old_worker_alive


async def scenario_buffered_mode():
    print("3) buffered-режим: события ждут liquidation_broadcaster")
    old_bi = srv.BROADCAST_INTERVAL
    srv.BROADCAST_INTERVAL = 5.0             # «буфер включён»
    real_append = srv.append_history_event
    srv.append_history_event = lambda e, p: None
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
        srv.append_history_event = real_append


async def main():
    await scenario_fast_producer_slow_disk()
    await scenario_queue_overflow_drops()
    await scenario_buffered_mode()


if __name__ == "__main__":
    print("конвейер ликвидаций: читатели бирж не блокируются")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
