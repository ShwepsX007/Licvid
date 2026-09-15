#!/usr/bin/env python3
"""Зонд вывода ликвидаций Hyperliquid из публичной ленты сделок.

Двигает ровно тот же код, что работает в терминале (`hl_infer.py`), только без
FastAPI: удобно проверить идею на проде, покрутить пороги и посмотреть, сколько
это стоит REST-запросов, не перезапуская прод. Логика общая — расхождение
«в зонде работало, в терминале нет» исключено by design.

    venv/bin/python3 tools/hl_liq_infer.py                     # 10 мин, всё по дефолтам
    venv/bin/python3 tools/hl_liq_infer.py --minutes 30 --min-usd 10000
    venv/bin/python3 tools/hl_liq_infer.py --dry               # только кандидаты, без /info
    venv/bin/python3 tools/hl_liq_infer.py --coins BTC,ETH --max-confirm 30

Код выхода: 0 — есть хотя бы одно подтверждённое событие, 1 — соединение живое,
подтверждений нет, 2 — не работает вовсе (сеть/подписки).

Ограничение метода (и любое другое на публичных данных HL не даст больше):
ADL и backstop-передачу позиции в liquidator vault увидеть нельзя — они не
создают сделок в стакане.
"""

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from aiohttp import ClientWSTimeout

from hl_infer import (HL_INFER_CONFIRM_TIMEOUT, HL_INFER_MAX_CONFIRM,
                      HL_INFER_MIN_SCORE, HL_INFER_MIN_USD,
                      HL_INFER_WINDOW_SEC, HlLiquidationInferer)
from market_feed import HL_UA

HL_REST = os.getenv("LIQSCOPE_HL_REST", "https://api.hyperliquid.xyz")
HL_WS = os.getenv("LIQSCOPE_HL_WS", "wss://api.hyperliquid.xyz/ws")
MAJORS = ["BTC", "ETH", "SOL", "XRP", "HYPE", "DOGE", "SUI", "BNB", "LINK",
          "AVAX", "NEAR", "ARB", "ADA", "WLD", "PUMP", "TAO", "ZEC", "kPEPE"]
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class DryInferer(HlLiquidationInferer):
    """Тот же детектор, но кандидатов не подтверждает — считать цену метода."""

    async def _confirm(self, burst: dict) -> None:
        rows = burst["rows"]
        print(f"{YELLOW}[кандидат]{RESET} {self.coin_name(burst['coin'])} "
              f"{rows[0]['position']} {burst['usd']:,.0f}$ score={burst['score']:.1f} "
              f"({len(rows)} филa) — {', '.join(burst['why'])}")
        self.stats["budget_skipped"] += 1


