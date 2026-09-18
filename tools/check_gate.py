#!/usr/bin/env python3
"""
Почему Gate «нет связи»: диагностика спецификаций контрактов.

Когда в /api/health по Gate видно
    "не загрузились спецификации контрактов Gate (...)"
или в ленте просто тишина — запустите это на том же сервере, где стоит
терминал. Скрипт проверяет ровно те шаги, которые делает MarketFeed:

  1. DNS: резолвится ли api.gateio.ws и запасной fx-api.gateio.ws;
  2. TCP+TLS: открывается ли соединение (443);
  3. GET /contracts: код ответа, время, размер, разбор quanto_multiplier;
  4. точечный /contracts/{SYMBOL};
  5. GET /tickers: сколько контрактов вообще торгуется (сверка с числом
     подписок в терминале);
  6. WebSocket futures.public_liquidates: принимает ли подписку и приходят ли
     события за WAIT секунд (0 событий по BTC — само по себе нормально:
     ликвидация случается не каждую минуту).

Часы сервера: Gate сверяет поле time в подписке со своим временем. Если
часы сбиты, подписка молча не подтверждается — скрипт сравнивает своё время
с HTTP-заголовком Date от Gate и предупреждает о расхождении.

Запуск:
    python3 tools/check_gate.py            # BTC_USDT
    python3 tools/check_gate.py ETH_USDT 15
"""

import asyncio
import email.utils
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402

from market_feed import (GATE_RESTS, GATE_WS, _gate_parse_specs,  # noqa: E402
                         to_gate)

SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else "BTC_USDT").upper()
WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
CONTRACT = to_gate(SYMBOL)

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
bad = 0


def line(name, ok, detail=""):
    global bad
    if ok is True:
        mark = f"{GREEN}OK {RESET}"
    elif ok is None:
        mark = f"{YELLOW}?  {RESET}"
    else:
        mark = f"{RED}НЕТ{RESET}"
        bad += 1
    print(f"  {mark} {name:<44} {detail}")


def dns(host):
    t0 = time.time()
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        line(f"DNS {host}", True, f"{', '.join(ips)} за {int((time.time()-t0)*1000)} мс")
        return ips
    except Exception as e:
        line(f"DNS {host}", False, f"{type(e).__name__}: {e}")
        return []


def tcp(host, port=443, timeout=6.0):
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            line(f"TCP {host}:{port}", True, f"{int((time.time()-t0)*1000)} мс")
            return True
    except Exception as e:
        line(f"TCP {host}:{port}", False, f"{type(e).__name__}: {e}")
        return False


async def clock_skew(session, url):
    """Расхождение наших часов со временем Gate (заголовок Date)."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            raw = r.headers.get("Date")
        if not raw:
            line("часы сервера", None, "Gate не прислал Date")
            return
        server = email.utils.parsedate_to_datetime(raw).timestamp()
        skew = time.time() - server
        ok = abs(skew) < 60
        line("часы сервера", ok,
             f"расхождение {skew:+.1f} с" + ("" if ok else
             " — подтяните NTP: сбитые часы ломают подписку Gate"))
    except Exception as e:
        line("часы сервера", None, str(e)[:90])


async def fetch(session, url, name):
    t0 = time.time()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            body = await r.text()
            ms = int((time.time() - t0) * 1000)
            if r.status != 200:
                line(name, False, f"HTTP {r.status}, {ms} мс — {body[:90]}")
                return None
            try:
                data = json.loads(body)
            except Exception:
                line(name, False, f"HTTP 200, но тело не JSON: {body[:90]}")
                return None
            line(name, True, f"HTTP 200, {ms} мс, {len(body)} байт")
            return data
    except asyncio.TimeoutError:
        line(name, False, f"таймаут {int((time.time()-t0)*1000)} мс — сервер не ответил")
        return None
    except Exception as e:
        line(name, False, f"{type(e).__name__}: {e}")
        return None


async def ws_check(session):
    try:
        async with session.ws_connect(GATE_WS, heartbeat=20,
                                      timeout=aiohttp.ClientTimeout(total=WAIT + 15)) as ws:
            await ws.send_json({"time": int(time.time()),
                                "channel": "futures.public_liquidates",
                                "event": "subscribe", "payload": [CONTRACT]})
            events = 0
            ack = None
            t0 = time.time()
            while time.time() - t0 < WAIT:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=WAIT)
                except asyncio.TimeoutError:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                try:
                    p = json.loads(msg.data)
                except Exception:
                    continue
                if p.get("event") == "subscribe":
                    ack = (p.get("result") or {}).get("status")
                    line("WS подписка public_liquidates", ack == "success",
                         f"status={ack}")
                elif p.get("error"):
                    line("WS подписка public_liquidates", False, str(p["error"])[:120])
                    return
                elif p.get("channel") == "futures.public_liquidates":
                    events += len(p.get("result") or [])
            if ack is None:
                line("WS подписка public_liquidates", None, "ответ на подписку не пришёл")
            line(f"WS события за {WAIT:.0f} с", None,
                 f"{events} шт." + ("" if events else
                 f" — тишина по {CONTRACT} нормальна: ликвидация не каждую минуту"))
    except Exception as e:
        line("WS futures.public_liquidates", False, f"{type(e).__name__}: {e}")


async def main():
    print(f"Диагностика Gate ({SYMBOL} → контракт {CONTRACT})\n")
    hosts = []
    for rest in GATE_RESTS:
        host = rest.split("//", 1)[-1].split("/", 1)[0]
        if host not in hosts:
            hosts.append(host)

    print("Сеть:")
    for h in hosts:
        dns(h)
        tcp(h)

    print("\nREST:")
    async with aiohttp.ClientSession() as s:
        good_rest = None
        for rest in GATE_RESTS:
            data = await fetch(s, f"{rest}/contracts", f"GET {rest}/contracts")
            if isinstance(data, list) and data:
                mult = _gate_parse_specs(data)
                line("разбор спецификаций", bool(mult),
                     f"контрактов {len(data)}, с множителем {len(mult)}")
                if CONTRACT in mult:
                    line(f"множитель {CONTRACT}", True, str(mult[CONTRACT]))
                else:
                    line(f"множитель {CONTRACT}", None,
                         "контракта нет в листинге — проверьте имя монеты")
                good_rest = rest
                break

        if good_rest:
            await fetch(s, f"{good_rest}/contracts/{CONTRACT}",
                       f"GET /contracts/{CONTRACT}")
            tickers = await fetch(s, f"{good_rest}/tickers", "GET /tickers")
            if isinstance(tickers, list):
                line("торгуется контрактов", True, f"{len(tickers)}")
            await clock_skew(s, f"{good_rest}/contracts")
        else:
            line("запасной путь", None,
                 "оба адреса недоступны — терминал возьмёт кэш из "
                 "data/gate_contracts.json, если он есть")

        print("\nWebSocket:")
        await ws_check(s)

    if bad:
        print(f"\n{RED}Проблемных проверок: {bad}{RESET}. "
              "Ищите причину в строках НЕТ выше: она попадёт в /api/health → "
              "gate.specs_error и в лог строкой «[gate] спецификации контрактов"
              " не загрузились».")
    else:
        print(f"\n{GREEN}Все проверки пройдены{RESET}: если лента всё же молчит, "
              "дело не в спецификациях — смотрите /api/health → gate.")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
