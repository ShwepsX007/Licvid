"""Fetch, validate, merge and persist public CEX wallet labels.

DeFiLlama's REST ``/protocols`` feed is filtered to CEX records and enriches
addresses from explicit chain-scoped fields or ``/protocol/{slug}``. Etherscan
labels are an optional, isolated secondary source.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import aiohttp

from tron_address import tron_to_base58

log = logging.getLogger(__name__)

DEFILLAMA_PROTOCOLS_API = os.getenv(
    "LIQSCOPE_DEFILLAMA_PROTOCOLS_API", "https://api.llama.fi/protocols")
DEFILLAMA_PROTOCOL_API_BASE = os.getenv(
    "LIQSCOPE_DEFILLAMA_PROTOCOL_API_BASE", "https://api.llama.fi/protocol")
ETHERSCAN_LABELS_URL = os.getenv(
    "LIQSCOPE_ETHERSCAN_LABELS_URL",
    "https://raw.githubusercontent.com/brianleect/etherscan-labels/main/data/etherscan/combined/combinedAllLabels.json",
)
SUPPORTED_CHAINS = {"ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA", "TRON"}
EVM_CHAINS = {"ETH", "BNB", "POLYGON", "ARBITRUM", "BASE"}
CHAIN_ALIASES = {
    "ethereum": "ETH", "eth": "ETH", "mainnet": "ETH",
    "bsc": "BNB", "binance": "BNB", "binancechain": "BNB",
    "binancesmartchain": "BNB", "bnb": "BNB",
    "polygon": "POLYGON", "polygonpos": "POLYGON", "matic": "POLYGON", "pol": "POLYGON",
    "arbitrum": "ARBITRUM", "arbitrumone": "ARBITRUM", "arb": "ARBITRUM",
    "base": "BASE", "solana": "SOLANA", "sol": "SOLANA",
    "tron": "TRON", "trx": "TRON",
}
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TRON_ADDRESS = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
MAX_SOURCE_BYTES = 2_000_000
SOURCE_TIMEOUT_SEC = 30
SOURCE_MAX_RETRIES = 2
SOURCE_RETRY_BACKOFF_SEC = 0.5
SOURCE_CHUNK_SIZE = 512 * 1024
MAX_PROTOCOLS_BYTES = 10_000_000
MAX_PROTOCOL_DETAIL_BYTES = 5_000_000
MAX_ETHERSCAN_LABELS_BYTES = 100_000_000
SOURCE_ERROR_BODY_CHARS = 300
SOURCE_USER_AGENT = "LiqScope-CEX-Wallets/1.0"
KNOWN_CEX_NAMES = ("Binance", "OKX", "Bybit", "Coinbase", "Kraken", "Bitfinex",
                   "Gate.io", "KuCoin", "Crypto.com", "Gemini", "MEXC", "HTX")


def _safe_source_url(url: str) -> str:
    parts = urlsplit(str(url))
    hostname = parts.hostname or ""
    if parts.port:
        hostname += f":{parts.port}"
    path = parts.path
    if (parts.hostname or "").lower() == "pro-api.llama.fi":
        segments = path.lstrip("/").split("/")
        if segments and segments[0]:
            segments[0] = "[redacted]"
            path = "/" + "/".join(segments)
    query = [(key, "[redacted]" if re.search(r"key|token|secret|auth", key, re.I) else value)
             for key, value in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, hostname, path, urlencode(query), ""))


def _safe_source_body(body: str) -> str:
    text = str(body or "")[:SOURCE_ERROR_BODY_CHARS]
    return re.sub(r"(?i)(api[-_]?key|token|secret|authorization)(\s*[=:]\s*)[^\s,;]+",
                  r"\1\2[redacted]", text)


class SourceFetchError(RuntimeError):
    """A sanitized provider failure suitable for logs and the admin UI."""

    def __init__(self, url: str, kind: str, *, status: int | None = None,
                 body: str = "", detail: str = ""):
        self.url = _safe_source_url(url)
        self.kind = str(kind)
        self.status = status
        self.body = _safe_source_body(body)
        self.detail = str(detail or "")[:240]
        parts = [self.kind, f"URL={self.url}"]
        if self.status is not None:
            parts.append(f"HTTP {self.status}")
        if self.detail:
            parts.append(self.detail)
        if self.body:
            parts.append(f"body={self.body}")
        super().__init__("; ".join(parts))


class WalletSourceError(RuntimeError):
    def __init__(self, errors: list[str]):
        self.errors = [str(error)[:600] for error in errors if error]
        super().__init__("; ".join(self.errors)[:1800] or "No supported wallet source returned data")


class WalletFetchResult(list):
    """List-compatible result carrying per-source counts and non-fatal warnings."""

    def __init__(self, rows=(), *, source_counts=None, source_errors=None,
                 source_status=None):
        super().__init__(rows)
        self.source_counts = dict(source_counts or {})
        self.source_errors = [str(error)[:600] for error in (source_errors or []) if error]
        self.source_status = dict(source_status or {})


def normalize_chain(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return CHAIN_ALIASES.get(key, "")


def address_key(chain: str, address: str) -> str:
    return address.lower() if chain in EVM_CHAINS else address


def wallet_id(chain: str, address: str) -> str:
    return hashlib.sha256((chain + "\0" + address_key(chain, address)).encode()).hexdigest()[:16]


def _base58_decoded_length(value: str) -> int:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for char in value:
        if char not in alphabet:
            return 0
        number = number * 58 + alphabet.index(char)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return len(value) - len(value.lstrip("1")) + len(raw)


def valid_address(chain: str, address: Any) -> bool:
    if not isinstance(address, str):
        return False
    if chain in EVM_CHAINS:
        return bool(EVM_ADDRESS.fullmatch(address))
    if chain == "SOLANA":
        return bool(BASE58.fullmatch(address)) and _base58_decoded_length(address) == 32
    if chain == "TRON":
        if not TRON_ADDRESS.fullmatch(address):
            return False
        try:
            return tron_to_base58(address) == address
        except ValueError:
            return False
    return False


def normalize_wallet_record(raw: dict, source: str | None = None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    chain = normalize_chain(raw.get("chain"))
    address = str(raw.get("address") or "").strip()
    name = str(raw.get("name") or raw.get("exchange") or "").strip()
    if chain not in SUPPORTED_CHAINS or not valid_address(chain, address) or not name:
        return None
    name = name[:100]
    src = source or str(raw.get("source") or "defillama")
    if src not in ("manual", "defillama", "etherscan"):
        src = "defillama"
    updated_at = raw.get("updated_at") or int(time.time())
    try:
        updated_at = int(updated_at)
    except (ValueError, TypeError):
        updated_at = int(time.time())
    return {"id": wallet_id(chain, address), "chain": chain, "address": address,
            "name": name, "source": src, "updated_at": updated_at}


def _compact_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _flatten_addresses(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [address for item in value for address in _flatten_addresses(item)]
    if isinstance(value, dict):
        out = []
        for key in ("address", "addresses", "wallet", "owner"):
            if key in value:
                out.extend(_flatten_addresses(value[key]))
        return out
    return []


def _chain_address_pairs(value: Any, default_chain: str = "") -> list[tuple[str, str]]:
    """Read common chain->address and [{chain,address}] REST API shapes."""
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        chain_fields = ("chain", "network", "chainName")
        explicit_chain = any(key in value and value.get(key) not in (None, "")
                             for key in chain_fields)
        row_chain = normalize_chain(next((value[key] for key in chain_fields
                                          if key in value and value[key]), ""))
        if explicit_chain:
            if row_chain:
                raw_addresses = next((value[key] for key in ("address", "addresses", "wallet", "owner")
                                      if key in value), None)
                pairs.extend((row_chain, address) for address in _flatten_addresses(raw_addresses))
            # An explicit but unsupported/multi-chain field must not inherit a
            # different default chain and misattribute the wallet.
            return pairs
        if default_chain:
            raw_addresses = next((value[key] for key in ("address", "addresses", "wallet", "owner")
                                  if key in value), None)
            if raw_addresses is not None:
                pairs.extend((default_chain, address) for address in _flatten_addresses(raw_addresses))
        for raw_chain, raw_addresses in value.items():
            chain = normalize_chain(raw_chain)
            if chain:
                pairs.extend((chain, address) for address in _flatten_addresses(raw_addresses))
        return pairs
    if isinstance(value, (list, tuple, set)):
        for item in value:
            pairs.extend(_chain_address_pairs(item, default_chain))
        return pairs
    if default_chain:
        pairs.extend((default_chain, address) for address in _flatten_addresses(value))
    return pairs


def _single_protocol_chain(protocol: dict) -> str:
    for key in ("chain", "chainName", "network"):
        chain = normalize_chain(protocol.get(key))
        if chain:
            return chain
    chains = protocol.get("chains")
    if isinstance(chains, list):
        candidates = {normalize_chain(item) for item in chains}
        candidates.discard("")
        if len(candidates) == 1:
            return next(iter(candidates))
    return ""


def parse_defillama_cex_protocol(protocol: Any, *, fallback_name: str = "") -> list[dict]:
    """Parse explicit CEX wallet fields from a DeFiLlama REST protocol record.

    Plain ``address`` values are accepted only when the record identifies one
    supported chain. Multi-chain addresses must be explicitly chain-scoped.
    """
    if not isinstance(protocol, dict):
        return []
    category = protocol.get("category")
    if category is not None and str(category).strip().casefold() != "cex":
        return []
    name = str(protocol.get("name") or protocol.get("displayName") or fallback_name or
               protocol.get("slug") or "").strip()
    if not name:
        return []

    pairs = _chain_address_pairs(protocol.get("chainAddresses"))
    default_chain = _single_protocol_chain(protocol)
    pairs.extend(_chain_address_pairs(protocol.get("address"), default_chain))

    records: dict[tuple[str, str], dict] = {}
    for chain, address in pairs:
        row = normalize_wallet_record({"chain": chain, "address": address,
                                       "name": name, "source": "defillama"})
        if row:
            records[(row["chain"], address_key(row["chain"], row["address"]))] = row
    return list(records.values())


def _dedupe_wallet_records(rows: list[dict]) -> list[dict]:
    result = {}
    for item in rows:
        row = normalize_wallet_record(item)
        if not row:
            continue
        key = (row["chain"], address_key(row["chain"], row["address"]))
        previous = result.get(key)
        if previous is None or (previous.get("source") == "etherscan" and
                                row.get("source") == "defillama"):
            result[key] = row
    return list(result.values())


def parse_etherscan_labels(payload: Any, exchange_names: list[str]) -> list[dict]:
    """Conservative ETH-only fallback for labels explicitly naming an exchange."""
    if not isinstance(payload, dict):
        return []
    names = {_compact_name(name): str(name) for name in exchange_names if name}
    out, seen = [], set()
    for address, value in payload.items():
        if not EVM_ADDRESS.fullmatch(str(address)) or not isinstance(value, dict):
            continue
        labels = value.get("labels") or []
        if isinstance(labels, dict):
            labels = list(labels) + list(labels.values())
        if not isinstance(labels, list):
            continue
        text = " ".join(str(label) for label in labels).lower()
        normalized = _compact_name(text)
        name = next((label for slug, label in names.items() if slug and slug in normalized), None)
        if not name:
            continue
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalize_wallet_record({"chain": "ETH", "address": key,
                                            "name": name, "source": "etherscan"}))
    return [row for row in out if row]


async def _read_source_bytes(session: aiohttp.ClientSession, url: str, *,
                            limit: int = MAX_SOURCE_BYTES) -> tuple[bytearray, int]:
    """Download a bounded response in chunks and reject incomplete bodies."""
    for attempt in range(SOURCE_MAX_RETRIES + 1):
        try:
            async with session.get(
                    url,
                    headers={"User-Agent": SOURCE_USER_AGENT, "Accept-Encoding": "identity"},
                    timeout=aiohttp.ClientTimeout(total=SOURCE_TIMEOUT_SEC)) as response:
                status = int(response.status)
                if status != 200:
                    try:
                        error_bytes = await response.content.read(SOURCE_ERROR_BODY_CHARS + 1)
                        body = error_bytes.decode("utf-8", errors="replace")
                    except Exception as exc:
                        body = f"[unable to read response body: {type(exc).__name__}]"
                    error = SourceFetchError(url, "HTTP error", status=status, body=body)
                    retryable = status == 429 or 500 <= status < 600
                    if retryable and attempt < SOURCE_MAX_RETRIES:
                        await asyncio.sleep(SOURCE_RETRY_BACKOFF_SEC * (2 ** attempt))
                        continue
                    raise error

                expected_length = None
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        expected_length = int(length)
                    except ValueError:
                        expected_length = None
                    if expected_length is not None and expected_length > limit:
                        raise SourceFetchError(url, "response too large", status=status,
                                               detail=f"Content-Length exceeds {limit} bytes")
                    if response.headers.get("Content-Encoding", "identity").lower() not in (
                            "", "identity"):
                        # aiohttp transparently decompresses encoded responses, so the
                        # wire Content-Length is not comparable to decoded chunk sizes.
                        expected_length = None

                body = bytearray()
                received = 0
                async for chunk in response.content.iter_chunked(SOURCE_CHUNK_SIZE):
                    received += len(chunk)
                    if received > limit:
                        raise SourceFetchError(url, "response too large", status=status,
                                               detail=f"response exceeds {limit} bytes")
                    body.extend(chunk)
                if expected_length is not None and received != expected_length:
                    error = SourceFetchError(
                        url, "incomplete response", status=status,
                        detail=f"received {received} of {expected_length} bytes")
                    if attempt < SOURCE_MAX_RETRIES:
                        await asyncio.sleep(SOURCE_RETRY_BACKOFF_SEC * (2 ** attempt))
                        continue
                    raise error
                return body, status
        except SourceFetchError:
            raise
        except asyncio.TimeoutError:
            error = SourceFetchError(url, "timeout",
                                     detail=f"request timed out after {SOURCE_TIMEOUT_SEC}s")
            if attempt < SOURCE_MAX_RETRIES:
                await asyncio.sleep(SOURCE_RETRY_BACKOFF_SEC * (2 ** attempt))
                continue
            raise error from None
        except aiohttp.ClientError as exc:
            error = SourceFetchError(url, "network error", detail=type(exc).__name__)
            if attempt < SOURCE_MAX_RETRIES:
                await asyncio.sleep(SOURCE_RETRY_BACKOFF_SEC * (2 ** attempt))
                continue
            raise error from None
    raise SourceFetchError(url, "network error", detail="request retries exhausted")


async def _read_json(session: aiohttp.ClientSession, url: str, *, limit: int = MAX_SOURCE_BYTES):
    raw, status = await _read_source_bytes(session, url, limit=limit)
    try:
        # json.loads accepts bytearray, avoiding an extra full-size UTF-8 copy
        # for the optional large Etherscan label file.
        return await asyncio.to_thread(json.loads, raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        detail = (f"JSON decode error at line {exc.lineno}, column {exc.colno}: {exc.msg}"
                  if isinstance(exc, json.JSONDecodeError) else "JSON decode error: invalid UTF-8")
        preview = raw[:SOURCE_ERROR_BODY_CHARS].decode("utf-8", errors="replace")
        raise SourceFetchError(url, "JSON decode error", status=status,
                               body=preview, detail=detail) from None


async def fetch_defillama_cex_wallets(session: aiohttp.ClientSession | None = None) -> WalletFetchResult:
    """Load CEX wallet records from independent DeFiLlama and Etherscan sources.

    DeFiLlama's documented ``/protocols`` REST response is filtered to CEX
    records. Explicit ``chainAddresses``/``address`` values are used directly;
    records without addresses are enriched from ``/protocol/{slug}``. The
    optional Etherscan labels file is isolated and can fail without discarding
    DeFiLlama results.
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(headers={"User-Agent": SOURCE_USER_AGENT})
    assert session is not None
    errors: list[str] = []
    source_status = {"defillama": "error", "etherscan": "error"}
    protocols: list[dict] = []
    defillama_rows: list[dict] = []

    try:
        try:
            payload = await _read_json(session, DEFILLAMA_PROTOCOLS_API,
                                       limit=MAX_PROTOCOLS_BYTES)
            if not isinstance(payload, list):
                raise SourceFetchError(DEFILLAMA_PROTOCOLS_API, "schema mismatch",
                                       status=200, detail="expected a protocols array")
            protocols = [row for row in payload
                         if isinstance(row, dict) and
                         str(row.get("category") or "").strip().casefold() == "cex"]
            source_status["defillama"] = "ok"
        except Exception as exc:  # isolate DeFiLlama API from the labels fallback
            error = (exc if isinstance(exc, SourceFetchError) else
                     SourceFetchError(DEFILLAMA_PROTOCOLS_API, "source error",
                                      detail=type(exc).__name__))
            errors.append(str(error))
            log.warning("[cex_updater] Ошибка DeFiLlama API: %s", error)
            protocols = []

        detail_candidates = []
        exchange_names = []
        for protocol in protocols:
            name = str(protocol.get("name") or protocol.get("displayName") or
                       protocol.get("slug") or "").strip()
            if name:
                exchange_names.append(name)
            try:
                direct = parse_defillama_cex_protocol(protocol)
            except Exception as exc:  # malformed protocol rows are isolated individually
                error = SourceFetchError(
                    DEFILLAMA_PROTOCOLS_API, "protocol parse error",
                    detail=f"{type(exc).__name__}: {str(protocol.get('slug') or '')[:80]}")
                errors.append(str(error))
                log.warning("[cex_updater] Ошибка CEX-записи DeFiLlama: %s", error)
                direct = []
            defillama_rows.extend(direct)
            if not direct and protocol.get("slug"):
                detail_candidates.append((str(protocol["slug"]).strip(), name))

        semaphore = asyncio.Semaphore(5)

        async def fetch_protocol_detail(slug: str, fallback_name: str) -> list[dict]:
            url = f"{DEFILLAMA_PROTOCOL_API_BASE.rstrip('/')}/{quote(slug, safe='')}"
            async with semaphore:
                try:
                    detail = await _read_json(session, url, limit=MAX_PROTOCOL_DETAIL_BYTES)
                    if not isinstance(detail, dict):
                        raise SourceFetchError(url, "schema mismatch", status=200,
                                               detail="expected a protocol object")
                    return parse_defillama_cex_protocol(detail, fallback_name=fallback_name)
                except Exception as exc:  # one unavailable protocol must not block peers
                    error = (exc if isinstance(exc, SourceFetchError) else
                             SourceFetchError(url, "source error", detail=type(exc).__name__))
                    errors.append(str(error))
                    log.warning("[cex_updater] Ошибка DeFiLlama API (%s): %s",
                                fallback_name or slug, error)
                    return []

        if detail_candidates:
            details = await asyncio.gather(*(
                fetch_protocol_detail(slug, name) for slug, name in detail_candidates))
            for rows in details:
                defillama_rows.extend(rows)
        defillama_rows = _dedupe_wallet_records(defillama_rows)

        # Etherscan labels are a secondary enrichment source. Its large JSON
        # file is streamed in chunks and decoded independently from DeFiLlama.
        etherscan_rows: list[dict] = []
        try:
            labels = await _read_json(session, ETHERSCAN_LABELS_URL,
                                      limit=MAX_ETHERSCAN_LABELS_BYTES)
            if not isinstance(labels, dict):
                raise SourceFetchError(ETHERSCAN_LABELS_URL, "schema mismatch", status=200,
                                       detail="expected address-to-label object")
            names = list(dict.fromkeys([*exchange_names, *KNOWN_CEX_NAMES]))
            etherscan_rows = await asyncio.to_thread(parse_etherscan_labels, labels, names)
            source_status["etherscan"] = "ok"
        except Exception as exc:  # malformed/truncated labels skip only this source
            error = (exc if isinstance(exc, SourceFetchError) else
                     SourceFetchError(ETHERSCAN_LABELS_URL, "source error",
                                      detail=type(exc).__name__))
            errors.append(str(error))
            log.warning("[cex_updater] Ошибка Etherscan labels (пропущено): %s", error)

        etherscan_rows = _dedupe_wallet_records(etherscan_rows)
        rows = _dedupe_wallet_records([*defillama_rows, *etherscan_rows])
        if not protocols and source_status["defillama"] != "ok" and source_status["etherscan"] != "ok":
            raise WalletSourceError(errors)
        return WalletFetchResult(
            rows,
            source_counts={"defillama": len(defillama_rows),
                           "etherscan": len(etherscan_rows)},
            source_errors=errors,
            source_status=source_status,
        )
    finally:
        if own_session:
            await session.close()


