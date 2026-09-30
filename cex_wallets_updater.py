"""Refresh, validate and persist public CEX wallet labels.

No wallet-address endpoint is assumed. The configurable ``/cexs`` candidate is
treated only as metadata, with ``/protocols`` as a metadata fallback. Public
wallet owners are parsed from the open-source ``cex/index.js`` adapter config;
only static ``owners`` lists are accepted, and computed JavaScript expressions
are deliberately ignored rather than guessed.
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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from tron_address import tron_to_base58

log = logging.getLogger(__name__)

DEFILLAMA_CEX_API = os.getenv("LIQSCOPE_DEFILLAMA_CEX_API", "https://api.llama.fi/cexs")
DEFILLAMA_CEX_CONFIG = os.getenv(
    "LIQSCOPE_DEFILLAMA_CEX_CONFIG",
    "https://raw.githubusercontent.com/DefiLlama/DefiLlama-Adapters/main/cex/index.js",
)
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
SOURCE_TIMEOUT_SEC = 25
SOURCE_MAX_RETRIES = 2
SOURCE_RETRY_BACKOFF_SEC = 0.5
SOURCE_ERROR_BODY_CHARS = 300
SOURCE_USER_AGENT = "LiqScope-CEX-Wallets/1.0"
DEFILLAMA_PROTOCOLS_API = os.getenv(
    "LIQSCOPE_DEFILLAMA_PROTOCOLS_API", "https://api.llama.fi/protocols")
DEFILLAMA_CEX_CONFIG_FALLBACK = (
    "https://raw.githubusercontent.com/DefiLlama/DefiLlama-Adapters/master/cex/index.js")


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


def _tokens(js: str) -> list[tuple[str, str]]:
    """Tokenize enough of the JS object-literal subset used in CEX configs."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(js)
    while i < n:
        ch = js[i]
        if ch.isspace():
            i += 1
            continue
        if js.startswith("//", i):
            end = js.find("\n", i + 2)
            i = n if end < 0 else end + 1
            continue
        if js.startswith("/*", i):
            end = js.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in "'\"`":
            quote, i = ch, i + 1
            value = []
            while i < n:
                if js[i] == "\\" and i + 1 < n:
                    esc = js[i + 1]
                    value.append({"n": "\n", "r": "\r", "t": "\t"}.get(esc, esc))
                    i += 2
                elif js[i] == quote:
                    i += 1
                    break
                else:
                    value.append(js[i])
                    i += 1
            out.append(("string", "".join(value)))
            continue
        if ch.isalpha() or ch in "_$":
            start = i
            i += 1
            while i < n and (js[i].isalnum() or js[i] in "_$-"):
                i += 1
            out.append(("id", js[start:i]))
            continue
        if ch.isdigit():
            start = i
            i += 1
            while i < n and (js[i].isalnum() or js[i] in ".xX"):
                i += 1
            out.append(("number", js[start:i]))
            continue
        out.append(("punct", ch))
        i += 1
    return out


