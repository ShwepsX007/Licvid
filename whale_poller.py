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
import re
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

import aiohttp

from whale_screener import (EVM_CHAINS, SOLANA_TOKENS, TOKENS, TRANSFER_TOPIC,
                            TRON_TOKENS, WhaleScreener, alchemy_key)
from alchemy_keys import AlchemyKeyStore, mask_key
from tron_address import tron_to_base58, tron_to_hex

log = logging.getLogger(__name__)
POLL_MODE = os.getenv("LIQSCOPE_WHALE_MODE", "realtime").strip().lower()
if POLL_MODE not in ("realtime", "economy"):
    POLL_MODE = "realtime"
HISTORY_INTERVAL_OPTIONS = (5, 10, 15, 30, 60)
SOLANA_HTTP_BASE = os.getenv("LIQSCOPE_SOLANA_HTTP", "https://solana-mainnet.g.alchemy.com/v2/")
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TRON_API_BASE = os.getenv("LIQSCOPE_TRONGRID_URL", "https://api.trongrid.io").rstrip("/")
TRON_EVENT_INTERVAL = 5.0
SOLANA_POLL_INTERVAL_SEC = 30.0
SOLANA_TOKEN_ACCOUNT_REFRESH_SEC = 600.0
SOLANA_SIGNATURE_LIMIT = 10
SOLANA_POLL_CONCURRENCY = 8
SOLANA_PROCESSED_SIGNATURE_LIMIT = 5000
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


