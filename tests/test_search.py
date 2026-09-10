"""
Офлайн-тесты поиска и добавления монет (сеть не нужна: лоадеры замоканы).

Проверяет главный сценарий пользователя: монеты GRAM нет в топ-списке,
но поиск находит её на биржах, добавляет в список, и она не выпадает
при последующем обновлении топа.

Запуск:  python3 tests/test_search.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_feed import MarketFeed

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


def make_feed():
    feed = MarketFeed(on_liquidation=lambda e: asyncio.sleep(0),
                      on_price=lambda s, p, c: asyncio.sleep(0),
                      on_trade=lambda s, p, q, t, side="": asyncio.sleep(0),
                      symbols_limit=40)

    # топ с Binance (без GRAM) + полный каталог с GRAM на Gate/OKX
    async def binance():
        rows = [{"symbol": "BTC_USDT", "volume24h": 1e10, "price": 95000.0, "change24h": 1.2},
                {"symbol": "ETH_USDT", "volume24h": 5e9, "price": 3300.0, "change24h": -0.4}]
        # 45 «популярных» монет с оборотом больше, чем у GRAM (1.2e8)
        rows += [{"symbol": f"COIN{i:02d}_USDT", "volume24h": 1e9 - i * 1e6,
                  "price": 10.0 + i, "change24h": 1.0} for i in range(45)]
        return rows

    async def okx():
        return [
            {"symbol": "SOL_USDT", "volume24h": 2e9, "price": 190.0, "change24h": 3.1},
            {"symbol": "GRAM_USDT", "volume24h": 1.2e8, "price": 0.42, "change24h": 12.5},
        ]

    async def gate():
        return [
            {"symbol": "GRAM_USDT", "volume24h": 8e7, "price": 0.41, "change24h": 11.0},
        ]

    async def bitget():
        return [
            {"symbol": "DOGE_USDT", "volume24h": 1e9, "price": 0.31, "change24h": 2.2},
        ]

    feed._symbols_binance = binance
    feed._symbols_okx = okx
    feed._symbols_gate = gate
    feed._symbols_bitget = bitget

    async def bybit():
        return []

    feed._symbols_bybit = bybit
    return feed


async def main():
    feed = make_feed()
    await feed.refresh_symbols()

    print("каталог и дефолтный список")
    check("каталог собран", feed.symbol_index_source == "binance+okx+gate+bitget",
          feed.symbol_index_source)
    check("в каталоге есть GRAM", "GRAM_USDT" in feed.symbol_index)
    check("в топе GRAM нет", "GRAM_USDT" not in feed.symbols[:feed.symbols_limit])

    print("поиск")
    res = await feed.search_symbols("GRAM")
    check("GRAM → GRAM_USDT", any(r["symbol"] == "GRAM_USDT" for r in res["results"]),
          res["results"])
    res2 = await feed.search_symbols("GRAMUSDT")
    check("GRAMUSDT → GRAM_USDT", any(r["symbol"] == "GRAM_USDT" for r in res2["results"]))
    res3 = await feed.search_symbols("gram/usdt")
    check("gram/usdt → GRAM_USDT", any(r["symbol"] == "GRAM_USDT" for r in res3["results"]))
    check("биржи в каталоге", set(next(r for r in res["results"]
                                      if r["symbol"] == "GRAM_USDT")["exchanges"]) == {"gate", "okx"})

    print("добавление")
    add = await feed.add_symbol("GRAM")
    check("добавлена", add["added"] is True, add)
    check("символ нормализован", add["symbol"] == "GRAM_USDT")
    check("попала в список", "GRAM_USDT" in feed.symbols)
    check("цена подтянута", abs(feed.prices["GRAM_USDT"] - 0.42) < 1e-9,
          feed.prices.get("GRAM_USDT"))

    print("выживание при обновлении топа")
    await feed.refresh_symbols()
    check("GRAM осталась в списке после refresh", "GRAM_USDT" in feed.symbols)
    check("метаданные сохранились", feed.symbol_meta.get("GRAM_USDT", {}).get("price") == 0.42)

    print("несуществующая пара")
    bad = await feed.add_symbol("ZZZ_NOT_REAL")
    check("не найдена → added=False", bad["added"] is False)
    check("не добавлена в список", "ZZZ_NOT_REAL_USDT" not in feed.symbols)


asyncio.run(main())
print(f"\nитог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
