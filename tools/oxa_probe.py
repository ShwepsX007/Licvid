#!/usr/bin/env python3
"""
Зонд 0xArchive (https://0xarchive.io) — индексатор Hyperliquid.

Зачем: 0xArchive — единственный известный способ получить ликвидации
Hyperliquid БЕЗ денег (в отличие от GoldRush, где ключ платный). Но у
бесплатного тарифа жёсткие рамки, и их надо проверить своими глазами:

  * 10 WS-подписок и 2 соединения (docs → websocket/tier-limits);
  * 50 000 кредитов в месяц, причём **каждое WS-сообщение = 1 кредит**
    (pricing → FAQ). То есть ~1 660 сообщений в сутки на ВСЁ;
  * канал `liquidations` подписывается ПО МОНЕТЕ — 10 подписок = 10 монет;
  * история бесплатного тарифа — последние 30 дней (окно ≤ 30 дней);
  * ключ в WS передаётся заголовком Authorization: Bearer, поэтому
    из браузера напрямую не подключиться — только с сервера.

Что делает зонд:
  1. REST  — GET /v1/hyperliquid/liquidations/{coin} за последние часы:
             сколько событий, какие поля, насколько свежие;
  2. WS    — wss://api.0xarchive.io/ws, подписка liquidations по монетам:
             сколько сообщений и заполнений пришло, задержка до события,
             расход кредитов в час/сутки и хватит ли месячного лимита.

Ключ берётся из переменной OXARCHIVE_API_KEY (или --key). Ключ нигде не
печатается.

Запуск:
    OXARCHIVE_API_KEY=ox_... python3 tools/oxa_probe.py --rest BTC ETH
    OXARCHIVE_API_KEY=ox_... python3 tools/oxa_probe.py BTC ETH SOL 300
    python3 tools/oxa_probe.py --selftest      # офлайн, ключ не нужен
"""

import asyncio
import json
import os
import sys
import time

BASE = os.getenv("OXA_REST", "https://api.0xarchive.io/v1/hyperliquid")
WS_URL = os.getenv("OXA_WS", "wss://api.0xarchive.io/ws")
FREE_CREDITS = 50_000            # кредитов в месяц на бесплатном тарифе
FREE_SUBS = 10                   # подписок WS на бесплатном тарифе


