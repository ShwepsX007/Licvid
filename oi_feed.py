"""Open Interest (открытый интерес) — мультибиржевой трекер.

Все источники — публичные REST без ключей:
  мгновенный OI: binance, bybit, okx, gate, bitget, htx, bitmex (опрос);
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
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp

log = logging.getLogger("licvid.oi")

BUCKET_SEC = 300                    # 5 минут — гранулярность серии
SERIES_KEEP_SEC = 30 * 3600         # окно серии: хватает на h24 + запас
SAMPLE_INTERVAL = 30.0              # живой опрос, секунд
BACKFILL_TTL = 600.0                # историю обновляем раз в 10 минут
STALE_LEG_SEC = 300.0               # нога старше — не входит в текущий тотал
MAX_WATCHED = 12                    # столько символов держим тёплыми

EXCHANGES = ("binance", "bybit", "okx", "gate", "bitget", "htx", "bitmex")
HIST_EXCHANGES = ("binance", "bybit", "gate")   # у кого есть 5m-история

BINANCE_REST = "https://fapi.binance.com"
BYBIT_REST = "https://api.bybit.com"
OKX_REST = "https://www.okx.com"
GATE_REST = "https://api.gateio.ws/api/v4/futures/usdt"
BITGET_REST = "https://api.bitget.com"
HTX_REST = "https://api.hbdm.com"
BITMEX_REST = "https://www.bitmex.com/api/v1"


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


def parse_bitmex_oi(payload, meta: Optional[dict],
                    price: Optional[float]) -> Optional[float]:
    """[{openInterest (контракты), lastPrice}].

    У инверсных (XBTUSD) 1 контракт = 1 USD, у линейных — через
    multiplier из метаданных инструментов (та же математика, что
    и для ликвидаций в market_feed.parse_bitmex_msg).
    """
    rows = payload if isinstance(payload, list) else []
    if not rows or not isinstance(rows[0], dict):
        return None
    try:
        contracts = float(rows[0].get("openInterest") or 0)
    except (TypeError, ValueError):
        return None
    if contracts <= 0:
        return None
    meta = meta or {}
    if meta.get("inverse"):
        return contracts
    mult = meta.get("multiplier") or 0
    try:
        mult = float(mult)
    except (TypeError, ValueError):
        return None
    if mult <= 0:
        return None
    px = _num(price)
    if px is None:                           # запасной путь — цена из ответа
        px = _num(rows[0].get("lastPrice"))
    if px is None:
        return None
    return contracts * mult * px


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


class OpenInterestTracker:
    def __init__(self,
                 price_fn: Optional[Callable[[str], Optional[float]]] = None,
                 bitmex_meta_fn: Optional[Callable[[], dict]] = None):
        self._session: Optional[aiohttp.ClientSession] = None
        self._price = price_fn or (lambda s: None)
        self._bitmex_meta = bitmex_meta_fn or (lambda: {})
        # symbol -> {bucket_ts: {exchange: usd}}
        self._series: Dict[str, Dict[int, Dict[str, float]]] = {}
        # symbol -> {exchange: (ts, usd)} — последний живой опрос
        self._live: Dict[str, Dict[str, Tuple[float, float]]] = {}
        self._watched: Dict[str, float] = {}          # symbol -> last access
        self._backfilled_at: Dict[str, float] = {}
        self._bitmex_sym_cache: Dict[str, Optional[str]] = {}
        self._lock = asyncio.Lock()
        self.last_errors: Dict[str, str] = {}         # exchange -> ошибка

    def bind(self, session: Optional[aiohttp.ClientSession]):
        self._session = session

    # -- символы ----------------------------------------------------------
    @staticmethod
    def _usd_pair(symbol: str) -> str:
        return f"{base_of(symbol)}USDT"

    def bitmex_symbol(self, symbol: str) -> Optional[str]:
        if symbol in self._bitmex_sym_cache:
            return self._bitmex_sym_cache[symbol]
        base = base_of(symbol)
        cands = ["XBTUSD", "XBTUSDT"] if base == "BTC" else \
            [f"{base}USDT", f"{base}USD"]
        known = self._bitmex_meta() or {}
        pick = next((c for c in cands if c in known), None)
        if pick is None and (not known or base == "BTC"):
            pick = cands[0]          # метаданные ещё не загрузились — пробуем
        if pick is not None and (known or base == "BTC"):
            self._bitmex_sym_cache[symbol] = pick
        return pick

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

    async def _fetch_bitmex(self, symbol: str) -> Optional[float]:
        bsym = self.bitmex_symbol(symbol)
        if not bsym:
            return None
        data = await _get_json(
            self._session, f"{BITMEX_REST}/instrument", {"symbol": bsym})
        meta = (self._bitmex_meta() or {}).get(bsym) or {}
        return parse_bitmex_oi(data, meta, self._price(symbol))

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
        """Живой опрос всех 7 бирж → текущий бакет. Возвращает ноги в USD."""
        if self._session is None:
            return {}
        self.watch(symbol)
        jobs = {"binance": self._fetch_binance(symbol),
                "bybit": self._fetch_bybit(symbol),
                "okx": self._fetch_okx(symbol),
                "gate": self._fetch_gate(symbol),
                "bitget": self._fetch_bitget(symbol),
                "htx": self._fetch_htx(symbol),
                "bitmex": self._fetch_bitmex(symbol)}
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
                buckets = self._series.setdefault(symbol, {})
                cell = buckets.setdefault(bucket_5m(now), {})
                for exch, usd in legs.items():
                    cell[exch] = usd                   # живое свежее истории
                self._prune_symbol(symbol)
        return legs

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

    def changes(self, symbol: str) -> dict:
        """Изменения за 5м/1ч/24ч {usd, pct} + partial.

        Считаем по ногам, присутствующим И в текущем, И в опорном бакете, —
        иначе подключение новой биржи дало бы ложный скачок дельты.
        """
        cells = self._series.get(symbol) or {}
        out = {"m5": None, "h1": None, "h24": None,
               "partial": {"m5": True, "h1": True, "h24": True},
               "hist_exchanges": [e for e in HIST_EXCHANGES
                                  if any(e in cell for cell in cells.values())]}
        if not cells:
            return out
        keys = sorted(cells)
        cur_cell = cells[keys[-1]]
        now = time.time()
        for name, window in (("m5", 300), ("h1", 3600), ("h24", 86400)):
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
                "changes": {k: ch[k] for k in ("m5", "h1", "h24")},
                "partial": ch["partial"],
                "ts": snap["ts"],
                "stale_sec": snap["stale_sec"]}
