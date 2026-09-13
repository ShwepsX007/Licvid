"""
Изоляционный A/B-прогон боевого HL-слушателя БЕЗ боевого сервера.

Зачем: боевой слушатель живёт внутри процесса с восемью биржами, REST-движками
и клиентами, и «на глаз» не отличить, кто именно рвёт Hyperliquid-соединение.
Этот скрипт запускает ТОТ ЖЕ боевой корутиин
(MarketFeed._hyperliquid_liquidations) в пустом процессе и прогоняет его в
двух конфигурациях сессии + голым клиентом-контролем:

  ВАРИАНТ 1  solo   — отдельная сессия только для HL, без общего
                      ClientTimeout(total=...) (текущий боевой дефолт)
  ВАРИАНТ 2  shared — боевая сессия (общий пул с REST/OI/свечами,
                      User-Agent + ClientTimeout(total=20)); старый профиль,
                      включается LIQSCOPE_HL_SHARED=1
  ВАРИАНТ 3  bare   — голый aiohttp-клиент: контроль сети/биржи

Расписание результатов:
  1 жив, 2 умер   → дело в общей сессии: держим solo (дефолт) и не трогаем
  1 умер, 3 жив   → баг в market_feed/обвязке: смотрите строку
                    «[ВАРИАНТ 1] УМЕР ...» целиком (в ней код закрытия и
                    причина от сторожа)
  3 умер          → биржа/сеть сейчас режет всех с этого IP: подождать
                    20-30 минут и повторить

Запуск (прод трогать не нужно, тест живёт рядом ~3 мин):
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

HL_WS = os.getenv("LIQSCOPE_HL_WS", "wss://api.hyperliquid.xyz/ws")
HL_REST = os.getenv("LIQSCOPE_HL_REST", "https://api.hyperliquid.xyz")
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


async def run_battle(label: str, bases, shared: bool):
    """Точный боевой корутиин в пустом процессе, HOLD секунд.

    shared=True воспроизводит прежний боевой профиль: HL ходит через общую
    сессию с User-Agent и ClientTimeout(total=20).
    """
    print(f"\n=== {label}: боевой код (_hyperliquid_liquidations), "
          f"держу {HOLD:.0f}с")
    os.environ["LIQSCOPE_HL_SHARED"] = "1" if shared else "0"
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
        diag = feed.status["hyperliquid"].as_dict()
    except asyncio.CancelledError:
        raise
    finally:
        life = time.monotonic() - t0
        if death is None:
            print(f"  [{label}] ЖИВО весь срок {life:.0f}с; "
                  f"ликвидаций: {liq_count['n']}")
        else:
            exc = task.exception() if task.done() else None
            print(f"  [{label}] УМЕР через {life:.1f}с: {death}; "
                  f"исключение: {exc!r}; "
                  f"last_error={feed.status['hyperliquid'].last_error[:200]}")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await feed._session.close()
        keys = ("connected", "events", "uptime_sec", "reconnects", "attempts",
                "hl_subs_total", "hl_subs_sent", "hl_subs_acked", "hl_pings",
                "hl_pongs", "hl_last_inbound_sec", "hl_skipped_stale")
        print("      диагностика: " +
              ", ".join(f"{k}={diag.get(k)}" for k in keys))
    return death is None


async def variant_bare_control(bases):
    """Голый клиент — контроль сети/биржи."""
    print(f"\n=== ВАРИАНТ 3 (bare): голый клиент, держу {CONTROL_HOLD:.0f}с")
    async with aiohttp.ClientSession() as s:
        t0 = time.monotonic()
        try:
            ws = await s.ws_connect(HL_WS, heartbeat=None,
                                    timeout=ClientWSTimeout(ws_close=25))
        except Exception as e:
            print(f"  [ВАРИАНТ 3] рукопожатие FAIL: {type(e).__name__}: {e}")
            return False
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
                print(f"  [ВАРИАНТ 3] УМЕР через {life:.1f}с: {death}; "
                      f"кадров {msgs}")
            else:
                print(f"  [ВАРИАНТ 3] ЖИВО весь срок {life:.0f}с; кадров {msgs}")
            try:
                await ws.close()
            except Exception:
                pass
    return death is None


async def universe_size():
    """Размер universe — заодно проверка, что REST до биржи вообще ходит."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{HL_REST}/info", json={"type": "meta"},
                              timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json(content_type=None)
        return len((data or {}).get("universe") or [])
    except Exception as e:
        print(f"  REST {HL_REST}/info недоступен: {type(e).__name__}: {e}")
        return 0


async def main():
    n = await universe_size()
    print(f"universe перпетуумов по REST: {n}")
    bases = await battle_bases()
    print(f"базы монет ({len(bases)}): {', '.join(bases[:24])}...")
    ok1 = await run_battle("ВАРИАНТ 1 (solo)", bases, shared=False)
    ok2 = await run_battle("ВАРИАНТ 2 (shared)", bases, shared=True)
    ok3 = await variant_bare_control(bases)
    print("\n=== ВЕРДИКТ:")
    if ok1 and not ok2:
        print("  общая сессия рвёт соединение, отдельная — нет: оставляем "
              "дефолт (solo), LIQSCOPE_HL_SHARED не включать")
    elif not ok1 and ok3:
        print("  боевой КОД умирает даже в изоляции → чиню market_feed "
              "(пришлите строку «[ВАРИАНТ 1] УМЕР...» целиком)")
    elif ok1 and ok2:
        print("  обе конфигурации живы → убийца сидит в обвязке полного "
              "сервера (нагрузка/клиенты); следующий шаг — смотреть "
              "journalctl в момент обрыва")
    elif not ok3:
        print("  даже голый клиент умер → сеть/HL сейчас сбрасывает всех "
              "с этого IP — подождать 30 мин и повторить")


if __name__ == "__main__":
    asyncio.run(main())
