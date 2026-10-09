"""Budgeted multi-chain whale collection.

EVM and Solana use Alchemy; TRON uses TronGrid; Hyperliquid remains an
independent native feed in ``whale_screener``. Realtime mode streams known CEX
ERC-20/SPL accounts and performs short REST catch-up; economy mode retains those
CEX streams while scanning non-CEX supported transfers at a selectable interval.
The CU ledger is conservative and local to this collector.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import random
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

import aiohttp

from circuit_breaker import CircuitOpenError, protect_url
from whale_screener import (EVM_CHAINS, SOLANA_TOKENS, TOKENS, TRANSFER_TOPIC,
                            TRON_TOKENS, WhaleScreener, alchemy_key)
from alchemy_keys import AlchemyKeyStore, mask_key
from tron_address import tron_to_base58, tron_to_hex

log = logging.getLogger(__name__)
POLL_MODE = os.getenv("LIQSCOPE_WHALE_MODE", "realtime").strip().lower()
if POLL_MODE not in ("realtime", "economy"):
    POLL_MODE = "realtime"
HISTORY_INTERVAL_OPTIONS = (5, 10, 15, 30, 60)  # legacy settings compatibility
POLL_INTERVAL_OPTIONS_SEC = (30, 60, 120, 300)
DEFAULT_POLL_INTERVAL_SEC = 60
SOLANA_HTTP_BASE = os.getenv("LIQSCOPE_SOLANA_HTTP", "https://solana-mainnet.g.alchemy.com/v2/")
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TRON_API_BASE = os.getenv("LIQSCOPE_TRONGRID_URL", "https://api.trongrid.io").rstrip("/")
TRON_EVENT_INTERVAL = 5.0
SOLANA_POLL_INTERVAL_SEC = 30.0  # compatibility alias for realtime mode
SOLANA_POLL_INTERVAL_REALTIME_SEC = 30.0
SOLANA_POLL_INTERVAL_ECONOMY_SEC = 60.0
SOLANA_TOKEN_ACCOUNT_REFRESH_SEC = 6 * 60 * 60
SOLANA_SIGNATURE_LIMIT = 10
SOLANA_MAX_SIGNATURE_REQUESTS_PER_CYCLE = 64
SOLANA_MAX_TRANSACTION_REQUESTS_PER_CYCLE = 16
SOLANA_MAX_RPC_REQUESTS_PER_CYCLE = (SOLANA_MAX_SIGNATURE_REQUESTS_PER_CYCLE +
                                     SOLANA_MAX_TRANSACTION_REQUESTS_PER_CYCLE)
SOLANA_POLL_CONCURRENCY = 8
SOLANA_PROCESSED_SIGNATURE_LIMIT = 5000
MAX_BLOCK_RANGE = 5  # inclusive block count; toBlock <= fromBlock + 4
MAX_BLOCK_RANGE_DELTA = MAX_BLOCK_RANGE - 1
MAX_CATCHUP_LAG_BLOCKS = 30
BLOCK_RANGE_PAUSE_SEC = 0.2
ALCHEMY_MIN_REQUEST_INTERVAL_SEC = 0.4
POLL_JITTER_SEC = 2.0
EVM_POLL_STAGGER_OFFSETS_SEC = {
    "ETH": 0.0, "BNB": 8.0, "POLYGON": 16.0, "ARBITRUM": 24.0, "BASE": 32.0,
}
SOLANA_POLL_STAGGER_SEC = 40.0
RATE_LIMIT_BASE_SEC = 60.0
RATE_LIMIT_MAX_SEC = 300.0
NETWORK_SUCCESS_TTL_SEC = 300.0
# Ключи Alchemy используются ПО ОДНОМУ: работает один, остальные — резерв.
# Переключаемся только когда текущий ключ перестал давать данные (выбит квота,
# 429, отказ авторизации или тишина дольше паузы ниже). Админ добавляет второй
# ключ не для того, чтобы жечь его параллельно, а чтобы было на что отойти.
# Idle silence is not evidence of quota exhaustion; rotate only on explicit provider failure.
KEY_IDLE_FAILOVER_SEC = 0.0
#: как надолго убираем «молчаливый» ключ в кулдаун, чтобы select_key не
#: вернулся на него же на следующем запросе
KEY_IDLE_PARK_SEC = 120.0
#: ключ, который провайдер отверг (401/403) или на котором умер стрим, отдыхает
#: 15 минут: месячная квота Alchemy за минуту не восстанавливается, но и
#: потерянный час на временной паузе пулу не нужен
KEY_AUTH_PARK_SEC = max(60.0, float(os.getenv("LIQSCOPE_KEY_AUTH_PARK_SEC", "900")))
TRON_USD_CONTRACTS = dict(TRON_TOKENS)
SOLANA_BLOCKS_PER_SEC = {"ETH": 1 / 12, "BNB": 1 / 3, "POLYGON": 0.5,
                         "ARBITRUM": 4.0, "BASE": 0.5}
SOLANA_MAX_WALLETS = min(333, max(1, int(os.getenv("LIQSCOPE_SOLANA_MAX_WALLETS", "250"))))
METHOD_CU = {"eth_blockNumber": 10, "eth_getLogs": 60,
             "alchemy_getAssetTransfers": 120,
             "solana_getHealth": 10,
             "solana_getTokenAccountsByOwner": 40,
             "solana_getSignaturesForAddress": 40,
             "solana_getTransaction": 40}

ENDPOINTS = {
    "ETH": "eth-mainnet", "BNB": "bnb-mainnet", "POLYGON": "polygon-mainnet",
    "ARBITRUM": "arb-mainnet", "BASE": "base-mainnet",
}
# Reported separately from Alchemy endpoints: Hyperliquid Core is collected by
# WhaleScreener's public native WebSocket, never via Alchemy JSON-RPC.
NATIVE_NETWORKS = {"HYPERLIQUID": "hyperliquid_ws"}
# The Transfers API documents these four networks, but does not promise BNB;
# token logs are still polled there. Hyperliquid Core is served by the separate
# native WebSocket source, not by an Alchemy endpoint.
NATIVE_INDEXED = {"ETH": "ETH", "POLYGON": "POL", "ARBITRUM": "ETH", "BASE": "ETH"}
# Alchemy's address-filtered mined-transaction stream is currently supported
# on these EVM networks; other networks keep token-log WS + REST native history.
MINED_TRANSACTION_CHAINS = {"ETH", "POLYGON", "ARBITRUM"}


def to_hex_block(val: int | str | None) -> str:
    """Normalize a block number/tag for Alchemy JSON-RPC parameters."""
    if isinstance(val, int):
        return hex(val)
    if isinstance(val, str) and not val.startswith("0x") and val != "latest":
        try:
            return hex(int(val))
        except ValueError:
            return "latest"
    return val or "latest"


def _block_int(value: int | str | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16) if value.lower().startswith("0x") else int(value)
        except ValueError:
            return None
    return None


def bounded_block_range(from_block: int | str | None, latest_block: int | str,
                        requested_to: int | str | None = None) -> tuple[int, int]:
    """Return an inclusive EVM window capped at five blocks."""
    latest = _block_int(latest_block)
    if latest is None or latest < 0:
        raise ValueError("latest_block must be a non-negative block number")
    start = _block_int(from_block)
    if start is None:
        start = max(0, latest - MAX_BLOCK_RANGE_DELTA)
    start = max(0, start)
    end = min(latest, start + MAX_BLOCK_RANGE_DELTA)
    requested_end = _block_int(requested_to)
    if requested_end is not None:
        end = min(end, requested_end)
    return start, end


def iter_block_ranges(from_block: int | str | None, latest_block: int | str):
    """Yield sequential inclusive ranges, each no wider than five blocks."""
    latest = _block_int(latest_block)
    if latest is None or latest < 0:
        raise ValueError("latest_block must be a non-negative block number")
    start = _block_int(from_block)
    if start is None:
        start = max(0, latest - MAX_BLOCK_RANGE_DELTA)
    start = max(0, start)
    while start <= latest:
        _start, end = bounded_block_range(start, latest)
        if end < start:
            return
        yield start, end
        start = end + 1


def _env_poll_interval_sec() -> int:
    # Интервал читается на каждый цикл, поэтому значение из Менеджера
    # настроек (админка → «Системные настройки») подхватывается на лету,
    # без перезапуска; окружение остаётся запасным вариантом.
    try:
        from app_settings import default_manager
        raw = default_manager().get("LIQSCOPE_WHALE_POLL_INTERVAL_SEC",
                                    str(DEFAULT_POLL_INTERVAL_SEC))
        value = int(raw)
    except Exception:  # noqa: BLE001 — при любой проблеме падаем на окружение
        try:
            value = int(os.getenv("LIQSCOPE_WHALE_POLL_INTERVAL_SEC",
                                  str(DEFAULT_POLL_INTERVAL_SEC)))
        except (TypeError, ValueError):
            value = DEFAULT_POLL_INTERVAL_SEC
    return value if value in POLL_INTERVAL_OPTIONS_SEC else DEFAULT_POLL_INTERVAL_SEC


class _AlchemyRequestLimiter:
    """Serialize Alchemy request starts process-wide, including across loops."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next_slot = 0.0

    async def wait_for_slot(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + ALCHEMY_MIN_REQUEST_INTERVAL_SEC
        delay = slot - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


_ALCHEMY_REQUEST_LIMITER = _AlchemyRequestLimiter()


def _base58_encode(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * zeroes + encoded


def _solana_http_url(base: str, key: str) -> str:
    if "{key}" in base:
        return base.replace("{key}", key)
    return base + key if base.endswith("/v2/") else base


class BudgetExhausted(Exception):
    pass


class PollError(Exception):
    pass


class NoKeys(PollError):
    pass


class RateLimited(PollError):
    def __init__(self, message: str, retry_after: float = RATE_LIMIT_BASE_SEC, *,
                 http_status: int | None = 429, rpc_code=None):
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after))
        self.status_code = 429
        self.http_status = http_status
        self.rpc_code = rpc_code


