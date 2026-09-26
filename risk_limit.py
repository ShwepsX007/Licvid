"""Риск-лимиты бирж: поддерживающая маржа (MMR) и предел плеча.

Зачем модуль. Цена ликвидации позиции считается как

    лонг:  P_liq = P_entry × (1 − 1/L + MMR)
    шорт:  P_liq = P_entry × (1 + 1/L − MMR)

и без честного MMR она уезжает ровно настолько, насколько ошиблись мы. У BTC
в первой ступени поддерживающая маржа 0.4–0.65 %, у альта на большом плече —
до 2–3 %: для уровня это разница в разы, а не «второй знак после запятой».
Раньше в проекте этих данных не было вовсе — модуль их и приносит.

Что публично, а что нет (проверено по документации бирж):

    bybit   GET /v5/market/risk-limit?category=linear&symbol=…  — ступени
            (riskLimitValue, maintenanceMargin, initialMargin, maxLeverage);
    okx     GET /api/v5/public/position-tiers?instType=SWAP&tdMode=isolated
            &instFamily=BTC-USDT — ступени (mmr, imr, maxLever, maxSz);
    gate    GET /api/v4/futures/usdt/contracts/{contract} — maintenance_rate,
            leverage_max и границы риск-лимита;
    binance устроена иначе: документированные ступени
            GET /fapi/v1/leverageBracket — USER_DATA, без подписи отказ; такого
            же набора в отладочном (futures/data) API нет. Публично биржа
            отдаёт только venue-wide значения в /fapi/v1/exchangeInfo
            (maintMarginPercent / requiredMarginPercent). Поэтому у Binance
            основным источником идут ступени веб-фида (если он ответит), а
            запасным — exchangeInfo с честной пометкой ``estimated``;
    bitget  GET /api/v2/mix/market/contracts (maxLever) + фид ступеней;
    htx     GET /linear-swap-api/v1/swap_query_elements (ступени плеча) и
            /linear-swap-api/v1/swap_risk_info.

Ответы бирж меняются (короткие имена полей, проценты против долей, вложенность
в ``data`` / ``result`` / ``list``). Поэтому разбор двухслойный: сначала
точечные парсеры под документированный формат, затем общий обход дерева JSON —
он ищет «похожее на ступень» по именам полей (maint*/mmr/maintenance*, imr/
initial*, initialLeverage/maxLever/lever_rate, границы ноционала). Так смена
формата не роняет расчёт: модуль либо достаёт числа, либо честно отдаёт
запасное значение с пометкой оценки.

Значения нормируются одной функцией: ``0.0065`` и ``"0.65"`` — это одно и то
же (0.65 %), потому что у бирж нет единого мнения; правило — «меньше 0.25 —
доля, больше — процент».

Кэш — в памяти и на диске (``data/risk_limits.json``), TTL по умолчанию 6
часов: таблицы меняются редко, а опрос по 40 монетам на шести биржах — это
заметный вес запросов. Всё, что не удалось получить, отдаётся с ``estimated:
True``, и это видно и в интерфейсе, и в ``/api/health``.

Диагностика без запуска сервера: ``python3 tools/check_risk_limits.py BTC``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("liqscore.risk")

HERE = os.path.dirname(os.path.abspath(__file__))

#: Куда складывать кэш ступеней ("" — только память).
CACHE_FILE = os.getenv("LIQSCOPE_RISK_LIMIT_FILE",
                       os.path.join(HERE, "data", "risk_limits.json")).strip()
if CACHE_FILE.lower() in ("0", "none", "off", "false"):
    CACHE_FILE = ""
#: Сколько часов держать ответ биржи, прежде чем перезапросить.
TTL_SEC = max(300.0, float(os.getenv("LIQSCOPE_RISK_LIMIT_TTL", "21600") or 21600))
#: Пауза между запросами к биржам: 40 монет × 6 бирж нельзя пускать очередью.
REQ_GAP_SEC = max(0.0, float(os.getenv("LIQSCOPE_RISK_LIMIT_GAP_MS", "250") or 250) / 1000.0)
#: Полный выключатель (например, на тестовом стенде без сети).
OFF = os.getenv("LIQSCOPE_RISK_LIMIT_OFF", "").strip().lower() in ("1", "true", "yes", "on")
REQUEST_TIMEOUT = float(os.getenv("LIQSCOPE_RISK_LIMIT_TIMEOUT", "12") or 12)

EXCHANGES = ("binance", "bybit", "okx", "gate", "bitget", "htx")

#: Битые или отсутствующие ступени заменяем этими значениями (доля).
#: BTC/ETH — по фактическим первым ступеням бирж; прочие — типовой альт.
DEFAULT_MMR_MAJOR = 0.005
DEFAULT_MMR_ALT = 0.01
MAJORS = ("BTC", "ETH")

#: Имена полей, по которым узнаём ступень (регистр не важен, сравниваем
#: «содержит»). Один список на все биржи — так разбор переживает переименование.
MMR_KEYS = ("maintmarginratio", "maintenancemarginrate", "maintenance_rate",
            "maintmarginpercent", "maintainmarginrate", "maintenancemargin",
            "maintenance_margin_rate", "maintenancemarginrate",
            "maint_margin_ratio", "mmr", "maintain_margin")
IMR_KEYS = ("initialmargin", "imr", "initial_margin", "requiredmarginpercent",
            "required_margin_percent", "initialmarginrate")
LEV_KEYS = ("initialleverage", "maxleverage", "leverage_max", "maxlever",
            "lever_rate", "max_leverage", "maxleveragerate")
MAX_NOTIONAL_KEYS = ("notionalcap", "risklimitvalue", "maxsz", "max_notional",
                     "notional_max", "max_position", "max_lots", "qtycap",
                     "risk_limit_max")
MIN_NOTIONAL_KEYS = ("notionalfloor", "minsz", "min_notional", "notional_min",
                     "min_position", "min_lots", "qtyfloor", "risk_limit_base")
#: Поля, из которых заведомо не надо доставать «ступени» (служебные).
SKIP_KEYS = ("cum", "bracket", "tier", "leverage", "leverage_max_step")

#: Вес запроса по биржам для клиента (секунды ожидания уже в REQ_GAP_SEC).
MAX_RATE = 0.25                 # потолок MMR: больше — уже не маржа, а мусор


async def _get_json(session, url: str, params: Optional[dict] = None):
    """GET с параметрами и коротким таймаутом — своя копия, чтобы не менять
    общий помощник market_feed (у него нет ``params``, а торговым путям он
    нужен как есть)."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    kwargs = {"params": params} if params else {}
    async with session.get(url, timeout=timeout, **kwargs) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return await resp.json(content_type=None)


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def norm_rate(v: Any) -> Optional[float]:
    """Доля MMR/IMR из того, как биржа его назвала.

    ``0.0065`` → 0.0065, ``"0.65"`` → 0.0065, ``"2.5"`` → 0.025. Всё, что не
    меньше 0.25, читаем как процент: биржи не сходятся в единицах (Bybit и OKX
    отдают доли, Binance в exchangeInfo — проценты), а дробной ставки в 30 %
    маржи не бывает. Больше 25 % — уже мусор, а не ступень.
    """
    f = _num(v)
    if f is None or f <= 0:
        return None
    if f >= MAX_RATE:
        f /= 100.0
    if f <= 0 or f > MAX_RATE:
        return None
    return f


