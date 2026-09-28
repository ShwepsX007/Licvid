"""Свечи графика: запрос не ходит на биржу, история грузится в фоне.

Задача — убрать тормоза сервера и защитить биржевые лимиты. /api/klines
обязан быть читателем локального буфера (кэш отдач → ``market_feed.
get_candles_cached`` → заготовка от последней цены), а загрузка истории новых
монет — фоновой задачей (``asyncio.create_task``). Иначе каждый зритель
графика добавляет единственному воркеру uvicorn синхронный HTTP-запрос к
Binance/Bybit/OKX: при 50+ пользователях это лаг и 100% CPU, а у биржи
кончается терпение (429/418 — бан IP всего сервера).

Проверяем:

* первый запрос отвечает сразу и не вызывает ``fetch_klines``;
* загрузка уходит в фон и заполняет буфер (source становится "exchange");
* 50 одновременных зрителей одной серии — один поход на биржу, не пятьдесят;
* повторный запрос читает кэш, протухший кэш отдаётся с ``stale``, а зазор
  ``CANDLES_REFETCH_SEC`` не пускает второй запрос к бирже сразу;
* зритель, которому досталась заготовка, догоняется кадром ``candles`` по WS;
* ``get_candles(force=True)`` (внутренние фоновые циклы) по-прежнему ждёт биржу.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="klines_")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq_history.jsonl")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_SECRET"] = "test-secret-not-the-published-default"
os.environ["LIQSCOPE_ADMIN_IDS"] = "1001"
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_BOT_TOKEN"] = ""
os.environ["LIQSCOPE_API_CACHE"] = "0"

import market_feed  # noqa: E402
import server  # noqa: E402
from market_feed import MarketFeed  # noqa: E402


def _candles(n: int = 40, tf: int = 5) -> list:
    """Ровные свечи нужного ТФ: клиент сверяет шаг времени (candlesMatchTf)."""
    tf_sec = tf * 60
    now_bucket = int(time.time() // tf_sec) * tf_sec
    return [{"time": now_bucket - (n - i) * tf_sec,
             "open": 100.0 + i, "high": 101.0 + i,
             "low": 99.0 + i, "close": 100.5 + i,
             "volume": 1000.0 + i, "cvd": 10.0 + i} for i in range(n)]


class FakeFeed(MarketFeed):
    """MarketFeed, у которого «биржа» — счётчик вызовов с задержкой."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fetch_calls = 0
        self.cvd_calls = 0
        self.gate = None        # если задан — «биржа» висит до gate.set()
        self.oi = None          # OI-трекер ходит в сеть: в тесте он не нужен

    async def fetch_klines(self, symbol, tf_min, limit=300):
        self.fetch_calls += 1
        if self.gate is not None:
            await self.gate.wait()      # зависшая биржа: запросы не должны ждать
        else:
            await asyncio.sleep(0.05)   # «сеть»: запрос не должен её ждать
        return _candles(tf=tf_min)

    async def fetch_cvd(self, *args, **kwargs):
        self.cvd_calls += 1
        return None


class FakeViewer:
    """WS-клиент для hub.broadcast: нужен только predicate и send."""

    def __init__(self, chart: str, tf: int):
        self.chart = chart
        self.tf = tf
        self.sent: list = []

    @property
    def chart_symbol(self) -> str:
        return self.chart

    async def send(self, msg: dict, text=None) -> bool:
        # сигнатура как у server.Client: рассылка отдаёт готовый кадр текстом
        # (сериализация одна на всех), а двойник проверяет сам payload
        self.sent.append(msg)
        return True


