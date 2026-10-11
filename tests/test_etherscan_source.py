"""Юнит-тесты Etherscan-коллектора (rotация ключей, курсоры, фильтры, статусы).

HTTP полностью подменён: коллекция преднастроенных ответов на (chain, module,
action, apikey). Сеть не нужна — проверяется логика, а не провайдер.

Run: python3 tests/test_etherscan_source.py
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

es = importlib.import_module("etherscan_source")


class FakeStore:
    """Пул ключей как у AlchemyKeyStore: [(id, secret), ...]."""

    def __init__(self, *pairs):
        self._pairs = list(pairs)
        self.removed = []

    def keys(self):
        return list(self._pairs)

    def public(self):
        return [{"id": kid, "hint": kid[:4] + "••••", "source": "admin"}
                for kid, _ in self._pairs]


class FakeScreener:
    """Пишет эмитнутые события; цены фиксированные, как у стейблов."""

    def __init__(self, wallets, tokens=None):
        self.wallets = wallets          # chain → {address: label}
        self.tokens = tokens or {}
        self.emitted = []
        self.seen = set()               # дедуп (chain, hash:index) как в скринере
        self.min_usd = 0

    def wallet_addresses(self, chain):
        return self.wallets.get(chain, {})

    async def _emit(self, chain, tx_hash, index, symbol, raw_amount, decimals,
                    sender, recipient, *, source="historical", timestamp=None):
        dedup = (chain, tx_hash + ":" + index)
        if dedup in self.seen:
            return
        self.seen.add(dedup)
        self.emitted.append({"chain": chain, "hash": tx_hash, "index": index,
                             "symbol": symbol, "raw": raw_amount,
                             "decimals": decimals, "from": sender, "to": recipient,
                             "source": source, "ts": timestamp})


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def text(self):
        return self._payload if isinstance(self._payload, str) else json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Отдаёт ответы по цепочке правил; пишет все запросы в self.calls.

    Правило: (pred(query) -> payload). payload — dict, кортеж (status, dict)
    или callable(query) → dict/кортеж: для stateful-сценариев (пагинация).
    """

    def __init__(self, rules):
        self.rules = rules
        self.calls = []

    def get(self, url, params=None, timeout=None):
        query = dict(params or {})
        self.calls.append(query)
        for pred, payload in self.rules:
            if pred(query):
                if callable(payload):
                    payload = payload(query)
                if isinstance(payload, tuple):
                    status, body = payload
                else:
                    status, body = 200, payload
                return FakeResponse(status, body)
        return FakeResponse(200, {"status": "0", "message": "No transactions found",
                                  "result": []})


def ok(rows):
    return {"status": "1", "message": "OK", "result": rows}


W1 = "0x" + "11" * 20
W2 = "0x" + "22" * 20
USDT_ETH = "0xdac17f958d2ee523a2206206994597c13d831ec7"
FAKE_HASH = "0x" + "ab" * 32


def tx_row(block, value, frm, to, *, tx_index=1, input_hex="0x", err="0",
           contract="", ts=1700000000, h=FAKE_HASH):
    return {"blockNumber": str(block), "hash": h, "from": frm, "to": to,
            "value": str(value), "transactionIndex": str(tx_index),
            "input": input_hex, "isError": err, "timeStamp": str(ts),
            "contractAddress": contract}


def make_collector(screener, store, **kw):
    tmp = tempfile.mkdtemp()
    return es.EtherscanCollector(
        screener, store, networks=None,
        state_file=Path(tmp) / "state.json",
        min_interval_sec=0.0, is_primary_active=kw.pop("primary", lambda: False),
        **kw)


