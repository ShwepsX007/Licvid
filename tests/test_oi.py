"""
Офлайн-тесты открытого интереса (OI).

Запуск:  python3 tests/test_oi.py
Проверяем: парсеры ответов 7 бирж, склейку 5-минутных бакетов без
ложных дельт при смене покрытия, раскладку по свечам, изменения
m5/h1/h24, снапшот/ответ API, фикс лидеров (top_coins глобальные
при фильтре монеты) и окна стат-боксов (ликв. 1м/15м/30м/4ч,
OI m1 по живым опросам + m15/m30/h4 по бакетам).
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oi_feed import (OpenInterestTracker, bucket_5m, bucket_changes,  # noqa: E402
                     map_candles_to_oi, parse_binance_hist, parse_binance_spot,
                     parse_bitget_oi, parse_bitmex_oi, parse_bybit_hist,
                     parse_bybit_oi, parse_gate_stats, parse_htx_oi,
                     parse_okx_oi)

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


B0 = 1700000000 // 300 * 300

print("parsers: spot")
check("binance spot", parse_binance_spot({"openInterest": "2.0"}, 50000.0) == 100000.0)
check("binance spot no price", parse_binance_spot({"openInterest": "2.0"}, None) is None)
check("binance spot garbage", parse_binance_spot({"openInterest": "xx"}, 1) is None)
check("binance spot not dict", parse_binance_spot([1], 1) is None)

ts, usd = parse_bybit_oi(
    {"result": {"list": [{"openInterest": "3.0", "timestamp": "1700000300000"}]}},
    100.0)
check("bybit spot", (ts, usd) == (1700000300, 300.0), (ts, usd))
check("bybit spot empty", parse_bybit_oi({"result": {"list": []}}, 1) == (None, None))
check("bybit spot none", parse_bybit_oi(None, 1) == (None, None))

check("okx base", parse_okx_oi({"data": [{"oi": "100"}]}, 0.01, "BTC", 50000.0) == 50000.0)
check("okx usdt", parse_okx_oi({"data": [{"oi": "100"}]}, 10, "USDT", None) == 1000.0)
check("okx empty", parse_okx_oi({"data": []}, 1, "BTC", 1) is None)
check("okx bad ctval", parse_okx_oi({"data": [{"oi": "1"}]}, 0, "BTC", 1) is None)

check("bitget", parse_bitget_oi({"data": {"openInterestAmount": "2"}}, 100.0) == 200.0)
check("bitget none", parse_bitget_oi({}, 100.0) is None)
check("htx", parse_htx_oi({"data": [{"amount": 3.0, "volume": 30}]}, 100.0) == 300.0)
check("htx empty", parse_htx_oi({"data": []}, 100.0) is None)
check("bitmex inverse",
      parse_bitmex_oi([{"openInterest": 1000}], {"inverse": True}, 0) == 1000)
check("bitmex linear fallback price",
      parse_bitmex_oi([{"openInterest": 10, "lastPrice": 3000}],
                      {"inverse": False, "multiplier": 0.001}, None) == 30.0)
check("bitmex no meta",
      parse_bitmex_oi([{"openInterest": 10}], {"inverse": False, "multiplier": 0}, 5) is None)
check("bitmex empty", parse_bitmex_oi([], {}, 1) is None)

print("parsers: history")
bh = parse_binance_hist([
    {"sumOpenInterestValue": "100.0", "timestamp": (B0 + 300) * 1000},
    {"sumOpenInterestValue": "110.0", "timestamp": (B0 + 600) * 1000},
    {"sumOpenInterestValue": "xx", "timestamp": (B0 + 900) * 1000},
    {"nope": 1}])
check("binance hist", bh == {B0: 100.0, B0 + 300: 110.0}, bh)

yh = parse_bybit_hist({"result": {"list": [
    {"openInterest": "2.0", "timestamp": str((B0 + 600) * 1000)},
    {"openInterest": "1.0", "timestamp": str((B0 + 300) * 1000)}]}}, 50000.0)
check("bybit hist", yh == {B0 + 300: 100000.0, B0: 50000.0}, yh)
check("bybit hist no price", parse_bybit_hist({"result": {"list": []}}, None) == {})

gh = parse_gate_stats([
    {"time": B0 + 600, "open_interest_usd": "5.5"},
    {"time": B0 + 900, "open_interest_usd": 0},
    {"time": "bad", "open_interest_usd": "1"}])
check("gate hist", gh == {B0 + 300: 5.5}, gh)
check("gate hist ms",
      parse_gate_stats([{"time": (B0 + 600) * 1000, "open_interest_usd": 7}]) ==
      {B0 + 300: 7.0})

print("buckets")
check("bucket_5m", bucket_5m(B0 + 61) == B0)
cells = {1000: {"binance": 100.0},
         1300: {"binance": 110.0, "bybit": 50.0},
         1600: {"binance": 120.0, "bybit": 60.0}}
check("bucket_changes common legs",
      bucket_changes(cells) == {1300: 10.0, 1600: 20.0}, bucket_changes(cells))
check("bucket_changes no common",
      bucket_changes({100: {"binance": 1.0}, 400: {"gate": 2.0}}) == {400: None})

print("candle mapping")
lv = {1000: 100.0, 1300: 110.0, 1600: 130.0}
ch = {1300: 10.0, 1600: 20.0}
m = map_candles_to_oi([1000, 1300, 1600], 5, lv, ch)
check("map5 first", m[1000] == {"oi": 100.0, "oiChg": None}, m[1000])
check("map5 mid", m[1300] == {"oi": 110.0, "oiChg": 10.0}, m[1300])
check("map5 last", m[1600] == {"oi": 130.0, "oiChg": 20.0}, m[1600])
m15 = map_candles_to_oi([900], 15, {900: 50.0, 1200: 60.0, 1500: 70.0},
                        {900: 1.0, 1200: 5.0, 1500: 7.0})
check("map15 sums buckets", m15[900] == {"oi": 70.0, "oiChg": 13.0}, m15[900])
lv1 = {900: 90.0, 1200: 100.0}
ch1 = {900: 7.0, 1200: 10.0}
m1 = map_candles_to_oi([900, 960, 1020, 1080, 1140], 1, lv1, ch1)
check("map1 quiet minutes",
      [m1[t]["oiChg"] for t in [900, 960, 1020, 1080]] == [0.0] * 4)
check("map1 boundary minute", m1[1140]["oiChg"] == 7.0, m1[1140])
check("map1 level", m1[900]["oi"] == 90.0)
check("map empty", map_candles_to_oi([100], 5, {}, {}) ==
      {100: {"oi": None, "oiChg": None}})

print("tracker: changes/snapshot/payload")
tr = OpenInterestTracker(price_fn=lambda s: 50000.0,
                         bitmex_meta_fn=lambda: {"XBTUSD": {"inverse": True}})
now_b = int(time.time() // 300) * 300
tr._series["BTC_USDT"] = {
    now_b - 86400: {"binance": 100.0, "gate": 50.0},
    now_b - 3600: {"binance": 110.0, "gate": 55.0, "bybit": 30.0},
    now_b: {"binance": 120.0, "gate": 60.0, "bybit": 33.0},
}
c = tr.changes("BTC_USDT")
check("changes h24 common legs", c["h24"] and c["h24"]["usd"] == 30.0, c["h24"])
check("changes h24 partial", c["partial"]["h24"] is True, c["partial"])
check("changes h1 full", c["h1"] and c["h1"]["usd"] == 18.0, c["h1"])
check("changes h1 not partial", c["partial"]["h1"] is False, c["partial"])
check("changes pct", c["h1"] and abs(c["h1"]["pct"] - 18 / 195 * 100) < 1e-3, c["h1"])
check("changes hist list", c["hist_exchanges"] == ["binance", "bybit", "gate"],
      c["hist_exchanges"])
check("changes empty", tr.changes("NOPE")["h1"] is None)

s0 = tr.snapshot("BTC_USDT")
check("snapshot empty", s0["total_usd"] is None and s0["per_exchange"] == {})

now = time.time()
tr._live["BTC_USDT"] = {"binance": (now - 5, 120.0), "gate": (now - 400, 60.0)}
s1 = tr.snapshot("BTC_USDT")
check("snapshot total fresh legs only", s1["total_usd"] == 120.0, s1)
check("snapshot legs", list(s1["per_exchange"]) == ["binance"], s1)

p = tr.payload("BTC_USDT")
check("payload keys", set(p) == {"symbol", "total_usd", "per_exchange",
                                 "live_exchanges", "hist_exchanges", "changes",
                                 "partial", "ts", "stale_sec"}, sorted(p))
check("payload symbol", p["symbol"] == "BTC_USDT")

print("tracker: series continuity")
tr2 = OpenInterestTracker()
tr2._series["X"] = {1000: {"binance": 100.0},
                    1300: {"binance": 110.0, "bybit": 50.0},
                    1600: {"binance": 120.0, "bybit": 60.0}}
ser = tr2.series("X")
check("series anchors first", ser[1000] == 100.0, ser)
check("series no coverage jump", ser[1300] == 110.0 and ser[1600] == 130.0, ser)
check("bucket_chg passthrough", tr2.bucket_chg("X") == {1300: 10.0, 1600: 20.0})

print("tracker: bitmex symbols")
check("bitmex btc", tr.bitmex_symbol("BTC_USDT") == "XBTUSD")
check("bitmex unknown cached none", tr.bitmex_symbol("DOGE_USDT") is None)
tr3 = OpenInterestTracker(bitmex_meta_fn=lambda: {"DOGEUSDT": {}})
check("bitmex prefers usdt", tr3.bitmex_symbol("DOGE_USDT") == "DOGEUSDT")

print("tracker: watch LRU")
tr4 = OpenInterestTracker()
for i in range(15):
    tr4.watch(f"C{i}_USDT")
check("watch cap", len(tr4.watched_symbols()) == 12, len(tr4.watched_symbols()))
check("watch newest kept", "C14_USDT" in tr4.watched_symbols())


class FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._p


class FakeSession:
    """Стаб aiohttp-сессии: url-подстрока -> ответ."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        for key, payload in self.routes.items():
            if key in url:
                return FakeResp(payload)
        return FakeResp({}, status=404)


