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
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

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
    """Трёхъярусное хранилище: сырые дни + минутные и часовые свёртки.

    Ярусы хранения (см. ``cleanup``):

    * **HOT** — сырые события по дням (``liq_2026-09-17.jsonl``). Читаются
      графиком, кластерами и калибровкой уровней; держим ``hot_days``
      (по умолчанию — прежний месячный TTL, чтобы окно калибровки уровней
      не скудело молча);
    * **WARM** — агрегаты: часовые свёртки (``hours_*.json``) и минутные
      (``min_*.json``, ликвидации long/short по монетам). Свёртки полные —
      они не режутся лимитом шарда, — весят на порядки меньше сырых дней и
      держатся ``warm_days`` (по умолчанию 90). Из них собирается
      исторический анализ (1m/5m/15m/1h/4h/1d) без раздувания горячего
      хранилища;
    * **COLD** (опционально, ``cold_days > 0``) — сырые дни старше HOT не
      удаляются, а пакуются в gzip (``.jsonl.gz``) и живут до COLD-границы.
      Чтение прозрачно: ``iter_events``/``query`` понимают оба вида.
    """

    #: сколько последних дней держать в памяти для мгновенных ответов
    MEM_DAYS = 2

    def __init__(self, base_path: str, ttl_hours: float = MONTH_HOURS,
                 shard_max_mb: float = 48.0, tz: int = 0,
                 hot_days: Optional[float] = None, warm_days: float = 90.0,
                 cold_days: float = 0.0):
        # base_path вида data/liq_history.jsonl -> дни рядом: liq_history_2026-09-17.jsonl
        self.base_path = base_path or ""
        self.dir = os.path.dirname(self.base_path) or "."
        self.stem = os.path.basename(self.base_path) or "liq_history.jsonl"
        if self.stem.endswith(".jsonl"):
            self.stem = self.stem[: -len(".jsonl")]
        self.ttl_hours = max(1.0, float(ttl_hours))
        self.shard_max_bytes = max(1, int(shard_max_mb * 1024 * 1024))
        self.tz = int(tz)
        # ярусы: HOT (сырые дни) / WARM (свёртки) / COLD (gzip сырых дней)
        self.hot_days = (max(1.0, float(hot_days)) if hot_days
                         else self.ttl_hours / 24.0)
        self.warm_days = max(self.hot_days, float(warm_days))
        self.cold_days = max(0.0, float(cold_days))
        # день -> {hour_ts: свёртка}
        self._mem: Dict[str, Dict[int, dict]] = {}
        # день -> {minute_ts: минутная свёртка ликвидаций}
        self._min_mem: Dict[str, Dict[int, dict]] = {}
        self._min_tried: set = set()
        # дни, свёртки которых пробовали читать (чтобы не перечитывать пустоту)
        self._tried: set = set()
        self._dirty: set = set()
        self._min_dirty: set = set()
        self._loaded = False
        self._day = ""              # текущий день записи
        self._dropped = 0           # сколько событий отсеяно лимитом шарда
        self._compressed = 0        # сколько сырых дней упаковано в COLD-gzip
        self._warned = False
        self.error: Optional[str] = None

    # ---- пути -------------------------------------------------------------
    def shard_path(self, day: str) -> str:
        return os.path.join(self.dir, f"{self.stem}_{day}.jsonl")

    def hours_path(self, day: str) -> str:
        return os.path.join(self.dir, f"hours_{day}.json")

    def minutes_path(self, day: str) -> str:
        return os.path.join(self.dir, f"min_{day}.json")

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

    def add_many(self, events) -> int:
        """Пачка событий одним открытием файла на шард.

        ``add`` открывает, пишет и закрывает файл на КАЖДОЕ событие. В
        единственном воркере это делает поток ликвидаций синхронным диском:
        на каскаде в сотни событий подряд воркер стоит на открытиях/закрытиях,
        а ``close()`` под давлением грязных страниц умеет ждать writeback.
        Замер: 200 событий по одному — 8 мс, из них почти всё — open/close.

        Свёртки (``_roll``) и проверки шарда остаются прежними и вызываются
        на каждое событие, поэтому разницы в содержимом файлов нет.
        """
        if not self.base_path:
            return 0
        by_path: Dict[str, list] = {}
        rolled = 0
        for event in events or ():
            t = _num(event.get("timestamp"), 0.0)
            if t <= 0:
                continue
            self._roll(event, t)
            rolled += 1
            self._day = day_key(t)
            path = self.shard_path(self._day)
            if not self._shard_ok(path, event):
                self._dropped += 1
                continue
            by_path.setdefault(path, []).append(event)
        written = 0
        for path, rows in by_path.items():
            try:
                os.makedirs(self.dir or ".", exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write("".join(
                        json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n"
                        for e in rows))
                self.error = None
                self._warned = False
                written += len(rows)
            except OSError as e:
                self.error = str(e)
                if not self._warned:
                    self._warned = True
                    print(f"[history] не пишется {path}: {e}")
        del rolled
        return written

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
        self._roll_minute(event, ts, day, sym, usd)
        self._dirty.add(day)
        self._prune_mem()

    def _roll_minute(self, event: dict, ts: float, day: str,
                     sym: str, usd: float) -> None:
        """Минутная свёртка (WARM-ярус): long/short по монетам за минуту.

        Из минутных ячеек собираются шаги 1m/5m/15m для исторического
        анализа и тепловых карт — сырые шарды для этого читать не нужно
        (и нельзя: лимит шарда режет мелочь, свёртки же полные).
        """
        m = int(ts // 60 * 60)
        cells = self._min_mem.get(day)
        if cells is None:
            cells = self._load_day_minutes(day)
            self._min_mem[day] = cells
        cell = cells.get(m)
        if cell is None:
            cell = {"usd": 0.0, "n": 0, "long": 0.0, "short": 0.0, "sym": {}}
            cells[m] = cell
        is_long = str(event.get("side") or "") == "SELL"
        cell["usd"] += usd
        cell["n"] += 1
        cell["long" if is_long else "short"] += usd
        ms = cell["sym"].setdefault(sym, {"usd": 0.0, "n": 0, "long": 0.0, "short": 0.0})
        ms["usd"] += usd
        ms["n"] += 1
        ms["long" if is_long else "short"] += usd
        self._min_dirty.add(day)

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
        if len(self._mem) > self.MEM_DAYS:
            for day in sorted(self._mem)[: -self.MEM_DAYS]:
                if day in self._dirty:
                    continue        # не выбрасываем то, что не сохранено
                self._mem.pop(day, None)
        if len(self._min_mem) > self.MEM_DAYS:
            for day in sorted(self._min_mem)[: -self.MEM_DAYS]:
                if day in self._min_dirty:
                    continue
                self._min_mem.pop(day, None)

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
        saved += self._flush_minutes()
        return saved

    def _flush_minutes(self) -> int:
        """Минутные свёртки — тем же атомарным способом, что и часовые."""
        saved = 0
        for day in sorted(self._min_dirty):
            cells = self._min_mem.get(day)
            if cells is None:
                continue
            path = self.minutes_path(day)
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
        self._min_dirty.clear()
        return saved

    def _load_day_minutes(self, day: str) -> Dict[int, dict]:
        """Минутные ячейки дня: из памяти или с диска (как часовые)."""
        cells = self._min_mem.get(day)
        if cells is not None and day in self._min_dirty:
            return cells
        if cells is None and day not in self._min_tried:
            self._min_tried.add(day)
            cells = {}
            path = self.minutes_path(day)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in (raw or {}).items():
                    if isinstance(v, dict):
                        cells[int(k)] = v
            except (OSError, ValueError):
                cells = {}
            self._min_mem[day] = cells
        return self._min_mem.get(day) or {}

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
        return (os.path.exists(self.hours_path(day))
                or os.path.exists(self.minutes_path(day))
                or os.path.exists(self.shard_path(day))
                or os.path.exists(self.shard_path(day) + ".gz"))

    # ---- чтение сырых событий --------------------------------------------
    @staticmethod
    def _open_shard(path: str):
        """Открыть шард дня: свежий jsonl или cold-gzip — прозрачно."""
        if path.endswith(".gz"):
            import gzip
            return gzip.open(path, "rt", encoding="utf-8")
        return open(path, "r", encoding="utf-8")

    def iter_events(self, since: float, until: Optional[float] = None,
                    symbol: Optional[str] = None) -> Iterator[dict]:
        """Поток событий за промежуток: дневные шарды читаются построчно.

        Тот же порядок, что у ``query``, но без сбора списка и без лимита —
        нужно там, где события сразу сворачиваются (кластеры графика). Строка
        сначала проверяется подстрокой: разбирать JSON каждого события дня
        ради одной монеты не нужно, а шард дня бывает в десятки мегабайт.
        COLD-дни (``.jsonl.gz``) читаются так же прозрачно.
        """
        if not self.base_path:
            return
        until = float(until if until is not None else time.time())
        since = float(since)
        if until < since:
            since, until = until, since
        needle = symbol if symbol and symbol != "ALL" else None
        day = day_key(since)
        last_day = day_key(until)
        guard = 0
        while day <= last_day and guard < 400:
            guard += 1
            for path in self._shard_files(day):
                try:
                    with self._open_shard(path) as f:
                        for line in f:
                            if not line or line == "\n":
                                continue
                            if needle and needle not in line:
                                continue
                            try:
                                ev = json.loads(line)
                            except ValueError:
                                continue
                            if not isinstance(ev, dict) or "id" not in ev:
                                continue
                            t = _num(ev.get("timestamp"), 0.0)
                            if t < since or t > until:
                                continue
                            if needle and ev.get("symbol") != needle:
                                continue
                            yield ev
                except OSError:
                    pass
            day = next_day(day)

    def query(self, since: float, until: Optional[float] = None,
              symbol: Optional[str] = None, min_usd: float = 0.0,
              limit: int = 2000, newest_first: bool = True) -> List[dict]:
        """События ликвидаций за промежуток: читаем дневные файлы в диапазоне.

        Оптимизация: при newest_first читаем с конца (свежие дни первыми) и
        останавливаемся, когда набрали достаточно для лимита — не нужно
        перебирать 31 день, если свежих 2 дней хватает на 4000 событий.
        """
        if not self.base_path:
            return []
        now = time.time()
        until = float(until if until is not None else now)
        since = float(since)
        rows: List[dict] = []
        # Собираем список дней в диапазоне
        days = []
        day = day_key(since)
        last_day = day_key(until)
        guard = 0
        while day <= last_day and guard < 400:
            guard += 1
            days.append(day)
            day = next_day(day)
        # При newest_first идём с конца, иначе с начала
        iter_days = reversed(days) if newest_first else days
        limit_int = max(1, int(limit))
        # Берём с запасом x2 для дедупликации и фильтрации, но не весь месяц
        # если уже набрали достаточно
        for d in iter_days:
            for path in self._shard_files(d):
                self._read_shard(path, since, until, symbol, min_usd, rows)
            if newest_first and len(rows) >= limit_int * 2:
                # Достаточно для лимита — дальше старые дни не нужны
                break
        rows.sort(key=lambda e: _num(e.get("timestamp"), 0.0), reverse=newest_first)
        if len(rows) > 1:
            seen = set()
            uniq = []
            for ev in rows:
                key = ev.get("id")
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(ev)
                if len(uniq) >= limit_int and newest_first:
                    # При newest_first после сортировки первые limit уже самые свежие
                    # Можно прервать дедуп, если набрали лимит
                    # Но продолжаем только если нужно ещё для точности? Прерываем для скорости
                    if len(rows) > limit_int * 3:
                        break
            rows = uniq
        return rows[:limit_int]

    def _read_shard(self, path: str, since: float, until: float,
                    symbol: Optional[str], min_usd: float, out: List[dict]) -> None:
        try:
            with self._open_shard(path) as f:
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
                                                   "vol": 0.0,
                                                   "long": 0.0, "short": 0.0})
                dst["usd"] += _num(val.get("usd"))
                dst["n"] += int(_num(val.get("n")))
                dst["cvd"] += _num(val.get("cvd"))
                dst["vol"] += _num(val.get("vol"))
                # long/short (в терминах выбитых позиций) нужны историческому
                # анализу по монете: /api/liq/series?symbol=…&tf=1h|4h|1d
                dst["long"] += _num(val.get("long"))
                dst["short"] += _num(val.get("short"))
            for exch, val in (cell.get("exch") or {}).items():
                dst = slot["exch"].setdefault(exch, {"usd": 0.0, "n": 0})
                dst["usd"] += _num(val.get("usd"))
                dst["n"] += int(_num(val.get("n")))
        return {"step_hours": step,
                "points": [steps[k] for k in sorted(steps)],
                "symbols": sorted({s for _, c in cells
                                   for s in (c.get("sym") or {})})}

    def minutes_range(self, since: float, until: Optional[float] = None,
                      now: Optional[float] = None) -> List[Tuple[int, dict]]:
        """Минутные ячейки за промежуток, по возрастанию времени."""
        now = float(now if now is not None else time.time())
        until = float(until if until is not None else now)
        since = max(float(since), until - self.warm_days * DAY)
        out: List[Tuple[int, dict]] = []
        day = day_key(since)
        last_day = day_key(until)
        guard = 0
        while day <= last_day and guard < 400:
            guard += 1
            for m, cell in (self._load_day_minutes(day) or {}).items():
                if m + 60 > since and m <= until:
                    out.append((m, cell))
            day = next_day(day)
        out.sort(key=lambda kv: kv[0])
        return out

    def series_minutes(self, since: float, until: Optional[float] = None,
                       symbols: Optional[Iterable[str]] = None,
                       step_min: int = 1) -> dict:
        """Ряды ликвидаций по минутам: 1m/5m/15m из WARM-свёрток.

        В отличие от ``series`` (часовые шаги), минуты нужны панелям анализа
        и тепловым картам за окно до суток: сырые шарды для этого не читаем —
        минутные свёртки полные и весят на порядки меньше.
        """
        cells = self.minutes_range(since, until)
        step = max(1, int(step_min)) * 60
        steps: Dict[int, dict] = {}
        wanted = {s for s in (symbols or []) if s}
        for m, cell in cells:
            b = m - (m % step)
            slot = steps.setdefault(b, {"t": b, "usd": 0.0, "n": 0,
                                        "long": 0.0, "short": 0.0, "sym": {}})
            usd = _num(cell.get("usd"))
            slot["usd"] += usd
            slot["n"] += int(_num(cell.get("n")))
            slot["long"] += _num(cell.get("long"))
            slot["short"] += _num(cell.get("short"))
            for sym, val in (cell.get("sym") or {}).items():
                if wanted and sym not in wanted:
                    continue
                dst = slot["sym"].setdefault(sym, {"usd": 0.0, "n": 0,
                                                   "long": 0.0, "short": 0.0})
                dst["usd"] += _num(val.get("usd"))
                dst["n"] += int(_num(val.get("n")))
                dst["long"] += _num(val.get("long"))
                dst["short"] += _num(val.get("short"))
        seen_syms = {s for _, c in cells for s in (c.get("sym") or {})}
        if wanted:
            seen_syms &= wanted
        return {"step_min": max(1, int(step_min)),
                "points": [steps[k] for k in sorted(steps)],
                "symbols": sorted(seen_syms)}

    def cleanup(self, now: Optional[float] = None) -> int:
        """Разложить дни по ярусам и убрать просроченное.

        * WARM: часовые (``hours_*``) и минутные (``min_*``) свёртки старше
          ``warm_days`` удаляются — исторический анализ ограничен этим окном;
        * HOT: сырые дни (``stem_*.jsonl``) старше ``hot_days`` либо удаляются
          (COLD выключен), либо упаковываются в gzip и живут до ``cold_days``;
        * COLD: ``.jsonl.gz`` старше ``cold_days`` удаляется.

        Возвращает число удалённых файлов (включая устаревший gzip).
        """
        if not self.base_path:
            return 0
        now = float(now if now is not None else time.time())
        raw_keep_from = day_key(now - self.hot_days * DAY)
        warm_keep_from = day_key(now - self.warm_days * DAY)
        cold_keep_from = day_key(now - self.cold_days * DAY) if self.cold_days else ""
        removed = 0
        try:
            names = os.listdir(self.dir or ".")
        except OSError:
            return 0
        for name in names:
            day = ""
            kind = ""
            if name.startswith(self.stem + "_"):
                rest = name[len(self.stem) + 1:]
                if rest.endswith(".jsonl.gz"):
                    day, kind = rest[: -len(".jsonl.gz")], "gz"
                elif rest.endswith(".jsonl"):
                    day, kind = rest[: -len(".jsonl")], "raw"
                else:
                    day, kind = rest.split(".")[0], "raw"
            elif name.startswith("hours_") and name.endswith(".json"):
                day, kind = name[len("hours_"):-len(".json")], "warm"
            elif name.startswith("min_") and name.endswith(".json"):
                day, kind = name[len("min_"):-len(".json")], "warm"
            else:
                continue
            if len(day) != 10:
                continue
            if kind == "warm":
                if day >= warm_keep_from:
                    continue
            elif kind == "gz":
                # упакованный день живёт до COLD-границы (COLD выключен —
                # таких файлов быть не должно, убираем на всякий случай)
                if self.cold_days and day >= cold_keep_from:
                    continue
            else:                           # сырой день
                if day >= raw_keep_from:
                    continue
                if self.cold_days and day >= cold_keep_from:
                    # HOT вышел, COLD ещё нет: пакуем и оставляем
                    if self.compress_shard(day):
                        continue
            try:
                os.remove(os.path.join(self.dir, name))
                removed += 1
                if kind != "warm":
                    self._mem.pop(day, None)
                    self._dirty.discard(day)
                    self._tried.discard(day)
                else:
                    self._min_mem.pop(day, None)
                    self._min_dirty.discard(day)
                    self._min_tried.discard(day)
            except OSError:
                continue
        return removed

    def compress_shard(self, day: str) -> bool:
        """Упаковать сырой день в gzip (HOT → COLD) и удалить оригинал.

        Возвращает True, если после вызова данные дня лежат в ``.jsonl.gz``
        (упаковали сейчас или он уже лежит там). Атомарно: сначала пишем
        ``.gz.tmp``, затем переименовываем.
        """
        src = self.shard_path(day)
        dst = src + ".gz"
        if not os.path.exists(src):
            return os.path.exists(dst)
        tmp = dst + ".tmp"
        try:
            import gzip
            os.makedirs(self.dir or ".", exist_ok=True)
            with open(src, "rb") as f_in, gzip.open(tmp, "wb", compresslevel=6) as f_out:
                while True:
                    chunk = f_in.read(1 << 20)
                    if not chunk:
                        break
                    f_out.write(chunk)
            os.replace(tmp, dst)
            os.remove(src)
            self._compressed += 1
            return True
        except OSError as e:
            self.error = str(e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    def _shard_files(self, day: str) -> List[str]:
        """Пути к сырым данным дня: свежий jsonl и/или cold-gzip."""
        out = []
        raw = self.shard_path(day)
        gz = raw + ".gz"
        if os.path.exists(raw):
            out.append(raw)
        if os.path.exists(gz):
            out.append(gz)
        return out

    # ---- состояние --------------------------------------------------------
    def stats(self) -> dict:
        days: List[str] = []
        gz_days: List[str] = []
        min_days: List[str] = []
        try:
            for name in os.listdir(self.dir or "."):
                if name.startswith(self.stem + "_"):
                    day = name[len(self.stem) + 1:][:10]
                    if len(day) == 10:
                        if name.endswith(".gz"):
                            gz_days.append(day)
                        else:
                            days.append(day)
                elif name.startswith("min_") and name.endswith(".json"):
                    day = name[len("min_"):-len(".json")]
                    if len(day) == 10:
                        min_days.append(day)
        except OSError:
            pass
        size = 0
        size_by_tier = {"hot": 0, "warm": 0, "cold": 0}
        for name in (os.listdir(self.dir or ".") if os.path.isdir(self.dir or ".") else []):
            try:
                b = os.path.getsize(os.path.join(self.dir or ".", name))
            except OSError:
                continue
            if name.startswith(self.stem + "_") and name.endswith(".gz"):
                size_by_tier["cold"] += b
            elif name.startswith(self.stem + "_"):
                size_by_tier["hot"] += b
            elif name.startswith("hours_") or name.startswith("min_"):
                size_by_tier["warm"] += b
            else:
                continue
            size += b
        return {
            "ttl_hours": self.ttl_hours,
            "days_on_disk": len(days),
            "first_day": min(days + gz_days) if (days or gz_days) else None,
            "last_day": max(days + gz_days) if (days or gz_days) else None,
            "bytes": size,
            "dropped_small": self._dropped,
            "error": self.error,
            # ярусы хранения: HOT сырые дни / WARM свёртки / COLD gzip
            "tiers": {
                "hot_days_retention": round(self.hot_days, 2),
                "warm_days_retention": round(self.warm_days, 2),
                "cold_days_retention": round(self.cold_days, 2) if self.cold_days else None,
                "hot_files": len(days),
                "warm_hour_files": _count_prefix(self.dir, "hours_"),
                "warm_minute_files": len(min_days),
                "cold_gz_files": len(gz_days),
                "cold_compressed_total": self._compressed,
                "bytes_hot": size_by_tier["hot"],
                "bytes_warm": size_by_tier["warm"],
                "bytes_cold": size_by_tier["cold"],
            },
        }


def _count_prefix(dir_path: str, prefix: str) -> int:
    """Сколько файлов с префиксом лежит в каталоге (для stats)."""
    try:
        return sum(1 for name in os.listdir(dir_path or ".")
                   if name.startswith(prefix))
    except OSError:
        return 0


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


def disk_has_series(path: str) -> bool:
    """Есть ли в файле истории непустой ``series`` (без разбора всего файла).

    Нужна перед записью пустого состояния: пустой снимок не должен затирать
    накопленную историю. Иначе сервер, поднятый до засева данных или без
    потока сделок, стирал бы месяц OI и профиля объёма — а по ним считаются
    уровни ликвидаций. Читаем начало файла: ключ ``series`` идёт первым, а
    миллион байт покрывает пустой снимок целиком.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(1 << 20)
    except OSError:
        return False
    at = head.find('"series":')
    if at < 0:
        return True            # чужой формат: не наше дело, но и не затираем
    rest = head[at + len('"series":'):].lstrip()
    return not rest.startswith("{}")