class KeyRotationTests(unittest.TestCase):
    def test_round_robin_two_keys(self):
        """Каждый вызов берёт следующий ключ по кругу."""
        store = FakeStore(("k1", "KEY1"), ("k2", "KEY2"))
        coll = make_collector(FakeScreener({}), store)
        session = FakeSession([(lambda q: True, ok([]))])
        used = []
        for _ in range(4):
            # _api сам выбирает ключ; смотрим в journal сессии
            asyncio.run(coll._api(session, "ETH", "account", "tokentx",
                                  address=W1))
            used.append(session.calls[-1]["apikey"])
        self.assertEqual(used, ["KEY1", "KEY2", "KEY1", "KEY2"])

    def test_rate_limit_parks_and_rotates(self):
        """429 у первого ключа → кулдаун, вызов уходит второму."""
        store = FakeStore(("k1", "KEY1"), ("k2", "KEY2"))
        coll = make_collector(FakeScreener({}), store)
        session = FakeSession([
            (lambda q: q["apikey"] == "KEY1", (429, "too many")),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        self.assertEqual(session.calls[-1]["apikey"], "KEY2")
        # KEY1 в кулдауне — следующий вызов снова KEY2
        session2 = FakeSession([(lambda q: True, ok([]))])
        asyncio.run(coll._api(session2, "ETH", "account", "tokentx", address=W1))
        self.assertEqual(session2.calls[0]["apikey"], "KEY2")

    def test_daily_limit_message_parks_key(self):
        """«Rate limit... daily» — ключ отдыхает до конца суток UTC."""
        store = FakeStore(("k1", "KEY1"), ("k2", "KEY2"))
        coll = make_collector(FakeScreener({}), store)
        session = FakeSession([
            (lambda q: q["apikey"] == "KEY1",
             {"status": "0", "message": "Max calls per day reached",
              "result": []}),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        row = coll.key_runtime["k1"]
        day = 86400
        remaining = row["cooldown_until"] - time.time()
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, day + 120)

    def test_daily_counter_parks_key_without_provider_error(self):
        """Счётчик вызовов сам останавливает ключ на дневном лимите."""
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store, daily_calls=3)
        session = FakeSession([(lambda q: True, ok([]))])
        for _ in range(3):
            asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        self.assertEqual(coll.key_runtime["k1"]["calls"], 3)
        with self.assertRaises(es.EtherscanError):
            asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))

    def test_daily_limit_string_in_result_parks_key(self):
        """Etherscan кладёт «Max calls ... day» в result (message=NOTOK)."""
        store = FakeStore(("k1", "KEY1"), ("k2", "KEY2"))
        coll = make_collector(FakeScreener({}), store)
        session = FakeSession([
            (lambda q: q["apikey"] == "KEY1",
             {"status": "0", "message": "NOTOK",
              "result": "Max calls per day reached (100000)"}),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        self.assertEqual(session.calls[-1]["apikey"], "KEY2")
        remaining = coll.key_runtime["k1"]["cooldown_until"] - time.time()
        self.assertGreater(remaining, 3600)   # не двухсекундный кулдаун

    def test_invalid_key_string_in_result_parks_key(self):
        """«Invalid API Key» приходит в result — ключ уходит в 15-минутный
        кулдаун, а вызов переходит следующему."""
        store = FakeStore(("k1", "KEY1"), ("k2", "KEY2"))
        coll = make_collector(FakeScreener({}), store)
        session = FakeSession([
            (lambda q: q["apikey"] == "KEY1",
             {"status": "0", "message": "NOTOK", "result": "Invalid API Key"}),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        self.assertEqual(session.calls[-1]["apikey"], "KEY2")
        remaining = coll.key_runtime["k1"]["cooldown_until"] - time.time()
        self.assertGreater(remaining, 600)

    def test_sec_rate_limit_in_result_short_park(self):
        """«rate limit ... per sec» в result — короткий кулдаун, не день."""
        store = FakeStore(("k1", "KEY1"), ("k2", "KEY2"))
        coll = make_collector(FakeScreener({}), store)
        session = FakeSession([
            (lambda q: q["apikey"] == "KEY1",
             {"status": "0", "message": "NOTOK",
              "result": "Max calls per sec rate limit reached (5/sec)"}),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        remaining = coll.key_runtime["k1"]["cooldown_until"] - time.time()
        self.assertLess(remaining, 30)

    def test_transient_rate_limit_waits_out_with_single_key(self):
        """Одиночный 429 у единственного ключа пересижен, проход не падает."""
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store)
        real_park = es.RATE_PARK_SEC
        es.RATE_PARK_SEC = 0.05          # куцый кулдаун, чтобы тест не спал 2 с
        self.addCleanup(setattr, es, "RATE_PARK_SEC", real_park)
        hits = {"n": 0}

        def rate_once(_query):
            hits["n"] += 1
            return (429, "too many") if hits["n"] == 1 else ok([{"blockNumber": "7"}])

        session = FakeSession([(lambda q: True, rate_once)])
        result = asyncio.run(coll._api(session, "ETH", "block", "getblocknobytime"))
        self.assertEqual(result, [{"blockNumber": "7"}])
        self.assertEqual(len(session.calls), 2)   # повтор после короткой паузы

    def test_pool_exhausted_daily_limit_message(self):
        """Когда все ключи выбрали дневную квоту — честное сообщение, не «rotation»."""
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store, daily_calls=1)
        session = FakeSession([(lambda q: True, ok([]))])
        asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        with self.assertRaises(es.EtherscanError) as ctx:
            asyncio.run(coll._api(session, "ETH", "account", "tokentx", address=W1))
        self.assertIn("daily", str(ctx.exception).lower())

    def test_pool_exhausted_long_cooldown_reports_wait(self):
        """Долгий кулдаун (неверный ключ) — ошибка называет время ожидания."""
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store)
        coll._park("k1", es.AUTH_PARK_SEC)
        with self.assertRaises(es.EtherscanError) as ctx:
            asyncio.run(coll._api(FakeSession([]), "ETH", "account", "tokentx",
                                  address=W1))
        self.assertIn("cooling down", str(ctx.exception))

    def test_chain_calls_counted_and_reset_on_new_day(self):
        """Счётчик вызовов по сети растёт с каждым запросом и обнуляется в UTC-день."""
        screener = FakeScreener({"ETH": {W1: "Binance 14"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")),
                              native_enabled=False)
        session = FakeSession([
            (lambda q: q.get("module") == "block", ok([{"blockNumber": "90"}])),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._poll_wallet(session, "ETH", W1,
                                      screener.wallets["ETH"], {}))
        # якорь цепи + один запрос токенов кошелька
        self.assertEqual(coll.network_status["ETH"]["calls"], 2)
        self.assertEqual(coll._pass_calls, 2)
        coll.status()                       # статус не теряет счётчик
        self.assertEqual(coll.status()["networks"]["ETH"]["calls"], 2)
        coll._calls_day = "2000-01-01"      # принудительная смена суток
        self.assertEqual(coll.status()["networks"]["ETH"]["calls"], 0)

    def test_budget_interval_stretches_to_survive_the_day(self):
        """Остаток квоты растягивает интервал, но не дальше потолка."""
        store = FakeStore(("k1", "KEY1"), ("k2", "KEY2"))
        coll = make_collector(FakeScreener({}), store,
                              poll_interval_sec=180, daily_calls=95000)
        # квоты вдоволь — базовый интервал
        self.assertEqual(coll._budget_interval(85), 180.0)
        # почти всё потрачено, до полуночи далеко: нужен длинный сон, но ≤ 30 мин
        coll._key_state("k1")["calls"] = 94999
        coll._key_state("k2")["calls"] = 94999
        self.assertEqual(coll._budget_interval(85), es.MAX_STRETCH_INTERVAL_SEC)
        # квота кончилась — интервал не тянем, _api сам скажет «до 00:00 UTC»
        coll._key_state("k1")["calls"] = 95000
        coll._key_state("k2")["calls"] = 95000
        self.assertEqual(coll._budget_interval(85), 180.0)
        # проход без вызовов (всё в кулдауне) pacing не запускает
        self.assertEqual(coll._budget_interval(0), 180.0)

    def test_disabled_source_mode(self):
        """Тумблер источника: mode=disabled, цикл не опрашивает."""
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({"ETH": {W1: "A"}}), store)
        coll.enabled = False
        self.assertEqual(coll.status()["mode"], "disabled")
        self.assertFalse(coll.status()["enabled"])

    def test_new_utc_day_resets_counter(self):
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store, daily_calls=1)
        coll._key_state("k1")["calls"] = 1
        # подменяем день на вчерашний
        coll._key_state("k1")["day"] = "2000-01-01"
        row = coll._key_state("k1")
        self.assertEqual(row["calls"], 0)   # reset по смене дня


class PollingTests(unittest.TestCase):
    def test_tokentx_filter_and_cursor(self):
        """Только отслеживаемые контракты и кошельки; курсор — последний блок."""
        screener = FakeScreener(
            {"ETH": {W1: "Binance 14", W2: "Binance 15"}},
            tokens=None)
        tokens = {"ETH": {USDT_ETH: ("USDT", 6)}}
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        rows = [
            tx_row(100, 5_000_000, W1, W2, contract=USDT_ETH),      # USDT: эмит
            tx_row(101, 7_000_000, W1, W2, contract="0xdead"),     # чужой токен
            tx_row(102, 9_000_000, "0x" + "33" * 20, "0x" + "44" * 20,
                   contract=USDT_ETH),      # оба адреса вне базы кошельков
        ]
        session = FakeSession([
            (lambda q: q.get("module") == "block",
             ok([{"blockNumber": "90"}])),
            (lambda q: q.get("action") == "tokentx" and int(q.get("startblock", -1)) == 87,
             ok(rows)),
            (lambda q: True, ok([])),
        ])
        events = asyncio.run(coll._poll_wallet(session, "ETH", W1,
                                               screener.wallets["ETH"],
                                               tokens["ETH"]))
        self.assertEqual(events, 1)
        emit = screener.emitted[0]
        self.assertEqual(emit["symbol"], "USDT")
        self.assertEqual(emit["raw"], 5_000_000)
        self.assertEqual(emit["decimals"], 6)
        self.assertEqual(emit["source"], "realtime")
        self.assertEqual(emit["ts"], 1700000000)
        # курсор дошёл до последнего блока ответа
        self.assertEqual(coll.cursors[f"ETH|{W1}|token"], 102)

    def test_native_txlist_filter(self):
        """Нативный перевод эмитится ETH-ом; вызов контракта и failed — нет."""
        screener = FakeScreener({"ETH": {W1: "Binance 14", W2: "OKX"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        rows = [
            tx_row(200, 10 ** 18, W1, W2),                          # чистый перевод
            tx_row(201, 10 ** 18, W1, W2, input_hex="0xa9059cbb"), # вызов контракта
            tx_row(202, 10 ** 18, W1, W2, err="1"),                 # failed
            tx_row(203, 10 ** 18, "0x" + "55" * 20, "0x" + "44" * 20),  # вне базы
        ]
        session = FakeSession([
            (lambda q: q.get("module") == "block",
             ok([{"blockNumber": "190"}])),
            (lambda q: q.get("action") == "tokentx", ok([])),
            (lambda q: q.get("action") == "txlist" and int(q.get("startblock", -1)) == 187,
             ok(rows)),
            (lambda q: True, ok([])),
        ])
        events = asyncio.run(coll._poll_wallet(session, "ETH", W1,
                                               screener.wallets["ETH"], {}))
        self.assertEqual(events, 1)
        emit = screener.emitted[0]
        self.assertEqual(emit["symbol"], "ETH")
        self.assertEqual(emit["decimals"], 18)
        self.assertEqual(emit["raw"], 10 ** 18)
        self.assertEqual(coll.cursors[f"ETH|{W1}|native"], 203)

    def test_pagination_continues_while_full_page(self):
        """Полная страница → следующий запрос с того же блока (дедуп по index)."""
        screener = FakeScreener({"ETH": {W1: "Binance 14"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        big = [tx_row(300 + i, 1_000_000, W1, W1, contract=USDT_ETH,
                      h="0x" + f"{i:02x}" * 32) for i in range(es.PAGE_OFFSET)]
        extra = [tx_row(500, 2_000_000, W1, W1, contract=USDT_ETH,
                        h="0x" + "ff" * 32)]
        all_rows = big + extra

        def tokentx(query):
            # как реальный API: blockNumber >= startblock, не больше offset
            start = int(query.get("startblock", 0))
            return ok([r for r in all_rows
                       if int(r["blockNumber"]) >= start][:es.PAGE_OFFSET])

        session = FakeSession([
            (lambda q: q.get("module") == "block", ok([{"blockNumber": "299"}])),
            (lambda q: q.get("action") == "tokentx", tokentx),
            (lambda q: True, ok([])),
        ])
        events = asyncio.run(coll._poll_wallet(
            session, "ETH", W1, screener.wallets["ETH"],
            {USDT_ETH: ("USDT", 6)}))
        # 200 уникальных строк первой страницы + блок 500 второй; повтор
        # блока 499 съедается дедупом hash:index (его держит скринер).
        # events — попытки эмита (включая дубли), emitted — реальные записи
        self.assertEqual(len(screener.emitted), es.PAGE_OFFSET + 1)
        self.assertGreaterEqual(events, es.PAGE_OFFSET + 1)
        self.assertEqual(coll.cursors[f"ETH|{W1}|token"], 500)

    def test_index_unique_inside_tx(self):
        """Два USDT-перевода в одном tx получают разные index (es5, es5.1)."""
        screener = FakeScreener({"ETH": {W1: "Binance 14", W2: "Binance 15"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        rows = [
            tx_row(500, 1_000_000, W1, W2, tx_index=7, contract=USDT_ETH),
            tx_row(500, 2_000_000, W1, W2, tx_index=7, contract=USDT_ETH),
        ]
        session = FakeSession([
            (lambda q: q.get("module") == "block", ok([{"blockNumber": "490"}])),
            (lambda q: q.get("action") == "tokentx", ok(rows)),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._poll_wallet(session, "ETH", W1,
                                      screener.wallets["ETH"],
                                      {USDT_ETH: ("USDT", 6)}))
        self.assertEqual([e["index"] for e in screener.emitted], ["es7", "es7.1"])

    def test_anchor_accepts_dict_result(self):
        """V2 getblocknobytime отдаёт result-ОБЪЕКТ: якорь обязан читаться."""
        screener = FakeScreener({"ETH": {W1: "Binance 14"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        rows = [tx_row(400, 1_000_000, W1, W1, contract=USDT_ETH)]
        session = FakeSession([
            (lambda q: q.get("module") == "block",
             {"status": "1", "message": "OK",
              "result": {"blockNumber": "390"}}),
            (lambda q: q.get("action") == "tokentx" and int(q.get("startblock", -1)) == 387,
             ok(rows)),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._poll_wallet(session, "ETH", W1,
                                      screener.wallets["ETH"],
                                      {USDT_ETH: ("USDT", 6)}))
        self.assertEqual(coll.cursors[f"ETH|{W1}|token"], 400)
        self.assertEqual(len(screener.emitted), 1)

    def test_anchor_list_result_still_works(self):
        """Старая форма (список) тоже читается — обе формы V2 валидны."""
        screener = FakeScreener({"ETH": {W1: "Binance 14"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        session = FakeSession([
            (lambda q: q.get("module") == "block",
             ok([{"blockNumber": "390"}])),
            (lambda q: True, ok([])),
        ])
        anchor = asyncio.run(coll._anchor_block(session, "ETH"))
        self.assertEqual(anchor, 390)

    def test_anchor_fallback_to_newest_tx_when_block_endpoint_fails(self):
        """Блок-эндпоинт не дал якорь → берём блок последней записи кошелька."""
        screener = FakeScreener({"ETH": {W1: "Binance 14"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        # последняя запись кошелька — блок 510 → курсор 507; дальше asc-опрос
        rows = [tx_row(508, 1_000_000, W1, W1, contract=USDT_ETH)]
        session = FakeSession([
            (lambda q: q.get("module") == "block", ok([])),   # пустой/битый
            (lambda q: q.get("action") == "tokentx" and q.get("sort") == "desc",
             ok([tx_row(510, 1, W1, W1, contract=USDT_ETH)])),
            (lambda q: q.get("action") == "tokentx" and int(q.get("startblock", -1)) == 507,
             ok(rows)),
            (lambda q: True, ok([])),
        ])
        events = asyncio.run(coll._poll_wallet(session, "ETH", W1,
                                               screener.wallets["ETH"],
                                               {USDT_ETH: ("USDT", 6)}))
        self.assertEqual(events, 1)
        self.assertEqual(coll.cursors[f"ETH|{W1}|token"], 508)

    def test_anchor_cached_per_chain(self):
        """Блок-якорь спрашивается один раз на цепочку, не на каждый кошелёк."""
        screener = FakeScreener({"ETH": {W1: "A", W2: "B"}})
        coll = make_collector(screener, FakeStore(("k1", "KEY1")))
        session = FakeSession([
            (lambda q: q.get("module") == "block", ok([{"blockNumber": "777"}])),
            (lambda q: True, ok([])),
        ])
        asyncio.run(coll._poll_wallet(session, "ETH", W1, screener.wallets["ETH"], {}))
        asyncio.run(coll._poll_wallet(session, "ETH", W2, screener.wallets["ETH"], {}))
        anchors = [c for c in session.calls if c.get("module") == "block"]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(coll.cursors[f"ETH|{W1}|token"], 774)


class StatusTests(unittest.TestCase):
    def test_mode_primary_and_paused(self):
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store)
        self.assertEqual(coll.status()["mode"], "primary")
        coll2 = make_collector(FakeScreener({}), store, primary=lambda: True)
        self.assertEqual(coll2.status()["mode"], "paused_alchemy")
        coll3 = make_collector(FakeScreener({}), FakeStore())
        self.assertEqual(coll3.status()["mode"], "paused_no_keys")

    def test_status_keys_shape(self):
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store)
        payload = coll.status()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["keys_configured"], 1)
        self.assertIn("calls_today", payload)
        self.assertIn("daily_calls_limit", payload)
        row = payload["keys"][0]
        self.assertEqual(row["state"], "active")
        self.assertEqual(row["calls_today"], 0)
        self.assertIn("hint", row)
        self.assertIn("ETH", payload["networks"])

    def test_state_file_roundtrip(self):
        store = FakeStore(("k1", "KEY1"))
        coll = make_collector(FakeScreener({}), store)
        coll.cursors["ETH|w|token"] = 42
        coll._save()
        coll2 = make_collector(FakeScreener({}), FakeStore(("k1", "KEY1")))
        coll2.state_file = coll.state_file
        coll2._load()
        self.assertEqual(coll2.cursors.get("ETH|w|token"), 42)


if __name__ == "__main__":
    unittest.main(verbosity=2)
