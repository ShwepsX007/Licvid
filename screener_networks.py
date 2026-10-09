"""Тумблеры сетей Скринера китов и срок хранения базы кошельков бирж.

Модуль намеренно без aiohttp, БД и импортов сервера: переключатели нужны и
коллектору (`whale_poller`), и выдаче (`whale_screener`, `/api/screener/*`), и
кабинету, и админке. Чтобы не плоить зависимости, состояние живёт в одном
маленьком JSON рядом с остальными файлами скринера и читается-пишется атомарно.

Смысл тумблеров: Alchemy берёт CU за каждую EVM-сеть и за Solana. Сбор по
восьми сетям съедал месячный Free-лимит за дни, а данными по большинству из
никто не пользовался. Сеть, выключенная админом, перестаёт опрашиваться
(расход CU падает до нуля) и перестаёт отдаваться в ленту, статистику, CSV и
кабинетные сигналы. История уже собранных событий не удаляется: включил сеть —
лента снова полная.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

# Порядок = порядок карточек в админке и чипов в скринере.
NETWORK_IDS: tuple[str, ...] = (
    "ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA", "TRON", "HYPERLIQUID",
)

#: id -> (заголовок, провайдер, тратит ли CU Alchemy)
NETWORK_META: dict[str, tuple[str, str, bool]] = {
    "ETH": ("Ethereum", "alchemy", True),
    "BNB": ("BNB Chain", "alchemy", True),
    "POLYGON": ("Polygon", "alchemy", True),
    "ARBITRUM": ("Arbitrum", "alchemy", True),
    "BASE": ("Base", "alchemy", True),
    "SOLANA": ("Solana", "alchemy_solana", True),
    "TRON": ("TRON", "trongrid", False),
    "HYPERLIQUID": ("Hyperliquid Core", "native_api", False),
}

#: Что включено при первом запуске: дорогая сеть с самыми полными данными плюс
#: два бесплатных источника. Остальное админ включает сам.
DEFAULT_ENABLED: tuple[str, ...] = ("ETH", "TRON", "HYPERLIQUID")

#: Сколько дней кошелек биржи может не светиться нигде, прежде чем база от него
#: избавится. 7 = ровно столько же, сколько хранится история событий.
WALLET_RETENTION_DAYS = 7
WALLET_RETENTION_MIN = 0
WALLET_RETENTION_MAX = 365

ENV_ENABLED = "LIQSCOPE_SCREENER_NETWORKS"
ENV_RETENTION = "LIQSCOPE_WALLET_RETENTION_DAYS"


ALIASES = {
    "ETHEREUM": "ETH", "MAINNET": "ETH",
    "MATIC": "POLYGON", "POL": "POLYGON", "POLYGONPOS": "POLYGON",
    "BSC": "BNB", "BINANCE": "BNB", "BINANCESMARTCHAIN": "BNB",
    "BNBCHAIN": "BNB", "SMARTCHAIN": "BNB",
    "ARBITRUMONE": "ARBITRUM", "ARB": "ARBITRUM", "SOL": "SOLANA",
    "TRX": "TRON", "HYPE": "HYPERLIQUID", "HYPERLIQUIDCORE": "HYPERLIQUID",
}


def normalize_network(value: object) -> str:
    """Идентификатор сети по любому написанию ('BNB Chain' -> 'BNB'), '' если нет.

    Сети подписывают по-разному: «BNB», «BNB Chain», «bsc», «Binance Smart
    Chain». Разбор один на весь продукт — иначе тумблер выключит одну подпись,
    а коллектор продолжит собирать другую и списывать CU.
    """
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text in NETWORK_IDS:
        return text
    compact = "".join(char for char in text if char.isalnum())
    if compact in NETWORK_IDS:
        return compact
    return ALIASES.get(compact, "")


def normalize_enabled(value: object, *, default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Список включённых сетей из чего угодно: строки, списка, словаря флагов.

    Правило безопасности: строка-мусор («MARS», опечатка в env) НЕ означает
    «всё выключено» — сети дороже опечатки, поэтому возвращаем `default`.
    А явный пустой список или {"ETH": false} — честное решение админа: сбор
    стоит, CU не тратится.
    """
    fallback = tuple(DEFAULT_ENABLED if default is None else default)
    if value is None:
        return fallback
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback
        if text.upper() in ("ALL", "*"):
            return tuple(NETWORK_IDS)
        items: list = [part for part in text.replace(";", ",").split(",")]
        junk_means_default = True
    elif isinstance(value, dict):
        items = [key for key, flag in value.items() if _truthy(flag)]
        junk_means_default = False
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        junk_means_default = False
    else:
        return fallback
    picked = {normalize_network(item) for item in items}
    picked.discard("")
    if junk_means_default and items and not picked:
        return fallback
    return tuple(chain for chain in NETWORK_IDS if chain in picked)


def retention_days(value: object, *, default: int = WALLET_RETENTION_DAYS) -> int:
    """Срок хранения кошельков в днях; 0 — автоочистка выключена."""
    try:
        days = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(WALLET_RETENTION_MIN, min(WALLET_RETENTION_MAX, days))


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off", "выкл")
    return bool(value)


