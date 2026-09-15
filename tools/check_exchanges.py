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


# ---------------------------------------------------------------------------
# Матчеры кадров. Вынесены из лямбд, чтобы их можно было проверить офлайн:
# логика «есть ли в кадре ликвидация» у новых бирж нетривиальная — у всех
# трёх ликвидация это метка внутри общего потока сделок.
# ---------------------------------------------------------------------------
def dydx_has_trades(p) -> bool:
    """dYdX v4: кадр v4_trades со сделками."""
    return (isinstance(p, dict) and p.get("channel") == "v4_trades"
            and bool((p.get("contents") or {}).get("trades")))


def dydx_has_liquidation(p) -> bool:
    """dYdX v4: среди сделок есть Liquidated/Deleveraged."""
    if not dydx_has_trades(p):
        return False
    trades = (p.get("contents") or {}).get("trades") or []
    return any(isinstance(t, dict)
               and str(t.get("type") or "").upper() in ("LIQUIDATED", "DELEVERAGED")
               for t in trades)


def _kraken_rows(p):
    """Сделки кадра Kraken: одиночный trade или список в trade_snapshot.

    Важно не принять за сделку служебный кадр: у подтверждения подписки тоже
    есть feed == "trade" ({"event":"subscribed","feed":"trade"}), и без этой
    проверки диагностика рапортовала бы «данные идут» на одном лишь connect.
    """
    if not isinstance(p, dict) or p.get("feed") not in ("trade", "trade_snapshot"):
        return []
    if p.get("event"):                      # subscribed / info / error / pong
        return []
    if p.get("feed") == "trade":
        # у настоящей сделки есть и цена, и объём
        return [p] if p.get("price") is not None and p.get("qty") is not None else []
    rows = p.get("trades")
    return rows if isinstance(rows, list) else []


def kraken_has_trades(p) -> bool:
    return bool(_kraken_rows(p))


def kraken_has_liquidation(p) -> bool:
    """Kraken Futures: type == liquidation (движок) или termination (страховой фонд)."""
    return any(str(r.get("type") or "") in ("liquidation", "termination")
               for r in _kraken_rows(p) if isinstance(r, dict))


