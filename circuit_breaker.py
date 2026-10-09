"""Small, dependency-light circuit breakers for external HTTP/RPC providers.

The breaker is intentionally separate from retry/key/quota policy. It only stops
repeated transport, timeout, rate-limit and upstream-server failures. Auth and
ordinary client errors do not trip it; provider-specific code keeps owning those.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

try:
    import aiohttp
except Exception:  # pragma: no cover - aiohttp is installed by the application
    aiohttp = None


DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_RECOVERY_TIMEOUT_SEC = 30.0
MAX_RECOVERY_TIMEOUT_SEC = 300.0
_TRANSIENT_HTTP = {408, 425, 429}
_HTTP_RE = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)


class CircuitOpenError(RuntimeError):
    """Raised without touching the provider while its circuit is open."""

    def __init__(self, name: str, retry_after: float):
        self.circuit_name = str(name)
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(
            f"Circuit breaker OPEN for {self.circuit_name}; "
            f"retry in {self.retry_after:.1f}s"
        )


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status", "status_code", "http_status"):
        try:
            value = getattr(exc, attr, None)
            if value is not None:
                return int(value)
        except (TypeError, ValueError, OverflowError):
            pass
    match = _HTTP_RE.search(str(exc)[:500])
    return int(match.group(1)) if match else None


def is_transient_failure(exc: BaseException) -> bool:
    """Only service/transport failures trip the breaker, not application errors."""
    if isinstance(exc, CircuitOpenError):
        return False
    status = _status_code(exc)
    if status is not None:
        return status in _TRANSIENT_HTTP or status >= 500
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, OSError, ConnectionError)):
        return True
    if aiohttp is not None and isinstance(exc, aiohttp.ClientError):
        return True
    # Keep this conservative: malformed application data, bad parameters,
    # authentication failures and local logic errors must not disable a provider.
    return False


def _safe_reason(exc: BaseException | str) -> str:
    if isinstance(exc, BaseException):
        status = _status_code(exc)
        if status is not None:
            return f"HTTP {status}"
        name = type(exc).__name__
        if name in {"TimeoutError", "ServerTimeoutError", "ConnectionTimeoutError"}:
            return name
        return name[:80]
    text = str(exc).strip()
    match = _HTTP_RE.search(text[:500])
    return f"HTTP {match.group(1)}" if match else text[:80] or "upstream failure"


def circuit_name(provider: str, url: str) -> str:
    """Stable provider key; deliberately excludes path, query and credentials."""
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except (TypeError, ValueError):
        host = ""
    return f"{str(provider or 'external').strip().lower()}:{host or 'unknown-host'}"


class CircuitBreaker:
    """Thread-safe CLOSED -> OPEN -> HALF_OPEN state machine."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT_SEC,
        max_recovery_timeout: float = MAX_RECOVERY_TIMEOUT_SEC,
    ):
        self.name = str(name)
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout = max(0.1, float(recovery_timeout))
        self.max_recovery_timeout = max(self.recovery_timeout, float(max_recovery_timeout))
        self._lock = threading.Lock()
        self.state = "CLOSED"
        self.consecutive_failures = 0
        self.total_calls = 0
        self.total_successes = 0
        self.total_failures = 0
        self.rejected_calls = 0
        self.open_count = 0
        self.open_until = 0.0  # monotonic deadline
        self.opened_at = 0.0  # wall-clock timestamp for diagnostics
        self.last_failure_at = 0.0
        self.last_success_at = 0.0
        self.last_error = ""
        self._probe_in_flight = False

    def acquire(self) -> bool:
        """Return whether this is the single HALF_OPEN probe; raises when blocked."""
        now = time.monotonic()
        with self._lock:
            if self.state == "OPEN":
                if now < self.open_until:
                    self.rejected_calls += 1
                    raise CircuitOpenError(self.name, self.open_until - now)
                self.state = "HALF_OPEN"
                self._probe_in_flight = False
            if self.state == "HALF_OPEN":
                if self._probe_in_flight:
                    self.rejected_calls += 1
                    raise CircuitOpenError(self.name, 1.0)
                self._probe_in_flight = True
                self.total_calls += 1
                return True
            self.total_calls += 1
            return False

    def success(self, probe: bool = False) -> None:
        with self._lock:
            self.total_successes += 1
            self.consecutive_failures = 0
            self.last_success_at = time.time()
            self.last_error = ""
            self.state = "CLOSED"
            self.open_until = 0.0
            self.opened_at = 0.0
            self._probe_in_flight = False

    def neutral(self, probe: bool = False) -> None:
        """A non-transient response means the service replied; don't trip it."""
        with self._lock:
            if probe or self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.consecutive_failures = 0
                self.open_until = 0.0
                self.opened_at = 0.0
                self._probe_in_flight = False

    def abandon(self, probe: bool = False) -> None:
        """Release a half-open probe when a request was cancelled/not sent."""
        with self._lock:
            if probe and self.state == "HALF_OPEN":
                self._probe_in_flight = False
                # Permit another probe on the next call; this call gave no
                # evidence either for or against provider health.
                self.state = "OPEN"
                self.open_until = time.monotonic()
                self.opened_at = time.time()

    def failure(self, reason: str | BaseException, *, retry_after: float = 0.0,
                probe: bool = False) -> None:
        now_mono, now_wall = time.monotonic(), time.time()
        with self._lock:
            self.total_failures += 1
            self.consecutive_failures += 1
            self.last_failure_at = now_wall
            self.last_error = _safe_reason(reason)
            self._probe_in_flight = False
            should_open = probe or self.state == "HALF_OPEN" or (
                self.consecutive_failures >= self.failure_threshold)
            if not should_open:
                return
            self.state = "OPEN"
            self.open_count += 1
            backoff = min(
                self.max_recovery_timeout,
                self.recovery_timeout * (2 ** min(max(0, self.open_count - 1), 20)),
            )
            cooldown = max(backoff, max(0.0, float(retry_after or 0.0)))
            self.open_until = now_mono + cooldown
            self.opened_at = now_wall

    def snapshot(self) -> dict:
        now_mono = time.monotonic()
        with self._lock:
            return {
                "name": self.name,
                "state": self.state,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_sec": self.recovery_timeout,
                "consecutive_failures": self.consecutive_failures,
                "total_calls": self.total_calls,
                "total_successes": self.total_successes,
                "total_failures": self.total_failures,
                "rejected_calls": self.rejected_calls,
                "open_count": self.open_count,
                "retry_in_sec": round(max(0.0, self.open_until - now_mono), 1)
                if self.state == "OPEN" else 0.0,
                "opened_at": self.opened_at,
                "last_failure_at": self.last_failure_at,
                "last_success_at": self.last_success_at,
                "last_error": self.last_error,
            }


