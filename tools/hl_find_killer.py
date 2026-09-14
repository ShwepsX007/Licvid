#!/usr/bin/env python3
"""
Поиск монеты, на которой Hyperliquid рвёт соединение.

Зачем: на боевом сервере сокет закрывался кодом 1000 через 1-4 секунды после
начала подписок — и в боевом коде, и в «голом» клиенте, при любом User-Agent
и любом таймауте сессии. Значит, дело не в сессии, а в том, ЧТО мы отправляем:
подписка на имя, которого у биржи нет, обрывает ВСЁ соединение.

Скрипт проходит ровно те монеты, на которые подписывается боевой слушатель
(тот же universe + тот же hl_coin_map), по одной, читая каждый кадр, и
называет монету, после которой биржа закрыла сокет. Найденные монеты
исключаются, проход повторяется — пока не останется чистый прогон.

Режимы:
    python3 tools/hl_find_killer.py            # контроль BTC + поиск виновников
    python3 tools/hl_find_killer.py --coins-only   # только список монет (быстро)
    python3 tools/hl_find_killer.py BTC 60     # одна монета, держать 60с

Что делать с результатом: если виновник найден, поднять поток без него можно
сразу, без правки кода:

    mkdir -p /etc/systemd/system/licvid.service.d
    printf '[Service]\\nEnvironment=LIQSCOPE_HL_SKIP_COINS=kPEPE,SHIB\\n' \\
        > /etc/systemd/system/licvid.service.d/hl.conf
    systemctl daemon-reload && systemctl restart licvid
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import ClientWSTimeout

from market_feed import HL_UA, hl_coin_map

HL_WS = os.getenv("LIQSCOPE_HL_WS", "wss://api.hyperliquid.xyz/ws")
HL_REST = os.getenv("LIQSCOPE_HL_REST", "https://api.hyperliquid.xyz")
SUB_GAP = 0.5          # пауза между подписками (медленно и заведомо безопасно)
ACK_WAIT = 3.0         # сколько ждать ответа на одну подписку
HOLD = 25.0            # сколько держать соединение в чистом прогоне
MAX_ROUNDS = 6         # сколько виновников ищем максимум


async def load_universe():
    async with aiohttp.ClientSession(headers={"User-Agent": HL_UA}) as s:
        async with s.post(f"{HL_REST}/info", json={"type": "meta"},
                          timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json(content_type=None)
    # регистр не трогаем: kPEPE/kSHIB — это имена биржи
    return [str(row.get("name") or "").strip()
            for row in (data or {}).get("universe") or [] if row.get("name")]


async def load_symbols(server):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{server}/api/symbols",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                return (await r.json()).get("symbols") or []
    except Exception as e:
        print(f"  /api/symbols недоступен ({e}) — беру встроенный топ")
        return []


async def battle_coins(server):
    """Ровно те монеты, на которые подписывается боевой слушатель."""
    universe = await load_universe()
    symbols = await load_symbols(server)
    if not symbols:
        symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "DOGE_USDT",
                   "HYPE_USDT", "SUI_USDT", "LINK_USDT", "AVAX_USDT", "NEAR_USDT",
                   "ARB_USDT", "ADA_USDT", "LTC_USDT", "DOT_USDT", "UNI_USDT",
                   "APT_USDT", "ENA_USDT", "WLD_USDT", "TIA_USDT", "PEPE_USDT"]
    mapped = hl_coin_map(symbols, universe)
    unmapped = sorted({s.split("_")[0] for s in symbols} -
                      {mapped.get(k, "").split("_")[0] for k in mapped} -
                      set(mapped))
    return sorted(mapped), mapped, unmapped, len(universe)


async def probe(coins, hold, label):
    """Одно соединение: подписки по одной, читаем всё. Возвращает описание исхода."""
    print(f"\n--- {label}: {len(coins)} монет, держу до {hold:.0f}с")
    async with aiohttp.ClientSession(headers={"User-Agent": HL_UA}) as s:
        t0 = time.monotonic()
        try:
            ws = await s.ws_connect(HL_WS, heartbeat=None,
                                    timeout=ClientWSTimeout(ws_close=25))
        except Exception as e:
            print(f"    рукопожатие FAIL: {type(e).__name__}: {e}")
            return {"death": "handshake", "coin": None, "life": 0.0}
        last = None
        acked = 0
        errors = []
        frames = 0
        death = None
        try:
            for coin in coins:
                last = coin
                await ws.send_json({"method": "subscribe",
                                    "subscription": {"type": "trades",
                                                     "coin": coin}})
                t_end = time.monotonic() + ACK_WAIT
                got_ack = False
                while time.monotonic() < t_end and not got_ack:
                    try:
                        msg = await ws.receive(
                            timeout=max(0.1, t_end - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        frames += 1
                        try:
                            p = json.loads(msg.data)
                        except Exception:
                            continue
                        if p.get("channel") == "subscriptionResponse":
                            d = p.get("data") or {}
                            blob = json.dumps(d, ensure_ascii=False)
                            if "error" in blob.lower():
                                errors.append((coin, blob[:180]))
                                print(f"    [{time.monotonic()-t0:5.1f}с] "
                                      f"{coin}: ОШИБКА подписки {blob[:180]}")
                            else:
                                acked += 1
                                got_ack = True
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        death = (f"{getattr(msg.type, 'name', msg.type)} "
                                 f"close_code={ws.close_code} "
                                 f"exc={ws.exception()!r}")
                        break
                if death:
                    break
                await asyncio.sleep(SUB_GAP)

            if not death:
                end = time.monotonic() + hold
                while time.monotonic() < end:
                    try:
                        msg = await ws.receive(timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        frames += 1
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        death = (f"{getattr(msg.type, 'name', msg.type)} "
                                 f"close_code={ws.close_code} "
                                 f"exc={ws.exception()!r}")
                        break
        finally:
            life = time.monotonic() - t0
            try:
                await ws.close()
            except Exception:
                pass
    if death:
        print(f"    [{life:5.1f}с] УМЕР: {death}; последняя монета={last}, "
              f"подтверждено {acked}/{len(coins)}, кадров {frames}")
        if errors:
            print(f"    ошибки подписок: {errors}")
        return {"death": death, "coin": last, "life": life,
                "errors": errors, "acked": acked}
    print(f"    [{life:5.1f}с] ЖИВ: подтверждено {acked}/{len(coins)}, "
          f"кадров {frames}, ошибок {len(errors)}")
    return {"death": None, "coin": None, "life": life,
            "errors": errors, "acked": acked}


async def main():
    args = [a for a in sys.argv[1:]]
    server = "http://127.0.0.1:8000"
    for a in list(args):
        if a.startswith("http"):
            server = a
            args.remove(a)

    coins, mapped, unmapped, n_universe = await battle_coins(server)
    print(f"universe: {n_universe} перпетуумов")
    print(f"боевых монет: {len(coins)} -> {', '.join(coins)}")
    if unmapped:
        print(f"не попали в HL (нет на бирже): {', '.join(unmapped)}")

    if "--coins-only" in args:
        return

    if args and args[0] not in ("--coins-only",):
        coin = args[0]
        hold = float(args[1]) if len(args) > 1 else HOLD
        res = await probe([coin], hold, f"контроль: одна монета {coin}")
        print("\nВЫВОД:", "канал с этого IP жив — дело в конкретной монете"
              if not res["death"] else
              "даже одна монета не держится — это сеть/блокировка IP")
        return

    # 1) контроль: одна заведомо валидная монета
    res = await probe(["BTC"], HOLD, "контроль: только BTC")
    if res["death"]:
        print("\nВЫВОД: соединение рвётся даже на одной валидной монете —")
        print("  это не про список подписок, а про сеть/фильтр/блокировку IP.")
        print("  Проверьте: curl -sS -m 8 -X POST https://api.hyperliquid.xyz/info "
              "-H 'Content-Type: application/json' -d '{\"type\":\"meta\"}' -o /dev/null "
              "-w 'HTTP %{http_code}\\n'")
        return

    # 2) ищем виновников: проход -> исключаем -> повтор
    exclude = []
    for rnd in range(1, MAX_ROUNDS + 1):
        batch = [c for c in coins if c not in exclude]
        if not batch:
            break
        res = await probe(batch, HOLD, f"проход {rnd} (исключено: {exclude or '—'})")
        if not res["death"]:
            print(f"\nВЫВОД: чистый прогон на {len(batch)} монетах — "
                  f"поток должен работать.")
            if exclude:
                print(f"  виновники: {', '.join(exclude)}")
                print("  Поднять поток без них (без правки кода):")
                print(f"    printf '[Service]\\nEnvironment=LIQSCOPE_HL_SKIP_COINS="
                      f"{','.join(exclude)}\\n' \\")
                print("        > /etc/systemd/system/licvid.service.d/hl.conf")
                print("    systemctl daemon-reload && systemctl restart licvid")
            return
        bad = res["coin"]
        if not bad or bad in exclude:
            print(f"\nВЫВОД: виновник не определяется однозначно "
                  f"(последняя монета={bad}); похоже на обрыв по времени/трафику, "
                  f"а не на конкретное имя.")
            return
        exclude.append(bad)
        print(f"  >> виновник прохода {rnd}: {bad}")

    print(f"\nВЫВОД: найдено {len(exclude)} проблемных монет: {', '.join(exclude)}")


if __name__ == "__main__":
    asyncio.run(main())
