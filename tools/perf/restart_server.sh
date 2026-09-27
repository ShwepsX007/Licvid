#!/bin/bash
cd /home/user/Licvid
pkill -9 -f "uvicorn.*8001" || true
sleep 1
rm -f /tmp/uv.log
LIQSCOPE_DEMO=1 LIQSCOPE_SECRET=test-secret-not-the-published-default LIQSCOPE_PERF_LOG=1 python3 -m uvicorn server:app --host 127.0.0.1 --port 8001 > /tmp/uv.log 2>&1 &
echo $!
sleep 4
cat /tmp/uv.log | tail -n 30
