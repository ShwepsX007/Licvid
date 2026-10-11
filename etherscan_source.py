"""Etherscan API V2 как источник EVM-данных скринера китов.

Зачем: без ключа Alchemy пять EVM-сетей (ETH/BNB/POLYGON/ARBITRUM/BASE) стоят
пустыми. Бесплатный тариф Etherscan (3 вызова/с, 100k вызовов/день, один ключ
на все сети V2) закрывает тот же поток данных REST-поллингом: переводы
токенов и нативные переводы по кошелькам бирж.

Ключи: пул из шифрованного хранилища (AlchemyKeyStore с отдельным файлом),
работают ПО ОЧЕРЕДИ — каждый вызов берёт следующий готовый ключ. Ключ уходит
в кулдаун по явной ошибке провайдера: 429/rate limit — 2 с, дневной лимит —
до конца UTC-суток, неверный ключ — 15 минут. Тишина данных кулдауном не
считается (урок из Alchemy-ротации).

Курсоры: по (сеть, кошелёк, тип) хранится последний обработанный блок.
Первый проход якорится блоком по текущему времени (getblocknobytime), чтобы
не тянуть всю историю адреса.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import aiohttp

try:  # контракты отслеживаемых токенов — общий словарь скринера
    from whale_screener import TOKENS as _SCREENER_TOKENS
except Exception:  # noqa: BLE001 — модуль опционален для юнит-тестов
    _SCREENER_TOKENS = {}

log = logging.getLogger("liqscope.etherscan")

ETHERSCAN_API = "https://api.etherscan.io/v2/api"
#: chainid для API V2 — один ключ работает на все сети
CHAIN_IDS = {"ETH": 1, "BNB": 56, "POLYGON": 137, "ARBITRUM": 42161, "BASE": 8453}
#: нативная монета сети и её decimals (txlist отдаёт value в wei)
NATIVE_SYMBOL = {"ETH": "ETH", "BNB": "BNB", "POLYGON": "POL",
                 "ARBITRUM": "ETH", "BASE": "ETH"}
#: сколько записей просить за вызов (максимум Etherscan — 10000)
PAGE_OFFSET = 200
#: предохранитель от бесконечной пагинации в одном проходе
MAX_PAGES_PER_WALLET = 6

RATE_PARK_SEC = 2.0          # 429 / "Max rate limit reached"
DAILY_PARK_MARGIN_SEC = 60.0  # дневной лимит → до конца UTC-суток (+минута)
AUTH_PARK_SEC = 900.0        # "Invalid API Key"
NETWORK_RETRY_SEC = 60.0     # пауза сети после сетевой ошибки


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _utc_day(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if ts is None else ts))


def _utc_midnight_after(ts: float) -> float:
    day = 86400.0
    return (int(ts // day) + 1) * day


class EtherscanError(Exception):
    pass


class EtherscanCollector:
    """Поллинг Etherscan API V2 по кошелькам бирж; эмитит события в скринер."""

    def __init__(self, screener, key_store, *, networks=None,
                 state_file: Path | None = None,
                 poll_interval_sec: float | None = None,
                 daily_calls: int | None = None,
                 min_interval_sec: float | None = None,
                 is_primary_active=None,
                 native_enabled: bool | None = None):
        self.screener = screener
        self.key_store = key_store
        # тумблеры сетей админки (screener_networks.NetworkSwitch); None = все
        self.networks = networks
        self.state_file = state_file or Path(
            __file__).resolve().parent / "data/etherscan_state.json"
        self.poll_interval_sec = max(30.0, float(
            poll_interval_sec if poll_interval_sec is not None
            else _env_float("LIQSCOPE_ETHERSCAN_POLL_SEC", 180.0)))
        self.daily_calls = int(daily_calls if daily_calls is not None
                               else int(os.getenv("LIQSCOPE_ETHERSCAN_DAILY_CALLS",
                                                  "95000") or 95000))
        # держимся ниже 3 вызовов/с бесплатного тарифа
        self.min_interval_sec = max(0.05, float(
            min_interval_sec if min_interval_sec is not None
            else _env_float("LIQSCOPE_ETHERSCAN_MIN_INTERVAL_SEC", 0.34)))
        self.is_primary_active = is_primary_active or (lambda: False)
        self.native_enabled = (native_enabled if native_enabled is not None
                               else os.getenv("LIQSCOPE_ETHERSCAN_NATIVE", "1") != "0")
        self.wakeup = asyncio.Event()
        # тумблер админки: источник выключен — ключи остаются, опроса нет
        self.enabled = True
        self._anchors: dict[str, int] = {}   # chain → блок старта (кэш прохода)
        self._call_lock = asyncio.Lock()
        self._last_call_at = 0.0
        self._key_cursor = 0
        # key_id → {"cooldown_until": float, "day": "YYYY-MM-DD", "calls": int}
        self.key_runtime: dict[str, dict] = {}
        self.cursors: dict[str, int] = {}
        self.network_status: dict[str, dict] = {
            chain: {"status": "waiting", "last_success": 0.0, "last_attempt": 0.0,
                    "last_event": 0.0, "error": "", "wallets": 0}
            for chain in CHAIN_IDS}
        self._load()

    # ---------------- состояние (курсоры) ---------------------------------

    def _load(self) -> None:
        try:
            blob = json.loads(self.state_file.read_text())
            if isinstance(blob.get("cursors"), dict):
                self.cursors = {str(k): int(v) for k, v in blob["cursors"].items()}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.cursors = {}

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cursors": self.cursors,
                                       "saved_at": int(time.time())}))
            os.replace(tmp, self.state_file)
        except OSError:
            pass  # курсоры — оптимизация, потеря дубликатов не критична

    def _cursor_key(self, chain: str, address: str, kind: str) -> str:
        return f"{chain}|{address.lower()}|{kind}"

    # ---------------- пул ключей ------------------------------------------

    def _pool(self) -> list[tuple[str, str]]:
        try:
            return list(self.key_store.keys()) if self.key_store else []
        except Exception:  # noqa: BLE001 — опциональный источник не роняет сайт
            return []

    def _key_state(self, key_id: str) -> dict:
        row = self.key_runtime.setdefault(
            key_id, {"cooldown_until": 0.0, "day": "", "calls": 0})
        today = _utc_day()
        if row["day"] != today:
            row["day"] = today          # новый UTC-день — квота обновилась
            row["calls"] = 0
            if row["cooldown_until"] < time.time():
                row["cooldown_until"] = 0.0
        return row

    def _pick_key(self) -> tuple[str, str] | None:
        """Следующий готовый ключ по кругу; None — все в кулдауне/лимите."""
        pool = self._pool()
        if not pool:
            return None
        now = time.time()
        for step in range(len(pool)):
            idx = (self._key_cursor + step) % len(pool)
            key_id, secret = pool[idx]
            row = self._key_state(key_id)
            if row["cooldown_until"] > now or row["calls"] >= self.daily_calls:
                continue
            self._key_cursor = (idx + 1) % len(pool)
            return key_id, secret
        return None

    def _park(self, key_id: str, seconds: float) -> None:
        if not key_id:
            return
        row = self._key_state(key_id)
        row["cooldown_until"] = max(row["cooldown_until"], time.time() + max(0.1, seconds))

    # ---------------- HTTP -------------------------------------------------

    async def _api(self, session: aiohttp.ClientSession, chain: str,
                   module: str, action: str, **params) -> list[dict]:
        """Один вызов Etherscan V2 с ротацией ключей; отдаёт список result."""
        for _attempt in range(max(1, len(self._pool()))):
            picked = self._pick_key()
            if picked is None:
                raise EtherscanError("all keys parked or daily limit reached")
            key_id, secret = picked
            async with self._call_lock:
                wait = self._last_call_at + self.min_interval_sec - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_call_at = time.monotonic()
            query = {"chainid": CHAIN_IDS[chain], "module": module,
                     "action": action, "apikey": secret, **params}
            try:
                async with session.get(ETHERSCAN_API, params=query,
                                       timeout=aiohttp.ClientTimeout(total=25)) as resp:
                    text = await resp.text()
                    if resp.status == 429:
                        self._park(key_id, RATE_PARK_SEC)
                        continue
                    if resp.status in (401, 403):
                        self._park(key_id, AUTH_PARK_SEC)
                        continue
                    if resp.status != 200:
                        raise EtherscanError(f"HTTP {resp.status}")
                    payload = json.loads(text)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                raise EtherscanError(f"{type(exc).__name__}: {exc}") from None
            self._key_state(key_id)["calls"] += 1
            status = str(payload.get("status", ""))
            message = str(payload.get("message") or "")
            result = payload.get("result")
            # status "1": result бывает списком (account), объектом (block)
            # или скаляром — отдаём как есть, тип разбирает вызывающий
            if status == "1" and result is not None:
                return result
            # status "0" — нормальный пустой ответ ИЛИ ошибка ключа.
            # Etherscan часто кладёт текст ошибки в result, а message = NOTOK,
            # поэтому каждый паттерн ищем в обоих местах.
            blob = (message + " " + (result if isinstance(result, str) else "")).lower()
            if ("no transactions" in blob or "no records" in blob
                    or "no matching" in blob or "no data" in blob):
                return []
            if "rate limit" in blob and "per day" not in blob:
                self._park(key_id, RATE_PARK_SEC)
                continue
            if ("daily" in blob or "day limit" in blob or "max calls" in blob
                    or "per day" in blob):
                # дневная квота ключа кончилась — до конца UTC-суток
                self._park(key_id, max(60.0, _utc_midnight_after(time.time())
                                       - time.time() + DAILY_PARK_MARGIN_SEC))
                continue
            if "invalid api key" in blob or "missing api" in blob:
                self._park(key_id, AUTH_PARK_SEC)
                continue
            if isinstance(result, str) and result:
                raise EtherscanError(result[:200])
            return []
        raise EtherscanError("no ready key after rotation")

    # ---------------- обработка строк --------------------------------------

    async def _anchor_block(self, session: aiohttp.ClientSession, chain: str) -> int:
        """Блок «сейчас» для якоря курсора. V2 отдаёт и объект, и список."""
        payload = await self._api(session, chain, "block", "getblocknobytime",
                                  timestamp=int(time.time()), closest="before")
        row = payload if isinstance(payload, dict) else (
            payload[0] if isinstance(payload, list) and payload else {})
        try:
            return int(row.get("blockNumber"))
        except (TypeError, ValueError):
            return 0

    def _wallet_action(self, kind: str) -> tuple[str, str]:
        return ("account", "tokentx") if kind == "token" else ("account", "txlist")

    async def _newest_block(self, session: aiohttp.ClientSession, chain: str,
                            kind: str, address: str) -> int:
        """Фолбэк-якорь: блок последней записи кошелька (sort=desc, 1 шт.)."""
        module, action = self._wallet_action(kind)
        try:
            rows = await self._api(session, chain, module, action,
                                   address=address, startblock=0,
                                   endblock=999999999, sort="desc",
                                   page=1, offset=1)
        except EtherscanError:
            return 0
        rows = rows if isinstance(rows, list) else []
        try:
            return int(rows[0].get("blockNumber", 0)) if rows else 0
        except (TypeError, ValueError):
            return 0

    async def _poll_wallet(self, session: aiohttp.ClientSession, chain: str,
                           address: str, wallets: dict[str, str],
                           tokens: dict) -> int:
        """Опрашивает кошелёк (токены + натив), возвращает число событий."""
        events = 0
        for kind in ("token", "native") if self.native_enabled else ("token",):
            cursor_key = self._cursor_key(chain, address, kind)
            cursor = self.cursors.get(cursor_key)
            if cursor is None:
                if chain not in self._anchors:
                    self._anchors[chain] = await self._anchor_block(session, chain)
                anchor = self._anchors[chain]
                if anchor <= 0:
                    # блок-эндпоинт не дал якоря — берём блок последней
                    # записи самого кошелька (гарантированно бесплатный путь)
                    anchor = await self._newest_block(session, chain, kind, address)
                if anchor <= 0:
                    continue   # нет ни блока, ни истории: повторим в следующий проход
                cursor = max(0, anchor - 3)   # маленький захват назад
                # базовый курсор пишем сразу: пустой ответ не должен
                # заставлять якорить блок заново каждый проход
                self.cursors[cursor_key] = cursor
                self._save()
            pages = 0
            while pages < MAX_PAGES_PER_WALLET:
                pages += 1
                module, action = self._wallet_action(kind)
                rows = await self._api(session, chain, module, action,
                                       address=address, startblock=cursor,
                                       endblock=999999999, sort="asc",
                                       page=1, offset=PAGE_OFFSET)
                rows = rows if isinstance(rows, list) else []
                if not rows:
                    break
                last_block = cursor
                per_hash: dict[str, int] = {}
                for row in rows:
                    try:
                        block = int(row.get("blockNumber", 0))
                    except (TypeError, ValueError):
                        continue
                    last_block = max(last_block, block)
                    made = await self._handle_row(chain, kind, row, wallets,
                                                  tokens, per_hash)
                    events += 1 if made else 0
                if last_block <= cursor:
                    break
                # включительно: строки того же блока придут снова — дедуп
                # скринера (hash:index) их съест; курсор двигаем на блок
                cursor = last_block
                self.cursors[cursor_key] = cursor
                self._save()
                if len(rows) < PAGE_OFFSET:
                    break
        return events

    async def _handle_row(self, chain: str, kind: str, row: dict,
                          wallets: dict[str, str], tokens: dict,
                          per_hash: dict[str, int]) -> bool:
        tx_hash = str(row.get("hash") or "").lower()
        sender = str(row.get("from") or "").lower()
        recipient = str(row.get("to") or "").lower()
        if not re.fullmatch(r"0x[0-9a-f]{64}", tx_hash):
            return False
        try:
            raw_value = int(str(row.get("value") or "0"))
        except (TypeError, ValueError):
            return False
        if raw_value <= 0:
            return False
        if sender not in wallets and recipient not in wallets:
            return False
        if kind == "token":
            token = tokens.get(str(row.get("contractAddress") or "").lower())
            if not token:
                return False
            symbol, decimals = token
        else:
            # нативный перевод: только чистая отправка (без вызова контракта)
            if str(row.get("input") or "0x") not in ("0x", "0X", ""):
                return False
            if str(row.get("isError") or "0") != "0":
                return False
            symbol, decimals = NATIVE_SYMBOL[chain], 18
        # уникальный index внутри tx: Etherscan не отдаёт logIndex
        seq = per_hash.get(tx_hash, 0)
        per_hash[tx_hash] = seq + 1
        index = f"es{row.get('transactionIndex', 0)}" + (f".{seq}" if seq else "")
        try:
            timestamp = int(row.get("timeStamp") or 0) or None
        except (TypeError, ValueError):
            timestamp = None
        try:
            await self.screener._emit(chain, tx_hash, index, symbol,
                                      raw_value, decimals, sender, recipient,
                                      source="realtime", timestamp=timestamp)
            self.network_status[chain]["last_event"] = time.time()
            return True
        except Exception:  # noqa: BLE001 — битая строка не роняет проход
            return False

    # ---------------- цикл --------------------------------------------------

    def network_enabled(self, chain: str) -> bool:
        if chain not in CHAIN_IDS:
            return False
        if self.networks is None:
            return True
        try:
            return bool(self.networks.enabled(chain))
        except Exception:  # noqa: BLE001
            return True

    async def poll_chain(self, session: aiohttp.ClientSession, chain: str) -> None:
        row = self.network_status[chain]
        row["last_attempt"] = time.time()
        wallets = self.screener.wallet_addresses(chain)
        row["wallets"] = len(wallets)
        if not wallets:
            row["status"], row["error"] = "waiting", ""
            return
        tokens = _SCREENER_TOKENS.get(chain, {})
        events = 0
        for address in wallets:
            events += await self._poll_wallet(session, chain, address, wallets, tokens)
        row["status"] = "online"
        row["last_success"] = time.time()
        row["error"] = ""
        if events:
            log.info("[etherscan] %s: +%d событий за проход", chain, events)

    async def run(self) -> None:
        """Основной цикл: пауза, пока нет ключей или работает Alchemy."""
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    if not self.enabled:
                        for chain in CHAIN_IDS:
                            self.network_status[chain].update(
                                status="paused",
                                error="Etherscan source disabled by admin")
                        await self._sleep_or_wakeup(self.poll_interval_sec)
                        continue
                    if not self._pool():
                        for chain in CHAIN_IDS:
                            self.network_status[chain].update(
                                status="paused",
                                error="Etherscan API key is not configured")
                        await self._sleep_or_wakeup(self.poll_interval_sec)
                        continue
                    if self.is_primary_active():
                        # Alchemy на месте — Etherscan не жжём (дубль данных)
                        for chain in CHAIN_IDS:
                            self.network_status[chain].update(
                                status="paused",
                                error="Alchemy keys configured; Etherscan is standby")
                        await self._sleep_or_wakeup(self.poll_interval_sec)
                        continue
                    for chain in CHAIN_IDS:
                        if not self.network_enabled(chain):
                            self.network_status[chain].update(
                                status="disabled", error="")
                            continue
                        try:
                            await self.poll_chain(session, chain)
                        except EtherscanError as exc:
                            self.network_status[chain].update(
                                status="error", error=str(exc)[:200])
                            log.warning("[etherscan] %s: %s", chain, exc)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            self.network_status[chain].update(
                                status="error", error=str(exc)[:200])
                            log.warning("[etherscan] %s: %s: %s",
                                        chain, type(exc).__name__, exc)
                    await self._sleep_or_wakeup(self.poll_interval_sec)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — коллектор не роняет сайт
                    log.warning("[etherscan] цикл: %s: %s", type(exc).__name__, exc)
                    await self._sleep_or_wakeup(NETWORK_RETRY_SEC)

    async def _sleep_or_wakeup(self, seconds: float) -> None:
        self.wakeup.clear()
        try:
            await asyncio.wait_for(self.wakeup.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ---------------- статус для админки ------------------------------------

    def status(self) -> dict:
        pool = self._pool()
        mode = ("disabled" if not self.enabled else
                "paused_no_keys" if not pool else
                "paused_alchemy" if self.is_primary_active() else "primary")
        keys = []
        calls_today = 0
        for key_id, _secret in pool:
            row = self._key_state(key_id)
            calls_today += row["calls"]
            now = time.time()
            if row["calls"] >= self.daily_calls:
                state = "daily_done"
            elif row["cooldown_until"] > now:
                state = "cooldown"
            else:
                state = "active"
            keys.append({"id": key_id,
                         "hint": key_id[:8],
                         "state": state,
                         "calls_today": row["calls"],
                         "cooldown_sec": max(0, int(row["cooldown_until"] - time.time()))})
        try:
            public = {r["id"]: r.get("hint", "") for r in self.key_store.public()}
        except Exception:  # noqa: BLE001
            public = {}
        for row in keys:
            row["hint"] = public.get(row["id"], row["id"][:8])
            row["source"] = "environment" if row["id"] == "env" else "admin"
        return {
            "available": True, "provider": "etherscan", "mode": mode,
            "enabled": bool(self.enabled),
            "poll_interval_sec": int(self.poll_interval_sec),
            "daily_calls_limit": self.daily_calls,
            "calls_today": calls_today,
            "keys_configured": len(pool),
            "keys": keys,
            "native_enabled": bool(self.native_enabled),
            "networks": {chain: dict(row) for chain, row in self.network_status.items()},
        }

