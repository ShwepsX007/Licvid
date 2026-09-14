"""
Матчеры кадров в tools/check_exchanges.py.

Диагностика отвечает на вопрос «приходят ли с биржи ДАННЫЕ, а не просто
открылся сокет». У dYdX, Kraken и Bitfinex ликвидация — это метка внутри
общего потока сделок, а не отдельный канал, поэтому матчеры «любые сделки» и
«ликвидации» обязаны различаться: иначе диагностика соврёт, что канала нет,
когда на бирже просто не было ликвидаций.

Каждый матчер проверяется на кадре реальной формы и на том, что его НЕ
должно срабатывать.

Сеть не нужна.  Запуск:  python3 tests/test_check_exchanges.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_exchanges as ce  # noqa: E402

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


print("dYdX v4")
dydx_trades = {"type": "data", "channel": "v4_trades", "id": "BTC-USD",
               "contents": {"trades": [
                   {"id": "1", "createdAt": "2026-09-13T10:00:00.000Z",
                    "side": "BUY", "price": "60000", "size": "0.1",
                    "type": "LIMIT"}]}}
dydx_liq = {"type": "data", "channel": "v4_trades", "id": "BTC-USD",
            "contents": {"trades": [
                {"id": "1", "createdAt": "2026-09-13T10:00:00.000Z",
                 "side": "BUY", "price": "60000", "size": "0.1",
                 "type": "LIMIT"},
                {"id": "2", "createdAt": "2026-09-13T10:00:01.000Z",
                 "side": "SELL", "price": "59900", "size": "2.5",
                 "type": "LIQUIDATED"}]}}
check("сделки видны", ce.dydx_has_trades(dydx_trades) is True)
check("обычные сделки ликвидацией не считаются",
      ce.dydx_has_liquidation(dydx_trades) is False)
check("Liquidated виден", ce.dydx_has_liquidation(dydx_liq) is True)
check("Deleveraged тоже считаем",
      ce.dydx_has_liquidation({"channel": "v4_trades", "contents": {"trades": [
          {"type": "DELEVERAGED", "price": "1", "size": "1"}]}}) is True)
check("регистр типа не важен",
      ce.dydx_has_liquidation({"channel": "v4_trades", "contents": {"trades": [
          {"type": "liquidated"}]}}) is True)
check("пустой кадр сделок не матчит",
      ce.dydx_has_trades({"channel": "v4_trades", "contents": {"trades": []}})
      is False)
check("чужой канал не матчит",
      ce.dydx_has_trades({"channel": "v4_orderbook"}) is False)
check("подтверждение подписки не матчит",
      ce.dydx_has_trades({"type": "subscribed", "channel": "v4_trades",
                          "contents": {}}) is False)

print("Kraken Futures")
kraken_fill = {"feed": "trade", "product_id": "PF_XBTUSD", "uid": "a",
               "side": "buy", "type": "fill", "seq": 1,
               "time": 1789300000000, "qty": 10, "price": 60000}
kraken_liq = dict(kraken_fill, type="liquidation", uid="b")
kraken_term = dict(kraken_fill, type="termination", uid="c", side="sell")
kraken_snap = {"feed": "trade_snapshot", "product_id": "PF_XBTUSD",
               "trades": [kraken_fill, kraken_liq]}
check("одиночная сделка видна", ce.kraken_has_trades(kraken_fill) is True)
check("fill ликвидацией не считается",
      ce.kraken_has_liquidation(kraken_fill) is False)
check("liquidation виден", ce.kraken_has_liquidation(kraken_liq) is True)
check("termination тоже считаем",
      ce.kraken_has_liquidation(kraken_term) is True)
check("снапшот разбирается", ce.kraken_has_trades(kraken_snap) is True)
check("ликвидация внутри снапшота видна",
      ce.kraken_has_liquidation(kraken_snap) is True)
check("снапшот без ликвидаций не матчит",
      ce.kraken_has_liquidation({"feed": "trade_snapshot", "product_id": "X",
                                 "trades": [kraken_fill]}) is False)
check("block не считаем ликвидацией",
      ce.kraken_has_liquidation(dict(kraken_fill, type="block")) is False)
check("чужой фид не матчит",
      ce.kraken_has_trades({"feed": "ticker", "product_id": "PF_XBTUSD"})
      is False)
check("подтверждение подписки не матчит",
      ce.kraken_has_trades({"event": "subscribed", "feed": "trade"}) is False)
check("снапшот без списка trades не матчит",
      ce.kraken_has_trades({"feed": "trade_snapshot", "product_id": "X"})
      is False)

print("Bitfinex")
bfx_liq = [1221, [["pos", 1, 1789300000000, None, "tBTCF0:USTF0", 2.5,
                   59000.0, None, 1, 1, None, 60000.0]]]
check("кадр ликвидации виден", ce.bitfinex_has_liq_row(bfx_liq) is True)
check("несколько строк в кадре",
      ce.bitfinex_has_liq_row([1221, [bfx_liq[1][0], bfx_liq[1][0]]]) is True)
check("heartbeat не матчит", ce.bitfinex_has_liq_row([1221, "hb"]) is False)
check("info-кадр не матчит",
      ce.bitfinex_has_liq_row({"event": "info", "version": 2}) is False)
check("подтверждение подписки не матчит",
      ce.bitfinex_has_liq_row({"event": "subscribed", "channel": "status",
                               "chanId": 1221, "key": "liq:global"}) is False)
check("чужой канал (не pos) не матчит",
      ce.bitfinex_has_liq_row([999, [["conf", 1, 2, 3]]]) is False)
check("пустой список строк не матчит",
      ce.bitfinex_has_liq_row([1221, []]) is False)
check("мусор не роняет матчер",
      ce.bitfinex_has_liq_row(None) is False
      and ce.bitfinex_has_liq_row("x") is False
      and ce.bitfinex_has_liq_row([1]) is False)

print("прочие матчеры остались на месте")
check("dydx_has_liquidation требует кадра сделок",
      ce.dydx_has_liquidation({"channel": "v4_orderbook"}) is False)
check("мусор на входе не роняет dYdX-матчеры",
      ce.dydx_has_trades(None) is False and ce.dydx_has_liquidation([]) is False)
check("мусор на входе не роняет Kraken-матчеры",
      ce.kraken_has_trades(None) is False
      and ce.kraken_has_liquidation("x") is False)

print()
print(f"итог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
