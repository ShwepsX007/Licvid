"""
Офлайн-тесты парсеров ликвидаций (сеть не нужна).

Запуск:  python3 tests/test_parsers.py
Payload'ы взяты из официальной документации бирж.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_feed import (canon, canon_bitmex, parse_binance_msg,
                         parse_bitget_msg, parse_bitmex_msg, parse_bybit_msg,
                         parse_gate_msg, parse_htx_msg, parse_okx_msg,
                         to_binance, to_bybit, to_gate, to_okx)

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


print("symbols")
check("canon binance", canon("BTCUSDT") == "BTC_USDT")
check("canon okx", canon("DYDX-USDT-SWAP") == "DYDX_USDT")
check("canon gate", canon("btc_usdt") == "BTC_USDT")
check("to_binance", to_binance("BTC_USDT") == "BTCUSDT")
check("to_bybit", to_bybit("BTC_USDT") == "BTCUSDT")
check("to_gate", to_gate("BTCUSDT") == "BTC_USDT")
check("to_okx", to_okx("BTC_USDT") == "BTC-USDT-SWAP")

print("binance !forceOrder@arr")
binance_msg = {
    "e": "forceOrder", "E": 1591154240950,
    "o": {"s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC", "q": "0.014",
          "p": "9425.5", "ap": "9496.5", "X": "FILLED", "l": "0.014",
          "z": "0.014", "T": 1591154240949},
}
res = parse_binance_msg(binance_msg)
check("1 событие", len(res) == 1, res)
check("символ", res[0]["symbol"] == "BTC_USDT")
check("SELL → LONG", res[0]["side"] == "LONG")
check("цена ap", res[0]["price"] == 9496.5)
check("объём", abs(res[0]["qty"] - 0.014) < 1e-9)
check("время в секундах", abs(res[0]["ts"] - 1591154240.949) < 0.01)
check("combined stream", len(parse_binance_msg({"stream": "!forceOrder@arr",
                                                "data": binance_msg})) == 1)
buy = {"e": "forceOrder", "E": 1, "o": dict(binance_msg["o"], S="BUY")}
check("BUY → SHORT", parse_binance_msg(buy)[0]["side"] == "SHORT")
check("мусор игнорируется", parse_binance_msg({"e": "aggTrade"}) == [])

print("bybit allLiquidation")
bybit_msg = {
    "topic": "allLiquidation.ROSEUSDT", "type": "snapshot", "ts": 1739502303204,
    "data": [{"T": 1739502302929, "s": "ROSEUSDT", "S": "Sell",
              "v": "20000", "p": "0.04499"}],
}
res = parse_bybit_msg(bybit_msg)
check("1 событие", len(res) == 1, res)
check("символ", res[0]["symbol"] == "ROSE_USDT")
# Документация Bybit: S — сторона ПОЗИЦИИ, Buy = ликвидирован лонг
check("Sell → SHORT", res[0]["side"] == "SHORT")
buy_msg = {"topic": "allLiquidation.BTCUSDT", "ts": 1739502303204,
           "data": [{"T": 1739502302929, "s": "BTCUSDT", "S": "Buy",
                     "v": "0.5", "p": "97000"}]}
check("Buy → LONG", parse_bybit_msg(buy_msg)[0]["side"] == "LONG")
check("usd = qty*price", abs(parse_bybit_msg(buy_msg)[0]["qty"] * 97000 - 48500) < 1e-6)
check("служебные игнорируются",
      parse_bybit_msg({"op": "subscribe", "success": True}) == [])

print("okx liquidation-orders")
okx_msg = {
    "arg": {"channel": "liquidation-orders", "instType": "SWAP"},
    "data": [{"details": [{"bkLoss": "0", "bkPx": "1.057", "ccy": "",
                           "posSide": "long", "side": "sell", "sz": "768",
                           "ts": "1723892524781"}],
              "instFamily": "DYDX-USDT", "instId": "DYDX-USDT-SWAP",
              "instType": "SWAP", "uly": "DYDX-USDT"}],
}
res = parse_okx_msg(okx_msg, {"DYDX-USDT-SWAP": 1.0})
check("1 событие", len(res) == 1, res)
check("символ", res[0]["symbol"] == "DYDX_USDT")
check("posSide long → LONG", res[0]["side"] == "LONG")
check("цена банкротства", res[0]["price"] == 1.057)
res_mult = parse_okx_msg(okx_msg, {"DYDX-USDT-SWAP": 10.0})
check("ctVal умножается", res_mult[0]["qty"] == 7680.0, res_mult)
short = {"data": [{"instId": "BTC-USDT-SWAP",
                   "details": [{"bkPx": "100000", "sz": "3", "side": "buy",
                                "posSide": "short", "ts": "1723892524781"}]}]}
check("posSide short → SHORT", parse_okx_msg(short, {"BTC-USDT-SWAP": 0.01})[0]["side"] == "SHORT")
check("BTC ctVal 0.01", abs(parse_okx_msg(short, {"BTC-USDT-SWAP": 0.01})[0]["qty"] - 0.03) < 1e-9)

print("gate futures.public_liquidates")
gate_msg = {
    "channel": "futures.public_liquidates", "event": "update",
    "time": 1541505434, "time_ms": 1541505434123,
    "result": [{"price": "215.1", "size": "-124", "time": 1541486601,
                "contract": "BTC_USDT"}],
}
res = parse_gate_msg(gate_msg, {"BTC_USDT": 0.0001})
check("1 событие", len(res) == 1, res)
check("size<0 → LONG", res[0]["side"] == "LONG")
check("quanto_multiplier", abs(res[0]["qty"] - 0.0124) < 1e-9, res)
check("время из time_ms конверта", abs(res[0]["ts"] - 1541505434.123) < 0.01)
item_ms = dict(gate_msg, result=[dict(gate_msg["result"][0], time_ms=1541486601000)])
check("время из time_ms события",
      abs(parse_gate_msg(item_ms, {"BTC_USDT": 0.0001})[0]["ts"] - 1541486601.0) < 0.01)
pos = dict(gate_msg, result=[dict(gate_msg["result"][0], size="124")])
check("size>0 → SHORT", parse_gate_msg(pos, {"BTC_USDT": 0.0001})[0]["side"] == "SHORT")
check("без множителя пропуск", parse_gate_msg(gate_msg, {}) == [])
check("чужой канал игнор",
      parse_gate_msg({"channel": "futures.tickers", "result": []}, {"BTC_USDT": 1}) == [])

print("bitget liquidation (UTA v3)")
bitget_msg = {
    "data": [{"symbol": "BTCUSDT", "side": "buy", "price": "89000",
              "amount": "37.722858", "ts": "1736371332162"}],
    "arg": {"instType": "usdt-futures", "topic": "liquidation"},
    "action": "update", "ts": 1736371332162,
}
res = parse_bitget_msg(bitget_msg)
check("1 событие", len(res) == 1, res)
check("символ", res[0]["symbol"] == "BTC_USDT")
# Документация Bitget: side=buy — ликвидация ЛОНГА
check("buy → LONG", res[0]["side"] == "LONG")
check("amount в USDT → usd", abs(res[0]["usd"] - 37.722858) < 1e-9)
check("qty = amount / price", abs(res[0]["qty"] - 37.722858 / 89000) < 1e-12)
sell = {"arg": {"topic": "liquidation"},
        "data": [{"symbol": "ETHUSDT", "side": "sell", "price": "3000",
                  "amount": "6000", "ts": "1736371332162"}]}
check("sell → SHORT", parse_bitget_msg(sell)[0]["side"] == "SHORT")
check("чужой топик игнор",
      parse_bitget_msg({"arg": {"topic": "ticker"}, "data": [{}]}) == [])

print("htx public.*.liquidation_orders")
htx_msg = {
    "op": "notify", "topic": "public.BTC-USDT.liquidation_orders",
    "ts": 1603879731301,
    "data": [{"contract_code": "BTC-USDT", "symbol": "BTC", "direction": "sell",
              "offset": "close", "volume": 173, "price": 17102.9,
              "created_at": 1606381842485, "amount": 1.0115243,
              "trade_turnover": 17300.5}],
}
res = parse_htx_msg(htx_msg)
check("1 событие", len(res) == 1, res)
check("символ BTC-USDT → BTC_USDT", res[0]["symbol"] == "BTC_USDT")
# direction — сторона ордера ликвидации: sell = закрывали лонг
check("sell → LONG", res[0]["side"] == "LONG")
check("qty из amount", abs(res[0]["qty"] - 1.0115243) < 1e-9)
check("usd из trade_turnover", abs(res[0]["usd"] - 17300.5) < 1e-9)
buy = {"topic": "public.ETH-USDT.liquidation_orders",
       "data": [{"contract_code": "ETH-USDT", "direction": "buy", "price": 3000,
                 "amount": 2, "created_at": 1606381842485}]}
check("buy → SHORT", parse_htx_msg(buy)[0]["side"] == "SHORT")
check("без turnover считаем сами", parse_htx_msg(buy)[0]["usd"] is None)
check("чужой топик игнор",
      parse_htx_msg({"topic": "market.BTC-USDT.trade.detail", "data": []}) == [])

print("bitmex liquidation")
check("canon XBTUSD", canon_bitmex("XBTUSD") == "BTC_USDT")
check("canon XBTUSDT", canon_bitmex("XBTUSDT") == "BTC_USDT")
check("canon ETHUSD", canon_bitmex("ETHUSD") == "ETH_USDT")
bitmex_inverse = {
    "table": "liquidation", "action": "insert",
    "data": [{"orderID": "98ae1dad", "symbol": "XBTUSD", "side": "Sell",
              "price": 13854.5, "leavesQty": 314930}],
}
instr = {"XBTUSD": {"inverse": True, "multiplier": 0.0},
         "XBTUSDT": {"inverse": False, "multiplier": 1e-6}}
res = parse_bitmex_msg(bitmex_inverse, instr)
check("1 событие", len(res) == 1, res)
check("Sell → LONG", res[0]["side"] == "LONG")
check("инверсный: usd = контракты", abs(res[0]["usd"] - 314930) < 1e-6)
check("инверсный: qty в BTC", abs(res[0]["qty"] - 314930 / 13854.5) < 1e-9)
linear = {"table": "liquidation", "action": "insert",
          "data": [{"symbol": "XBTUSDT", "side": "Buy", "price": 90000,
                    "leavesQty": 1000000}]}
res = parse_bitmex_msg(linear, instr)
check("Buy → SHORT", res[0]["side"] == "SHORT")
check("линейный: множитель контракта", abs(res[0]["qty"] - 1.0) < 1e-9, res)
check("линейный: usd = qty*price", abs(res[0]["usd"] - 90000) < 1e-6)
check("update не учитываем (задвоение объёма)",
      parse_bitmex_msg({"table": "liquidation", "action": "update",
                        "data": [{"symbol": "XBTUSD", "leavesQty": 305515}]},
                       instr) == [])
check("без метаданных линейный пропускаем",
      parse_bitmex_msg(linear, {}) == [])

print()
print(f"итог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
