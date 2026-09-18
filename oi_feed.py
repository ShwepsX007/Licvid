"""Open Interest (открытый интерес) — мультибиржевой трекер.

Все источники — публичные REST без ключей:
  мгновенный OI: binance, bybit, okx, gate, bitget, htx (опрос);
  история 5m:    binance (openInterestHist, до 500 точек),
                 bybit (open-interest 5min, до 200),
                 gate (contract_stats 5m).

Модель данных: по каждому символу хранится серия 5-минутных бакетов
{bucket_ts: {exchange: oi_usd}}. Прошлое — из истории бирж, текущий
бакет — из живого опроса. Изменения m5/h1/h24 и по-свечные oi/oiChg
считаются по НЕПРЕРЫВНОМУ подмножеству бирж с историей (hist_exchanges),
а шапка «текущий OI» — по всем живым ногам. Покрытие честно отдаётся в API.

OI — это запас (stock), а не поток: значение свечи = OI на её конец,
изменение свечи = разница с концом предыдущей.
"""

import asyncio
import bisect
import logging
import os
import time
from collections import deque
from typing import Awaitable, Callable, Deque, Dict, List, Optional, Tuple

import aiohttp

log = logging.getLogger("liqscope.oi")

# Окна изменений для боксов шапки: ключ -> секунд. m1 считается по живым
# опросам (кольцо _live_hist), остальные — по 5-минутным бакетам.
OI_WINDOWS = (("m1", 60), ("m5", 300), ("m15", 900), ("m30", 1800),
               ("h1", 3600), ("h4", 14400), ("h24", 86400))
LIVE_HIST_KEEP_SEC = 900                # окно живых опросов для m1

BUCKET_SEC = 300                    # 5 минут — гранулярность серии
SERIES_KEEP_SEC = 30 * 3600         # окно серии: хватает на h24 + запас
SAMPLE_INTERVAL = 30.0              # живой опрос, секунд
BACKFILL_TTL = 600.0                # историю обновляем раз в 10 минут
STALE_LEG_SEC = 300.0               # нога старше — не входит в текущий тотал
MAX_WATCHED = 12                    # столько символов держим тёплыми

EXCHANGES = ("binance", "bybit", "okx", "gate", "bitget", "htx",
             "dydx", "kraken", "bitfinex", "hyperliquid")
HIST_EXCHANGES = ("binance", "bybit", "gate")   # у кого есть 5m-история

BINANCE_REST = "https://fapi.binance.com"
BYBIT_REST = "https://api.bybit.com"
OKX_REST = "https://www.okx.com"
GATE_REST = "https://api.gateio.ws/api/v4/futures/usdt"
BITGET_REST = "https://api.bitget.com"
HTX_REST = "https://api.hbdm.com"
DYDX_REST = os.getenv("DYDX_REST", "https://indexer.dydx.trade")
KRAKEN_FUT_REST = os.getenv("KRAKEN_FUT_REST",
                            "https://futures.kraken.com/derivatives/api/v3")
