"""Refresh, validate and persist public CEX wallet labels.

DeFiLlama's ``/cexs`` endpoint publishes exchange metadata and proof-of-reserves
links, not an ``addresses`` array. The actual public wallet owners are in the
open-source ``cex/index.js`` adapter config, so the updater joins the API
metadata with that config and parses its static ``owners`` lists. Unsupported
or computed JS expressions are deliberately ignored rather than guessed.
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
        name = names.get(_compact_name(slug), "")
        if not name:
            aliases = re.sub(r"[-_]+", " ", str(slug)).strip()
            name = aliases.title() or "CEX"
            # Common display-name corrections for DefiLlama adapter IDs.
            name = name.replace("Bsc", "BSC").replace("Us", "US")
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


async def _read_json(session: aiohttp.ClientSession, url: str, *, limit: int = MAX_SOURCE_BYTES):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=45)) as response:
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise ValueError("remote wallet source exceeds size limit")
        raw = await response.content.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("remote wallet source exceeds size limit")
    return json.loads(raw.decode("utf-8"))


async def _read_text(session: aiohttp.ClientSession, url: str, *, limit: int = MAX_SOURCE_BYTES):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=45)) as response:
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise ValueError("remote CEX config exceeds size limit")
        raw = await response.content.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("remote CEX config exceeds size limit")
    return raw.decode("utf-8")


async def fetch_defillama_cex_wallets(session: aiohttp.ClientSession | None = None) -> list[dict]:
    """Fetch public CEX owner addresses from DefiLlama's API + adapter config.

    The return value is a list of normalized chain/address/name records. The
    public ``/cexs`` API currently contains metadata (not address lists); the
    companion open-source adapter config contains the chain-scoped ``owners``.
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(headers={"User-Agent": "LiqScope-CEX-Wallets/1.0"})
    assert session is not None
    try:
        metadata = None
        try:
            metadata = await _read_json(session, DEFILLAMA_CEX_API, limit=5_000_000)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            log.warning("[cex_wallets] DeFiLlama metadata unavailable: %s", type(exc).__name__)
        try:
            config = await _read_text(session, DEFILLAMA_CEX_CONFIG)
            records = parse_defillama_cex_config(config, metadata)
            if records:
                return records
            raise ValueError("no supported static owner addresses in adapter config")
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, ValueError) as exc:
            log.warning("[cex_wallets] DeFiLlama adapter source unavailable: %s", type(exc).__name__)
        labels = await _read_json(session, ETHERSCAN_LABELS_URL, limit=50_000_000)
        names = []
        rows = metadata.get("cexs", []) if isinstance(metadata, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("name"):
                names.append(str(row["name"]))
        fallback = parse_etherscan_labels(labels, names)
        if not fallback:
            raise ValueError("wallet sources returned no recognized addresses")
        return fallback
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
            # Legacy format is address -> label and was EVM/ETH-only.
            if isinstance(payload, dict):
                ts = int(self.path.stat().st_mtime) if self.path.exists() else int(time.time())
                # The legacy JSON had no chain field and the old poller used
                # each address on every supported EVM chain. Preserve that exact
                # behaviour while making the scope explicit in the new schema.
                self._manual = [normalize_wallet_record({"chain": chain, "address": address,
                                                         "name": label, "source": "manual",
                                                         "updated_at": ts})
                                for address, label in payload.items()
                                for chain in sorted(EVM_CHAINS)]
                self._manual = [row for row in self._manual if row]
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
                if not fresh:
                    raise ValueError("No public wallet records were returned")
                with self._lock:
                    self._automatic = [row for item in fresh
                                       if (row := normalize_wallet_record(item))
                                       and row["source"] != "manual"]
                    self.updated_at = int(time.time())
                    self.last_source = "defillama" if any(
                        row["source"] == "defillama" for row in fresh) else "etherscan"
                    self.last_error = ""
                    self._save_locked(source=self.last_source)
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
                self.last_error = str(exc)[:200]
                log.warning("[cex_wallets] refresh failed: %s", type(exc).__name__)
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
