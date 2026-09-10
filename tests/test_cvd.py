"""
Офлайн-тесты CVD (тейкер-дельта: агрессивные покупки минус продажи).

Запуск:  python3 tests/test_cvd.py
Форматы полей взяты из документации бирж:
  Binance kline: [0]=ts, [7]=quote vol (USDT), [10]=taker buy quote vol
  OKX rubik taker-volume-contract: [ts, sellVol, buyVol] (базовая монета)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_feed import (MarketFeed, binance_kline_cvd, cvd_map_from_okx_taker)

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


print("binance_kline_cvd")
row = [1499040000000, "0.01", "0.9", "0.01", "0.02",      # ts, o,h,l,c
       "148976.1", 1499644799999, "1000",                  # base vol, closeTs, quote vol
       308, "123.4", "700", "17928899"]                     # trades, tbBase, tbQuote, ignore
check("delta = 2*tb - quote = +400", binance_kline_cvd(row) == 400.0,
      binance_kline_cvd(row))
row_sell = list(row); row_sell[10] = "300"                 # продажи преобладают
check("delta отрицательная при перевесе продаж",
      binance_kline_cvd(row_sell) == -400.0, binance_kline_cvd(row_sell))
check("короткая строка — None", binance_kline_cvd([1, 2, 3]) is None)
check("нулевой объём — None",
      binance_kline_cvd([0, "1", "1", "1", "1", "0", 0, "0", 0, "0", "0", "0"]) is None)
check("мусор — None", binance_kline_cvd(["x"] * 12) is None)

print("binance aggTrade — сторона тейкера")


def binance_payload(m):
    return {"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.2",
            "T": int(time.time() * 1000), "m": m}


trades = MarketFeed._handle_trade_payload(object(), binance_payload(False))
check("m=false → агрессор-покупатель BUY",
      len(trades) == 1 and trades[0][4] == "BUY", trades)
trades = MarketFeed._handle_trade_payload(object(), binance_payload(True))
check("m=true (покупатель — мейкер) → SELL",
      len(trades) == 1 and trades[0][4] == "SELL", trades)
check("не-сделка игнорируется",
      MarketFeed._handle_trade_payload(object(), {"e": "other"}) == [])

print("cvd_map_from_okx_taker")
# ts 1200 = bucket 1200 при tf_sec=300; sell 100, buy 180, цена 50 → +4000
pm = {1200: 50.0}
out = cvd_map_from_okx_taker([[1200_000, "100", "180"]], pm, 300)
check("пересчёт в USDT: (buy-sell)*close", out.get(1200) == 4000.0, out)
out = cvd_map_from_okx_taker([[1200_000, "300", "180"]], pm, 300)
check("перевес продаж → отрицательная дельта", out.get(1200) == -6000.0, out)
out = cvd_map_from_okx_taker([[1350_000, "10", "20"], [1500_000, "10", "20"]],
                             {1200: 50.0, 1500: 2.0}, 300)
check("округление к сетке таймфрейма", set(out.keys()) == {1200, 1500}, out)
check("нет цены для бакета — строка пропущена",
      cvd_map_from_okx_taker([[1800_000, "5", "9"]], pm, 300) == {})
check("мусор не роняет парсер", cvd_map_from_okx_taker([["x"]], pm, 300) == {})

print("сервер: живые накопители CVD")
import server

server.CVD_ACC.clear()
server.CANDLES.clear()
tf_sec = 300
bucket = int(time.time() // tf_sec) * tf_sec
server.CANDLES["BTC_USDT|5"] = {
    "candles": [{"time": bucket, "open": 1.0, "high": 1.0, "low": 1.0,
                 "close": 1.0, "volume": 0.0}],
    "ts": time.time() + 1000, "source": "test", "cvd_base": 0.0,
}
server._cvd_add("BTC_USDT", bucket + 5, 100.0)          # агрессивная покупка
check("накоплена дельта по 5м", server._cvd_live_of("BTC_USDT", 5, bucket) == 100.0)
check("накоплена дельта по 1м (тот же бакет минуты)",
      server._cvd_live_of("BTC_USDT", 1, bucket) == 100.0)
server._cvd_add("BTC_USDT", bucket + 6, -40.0)          # агрессивная продажа
check("продажи вычитаются", server._cvd_live_of("BTC_USDT", 5, bucket) == 60.0)
updated = server._apply_price_to_candles("BTC_USDT", 1.05)
candle = dict(updated[0][1]) if updated else {}
check("свеча получила cvd=60", candle.get("cvd") == 60.0, candle)
server._cvd_seed_base(server.CANDLES["BTC_USDT|5"], "BTC_USDT", 5)
check("после seed база = cvd - live",
      server.CANDLES["BTC_USDT|5"]["cvd_base"] == 0.0,
      server.CANDLES["BTC_USDT|5"]["cvd_base"])
server._cvd_add("BTC_USDT", bucket + 7, 15.0)
server._apply_price_to_candles("BTC_USDT", 1.06)
c5 = server.CANDLES["BTC_USDT|5"]["candles"][-1]
check("живой довесок к базе учтён один раз (75, не 135)",
      c5["cvd"] == 75.0, c5["cvd"])
# новая свеча: база обнуляется, cvd только из тиков новой свечи
server.CVD_ACC.clear()
server.CANDLES["BTC_USDT|5"]["candles"][-1]["time"] = bucket - 100000  # «из прошлого»
server._cvd_add("BTC_USDT", time.time(), 25.0)
updated = server._apply_price_to_candles("BTC_USDT", 1.07)
newc = updated[0][1]
check("новая свеча стартует с чистой дельты тиков", newc.get("cvd") == 25.0, newc)
check("база новой свечи обнулена",
      server.CANDLES["BTC_USDT|5"].get("cvd_base") == 0.0,
      server.CANDLES["BTC_USDT|5"].get("cvd_base"))

print()
print(f"итог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
