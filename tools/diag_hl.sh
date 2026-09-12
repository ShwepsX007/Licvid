#!/usr/bin/env bash
# Диагностика Hyperliquid-источника LiqScope, версия 2.
# Запуск:  bash tools/diag_hl.sh   (идёт ~2.5 минуты — это нормально)
# Вывод можно целиком вставлять в чат/тикет — секретов не содержит.

set +e
cd "$(dirname "$0")/.." || exit 1

echo "=== дата/хост:"
date '+%F %T %z'
echo
echo "=== 1) процесс терминала (время старта важно):"
ps -eo pid,lstart,cmd | grep -E "uvicorn|server\.py" | grep -v grep
echo
echo "=== 2) REST до Hyperliquid (ждём HTTP 200):"
curl -sS -m 8 -X POST https://api.hyperliquid.xyz/info \
     -H 'Content-Type: application/json' -d '{"type":"meta"}' \
     -o /dev/null -w 'HTTP %{http_code}, %{time_total}s\n'
echo
echo "=== 3) ДЛИТЕЛЬНЫЙ WS-эксперимент v2 (боевые монеты через hl_coin_map):"
PYBIN=/root/Licvid/venv/bin/python3
[ -x "$PYBIN" ] || PYBIN=python3
"$PYBIN" - <<'PYEOF'
import asyncio, sys, time
sys.path.insert(0, ".")
import aiohttp
from aiohttp import ClientWSTimeout
from market_feed import hl_coin_map

HOLD = 25.0
UA_PROD = "LiqScope-Terminal/4.1"

async def get_coins():
    async with aiohttp.ClientSession() as s:
        async with s.get("http://127.0.0.1:8000/api/symbols",
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            syms = (await r.json()).get("symbols") or []
        async with s.post("https://api.hyperliquid.xyz/info",
                          json={"type": "meta"},
                          timeout=aiohttp.ClientTimeout(total=10)) as r:
            meta = await r.json()
    universe = [u.get("name") for u in (meta.get("universe") or [])
                if u.get("name")]
    m = hl_coin_map(syms, universe)
    return sorted(m), len(universe), len(syms)

async def arm(label, headers, session_timeout):
    kw = {}
    if session_timeout:
        kw["timeout"] = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, **kw) as s:
        t0 = time.monotonic()
        try:
            ws = await s.ws_connect("wss://api.hyperliquid.xyz/ws",
                                    heartbeat=None,
                                    timeout=ClientWSTimeout(ws_close=25))
        except Exception as e:
            print(f"  [{label}] рукопожатие FAIL: {type(e).__name__}: {e}")
            return
        last = "-"
        death = None
        msgs = 0
        nsub = 0
        try:
            for coin in COINS:
                last = coin
                await ws.send_json({"method": "subscribe",
                                    "subscription": {"type": "trades",
                                                     "coin": coin}})
                nsub += 1
                t_end = time.monotonic() + 2.5
                acked = False
                while not acked and time.monotonic() < t_end:
                    try:
                        msg = await ws.receive(timeout=0.1)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        msgs += 1
                        if "subscriptionResponse" in msg.data and coin in msg.data:
                            acked = True
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        death = (f"{msg.type}, close_code={ws.close_code}, "
                                 f"exc={ws.exception()!r}")
                        break
                if death:
                    break
                await asyncio.sleep(0.2)
            if not death:
                end = time.monotonic() + HOLD
                while time.monotonic() < end:
                    try:
                        msg = await ws.receive(timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        msgs += 1
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        death = (f"{msg.type}, close_code={ws.close_code}, "
                                 f"exc={ws.exception()!r}")
                        break
        finally:
            life = time.monotonic() - t0
            if death:
                print(f"  [{label}] УМЕР через {life:.1f}с: {death}; "
                      f"последняя монета={last}, подписок {nsub}, кадров {msgs}")
            else:
                print(f"  [{label}] ЖИВО весь срок {life:.0f}с; подписок {nsub}, "
                      f"кадров {msgs}")
            try:
                await ws.close()
            except Exception:
                pass

async def main():
    global COINS
    COINS, nuniv, nsyms = await get_coins()
    print(f"universe={nuniv}, символов сервера={nsyms}, боевых монет={len(COINS)}")
    print(f"монеты: {', '.join(COINS)}")
    print(f"каждое подключение живёт до {HOLD:.0f}с — итог через ~2 мин")
    await arm("A: дефолтный UA, голая сессия (копия ночного зонда)",
              None, False)
    await arm("B: боевой UA, голая сессия",
              {"User-Agent": UA_PROD}, False)
    await arm("C: боевой UA + ClientTimeout(total=20) (копия боя)",
              {"User-Agent": UA_PROD}, True)

asyncio.run(main())
PYEOF
echo
echo "=== 4) состояние HL-источника:"
curl -s http://127.0.0.1:8000/api/health | python3 -c '
import json, sys
try:
    h = json.load(sys.stdin)["sources"]["hyperliquid"]
except Exception as e:
    print("health недоступен:", e); sys.exit(0)
for k in ("enabled", "connected", "uptime_sec", "reconnects", "attempts",
          "last_error"):
    print(f"  {k}: {h.get(k)}")'
echo
echo "=== конец диагностики v2 ==="
