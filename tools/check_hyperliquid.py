#!/usr/bin/env python3
"""Быстрый зонд Hyperliquid WS: одна монета, видно каждый шаг.

Показывает, на каком этапе умирает соединение: handshake, подписка,
ответы subscriptionResponse, поток trades — или всё живо. Каждая строка
печатается сразу с меткой времени, молчаливых ожиданий нет.

Запуск (питоном сервиса, где есть aiohttp):
    venv/bin/python3 tools/check_hyperliquid.py BTC 15
"""

import asyncio
import json
import sys
import time

import aiohttp

COIN = sys.argv[1] if len(sys.argv) > 1 else "BTC"
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
VERBOSE_FIRST = 12  # первые N сообщений — подробно, дальше — счётчики


async def main() -> None:
    t0 = time.time()

    def log(*a):
        print(f"[{time.time() - t0:6.2f}с]", *a, flush=True)

    log("connect wss://api.hyperliquid.xyz/ws ...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect("wss://api.hyperliquid.xyz/ws",
                                    timeout=15) as ws:
                log("handshake OK, шлю subscribe trades", COIN)
                await ws.send_json({"method": "subscribe",
                                    "subscription": {"type": "trades",
                                                     "coin": COIN}})
                log("подписка ушла, слушаю входящие...")
                n = 0
                trades = 0
                liqs = 0
                next_stat = t0 + 5.0
                while time.time() - t0 < DUR:
                    try:
                        msg = await ws.receive(timeout=max(
                            0.1, min(DUR - (time.time() - t0),
                                     next_stat - time.time())))
                    except asyncio.TimeoutError:
                        if time.time() >= next_stat:
                            log(f"... считаю молча: сообщений={n} "
                                f"сделок={trades} ликвидаций={liqs}")
                            next_stat = time.time() + 5.0
                            continue
                        break
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        n += 1
                        try:
                            p = json.loads(msg.data)
                        except Exception:
                            log(f"msg#{n} НЕ-JSON ({len(msg.data)} байт)")
                            continue
                        ch = p.get("channel", "?")
                        if ch == "trades":
                            data = p.get("data") or []
                            nl = sum(1 for t in data
                                     if isinstance(t, dict)
                                     and "liquidation" in t)
                            trades += len(data)
                            liqs += nl
                            if n <= VERBOSE_FIRST:
                                log(f"msg#{n} trades: сделок={len(data)} "
                                    f"ликвидаций={nl} ({len(msg.data)} байт)")
                        elif n <= VERBOSE_FIRST:
                            log(f"msg#{n} channel={ch} {msg.data[:160]}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        log(f"ВХОДЯЩИЙ {msg.type}: "
                            f"close_code={ws.close_code} "
                            f"exc={ws.exception()!r} "
                            f"data={str(msg.data)[:120]}")
                        break
                    else:
                        log(f"служебный {msg.type}")
                log(f"ИТОГ: сообщений={n} сделок={trades} ликвидаций={liqs} "
                    f"соединение={'живо' if not ws.closed else 'закрыто'}")
    except Exception as e:
        log(f"ИСКЛЮЧЕНИЕ: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
