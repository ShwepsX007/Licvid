#!/usr/bin/env python3
"""
Measure WebSocket open time and init payload size
Usage: python tools/perf/measure_ws.py [--url ws://127.0.0.1:8001/ws]
"""
import argparse
import time
import json
import asyncio
import sys
import os

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

async def measure_ws(url):
    if not HAS_WS:
        print("websockets library not installed, trying aiohttp")
        import aiohttp
        t0 = time.monotonic()
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url.replace('ws://','http://').replace('wss://','https://').replace('/ws','/ws')) as ws:
                t_open = time.monotonic()
                print(f"WS open time: {(t_open - t0)*1000:.1f}ms")
                # wait for init
                t_init_start = time.monotonic()
                msg = await ws.receive()
                t_init = time.monotonic()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    size = len(msg.data)
                    print(f"First message type={data.get('type')} size={size} bytes time={(t_init - t_init_start)*1000:.1f}ms total={(t_init - t0)*1000:.1f}ms")
                    print(f"  symbols={len(data.get('symbols',[]))} details={len(data.get('details',[]))} recent_liq={len(data.get('recent_liquidations',[]))} stats keys={list(data.get('stats',{}).keys())[:5]}")
                else:
                    print(f"First msg type {msg.type}")
        return

    # websockets library path
    t0 = time.monotonic()
    async with websockets.connect(url) as ws:
        t_open = time.monotonic()
        open_ms = (t_open - t0)*1000
        print(f"WS open time: {open_ms:.1f}ms")
        t_init_start = time.monotonic()
        raw = await ws.recv()
        t_init = time.monotonic()
        init_ms = (t_init - t_init_start)*1000
        total_ms = (t_init - t0)*1000
        size = len(raw.encode('utf-8') if isinstance(raw, str) else raw)
        try:
            data = json.loads(raw)
            print(f"Init payload: type={data.get('type')} size={size} bytes init_wait={init_ms:.1f}ms total={total_ms:.1f}ms")
            print(f"  symbols={len(data.get('symbols',[]))} details={len(data.get('details',[]))} recent_liq={len(data.get('recent_liquidations',[]))}")
            # stats top_coins
            stats = data.get('stats',{})
            print(f"  stats top_coins={len(stats.get('top_coins',[]))} exchanges={len(stats.get('exchanges',{}))}")
            # flow
            flow = data.get('flow',{})
            print(f"  flow cvd={len(flow.get('cvd',[]))} oi={len(flow.get('oi',[]))} liq={len(flow.get('liq',[]))}")
        except Exception as e:
            print(f"Failed to parse init: {e}, size {size}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='ws://127.0.0.1:8001/ws')
    args = parser.parse_args()
    asyncio.run(measure_ws(args.url))
