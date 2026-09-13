#!/usr/bin/env python3
"""
Есть ли в ленте trades Hyperliquid ликвидационная метка.

Зачем: соединение с HL стабильно (подписки подтверждены, pong ходит), а
`events` в /api/health равен 0. Причины две, и их надо различить:

  А) ликвидаций просто не было за это время (тихий рынок) — тогда поток
     исправен и события появятся сами;
  Б) биржа больше не помечает ликвидации в публичном канале trades. В текущей
     документации HL тип WsTrade — это coin/side/px/sz/hash/time/tid/users,
     БЕЗ поля liquidation; объект liquidation описан только для WsFill
     (приватный поток userFills по адресу кошелька). Если это так, то
     слушатель не «сломан», а источник иссяк: нужен другой способ.

Скрипт слушает боевой набор монет и считает: сколько пришло сделок, сколько
из них содержат ключ `liquidation`, и какие вообще поля приходят в сделках.
По набору полей сразу видно, какой из двух вариантов перед нами.

Запуск:
    venv/bin/python3 tools/hl_liq_probe.py            # 180 секунд
    venv/bin/python3 tools/hl_liq_probe.py 600        # 10 минут
"""

import asyncio
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import ClientWSTimeout

from market_feed import HL_UA, hl_coin_map

HL_WS = os.getenv("LIQSCOPE_HL_WS", "wss://api.hyperliquid.xyz/ws")
HL_REST = os.getenv("LIQSCOPE_HL_REST", "https://api.hyperliquid.xyz")
SUB_GAP = 0.1


async def battle_coins(server):
    async with aiohttp.ClientSession(headers={"User-Agent": HL_UA}) as s:
        async with s.post(f"{HL_REST}/info", json={"type": "meta"},
                          timeout=aiohttp.ClientTimeout(total=15)) as r:
            meta = await r.json(content_type=None)
    universe = [str(row.get("name") or "").strip()
                for row in (meta or {}).get("universe") or [] if row.get("name")]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{server}/api/symbols",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                symbols = (await r.json()).get("symbols") or []
    except Exception:
        symbols = []
    if not symbols:
        symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "DOGE_USDT",
                   "HYPE_USDT", "SUI_USDT", "LINK_USDT", "AVAX_USDT", "NEAR_USDT"]
    return sorted(hl_coin_map(symbols, universe))


async def main():
    dur = 180.0
    server = "http://127.0.0.1:8000"
    for a in sys.argv[1:]:
        if a.startswith("http"):
            server = a
        else:
            try:
                dur = float(a)
            except ValueError:
                pass

    coins = await battle_coins(server)
    print(f"монет: {len(coins)} -> {', '.join(coins)}")
    print(f"слушаю {dur:.0f}с...\n")

    frames = 0
    trades = 0
    liqs = 0
    field_sets = Counter()
    sample_liq = None
    sample_plain = None
    per_coin = Counter()

    async with aiohttp.ClientSession(headers={"User-Agent": HL_UA}) as s:
        ws = await s.ws_connect(HL_WS, heartbeat=None,
                                timeout=ClientWSTimeout(ws_close=25))
        t0 = time.monotonic()
        try:
            for coin in coins:
                await ws.send_json({"method": "subscribe",
                                    "subscription": {"type": "trades",
                                                     "coin": coin}})
                await asyncio.sleep(SUB_GAP)
            next_report = t0 + 30
            while time.monotonic() - t0 < dur:
                try:
                    msg = await ws.receive(timeout=1)
                except asyncio.TimeoutError:
                    if time.monotonic() - t0 > 55:
                        await ws.send_json({"method": "ping"})
                    if time.monotonic() >= next_report:
                        print(f"  [{time.monotonic()-t0:5.0f}с] сделок={trades} "
                              f"с меткой liquidation={liqs}")
                        next_report += 30
                    continue
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                frames += 1
                try:
                    p = json.loads(msg.data)
                except Exception:
                    continue
                if p.get("channel") != "trades":
                    continue
                for t in p.get("data") or []:
                    if not isinstance(t, dict):
                        continue
                    trades += 1
                    field_sets[tuple(sorted(t.keys()))] += 1
                    per_coin[t.get("coin")] += 1
                    if "liquidation" in t:
                        liqs += 1
                        if sample_liq is None:
                            sample_liq = t
                    elif sample_plain is None:
                        sample_plain = t
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    print(f"\n=== ИТОГ за {dur:.0f}с")
    print(f"  кадров: {frames}, сделок: {trades}, "
          f"с объектом liquidation: {liqs}")
    print(f"  сделки по монетам (топ-10): {per_coin.most_common(10)}")
    print("  наборы полей в сделках:")
    for keys, n in field_sets.most_common(6):
        print(f"    {n:6d}x  {', '.join(keys)}")
    if sample_liq is not None:
        print(f"\n  пример сделки-ликвидации:\n    {json.dumps(sample_liq, ensure_ascii=False)[:500]}")
    if sample_plain is not None:
        print(f"\n  пример обычной сделки:\n    {json.dumps(sample_plain, ensure_ascii=False)[:500]}")

    print("\n=== ВЫВОД")
    if liqs:
        print(f"  метка liquidation в trades ЕСТЬ ({liqs} шт) — источник жив,"
              "\n  events в health должен расти; если там 0, смотрите фильтр"
              "\n  по символам и hl_skipped_stale.")
    elif trades:
        print(f"  сделок {trades}, а метки liquidation — НОЛЬ. Либо за это время"
              "\n  не было ни одной ликвидации (тихий рынок: повторите на 10-30 мин"
              "\n  или в момент резкого движения), либо биржа больше не помечает"
              "\n  ликвидации в публичном trades — тогда смотрите наборы полей выше:"
              "\n  если там нет ни liquidation, ни dir/startPosition, значит"
              "\n  публичный поток ликвидаций не отдаёт, и нужен другой источник.")
    else:
        print("  сделок не было вовсе — проверьте список монет и соединение.")


if __name__ == "__main__":
    asyncio.run(main())
