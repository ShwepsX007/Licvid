"""Помощники диагностики риск-лимитов: имена монет и разбор ступеней.

``tools/check_risk_limits.py`` печатает то же, что сервер берёт для расчёта
уровней. Проверяем то, что можно проверить без сети: монета из «btc» и
«BTC/USDT» превращается в канон, а строка ступеней считает маржу по
ноционалу — по ней глазами видно, где включается следующая ступень.

Сеть не нужна.  Запуск:  python3 tests/test_check_risk_limits.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import check_risk_limits as crl  # noqa: E402

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}  [{detail}]")


print("имена монет")
check("тикер → канон", crl._symbol("btc") == "BTC_USDT")
check("пара как есть", crl._symbol("eth_usdt") == "ETH_USDT")
check("слэш и дефис", crl._symbol("sol/usdt") == "SOL_USDT"
      and crl._symbol("doge-usdt") == "DOGE_USDT")
check("пустое имя отбрасывается", crl._symbol("   ") == "")

print("строка ступеней")
tiers = [
    {"max_leverage": 125, "mmr": 0.004, "max_notional": 50_000},
    {"max_leverage": 100, "mmr": 0.005, "max_notional": 250_000},
    {"max_leverage": 50, "mmr": 0.01, "max_notional": 1_000_000},
]
row = crl._row(tiers, (0.0, 100_000.0))
check("ноционал в тысячах", "0K:" in row and "100K:" in row, row)
check("маржа ступени в процентах", "0.500%" in row, row)
check("без ступеней — прочерки", crl._row([]).count("K:—") == 4)

print(f"\nитог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
