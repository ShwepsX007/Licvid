"""Перевес сторон: тейкерский баланс и фандинг — кто набирал позиции.

Уровень ликвидаций — это не только цена, но и сторона: вверх стоят шорты,
вниз лонги. Проект уже знает живую CVD по своим тикам (``FlowFeed``), но
истории его мало: она о последних часах, а позиции набирались за месяцы.

Поэтому здесь берём публичные данные биржи о балансе тейкера (``taker
long/short``: сколько покупок и продаж прошло через биржу) и о фандинге:
положительный фандинг значит, что лонгов больше и они платят шортам.
Первое — точная сторона, но живёт неделю-две; второе — грубая сторона, но
доступна месяцами и переживает любую дыру в истории.

Если тейкер не отдался, пробуем баланс счетов (``globalLongShortAccountRatio``
— LSR: доля счетов в лонгах), а если и его нет — средний LSR по четырём
биржам (``liq_api.get_multi_lsr``): одна биржа молчит, а рынок всё равно виден.

Модуль не ходит в сеть сам по себе: ``ensure`` вызывается сервером и
кэширует данные на TTL (по умолчанию 15 минут). Всё, что можно проверить без
сети — чистые парсеры сверху.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from volume_profile import bucket_of

log = logging.getLogger("liqscore.side")

BINANCE_REST = os.getenv("LIQSCOPE_BINANCE_REST", "https://fapi.binance.com").rstrip("/")
#: Как долго держим уже загруженный перевес (секунды).
TTL_SEC = max(60.0, float(os.getenv("LIQSCOPE_SIDE_TTL", "900") or 900))
#: Сколько 5-минутных слотов просить (максимум биржи — 500, это ~1.7 суток).
TAKER_LIMIT = max(30, min(int(os.getenv("LIQSCOPE_SIDE_LIMIT", "500") or 500), 500))
#: Сколько слотов баланса счетов просить (максимум биржи — 500).
LSR_LIMIT = max(30, min(int(os.getenv("LIQSCOPE_SIDE_LSR_LIMIT", "500") or 500), 500))
#: Сколько ставок фандинга просить (максимум 1000, это ~11 суток).
FUNDING_LIMIT = max(30, min(int(os.getenv("LIQSCOPE_FUNDING_LIMIT", "1000") or 1000), 1000))
#: Во сколько раз фандинг тянет перевес: 0.01 % (нормальный рынок) даёт ±5 %.
FUNDING_SCALE = float(os.getenv("LIQSCOPE_SIDE_FUNDING_SCALE", "500") or 500)
#: Потолок смещения от фандинга: сильнее 65/35 сторона по ставке не читается.
FUNDING_CLIP = min(0.3, max(0.0, float(os.getenv("LIQSCOPE_SIDE_FUNDING_CLIP", "0.15") or 0.15)))
#: Насколько близко к слоту ищем замер тейкера (секунды, по умолчанию ±10 мин).
TAKER_TOL_SEC = max(60.0, float(os.getenv("LIQSCOPE_SIDE_TOL", "600") or 600))
SIDE_OFF = os.getenv("LIQSCOPE_SIDE_OFF", "").strip().lower() in ("1", "true", "yes", "on")

#: Откуда пришёл перевес — показываем в заметках к картинке уровней.
SOURCES = ("taker", "lsr", "funding", "ticks", "none")
#: Сколько ждём биржу (секунды) — как у риск-лимитов, чтобы запросы не висели.
REQUEST_TIMEOUT = max(3.0, float(os.getenv("LIQSCOPE_SIDE_TIMEOUT", "12") or 12))


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def taker_share(ratio: Any) -> Optional[float]:
    """Доля покупок из отношения «покупки/продажи»: r/(1+r)."""
    r = _num(ratio)
    if r is None or r <= 0:
        return None
    return r / (1.0 + r)


def funding_share(rate: Any, scale: float = FUNDING_SCALE,
                  clip: float = FUNDING_CLIP) -> Optional[float]:
    """Грубая доля лонгов из ставки фандинга (клип по краям).

    Положительная ставка — лонгов больше, они платят шортам. Смещение от
    нейтральных 50 % линейно по ставке и ограничено ``clip``: по одному
    фандингу точную сторону не увидеть, только перекос.
    """
    r = _num(rate)
    if r is None:
        return None
    shift = max(-clip, min(clip, r * scale))
    return 0.5 + shift


def parse_taker_ratio(rows: Iterable[dict], step: int = 300) -> Dict[int, dict]:
    """Ответ ``takerlongshortRatio`` → {начало слота: {доля, отношение}}.

    Нужен и ``buySellRatio``, и явные ``buyVol``/``sellVol`` (по ним доля
    точнее). Рядом с долей держим исходное отношение биржи: по нему видно,
    насколько замер уверенный. Мусорные строки пропускаем: биржа иногда
    отдаёт нули.
    """
    out: Dict[int, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ts = _num(row.get("timestamp")) or _num(row.get("time"))
        if ts is None:
            continue
        if ts > 10_000_000_000:                 # миллисекунды
            ts = ts / 1000.0
        share = ratio = None
        buy, sell = _num(row.get("buyVol")), _num(row.get("sellVol"))
        if buy is not None and sell is not None and buy + sell > 0:
            share = buy / (buy + sell)
            ratio = buy / sell if sell > 0 else None
        if share is None:
            raw = row.get("buySellRatio") or row.get("longShortRatio")
            ratio = _num(raw)
            share = taker_share(raw)
        if share is None:
            continue
        b = int(ts // max(int(step), 1)) * max(int(step), 1)
        out[b] = {"buy_share": min(max(share, 0.0), 1.0), "ratio": ratio}
    return out


def parse_global_lsr(rows: Iterable[dict], step: int = 300) -> Dict[int, dict]:
    """Ответ ``globalLongShortAccountRatio`` → {слот: {доля лонгов, отношение}}.

    Глобальное соотношение счетов лонг/шорт: сколько аккаунтов держат лонги.
    Это не объём (у крупного счёта и у мелкого вес разный), поэтому источник
    идёт запасным к тейкерскому балансу — но он переживает дыры в истории и
    подтверждает сторону там, где тейкер не отдался.
    """
    out: Dict[int, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ts = _num(row.get("timestamp")) or _num(row.get("time"))
        if ts is None:
            continue
        if ts > 10_000_000_000:                 # миллисекунды
            ts = ts / 1000.0
        ratio = _num(row.get("longShortRatio"))
        long_share = taker_share(ratio)
        if long_share is None:
            long_account = _num(row.get("longAccount"))
            short_account = _num(row.get("shortAccount"))
            if long_account is not None and short_account is not None \
                    and long_account + short_account > 0:
                long_share = long_account / (long_account + short_account)
        if long_share is None:
            continue
        b = int(ts // max(int(step), 1)) * max(int(step), 1)
        out[b] = {"long_share": min(max(long_share, 0.0), 1.0), "ratio": ratio}
    return out


def _share_of(row: Any) -> Optional[float]:
    """Доля из записи ряда: либо число, либо словарь с ``buy_share``/``long_share``."""
    if isinstance(row, dict):
        for key in ("buy_share", "long_share"):
            if row.get(key) is not None:
                return _num(row.get(key))
        return None
    return _num(row)


def parse_funding(rows: Iterable[dict]) -> List[Tuple[int, float]]:
    """Ответ ``fundingRate`` → отсортированный [(время, ставка)]."""
    out: List[Tuple[int, float]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ts = _num(row.get("fundingTime")) or _num(row.get("ts"))
        rate = _num(row.get("fundingRate"))
        if ts is None or rate is None:
            continue
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        out.append((int(ts), rate))
    out.sort()
    return out


def nearest_bucket(rows: Dict[int, Any], ts: Any,
                   tol: float = TAKER_TOL_SEC) -> Optional[float]:
    """Доля покупок ближайшего слота в пределах допуска (иначе None)."""
    if not rows:
        return None
    try:
        want = float(ts)
    except (TypeError, ValueError):
        return None
    best, best_gap = None, None
    for b, row in rows.items():
        share = _share_of(row)
        if share is None:
            continue
        gap = abs(float(b) + 150.0 - want)      # середина 5-минутного слота
        # при равной близости берём более свежий слот: сторона позиций меняется
        # быстро, и вчерашний замер здесь хуже сегодняшнего
        if best_gap is None or gap <= best_gap:
            best, best_gap = share, gap
    if best_gap is not None and best_gap <= float(tol):
        return best
    return None


def funding_at(points: List[Tuple[int, float]], ts: Any) -> Optional[float]:
    """Ставка фандинга, действовавшая на момент ``ts`` (последняя не позже).

    Раньше самой истории — None: подставлять первую известную ставку значит
    выдумывать сторону за месяцы, которых мы не видели.
    """
    if not points:
        return None
    try:
        want = float(ts)
    except (TypeError, ValueError):
        return None
    out = None
    for t, rate in points:
        if float(t) <= want:
            out = rate
        else:
            break
    return out


#: Прежнее имя помощника: оставлено, чтобы не ломать внешние вызовы.
rate_at = funding_at


class SideFeed:
    """Кэш перевеса сторон по монетам: сеть трогает только ``ensure``."""

    def __init__(self, session: Any = None, ttl: float = TTL_SEC,
                 source: str = BINANCE_REST, off: bool = SIDE_OFF,
                 enabled: Optional[bool] = None):
        self.session = session
        self.ttl = float(ttl)
        self.source = str(source or BINANCE_REST).rstrip("/")
        # ``enabled`` — понятный снаружи выключатель (админка/тесты), ``off`` —
        # переменная окружения; явный аргумент сильнее.
        self.off = (not bool(enabled)) if enabled is not None else bool(off)
        self._taker: Dict[str, Dict[int, dict]] = {}
        self._lsr: Dict[str, Dict[int, dict]] = {}
        self._funding: Dict[str, List[Tuple[int, float]]] = {}
        self._at: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self.errors: Dict[str, str] = {}
        self.fetches = 0

    # ----- сеть ------------------------------------------------------------
    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET с параметрами: своя копия помощника (общий ``market_feed._get_json``
        параметров не принимает, а здесь без них не обойтись)."""
        import aiohttp
        session = self.session
        if session is None:
            return None
        url = self.source + path
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        kwargs = {"params": params} if params else {}
        async with session.get(url, timeout=timeout, **kwargs) as resp:
            if getattr(resp, "status", 200) != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
        if isinstance(data, dict):
            if data.get("code") not in (None, 0, "0") and data.get("msg"):
                raise RuntimeError(str(data.get("msg"))[:120])
            return data.get("data") if "data" in data else data
        return data

    def snapshot(self, symbol: str) -> dict:
        """Что известно по монете: свежие доли, ставка и ошибки загрузки."""
        sym = str(symbol or "").upper()
        taker = self._taker.get(sym) or {}
        lsr = self._lsr.get(sym) or {}
        funding = self._funding.get(sym) or []
        errs = [f"{k.split(':', 1)[0]}: {v}" for k, v in self.errors.items()
                if k.endswith(":" + sym)]
        return {
            "symbol": sym,
            "taker": (taker[max(taker)]["buy_share"] if taker else None),
            "lsr": (lsr[max(lsr)]["long_share"] if lsr else None),
            "funding": (funding[-1][1] if funding else None),
            "buckets": len(taker) or len(lsr),
            "error": "; ".join(errs)[:200],
        }

    async def ensure(self, session: Any = None, symbol: str = "",
                     force: bool = False) -> dict:
        """Подтянуть перевес и фандинг, если кэш устарел.

        Возвращает снимок (``taker``/``lsr``/``funding``/``error``): вызывающему
        важно не только «данные есть», но и что именно не отдалось.
        """
        sym = str(symbol or "").upper()
        if not sym or self.off:
            return {"symbol": sym, "taker": None, "lsr": None, "funding": None,
                    "buckets": 0, "error": "выключено"}
        if session is not None:
            self.session = session
        if self.session is None:
            return {"symbol": sym, "taker": None, "lsr": None, "funding": None,
                    "buckets": 0, "error": "нет сессии"}
        now = time.time()
        if not force and now - self._at.get(sym, 0.0) < self.ttl:
            return self.snapshot(sym)
        lock = self._locks.setdefault(sym, asyncio.Lock())
        async with lock:
            if not force and time.time() - self._at.get(sym, 0.0) < self.ttl:
                return self.snapshot(sym)
            base = sym.replace("_USDT", "").replace("_", "")
            await self._fetch_taker(base, sym)
            if not self._taker.get(sym):
                # тейкера нет — сторона из баланса счетов (LSR)
                await self._fetch_lsr(base, sym)
            await self._fetch_funding(base, sym)
            self._at[sym] = time.time()
            return self.snapshot(sym)

    async def _fetch_taker(self, base: str, sym: str) -> Dict[int, float]:
        try:
            rows = await self._get("/futures/data/takerlongshortRatio",
                                   {"symbol": base + "USDT", "period": "5m",
                                    "limit": TAKER_LIMIT})
            parsed = parse_taker_ratio(rows or [])
        except Exception as e:                       # noqa: BLE001
            self.errors[f"taker:{sym}"] = f"{type(e).__name__}: {e}"[:120]
            return {}
        if parsed:
            self._taker[sym] = parsed
            self.fetches += 1
        return parsed

    async def _fetch_lsr(self, base: str, sym: str) -> Dict[int, dict]:
        """Запасной перевес: баланс счетов, а при отказе — среднее по биржам.

        Тейкер меряет оборот слота, LSR — сколько счетов сидит в лонгах: это
        не то же самое (толпа мельче по объёму, но упорнее по позиции), зато
        у LSR своя история слотов и он переживает дыру в тейкерском ряду.
        """
        parsed: Dict[int, dict] = {}
        try:
            rows = await self._get("/futures/data/globalLongShortAccountRatio",
                                   {"symbol": base + "USDT", "period": "5m",
                                    "limit": LSR_LIMIT})
            parsed = parse_global_lsr(rows or [])
        except Exception as e:                       # noqa: BLE001
            self.errors[f"lsr:{sym}"] = f"{type(e).__name__}: {e}"[:120]
        if not parsed:
            parsed = await self._lsr_from_venues(sym)
        if parsed:
            self._lsr[sym] = parsed
            self.fetches += 1
        return parsed

    async def _lsr_from_venues(self, sym: str) -> Dict[int, dict]:
        """Средний LSR по Gate/Bybit/OKX/Binance — одним текущим слотом.

        У чужих бирж истории нет, но и один замер полезнее, чем ничего: он
        ставится в слот «сейчас» и дальше живёт до следующего обновления.
        """
        session = self.session
        if session is None:
            return {}
        try:
            from liq_api import get_multi_lsr
        except Exception as e:                       # noqa: BLE001
            self.errors[f"lsr_multi:{sym}"] = f"{type(e).__name__}: {e}"[:120]
            return {}
        try:
            data = await get_multi_lsr(session, sym)
        except Exception as e:                       # noqa: BLE001
            self.errors[f"lsr_multi:{sym}"] = f"{type(e).__name__}: {e}"[:120]
            return {}
        avg = _num((data or {}).get("average"))
        if not avg or avg <= 0:
            return {}
        return {bucket_of(time.time()): {"long_share": round(avg / (1.0 + avg), 6)}}

    async def _fetch_funding(self, base: str, sym: str) -> List[Tuple[int, float]]:
        try:
            rows = await self._get("/fapi/v1/fundingRate",
                                   {"symbol": base + "USDT", "limit": FUNDING_LIMIT})
            parsed = parse_funding(rows or [])
        except Exception as e:                       # noqa: BLE001
            self.errors[f"funding:{sym}"] = f"{type(e).__name__}: {e}"[:120]
            return []
        if parsed:
            self._funding[sym] = parsed
            self.fetches += 1
        return parsed

    # ----- чтение ----------------------------------------------------------
    def taker_share_at(self, symbol: str, ts: Any) -> Optional[float]:
        return nearest_bucket(self._taker.get(str(symbol or "").upper()) or {}, ts)

    def ratio_at(self, symbol: str, ts: Any) -> Optional[float]:
        """Доля покупок тейкера на момент (краткое имя для расчёта уровней)."""
        return self.taker_share_at(symbol, ts)

    def lsr_share_at(self, symbol: str, ts: Any) -> Optional[float]:
        """Доля лонгов из баланса счетов, если тейкер не отдался.

        ``_lsr`` хранит слоты как ``{"long_share": …}``: ``_share_of`` сам
        достаёт долю из такой записи.
        """
        return nearest_bucket(self._lsr.get(str(symbol or "").upper()) or {}, ts)

    def funding_rate_at(self, symbol: str, ts: Any) -> Optional[float]:
        return funding_at(self._funding.get(str(symbol or "").upper()) or [], ts)

    def side_at(self, symbol: str, ts: Any,
                tick_share: Optional[float] = None) -> Tuple[float, str]:
        """(доля лонгов, источник): тейкер биржи → наши тики → фандинг.

        Тейкерский баланс биржи — это полный оборот слота, а ``tick_share``
        считает вызывающий по своей ленте: она бывает уже срезом. Поэтому
        биржевое слово первое, тики — запасной точный источник.
        """
        sym = str(symbol or "").upper()
        share = self.taker_share_at(sym, ts)
        if share is None:
            share = self.lsr_share_at(sym, ts)
        if share is not None:
            return (share, "taker" if self._taker.get(sym) else "lsr")
        if tick_share is not None:
            return (min(max(float(tick_share), 0.0), 1.0), "ticks")
        rate = self.funding_rate_at(sym, ts)
        funded = funding_share(rate)
        if funded is not None:
            return (funded, "funding")
        return (0.5, "none")

    def status(self) -> dict:
        # «failed» — сколько монет осталось без данных: ошибок на монету может
        # быть несколько (тейкер и фандинг), а сломанной считается монета.
        failed = {k.split(":", 1)[-1] for k in self.errors}
        return {"ok": not self.off, "enabled": not self.off, "off": self.off,
                "ttl": self.ttl,
                "symbols": sorted(set(self._taker) | set(self._lsr) | set(self._funding)),
                "taker_symbols": len(self._taker), "lsr_symbols": len(self._lsr),
                "funding_symbols": len(self._funding),
                "fetches": self.fetches, "failed": len(failed), "source": self.source,
                "errors": {k: v for k, v in list(self.errors.items())[-6:]}}

    def clear(self) -> None:
        self._taker.clear()
        self._lsr.clear()
        self._funding.clear()
        self._at.clear()
