#!/usr/bin/env python3
"""Зонд Hyperliquid WS. Три режима:

1) watch (одна монета, поток сделок ~N секунд):
     venv/bin/python3 tools/check_hyperliquid.py BTC 15

2) bisect (пошаговые подписки ровно на те монеты, что и боевой слушатель;
   показывает, на какой по счёту/какой именно подписке умирает соединение):
     venv/bin/python3 tools/check_hyperliquid.py --bisect
     venv/bin/python3 tools/check_hyperliquid.py --bisect http://127.0.0.1:8000

3) soak (те же подписки + держим соединение минуты/часы как боевой слушатель:
   пинг раз в HL_PING_INTERVAL, контроль pong, возраста входящих; при обрыве —
   код закрытия и время жизни). Код выхода: 0 — пережил весь срок, 4 — умер:
     venv/bin/python3 tools/check_hyperliquid.py --soak
     venv/bin/python3 tools/check_hyperliquid.py --soak http://127.0.0.1:8000 1800
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402

from market_feed import HL_PING_INTERVAL, hl_coin_map  # noqa: E402

HL_WS = "wss://api.hyperliquid.xyz/ws"
HL_REST = "https://api.hyperliquid.xyz"
VERBOSE_FIRST = 12  # первые N сообщений watch — подробно, дальше — счётчики
SUB_GAP = 0.4       # пауза между подписками в bisect/soak
RESP_WAIT = 2.5     # сколько ждать subscriptionResponse на каждую монету
SOAK_PING = HL_PING_INTERVAL        # тот же интервал, что у боевого слушателя
SOAK_STALE = HL_PING_INTERVAL * 3   # сколько молчания считаем мёртвым сокетом


async def watch(coin: str, dur: float) -> None:
    t0 = time.time()

    def log(*a):
        print(f"[{time.time() - t0:6.2f}с]", *a, flush=True)

    log("connect", HL_WS, "...")
    try:
        async with aiohttp.ClientSession() as s:
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


async def subscribe_gradually(ws, coins, log):
    """Подписки по одной с ожиданием subscriptionResponse (темп боевого
    слушателя и --bisect). Возвращает число подтверждённых подписок."""
    alive = 0
    for i, coin in enumerate(coins, 1):
        tag = f"sub#{i:02d}/{len(coins)} {coin}"
        try:
            await ws.send_json(
                {"method": "subscribe",
                 "subscription": {"type": "trades", "coin": coin}})
        except Exception as e:
            log(f"{tag} ... СМЕРТЬ НА ОТПРАВКЕ: {type(e).__name__}: {e}")
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
    return alive


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
                alive = await subscribe_gradually(ws, coins, log)
                log(f"ИТОГ BISECT: выжило подписок {alive}/{len(coins)} "
                    f"соединение={'живо' if not ws.closed else 'закрыто'}")
    except Exception as e:
        log(f"ИСКЛЮЧЕНИЕ: {type(e).__name__}: {e}")


async def soak(server: str, dur: float) -> int:
    """Долгая проверка: подписки как в бою + держим соединение dur секунд.

    Пингуем тем же интервалом, что боевой слушатель (SOAK_PING), следим, что
    приходит pong, и что входящие не молчат дольше SOAK_STALE. Показывает,
    живёт ли соединение само по себе (тогда причина обрывов — в боевом
    коде/окружении) или умирает и на «эталонном» клиенте (тогда это биржа/
    сеть). Возвращает 0, если пережили весь срок, 4 — если соединение умерло.
    """
    coin_map = await fetch_production_map(server)
    coins = sorted(coin_map)
    print(f"батарея подписок ({len(coins)}): {', '.join(coins)}")
    if not coins:
        print("подписываться не на что — выхожу")
        return 4
    print(f"держу соединение {dur:.0f}с: ping каждые {SOAK_PING:.0f}с, "
          f"молчание входящих >{SOAK_STALE:.0f}с = мёртвый сокет")
    t0 = time.time()

    def log(*a):
        print(f"[{time.time() - t0:7.2f}с]", *a, flush=True)

    reason = None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(HL_WS, timeout=15) as ws:
                log("handshake OK, подписываюсь по одной...")
                alive = await subscribe_gradually(ws, coins, log)
                if ws.closed:
                    log("ИТОГ SOAK: соединение умерло ещё на подписках")
                    return 4
                log(f"подписались {alive}/{len(coins)} — держу фазу "
                    f"наблюдения {dur:.0f}с...")
                msgs = trades = liqs = 0
                pings_sent = 0
                pong_since_ping = False
                missed_pongs = 0
                last_inbound = time.time()
                next_ping = time.time() + SOAK_PING
                next_stat = time.time() + 30.0
                while time.time() - t0 < dur:
                    now = time.time()
                    wake = min(next_ping, next_stat, t0 + dur)
                    try:
                        msg = await ws.receive(
                            timeout=max(0.2, min(wake - now, 5.0)))
                    except asyncio.TimeoutError:
                        msg = None
                    now = time.time()
                    if msg is not None:
                        if msg.type is aiohttp.WSMsgType.TEXT:
                            last_inbound = now
                            try:
                                p = json.loads(msg.data)
                            except Exception:
                                p = {}
                            ch = p.get("channel", "?")
                            if ch == "pong":
                                pong_since_ping = True
                            elif ch == "trades":
                                data = p.get("data") or []
                                msgs += 1
                                trades += len(data)
                                liqs += sum(
                                    1 for t in data if isinstance(t, dict)
                                    and "liquidation" in t)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.CLOSING,
                                          aiohttp.WSMsgType.ERROR):
                            reason = (f"ВХОДЯЩИЙ {msg.type}: "
                                      f"close_code={ws.close_code} "
                                      f"exc={ws.exception()!r} "
                                      f"data={str(msg.data)[:120]}")
                            break
                    now = time.time()
                    if now >= next_ping:
                        if now - last_inbound > SOAK_STALE:
                            reason = (f"МЁРТВЫЙ СОКЕТ: входящих нет "
                                      f"{now - last_inbound:.0f}с — ни pong, "
                                      f"ни ленты")
                            break
                        if pings_sent and not pong_since_ping:
                            missed_pongs += 1
                            log(f"ВНИМАНИЕ: pong на ping#{pings_sent} не пришёл "
                                f"(подряд: {missed_pongs})")
                            if missed_pongs >= 2:
                                reason = ("два ping подряд без pong — "
                                          "соединение не отвечает")
                                break
                        try:
                            await ws.send_json({"method": "ping"})
                            pings_sent += 1
                            pong_since_ping = False
                            next_ping = now + SOAK_PING
                        except Exception as e:
                            reason = (f"ping не ушёл: "
                                      f"{type(e).__name__}: {e}")
                            break
                    if now >= next_stat:
                        log(f"... держится: сообщений={msgs} сделок={trades} "
                            f"ликвидаций={liqs} пингов={pings_sent} "
                            f"входящих молчит {(now - last_inbound):.0f}с")
                        next_stat = now + 30.0
                if reason is None:
                    log(f"ИТОГ SOAK: пережил весь срок ({dur:.0f}с), "
                        f"соединение живо: сообщений={msgs} сделок={trades} "
                        f"ликвидаций={liqs} пингов={pings_sent} "
                        f"(без pong: {missed_pongs})")
                    return 0
    except Exception as e:
        reason = f"ИСКЛЮЧЕНИЕ: {type(e).__name__}: {e}"
    log(f"ИТОГ SOAK: СОЕДИНЕНИЕ УМЕРЛО через {time.time() - t0:.0f}с: {reason}")
    return 4


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--bisect":
        asyncio.run(bisect(sys.argv[2] if len(sys.argv) > 2
                           else "http://127.0.0.1:8000"))
    elif len(sys.argv) > 1 and sys.argv[1] == "--soak":
        server = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
        dur = float(sys.argv[3]) if len(sys.argv) > 3 else 600.0
        sys.exit(asyncio.run(soak(server, dur)))
    else:
        coin = sys.argv[1] if len(sys.argv) > 1 else "BTC"
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
        asyncio.run(watch(coin, secs))