class _ObjectParser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.i = 0

    def _peek(self, value: str | None = None) -> bool:
        if self.i >= len(self.tokens):
            return False
        return value is None or self.tokens[self.i][1] == value

    def _take(self) -> tuple[str, str]:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def _skip_value(self, stop: set[str]) -> None:
        depth = 0
        while self.i < len(self.tokens):
            value = self.tokens[self.i][1]
            if depth == 0 and value in stop:
                return
            if value in ("{", "[", "("):
                depth += 1
            elif value in ("}", "]", ")"):
                if depth == 0:
                    return
                depth -= 1
            self.i += 1

    def value(self):
        if not self._peek():
            return None
        kind, value = self._take()
        if value == "{":
            result = {}
            while self.i < len(self.tokens) and not self._peek("}"):
                if self._peek(","):
                    self.i += 1
                    continue
                key_kind, key = self._take()
                if key in ("...", "["):
                    self._skip_value({",", "}"})
                    if self._peek(","):
                        self.i += 1
                    continue
                if not self._peek(":"):
                    self._skip_value({",", "}"})
                    if self._peek(","):
                        self.i += 1
                    continue
                self.i += 1
                result[str(key)] = self.value()
                if self._peek(","):
                    self.i += 1
            if self._peek("}"):
                self.i += 1
            return result
        if value == "[":
            result = []
            while self.i < len(self.tokens) and not self._peek("]"):
                if self._peek(","):
                    self.i += 1
                    continue
                start = self.i
                parsed = self.value()
                if parsed is not None:
                    result.append(parsed)
                if self.i == start:
                    self._skip_value({",", "]"})
                if self._peek(","):
                    self.i += 1
            if self._peek("]"):
                self.i += 1
            return result
        if kind == "string":
            return value
        if value in ("true", "false", "null", "undefined"):
            return {"true": True, "false": False}.get(value)
        if kind == "number":
            try:
                return float(value) if "." in value else int(value, 0)
            except ValueError:
                return value
        # Only static strings/arrays are used as wallet owners. An unknown
        # expression is skipped at its surrounding comma by object/array parse.
        if value in ("function", "async"):
            self._skip_value({",", "}", "]"})
        return value


def parse_config_object(source: str) -> dict:
    """Extract the static ``const configs = {...}`` object from DefiLlama JS."""
    match = re.search(r"\bconst\s+configs\s*=\s*\{", source)
    if not match:
        raise ValueError("DefiLlama CEX config object not found")
    start = match.end() - 1
    depth, quote, escaped, line_comment, block_comment = 0, "", False, False, False
    end = None
    i = start
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if line_comment:
            if ch == "\n": line_comment = False
        elif block_comment:
            if ch == "*" and nxt == "/": block_comment = False; i += 1
        elif quote:
            if escaped: escaped = False
            elif ch == "\\": escaped = True
            elif ch == quote: quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == "/" and nxt == "/": line_comment = True; i += 1
        elif ch == "/" and nxt == "*": block_comment = True; i += 1
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    if end is None:
        raise ValueError("Unclosed DefiLlama CEX config object")
    parser = _ObjectParser(_tokens(source[start:end]))
    result = parser.value()
    if not isinstance(result, dict):
        raise ValueError("DefiLlama CEX config is not an object")
    return result


def _compact_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _exchange_names(payload: Any) -> dict[str, str]:
    rows = payload.get("cexs", []) if isinstance(payload, dict) else payload
    result = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        for val in (row.get("slug"), row.get("name"), row.get("id")):
            if val is not None:
                result[_compact_name(val)] = name
    return result


