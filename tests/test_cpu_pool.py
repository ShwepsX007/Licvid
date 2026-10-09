"""CPU math stays in a spawned child; failures never run math on the loop."""
import asyncio
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpu_pool import pool, run, shutdown
from liq_levels import DEFAULT_SETTINGS, _ladder_math, _history_events_worker
from history import HistoryStore


class ProcessMathTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutdown()

    def test_process_is_separate_and_bounded(self):
        async def scenario():
            pid = await run(os.getpid)
            self.assertNotEqual(pid, os.getpid())
            self.assertEqual(pool()._max_workers, 2)
        asyncio.run(scenario())

    def test_worker_error_does_not_poison_the_loop(self):
        async def scenario():
            with self.assertRaises(ValueError):
                await run(int, "not-an-integer")
            self.assertNotEqual(await run(os.getpid), os.getpid())
        asyncio.run(scenario())

    def test_ladder_and_subtraction_round_trip(self):
        rows = [{"entry": 1000.0, "long_usd": 200.0,
                 "short_usd": 100.0, "mmr": 0.0}]
        async def scenario():
            eff, step, ladder, applied = await run(
                _ladder_math, rows, [], 1000.0, DEFAULT_SETTINGS, {})
            self.assertTrue(ladder)
            self.assertGreater(step, 0)
            self.assertEqual(applied["events"], 0)
            self.assertEqual(eff["step_rel"], DEFAULT_SETTINGS["step_rel"])
        asyncio.run(scenario())


class HistoryWorkerTest(unittest.TestCase):
    def test_jsonl_parsing_runs_outside_web_process(self):
        with tempfile.TemporaryDirectory() as d:
            hist = HistoryStore(os.path.join(d, "liq.jsonl"))
            now = time.time()
            hist.add({"id": "one", "timestamp": now, "symbol": "BTC_USDT",
                      "usd": 100.0, "price": 1000.0, "side": "SELL"})
            hist.add({"id": "two", "timestamp": now, "symbol": "ETH_USDT",
                      "usd": 100.0, "price": 1000.0, "side": "BUY"})
            async def scenario():
                return await run(_history_events_worker, hist.base_path,
                                 "BTC_USDT", now - 1, now + 1, 50.0)
            self.assertEqual(asyncio.run(scenario()),
                             [{"price": 1000.0, "usd": 100.0, "side": "SELL"}])
