"""Риск-лимиты бирж: ступени MMR, предел плеча и кэш.

Регрессия из задачи «расчётные уровни ликвидаций»: без поддерживающей маржи
цена ликвидации считается «на глаз» — у BTC это 0.4–0.65 %, у альта на большом
плече до 2–3 %, то есть ошибка в разы. Модуль собирает эти числа с публичных
ручек бирж, а где их нет (Binance: ступени за подписью) — честно помечает
значение оценкой, а не выдаёт её за факт.

Проверяем:
    * нормировку ставок: «0.0065», «0.65» и 2.5 — это доли, а не одно и то же
      число (биржи не сходятся в единицах);
    * точечные парсеры Bybit / OKX / Gate / Binance на документированных
      ответах и общий обход дерева — на переехавшем формате;
    * выбор ступени по объёму позиции (сортировка лестницы и «хвост»);
    * запасное значение, когда биржа не ответила (и пометку estimated);
    * кэш: свежий ответ не перезапрашивается, протухший — запрашивается;
    * диск: сохранение и чтение кэша, устаревшие записи не воскресают.

Запуск: python3 -m pytest tests/test_risk_limit.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import risk_limit  # noqa: E402
from risk_limit import (RiskLimits, default_mmr, norm_rate,  # noqa: E402
                        parse_binance_brackets, parse_binance_exchange_info,
                        parse_bybit_risk_limit, parse_gate_contract,
                        parse_okx_position_tiers, sort_tiers, tier_mmr,
                        walk_tiers)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Сессия aiohttp «в миниатюре»: отдаёт заготовленные ответы по адресу."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        payload = self.routes.get(url)
        if payload is None:
            raise RuntimeError(f"нет ответа для {url}")
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)


BYBIT = {"retCode": 0, "result": {"list": [
    {"id": 1, "symbol": "BTCUSDT", "riskLimitValue": "2000000",
     "maintenanceMargin": "0.005", "initialMargin": "0.01",
     "maxLeverage": "100", "isLowestRisk": 1},
    {"id": 2, "symbol": "BTCUSDT", "riskLimitValue": "5000000",
     "maintenanceMargin": "0.01", "initialMargin": "0.02", "maxLeverage": "50"},
]}}

OKX = {"code": "0", "data": [
    {"instFamily": "BTC-USDT", "tier": "1", "minSz": "0", "maxSz": "100",
     "mmr": "0.004", "imr": "0.008", "maxLever": "125"},
    {"instFamily": "BTC-USDT", "tier": "2", "minSz": "100", "maxSz": "500",
     "mmr": "0.006", "imr": "0.012", "maxLever": "75"},
]}

GATE = {"name": "BTC_USDT", "maintenance_rate": "0.005", "leverage_max": "100",
        "risk_limit_base": "500000", "risk_limit_step": "500000",
        "risk_limit_max": "3000000"}

BINANCE_INFO = {"symbols": [
    {"symbol": "BTCUSDT", "maintMarginPercent": "2.5000",
     "requiredMarginPercent": "5.0000", "pricePrecision": 2},
    {"symbol": "ETHUSDT", "maintMarginPercent": "2.5000",
     "requiredMarginPercent": "5.0000"},
]}

BINANCE_BRACKETS = [{"symbol": "BTCUSDT", "notionalCoef": 1.5, "brackets": [
    {"bracket": 1, "initialLeverage": 125, "notionalCap": 50000,
     "notionalFloor": 0, "maintMarginRatio": 0.004, "cum": 0.0},
    {"bracket": 2, "initialLeverage": 100, "notionalCap": 250000,
     "notionalFloor": 50000, "maintMarginRatio": 0.005, "cum": 50.0},
]}]


class NormRateTest(unittest.TestCase):
    def test_fraction_and_percent_are_one_rate(self):
        self.assertAlmostEqual(norm_rate(0.0065), 0.0065, places=9)
        self.assertAlmostEqual(norm_rate("0.65"), 0.0065, places=9)
        self.assertAlmostEqual(norm_rate("2.5"), 0.025, places=9)
        self.assertAlmostEqual(norm_rate(2), 0.02, places=9)

    def test_ambiguous_values_are_read_as_percent(self):
        """0.3 — это 0.3 %, а не 30 % маржи: таких ставок не бывает."""
        self.assertAlmostEqual(norm_rate(0.3), 0.003, places=9)
        self.assertAlmostEqual(norm_rate("0.9"), 0.009, places=9)
        self.assertAlmostEqual(norm_rate(0.25), 0.0025, places=9)

    def test_garbage_is_not_a_rate(self):
        for bad in (None, "", "abc", 0, -1, 30, "не число"):
            self.assertIsNone(norm_rate(bad), f"{bad!r} — не ставка")


class ParserTest(unittest.TestCase):
    def test_bybit_tiers(self):
        tiers = parse_bybit_risk_limit(BYBIT)
        self.assertEqual(len(tiers), 2)
        self.assertAlmostEqual(tiers[0]["mmr"], 0.005, places=9)
        self.assertEqual(tiers[0]["max_leverage"], 100)
        self.assertEqual(tiers[0]["max_notional"], 2_000_000)
        self.assertAlmostEqual(tiers[1]["mmr"], 0.01, places=9)

    def test_okx_tiers_keep_contract_bounds(self):
        tiers = parse_okx_position_tiers(OKX)
        self.assertEqual([t["mmr"] for t in tiers], [0.004, 0.006])
        self.assertEqual(tiers[0]["max_leverage"], 125)
        # границы OKX — в контрактах, а не в USD: важно не выдать их за ноционал
        self.assertIsNone(tiers[0]["max_notional"])
        self.assertEqual(tiers[0]["max_contracts"], 100)

    def test_gate_single_tier_from_contract(self):
        tiers = parse_gate_contract(GATE)
        self.assertEqual(len(tiers), 1)
        self.assertAlmostEqual(tiers[0]["mmr"], 0.005, places=9)
        self.assertEqual(tiers[0]["max_leverage"], 100)

    def test_binance_exchange_info_is_the_venue_default(self):
        tiers = parse_binance_exchange_info(BINANCE_INFO, "BTC_USDT")
        self.assertEqual(len(tiers), 1)
        self.assertAlmostEqual(tiers[0]["mmr"], 0.025, places=9)
        self.assertIsNone(tiers[0]["max_leverage"])
        self.assertEqual(parse_binance_exchange_info(BINANCE_INFO, "XRP_USDT"), [])

    def test_binance_brackets_sorted_ladder(self):
        tiers = parse_binance_brackets(BINANCE_BRACKETS)
        self.assertEqual([t["max_notional"] for t in tiers], [50000, 250000])
        self.assertEqual([t["mmr"] for t in tiers], [0.004, 0.005])

    def test_walk_tiers_survives_moved_payload(self):
        """Формат уехал в другой контейнер — общий обход всё равно достаёт."""
        payload = {"code": 0, "data": {"rows": [
            {"info": {"maintenance_rate": "0.012", "max_leverage": "20"}},
            {"info": {"liquidation": {"mmr": 0.5, "maxLever": 10}}},
        ]}}
        tiers = walk_tiers(payload)
        rates = sorted(t["mmr"] for t in tiers)
        self.assertEqual(rates, [0.005, 0.012])


class TierPickTest(unittest.TestCase):
    def test_ladder_is_sorted_and_picked_by_notional(self):
        tiers = [{"mmr": 0.01, "max_notional": 5_000_000},
                 {"mmr": 0.005, "max_notional": 2_000_000}]
        self.assertAlmostEqual(tier_mmr(tiers, 1000), 0.005, places=9)
        self.assertAlmostEqual(tier_mmr(tiers, 3_000_000), 0.01, places=9)

    def test_tail_without_cap_is_last(self):
        tiers = [{"mmr": 0.05, "max_notional": None},
                 {"mmr": 0.004, "max_notional": 50_000}]
        self.assertAlmostEqual(tier_mmr(tiers, 1_000), 0.004, places=9)
        self.assertAlmostEqual(tier_mmr(tiers, 900_000), 0.05, places=9)

    def test_unknown_notional_takes_first_rung(self):
        tiers = sort_tiers([{"mmr": 0.006, "max_notional": 20_000},
                            {"mmr": 0.02, "max_notional": None}])
        self.assertAlmostEqual(tier_mmr(tiers, None), 0.006, places=9)


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="risklimit_")
        self.path = os.path.join(self.tmp, "risk.json")

    def test_disk_roundtrip(self):
        rl = RiskLimits(path=self.path, ttl=3600)
        rl._data["BTC_USDT"] = {"bybit": risk_limit._venue("risk-limit",
                                                            parse_bybit_risk_limit(BYBIT))}
        self.assertTrue(rl.save())
        other = RiskLimits(path=self.path, ttl=3600)
        self.assertEqual(other.load(), 1)
        self.assertAlmostEqual(other.mmr("BTC_USDT", "bybit"), 0.005, places=9)
        self.assertEqual(other.get("BTC_USDT")["bybit"]["source"], "risk-limit")

    def test_stale_disk_entries_are_not_revived(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"symbols": {"BTC_USDT": {"bybit": {
                "tiers": [{"mmr": 0.005}], "ts": time.time() - 10 * 86400}}}}, f)
        rl = RiskLimits(path=self.path, ttl=3600)
        self.assertEqual(rl.load(), 0)

    def test_fallback_is_flagged_estimated(self):
        rl = RiskLimits(path="", ttl=3600)
        rate, estimated = rl.mmr_or_default("BTC_USDT")
        self.assertTrue(estimated)
        self.assertAlmostEqual(rate, default_mmr("BTC_USDT"), places=9)
        self.assertAlmostEqual(rate, 0.005, places=9)
        self.assertAlmostEqual(default_mmr("PEPE_USDT"), 0.01, places=9)

    def test_venues_are_averaged_when_several_answer(self):
        rl = RiskLimits(path="", ttl=3600)
        rl._data["BTC_USDT"] = {
            "bybit": risk_limit._venue("risk-limit", [{"mmr": 0.005}]),
            "okx": risk_limit._venue("position-tiers", [{"mmr": 0.004}]),
        }
        rate, estimated = rl.mmr_or_default("BTC_USDT")
        self.assertFalse(estimated)
        self.assertAlmostEqual(rate, 0.0045, places=9)
        # с весами бирж (доля их OI) среднее считается по ним
        rate, _ = rl.mmr_or_default("BTC_USDT",
                                    weights={"bybit": 3.0, "okx": 1.0})
        self.assertAlmostEqual(rate, 0.00475, places=9)

    def test_snapshot_shape_for_api(self):
        rl = RiskLimits(path="", ttl=3600)
        rl._data["BTC_USDT"] = {"bybit": risk_limit._venue(
            "risk-limit", parse_bybit_risk_limit(BYBIT), url="u")}
        snap = rl.snapshot("BTC_USDT")
        self.assertFalse(snap["estimated"])
        self.assertEqual(snap["max_leverage"], 100)
        self.assertEqual(snap["venues"]["bybit"]["source"], "risk-limit")
        self.assertIn("age_sec", snap["venues"]["bybit"])


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="riskfetch_")
        risk_limit.REQ_GAP_SEC = 0.0

    def test_fetch_uses_documented_route(self):
        session = FakeSession({
            "https://api.bybit.com/v5/market/risk-limit": BYBIT,
        })
        rl = RiskLimits(path="", ttl=3600)
        out = asyncio.run(rl.ensure(session, "BTC_USDT", exchanges=["bybit"]))
        self.assertIn("bybit", out)
        self.assertAlmostEqual(rl.mmr("BTC_USDT", "bybit"), 0.005, places=9)
        url, params = session.calls[0]
        self.assertEqual(params["symbol"], "BTCUSDT")
        self.assertEqual(params["category"], "linear")

    def test_fresh_answer_is_not_requested_again(self):
        session = FakeSession({
            "https://api.bybit.com/v5/market/risk-limit": BYBIT,
        })
        rl = RiskLimits(path="", ttl=3600)
        asyncio.run(rl.ensure(session, "BTC_USDT", exchanges=["bybit"]))
        asyncio.run(rl.ensure(session, "BTC_USDT", exchanges=["bybit"]))
        self.assertEqual(len(session.calls), 1)

    def test_binance_falls_back_to_exchange_info(self):
        session = FakeSession({
            risk_limit._binance_web_brackets("BTC_USDT"): RuntimeError("403"),
            "https://fapi.binance.com/fapi/v1/exchangeInfo": BINANCE_INFO,
        })
        rl = RiskLimits(path="", ttl=3600)
        asyncio.run(rl.ensure(session, "BTC_USDT", exchanges=["binance"]))
        venue = rl.get("BTC_USDT")["binance"]
        self.assertEqual(venue["source"], "exchange-info")
        self.assertAlmostEqual(rl.mmr("BTC_USDT", "binance"), 0.025, places=9)

    def test_failed_venue_is_remembered_not_hammered(self):
        session = FakeSession({})            # все маршруты падают
        rl = RiskLimits(path="", ttl=3600)
        asyncio.run(rl.ensure(session, "BTC_USDT", exchanges=["bybit"]))
        first = len(session.calls)
        asyncio.run(rl.ensure(session, "BTC_USDT", exchanges=["bybit"]))
        self.assertEqual(len(session.calls), first)   # повтор не пошёл
        rate, estimated = rl.mmr_or_default("BTC_USDT", "bybit")
        self.assertTrue(estimated)

    def test_disabled_module_never_touches_network(self):
        session = FakeSession({})
        rl = RiskLimits(path="", ttl=3600, enabled=False)
        asyncio.run(rl.ensure(session, "BTC_USDT"))
        self.assertEqual(session.calls, [])
        self.assertEqual(rl.status()["enabled"], False)

    def test_status_reports_sources(self):
        session = FakeSession({
            "https://api.bybit.com/v5/market/risk-limit": BYBIT,
        })
        rl = RiskLimits(path="", ttl=3600)
        asyncio.run(rl.ensure(session, "BTC_USDT", exchanges=["bybit"]))
        st = rl.status()
        self.assertEqual(st["symbols"], 1)
        self.assertEqual(st["venues"], 1)
        self.assertEqual(st["sources"], {"risk-limit": 1})
        self.assertGreaterEqual(st["requests"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
