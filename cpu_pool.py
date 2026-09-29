"""Shared, bounded pool for picklable CPU-only calculations.

Spawn is deliberate: the web server already owns threads, sockets and locks when
its first calculation runs; forking that state into a worker is unsafe.
"""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from typing import Any, Callable, Optional

_pool: Optional[ProcessPoolExecutor] = None


def _lower_worker_priority() -> None:
    # Two CPU workers can saturate a two-core host even without GIL contention.
    # Give the web process scheduling priority; no effect on portability.
    try:
        os.nice(5)
    except (AttributeError, OSError):
        pass


def pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn"),
                                    initializer=_lower_worker_priority)
    return _pool


async def run(fn: Callable[..., Any], *args: Any) -> Any:
    loop = asyncio.get_running_loop()
    # Only module-level functions and plain snapshots cross the process boundary.
    current = pool()
    try:
        return await loop.run_in_executor(current, fn, *args)
    except BrokenProcessPool:
        # A crashed child poisons the executor; replace it for the next request.
        global _pool
        if _pool is current:
            shutdown()
        raise


def shutdown() -> None:
    global _pool
    old, _pool = _pool, None
    if old is not None:
        old.shutdown(wait=False, cancel_futures=True)