def parse_defillama_cex_config(source: str, metadata: Any = None) -> list[dict]:
    configs = parse_config_object(source)
    names = _exchange_names(metadata)
    records, seen = [], set()
    for slug, exchange_data in configs.items():
        if not isinstance(exchange_data, dict):
            continue
        # Do not turn an adapter slug into an asserted exchange label. Only
        # attach owners when the API metadata confirms the exchange name.
        name = names.get(_compact_name(slug), "")
        if not name:
            continue
        for raw_chain, chain_data in exchange_data.items():
            chain = normalize_chain(raw_chain)
            if chain not in SUPPORTED_CHAINS or not isinstance(chain_data, dict):
                continue
            owners = chain_data.get("owners")
            if isinstance(owners, str):
                owners = [owners]
            if not isinstance(owners, list):
                continue
            for address in owners:
                record = normalize_wallet_record({"chain": chain, "address": address,
                                                  "name": name, "source": "defillama"})
                if not record:
                    continue
                key = (chain, address_key(chain, record["address"]))
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
    return records


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
                            limit: int = MAX_SOURCE_BYTES) -> tuple[bytes, int]:
    for attempt in range(SOURCE_MAX_RETRIES + 1):
        try:
            async with session.get(url,
                    headers={"User-Agent": SOURCE_USER_AGENT},
                    timeout=aiohttp.ClientTimeout(total=SOURCE_TIMEOUT_SEC)) as response:
                status = int(response.status)
                if status != 200:
                    try:
                        body = await response.text(errors="replace")
                    except Exception as exc:
                        body = f"[unable to read response body: {type(exc).__name__}]"
                    error = SourceFetchError(url, "HTTP error", status=status, body=body)
                    retryable = status == 429 or 500 <= status < 600
                    if retryable and attempt < SOURCE_MAX_RETRIES:
                        await asyncio.sleep(SOURCE_RETRY_BACKOFF_SEC * (2 ** attempt))
                        continue
                    raise error
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        if int(length) > limit:
                            raise SourceFetchError(url, "schema mismatch", status=status,
                                                   detail="response exceeds size limit")
                    except ValueError:
                        pass
                raw = await response.content.read(limit + 1)
                if len(raw) > limit:
                    raise SourceFetchError(url, "schema mismatch", status=status,
                                           detail="response exceeds size limit")
                return raw, status
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
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        detail = (f"JSON decode error at line {exc.lineno}, column {exc.colno}: {exc.msg}"
                  if isinstance(exc, json.JSONDecodeError) else "JSON decode error: invalid UTF-8")
        preview = raw[:SOURCE_ERROR_BODY_CHARS].decode("utf-8", errors="replace")
        raise SourceFetchError(url, "JSON decode error", status=status,
                               body=preview, detail=detail) from None


async def _read_text(session: aiohttp.ClientSession, url: str, *, limit: int = MAX_SOURCE_BYTES):
    raw, status = await _read_source_bytes(session, url, limit=limit)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        preview = raw[:SOURCE_ERROR_BODY_CHARS].decode("utf-8", errors="replace")
        raise SourceFetchError(url, "text decode error", status=status,
                               body=preview, detail="response is not valid UTF-8") from None


