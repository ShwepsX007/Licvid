"""Поток по ВСЕМ монетам: ликвидации, объём, CVD и OI по минутам.

Зачем: лента CVD/OI в терминале строилась по свечам **графика**. Пока выбрана
одна монета, это правильно; но стоит переключиться на «ВСЕ» — график остаётся
прежним (он и должен), а лента замирала на той же монете: казалось, что данные
пропали.

Здесь лежит независимая от графика таблица: по каждой монете и каждой минуте
накапливаются

    * ликвидации — суммы лонгов и шортов (есть всегда: лента ликвидаций
      приходит по всем монетам сразу);
    * объём — оборот в USDT (по минутным свечам цены);
    * CVD — тейкер-дельта в USDT (там, где идут тики сделок);
    * OI — открытый интерес и его изменение внутри минуты.

Модуль ничего не знает про сеть и время: данные приносит server.py, а строки
для ленты собирает :meth:`FlowFeed.rows`. Дальше этим пользуются:

    * лента «ВСЕ» в CVD/OI (сервер рассылает строки по WS);
    * корреляции валют и сторож пампов — они читают те же минутки.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

MINUTE = 60


def minute_of(ts: float) -> int:
    """Начало минуты для отметки времени."""
    try:
        return int(float(ts) // MINUTE * MINUTE)
    except (TypeError, ValueError):
        return 0


class FlowFeed:
    """Минутная таблица потоков по монетам (см. модуль целиком)."""

    def __init__(self, keep_min: int = 120, symbols_max: int = 40):
        self.keep_min = max(10, int(keep_min))
        self.symbols_max = max(5, int(symbols_max))
        self._by_sym: Dict[str, Dict[int, dict]] = {}
        self._price: Dict[str, float] = {}

    # ----- наполнение ------------------------------------------------------
    def _cell(self, sym: str, ts: float) -> Optional[dict]:
        if not sym:
            return None
        m = minute_of(ts)
        if not m:
            return None
        rows = self._by_sym.setdefault(sym, {})
        cell = rows.get(m)
        if cell is None:
            cell = {"m": m, "liq_long": 0.0, "liq_short": 0.0, "liq_count": 0,
                    "vol": 0.0, "cvd": 0.0, "has_cvd": False,
                    "oi": None, "oi_from": None}
            rows[m] = cell
            if len(rows) > self.keep_min + 5:
                for old in sorted(rows)[:len(rows) - self.keep_min]:
                    rows.pop(old, None)
        return cell

    def add_liq(self, event: dict) -> None:
        """Ликвидация: в лонги или шорты, сумма и число событий за минуту."""
        try:
            ts = float(event.get("timestamp") or 0)
            usd = float(event.get("usd") or 0)
        except (TypeError, ValueError):
            return
        if usd <= 0 or ts <= 0:
            return
        cell = self._cell(str(event.get("symbol") or ""), ts)
        if cell is None:
            return
        cell["liq_count"] += 1
        if str(event.get("side") or "") == "SELL":     # принудительно продали — лонг
            cell["liq_long"] += usd
        else:
            cell["liq_short"] += usd

    def add_trade(self, symbol: str, ts: float, signed_usd: float) -> None:
        """Тейкер-дельта: плюс — покупали (CVD ▲), минус — продавали (▼)."""
        if not symbol or not signed_usd:
            return
        cell = self._cell(symbol, ts)
        if cell is None:
            return
        cell["cvd"] += float(signed_usd)
        cell["has_cvd"] = True

    def add_volume(self, symbol: str, ts: float, usd: float) -> None:
        """Оборот в USDT за минуту (свечи цены отдают его накопительно)."""
        if not symbol or not usd:
            return
        cell = self._cell(symbol, ts)
        if cell is None:
            return
        cell["vol"] += max(0.0, float(usd))

    def add_oi(self, symbol: str, ts: float, total_usd: float) -> None:
        """Снимок OI: первое значение в минуте — база, последнее — итог."""
        if not symbol or not total_usd:
            return
        cell = self._cell(symbol, ts)
        if cell is None:
            return
        if cell["oi_from"] is None:
            cell["oi_from"] = float(total_usd)
        cell["oi"] = float(total_usd)

    def set_price(self, symbol: str, price) -> None:
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        if p > 0:
            self._price[symbol] = p

    # ----- чтение ----------------------------------------------------------
    def _window(self, sym: str, window_min: int, now: float) -> Optional[dict]:
        rows = self._by_sym.get(sym) or {}
        first = minute_of(now) - (max(1, window_min) - 1) * MINUTE
        agg = {"liq_long": 0.0, "liq_short": 0.0, "liq_count": 0, "vol": 0.0,
               "cvd": 0.0, "has_cvd": False, "oi": None, "oi_from": None,
               "minutes": 0, "last": 0}
        for m in sorted(rows):
            if m < first:
                continue
            c = rows[m]
            agg["minutes"] += 1
            agg["last"] = m
            agg["liq_long"] += c.get("liq_long") or 0.0
            agg["liq_short"] += c.get("liq_short") or 0.0
            agg["liq_count"] += int(c.get("liq_count") or 0)
            agg["vol"] += c.get("vol") or 0.0
            if c.get("has_cvd"):
                agg["cvd"] += c.get("cvd") or 0.0
                agg["has_cvd"] = True
            if c.get("oi") is not None:
                if agg["oi_from"] is None:
                    agg["oi_from"] = c.get("oi_from")
                agg["oi"] = c.get("oi")
        if not agg["minutes"]:
            return None
        return agg

    def rows(self, kind: str = "cvd", window_min: int = 5, now: Optional[float] = None,
             limit: int = 40, min_abs: float = 0.0) -> List[dict]:
        """Строки ленты «ВСЕ»: по монете — агрегат за окно и метрика окна.

        kind: ``cvd`` — перекос тейкер-потока, ``oi`` — изменение интереса,
        ``liq`` — ликвидации (лонги/шорты), ``vol`` — оборот.
        Сортировка — по величине метрики, свежие монеты впереди при равенстве.
        """
        now = float(now if now is not None else time.time())
        out: List[dict] = []
        for sym in list(self._by_sym):
            agg = self._window(sym, window_min, now)
            if not agg:
                continue
            cvd = agg["cvd"] if agg["has_cvd"] else None
            oi_delta = None
            if agg["oi"] is not None and agg["oi_from"]:
                oi_delta = float(agg["oi"]) - float(agg["oi_from"])
            row = {
                "symbol": sym,
                "ts": agg["last"] or minute_of(now),
                "window_min": int(window_min),
                "vol": round(agg["vol"], 2),
                "cvd": None if cvd is None else round(cvd, 2),
                "cvd_share": (None if (cvd is None or not agg["vol"])
                              else round(cvd / agg["vol"] * 100.0, 1)),
                "liq_long": round(agg["liq_long"], 2),
                "liq_short": round(agg["liq_short"], 2),
                "liq_count": agg["liq_count"],
                "oi_delta": None if oi_delta is None else round(oi_delta, 2),
                "oi_usd": None if agg["oi"] is None else round(float(agg["oi"]), 2),
                "price": self._price.get(sym),
            }
            if kind == "cvd":
                if cvd is None:
                    continue
                row["value"] = abs(cvd)
                row["delta"] = cvd
            elif kind == "oi":
                if oi_delta is None:
                    continue
                row["value"] = abs(oi_delta)
                row["delta"] = oi_delta
            elif kind == "liq":
                if not agg["liq_count"]:
                    continue
                row["value"] = agg["liq_long"] + agg["liq_short"]
                row["delta"] = row["value"]
            else:                                   # объём
                if not agg["vol"]:
                    continue
                row["value"] = agg["vol"]
                row["delta"] = agg["vol"]
            if row["value"] < float(min_abs or 0):
                continue
            out.append(row)
        out.sort(key=lambda r: (r["value"], r["ts"]), reverse=True)
        return out[:max(1, int(limit))]

    def summary(self, window_min: int = 60, now: Optional[float] = None) -> dict:
        """Короткая сводка «кто куда»: перекос ликвидаций, CVD и OI.

        Её показывают лента «ВСЕ», кабинет и корреляции: сразу видно, где
        сносило шорты, а где лонги, где покупали, а где продавали.
        """
        now = float(now if now is not None else time.time())
        liq = self.rows("liq", window_min, now, limit=200)
        cvd = self.rows("cvd", window_min, now, limit=200)
        oi = self.rows("oi", window_min, now, limit=200)
        longs = [r for r in liq if r["liq_long"] > r["liq_short"]]
        shorts = [r for r in liq if r["liq_short"] > r["liq_long"]]
        buyers = [r for r in cvd if (r["cvd"] or 0) > 0]
        sellers = [r for r in cvd if (r["cvd"] or 0) < 0]
        oi_up = [r for r in oi if (r["oi_delta"] or 0) > 0]
        oi_down = [r for r in oi if (r["oi_delta"] or 0) < 0]
        by = lambda rows: sorted(rows, key=lambda r: r["value"], reverse=True)  # noqa: E731
        return {
            "window_min": int(window_min),
            "now": now,
            "symbols": len(self._by_sym),
            "liquidated_long": [r["symbol"] for r in by(longs)[:5]],
            "liquidated_short": [r["symbol"] for r in by(shorts)[:5]],
            "cvd_sellers": [r["symbol"] for r in by(sellers)[:5]],
            "cvd_buyers": [r["symbol"] for r in by(buyers)[:5]],
            "oi_up": [r["symbol"] for r in by(oi_up)[:5]],
            "oi_down": [r["symbol"] for r in by(oi_down)[:5]],
            "liq_usd": round(sum(r["value"] for r in liq), 2),
            "cvd_usd": round(sum(r["cvd"] or 0 for r in cvd), 2),
            "vol_usd": round(sum(r["vol"] for r in liq), 2),
        }

    def symbol_series(self, symbol: str, window_min: int = 60,
                      now: Optional[float] = None) -> List[dict]:
        """Минутки одной монеты — для графиков сайта и корреляций."""
        now = float(now if now is not None else time.time())
        rows = self._by_sym.get(symbol) or {}
        first = minute_of(now) - (max(1, window_min) - 1) * MINUTE
        out = []
        for m in sorted(rows):
            if m < first:
                continue
            c = dict(rows[m])
            c["cvd"] = c["cvd"] if c.get("has_cvd") else None
            c["oi_delta"] = (None if (c.get("oi") is None or not c.get("oi_from"))
                             else round(float(c["oi"]) - float(c["oi_from"]), 2))
            c.pop("has_cvd", None)
            out.append(c)
        return out

    def by_slot(self, slot_sec: int = 900, count: int = 40,
                now: Optional[float] = None,
                tz: int = 0) -> Dict[str, Dict[int, dict]]:
        """Минутки → слоты постов: {монета: {начало слота: {cvd, vol, has_cvd}}}.

        Сетку задаёт вызывающий: посты в канал складывают блок анализа из
        слотов по ``slot_sec`` секунд (15 минут). Собираем из минут, что уже
        есть в памяти, — без походов в сеть, поэтому и после рестарта слоты
        наполняются сразу, а не ждут свечей.

        Слот без тейкер-делты (CVD) не выкидываем: объём в нём может быть, а
        долю CVD по такому слоту просто не считаем (``has_cvd`` = False).
        """
        slot_sec = max(60, int(slot_sec))
        count = max(1, int(count))
        now = float(now if now is not None else time.time())
        last = int((now + tz) // slot_sec) * slot_sec - tz
        first = last - (count - 1) * slot_sec
        out: Dict[str, Dict[int, dict]] = {}
        for sym, rows in self._by_sym.items():
            by_slot: Dict[int, dict] = {}
            for m in sorted(rows):
                if m < first or m > now:
                    continue
                c = rows[m] or {}
                start = int((float(m) + tz) // slot_sec) * slot_sec - tz
                cell = by_slot.get(start)
                if cell is None:
                    cell = {"cvd": 0.0, "vol": 0.0, "has_cvd": False}
                    by_slot[start] = cell
                cell["vol"] += float(c.get("vol") or 0.0)
                if c.get("has_cvd"):
                    cell["cvd"] += float(c.get("cvd") or 0.0)
                    cell["has_cvd"] = True
            if by_slot:
                out[sym] = by_slot
        return out

    def symbols(self) -> List[str]:
        return sorted(self._by_sym)

    def known(self) -> int:
        return len(self._by_sym)