class CEXWalletRegistry:
    """Thread-safe JSON registry with automatic/manual sources and backups."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._refresh_lock = asyncio.Lock()
        self._automatic: list[dict] = []
        self._manual: list[dict] = []
        self.updated_at = 0
        self.last_source = ""
        self.last_error = ""
        # Сводка последней автоочистки: когда, сколько убрали, почему не убрали.
        self.last_prune: dict = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("version") == 2:
                self._automatic = [row for item in payload.get("automatic", [])
                                   if (row := normalize_wallet_record(item))
                                   and row["source"] != "manual"]
                self._manual = [row for item in payload.get("manual", [])
                                if (row := normalize_wallet_record(item, "manual"))]
                self.updated_at = int(payload.get("updated_at") or 0)
                self.last_source = str(payload.get("source") or "")
                return
            # The legacy address -> label map was consumed by every supported
            # EVM poller. Preserve that established EVM scope, but never project
            # it onto non-EVM networks without chain-specific confirmation.
            if isinstance(payload, dict):
                ts = int(self.path.stat().st_mtime) if self.path.exists() else int(time.time())
                self._manual = [row for address, label in payload.items()
                                for chain in sorted(EVM_CHAINS)
                                if (row := normalize_wallet_record({
                                    "chain": chain, "address": address, "name": label,
                                    "source": "manual", "updated_at": ts}))]
                self._automatic = []

    def records(self, *, chain: str = "ALL", exchange: str = "") -> list[dict]:
        with self._lock:
            merged = {}
            for row in self._automatic + self._manual:
                key = (row["chain"], address_key(row["chain"], row["address"]))
                merged[key] = dict(row)  # manual rows come last and take precedence
            rows = list(merged.values())
        chain = normalize_chain(chain) if chain and chain.upper() != "ALL" else "ALL"
        if chain != "ALL":
            rows = [row for row in rows if row["chain"] == chain]
        if exchange:
            needle = exchange.casefold()
            rows = [row for row in rows if needle in row["name"].casefold()]
        rows.sort(key=lambda row: (row["chain"], row["name"].casefold(), row["address"]))
        return rows

    def runtime_wallets(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for row in self.records():
            result.setdefault(row["chain"], {})[row["address"]] = row["name"]
        return result

    def summary(self) -> dict:
        rows = self.records()
        counts = {}
        for row in rows:
            counts[row["chain"]] = counts.get(row["chain"], 0) + 1
        oldest = min((int(row.get("updated_at") or 0) for row in self._automatic),
                     default=0)
        return {"total": len(rows), "by_chain": counts, "updated_at": self.updated_at,
                "source": self.last_source, "error": self.last_error,
                "automatic": len(self._automatic), "manual": len(self._manual),
                "oldest_updated_at": oldest, "last_prune": dict(self.last_prune)}

    def _save_locked(self, *, source: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            backup = self.path.with_suffix(self.path.suffix + ".bak")
            try:
                shutil.copy2(self.path, backup)
            except OSError as exc:
                log.warning("[cex_wallets] backup copy failed: %s", type(exc).__name__)
        payload = {"version": 2, "updated_at": self.updated_at,
                   "source": source or self.last_source,
                   "automatic": self._automatic, "manual": self._manual}
        fd, temp_name = tempfile.mkstemp(prefix="cex_wallets-", suffix=".tmp",
                                         dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def add_manual(self, chain: str, address: str, name: str) -> dict:
        row = normalize_wallet_record({"chain": chain, "address": address,
                                       "name": name, "source": "manual"}, "manual")
        if not row:
            raise ValueError("Invalid network, address or exchange name")
        with self._lock:
            previous = self._manual
            key = (row["chain"], address_key(row["chain"], row["address"]))
            self._manual = [item for item in self._manual
                            if (item["chain"], address_key(item["chain"], item["address"])) != key]
            self._manual.append(row)
            try:
                self._save_locked()
            except OSError as exc:
                self._manual = previous
                raise OSError("Could not save wallet registry") from exc
        return dict(row)

    def update_manual(self, identifier: str, chain: str, address: str, name: str) -> dict | None:
        row = normalize_wallet_record({"chain": chain, "address": address,
                                       "name": name, "source": "manual"}, "manual")
        if not row:
            raise ValueError("Invalid network, address or exchange name")
        with self._lock:
            index = next((i for i, item in enumerate(self._manual)
                          if item["id"] == identifier), None)
            if index is None:
                return None
            previous = self._manual
            key = (row["chain"], address_key(row["chain"], row["address"]))
            self._manual = [item for i, item in enumerate(self._manual)
                            if i == index or
                            (item["chain"], address_key(item["chain"], item["address"])) != key]
            target_index = next((i for i, item in enumerate(self._manual)
                                 if item["id"] == identifier), None)
            if target_index is None:
                self._manual = previous
                return None
            self._manual[target_index] = row
            try:
                self._save_locked()
            except OSError as exc:
                self._manual = previous
                raise OSError("Could not save wallet registry") from exc
        return dict(row)

    def remove_manual(self, identifier: str) -> bool:
        with self._lock:
            new_rows = [row for row in self._manual if row["id"] != identifier]
            if len(new_rows) == len(self._manual):
                return False
            previous, self._manual = self._manual, new_rows
            try:
                self._save_locked()
            except OSError as exc:
                self._manual = previous
                raise OSError("Could not save wallet registry") from exc
            return True

    def prune_stale(self, *, cutoff: float, active_keys: set | None = None,
                    min_keep_ratio: float = 0.5, apply: bool = True) -> dict:
        """Убрать кошельки, которые не видно ни в источниках, ни в скринере.

        Кошелёк считается мёртвым, если одновременно:
          • ни один источник (DeFiLlama / Etherscan) не публиковал его с `cutoff`
            — то есть сутки за сутками он не обновлялся в реестре;
          • его адреса нет ни в одном событии Скринера за это же окно
            (`active_keys` из history-базы).

        Ручные кошельки (`source == "manual"`) не трогаем никогда: их добавил
        админ осознанно, и решение об удалении — тоже его.

        Страховка от сюрпризов провайдера: если за один проход набегает больше
        половины автосписка, ничего не удаляем — такое бывает, когда источник
        меняет формат или молчит, а не когда кошельки правда умерли.
        """
        wanted = {(str(chain).upper(), str(address or ""))
                  for chain, address in (active_keys or set())
                  if str(chain or "").strip()}
        cutoff = int(cutoff)
        with self._lock:
            keep: list[dict] = []
            drop: list[dict] = []
            for row in self._automatic:
                key = (row["chain"], address_key(row["chain"], row["address"]))
                fresh = int(row.get("updated_at") or 0) >= cutoff
                if fresh or key in wanted or (row["chain"], row["address"]) in wanted:
                    keep.append(row)
                else:
                    drop.append(row)
            result = {
                "ok": True,
                "cutoff": cutoff,
                "before": len(self._automatic) + len(self._manual),
                "removed": len(drop),
                "automatic_total": len(self._automatic),
                "manual_kept": len(self._manual),
                "by_chain": {},
                "applied": False,
                "activity_source": "history" if active_keys is not None else "unavailable",
            }
            for row in drop:
                result["by_chain"][row["chain"]] = result["by_chain"].get(row["chain"], 0) + 1
            if not drop:
                self.last_prune = {**result, "at": int(time.time())}
                return result
            # На маленькой базе «половина» — пара строк, и любая плановая
            # чистка упиралась бы в лимит. Значит: до 50 за проход можно всегда,
            # дальше — не больше (1 - min_keep_ratio) автосписка.
            limit = max(50, int(len(self._automatic) * max(0.0, 1.0 - min_keep_ratio)))
            if len(drop) > limit:
                result.update(ok=False, removed=0,
                              reason=("слишком много неактуальных за раз "
                                      f"({len(drop)} из {len(self._automatic)}) — "
                                      f"лимит на проход {limit}; ничего не удалено"))
                self.last_prune = {**result, "at": int(time.time())}
                log.warning("[cex_wallets] prune skipped: %s", result["reason"])
                return result
            if not apply:
                result["reason"] = "dry run"
                return result
            previous = self._automatic
            self._automatic = keep
            try:
                self._save_locked(source=self.last_source)
            except OSError as exc:
                self._automatic = previous
                result.update(ok=False, removed=0, applied=False,
                              reason=f"не удалось записать реестр: {type(exc).__name__}")
                return result
            result["after"] = len(keep) + len(self._manual)
            result["applied"] = True
            self.last_prune = {**result, "at": int(time.time())}
            log.info("[cex_wallets] автоочистка: убрано %d из %d (older than %s)",
                     len(drop), len(previous), time.strftime(
                         "%Y-%m-%d", time.gmtime(cutoff)))
            return result

    def stale_count(self, *, cutoff: float, active_keys: set | None = None) -> int:
        """Сколько кошельков считаются мёртвыми. Только счёт, без страховки.

        Страховку «не слить половину базы» оставляет за собой самой чистке: в
        предпросмотре админ обязан увидеть реальное число, иначе молчаливый ноль
        выглядит как «чистить нечего».
        """
        preview = self.prune_stale(cutoff=cutoff, active_keys=active_keys,
                                  min_keep_ratio=0.0, apply=False)
        return int(preview.get("removed") or 0)

    async def refresh(self, session: aiohttp.ClientSession | None = None) -> dict:
        async with self._refresh_lock:
            before_rows = self.records()
            before_keys = {(row["chain"], address_key(row["chain"], row["address"]))
                           for row in before_rows}
            try:
                fetched = await fetch_defillama_cex_wallets(session)
                automatic = [row for item in fetched
                             if (row := normalize_wallet_record(item))
                             and row["source"] != "manual"]
                raw_counts = getattr(fetched, "source_counts", {})
                if not isinstance(raw_counts, dict):
                    raw_counts = {}
                defillama_count = int(raw_counts.get(
                    "defillama", sum(row["source"] == "defillama" for row in automatic)) or 0)
                etherscan_count = int(raw_counts.get(
                    "etherscan", sum(row["source"] == "etherscan" for row in automatic)) or 0)
                warnings = list(getattr(fetched, "source_errors", []) or [])
                source_status = dict(getattr(fetched, "source_status", {}) or {})

                # Union fresh records with the current automatic registry. A
                # transient empty response can never delete previously saved
                # wallets; manual rows remain separate and always win on display.
                if automatic:
                    with self._lock:
                        previous = (self._automatic, self.updated_at,
                                    self.last_source, self.last_error)
                        try:
                            merged = {
                                (row["chain"], address_key(row["chain"], row["address"])): row
                                for row in self._automatic
                            }
                            priority = {"etherscan": 1, "defillama": 2}
                            for row in automatic:
                                key = (row["chain"], address_key(row["chain"], row["address"]))
                                old = merged.get(key)
                                if old is None or priority.get(row["source"], 0) >= priority.get(old["source"], 0):
                                    merged[key] = row
                            self._automatic = list(merged.values())
                            self.updated_at = int(time.time())
                            if defillama_count:
                                self.last_source = "defillama"
                            elif etherscan_count:
                                self.last_source = "etherscan"
                            self.last_error = ""
                            self._save_locked(source=self.last_source)
                        except Exception:
                            self._automatic, self.updated_at, self.last_source, self.last_error = previous
                            raise
                else:
                    # A successful empty result is a no-op, not a reason to
                    # replace the file or surface a JSON/provider exception.
                    self.last_error = ""

                after_rows = self.records()
                after_keys = {(row["chain"], address_key(row["chain"], row["address"]))
                              for row in after_rows}
                added = len(after_keys - before_keys)
                log.info("[cex_updater] refresh merged: fetched DefiLlama=%d, Etherscan=%d, "
                         "registry before=%d after=%d",
                         defillama_count, etherscan_count, len(before_rows), len(after_rows))
                return {
                    "ok": True,
                    "before": len(before_rows),
                    "after": len(after_rows),
                    "added": added,
                    "defillama_count": defillama_count,
                    "etherscan_count": etherscan_count,
                    "message": (f"Успешно обновлено: получено {defillama_count} "
                                "адресов из DeFiLlama API"),
                    "warnings": warnings,
                    "source_status": source_status,
                    "updated_at": self.updated_at,
                    "summary": self.summary(),
                }
            except Exception as exc:
                self.last_error = str(exc)[:1800]
                log.warning("[cex_updater] refresh failed: %s", self.last_error)
                raise


async def auto_refresh_loop(registry: CEXWalletRegistry, on_updated=None,
                            interval: int = 24 * 60 * 60) -> None:
    """Refresh daily; retry a failed source after one hour, without blocking startup."""
    session = aiohttp.ClientSession(headers={"User-Agent": "LiqScope-CEX-Wallets/1.0"})
    try:
        first = True
        while True:
            try:
                result = await registry.refresh(session)
                if on_updated:
                    outcome = on_updated(result)
                    if asyncio.iscoroutine(outcome):
                        await outcome
                delay = interval
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = 3600 if first else interval
            first = False
            await asyncio.sleep(delay)
    finally:
        await session.close()