async def fetch_defillama_cex_wallets(session: aiohttp.ClientSession | None = None) -> list[dict]:
    """Fetch CEX metadata and parse the chain-scoped public adapter owners.

    A configured ``/cexs`` candidate is treated as metadata, never as a wallet
    list. ``/protocols`` is the metadata fallback, while adapter ``owners`` are
    the address source. Failed or changed API variants are recorded verbatim
    (with secrets redacted) and cannot replace the local registry with emptiness.
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(headers={"User-Agent": SOURCE_USER_AGENT})
    assert session is not None
    errors: list[str] = []
    try:
        metadata = None
        metadata_urls = list(dict.fromkeys((DEFILLAMA_CEX_API, DEFILLAMA_PROTOCOLS_API)))
        for url in metadata_urls:
            try:
                payload = await _read_json(session, url, limit=5_000_000)
                if isinstance(payload, dict):
                    rows = payload.get("cexs")
                    if rows is None:
                        rows = payload.get("protocols")
                elif isinstance(payload, list):
                    rows = payload
                else:
                    rows = None
                if not isinstance(rows, list):
                    preview = json.dumps(payload, ensure_ascii=False)[:SOURCE_ERROR_BODY_CHARS]
                    raise SourceFetchError(url, "schema mismatch", status=200,
                                           body=preview,
                                           detail="expected a cexs or protocols array")
                if "protocols" in url:
                    rows = [row for row in rows if isinstance(row, dict) and
                            str(row.get("category") or "").casefold() == "cex"]
                rows = [row for row in rows if isinstance(row, dict)]
                if not rows:
                    preview = json.dumps(payload, ensure_ascii=False)[:SOURCE_ERROR_BODY_CHARS]
                    raise SourceFetchError(url, "schema mismatch", status=200,
                                           body=preview, detail="no CEX metadata records")
                metadata = {"cexs": rows}
                break
            except SourceFetchError as exc:
                errors.append(str(exc))
                log.warning("[cex_wallets] metadata source failed: %s", exc)

        config_urls = list(dict.fromkeys((DEFILLAMA_CEX_CONFIG,
                                          DEFILLAMA_CEX_CONFIG_FALLBACK)))
        for url in config_urls:
            try:
                config = await _read_text(session, url)
                records = parse_defillama_cex_config(config, metadata)
                if records:
                    return records
                raise SourceFetchError(url, "schema mismatch", status=200, body=config,
                                       detail="no supported static owner addresses in adapter config")
            except SourceFetchError as exc:
                errors.append(str(exc))
                log.warning("[cex_wallets] adapter source failed: %s", exc)
            except (UnicodeError, ValueError) as exc:
                error = SourceFetchError(url, "schema mismatch", detail=type(exc).__name__)
                errors.append(str(error))
                log.warning("[cex_wallets] adapter source failed: %s", error)

        try:
            labels = await _read_json(session, ETHERSCAN_LABELS_URL, limit=50_000_000)
            if not isinstance(labels, dict):
                raise SourceFetchError(ETHERSCAN_LABELS_URL, "schema mismatch", status=200,
                                       body=json.dumps(labels, ensure_ascii=False)[:300],
                                       detail="expected address-to-label object")
            rows = metadata.get("cexs", []) if isinstance(metadata, dict) else []
            names = [str(row["name"]) for row in rows
                     if isinstance(row, dict) and row.get("name")]
            fallback = parse_etherscan_labels(labels, names)
            if fallback:
                return fallback
            raise SourceFetchError(ETHERSCAN_LABELS_URL, "schema mismatch", status=200,
                                   detail="no recognized exchange labels or wallet records")
        except SourceFetchError as exc:
            errors.append(str(exc))
            log.warning("[cex_wallets] fallback source failed: %s", exc)
        raise WalletSourceError(errors)
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
        return {"total": len(rows), "by_chain": counts, "updated_at": self.updated_at,
                "source": self.last_source, "error": self.last_error}

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

    async def refresh(self, session: aiohttp.ClientSession | None = None) -> dict:
        async with self._refresh_lock:
            before = self.records()
            old_by_exchange: dict[str, int] = {}
            for row in before:
                old_by_exchange[row["name"]] = old_by_exchange.get(row["name"], 0) + 1
            try:
                fresh = await fetch_defillama_cex_wallets(session)
                automatic = [row for item in fresh
                             if (row := normalize_wallet_record(item))
                             and row["source"] != "manual"]
                if not automatic:
                    raise WalletSourceError([
                        "schema mismatch: no supported automatic wallet records were returned; "
                        "the existing registry was left unchanged"])
                source = "defillama" if any(row["source"] == "defillama" for row in automatic) else "etherscan"
                with self._lock:
                    previous = (self._automatic, self.updated_at, self.last_source, self.last_error)
                    try:
                        self._automatic = automatic
                        self.updated_at = int(time.time())
                        self.last_source = source
                        self.last_error = ""
                        self._save_locked(source=self.last_source)
                    except Exception:
                        self._automatic, self.updated_at, self.last_source, self.last_error = previous
                        raise
                after = self.records()
                new_by_exchange: dict[str, int] = {}
                for row in after:
                    new_by_exchange[row["name"]] = new_by_exchange.get(row["name"], 0) + 1
                changes = []
                for name in sorted(set(old_by_exchange) | set(new_by_exchange)):
                    delta = new_by_exchange.get(name, 0) - old_by_exchange.get(name, 0)
                    if delta:
                        changes.append(f"{name} {'+' if delta > 0 else ''}{delta}")
                log.info("[cex_wallets] обновлено: было %d адресов, стало %d адресов%s",
                         len(before), len(after), " (" + ", ".join(changes[:12]) + ")" if changes else "")
                return {"ok": True, "before": len(before), "after": len(after),
                        "added": max(0, len(after) - len(before)),
                        "updated_at": self.updated_at, "summary": self.summary()}
            except Exception as exc:
                self.last_error = str(exc)[:1800]
                log.warning("[cex_wallets] refresh failed: %s", self.last_error)
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