class WhalePoller:
    def __init__(self, key: str, screener: WhaleScreener, *, interval: int | None = None,
                 monthly_cu: int = 10_000_000, state_file: Path | None = None,
                 endpoints: dict[str, str] | None = None,
                 key_store: AlchemyKeyStore | None = None,
                 trongrid_key_store: AlchemyKeyStore | None = None,
                 mode: str | None = None,
                 history_interval_min: int | None = None):
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
        self.mode = str(mode or POLL_MODE).strip().lower()
        if self.mode not in ("realtime", "economy"):
            self.mode = "realtime"
        default_interval = 5 if self.mode == "realtime" else 15
        requested_min = history_interval_min
        if requested_min is None:
            try:
                requested_min = int(os.getenv("LIQSCOPE_WHALE_HISTORY_INTERVAL_MIN", default_interval))
            except (TypeError, ValueError):
                requested_min = default_interval
        self.history_interval_min = (requested_min if requested_min in HISTORY_INTERVAL_OPTIONS
                                     else default_interval)
        self.interval = (self.history_interval_min * 60 if interval is None
                         else max(300, int(interval)))
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
        self.solana_status = {"network": "SOLANA", "provider": "alchemy",
                              "connected": False, "state": "waiting", "subscriptions": 0,
                              "submitted": 0, "active_wallets": 0, "mode": "cex_wallets",
                              "processing_state": "idle", "waiting_for_filters": False,
                              "token_accounts": 0, "poll_addresses": 0,
                              "events": 0, "last_success": 0.0, "error": ""}
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
                data.setdefault("active_key", "")
                data.setdefault("month", "")
                data.setdefault("cu", 0)
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"month": "", "cu": 0, "cursors": {}, "history_cursors": {},
                "key_usage": {}, "active_key": ""}

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

    def configure(self, *, mode: str, history_interval_min: int, monthly_cu: int) -> None:
        mode = str(mode or "").strip().lower()
        if mode not in ("realtime", "economy"):
            raise ValueError("Invalid whale mode")
        if history_interval_min not in HISTORY_INTERVAL_OPTIONS:
            raise ValueError("Invalid history interval")
        if not 1_000 <= int(monthly_cu) <= 20_000_000:
            raise ValueError("Invalid monthly CU limit")
        self.mode = mode
        self.history_interval_min = int(history_interval_min)
        self.interval = self.history_interval_min * 60
        self.monthly_cu = int(monthly_cu)
        self.wakeup.set()

    def _reserve(self, method: str, key_id: str = "legacy") -> None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if self.state["month"] != month:
            self.state.update(month=month, cu=0, key_usage={})
            self._save()
        cost = METHOD_CU[method]
        if self.state["cu"] + cost > self.monthly_cu:
            raise BudgetExhausted("global")
        keys = self._keys()
        # Soft per-key slice only controls rotation. All keys share the same
        # *global* budget, so adding keys cannot multiply the Free allowance.
        per_key = max(1000, self.monthly_cu // max(1, len(keys)))
        usage = self.state.setdefault("key_usage", {})
        if usage.get(key_id, 0) + cost > per_key:
            raise BudgetExhausted("key")
        self.state["cu"] += cost
        usage[key_id] = usage.get(key_id, 0) + cost
        self.state["active_key"] = key_id
        self._save()

    def key_status(self) -> list[dict]:
        public = (self.key_store.public() if self.key_store else
                  [{"id": "legacy", "hint": mask_key(self._fallback_key),
                    "source": "environment"}] if self._fallback_key else [])
        now = time.time()
        return [{**row,
                 "reserved_cu": self.state.get("key_usage", {}).get(row["id"], 0),
                 "state": ("cooldown" if self.key_cooldown.get(row["id"], 0) > now
                           else "active" if row["id"] == self.state.get("active_key")
                           else "ready"),
                 "reason": self.key_errors.get(row["id"], "")}
                for row in public]

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
        other = {c: "not_configured" for c in
                 ("BITCOIN", "BITCOINCASH", "LITECOIN", "SUI", "DOGECOIN")}
        other["SOLANA"] = sol["state"]
        other["TRON"] = tron["state"]
        return {"mode": self.mode, "interval_sec": self.interval,
                "history_interval_min": self.history_interval_min,
                "budget_cu": self.monthly_cu, "reserved_cu": used,
                "estimated_monthly_cu": estimate,
                "month": self.state.get("month", ""),
                "cursors": self.state.get("cursors", {}).copy(),
                "history_cursors": self.state.get("history_cursors", {}).copy(),
                "errors": self.errors.copy(), "supported": list(self.endpoints),
                "native_supported": [*list(NATIVE_NETWORKS), "SOLANA", "TRON"],
                "keys_configured": len(keys),
                "active_key_id": self.state.get("active_key", ""),
                "last_attempt": self.last_attempt.copy(),
                "last_success": self.last_success.copy(),
                "latest_heads": self.latest_heads.copy(),
                "evm_streams": {chain: row.copy() for chain, row in self.evm_streams.items()},
                "solana": sol, "tron": tron,
                "phase": ("no_key" if not keys else
                          "budget_exhausted" if used >= self.monthly_cu else
                          "error" if self.errors else
                          "ok" if self.last_success else "waiting"),
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
        if not keys:
            raise NoKeys("API-ключ не задан")
        active_id = self.state.get("active_key")
        index = next((i for i, (kid, _) in enumerate(keys) if kid == active_id), 0)
        now = time.time()
        deferred = False
        for offset in range(len(keys)):
            key_id, key = keys[(index + offset) % len(keys)]
            if (self.key_cooldown.get(key_id, 0) > now or
                    self.chain_cooldown.get((chain, key_id), 0) > now):
                deferred = True
                continue
            try:
                self._reserve(method, key_id)
            except BudgetExhausted as exc:
                if str(exc) == "global":
                    raise
                deferred = True
                self.key_errors[key_id] = "Локальная доля ключа исчерпана; переключение"
                continue
            # Test endpoints may be complete local URLs. Production endpoints
            # are prefixes; do not persist or expose URLs containing keys.
            url = self.endpoints[chain]
            if url.startswith("https://") and url.endswith("/v2/"):
                url += key
            try:
                async with session.post(url, json={
                        "jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                        timeout=aiohttp.ClientTimeout(total=25)) as response:
                    if response.status == 400:
                        try:
                            detail = await response.text(errors="replace")
                        except Exception as exc:  # preserve the RPC error even if body decoding fails
                            detail = f"[unable to read response body: {type(exc).__name__}]"
                        self._reset_cursor(chain)
                        log.error("[Alchemy] HTTP 400 network=%s method=%s response=%s",
                                  chain, method, self._redact_api_keys(detail, extra_secrets=(key,)))
                        raise PollError("HTTP 400")
                    if response.status in (401, 403, 429):
                        reason = ("Неверный ключ или сеть запрещена" if response.status in (401, 403)
                                  else "HTTP 429: ограничение Alchemy")
                        self.key_errors[key_id] = reason
                        if response.status == 403:
                            self.chain_cooldown[(chain, key_id)] = now + 900
                        else:
                            self.key_cooldown[key_id] = now + (300 if response.status == 429 else 900)
                        deferred = True
                        continue
                    if response.status != 200:
                        raise PollError(f"HTTP {response.status}")
                    data = await response.json()
                    if not isinstance(data, dict):
                        raise PollError("Некорректный ответ RPC")
                    error = data.get("error") or {}
                    if error:
                        self._reset_cursor(chain)
                        if isinstance(error, dict) and error.get("code") in (429, -32005):
                            self.key_errors[key_id] = "Ограничение RPC; переключение"
                            self.key_cooldown[key_id] = now + 300
                            deferred = True
                            continue
                        raise PollError("RPC отклонил запрос (возможна неподдерживаемая сеть/метод)")
                    if "result" not in data:
                        raise PollError("Пустой ответ RPC")
                    self.key_errors.pop(key_id, None)
                    self.chain_cooldown.pop((chain, key_id), None)
                    return data["result"]
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                # Never include aiohttp exception URL; it contains the key.
                raise PollError(type(exc).__name__) from None
        raise PollError("Все ключи исчерпаны, заблокированы или на паузе" if deferred
                        else "Нет доступного ключа")

    async def _logs(self, session, chain: str, start: int, end: int) -> None:
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
        if start > head:
            return
        end = min(head, start + 49_999)
        await self._asset_transfer_history(session, chain, start, end)
        cursors[chain] = end
        self._save()

    async def poll_chain(self, session: aiohttp.ClientSession, chain: str) -> None:
        head = await self._rpc(session, chain, "eth_blockNumber", [])
        if not isinstance(head, str) or not head.startswith("0x"):
            raise PollError("Malformed block number")
        latest = int(head, 16)
        self.latest_heads[chain] = latest
        cursor = self.state["cursors"].get(chain)
        if cursor is None or cursor == "latest":
            self.state["cursors"][chain] = latest
            self.state.setdefault("history_cursors", {}).setdefault(chain, latest)
            self._save()
            return  # no historical replay without a user-specified starting point
        # Bound backlog per pass, including the native index query. If down
        # for weeks, subsequent hourly passes catch up without a huge request.
        try:
            start = int(cursor) + 1
        except (TypeError, ValueError):
            self._reset_cursor(chain)
            return
        latest = min(latest, start + 49_999)
        # Address-indexed native query once per range (not once per log
        # chunk): otherwise Arbitrum's many small blocks exhaust Free CU.
        if start <= latest:
            await self._native(session, chain, start, latest)
        # One hour of Arbitrum can span 14,400+ blocks; bound each log query.
        for _ in range(100):
            if start > latest:
                break
            end = min(start + 999, latest)
            await self._logs(session, chain, start, end)
            self.state["cursors"][chain] = end
            self._save()
            start = end + 1
        # Remaining backlog stays on disk, never silently skipped. Transfer
        # history has an independent cursor so an asset-transfer error never
        # skips ranges that the CEX log poller has already completed.
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

    async def _run_evm_ws_chain(self, chain: str) -> None:
        """Subscribe to CEX-indexed token/native transfers using Alchemy WS."""
        state = self.evm_streams[chain]
        retry = 3.0
        while True:
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
            key_id, key = keys[0]
            host = ENDPOINTS.get(chain)
            if not host:
                state.update(connected=False, state="error", error="Unsupported EVM network")
                await asyncio.sleep(60)
                continue
            url = f"wss://{host}.g.alchemy.com/v2/{key}"
            try:
                async with aiohttp.ClientSession() as session:
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
                                await ws.send_json({"jsonrpc": "2.0", "id": sub_id,
                                    "method": "eth_subscribe", "params": ["logs", {
                                        "address": list(TOKENS[chain]), "topics": topics}]})
                                sub_id += 1
                                subscriptions += 1
                        if chain in MINED_TRANSACTION_CHAINS:
                            native_addresses = sorted(wallets)[:500]
                            filters = ([{"from": address} for address in native_addresses] +
                                       [{"to": address} for address in native_addresses])
                            await ws.send_json({"jsonrpc": "2.0", "id": sub_id,
                                "method": "eth_subscribe", "params": ["alchemy_minedTransactions", {
                                    "addresses": filters, "includeRemoved": False,
                                    "hashesOnly": False}]})
                            subscriptions += 1
                        state.update(connected=True, state="online",
                                     subscriptions=subscriptions, last_success=time.time(), error="")
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
            except asyncio.CancelledError:
                state.update(connected=False, state="stopped")
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
                state.update(connected=False, state="error", error=type(exc).__name__)
                state["reconnects"] = int(state.get("reconnects", 0)) + 1
            except Exception as exc:  # noqa: BLE001 — keep one provider outage isolated
                state.update(connected=False, state="error", error=type(exc).__name__)
                state["reconnects"] = int(state.get("reconnects", 0)) + 1
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
        if not keys:
            raise NoKeys("Alchemy API key is not configured")
        key_id, key = keys[0]
        self._reserve("solana_" + method, key_id)
        url = _solana_http_url(SOLANA_HTTP_BASE, key)
        try:
            async with session.post(url, json={"jsonrpc": "2.0", "id": 1,
                                               "method": method, "params": params},
                                    timeout=aiohttp.ClientTimeout(total=25)) as response:
                if response.status != 200:
                    detail = (await response.text())[:180]
                    if key:
                        detail = detail.replace(key, "[redacted]")
                    self.key_errors[key_id] = f"Solana RPC HTTP {response.status}: {detail}"
                    raise PollError(f"Solana RPC HTTP {response.status}: {detail}")
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            detail = str(exc).replace(key, "[redacted]") if key else str(exc)
            self.key_errors[key_id] = f"Solana RPC: {type(exc).__name__}"
            raise PollError(f"{type(exc).__name__}: {detail[:240]}") from None
        if not isinstance(payload, dict):
            raise PollError("Solana RPC returned a malformed response")
        if payload.get("error"):
            error = payload["error"]
            if isinstance(error, dict):
                detail = f"code={error.get('code', '?')}: {error.get('message', 'RPC error')}"
            else:
                detail = str(error)
            detail = self._solana_safe_text(detail)
            self.key_errors[key_id] = detail[:200]
            raise PollError(f"{method}: {detail[:240]}")
        self.key_errors.pop(key_id, None)
        return payload.get("result")

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
        """Poll CEX owners and discovered token accounts, then fetch each tx once."""
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
        semaphore = asyncio.Semaphore(SOLANA_POLL_CONCURRENCY)

        async def fetch_signatures(address: str):
            async with semaphore:
                rows = await self._solana_rpc(session, "getSignaturesForAddress", [address, {
                    "commitment": "confirmed", "limit": SOLANA_SIGNATURE_LIMIT}])
            if not isinstance(rows, list):
                raise PollError("Malformed getSignaturesForAddress response")
            return address, rows

        responses = await asyncio.gather(*(fetch_signatures(address) for address in address_rows),
                                         return_exceptions=True)
        signature_rows: dict[str, object] = {}
        first_error = ""
        successful_addresses = 0
        for response in responses:
            if isinstance(response, BaseException):
                if isinstance(response, BudgetExhausted):
                    raise response
                if isinstance(response, asyncio.CancelledError):
                    raise response
                if not first_error:
                    first_error = self._solana_safe_text(
                        str(response) or type(response).__name__)[:180]
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
            raise PollError(first_error or "No Solana signature poll succeeded")

        def signature_order(item):
            try:
                return float(item[1] or 0)
            except (TypeError, ValueError, OverflowError):
                return 0.0

        tracked_owners = set(active_owners)
        for signature, _block_time in sorted(signature_rows.items(), key=signature_order):
            if (signature in self._solana_processed_signatures or
                    signature in self._solana_signature_inflight):
                continue
            self._solana_signature_inflight.add(signature)
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
            except Exception as exc:  # isolate one unavailable transaction from other CEX wallets
                if not first_error:
                    first_error = self._solana_safe_text(
                        str(exc) or type(exc).__name__)[:180]
                log.warning("[Solana] Transaction lookup failed: %s",
                            self._solana_safe_text(str(exc) or type(exc).__name__)[:180])
            finally:
                self._solana_signature_inflight.discard(signature)

        self.solana_status.update(connected=True, state="online", mode="cex_poll",
                                  processing_state="polling", waiting_for_filters=False,
                                  last_success=time.time(), error=first_error)

    async def _solana_run_cex_poll(self, session, wallets: dict[str, str]) -> None:
        owners = list(wallets.items())[:SOLANA_MAX_WALLETS]
        self.solana_status.update(connected=False, state="connecting", mode="cex_poll",
                                  subscriptions=0, submitted=0, active_wallets=len(owners),
                                  token_accounts=0, poll_addresses=0,
                                  processing_state="checking_health", waiting_for_filters=False,
                                  error="")
        health = await self._solana_rpc(session, "getHealth", [])
        if health != "ok":
            raise PollError("Alchemy Solana getHealth did not return ok")
        token_accounts = await self._solana_refresh_token_accounts(session, owners)
        self._solana_token_accounts = token_accounts
        self.solana_status.update(connected=True, state="online", mode="cex_poll",
                                  subscriptions=0, submitted=0, active_wallets=len(owners),
                                  token_accounts=sum(len(rows) for mints in token_accounts.values()
                                                     for rows in mints.values()),
                                  processing_state="polling", waiting_for_filters=False,
                                  last_success=time.time(), error="")
        loop = asyncio.get_running_loop()
        last_account_refresh = loop.time()
        while True:
            poll_started = loop.time()
            current = list(self.screener.wallet_addresses("SOLANA").items())[:SOLANA_MAX_WALLETS]
            if current != owners:
                return
            if loop.time() - last_account_refresh >= SOLANA_TOKEN_ACCOUNT_REFRESH_SEC:
                token_accounts = await self._solana_refresh_token_accounts(session, owners)
                self._solana_token_accounts = token_accounts
                last_account_refresh = loop.time()
            await self._solana_poll_wallets_once(session, owners, token_accounts)
            delay = max(0.0, SOLANA_POLL_INTERVAL_SEC - (loop.time() - poll_started))
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
        while True:
            keys = self._keys()
            wallets = self.screener.wallet_addresses("SOLANA")
            owners = list(wallets.items())[:SOLANA_MAX_WALLETS]
            mode = "cex_poll" if owners else "waiting_for_filters"
            if not keys:
                self.solana_status.update(connected=False, state="waiting",
                    subscriptions=0, submitted=0, active_wallets=len(owners), mode=mode,
                    processing_state="waiting_for_key", waiting_for_filters=False,
                    error="Alchemy API key is not configured")
                await asyncio.sleep(5)
                continue

            returned_for_registry_change = False
            try:
                async with aiohttp.ClientSession() as session:
                    if owners:
                        await self._solana_run_cex_poll(session, dict(owners))
                    else:
                        await self._solana_wait_for_filters(session)
                        if not self.screener.wallet_addresses("SOLANA"):
                            await asyncio.sleep(SOLANA_POLL_INTERVAL_SEC)
                returned_for_registry_change = True
                retry = 3.0
            except asyncio.CancelledError:
                self.solana_status.update(connected=False, state="stopped")
                raise
            except BudgetExhausted as exc:
                self.solana_status.update(connected=False, state="budget_exhausted",
                    mode=mode, waiting_for_filters=not bool(owners),
                    error=self._solana_safe_text(str(exc) or "Alchemy CU budget exhausted")[:200])
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
                    PollError) as exc:
                self.solana_status.update(connected=False, state="error", mode=mode,
                    waiting_for_filters=False, error=self._solana_safe_text(
                        str(exc) or type(exc).__name__)[:200])
                self._log_solana_exception(exc)
            except Exception as exc:  # noqa: BLE001 — keep Solana provider failures isolated
                self.solana_status.update(connected=False, state="error", mode=mode,
                    waiting_for_filters=False, error=self._solana_safe_text(
                        str(exc) or type(exc).__name__)[:200])
                self._log_solana_exception(exc)
            if returned_for_registry_change:
                await asyncio.sleep(0)
                continue
            await asyncio.sleep(min(60.0, retry))
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
        async with aiohttp.ClientSession() as session:
            while True:
                for chain in self.endpoints:
                    if not self._keys():
                        self.errors[chain] = "API-ключ не задан: добавьте его в админке"
                        break
                    try:
                        self.last_attempt[chain] = time.time()
                        await self.poll_chain(session, chain)
                        self.last_success[chain] = time.time()
                        self.errors.pop(chain, None)
                    except asyncio.CancelledError:
                        raise
                    except BudgetExhausted:
                        self.errors[chain] = "Месячный бюджет скринера исчерпан"
                        break
                    except (PollError, ValueError, OSError) as exc:
                        self.errors[chain] = str(exc)[:100]
                    await asyncio.sleep(0.01)
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass
                self.wakeup.clear()
