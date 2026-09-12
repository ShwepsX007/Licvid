#!/usr/bin/env bash
# Диагностика Hyperliquid-источника LiqScope одним махом.
# Запуск:  bash tools/diag_hl.sh
# Вывод можно целиком вставлять в чат/тикет — секретов не содержит.

set +e
cd "$(dirname "$0")/.." || exit 1

echo "=== дата/хост:"
date '+%F %T %z'
hostname
echo
echo "=== 1) коммит в репо (свежий — 21f4a78 или новее):"
git log --oneline -1
echo
echo "=== 2) процесс терминала на :8000 (время старта важно):"
ps -eo pid,lstart,cmd | grep -E "uvicorn|server\.py" | grep -v grep
echo
echo "=== 3) слушается ли порт 8000:"
ss -ltnp 2>/dev/null | grep ":8000" || netstat -ltnp 2>/dev/null | grep ":8000"
echo
echo "=== 4) REST до Hyperliquid с этой машины (ждём HTTP 200):"
curl -sS -m 8 -X POST https://api.hyperliquid.xyz/info \
     -H 'Content-Type: application/json' -d '{"type":"meta"}' \
     -o /dev/null -w 'HTTP %{http_code}, %{time_total}s\n'
echo
echo "=== 5) TLS/WS-рукопожатие до Hyperliquid (python, тот же aiohttp, что в бою):"
python3 - <<'PYEOF'
import asyncio, os, sys
try:
    import aiohttp
except ImportError:
    print("aiohttp не найден в этом python; пропускаю")
    sys.exit(0)

async def main():
    async with aiohttp.ClientSession(
            headers={"User-Agent": "LiqScope-Terminal/4.1"}) as s:
        try:
            ws = await s.ws_connect("wss://api.hyperliquid.xyz/ws",
                                    heartbeat=None, timeout=25)
            await ws.send_json({"method": "subscribe",
                                "subscription": {"type": "trades",
                                                 "coin": "BTC"}})
            msg = await ws.receive(timeout=5)
            print(f"WS OK: первый кадр {msg.type}, закрыт={ws.closed}")
            await ws.close()
        except Exception as e:
            print(f"WS FAIL: {type(e).__name__}: {e}")

asyncio.run(main())
PYEOF
echo
echo "=== 6) состояние HL-источника (attempts — сколько попыток коннекта):"
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
echo "=== 7) последние записи журнала про hyperliquid:"
if command -v journalctl >/dev/null 2>&1; then
    J=$(journalctl -u liqscope -n 200 --no-pager 2>/dev/null | \
         grep -iE "hyperliquid|обрыв" | tail -15)
    [ -n "$J" ] && echo "$J" || echo "(в journalctl -u liqscope пусто)"
else
    echo "(journalctl недоступен)"
fi
if [ -z "$J" ]; then
    for f in nohup.out server.log liqscope.log logs/*.log; do
        [ -f "$f" ] && echo "--- хвост $f:" && \
            grep -iE "hyperliquid|обрыв" "$f" | tail -15 && break
    done
fi
echo
echo "=== конец диагностики ==="