async def _fetch_all():
    fx = OpenInterestTracker(price_fn=lambda s: 100.0,
                             bitmex_meta_fn=lambda: {"XBTUSD": {"inverse": True}})
    fx.bind(FakeSession({
        "fapi/v1/openInterest": {"openInterest": "10"},
        "premiumIndex": {"markPrice": "100"},
        "openInterestHist": [{"sumOpenInterestValue": "777"}],
        "v5/market/open-interest": {"result": {"list": [
            {"openInterest": "5", "timestamp": "1700000300000"}]}},
        "public/open-interest": {"data": [{"oi": "20"}]},
        "contract_stats": [{"time": B0 + 300, "open_interest_usd": "33"}],
        "mix/market/open-interest": {"data": {"openInterestAmount": "7"}},
        "swap_open_interest": {"data": [{"amount": 9.0}]},
        "instrument": [{"openInterest": 111, "lastPrice": 100.0}],
    }))
    import liq_api
    real_spec = liq_api.get_okx_spec

    async def _fake_spec(session, base):
        return {"ctVal": "0.5", "ctValCcy": "BTC"}

    liq_api.get_okx_spec = _fake_spec
    try:
        return {
            "binance": await fx._fetch_binance("BTC_USDT"),
            "bybit": await fx._fetch_bybit("BTC_USDT"),
            "okx": await fx._fetch_okx("BTC_USDT"),
            "gate": await fx._fetch_gate("BTC_USDT"),
            "bitget": await fx._fetch_bitget("BTC_USDT"),
            "htx": await fx._fetch_htx("BTC_USDT"),
            "bitmex": await fx._fetch_bitmex("BTC_USDT"),
        }, fx
    finally:
        liq_api.get_okx_spec = real_spec


