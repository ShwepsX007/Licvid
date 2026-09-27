#!/usr/bin/env python3
"""
Perf regression guard: ensures /terminal and /ws are fast enough
Thresholds (demo, 60k events):
  - TTFB HTML /terminal < 200ms
  - WS open < 300ms
  - WS init (time to first message) < 500ms
  - WS init payload size < 100KB
  - /api/history raw 7 days < 500ms
  - /api/liq_levels (empty OI) < 100ms

Usage: python tools/perf/check_thresholds.py --url http://127.0.0.1:8001
Exit 0 if all pass, 1 if any fail.
"""
import argparse
import asyncio
import time
import json
import sys

async def check(base_url):
    base_url = base_url.rstrip('/')
    ws_url = base_url.replace('http://','ws://').replace('https://','wss://') + '/ws'
    import aiohttp

    thresholds = {
        'ttfb_html': 200,
        'ws_open': 300,
        'ws_init_total': 500,
        'ws_init_size': 100*1024,
        'history_raw': 500,
        'liq_levels': 100,
    }
    failed = []

    async with aiohttp.ClientSession() as session:
        # 1) TTFB HTML
        url = base_url + '/terminal'
        t0 = time.monotonic()
        async with session.get(url) as resp:
            t_headers = time.monotonic()
            body = await resp.read()
            ttfb = (t_headers - t0)*1000
            print(f"TTFB /terminal: {ttfb:.1f}ms (threshold {thresholds['ttfb_html']}ms) size={len(body)}")
            if ttfb > thresholds['ttfb_html']:
                failed.append(f"TTFB {ttfb:.1f}ms > {thresholds['ttfb_html']}ms")

        # 2) WS open + init
        try:
            import websockets
            has_ws = True
        except ImportError:
            has_ws = False

        if has_ws:
            import websockets
            t0 = time.monotonic()
            async with websockets.connect(ws_url) as ws:
                t_open = time.monotonic()
                open_ms = (t_open - t0)*1000
                raw = await ws.recv()
                t_init = time.monotonic()
                total_ms = (t_init - t0)*1000
                size = len(raw.encode('utf-8'))
                print(f"WS open: {open_ms:.1f}ms (threshold {thresholds['ws_open']}ms)")
                print(f"WS init total: {total_ms:.1f}ms (threshold {thresholds['ws_init_total']}ms) size={size} (threshold {thresholds['ws_init_size']})")
                if open_ms > thresholds['ws_open']:
                    failed.append(f"WS open {open_ms:.1f}ms > {thresholds['ws_open']}ms")
                if total_ms > thresholds['ws_init_total']:
                    failed.append(f"WS init total {total_ms:.1f}ms > {thresholds['ws_init_total']}ms")
                if size > thresholds['ws_init_size']:
                    failed.append(f"WS init size {size} > {thresholds['ws_init_size']}")
        else:
            # aiohttp ws
            t0 = time.monotonic()
            async with session.ws_connect(ws_url) as ws:
                t_open = time.monotonic()
                open_ms = (t_open - t0)*1000
                msg = await ws.receive()
                t_init = time.monotonic()
                total_ms = (t_init - t0)*1000
                size = len(msg.data) if hasattr(msg, 'data') else 0
                print(f"WS open: {open_ms:.1f}ms (threshold {thresholds['ws_open']}ms)")
                print(f"WS init total: {total_ms:.1f}ms (threshold {thresholds['ws_init_total']}ms) size={size}")
                if open_ms > thresholds['ws_open']:
                    failed.append(f"WS open {open_ms:.1f}ms > {thresholds['ws_open']}ms")
                if total_ms > thresholds['ws_init_total']:
                    failed.append(f"WS init total {total_ms:.1f}ms > {thresholds['ws_init_total']}ms")

        # 3) /api/history raw 7 days
        url = base_url + '/api/history?bucket=raw&hours=168&limit=4000'
        t0 = time.monotonic()
        async with session.get(url) as resp:
            body = await resp.read()
            dt = (time.monotonic() - t0)*1000
            print(f"/api/history raw 7d: {dt:.1f}ms (threshold {thresholds['history_raw']}ms) size={len(body)}")
            if dt > thresholds['history_raw']:
                failed.append(f"history raw {dt:.1f}ms > {thresholds['history_raw']}ms")

        # 4) /api/liq_levels
        url = base_url + '/api/liq_levels?symbol=BTC_USDT'
        t0 = time.monotonic()
        async with session.get(url) as resp:
            body = await resp.read()
            dt = (time.monotonic() - t0)*1000
            print(f"/api/liq_levels: {dt:.1f}ms (threshold {thresholds['liq_levels']}ms) size={len(body)}")
            if dt > thresholds['liq_levels']:
                failed.append(f"liq_levels {dt:.1f}ms > {thresholds['liq_levels']}ms")

    if failed:
        print("\nFAILED:")
        for f in failed:
            print(f"  - {f}")
        return 1
    else:
        print("\nAll thresholds passed")
        return 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    args = parser.parse_args()
    exit_code = asyncio.run(check(args.url))
    sys.exit(exit_code)
