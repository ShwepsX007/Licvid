"""Shared whale feeds: Alchemy EVM streams plus native Hyperliquid Core trades.

The EVM path counts confirmed top-level native transfers and Transfer logs of
allowlisted contracts. Hyperliquid uses its public ``meta`` + ``trades`` API and
emits large fills as trades (not as deposits/withdrawals). Internal EVM calls,
pending transactions and historical backfill are not counted.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import socket
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")
HASH = re.compile(r"^0x[0-9a-f]{64}$")

# Hyperliquid Core is not an EVM network and must never be routed through the
# Alchemy endpoints below. Its public API has no credential requirement.
HYPERLIQUID_WS = os.getenv("LIQSCOPE_HL_WS", "wss://api.hyperliquid.xyz/ws")
HYPERLIQUID_REST = os.getenv("LIQSCOPE_HL_REST", "https://api.hyperliquid.xyz")
HL_WHALE_ENABLED = os.getenv("LIQSCOPE_HL_WHALE_STREAM", "1").strip().lower() not in (
    "0", "off", "no", "false")
HL_WHALE_SUB_GAP_SEC = max(0.0, float(os.getenv("LIQSCOPE_HL_WHALE_SUB_GAP_MS", "80")) / 1000)
HL_WHALE_PING_SEC = max(1.0, float(os.getenv("LIQSCOPE_HL_WHALE_PING_SEC", "20")))
HL_WHALE_FRESH_SEC = max(1.0, float(os.getenv("LIQSCOPE_HL_WHALE_FRESH_SEC", "120")))
HL_WHALE_UNIVERSE_TTL_SEC = max(60.0, float(os.getenv("LIQSCOPE_HL_WHALE_UNIVERSE_TTL_SEC", "3600")))
HL_USER_AGENT = os.getenv("LIQSCOPE_HL_UA", "LiqScope-Terminal/4.1")

# Decimals are contract-specific, NOT symbol-specific (BSC USDT is 18, ETH is 6).
TOKENS = {
    "ETH": {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
        "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    },
    "BNB": {
        "0x55d398326f99059ff775485246999027b3197955": ("USDT", 18),
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18),
        "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3": ("DAI", 18),
        # BTCB is the established BNB Chain BTC-pegged token; not ETH WBTC.
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": ("BTCB", 18),
    },
    "POLYGON": {
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": ("USDT", 6),
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USDC", 6),
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": ("USDC", 6),
    },
    "ARBITRUM": {
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USDT", 6),
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", 6),
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18),
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": ("WBTC", 8),
    },
    "BASE": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
    },
}

# EVM token contracts live only in TOKENS. Hyperliquid is a native trade feed,
# but still needs its own bounded dedup bucket alongside the EVM networks.
TRACKED_NETWORKS = (*TOKENS, "HYPERLIQUID")


def alchemy_key(value: str) -> str:
    """Accept a bare key or an Alchemy v2 URL; never embed an endpoint as a key.

    The URL's network is deliberately ignored: the same credential is used
    with each supported network's own endpoint. Do not include secrets in errors.
    """
    value = value.strip()
    if "://" in value or "/" in value or "?" in value or "#" in value:
        try:
            url = urlsplit(value)
            if (url.scheme not in ("https", "wss")
                    or not re.fullmatch(r"[a-z0-9-]+\.g\.alchemy\.com", url.hostname or "")
                    or url.username or url.password or url.port
                    or url.query or url.fragment
                    or not re.fullmatch(r"/v2/[A-Za-z0-9._~-]+", url.path)):
                raise ValueError
            value = url.path.removeprefix("/v2/")
        except ValueError:
            raise ValueError("Provide an Alchemy API key or an HTTPS/WSS Alchemy /v2/ URL") from None
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", value):
        raise ValueError("Provide an Alchemy API key or an HTTPS/WSS Alchemy /v2/ URL")
    return value


def load_wallets(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("cex_wallets.json must be an address-to-label object")
    wallets = {}
    for addr, label in raw.items():
        if not isinstance(addr, str) or not ADDRESS.fullmatch(addr.lower()):
            raise ValueError("Invalid CEX address")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Invalid CEX label")
        wallets[addr.lower()] = label.strip()
    return wallets


class WhaleScreener:
    def __init__(self, api_key: str, price_fn, broadcast,
                 wallet_path: Path | None = None, min_usd: float = 100_000,
                 endpoints: dict[str, str] | None = None,
                 bnb_api_key: str | None = None,
                 hl_ws: str | None = None, hl_rest: str | None = None,
                 hl_enabled: bool | None = None, hl_sub_gap: float | None = None,
                 hl_ping_sec: float | None = None, hl_fresh_sec: float | None = None):
        self.price_fn = price_fn
        self.broadcast = broadcast
        self.wallets = load_wallets(wallet_path or Path(__file__).resolve().parent / "data/cex_wallets.json")
        self.min_usd = min_usd
        self.events: deque[dict] = deque(maxlen=100)
        self.seen: dict[str, deque[str]] = {chain: deque(maxlen=4000) for chain in TRACKED_NETWORKS}
        self.seen_set: dict[str, set[str]] = {chain: set() for chain in TRACKED_NETWORKS}
        self._pending_blocks: dict[str, dict[int, str]] = {chain: {} for chain in TOKENS}
        self._next_rpc_id = 10
        if endpoints is None:
            key = alchemy_key(api_key)
            bnb_key = alchemy_key(bnb_api_key) if bnb_api_key else key
            self.endpoints = {
                "ETH": f"wss://eth-mainnet.g.alchemy.com/v2/{key}",
                "BNB": f"wss://bnb-mainnet.g.alchemy.com/v2/{bnb_key}",
            }
        else:
            self.endpoints = endpoints
        if "HYPERLIQUID" in self.endpoints:
            raise ValueError("Hyperliquid must use its native API, never an Alchemy endpoint")
        self.hl_ws = (hl_ws or HYPERLIQUID_WS).rstrip("/")
        self.hl_rest = (hl_rest or HYPERLIQUID_REST).rstrip("/")
        self.hl_enabled = HL_WHALE_ENABLED if hl_enabled is None else bool(hl_enabled)
        self.hl_sub_gap = max(0.0, HL_WHALE_SUB_GAP_SEC if hl_sub_gap is None else hl_sub_gap)
        self.hl_ping_sec = max(1.0, HL_WHALE_PING_SEC if hl_ping_sec is None else hl_ping_sec)
        self.hl_fresh_sec = max(1.0, HL_WHALE_FRESH_SEC if hl_fresh_sec is None else hl_fresh_sec)
        self.hl_status = {
            "network": "HYPERLIQUID", "provider": "native_api",
            "enabled": self.hl_enabled, "connected": False,
            "state": "waiting" if self.hl_enabled else "disabled",
            "market_count": 0, "subscriptions_sent": 0,
            "subscriptions_acked": 0, "trades_seen": 0,
            "events": 0, "last_message_ts": 0.0,
            "last_trade_ts": 0.0, "last_event_ts": 0.0,
            "reconnects": 0, "error": "",
        }

    def history(self, min_usd: float = 100_000, chain: str = "ALL", limit: int = 50) -> list[dict]:
        return [ev.copy() for ev in reversed(self.events)
                if ev["usd"] >= min_usd and (chain == "ALL" or ev["chain"] == chain)][:limit]

    def native_status(self) -> dict:
        """Health of the credential-free Hyperliquid Core feed."""
        return self.hl_status.copy()

    def _price(self, symbol: str) -> float | None:
        # Stablecoin values are nominal USD if no quote is available.
        if symbol in ("USDT", "USDC", "DAI"):
            pair = symbol + "_USDT"
            if symbol == "USDT":
                return 1.0
            price = self.price_fn(pair)
            return float(price) if price and math.isfinite(float(price)) and float(price) > 0 else 1.0
        pair = ("BTC" if symbol in ("BTCB", "WBTC") else symbol) + "_USDT"
        price = self.price_fn(pair)
        if price is None:
            return None
        price = float(price)
        return price if math.isfinite(price) and price > 0 else None

    def _remember(self, chain: str, key: str) -> bool:
        if key in self.seen_set[chain]:
            return False
        items = self.seen[chain]
        if len(items) == items.maxlen:
            self.seen_set[chain].discard(items.popleft())
        items.append(key)
        self.seen_set[chain].add(key)
        return True

    async def _emit(self, chain: str, tx_hash: str, index: str, symbol: str,
                    raw_amount: int, decimals: int, sender: str, recipient: str) -> None:
        if raw_amount <= 0:
            return
        price = self._price(symbol)
        if price is None:
            return  # never guess the USD value of a volatile asset
        amount = raw_amount / (10 ** decimals)
        usd = amount * price
        if not math.isfinite(usd) or usd < self.min_usd:
            return
        key = tx_hash + ":" + index
        if not self._remember(chain, key):
            return
        from_label = self.wallets.get(sender, "")
        to_label = self.wallets.get(recipient, "")
        direction = "transfer"
        if to_label and not from_label:
            direction = "inflow"
        elif from_label and not to_label:
            direction = "outflow"
        event = {"chain": chain, "hash": tx_hash, "log_index": index,
                 "timestamp": int(time.time()), "symbol": symbol,
                 "amount": amount, "usd": round(usd, 2), "from": sender,
                 "to": recipient, "from_label": from_label, "to_label": to_label,
                 "direction": direction}
        self.events.append(event)
        await self.broadcast({"type": "whale_tx", **event})

    async def handle_log(self, chain: str, log: dict) -> None:
        if log.get("removed") or chain not in TOKENS:
            return
        token = TOKENS[chain].get(str(log.get("address", "")).lower())
        topics = log.get("topics") or []
        tx_hash = str(log.get("transactionHash", "")).lower()
        if (not token or len(topics) != 3 or str(topics[0]).lower() != TRANSFER_TOPIC
                or not HASH.fullmatch(tx_hash)):
            return
        try:
            # ABI address is right-aligned in a 32-byte topic.
            if any(not re.fullmatch(r"0x[0-9a-fA-F]{64}", x) for x in topics[1:]):
                return
            sender, recipient = ("0x" + x[-40:].lower() for x in topics[1:])
            data = log["data"]
            if not re.fullmatch(r"0x[0-9a-fA-F]{64}", data):
                return
            index = str(log["logIndex"]).lower()
            if not re.fullmatch(r"0x[0-9a-f]+", index):
                return
            await self._emit(chain, tx_hash, index, token[0], int(data, 16), token[1], sender, recipient)
        except (KeyError, TypeError, ValueError):
            return

    async def handle_native(self, chain: str, tx: dict) -> None:
        if not isinstance(tx, dict):
            return
        tx_hash = str(tx.get("hash", "")).lower()
        sender, recipient = str(tx.get("from") or "").lower(), str(tx.get("to") or "").lower()
        if not HASH.fullmatch(tx_hash) or not ADDRESS.fullmatch(sender) or not ADDRESS.fullmatch(recipient):
            return
        # Calls with calldata are not simple native payments. Internal transfers
        # cannot be reconstructed from the top-level transaction alone.
        if tx.get("input", "0x") not in ("0x", "0X", ""):
            return
        try:
            value = tx["value"]
            if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", value):
                return
            await self._emit(chain, tx_hash, "native", chain, int(value, 16), 18, sender, recipient)
        except (KeyError, ValueError, TypeError):
            return

    @staticmethod
    def _hl_trade_rows(payload: dict) -> list[dict]:
        """Accept both documented WsTrade arrays and the grouped trades shape."""
        if not isinstance(payload, dict) or payload.get("channel") != "trades":
            return []
        if payload.get("isSnapshot"):
            return []
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            coin = data.get("coin")
            trades = data.get("trades")
            if isinstance(trades, list):
                return [({"coin": coin, **row} if "coin" not in row else row)
                        for row in trades if isinstance(row, dict)]
        return []

    @staticmethod
    def _hl_timestamp(value) -> float:
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(ts):
            return 0.0
        # WsTrade timestamps are milliseconds; tolerate second-based test feeds.
        return ts / 1000.0 if ts > 100_000_000_000 else ts

    async def handle_hyperliquid_trade(self, row: dict, *, now: float | None = None) -> bool:
        """Store one sufficiently large public Hyperliquid Core fill.

        ``trades`` is a public native Hyperliquid stream. Unlike EVM token
        transfers, these records are market fills rather than deposits or
        withdrawals; ``side`` therefore uses the explicit ``trade`` category.
        """
        if not isinstance(row, dict):
            return False
        tx_hash = str(row.get("hash") or "").lower()
        if not HASH.fullmatch(tx_hash):
            return False
        coin = str(row.get("coin") or "").strip()
        side_raw = str(row.get("side") or "").upper()
        if not coin or side_raw not in ("A", "B"):
            return False
        try:
            price, amount = float(row.get("px") or 0), float(row.get("sz") or 0)
        except (TypeError, ValueError):
            return False
        usd = price * amount
        if (price <= 0 or amount <= 0 or not math.isfinite(usd)
                or usd < self.min_usd):
            return False
        timestamp = self._hl_timestamp(row.get("time"))
        now = time.time() if now is None else now
        if (not timestamp or timestamp > now + 30 or now - timestamp > self.hl_fresh_sec):
            return False

        users = row.get("users") or []
        if not isinstance(users, list):
            users = []
        buyer = str(users[0]).lower() if len(users) > 0 else ""
        seller = str(users[1]).lower() if len(users) > 1 else ""
        if not ADDRESS.fullmatch(buyer):
            buyer = ""
        if not ADDRESS.fullmatch(seller):
            seller = ""
        trade_id = row.get("tid")
        if isinstance(trade_id, (str, int)) and not isinstance(trade_id, bool):
            trade_id = coin + ":" + str(trade_id)[:100]
        else:
            trade_id = ""
        dedup_id = (tx_hash + ":" + trade_id if trade_id else
                    ":".join((tx_hash, coin, str(int(timestamp * 1000)), side_raw,
                               str(row.get("px") or ""), str(row.get("sz") or ""),
                               buyer, seller)))
        if not self._remember("HYPERLIQUID", dedup_id):
            return False

        # WsTrade.side is the taker's side. users is [buyer, seller].
        side = "BUY" if side_raw == "B" else "SELL"
        event = {
            "chain": "HYPERLIQUID", "hash": tx_hash,
            "log_index": trade_id or dedup_id,
            "timestamp": int(timestamp), "symbol": coin,
            "amount": amount, "usd": round(usd, 2),
            "from": buyer, "to": seller,
            "from_label": self.wallets.get(buyer, ""),
            "to_label": self.wallets.get(seller, ""),
            "direction": "trade", "side": side, "kind": "trade",
        }
        self.events.append(event)
        self.hl_status["events"] += 1
        self.hl_status["last_event_ts"] = timestamp
        await self.broadcast({"type": "whale_tx", **event})
        return True

    async def _load_hyperliquid_universe(self, session: aiohttp.ClientSession) -> list[str]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with session.post(self.hl_rest + "/info", json={"type": "meta"},
                                timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"Hyperliquid /info HTTP {response.status}")
            data = await response.json()
        if not isinstance(data, dict) or not isinstance(data.get("universe"), list):
            raise RuntimeError("Hyperliquid meta response has no universe")
        coins = []
        seen = set()
        for item in data["universe"]:
            if not isinstance(item, dict) or item.get("isDelisted"):
                continue
            # Preserve the exact API case (e.g. kPEPE); WS subscriptions are
            # case-sensitive and an unknown name can close the socket.
            name = str(item.get("name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                coins.append(name)
        if not coins:
            raise RuntimeError("Hyperliquid returned an empty universe")
        return coins

    async def _subscribe_hyperliquid(self, ws, coins: list[str]) -> None:
        for coin in coins:
            await ws.send_json({"method": "subscribe",
                                "subscription": {"type": "trades", "coin": coin}})
            self.hl_status["subscriptions_sent"] += 1
            if self.hl_sub_gap:
                await asyncio.sleep(self.hl_sub_gap)

    async def _run_hyperliquid_connection(self, session: aiohttp.ClientSession,
                                          coins: list[str]) -> None:
        timeout = (aiohttp.ClientWSTimeout(ws_close=25)
                   if hasattr(aiohttp, "ClientWSTimeout") else 25)
        async with session.ws_connect(self.hl_ws, heartbeat=None, timeout=timeout,
                                      max_msg_size=16 * 1024 * 1024) as ws:
            self.hl_status.update(connected=True, state="subscribing", error="",
                                  market_count=len(coins), subscriptions_sent=0,
                                  subscriptions_acked=0)
            sender = asyncio.create_task(self._subscribe_hyperliquid(ws, coins),
                                         name="whale-hl-subscriptions")
            last_ping = time.monotonic()
            try:
                while True:
                    if sender.done():
                        error = sender.exception()
                        if error:
                            raise RuntimeError("Hyperliquid subscription send failed") from error
                        if self.hl_status["subscriptions_acked"] >= len(coins):
                            self.hl_status["state"] = "connected"
                    if time.monotonic() - last_ping >= self.hl_ping_sec:
                        await ws.send_json({"method": "ping"})
                        last_ping = time.monotonic()
                    try:
                        msg = await ws.receive(timeout=min(5.0, self.hl_ping_sec))
                    except asyncio.TimeoutError:
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                    aiohttp.WSMsgType.ERROR):
                        raise RuntimeError("Hyperliquid WebSocket closed")
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    self.hl_status["last_message_ts"] = time.time()
                    try:
                        payload = json.loads(msg.data)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("channel") == "subscriptionResponse":
                        data = payload.get("data") or {}
                        if isinstance(data, dict) and data.get("error"):
                            raise RuntimeError("Hyperliquid rejected a trades subscription")
                        if isinstance(data, dict) and data.get("method") == "subscribe":
                            self.hl_status["subscriptions_acked"] += 1
                        if (sender.done() and
                                self.hl_status["subscriptions_acked"] >= len(coins)):
                            self.hl_status["state"] = "connected"
                        continue
                    rows = self._hl_trade_rows(payload)
                    if not rows:
                        continue
                    now = time.time()
                    for row in rows:
                        timestamp = self._hl_timestamp(row.get("time"))
                        if (not timestamp or timestamp > now + 30 or
                                now - timestamp > self.hl_fresh_sec):
                            continue
                        self.hl_status["trades_seen"] += 1
                        self.hl_status["last_trade_ts"] = timestamp
                        await self.handle_hyperliquid_trade(row, now=now)
                    if sender.done() and self.hl_status["subscriptions_acked"] >= len(coins):
                        self.hl_status["state"] = "connected"
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    async def run_hyperliquid(self) -> None:
        """Reconnectable public Hyperliquid trades feed; no Alchemy key/RPC."""
        if not self.hl_enabled:
            self.hl_status.update(state="disabled", connected=False)
            return
        delay = 2.0
        coins: list[str] = []
        universe_loaded_at = 0.0
        family = os.getenv("LIQSCOPE_FAMILY", "").strip()
        connector = (aiohttp.TCPConnector(
            family=socket.AF_INET if family == "4" else socket.AF_INET6)
            if family in ("4", "6") else None)
        async with aiohttp.ClientSession(
                headers={"User-Agent": HL_USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15),
                connector=connector) as session:
            while True:
                self.hl_status.update(state="connecting", connected=False)
                try:
                    if (not coins or time.time() - universe_loaded_at >=
                            HL_WHALE_UNIVERSE_TTL_SEC):
                        coins = await self._load_hyperliquid_universe(session)
                        universe_loaded_at = time.time()
                    self.hl_status["market_count"] = len(coins)
                    await self._run_hyperliquid_connection(session, coins)
                    raise RuntimeError("Hyperliquid WebSocket ended")
                except asyncio.CancelledError:
                    self.hl_status.update(state="stopped", connected=False)
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError,
                        RuntimeError, ValueError) as exc:
                    was_connected = self.hl_status["connected"]
                    self.hl_status.update(state="reconnecting", connected=False,
                                          error=str(exc)[:160])
                    if was_connected:
                        self.hl_status["reconnects"] += 1
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60.0)

    async def _subscribe(self, ws, chain: str) -> dict[str, str]:
        addresses = list(TOKENS[chain])
        await ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe",
                            "params": ["logs", {"address": addresses, "topics": [TRANSFER_TOPIC]}]})
        await ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "eth_subscribe",
                            "params": ["alchemy_minedTransactions", {"hashesOnly": False}]})
        subs = {}
        # Subscription responses arrive before notifications on a fresh socket.
        while len(subs) < 2:
            msg = await ws.receive_json()
            if msg.get("id") == 1:
                if "error" in msg or not msg.get("result"):
                    raise RuntimeError("Alchemy logs subscription rejected")
                subs[msg["result"]] = "logs"
            elif msg.get("id") == 2:
                if "error" in msg or not msg.get("result"):
                    # BNB may not implement the Alchemy-only mined extension;
                    # standard newHeads + eth_getBlockByNumber works on both.
                    await ws.send_json({"jsonrpc": "2.0", "id": 3, "method": "eth_subscribe",
                                        "params": ["newHeads"]})
                else:
                    subs[msg["result"]] = "native"
            elif msg.get("id") == 3:
                if "error" in msg or not msg.get("result"):
                    raise RuntimeError("Alchemy native subscription rejected")
                subs[msg["result"]] = "heads"
            elif msg.get("method") == "eth_subscription":
                await self._notification(chain, msg, subs, ws)
        return subs

    async def _notification(self, chain: str, msg: dict, subs: dict, ws) -> None:
        params = msg.get("params") or {}
        kind = subs.get(params.get("subscription"))
        item = params.get("result") or {}
        if not isinstance(item, dict):
            return
        if kind == "logs":
            await self.handle_log(chain, item)
        elif kind == "native":
            if not item.get("removed"):
                await self.handle_native(chain, item.get("transaction") or {})
        elif kind == "heads":
            number = item.get("number")
            if not isinstance(number, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", number):
                return
            pending = self._pending_blocks[chain]
            if len(pending) >= 32:
                raise RuntimeError("Block responses are falling behind")
            self._next_rpc_id += 1
            request_id = self._next_rpc_id
            pending[request_id] = number
            await ws.send_json({"jsonrpc": "2.0", "id": request_id,
                                "method": "eth_getBlockByNumber", "params": [number, True]})
    async def _block_reply(self, chain: str, msg: dict) -> None:
        pending = self._pending_blocks[chain]
        if msg.get("id") not in pending:
            return
        pending.pop(msg["id"])
        block = msg.get("result")
        if not isinstance(block, dict) or not isinstance(block.get("transactions"), list):
            raise RuntimeError("Alchemy block response rejected")
        for tx in block["transactions"]:
            await self.handle_native(chain, tx)

    async def _run_chain(self, chain: str, session: aiohttp.ClientSession) -> None:
        delay = 5
        while True:
            try:
                self._pending_blocks[chain].clear()
                timeout = ({"timeout": aiohttp.ClientWSTimeout(ws_receive=90)}
                           if hasattr(aiohttp, "ClientWSTimeout") else {"receive_timeout": 90})
                async with session.ws_connect(self.endpoints[chain], heartbeat=30,
                                              max_msg_size=16 * 1024 * 1024, **timeout) as ws:
                    subs = await self._subscribe(ws, chain)
                    delay = 5
                    async for frame in ws:
                        if frame.type == aiohttp.WSMsgType.TEXT:
                            try:
                                msg = json.loads(frame.data)
                                if isinstance(msg, dict):
                                    if msg.get("method") == "eth_subscription":
                                        await self._notification(chain, msg, subs, ws)
                                    elif "id" in msg:
                                        await self._block_reply(chain, msg)
                            except (ValueError, TypeError, KeyError):
                                continue
                        elif frame.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError):
                pass  # reconnect without exposing the API key in exception URLs
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with asyncio.TaskGroup() as group:
                for chain in self.endpoints:
                    group.create_task(self._run_chain(chain, session))
