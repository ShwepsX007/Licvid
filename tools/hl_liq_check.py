#!/usr/bin/env python3
"""Одна команда на все вопросы про ликвидации Hyperliquid: работает или нет.

Вопроса три, и ответы на них разные:

  1) Пусть ли HL-соединение?  Публичные каналы HL ключей НЕ требуют вообще
     (у биржи нет понятия «API-ключ для данных»: ключом является кошелёк, а
     приватные ленты читаются подпиской по адресу).
  2) Даёт ли публичный trades ликвидации? Слушаем канал `trades` по монетам и
     смотрим, появляется ли в сделках объект `liquidation`. По официальной
     схеме WsTrade его там нет (есть только в WsFill приватных userFills), и
     боевой замер 13.09.2026 это подтвердил: 18144 сделки, ноль меток.
  3) Работает ли путь через API-ключи (0xArchive)? Ключи HL не нужны — ключ
     нужен индексатору 0xArchive: LIQSCOPE_OXA_KEYS / OXARCHIVE_API_KEY.
     Проверяем каждый ключ одним REST-запросом /liquidations/<coin> и говорим,
     жив ли ключ, сколько строк и насколько свежие.

Запуск на боевом сервере:

    cd /root/Licvid && python3 tools/hl_liq_check.py            # ~60 с на ленту
    python3 tools/hl_liq_check.py --seconds 300 --coins 10       # подольше
    python3 tools/hl_liq_check.py --skip-ws                      # только health+ключи
    python3 tools/hl_liq_check.py --server http://127.0.0.1:8000 # health живого терминала

Код выхода: 0 — хоть один путь отдаёт ликвидации; 1 — соединение/ключи есть,
ликвидаций нет (тихий рынок или источник иссяк); 2 — не работает ничего
(сеть, подписки, ключи). Удобно для cron/healthcheck.

Скрипт ничего не пишет и ключи не печатает — только читает биржи.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiohttp
    from aiohttp import ClientWSTimeout

    from market_feed import (HL_FRESH_SEC, HL_PING_INTERVAL, HL_SUB_GAP,
                             HL_UA, oxa_rest_max_age, parse_hyperliquid_msg,
                             parse_oxa_liquidations, hl_coin_map, oxa_keys)
except ImportError as e:                                  # запуск не из venv
    sys.exit(f"не хватает зависимостей ({e}). Запусти из окружения проекта:\n"
             f"  cd /root/Licvid && pip install -r requirements.txt\n"
             f"  или: venv/bin/python3 tools/hl_liq_check.py")

HL_REST = os.getenv("LIQSCOPE_HL_REST", "https://api.hyperliquid.xyz")
HL_WS = os.getenv("LIQSCOPE_HL_WS", "wss://api.hyperliquid.xyz/ws")
OXA_REST = os.getenv("LIQSCOPE_OXA_REST", "https://api.0xarchive.io/v1/hyperliquid")
# Крупные монеты: на них ликвидации случаются чаще всего, и если метки нет
# здесь — дело не в «тихом» дешёвом токене.
MAJORS = ["BTC", "ETH", "SOL", "XRP", "HYPE", "DOGE", "SUI", "BNB", "LINK",
          "AVAX", "NEAR", "ARB", "ADA", "WLD", "PUMP", "TAO", "ZEC", "FIL",
          "UNI", "kPEPE"]
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def ok(msg):
    print(f"  {GREEN}ДА {RESET}  {msg}")


def bad(msg):
    print(f"  {RED}НЕТ {RESET}  {msg}")


def warn(msg):
    print(f"  {YELLOW}??  {RESET}  {msg}")


def note(msg):
    print(f"  {DIM}    {msg}{RESET}")


def head(title):
    print(f"\n=== {title} " + "=" * max(0, 62 - len(title)))


# ---------------------------------------------------------------------------
#  0) /api/health боевого терминала: что о себе говорит сам слушатель
# ---------------------------------------------------------------------------
async def check_health(server: str) -> dict:
    head("1) Терминал: /api/health")
    out = {"reachable": False, "hl_events": None, "oxa_events": None}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{server}/api/health",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        warn(f"терминал на {server} не отвечает ({type(e).__name__}: {e})")
        note("это не поломка источника: health есть только когда запущен "
             "python3 -m uvicorn server:app")
        return out
    out["reachable"] = True
    srcs = data.get("sources") or {}

    hl = srcs.get("hyperliquid") or {}
    out["hl_events"] = hl.get("events")
    print(f"  hyperliquid: connected={hl.get('connected')} "
          f"событий={hl.get('events')} "
          f"последнее_событие_сек_назад={hl.get('seconds_since_event')}")
    note(f"подписки отправлено/подтверждено: {hl.get('hl_subs_sent')}/{hl.get('hl_subs_acked')}, "
         f"пинги/понги: {hl.get('hl_pings')}/{hl.get('hl_pongs')}, "
         f"нет входящих {hl.get('hl_last_inbound_sec')} с, "
         f"отброшено несвежих: {hl.get('hl_skipped_stale')}, "
         f"отключено монет: {len(hl.get('hl_coins_banned') or [])}")
    if hl.get("last_error"):
        bad(f"last_error: {str(hl['last_error'])[:160]}")
    # вывод ликвидаций из ленты сделок (hl_infer): отдельная строка, потому что
    # именно она отвечает на вопрос «почему у HL events=0»
    if hl.get("hl_infer_trades") is not None:
        note(f"вывод из ленты: сделок {hl.get('hl_infer_trades')}, кандидатов "
             f"{hl.get('hl_infer_candidates')}, подтверждено "
             f"{hl.get('hl_infer_confirmed')} на {hl.get('hl_infer_usd')}$, "
             f"отклонено {hl.get('hl_infer_rejected')}, вне бюджета "
             f"{hl.get('hl_infer_budget_skipped')} (клампа "
             f"{hl.get('hl_infer_budget_per_min')}/мин), /info: "
             f"{hl.get('hl_infer_info_calls')} вызовов, "
             f"{hl.get('hl_infer_info_errors')} ошибок, латентность "
             f"{hl.get('hl_infer_latency_ms')} мс")
        if hl.get("hl_infer_info_errors"):
            warn("/info отвечает ошибкой: с этого сервера подтверждения не "
                 "доходят (429 = лимит веса на IP)")
        if not hl.get("hl_infer_info_calls"):
            note("ни одного подтверждения: либо всплесков не было (лента "
                 "спокойная), либо вывод выключен (LIQSCOPE_HL_INFER=0)")
    if not hl.get("connected"):
        bad("канал Hyperliquid не подключён (см. причину ниже в п.2)")
    elif not hl.get("events"):
        warn("соединение есть, ликвидаций за время работы — ноль")
        note("с включённым выводом из ленты это значит лишь то, что подходящих "
             "всплесков не было; без него (LIQSCOPE_HL_INFER=0) HL пуст всегда: "
             "биржа метку `liquidation` в публичный trades не кладёт")

    oxa = srcs.get("oxa") or {}
    out["oxa_events"] = oxa.get("events")
    ex = oxa.get("extra") or oxa
    if oxa.get("connected"):
        print(f"  {GREEN}ДА {RESET}  oxa: ключей={ex.get('oxa_keys')} "
              f"ликвидаций={ex.get('oxa_liquidations')} запросов={ex.get('oxa_requests')} "
              f"ошибок={ex.get('oxa_errors')} строк={ex.get('oxa_raw_rows')}")
        note(f"режим={ex.get('oxa_mode')} дублей={ex.get('oxa_dupes')} "
             f"несвежих={ex.get('oxa_skipped_stale')} "
             f"кредитов/мес≈{ex.get('oxa_credits_month_eta') or 'накапливается'} "
             f"из {ex.get('oxa_credits_budget')} "
             f"влезает в бюджет: {ex.get('oxa_fits_budget')}")
    else:
        warn(f"oxa не подключён: {oxa.get('last_error') or 'источник выключен'}")
        note("без LIQSCOPE_OXA_KEYS это ожидаемо — проект по умолчанию «без ключей»")

    # Сквозная проверка: доходят ли ликвидации HL до буфера терминала,
    # который читает фронтенд. Это и есть «работает или нет» для пользователя.
    out["live_hl"] = await _live_hl_events(server)
    return out


async def _live_hl_events(server: str) -> dict:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{server}/api/liquidations",
                             params={"exchange": "hyperliquid", "limit": 500},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                rows = (await r.json(content_type=None)).get("liquidations") or []
    except Exception:
        return {}
    now = time.time()
    ages = [now - float(x.get("timestamp") or 0) for x in rows if x.get("timestamp")]
    tape = [x for x in rows if x.get("kind") == "tape"]
    if rows:
        ok(f"в ленте терминала есть гиперликвид: событий {len(rows)} "
           f"(из них выведено из ленты: {len(tape)}), "
           f"свежайшее {min(ages):.0f} с назад")
        note(f"пример: {json.dumps(rows[-1], ensure_ascii=False)[:220]}")
    else:
        warn("в /api/liquidations?exchange=hyperliquid пусто — на экран HL не даёт")
    return {"count": len(rows), "tape": len(tape),
            "newest_age": min(ages) if ages else None}


# ---------------------------------------------------------------------------
#  1) HL REST: доступен ли хост с этого сервера (ключей не нужно)
# ---------------------------------------------------------------------------
async def check_hl_rest() -> list:
    head("2) Hyperliquid REST /info {\"type\":\"meta\"} (без ключей)")
    t0 = time.time()
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": HL_UA}) as s:
            async with s.post(f"{HL_REST}/info", json={"type": "meta"},
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                code = r.status
                meta = await r.json(content_type=None)
    except Exception as e:
        bad(f"запрос не прошёл: {type(e).__name__}: {e}")
        note("частые причины: порт 443 наружу, блокировка IP дата-центра, "
             "прокси. Проверка: "
             "curl -sS -m 8 -X POST https://api.hyperliquid.xyz/info "
             "-H 'Content-Type: application/json' -d '{\"type\":\"meta\"}' -o /dev/null -w '%{http_code}\\n'")
        return []
    ms = int((time.time() - t0) * 1000)
    universe = [str(u.get("name") or "").strip() for u in (meta or {}).get("universe") or []
                if isinstance(u, dict) and u.get("name")]
    if code == 200 and universe:
        ok(f"HTTP {code}, {ms} мс, монет в universe: {len(universe)}")
    else:
        bad(f"HTTP {code}, {ms} мс, монет: {len(universe)}")
    note(f"пример имён (регистр биржи важен!): {', '.join(universe[:8])}")
    return universe


# ---------------------------------------------------------------------------
#  2) HL WS trades: приходит ли лента и есть ли в ней метка liquidation
# ---------------------------------------------------------------------------
async def check_hl_ws(coins: list, seconds: float) -> dict:
    head(f"3) Hyperliquid WS: лента trades по {len(coins)} монетам, {seconds:.0f} с")
    res = {"frames": 0, "trades": 0, "liqs": 0, "acked": 0, "pongs": 0,
           "sent": 0, "fields": Counter(), "closed": None, "sample": None,
           "life": 0.0, "parsed": 0}
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": HL_UA}) as s:
            async with s.ws_connect(HL_WS, heartbeat=None, autoclose=False,
                                    timeout=ClientWSTimeout(ws_close=25)) as ws:
                t0 = time.time()
                res["life"] = seconds
                next_ping = t0 + min(10.0, HL_PING_INTERVAL)

                async def ping():
                    """Тот же keepalive, что у боевого слушателя: биржа рвёт
                    сокет, который молчит 60 с."""
                    nonlocal next_ping
                    while True:
                        await asyncio.sleep(1.0)
                        if time.time() >= next_ping:
                            next_ping = time.time() + HL_PING_INTERVAL
                            try:
                                await ws.send_json({"method": "ping"})
                            except Exception:
                                return

                pinger = asyncio.create_task(ping())
                try:
                    for c in coins:
                        await ws.send_json({"method": "subscribe",
                                            "subscription": {"type": "trades",
                                                             "coin": c}})
                        res["sent"] += 1
                        await asyncio.sleep(HL_SUB_GAP)
                    note(f"подписок отправлено {res['sent']}, жду входящие...")
                    while time.time() - t0 < seconds:
                        try:
                            msg = await ws.receive(timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type is aiohttp.WSMsgType.CLOSE:
                            res["closed"] = f"CLOSE code={msg.data}"
                            break
                        if msg.type in (aiohttp.WSMsgType.CLOSED,
                                        aiohttp.WSMsgType.CLOSING,
                                        aiohttp.WSMsgType.ERROR):
                            res["closed"] = f"{msg.type.name} {msg.data or ''}".strip()
                            break
                        if msg.type is not aiohttp.WSMsgType.TEXT:
                            continue
                        res["frames"] += 1
                        try:
                            p = json.loads(msg.data)
                        except Exception:
                            continue
                        ch = p.get("channel")
                        if ch == "pong":
                            res["pongs"] += 1
                            continue
                        if ch == "subscriptionResponse":
                            res["acked"] += 1
                            continue
                        if ch == "trades":
                            for t in p.get("data") or []:
                                if not isinstance(t, dict):
                                    continue
                                res["trades"] += 1
                                res["fields"][tuple(sorted(t.keys()))] += 1
                                if "liquidation" in t:
                                    res["liqs"] += 1
                                    if res["sample"] is None:
                                        res["sample"] = t
                            # заодно прогоняем через боевой парсер: видно,
                            # сколько бы событий прошло все фильтры
                            res["parsed"] += len(parse_hyperliquid_msg(
                                p, now=time.time(), max_age=HL_FRESH_SEC))
                        elif ch not in ("bbo", "l2Book"):
                            res["fields"][("<канал:", ch)] += 1
                finally:
                    pinger.cancel()
                res["life"] = time.time() - t0
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        bad(f"WS не открылся: {res['error']}")
        return res

    if res["liqs"]:
        ok(f"кадров {res['frames']}, сделок {res['trades']}, "
           f"с меткой liquidation: {res['liqs']} (через парсер: {res['parsed']})")
    elif res["trades"]:
        warn(f"кадров {res['frames']}, сделок {res['trades']}, "
             f"с меткой liquidation: 0 (за {res['life']:.0f} с)")
    else:
        bad(f"кадров {res['frames']}, сделок 0"
            + (f", обрыв: {res['closed']}" if res.get("closed") else ""))
    note(f"подписок подтверждено {res['acked']}/{res['sent']}, pong: {res['pongs']}, "
         f"соединение прожило {res['life']:.0f} с")
    if res["fields"]:
        note("наборы полей в сделках (так видно, что биржа реально отдаёт):")
        for keys, n in res["fields"].most_common(3):
            if keys and keys[0] == "<канал:":
                note(f"    {n:6d}x  только канал {keys[1]}")
            else:
                note(f"    {n:6d}x  {', '.join(keys)}")
    if res["sample"]:
        note(f"пример ликвидации: {json.dumps(res['sample'], ensure_ascii=False)[:400]}")
    return res


# ---------------------------------------------------------------------------
#  3) 0xArchive: живой ли путь по API-ключу
# ---------------------------------------------------------------------------
async def check_oxa(coins: list, hours: float = 24.0) -> dict:
    head("4) 0xArchive — ликвидации HL по API-ключу (рабочий путь)")
    keys = oxa_keys()
    res = {"keys": len(keys), "usable": 0, "rows": 0, "liq": 0, "age": None}
    if not keys:
        warn("ключей нет: LIQSCOPE_OXA_KEYS и OXARCHIVE_API_KEY пусты")
        note("это норма: без ключей проект работает по публичным лентам. "
             "Хочешь проверки HL — задай ключи (https://0xarchive.io):")
        note("  LIQSCOPE_OXA_KEYS=ключ1,ключ2 python3 tools/hl_liq_check.py")
        note("в systemd — строка Environment= в [Service]; см. deploy/licvid.service")
        return res
    coin = (coins or ["BTC"])[0]
    end = time.time()
    params = {"start": int((end - hours * 3600) * 1000), "end": int(end * 1000),
              "limit": 1000}
    for i, key in enumerate(keys, 1):
        url = f"{OXA_REST}/liquidations/{coin}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, params=params, headers={"X-API-Key": key},
                                 timeout=aiohttp.ClientTimeout(total=20)) as r:
                    code = r.status
                    body = await r.text()
        except Exception as e:
            bad(f"ключ #{i} (длина {len(key)}, ...{key[-3:]}): запрос не прошёл — "
                f"{type(e).__name__}: {e}")
            continue
        if code != 200:
            meaning = {401: "ключ не принят (проверь значение и активацию)",
                       403: "ключ не имеет права на маршрут (тариф)",
                       404: "маршрута нет (проверь LIQSCOPE_OXA_REST)",
                       429: "лимит запросов/кредитов на ключ"}.get(code, "")
            bad(f"ключ #{i} (длина {len(key)}, ...{key[-3:]}): HTTP {code} {meaning}".strip())
            note(body[:200])
            continue
        try:
            payload = json.loads(body)
        except Exception:
            bad(f"ключ #{i} (длина {len(key)}, ...{key[-3:]}): HTTP 200, но ответ не JSON: {body[:160]}")
            continue
        stats: dict = {}
        # Боевой парсер, но окно свежести снимаем: сначала надо увидеть ВСЕ
        # строки, которые вернул API, а потом уже посчитать, сколько из них
        # пустили бы в терминал фильтры по возрасту.
        evs = parse_oxa_liquidations(payload, coin_map={coin: f"{coin}_USDT"},
                                    now=time.time(),
                                    max_age=None, stats=stats,
                                    all_rows=True)
        live = parse_oxa_liquidations(payload, coin_map={coin: f"{coin}_USDT"},
                                     now=time.time(),
                                     max_age=oxa_rest_max_age(), all_rows=True)
        res["usable"] += 1
        rows = int(stats.get("raw_rows") or 0)
        res["rows"] += rows
        res["liq"] += len(evs)
        ts_list = [float(e["ts"]) for e in evs if e.get("ts")]
        if ts_list:
            age = time.time() - max(ts_list)
            res["age"] = age if res["age"] is None else min(res["age"], age)
        ok(f"ключ #{i} (длина {len(key)}, ...{key[-3:]}): HTTP 200, строк {rows}, "
           f"разобрано ликвидаций {len(evs)} (монета {coin}, окно {hours:.0f} ч)")
        if evs:
            newest = max(ts_list)
            note(f"свежайшая: {end - newest:.0f} с назад; в терминал с боевым "
                 f"окном свежести ({oxa_rest_max_age():.0f} с) прошли бы "
                 f"{len(live)} из {len(evs)}")
            note(f"пример: {json.dumps(evs[-1], ensure_ascii=False)[:220]}")
        else:
            note(f"строк {rows}, но ни одна не похожа на ликвидацию — формат "
                 f"ответа изменился? Первый кадр: {body[:200]}")
        note(f"это 1 кредит из ~{os.getenv('LIQSCOPE_OXA_CREDITS', '50000')} на ключ в месяц; "
             f"боевой опрос: 1 запрос на монету раз в "
             f"{os.getenv('LIQSCOPE_OXA_POLL_SEC', '120')} с")
    return res


# ---------------------------------------------------------------------------
def verdict(hl_health, ws, rest_ok, oxa) -> int:
    head("ВЫВОД")
    lines = []
    if not rest_ok and "error" in ws and not oxa["usable"]:
        lines.append((RED, "сеть до Hyperliquid/0xArchive с этой машины не проходит — "
                           "ни один источник не может работать. Чинить доступ, "
                           "а не код."))
    if oxa["liq"]:
        lines.append((GREEN, f"путь по API-ключу РАБОТАЕТ: 0xArchive отдал "
                             f"{oxa['liq']} ликвидаций за сутки на монете. "
                             f"Терминал получает их как биржу hyperliquid"))
    elif oxa["usable"]:
        lines.append((YELLOW, "ключи живые (HTTP 200), но строк нет — проверь, что "
                              "монета есть в списке биржи, и попробуй --hours 72"))
    elif oxa["keys"]:
        lines.append((RED, f"из {oxa['keys']} ключей не работает ни один — "
                           f"смотрите HTTP-статусы выше"))
    else:
        lines.append((YELLOW, "ключей нет — путь 0xArchive выключен; в ленте HL "
                              "будет только то, что отдаёт публичный trades"))
    if ws["liqs"]:
        lines.append((GREEN, f"публичный trades отдаёт ликвидации ({ws['liqs']} шт за "
                             f"{ws['life']:.0f} с) — он и наполняет ленту"))
    elif ws["trades"]:
        lines.append((YELLOW, f"публичный trades жив: {ws['trades']} сделок за "
                              f"{ws['life']:.0f} с, а метки liquidation — ноль. По схеме "
                              f"HL поле liquidation есть только у WsFill (приватные "
                              f"userFills по адресу), в WsTrade его нет. Значит либо "
                              f"тихий рынок (слушай дольше или на волатильности), либо "
                              f"публичный поток ликвидаций не отдаёт — тогда путь один: "
                              f"0xArchive по API-ключу (п.4)"))
    else:
        lines.append((RED, "публичный trades не принёс ни одной сделки — смотри "
                           "подписки/обрыв выше (tools/check_hyperliquid.py --bisect)"))
    live = hl_health.get("live_hl") or {}
    if live.get("count"):
        lines.append((GREEN, f"сквозная проверка: терминал отдаёт {live['count']} "
                             f"ликвидаций hyperliquid, свежайшая "
                             f"{live['newest_age']:.0f} с назад — на экране они есть"))
    elif hl_health.get("reachable"):
        lines.append((RED, "сквозная проверка: /api/liquidations?exchange=hyperliquid "
                           "пуст — даже если источник жив, до экрана ничего не дойдёт"
                           " (смотрите hl_skipped_stale и список монет)"))
    if hl_health.get("reachable"):
        lines.append((DIM, "те же цифры в бою: curl -s http://127.0.0.1:8000/api/health "
                           "| python3 -m json.tool"))
    for color, text in lines:
        print(f"  {color}{text}{RESET}")
    if oxa["liq"] or ws["liqs"] or (hl_health.get("live_hl") or {}).get("count"):
        return 0
    if ws["trades"] or oxa["usable"] or hl_health.get("reachable"):
        return 1
    return 2


async def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("--seconds", type=float, default=60.0,
                    help="сколько слушать WS trades (по умолчанию 60)")
    ap.add_argument("--coins", type=int, default=8,
                    help="сколько монет подписать (по умолчанию 8)")
    ap.add_argument("--server", default=os.getenv("LIQSCOPE_HEALTH",
                                                   "http://127.0.0.1:8000"))
    ap.add_argument("--skip-ws", action="store_true", help="не слушать WS")
    ap.add_argument("--skip-oxa", action="store_true", help="не трогать 0xArchive")
    ap.add_argument("--hours", type=float, default=24.0,
                    help="окно истории для проверки ключей 0xArchive")
    args = ap.parse_args()

    print(f"Проверка Hyperliquid-ликвидаций · {time.strftime('%F %T')} · "
          f"HL={HL_REST}")
    health = await check_health(args.server)
    universe = await check_hl_rest()

    # Монеты для подписки: имя берётся ИЗ UNIVERSE (регистр биржи: kPEPE, а не
    # KPEPE — на неверное имя HL закрывает весь сокет)
    coins = [c for c in MAJORS if c in set(universe)] or list(universe)[:args.coins]
    coins = coins[:args.coins]
    if universe:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{args.server}/api/symbols",
                                 timeout=aiohttp.ClientTimeout(total=6)) as r:
                    syms = ((await r.json(content_type=None)) or {}).get("symbols") or []
            mapped = sorted(hl_coin_map(syms, universe))
            if mapped:
                coins = [c for c in mapped if c in coins] or mapped[:args.coins]
        except Exception:
            pass

    ws = {"frames": 0, "trades": 0, "liqs": 0, "acked": 0, "pongs": 0,
          "sent": 0, "fields": Counter(), "life": 0.0, "parsed": 0,
          "error": "пропущено (--skip-ws)"}
    if args.skip_ws or not coins:
        head("3) Hyperliquid WS: пропущен" if args.skip_ws else
             "3) Hyperliquid WS: монет для подписки нет")
        if not coins and not args.skip_ws:
            bad("universe пуст — подписываться не на что (см. п.2)")
    else:
        ws.update(await check_hl_ws(coins, args.seconds))

    oxa = {"keys": 0, "usable": 0, "rows": 0, "liq": 0, "age": None}
    if args.skip_oxa:
        head("4) 0xArchive: пропущен (--skip-oxa)")
    else:
        oxa.update(await check_oxa(coins or ["BTC"], args.hours))

    return verdict(health, ws, bool(universe), oxa)


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
