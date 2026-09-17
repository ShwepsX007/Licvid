"""История рынка за месяц: ликвидации, CVD, объём и OI.

Задача модуля — чтобы данные не терялись через сутки. Раньше всё держалось в
памяти процесса (сутки) и в одном JSONL, который при разрастании переписывался
целиком: месячной истории не получалось ни по объёму, ни по скорости.

Как устроено:

    * сырые события ликвидаций лежат по дням — ``liq_2026-09-17.jsonl``.
      Свежий день пишется построчно (append), старые дни просто читаются;
    * рядом по дню лежат часовые свёртки — ``hours_2026-09-17.json``: сумма и
      число ликвидаций по монетам и биржам, лонги/шорты, максимум дня; туда же
      идут CVD и объём по монетам. Свёртки отвечают на вопросы страниц и
      сервисов (месяц, корреляции, сторож пампов) без чтения миллионов строк;
    * OI живёт отдельной историей (hour_board.OiHistory), у неё свой файл;
    * по истечении TTL (по умолчанию 31 сутки) дневные файлы удаляются.

Свёртки за сегодня и вчера держатся в памяти и сбрасываются на диск раз в
несколько минут — так суточный сервис не зависит от чтения JSONL.
"""
from __future__ import annotations

import calendar
import json
import os
import time
from typing import Dict, Iterable, List, Optional, Tuple

HOUR = 3600
DAY = 86400
MONTH_HOURS = 31 * 24          # 31 сутки — «минимум месяц» из задания


def day_key(ts: float) -> str:
    """Ключ дня по UTC-календарю (файлы истории — по дням, не по ТЗ канала)."""
    return time.strftime("%Y-%m-%d", time.gmtime(float(ts)))