class CircuitGuard:
    """Async context manager with optional explicit outcomes for HTTP status codes."""

    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker
        self._probe = False
        self._recorded = False

    async def __aenter__(self):
        self._probe = self.breaker.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._recorded:
            return False
        if exc is None:
            self.breaker.success(self._probe)
        elif isinstance(exc, asyncio.CancelledError):
            self.breaker.abandon(self._probe)
        elif is_transient_failure(exc):
            self.breaker.failure(exc, probe=self._probe)
        else:
            self.breaker.neutral(self._probe)
        return False

    def success(self) -> None:
        if not self._recorded:
            self.breaker.success(self._probe)
            self._recorded = True

    def neutral(self) -> None:
        if not self._recorded:
            self.breaker.neutral(self._probe)
            self._recorded = True

    def failure(self, reason: str | BaseException, *, retry_after: float = 0.0) -> None:
        if not self._recorded:
            self.breaker.failure(reason, retry_after=retry_after, probe=self._probe)
            self._recorded = True

    def abandon(self) -> None:
        if not self._recorded:
            self.breaker.abandon(self._probe)
            self._recorded = True


class CircuitBreakerRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str, *, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
            recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT_SEC) -> CircuitBreaker:
        key = str(name)
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(
                    key, failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                )
                self._breakers[key] = breaker
            return breaker

    def snapshot(self) -> list[dict]:
        with self._lock:
            breakers = list(self._breakers.values())
        return sorted((breaker.snapshot() for breaker in breakers),
                      key=lambda item: item["name"])

    def reset(self) -> None:
        """Test helper; never used by runtime code."""
        with self._lock:
            self._breakers.clear()


REGISTRY = CircuitBreakerRegistry()


def get_breaker(name: str, *, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
                recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT_SEC) -> CircuitBreaker:
    return REGISTRY.get(name, failure_threshold=failure_threshold,
                        recovery_timeout=recovery_timeout)


def snapshot() -> list[dict]:
    return REGISTRY.snapshot()


@asynccontextmanager
async def protect(name: str, *, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
                  recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT_SEC):
    guard = CircuitGuard(get_breaker(
        name, failure_threshold=failure_threshold, recovery_timeout=recovery_timeout))
    async with guard:
        yield guard


@asynccontextmanager
async def protect_url(provider: str, url: str, *,
                      failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
                      recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT_SEC):
    async with protect(circuit_name(provider, url),
                       failure_threshold=failure_threshold,
                       recovery_timeout=recovery_timeout) as guard:
        yield guard
