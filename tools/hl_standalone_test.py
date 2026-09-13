"""
Изоляционный тест боевого HL-слушателя БЕЗ боевого сервера.

Матрица diag_hl v2 показала: голые клиенты (любой UA, любой таймаут) живут,
а боевой процесс умирает на ~4-й секунде. Этот скрипт запускает ТОТ ЖЕ
боевой корутиин (MarketFeed._hyperliquid_liquidations) в отдельном пустом
процессе, с той же сессией (UA + ClientTimeout(total=20)) и с тем же
порядком «REST universe → WS на той же сессии». Плюс контрольный голый
клиент. Сеть-расписание: результат скажет, где сидит убийца —

  ВАРИАНТ 1 умер  → воспроизводится вне сервера: баг в market_feed/сессии
                    (тогда чиню быстро, уже знаю где искать)
  ВАРИАНТ 1 жив   → убийца — взаимодействие внутри полного сервера
                    (обвязка/нагрузка); лечим разделением сессии/циркулем

Запуск (прод трогать не нужно, тест живёт рядом ~2.5 мин):
  /root/Licvid/venv/bin/python3 /root/Licvid/tools/hl_standalone_test.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import ClientWSTimeout

from market_feed import MarketFeed, hl_coin_map

HL_WS = "wss://api.hyperliquid.xyz/ws"
HL_REST = "https://api.hyperliquid.xyz"
HOLD = 45.0          # сколько держим боевой корутиин
CONTROL_HOLD = 20.0  # контрольный голый клиент


async def battle_bases():
    """Боевые базы монет: с сервера, при недоступности — majors."""
    bases = []
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("http://127.0.0.1:8000/api/symbols",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                syms = (await r.json()).get("symbols") or []
        bases = [str(x).split("_")[0].upper() for x in syms]
    except Exception as e:
        print(f"  (сервер не отдал символы: {e} — беру majors)")
    if not bases:
        bases = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP", "SUI", "LINK",
                 "AVAX", "NEAR", "ARB", "ADA", "BNB", "LTC", "DOT", "UNI",
                 "APT", "ENA", "WLD", "TIA"]
    return bases


async def variant1_battle_code(bases):
    """Точный боевой корутиин в пустом процессе, HOLD секунд."""
    print(f"\n=== ВАРИАНТ 1: боевой код (_hyperliquid_liquidations), "
          f"держу {HOLD:.0f}с")
    liq_count = {"n": 0}

    async def on_liq(ev):
        liq_count["n"] += 1
        if liq_count["n"] <= 5:
            print(f"    ликвидация: {ev['symbol']} ${ev['usd']:.0f}")

    async def on_price(*a):
        pass

    feed = MarketFeed(on_liquidation=on_liq, on_price=on_price, exchanges=[])
    # боевой профиль сессии: UA + общий таймаут (копия server.py)
    feed._session = aiohttp.ClientSession(
        headers={"User-Agent": "LiqScope-Terminal/4.1"},
        timeout=aiohttp.ClientTimeout(total=20))
    feed.symbols = [f"{b}_USDT" for b in bases]

    t0 = time.monotonic()
    task = asyncio.create_task(feed._hyperliquid_liquidations())
    death = None
    try:
        while time.monotonic() - t0 < HOLD:
            await asyncio.sleep(0.5)
            if task.done():
                death = "завершился раньше срока"
                break
    except asyncio.CancelledError:
        raise
    finally:
        life = time.monotonic() - t0
        if death is None:
            print(f"  [ВАРИАНТ 1] ЖИВО весь срок {life:.0f}с; "
                  f"событий ликвидаций: {liq_count['n']}; "
                  f"status={feed.status['hyperliquid'].as_dict()}")
        else:
            exc = task.exception() if task.done() else None
            print(f"  [ВАРИАНТ 1] УМЕР через {life:.1f}с: {death}; "
                  f"исключение: {exc!r}; "
                  f"last_error={feed.status['hyperliquid'].last_error[:160]}")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await feed._session.close()
    return death is None


async def variant2_bare_control(bases):
    """Голый клиент (копия выживавшей матрицы A) — контроль сети."""
    print(f"\n=== ВАРИАНТ 2: голый клиент (контроль), держу "
          f"{CONTROL_HOLD:.0f}с")
    async with aiohttp.ClientSession() as s:
        t0 = time.monotonic()
        try:
            ws = await s.ws_connect(HL_WS, heartbeat=None,
                                    timeout=ClientWSTimeout(ws_close=25))
        except Exception as e:
            print(f"  [ВАРИАНТ 2] рукопожатие FAIL: {type(e).__name__}: {e}")
            return False
        # universe отдельно (как в матрице — через эту же сессию, но REST
        # делаем ДО ws и на той же сессии для единообразия проверки)
        death = None
        msgs = 0
        try:
            for coin in bases[:24]:
                await ws.send_json({"method": "subscribe",
                                    "subscription": {"type": "trades",
                                                     "coin": coin}})
                await asyncio.sleep(0.3)
            end = time.monotonic() + CONTROL_HOLD
            while time.monotonic() < end:
                try:
                    msg = await ws.receive(timeout=1)
                except asyncio.TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    msgs += 1
                elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                  aiohttp.WSMsgType.CLOSING,
                                  aiohttp.WSMsgType.ERROR):
                    death = (f"{msg.type}, close_code={ws.close_code}, "
                             f"exc={ws.exception()!r}")
                    break
        finally:
            life = time.monotonic() - t0
            if death:
                print(f"  [ВАРИАНТ 2] УМЕР через {life:.1f}с: {death}; "
                      f"кадров {msgs}")
            else:
                print(f"  [ВАРИАНТ 2] ЖИВО весь срок {life:.0f}с; "
                      f"кадров {msgs}")
            try:
                await ws.close()
            except Exception:
                pass
    return death is None


async def main():
    bases = await battle_bases()
    print(f"базы монет ({len(bases)}): {', '.join(bases[:24])}...")
    ok1 = await variant1_battle_code(bases)
    ok2 = await variant2_bare_control(bases)
    print("\n=== ВЕРДИКТ:")
    if not ok1 and ok2:
        print("  боевой КОД умирает даже в изоляции → чиню market_feed "
              "(пришлите строку «[ВАРИАНТ 1] УМЕР...» целиком)")
    elif ok1 and ok2:
        print("  боевой код в изоляции ЖИВЕТ → убийца сидит в обвязке "
              "полного сервера (нужен следующий шаг: SOLO/разнос сессий)")
    elif not ok2:
        print("  даже голый клиент умер → сеть/HL сейчас сбрасывает всех "
              "с этого IP — подождать 30 мин и повторить")


if __name__ == "__main__":
    asyncio.run(main())
