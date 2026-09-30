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
SOLANA_WS_BASE = os.getenv("LIQSCOPE_SOLANA_WS", "wss://solana-mainnet.g.alchemy.com/v2/")
SOLANA_HTTP_BASE = os.getenv("LIQSCOPE_SOLANA_HTTP", "https://solana-mainnet.g.alchemy.com/v2/")
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TRON_API_BASE = os.getenv("LIQSCOPE_TRONGRID_URL", "https://api.trongrid.io").rstrip("/")
TRON_EVENT_INTERVAL = 5.0
SOLANA_WS_PENDING_BATCH = 100  # stay below Alchemy's 200 pending-request limit
TRON_USD_CONTRACTS = dict(TRON_TOKENS)
SOLANA_BLOCKS_PER_SEC = {"ETH": 1 / 12, "BNB": 1 / 3, "POLYGON": 0.5,
                         "ARBITRUM": 4.0, "BASE": 0.5}
SOLANA_MAX_WALLETS = min(333, max(1, int(os.getenv("LIQSCOPE_SOLANA_MAX_WALLETS", "250"))))
METHOD_CU = {"eth_blockNumber": 10, "eth_getLogs": 60,
             "alchemy_getAssetTransfers": 120,
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
                              "submitted": 0, "active_wallets": 0,
                              "events": 0, "last_success": 0.0, "error": ""}
        self.tron_status = {"network": "TRON", "provider": "trongrid",
                            "connected": False, "state": "waiting", "events": 0,
                            "wallets": 0, "last_success": 0.0, "last_block": 0, "error": ""}
        self._solana_last_signature: dict[str, str] = {}
        self._solana_balance: dict[str, int] = {}
        self._solana_inflight: set[str] = set()
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
                        self._reset_cursor(chain)
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
        queries = [("external", None), ("erc20", list(TOKENS[chain]))]
        for category, contracts in queries:
            page = None
            pages = 0
            while True:
                query = {"fromBlock": to_hex_block(start), "toBlock": to_hex_block(end),
                         "category": [category], "withMetadata": True,
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
                    if category == "external":
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
        message = str(value).strip()
        for _identifier, secret in self._keys():
            if secret:
                message = message.replace(secret, "[redacted]")
        return message

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
        timestamp = tx.get("blockTime")
        count = 0
        for index, instruction in enumerate(self._solana_instructions(tx)):
            parsed = instruction.get("parsed") or {}
            info = parsed.get("info") or {}
            if instruction.get("program") != "system" or parsed.get("type") != "transfer":
                continue
            sender, recipient = str(info.get("source") or ""), str(info.get("destination") or "")
            if owner not in (sender, recipient):
                continue
            try:
                lamports = int(info.get("lamports") or 0)
            except (TypeError, ValueError):
                continue
            if lamports <= 0:
                continue
            amount = lamports / 1_000_000_000
            price = self.screener._price("SOL")
            if price is None:
                continue
            if await self.screener.record_transfer(
                    "SOLANA", signature, f"native:{index}", "SOL", amount,
                    amount * price, sender, recipient, timestamp=timestamp,
                    source="realtime"):
                count += 1
        return count

    async def _solana_process_token(self, tx: dict, owner: str, mint: str) -> int:
        if not isinstance(tx, dict) or (tx.get("meta") or {}).get("err"):
            return 0
        signature = self._solana_tx_signature(tx)
        if not signature:
            return 0
        meta = tx.get("meta") or {}
        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []

        def amount_map(rows):
            total, owners = 0, {}
            for row in rows:
                if not isinstance(row, dict) or row.get("mint") != mint:
                    continue
                row_owner = str(row.get("owner") or "")
                account_index = row.get("accountIndex")
                token_amount = row.get("uiTokenAmount") or {}
                try:
                    raw = int(token_amount.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
                if row_owner:
                    owners[str(account_index)] = row_owner
                if row_owner == owner:
                    total += raw
            return total, owners

        before, before_owners = amount_map(pre)
        after, after_owners = amount_map(post)
        delta = after - before
        if not delta:
            return 0
        symbol, decimals = SOLANA_TOKENS.get(mint, ("", 6))
        if not symbol:
            return 0

        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []
        account_by_index = {}
        for i, key in enumerate(account_keys):
            if isinstance(key, dict):
                account_by_index[str(i)] = str(key.get("pubkey") or "")
            else:
                account_by_index[str(i)] = str(key)
        account_owners = {**before_owners, **after_owners}
        owner_by_key = {account_by_index[idx]: value for idx, value in account_owners.items()
                        if idx in account_by_index and account_by_index[idx]}
        sender, recipient = ("", owner) if delta > 0 else (owner, "")
        for instruction in self._solana_instructions(tx):
            parsed = instruction.get("parsed") or {}
            if parsed.get("type") not in ("transfer", "transferChecked"):
                continue
            info = parsed.get("info") or {}
            source_account = str(info.get("source") or "")
            destination_account = str(info.get("destination") or "")
            source_owner = owner_by_key.get(source_account, "")
            destination_owner = owner_by_key.get(destination_account, "")
            if destination_owner == owner and delta > 0:
                sender, recipient = source_owner or source_account, owner
                break
            if source_owner == owner and delta < 0:
                sender, recipient = owner, destination_owner or destination_account
                break
        amount = abs(delta) / (10 ** decimals)
        price = self.screener._price(symbol)
        if price is None:
            return 0
        if await self.screener.record_transfer(
                "SOLANA", signature, f"spl:{owner}:{mint}", symbol, amount,
                amount * price, sender, recipient, timestamp=tx.get("blockTime"),
                source="realtime"):
            return 1
        return 0

    async def _solana_recent_transactions(self, session, address: str, owner: str,
                                          mint: str | None = None) -> None:
        lock_key = address + (":" + mint if mint else ":native")
        if lock_key in self._solana_inflight:
            return
        self._solana_inflight.add(lock_key)
        try:
            rows = await self._solana_rpc(session, "getSignaturesForAddress", [address, {
                "commitment": "confirmed", "limit": 20}])
            if not isinstance(rows, list):
                return
            previous = self._solana_last_signature.get(lock_key)
            selected = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                signature = str(row.get("signature") or "")
                if not signature:
                    continue
                if signature == previous:
                    break
                if previous is None:
                    block_time = row.get("blockTime")
                    if block_time and float(block_time) < time.time() - 600:
                        continue
                if not row.get("err"):
                    selected.append(signature)
            for signature in reversed(selected[:10]):
                tx = await self._solana_rpc(session, "getTransaction", [signature, {
                    "encoding": "jsonParsed", "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0}])
                if not isinstance(tx, dict):
                    continue
                if mint:
                    self.solana_status["events"] += await self._solana_process_token(tx, owner, mint)
                else:
                    self.solana_status["events"] += await self._solana_process_native(tx, owner)
            if rows and isinstance(rows[0], dict) and rows[0].get("signature"):
                self._solana_last_signature[lock_key] = str(rows[0]["signature"])
            self.solana_status["last_success"] = time.time()
            self.solana_status["error"] = ""
        except BudgetExhausted as exc:
            self.solana_status.update(state="budget_exhausted",
                                       error=self._solana_safe_text(exc)[:200])
            self._log_solana_exception(exc)
        except (PollError, ValueError, TypeError, KeyError, aiohttp.ClientError,
                asyncio.TimeoutError) as exc:
            self.solana_status["error"] = self._solana_safe_text(
                str(exc) or type(exc).__name__)[:200]
            self._log_solana_exception(exc)
        finally:
            self._solana_inflight.discard(lock_key)

    async def run_solana(self) -> None:
        """Monitor SPL USDT/USDC token accounts and native SOL CEX accounts.

        Alchemy's Solana websocket has a 200-request in-flight limit and a
        1,000-subscription limit per connection. Subscribe in acknowledged
        batches so a large CEX registry cannot silently stall setup.
        """
        retry = 3.0
        while True:
            keys = self._keys()
            wallets = self.screener.wallet_addresses("SOLANA")
            if not keys:
                self.solana_status.update(connected=False, state="waiting",
                    subscriptions=0, submitted=0, active_wallets=0,
                    error="Alchemy API key is not configured")
                await asyncio.sleep(5)
                continue
            if not wallets:
                self.solana_status.update(connected=False, state="waiting",
                    subscriptions=0, submitted=0, active_wallets=0,
                    error="No indexed Solana CEX wallets")
                log.warning("[Solana] Ожидание: в реестре нет CEX-адресов Solana")
                await asyncio.sleep(30)
                continue

            key_id, key = keys[0]
            url = _solana_http_url(SOLANA_WS_BASE, key)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, heartbeat=30, receive_timeout=90) as ws:
                        owners = list(wallets.items())[:SOLANA_MAX_WALLETS]
                        requests = []
                        for owner, _name in owners:
                            requests.append(("accountSubscribe", [owner, {
                                "commitment": "confirmed", "encoding": "base64"}],
                                ("native", owner, None)))
                            for mint in SOLANA_TOKENS:
                                requests.append(("programSubscribe", [SPL_TOKEN_PROGRAM, {
                                    "commitment": "confirmed", "encoding": "base64",
                                    "filters": [{"dataSize": 165},
                                        {"memcmp": {"offset": 0, "bytes": mint}},
                                        {"memcmp": {"offset": 32, "bytes": owner}}]}],
                                    ("spl", owner, mint)))

                        pending: dict[int, tuple[str, str, str | None]] = {}
                        active: dict[int, tuple[str, str, str | None]] = {}
                        request_id = 1
                        accepted = 0
                        rejected = 0

                        async def handle_message(message) -> None:
                            nonlocal accepted, rejected
                            if message.type != aiohttp.WSMsgType.TEXT:
                                if message.type in (aiohttp.WSMsgType.CLOSED,
                                                    aiohttp.WSMsgType.ERROR):
                                    detail = getattr(message, "data", "")
                                    if isinstance(detail, BaseException):
                                        detail = f"{type(detail).__name__}: {detail}"
                                    detail = str(detail or f"close code={ws.close_code}")[:160]
                                    raise PollError(f"Solana WebSocket closed: {detail}")
                                return
                            try:
                                payload = json.loads(message.data)
                            except (TypeError, ValueError):
                                return
                            if not isinstance(payload, dict):
                                return
                            if "id" in payload:
                                try:
                                    response_id = int(payload.get("id"))
                                except (TypeError, ValueError):
                                    return
                                target = pending.pop(response_id, None)
                                if not target:
                                    return
                                result = payload.get("result")
                                if isinstance(result, int) and not payload.get("error"):
                                    active[result] = target
                                    accepted += 1
                                else:
                                    rejected += 1
                                    error = payload.get("error") or {}
                                    detail = error.get("message", error) if isinstance(error, dict) else error
                                    detail = str(detail or "subscription rejected")[:180]
                                    detail = self._solana_safe_text(detail)[:180]
                                    self.solana_status["error"] = detail
                                    log.error("[Solana] subscription rejected: %s", detail)
                                return

                            method = payload.get("method")
                            if method not in ("accountNotification", "programNotification"):
                                return
                            params = payload.get("params") or {}
                            target = active.get(params.get("subscription"))
                            if not target:
                                return
                            kind, owner, mint = target
                            result = params.get("result") or {}
                            if kind == "native":
                                account = result.get("value") or {}
                                try:
                                    balance = int(account.get("lamports") or 0)
                                except (TypeError, ValueError):
                                    return
                                old = self._solana_balance.get(owner)
                                self._solana_balance[owner] = balance
                                if old is not None and old != balance:
                                    await self._solana_recent_transactions(session, owner, owner)
                            else:
                                account = result.get("value") or {}
                                pubkey = str(account.get("pubkey") or "")
                                if pubkey and mint:
                                    await self._solana_recent_transactions(session, pubkey, owner, mint)
                            self.solana_status.update(last_success=time.time(), error="")

                        for offset in range(0, len(requests), SOLANA_WS_PENDING_BATCH):
                            batch = requests[offset:offset + SOLANA_WS_PENDING_BATCH]
                            batch_ids = []
                            for method, params, target in batch:
                                pending[request_id] = target
                                batch_ids.append(request_id)
                                await ws.send_json({"jsonrpc": "2.0", "id": request_id,
                                                    "method": method, "params": params})
                                request_id += 1
                            self.solana_status.update(submitted=request_id - 1,
                                                      active_wallets=len(owners))
                            while any(item in pending for item in batch_ids):
                                message = await ws.receive(timeout=20)
                                await handle_message(message)

                        if accepted <= 0:
                            detail = self.solana_status.get("error") or "All Solana subscriptions were rejected"
                            raise PollError(f"{detail} ({rejected}/{len(requests)} rejected)")
                        self.solana_status.update(connected=True, state="online",
                            subscriptions=accepted, submitted=len(requests),
                            active_wallets=len(owners), error=(
                                f"{rejected} Solana subscription(s) rejected" if rejected else ""))
                        retry = 3.0
                        while True:
                            message = await ws.receive()
                            await handle_message(message)
            except asyncio.CancelledError:
                self.solana_status.update(connected=False, state="stopped")
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
                    PollError) as exc:
                self.solana_status.update(connected=False, state="error",
                                              error=self._solana_safe_text(str(exc) or type(exc).__name__)[:200])
                self._log_solana_exception(exc)
            except Exception as exc:  # noqa: BLE001 — keep a Solana provider outage isolated
                self.solana_status.update(connected=False, state="error",
                                              error=self._solana_safe_text(str(exc) or type(exc).__name__)[:200])
                self._log_solana_exception(exc)
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
