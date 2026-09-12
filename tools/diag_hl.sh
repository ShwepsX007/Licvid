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
echo "=== 0) это git-репозиторий?"
pwd
if [ -d .git ]; then
    git log --oneline -1
else
    echo "(.git нет — код обновляется архивом/копией, git-команды тут не работают)"
fi
echo
echo "=== 1) процесс терминала (время старта важно):"
ps -eo pid,lstart,cmd | grep -E "uvicorn|server\.py" | grep -v grep
echo
echo "=== 2) слушается ли порт 8000:"
ss -ltnp 2>/dev/null | grep ":8000" || netstat -ltnp 2>/dev/null | grep ":8000"
echo
echo "=== 3) REST до Hyperliquid (ждём HTTP 200):"
curl -sS -m 8 -X POST https://api.hyperliquid.xyz/info \
     -H 'Content-Type: application/json' -d '{"type":"meta"}' \
     -o /dev/null -w 'HTTP %{http_code}, %{time_total}s\n'
echo
echo "=== 4) WS-эксперименты до Hyperliquid (матрица: семейство x User-Agent):"
python3 - <<'PYEOF'
import asyncio, socket, sys
try:
    import aiohttp
except ImportError:
    print("aiohttp не найден в этом python; пропускаю")
    sys.exit(0)

print("python:", sys.version.split()[0], "| aiohttp:", aiohttp.__version__)
try:
    infos = socket.getaddrinfo("api.hyperliquid.xyz", 443)
    fams = [f"{'v4' if i[0]==socket.AF_INET else 'v6'}:{i[4][0]}" for i in infos]
    print("DNS:", ", ".join(fams))
except Exception as e:
    print("DNS FAIL:", e)

HL_WS = "wss://api.hyperliquid.xyz/ws"

async def attempt(label, ua=None, family=None):
    kw = {}
    if family:
        kw["connector"] = aiohttp.TCPConnector(family=family)
    async with aiohttp.ClientSession(
            headers=({"User-Agent": ua} if ua else None), **kw) as s:
        try:
            ws = await s.ws_connect(HL_WS, heartbeat=None, timeout=25)
            await ws.send_json({"method": "subscribe",
                                "subscription": {"type": "trades",
                                                 "coin": "BTC"}})
            got = "нет ответа за 3с"
            for _ in range(3):
                msg = await ws.receive(timeout=3)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    got = "ack/лента пришла, соединение ЖИВО"
                    break
                if msg.type in (aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR):
                    got = (f"УМЕР: {msg.type}, close_code={ws.close_code}, "
                           f"exc={ws.exception()!r}")
                    break
            print(f"  [{label}] рукопожатие OK; подписка: {got}")
            await ws.close()
        except Exception as e:
            print(f"  [{label}] FAIL: {type(e).__name__}: {e}")

async def main():
    await attempt("боевой UA, как получится", ua="LiqScope-Terminal/4.1")
    await attempt("боевой UA, только IPv4", ua="LiqScope-Terminal/4.1",
                  family=socket.AF_INET)
    await attempt("боевой UA, только IPv6", ua="LiqScope-Terminal/4.1",
                  family=socket.AF_INET6)
    await attempt("дефолтный UA (как у зонда), как получится")

asyncio.run(main())
PYEOF
echo
echo "=== 4b) версии python/aiohttp В БОЕВОМ venv:"
/root/Licvid/venv/bin/python3 -c "import sys, aiohttp; print('python', sys.version.split()[0], '| aiohttp', aiohttp.__version__)" 2>/dev/null || echo "(venv не в /root/Licvid/venv — укажите путь)"
echo
echo "=== 5) состояние HL-источника (attempts — число попыток коннекта):"
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
echo "=== 6) последние записи журнала про hyperliquid:"
J=""
if command -v journalctl >/dev/null 2>&1; then
    for u in liqscope licvid licvid-terminal liqscope-terminal; do
        T=$(journalctl -u "$u" -n 200 --no-pager 2>/dev/null | \
             grep -iE "hyperliquid|обрыв" | tail -15)
        [ -n "$T" ] && J="$T" && echo "(из unit: $u)" && break
    done
fi
if [ -z "$J" ]; then
    for f in nohup.out server.log liqscope.log logs/*.log /var/log/liqscope.log; do
        if [ -f "$f" ]; then
            J=$(grep -iE "hyperliquid|обрыв" "$f" | tail -15)
            [ -n "$J" ] && echo "(из $f)" && break
        fi
    done
fi
[ -n "$J" ] && echo "$J" || echo "(журнал не найден — напишите, куда пишутся логи сервиса)"
echo
echo "=== конец диагностики ==="
