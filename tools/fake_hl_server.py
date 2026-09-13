#!/usr/bin/env python3
"""
Локальная псевдо-биржа Hyperliquid — посмотреть терминал без сети.

Отдаёт ровно то, что ждёт слушатель: `POST /info {"type":"meta"}` с universe
и WS `/ws` с каналом trades, где часть сделок помечена объектом `liquidation`
(как в документации HL: liquidatedUser/markPx/method), плюс отвечает
`{"channel":"pong"}` на `{"method":"ping"}` и подтверждает подписки.

Запуск (порт по умолчанию 8899):
    python3 tools/fake_hl_server.py
    python3 tools/fake_hl_server.py 8899 0.5     # порт, период ликвидаций

Терминал рядом:
    LIQSCOPE_HL_WS=http://127.0.0.1:8899/ws \
    LIQSCOPE_HL_REST=http://127.0.0.1:8899 \
    LIQSCOPE_DEMO=1 python3 -m uvicorn server:app --host 0.0.0.0 --port 8000

Всё, что придёт в ленту с такой «биржи», — синтетика: в шапке терминала
горят бейдж DEMO и биржа hyperliquid.
"""

import asyncio
import json
import random
import sys
import time

from aiohttp import web

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "SUI", "LINK", "AVAX",
         "NEAR", "ARB", "ADA", "UNI", "WLD", "ENA", "kPEPE", "ZEC", "TAO",
         "FIL", "PUMP", "REZ", "ARK", "BNB"]
PRICE = {"BTC": 68000.0, "ETH": 3400.0, "SOL": 170.0, "kPEPE": 0.011}


def px(coin: str) -> float:
    base = PRICE.get(coin, 2.5)
    return round(base * random.uniform(0.995, 1.005), 6)


async def info(request):
    return web.json_response({"universe": [{"name": c} for c in COINS]})


async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    every = float(request.app["liq_every"])
    coins = set()
    next_liq = time.monotonic() + every

    async def tape():
        nonlocal next_liq
        while not ws.closed:
            await asyncio.sleep(0.15)
            if not coins:
                continue
            coin = random.choice(sorted(coins))
            p = px(coin)
            row = {"coin": coin, "side": random.choice("AB"), "px": str(p),
                   "sz": str(round(random.uniform(0.01, 5), 4)),
                   "time": int(time.time() * 1000),
                   "hash": "0x%040x" % random.getrandbits(160),
                   "tid": random.getrandbits(40),
                   "users": ["0xmaker", "0xtaker"]}
            if time.monotonic() >= next_liq:
                next_liq = time.monotonic() + every
                row = {"coin": coin,
                       "side": random.choice("AB"),
                       "px": str(p), "sz": str(round(random.uniform(0.5, 40), 3)),
                       "time": int(time.time() * 1000),
                       "hash": "0x%040x" % random.getrandbits(160),
                       "tid": random.getrandbits(40),
                       "users": ["0xliqvault", "0xvictim"],
                       "liquidation": {"liquidatedUser": "0xvictim",
                                       "markPx": p, "method": "market"}}
            try:
                await ws.send_json({"channel": "trades", "data": [row]})
            except Exception:
                return

    task = asyncio.create_task(tape())
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                p = json.loads(msg.data)
            except Exception:
                continue
            if p.get("method") == "ping":
                await ws.send_json({"channel": "pong"})
            elif p.get("method") == "subscribe":
                sub = p.get("subscription") or {}
                if sub.get("coin") in COINS:
                    coins.add(sub["coin"])
                await ws.send_json({"channel": "subscriptionResponse",
                                    "data": {"method": p["method"],
                                             "subscription": sub}})
            elif p.get("method") == "unsubscribe":
                coins.discard((p.get("subscription") or {}).get("coin"))
    finally:
        task.cancel()
    return ws


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    every = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    app = web.Application()
    app["liq_every"] = every
    app.router.add_post("/info", info)
    app.router.add_get("/ws", ws_handler)
    print(f"псевдо-HL на http://127.0.0.1:{port} "
          f"(монет {len(COINS)}, ликвидация раз в {every}с)")
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
