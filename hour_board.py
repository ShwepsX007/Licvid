"""Часовой стенд для постов в канал: ликвидации, CVD и OI по календарным часам.

Зачем отдельный модуль: сводка «за 4 часа» собиралась из буфера LIQUIDATIONS,
но он кольцевой и при рестарте теряет прошлое. Стенду нужны ровно четыре
календарных часа — их и копим здесь, а не выпрашиваем у буфера.

Что внутри:

* ``HourBoard`` — по каждому часу: сумма ликвидаций, лонги/шорты, объёмы по
  монетам, топ-7 крупнейших ударов (с биржей) и перекос CVD по монетам;
* ``OiHistory`` — срезы открытого интереса по монетам (шаг 5 минут, хранится
  6 часов) с сохранением на диск, чтобы после рестарта стенд не был пустым.

Часовой пояс у обоих — параметр ``tz_offset`` (по умолчанию МСК, UTC+3):
пост подписан тем же поясом, что и часы в стенде.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

HOUR = 3600
KEEP_HOURS = 12          # держим с запасом: нужны ещё и предыдущие 4ч для сравнения
TOP_N = 7                # «топ 7 крупных ликвидаций по каждому часу»


def tz_offset() -> int:
    """Часовой пояс стенда: МСК по умолчанию, задаётся LIQSCOPE_DIGEST_TZ."""
    raw = (os.getenv("LIQSCOPE_DIGEST_TZ") or "+3").strip()
    try:
        return int(round(float(raw) * HOUR))
    except ValueError:
        return 3 * HOUR


def hour_start(ts: float, tz: int = 0) -> int:
    """Начало календарного часа для момента ts — уже в секундах эпохи.

    Сдвиг пояса учитываем ровно один раз: переводим момент в местное время,
    отрезаем час и возвращаемся назад в эпоху. Иначе граница уезжает в
    будущее (часовой пояс применялся бы второй раз при выводе подписи).
    """
    return int((float(ts) + tz) // HOUR) * HOUR - tz


def hour_hhmm(ts: float, tz: int = 0) -> str:
    lt = time.gmtime(float(ts) + tz)
    return f"{lt.tm_hour:02d}:00"


class HourBoard:
    """Сводки по календарным часам — для стенда в постах канала."""

    def __init__(self, tz: Optional[int] = None, keep_hours: int = KEEP_HOURS,
                 top_n: int = TOP_N):
        self.tz = tz_offset() if tz is None else int(tz)
        self.keep_hours = int(keep_hours)
        self.top_n = int(top_n)
        self._lock = threading.Lock()
        self._hours: Dict[int, dict] = {}

    # ----- наполнение ------------------------------------------------------
    def _cell(self, ts: float) -> Optional[dict]:
        """Ячейка часа внутри уже взятого замка."""
        h = hour_start(ts, self.tz)
        cell = self._hours.get(h)
        if cell is None:
            cell = {"h": h, "total": 0.0, "longs": 0.0, "shorts": 0.0, "count": 0,
                    "coins": {}, "cvd": {}, "top": []}
            self._hours[h] = cell
            # старые часы больше не нужны: сравнение идёт на 4-8 часов назад
            if len(self._hours) > self.keep_hours:
                for old in sorted(self._hours)[:-self.keep_hours]:
                    self._hours.pop(old, None)
        return cell

    def add_liq(self, event: dict) -> None:
        """Ликвидация: сумма по часу, монете и бирже + кандидат в топ-7."""
        try:
            ts = float(event.get("timestamp") or 0)
            usd = float(event.get("usd") or 0)
        except (TypeError, ValueError):
            return
        if not ts or usd <= 0:
            return
        sym = str(event.get("symbol") or "")
        side = str(event.get("side") or "")
        with self._lock:
            cell = self._cell(ts)
            if cell is None:
                return
            cell["total"] += usd
            cell["count"] += 1
            if side == "SELL":
                cell["longs"] += usd
            else:
                cell["shorts"] += usd
            cell["coins"][sym] = cell["coins"].get(sym, 0.0) + usd
            top = cell["top"]
            top.append({"symbol": sym, "exchange": str(event.get("exchange") or ""),
                        "usd": usd, "side": side, "ts": ts})
            # держим список коротким: полная сортировка нужна только когда
            # кандидатов набралось заметно больше, чем мест в топе
            if len(top) > self.top_n * 8:
                top.sort(key=lambda x: x["usd"], reverse=True)
                del top[self.top_n:]

    def add_cvd(self, symbol: str, ts: float, signed_usd: float) -> None:
        """Перекос тейкер-потока: плюс — покупали, минус — продавали."""
        if not symbol or not signed_usd:
            return
        with self._lock:
            cell = self._cell(ts)
            if cell is None:
                return
            cell["cvd"][symbol] = cell["cvd"].get(symbol, 0.0) + float(signed_usd)

    # ----- чтение ----------------------------------------------------------
    def hours(self, count: int = 4, now: Optional[float] = None) -> List[dict]:
        """Последние ``count`` часов, свежий — последним.

        Внутри каждого часа: total, longs, shorts, count, coins, cvd и top
        (крупнейшие ликвидации, отсортированные по убыванию).
        """
        now = float(now if now is not None else time.time())
        last = hour_start(now, self.tz)
        out = []
        with self._lock:
            for n in range(int(count) - 1, -1, -1):
                h = last - n * HOUR
                cell = self._hours.get(h)
                if cell is None:
                    out.append({"h": h, "total": 0.0, "longs": 0.0, "shorts": 0.0,
                                "count": 0, "coins": {}, "cvd": {}, "top": [],
                                "empty": True})
                    continue
                top = sorted(cell["top"], key=lambda x: x["usd"], reverse=True)[:self.top_n]
                out.append({"h": h, "total": cell["total"], "longs": cell["longs"],
                            "shorts": cell["shorts"], "count": cell["count"],
                            "coins": dict(cell["coins"]), "cvd": dict(cell["cvd"]),
                            "top": top, "empty": False})
        return out

    def cleared(self) -> None:
        with self._lock:
            self._hours.clear()


class OiHistory:
    """Срезы открытого интереса по монетам: шаг во времени — минуты, не часы.

    Уровни OI приходят из трекера бирж (``feed.oi.payload``) и из свечей, где
    поле oi уже посчитано. Храним разреженный ряд — сколько успели увидеть,
    столько и есть; дырки не выдумываем, стенд просто покажет прочерк.
    """

    def __init__(self, keep_minutes: int = 6 * 60, tz: Optional[int] = None,
                 step_sec: int = 300):
        self.tz = tz_offset() if tz is None else int(tz)
        self.keep = int(keep_minutes) * 60
        self.step = max(60, int(step_sec))
        self._lock = threading.Lock()
        self._series: Dict[str, List[tuple]] = {}     # symbol -> [(ts, usd), …]
        self._last: Dict[str, float] = {}             # symbol -> ts последней записи
        self._path = ""

    # ----- наполнение ------------------------------------------------------
    def add(self, symbol: str, usd: Any, ts: Optional[float] = None) -> None:
        try:
            value = float(usd)
        except (TypeError, ValueError):
            return
        if not symbol or value <= 0 or value != value:
            return
        now = float(ts if ts is not None else time.time())
        with self._lock:
            series = self._series.setdefault(str(symbol), [])
            if series and now - series[-1][0] < self.step * 0.5:
                # слишком часто: держим шаг, но уровень обновляем — важно,
                # что OI «сейчас», а не только в момент прошлого снапшота
                series[-1] = (series[-1][0], value)
                self._last[str(symbol)] = now
                return
            series.append((now, value))
            self._last[str(symbol)] = now
            cut = now - self.keep
            if len(series) > 4 and series[0][0] < cut:
                keep_from = 0
                for i, (t, _v) in enumerate(series):
                    if t >= cut:
                        keep_from = i
                        break
                del series[:keep_from]

    def add_many(self, items: Dict[str, Any], ts: Optional[float] = None) -> int:
        """Снапшот трекера: {symbol: значение}. Возвращает число записей."""
        n = 0
        for sym, val in (items or {}).items():
            if isinstance(val, dict):
                val = val.get("total_usd")
            before = self._last.get(str(sym))
            self.add(sym, val, ts)
            if self._last.get(str(sym)) != before:
                n += 1
        return n

    # ----- чтение ----------------------------------------------------------
    def at(self, symbol: str, ts: float, tolerance: float = 4200.0) -> Optional[float]:
        """Значение на момент ts: последний срез не позже (с допуском)."""
        series = self._series.get(str(symbol)) or []
        best = None
        for t, v in series:
            if t <= float(ts):
                best = v
            else:
                break
        if best is None:
            return None
        return best

    def latest(self, symbol: str) -> Optional[tuple]:
        series = self._series.get(str(symbol)) or []
        return series[-1] if series else None

    def change(self, symbol: str, hours: float = 4.0,
               now: Optional[float] = None) -> Optional[dict]:
        """Изменился ли OI за окно: {from, to, pct, span_sec} или None."""
        now = float(now if now is not None else time.time())
        to = self.latest(symbol)
        if not to:
            return None
        frm = self.at(symbol, now - float(hours) * HOUR)
        if frm is None or frm <= 0:
            return None
        return {"from": float(frm), "to": float(to[1]),
                "pct": (float(to[1]) - float(frm)) / float(frm) * 100.0,
                "span_sec": now - float(now - float(hours) * HOUR)}

    def hour_slices(self, symbol: str, hours: int = 4,
                    now: Optional[float] = None) -> List[Optional[dict]]:
        """По каждому из часов: значение на конец часа и % к его началу.

        Стенд подписывает OI теми же часами, что и ликвидации: «сколько было
        на конец часа» и «куда пошло за этот час».
        """
        now = float(now if now is not None else time.time())
        last = hour_start(now, self.tz)
        out: List[Optional[dict]] = []
        for n in range(int(hours) - 1, -1, -1):
            h = last - n * HOUR
            end = min(h + HOUR, now)
            to = self.at(symbol, end)
            frm = self.at(symbol, h, tolerance=1800.0)
            if to is None:
                out.append(None)
                continue
            pct = ((to - frm) / frm * 100.0) if (frm and frm > 0) else None
            out.append({"value": to, "from": frm, "pct": pct})
        return out

    def symbols(self) -> List[str]:
        return sorted(self._series)

    # ----- диск -----------------------------------------------------------
    def save(self, path: str) -> bool:
        data = {"tz": self.tz, "step": self.step,
                "series": {k: v for k, v in self._series.items()}}
        try:
            folder = os.path.dirname(os.path.abspath(path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
            self._path = path
            return True
        except OSError:
            return False

    def load(self, path: str) -> int:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return 0
        series = data.get("series") or {}
        now = time.time()
        n = 0
        for sym, rows in series.items():
            clean = []
            for row in rows or []:
                try:
                    t, v = float(row[0]), float(row[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if v > 0 and now - t <= self.keep:
                    clean.append((t, v))
            if clean:
                self._series[str(sym)] = sorted(clean)
                self._last[str(sym)] = clean[-1][0]
                n += 1
        self._path = path
        return n

    def clear(self) -> None:
        with self._lock:
            self._series.clear()
            self._last.clear()


def _bias_word(longs: float, shorts: float) -> str:
    """Кто задавал тон в часе: 'long', 'short' или пусто (обе стороны)."""
    if longs <= 0 and shorts <= 0:
        return ""
    if longs > shorts * 1.25:
        return "long"
    if shorts > longs * 1.25:
        return "short"
    return ""


def build_snapshot(board: "HourBoard", oi: "OiHistory", now: Optional[float] = None,
                   span: int = 4) -> dict:
    """Стенд для поста: часы, топ-7 ударов часа, перекос CVD и ряды OI.

    Считаем на стороне сервера и один раз: пост рендерится и для русского
    канала, и для английского — данные у них общие, отличаются подписи.
    """
    now = float(now if now is not None else time.time())
    hours = board.hours(span, now)
    prev = board.hours(span * 2, now)[:span]

    # монеты, по которым показываем OI: самые крупные за окно
    volume: Dict[str, float] = {}
    for hr in hours:
        for sym, usd in (hr.get("coins") or {}).items():
            volume[sym] = volume.get(sym, 0.0) + float(usd)
    oi_coins = [s for s, _v in sorted(volume.items(), key=lambda kv: kv[1],
                                      reverse=True)[:24]]

    # OI по часам: суммируем только те монеты, где ряд есть — честный прочерк
    # лучше выдуманного уровня
    slices: Dict[str, list] = {c: oi.hour_slices(c, span, now) for c in oi_coins}
    oi_value = [0.0] * span
    oi_from = [0.0] * span
    have_value = [False] * span
    have_from = [False] * span
    for _c, rows in slices.items():
        for i, cell in enumerate(rows or []):
            if not cell:
                continue
            if cell.get("value"):
                oi_value[i] += float(cell["value"])
                have_value[i] = True
            if cell.get("from"):
                oi_from[i] += float(cell["from"])
                have_from[i] = True

    out_hours = []
    oi_hours = []
    for i, hr in enumerate(hours):
        cvd = hr.get("cvd") or {}
        coins = []
        for sym, usd in sorted((hr.get("coins") or {}).items(),
                              key=lambda kv: kv[1], reverse=True)[:3]:
            flow = cvd.get(sym)
            coins.append({"symbol": sym, "usd": float(usd),
                          "flow": float(flow) if flow is not None else None})
        longs, shorts = float(hr.get("longs") or 0), float(hr.get("shorts") or 0)
        bias = _bias_word(longs, shorts)
        pct = None
        if have_value[i] and have_from[i] and oi_from[i] > 0:
            pct = (oi_value[i] - oi_from[i]) / oi_from[i] * 100.0
        out_hours.append({
            "h": hr.get("h"), "total": hr.get("total"), "count": hr.get("count"),
            "longs": longs, "shorts": shorts,
            "side_sum": max(longs, shorts) if bias else 0.0,
            "bias": bias, "coins": coins, "cvd": dict(cvd),
            "cvd_sum": sum(float(v) for v in cvd.values()),
            "oi": {"value": oi_value[i] if have_value[i] else None, "pct": pct},
        })
        oi_hours.append({"h": hr.get("h"), "value": oi_value[i] if have_value[i] else None,
                         "pct": pct})

    total = sum(float(hr.get("total") or 0) for hr in hours)
    count = sum(int(hr.get("count") or 0) for hr in hours)
    prev_total = sum(float(hr.get("total") or 0) for hr in prev)
    diff_pct = ((total - prev_total) / prev_total * 100.0) if prev_total > 0 else None

    top_hours = [{"h": hr.get("h"), "items": hr.get("top") or []}
                 for hr in reversed(hours)]

    first_ok = next((i for i in range(span) if have_value[i]), None)
    last_ok = next((i for i in range(span - 1, -1, -1) if have_value[i]), None)
    oi_4h_pct = None
    if first_ok is not None and last_ok is not None and last_ok > first_ok:
        base = oi_from[first_ok] or oi_value[first_ok]
        if base:
            oi_4h_pct = (oi_value[last_ok] - base) / base * 100.0

    return {
        "span_hours": span,
        "hours": out_hours,
        "top_hours": top_hours,
        "total_usd": total,
        "count": count,
        "prev_total": prev_total,
        "diff_pct": diff_pct,
        "oi_hours": oi_hours,
        "oi_now_usd": oi_value[last_ok] if last_ok is not None else None,
        "oi_4h_pct": oi_4h_pct,
        "tz": board.tz,
    }


# Глобальные копилки процесса: пишет сервер, читает сводка канала.
BOARD = HourBoard()
OI = OiHistory()
