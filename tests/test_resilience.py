"""
Живучесть обвязки потоков: супервайзер и рассылка клиентам.

Два механизма, от которых зависит «не висит ли терминал мёртвым»:

  1. Backoff супервайзера: короткоживущий «успешный» коннект больше не
     сбрасывает паузу к минимуму (иначе биржа, рвущая соединения пачкой,
     попадает в цикл долбёжки и не поднимается); честный сброс — только
     после соединения, прожившего >= stable_uptime. initial_delay отодвигает
     ПЕРВУЮ попытку, не подмешиваясь в ретраи.

  2. Зависший WS-клиент терминала: Client.send ограничен по времени и
     помечает клиента мёртвым вместо бесконечного drain(), подвешивавшего
     читателей бирж.

(Раньше здесь были ещё сценарии сторожа зомби-сокета и зонда --soak — они
проверяли слушатель Hyperliquid, удалённый из проекта: публичный канал
ликвидаций у этой биржи не отдаёт метку liquidation, замер 13.09.2026 —
18 144 сделки и 0 меток. История — в git.)

Сеть наружу не нужна.  Запуск:  python3 tests/test_resilience.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_feed
import server as srv
from market_feed import MarketFeed, SourceStatus

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


async def on_liq(ev):
    pass


async def noop_price(*a):
    pass


# --- 1) backoff супервайзера -------------------------------------------------

async def scenario_supervise_backoff():
    print("1) backoff: короткие «успехи» не сбрасывают паузу, долгий — сбрасывает")
    feed = MarketFeed(on_liquidation=on_liq, on_price=noop_price, exchanges=[])
    # «fakesrc» в списке боевых источников не значится — регистрируем статус
    # сами: _supervise с источником работает по имени, не по списку бирж.
    feed.status["fakesrc"] = SourceStatus("fakesrc")
    plan = [
        ("ok", 0.05),    # короткоживущий успех — раньше сбрасывал паузу
        ("ok", 0.05),
        ("ok", 0.05),
        ("ok", 0.45),    # долгий успех (>= stable_uptime) — честный сброс
        ("fail", 0.0),   # после него цикл завершается (ставит _stop)
    ]
    calls = {"n": 0}

    async def factory():
        i = calls["n"]
        calls["n"] += 1
        mode, life = plan[i]
        await asyncio.sleep(life)
        if mode == "fail" or i + 1 >= len(plan):
            feed._stop.set()
        if mode == "fail":
            raise RuntimeError(f"boom#{i}")

    sleeps = []

    class Shim:
        """Перехват asyncio.sleep внутри market_feed._supervise."""

        def __getattr__(self, name):
            return getattr(asyncio, name)

        @staticmethod
        async def sleep(d):
            sleeps.append(d)

    orig = market_feed.asyncio
    market_feed.asyncio = Shim()
    try:
        await asyncio.wait_for(
            feed._supervise("fakesrc", factory,
                            base_delay=0.1, stable_uptime=0.3), 10)
    finally:
        market_feed.asyncio = orig

    check("паузы копятся на короткоживущих успехах (0.1 → 0.16 → 0.256)",
          len(sleeps) >= 3 and sleeps[1] > sleeps[0] and sleeps[2] > sleeps[1],
          sleeps[:3])
    check("долгоживущий успех сбросил паузу к base",
          len(sleeps) >= 4 and abs(sleeps[3] - 0.1) < 1e-9, sleeps[:4])
    check("после сброса рост начинается заново",
          len(sleeps) >= 5 and sleeps[4] > sleeps[3], sleeps[:5])
    check("попытка учтена в статусе источника",
          feed.status["fakesrc"].attempts == len(plan), feed.status["fakesrc"].attempts)
    check("последний обрыв записан в last_error",
          "boom#" in feed.status["fakesrc"].last_error,
          feed.status["fakesrc"].last_error)

    # initial_delay: пауза перед первой попыткой, в ретраи не подмешивается
    feed._stop.clear()
    sleeps.clear()
    calls["n"] = 0

    async def factory_once():
        feed._stop.set()
        raise RuntimeError("once")

    market_feed.asyncio = Shim()
    try:
        await asyncio.wait_for(
            feed._supervise("fakesrc", factory_once,
                            base_delay=0.1, initial_delay=0.5), 10)
    finally:
        market_feed.asyncio = orig
    check("initial_delay ждёт до первой попытки (0.5, потом 0.1)",
          len(sleeps) >= 2 and abs(sleeps[0] - 0.5) < 1e-9
          and abs(sleeps[1] - 0.1) < 1e-9, sleeps[:2])
    feed._stop.clear()


# --- 2) таймаут отправки зависшему клиенту -----------------------------------

class SlowWS:
    def __init__(self, lag: float):
        self.lag = lag

    async def send_json(self, msg):
        await asyncio.sleep(self.lag)


async def scenario_client_send_timeout():
    print("2) зависший WS-клиент: send ограничен по времени, клиент мёртв")
    old = srv.Client.SEND_TIMEOUT
    srv.Client.SEND_TIMEOUT = 0.15
    try:
        c = srv.Client(SlowWS(2.0))
        t0 = time.monotonic()
        sent = await c.send({"t": 1})
        dt = time.monotonic() - t0
        check("send вернул False для зависшего клиента", sent is False)
        check("клиент помечен мёртвым", c.alive is False)
        check("не ждали lag отправщика, вернулись по таймауту",
              dt < 1.0, f"{dt:.2f}с")
        c2 = srv.Client(SlowWS(0.01))
        sent2 = await c2.send({"t": 1})
        check("живой клиент проходит как раньше",
              sent2 is True and c2.alive is True)
    finally:
        srv.Client.SEND_TIMEOUT = old


async def main():
    await scenario_supervise_backoff()
    await scenario_client_send_timeout()


if __name__ == "__main__":
    print("живучесть обвязки: супервайзер и рассылка клиентам")
    asyncio.run(main())
    print()
    print(f"итог: {ok} ок, {fail} ошибок")
    sys.exit(1 if fail else 0)
