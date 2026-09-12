#!/usr/bin/env bash
# Диагностика Hyperliquid-источника LiqScope одним махом.
# Запуск:  bash tools/diag_hl.sh   (идёт ~2 минуты — это нормально)
# Вывод можно целиком вставлять в чат/тикет — секретов не содержит.

set +e
cd "$(dirname "$0")/.." || exit 1

echo "=== дата/хост:"
date '+%F %T %z'
hostname
echo
echo "=== 0) это git-репозиторий?"
pwd
if [ -d .git ]; then
    git log --oneline -1
else
    echo "(.git нет — код обновляется копией файлов, git-команды тут не работают)"
fi
echo
echo "=== 1) процесс терминала (время старта важно):"
ps -eo pid,lstart,cmd | grep -E "uvicorn|server\.py" | grep -v grep
echo
echo "=== 2) REST до Hyperliquid (ждём HTTP 200):"
curl -sS -m 8 -X POST https://api.hyperliquid.xyz/info \
     -H 'Content-Type: application/json' -d '{"type":"meta"}' \
     -o /dev/null -w 'HTTP %{http_code}, %{time_total}s\n'
echo
echo "=== 3) ДЛИТЕЛЬНЫЙ WS-эксперимент (3 подключения по 25с, монеты боевые):"
python3 - <<'PYEOF'
import asyncio, json, socket, sys, time
try:
    import aiohttp
except ImportError:
    print("aiohttp не найден в этом python; пропускаю")
    sys.exit(0)

print("python:", sys.version.split()[0], "| aiohttp:", aiohttp.__version__)
try:
    infos = socket.getaddrinfo("api.hyperliquid.xyz", 443)
    fams = sorted({f"{'v4' if i[0]==socket.AF_INET else 'v6'}" for i in infos})
    print("DNS-семейства:", ", ".join(fams))
except Exception as e:
    print("DNS FAIL:", e)

HL_WS = "wss://api.hyperliquid.xyz/ws"
HOLD = 25.0          # сколько секунд держать каждое подключение
UA_PROD = "LiqScope-Terminal/4.1"

async def get_coins():
    """Монеты боевого слушателя; если сервер недоступен — majors."""
    bases = []
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("http://127.0.0.1:8000/api/symbols",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                syms = (await r.json()).get("symbols") or []
        for s_ in syms:
            b = str(s_).split("_")[0].upper()
            bases.append("kPEPE" if b == "PEPE" else b)
    except Exception as e:
        print("  (сервер не отдал монеты, беру majors:", e, ")")
        bases = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP", "SUI", "LINK",
                 "AVAX", "NEAR", "ARB", "ADA", "BNB", "LTC", "DOT"]
    return sorted(set(bases))

async def arm(label, headers, session_timeout):
    kw = {}
    if session_timeout:
        kw["timeout"] = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, **kw) as s:
        t0 = time.monotonic()
        try:
            ws = await s.ws_connect(HL_WS, heartbeat=None,
                                    timeout=__import__("aiohttp",
                                                       fromlist=["x"])
                                    .ClientWSTimeout(ws_close=25))
        except Exception as e:
            print(f"  [{label}] рукопожатие FAIL: {type(e).__name__}: {e}")
            return
        msgs = 0
        subs = 0
        death = None
        try:
            for coin in COINS:
                try:
                    await ws.send_json({"method": "subscribe",
                                        "subscription": {"type": "trades",
                                                         "coin": coin}})
                    subs += 1
                except Exception as e:
                    death = f"СМЕРТЬ НА ОТПРАВКЕ {coin}: {type(e).__name__}: {e}"
                    break
                await asyncio.sleep(0.4)     # темп боевого слушателя
                while True:
                    try:
                        msg = await ws.receive(timeout=0.05)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        msgs += 1
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        death = (f"{msg.type}, close_code={ws.close_code}, "
                                 f"exc={ws.exception()!r}")
                        break
                if death:
                    break
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
                      f"подписок {subs}, кадров {msgs}")
            else:
                print(f"  [{label}] ЖИВО весь срок {life:.0f}с; "
                      f"подписок {subs}, кадров {msgs}")
            try:
                await ws.close()
            except Exception:
                pass

async def main():
    global COINS
    COINS = await get_coins()
    print(f"монеты ({len(COINS)}): {', '.join(COINS)}")
    print(f"каждое подключение живёт до {HOLD:.0f}с — ждите, итог ~90с")
    await arm("A: дефолтный UA (копия ночного зонда)", None, False)
    await arm("B: боевой UA, без таймаута сессии",
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
echo "=== 5) журнал: последние строки про hyperliquid:"
J=""
if command -v journalctl >/dev/null 2>&1; then
    for u in licvid liqscope; do
        T=$(journalctl -u "$u" -n 200 --no-pager 2>/dev/null | \
             grep -iE "hyperliquid|обрыв" | tail -8)
        [ -n "$T" ] && J="$T" && echo "(из unit: $u)" && break
    done
fi
[ -n "$J" ] && echo "$J" || echo "(журнал пуст — не страшно)"
echo
echo "=== конец диагностики ==="
