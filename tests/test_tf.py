"""
Офлайн-тесты таймфреймов: дневной 1440 не должен схлопываться в 5м.

Запуск:  python3 tests/test_tf.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeframes import (OKX_CVD_SEC, TF_BINANCE, TF_BYBIT, TF_MINUTES, TF_OKX,
                        kline_interval, parse_tf)

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


print("карты интервалов")
check("1440 в TF_MINUTES", 1440 in TF_MINUTES)
check("Binance 1d", TF_BINANCE.get(1440) == "1d")
check("Bybit D (не 1440)", TF_BYBIT.get(1440) == "D")
check("OKX 1D", TF_OKX.get(1440) == "1D")
check("OKX CVD 1д = сутки", OKX_CVD_SEC.get(1440) == 86400)

print("ловушка membership")
check("строка '1440' не лежит в списке int", "1440" not in TF_MINUTES)
check("parse_tf('1440') == 1440", parse_tf("1440") == 1440)
check("parse_tf(1440) == 1440", parse_tf(1440) == 1440)
check("parse_tf('1d') == 1440", parse_tf("1d") == 1440)
check("parse_tf('1D') == 1440", parse_tf("1D") == 1440)
check("parse_tf('D') == 1440", parse_tf("D") == 1440)
check("parse_tf(5) == 5", parse_tf(5) == 5)
check("parse_tf('5m') == 5", parse_tf("5m") == 5)
check("parse_tf(None) is None", parse_tf(None) is None)
check("parse_tf('nope') is None", parse_tf("nope") is None)
check("parse_tf(7) is None", parse_tf(7) is None)

print("kline_interval — не подменять 1д на 5м")
check("binance 1440 → 1d", kline_interval(TF_BINANCE, 1440) == "1d")
check("binance '1440' → 1d", kline_interval(TF_BINANCE, "1440") == "1d")
check("binance '1d' → 1d", kline_interval(TF_BINANCE, "1d") == "1d")
check("bybit 1440 → D", kline_interval(TF_BYBIT, 1440) == "D")
check("bybit '1440' → D", kline_interval(TF_BYBIT, "1440") == "D")
check("okx 1440 → 1D", kline_interval(TF_OKX, 1440) == "1D")
check("неизвестный ТФ → None, не 5m",
      kline_interval(TF_BINANCE, 999) is None)
check("строковый мусор → None", kline_interval(TF_BINANCE, "5x") is None)
check("5м остаётся 5m", kline_interval(TF_BINANCE, 5) == "5m")
check("4ч binance 4h", kline_interval(TF_BINANCE, 240) == "4h")

print()
print(f"итог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
