"""
Ingestion-слой рыночных данных: MarketEvent + EventBus + нормализаторы.

Проверяем контракты слоя, на который переведены все биржевые коннекторы:

* ``MarketEvent`` — единый формат события (поля, умолчания, неизменяемость);
* ``EventBus`` — прямая рассылка подписчикам по порядку подписки, без
  очередей: горячий путь тиков не должен платить аллокациями;
* ``liquidation_events`` — реестр нормализаторов LIQUIDATION_NORMALIZERS:
  кадр любой биржи из реестра -> List[MarketEvent] одной функцией;
* ``MarketFeed`` — коннекторы публикуют события в шину, а потребители
  (_consume_trade/_consume_liquidation/_consume_price) раздают их в
  prices/on_trade/on_liquidation/on_price: новый источник не меняет
  потребителей, и наоборот.

Сеть наружу не нужна.  Запуск:  python3 tests/test_market_events.py
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_events import EventBus, MarketEvent
from market_feed import MarketFeed, liquidation_events, parse_bitget_msg

NOW = 1791500000.0


def binance_force_order(symbol="BTCUSDT", side="SELL", price="60000",
                        qty="2.5", ts_ms=1791500000000):
    return {"data": {"e": "forceOrder", "E": ts_ms,
                     "o": {"s": symbol, "S": side, "q": qty, "p": price,
                           "ap": price, "T": ts_ms, "z": qty}}}


def bitget_liquidation(symbol="BTCUSDT", side="buy", price="60000",
                       amount="150000", ts_ms=1791500000000):
    return {"arg": {"topic": "liquidation"}, "ts": ts_ms,
            "data": [{"symbol": symbol, "side": side, "price": price,
                      "amount": amount, "ts": ts_ms}]}


class TestMarketEvent(unittest.TestCase):
    def test_fields_and_defaults(self):
        """Полный набор полей и разумные умолчания для «лишних» аргументов."""
        ev = MarketEvent(source="binance", symbol="BTC_USDT", ts=NOW,
                         type="trade", price=60000.0, qty=2.5, side="BUY")
        self.assertEqual(ev.source, "binance")
        self.assertEqual(ev.type, "trade")
        self.assertEqual(ev.side, "BUY")
        self.assertEqual(ev.usd, 0.0)          # посчитает потребитель
        self.assertIsNone(ev.kind)
        self.assertIsNone(ev.details)
        self.assertIsNone(ev.candle)

    def test_immutable(self):
        """NamedTuple: событие нельзя «подправить» после публикации."""
        ev = MarketEvent("binance", "BTC_USDT", NOW, "trade", 1.0, 1.0)
        with self.assertRaises(AttributeError):
            ev.price = 2.0


class TestEventBus(unittest.TestCase):
    def test_publish_order_and_count(self):
        """Подписчики вызываются по порядку подписки; publish возвращает их число."""
        bus = EventBus()
        seen = []

        async def a(ev): seen.append(("a", ev.symbol))
        async def b(ev): seen.append(("b", ev.symbol))

        bus.subscribe("trade", a)
        bus.subscribe("trade", b)

        async def run():
            ev = MarketEvent("binance", "BTC_USDT", NOW, "trade", 1.0, 1.0, "BUY")
            return await bus.publish(ev)

        self.assertEqual(asyncio.run(run()), 2)
        self.assertEqual(seen, [("a", "BTC_USDT"), ("b", "BTC_USDT")])

    def test_unknown_type_is_not_an_error(self):
        """Событие без подписчиков — не ошибка: просто никто не слушает."""
        bus = EventBus()

        async def run():
            return await bus.publish(
                MarketEvent("binance", "BTC_USDT", NOW, "nope", 1.0, 1.0))

        self.assertEqual(asyncio.run(run()), 0)

    def test_types_are_isolated(self):
        """Подписчик trades не получает ликвидаций и наоборот."""
        bus = EventBus()
        trades, liqs = [], []

        async def on_trade(ev): trades.append(ev)
        async def on_liq(ev): liqs.append(ev)

        bus.subscribe("trade", on_trade)
        bus.subscribe("liquidation", on_liq)

        async def run():
            await bus.publish(MarketEvent("binance", "BTC_USDT", NOW,
                                          "trade", 1.0, 1.0))
            await bus.publish(MarketEvent("bybit", "ETH_USDT", NOW,
                                          "liquidation", 1.0, 1.0))

        asyncio.run(run())
        self.assertEqual(len(trades), 1)
        self.assertEqual(len(liqs), 1)
        self.assertEqual(trades[0].type, "trade")
        self.assertEqual(liqs[0].type, "liquidation")

    def test_unsubscribe(self):
        bus = EventBus()

        async def h(ev): pass

        bus.subscribe("trade", h)
        self.assertTrue(bus.unsubscribe("trade", h))
        self.assertFalse(bus.unsubscribe("trade", h))   # уже отписан
        self.assertEqual(bus.subscriber_count("trade"), 0)

    def test_subscriber_count_total(self):
        bus = EventBus()

        async def h(ev): pass

        bus.subscribe("trade", h)
        bus.subscribe("price", h)
        self.assertEqual(bus.subscriber_count(), 2)
        self.assertEqual(bus.subscriber_count("trade"), 1)


class TestNormalizer(unittest.TestCase):
    def test_binance_payload(self):
        """forceOrder SELL -> вынесен LONG; источник и ts сохранены."""
        evs = liquidation_events("binance", binance_force_order())
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertEqual(ev.type, "liquidation")
        self.assertEqual(ev.source, "binance")
        self.assertEqual(ev.symbol, "BTC_USDT")
        self.assertEqual(ev.side, "LONG")       # SELL-ордер = ликвидация лонга
        self.assertEqual(ev.price, 60000.0)
        self.assertEqual(ev.qty, 2.5)
        self.assertEqual(ev.ts, NOW)
        self.assertEqual(ev.usd, 0.0)           # потребитель посчитает из p*q

    def test_bitget_payload_keeps_exchange_usd(self):
        """Bitget сам даёт сумму в USDT — она доезжает до события."""
        evs = liquidation_events("bitget", bitget_liquidation())
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].usd, 150000.0)
        self.assertEqual(evs[0].side, "LONG")   # side=buy = закрывали лонг
        self.assertAlmostEqual(evs[0].qty, 2.5)

    def test_okx_ctx_passthrough(self):
        """OKX-нормализатор получает contract_values через ctx реестра."""
        from market_feed import parse_okx_msg, LIQUIDATION_NORMALIZERS
        self.assertIs(LIQUIDATION_NORMALIZERS["okx"], parse_okx_msg)
        # кадров без liquidation-orders нормализатор не примет — но вызов с
        # ctx не должен падать на неподдерживаемой подписи
        self.assertEqual(liquidation_events("okx", {"arg": {"channel": "x"}},
                                            contract_values={"BTC-USDT-SWAP": 0.01}),
                         [])

    def test_unknown_source_and_bad_payload(self):
        """Нет источника в реестре / не-словарь -> пустой список, без исключений."""
        self.assertEqual(liquidation_events("kucoin", {"data": {}}), [])
        self.assertEqual(liquidation_events("binance", [1, 2, 3]), [])
        self.assertEqual(liquidation_events("binance", {}), [])


def make_feed():
    """Копия стенда test_trade_sources: колбэки пишут в списки."""
    feed_box = {"liq": [], "trade": [], "price": []}

    async def on_liq(ev): feed_box["liq"].append(ev)
    async def on_price(sym, price, candle): feed_box["price"].append((sym, price, candle))
    async def on_trade(sym, price, qty, ts, side):
        feed_box["trade"].append((sym, price, qty, ts, side))

    feed = MarketFeed(on_liquidation=on_liq, on_price=on_price,
                      on_trade=on_trade, exchanges=[])
    feed.symbols = ["BTC_USDT", "ETH_USDT"]
    return feed, feed_box


class TestFeedBusIntegration(unittest.TestCase):
    def test_trade_event_updates_price_and_cvd_callback(self):
        """trade -> шина -> prices + on_trade с теми же аргументами."""
        feed, box = make_feed()

        async def run():
            await feed.bus.publish(MarketEvent(
                "binance", "BTC_USDT", NOW, "trade", 60123.5, 1.25, "BUY"))

        asyncio.run(run())
        self.assertEqual(feed.prices["BTC_USDT"], 60123.5)
        self.assertEqual(box["trade"], [("BTC_USDT", 60123.5, 1.25, NOW, "BUY")])

    def test_publish_trades_batch(self):
        """_publish_trades публикует каждую сделку и возвращает их число."""
        feed, box = make_feed()
        trades = [("BTC_USDT", 1.0, 1.0, NOW, "BUY"),
                  ("ETH_USDT", 2.0, 3.0, NOW + 1, "SELL"),
                  ("BTC_USDT", 1.5, 2.0, NOW + 2, "")]

        async def run():
            return await feed._publish_trades("bybit", trades)

        self.assertEqual(asyncio.run(run()), 3)
        self.assertEqual(len(box["trade"]), 3)
        self.assertEqual(feed.prices["BTC_USDT"], 1.5)   # последняя цена
        self.assertEqual(feed.prices["ETH_USDT"], 2.0)
        # пустая сторона доехала как есть: биржа может не сообщать тейкера
        self.assertEqual(box["trade"][2][4], "")

    def test_liquidation_via_emit(self):
        """_emit -> шина -> валидация, счётчик источника и словарь ленты."""
        feed, box = make_feed()

        async def run():
            await feed._emit("binance", "BTC_USDT", "LONG", 60000.0, 2.0, NOW)
            # сумма от биржи приоритетнее price*qty
            await feed._emit("gate", "ETH_USDT", "SHORT", 3000.0, 10.0,
                             NOW + 1, usd=12345.0)
            # монеты нет в списке — событие отсеивается ДО счётчика источника
            await feed._emit("okx", "SOL_USDT", "LONG", 100.0, 1.0, NOW + 2)
            # мусорные значения не попадают в ленту
            await feed._emit("binance", "BTC_USDT", "LONG", 0.0, 2.0, NOW + 3)

        asyncio.run(run())
        self.assertEqual(len(box["liq"]), 2)
        first = box["liq"][0]
        self.assertEqual(first["symbol"], "BTC_USDT")
        self.assertEqual(first["exchange"], "binance")
        self.assertEqual(first["side"], "LONG")
        self.assertEqual(first["usd"], 120000.0)          # 60000 * 2
        self.assertEqual(first["timestamp"], NOW)
        second = box["liq"][1]
        self.assertEqual(second["usd"], 12345.0)          # сумма биржи
        # счётчики источников: по одному валидному событию на источник
        self.assertEqual(feed.status["binance"].events, 1)
        self.assertEqual(feed.status["gate"].events, 1)
        self.assertEqual(feed.status["okx"].events, 0)    # отсеян до hit()

    def test_emit_tape_event_keeps_kind_and_details(self):
        """Выведенные события (hl_infer) доносят kind и подтверждения."""
        feed, box = make_feed()

        async def run():
            await feed._emit("hyperliquid", "BTC_USDT", "LONG", 60000.0, 2.0,
                             NOW, kind="tape",
                             details={"method": "closetoMarkprice"})

        asyncio.run(run())
        ev = box["liq"][0]
        self.assertEqual(ev["kind"], "tape")
        self.assertEqual(ev["method"], "closetoMarkprice")

    def test_emit_fresh_ts_fallback(self):
        """ts=0 -> потребитель подставляет время ленты, как раньше."""
        feed, box = make_feed()

        async def run():
            await feed._emit("binance", "BTC_USDT", "LONG", 1.0, 1.0, 0)

        asyncio.run(run())
        self.assertGreater(box["liq"][0]["timestamp"], NOW)

    def test_price_event_with_candle(self):
        """price -> шина -> prices + on_price с той же свечой."""
        feed, box = make_feed()
        candle = {"time": 1791500000, "open": 1, "high": 2, "low": 0.5,
                  "close": 1.75, "volume": 100}

        async def run():
            await feed._publish_price("binance", "BTC_USDT", 1.75, candle)
            await feed._publish_price("bybit", "ETH_USDT", 3000.0)

        asyncio.run(run())
        self.assertEqual(feed.prices["BTC_USDT"], 1.75)
        self.assertEqual(feed.prices["ETH_USDT"], 3000.0)
        self.assertEqual(box["price"][0], ("BTC_USDT", 1.75, candle))
        # REST-тикер без свечи: candle=None, как раньше
        self.assertEqual(box["price"][1], ("ETH_USDT", 3000.0, None))

    def test_normalizer_to_bus_end_to_end(self):
        """Цепочка целиком: кадр Bitget -> нормализатор -> шина -> лента."""
        feed, box = make_feed()

        async def run():
            for ev in liquidation_events("bitget", bitget_liquidation()):
                await feed.bus.publish(ev)

        asyncio.run(run())
        self.assertEqual(len(box["liq"]), 1)
        ev = box["liq"][0]
        self.assertEqual(ev["exchange"], "bitget")
        self.assertEqual(ev["usd"], 150000.0)
        self.assertEqual(feed.status["bitget"].events, 1)

    def test_health_reports_bus_subscribers(self):
        """/api/health показывает подписчиков шины по типам событий."""
        feed, _ = make_feed()
        h = feed.health()
        self.assertEqual(h["event_bus"],
                         {"trade": 1, "liquidation": 1, "price": 1})

    def test_parsers_unchanged(self):
        """Регресс: parse_*-функции не изменили контракт (bitget как пример)."""
        rows = parse_bitget_msg(bitget_liquidation())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["usd"], 150000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
