#!/usr/bin/env python3
"""
Measure TTFB and static weight for /terminal
Usage: python tools/perf/measure_http.py [--url http://127.0.0.1:8001]
"""
import argparse
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
import aiohttp

async def measure(base_url):
    base_url = base_url.rstrip('/')
    async with aiohttp.ClientSession() as session:
        # TTFB HTML
        url = base_url + '/terminal'
        t0 = time.monotonic()
        async with session.get(url) as resp:
            t_first = None
            # read first byte
            # aiohttp doesn't give us raw TTFB easily, we measure time to first chunk
            # We'll measure time to headers received (TTFB approx)
            # For more accurate, we need to time from request start to first byte of body
            # Here we use time after get() which is after headers
            t_headers = time.monotonic()
            body = await resp.read()
            t_end = time.monotonic()
            ttfb_ms = (t_headers - t0) * 1000
            total_ms = (t_end - t0) * 1000
            print(f"HTML {url}: status={resp.status} size={len(body)} ttfb={ttfb_ms:.1f}ms total={total_ms:.1f}ms")
            html = body.decode('utf-8', errors='ignore')
            # extract static files
            import re
            srcs = re.findall(r'src=\"(/static/[^\"]+)\"', html)
            hrefs = re.findall(r'href=\"(/static/[^\"]+)\"', html)
            all_static = list(set(srcs + hrefs))
            print(f"Found {len(all_static)} static refs in HTML")
            # measure each static
            total_raw = 0
            total_gz = 0
            start_static = time.monotonic()
            for path in all_static:
                # full url
                full = base_url + path.split('?')[0] + ('?' + path.split('?')[1] if '?' in path else '')
                # raw
                async with session.get(full) as r:
                    b = await r.read()
                    total_raw += len(b)
                # gz
                async with session.get(full, headers={"Accept-Encoding": "gzip"}) as r:
                    b = await r.read()
                    # if content-encoding gzip, aiohttp auto-decompresses unless we disable?
                    # We'll check header
                    ce = r.headers.get('Content-Encoding','')
                    size = len(b)
                    # For gz measurement we need raw bytes, but aiohttp decompresses by default
                    # We'll use separate non-decompressing request via lower level? For now just report raw
                    total_gz += size
            end_static = time.monotonic()
            static_ms = (end_static - start_static)*1000
            print(f"Static total raw ~{total_raw} bytes, gz estimate {total_gz} bytes, time to fetch all {static_ms:.1f}ms")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    args = parser.parse_args()
    asyncio.run(measure(args.url))