class AuthError(PollError):
    def __init__(self, message: str, status_code: int = 401, retry_after: float = 900.0,
                 rpc_code=None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.retry_after = float(retry_after)
        self.rpc_code = rpc_code


class QuotaExhausted(PollError):
    def __init__(self, message: str, status_code: int | None = None,
                 retry_after: float = 300.0, key_active: bool = False, rpc_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = float(retry_after)
        self.key_active = bool(key_active)
        self.rpc_code = rpc_code


class NetworkError(PollError):
    pass


class WhalePoller:
    def __init__(self, key: str, screener: WhaleScreener, *, interval: int | None = None,
                 monthly_cu: int = 10_000_000, state_file: Path | None = None,
                 endpoints: dict[str, str] | None = None,
                 key_store: AlchemyKeyStore | None = None,
                 trongrid_key_store: AlchemyKeyStore | None = None,
                 mode: str | None = None,
                 history_interval_min: int | None = None,
                 poll_interval_sec: int | None = None,
                 networks=None):
        # Тумблер сетей админки (`screener_networks.NetworkSwitch`). None =
        # не фильтровать: коллектор работает как раньше.
        self.networks = networks
        self._started_at = time.time()
        self.last_data_at = 0.0
        self.last_key_switch_at = 0.0
        self.screener = screener
        self.key_store = key_store
        self.trongrid_key_store = trongrid_key_store
        self._fallback_key = alchemy_key(key) if key and key_store is None else ""
        self.wakeup = asyncio.Event()
        self.last_attempt: dict[str, float] = {}
        self.last_success: dict[str, float] = {}
        self.latest_heads: dict[str, int] = {}
        self.key_errors: dict[str, str] = {}
        self.key_cooldown: dict[str, float] = {}
        self.chain_cooldown: dict[tuple[str, str], float] = {}
        self.chain_cooldown_status: dict[tuple[str, str], str] = {}
        self.rate_limit_attempts: dict[tuple[str, str], int] = {}
        self._solana_address_cursor = 0
        self._solana_token_accounts_updated_at = 0.0
        self._solana_token_accounts_owners: tuple[tuple[str, str], ...] = ()
        self.mode = str(mode or POLL_MODE).strip().lower()
        if self.mode not in ("realtime", "economy"):
            self.mode = "realtime"
        if poll_interval_sec is not None:
            selected_interval = poll_interval_sec
        elif interval is not None:
            selected_interval = interval
        elif history_interval_min is not None:
            selected_interval = int(history_interval_min) * 60
        else:
            selected_interval = _env_poll_interval_sec()
        self.poll_interval_sec = max(1, min(3600, int(selected_interval)))
        # Compatibility aliases retained for existing status consumers/clients.
        self.interval = self.poll_interval_sec
        self.history_interval_min = max(1, math.ceil(self.poll_interval_sec / 60))
        # Deliberate reserve: Alchemy Free is 30M CU for the whole app, not only
        # this collector. A custom cap can be *lower*, not greater than 30M.
        self.monthly_cu = min(30_000_000, max(1, monthly_cu))
        self.state_file = state_file or Path(__file__).resolve().parent / "data/whale_poller.json"
        self.endpoints = endpoints or {
            c: f"https://{host}.g.alchemy.com/v2/" for c, host in ENDPOINTS.items()
        }
        if "HYPERLIQUID" in self.endpoints:
            raise ValueError("Hyperliquid must use its native API, never an Alchemy endpoint")
        self.state = self._load()
        self.errors: dict[str, str] = {}
        self.network_status = {
            chain: {"status": "paused", "last_success": 0.0, "last_event": 0.0,
                    "last_attempt": 0.0, "last_error_at": 0.0, "retry_at": 0.0,
                    "http_status": None, "rpc_code": None, "error": "",
                    "warning_status": "", "error_count": 0, "key_active": True}
            for chain in (*self.endpoints, "SOLANA")
        }
        self.solana_status = {"network": "SOLANA", "provider": "alchemy",
                              "connected": False, "state": "paused", "subscriptions": 0,
                              "submitted": 0, "active_wallets": 0, "mode": "cex_wallets",
                              "processing_state": "idle", "waiting_for_filters": False,
                              "token_accounts": 0, "poll_addresses": 0,
                              "requests_last_cycle": 0, "request_budget": SOLANA_MAX_RPC_REQUESTS_PER_CYCLE,
                              "signature_requests_last_cycle": 0,
                              "transaction_requests_last_cycle": 0,
                              "events": 0, "last_attempt": 0.0,
                              "last_success": 0.0, "error": ""}
        self.tron_status = {"network": "TRON", "provider": "trongrid",
                            "connected": False, "state": "waiting", "events": 0,
                            "wallets": 0, "last_success": 0.0, "last_block": 0, "error": ""}
        self._solana_processed_signatures: set[str] = set()
        self._solana_processed_signature_order: deque[str] = deque()
        self._solana_signature_inflight: set[str] = set()
        self._solana_token_accounts: dict[str, dict[str, set[str]]] = {}
        self._tron_seen: dict[str, float] = {}
        self._tron_last_event_ms = int(time.time() * 1000) - 15_000
        self.evm_streams = {chain: {"connected": False, "state": "waiting",
                                    "subscriptions": 0, "last_success": 0.0,
                                    "reconnects": 0, "error": ""}
                            for chain in self.endpoints}

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("cursors"), dict):
                data.setdefault("history_cursors", {})
                data.setdefault("key_usage", {})
                data.setdefault("network_cu", {})
                data.setdefault("active_key", "")
                data.setdefault("month", "")
                data.setdefault("cu", 0)
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"month": "", "cu": 0, "cursors": {}, "history_cursors": {},
                "key_usage": {}, "network_cu": {}, "active_key": ""}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, self.state_file)

    def _reset_cursor(self, chain: str) -> None:
        """Discard a rejected block range; the next poll seeds from its head."""
        self.state.setdefault("cursors", {})[chain] = "latest"
        self.state.setdefault("history_cursors", {})[chain] = "latest"
        self._save()

    def _keys(self) -> list[tuple[str, str]]:
        return self.key_store.keys() if self.key_store else (
            [("legacy", self._fallback_key)] if self._fallback_key else [])

    # --- тумблер сетей ---------------------------------------------------
    def network_enabled(self, chain: str) -> bool:
        """Собирать ли сеть: решение за админкой, нас здесь нет — собираем всё."""
        switch = getattr(self, "networks", None)
        if switch is None:
            return True
        try:
            return bool(switch.is_enabled(chain))
        except Exception:  # noqa: BLE001 — тумблер не имеет права ронять сбор
            return True

    def enabled_chains(self) -> tuple[str, ...]:
        return tuple(chain for chain in self.endpoints if self.network_enabled(chain))

    # --- пул ключей: один рабочий, остальные в резерве ------------------
    def _month(self) -> str:
        return (self.state.get("month") or
                datetime.now(timezone.utc).strftime("%Y-%m"))

    def key_locally_exhausted(self, key_id: str) -> bool:
        """Ключ выбит своей месячной CU-квотой (до перезапуска ledger'а)."""
        if not key_id:
            return False
        # Ignore legacy local-estimate exhaustion marks. Only provider-confirmed
        # exhaustion may block a key from this version onward.
        return self.state.setdefault("provider_key_exhausted", {}).get(key_id) == self._month()

    def key_ready(self, key_id: str, *, now: float | None = None) -> bool:
        """Можно ли брать этот ключ прямо сейчас (кулдаун + локальная квота)."""
        if not key_id:
            return False
        moment = time.time() if now is None else now
        if self.key_locally_exhausted(key_id):
            return False
        return moment >= float(self.key_cooldown.get(key_id, 0.0) or 0.0)

    def resolve_key(self) -> tuple[int, float]:
        """(индекс рабочего ключа, пауза) — только чтение, ничего не меняет.

        Активный ключ липкий: пока он жив, другие ключи не тратятся. Следующий
        берётся только когда текущий реально нерабочий — локальная CU-квота или
        кулдаун после 429/отказа. Живых нет ни у кого — берём того, кто
        освободится раньше, и честно спим до него.
        """
        keys = self._keys()
        if not keys:
            return -1, 0.0
        ids = [key_id for key_id, _key in keys]
        now = time.time()
        active = str(self.state.get("active_key") or "")
        index = ids.index(active) if active in ids else 0
        if self.key_ready(ids[index], now=now):
            return index, 0.0
        for offset in range(1, len(keys)):
            candidate = (index + offset) % len(keys)
            if self.key_ready(ids[candidate], now=now):
                return candidate, 0.0
        ready = min(((float(self.key_cooldown.get(key_id, 0.0) or 0.0), position)
                     for position, key_id in enumerate(ids)), key=lambda item: item[0])
        return ready[1], max(0.0, ready[0] - now)

    def select_key(self) -> tuple[int, float]:
        """То же, что `resolve_key`, но переход записываем в состояние."""
        keys = self._keys()
        index, wait_sec = self.resolve_key()
        if not keys or index < 0:
            return index, wait_sec
        ids = [key_id for key_id, _key in keys]
        active = str(self.state.get("active_key") or "")
        if ids[index] != active:
            reason = ("все ключи на паузе, ждём ближайшего" if wait_sec > 0 else
                      "первый живой ключ в пуле" if not active else
                      f"ключ {self._hint(active)} не отвечает")
            self.set_active_key(ids[index], reason=reason)
        return index, wait_sec

    def active_key(self) -> tuple[str, str]:
        """(id, ключ) текущего рабочего ключа; ("", "") если ключей нет."""
        keys = self._keys()
        index, _wait = self.resolve_key()
        if index < 0 or index >= len(keys):
            return "", ""
        return keys[index]

    def _hint(self, key_id: str) -> str:
        """Маскированная подсказка ключа для логов и админки (без секрета)."""
        for candidate, secret in self._keys():
            if candidate == key_id:
                return mask_key(secret)
        return key_id or "—"

    def set_active_key(self, key_id: str, *, reason: str = "") -> None:
        if not key_id or self.state.get("active_key") == key_id:
            return
        previous = str(self.state.get("active_key") or "")
        self.state["active_key"] = key_id
        self.state["key_switch"] = {"from": previous, "to": key_id,
                                    "reason": (reason or "")[:200],
                                    "at": int(time.time())}
        try:
            self._save()
        except OSError:
            pass
        self.last_key_switch_at = time.time()
        log.warning("[ключи] рабочий ключ переключён: %s → %s · %s",
                    self._hint(previous) if previous else "—", self._hint(key_id),
                    reason or "без причины")

    def mark_key_exhausted(self, key_id: str, reason: str = "") -> None:
        """Ключ выбит локальной квотой: до конца месяца его не трогаем."""
        if not key_id:
            return
        ledger = self.state.setdefault("provider_key_exhausted", {})
        month = self._month()
        if ledger.get(key_id) != month:
            ledger[key_id] = month
            try:
                self._save()
            except OSError:
                pass
        used = int(self.state.get("key_usage", {}).get(key_id, 0) or 0)
        self.key_errors[key_id] = (reason or
                                    "Квота CU этого ключа исчерпана; "
                                    "ключ жив, сбор продолжается на следующем")
        log.warning("[ключи] %s: %s (%d CU из %d), месяц %s",
                    self._hint(key_id), reason or "исчерпана локальная квота",
                    used, self.key_cu_allowance(), month)

    def park_key(self, key_id: str, seconds: float, reason: str) -> None:
        """Временная пенсия ключа: он не будет выбран, пока не пройдёт пауза."""
        if not key_id:
            return
        until = time.time() + max(1.0, float(seconds or 0.0))
        if until > float(self.key_cooldown.get(key_id, 0.0) or 0.0):
            self.key_cooldown[key_id] = until
        if reason:
            self.key_errors[key_id] = reason[:200]
        self.select_key()  # сразу сдвигаем активный ключ на живого

    def note_key_data(self, key_id: str = "") -> None:
        """Данные пошли — снимаем с ключа отметку «молчит»."""
        self.last_data_at = time.time()
        if key_id:
            self.key_errors.pop(key_id, None)

    def maybe_failover_idle(self) -> str:
        """Compatibility hook; idle silence never rotates Alchemy keys."""
        return ""
    def configure(self, *, mode: str, monthly_cu: int,
                  poll_interval_sec: int | None = None,
                  history_interval_min: int | None = None) -> None:
        mode = str(mode or "").strip().lower()
        if mode not in ("realtime", "economy"):
            raise ValueError("Invalid whale mode")
        if poll_interval_sec is None:
            if history_interval_min is None:
                poll_interval_sec = self.poll_interval_sec
            elif history_interval_min in HISTORY_INTERVAL_OPTIONS:
                # Legacy admin clients submit minutes; map them to supported seconds.
                poll_interval_sec = min(POLL_INTERVAL_OPTIONS_SEC[-1],
                                        max(POLL_INTERVAL_OPTIONS_SEC[0],
                                            int(history_interval_min) * 60))
            else:
                raise ValueError("Invalid poll interval")
        if int(poll_interval_sec) not in POLL_INTERVAL_OPTIONS_SEC:
            raise ValueError("Invalid poll interval")
        if not 1_000 <= int(monthly_cu) <= 20_000_000:
            raise ValueError("Invalid monthly CU limit")
        self.mode = mode
        self.poll_interval_sec = int(poll_interval_sec)
        self.interval = self.poll_interval_sec
        self.history_interval_min = max(1, math.ceil(self.poll_interval_sec / 60))
        self.monthly_cu = int(monthly_cu)
        self.wakeup.set()

    def key_cu_allowance(self) -> int:
        """Месячная квота CU на один ключ: её админ и задаёт в `/admin`.

        Ключи Alchemy живут в разных проектах, и у каждого свой Free-тариф,
        поэтому локальный ledger считаем на ключ, а не на всё приложение.
        Раньше бюджет был общий: как только первый ключ его выбирал,
        `_reserve` поднимал «global» и пул не успевал даже посмотреть на
        второй ключ — данные не шли, хотя у добавленного ключа запас был.
        """
        return max(1, int(self.monthly_cu))

    def cu_ceiling(self) -> int:
        """Потолок пула: квота каждого configured ключа."""
        try:
            keys = self._keys()
        except Exception:  # noqa: BLE001 — хранилище ключей не валит учёт
            keys = []
        return self.key_cu_allowance() * max(1, len(keys))

    def _reserve(self, method: str, key_id: str = "legacy", chain: str = "") -> None:
        """Record estimated CU for telemetry; never block a live provider key."""
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if self.state["month"] != month:
            self.state.update(month=month, cu=0, key_usage={}, network_cu={},
                              key_exhausted={}, provider_key_exhausted={}, active_key="")
            self._save()
        cost = METHOD_CU[method]
        usage = self.state.setdefault("key_usage", {})
        self.state["cu"] = int(self.state.get("cu", 0) or 0) + cost
        usage[key_id] = int(usage.get(key_id, 0) or 0) + cost
        if chain:
            network_usage = self.state.setdefault("network_cu", {})
            network_usage[chain] = int(network_usage.get(chain, 0) or 0) + cost
        self.state["active_key"] = key_id
        self._save()
    async def _wait_for_alchemy_slot(self) -> None:
        await _ALCHEMY_REQUEST_LIMITER.wait_for_slot()

    @staticmethod
    def _stagger_delay(chain: str) -> float:
        offset = (SOLANA_POLL_STAGGER_SEC if chain == "SOLANA" else
                  EVM_POLL_STAGGER_OFFSETS_SEC.get(chain, 0.0))
        return max(0.0, float(offset) + random.uniform(-POLL_JITTER_SEC, POLL_JITTER_SEC))

    async def _wait_for_staggered_start(self, chain: str) -> None:
        delay = self._stagger_delay(chain)
        if delay > 0:
            await asyncio.sleep(delay)

    def _initial_evm_poll_schedule(self, started_at: float) -> dict[str, float]:
        return {chain: started_at + self._stagger_delay(chain) for chain in self.endpoints}

    def _next_evm_poll_due(self, due_at: float, chain: str) -> float:
        loop = asyncio.get_running_loop()
        now = loop.time()
        interval = max(1.0, float(self.interval))
        next_due = due_at + interval
        if now >= next_due:
            next_due = now + interval
        next_due = max(now, next_due + random.uniform(-POLL_JITTER_SEC, POLL_JITTER_SEC))
        row = self._network_row(chain)
        if row.get("status") in ("rate_limited", "auth_error", "quota_exhausted"):
            retry_at = float(row.get("retry_at") or 0.0)
            next_due = max(next_due, now + max(0.0, retry_at - time.time()))
        return next_due

    def _solana_poll_interval(self) -> float:
        return (SOLANA_POLL_INTERVAL_ECONOMY_SEC if self.mode == "economy"
                else SOLANA_POLL_INTERVAL_REALTIME_SEC)

    def _solana_jittered_interval(self) -> float:
        return max(0.0, self._solana_poll_interval() +
                   random.uniform(-POLL_JITTER_SEC, POLL_JITTER_SEC))

    def _solana_retry_delay(self, requested_delay: float = 0.0) -> float:
        return min(RATE_LIMIT_MAX_SEC, max(self._solana_poll_interval(),
                                           max(0.0, float(requested_delay or 0.0))))

    def _network_row(self, chain: str) -> dict:
        return self.network_status.setdefault(chain, {
            "status": "paused", "last_success": 0.0, "last_event": 0.0,
            "last_attempt": 0.0, "last_error_at": 0.0, "retry_at": 0.0,
            "http_status": None, "rpc_code": None, "error": "",
            "warning_status": "", "error_count": 0, "key_active": True})

    def _mark_network_attempt(self, chain: str) -> float:
        now = time.time()
        row = self._network_row(chain)
        row["last_attempt"] = now
        self.last_attempt[chain] = now
        if chain == "SOLANA":
            self.solana_status["last_attempt"] = now
        return now

    def _mark_network_success(self, chain: str, *, key_id: str = "", clear_error: bool = True) -> None:
        now = time.time()
        self.last_data_at = now
        row = self._network_row(chain)
        previous = row.get("status")
        row["status"] = "online"
        row["last_success"] = now
        row["key_active"] = True
        if clear_error:
            # A successful key clears only its own cooldown. Do not erase a
            # parallel key's 429/auth/quota warning when this network is usable.
            for cooldown_key in list(self.chain_cooldown):
                if cooldown_key[0] == chain and key_id and cooldown_key[1] == key_id:
                    self.chain_cooldown.pop(cooldown_key, None)
                    self.chain_cooldown_status.pop(cooldown_key, None)
                    self.rate_limit_attempts.pop(cooldown_key, None)
            active_cooldowns = [
                (self.chain_cooldown[key], self.chain_cooldown_status.get(key, ""))
                for key in self.chain_cooldown
                if key[0] == chain and self.chain_cooldown.get(key, 0.0) > now and
                self.chain_cooldown_status.get(key) in
                ("rate_limited", "auth_error", "quota_exhausted")]
            if active_cooldowns:
                warning = (row.get("warning_status") or
                           previous if previous in ("rate_limited", "auth_error", "quota_exhausted")
                           else active_cooldowns[0][1])
                row.update(status="online", warning_status=warning,
                           retry_at=min(expiry for expiry, _status in active_cooldowns))
                self.errors[chain] = row.get("error", "")
            else:
                row.update(error="", http_status=None, rpc_code=None, retry_at=0.0,
                           warning_status="")
                self.errors.pop(chain, None)
            self.last_success[chain] = now
            if chain == "SOLANA":
                self.solana_status.update(connected=True, state="online", last_success=now,
                                          error=row.get("error", ""),
                                          retry_in_sec=int(math.ceil(max(
                                              0.0, float(row.get("retry_at") or 0) - now))),
                                          key_active=True, http_status=row.get("http_status"),
                                          rpc_code=row.get("rpc_code"))
        elif previous in ("rate_limited", "auth_error", "quota_exhausted", "network_error"):
            row["warning_status"] = previous

    def _mark_network_event(self, chain: str) -> None:
        row = self._network_row(chain)
        row["last_event"] = time.time()
        self.last_data_at = row["last_event"]
        if row.get("status") in ("rate_limited", "auth_error", "quota_exhausted", "network_error"):
            row["warning_status"] = row["status"]
            row["status"] = "online"

    def _mark_network_failure(self, chain: str, status: str, message: str, *,
                              http_status: int | None = None, rpc_code=None,
                              retry_after: float = 0.0, key_active: bool = True) -> None:
        row = self._network_row(chain)
        safe_message = self._redact_api_keys(message)[:400]
        if row.get("status") != status or row.get("error") != safe_message:
            row["error_count"] = int(row.get("error_count") or 0) + 1
        row.update(status=status, error=safe_message, http_status=http_status,
                   rpc_code=rpc_code, last_error_at=time.time(),
                   retry_at=time.time() + max(0.0, retry_after),
                   key_active=bool(key_active), warning_status="")
        self.errors[chain] = safe_message
        if chain == "SOLANA":
            self.solana_status.update(state=status, connected=(status == "rate_limited" and
                time.time() - float(row.get("last_success") or 0) <= NETWORK_SUCCESS_TTL_SEC),
                error=safe_message, retry_in_sec=int(math.ceil(max(0.0, retry_after))),
                http_status=http_status, rpc_code=rpc_code, key_active=bool(key_active))

    def _note_rate_limit(self, chain: str, key_id: str, detail: str, *,
                         http_status: int | None = 429, rpc_code=None) -> RateLimited:
        now = time.time()
        cooldown_key = (chain, key_id)
        retry_at = self.chain_cooldown.get(cooldown_key, 0.0)
        if retry_at <= now:
            attempt = self.rate_limit_attempts.get(cooldown_key, 0)
            delay = min(RATE_LIMIT_MAX_SEC, RATE_LIMIT_BASE_SEC * (attempt + 1))
            retry_at = now + delay
            self.rate_limit_attempts[cooldown_key] = attempt + 1
            self.chain_cooldown[cooldown_key] = retry_at
        else:
            delay = retry_at - now
        self.chain_cooldown_status[cooldown_key] = "rate_limited"
        safe_detail = self._redact_api_keys(detail)
        message = (safe_detail if http_status == 429 and safe_detail.startswith("HTTP 429:")
                   else f"HTTP 429: {safe_detail}" if http_status == 429 else safe_detail)
        self._mark_network_failure(chain, "rate_limited", message,
                                   http_status=http_status, rpc_code=rpc_code,
                                   retry_after=delay, key_active=True)
        return RateLimited(message, delay, http_status=http_status, rpc_code=rpc_code)

    @staticmethod
    def _provider_failure_kind(http_status: int | None, detail: str, rpc_code=None) -> str:
        text = f"{detail} {rpc_code or ''}".lower()
        try:
            code = int(rpc_code)
        except (TypeError, ValueError, OverflowError):
            code = None
        if http_status == 429 or code in (429, -32005) or any(
                term in text for term in ("rate limit", "too many requests", "throttl", "request limit")):
            return "rate_limited"
        # Alchemy отдаёт исчерпанный месячный CU как 429/400 с текстом про
        # «compute unit limit» и «monthly limit». Без этого ответа ключ
        # считался живым и сеть упорно ходила в него вместо следующего.
        if http_status in (402, 4020) or code in (402, 4020) or any(
                term in text for term in ("quota exhausted", "quota exceeded", "monthly quota",
                                          "compute unit quota", "credits exhausted",
                                          "out of credits", "compute unit limit",
                                          "compute units limit", "monthly limit",
                                          "monthly request limit", "over quota",
                                          "billing", "spending limit")):
            return "quota_exhausted"
        if (http_status in (401, 403) or code in (401, 403) or
                any(term in text for term in
                    ("unauthorized", "invalid api key", "invalid key", "authentication error",
                     "not authorized", "forbidden"))):
            return "auth_error"
        return "network_error"

    def _record_provider_failure(self, chain: str, key_id: str, detail: str, *,
                                 http_status: int | None = None, rpc_code=None) -> PollError:
        kind = self._provider_failure_kind(http_status, detail, rpc_code)
        safe_detail = self._redact_api_keys(detail)
        if kind == "rate_limited":
            return self._note_rate_limit(chain, key_id, safe_detail,
                                         http_status=http_status, rpc_code=rpc_code)
        if kind == "auth_error":
            retry_after = 900.0
            cooldown_key = (chain, key_id)
            self.chain_cooldown[cooldown_key] = time.time() + retry_after
            # Отказ ключа — не «одна сеть поперхнулась»: убираем его из работы
            # целиком, иначе пять сетей продолжат долбить его же.
            if self.key_cooldown.get(key_id, 0.0) < time.time() + retry_after:
                self.key_cooldown[key_id] = time.time() + retry_after
                self.select_key()
            self.chain_cooldown_status[cooldown_key] = kind
            self._mark_network_failure(chain, kind, safe_detail,
                                       http_status=http_status, rpc_code=rpc_code,
                                       retry_after=retry_after, key_active=False)
            return AuthError(safe_detail, status_code=http_status or 401,
                             retry_after=retry_after, rpc_code=rpc_code)
        if kind == "quota_exhausted":
            retry_after = 300.0
            cooldown_key = (chain, key_id)
            self.chain_cooldown[cooldown_key] = time.time() + retry_after
            # Месячный CU-лимит провайдера: ключ бессмысленно трогать до
            # конца периода, переводим сбор на следующий ключ пула.
            self.mark_key_exhausted(key_id, "Провайдер сообщил об исчерпанной квоте CU")
            self.chain_cooldown_status[cooldown_key] = kind
            self._mark_network_failure(chain, kind, safe_detail,
                                       http_status=http_status, rpc_code=rpc_code,
                                       retry_after=retry_after, key_active=False)
            return QuotaExhausted(safe_detail, status_code=http_status,
                                  retry_after=retry_after, rpc_code=rpc_code)
        self._mark_network_failure(chain, kind, safe_detail,
                                   http_status=http_status, rpc_code=rpc_code,
                                   key_active=True)
        return NetworkError(safe_detail)

    def key_status(self) -> list[dict]:
        public = (self.key_store.public() if self.key_store else
                  [{"id": "legacy", "hint": mask_key(self._fallback_key),
                    "source": "environment"}] if self._fallback_key else [])
        now = time.time()
        allowance = self.key_cu_allowance()
        usage = self.state.get("key_usage", {})
        keys = self._keys()
        active_index, _wait = self.resolve_key()
        active_id = keys[active_index][0] if 0 <= active_index < len(keys) else ""
        out = []
        for row in public:
            key_id = row["id"]
            cooling = float(self.key_cooldown.get(key_id, 0.0) or 0.0) > now
            exhausted = self.key_locally_exhausted(key_id)
            # «reserve» — ключ целый и ждёт своей очереди: он НЕ расходуется,
            # пока работает активный. Это то, чего ждёт админ, добавляя ключ.
            state = ("exhausted" if exhausted and not cooling else
                     "cooldown" if cooling else
                     "active" if key_id == active_id else "reserve")
            out.append({**row,
                        "reserved_cu": usage.get(key_id, 0),
                        # квота на ключ: админка по ней рисует полоску расхода,
                        # а по «reason» объясняет, почему ключ не работает
                        "cu_allowance": allowance,
                        "state": state,
                        "active": key_id == active_id,
                        "cooldown_in_sec": int(math.ceil(max(
                            0.0, float(self.key_cooldown.get(key_id, 0.0) or 0.0) - now))),
                        "reason": self.key_errors.get(key_id, "")})
        return out

    def status(self) -> dict:
        keys = self._keys()
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elapsed = max(1.0, now.timestamp() - month_start.timestamp())
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0,
                                     second=0, microsecond=0)
        else:
            next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0,
                                     second=0, microsecond=0)
        month_seconds = (next_month - month_start).total_seconds()
        used = int(self.state.get("cu", 0))
        estimate = int(round(used * month_seconds / elapsed)) if used else 0
        sol = self.solana_status.copy()
        tron = self.tron_status.copy()
        network_status = {}
        now_ts = time.time()
        for chain, state in self.network_status.items():
            row = state.copy()
            row["retry_in_sec"] = int(math.ceil(max(0.0, float(row.get("retry_at") or 0) - now_ts)))
            row["cu_used"] = int(self.state.get("network_cu", {}).get(chain, 0))
            row["enabled"] = self.network_enabled(chain)
            if not row["enabled"]:
                # Выключенную сеть показываем выключенной, а не «ожиданием»:
                # админ должен видеть тумблер, а не гадать по пустым карточкам.
                row.update(status="disabled", error="", warning_status="",
                           retry_at=0.0, retry_in_sec=0, key_active=False)
            network_status[chain] = row
        other = {c: "not_configured" for c in
                 ("BITCOIN", "BITCOINCASH", "LITECOIN", "SUI", "DOGECOIN")}
        other["SOLANA"] = sol["state"]
        other["TRON"] = tron["state"]
        network_switch = getattr(self, "networks", None)
        enabled = self.enabled_chains()
        pool_keys = keys
        active_index, active_wait = self.resolve_key()
        active_id = (pool_keys[active_index][0]
                     if 0 <= active_index < len(pool_keys) else "")
        key_pool = {
            "keys_configured": len(pool_keys),
            "active_key_id": active_id,
            "active_hint": self._hint(active_id) if active_id else "",
            "wait_sec": int(math.ceil(max(0.0, active_wait))),
            # резерв = целые ключи, которые сейчас НЕ расходуются
            "reserve": [self._hint(key_id) for key_id, _secret in pool_keys
                        if key_id != active_id and not self.key_locally_exhausted(key_id)],
            "exhausted": [self._hint(key_id) for key_id, _secret in pool_keys
                          if self.key_locally_exhausted(key_id)],
            "idle_sec": int(max(0.0, now_ts - max(self.last_data_at, self._started_at))),
            "idle_failover_sec": int(max(KEY_IDLE_FAILOVER_SEC, 3.0 * self.poll_interval_sec)),
            "switch": dict(self.state.get("key_switch") or {}),
        }
        return {"mode": self.mode, "interval_sec": self.poll_interval_sec,
                "poll_interval_sec": self.poll_interval_sec,
                "history_interval_min": self.history_interval_min,
                "budget_cu": self.monthly_cu, "reserved_cu": used,
                # квота на ключ и потолок всего пула — админка показывает оба:
                # «исчерпан лимит» на одном ключе не означает остановку сбора
                "per_key_cu": self.key_cu_allowance(),
                "budget_total_cu": self.cu_ceiling(),
                "estimated_monthly_cu": estimate,
                "month": self.state.get("month", ""),
                "cursors": self.state.get("cursors", {}).copy(),
                "history_cursors": self.state.get("history_cursors", {}).copy(),
                "errors": self.errors.copy(), "supported": list(self.endpoints),
                # что оставил включённым админ: сети collect'ятся только эти
                "enabled_networks": list(enabled),
                "network_switch": (network_switch.status()
                                    if hasattr(network_switch, "status") else None),
                "key_pool": key_pool,
                "native_supported": [*list(NATIVE_NETWORKS), "SOLANA", "TRON"],
                "keys_configured": len(keys),
                "active_key_id": self.state.get("active_key", ""),
                "last_attempt": self.last_attempt.copy(),
                "last_success": self.last_success.copy(),
                "network_cu": self.state.get("network_cu", {}).copy(),
                "network_status": network_status,
                "latest_heads": self.latest_heads.copy(),
                "evm_streams": {chain: {**row.copy(),
                                        "enabled": self.network_enabled(chain)}
                                for chain, row in self.evm_streams.items()},
                "solana": sol, "tron": tron,
                "phase": ("no_key" if not keys else
                          "error" if self.errors and not self.last_success else
                          "ok" if self.last_success else "waiting"),
                "local_budget_enforced": False,
                "provider_quota_authoritative": True,
                "other_networks": other}

    def _redact_api_keys(self, value: object, extra_secrets=()) -> str:
        message = str(value)
        try:
            keys = self._keys()
        except Exception:  # an unavailable key vault must not break safe logging
            keys = []
        secrets = [secret for _identifier, secret in keys if secret]
        secrets.extend(secret for secret in extra_secrets if secret)
        for secret in secrets:
            message = message.replace(secret, "[redacted]")
        return message

    async def _rpc(self, session: aiohttp.ClientSession, chain: str,
                   method: str, params: list) -> object:
        keys = self._keys()
        self._mark_network_attempt(chain)
        if not keys:
            self._mark_network_failure(chain, "paused", "Alchemy API key is not configured",
                                       key_active=False)
            raise NoKeys("Alchemy API key is not configured")
        index, _wait = self.select_key()
        # Кандидатов максимум два: рабочий ключ и одна ротация на следующий,
        # если текущий умер прямо во время запроса. Прогонять в одном запросе
        # весь пул нельзя — CU начнут тратить все ключи сразу.
        attempts = min(2, len(keys))
        now = time.time()
        rate_limited: list[tuple[float, str]] = []
        auth_errors: list[str] = []
        quota_errors: list[str] = []
        paused = False
        allocation_exhausted = False
        last_failure: PollError | None = None
        for offset in range(attempts):
            key_id, key = keys[(index + offset) % len(keys)]
            cooldown_key = (chain, key_id)
            if self.key_cooldown.get(key_id, 0) > now:
                paused = True
                continue
            if self.chain_cooldown.get(cooldown_key, 0) > now:
                retry_after = self.chain_cooldown[cooldown_key] - now
                kind = self.chain_cooldown_status.get(cooldown_key, "paused")
                if kind == "rate_limited":
                    detail = self._network_row(chain).get("error") or "HTTP 429: provider cooldown"
                    rate_limited.append((retry_after, str(detail)))
                elif kind == "auth_error":
                    auth_errors.append("Provider rejected this key for this network")
                elif kind == "quota_exhausted":
                    quota_errors.append("Provider quota exhausted for this network")
                else:
                    paused = True
                continue
            # Test endpoints may be complete local URLs. Production endpoints
            # are prefixes; do not persist or expose URLs containing keys.
            url = self.endpoints[chain]
            if url.startswith("https://") and url.endswith("/v2/"):
                url += key
            try:
                await self._wait_for_alchemy_slot()
                # Cooldowns can change while this network waits behind another
                # Alchemy request. Re-check before reserving CU or sending.
                now = time.time()
                if self.key_cooldown.get(key_id, 0.0) > now:
                    paused = True
                    continue
                cooldown_until = self.chain_cooldown.get(cooldown_key, 0.0)
                if cooldown_until > now:
                    retry_after = cooldown_until - now
                    if self.chain_cooldown_status.get(cooldown_key) == "rate_limited":
                        detail = self._network_row(chain).get("error") or "HTTP 429: provider cooldown"
                        rate_limited.append((retry_after, str(detail)))
                    else:
                        paused = True
                    continue
                async with protect_url("alchemy-rpc", url) as circuit:
                    try:
                        self._reserve(method, key_id, chain=chain)
                    except BudgetExhausted as exc:
                        circuit.abandon()
                        if str(exc) == "global":
                            self._mark_network_failure(chain, "quota_exhausted",
                                                       "Local monthly CU budget exhausted",
                                                       key_active=True)
                            raise
                        allocation_exhausted = True
                        self.key_errors[key_id] = ("Квота CU этого ключа исчерпана; "
                                           "ключ жив, сбор продолжается на "
                                           "следующем")
                        continue
                    async with session.post(url, json={
                            "jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                            timeout=aiohttp.ClientTimeout(total=25)) as response:
                        if response.status != 200:
                            try:
                                detail = (await response.text(errors="replace"))[:300]
                            except Exception as exc:
                                detail = f"[unable to read response body: {type(exc).__name__}]"
                            detail = self._redact_api_keys(detail, extra_secrets=(key,))
                            message = f"HTTP {response.status}: {detail}"
                            if response.status == 400:
                                self._reset_cursor(chain)
                                log.error("[Alchemy] HTTP 400 network=%s method=%s response=%s",
                                          chain, method, detail)
                            failure = self._record_provider_failure(
                                chain, key_id, message, http_status=response.status)
                            if isinstance(failure, (RateLimited, AuthError, QuotaExhausted)):
                                circuit.neutral()
                            elif response.status in (408, 425) or response.status >= 500:
                                circuit.failure(f"HTTP {response.status}")
                            else:
                                circuit.neutral()
                            if isinstance(failure, RateLimited):
                                rate_limited.append((failure.retry_after, str(failure)))
                                continue
                            if isinstance(failure, AuthError):
                                auth_errors.append(str(failure))
                                continue
                            if isinstance(failure, QuotaExhausted):
                                quota_errors.append(str(failure))
                                continue
                            if offset + 1 < len(keys):
                                # Неизвестный ответ провайдера — не повод стопить
                                # сеть: пробуем следующий ключ, а ошибку помним.
                                last_failure = failure
                                continue
                            raise failure
                        try:
                            data = await response.json()
                        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                            circuit.failure(f"JSON-RPC decode failed ({type(exc).__name__})")
                            raise NetworkError(f"JSON-RPC decode failed: {type(exc).__name__}") from None
                        if not isinstance(data, dict):
                            circuit.failure("Malformed JSON-RPC response")
                            raise NetworkError("Malformed JSON-RPC response")
                        error = data.get("error")
                        if error:
                            if isinstance(error, dict):
                                code = error.get("code")
                                rpc_message = str(error.get("message") or "RPC error")[:300]
                                detail = self._redact_api_keys(
                                    f"JSON-RPC code={code}: {rpc_message}", extra_secrets=(key,))
                            else:
                                code = None
                                detail = self._redact_api_keys(str(error)[:300], extra_secrets=(key,))
                            failure = self._record_provider_failure(
                                chain, key_id, detail, http_status=response.status, rpc_code=code)
                            if isinstance(failure, (RateLimited, AuthError, QuotaExhausted)):
                                circuit.neutral()
                            else:
                                circuit.failure("JSON-RPC upstream error")
                            if isinstance(failure, RateLimited):
                                rate_limited.append((failure.retry_after, str(failure)))
                                continue
                            if isinstance(failure, AuthError):
                                auth_errors.append(str(failure))
                                continue
                            if isinstance(failure, QuotaExhausted):
                                quota_errors.append(str(failure))
                                continue
                            if method in ("eth_getLogs", "alchemy_getAssetTransfers"):
                                self._reset_cursor(chain)
                            # Ключ умер — убираем в резерв, чтобы и остальные сети
                            # следующей попытки шли уже по другому ключу.
                            if isinstance(failure, AuthError):
                                self.park_key(key_id, KEY_AUTH_PARK_SEC,
                                              "Провайдер отверг этот ключ")
                            elif isinstance(failure, QuotaExhausted):
                                self.mark_key_exhausted(
                                    key_id, "Провайдер сообщил об исчерпанной квоте CU")
                            if offset + 1 < attempts:
                                last_failure = failure
                                continue
                            raise failure
                        if "result" not in data:
                            circuit.failure("Malformed JSON-RPC response")
                            raise NetworkError("JSON-RPC response has no result field")
                        self.key_errors.pop(key_id, None)
                        self.chain_cooldown.pop(cooldown_key, None)
                        self.chain_cooldown_status.pop(cooldown_key, None)
                        self.rate_limit_attempts.pop(cooldown_key, None)
                        self.note_key_data(key_id)
                        self._mark_network_success(chain, key_id=key_id)
                        return data["result"]
                except CircuitOpenError as exc:
                self._mark_network_failure(
                    chain, "circuit_open", str(exc), http_status=503,
                    retry_after=exc.retry_after, key_active=True)
                raise NetworkError(str(exc)) from None
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                # Never include aiohttp exception URLs; production paths contain keys.
                circuit.failure(type(exc).__name__)
                failure = NetworkError(type(exc).__name__)
                self._mark_network_failure(chain, "network_error", str(failure), key_active=True)
                raise failure from None
        if rate_limited:
            retry_after, message = min(rate_limited, key=lambda item: item[0])
            row = self._network_row(chain)
            http_status, rpc_code = row.get("http_status"), row.get("rpc_code")
            self._mark_network_failure(chain, "rate_limited", message,
                                       http_status=http_status, rpc_code=rpc_code,
                                       retry_after=retry_after, key_active=True)
            raise RateLimited(message, retry_after, http_status=http_status,
                              rpc_code=rpc_code)
        if auth_errors:
            message = auth_errors[0]
            row = self._network_row(chain)
            retry_after = max(1.0, float(row.get("retry_at", 0) or 0) - time.time())
            raise AuthError(message, status_code=row.get("http_status") or 401,
                            retry_after=retry_after, rpc_code=row.get("rpc_code"))
        if quota_errors:
            message = quota_errors[0]
            row = self._network_row(chain)
            retry_after = max(1.0, float(row.get("retry_at", 0) or 0) - time.time())
            raise QuotaExhausted(message, status_code=row.get("http_status"),
                                 retry_after=retry_after, rpc_code=row.get("rpc_code"))
        if allocation_exhausted:
            message = "Local provider-key CU allocation exhausted"
            self._mark_network_failure(chain, "quota_exhausted", message,
                                       retry_after=300, key_active=True)
            raise QuotaExhausted(message, retry_after=300, key_active=True)
        if last_failure is not None:
            # Ни один ключ пула не ответил нормально — поднимаем последнюю
            # ошибку провайдера, а не «ключей нет».
            raise last_failure
        if paused:
            message = "Network paused while provider keys are cooling down or locally capped"
            self._mark_network_failure(chain, "paused", message, key_active=True)
            raise PollError(message)
        message = "No available provider key"
        self._mark_network_failure(chain, "paused", message, key_active=False)
        raise NoKeys(message)

    async def _logs(self, session, chain: str, start: int, end: int) -> None:
        if end - start > MAX_BLOCK_RANGE_DELTA:
            for range_start, range_end in iter_block_ranges(start, end):
                await self._logs(session, chain, range_start, range_end)
            return
        if start > end:
            return
        addresses = list(TOKENS[chain])
        wallet_rows = self.screener.wallet_addresses(chain)
        wallets = ["0x" + "0" * 24 + a[2:] for a in wallet_rows]
        if not addresses or not wallets:
            return
        seen = set()
        # Indexed Transfer topics: incoming OR outgoing. A transfer between
        # tracked wallets appears twice; deduplicate by (hash, logIndex).
        for from_wallet in (True, False):
            topics = [TRANSFER_TOPIC, wallets] if from_wallet else [TRANSFER_TOPIC, None, wallets]
            logs = await self._rpc(session, chain, "eth_getLogs", [{
                "fromBlock": to_hex_block(start), "toBlock": to_hex_block(end),
                "address": addresses, "topics": topics}])
            if not isinstance(logs, list):
                raise PollError("Malformed log response")
            for event in logs:
                if not isinstance(event, dict):
                    continue
                topic_rows = event.get("topics") or []
                if len(topic_rows) != 3 or not any(
                        isinstance(topic, str) and len(topic) == 66 and
                        ("0x" + topic[-40:].lower()) in wallet_rows
                        for topic in topic_rows[1:]):
                    continue
                key = (event.get("transactionHash"), event.get("logIndex"))
                if key not in seen:
                    seen.add(key)
                    await self.screener.handle_log(chain, event, source="historical")

    async def _native(self, session, chain: str, start: int, end: int) -> None:
        if end - start > MAX_BLOCK_RANGE_DELTA:
            for range_start, range_end in iter_block_ranges(start, end):
                await self._native(session, chain, range_start, range_end)
            return
        if start > end:
            return
        if chain not in NATIVE_INDEXED:
            return
        asset = NATIVE_INDEXED[chain]
        wallets = self.screener.wallet_addresses(chain)
        # Full pagination; the cursor must never advance on partial responses.
        for address in wallets:
            for side in ("fromAddress", "toAddress"):
                page = None
                while True:
                    query = {side: address, "fromBlock": to_hex_block(start),
                             "toBlock": to_hex_block(end),
                             "category": ["external"], "withMetadata": False,
                             "excludeZeroValue": True, "maxCount": "0x3e8"}
                    if page:
                        query["pageKey"] = page
                    result = await self._rpc(session, chain, "alchemy_getAssetTransfers", [query])
                    if not isinstance(result, dict) or not isinstance(result.get("transfers"), list):
                        raise PollError("Malformed transfer response")
                    for tx in result["transfers"]:
                        if not isinstance(tx, dict) or not tx.get("hash"):
                            continue
                        sender = str(tx.get("from") or "").lower()
                        recipient = str(tx.get("to") or "").lower()
                        if not (sender in wallets or recipient in wallets):
                            continue
                        try:
                            amount = Decimal(str(tx["value"]))
                            if not amount.is_finite() or amount <= 0:
                                continue
                            wei = int(amount * 10**18)
                        except (InvalidOperation, KeyError, ValueError, TypeError):
                            continue
                        await self.screener._emit(chain, str(tx["hash"]).lower(), "native",
                                                  asset, wei, 18, sender, recipient,
                                                  source="historical")
                    page = result.get("pageKey")
                    if not page:
                        break

    @staticmethod
    def _timestamp(value) -> int | None:
        if isinstance(value, (int, float)):
            return int(value / 1000 if value > 100_000_000_000 else value)
        if isinstance(value, str):
            try:
                return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
            except ValueError:
                return None
        return None

    async def _wide_token_logs(self, session, chain: str, start: int, end: int) -> None:
        """Fallback token history for BNB where Asset Transfers is not promised."""
        if end - start > MAX_BLOCK_RANGE_DELTA:
            for range_start, range_end in iter_block_ranges(start, end):
                await self._wide_token_logs(session, chain, range_start, range_end)
            return
        if start > end:
            return
        logs = await self._rpc(session, chain, "eth_getLogs", [{
            "fromBlock": to_hex_block(start), "toBlock": to_hex_block(end),
            "address": list(TOKENS[chain]), "topics": [TRANSFER_TOPIC]}])
        if not isinstance(logs, list):
            raise PollError("Malformed token history response")
        wallets = self.screener.wallet_addresses(chain)
        for event in logs:
            if not isinstance(event, dict):
                continue
            topics = event.get("topics") or []
            if len(topics) != 3:
                continue
            participants = []
            for topic in topics[1:]:
                if isinstance(topic, str) and len(topic) == 66:
                    participants.append("0x" + topic[-40:].lower())
            if self.mode == "economy" and any(address in wallets for address in participants):
                continue  # known CEX addresses are covered by their live stream
            await self.screener.handle_log(chain, event, source="historical")

    async def _asset_transfer_history(self, session, chain: str, start: int, end: int) -> None:
        if end - start > MAX_BLOCK_RANGE_DELTA:
            for range_start, range_end in iter_block_ranges(start, end):
                await self._asset_transfer_history(session, chain, range_start, range_end)
            return
        if start > end:
            return
        if chain not in ("ETH", "POLYGON", "ARBITRUM", "BASE"):
            if chain == "BNB":
                await self._wide_token_logs(session, chain, start, end)
            return
        wallet_rows = self.screener.wallet_addresses(chain)
        native_symbol = NATIVE_INDEXED.get(chain)
        queries = [(["external", "erc20"], list(TOKENS[chain]))]
        for categories, contracts in queries:
            page = None
            pages = 0
            while True:
                query = {"fromBlock": to_hex_block(start), "toBlock": to_hex_block(end),
                         "category": categories, "withMetadata": True,
                         "excludeZeroValue": True, "maxCount": "0x3e8"}
                if contracts:
                    query["contractAddresses"] = contracts
                if page:
                    query["pageKey"] = page
                result = await self._rpc(session, chain, "alchemy_getAssetTransfers", [query])
                if not isinstance(result, dict) or not isinstance(result.get("transfers"), list):
                    raise PollError("Malformed transfer history response")
                for tx in result["transfers"]:
                    if not isinstance(tx, dict) or not tx.get("hash"):
                        continue
                    sender = str(tx.get("from") or "").lower()
                    recipient = str(tx.get("to") or "").lower()
                    if self.mode == "economy" and (sender in wallet_rows or recipient in wallet_rows):
                        continue
                    raw_contract = tx.get("rawContract") or {}
                    contract = str(raw_contract.get("address") or tx.get("contractAddress") or "").lower()
                    tx_category = str(tx.get("category") or "").lower()
                    if tx_category not in categories:
                        tx_category = "erc20" if contract in TOKENS[chain] else "external"
                    if tx_category == "external":
                        symbol = str(tx.get("asset") or native_symbol or "")
                        decimals = 18
                        index = "native"
                    else:
                        token = TOKENS[chain].get(contract)
                        if not token:
                            continue
                        symbol, decimals = token
                        index = tx.get("logIndex") or raw_contract.get("logIndex")
                        if index is None:
                            unique = str(tx.get("uniqueId") or "")
                            index = unique.rsplit(":", 1)[-1] if ":" in unique else unique
                        if isinstance(index, int):
                            index = hex(index)
                        index = str(index or "")
                    if not symbol or not index:
                        continue
                    try:
                        amount_value = Decimal(str(tx.get("value")))
                        if not amount_value.is_finite() or amount_value <= 0:
                            continue
                        amount = float(amount_value)
                    except (InvalidOperation, ValueError, TypeError):
                        continue
                    price = self.screener._price(symbol)
                    if price is None:
                        continue
                    timestamp = self._timestamp((tx.get("metadata") or {}).get("blockTimestamp"))
                    await self.screener.record_transfer(
                        chain, str(tx["hash"]).lower(), str(index), symbol, amount,
                        amount * price, sender, recipient, timestamp=timestamp,
                        source="historical")
                page = result.get("pageKey")
                if not page:
                    break
                pages += 1
                if pages >= 20:
                    raise PollError("Transfer history pagination is incomplete")

    def _reset_stale_cursor(self, chain: str, cursor_name: str,
                            latest: int, scan_start: int) -> int:
        lag = max(0, latest - scan_start + 1)
        if lag <= MAX_CATCHUP_LAG_BLOCKS:
            return scan_start
        scan_start = max(0, latest - MAX_BLOCK_RANGE_DELTA)
        self.state.setdefault(cursor_name, {})[chain] = scan_start - 1
        self._save()
        log.warning("[screener/%s] Отставание > 30 блоков. "
                    "Безопасный сброс на latest - 5.", chain.lower())
        return scan_start

    async def poll_transfer_history(self, session, chain: str, head: int) -> None:
        cursors = self.state.setdefault("history_cursors", {})
        cursor = cursors.get(chain)
        if cursor is None or cursor == "latest":
            cursors[chain] = head
            self._save()
            return
        try:
            start = int(cursor) + 1
        except (TypeError, ValueError):
            cursors[chain] = "latest"
            self._save()
            return
        if start <= 0:
            start = max(0, head - MAX_BLOCK_RANGE_DELTA)
        start = self._reset_stale_cursor(chain, "history_cursors", head, start)
        while start <= head:
            range_start, range_end = bounded_block_range(start, head)
            await self._asset_transfer_history(session, chain, range_start, range_end)
            cursors[chain] = range_end
            self._save()
            start = range_end + 1
            if start <= head:
                await asyncio.sleep(BLOCK_RANGE_PAUSE_SEC)

    async def poll_chain(self, session: aiohttp.ClientSession, chain: str) -> None:
        head = await self._rpc(session, chain, "eth_blockNumber", [])
        latest = _block_int(head)
        if latest is None or latest < 0:
            raise PollError("Malformed block number")
        self.latest_heads[chain] = latest
        cursor = self.state["cursors"].get(chain)
        if cursor is None or cursor == "latest":
            self.state["cursors"][chain] = latest
            self.state.setdefault("history_cursors", {}).setdefault(chain, latest)
            self._save()
            return  # no historical replay without a user-specified starting point
        try:
            start = int(cursor) + 1
        except (TypeError, ValueError):
            self._reset_cursor(chain)
            return
        if start <= 0:
            start = max(0, latest - MAX_BLOCK_RANGE_DELTA)
        start = self._reset_stale_cursor(chain, "cursors", latest, start)
        while start <= latest:
            range_start, range_end = bounded_block_range(start, latest)
            await self._native(session, chain, range_start, range_end)
            await self._logs(session, chain, range_start, range_end)
            self.state["cursors"][chain] = range_end
            self._save()
            start = range_end + 1
            if start <= latest:
                await asyncio.sleep(BLOCK_RANGE_PAUSE_SEC)
        # Broad-history and CEX-feed cursors remain independent, and each
        # advances only after its own five-block window has succeeded.
        await self.poll_transfer_history(session, chain, latest)

    async def _handle_mined_native(self, chain: str, envelope: dict) -> bool:
        """Record one filtered, mined top-level native transfer notification."""
        if chain not in MINED_TRANSACTION_CHAINS or envelope.get("removed"):
            return False
        tx = envelope.get("transaction")
        if not isinstance(tx, dict):
            return False
        tx_hash = str(tx.get("hash") or "").lower()
        sender = str(tx.get("from") or "").lower()
        recipient = str(tx.get("to") or "").lower()
        wallets = self.screener.wallet_addresses(chain)
        if (not re.fullmatch(r"0x[0-9a-f]{64}", tx_hash) or
                sender not in wallets and recipient not in wallets or
                not re.fullmatch(r"0x[0-9a-f]{40}", sender) or
                not re.fullmatch(r"0x[0-9a-f]{40}", recipient) or
                tx.get("input", "0x") not in ("0x", "0X", "")):
            return False
        try:
            value = tx.get("value")
            raw_value = int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)
        except (TypeError, ValueError):
            return False
        if raw_value <= 0:
            return False
        await self.screener._emit(chain, tx_hash, "native", NATIVE_INDEXED[chain],
                                  raw_value, 18, sender, recipient, source="realtime")
        return True

    def _key_ready_at(self, chain: str, key_id: str) -> float:
        """Когда ключ снова можно брать в этой сети: максимум двух кулдаунов."""
        return max(float(self.key_cooldown.get(key_id, 0.0) or 0.0),
                   float(self.chain_cooldown.get((chain, key_id), 0.0) or 0.0))

    def _mark_key_paused(self, chain: str, key_id: str) -> None:
        """Ключ наказан: он уходит в резерв, а весь пул — на следующий.

        Раньше это был сдвиг курсора одной сети: стримы жили на разных ключах
        одновременно, и CU тратились со всех сразу.
        """
        if not key_id:
            return
        self.park_key(key_id, KEY_AUTH_PARK_SEC,
                      f"стрим {chain} оборвался на этом ключе")

    def _has_ready_stream_key(self, chain: str, keys: list[tuple[str, str]],
                              except_key_id: str) -> bool:
        """Есть ли в пулу живой ключ, кроме указанного (для быстрой ротации)."""
        now = time.time()
        return any(key_id != except_key_id and self._key_ready_at(chain, key_id) <= now
                   for key_id, _key in keys)

    def _stream_retry_wait(self, chain: str, keys: list[tuple[str, str]],
                           failed_key_id: str, retry_after: float) -> float:
        """Пауза перед переподключением стрима: 1 сек, если следующий ключ жив.

        Кулдаун привязан к ключу, поэтому ждать его, имея в пулу живой ключ, —
        значит оставить сеть без данных на всю паузу провайдера.
        """
        if self._has_ready_stream_key(chain, keys, failed_key_id):
            return 1.0
        return max(1.0, float(retry_after or 0.0))

    def _stream_key(self, chain: str, keys: list[tuple[str, str]]) -> tuple[str, str, int, float]:
        """Ключ для live-стрима сети и пауза до следующей попытки.

        Ключ один на весь пул — тот же, на котором сейчас идёт REST-опрос.
        Раньше стрим жёстко брал ``keys[0]`` и умирал вместе с его лимитом,
        затем (в промежуточной версии) каждая сеть крутила свой курсор и CU
        начинали жрать все ключи сразу. Теперь выбор общий: живой активный
        ключ, при его смерти — следующий, при всеобщем кулдауне — тот, кто
        освободится раньше, и честная пауза до него.

        Возвращает ``(key_id, key, index, wait_sec)``; ``key_id`` пустой, если
        ключей нет вообще.
        """
        if not keys:
            return "", "", 0, 0.0
        index, wait_sec = self.select_key()
        if index < 0 or index >= len(keys):
            return "", "", 0, 0.0
        key_id, key = keys[index]
        # Кулдаун пары «сеть + ключ» тоже учитываем: на этом же ключе стрим
        # этой сети пока не пускают, ждём, пока отпустит.
        pair_wait = max(0.0, float(self.chain_cooldown.get((chain, key_id), 0.0) or 0.0)
                        - time.time())
        return key_id, key, index, max(wait_sec, pair_wait)

    async def _run_evm_ws_chain(self, chain: str) -> None:
        """Subscribe to CEX-indexed token/native transfers using Alchemy WS."""
        state = self.evm_streams[chain]
        retry = 3.0
        first_connect = True
        while True:
            if first_connect:
                await self._wait_for_staggered_start(chain)
                first_connect = False
            if not self.network_enabled(chain):
                # Тумблер в админке: сеть не слушаем, CU не тратим. Проверяем
                # раз в минуту — включение должно жить без рестарта процесса.
                state.update(connected=False, state="disabled", subscriptions=0,
                             error="", retry_in_sec=0)
                await asyncio.sleep(60.0)
                continue
            rate_limit_delay = None
            keys = self._keys()
            wallets = self.screener.wallet_addresses(chain)
            if not keys:
                state.update(connected=False, state="waiting", subscriptions=0,
                             error="Alchemy API key is not configured")
                await asyncio.sleep(5)
                continue
            if not wallets:
                state.update(connected=False, state="waiting", subscriptions=0,
                             error="No CEX addresses for this network")
                await asyncio.sleep(30)
                continue
            host = ENDPOINTS.get(chain)
            if not host:
                state.update(connected=False, state="error", error="Unsupported EVM network")
                await asyncio.sleep(60)
                continue
            key_id, key, key_index, wait_sec = self._stream_key(chain, keys)
            if not key_id:
                await asyncio.sleep(5)
                continue
            if wait_sec > 0:
                # Ни один ключ не готов: спим до ближайшего, но не дольше
                # минуты — пул могли пополнить в админке.
                state.update(connected=False, state="rate_limited", subscriptions=0,
                             error="Все ключи Alchemy на паузе",
                             retry_in_sec=int(math.ceil(wait_sec)))
                await asyncio.sleep(min(wait_sec, 60.0))
                continue
            url = f"wss://{host}.g.alchemy.com/v2/{key}"
            try:
                async with aiohttp.ClientSession() as session:
                    await self._wait_for_alchemy_slot()
                    async with session.ws_connect(url, heartbeat=25, receive_timeout=90) as ws:
                        sub_id = 1
                        subscriptions = 0
                        padded = ["0x" + "0" * 24 + address[2:]
                                  for address in sorted(wallets)]
                        for offset in range(0, len(padded), 64):
                            batch = padded[offset:offset + 64]
                            for incoming in (True, False):
                                topics = ([TRANSFER_TOPIC, batch] if incoming else
                                          [TRANSFER_TOPIC, None, batch])
                                await self._wait_for_alchemy_slot()
                                await ws.send_json({"jsonrpc": "2.0", "id": sub_id,
                                    "method": "eth_subscribe", "params": ["logs", {
                                        "address": list(TOKENS[chain]), "topics": topics}]})
                                sub_id += 1
                                subscriptions += 1
                        if chain in MINED_TRANSACTION_CHAINS:
                            native_addresses = sorted(wallets)[:500]
                            filters = ([{"from": address} for address in native_addresses] +
                                       [{"to": address} for address in native_addresses])
                            await self._wait_for_alchemy_slot()
                            await ws.send_json({"jsonrpc": "2.0", "id": sub_id,
                                "method": "eth_subscribe", "params": ["alchemy_minedTransactions", {
                                    "addresses": filters, "includeRemoved": False,
                                    "hashesOnly": False}]})
                            subscriptions += 1
                        state.update(connected=True, state="online",
                                     subscriptions=subscriptions, last_success=time.time(), error="")
                        self._mark_network_success(chain, key_id=key_id)
                        self.state["active_key"] = key_id
                        retry = 3.0
                        async for message in ws:
                            if message.type != aiohttp.WSMsgType.TEXT:
                                if message.type in (aiohttp.WSMsgType.CLOSED,
                                                    aiohttp.WSMsgType.ERROR):
                                    break
                                continue
                            try:
                                payload = json.loads(message.data)
                            except (TypeError, ValueError):
                                continue
                            if payload.get("error"):
                                error = payload["error"]
                                if isinstance(error, dict):
                                    rpc_code = error.get("code")
                                    rpc_message = str(error.get("message") or "RPC error")[:300]
                                    detail = self._redact_api_keys(
                                        f"JSON-RPC code={rpc_code}: {rpc_message}",
                                        extra_secrets=(key,))
                                else:
                                    rpc_code = None
                                    detail = self._redact_api_keys(str(error)[:300],
                                                                   extra_secrets=(key,))
                                if self._provider_failure_kind(None, detail, rpc_code) in (
                                        "rate_limited", "auth_error", "quota_exhausted"):
                                    failure = self._record_provider_failure(
                                        chain, key_id, detail, http_status=None,
                                        rpc_code=rpc_code)
                                    if isinstance(failure, (RateLimited, QuotaExhausted)):
                                        # Ключ (или вся его квота) кончился: уходим на
                                        # следующий сразу, а не глотаем паузу на мёртвом.
                                        self._mark_key_paused(chain, key_id)
                                        rate_limit_delay = self._stream_retry_wait(
                                            chain, keys, key_id, failure.retry_after)
                                        state.update(connected=False, state="rate_limited",
                                            error=str(failure), retry_in_sec=int(math.ceil(
                                                rate_limit_delay)))
                                        break
                                state["error"] = "Alchemy WebSocket subscription error"
                                continue
                            if payload.get("method") != "eth_subscription":
                                continue
                            result = (payload.get("params") or {}).get("result")
                            if not isinstance(result, dict):
                                continue
                            if isinstance(result.get("transaction"), dict):
                                await self._handle_mined_native(chain, result)
                            else:
                                await self.screener.handle_log(chain, result, source="realtime")
                            state["last_success"] = time.time()
                            self._mark_network_event(chain)
            except asyncio.CancelledError:
                state.update(connected=False, state="stopped")
                raise
            except aiohttp.WSServerHandshakeError as exc:
                state["reconnects"] = int(state.get("reconnects", 0)) + 1
                failure = self._record_provider_failure(
                    chain, key_id, f"HTTP {exc.status}: Alchemy WebSocket handshake rejected",
                    http_status=exc.status)
                self._mark_key_paused(chain, key_id)
                if isinstance(failure, (RateLimited, AuthError, QuotaExhausted)):
                    # Кулдаун лег на этот ключ: если следующий свободен, идём к
                    # нему сразу, а не спим всю паузу на отказавшем.
                    rate_limit_delay = self._stream_retry_wait(
                        chain, keys, key_id, failure.retry_after)
                    state.update(connected=False,
                                 state=("rate_limited" if isinstance(failure, RateLimited)
                                        else "error"),
                                 error=str(failure),
                                 retry_in_sec=int(math.ceil(rate_limit_delay)))
                else:
                    state.update(connected=False, state="error",
                                 error=f"WebSocket handshake HTTP {exc.status}")
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
                state.update(connected=False, state="error", error=type(exc).__name__)
                state["reconnects"] = int(state.get("reconnects", 0)) + 1
                # Транспортная ошибка не привязана к ключу, но переподключение
                # с другим ключом дешевле, чем ждать экспоненциальную паузу на
                # том же соединении: сдвигаемся и пробуем следующий.
                if len(keys) > 1:
                    self._mark_key_paused(chain, key_id)
            except Exception as exc:  # noqa: BLE001 — keep one provider outage isolated
                state.update(connected=False, state="error", error=type(exc).__name__)
                state["reconnects"] = int(state.get("reconnects", 0)) + 1
            if rate_limit_delay is not None:
                await asyncio.sleep(rate_limit_delay)
                retry = 3.0
            else:
                await asyncio.sleep(min(60.0, retry))
                retry = min(60.0, retry * 2)

    def _solana_safe_text(self, value: object) -> str:
        return self._redact_api_keys(str(value).strip())

    def _remember_solana_processed_signature(self, signature: str) -> None:
        if signature in self._solana_processed_signatures:
            return
        if len(self._solana_processed_signature_order) >= SOLANA_PROCESSED_SIGNATURE_LIMIT:
            expired = self._solana_processed_signature_order.popleft()
            self._solana_processed_signatures.discard(expired)
        self._solana_processed_signature_order.append(signature)
        self._solana_processed_signatures.add(signature)

    def _log_solana_exception(self, exc: BaseException) -> None:
        """Log provider failures with a traceback, masking API keys in URLs."""
        original = str(exc) or type(exc).__name__
        message = self._solana_safe_text(original)
        if message == original:
            log.error("[Solana] Ошибка: %s", exc, exc_info=True)
        else:
            safe_exc = RuntimeError(f"{type(exc).__name__}: {message}")
            log.error("[Solana] Ошибка: %s", message,
                      exc_info=(type(safe_exc), safe_exc, exc.__traceback__))

    async def _solana_rpc(self, session, method: str, params: list):
        keys = self._keys()
        self._mark_network_attempt("SOLANA")
        if not keys:
            self._mark_network_failure("SOLANA", "paused",
                                       "Alchemy API key is not configured", key_active=False)
            raise NoKeys("Alchemy API key is not configured")
        index, _wait = self.select_key()
        start = index
        attempts = min(2, len(keys))
        now = time.time()
        rate_limited: list[tuple[float, str]] = []
        auth_errors: list[str] = []
        quota_errors: list[str] = []
        paused = False
        allocation_exhausted = False
        last_failure: PollError | None = None
        for offset in range(attempts):
            key_id, key = keys[(start + offset) % len(keys)]
            cooldown_key = ("SOLANA", key_id)
            if self.key_cooldown.get(key_id, 0) > now:
                paused = True
                continue
            if self.chain_cooldown.get(cooldown_key, 0) > now:
                retry_after = self.chain_cooldown[cooldown_key] - now
                kind = self.chain_cooldown_status.get(cooldown_key, "paused")
                if kind == "rate_limited":
                    detail = self._network_row("SOLANA").get("error") or "HTTP 429: provider cooldown"
                    rate_limited.append((retry_after, str(detail)))
                elif kind == "auth_error":
                    auth_errors.append("Provider rejected this key for Solana")
                elif kind == "quota_exhausted":
                    quota_errors.append("Provider quota exhausted for Solana")
                else:
                    paused = True
                continue
            url = _solana_http_url(SOLANA_HTTP_BASE, key)
            try:
                await self._wait_for_alchemy_slot()
                now = time.time()
                if self.key_cooldown.get(key_id, 0.0) > now:
                    paused = True
                    continue
                cooldown_until = self.chain_cooldown.get(cooldown_key, 0.0)
                if cooldown_until > now:
                    retry_after = cooldown_until - now
                    if self.chain_cooldown_status.get(cooldown_key) == "rate_limited":
                        detail = self._network_row("SOLANA").get("error") or "HTTP 429: provider cooldown"
                        rate_limited.append((retry_after, str(detail)))
                    else:
                        paused = True
                    continue
                try:
                    self._reserve("solana_" + method, key_id, chain="SOLANA")
                except BudgetExhausted as exc:
                    if str(exc) == "global":
                        self._mark_network_failure("SOLANA", "quota_exhausted",
                                                   "Local monthly CU budget exhausted",
                                                   key_active=True)
                        raise
                    allocation_exhausted = True
                    self.key_errors[key_id] = ("Квота CU этого ключа исчерпана; "
                                       "ключ жив, сбор продолжается на "
                                       "следующем")
                    continue
                async with session.post(url, json={"jsonrpc": "2.0", "id": 1,
                                                   "method": method, "params": params},
                                        timeout=aiohttp.ClientTimeout(total=25)) as response:
                    if response.status != 200:
                        try:
                            body = (await response.text(errors="replace"))[:300]
                        except Exception as exc:
                            body = f"[unable to read response body: {type(exc).__name__}]"
                        body = self._redact_api_keys(body, extra_secrets=(key,))
                        detail = (f"HTTP 429: {body}" if response.status == 429 else
                                  f"Solana RPC HTTP {response.status}: {body}")
                        failure = self._record_provider_failure(
                            "SOLANA", key_id, detail, http_status=response.status)
                        if isinstance(failure, RateLimited):
                            rate_limited.append((failure.retry_after, str(failure)))
                            continue
                        if isinstance(failure, AuthError):
                            auth_errors.append(str(failure))
                            continue
                        if isinstance(failure, QuotaExhausted):
                            quota_errors.append(str(failure))
                            continue
                        if isinstance(failure, AuthError):
                            self.park_key(key_id, KEY_AUTH_PARK_SEC,
                                          "Провайдер отверг этот ключ (Solana)")
                        elif isinstance(failure, QuotaExhausted):
                            self.mark_key_exhausted(
                                key_id, "Провайдер сообщил об исчерпанной квоте CU")
                        if offset + 1 < attempts:
                            last_failure = failure
                            continue
                        raise failure
                    try:
                        payload = await response.json()
                    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                        failure = NetworkError(f"Solana RPC JSON decode failed: {type(exc).__name__}")
                        self._mark_network_failure("SOLANA", "network_error", str(failure))
                        raise failure from None
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                failure = NetworkError(f"Solana RPC {type(exc).__name__}")
                self._mark_network_failure("SOLANA", "network_error", str(failure))
                raise failure from None
            if not isinstance(payload, dict):
                failure = NetworkError("Solana RPC returned a malformed response")
                self._mark_network_failure("SOLANA", "network_error", str(failure))
                raise failure
            if payload.get("error"):
                error = payload["error"]
                if isinstance(error, dict):
                    code = error.get("code")
                    detail = f"{method}: JSON-RPC code={code}: {error.get('message', 'RPC error')}"
                else:
                    code = None
                    detail = f"{method}: {error}"
                detail = self._redact_api_keys(detail, extra_secrets=(key,))[:300]
                failure = self._record_provider_failure("SOLANA", key_id, detail,
                                                         http_status=response.status,
                                                         rpc_code=code)
                if isinstance(failure, RateLimited):
                    rate_limited.append((failure.retry_after, str(failure)))
                    continue
                if isinstance(failure, AuthError):
                    auth_errors.append(str(failure))
                    continue
                if isinstance(failure, QuotaExhausted):
                    quota_errors.append(str(failure))
                    continue
                if isinstance(failure, AuthError):
                    self.park_key(key_id, KEY_AUTH_PARK_SEC,
                                  "Провайдер отверг этот ключ (Solana)")
                elif isinstance(failure, QuotaExhausted):
                    self.mark_key_exhausted(key_id,
                                            "Провайдер сообщил об исчерпанной квоте CU")
                if offset + 1 < attempts:
                    last_failure = failure
                    continue
                raise failure
            if "result" not in payload:
                failure = NetworkError("Solana RPC response has no result field")
                self._mark_network_failure("SOLANA", "network_error", str(failure))
                raise failure
            self.key_errors.pop(key_id, None)
            self.chain_cooldown.pop(cooldown_key, None)
            self.chain_cooldown_status.pop(cooldown_key, None)
            self.rate_limit_attempts.pop(cooldown_key, None)
            self.note_key_data(key_id)
            self._mark_network_success("SOLANA", key_id=key_id)
            return payload["result"]
        if rate_limited:
            retry_after, message = min(rate_limited, key=lambda item: item[0])
            row = self._network_row("SOLANA")
            http_status, rpc_code = row.get("http_status"), row.get("rpc_code")
            self._mark_network_failure("SOLANA", "rate_limited", message,
                                       http_status=http_status, rpc_code=rpc_code,
                                       retry_after=retry_after, key_active=True)
            raise RateLimited(message, retry_after, http_status=http_status,
                              rpc_code=rpc_code)
        if auth_errors:
            message = auth_errors[0]
            row = self._network_row("SOLANA")
            retry_after = max(1.0, float(row.get("retry_at", 0) or 0) - time.time())
            raise AuthError(message, status_code=row.get("http_status") or 401,
                            retry_after=retry_after, rpc_code=row.get("rpc_code"))
        if quota_errors:
            message = quota_errors[0]
            row = self._network_row("SOLANA")
            retry_after = max(1.0, float(row.get("retry_at", 0) or 0) - time.time())
            raise QuotaExhausted(message, status_code=row.get("http_status"),
                                 retry_after=retry_after, rpc_code=row.get("rpc_code"))
        if allocation_exhausted:
            message = "Local provider-key CU allocation exhausted"
            self._mark_network_failure("SOLANA", "quota_exhausted", message,
                                       retry_after=300, key_active=True)
            raise QuotaExhausted(message, retry_after=300, key_active=True)
        if last_failure is not None:
            # Ни один ключ пула не ответил нормально — поднимаем последнюю
            # ошибку провайдера, а не «ключей нет».
            raise last_failure
        if paused:
            message = "Solana polling paused while provider keys are cooling down or locally capped"
            self._mark_network_failure("SOLANA", "paused", message, key_active=True)
            raise PollError(message)
        message = "No available provider key for Solana"
        self._mark_network_failure("SOLANA", "paused", message, key_active=False)
        raise NoKeys(message)

    @staticmethod
    def _solana_tx_signature(tx: dict) -> str:
        try:
            signatures = tx.get("transaction", {}).get("signatures", [])
            return str(signatures[0]) if signatures else ""
        except (AttributeError, TypeError, IndexError):
            return ""

    @staticmethod
    def _solana_instructions(tx: dict) -> list[dict]:
        meta = tx.get("meta") or {}
        message = (tx.get("transaction") or {}).get("message") or {}
        rows = [item for item in message.get("instructions", []) if isinstance(item, dict)]
        for group in meta.get("innerInstructions", []) or []:
            rows.extend(item for item in group.get("instructions", []) if isinstance(item, dict))
        return rows

    async def _solana_process_native(self, tx: dict, owner: str) -> int:
        if not isinstance(tx, dict) or (tx.get("meta") or {}).get("err"):
            return 0
        signature = self._solana_tx_signature(tx)
        if not signature:
            return 0
        meta = tx.get("meta") or {}
        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []
        pubkeys = []
        for key in account_keys:
            pubkeys.append(str(key.get("pubkey") or "") if isinstance(key, dict) else str(key))
        index_by_pubkey = {pubkey: index for index, pubkey in enumerate(pubkeys) if pubkey}
        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        try:
            fee = int(meta.get("fee") or 0)
        except (TypeError, ValueError, OverflowError):
            fee = 0

        def balance_delta(index):
            if index is None or index < 0 or index >= len(pre) or index >= len(post):
                return None
            try:
                delta = int(post[index]) - int(pre[index])
            except (TypeError, ValueError, OverflowError):
                return None
            # The fee payer is account zero. Add back its network fee whether
            # its net balance change is positive or negative.
            return delta + fee if index == 0 else delta

        instructions = []
        for index, instruction in enumerate(self._solana_instructions(tx)):
            parsed = instruction.get("parsed") or {}
            info = parsed.get("info") or {}
            if (instruction.get("program") != "system" or
                    parsed.get("type") not in ("transfer", "transferWithSeed")):
                continue
            sender = str(info.get("source") or "")
            recipient = str(info.get("destination") or "")
            if not sender or not recipient:
                continue
            try:
                lamports = int(info.get("lamports") or 0)
            except (TypeError, ValueError, OverflowError):
                lamports = 0
            instructions.append((index, sender, recipient,
                                 index_by_pubkey.get(sender), index_by_pubkey.get(recipient),
                                 lamports))

        if instructions:
            remaining_out = {}
            remaining_in = {}
            count = 0
            for index, sender, recipient, sender_index, recipient_index, hint in instructions:
                sender_delta = balance_delta(sender_index)
                recipient_delta = balance_delta(recipient_index)
                sender_out = max(0, -sender_delta) if sender_delta is not None else 0
                recipient_in = max(0, recipient_delta) if recipient_delta is not None else 0
                sender_out = remaining_out.setdefault(sender_index, sender_out)
                recipient_in = remaining_in.setdefault(recipient_index, recipient_in)
                observed = (min(sender_out, recipient_in) if sender_out and recipient_in
                            else max(sender_out, recipient_in))
                lamports = min(observed, hint) if observed and hint else (observed or hint)
                if sender_index is not None and sender_out:
                    remaining_out[sender_index] = max(0, sender_out - lamports)
                if recipient_index is not None and recipient_in:
                    remaining_in[recipient_index] = max(0, recipient_in - lamports)
                if owner not in (sender, recipient) or lamports <= 0:
                    continue
                amount = lamports / 1_000_000_000
                price = self.screener._price("SOL")
                usd = amount * price if price is not None else 0.0
                if price is None or not math.isfinite(usd) or usd < 100_000:
                    continue
                if await self.screener.record_transfer(
                        "SOLANA", signature, f"native:{index}", "SOL", amount, usd,
                        sender, recipient, timestamp=tx.get("blockTime"), source="realtime"):
                    count += 1
            return count

        # For RPC transactions without parsed System Program instructions,
        # use the owner's pre/post lamport delta and exclude the fee payer cost.
        owner_index = index_by_pubkey.get(owner)
        delta = balance_delta(owner_index)
        if delta is None or not delta:
            return 0
        amount = abs(delta) / 1_000_000_000
        price = self.screener._price("SOL")
        usd = amount * price if price is not None else 0.0
        if price is None or not math.isfinite(usd) or usd < 100_000:
            return 0
        counterparty = next((pubkey for pubkey in pubkeys if pubkey and pubkey != owner), "")
        sender, recipient = ((owner, counterparty) if delta < 0 else (counterparty, owner))
        if await self.screener.record_transfer(
                "SOLANA", signature, f"native-balance:{owner_index}", "SOL", amount,
                usd, sender, recipient, timestamp=tx.get("blockTime"), source="realtime"):
            return 1
        return 0

    async def _solana_process_network_token_tx(
            self, tx: dict, tracked_owners: set[str] | None = None) -> int:
        """Extract large USDT/USDC transfers using parsed token-balance deltas."""
        if not isinstance(tx, dict) or (tx.get("meta") or {}).get("err"):
            return 0
        signature = self._solana_tx_signature(tx)
        if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{64,100}", signature):
            return 0
        meta = tx.get("meta") or {}

        def balances_by_index(rows):
            result = {}
            for row in rows:
                if not isinstance(row, dict) or row.get("mint") not in SOLANA_TOKENS:
                    continue
                try:
                    index = int(row.get("accountIndex"))
                    amount = int((row.get("uiTokenAmount") or {}).get("amount") or 0)
                except (TypeError, ValueError, OverflowError):
                    continue
                mint = str(row["mint"])
                record = result.setdefault((index, mint), {"owner": "", "amount": 0})
                record["amount"] += amount
                if row.get("owner"):
                    record["owner"] = str(row["owner"])
            return result

        pre = balances_by_index(meta.get("preTokenBalances") or [])
        post = balances_by_index(meta.get("postTokenBalances") or [])
        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []
        pubkeys = {}
        for index, key in enumerate(account_keys):
            pubkeys[index] = (str(key.get("pubkey") or "") if isinstance(key, dict)
                              else str(key))
        index_by_pubkey = {pubkey: index for index, pubkey in pubkeys.items() if pubkey}
        account_owner_by_pubkey = {}
        for owner, mint_accounts in self._solana_token_accounts.items():
            for addresses in mint_accounts.values():
                for address in addresses:
                    account_owner_by_pubkey[address] = owner

        def balance_row(index, mint):
            before = pre.get((index, mint), {})
            after = post.get((index, mint), {})
            owner = str(after.get("owner") or before.get("owner") or
                        account_owner_by_pubkey.get(pubkeys.get(index, ""), ""))
            return before, after, owner

        instructions = []
        for index, instruction in enumerate(self._solana_instructions(tx)):
            parsed = instruction.get("parsed") or {}
            instruction_type = str(parsed.get("type") or "").lower()
            if instruction_type not in ("transfer", "transferchecked"):
                continue
            program = str(instruction.get("program") or "").lower()
            program_id = str(instruction.get("programId") or "")
            if ((program and program not in ("spl-token", "spl_token")) or
                    (program_id and program_id != SPL_TOKEN_PROGRAM)):
                continue
            info = parsed.get("info") or {}
            source_account = str(info.get("source") or "")
            destination_account = str(info.get("destination") or "")
            source_index = index_by_pubkey.get(source_account)
            destination_index = index_by_pubkey.get(destination_account)
            mint = str(info.get("mint") or "")
            if mint not in SOLANA_TOKENS:
                candidates = []
                for account_index in (source_index, destination_index):
                    if account_index is None:
                        continue
                    for balances in (post, pre):
                        candidates.extend(candidate_mint for (candidate_index, candidate_mint)
                                          in balances if candidate_index == account_index)
                candidates = list(dict.fromkeys(candidates))
                mint = candidates[0] if len(candidates) == 1 else ""
            if mint not in SOLANA_TOKENS:
                continue
            _source_pre, _source_post, sender_owner = balance_row(source_index, mint)
            _destination_pre, _destination_post, recipient_owner = balance_row(
                destination_index, mint)
            sender = sender_owner or source_account
            recipient = recipient_owner or destination_account
            if not sender or not recipient:
                continue
            instructions.append((index, mint, source_index, destination_index,
                                 sender, recipient, info))

        count = 0
        price_by_mint = {}

        def prepare_event(raw_amount, mint, sender, recipient, index):
            if raw_amount <= 0 or (tracked_owners is not None and
                                   not ({sender, recipient} & tracked_owners)):
                return None
            symbol, decimals = SOLANA_TOKENS[mint]
            amount = float(Decimal(raw_amount) / (Decimal(10) ** decimals))
            if mint not in price_by_mint:
                price_by_mint[mint] = self.screener._price(symbol)
            price = price_by_mint[mint]
            if price is None:
                return None
            usd = amount * price
            if (not math.isfinite(usd) or usd < 100_000 or
                    usd < self.screener.min_usd):
                return None
            return symbol, decimals, amount, usd, index

        if instructions:
            remaining_out = {}
            remaining_in = {}
            for index, mint, source_index, destination_index, sender, recipient, info in instructions:
                if tracked_owners is not None and not ({sender, recipient} & tracked_owners):
                    continue
                source_pre, source_post, _source_owner = balance_row(source_index, mint)
                destination_pre, destination_post, _destination_owner = balance_row(
                    destination_index, mint)
                source_key = (source_index, mint)
                destination_key = (destination_index, mint)
                source_out = remaining_out.setdefault(
                    source_key, max(0, int(source_pre.get("amount", 0)) -
                                    int(source_post.get("amount", 0))))
                destination_in = remaining_in.setdefault(
                    destination_key, max(0, int(destination_post.get("amount", 0)) -
                                         int(destination_pre.get("amount", 0))))
                observed = (min(source_out, destination_in) if source_out and destination_in
                            else max(source_out, destination_in))
                token_amount = info.get("tokenAmount") or {}
                raw_hint = token_amount.get("amount")
                if raw_hint is None:
                    raw_hint = info.get("amount")
                try:
                    raw_hint = int(raw_hint) if raw_hint is not None else 0
                except (TypeError, ValueError, OverflowError):
                    raw_hint = 0
                raw_amount = min(observed, raw_hint) if observed and raw_hint else observed
                if not raw_amount:
                    # Balance arrays are authoritative when present; parsed
                    # instruction amount is a compatibility fallback only.
                    raw_amount = raw_hint
                if raw_amount <= 0:
                    continue
                if source_index is not None and source_out:
                    remaining_out[source_key] = max(0, source_out - raw_amount)
                if destination_index is not None and destination_in:
                    remaining_in[destination_key] = max(0, destination_in - raw_amount)
                event = prepare_event(raw_amount, mint, sender, recipient,
                                          f"spl-network:{index}:{mint}")
                if event:
                    symbol, _decimals, amount, usd, event_index = event
                    if await self.screener.record_transfer(
                            "SOLANA", signature, event_index, symbol, amount, usd,
                            sender, recipient, timestamp=tx.get("blockTime"),
                            source="realtime"):
                        count += 1
            return count

        # Some RPC responses omit parsed instructions. Pair opposite owner-level
        # balance deltas so an incoming/outgoing CEX transfer is still detected.
        owners_by_mint = {}
        for (account_index, mint) in set(pre) | set(post):
            before, after, owner = balance_row(account_index, mint)
            if not owner:
                continue
            try:
                delta = int(after.get("amount", 0)) - int(before.get("amount", 0))
            except (TypeError, ValueError, OverflowError):
                continue
            owner_deltas = owners_by_mint.setdefault(mint, {})
            owner_deltas[owner] = owner_deltas.get(owner, 0) + delta

        for mint, owner_deltas in owners_by_mint.items():
            outgoing = [[owner, -delta] for owner, delta in sorted(owner_deltas.items()) if delta < 0]
            incoming = [[owner, delta] for owner, delta in sorted(owner_deltas.items()) if delta > 0]
            source_index = destination_index = 0
            while source_index < len(outgoing) and destination_index < len(incoming):
                sender, available_out = outgoing[source_index]
                recipient, available_in = incoming[destination_index]
                raw_amount = min(available_out, available_in)
                if sender != recipient:
                    event = prepare_event(
                        raw_amount, mint, sender, recipient,
                        f"spl-delta:{mint}:{source_index}:{destination_index}")
                    if event:
                        symbol, _decimals, amount, usd, event_index = event
                        if await self.screener.record_transfer(
                                "SOLANA", signature, event_index, symbol, amount, usd,
                                sender, recipient, timestamp=tx.get("blockTime"),
                                source="realtime"):
                            count += 1
                outgoing[source_index][1] -= raw_amount
                incoming[destination_index][1] -= raw_amount
                if outgoing[source_index][1] <= 0:
                    source_index += 1
                if incoming[destination_index][1] <= 0:
                    destination_index += 1
        return count

    async def _solana_refresh_token_accounts(
            self, session, owners: list[tuple[str, str]]) -> dict[str, dict[str, set[str]]]:
        """Load each tracked owner's USDT/USDC token accounts through JSON-RPC."""
        semaphore = asyncio.Semaphore(SOLANA_POLL_CONCURRENCY)
        jobs = [(owner, mint) for owner, _label in owners for mint in SOLANA_TOKENS]

        async def fetch(owner: str, mint: str):
            async with semaphore:
                result = await self._solana_rpc(session, "getTokenAccountsByOwner", [
                    owner, {"mint": mint},
                    {"encoding": "jsonParsed", "commitment": "confirmed"}])
            if not isinstance(result, dict) or not isinstance(result.get("value"), list):
                raise PollError("Malformed getTokenAccountsByOwner response")
            accounts = set()
            for row in result["value"]:
                if not isinstance(row, dict):
                    continue
                address = str(row.get("pubkey") or "")
                if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address):
                    continue
                info = (((row.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
                account_mint = str(info.get("mint") or "")
                if account_mint and account_mint != mint:
                    continue
                accounts.add(address)
            return owner, mint, accounts

        results = await asyncio.gather(*(fetch(owner, mint) for owner, mint in jobs),
                                       return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise failures[0]
        accounts: dict[str, dict[str, set[str]]] = {
            owner: {mint: set() for mint in SOLANA_TOKENS} for owner, _label in owners}
        for owner, mint, addresses in results:
            accounts[owner][mint] = addresses
        return accounts

    async def _solana_process_tracked_transaction(
            self, tx: dict, tracked_owners: set[str]) -> int:
        if not isinstance(tx, dict) or (tx.get("meta") or {}).get("err"):
            return 0
        count = 0
        for owner in tracked_owners:
            count += await self._solana_process_native(tx, owner)
        count += await self._solana_process_network_token_tx(tx, tracked_owners)
        return count

    async def _solana_poll_wallets_once(
            self, session, owners: list[tuple[str, str]],
            token_accounts: dict[str, dict[str, set[str]]]) -> None:
        """Poll a bounded rotating batch of owners and transactions per cycle."""
        addresses = {owner for owner, _label in owners}
        active_owners = {owner for owner, _label in owners}
        for owner, mint_accounts in token_accounts.items():
            if owner not in active_owners:
                continue
            for mint in SOLANA_TOKENS:
                addresses.update(mint_accounts.get(mint, set()))
        address_rows = sorted(addresses)
        self.solana_status.update(poll_addresses=len(address_rows),
                                  token_accounts=sum(len(rows) for mints in token_accounts.values()
                                                     for rows in mints.values()))
        if address_rows:
            start = self._solana_address_cursor % len(address_rows)
            signature_addresses = [address_rows[(start + index) % len(address_rows)]
                                   for index in range(min(len(address_rows),
                                       SOLANA_MAX_SIGNATURE_REQUESTS_PER_CYCLE))]
            self._solana_address_cursor = (start + len(signature_addresses)) % len(address_rows)
        else:
            signature_addresses = []
        semaphore = asyncio.Semaphore(SOLANA_POLL_CONCURRENCY)

        async def fetch_signatures(address: str):
            async with semaphore:
                rows = await self._solana_rpc(session, "getSignaturesForAddress", [address, {
                    "commitment": "confirmed", "limit": SOLANA_SIGNATURE_LIMIT}])
            if not isinstance(rows, list):
                raise PollError("Malformed getSignaturesForAddress response")
            return address, rows

        responses = await asyncio.gather(*(fetch_signatures(address)
                                            for address in signature_addresses),
                                         return_exceptions=True)
        signature_rows: dict[str, object] = {}
        first_error = ""
        successful_addresses = 0
        cycle_rate_limit: RateLimited | None = None
        cycle_provider_failure: PollError | None = None
        for response in responses:
            if isinstance(response, BaseException):
                if isinstance(response, BudgetExhausted):
                    raise response
                if isinstance(response, asyncio.CancelledError):
                    raise response
                if isinstance(response, RateLimited):
                    cycle_rate_limit = cycle_rate_limit or response
                elif isinstance(response, PollError):
                    cycle_provider_failure = cycle_provider_failure or response
                if not first_error:
                    first_error = self._solana_safe_text(
                        str(response) or type(response).__name__)[:300]
                continue
            address, rows = response
            successful_addresses += 1
            for row in rows:
                if not isinstance(row, dict) or row.get("err") is not None:
                    continue
                signature = str(row.get("signature") or "")
                if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{64,100}", signature):
                    signature_rows.setdefault(signature, row.get("blockTime"))
        if not successful_addresses:
            if cycle_rate_limit:
                raise cycle_rate_limit
            if cycle_provider_failure:
                raise cycle_provider_failure
            raise PollError(first_error or "No Solana signature poll succeeded")

        def signature_order(item):
            try:
                return float(item[1] or 0)
            except (TypeError, ValueError, OverflowError):
                return 0.0

        tracked_owners = set(active_owners)
        transaction_budget = min(SOLANA_MAX_TRANSACTION_REQUESTS_PER_CYCLE,
                                 max(0, SOLANA_MAX_RPC_REQUESTS_PER_CYCLE -
                                     len(signature_addresses)))
        transaction_requests = 0
        for signature, _block_time in sorted(signature_rows.items(), key=signature_order):
            if transaction_requests >= transaction_budget:
                break
            if (signature in self._solana_processed_signatures or
                    signature in self._solana_signature_inflight):
                continue
            self._solana_signature_inflight.add(signature)
            transaction_requests += 1
            try:
                tx = await self._solana_rpc(session, "getTransaction", [signature, {
                    "encoding": "jsonParsed", "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0}])
                if not isinstance(tx, dict):
                    continue
                count = await self._solana_process_tracked_transaction(tx, tracked_owners)
                self._remember_solana_processed_signature(signature)
                self.solana_status["events"] += count
            except BudgetExhausted:
                raise
            except RateLimited as exc:
                cycle_rate_limit = cycle_rate_limit or exc
                first_error = self._solana_safe_text(str(exc))[:300]
                break
            except Exception as exc:  # isolate one unavailable transaction from other CEX wallets
                if not first_error:
                    first_error = self._solana_safe_text(
                        str(exc) or type(exc).__name__)[:300]
                log.warning("[Solana] Transaction lookup failed: %s",
                            self._solana_safe_text(str(exc) or type(exc).__name__)[:300])
            finally:
                self._solana_signature_inflight.discard(signature)

        self.solana_status.update(
            mode="cex_poll", processing_state="polling", waiting_for_filters=False,
            requests_last_cycle=len(signature_addresses) + transaction_requests,
            signature_requests_last_cycle=len(signature_addresses),
            transaction_requests_last_cycle=transaction_requests,
            request_budget=SOLANA_MAX_RPC_REQUESTS_PER_CYCLE)
        if cycle_rate_limit:
            self._mark_network_failure("SOLANA", "rate_limited", str(cycle_rate_limit),
                                       http_status=cycle_rate_limit.http_status,
                                       rpc_code=cycle_rate_limit.rpc_code,
                                       retry_after=cycle_rate_limit.retry_after,
                                       key_active=True)
            self.solana_status.update(state="rate_limited", error=str(cycle_rate_limit),
                                      retry_in_sec=int(math.ceil(cycle_rate_limit.retry_after)))
        elif cycle_provider_failure and not first_error:
            raise cycle_provider_failure
        else:
            self._mark_network_success("SOLANA", clear_error=True)
            self.solana_status.update(connected=True, state="online", error=first_error,
                                      last_success=time.time(), retry_in_sec=0)

    async def _solana_run_cex_poll(self, session, wallets: dict[str, str]) -> None:
        owners = list(wallets.items())[:SOLANA_MAX_WALLETS]
        owners_key = tuple(owners)
        self.solana_status.update(connected=False, state="connecting", mode="cex_poll",
                                  subscriptions=0, submitted=0, active_wallets=len(owners),
                                  token_accounts=0, poll_addresses=0,
                                  processing_state="checking_health", waiting_for_filters=False,
                                  error="")
        health = await self._solana_rpc(session, "getHealth", [])
        if health != "ok":
            raise PollError("Alchemy Solana getHealth did not return ok")
        loop = asyncio.get_running_loop()
        now = loop.time()
        cache_valid = (self._solana_token_accounts_owners == owners_key and
                       self._solana_token_accounts and
                       now - self._solana_token_accounts_updated_at <
                       SOLANA_TOKEN_ACCOUNT_REFRESH_SEC)
        if cache_valid:
            token_accounts = self._solana_token_accounts
        else:
            token_accounts = await self._solana_refresh_token_accounts(session, owners)
            self._solana_token_accounts = token_accounts
            self._solana_token_accounts_owners = owners_key
            self._solana_token_accounts_updated_at = loop.time()
        self.solana_status.update(connected=True, state="online", mode="cex_poll",
                                  subscriptions=0, submitted=0, active_wallets=len(owners),
                                  token_accounts=sum(len(rows) for mints in token_accounts.values()
                                                     for rows in mints.values()),
                                  token_accounts_updated_at=time.time() - max(
                                      0.0, loop.time() - self._solana_token_accounts_updated_at),
                                  processing_state="polling", waiting_for_filters=False,
                                  last_success=time.time(), error="")
        last_account_refresh = self._solana_token_accounts_updated_at
        while True:
            poll_started = loop.time()
            current = list(self.screener.wallet_addresses("SOLANA").items())[:SOLANA_MAX_WALLETS]
            if current != owners:
                return
            if loop.time() - last_account_refresh >= SOLANA_TOKEN_ACCOUNT_REFRESH_SEC:
                token_accounts = await self._solana_refresh_token_accounts(session, owners)
                self._solana_token_accounts = token_accounts
                self._solana_token_accounts_owners = owners_key
                last_account_refresh = loop.time()
                self._solana_token_accounts_updated_at = last_account_refresh
            await self._solana_poll_wallets_once(session, owners, token_accounts)
            delay = max(0.0, self._solana_jittered_interval() -
                        (loop.time() - poll_started))
            await asyncio.sleep(delay)

    async def _solana_wait_for_filters(self, session) -> None:
        """Confirm RPC health and wait without claiming a CEX feed is active."""
        if self.screener.wallet_addresses("SOLANA"):
            return
        self.solana_status.update(connected=False, state="connecting", mode="waiting_for_filters",
            subscriptions=0, submitted=0, active_wallets=0, token_accounts=0, poll_addresses=0,
            processing_state="checking_health", waiting_for_filters=True, error="")
        health = await self._solana_rpc(session, "getHealth", [])
        if health != "ok":
            raise PollError("Alchemy Solana getHealth did not return ok")
        self.solana_status.update(connected=True, state="online", mode="waiting_for_filters",
            subscriptions=0, submitted=0, active_wallets=0,
            processing_state="waiting_for_filters", waiting_for_filters=True,
            last_success=time.time(), error="")

    async def run_solana(self) -> None:
        """Poll tracked CEX owners over JSON-RPC; wait on health when no filters exist."""
        retry = 3.0
        first_cycle = True
        while True:
            if first_cycle:
                await self._wait_for_staggered_start("SOLANA")
                first_cycle = False
            if not self.network_enabled("SOLANA"):
                self.solana_status.update(connected=False, state="disabled",
                                          subscriptions=0, submitted=0,
                                          processing_state="disabled",
                                          waiting_for_filters=False, error="")
                self._network_row("SOLANA").update(status="disabled", key_active=False)
                await asyncio.sleep(60.0)
                continue
            keys = self._keys()
            wallets = self.screener.wallet_addresses("SOLANA")
            owners = list(wallets.items())[:SOLANA_MAX_WALLETS]
            mode = "cex_poll" if owners else "waiting_for_filters"
            if not keys:
                message = "Alchemy API key is not configured"
                self._mark_network_failure("SOLANA", "paused", message, key_active=False)
                self.solana_status.update(connected=False, state="paused",
                    subscriptions=0, submitted=0, active_wallets=len(owners), mode=mode,
                    processing_state="waiting_for_key", waiting_for_filters=False,
                    error=message)
                await asyncio.sleep(5)
                continue

            returned_for_registry_change = False
            failure_delay = None
            try:
                async with aiohttp.ClientSession() as session:
                    if owners:
                        await self._solana_run_cex_poll(session, dict(owners))
                    else:
                        await self._solana_wait_for_filters(session)
                        if not self.screener.wallet_addresses("SOLANA"):
                            await asyncio.sleep(self._solana_jittered_interval())
                returned_for_registry_change = True
                retry = 3.0
            except asyncio.CancelledError:
                self.solana_status.update(connected=False, state="stopped")
                raise
            except Exception as exc:  # noqa: BLE001 — isolate all Solana provider failures
                detail = self._solana_safe_text(str(exc) or type(exc).__name__)[:300]
                if isinstance(exc, RateLimited):
                    state = "rate_limited"
                    failure_delay = max(1.0, exc.retry_after)
                    http_status = exc.http_status
                    key_active = True
                elif isinstance(exc, (BudgetExhausted, QuotaExhausted)):
                    state = "quota_exhausted"
                    failure_delay = 300.0
                    http_status = getattr(exc, "status_code", None)
                    key_active = True
                elif isinstance(exc, AuthError):
                    state = "auth_error"
                    failure_delay = 60.0
                    http_status = getattr(exc, "status_code", 401)
                    key_active = False
                elif isinstance(exc, NoKeys):
                    state = "paused"
                    http_status = None
                    key_active = False
                else:
                    state = "network_error"
                    http_status = getattr(exc, "status_code", None)
                    key_active = True
                self._mark_network_failure("SOLANA", state, detail,
                    http_status=http_status,
                    rpc_code=getattr(exc, "rpc_code", None),
                    retry_after=failure_delay or getattr(exc, "retry_after", 0.0),
                    key_active=key_active)
                self.solana_status.update(
                    mode=mode, waiting_for_filters=not bool(owners),
                    state=state, error=detail,
                    connected=(state == "rate_limited" and
                               time.time() - float(self.solana_status.get("last_success") or 0)
                               <= NETWORK_SUCCESS_TTL_SEC),
                    retry_in_sec=int(math.ceil(max(0.0, failure_delay or 0.0))))
                if isinstance(exc, RateLimited):
                    log.warning("[Solana] rate_limited retry_in=%ss: %s",
                                int(math.ceil(exc.retry_after)), detail)
                else:
                    self._log_solana_exception(exc)
            if returned_for_registry_change:
                current = list(self.screener.wallet_addresses("SOLANA").items())[:SOLANA_MAX_WALLETS]
                if current != owners:
                    await asyncio.sleep(self._solana_jittered_interval())
                else:
                    await asyncio.sleep(0)
                continue
            if failure_delay is not None:
                await asyncio.sleep(self._solana_retry_delay(failure_delay))
                retry = 3.0
            else:
                await asyncio.sleep(self._solana_retry_delay(retry))
                retry = min(60.0, retry * 2)

    def _trongrid_key(self) -> str:
        if not self.trongrid_key_store:
            return ""
        try:
            keys = self.trongrid_key_store.keys()
            return keys[0][1] if keys else ""
        except Exception:  # noqa: BLE001 — an optional key must not stop TRON
            return ""

    def _trongrid_safe_text(self, value: object) -> str:
        message = str(value).strip()
        api_key = self._trongrid_key()
        return message.replace(api_key, "[redacted]") if api_key else message

    def _log_tron_exception(self, exc: BaseException) -> None:
        original = str(exc) or type(exc).__name__
        message = self._trongrid_safe_text(original)
        if message == original:
            log.error("[TRON] Ошибка TronGrid: %s", exc, exc_info=True)
        else:
            safe_exc = RuntimeError(f"{type(exc).__name__}: {message}")
            log.error("[TRON] Ошибка TronGrid: %s", message,
                      exc_info=(type(safe_exc), safe_exc, exc.__traceback__))

    async def _trongrid_get(self, session, url: str, params: dict | None = None):
        headers = {}
        api_key = self._trongrid_key()
        if api_key:
            headers["TRON-PRO-API-KEY"] = api_key
        try:
            async with session.get(url, params=params, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status != 200:
                    detail = self._trongrid_safe_text(await response.text())[:180]
                    raise PollError(f"TronGrid HTTP {response.status}: {detail}")
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            detail = self._trongrid_safe_text(f"{type(exc).__name__}: {exc}")
            raise PollError(detail[:240]) from None
        if not isinstance(payload, dict):
            raise PollError("Malformed TronGrid response")
        return payload

    async def _trongrid_post(self, session, url: str):
        headers = {}
        api_key = self._trongrid_key()
        if api_key:
            headers["TRON-PRO-API-KEY"] = api_key
        try:
            async with session.post(url, json={}, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status != 200:
                    detail = self._trongrid_safe_text(await response.text())[:180]
                    raise PollError(f"TronGrid HTTP {response.status}: {detail}")
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            detail = self._trongrid_safe_text(f"{type(exc).__name__}: {exc}")
            raise PollError(detail[:240]) from None
        if not isinstance(payload, dict):
            raise PollError("Malformed TronGrid response")
        return payload

    async def _process_tron_block(self, block: dict) -> None:
        raw_block = (block.get("block_header") or {}).get("raw_data") or {}
        self.tron_status["last_block"] = int(raw_block.get("number") or 0)
        timestamp = raw_block.get("timestamp")
        wallets = self.screener.wallet_addresses("TRON")
        for tx in block.get("transactions") or []:
            tx_hash = str(tx.get("txID") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", tx_hash):
                continue
            for index, contract in enumerate((tx.get("raw_data") or {}).get("contract") or []):
                if contract.get("type") != "TransferContract":
                    continue
                value = ((contract.get("parameter") or {}).get("value") or {})
                try:
                    sender = tron_to_base58(value.get("owner_address", ""))
                    recipient = tron_to_base58(value.get("to_address", ""))
                    sun = int(value.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
                if not sun or (sender not in wallets and recipient not in wallets):
                    continue
                amount = sun / 1_000_000
                price = self.screener._price("TRX")
                if price is None:
                    continue
                if await self.screener.record_transfer(
                        "TRON", tx_hash, f"native:{index}", "TRX", amount,
                        amount * price, sender, recipient,
                        timestamp=(float(timestamp or 0) / 1000 if timestamp else None),
                        source="realtime"):
                    self.tron_status["events"] += 1

    async def _poll_tron_contract(self, session, contract: str, symbol: str,
                                  decimals: int, now_ms: int) -> None:
        state = getattr(self, "_tron_pages", {}).get(contract)
        if state is None:
            state = {"min_timestamp": self._tron_last_event_ms,
                     "max_timestamp": now_ms, "fingerprint": ""}
            if not hasattr(self, "_tron_pages"):
                self._tron_pages = {}
            self._tron_pages[contract] = state
        elif not state.get("fingerprint"):
            state.update(min_timestamp=self._tron_last_event_ms,
                         max_timestamp=now_ms, fingerprint="")
        pages = 0
        while pages < 5:
            params = {"event_name": "Transfer", "only_confirmed": "true",
                      "limit": 50, "order_by": "block_timestamp,asc",
                      "min_timestamp": state["min_timestamp"],
                      "max_timestamp": state["max_timestamp"]}
            if state.get("fingerprint"):
                params["fingerprint"] = state["fingerprint"]
            payload = await self._trongrid_get(
                session, f"{TRON_API_BASE}/v1/contracts/{quote(contract, safe='')}/events", params)
            rows = payload.get("data") or []
            if not isinstance(rows, list):
                raise PollError("Malformed TronGrid event page")
            for event in rows:
                if not isinstance(event, dict):
                    continue
                result = event.get("result") or {}
                try:
                    sender = tron_to_base58(result.get("from", ""))
                    recipient = tron_to_base58(result.get("to", ""))
                    raw_value = int(result.get("value") or 0)
                except (TypeError, ValueError):
                    continue
                if not raw_value:
                    continue
                tx_hash = str(event.get("transaction_id") or event.get("transactionId") or "").lower()
                if not re.fullmatch(r"[0-9a-f]{64}", tx_hash):
                    continue
                event_index = str(event.get("event_index") or event.get("eventIndex") or
                                  event.get("block_number", "0"))
                try:
                    timestamp = float(event.get("block_timestamp") or 0) / 1000
                except (TypeError, ValueError):
                    timestamp = None
                amount = raw_value / (10 ** decimals)
                if await self.screener.record_transfer(
                        "TRON", tx_hash, "trc20:" + event_index, symbol, amount,
                        amount, sender, recipient, timestamp=timestamp,
                        source="realtime"):
                    self.tron_status["events"] += 1
            meta = payload.get("meta") or {}
            fingerprint = str(meta.get("fingerprint") or payload.get("fingerprint") or "")
            state["fingerprint"] = fingerprint
            pages += 1
            if not fingerprint:
                state.update(min_timestamp=state["max_timestamp"],
                             max_timestamp=now_ms, fingerprint="")
                break
        if state.get("fingerprint") and pages >= 5:
            self.tron_status["error"] = "TronGrid backlog paginated; continuing next cycle"

    async def run_tron(self) -> None:
        """Poll TronGrid TRX blocks and confirmed TRC-20 transfers every 5s."""
        while True:
            if not self.network_enabled("TRON"):
                # TronGrid бесплатный, но выключенную админом сеть не трогаем:
                # в ленте и в кабинете TRON тогда не показывается.
                self.tron_status.update(connected=False, state="disabled", error="")
                self._network_row("TRON").update(status="disabled", key_active=False)
                await asyncio.sleep(60.0)
                continue
            self.tron_status["wallets"] = len(self.screener.wallet_addresses("TRON"))
            try:
                async with aiohttp.ClientSession() as session:
                    while True:
                        now_ms = int(time.time() * 1000)
                        # The confirmed TRC-20 contract event API is the primary
                        # feed and must not be blocked by the optional TRX block API.
                        for contract, (symbol, decimals) in TRON_USD_CONTRACTS.items():
                            await self._poll_tron_contract(session, contract, symbol,
                                                          decimals, now_ms)
                        block_error = ""
                        try:
                            block = await self._trongrid_post(
                                session, f"{TRON_API_BASE}/wallet/getnowblock")
                            if isinstance(block.get("transactions"), list):
                                await self._process_tron_block(block)
                        except Exception as exc:  # optional native TRX source
                            block_error = self._trongrid_safe_text(
                                str(exc) or type(exc).__name__)[:200]
                            self._log_tron_exception(exc)
                        self._tron_last_event_ms = max(self._tron_last_event_ms,
                                                       now_ms - 250)
                        self.tron_status.update(connected=True, state="online",
                            wallets=len(self.screener.wallet_addresses("TRON")),
                            last_success=time.time(), error=block_error)
                        await asyncio.sleep(TRON_EVENT_INTERVAL)
            except asyncio.CancelledError:
                self.tron_status.update(connected=False, state="stopped")
                raise
            except (PollError, BudgetExhausted, ValueError, OSError,
                    aiohttp.ClientError, asyncio.TimeoutError) as exc:
                detail = self._trongrid_safe_text(str(exc) or type(exc).__name__)[:200]
                self.tron_status.update(connected=False, state="error", error=detail)
                self._log_tron_exception(exc)
                await asyncio.sleep(TRON_EVENT_INTERVAL)
            except Exception as exc:  # noqa: BLE001 — isolate TronGrid failures
                detail = self._trongrid_safe_text(str(exc) or type(exc).__name__)[:200]
                self.tron_status.update(connected=False, state="error", error=detail)
                self._log_tron_exception(exc)
                await asyncio.sleep(TRON_EVENT_INTERVAL)

    async def run_streams(self) -> None:
        """Run EVM/Solana/Tron realtime streams independently of REST catch-up."""
        tasks = [asyncio.create_task(self._run_evm_ws_chain(chain),
                                     name=f"whale-{chain.lower()}-ws")
                 for chain in self.endpoints]
        tasks.extend((asyncio.create_task(self.run_solana(), name="whale-solana-ws"),
                      asyncio.create_task(self.run_tron(), name="whale-trongrid")))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self) -> None:
        """Poll EVM networks sequentially on staggered, jittered schedules."""
        async with aiohttp.ClientSession() as session:
            loop = asyncio.get_running_loop()
            next_due = self._initial_evm_poll_schedule(loop.time())
            while True:
                if self.wakeup.is_set():
                    self.wakeup.clear()
                    next_due = self._initial_evm_poll_schedule(loop.time())

                # One scheduler owns every EVM network. It awaits each poll fully
                # before selecting the next due chain, preventing synchronized bursts.
                chain = min(next_due, key=next_due.get)
                if chain in next_due and not self.network_enabled(chain):
                    # Сеть выключена тумблером: не опрашиваем, но раз в минуту
                    # проверяем снова, чтобы включение сработало без рестарта.
                    next_due[chain] = loop.time() + 60.0
                    try:
                        await asyncio.wait_for(self.wakeup.wait(), timeout=60.0)
                    except asyncio.TimeoutError:
                        pass
                    if self.wakeup.is_set():
                        next_due = self._initial_evm_poll_schedule(loop.time())
                    continue
                due_at = next_due[chain]
                delay = due_at - loop.time()
                if delay > 0:
                    try:
                        await asyncio.wait_for(self.wakeup.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    continue
                if self.wakeup.is_set():
                    continue

                keys = self._keys()
                # Потолок считаем по пулу ключей: квота каждого ключа своя, а
                # не одна на всех, поэтому «исчерпано» только когда кончился
                # запас у всех ключей сразу.
                global_quota_exhausted = int(self.state.get("cu", 0)) >= self.cu_ceiling()
                if global_quota_exhausted:
                    for network in self.endpoints:
                        self._mark_network_failure(
                            network, "quota_exhausted",
                            "Local monthly CU budget exhausted", key_active=True)
                elif not keys:
                    self._mark_network_failure(
                        chain, "paused", "Alchemy API key is not configured", key_active=False)
                else:
                    row = self._network_row(chain)
                    retry_at = float(row.get("retry_at") or 0.0)
                    if (row.get("status") in
                            ("rate_limited", "auth_error", "quota_exhausted") and
                            retry_at > time.time()):
                        next_due[chain] = max(
                            loop.time() + 0.1, loop.time() + retry_at - time.time())
                        continue
                    try:
                        self._mark_network_attempt(chain)
                        await self.poll_chain(session, chain)
                        self.last_success[chain] = time.time()
                        self._mark_network_success(chain)
                    except asyncio.CancelledError:
                        raise
                    except BudgetExhausted as exc:
                        if str(exc) == "global":
                            for network in self.endpoints:
                                self._mark_network_failure(
                                    network, "quota_exhausted",
                                    "Local monthly CU budget exhausted", key_active=True)
                        else:
                            self._mark_network_failure(
                                chain, "quota_exhausted",
                                "Local provider key allocation exhausted", key_active=True)
                    except Exception as exc:  # isolate failures to this chain
                        detail = self._redact_api_keys(str(exc) or type(exc).__name__)[:400]
                        if isinstance(exc, RateLimited):
                            state = "rate_limited"
                            retry_after = exc.retry_after
                            http_status = exc.http_status
                            key_active = True
                        elif isinstance(exc, AuthError):
                            state = "auth_error"
                            retry_after = 900.0
                            http_status = getattr(exc, "status_code", 401)
                            key_active = False
                        elif isinstance(exc, QuotaExhausted):
                            state = "quota_exhausted"
                            retry_after = getattr(exc, "retry_after", 300.0)
                            http_status = getattr(exc, "status_code", None)
                            key_active = getattr(exc, "key_active", False)
                        elif isinstance(exc, NoKeys):
                            state = "paused"
                            retry_after = 0.0
                            http_status = None
                            key_active = False
                        else:
                            state = "network_error"
                            retry_after = 0.0
                            http_status = getattr(exc, "status_code", None)
                            key_active = True
                        self._mark_network_failure(
                            chain, state, detail, http_status=http_status,
                            rpc_code=getattr(exc, "rpc_code", None),
                            retry_after=retry_after, key_active=key_active)

                next_due[chain] = self._next_evm_poll_due(due_at, chain)