print("fetchers (fake session)")
res, _ = asyncio.run(_fetch_all())
check("fetch binance", res["binance"] == 1000.0, res["binance"])
check("fetch bybit", res["bybit"] == 500.0, res["bybit"])
check("fetch okx", res["okx"] == 1000.0, res["okx"])
check("fetch gate", res["gate"] == 33.0, res["gate"])
check("fetch bitget", res["bitget"] == 700.0, res["bitget"])
check("fetch htx", res["htx"] == 900.0, res["htx"])
check("fetch bitmex", res["bitmex"] == 111.0, res["bitmex"])


async def _sample_merge():
    fx = OpenInterestTracker(price_fn=lambda s: 100.0,
                             bitmex_meta_fn=lambda: {"XBTUSD": {"inverse": True}})
    fx.bind(FakeSession({
        "fapi/v1/openInterest": {"openInterest": "10"},
        "v5/market/open-interest": {"result": {"list": [
            {"openInterest": "5", "timestamp": str(int(time.time() * 1000))}]}},
        "public/open-interest": {"data": [{"oi": "20"}]},
        "contract_stats": [{"time": int(time.time()), "open_interest_usd": "33"}],
        "mix/market/open-interest": {"data": {"openInterestAmount": "7"}},
        "swap_open_interest": {"data": [{"amount": 9.0}]},
        "instrument": [{"openInterest": 111}],
    }))
    import liq_api
    real_spec = liq_api.get_okx_spec

    async def _fake_spec(session, base):
        return {"ctVal": "0.5", "ctValCcy": "BTC"}

    liq_api.get_okx_spec = _fake_spec
    try:
        legs = await fx.sample_symbol("BTC_USDT")
        return legs, fx
    finally:
        liq_api.get_okx_spec = real_spec