BITFINEX_REST = os.getenv("BITFINEX_REST", "https://api-pub.bitfinex.com")
HL_REST = os.getenv("HL_REST", "https://api.hyperliquid.xyz")


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def bucket_5m(ts: float) -> int:
    return int(ts // BUCKET_SEC) * BUCKET_SEC


def base_of(symbol: str) -> str:
    return (symbol or "").split("_")[0].upper()


# ----------------------------------------------------------------------------
# Чистые парсеры ответов бирж (все возвращают USD или None; историю —
# {bucket_ts: usd}). Без сети — покрыты офлайн-тестами.
# ----------------------------------------------------------------------------
def parse_binance_spot(payload, price: Optional[float]) -> Optional[float]:
    """{"openInterest": "123.4", ...} — OI в базовой монете."""
    if not isinstance(payload, dict):
        return None
    oi = _num(payload.get("openInterest"))
    px = _num(price)
    if oi is None or px is None:
        return None
    return oi * px


def parse_binance_hist(rows) -> Dict[int, float]:
    """openInterestHist: [{sumOpenInterestValue, timestamp(ms)}] — уже USD."""
    out: Dict[int, float] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        usd = _num(r.get("sumOpenInterestValue"))
        ts = r.get("timestamp")
        try:
            ts = int(ts) / 1000.0
        except (TypeError, ValueError):
            continue
        if usd is not None:
            # значение относится к КОНЦУ 5-минутки → бакет на один раньше
            out[bucket_5m(ts) - BUCKET_SEC] = usd
    return out


def parse_bybit_oi(payload, price: Optional[float]) -> Tuple[Optional[int], Optional[float]]:
    """{result: {list: [{openInterest, timestamp(ms)}]}} — база × цена."""
    px = _num(price)
    try:
        item = (payload or {}).get("result", {}).get("list", [])[0]
        oi = _num(item.get("openInterest"))
        ts = int(str(item.get("timestamp"))) / 1000.0
    except (TypeError, ValueError, IndexError, AttributeError):
        return None, None
    if oi is None or px is None:
        return None, None
    return int(ts), oi * px


def parse_bybit_hist(payload, price: Optional[float]) -> Dict[int, float]:
    out: Dict[int, float] = {}
    px = _num(price)
    if px is None:
        return out
    try:
        rows = (payload or {}).get("result", {}).get("list", [])
    except AttributeError:
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        oi = _num(r.get("openInterest"))
        try:
            ts = int(str(r.get("timestamp"))) / 1000.0
        except (TypeError, ValueError):
            continue
        if oi is not None:
            out[bucket_5m(ts) - BUCKET_SEC] = oi * px
    return out


def parse_okx_oi(payload, ct_val: float, ct_val_ccy: str,
                 price: Optional[float]) -> Optional[float]:
    """data[0].oi — контракты; ctVal — размер контракта."""
    try:
        items = (payload or {}).get("data", [])
        oi = _num(items[0].get("oi")) if items else None
    except (AttributeError, IndexError, TypeError):
        return None
    if oi is None:
        return None
    try:
        cv = float(ct_val)
    except (TypeError, ValueError):
        return None
    if cv <= 0:
        return None
    if str(ct_val_ccy or "").upper() == "USDT":
        return oi * cv                       # уже доллары
    px = _num(price)
    if px is None:
        return None
    return oi * cv * px                      # контракты → база → USD


def parse_gate_stats(rows) -> Dict[int, float]:
    """contract_stats: [{time(sec), open_interest_usd}] — уже USD."""
    out: Dict[int, float] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        usd = _num(r.get("open_interest_usd"))
        ts = r.get("time", r.get("timestamp", r.get("ts")))
        try:
            ts = float(ts)
            if ts > 1e12:                    # на всякий случай — миллисекунды
                ts /= 1000.0
        except (TypeError, ValueError):
            continue
        if usd is not None:
            out[bucket_5m(ts) - BUCKET_SEC] = usd
    return out


def parse_bitget_oi(payload, price: Optional[float]) -> Optional[float]:
    """{data: {openInterestAmount}} — база × цена."""
    px = _num(price)
    try:
        oi = _num((payload or {}).get("data", {}).get("openInterestAmount"))
    except AttributeError:
        return None
    if oi is None or px is None:
        return None
    return oi * px


def parse_htx_oi(payload, price: Optional[float]) -> Optional[float]:
    """data[0]: {amount (база), volume (контракты)} — берём amount."""
    px = _num(price)
    try:
        rows = (payload or {}).get("data", [])
        amt = _num(rows[0].get("amount")) if rows else None
    except (AttributeError, IndexError, TypeError):
        return None
    if amt is None or px is None:
        return None
    return amt * px


# ----------------------------------------------------------------------------
# Раскладка свечей по OI-серии (чистая функция — для klines и тестов)
# ----------------------------------------------------------------------------
def bucket_changes(cells: Dict[int, Dict[str, float]]) -> Dict[int, Optional[float]]:
    """Изменение бакета к предыдущему по ОБЩИМ ногам (без скачков покрытия).

    cells: {bucket_ts: {exchange: usd}}. Возвращает {bucket_ts: chg|None}.
    """
    keys = sorted(cells)
    out: Dict[int, Optional[float]] = {}
    for n in range(1, len(keys)):
        prev_c, cur_c = cells[keys[n - 1]], cells[keys[n]]
        legs = [e for e in HIST_EXCHANGES if e in prev_c and e in cur_c]
        if not legs:
            out[keys[n]] = None
            continue
        out[keys[n]] = sum(cur_c[e] - prev_c[e] for e in legs)
    return out


def map_candles_to_oi(candle_times: List[int], tf_min: int,
                      levels: Dict[int, float],
                      chgs: Dict[int, Optional[float]]) -> Dict[int, dict]:
    """{время_свечи: {"oi": usd|None, "oiChg": usd|None}}.

    oi = последнее известное значение на конец свечи; oiChg = сумма
    посубакетных изменений внутри свечи (каждое — по общим ногам).
    Для tf=1 изменение светится только на последней минуте 5-минутки
    (честная дискретность истории, не интерполяция).
    """
    tf_sec = max(int(tf_min), 1) * 60
    lvl_keys = sorted(levels)
    out: Dict[int, dict] = {}
    for t in sorted(candle_times):
        end = t + tf_sec
        i = bisect.bisect_right(lvl_keys, end - 1) - 1
        oi = levels[lvl_keys[i]] if i >= 0 else None
        if tf_sec >= BUCKET_SEC:
            span = [b for b in range(t, end, BUCKET_SEC)]
            vals = [chgs[b] for b in span if chgs.get(b) is not None]
            chg = sum(vals) if vals else None
            if vals and len(vals) < len(span):
                pass                        # частичное покрытие — как есть
        else:
            b = bucket_5m(t)
            chg = chgs.get(b) if t + tf_sec >= b + BUCKET_SEC else 0.0
        out[t] = {"oi": oi, "oiChg": chg}
    return out


# ----------------------------------------------------------------------------
# Трекер: живой опрос + история + серии
# ----------------------------------------------------------------------------
async def _get_json(session: aiohttp.ClientSession, url: str,
                    params: Optional[dict] = None,
                    timeout: float = 8.0):
    async with session.get(url, params=params,
                           timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return await resp.json(content_type=None)


async def _post_json(session: aiohttp.ClientSession, url: str, body: dict,
                     timeout: float = 8.0):
    async with session.post(url, json=body,
                            timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return await resp.json(content_type=None)


# -- dYdX / Kraken Futures / Bitfinex / Hyperliquid -------------------------
def parse_dydx_oi(payload, ticker: str, price: Optional[float]) -> Optional[float]:
    """dYdX: /v4/perpetualMarkets -> markets[TICKER].openInterest в БАЗОВОЙ
    монете (BTC), поэтому USD = openInterest * цена."""
    if not isinstance(payload, dict):
        return None
    mk = (payload.get("markets") or {}).get(ticker)
    if not isinstance(mk, dict):
        return None
    qty = _num(mk.get("openInterest"))
    if qty is None or qty <= 0:
        return None
    px = price if (price and price > 0) else _num(mk.get("oraclePrice")) \
        or _num(mk.get("indexPrice"))
    if not px:
        return None
    return qty * px


def parse_kraken_oi(payload, product: str, meta: Optional[dict],
                    price: Optional[float]) -> Optional[float]:
    """Kraken Futures: /derivatives/api/v3/tickers -> tickers[PRODUCT]
    .openInterest в КОНТРАКТАХ. Стоимость контракта берём из метаданных
    /instruments: у inverse-контрактов contractSize уже в USD, у linear —
    в базовой монете, её домножаем на цену."""
    if not isinstance(payload, dict):
        return None
    tk = (payload.get("tickers") or {}).get(product)
    if not isinstance(tk, dict):
        return None
    qty = _num(tk.get("openInterest"))
    if qty is None or qty <= 0:
        return None
    meta = meta or {}
    size = _num(meta.get("contractSize")) or 1.0
    if str(meta.get("type") or "").startswith("futures_linear"):
        px = price if (price and price > 0) else _num(tk.get("markPrice"))
        if not px:
            return None
        return qty * size * px
    return qty * size


def parse_bitfinex_oi(payload, price: Optional[float]) -> Optional[float]:
    """Bitfinex: /v2/status/deriv -> [[SYMBOL, PRICE, SPOT_PRICE, ...]]
    OPEN_INTEREST лежит на индексе 17 и выражен в базовой монете."""
    if not isinstance(payload, list) or not payload:
        return None
    row = payload[0]
    if not isinstance(row, list) or len(row) < 18:
        return None
    qty = _num(row[17])
    if qty is None or qty <= 0:
        return None
    px = price if (price and price > 0) else _num(row[1])
    if not px:
        return None
    return qty * px


def parse_hl_oi(payload, coin: str, price: Optional[float]) -> Optional[float]:
    """Hyperliquid: POST /info {"type":"metaAndAssetCtx"} ->
    [[{universes:[{name:...}]}, [{openInterest, markPx, ...}, ...]]]
    openInterest в базовой монете, markPx рядом с ним."""
    if not isinstance(payload, list) or len(payload) != 2:
        return None
    universes = (payload[0] or {}).get("universe") if isinstance(payload[0], dict) \
        else None
    ctxs = payload[1] if isinstance(payload[1], list) else None
    if not universes or not ctxs:
        return None
    coin = coin.upper()
    for i, u in enumerate(universes):
        if not isinstance(u, dict) or str(u.get("name") or "").upper() != coin:
            continue
        ctx = ctxs[i] if i < len(ctxs) else None
        if not isinstance(ctx, dict):
            return None
        qty = _num(ctx.get("openInterest"))
        if qty is None or qty <= 0:
            return None
        px = price if (price and price > 0) else _num(ctx.get("markPx"))
        if not px:
            return None
        return qty * px
    return None


class OpenInterestTracker:
    def __init__(self,
                 price_fn: Optional[Callable[[str], Optional[float]]] = None):
        self._session: Optional[aiohttp.ClientSession] = None
        self._price = price_fn or (lambda s: None)
        # symbol -> {bucket_ts: {exchange: usd}}
        self._series: Dict[str, Dict[int, Dict[str, float]]] = {}
        # symbol -> {exchange: (ts, usd)} — последний живой опрос
        self._live: Dict[str, Dict[str, Tuple[float, float]]] = {}
        # symbol -> [(ts, {exchange: usd}), ...] — кольцо опросов для m1
        self._live_hist: Dict[str, Deque[Tuple[float, Dict[str, float]]]] = {}
        self._watched: Dict[str, float] = {}          # symbol -> last access
        self._backfilled_at: Dict[str, float] = {}
        self._dydx_markets_cache: dict = {}
        self._kraken_meta: Optional[dict] = None
        self._kraken_meta_cache: dict = {}
        self._lock = asyncio.Lock()
        self.last_errors: Dict[str, str] = {}         # exchange -> ошибка

    def bind(self, session: Optional[aiohttp.ClientSession]):
        self._session = session

    # -- символы ----------------------------------------------------------
    @staticmethod
    def _usd_pair(symbol: str) -> str:
        return f"{base_of(symbol)}USDT"

    def watched_symbols(self) -> List[str]:
        return sorted(self._watched, key=self._watched.get, reverse=True)

    def watch(self, symbol: str):
        now = time.time()
        self._watched[symbol] = now
        if len(self._watched) > MAX_WATCHED:
            for old in sorted(self._watched, key=self._watched.get)[:-MAX_WATCHED]:
                self._watched.pop(old, None)
                self._series.pop(old, None)
                self._live.pop(old, None)

    # -- точечные опросы бирж (возвращают USD или None) -------------------
    async def _fetch_binance(self, symbol: str) -> Optional[float]:
        px = self._price(symbol)
        if px is None:
            mp = await _get_json(
                self._session, f"{BINANCE_REST}/fapi/v1/premiumIndex",
                {"symbol": self._usd_pair(symbol)})
            if isinstance(mp, list):
                mp = mp[0] if mp else {}
            px = (mp or {}).get("markPrice") if isinstance(mp, dict) else None
        data = await _get_json(
            self._session, f"{BINANCE_REST}/fapi/v1/openInterest",
            {"symbol": self._usd_pair(symbol)})
        usd = parse_binance_spot(data, px)
        if usd is not None:
            return usd
        # фолбэк: готовые USD из статистики (обновляется раз в 5 минут)
        hist = await _get_json(
            self._session, f"{BINANCE_REST}/futures/data/openInterestHist",
            {"symbol": self._usd_pair(symbol), "period": "5m", "limit": 1})
        if isinstance(hist, list) and hist:
            return _num(hist[-1].get("sumOpenInterestValue"))
        return None

    async def binance_usd(self, symbol: str) -> Optional[float]:
        """Только Binance: один запрос на монету — для потока «ВСЕ».

        Полный опрос по всем биржам для десятка чужих монет слишком дорог, а
        сравнивать монеты между собой честнее на одной бирже: тогда рост и
        падение OI в ленте — про одно и то же место рынка.
        """
        return await self._fetch_binance(symbol)

    async def _fetch_bybit(self, symbol: str) -> Optional[float]:
        px = self._price(symbol)
        if px is None:
            tk = await _get_json(
                self._session, f"{BYBIT_REST}/v5/market/tickers",
                {"category": "linear", "symbol": self._usd_pair(symbol)})
            try:
                px = tk.get("result", {}).get("list", [])[0].get("markPrice")
            except (AttributeError, IndexError, TypeError):
                px = None
        data = await _get_json(
            self._session, f"{BYBIT_REST}/v5/market/open-interest",
            {"category": "linear", "symbol": self._usd_pair(symbol),
             "intervalTime": "5min", "limit": 1})
        _, usd = parse_bybit_oi(data, px)
        return usd

    async def _fetch_okx(self, symbol: str) -> Optional[float]:
        from liq_api import get_okx_spec          # лениво — без циклов импорта
        base = base_of(symbol)
        data = await _get_json(
            self._session, f"{OKX_REST}/api/v5/public/open-interest",
            {"instType": "SWAP", "instId": f"{base}-USDT-SWAP"})
        spec = await get_okx_spec(self._session, base)
        if not spec:
            return None
        return parse_okx_oi(data, spec.get("ctVal", 1),
                            spec.get("ctValCcy", ""), self._price(symbol))

    async def _fetch_gate(self, symbol: str) -> Optional[float]:
        rows = await _get_json(
            self._session, f"{GATE_REST}/contract_stats",
            {"contract": symbol, "interval": "5m", "limit": 1})
        if isinstance(rows, list) and rows:
            return _num(rows[-1].get("open_interest_usd"))
        return None

    async def _fetch_bitget(self, symbol: str) -> Optional[float]:
        px = self._price(symbol)
        if px is None:
            tk = await _get_json(
                self._session, f"{BITGET_REST}/api/v2/mix/market/ticker",
                {"symbol": self._usd_pair(symbol), "productType": "USDT-FUTURES"})
            try:
                px = (tk or {}).get("data", {}).get("markPrice")
            except AttributeError:
                px = None
        data = await _get_json(
            self._session, f"{BITGET_REST}/api/v2/mix/market/open-interest",
            {"symbol": self._usd_pair(symbol), "productType": "USDT-FUTURES"})
        return parse_bitget_oi(data, px)

    async def _fetch_htx(self, symbol: str) -> Optional[float]:
        data = await _get_json(
            self._session, f"{HTX_REST}/linear-swap-api/v1/swap_open_interest",
            {"contract_code": f"{base_of(symbol)}-USDT"})
        return parse_htx_oi(data, self._price(symbol))

    async def _fetch_dydx(self, symbol: str) -> Optional[float]:
        base = base_of(symbol)
        ticker = f"{base}-USD"
        if not self._dydx_markets_cache:
            self._dydx_markets_cache = await _get_json(
                self._session, f"{DYDX_REST}/v4/perpetualMarkets") or {}
        payload = self._dydx_markets_cache
        usd = parse_dydx_oi(payload, ticker, self._price(symbol))
        if usd is None and payload:
            # кэш протух или монета появилась позже — обновляем один раз
            self._dydx_markets_cache = await _get_json(
                self._session, f"{DYDX_REST}/v4/perpetualMarkets") or {}
            usd = parse_dydx_oi(self._dydx_markets_cache, ticker,
                                self._price(symbol))
        return usd

    async def _kraken_product(self, symbol: str) -> Optional[str]:
        base = base_of(symbol).upper()
        product = "PF_XBTUSD" if base == "BTC" else f"PF_{base}USD"
        if self._kraken_meta is None:
            self._kraken_meta = await _get_json(
                self._session, f"{KRAKEN_FUT_REST}/instruments") or {}
        instr = (self._kraken_meta.get("instruments") or {})
        if product not in instr:
            return None
        self._kraken_meta_cache = instr
        return product

    async def _fetch_kraken(self, symbol: str) -> Optional[float]:
        product = await self._kraken_product(symbol)
        if not product:
            return None
        data = await _get_json(self._session, f"{KRAKEN_FUT_REST}/tickers")
        meta = (self._kraken_meta_cache or {}).get(product)
        return parse_kraken_oi(data, product, meta, self._price(symbol))

    async def _fetch_bitfinex(self, symbol: str) -> Optional[float]:
        base = base_of(symbol).upper()
        pair = "tBTCF0:USTF0" if base == "BTC" else f"t{base}F0:USTF0"
        data = await _post_json(
            self._session, f"{BITFINEX_REST}/v2/status/deriv", {"keys": [pair]})
        return parse_bitfinex_oi(data, self._price(symbol))

    async def _fetch_hyperliquid(self, symbol: str) -> Optional[float]:
        coin = base_of(symbol).upper()
        payload = await _post_json(self._session, f"{HL_REST}/info",
                                   {"type": "metaAndAssetCtx"})
        return parse_hl_oi(payload, coin, self._price(symbol))

    # -- история 5m -------------------------------------------------------
    async def _hist_binance(self, symbol: str) -> Dict[int, float]:
        rows = await _get_json(
            self._session, f"{BINANCE_REST}/futures/data/openInterestHist",
            {"symbol": self._usd_pair(symbol), "period": "5m", "limit": 500})
        return parse_binance_hist(rows)

    async def _hist_bybit(self, symbol: str) -> Dict[int, float]:
        px = self._price(symbol)
        if px is None:
            return {}
        data = await _get_json(
            self._session, f"{BYBIT_REST}/v5/market/open-interest",
            {"category": "linear", "symbol": self._usd_pair(symbol),
             "intervalTime": "5min", "limit": 200})
        return parse_bybit_hist(data, px)

    async def _hist_gate(self, symbol: str) -> Dict[int, float]:
        rows = await _get_json(
            self._session, f"{GATE_REST}/contract_stats",
            {"contract": symbol, "interval": "5m", "limit": 500})
        return parse_gate_stats(rows)

    # -- слияние ----------------------------------------------------------
    def _merge(self, symbol: str, exch: str, points: Dict[int, float],
               overwrite: bool = False):
        buckets = self._series.setdefault(symbol, {})
        for b, v in points.items():
            cell = buckets.setdefault(b, {})
            if overwrite or exch not in cell:
                cell[exch] = v

    async def backfill_symbol(self, symbol: str, force: bool = False) -> Dict[str, int]:
        """Подтянуть 5m-историю (binance/bybit/gate). Возвращает счётчики."""
        if self._session is None:
            return {}
        now = time.time()
        if not force and now - self._backfilled_at.get(symbol, 0) < BACKFILL_TTL:
            return {}
        self.watch(symbol)
        jobs = {"binance": self._hist_binance(symbol),
                "bybit": self._hist_bybit(symbol),
                "gate": self._hist_gate(symbol)}
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        counts: Dict[str, int] = {}
        async with self._lock:
            for exch, res in zip(jobs, results):
                if isinstance(res, Exception):
                    self.last_errors[exch] = f"{type(res).__name__}: {res}"
                    log.debug("oi backfill %s %s: %s", symbol, exch, res)
                    continue
                if res:
                    self._merge(symbol, exch, res)
                    counts[exch] = len(res)
            self._prune_symbol(symbol)
        self._backfilled_at[symbol] = now
        return counts

    async def sample_symbol(self, symbol: str) -> Dict[str, float]:
        """Живой опрос всех бирж → текущий бакет. Возвращает ноги в USD."""
        if self._session is None:
            return {}
        self.watch(symbol)
        jobs = {"binance": self._fetch_binance(symbol),
                "bybit": self._fetch_bybit(symbol),
                "okx": self._fetch_okx(symbol),
                "gate": self._fetch_gate(symbol),
                "bitget": self._fetch_bitget(symbol),
                "htx": self._fetch_htx(symbol),
                "dydx": self._fetch_dydx(symbol),
                "kraken": self._fetch_kraken(symbol),
                "bitfinex": self._fetch_bitfinex(symbol),
                "hyperliquid": self._fetch_hyperliquid(symbol)}
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        legs: Dict[str, float] = {}
        now = time.time()
        for exch, res in zip(jobs, results):
            if isinstance(res, Exception):
                self.last_errors[exch] = f"{type(res).__name__}: {res}"
                log.debug("oi sample %s %s: %s", symbol, exch, res)
                continue
            if isinstance(res, (int, float)) and res > 0:
                legs[exch] = float(res)
        if legs:
            async with self._lock:
                live = self._live.setdefault(symbol, {})
                for exch, usd in legs.items():
                    live[exch] = (now, usd)
                hist = self._live_hist.setdefault(symbol, deque(maxlen=40))
                hist.append((now, dict(legs)))
                while hist and now - hist[0][0] > LIVE_HIST_KEEP_SEC:
                    hist.popleft()
                buckets = self._series.setdefault(symbol, {})
                cell = buckets.setdefault(bucket_5m(now), {})
                for exch, usd in legs.items():
                    cell[exch] = usd                   # живое свежее истории
                self._prune_symbol(symbol)
        return legs

    def live_series(self, symbol: str) -> Dict[float, float]:
        """Сумма OI по живым опросам: {время: тотал}.

        Кольцо ``_live_hist`` обновляется каждым опросом (десятки секунд), а
        5-минутные бакеты — редко: по бакетам микрографик OI стоял «как
        картинка». Здесь тот же горизонт, что у m1, но плотным рядом.
        """
        hist = list(self._live_hist.get(symbol) or [])
        out: Dict[float, float] = {}
        for ts, legs in hist:
            try:
                total = sum(float(v) for v in (legs or {}).values())
            except (TypeError, ValueError):
                continue
            if total > 0:
                out[round(float(ts), 3)] = round(total, 2)
        return out

    def _prune_symbol(self, symbol: str):
        cutoff = time.time() - SERIES_KEEP_SEC
        buckets = self._series.get(symbol)
        if not buckets:
            return
        for b in [b for b in buckets if b < cutoff]:
            buckets.pop(b, None)

    async def ensure_symbol(self, symbol: str):
        """Гарантировать данные по символу: история + свежий опрос при нужде."""
        await self.backfill_symbol(symbol)
        live = self._live.get(symbol) or {}
        if not live or time.time() - max(ts for ts, _ in live.values()) > SAMPLE_INTERVAL * 2:
            await self.sample_symbol(symbol)

    # -- чтение (без сети) ------------------------------------------------
    def _subset_totals(self, symbol: str) -> Dict[int, float]:
        """Сырые уровни: сумма ног с историей в каждом бакете (могут прыгать
        при смене покрытия — для непрерывности есть series())."""
        out: Dict[int, float] = {}
        for b, cell in (self._series.get(symbol) or {}).items():
            s = sum(v for e, v in cell.items() if e in HIST_EXCHANGES)
            if s > 0:
                out[b] = s
        return out

    def series(self, symbol: str) -> Dict[int, float]:
        """Непрерывный ряд уровней: идём по бакетам, прибавляя согласованные
        изменения (по общим ногам). Разрыв без общих ног — перепривязка."""
        cells = self._series.get(symbol) or {}
        raw = {b: sum(v for e, v in cell.items() if e in HIST_EXCHANGES)
               for b, cell in cells.items()}
        raw = {b: v for b, v in raw.items() if v > 0}
        if not raw:
            return {}
        chg = bucket_changes(cells)
        keys = sorted(raw)
        out = {keys[0]: raw[keys[0]]}
        for n in range(1, len(keys)):
            d = chg.get(keys[n])
            out[keys[n]] = out[keys[n - 1]] + d if d is not None else raw[keys[n]]
        return out

    def bucket_chg(self, symbol: str) -> Dict[int, Optional[float]]:
        return bucket_changes(self._series.get(symbol) or {})

    def snapshot(self, symbol: str) -> dict:
        now = time.time()
        legs = {}
        total = 0.0
        newest = 0.0
        for exch, (ts, usd) in (self._live.get(symbol) or {}).items():
            if now - ts <= STALE_LEG_SEC and usd > 0:
                legs[exch] = {"usd": usd, "age_sec": round(now - ts, 1)}
                total += usd
            newest = max(newest, ts)
        return {"total_usd": total if legs else None,
                "per_exchange": legs,
                "ts": newest or None,
                "stale_sec": round(now - newest, 1) if newest else None}

    def _change_m1(self, symbol: str) -> Tuple[Optional[dict], bool]:
        """Изменение за ~минуту по кольцу живых опросов (все ноги)."""
        hist = list(self._live_hist.get(symbol) or [])
        if len(hist) < 2:
            return None, True
        cur_ts, cur = hist[-1]
        ref_ts, ref = min(hist[:-1], key=lambda p: abs(p[0] - (cur_ts - 60)))
        legs = [e for e in cur if e in ref]
        if not legs:
            return None, True
        base = sum(ref[e] for e in legs)
        if base <= 0:
            return None, True
        delta = sum(cur[e] - ref[e] for e in legs)
        dt = cur_ts - ref_ts
        return ({"usd": round(delta, 2), "pct": round(delta / base * 100, 3)},
                not (45 <= dt <= 150))

    def changes(self, symbol: str) -> dict:
        """Изменения за окна OI_WINDOWS {usd, pct} + partial.

        Бакетные окна считаем по ногам, присутствующим И в текущем, И в
        опорном бакете, — иначе подключение новой биржи дало бы ложный
        скачок дельты. m1 — по кольцу живых опросов.
        """
        cells = self._series.get(symbol) or {}
        out = {name: None for name, _ in OI_WINDOWS}
        out["partial"] = {name: True for name, _ in OI_WINDOWS}
        out["hist_exchanges"] = [e for e in HIST_EXCHANGES
                                 if any(e in cell for cell in cells.values())]
        m1, m1_partial = self._change_m1(symbol)
        out["m1"] = m1
        out["partial"]["m1"] = m1_partial
        if not cells:
            return out
        keys = sorted(cells)
        cur_cell = cells[keys[-1]]
        now = time.time()
        for name, window in OI_WINDOWS:
            if name == "m1":
                continue                       # уже посчитано по опросам
            ref_ts = now - window
            i = bisect.bisect_right(keys, ref_ts) - 1
            if i < 0:
                continue                       # данных меньше окна — SKIP
            ref_cell = cells[keys[i]]
            legs = [e for e in HIST_EXCHANGES
                    if e in cur_cell and e in ref_cell]
            if not legs:
                continue
            base = sum(ref_cell[e] for e in legs)
            if base <= 0:
                continue
            delta = sum(cur_cell[e] - ref_cell[e] for e in legs)
            out[name] = {"usd": round(delta, 2),
                         "pct": round(delta / base * 100, 3)}
            out["partial"][name] = (
                len(legs) < len(HIST_EXCHANGES)
                or keys[i] < ref_ts - BUCKET_SEC * 2)
        return out

    def payload(self, symbol: str) -> dict:
        """Готовый ответ для /api/oi."""
        snap = self.snapshot(symbol)
        ch = self.changes(symbol)
        return {"symbol": symbol,
                "total_usd": snap["total_usd"],
                "per_exchange": {e: v["usd"] for e, v in snap["per_exchange"].items()},
                "live_exchanges": sorted(snap["per_exchange"]),
                "hist_exchanges": ch["hist_exchanges"],
                "changes": {k: ch[k] for k, _ in OI_WINDOWS},
                "partial": ch["partial"],
                "ts": snap["ts"],
                "stale_sec": snap["stale_sec"]}
