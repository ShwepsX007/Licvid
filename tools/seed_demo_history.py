"""Демо-история за месяц: часовые свёртки и ряд OI без реальных бирж.

Нужен для локального просмотра и демо-стенда: сервисы «Корреляции валют» и
«Сторож монет» показывают картину только тогда, когда есть история. Скрипт
пишет те же файлы, что и рабочий HistoryStore — ``data/hours_2026-09-17.json``
(часовые свёртки: ликвидации по монетам и биржам, CVD, объём) — и ряд OI в
``data/oi_history.json``. Сырых событий не пишем: их даёт живой поток.

Запуск: /tmp/venv/bin/python tools/seed_demo_history.py [--days 31] [--dir data]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from history import HOUR, day_key, hour_start, next_day  # noqa: E402

# Монеты демо-стенда и их «характер»: база цены, средний оборот за час и
# склонность к пампам. Коррелированные пары задают связность рынка: BTC с ETH,
# SOL с AVAX, DOGE с WIF и так далее — чтобы тепловая карта не была шумом.
COINS = {
    "BTC_USDT": (96000.0, 9.0e8),
    "ETH_USDT": (3300.0, 5.0e8),
    "SOL_USDT": (190.0, 2.4e8),
    "XRP_USDT": (2.30, 1.1e8),
    "DOGE_USDT": (0.32, 8.0e7),
    "BNB_USDT": (690.0, 1.4e8),
    "ADA_USDT": (0.95, 5.0e7),
    "AVAX_USDT": (38.0, 6.0e7),
    "LINK_USDT": (22.0, 7.0e7),
    "TON_USDT": (5.4, 4.5e7),
    "SUI_USDT": (4.4, 6.5e7),
    "WIF_USDT": (2.10, 3.0e7),
}
FACTORS = [0.0, 0.55, 0.85, 0.2]          # общий рынок, альты, мемы, своё
CLUSTER = {"BTC_USDT": 0, "ETH_USDT": 0, "BNB_USDT": 0, "SOL_USDT": 1,
           "AVAX_USDT": 1, "SUI_USDT": 1, "LINK_USDT": 1, "XRP_USDT": 1,
           "ADA_USDT": 1, "DOGE_USDT": 2, "WIF_USDT": 2, "TON_USDT": 3}
EXCHANGES = ["BINANCE", "BYBIT", "OKX", "GATE", "BITGET"]


def make_hours(day: str, now: float, rnd: random.Random) -> dict:
    """Часовые ячейки одного дня: до текущего часа включительно."""
    cells: dict = {}
    start = hour_start(time.mktime(time.strptime(day, "%Y-%m-%d")))
    for hh in range(24):
        h = int(start + hh * HOUR)
        if h > now:
            break
        market = rnd.gauss(0.0, 0.9)                     # общий тон часа
        alt = market * 0.85 + rnd.gauss(0.0, 0.5)
        mem = market * 0.5 + rnd.gauss(0.0, 1.4)
        own = rnd.gauss(0.0, 1.0)
        cell = {"liq_usd": 0.0, "liq_count": 0, "liq_long": 0.0, "liq_short": 0.0,
                "max_usd": 0.0, "max_symbol": "", "cvd": 0.0, "vol": 0.0,
                "has_cvd": True, "sym": {}, "exch": {}}
        for sym, (price, base_vol) in COINS.items():
            c = CLUSTER[sym]
            tone = (market, alt, mem, own)[c % len(FACTORS) if c else 0] if c else market
            tone = {"0": market, "1": alt, "2": mem, "3": own}.get(str(c), market)
            vol = base_vol * rnd.uniform(0.7, 1.35) * (1 + 0.25 * abs(tone))
            cvd = vol * max(-0.45, min(0.45, tone * 0.05 + rnd.gauss(0, 0.03)))
            # ликвидации вспыхивают, когда цена идёт против толпы
            burst = max(0.0, tone) * rnd.uniform(4e5, 3.5e6) + rnd.uniform(0, 6e5)
            longs = burst * rnd.uniform(0.35, 1.5) if tone < 0 else burst * 0.4
            shorts = burst * rnd.uniform(0.35, 1.5) if tone > 0 else burst * 0.4
            longs, shorts = abs(longs), abs(shorts)
            n = int(max(1, (longs + shorts) / 4.0e5))
            cell["sym"][sym] = {"usd": round(longs + shorts, 2), "n": n,
                                "long": round(longs, 2), "short": round(shorts, 2),
                                "cvd": round(cvd, 2), "vol": round(vol, 2)}
            cell["liq_usd"] += longs + shorts
            cell["liq_count"] += n
            cell["liq_long"] += longs
            cell["liq_short"] += shorts
            cell["cvd"] += cvd
            cell["vol"] += vol
            if longs + shorts > cell["max_usd"]:
                cell["max_usd"] = round(longs + shorts, 2)
                cell["max_symbol"] = sym
        for exch in EXCHANGES:
            share = rnd.uniform(0.05, 0.4)
            cell["exch"][exch] = {"usd": round(cell["liq_usd"] * share, 2),
                                  "n": int(cell["liq_count"] * share)}
        cells[str(h)] = cell
    return cells


def make_oi(days: int, now: float, step_min: int = 30) -> dict:
    """Ряд OI: разреженные срезы по монетам (тот же формат, что OiHistory.save)."""
    series = {}
    points = int(days * 24 * 60 / step_min)
    for i, (sym, (price, base_vol)) in enumerate(COINS.items()):
        level = base_vol * rnd_uniform(6.0, 14.0)
        row = []
        for k in range(points):
            ts = now - (points - k) * step_min * 60
            level = max(level * (1 + random.gauss(0.0, 0.012)), 1e5)
            row.append([int(ts), round(level, 2)])
        series[sym] = row
        del price, i
    return {"series": series, "saved": now}


def rnd_uniform(a: float, b: float) -> float:
    return a + (b - a) * random.random()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=31)
    ap.add_argument("--dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--seed", type=int, default=20260917)
    ap.add_argument("--oi", action="store_true", help="перезаписать и ряд OI")
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    random.seed(args.seed)
    now = time.time()
    os.makedirs(args.dir, exist_ok=True)
    written = 0
    day = day_key(now - (args.days - 1) * 24 * HOUR)
    last = day_key(now)
    while day <= last:
        path = os.path.join(args.dir, f"hours_{day}.json")
        cells = make_hours(day, now, rnd)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cells, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        written += 1
        day = next_day(day)
    print(f"часовые свёртки: {written} дней в {args.dir}")

    if args.oi:
        path = os.path.join(args.dir, "oi_history.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(make_oi(args.days, now), f, ensure_ascii=False,
                      separators=(",", ":"))
        print(f"ряд OI: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