print("sample merge")
legs, fx = asyncio.run(_sample_merge())
check("sample 7 legs", len(legs) == 7, sorted(legs))
check("sample live stored", "binance" in (fx._live.get("BTC_USDT") or {}))
check("sample bucket merged",
      "binance" in fx._series["BTC_USDT"][bucket_5m(time.time())])
check("sample snapshot total", fx.snapshot("BTC_USDT")["total_usd"] ==
      sum(legs.values()))

print("leaders stay global")
import server as srv

srv.LIQUIDATIONS.clear()
srv.LIQUIDATIONS.extend([
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "SELL",
     "usd": 100.0, "timestamp": time.time()},
    {"symbol": "SOL_USDT", "exchange": "bybit", "side": "BUY",
     "usd": 500.0, "timestamp": time.time()},
])
try:
    filt = srv.compute_stats("SOL_USDT")
    check("filtered totals", filt["total_usd_24h"] == 500.0, filt["total_usd_24h"])
    check("leaders still all coins",
          [c["symbol"] for c in filt["top_coins"]] == ["SOL_USDT", "BTC_USDT"],
          [c["symbol"] for c in filt["top_coins"]])
    check("leaders usd", filt["top_coins"][1]["usd"] == 100.0)
    glob = srv.compute_stats()
    check("global totals", glob["total_usd_24h"] == 600.0)
    check("global leaders", len(glob["top_coins"]) == 2)
finally:
    srv.LIQUIDATIONS.clear()

print("statwin: liq windows")
srv.LIQUIDATIONS.clear()
_now = time.time()
srv.LIQUIDATIONS.extend([
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "SELL",
     "usd": 10.0, "timestamp": _now - 30},
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "BUY",
     "usd": 20.0, "timestamp": _now - 200},
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "SELL",
     "usd": 40.0, "timestamp": _now - 700},
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "BUY",
     "usd": 80.0, "timestamp": _now - 1500},
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "SELL",
     "usd": 160.0, "timestamp": _now - 3000},
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "BUY",
     "usd": 320.0, "timestamp": _now - 10000},
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "SELL",
     "usd": 640.0, "timestamp": _now - 40000},
    {"symbol": "BTC_USDT", "exchange": "binance", "side": "BUY",
     "usd": 1280.0, "timestamp": _now - 90000},
])
try:
    w = srv.compute_stats()
    check("win keys", all(k in w for k in
          ("total_usd_1m", "total_usd_15m", "total_usd_30m", "total_usd_4h")))
    check("win 1m", w["total_usd_1m"] == 10.0, w["total_usd_1m"])
    check("win 5m", w["total_usd_5m"] == 30.0, w["total_usd_5m"])
    check("win 15m", w["total_usd_15m"] == 70.0, w["total_usd_15m"])
    check("win 30m", w["total_usd_30m"] == 150.0, w["total_usd_30m"])
    check("win 1h", w["total_usd_1h"] == 310.0, w["total_usd_1h"])
    check("win 4h", w["total_usd_4h"] == 630.0, w["total_usd_4h"])
    check("win 24h", w["total_usd_24h"] == 1270.0, w["total_usd_24h"])
    check("win longs 1h", w["longs_usd_1h"] == 210.0, w["longs_usd_1h"])
    check("win shorts 4h", w["shorts_usd_4h"] == 420.0, w["shorts_usd_4h"])