# ---------------------------------------------------------------------------
# Разбор ответа. Вынесено в функции ради --selftest: сеть для них не нужна.
# ---------------------------------------------------------------------------
def parse_liq_rows(payload):
    """Достаёт строки ликвидаций из кадра WS или тела REST.

    По документации каждое событие ликвидации — это строка заполнения
    (fill) с признаком is_liquidation: true, то есть по форме совпадает
    со сделкой. Поля могут прийти как px/sz/side/time, поэтому имена
    перебираются.
    """
    rows = []
    if isinstance(payload, dict):
        for key in ("data", "rows", "liquidations", "result"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return rows
    for row in payload:
        if not isinstance(row, dict):
            continue
        ev = parse_liq_row(row)
        if ev is not None:
            rows.append(ev)
    return rows


def parse_liq_row(row):
    """Одна строка заполнения → наше событие, либо None (не ликвидация)."""
    flag = row.get("is_liquidation")
    if flag is None:
        # REST может отдать поле direction/method вместо признака
        flag = bool(row.get("liquidation") or row.get("liquidator")
                    or row.get("direction") or row.get("method"))
    if not flag:
        return None
    try:
        price = float(row.get("px") or row.get("price") or 0)
        qty = float(row.get("sz") or row.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if price <= 0 or qty <= 0:
        return None
    ts = row.get("time") or row.get("timestamp") or row.get("ts")
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts > 1e12:                      # миллисекунды
        ts /= 1000.0
    side = str(row.get("side") or row.get("direction") or "").upper()
    # тейкер продал (A/SELL) → вынесли LONG, как во всех наших источниках
    position = "LONG" if side.startswith(("A", "S")) else \
        "SHORT" if side.startswith(("B", "L")) else ""
    return {
        "coin": str(row.get("coin") or row.get("symbol") or ""),
        "price": price, "qty": qty, "usd": price * qty,
        "ts": ts or time.time(), "position": position,
        "keys": sorted(row.keys()),
    }


def credit_forecast(msgs, seconds):
    """Сколько кредитов съест поток при таком темпе."""
    if seconds <= 0:
        return {}
    per_hour = msgs * 3600.0 / seconds
    per_day = per_hour * 24
    return {
        "per_hour": round(per_hour, 1),
        "per_day": round(per_day),
        "per_month": round(per_day * 30),
        "fits_free_month": per_day * 30 <= FREE_CREDITS,
        "days_until_free_limit": round(FREE_CREDITS / per_day, 1) if per_day else None,
    }


# ---------------------------------------------------------------------------
async def probe_rest(coins, hours, key, verbose=True):
    import aiohttp
    end = int(time.time() * 1000)
    start = end - int(hours * 3600 * 1000)
    total = 0
    async with aiohttp.ClientSession() as s:
        for coin in coins:
            url = f"{BASE}/liquidations/{coin}"
            params = {"start": start, "end": end, "limit": 1000}
            try:
                async with s.get(url, params=params,
                                 headers={"X-API-Key": key},
                                 timeout=aiohttp.ClientTimeout(total=20)) as r:
                    body = await r.json(content_type=None)
                    if r.status != 200:
                        print(f"  {coin:<8} HTTP {r.status}: {str(body)[:160]}")
                        continue
            except Exception as e:
                print(f"  {coin:<8} ошибка: {type(e).__name__}: {e}")
                continue
            rows = parse_liq_rows(body)
            total += len(rows)
            if not rows:
                print(f"  {coin:<8} событий 0 за {hours} ч (ответ: "
                      f"{str(body)[:120]})")
                continue
            fresh = min(time.time() - r_["ts"] for r_ in rows)
            usd = sum(r_["usd"] for r_ in rows)
            print(f"  {coin:<8} событий {len(rows):>5}  сумма ${usd:,.0f}  "
                  f"самое свежее {fresh:,.0f} с назад  "
                  f"сторона: {sorted({r_['position'] for r_ in rows if r_['position']})}")
            if verbose and rows:
                print(f"           поля: {', '.join(rows[0]['keys'])}")
    print(f"  итого событий: {total}")
    return total


async def probe_ws(coins, seconds, key):
    import aiohttp
    if len(coins) > FREE_SUBS:
        print(f"  внимание: монет {len(coins)}, а бесплатных подписок "
              f"{FREE_SUBS} — часть будет отклонена")
    stats = {"messages": 0, "fills": 0, "errors": 0, "gaps": 0,
             "per_coin": {}, "first_at": None, "latency": []}
    subs_acked = 0

    async with aiohttp.ClientSession() as s:
        try:
            async with s.ws_connect(
                    WS_URL, headers={"Authorization": f"Bearer {key}"},
                    timeout=25) as ws:
                for coin in coins:
                    await ws.send_json({"op": "subscribe",
                                        "channel": "liquidations",
                                        "symbol": coin})
                print(f"  подписок отправлено: {len(coins)}; слушаю {seconds} с")

                async def pinger():
                    while True:
                        await asyncio.sleep(25)
                        await ws.send_json({"op": "ping"})

                p = asyncio.create_task(pinger())
                deadline = time.time() + seconds
                try:
                    while time.time() < deadline:
                        try:
                            msg = await ws.receive(timeout=2.0)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED,
                                            aiohttp.WSMsgType.CLOSING,
                                            aiohttp.WSMsgType.ERROR):
                                print(f"  соединение закрыто: {msg.type}")
                                break
                            continue
                        try:
                            data = json.loads(msg.data)
                        except Exception:
                            continue
                        kind = data.get("type")
                        if kind == "subscribed":
                            subs_acked += 1
                        elif kind == "error":
                            stats["errors"] += 1
                            if stats["errors"] <= 3:
                                print(f"  ошибка сервера: "
                                      f"{str(data.get('message'))[:160]}")
                        elif kind == "gap_detected":
                            stats["gaps"] += 1
                        elif kind == "data":
                            stats["messages"] += 1
                            if stats["first_at"] is None:
                                stats["first_at"] = time.time()
                            coin = str(data.get("symbol") or data.get("coin")
                                       or "")
                            rows = parse_liq_rows(data)
                            stats["fills"] += len(rows)
                            cell = stats["per_coin"].setdefault(
                                coin, {"messages": 0, "fills": 0, "usd": 0.0})
                            cell["messages"] += 1
                            cell["fills"] += len(rows)
                            now = time.time()
                            for r in rows:
                                cell["usd"] += r["usd"]
                                stats["latency"].append(now - r["ts"])
                finally:
                    p.cancel()
        except Exception as e:
            print(f"  не удалось подключиться: {type(e).__name__}: {e}")
            return stats

    print(f"  подтверждено подписок: {subs_acked}/{len(coins)}")
    print(f"  сообщений WS: {stats['messages']}  (столько же кредитов)")
    print(f"  строк ликвидаций: {stats['fills']}   ошибок: {stats['errors']}"
          f"   gap_detected: {stats['gaps']}")
    if stats["latency"]:
        lat = sorted(stats["latency"])
        print(f"  задержка до события: медиана {lat[len(lat)//2]:.2f} с, "
              f"макс {lat[-1]:.2f} с")
    for coin, c in sorted(stats["per_coin"].items(),
                          key=lambda kv: -kv[1]["fills"]):
        print(f"    {coin or '?':<8} сообщ. {c['messages']:>4}  "
              f"ликв. {c['fills']:>4}  ${c['usd']:,.0f}")
    fc = credit_forecast(stats["messages"], seconds)
    if fc:
        print(f"  расход: {fc['per_hour']}/ч, {fc['per_day']}/сутки, "
              f"~{fc['per_month']}/мес при лимите {FREE_CREDITS}")
        print(f"  {'хватает' if fc['fits_free_month'] else 'НЕ хватает'} "
              f"бесплатного тарифа"
              + (f" (лимит выберется за {fc['days_until_free_limit']} дн.)"
                 if fc["days_until_free_limit"] else ""))
    return stats


# ---------------------------------------------------------------------------
def selftest():
    """Офлайн-проверка разбора: сеть и ключ не нужны."""
    ok = fail = 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  ok   {name}")
        else:
            fail += 1
            print(f"  FAIL {name} {detail}")

    print("selftest разбора ликвидаций 0xArchive")
    # WS-кадр: список заполнений с is_liquidation
    ws_frame = {"type": "data", "channel": "liquidations", "symbol": "BTC",
                "data": [
                    {"coin": "BTC", "px": "60000", "sz": "0.5", "side": "A",
                     "time": 1789300000000, "is_liquidation": True,
                     "user_address": "0xabc"},
                    {"coin": "BTC", "px": "60100", "sz": "0.1", "side": "B",
                     "time": 1789300000100, "is_liquidation": True},
                    {"coin": "BTC", "px": "60200", "sz": "0.2", "side": "B",
                     "time": 1789300000200, "is_liquidation": False},
                ]}
    rows = parse_liq_rows(ws_frame)
    check("только is_liquidation=true", len(rows) == 2, rows)
    check("сторона A → вынесли LONG", rows[0]["position"] == "LONG", rows[0])
    check("сторона B → вынесли SHORT", rows[1]["position"] == "SHORT", rows[1])
    check("мс → секунды", abs(rows[0]["ts"] - 1789300000.0) < 1e-6, rows[0])
    check("сумма в USD", abs(rows[0]["usd"] - 30000.0) < 1e-6, rows[0])
    check("поля видны", "px" in rows[0]["keys"], rows[0]["keys"])

    # REST-форма: direction вместо признака
    rest = {"data": [{"coin": "ETH", "price": 3000, "size": 2,
                       "direction": "Long", "timestamp": 1789300000}]}
    rows = parse_liq_rows(rest)
    check("REST: direction=Long распознан", len(rows) == 1, rows)
    check("REST: поля price/size приняты",
          rows and rows[0]["price"] == 3000 and rows[0]["qty"] == 2, rows)

    check("мусор игнорируется", parse_liq_rows({"data": [1, "x", None]}) == [])
    check("не словарь игнорируется", parse_liq_rows("nope") == [])
    check("нулевая цена отброшена",
          parse_liq_rows([{"is_liquidation": True, "px": 0, "sz": 1}]) == [])

    fc = credit_forecast(100, 60)          # 100 сообщений в минуту
    check("прогноз: 6000/час", fc["per_hour"] == 6000.0, fc)
    check("прогноз: 144000/сутки", fc["per_day"] == 144000, fc)
    check("прогноз: бесплатный лимит не вытягивает",
          fc["fits_free_month"] is False, fc)
    fc2 = credit_forecast(1, 3600)         # 1 сообщение в час
    check("прогноз: редкий поток влезает", fc2["fits_free_month"] is True, fc2)
    check("прогноз: ноль сообщений — без деления на ноль",
          credit_forecast(0, 60)["days_until_free_limit"] is None)

    print(f"\nитог: {ok} ок, {fail} ошибок")
    return fail


async def main():
    args = [a for a in sys.argv[1:]]
    if "--selftest" in args:
        sys.exit(1 if selftest() else 0)

    key = os.getenv("OXARCHIVE_API_KEY", "")
    if "--key" in args:
        i = args.index("--key")
        key = args[i + 1]
        del args[i:i + 2]
    mode_rest = "--rest" in args
    if mode_rest:
        args.remove("--rest")

    coins, seconds = [], 180
    for a in args:
        if a.isdigit():
            seconds = int(a)
        else:
            coins.append(a)
    coins = coins or ["BTC", "ETH"]

    if not key:
        print("нужен ключ: OXARCHIVE_API_KEY=ox_... python3 tools/oxa_probe.py "
              "BTC ETH 180   (или --selftest без ключа)")
        sys.exit(2)

    print(f"0xArchive: REST {BASE}")
    print(f"           WS   {WS_URL}")
    print(f"           монет {len(coins)}: {', '.join(coins)}")
    if mode_rest:
        print("\n[1] REST: сколько ликвидаций уже есть в архиве")
        await probe_rest(coins, max(1, seconds / 3600.0 * 24) or 24, key)
        return
    print(f"\n[1] WS: слушаю {seconds} с, считаю сообщения и кредиты")
    await probe_ws(coins, seconds, key)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nпрервано")