def bitfinex_has_liq_row(p) -> bool:
    """Bitfinex status/liq:global: кадр [chanId, [["pos", ...], ...]].

    chanId приходит в подтверждении подписки, поэтому проверяем форму кадра,
    а не конкретный идентификатор канала.
    """
    if not isinstance(p, list) or len(p) != 2 or not isinstance(p[1], list):
        return False
    return any(isinstance(r, list) and r and r[0] == "pos" for r in p[1])


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
        await check_rest(s, "dYdX v4 perpetualMarkets",
                         "https://indexer.dydx.trade/v4/perpetualMarkets")
        await check_rest(s, "Kraken Futures instruments",
                         "https://futures.kraken.com/derivatives/api/v3/instruments")

        print("\nWebSocket (главное — приходят ли ДАННЫЕ, а не просто connect):")
        # 2026-04-23 Binance отключил legacy-пути WS: …/stream и …/ws теперь
        # открываются и молчат. Поэтому каждый стрим проверяем по обоим путям
        # — по ответу сразу видно: источник жив или мы просто не туда смотрим.
        for suffix, sub, matcher, nm, wt in (
            (f"stream?streams={low}@aggTrade", None,
             lambda p: (p.get("data") or p).get("e") in ("aggTrade", "trade"),
             "Binance combined aggTrade", WAIT),
            ("ws", {"method": "SUBSCRIBE", "params": [f"{low}@aggTrade"], "id": 1},
             lambda p: (p.get("data") or p).get("e") in ("aggTrade", "trade"),
             "Binance raw + SUBSCRIBE aggTrade", WAIT),
            (f"stream?streams={low}@kline_1m", None,
             lambda p: (p.get("data") or p).get("e") == "kline",
             "Binance combined kline_1m", WAIT),
            ("stream?streams=!forceOrder@arr", None,
             lambda p: (p.get("data") or p).get("e") == "forceOrder",
             "Binance !forceOrder@arr (ликвидации)", max(WAIT, 20)),
        ):
            hits = []
            for root, tag in (("wss://fstream.binance.com/market", "/market"),
                              ("wss://fstream.binance.com", "legacy")):
                if await check_ws(s, f"{nm} [{tag}]", f"{root}/{suffix}",
                                  subscribe=sub, match=matcher, wait=wt):
                    hits.append(tag)
            if hits == ["legacy"]:
                line(f"{nm}: итог", False,
                     "данные только по legacy-пути — терминал должен ходить на "
                     "/market (LIQSCOPE_BINANCE_WS)")
            elif not hits:
                line(f"{nm}: итог", False, "ни один путь Binance не отдал данные")
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

        dydx_ticker = SYMBOL[:-4] + "-USD" if SYMBOL.endswith("USDT") else SYMBOL
        await check_ws(s, f"dYdX v4_trades {dydx_ticker} (любые сделки)",
                       "wss://indexer.dydx.trade/v4/ws",
                       subscribe={"type": "subscribe", "channel": "v4_trades",
                                  "id": dydx_ticker},
                       match=dydx_has_trades)
        await check_ws(s, f"dYdX v4_trades {dydx_ticker} (ликвидации)",
                       "wss://indexer.dydx.trade/v4/ws",
                       subscribe={"type": "subscribe", "channel": "v4_trades",
                                  "id": dydx_ticker},
                       match=dydx_has_liquidation,
                       wait=max(WAIT, 60))
        kraken_product = "PF_XBTUSD" if SYMBOL.startswith("BTC") else \
            "PF_" + (SYMBOL[:-4] if SYMBOL.endswith("USDT") else SYMBOL) + "USD"
        await check_ws(s, f"Kraken trade {kraken_product} (любые сделки)",
                       "wss://futures.kraken.com/ws/v1",
                       subscribe={"event": "subscribe", "feed": "trade",
                                  "product_ids": [kraken_product]},
                       match=kraken_has_trades)
        await check_ws(s, f"Kraken trade {kraken_product} (ликвидации)",
                       "wss://futures.kraken.com/ws/v1",
                       subscribe={"event": "subscribe", "feed": "trade",
                                  "product_ids": [kraken_product]},
                       match=kraken_has_liquidation,
                       wait=max(WAIT, 60))
        await check_ws(s, "Bitfinex status liq:global (ликвидации всей биржи)",
                       "wss://api-pub.bitfinex.com/ws/2",
                       subscribe={"event": "subscribe", "channel": "status",
                                  "key": "liq:global"},
                       # кадр приходит по chanId из подтверждения подписки,
                       # поэтому смотрим любой массив со строкой "pos"
                       match=bitfinex_has_liq_row,
                       wait=max(WAIT, 60))

    print("""
Как читать:
  «НЕТ» у ликвидаций может означать просто затишье на рынке — повторите
  с большим ожиданием: python3 tools/check_exchanges.py BTCUSDT 60
  «НЕТ» у aggTrade/kline при живом connect — биржа не отдаёт этому серверу
  рыночные данные. Тогда закрепите рабочий источник в /etc/systemd/system/liqscope.service:
      Environment=LIQSCOPE_TICK_SOURCE=bybit
  У dYdX, Kraken и Bitfinex ликвидация — это МЕТКА внутри общего потока
  сделок, а не отдельный канал. Поэтому «ОК» у строк «любые сделки» при
  «НЕТ» у строк «ликвидации» — это норма: канал работает, ликвидаций на этой
  бирже за время ожидания просто не случилось. Тревожиться нужно, если
  «НЕТ» именно у «любые сделки».
  Ликвидации Hyperliquid отдельно проверяются зондом 0xArchive (нужен ключ):
      OXARCHIVE_API_KEY=... python3 tools/oxa_probe.py --rest BTC --hours 24
""")


if __name__ == "__main__":
    asyncio.run(main())
