"""
Офлайн-тесты среднего оборота за неделю (volAvg7d).

Запуск:  python3 tests/test_volavg.py   (нужен aiohttp/fastapi: /tmp/v310)
Проверяем: запись часовых срезов оборота, среднее по кольцу, фолбэк
на текущий volume24h, чистку старых срезов и кап длины, персист
в файл + загрузку при старте, поле volAvg7d в /api/symbols.
"""

import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_feed as mf  # noqa: E402
from market_feed import MarketFeed  # noqa: E402

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
    return MarketFeed(on_liquidation=lambda e: asyncio.sleep(0),
                      on_price=lambda s, p, c: asyncio.sleep(0))


# Изолируем файл истории от рабочего data/
tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
tmp.close()
os.unlink(tmp.name)
mf.VOL_HIST_FILE = tmp.name

print("vol history: record/avg/fallback")
feed = make_feed()
check("unknown coin zero", feed.vol_avg7d("BTC_USDT") == 0.0)

feed.symbol_meta["BTC_USDT"] = {"volume24h": 6e10, "price": 1.0, "change24h": 0.0}
check("fallback to volume24h", feed.vol_avg7d("BTC_USDT") == 6e10)

feed._record_volumes()
check("recorded one", len(feed.vol_hist.get("BTC_USDT") or []) == 1)
check("still fallback (< 3)", feed.vol_avg7d("BTC_USDT") == 6e10)

feed.symbol_meta["BTC_USDT"]["volume24h"] = 4e10
feed._record_volumes()
feed.symbol_meta["BTC_USDT"]["volume24h"] = 5e10
feed._record_volumes()
check("three samples", len(feed.vol_hist["BTC_USDT"]) == 3)
check("avg math", feed.vol_avg7d("BTC_USDT") == (6e10 + 4e10 + 5e10) / 3,
      feed.vol_avg7d("BTC_USDT"))

print("vol history: prune/cap/zeros")
now = time.time()
feed.vol_hist["OLD_USDT"] = [[now - 8 * 86400, 100.0], [now - 3600, 200.0]]
feed.symbol_meta["OLD_USDT"] = {"volume24h": 300.0}
feed._record_volumes()
check("stale pruned", feed.vol_hist["OLD_USDT"] == [[now - 3600, 200.0],
                                                   feed.vol_hist["OLD_USDT"][-1]],
      feed.vol_hist["OLD_USDT"])
check("pruned tail fresh", feed.vol_hist["OLD_USDT"][-1][1] == 300.0)

feed.vol_hist["CAP_USDT"] = [[now - i, 1.0] for i in range(250, 0, -1)]
feed.symbol_meta["CAP_USDT"] = {"volume24h": 1.0}
feed._record_volumes()
check("capped at 200", len(feed.vol_hist["CAP_USDT"]) == mf.VOL_HIST_KEEP_N,
      len(feed.vol_hist["CAP_USDT"]))

feed.symbol_meta["ZERO_USDT"] = {"volume24h": 0.0}
feed.symbol_meta["NEG_USDT"] = {"volume24h": -5.0}
feed._record_volumes()
check("zero not recorded", "ZERO_USDT" not in feed.vol_hist)
check("negative not recorded", "NEG_USDT" not in feed.vol_hist)

print("vol history: persist/load")
feed._save_vol_hist()
check("file written", os.path.exists(tmp.name))
feed2 = make_feed()
check("loaded back", feed2.vol_hist.get("BTC_USDT") == feed.vol_hist["BTC_USDT"])
check("loaded avg", feed2.vol_avg7d("BTC_USDT") == feed.vol_avg7d("BTC_USDT"))

with open(tmp.name, "w", encoding="utf-8") as f:
    json.dump({"STALE_USDT": [[now - 10 * 86400, 50.0]]}, f)
feed3 = make_feed()
check("stale dropped on load", "STALE_USDT" not in feed3.vol_hist)

print("vol history: api field")
import server as srv  # noqa: E402

feed.symbols = ["BTC_USDT", "GRAM_USDT"]
feed.symbol_meta["GRAM_USDT"] = {"volume24h": 6e7, "price": 0.4,
                                 "change24h": 1.0, "exchanges": ["gate"]}
feed.prices = {}
srv.feed = feed
try:
    payload = asyncio.run(srv.api_symbols())
finally:
    srv.feed = None
rows = {r["symbol"]: r for r in payload["details"]}
check("row has volAvg7d", "volAvg7d" in rows["BTC_USDT"], sorted(rows["BTC_USDT"]))
check("row avg value", rows["BTC_USDT"]["volAvg7d"] == 5e10, rows["BTC_USDT"]["volAvg7d"])
check("row fallback value", rows["GRAM_USDT"]["volAvg7d"] == 6e7,
      rows["GRAM_USDT"]["volAvg7d"])

try:
    os.unlink(tmp.name)
except OSError:
    pass

print()
print(f"итог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