class NetworkSwitch:
    """Включённые сети + настройка автоочистки кошельков, с JSON-файлом.

    Файл не обязателен (тесты и демо-нога живут без него): тогда состояние
    только в памяти. Чтение/запись под `RLock`, запись атомарная — коллектор и
    админка трогают его из разных потоков.
    """

    def __init__(self, path: str | os.PathLike | None = None, *,
                 enabled: object = None, retention: object = None,
                 env: dict | None = None) -> None:
        self.path = str(path) if path else ""
        self._lock = threading.RLock()
        environ = os.environ if env is None else env
        self._enabled: dict[str, bool] = {}
        self._retention = WALLET_RETENTION_DAYS
        self.last_error = ""
        # файл читался битым — помним постоянно: это ответ на вопрос
        # «почему тумблеры вернулись к заводским»
        self.load_error = ""
        self.updated_at = 0.0

        loaded: dict = {}
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    raw = json.load(handle)
                if isinstance(raw, dict):
                    loaded = raw
            except FileNotFoundError:
                loaded = {}
            except (OSError, ValueError) as exc:
                # Битый файл не должен уронить скринер: читаем умолчания,
                # перезапишем при первой же смене тумблера.
                self.load_error = self.last_error = f"{type(exc).__name__}: {exc}"[:200]

        # Приоритет: явный аргумент (тесты) > файл > окружение > умолчание.
        # Неизвестный тип — «значения нет», идём дальше, а не «сетей нет».
        for source in (enabled, loaded.get("networks"), environ.get(ENV_ENABLED)):
            if isinstance(source, (str, list, tuple, set, frozenset, dict)):
                self._set_enabled_locked(normalize_enabled(source, default=DEFAULT_ENABLED))
                break
        else:
            self._set_enabled_locked(DEFAULT_ENABLED)
        for source in (retention, loaded.get("wallet_retention_days"),
                       environ.get(ENV_RETENTION)):
            if isinstance(source, bool) or source is None:
                continue
            if isinstance(source, (int, float)) or (isinstance(source, str) and source.strip()):
                self._retention = retention_days(source)
                break
        self.updated_at = float(loaded.get("updated_at") or 0.0)
        if self.path and not loaded:
            self._persist_locked()

    # --- состояние -------------------------------------------------------
    def is_enabled(self, chain: object) -> bool:
        """Включена ли сеть. Неизвестная метка (GENERAL, EVM) — не блокируем."""
        key = normalize_network(chain)
        if not key:
            return True
        with self._lock:
            return self._enabled.get(key, True)

    def enabled(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(chain for chain in NETWORK_IDS if self._enabled.get(chain))

    def status(self) -> list[dict]:
        with self._lock:
            return [{"id": chain,
                     "title": NETWORK_META[chain][0],
                     "provider": NETWORK_META[chain][1],
                     "paid": NETWORK_META[chain][2],
                     "enabled": bool(self._enabled.get(chain))}
                    for chain in NETWORK_IDS]

    @property
    def wallet_retention_days(self) -> int:
        with self._lock:
            return self._retention

    def any_paid_enabled(self) -> bool:
        """Есть ли включённая сеть, которая платит CU (по ней резон будить ключ)."""
        with self._lock:
            return any(self._enabled.get(chain) and NETWORK_META[chain][2]
                       for chain in NETWORK_IDS)

    # --- изменения -------------------------------------------------------
    def set_enabled(self, chain: object, flag: object) -> tuple[bool, bool]:
        """Вернуть (изменено, текущее состояние)."""
        key = normalize_network(chain)
        if not key:
            return False, False
        wanted = _truthy(flag)
        with self._lock:
            before = bool(self._enabled.get(key))
            changed = before != wanted
            self._enabled[key] = wanted
            if changed:
                self.updated_at = time.time()
                self._persist_locked()
            return changed, wanted

    def set_retention(self, days: object) -> int:
        value = retention_days(days)
        with self._lock:
            changed = value != self._retention
            self._retention = value
            if changed:
                self.updated_at = time.time()
                self._persist_locked()
        return value

    # --- служебное --------------------------------------------------------
    def _set_enabled_locked(self, chains: tuple[str, ...]) -> None:
        self._enabled = {chain: chain in chains for chain in NETWORK_IDS}

    def _persist_locked(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path) or "."
        payload = {"version": 1, "updated_at": self.updated_at,
                   "networks": list(self.enabled()),
                   "wallet_retention_days": self._retention}
        try:
            os.makedirs(directory, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix="screener_networks-", suffix=".tmp",
                                             dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            finally:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            self.last_error = self.load_error
        except OSError as exc:
            # Тумблер уже подействовал в памяти:persist не вправе ронять админку.
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]

    def as_dict(self) -> dict:
        return {"networks": self.status(),
                "enabled": list(self.enabled()),
                "wallet_retention_days": self.wallet_retention_days,
                "updated_at": self.updated_at,
                "error": self.last_error,
                "load_error": self.load_error}


def switch_from_env(path: str | os.PathLike | None = None) -> NetworkSwitch:
    """Переключатели для запуска без админских настроек (тесты, демо-нога)."""
    return NetworkSwitch(path)
