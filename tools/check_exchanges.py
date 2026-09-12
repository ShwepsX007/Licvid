#!/usr/bin/env python3
"""
Диагностика доступности бирж с этого сервера.

Проверяет REST и, главное, реально ли приходят данные в WebSocket-каналы,
которыми пользуется терминал. Полезно, когда сокет открывается, но данных
нет (именно так вёл себя Binance aggTrade на некоторых серверах).

Запуск:
    python3 tools/check_exchanges.py            # проверка по BTCUSDT
    python3 tools/check_exchanges.py ETHUSDT 15 # своя монета и таймаут ожидания
"""

import asyncio
import gzip
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def line(name, ok, detail=""):
    mark = f"{GREEN}OK {RESET}" if ok is True else (
        f"{YELLOW}?  {RESET}" if ok is None else f"{RED}НЕТ{RESET}")
    print(f"  {mark} {name:<46} {detail}")


async def check_rest(session, name, url):
    t0 = time.time()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            body = await r.text()
            ms = int((time.time() - t0) * 1000)
            line(name, r.status == 200, f"HTTP {r.status}, {ms} мс, {len(body)} байт")
            return r.status == 200
    except Exception as e:
        line(name, False, str(e)[:90])
        return False


async def check_rest_post(session, name, url, payload):
    t0 = time.time()
    try:
        async with session.post(url, json=payload,
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
            body = await r.text()
            ms = int((time.time() - t0) * 1000)
            line(name, r.status == 200, f"HTTP {r.status}, {ms} мс, {len(body)} байт")
            return r.status == 200
    except Exception as e:
        line(name, False, str(e)[:90])
        return False


async def check_ws(session, name, url, subscribe=None, match=None, wait=WAIT,
                   gzipped=False, on_message=None):
    """Подключается, шлёт подписку и ждёт ПЕРВОЕ подходящее сообщение."""
    t0 = time.time()
    try:
        async with session.ws_connect(url, timeout=15, heartbeat=20) as ws:
            connect_ms = int((time.time() - t0) * 1000)
            if subscribe:
                await ws.send_json(subscribe)
            deadline = time.time() + wait
            other = 0
            first_other = ""
            while time.time() < deadline:
                try:
                    msg = await ws.receive(timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    break
                if msg.type is aiohttp.WSMsgType.BINARY and gzipped:
                    try:
                        raw = gzip.decompress(msg.data).decode("utf-8")
                    except Exception:
                        continue
                elif msg.type is aiohttp.WSMsgType.TEXT:
                    raw = msg.data
                else:
                    break
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if on_message:
                    reply = on_message(payload)
                    if reply is not None:
                        await ws.send_json(reply)
                        continue
                if match is None or match(payload):
                    ms = int((time.time() - t0) * 1000)
                    line(name, True, f"connect {connect_ms} мс, данные через {ms} мс")
                    return True
                other += 1
                if not first_other:
                    first_other = json.dumps(payload, ensure_ascii=False)[:80]
            line(name, False,
                 f"connect {connect_ms} мс, данных нет за {wait:.0f} с"
                 + (f"; служебных сообщений {other}: {first_other}" if other else ""))
            return False
    except Exception as e:
        line(name, False, str(e)[:90])
        return False


async def main():
    low = SYMBOL.lower()
    print(f"\nПроверка бирж с этого сервера (монета {SYMBOL}, ожидание {WAIT:.0f} с)\n")

    async with aiohttp.ClientSession() as s:
        print("REST:")
        await check_rest(s, "Binance fapi ping", "https://fapi.binance.com/fapi/v1/ping")
        await check_rest(s, "Binance fapi klines",
                         f"https://fapi.binance.com/fapi/v1/klines?symbol={SYMBOL}&interval=1m&limit=2")
        await check_rest(s, "Bybit v5 time", "https://api.bybit.com/v5/market/time")
        await check_rest(s, "OKX tickers",
                         "https://www.okx.com/api/v5/market/tickers?instType=SWAP")
        await check_rest(s, "Gate contracts",
                         "https://api.gateio.ws/api/v4/futures/usdt/contracts?limit=1")
        await check_rest(s, "Bitget UTA time",
                         "https://api.bitget.com/api/v3/market/current-fund-rate?category=USDT-FUTURES&symbol=BTCUSDT")
        await check_rest(s, "HTX linear swap index",
                         "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info?contract_code=BTC-USDT")
        await check_rest(s, "BitMEX instruments",
                         "https://www.bitmex.com/api/v1/instrument/active")
        await check_rest_post(s, "Hyperliquid meta universe",
                              "https://api.hyperliquid.xyz/info", {"type": "meta"})

        print("\nWebSocket (главное — приходят ли ДАННЫЕ, а не просто connect):")
        await check_ws(s, "Binance combined aggTrade",
                       f"wss://fstream.binance.com/stream?streams={low}@aggTrade",
                       match=lambda p: (p.get("data") or p).get("e") in ("aggTrade", "trade"))
        await check_ws(s, "Binance raw + SUBSCRIBE aggTrade",
                       "wss://fstream.binance.com/ws",
                       subscribe={"method": "SUBSCRIBE",
                                  "params": [f"{low}@aggTrade"], "id": 1},
                       match=lambda p: (p.get("data") or p).get("e") in ("aggTrade", "trade"))
        await check_ws(s, "Binance combined kline_1m",
                       f"wss://fstream.binance.com/stream?streams={low}@kline_1m",
                       match=lambda p: (p.get("data") or p).get("e") == "kline")
        await check_ws(s, "Binance !forceOrder@arr (ликвидации)",
                       "wss://fstream.binance.com/stream?streams=!forceOrder@arr",
                       match=lambda p: (p.get("data") or p).get("e") == "forceOrder",
                       wait=max(WAIT, 20))
        await check_ws(s, "Bybit publicTrade",
                       "wss://stream.bybit.com/v5/public/linear",
                       subscribe={"op": "subscribe", "args": [f"publicTrade.{SYMBOL}"]},
                       match=lambda p: str(p.get("topic", "")).startswith("publicTrade"))
        await check_ws(s, "Bybit allLiquidation",
                       "wss://stream.bybit.com/v5/public/linear",
                       subscribe={"op": "subscribe", "args": [f"allLiquidation.{SYMBOL}"]},
                       match=lambda p: str(p.get("topic", "")).startswith("allLiquidation"),
                       wait=max(WAIT, 20))
        await check_ws(s, "OKX liquidation-orders",
                       "wss://ws.okx.com:8443/ws/v5/public",
                       subscribe={"op": "subscribe",
                                  "args": [{"channel": "liquidation-orders",
                                            "instType": "SWAP"}]},
                       match=lambda p: p.get("arg", {}).get("channel") == "liquidation-orders"
                       and bool(p.get("data")),
                       wait=max(WAIT, 20))
        await check_ws(s, "Bitget liquidation (UTA v3)",
                       "wss://ws.bitget.com/v3/ws/public",
                       subscribe={"op": "subscribe",
                                  "args": [{"instType": "usdt-futures",
                                            "topic": "liquidation"}]},
                       match=lambda p: (p.get("arg", {}).get("topic") == "liquidation"
                                        and bool(p.get("data"))),
                       wait=max(WAIT, 20))
        await check_ws(s, "HTX public.*.liquidation_orders",
                       "wss://api.hbdm.com/linear-swap-notification",
                       subscribe={"op": "sub", "cid": "check",
                                  "topic": "public.*.liquidation_orders"},
                       match=lambda p: ".liquidation_orders" in str(p.get("topic", ""))
                       and bool(p.get("data")),
                       gzipped=True,
                       on_message=lambda p: ({"op": "pong", "ts": p.get("ts")}
                                             if p.get("op") == "ping" else None),
                       wait=max(WAIT, 20))
        await check_ws(s, "BitMEX liquidation",
                       "wss://ws.bitmex.com/realtime?subscribe=liquidation",
                       match=lambda p: p.get("table") == "liquidation"
                       and p.get("action") == "insert",
                       wait=max(WAIT, 20))
        await check_ws(s, "Gate futures.public_liquidates",
                       "wss://fx-ws.gateio.ws/v4/ws/usdt",
                       subscribe={"time": int(time.time()),
                                  "channel": "futures.public_liquidates",
                                  "event": "subscribe", "payload": ["BTC_USDT"]},
                       match=lambda p: p.get("channel") == "futures.public_liquidates"
                       and bool(p.get("result")),
                       wait=max(WAIT, 20))
        hl_coin = SYMBOL[:-4] if SYMBOL.endswith("USDT") else SYMBOL
        await check_ws(s, f"Hyperliquid trades {hl_coin} (любые сделки)",
                       "wss://api.hyperliquid.xyz/ws",
                       subscribe={"method": "subscribe",
                                  "subscription": {"type": "trades", "coin": hl_coin}},
                       match=lambda p: p.get("channel") == "trades"
                       and bool(p.get("data")))
        await check_ws(s, f"Hyperliquid trades {hl_coin} (ликвидации)",
                       "wss://api.hyperliquid.xyz/ws",
                       subscribe={"method": "subscribe",
                                  "subscription": {"type": "trades", "coin": hl_coin}},
                       match=lambda p: p.get("channel") == "trades" and any(
                           isinstance(t, dict) and "liquidation" in t
                           for t in (p.get("data") or [])),
                       wait=max(WAIT, 20))

    print("""
Как читать:
  «НЕТ» у ликвидаций может означать просто затишье на рынке — повторите
  с большим ожиданием: python3 tools/check_exchanges.py BTCUSDT 60
  «НЕТ» у aggTrade/kline при живом connect — биржа не отдаёт этому серверу
  рыночные данные. Тогда закрепите рабочий источник в /etc/systemd/system/liqscope.service:
      Environment=LIQSCOPE_TICK_SOURCE=bybit
""")


if __name__ == "__main__":
    asyncio.run(main())
