#!/usr/bin/env python3
"""Зонд Hyperliquid WS. Два режима:

1) watch (одна монета, поток сделок ~N секунд):
     venv/bin/python3 tools/check_hyperliquid.py BTC 15

2) bisect (пошаговые подписки ровно на те монеты, что и боевой слушатель;
   показывает, на какой по счёту/какой именно подписке умирает соединение):
     venv/bin/python3 tools/check_hyperliquid.py --bisect
     venv/bin/python3 tools/check_hyperliquid.py --bisect http://127.0.0.1:8000

3) диагностика убийцы соединения (два прогона, ~35 секунд, рестарт не нужен):
     venv/bin/python3 tools/check_hyperliquid.py BTC 20 --ua "LiqScope-Terminal/4.1"
     venv/bin/python3 tools/check_hyperliquid.py --burst
   Первый — одна монета, но с User-Agent боевого кода; второй — все 23
   подписки мгновенной пачкой, но со стандартным UA. Какой умрёт — тот
   фактор и убивает (кастомный UA режет WAF / всплеск режет лимитер).
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402

from market_feed import hl_coin_map  # noqa: E402

HL_WS = "wss://api.hyperliquid.xyz/ws"
HL_REST = "https://api.hyperliquid.xyz"
VERBOSE_FIRST = 12  # первые N сообщений watch — подробно, дальше — счётчики
SUB_GAP = 0.4       # пауза между подписками в bisect
RESP_WAIT = 2.5     # сколько ждать subscriptionResponse на каждую монету


async def watch(coin: str, dur: float, ua: str = "") -> None:
    t0 = time.time()

    def log(*a):
        print(f"[{time.time() - t0:6.2f}с]", *a, flush=True)

    headers = {"User-Agent": ua} if ua else None
    if ua:
        log(f"User-Agent: {ua}")
    log("connect", HL_WS, "...")
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.ws_connect(HL_WS, timeout=15) as ws:
                log("handshake OK, шлю subscribe trades", coin)
                await ws.send_json({"method": "subscribe",
                                    "subscription": {"type": "trades",
                                                     "coin": coin}})
                log("подписка ушла, слушаю входящие...")
                n = 0
                trades = 0
                liqs = 0
                next_stat = t0 + 5.0
                while time.time() - t0 < dur:
                    try:
                        msg = await ws.receive(timeout=max(
                            0.1, min(dur - (time.time() - t0),
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


async def fetch_production_map(server: str):
    """Монеты боевого слушателя: топ-40 сервера × universe биржи."""
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(f"{server}/api/symbols",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                js = await r.json()
        except Exception as e:
            print(f"сервер {server} не отдал /api/symbols: {e}")
            print("подставлю встроенный топ-20 majors")
            symbols = [f"{c}_USDT" for c in
                       ("BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP", "SUI",
                        "LINK", "AVAX", "NEAR", "ARB", "OP", "APT", "TAO",
                        "INJ", "DOT", "ADA", "LTC", "BCH", "PEPE")]
        else:
            symbols = js.get("symbols") or []
        print(f"символов сервера: {len(symbols)}")
        try:
            async with s.post(f"{HL_REST}/info", json={"type": "meta"},
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                meta = await r.json()
        except Exception as e:
            print(f"HL REST /info недоступен: {e}")
            return {}
        universe = [u.get("name") for u in (meta.get("universe") or [])
                    if u.get("name")]
        print(f"universe перпетуумов: {len(universe)}")
        return hl_coin_map(symbols, universe)


async def bisect(server: str) -> None:
    coin_map = await fetch_production_map(server)
    coins = sorted(coin_map)
    print(f"батарея подписок ({len(coins)}): {', '.join(coins)}")
    if not coins:
        print("подписываться не на что — выхожу")
        return
    t0 = time.time()

    def log(*a):
        print(f"[{time.time() - t0:6.2f}с]", *a, flush=True)

    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(HL_WS, timeout=15) as ws:
                log("handshake OK, подписываюсь по одной...")
                alive = 0
                for i, coin in enumerate(coins, 1):
                    tag = f"sub#{i:02d}/{len(coins)} {coin}"
                    try:
                        await ws.send_json(
                            {"method": "subscribe",
                             "subscription": {"type": "trades", "coin": coin}})
                    except Exception as e:
                        log(f"{tag} ... СМЕРТЬ НА ОТПРАВКЕ: "
                            f"{type(e).__name__}: {e}")
                        break
                    sent = time.time()
                    ok = False
                    passing = 0
                    while time.time() - sent < RESP_WAIT:
                        try:
                            msg = await ws.receive(
                                timeout=max(0.1, RESP_WAIT - (time.time() - sent)))
                        except asyncio.TimeoutError:
                            break
                        if msg.type is aiohttp.WSMsgType.TEXT:
                            try:
                                p = json.loads(msg.data)
                            except Exception:
                                continue
                            if p.get("channel") == "trades":
                                passing += len(p.get("data") or [])
                            elif (p.get("channel") == "subscriptionResponse"
                                  and (p.get("data") or {}).get(
                                      "subscription", {}).get("coin") == coin):
                                ok = True
                                break
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.CLOSING,
                                          aiohttp.WSMsgType.ERROR):
                            log(f"{tag} ... СМЕРТЬ: ВХОДЯЩИЙ {msg.type} "
                                f"close_code={ws.close_code} "
                                f"exc={ws.exception()!r} "
                                f"data={str(msg.data)[:120]}")
                            break
                    if ws.closed:
                        break
                    if ok:
                        alive += 1
                        log(f"{tag} ... OK (отв. {time.time() - sent:.2f}с, "
                            f"сделок мимоходом {passing})")
                    else:
                        log(f"{tag} ... НЕТ ОТВЕТА за {RESP_WAIT}с "
                            f"(сделок мимоходом {passing}) — иду дальше")
                    await asyncio.sleep(SUB_GAP)
                log(f"ИТОГ BISECT: выжило подписок {alive}/{len(coins)} "
                    f"соединение={'живо' if not ws.closed else 'закрыто'}")
    except Exception as e:
        log(f"ИСКЛЮЧЕНИЕ: {type(e).__name__}: {e}")


async def burst(server: str, dur: float = 15.0) -> None:
    """Вся батарея продакшена одной мгновенной пачкой (стандартный UA).

    Если выживет — всплеск подписок невиновен и дело в чём-то ещё
    (главный подозреваемый — кастомный User-Agent боевого кода).
    """
    coin_map = await fetch_production_map(server)
    coins = sorted(coin_map)
    print(f"батарея подписок ({len(coins)}): {', '.join(coins)}")
    if not coins:
        print("подписываться не на что — выхожу")
        return
    t0 = time.time()

    def log(*a):
        print(f"[{time.time() - t0:6.2f}с]", *a, flush=True)

    log("connect", HL_WS, "(стандартный User-Agent) ...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(HL_WS, timeout=15) as ws:
                log("handshake OK, шлю все подписки пачкой без пауз...")
                for coin in coins:
                    await ws.send_json({"method": "subscribe",
                                        "subscription": {"type": "trades",
                                                         "coin": coin}})
                log(f"пачка из {len(coins)} ушла за "
                    f"{time.time() - t0:.2f}с, слушаю...")
                n = 0
                next_stat = t0 + 5.0
                while time.time() - t0 < dur:
                    try:
                        msg = await ws.receive(timeout=max(
                            0.1, min(dur - (time.time() - t0),
                                     next_stat - time.time())))
                    except asyncio.TimeoutError:
                        if time.time() >= next_stat:
                            log(f"... живо, сообщений: {n}")
                            next_stat = time.time() + 5.0
                            continue
                        break
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        n += 1
                        if n <= 5:
                            try:
                                p = json.loads(msg.data)
                                ch = p.get("channel", "?")
                            except Exception:
                                ch = "НЕ-JSON"
                            log(f"msg#{n} channel={ch} ({len(msg.data)} байт)")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        log(f"ВХОДЯЩИЙ {msg.type}: "
                            f"close_code={ws.close_code} "
                            f"exc={ws.exception()!r} "
                            f"data={str(msg.data)[:120]}")
                        break
                log(f"ИТОГ BURST: сообщений={n} "
                    f"соединение={'живо' if not ws.closed else 'закрыто'}")
    except Exception as e:
        log(f"ИСКЛЮЧЕНИЕ: {type(e).__name__}: {e}")


if __name__ == "__main__":
    args = list(sys.argv[1:])
    ua = ""
    if "--ua" in args:
        i = args.index("--ua")
        ua = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if args and args[0] == "--bisect":
        asyncio.run(bisect(args[1] if len(args) > 1
                            else "http://127.0.0.1:8000"))
    elif args and args[0] == "--burst":
        asyncio.run(burst(args[1] if len(args) > 1
                           else "http://127.0.0.1:8000"))
    else:
        coin = args[0] if args else "BTC"
        secs = float(args[1]) if len(args) > 1 else 15.0
        asyncio.run(watch(coin, secs, ua))