async def main(args) -> int:
    cls = DryInferer if args.dry else HlLiquidationInferer
    net_fail = False
    async with aiohttp.ClientSession(headers={"User-Agent": HL_UA}) as session:
        # 1) имена монет — ровно те, что знает биржа (регистр: kPEPE, не KPEPE)
        try:
            async with session.post(f"{HL_REST}/info", json={"type": "meta"},
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                meta = await r.json(content_type=None)
            universe = [str(u.get("name") or "").strip()
                        for u in (meta or {}).get("universe") or []
                        if isinstance(u, dict) and u.get("name")]
        except Exception as e:
            print(f"{RED}нет доступа к HL REST: {type(e).__name__}: {e}{RESET}")
            return 2
        want = [c.strip() for c in args.coins.split(",") if c.strip()]
        known = set(universe)
        coins = want or [c for c in MAJORS if c in known] or universe[:10]
        coins = [c for c in coins if c in known] or coins
        coin_map = {c: f"{c}_USDT" for c in coins}

        events = []

        async def on_event(ev: dict):
            events.append(ev)
            print(f"{GREEN}ЛИКВИДАЦИЯ{RESET} {ev['symbol']} {ev['side']} "
                  f"{ev['usd']:,.0f}$ @ {ev['price']:.6g} · "
                  f"{json.dumps(ev['liquidation'], ensure_ascii=False)}")

        inferer = cls(coin_map, session, on_event, hl_rest=HL_REST,
                      max_age=args.max_age, min_usd=args.min_usd,
                      window=args.window, min_score=args.min_score,
                      max_confirm_per_min=args.max_confirm,
                      confirm_timeout=HL_INFER_CONFIRM_TIMEOUT)
        worker = asyncio.create_task(inferer.worker())
        print(f"монет {len(coins)}: {', '.join(coins)}\n"
              f"слушаю {args.minutes:.1f} мин · порог ${args.min_usd:,.0f}, "
              f"окно {args.window:g}с, score>={args.min_score:g}, "
              f"подтверждений до {args.max_confirm}/мин"
              + (" · РЕЖИМ DRY: /info не дёргаю" if args.dry else ""))
        t_end = time.time() + args.minutes * 60
        subs_ok = 0
        try:
            while time.time() < t_end:
                try:
                    async with session.ws_connect(
                            HL_WS, heartbeat=None,
                            timeout=ClientWSTimeout(ws_close=25)) as ws:
                        for c in coins:
                            await ws.send_json({"method": "subscribe",
                                                "subscription": {"type": "trades",
                                                                 "coin": c}})
                            await asyncio.sleep(0.08)
                        print("подписался, лента пошла...", flush=True)
                        while time.time() < t_end:
                            try:
                                msg = await ws.receive(timeout=20)
                            except asyncio.TimeoutError:
                                await ws.send_json({"method": "ping"})
                                continue
                            if msg.type is not aiohttp.WSMsgType.TEXT:
                                break
                            try:
                                payload = json.loads(msg.data)
                            except Exception:
                                continue
                            if payload.get("channel") == "subscriptionResponse":
                                subs_ok += 1
                                continue
                            inferer.observe(payload)
                            inferer.sweep()
                            inferer.maybe_log()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    net_fail = True
                    print(f"{YELLOW}сокет упал ({type(e).__name__}: {e}) — "
                          f"переподключение через 5 с{RESET}")
                    await asyncio.sleep(5)
        finally:
            await inferer.aclose()
            worker.cancel()
            try:
                await worker
            except (asyncio.CancelledError, Exception):
                pass

    s = inferer.stats
    print(f"\n{DIM}подписок подтверждено {subs_ok}/{len(coins)}{RESET}")
    print(f"=== ИТОГ: сделок {s['trades']}, кандидатов {s['candidates']}, "
          f"подтверждено {s['confirmed']} на {s['usd']:,.0f}$, "
          f"отклонено {s['rejected']}, вне бюджета {s['budget_skipped']}, "
          f"очередь переполнялась {s['queue_skipped']} раз, "
          f"/info: {s['info_calls']} вызовов ({s['info_errors']} ошибок), "
          f"латентность {s['latency_ms']:.0f} мс")
    if args.dry:
        print("режим dry: кандидаты не проверялись — «отклонено» тут означает "
              "«напечатано», а не «не ликвидация»")
        return 0 if s["candidates"] else 1
    if s["confirmed"]:
        cost = s["info_calls"] / max(1e-9, args.minutes) * 60
        print(f"{GREEN}схема работает{RESET}: средняя цена — {cost:.1f} запросов "
              f"/info в минуту при этом потоке; боевые настройки — "
              f"LIQSCOPE_HL_INFER_MIN_USD={args.min_usd:g} "
              f"LIQSCOPE_HL_INFER_MAX_CONFIRM_PER_MIN={args.max_confirm}")
        return 0
    if s["candidates"]:
        print(f"{YELLOW}кандидаты есть, подтверждений нет{RESET}: либо рынок "
              f"спокойный, либо /info не отвечает (ошибок {s['info_errors']}), "
              f"либо метку ликвидаций биржа не отдаёт даже по адресу")
        return 1
    print(f"{RED}подтверждённых нет{RESET}: увеличьте время/снизьте --min-usd; "
          f"сеть {'недоступна' if net_fail else 'в порядке'}")
    return 2 if net_fail else 1


def run():
    ap = argparse.ArgumentParser(description="зонд вывода ликвидаций HL из ленты")
    ap.add_argument("--minutes", type=float, default=10.0,
                    help="сколько слушать trades (по умолчанию 10)")
    ap.add_argument("--coins", default="", help="BTC,ETH (по умолчанию крупные)")
    ap.add_argument("--min-usd", type=float, default=HL_INFER_MIN_USD,
                    help="порог нотионала серии, по умолчанию %.0f" % HL_INFER_MIN_USD)
    ap.add_argument("--window", type=float, default=HL_INFER_WINDOW_SEC,
                    help="окно склейки всплеска по одному адресу, %.1f с" % HL_INFER_WINDOW_SEC)
    ap.add_argument("--min-score", type=float, default=HL_INFER_MIN_SCORE,
                    help="порог очков кандидата, %.1f" % HL_INFER_MIN_SCORE)
    ap.add_argument("--max-confirm", type=int, default=HL_INFER_MAX_CONFIRM,
                    help="бюджет подтверждений /info в минуту, %d" % HL_INFER_MAX_CONFIRM)
    ap.add_argument("--max-age", type=float, default=120.0,
                    help="старее этого возраста всплески не подтверждаем (срез истории)")
    ap.add_argument("--dry", action="store_true",
                    help="только кандидаты, без единого запроса /info")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(args)))
    except KeyboardInterrupt:
        print("\nостановлено")
        sys.exit(0)


if __name__ == "__main__":
    run()