def _num_from_key(row: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    """Число из поля, имя которого содержит любой из ``keys``."""
    for k, v in row.items():
        kl = str(k).lower()
        if kl in SKIP_KEYS:
            continue
        if any(x in kl for x in keys):
            n = _num(v)
            if n is not None:
                return n
    return None


def _row_tier(row: Dict[str, Any]) -> Optional[dict]:
    """Строка ответа → ступень риск-лимита (или None, если это не ступень)."""
    if not isinstance(row, dict):
        return None
    mmr = norm_rate(_num_from_key(row, MMR_KEYS))
    if mmr is None:
        return None
    imr = norm_rate(_num_from_key(row, IMR_KEYS))
    lev = _num_from_key(row, LEV_KEYS)
    tier = {
        "mmr": mmr,
        "imr": imr,
        "max_leverage": int(lev) if lev and 1 <= lev <= 2000 else None,
        "max_notional": _num_from_key(row, MAX_NOTIONAL_KEYS),
        "min_notional": _num_from_key(row, MIN_NOTIONAL_KEYS),
    }
    if tier["min_notional"] is None and tier["max_notional"] is None:
        tier["max_notional"] = None
    if tier["mmr"] <= 0:
        return None
    return tier


def walk_tiers(node: Any, depth: int = 0) -> List[dict]:
    """Общий обход ответа биржи: все «похожие на ступень» строки дерева.

    Нужен как второй слой разбора: точечный парсер знает документированный
    формат, а этот — переживает переезд данных в другой контейнер и пересмену
    имён полей. Порядок обхода сохраняем (сверху вниз), чтобы ступень 1
    осталась первой.
    """
    out: List[dict] = []
    if depth > 8:
        return out
    if isinstance(node, dict):
        tier = _row_tier(node)
        if tier is not None:
            out.append(tier)
        for v in node.values():
            if isinstance(v, (dict, list)):
                out.extend(walk_tiers(v, depth + 1))
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                out.extend(walk_tiers(v, depth + 1))
    return out


def sort_tiers(tiers: Iterable[dict]) -> List[dict]:
    """Ступени по возрастанию верхней границы ноционала.

    Биржи отдают их и «как есть», и лестницей сверху вниз; порядок нужен
    ровный — по ступени выбирается MMR для конкретного объёма позиции.
    Ступени без границы считаем последними (они «на всё, что выше»).
    """
    def key(t: dict) -> tuple:
        cap = _num(t.get("max_notional"))
        return (1 if cap is None else 0, cap if cap is not None else 0.0)

    return sorted((t for t in tiers if isinstance(t, dict)), key=key)


def dedupe_tiers(tiers: Iterable[dict]) -> List[dict]:
    """Убрать повторы: одна и та же ступень может попасть в дерево дважды."""
    out: List[dict] = []
    seen = set()
    for t in sort_tiers(tiers):
        key = (round(float(t.get("mmr") or 0), 8),
               t.get("max_notional"), t.get("min_notional"),
               t.get("max_leverage"))
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


# ---------------------------------------------------------------------------
#  Точечные парсеры (документированные форматы). Все — чистые функции:
#  покрыты офлайн-тестами, сети не знают.
# ---------------------------------------------------------------------------

def parse_bybit_risk_limit(payload: Any) -> List[dict]:
    """Bybit v5 /v5/market/risk-limit → ступени.

    ``result.list``: riskLimitValue — верхняя граница ступени в USDT,
    maintenanceMargin / initialMargin — доли, maxLeverage — предел плеча.
    """
    rows = ((payload or {}).get("result") or {}).get("list") \
        if isinstance(payload, dict) else None
    out: List[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        mmr = norm_rate(r.get("maintenanceMargin"))
        if mmr is None:
            continue
        out.append({
            "mmr": mmr,
            "imr": norm_rate(r.get("initialMargin")),
            "max_leverage": int(_num(r.get("maxLeverage")) or 0) or None,
            "max_notional": _num(r.get("riskLimitValue")),
            "min_notional": None,
        })
    return dedupe_tiers(out)


def parse_okx_position_tiers(payload: Any) -> List[dict]:
    """OKX /api/v5/public/position-tiers → ступени.

    ``data``: tier, minSz/maxSz (в контрактах, не в USD), mmr, imr, maxLever.
    Границы остаются в контрактах — в ноционал их переводит вызывающий, если
    знает стоимость контракта; на выбор MMR это не влияет (первая ступень).
    """
    rows = (payload or {}).get("data") if isinstance(payload, dict) else None
    out: List[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        mmr = norm_rate(r.get("mmr"))
        if mmr is None:
            continue
        out.append({
            "mmr": mmr,
            "imr": norm_rate(r.get("imr")),
            "max_leverage": int(_num(r.get("maxLever")) or 0) or None,
            "max_notional": None,
            "max_contracts": _num(r.get("maxSz")),
            "min_contracts": _num(r.get("minSz")),
        })
    return dedupe_tiers(out)


def parse_gate_contract(payload: Any) -> List[dict]:
    """Gate v4 contract: maintenance_rate + leverage_max (одна ступень).

    У Gate поддерживающая маржа лежит прямо в контракте; ступени риск-лимита
    (risk_limit_base/step/max) публично не отдаются отдельным списком, но
    ``maintenance_rate`` — уже честное число биржи для первой ступени.
    """
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return []
    mmr = norm_rate(payload.get("maintenance_rate"))
    if mmr is None:
        return []
    lev = _num(payload.get("leverage_max"))
    return [{
        "mmr": mmr,
        "imr": None,
        "max_leverage": int(lev) if lev else None,
        "max_notional": _num(payload.get("risk_limit_max")),
        "min_notional": _num(payload.get("risk_limit_base")),
    }]


def parse_binance_exchange_info(payload: Any, symbol: str) -> List[dict]:
    """Binance /fapi/v1/exchangeInfo → venue-wide ставка для монеты.

    Документированные ступени (leverageBracket) подписаны, поэтому без ключа
    биржа даёт только это: ``maintMarginPercent`` / ``requiredMarginPercent``
    — общие для всего рынка значения (обычно 2.5 / 5). Считаем их одной
    ступенью и помечаем источником exchangeInfo: пусть честная оценка лучше
    выдуманной точности.
    """
    rows = (payload or {}).get("symbols") if isinstance(payload, dict) else None
    want = str(symbol or "").upper().replace("_", "")
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("symbol") or "").upper() != want:
            continue
        mmr = norm_rate(r.get("maintMarginPercent"))
        if mmr is None:
            return []
        return [{
            "mmr": mmr,
            "imr": norm_rate(r.get("requiredMarginPercent")),
            "max_leverage": None,
            "max_notional": None,
            "min_notional": None,
        }]
    return []


def parse_binance_brackets(payload: Any) -> List[dict]:
    """Ступени Binance (легаси-формат для тех, у кого есть подпись/веб-фид).

    Работает и на объект ``{symbol, brackets:[…]}``, и на список таких
    объектов: каждая ступень — bracket/initialLeverage/notionalCap/
    notionalFloor/maintMarginRatio.
    """
    rows: List[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("brackets") or payload.get("data") or [payload]
    else:
        rows = []
    out: List[dict] = []
    for r in rows:
        tiers = r.get("brackets") if isinstance(r, dict) else None
        if isinstance(tiers, list):
            out.extend(walk_tiers(tiers))
        else:
            tiers = walk_tiers(r)
            out.extend(tiers)
    return dedupe_tiers(out)


# ---------------------------------------------------------------------------
#  Запасные значения
# ---------------------------------------------------------------------------

def default_mmr(symbol: str) -> float:
    """MMR, если биржа не дала ничего: 0.5 % для BTC/ETH, 1 % для остальных."""
    base = str(symbol or "").split("_")[0].upper()
    return DEFAULT_MMR_MAJOR if base in MAJORS else DEFAULT_MMR_ALT


def tier_mmr(tiers: Iterable[dict], notional: Optional[float] = None) -> Optional[float]:
    """MMR ступени, в которую попадает объём позиции.

    Порядок ступеней приводим сами (``sort_tiers``): без границы считаем
    «последней» — она и есть «всё, что выше». Если неционал неизвестен,
    берём первую ступень: типовой розничный объём живёт именно там.
    """
    rows = sort_tiers(tiers)
    if not rows:
        return None
    if notional:
        for t in rows:
            cap = _num(t.get("max_notional"))
            if cap is None or float(notional) <= cap:
                return _num(t.get("mmr"))
    return _num(rows[0].get("mmr"))


# ---------------------------------------------------------------------------
#  Адаптеры бирж: где спросить и как разобрать
# ---------------------------------------------------------------------------

def _binance_web_brackets(symbol: str) -> str:
    """Веб-фид ступеней Binance (публичный, но недокументированный).

    Документированный /fapi/v1/leverageBracket требует подписи, поэтому
    сначала пробуем то, чем пользуется сам сайт биржи; если фид закрылся —
    остаётся exchangeInfo (см. ``_binance_requests``).
    """
    return ("https://www.binance.com/bapi/futures/v1/public/future/"
            "leverage/bracket?symbol=" + str(symbol or "").upper().replace("_", ""))


def _binance_requests(symbol: str) -> List[Tuple[str, str, dict, Callable]]:
    sym = str(symbol or "").upper().replace("_", "")
    return [
        ("leverage-bracket", _binance_web_brackets(symbol), {},
         parse_binance_brackets),
        ("exchange-info", "https://fapi.binance.com/fapi/v1/exchangeInfo",
         {}, lambda p: parse_binance_exchange_info(p, symbol)),
    ]


def _bybit_requests(symbol: str) -> List[Tuple[str, str, dict, Callable]]:
    sym = str(symbol or "").upper().replace("_", "")
    return [
        ("risk-limit", "https://api.bybit.com/v5/market/risk-limit",
         {"category": "linear", "symbol": sym}, parse_bybit_risk_limit),
    ]


def _okx_requests(symbol: str) -> List[Tuple[str, str, dict, Callable]]:
    fam = str(symbol or "").upper().replace("_", "-")
    return [
        ("position-tiers", "https://www.okx.com/api/v5/public/position-tiers",
         {"instType": "SWAP", "tdMode": "isolated", "instFamily": fam},
         parse_okx_position_tiers),
    ]


def _gate_requests(symbol: str) -> List[Tuple[str, str, dict, Callable]]:
    contract = str(symbol or "").upper()
    return [
        ("contract", f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}",
         {}, parse_gate_contract),
    ]


def _bitget_requests(symbol: str) -> List[Tuple[str, str, dict, Callable]]:
    sym = str(symbol or "").upper().replace("_", "")
    return [
        ("contracts", "https://api.bitget.com/api/v2/mix/market/contracts",
         {"productType": "USDT-FUTURES", "symbol": sym},
         lambda p: dedupe_tiers(walk_tiers(p))),
    ]


def _htx_requests(symbol: str) -> List[Tuple[str, str, dict, Callable]]:
    contract = str(symbol or "").upper().replace("_", "-")
    return [
        ("query-elements", "https://api.hbdm.com/linear-swap-api/v1/swap_query_elements",
         {"contract_code": contract}, lambda p: dedupe_tiers(walk_tiers(p))),
        ("risk-info", "https://api.hbdm.com/linear-swap-api/v1/swap_risk_info",
         {"contract_code": contract}, lambda p: dedupe_tiers(walk_tiers(p))),
    ]


#: Порядок бирж для опроса. Имя → функция «что спросить».
REQUESTS: Dict[str, Callable[[str], List[Tuple[str, str, dict, Callable]]]] = {
    "binance": _binance_requests,
    "bybit": _bybit_requests,
    "okx": _okx_requests,
    "gate": _gate_requests,
    "bitget": _bitget_requests,
    "htx": _htx_requests,
}


def _venue(source: str, tiers: List[dict], url: str = "",
           error: str = "") -> dict:
    """Одна биржа в кэше монеты."""
    return {"source": source, "tiers": tiers, "url": url, "error": error,
            "ts": time.time(), "estimated": False}


class RiskLimits:
    """Кэш риск-лимитов по монетам: ступени, MMR и предел плеча.

    Сеть трогает только :meth:`ensure` (и :meth:`ensure_many`). Чтение
    (:meth:`mmr`, :meth:`get`, :meth:`snapshot`) — всегда из памяти, поэтому
    отрисовка графика не ждёт биржу.

    Опрос устроен как у стакана: символы берём по одному, внутри символа
    биржи идут подряд с паузой ``REQ_GAP_SEC``; уже свежие ответы не
    перезапрашиваются (TTL), а неудачные попытки повторяются не чаще, чем раз
    в минуту — иначе забаненная биржа превратит опрос в поток отказов.
    """

    RETRY_AFTER_ERR = 60.0

    def __init__(self, path: Optional[str] = None, ttl: Optional[float] = None,
                 enabled: Optional[bool] = None):
        self.path = CACHE_FILE if path is None else path
        self.ttl = float(TTL_SEC if ttl is None else ttl)
        self.enabled = (not OFF) if enabled is None else bool(enabled)
        self._data: Dict[str, Dict[str, dict]] = {}      # symbol -> {exch: venue}
        self._lock = asyncio.Lock()
        self._sym_locks: Dict[str, asyncio.Lock] = {}
        self._last_req = 0.0
        self._last_err: Dict[str, float] = {}
        self.errors: Dict[str, str] = {}
        self.requests = 0
        self.served = 0
        self.failed = 0
        self.loaded_at = 0.0

    # ----- диск ------------------------------------------------------------
    def load(self) -> int:
        """Прочитать кэш с диска. Возвращает число монет."""
        if not self.path:
            return 0
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return 0
        rows = (data or {}).get("symbols") or {}
        now = time.time()
        n = 0
        for sym, venues in rows.items():
            if not isinstance(venues, dict):
                continue
            clean = {e: v for e, v in venues.items()
                     if isinstance(v, dict) and now - float(v.get("ts") or 0) <= self.ttl * 4}
            if clean:
                self._data[str(sym)] = clean
                n += 1
        self.loaded_at = now
        return n

    def save(self) -> bool:
        if not self.path:
            return False
        try:
            folder = os.path.dirname(os.path.abspath(self.path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            payload = {"ts": time.time(), "ttl": self.ttl, "symbols": self._data}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            return True
        except OSError as e:
            log.debug("кэш риск-лимитов не сохранился: %s", e)
            return False

    # ----- чтение ----------------------------------------------------------
    def _fresh(self, venue: dict, now: Optional[float] = None) -> bool:
        now = now or time.time()
        return (now - float((venue or {}).get("ts") or 0)) <= self.ttl

    def get(self, symbol: str) -> Dict[str, dict]:
        """Кэш монеты: {биржа: {tiers, source, ts, …}} — без сети."""
        return dict(self._data.get(str(symbol or "").upper()) or {})

    def tiers(self, symbol: str, exchange: str) -> List[dict]:
        v = self.get(symbol).get(str(exchange or "").lower()) or {}
        return list(v.get("tiers") or [])

    def mmr(self, symbol: str, exchange: Optional[str] = None,
            notional: Optional[float] = None) -> Optional[float]:
        """MMR из данных бирж: лучшая ступень, иначе None (см. ``mmr_or_default``)."""
        rows = (self.tiers(symbol, exchange) if exchange
                else self._all_tiers(symbol, notional))
        if exchange:
            return tier_mmr(rows, notional)
        best = [t for t in rows if t]
        return tier_mmr(best, notional) if best else None

    def _all_tiers(self, symbol: str, notional: Optional[float] = None) -> List[dict]:
        """Ступени всех бирж монеты одним списком (по возрастанию границы)."""
        out: List[dict] = []
        for exch, venue in self.get(symbol).items():
            for t in venue.get("tiers") or []:
                if isinstance(t, dict):
                    out.append(dict(t, exchange=exch))
        return sort_tiers(out)

    def mmr_or_default(self, symbol: str, exchange: Optional[str] = None,
                       notional: Optional[float] = None,
                       weights: Optional[Dict[str, float]] = None) -> Tuple[float, bool]:
        """Пара (MMR, оценка ли это).

        Если по монете есть ступени хотя бы одной биржи — берём их (при
        нескольких биржах — средневзвешенно по ``weights``, по умолчанию
        поровну), иначе запасное значение и ``estimated=True``.
        """
        venues = self.get(symbol)
        rows: List[Tuple[float, float]] = []      # (mmr, вес)
        for exch, venue in venues.items():
            if exchange and exch != str(exchange).lower():
                continue
            rate = tier_mmr(venue.get("tiers") or [], notional)
            if rate:
                w = float((weights or {}).get(exch, 1.0))
                rows.append((rate, w if w > 0 else 1.0))
        if not rows:
            return default_mmr(symbol), True
        total = sum(w for _r, w in rows) or 1.0
        return sum(r * w for r, w in rows) / total, False

    def max_leverage(self, symbol: str, exchange: Optional[str] = None) -> Optional[int]:
        """Предел плеча: максимум по ступеням (нужен для «докуда вообще можно»)."""
        out: List[float] = []
        for exch, venue in self.get(symbol).items():
            if exchange and exch != str(exchange).lower():
                continue
            for t in venue.get("tiers") or []:
                lev = _num(t.get("max_leverage"))
                if lev:
                    out.append(lev)
        return int(max(out)) if out else None

    def snapshot(self, symbol: str) -> dict:
        """Готовый ответ для API: по биржам — ступени, источник, оценка."""
        sym = str(symbol or "").upper()
        venues = self.get(sym)
        default, estimated = self.mmr_or_default(sym)
        return {
            "symbol": sym,
            "estimated": estimated,
            "mmr": default,
            "max_leverage": self.max_leverage(sym),
            "venues": {
                e: {
                    "tiers": [dict(t) for t in (v.get("tiers") or [])],
                    "source": v.get("source") or "",
                    "ts": v.get("ts"),
                    "age_sec": round(time.time() - float(v.get("ts") or 0), 1),
                    "estimated": bool(v.get("estimated")),
                    "error": v.get("error") or "",
                }
                for e, v in sorted(venues.items())
            },
        }

    # ----- сеть ------------------------------------------------------------
    def _sym_lock(self, symbol: str) -> asyncio.Lock:
        lock = self._sym_locks.get(symbol)
        if lock is None:
            lock = self._sym_locks[symbol] = asyncio.Lock()
        return lock

    async def _throttle(self) -> None:
        """Пауза между запросами к биржам — общая на процесс."""
        if REQ_GAP_SEC <= 0:
            return
        wait = REQ_GAP_SEC - (time.monotonic() - self._last_req)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_req = time.monotonic()

    async def _fetch_venue(self, session, symbol: str, exchange: str,
                           force: bool) -> Optional[dict]:
        """Опрос одной биржи по одной монете. Возвращает venue или None."""
        cached = self.get(symbol).get(exchange) or {}
        if cached and not force and self._fresh(cached):
            return cached
        err_ts = self._last_err.get(f"{symbol}|{exchange}", 0.0)
        if cached.get("error") and time.time() - err_ts < self.RETRY_AFTER_ERR:
            return cached                       # не долбим упавшую биржу
        last_err = ""
        for source, url, params, parser in REQUESTS.get(exchange, lambda _s: [])(symbol):
            try:
                await self._throttle()
                self.requests += 1
                payload = await _get_json(session, url, params)
                tiers = parser(payload)
                if tiers:
                    self.served += 1
                    self.errors.pop(f"{symbol}|{exchange}", None)
                    return _venue(source, tiers, url=url)
                last_err = f"{source}: пустой ответ"
            except Exception as e:              # noqa: BLE001 — биржа важнее формата
                last_err = f"{source}: {str(e)[:120]}"
                log.debug("риск-лимит %s/%s: %s", exchange, symbol, last_err)
        self.failed += 1
        self.errors[f"{symbol}|{exchange}"] = last_err
        self._last_err[f"{symbol}|{exchange}"] = time.time()
        if cached.get("tiers"):
            # был рабочий ответ — не теряем его из-за разовой ошибки
            keep = dict(cached)
            keep["error"] = last_err
            return keep
        return {"source": "", "tiers": [], "url": "", "error": last_err,
                "ts": time.time(), "estimated": True}

    async def ensure(self, session, symbol: str, force: bool = False,
                     exchanges: Optional[Iterable[str]] = None) -> Dict[str, dict]:
        """Догрузить ступени монеты (то, чего нет или что протухло)."""
        sym = str(symbol or "").upper()
        if not sym:
            return {}
        if not self.enabled or session is None:
            return self.get(sym)
        async with self._sym_lock(sym):
            for exch in (list(exchanges) if exchanges else list(EXCHANGES)):
                cur = self.get(sym).get(exch)
                if cur and not force and self._fresh(cur) and cur.get("tiers"):
                    continue
                venue = await self._fetch_venue(session, sym, exch, force)
                if venue:
                    self._data.setdefault(sym, {})[exch] = venue
        return self.get(sym)

    async def ensure_many(self, session, symbols: Iterable[str],
                          force: bool = False, limit: int = 8) -> int:
        """Прогреть несколько монет (для фоновой задачи и ручной диагностики)."""
        n = 0
        for sym in list(symbols)[:max(0, int(limit))]:
            await self.ensure(session, sym, force=force)
            n += 1
        return n

    def want_refresh(self, symbols: Iterable[str]) -> List[str]:
        """Какие из монет стоит прогреть: нет данных или они протухли."""
        out = []
        now = time.time()
        for sym in symbols or []:
            venues = self.get(str(sym).upper())
            need = False
            for exch in EXCHANGES:
                v = venues.get(exch)
                if not v or not self._fresh(v, now) or not v.get("tiers"):
                    need = True
                    break
            if need:
                out.append(str(sym).upper())
        return out

    def status(self) -> dict:
        """Диагностика для /api/health: что спросили и что получили."""
        venues = 0
        symbols = 0
        sources: Dict[str, int] = {}
        for sym, rows in self._data.items():
            if not rows:
                continue
            symbols += 1
            for exch, v in rows.items():
                if v.get("tiers"):
                    venues += 1
                    sources[v.get("source") or "?"] = sources.get(v.get("source") or "?", 0) + 1
        return {
            "enabled": self.enabled,
            "symbols": symbols,
            "venues": venues,
            "sources": sources,
            "requests": self.requests,
            "served": self.served,
            "failed": self.failed,
            "errors": {k: v for k, v in list(self.errors.items())[-8:]},
            "ttl_sec": self.ttl,
            "persist": bool(self.path),
        }
