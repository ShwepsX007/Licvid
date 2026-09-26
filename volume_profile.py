"""Профиль объёма по цене и времени: где набирали позиции.

Расчёт уровней ликвидаций опирается на цену входа: OI вырос на столько-то —
значит, где-то по этой цене набрали позиции. Свеча такого не расскажет, а
вот объём, прошедший по цене, — расскажет.

Модуль копит по каждой монете и каждому 5-минутному слоту:

    vol       — оборот в USDT (тейкерные сделки, а где их нет — минутные свечи);
    buy/sell  — тот же оборот, разложенный по стороне тейкера;
    vwap_num  = Σ(цена × usd) — числитель VWAP (делить на vol);
    lo/hi     — диапазон цен слота;
    n         — сколько сделок учтено (или сколько свечей).

Шаг 5 минут — как у ряда OI (``oi_feed.BUCKET_SEC``): пара «дельта OI» и
«VWAP слота» и даёт цену входа той массы, которая набрана за эти пять минут.
Хранение — месяц, как у остальной истории проекта (``data/volume_profile.json``);
формат плоский, чтобы файл читался глазами и переживал рестарт сервера.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from history import disk_has_series

log = logging.getLogger("liqscore.volume")

BUCKET_SEC = 300                     # 5 минут — шаг ряда OI
HOUR = 3600
#: Сколько держать профиль. Месяц — как у остальной истории проекта.
KEEP_HOURS = float(os.getenv("LIQSCOPE_VP_KEEP_HOURS", str(31 * 24)) or 31 * 24)
#: Потолок слотов на монету: страховка от мусора в памяти (месяц = 8928).
MAX_BUCKETS = int(os.getenv("LIQSCOPE_VP_MAX_BUCKETS", "9500") or 9500)
SAVE_EVERY_SEC = max(60.0, float(os.getenv("LIQSCOPE_VP_SAVE_SEC", "300") or 300))


def bucket_of(ts: Any) -> int:
    """Начало 5-минутного слота для момента времени."""
    try:
        return int(float(ts) // BUCKET_SEC) * BUCKET_SEC
    except (TypeError, ValueError):
        return 0


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def empty_cell() -> dict:
    return {"vol": 0.0, "buy": 0.0, "sell": 0.0, "vwap_num": 0.0,
            "lo": None, "hi": None, "n": 0}


def add_to_cell(cell: dict, price: float, usd: float, side: str = "") -> None:
    """Добавить сделку в слот: сумма, числитель VWAP и диапазон цен."""
    cell["vol"] += usd
    cell["vwap_num"] += price * usd
    cell["n"] += 1
    s = str(side or "").upper()
    if s == "BUY":
        cell["buy"] += usd
    elif s == "SELL":
        cell["sell"] += usd
    lo, hi = cell.get("lo"), cell.get("hi")
    cell["lo"] = price if lo is None else min(lo, price)
    cell["hi"] = price if hi is None else max(hi, price)


def cell_vwap(cell: Optional[dict]) -> Optional[float]:
    """Средняя цена слота: Σ(цена × объём) / объём."""
    if not cell:
        return None
    vol = _num(cell.get("vol")) or 0.0
    num = _num(cell.get("vwap_num")) or 0.0
    if vol <= 0 or num <= 0:
        return None
    return num / vol


def cell_share(cell: Optional[dict]) -> Optional[float]:
    """Доля покупок тейкера в слоте (0…1) или None, если стороны не размечены.

    None честнее половины: если биржа не отдала сторону сделки, выдумывать
    «ровно пополам» нельзя — на этом строятся и сторона уровня, и калибровка.
    """
    if not cell:
        return None
    buy = _num(cell.get("buy")) or 0.0
    sell = _num(cell.get("sell")) or 0.0
    if buy + sell <= 0:
        return None
    return buy / (buy + sell)


def merge_cells(dst: dict, src: dict) -> dict:
    """Слить два слота (например, тики и свечу по одному промежутку)."""
    for k in ("vol", "buy", "sell", "vwap_num"):
        dst[k] = (_num(dst.get(k)) or 0.0) + (_num(src.get(k)) or 0.0)
    dst["n"] = int(dst.get("n") or 0) + int(src.get("n") or 0)
    for k, better in (("lo", min), ("hi", max)):
        a, b = _num(dst.get(k)), _num(src.get(k))
        if a is None:
            dst[k] = b
        elif b is not None:
            dst[k] = better(a, b)
    return dst


class VolumeProfile:
    """Слоты «объём по цене» по монетам: чистое хранилище, без сети.

    Все методы чтения сами выравнивают время по слотам: вызывающему не нужно
    помнить, что 12:03 принадлежит слоту 12:00.
    """

    def __init__(self, path: Optional[str] = None, keep_hours: Optional[float] = None):
        self.path = path if path is not None else os.getenv(
            "LIQSCOPE_VP_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "volume_profile.json"))
        self.keep = float(keep_hours if keep_hours is not None else KEEP_HOURS)
        self._series: Dict[str, Dict[int, dict]] = {}
        self._lock = threading.RLock()
        self._saved_at = 0.0
        self.added = 0
        self.dropped = 0

    # ----- запись ----------------------------------------------------------
    def _cell_for(self, symbol: str, ts: Any) -> Optional[dict]:
        key = str(symbol or "")
        if not key:
            return None
        b = bucket_of(ts)
        if not b:
            return None
        rows = self._series.setdefault(key, {})
        cell = rows.setdefault(b, empty_cell())
        # Потолок слотов — страховка от «застрявших» монет: держим самые
        # свежие. Константу читаем на каждом вызове: тесты её подменяют.
        if len(rows) > MAX_BUCKETS:
            for old in sorted(rows)[:len(rows) - MAX_BUCKETS]:
                rows.pop(old, None)
                self.dropped += 1
        return cell

    def add_trade(self, symbol: str, ts: Any, price: Any, usd: Any,
                  side: str = "") -> None:
        """Тейкерская сделка: цена, объём (USDT) и сторона тейкера."""
        p, v = _num(price), _num(usd)
        if not p or not v or p <= 0 or v <= 0:
            return
        with self._lock:
            cell = self._cell_for(symbol, ts)
            if cell is None:
                return
            add_to_cell(cell, p, v, side)
            self.added += 1

    def add_candle(self, symbol: str, ts: Any, high: Any, low: Any, close: Any,
                   volume_usd: Any, buy_usd: Any = None,
                   sell_usd: Any = None) -> None:
        """Минутная свеча как грубая замена тика.

        Цена — типичная ((high+low+close)/3): у свечи нет одной цены, а именно
        такой вес даёт наименьшее искажение VWAP на нормальном рынке.
        Если у свечи есть разбивка по сторонам (не у всех источников), она
        добавляется как есть.
        """
        v = _num(volume_usd)
        if not v or v <= 0:
            return
        hi, lo, cl = _num(high), _num(low), _num(close)
        prices = [x for x in (hi, lo, cl) if x and x > 0]
        if not prices:
            return
        typical = (max(prices) + min(prices) + (cl if cl else max(prices))) / 3.0
        with self._lock:
            cell = self._cell_for(symbol, ts)
            if cell is None:
                return
            cell["vol"] += v
            cell["vwap_num"] += typical * v
            cell["n"] += 1
            b, s = _num(buy_usd), _num(sell_usd)
            if b:
                cell["buy"] += b
            if s:
                cell["sell"] += s
            if lo is not None:
                cell["lo"] = lo if cell["lo"] is None else min(cell["lo"], lo)
            if hi is not None:
                cell["hi"] = hi if cell["hi"] is None else max(cell["hi"], hi)
            self.added += 1

    # ----- чтение ----------------------------------------------------------
    def _rows(self, symbol: str) -> Dict[int, dict]:
        return self._series.get(str(symbol or "")) or {}

    def cell(self, symbol: str, bucket_ts: Any) -> Optional[dict]:
        """Слот по любому моменту внутри него (не по точному началу)."""
        return self._rows(symbol).get(bucket_of(bucket_ts))

    def bucket_vwap(self, symbol: str, bucket_ts: Any) -> Optional[float]:
        return cell_vwap(self.cell(symbol, bucket_ts))

    def bucket_share(self, symbol: str, bucket_ts: Any) -> Optional[float]:
        return cell_share(self.cell(symbol, bucket_ts))

    def range_cells(self, symbol: str, since: Any, until: Any = None) -> List[Tuple[int, dict]]:
        """Слоты промежутка (границы включаются, время выравнивается).

        ``until=None`` — открытый конец: до последнего известного слота. Начало
        в будущем (данных там нет) даёт пустой список, а не последний слот.
        """
        rows = self._rows(symbol)
        if not rows:
            return []
        start = bucket_of(since) if since else min(rows)
        end = bucket_of(until) if until else max(rows)
        if start > end:
            return []
        return [(b, rows[b]) for b in sorted(rows) if start <= b <= end]

    def vwap(self, symbol: str, since: Any, until: Any = None) -> Optional[float]:
        """VWAP промежутка: Σ(цена × объём) / объём."""
        vol = num = 0.0
        for _b, c in self.range_cells(symbol, since, until):
            vol += _num(c.get("vol")) or 0.0
            num += _num(c.get("vwap_num")) or 0.0
        if vol <= 0 or num <= 0:
            return None
        return num / vol

    def volume(self, symbol: str, since: Any, until: Any = None) -> float:
        return sum((_num(c.get("vol")) or 0.0) for _b, c in
                   self.range_cells(symbol, since, until))

    def side_ratio(self, symbol: str, since: Any, until: Any = None) -> Optional[float]:
        """Доля покупок тейкера в промежутке или None, если стороны неизвестны."""
        buy = sell = 0.0
        for _b, c in self.range_cells(symbol, since, until):
            buy += _num(c.get("buy")) or 0.0
            sell += _num(c.get("sell")) or 0.0
        if buy + sell <= 0:
            return None
        return buy / (buy + sell)

    def series(self, symbol: str, since: Any = None, until: Any = None,
               limit: int = 0) -> List[dict]:
        """Ряд слотов для панели: время, объём, VWAP, диапазон и сторона."""
        out: List[dict] = []
        for b, c in self.range_cells(symbol, since, until):
            vwap = cell_vwap(c)
            if vwap is None:
                continue
            row = {"t": b, "vol": round(_num(c.get("vol")) or 0.0, 2),
                   "vwap": vwap, "lo": _num(c.get("lo")), "hi": _num(c.get("hi")),
                   "n": int(c.get("n") or 0)}
            share = cell_share(c)
            if share is not None:
                row["buy_share"] = share
            out.append(row)
        if limit and len(out) > limit:
            out = out[-int(limit):]
        return out

    def coverage(self, symbol: str, since: Any = None) -> dict:
        """Что есть по монете: слоты, объём, границы и размеченная сторона."""
        cells = self.range_cells(symbol, since) if since else \
            [(b, c) for b, c in sorted(self._rows(symbol).items())]
        vol = sum((_num(c.get("vol")) or 0.0) for _b, c in cells)
        with_side = sum(1 for _b, c in cells if cell_share(c) is not None)
        return {"buckets": len(cells),
                "vol_usd": round(vol, 2),
                "with_side": with_side,
                "from": (cells[0][0] if cells else None),
                "to": (cells[-1][0] if cells else None)}

    def symbols(self) -> List[str]:
        return sorted(self._series)

    def stats(self) -> dict:
        cells = sum(len(rows) for rows in self._series.values())
        return {"symbols": len(self._series), "buckets": cells,
                "added": self.added, "dropped": self.dropped,
                "keep_hours": self.keep, "path": self.path}

    # ----- обслуживание ----------------------------------------------------
    def trim(self, now: Optional[float] = None) -> int:
        """Выбросить слоты старше ``keep`` и лишние по потолку на монету."""
        now = float(now if now is not None else time.time())
        cutoff = bucket_of(now - self.keep * HOUR)
        removed = 0
        with self._lock:
            for sym in list(self._series):
                rows = self._series[sym]
                for b in [b for b in rows if b < cutoff]:
                    rows.pop(b, None)
                    removed += 1
                if len(rows) > MAX_BUCKETS:
                    for b in sorted(rows)[:len(rows) - MAX_BUCKETS]:
                        rows.pop(b, None)
                        removed += 1
                if not rows:
                    self._series.pop(sym, None)
        self.dropped += removed
        return removed

    def save(self, path: Optional[str] = None, force: bool = False) -> bool:
        """Сброс на диск (не чаще ``SAVE_EVERY_SEC``, кроме force).

        Пустое состояние в памяти не затирает готовый файл: сервер, поднятый
        до засева истории или без потока сделок, иначе стирал бы накопленный
        профиль — а по нему считаются уровни ликвидаций.
        """
        target = self.path if path is None else path
        if not target:
            return False
        now = time.time()
        if not force and now - self._saved_at < SAVE_EVERY_SEC:
            return False
        with self._lock:
            empty = not any(rows for rows in self._series.values())
        if empty and disk_has_series(target):
            log.debug("профиль объёма: пустое состояние не пишем поверх %s", target)
            return False
        with self._lock:
            payload = {"ts": now, "step": BUCKET_SEC,
                       "series": {s: {str(b): c for b, c in rows.items()}
                                  for s, rows in self._series.items()}}
            self._saved_at = now
        try:
            folder = os.path.dirname(os.path.abspath(target))
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, target)
            return True
        except OSError as e:
            log.debug("профиль объёма не сохранился: %s", e)
            return False

    def load(self, path: Optional[str] = None) -> int:
        """Прочитать профиль с диска (старые слоты отбрасываются)."""
        target = self.path if path is None else path
        if not target:
            return 0
        try:
            with open(target, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return 0
        if (data or {}).get("step") not in (None, BUCKET_SEC):
            log.debug("профиль объёма другого шага — пропускаю")
            return 0
        cutoff = bucket_of(time.time() - self.keep * HOUR)
        n = 0
        with self._lock:
            for sym, rows in ((data or {}).get("series") or {}).items():
                if not isinstance(rows, dict):
                    continue
                for b, cell in rows.items():
                    try:
                        b_i = int(b)
                    except (TypeError, ValueError):
                        continue
                    if b_i < cutoff or not isinstance(cell, dict):
                        continue
                    vol = _num(cell.get("vol")) or 0.0
                    if vol <= 0:
                        continue
                    keep = empty_cell()
                    keep["vol"] = vol
                    keep["buy"] = _num(cell.get("buy")) or 0.0
                    keep["sell"] = _num(cell.get("sell")) or 0.0
                    keep["vwap_num"] = _num(cell.get("vwap_num")) or 0.0
                    keep["lo"] = _num(cell.get("lo"))
                    keep["hi"] = _num(cell.get("hi"))
                    keep["n"] = int(_num(cell.get("n")) or 0)
                    self._series.setdefault(str(sym), {})[b_i] = keep
                    n += 1
        self._saved_at = time.time()
        return len(self._series) if n else 0

    def clear(self) -> None:
        with self._lock:
            self._series.clear()
