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
#: Базовая сетка для постов в канал. 15 минут: блок анализа поста всегда кратен
#: ей — при посте раз в N часов блок равен N/4 часа (15, 30, 45 мин … 2.5 ч),
#: а сама сетка при смене частоты не меняется, поэтому уже собранные данные
#: никуда не пропадают.
SLOT_SEC = 900
#: Сколько базовых слотов держать: максимальная частота постов — раз в 10 ч,
#: на окно и предыдущее окно нужно 2×10 ч, плюс запас на текущий блок.
KEEP_SLOTS = (2 * 10 + 2) * (HOUR // SLOT_SEC)


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
    return slot_start(ts, tz, HOUR)


def slot_start(ts: float, tz: int = 0, slot: int = HOUR) -> int:
    """Начало слота длиной ``slot`` секунд, в который попадает момент ts."""
    slot = max(60, int(slot))
    return int((float(ts) + tz) // slot) * slot - tz


def slot_index(ts: float, tz: int = 0, slot: int = HOUR) -> int:
    """Номер слота — нужен, чтобы группировать базовые слоты в блоки поста."""
    slot = max(60, int(slot))
    return int((float(ts) + tz) // slot)


def hour_hhmm(ts: float, tz: int = 0) -> str:
    """«ЧЧ:00» — подпись календарного часа (минуты у часа всегда нулевые)."""
    lt = time.gmtime(float(ts) + tz)
    return f"{lt.tm_hour:02d}:00"


def slot_hhmm(ts: float, tz: int = 0) -> str:
    """«ЧЧ:ММ» — подпись слота постов: блок анализа бывает и 15 минут.

    У постов в канал блок равен четверти промежутка между постами, поэтому
    подпись «21:15» должна отличаться от «21:00» — иначе четыре четверти
    часа выглядели бы одним и тем же часом.
    """
    lt = time.gmtime(float(ts) + tz)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


class HourBoard:
    """Сводки по календарным часам — для стенда в постах канала."""

    def __init__(self, tz: Optional[int] = None, keep_hours: int = KEEP_HOURS,
                 top_n: int = TOP_N, slot_sec: int = HOUR):
        self.tz = tz_offset() if tz is None else int(tz)
        self.keep_hours = int(keep_hours)
        self.top_n = int(top_n)
        self.slot_sec = max(60, int(slot_sec))
        self._lock = threading.Lock()
        self._hours: Dict[int, dict] = {}

    # ----- наполнение ------------------------------------------------------
    def _cell(self, ts: float) -> Optional[dict]:
        """Ячейка слота внутри уже взятого замка."""
        h = slot_start(ts, self.tz, self.slot_sec)
        cell = self._hours.get(h)
        if cell is None:
            # cnt — сколько событий дала монета: лидер часа считается и по
            # количеству ликвидаций, а не только по деньгам
            cell = {"h": h, "total": 0.0, "longs": 0.0, "shorts": 0.0, "count": 0,
                    "coins": {}, "cnt": {}, "cvd": {}, "top": []}
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
            cell["cnt"][sym] = cell["cnt"].get(sym, 0) + 1
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
    def slots(self, count: int = 4, now: Optional[float] = None) -> List[dict]:
        """Последние ``count`` слотов (по ``slot_sec`` секунд), свежий — последним.

        Внутри каждого слота: total, longs, shorts, count, coins, cvd и top
        (крупнейшие ликвидации, отсортированные по убыванию).
        """
        now = float(now if now is not None else time.time())
        last = slot_start(now, self.tz, self.slot_sec)
        out = []
        with self._lock:
            for n in range(int(count) - 1, -1, -1):
                h = last - n * self.slot_sec
                cell = self._hours.get(h)
                if cell is None:
                    out.append({"h": h, "total": 0.0, "longs": 0.0, "shorts": 0.0,
                                "count": 0, "coins": {}, "cnt": {}, "cvd": {},
                                "top": [], "empty": True})
                    continue
                top = sorted(cell["top"], key=lambda x: x["usd"], reverse=True)[:self.top_n]
                out.append({"h": h, "total": cell["total"], "longs": cell["longs"],
                            "shorts": cell["shorts"], "count": cell["count"],
                            "coins": dict(cell["coins"]), "cnt": dict(cell["cnt"]),
                            "cvd": dict(cell["cvd"]),
                            "top": top, "empty": False})
        return out

    def hours(self, count: int = 4, now: Optional[float] = None) -> List[dict]:
        """Последние ``count`` слотов — то же, что ``slots`` (для часовых досок)."""
        return self.slots(count, now)

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

    def range_slices(self, symbol: str, spans: List[tuple]) -> List[Optional[dict]]:
        """По каждому промежутку (начало, конец): уровень OI и % к началу.

        Промежутки задаёт вызывающий: у поста в канал это блоки анализа
        (при частоте раз в N часов блок равен N/4 часа), у часового стенда —
        календарные часы. Уровень «на конец» берём последним срезом не позже
        конца, «на начало» — с допуском: снимки OI идут раз в минуты.
        """
        out: List[Optional[dict]] = []
        for span in spans or []:
            try:
                h, end = float(span[0]), float(span[1])
            except (TypeError, ValueError, IndexError):
                out.append(None)
                continue
            to = self.at(symbol, end)
            frm = self.at(symbol, h, tolerance=1800.0)
            if to is None:
                out.append(None)
                continue
            pct = ((to - frm) / frm * 100.0) if (frm and frm > 0) else None
            out.append({"value": to, "from": frm, "pct": pct})
        return out

    def hour_slices(self, symbol: str, hours: int = 4,
                    now: Optional[float] = None) -> List[Optional[dict]]:
        """По каждому из часов: значение на конец часа и % к его началу.

        Стенд подписывает OI теми же часами, что и ликвидации: «сколько было
        на конец часа» и «куда пошло за этот час».
        """
        now = float(now if now is not None else time.time())
        last = hour_start(now, self.tz)
        spans = [(last - n * HOUR, min(last - n * HOUR + HOUR, now))
                 for n in range(int(hours) - 1, -1, -1)]
        return self.range_slices(symbol, spans)

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


def fold_slots(cells: List[dict], group: int = 1) -> List[dict]:
    """Базовые слоты → блоки по ``group`` слотов (от старого к свежему).

    Нужно постам в канал: частота постов задаёт длину блока (N/4 часа), а
    копятся данные всегда на одной сетке — 15 минут. Блок получает время
    первого своего слота, поэтому подпись «🕘 18:30» остаётся честной.
    """
    group = max(1, int(group))
    cells = list(cells or [])
    if group == 1:
        return cells
    out: List[dict] = []
    for i in range(0, len(cells), group):
        chunk = cells[i:i + group]
        if not chunk:
            continue
        head = dict(chunk[0])
        coins: Dict[str, float] = {}
        cnt: Dict[str, int] = {}
        cvd: Dict[str, float] = {}
        top: List[dict] = []
        total = longs = shorts = 0.0
        count = 0
        for c in chunk:
            total += float(c.get("total") or 0)
            longs += float(c.get("longs") or 0)
            shorts += float(c.get("shorts") or 0)
            count += int(c.get("count") or 0)
            for sym, usd in (c.get("coins") or {}).items():
                coins[sym] = coins.get(sym, 0.0) + float(usd or 0)
            for sym, n in (c.get("cnt") or {}).items():
                cnt[sym] = cnt.get(sym, 0) + int(n or 0)
            for sym, val in (c.get("cvd") or {}).items():
                cvd[sym] = cvd.get(sym, 0.0) + float(val or 0)
            top.extend(c.get("top") or [])
        top.sort(key=lambda x: float(x.get("usd") or 0), reverse=True)
        head.update({
            "h": chunk[0].get("h"),
            "total": total, "longs": longs, "shorts": shorts, "count": count,
            "coins": coins, "cnt": cnt, "cvd": cvd, "top": top[:TOP_N],
            "empty": all(c.get("empty") for c in chunk),
            "group": group,
        })
        out.append(head)
    return out


def group_start(ts: float, tz: int = 0, slot: int = SLOT_SEC, group: int = 1) -> int:
    """Начало блока из ``group`` базовых слотов, содержащего момент ts."""
    base = max(60, int(slot))
    idx = max(1, int(group))
    start = (slot_index(ts, tz, base) // idx) * idx
    return start * base - tz


def _bias_word(longs: float, shorts: float) -> str:
    """Кто задавал тон в часе: 'long', 'short' или пусто (обе стороны)."""
    if longs <= 0 and shorts <= 0:
        return ""
    if longs > shorts * 1.25:
        return "long"
    if shorts > longs * 1.25:
        return "short"
    return ""


def leaders_of(cell: Optional[dict], oi_rows: Optional[list] = None) -> Dict[str, Any]:
    """Лидеры блока: по деньгам, по числу событий и по перекосу CVD.

    Пост отвечает не только «на сколько горело», но и «где именно»: кто взял
    больше всех денег за окно, кто дал больше всех событий (одна крупная
    ликвидация и сотня мелких — разные истории) и по какой монете сильнее
    всего перекошен поток. Раньше лидера считали только по деньгам, и
    «лидер по количеству» в тексте было взять неоткуда.
    """
    cell = cell or {}
    coins = {str(k): float(v or 0) for k, v in (cell.get("coins") or {}).items()}
    counts = {str(k): int(v or 0) for k, v in (cell.get("cnt") or {}).items()}
    cvd = {str(k): float(v or 0) for k, v in (cell.get("cvd") or {}).items()}
    total = sum(coins.values()) or 0.0
    out: Dict[str, Any] = {}
    if coins:
        sym = max(coins, key=lambda k: coins[k])
        out["vol"] = {"symbol": sym, "usd": coins[sym], "count": counts.get(sym, 0),
                      "share": (coins[sym] / total * 100.0) if total > 0 else None}
    if counts:
        sym = max(counts, key=lambda k: (counts[k], coins.get(k, 0.0)))
        out["count"] = {"symbol": sym, "count": counts[sym], "usd": coins.get(sym, 0.0),
                        "share": (counts[sym] / sum(counts.values()) * 100.0)
                                 if sum(counts.values()) else None}
    if cvd:
        sym = max(cvd, key=lambda k: abs(cvd[k]))
        out["cvd"] = {"symbol": sym, "net": cvd[sym],
                      "usd": coins.get(sym, 0.0), "count": counts.get(sym, 0)}
    # OI: самый заметный рост открытого интереса за окно (ряд уже посчитан)
    best = None
    for sym, days_ in (oi_rows or {}).items():
        for delta, pct in (days_ or []):
            if delta is None or pct is None:
                continue
            if best is None or abs(delta) > abs(best["usd"]):
                best = {"symbol": sym, "usd": float(delta), "pct": float(pct)}
    if best:
        out["oi"] = best
    return out


def pct_change(cur: float, base: float) -> Optional[float]:
    """Изменение в процентах: None, если базы нет или она нулевая."""
    try:
        cur, base = float(cur or 0), float(base or 0)
    except (TypeError, ValueError):
        return None
    if base <= 0:
        return None
    return (cur - base) / base * 100.0


def build_snapshot(board: "HourBoard", oi: "OiHistory", now: Optional[float] = None,
                   span: int = 4, flows: Optional[dict] = None,
                   group: int = 1) -> dict:
    """Стенд для поста: блоки, топ-7 ударов блока, перекос CVD и ряды OI.

    Считаем на стороне сервера и один раз: пост рендерится и для русского
    канала, и для английского — данные у них общие, отличаются подписи.

    ``flows`` — {монета: {слот: {"cvd": Δ, "vol": объём, "has_cvd": bool}}}
    из свечей базовой сетки: по ним считается CVD блока и его доля в объёме
    рынка. Блоков берётся на один больше окна: первому блоку поста нужен
    предыдущий, чтобы показать процент изменения.

    ``group`` — сколько базовых слотов (``board.slot_sec``) приходится на один
    блок поста. Частота постов в канал задаёт его: пост раз в N часов делит
    окно на 4 блока по N/4 часа, а копятся данные всегда на сетке 15 минут
    (``SLOT_SEC``), поэтому смена частоты не рвёт уже собранную историю.
    """
    now = float(now if now is not None else time.time())
    group = max(1, int(group))
    base = max(60, int(getattr(board, "slot_sec", HOUR) or HOUR))
    block_sec = base * group
    tail = fold_slots(board.slots((span + 1) * group, now), group)   # +1 блок — база
    hours = tail[-span:] if len(tail) > span else tail
    prev = fold_slots(board.slots(span * 2 * group, now)[:span * group], group)
    first_h = hours[0].get("h") if hours else None

    # объём и CVD рынка по блокам: складываем только те монеты, где CVD есть
    market: Dict[float, dict] = {}
    for _sym, by_slot in (flows or {}).items():
        for h, cell in (by_slot or {}).items():
            try:
                vol = float((cell or {}).get("vol") or 0)
            except (TypeError, ValueError):
                continue
            if vol <= 0 or not (cell or {}).get("has_cvd"):
                continue
            if first_h is None:
                key = float(h)
            else:
                # слот свечи → блок поста: блоки идут подряд от первого
                key = float(first_h) + ((float(h) - float(first_h)) // block_sec) * block_sec
            acc = market.setdefault(float(key), {"cvd": 0.0, "vol": 0.0})
            acc["vol"] += vol
            acc["cvd"] += float((cell or {}).get("cvd") or 0)

    # монеты, по которым показываем OI: самые крупные за окно
    volume: Dict[str, float] = {}
    for hr in hours:
        for sym, usd in (hr.get("coins") or {}).items():
            volume[sym] = volume.get(sym, 0.0) + float(usd)
    oi_coins = [s for s, _v in sorted(volume.items(), key=lambda kv: kv[1],
                                      reverse=True)[:24]]

    # OI по блокам: суммируем только те монеты, где ряд есть — честный прочерк
    # лучше выдуманного уровня
    spans = [(int(hr.get("h") or 0), min(int(hr.get("h") or 0) + block_sec, int(now)))
             for hr in hours]
    slices: Dict[str, list] = {c: oi.range_slices(c, spans) for c in oi_coins}
    # Изменение OI по монете внутри каждого блока (и за окно целиком): из него
    # берётся «лидер по OI» — самая заметная перекладка открытого интереса.
    oi_delta: List[Dict[str, tuple]] = [dict() for _ in range(span)]
    oi_win: Dict[str, list] = {}
    for _c, rows in (slices or {}).items():
        acc_from = 0.0
        acc_delta = 0.0
        for i, cell in enumerate(rows or []):
            if not cell or i >= span:
                continue
            try:
                frm, to = float(cell.get("from") or 0), float(cell.get("to") or 0)
            except (TypeError, ValueError):
                continue
            if not frm or not to:
                continue
            delta = to - frm
            oi_delta[i][_c] = (delta, (delta / frm * 100.0) if frm else 0.0)
            if not acc_from:
                acc_from = frm
            acc_delta += delta
        if acc_from:
            oi_win[_c] = [(acc_delta, acc_delta / acc_from * 100.0)]
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
    # Предыдущий час для каждого показанного: соседний в хвосте. Порядок
    # важен — иначе час сравнивался бы сам с собой и процент был бы нулевым.
    prev_map = {tail[i].get("h"): tail[i - 1] for i in range(1, len(tail))}
    live_h = group_start(now, board.tz, base, group)
    for i, hr in enumerate(hours):
        cvd = hr.get("cvd") or {}
        was = prev_map.get(hr.get("h")) or {}
        was_coins = was.get("coins") or {}
        coins = []
        for sym, usd in sorted((hr.get("coins") or {}).items(),
                              key=lambda kv: kv[1], reverse=True)[:3]:
            flow = cvd.get(sym)
            coins.append({"symbol": sym, "usd": float(usd),
                          "flow": float(flow) if flow is not None else None,
                          "pct": pct_change(usd, was_coins.get(sym))})
        longs, shorts = float(hr.get("longs") or 0), float(hr.get("shorts") or 0)
        bias = _bias_word(longs, shorts)
        pct = None
        if have_value[i] and have_from[i] and oi_from[i] > 0:
            pct = (oi_value[i] - oi_from[i]) / oi_from[i] * 100.0
        mk = market.get(float(hr.get("h") or 0)) or {}
        m_vol = float(mk.get("vol") or 0)
        m_cvd = float(mk.get("cvd") or 0)
        out_hours.append({
            "h": hr.get("h"), "total": hr.get("total"), "count": hr.get("count"),
            "block_sec": block_sec, "tz": board.tz,
            "longs": longs, "shorts": shorts,
            "side_sum": max(longs, shorts) if bias else 0.0,
            "bias": bias, "coins": coins, "cvd": dict(cvd),
            # сколько событий дала каждая монета блока: из этого лидер по
            # количеству в посте и в сумме лидеров окна
            "cnt": dict(hr.get("cnt") or {}),
            "cvd_sum": sum(float(v) for v in cvd.values()),
            # сравнение с предыдущим часом: видно, больше или меньше стало
            "liq_pct": pct_change(hr.get("total"), was.get("total")),
            # текущий час идёт прямо сейчас: сумма ещё не финальная
            "live": bool(hr.get("h") == live_h),
            "count_pct": pct_change(hr.get("count"), was.get("count")),
            # CVD рынка за час и его доля в объёме торгов (в процентах)
            "cvd_net": m_cvd if m_vol > 0 else None,
            "cvd_share": (m_cvd / m_vol * 100.0) if m_vol > 0 else None,
            "vol_usd": m_vol if m_vol > 0 else None,
            "oi": {"value": oi_value[i] if have_value[i] else None, "pct": pct},
            # Лидеры блока: по деньгам, по числу событий, по перекосу CVD и по
            # сдвигу открытого интереса — из них собирается строка «Лидер часа»
            "leaders": leaders_of(hr, {c: [v] for c, v in (oi_delta[i] or {}).items()}),
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

    win_cell: Dict[str, dict] = {"coins": {}, "cnt": {}, "cvd": {}}
    for hr in hours:
        for sym, usd in (hr.get("coins") or {}).items():
            win_cell["coins"][sym] = win_cell["coins"].get(sym, 0.0) + float(usd or 0)
        for sym, n in (hr.get("cnt") or {}).items():
            win_cell["cnt"][sym] = win_cell["cnt"].get(sym, 0) + int(n or 0)
        for sym, val in (hr.get("cvd") or {}).items():
            win_cell["cvd"][sym] = win_cell["cvd"].get(sym, 0.0) + float(val or 0)
    win_leaders = leaders_of(win_cell, oi_win)

    win_vol = sum(float((market.get(float(hr.get("h") or 0)) or {}).get("vol") or 0)
                  for hr in hours)
    win_cvd = sum(float((market.get(float(hr.get("h") or 0)) or {}).get("cvd") or 0)
                  for hr in hours)
    return {
        "span_hours": span,
        "span_blocks": span,
        "window_hours": int(round(block_sec * span / 3600.0)) or 1,
        "block_sec": block_sec,
        "slot_sec": base,
        "group": group,
        "window_sec": block_sec * span,
        "cvd_4h": win_cvd if win_vol > 0 else None,
        "cvd_4h_share": (win_cvd / win_vol * 100.0) if win_vol > 0 else None,
        "vol_4h": win_vol if win_vol > 0 else None,
        "hours": out_hours,
        "leaders": win_leaders,
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
#: Доска постов в канал: сетка 15 минут. Из неё собираются блоки анализа —
#: один блок равен четверти промежутка между постами (частота 1…10 часов).
SLOTS = HourBoard(slot_sec=SLOT_SEC, keep_hours=KEEP_SLOTS)
