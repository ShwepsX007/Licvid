#!/usr/bin/env python3
"""Диагностика риск-лимитов: ступени маржи и плечи по монете — без сервера.

Уровни ликвидаций считаются от поддерживающей маржи (MMR) и плеча, а эти
числа дают биржи. Скрипт дёргает те же публичные ручки, что и сервер, и
печатает, что именно отдала каждая биржа: сколько ступеней, какая маржа от
ноционала и до какого плеча вообще пускают.

Запуск:
    python3 tools/check_risk_limits.py                 # монеты по умолчанию
    python3 tools/check_risk_limits.py BTC ETH SOL     # свои монеты
    python3 tools/check_risk_limits.py BTC --json      # машинный вывод

Сеть нужна только здесь: в сервере те же данные берёт ``RiskLimits.ensure``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from risk_limit import EXCHANGES, RiskLimits, default_mmr, tier_mmr  # noqa: E402

DEFAULT_COINS = ("BTC", "ETH", "SOL", "DOGE")


def _symbol(coin: str) -> str:
    c = str(coin or "").strip().upper().replace("-", "_").replace("/", "_")
    if not c:
        return ""
    return c if "_" in c else c + "_USDT"


def _row(tiers: list, notionals=(0.0, 10_000.0, 100_000.0, 1_000_000.0)) -> str:
    """Маржа на нескольких ноционалах — видно, где включаются ступени."""
    parts = []
    for n in notionals:
        rate = tier_mmr(tiers, n)
        # ступеней нет — так и пишем: пустая строка не должна выглядеть нулём
        parts.append(f"{n / 1000:.0f}K:" +
                     ("—" if rate is None else f"{rate * 100:.3f}%"))
    return " ".join(parts)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Риск-лимиты бирж: ступени и плечи")
    ap.add_argument("coins", nargs="*", default=list(DEFAULT_COINS),
                    help="монеты (BTC или BTC_USDT); по умолчанию BTC ETH SOL DOGE")
    ap.add_argument("--json", action="store_true", help="вывести JSON как есть")
    ap.add_argument("--ttl", type=float, default=0.0, help="перезапросить (TTL=0)")
    args = ap.parse_args()

    limits = RiskLimits(ttl=max(1.0, args.ttl) if args.ttl else None)
    coins = [_symbol(c) for c in (args.coins or DEFAULT_COINS)]
    coins = [c for c in coins if c]
    print(f"риск-лимиты: {len(EXCHANGES)} бирж ({', '.join(EXCHANGES)})")
    print(f"монеты: {', '.join(coins)}")
    print(f"запасной MMR, если биржа молчит: {default_mmr('BTC_USDT') * 100:.2f}% "
          f"(мажор) / {default_mmr('PEPE_USDT') * 100:.2f}% (альт)")

    import aiohttp

    async with aiohttp.ClientSession() as session:
        for sym in coins:
            snapshot = await limits.ensure(session, sym, force=True)
            if args.json:
                print(json.dumps(snapshot, ensure_ascii=False, indent=2))
                continue
            rate, estimated = limits.mmr_or_default(sym)
            top = limits.max_leverage(sym)
            print(f"\n=== {sym} ===")
            print(f"  средний MMR: {rate * 100:.3f}%"
                  f"{' (оценка: ступеней нет)' if estimated else ''}"
                  f" · предельное плечо: {('до ' + str(top) + 'x') if top else '—'}")
            for exch, venue in sorted((snapshot or {}).items()):
                tiers = venue.get("tiers") or []
                if not tiers:
                    print(f"  {exch:<10} — {'ошибка: ' + str(venue.get('error'))[:60] if venue.get('error') else 'ступеней нет'}")
                    continue
                mark = "оценка" if venue.get("estimated") else (venue.get("source") or "")
                print(f"  {exch:<10} ступеней {len(tiers):>3} · {mark}")
                print(f"      маржа: {_row(tiers)}")
                print(f"      плечи: {', '.join(str(int(t.get('max_leverage') or 0)) for t in tiers[:6])}"
                      + (" …" if len(tiers) > 6 else ""))
    print("\nготово: сервер возьмёт эти же данные через RiskLimits.ensure (кэш data/risk_limits.json)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
