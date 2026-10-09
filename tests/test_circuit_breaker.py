"""Unit tests for the provider circuit-breaker state machine."""
from __future__ import annotations

import asyncio
import unittest

from circuit_breaker import (
    CircuitBreaker,
    CircuitGuard,
    CircuitOpenError,
    circuit_name,
    is_transient_failure,
)


class CircuitBreakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_opens_after_consecutive_transient_failures(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=2, recovery_timeout=0.1)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                async with CircuitGuard(breaker):
                    raise RuntimeError("HTTP 503 upstream unavailable")
        state = breaker.snapshot()
        self.assertEqual(state["state"], "OPEN")
        self.assertEqual(state["consecutive_failures"], 2)
        self.assertEqual(state["total_failures"], 2)
        self.assertEqual(state["open_count"], 1)
        self.assertEqual(state["last_error"], "HTTP 503")
        with self.assertRaises(CircuitOpenError) as caught:
            async with CircuitGuard(breaker):
                self.fail("open circuit must not enter the request")
        self.assertGreater(caught.exception.retry_after, 0)
        self.assertEqual(breaker.snapshot()["rejected_calls"], 1)

    async def test_half_open_probe_success_closes_and_resets(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=1, recovery_timeout=0.02)
        with self.assertRaises(RuntimeError):
            async with CircuitGuard(breaker):
                raise RuntimeError("HTTP 503")
        await asyncio.sleep(0.04)
        async with CircuitGuard(breaker):
            pass
        state = breaker.snapshot()
        self.assertEqual(state["state"], "CLOSED")
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(state["total_successes"], 1)
        self.assertEqual(state["open_count"], 0, "a recovered incident resets backoff")
        self.assertEqual(state["retry_in_sec"], 0)

    async def test_only_one_half_open_probe_is_allowed(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=1, recovery_timeout=0.01)
        with self.assertRaises(RuntimeError):
            async with CircuitGuard(breaker):
                raise RuntimeError("HTTP 503")
        await asyncio.sleep(0.02)

        probe = CircuitGuard(breaker)
        await probe.__aenter__()
        self.assertEqual(breaker.snapshot()["state"], "HALF_OPEN")
        with self.assertRaises(CircuitOpenError):
            async with CircuitGuard(breaker):
                pass
        probe.success()
        await probe.__aexit__(None, None, None)
        self.assertEqual(breaker.snapshot()["state"], "CLOSED")

    async def test_half_open_failure_reopens_with_backoff(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=1,
                                 recovery_timeout=0.02, max_recovery_timeout=0.1)
        with self.assertRaises(RuntimeError):
            async with CircuitGuard(breaker):
                raise RuntimeError("HTTP 503")
        await asyncio.sleep(0.03)
        with self.assertRaises(RuntimeError):
            async with CircuitGuard(breaker):
                raise RuntimeError("HTTP 502")
        state = breaker.snapshot()
        self.assertEqual(state["state"], "OPEN")
        self.assertEqual(state["open_count"], 2)
        self.assertGreaterEqual(state["retry_in_sec"], 0.03)

    async def test_invalid_json_is_transient(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=1, recovery_timeout=0.1)
        with self.assertRaises(ValueError):
            async with CircuitGuard(breaker):
                raise __import__("json").JSONDecodeError("bad JSON", "{", 0)
        self.assertEqual(breaker.snapshot()["state"], "OPEN")
        self.assertEqual(breaker.snapshot()["last_error"], "JSONDecodeError")

    async def test_non_transient_http_errors_do_not_trip_breaker(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=1, recovery_timeout=0.1)
        self.assertFalse(is_transient_failure(RuntimeError("HTTP 401 unauthorized")))
        with self.assertRaises(RuntimeError):
            async with CircuitGuard(breaker):
                raise RuntimeError("HTTP 401 unauthorized")
        state = breaker.snapshot()
        self.assertEqual(state["state"], "CLOSED")
        self.assertEqual(state["total_failures"], 0)

    async def test_retry_after_extends_open_period(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=1, recovery_timeout=0.01)
        guard = CircuitGuard(breaker)
        await guard.__aenter__()
        guard.failure("HTTP 429", retry_after=0.12)
        await guard.__aexit__(None, None, None)
        self.assertGreaterEqual(breaker.snapshot()["retry_in_sec"], 0.08)

    async def test_cancelled_probe_is_released_without_counting_failure(self):
        breaker = CircuitBreaker("test:provider", failure_threshold=1, recovery_timeout=0.01)
        with self.assertRaises(RuntimeError):
            async with CircuitGuard(breaker):
                raise RuntimeError("HTTP 503")
        await asyncio.sleep(0.02)
        guard = CircuitGuard(breaker)
        await guard.__aenter__()
        guard.abandon()
        await guard.__aexit__(asyncio.CancelledError, asyncio.CancelledError(), None)
        state = breaker.snapshot()
        self.assertEqual(state["total_failures"], 1)
        self.assertEqual(state["state"], "OPEN")

    def test_names_never_include_path_query_or_key(self):
        name = circuit_name("alchemy-rpc", "https://eth-mainnet.g.alchemy.com/v2/secret-key?token=hidden")
        self.assertEqual(name, "alchemy-rpc:eth-mainnet.g.alchemy.com")
        self.assertNotIn("secret-key", name)
        self.assertNotIn("hidden", name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