class KlinesIsolationTest(unittest.TestCase):
    def setUp(self):
        self.feed = FakeFeed(on_liquidation=lambda ev: asyncio.sleep(0),
                             on_price=lambda s, p, c: asyncio.sleep(0),
                             symbols_limit=4)
        self.feed.on_candles = server._on_candles_ready
        self._prev_feed = server.feed
        self._prev_candles = dict(server.CANDLES)
        server.feed = self.feed
        server.CANDLES.clear()

    def tearDown(self):
        server.feed = self._prev_feed
        server.CANDLES.clear()
        server.CANDLES.update(self._prev_candles)

    async def _drain(self):
        """Дождаться фоновых загрузок фида (их держит сильный набор ссылок)."""
        for _ in range(5):
            tasks = list(self.feed._candles_tasks)
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- путь запроса -------------------------------------------------------
    def test_request_never_waits_for_exchange(self):
        async def run():
            data = await server.api_klines("BTC_USDT", 5)
            # ответ уже здесь, а биржу ещё не трогали — значит, ждали не её
            self.assertEqual(self.feed.fetch_calls, 0)
            self.assertTrue(data["candles"], "клиент обязан получить свечи")
            self.assertEqual(data["symbol"], "BTC_USDT")
            self.assertEqual(data["timeframe"], 5)
            self.assertNotEqual(data["source"], "exchange")
            self.assertTrue(data["pending"], "загрузка истории не поставлена в фон")
            self.assertTrue(self.feed.candles_pending("BTC_USDT", 5))
            await self._drain()

        asyncio.run(run())
        self.assertEqual(self.feed.fetch_calls, 1, "фон сходил на биржу один раз")
        entry = server.CANDLES.get("BTC_USDT|5")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["source"], "exchange")
        self.assertTrue(entry["candles"])
        # буфер фида — то, чем ручка ответит в следующий раз
        self.assertIsNotNone(self.feed.get_candles_cached("BTC_USDT", 5))

    def test_fifty_viewers_cost_one_exchange_call(self):
        """Биржа «висит», а 50 зрителей получают ответ сразу и один поход в фон."""
        async def run():
            self.feed.gate = asyncio.Event()
            got = await asyncio.gather(*[server.api_klines("SOL_USDT", 5)
                                         for _ in range(50)])
            self.assertTrue(all(g["candles"] for g in got),
                            "кто-то из зрителей остался без свечей")
            self.assertEqual(self.feed.fetch_calls, 1,
                             "в фон должен уйти ровно один запрос к бирже")
            self.assertTrue(self.feed.candles_pending("SOL_USDT", 5))
            self.feed.gate.set()
            await self._drain()
            self.assertEqual(self.feed.fetch_calls, 1,
                             "50 зрителей одного графика — один запрос к бирже")
            self.assertEqual(server.CANDLES["SOL_USDT|5"]["source"], "exchange")

        asyncio.run(run())

    def test_second_request_reads_cache(self):
        async def run():
            await server.api_klines("ETH_USDT", 5)
            await self._drain()
            before = self.feed.fetch_calls
            data = await server.api_klines("ETH_USDT", 5)
            self.assertEqual(self.feed.fetch_calls, before,
                             "свежий кэш не должен трогать биржу")
            self.assertEqual(data["source"], "exchange")
            self.assertFalse(data["stale"])
            self.assertEqual(len(data["candles"]), 40)

        asyncio.run(run())

    def test_stale_cache_is_served_and_refetch_is_throttled(self):
        async def run():
            await server.api_klines("DOGE_USDT", 5)
            await self._drain()
            self.assertEqual(self.feed.fetch_calls, 1)
            # кэш протух: отдаём старое сразу, второй поход на биржу держит зазор
            server.CANDLES["DOGE_USDT|5"]["ts"] = time.time() - server.KLINE_TTL - 1
            data = await server.api_klines("DOGE_USDT", 5)
            self.assertEqual(self.feed.fetch_calls, 1,
                             "зазор CANDLES_REFETCH_SEC не сработал")
            self.assertTrue(data["stale"])
            self.assertEqual(data["source"], "exchange")

        asyncio.run(run())

    def test_feed_buffer_is_used_when_server_cache_is_empty(self):
        async def run():
            key = market_feed.candle_key("XRP_USDT", 15)
            self.feed.cache_candles("XRP_USDT", 15, _candles(tf=15), "exchange",
                                    ts=time.time() - 1)
            # зазор уже выдержан: фон не ставим, проверяем именно чтение буфера
            self.feed._candles_fetch_at[key] = time.monotonic()
            data = await server.api_klines("XRP_USDT", 15)
            self.assertEqual(self.feed.fetch_calls, 0)
            self.assertEqual(data["source"], "exchange")
            self.assertEqual(data["timeframe"], 15)
            self.assertEqual(len(data["candles"]), 40)
            self.assertTrue(data["stale"])

        asyncio.run(run())

    def test_buffer_reader_is_local_and_per_series(self):
        self.assertIsNone(self.feed.get_candles_cached("BTC_USDT", 5))
        self.feed.cache_candles("BTC_USDT", 5, _candles(), "exchange")
        got = self.feed.get_candles_cached("BTC_USDT", 5)
        self.assertIsNotNone(got)
        self.assertEqual(got["source"], "exchange")
        self.assertEqual(len(got["candles"]), 40)
        self.assertGreaterEqual(got["age_sec"], 0.0)
        # другая монета и другой ТФ — свои серии, чужую не отдаём
        self.assertIsNone(self.feed.get_candles_cached("BTC_USDT", 15))
        self.assertIsNone(self.feed.get_candles_cached("ETH_USDT", 5))
        # пустой список — не ответ
        self.feed.cache_candles("ADA_USDT", 5, [], "exchange")
        self.assertIsNone(self.feed.get_candles_cached("ADA_USDT", 5))

    def test_buffer_is_capped(self):
        saved = market_feed.CANDLES_CACHE_MAX
        market_feed.CANDLES_CACHE_MAX = 8
        try:
            for i in range(30):
                self.feed.cache_candles(f"COIN{i}_USDT", 5, _candles(n=2),
                                        "exchange", ts=time.time() + i)
            self.assertLessEqual(len(self.feed.candles_cache), 8)
            # самые свежие остались
            self.assertIsNotNone(self.feed.get_candles_cached("COIN29_USDT", 5))
        finally:
            market_feed.CANDLES_CACHE_MAX = saved

    # --- догоняющий кадр WS -------------------------------------------------
    def test_placeholder_is_followed_by_ws_push(self):
        viewer = FakeViewer("BTC_USDT", 5)
        other = FakeViewer("ETH_USDT", 60)
        server.hub.clients.add(viewer)
        server.hub.clients.add(other)
        try:
            async def run():
                first = await server.api_klines("BTC_USDT", 5)
                self.assertNotEqual(first["source"], "exchange")
                await self._drain()

            asyncio.run(run())
        finally:
            server.hub.clients.discard(viewer)
            server.hub.clients.discard(other)
        frames = [m for m in viewer.sent if m.get("type") == "candles"]
        self.assertEqual(len(frames), 1, "зритель заготовки не получил настоящие свечи")
        self.assertEqual(frames[0]["symbol"], "BTC_USDT")
        self.assertEqual(frames[0]["tf"], 5)
        self.assertEqual(frames[0]["source"], "exchange")
        self.assertTrue(frames[0]["candles"])
        self.assertEqual([m for m in other.sent if m.get("type") == "candles"], [],
                         "чужой график не должен получать эти свечи")

    def test_steady_state_does_not_spam_ws(self):
        """Обычное обновление серии (в кэше уже exchange) не рассылается всем."""
        viewer = FakeViewer("BTC_USDT", 5)
        server.CANDLES["BTC_USDT|5"] = {"candles": _candles(), "ts": time.time(),
                                        "source": "exchange"}
        server.hub.clients.add(viewer)
        try:
            async def run():
                await server._on_candles_ready("BTC_USDT", 5, _candles(), "exchange")

            asyncio.run(run())
        finally:
            server.hub.clients.discard(viewer)
        self.assertEqual([m for m in viewer.sent if m.get("type") == "candles"], [])

    # --- force=True: прежнее поведение для фоновых циклов -------------------
    def test_force_still_waits_for_exchange(self):
        async def run():
            entry = await server.get_candles("LINK_USDT", 5, force=True)
            self.assertEqual(self.feed.fetch_calls, 1)
            self.assertEqual(entry["source"], "exchange")
            self.assertEqual(len(entry["candles"]), 40)

        asyncio.run(run())

    def test_ws_sub_path_does_not_block_on_exchange(self):
        """WS `sub` зовёт get_candles без force — значит, тоже не ждёт биржу."""
        async def run():
            entry = await server.get_candles("AVAX_USDT", 15)
            self.assertEqual(self.feed.fetch_calls, 0)
            self.assertTrue(entry["candles"])
            await self._drain()

        asyncio.run(run())
        self.assertEqual(self.feed.fetch_calls, 1)
        self.assertEqual(server.CANDLES["AVAX_USDT|15"]["source"], "exchange")

    def test_kline_route_shape_is_backward_compatible(self):
        """Клиент (app.js, chart_panel.js) ждёт symbol/timeframe/source/candles."""
        async def run():
            data = await server.api_klines("btcusdt", 5)
            await self._drain()
            return data

        data = asyncio.run(run())
        for key in ("symbol", "timeframe", "source", "candles"):
            self.assertIn(key, data)
        self.assertEqual(data["symbol"], "BTC_USDT", "canon() обязан нормализовать")
        self.assertIsInstance(data["candles"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
