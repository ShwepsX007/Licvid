"""Budget-limited hourly Alchemy poller for *known EVM exchange addresses*.

Not a full-chain indexer. Cursor and conservative CU reservations survive restarts.
The monthly cap applies only to this process, not to other use of the Alchemy app.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import aiohttp

from whale_screener import TOKENS, TRANSFER_TOPIC, WhaleScreener, alchemy_key
from alchemy_keys import AlchemyKeyStore

ENDPOINTS = {
    "ETH": "eth-mainnet", "BNB": "bnb-mainnet", "POLYGON": "polygon-mainnet",
    "ARBITRUM": "arb-mainnet", "BASE": "base-mainnet",
}
# The Transfers API documents these four networks, but does not promise BNB;
# token logs are still polled there. HyperEVM/Hyperliquid has no Alchemy endpoint.
NATIVE_INDEXED = {"ETH": "ETH", "POLYGON": "POL", "ARBITRUM": "ETH", "BASE": "ETH"}
METHOD_CU = {"eth_blockNumber": 10, "eth_getLogs": 60, "alchemy_getAssetTransfers": 120}


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


class BudgetExhausted(Exception):
    pass


class PollError(Exception):
    pass


class NoKeys(PollError):
    pass


class WhalePoller:
    def __init__(self, key: str, screener: WhaleScreener, *, interval: int = 3600,
                 monthly_cu: int = 10_000_000, state_file: Path | None = None,
                 endpoints: dict[str, str] | None = None,
                 key_store: AlchemyKeyStore | None = None):
        self.screener = screener
        self.key_store = key_store
        self._fallback_key = alchemy_key(key) if key and key_store is None else ""
        self.wakeup = asyncio.Event()
        self.last_attempt: dict[str, float] = {}
        self.last_success: dict[str, float] = {}
        self.latest_heads: dict[str, int] = {}
        self.key_errors: dict[str, str] = {}
        self.key_cooldown: dict[str, float] = {}
        self.chain_cooldown: dict[tuple[str, str], float] = {}
        self.interval = max(600, interval)
        # Deliberate reserve: Alchemy Free is 30M CU for the whole app, not only
        # this collector. A custom cap can be *lower*, not greater than 30M.
        self.monthly_cu = min(30_000_000, max(1, monthly_cu))
        self.state_file = state_file or Path(__file__).resolve().parent / "data/whale_poller.json"
        self.endpoints = endpoints or {
            c: f"https://{host}.g.alchemy.com/v2/" for c, host in ENDPOINTS.items()
        }
        self.state = self._load()
        self.errors: dict[str, str] = {}

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("cursors"), dict):
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"month": "", "cu": 0, "cursors": {}, "key_usage": {}, "active_key": ""}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, self.state_file)

    def _reset_cursor(self, chain: str) -> None:
        """Discard a rejected block range; the next poll seeds from its head."""
        self.state.setdefault("cursors", {})[chain] = "latest"
        self._save()

    def _keys(self) -> list[tuple[str, str]]:
        return self.key_store.keys() if self.key_store else (
            [("legacy", self._fallback_key)] if self._fallback_key else [])

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
                  [{"id": "legacy", "hint": "••••" + self._fallback_key[-4:],
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
        return {"mode": "cex_only", "interval_sec": self.interval,
                "budget_cu": self.monthly_cu, "reserved_cu": self.state["cu"],
                "month": self.state["month"], "cursors": self.state["cursors"].copy(),
                "errors": self.errors.copy(), "supported": list(self.endpoints),
                "keys_configured": len(keys),
                "active_key_id": self.state.get("active_key", ""),
                "last_attempt": self.last_attempt.copy(),
                "last_success": self.last_success.copy(),
                "latest_heads": self.latest_heads.copy(),
                "phase": ("no_key" if not keys else
                          "budget_exhausted" if self.state["cu"] >= self.monthly_cu else
                          "error" if self.errors else
                          "ok" if self.last_success else "waiting"),
                "other_networks": {c: "not_configured" for c in
                                   ("SOLANA", "BITCOIN", "BITCOINCASH", "LITECOIN",
                                    "TRON", "SUI", "DOGECOIN")}}

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
        wallets = ["0x" + "0" * 24 + a[2:] for a in self.screener.wallets]
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
            for log in logs:
                if not isinstance(log, dict):
                    continue
                topic_rows = log.get("topics") or []
                if len(topic_rows) != 3 or not any(
                        isinstance(topic, str) and len(topic) == 66 and
                        ("0x" + topic[-40:].lower()) in self.screener.wallets
                        for topic in topic_rows[1:]):
                    continue
                key = (log.get("transactionHash"), log.get("logIndex"))
                if key not in seen:
                    seen.add(key)
                    await self.screener.handle_log(chain, log)

    async def _native(self, session, chain: str, start: int, end: int) -> None:
        if chain not in NATIVE_INDEXED:
            return
        asset = NATIVE_INDEXED[chain]
        # Full pagination; the cursor must never advance on partial responses.
        for address in self.screener.wallets:
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
                        if not (sender in self.screener.wallets or recipient in self.screener.wallets):
                            continue
                        try:
                            amount = Decimal(str(tx["value"]))
                            if not amount.is_finite() or amount <= 0:
                                continue
                            wei = int(amount * 10**18)
                        except (InvalidOperation, KeyError, ValueError, TypeError):
                            continue
                        await self.screener._emit(chain, str(tx["hash"]).lower(), "native",
                                                  asset, wei, 18, sender, recipient)
                    page = result.get("pageKey")
                    if not page:
                        break

    async def poll_chain(self, session: aiohttp.ClientSession, chain: str) -> None:
        head = await self._rpc(session, chain, "eth_blockNumber", [])
        if not isinstance(head, str) or not head.startswith("0x"):
            raise PollError("Malformed block number")
        latest = int(head, 16)
        self.latest_heads[chain] = latest
        cursor = self.state["cursors"].get(chain)
        if cursor is None or cursor == "latest":
            self.state["cursors"][chain] = latest
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
        # Remaining backlog stays on disk, never silently skipped.

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