finally:
    srv.LIQUIDATIONS.clear()

print("statwin: oi m1 + windows")
from collections import deque  # noqa: E402
from oi_feed import OI_WINDOWS  # noqa: E402

check("oi win keys", [k for k, _ in OI_WINDOWS] ==
      ["m1", "m5", "m15", "m30", "h1", "h4", "h24"])
trw = OpenInterestTracker()
_nowf = time.time()
trw._live_hist["BTC_USDT"] = deque([(_nowf - 60, {"binance": 1000.0}),
                                    (_nowf, {"binance": 1100.0})])
cw = trw.changes("BTC_USDT")
check("oi m1 delta", cw["m1"] == {"usd": 100.0, "pct": 10.0}, cw["m1"])
check("oi m1 full", cw["partial"]["m1"] is False, cw["partial"])
check("oi m5 none w/o buckets", cw["m5"] is None)

trw._live_hist["ONE"] = deque([(_nowf, {"binance": 5.0})])
co = trw.changes("ONE")
check("oi m1 single none", co["m1"] is None)
check("oi m1 single partial", co["partial"]["m1"] is True)

trw._live_hist["FAST"] = deque([(_nowf - 10, {"binance": 100.0}),
                                (_nowf, {"binance": 110.0})])
cf = trw.changes("FAST")
check("oi m1 short dt delta", cf["m1"] and cf["m1"]["usd"] == 10.0, cf["m1"])
check("oi m1 short dt partial", cf["partial"]["m1"] is True, cf["partial"])

trw._live_hist["PICK"] = deque([(_nowf - 70, {"binance": 100.0}),
                                (_nowf - 40, {"binance": 105.0}),
                                (_nowf, {"binance": 110.0})])
cp = trw.changes("PICK")
check("oi m1 picks nearest 60s", cp["m1"] and cp["m1"]["usd"] == 10.0, cp["m1"])
check("oi m1 nearest full", cp["partial"]["m1"] is False, cp["partial"])

trb = OpenInterestTracker()
_now_b = int(time.time() // 300) * 300
trb._series["BTC_USDT"] = {
    _now_b - 14400: {"binance": 100.0, "bybit": 100.0, "gate": 100.0},
    _now_b - 1800: {"binance": 110.0, "bybit": 110.0, "gate": 110.0},
    _now_b - 900: {"binance": 120.0, "bybit": 120.0, "gate": 120.0},
    _now_b: {"binance": 130.0, "bybit": 130.0, "gate": 130.0},
}
cb = trb.changes("BTC_USDT")
check("oi h4", cb["h4"] and cb["h4"]["usd"] == 90.0, cb["h4"])
check("oi h4 full", cb["partial"]["h4"] is False, cb["partial"])
check("oi m30", cb["m30"] and cb["m30"]["usd"] == 60.0, cb["m30"])
check("oi m15", cb["m15"] and cb["m15"]["usd"] == 30.0, cb["m15"])
check("oi payload win keys",
      set(trb.payload("BTC_USDT")["changes"]) == {k for k, _ in OI_WINDOWS})
_demo = srv._demo_oi_payload("BTC_USDT")
check("oi demo win keys",
      set(_demo["changes"]) == {k for k, _ in OI_WINDOWS} and
      set(_demo["partial"]) == {k for k, _ in OI_WINDOWS})

print()
print(f"итог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