def hour_start(ts: float) -> int:
    return int(float(ts) // HOUR * HOUR)


def next_day(day: str) -> str:
    """Следующий день календаря UTC (дни истории — UTC-сутки)."""
    try:
        base = calendar.timegm(time.strptime(day, "%Y-%m-%d"))
    except ValueError:
        return day
    return day_key(base + DAY)


def _num(v, default=0.0) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if n == n and n not in (float("inf"), float("-inf")) else default


class HistoryStore:
    """Месячное хранилище: сырые ликвидации по дням + часовые свёртки."""

    #: сколько последних дней держать в памяти для мгновенных ответов
    MEM_DAYS = 2

    def __init__(self, base_path: str, ttl_hours: float = MONTH_HOURS,
                 shard_max_mb: float = 48.0, tz: int = 0):
        # base_path вида data/liq_history.jsonl -> дни рядом: liq_history_2026-09-17.jsonl
        self.base_path = base_path or ""
        self.dir = os.path.dirname(self.base_path) or "."
        self.stem = os.path.basename(self.base_path) or "liq_history.jsonl"
        if self.stem.endswith(".jsonl"):
            self.stem = self.stem[: -len(".jsonl")]
        self.ttl_hours = max(1.0, float(ttl_hours))
        self.shard_max_bytes = max(1, int(shard_max_mb * 1024 * 1024))
        self.tz = int(tz)
        # день -> {hour_ts: свёртка}
        self._mem: Dict[str, Dict[int, dict]] = {}
        # дни, свёртки которых пробовали читать (чтобы не перечитывать пустоту)
        self._tried: set = set()
        self._dirty: set = set()
        self._loaded = False
        self._day = ""              # текущий день записи
        self._dropped = 0           # сколько событий отсеяно лимитом шарда
        self._warned = False
        self.error: Optional[str] = None

    # ---- пути -------------------------------------------------------------
    def shard_path(self, day: str) -> str:
        return os.path.join(self.dir, f"{self.stem}_{day}.jsonl")

    def hours_path(self, day: str) -> str:
        return os.path.join(self.dir, f"hours_{day}.json")

    @property
    def legacy_path(self) -> str:
        return self.base_path

    # ---- запись -----------------------------------------------------------
    def add(self, event: dict, ts: Optional[float] = None) -> bool:
        """Сырое событие: в дневной файл + в часовую свёртку дня."""
        if not self.base_path:
            return False
        t = _num(ts if ts is not None else event.get("timestamp"), 0.0)
        if t <= 0:
            return False
        self._roll(event, t)
        day = day_key(t)
        self._day = day
        path = self.shard_path(day)
        if not self._shard_ok(path, event):
            self._dropped += 1
            return False
        try:
            os.makedirs(self.dir or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
            self.error = None
            self._warned = False
            return True
        except OSError as e:
            self.error = str(e)
            if not self._warned:
                self._warned = True
                print(f"[history] не пишется {path}: {e}")
            return False

    def _shard_ok(self, path: str, event: dict) -> bool:
        """Шард дня не должен разрастаться без предела: крупные события пишем
        всегда, мелочь — пока файл меньше лимита (свёртки при этом полные)."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return True
        if size <= self.shard_max_bytes:
            return True
        return _num(event.get("usd"), 0.0) >= 50_000.0

    def _day_cells(self, day: str) -> Dict[int, dict]:
        """Ячейки дня: если дня нет в памяти — сначала читаем диск.

        Так час продолжается с того, что уже записано. Иначе после перезапуска
        первое же сохранение затирало бы свёртки дня, собранные до него
        (сырые события при этом целы, а часовые итоги терялись бы).
        """
        cells = self._mem.get(day)
        if cells is None:
            cells = self._load_day_hours(day)
            self._mem[day] = cells
        return cells

    def _roll(self, event: dict, ts: float) -> None:
        h = hour_start(ts)
        day = day_key(ts)
        cells = self._day_cells(day)
        cell = cells.get(h)
        if cell is None:
            cell = {"liq_usd": 0.0, "liq_count": 0, "liq_long": 0.0,
                    "liq_short": 0.0, "max_usd": 0.0, "max_symbol": "",
                    "cvd": 0.0, "vol": 0.0, "has_cvd": False,
                    "sym": {}, "exch": {}}
            cells[h] = cell
        usd = _num(event.get("usd"), 0.0)
        sym = str(event.get("symbol") or "?")
        exch = str(event.get("exchange") or "?")
        cell["liq_usd"] += usd
        cell["liq_count"] += 1
        # side в терминах ордера: SELL = вынесли лонг, BUY = вынесли шорт
        if str(event.get("side") or "") == "SELL":
            cell["liq_long"] += usd
        else:
            cell["liq_short"] += usd
        if usd > cell["max_usd"]:
            cell["max_usd"] = usd
            cell["max_symbol"] = sym
        s = cell["sym"].setdefault(sym, {"usd": 0.0, "n": 0, "long": 0.0, "short": 0.0})
        s["usd"] += usd
        s["n"] += 1
        s["long" if str(event.get("side") or "") == "SELL" else "short"] += usd
        e = cell["exch"].setdefault(exch, {"usd": 0.0, "n": 0})
        e["usd"] += usd
        e["n"] += 1
        self._dirty.add(day)
        self._prune_mem()

    def add_flow(self, symbol: str, ts: float, cvd: float = 0.0,
                 vol: float = 0.0) -> None:
        """CVD и объём в часовую свёртку (для месячных графиков и сервисов)."""
        if not self.base_path or not symbol:
            return
        cvd = _num(cvd, 0.0)
        vol = _num(vol, 0.0)
        if not cvd and not vol:
            return
        day = day_key(ts)
        h = hour_start(ts)
        cells = self._day_cells(day)
        cell = cells.get(h)
        if cell is None:
            cell = {"liq_usd": 0.0, "liq_count": 0, "liq_long": 0.0,
                    "liq_short": 0.0, "max_usd": 0.0, "max_symbol": "",
                    "cvd": 0.0, "vol": 0.0, "has_cvd": False,
                    "sym": {}, "exch": {}}
            cells[h] = cell
        if cvd:
            cell["cvd"] += cvd
            cell["has_cvd"] = True
            s = cell["sym"].setdefault(symbol, {"usd": 0.0, "n": 0, "long": 0.0,
                                                "short": 0.0, "cvd": 0.0, "vol": 0.0})
            s["cvd"] = _num(s.get("cvd"), 0.0) + cvd
        if vol:
            cell["vol"] += vol
            s = cell["sym"].setdefault(symbol, {"usd": 0.0, "n": 0, "long": 0.0,
                                                "short": 0.0, "cvd": 0.0, "vol": 0.0})
            s["vol"] = _num(s.get("vol"), 0.0) + vol
        self._dirty.add(day)
        self._prune_mem()

    def _prune_mem(self) -> None:
        """В памяти держим только свежие дни — остальное читаем с диска."""
        if len(self._mem) <= self.MEM_DAYS:
            return
        days = sorted(self._mem)
        for day in days[: -self.MEM_DAYS]:
            if day in self._dirty:
                continue            # не выбрасываем то, что не сохранено
            self._mem.pop(day, None)

    # ---- сохранение свёрток ----------------------------------------------
    def flush(self, force: bool = False) -> int:
        """Записать свёртки изменённых дней на диск (атомарно)."""
        if not self.base_path:
            return 0
        saved = 0
        for day in sorted(self._dirty):
            if not force and day not in self._mem:
                continue
            cells = self._mem.get(day)
            if cells is None:
                continue
            path = self.hours_path(day)
            tmp = path + ".tmp"
            try:
                os.makedirs(self.dir or ".", exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({str(k): v for k, v in sorted(cells.items())}, f,
                              ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, path)
                saved += 1
            except OSError as e:
                self.error = str(e)
                continue
        self._dirty.clear()
        return saved

    # ---- чтение свёрток ---------------------------------------------------
    def _load_day_hours(self, day: str) -> Dict[int, dict]:
        cells = self._mem.get(day)
        if cells is not None and day in self._dirty:
            return cells
        if cells is None and day not in self._tried:
            self._tried.add(day)
            cells = {}
            path = self.hours_path(day)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in (raw or {}).items():
                    if isinstance(v, dict):
                        cells[int(k)] = v
            except (OSError, ValueError):
                cells = {}
            if day in self._mem or len(self._mem) < self.MEM_DAYS:
                self._mem[day] = cells
        return self._mem.get(day) or {}

    def hours_range(self, since: float, until: Optional[float] = None,
                    now: Optional[float] = None) -> List[Tuple[int, dict]]:
        """Часовые свёртки за промежуток, по возрастанию времени."""
        now = float(now if now is not None else time.time())
        until = float(until if until is not None else now)
        since = max(float(since), until - self.ttl_hours * HOUR)
        out: List[Tuple[int, dict]] = []
        day = day_key(since)
        last_day = day_key(until)
        guard = 0
        while day <= last_day and guard < 400:
            guard += 1
            for h, cell in (self._load_day_hours(day) or {}).items():
                # час берём, если он пересекается с промежутком: ячейка — это
                # весь час, а не мгновение, иначе запрос на «последний час»
                # терял бы свежую ячейку (её начало раньше границы окна)
                if h + HOUR > since and h <= until:
                    out.append((h, cell))
            day = next_day(day)
        out.sort(key=lambda kv: kv[0])
        return out

    def has_data(self, day: str) -> bool:
        if day in self._mem:
            return True
        return os.path.exists(self.hours_path(day)) or os.path.exists(self.shard_path(day))

    # ---- чтение сырых событий --------------------------------------------
    def query(self, since: float, until: Optional[float] = None,
              symbol: Optional[str] = None, min_usd: float = 0.0,
              limit: int = 2000, newest_first: bool = True) -> List[dict]:
        """События ликвидаций за промежуток: читаем дневные файлы в диапазоне."""
        if not self.base_path:
            return []
        now = time.time()
        until = float(until if until is not None else now)
        since = float(since)
        rows: List[dict] = []
        day = day_key(since)
        last_day = day_key(until)
        guard = 0
        while day <= last_day and guard < 400:
            guard += 1
            self._read_shard(self.shard_path(day), since, until, symbol, min_usd, rows)
            day = next_day(day)
        rows.sort(key=lambda e: _num(e.get("timestamp"), 0.0), reverse=newest_first)
        if len(rows) > 1:
            # страховка от повторов (например, миграция унаследованного файла)
            seen = set()
            uniq = []
            for ev in rows:
                key = ev.get("id")
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(ev)
            rows = uniq
        return rows[: max(1, int(limit))]

    def _read_shard(self, path: str, since: float, until: float,
                    symbol: Optional[str], min_usd: float, out: List[dict]) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    self._take(line, since, until, symbol, min_usd, out)
        except OSError:
            return

    def import_legacy(self, known: Optional[Iterable[str]] = None,
                      limit: int = 200_000) -> int:
        """Перенести события старого одиночного файла в дневные шарды.

        Файл прежних версий писался целиком в один jsonl; чтобы история за
        месяц не расползалась по двум форматам, события раскладываем по дням
        (и пересчитываем часовые свёртки), после чего файл удаляем.
        """
        path = self.legacy_path
        if not path or not os.path.exists(path):
            return 0
        seen = set(known or ())
        rows = load_legacy_jsonl(path, limit=limit)
        moved = 0
        for ev in rows:
            if ev.get("id") in seen:
                continue
            if self.add(ev):
                seen.add(ev.get("id"))
                moved += 1
        self.flush(force=True)
        if moved or not rows:
            try:
                os.remove(path)
            except OSError:
                pass
        return moved

    def _take(self, line: str, since: float, until: float,
              symbol: Optional[str], min_usd: float, out: List[dict]) -> None:
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict) or "id" not in ev:
            return
        t = _num(ev.get("timestamp"), 0.0)
        if t < since or t > until:
            return
        if symbol and symbol != "ALL" and ev.get("symbol") != symbol:
            return
        if min_usd > 0 and _num(ev.get("usd"), 0.0) < min_usd:
            return
        out.append(ev)

    # ---- агрегаты ---------------------------------------------------------
    def totals(self, since: float, until: Optional[float] = None,
               symbol: Optional[str] = None) -> dict:
        """Итоги за промежуток по часовым свёрткам: сумма, число, лонги, шорты,
        биржи и топ монет. Часовые свёртки полные, в отличие от сырых шардов."""
        cells = self.hours_range(since, until)
        return aggregate_hours(cells, symbol=symbol)

    def days(self, since: float, until: Optional[float] = None,
             symbol: Optional[str] = None) -> List[dict]:
        """Разбивка по дням — для месячных графиков на сайте."""
        cells = self.hours_range(since, until)
        by_day: Dict[str, List[Tuple[int, dict]]] = {}
        for h, cell in cells:
            by_day.setdefault(day_key(h), []).append((h, cell))
        out = []
        for day in sorted(by_day):
            agg = aggregate_hours(by_day[day], symbol=symbol)
            agg["day"] = day
            out.append(agg)
        return out

    def series(self, since: float, until: Optional[float] = None,
               symbols: Optional[Iterable[str]] = None, step_hours: int = 1) -> dict:
        """Ряды по часам: ликвидации, CVD и объём — по монете и в сумме."""
        cells = self.hours_range(since, until)
        step = max(1, int(step_hours))
        steps: Dict[int, dict] = {}
        wanted = {s for s in (symbols or []) if s}
        for h, cell in cells:
            b = h - (h % (step * HOUR))
            slot = steps.setdefault(b, {"h": b, "liq_usd": 0.0, "liq_count": 0,
                                        "liq_long": 0.0, "liq_short": 0.0,
                                        "cvd": 0.0, "vol": 0.0, "sym": {}, "exch": {}})
            slot["liq_usd"] += _num(cell.get("liq_usd"))
            slot["liq_count"] += int(_num(cell.get("liq_count")))
            slot["liq_long"] += _num(cell.get("liq_long"))
            slot["liq_short"] += _num(cell.get("liq_short"))
            slot["cvd"] += _num(cell.get("cvd"))
            slot["vol"] += _num(cell.get("vol"))
            for sym, val in (cell.get("sym") or {}).items():
                if wanted and sym not in wanted:
                    continue
                dst = slot["sym"].setdefault(sym, {"usd": 0.0, "n": 0, "cvd": 0.0,
                                                   "vol": 0.0})
                dst["usd"] += _num(val.get("usd"))
                dst["n"] += int(_num(val.get("n")))
                dst["cvd"] += _num(val.get("cvd"))
                dst["vol"] += _num(val.get("vol"))
            for exch, val in (cell.get("exch") or {}).items():
                dst = slot["exch"].setdefault(exch, {"usd": 0.0, "n": 0})
                dst["usd"] += _num(val.get("usd"))
                dst["n"] += int(_num(val.get("n")))
        return {"step_hours": step,
                "points": [steps[k] for k in sorted(steps)],
                "symbols": sorted({s for _, c in cells
                                   for s in (c.get("sym") or {})})}

    def cleanup(self, now: Optional[float] = None) -> int:
        """Удалить дневные файлы старше TTL (месяц по умолчанию)."""
        if not self.base_path:
            return 0
        now = float(now if now is not None else time.time())
        cutoff = now - self.ttl_hours * HOUR
        keep_from = day_key(cutoff)
        removed = 0
        try:
            names = os.listdir(self.dir or ".")
        except OSError:
            return 0
        for name in names:
            if not name.startswith(self.stem + "_"):
                continue
            rest = name[len(self.stem) + 1:]
            day = rest.split(".")[0]
            if len(day) != 10 or day >= keep_from:
                continue
            try:
                os.remove(os.path.join(self.dir, name))
                removed += 1
                self._mem.pop(day, None)
                self._dirty.discard(day)
                self._tried.discard(day)
            except OSError:
                continue
        return removed

    # ---- состояние --------------------------------------------------------
    def stats(self) -> dict:
        days: List[str] = []
        try:
            for name in os.listdir(self.dir or "."):
                if name.startswith(self.stem + "_") and name.endswith(".jsonl"):
                    day = name[len(self.stem) + 1:][:10]
                    if len(day) == 10:
                        days.append(day)
        except OSError:
            pass
        size = 0
        for name in (os.listdir(self.dir or ".") if os.path.isdir(self.dir or ".") else []):
            if name.startswith(self.stem + "_") or name.startswith("hours_"):
                try:
                    size += os.path.getsize(os.path.join(self.dir or ".", name))
                except OSError:
                    pass
        return {
            "ttl_hours": self.ttl_hours,
            "days_on_disk": len(days),
            "first_day": min(days) if days else None,
            "last_day": max(days) if days else None,
            "bytes": size,
            "dropped_small": self._dropped,
            "error": self.error,
        }


def load_legacy_jsonl(path: str, limit: int = 200_000) -> List[dict]:
    """Прочитать одиночный jsonl прежних версий (без фильтров по времени)."""
    rows: List[dict] = []
    if not path or not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and "id" in ev:
                    rows.append(ev)
                    if len(rows) >= limit:
                        break
    except OSError:
        return rows
    return rows


def aggregate_hours(cells: Iterable[Tuple[int, dict]],
                    symbol: Optional[str] = None) -> dict:
    """Свернуть часовые ячейки в один итог (при желании — по одной монете).

    Часовые свёртки полные, поэтому итог не зависит от того, урезан ли сырой
    дневной файл лимитом размера: считаем по ячейкам, а не по событиям.
    """
    cells = list(cells)
    total = count = longs = shorts = 0.0
    cvd = vol = 0.0
    by_symbol: Dict[str, float] = {}
    by_exchange: Dict[str, float] = {}
    one = bool(symbol) and symbol != "ALL"
    best = 0.0
    best_sym = ""
    since = None
    until = None
    for h, cell in cells:
        if since is None or h < since:
            since = h
        if until is None or h > until:
            until = h
        syms = cell.get("sym") or {}
        if one:
            val = syms.get(symbol) or {}
            usd = _num(val.get("usd"))
            total += usd
            count += int(_num(val.get("n")))
            longs += _num(val.get("long"))
            shorts += _num(val.get("short"))
            cvd += _num(val.get("cvd"))
            vol += _num(val.get("vol"))
            if usd:
                by_symbol[symbol] = by_symbol.get(symbol, 0.0) + usd
            continue
        total += _num(cell.get("liq_usd"))
        count += int(_num(cell.get("liq_count")))
        longs += _num(cell.get("liq_long"))
        shorts += _num(cell.get("liq_short"))
        cvd += _num(cell.get("cvd"))
        vol += _num(cell.get("vol"))
        for sym, val in syms.items():
            usd = _num(val.get("usd"))
            by_symbol[sym] = by_symbol.get(sym, 0.0) + usd
            if usd > best:
                best = usd
                best_sym = sym
        for exch, val in (cell.get("exch") or {}).items():
            by_exchange[exch] = by_exchange.get(exch, 0.0) + _num(val.get("usd"))
    return {
        "since": since,
        "until": until,
        "hours": len(cells),
        "usd": round(total, 2),
        "count": int(count),
        "long_usd": round(longs, 2),
        "short_usd": round(shorts, 2),
        "cvd": round(cvd, 2),
        "vol": round(vol, 2),
        "cvd_share": (round(cvd / vol * 100.0, 2) if vol else None),
        "max_symbol": best_sym,
        "by_symbol": dict(sorted(by_symbol.items(), key=lambda kv: kv[1], reverse=True)),
        "by_exchange": dict(sorted(by_exchange.items(), key=lambda kv: kv[1], reverse=True)),
    }
