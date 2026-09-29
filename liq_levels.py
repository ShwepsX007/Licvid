"""Расчётные уровни ликвидаций: где стоят чужие вынужденные выходы.

Что здесь считается
-------------------
Ликвидация одной позиции — чистая арифметика: цена входа, плечо и
поддерживающая маржа. Позиции приватны, но их сумма видна как открытый
интерес (OI). Значит, OI можно разложить на правдоподобные позиции:

  1. где набирали — по цене слота, в котором OI вырос (VWAP пятиминутки);
  2. сколько набрали — по дельте OI: рост = новые позиции, падение OI
     уровней не создаёт, оно лишь уменьшает выжившую часть (``survive``);
  3. чьи это позиции — по перевесу сторон (тейкер/funding/тики): доля лонгов;
  4. с каким плечом — по распределению плеч (гипотеза, её и калибруем);
  5. докуда доживёт — по ступеням поддерживающей маржи бирж.

Масса «размазывается» колоколом: цена входа известна неточно (VWAP слота,
кросс-маржа, разные ступени MMR), поэтому уровень — это не линия, а полоса
шириной около процента. Из лестницы убирается то, что уже отработало: факт
ликвидаций вычитается из ближайших уровней той же стороны.

Чего модуль не умеет
--------------------
Это ОЦЕНКА, а не факт: биржа не публикует ни плечи клиентов, ни их входы.
Поэтому в ответе всегда есть покрытие и оговорки (``notes``), в интерфейсе —
метка «оценка», а где данных мало — честно пустая картинка вместо выдумки.

Модуль не ходит в сеть сам: ``warm`` дёргает загрузку перевеса и риск-лимитов,
всё остальное читается из уже собранных рядов (OI, профиль объёма, история).
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("liqscore.levels")

HOUR = 3600.0
BUCKET = 300.0

#: Гипотеза о плечах: где ночует основная масса розницы и среднего счёта.
#: Калибровка по факту двигает её целиком (``lev_scale``), не переписывая.
DEFAULT_LEV_DIST: Tuple[Tuple[float, float], ...] = (
    (5, 0.18), (10, 0.26), (20, 0.24), (25, 0.10), (50, 0.14), (100, 0.08),
)

#: Сетка калибровки: масштаб плеч × ширина колокола.
CALIB_LEV = (0.5, 0.7, 1.0, 1.4, 2.0)
CALIB_SPREAD = (0.5, 0.75, 1.0, 1.5, 2.0)
#: Калибровка сравнивается на грубой сетке (~1 %): мелкая штрафует верную
#: модель за размазанность колокола, крупная путает соседние ступени плеч.
CALIB_COARSE_REL = 0.01
CALIB_TTL_SEC = 86400.0
# Бюджет пересчётов калибровки. Кэш калибровок живёт сутки, но после каждого
# рестарта он ПУСТ, и первый же проход фона платил пересчёт по всем монетам
# батча разом: на бою 29.09.2026 это дало провал на 8 с, в течение которого
# event loop оставался отзывчивым (loop_lag_max_ms 481), обработчик отвечал за
# 15 мс, а запросы зрителей стояли в очереди по 5-8 с — воркер делил CPU с
# расчётом. Бюджет растягивает пересчёты по проходам: монета без свежей
# калибровки работает с прежним или умолчательным масштабом и добирает пересчёт
# следующим проходом. Явный recalibrate=True из API бюджет не расходует.
# Схлопывание строк перед лестницей: окно 30 суток даёт тысячи точек OI, а
# различных цен входа в пределах шага сетки — сотни. Выключение (0) возвращает
# обход каждой строки, как было.
AGG_ROWS = os.getenv("LIQSCOPE_LEVELS_AGG_ROWS", "1").strip() not in (
    "0", "false", "no", "off")
# Ширина корзины схлопывания в долях шага сетки. Замер на профиле боевой формы
# (8640 точек OI, 30 суток): корзина в шаг сетки — 272 строки и лестница за
# 25.8 мс вместо 738.5 (×28.6), но форма лестницы уезжает на 1.67 % массы и до
# 6.9 % на отдельной крупной ячейке; шаг/2 — 535 строк, 52.1 мс (×14.2),
# отклонение 0.88 % и не выше 3.1 % на ячейке; шаг/4 — ×8.3 и 0.42 %.
# Полшага сетки взяты умолчанием: это в 8 раз уже колокола размазывания
# (KERNEL_RADIUS = 4 шага), поэтому форма остаётся в пределах неопределённости
# самой модели, а проход фона перестаёт быть секундным.
try:
    AGG_BUCKET = min(1.0, max(
        0.05, float(os.getenv("LIQSCOPE_LEVELS_AGG_BUCKET", "0.5") or 0.5)))
except (TypeError, ValueError):
    AGG_BUCKET = 0.5
CALIB_BUDGET = max(0, int(os.getenv("LIQSCOPE_LEVELS_CALIB_BUDGET", "2") or 2))
CALIB_BUDGET_SEC = max(1.0, float(
    os.getenv("LIQSCOPE_LEVELS_CALIB_BUDGET_SEC", "30") or 30))
MIN_CALIB_EVENTS = 20
MIN_CALIB_SCORE = 0.15

#: Радиус колокола в шагах сетки (шире — уже шум, а не уровень).
KERNEL_RADIUS = 4
MAX_ROWS = 20000
MAX_EVENTS = 20000
#: Сколько уровней по умолчанию отдаём клиенту.
MAX_LEVELS = 400
#: Событие считается «про этот уровень», если оно в пределах 2 % цены.
EXEC_TOLERANCE_REL = 0.02
#: Порог магнита по умолчанию: доля всей массы лестницы.
MAGNET_MIN_SHARE = 0.02
#: Куда не пускаем уровни: дальше половины цены это уже не тот рынок.
MAX_DISTANCE_REL = 0.5

PAYLOAD_TTL = max(1.0, float(os.getenv("LIQSCOPE_LEVELS_TTL", "20") or 20))
#: Шаг бакета цены в ключе кэша ответов, в долях цены (0.0005 = 0.05%).
#: Цена в ключе с точностью до 8 знаков означала, что кэш НЕ СРАБАТЫВАЛ НИКОГДА:
#: клиент передаёт цену со своего графика, а фон — текущую цену ленты, и они
#: различаются в последнем знаке на каждом запросе. Пересчёт лестницы шёл на
#: каждый вызов. Ответ и так может быть старше PAYLOAD_TTL (20 с), за которые
#: цена уходит заметно дальше 0.05%, поэтому бакет ничего не ломает.
PRICE_KEY_STEP = max(0.0, float(os.getenv("LIQSCOPE_LEVELS_PRICE_KEY_STEP",
                                          "0.0005") or 0.0005))


def price_key(price: Any) -> float:
    """Ключ цены для кэша: одинаковая корзина при движении в пределах шага.

    Бакет логарифмический, поэтому шаг одинаково работает и для BTC по 60000,
    и для щиткоина по 0.00001. При ``PRICE_KEY_STEP = 0`` возвращается цена
    как есть — прежнее поведение.
    """
    try:
        p = float(price or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if p <= 0.0 or PRICE_KEY_STEP <= 0.0:
        return round(p, 8)
    return float(round(math.log(p) / math.log1p(PRICE_KEY_STEP)))
PRICE_TTL = max(60.0, float(os.getenv("LIQSCOPE_LEVELS_PRICE_TTL", "300") or 300))
#: Файл настроек: там же лежит и последняя калибровка (она дорогая).
SETTINGS_FILE = os.getenv(
    "LIQSCOPE_LEVELS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "liq_levels.json"))

DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "window_hours": 720,          # окно разбора OI: месяц, как остальная история
    "step_rel": 0.001,            # шаг сетки уровней: 0.1 % цены
    "spread_rel": 0.01,           # ширина колокола (σ): 1 % цены
    "lev_scale": 1.0,             # множитель гипотезы о плечах
    "spread_scale": 1.0,          # множитель ширины колокола
    "min_doi_rel": 0.0005,        # прирост OI меньше 0.05 % — это округление
    "min_liq_usd": 10_000.0,      # мелочь в факте не вычитаем: это шум
    "calib_days": 7.0,            # сколько дней факта берём для калибровки
    "calibrate": True,            # калибровать ли по факту
    "subtract_executed": True,    # вычитать ли уже отработавшее
    "max_levels": MAX_LEVELS,
    "magnet_min_share": MAGNET_MIN_SHARE,
    "lev_dist": [list(p) for p in DEFAULT_LEV_DIST],
}


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _fnum(v: Any, default: float = 0.0) -> float:
    f = _num(v)
    return default if f is None else f


# ---------------------------------------------------------------------------
#  Чистая часть: цена ликвидации, сетка и колокол
# ---------------------------------------------------------------------------

def normalize_dist(dist: Any) -> List[Tuple[float, float]]:
    """Распределение плеч: только разумные пары, доли приведены к единице.

    Пустое или мусорное распределение заменяется умолчанием: без него модель
    молча выдала бы «нет уровней», а это неправда о рынке. Понимаем и список
    пар, и словарь ``{"20": 1, "50": 3}`` — так удобнее писать настройки.
    """
    rows: List[Tuple[float, float]] = []
    items: Iterable[Any]
    if isinstance(dist, dict):
        items = list(dist.items())
    else:
        items = list(dist or [])
    for item in items:
        try:
            lev, share = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if lev <= 1.0 or share <= 0.0:
            continue
        rows.append((round(lev, 1), share))
    if not rows:
        return [(lev, share) for lev, share in DEFAULT_LEV_DIST]
    total = sum(share for _lev, share in rows)
    return [(lev, share / total) for lev, share in rows]


def liq_price(entry: Any, is_long: bool, leverage: Any, mmr: Any = 0.0) -> Optional[float]:
    """Цена ликвидации позиции при изолированной марже.

    Лонг: ``entry × (1 − 1/L + MMR)``, шорт: ``entry × (1 + 1/L − MMR)``.
    Так считает биржа: позицию выносит, когда убыток съедает начальную маржу
    (``1/L``) за вычетом поддерживающей (``MMR``).
    """
    e, lev = _num(entry), _num(leverage)
    m = _num(mmr)
    if e is None or e <= 0:
        return None
    if lev is None or lev <= 1.0:
        return None
    if m is None or m < 0:
        m = 0.0
    if m >= 0.5:                      # «маржа» в 50 % — это уже не ступень
        return None
    price = e * (1.0 - 1.0 / lev + m) if is_long else e * (1.0 + 1.0 / lev - m)
    return price if price > 0 else None


def grid_step(price: Any, step_rel: Any) -> float:
    """Шаг ценовой сетки: доля цены (0.1 %), но не меньше знака цены."""
    p = _num(price)
    rel = max(_fnum(step_rel, 0.001), 1e-6)
    if p is None or p <= 0:
        return 0.0
    return p * rel


def level_index(price: Any, step: Any) -> Optional[int]:
    """Номер корзины цены на сетке.

    Округление вверх на половине (``floor(p/s + 0.5)``): корзины нумеруются
    целыми, поэтому ни одна цена не «перепрыгивает» границу из-за
    банковского округления float.
    """
    p, s = _num(price), _num(step)
    if p is None or s is None or s <= 0:
        return None
    return int(math.floor(p / s + 0.5))


def index_price(idx: Any, step: Any) -> float:
    """Цена корзины по её номеру (ключ лестницы — всегда цена)."""
    return round(_fnum(idx) * _fnum(step), 10)


def snap(price: Any, step: Any) -> Optional[float]:
    """Цену — на сетку уровней (шаг нулевой или мусорный: цена как есть)."""
    p = _num(price)
    if p is None or p <= 0:
        return None
    s = _num(step)
    if s is None or s <= 0:
        return p
    idx = level_index(p, s)
    if idx is None:
        return p
    return index_price(idx, s)


def spread_kernel(spread_rel: Any, step_rel: Any,
                  radius: int = KERNEL_RADIUS) -> List[Tuple[float, float]]:
    """Колокол размазывания: [(смещение долей цены, вес)], сумма весов = 1.

    Смещения кратны шагу сетки (``step_rel``), ширина — ``spread_rel``.
    Нулевая ширина означает «уровень как линия»: единственная точка с весом 1.
    """
    width = _fnum(spread_rel, 0.0)
    if width <= 0:
        return [(0.0, 1.0)]
    st = max(_fnum(step_rel, 0.001), 1e-9)
    out: List[Tuple[float, float]] = []
    total = 0.0
    for k in range(-int(radius), int(radius) + 1):
        x = (k * st) / width
        w = math.exp(-0.5 * x * x)
        out.append((round(k * st, 12), w))
        total += w
    if total <= 0:
        return [(0.0, 1.0)]
    return [(off, w / total) for off, w in out]


# ---------------------------------------------------------------------------
#  Сборка строк: «сколько, почём и чей прирост OI»
# ---------------------------------------------------------------------------

def build_rows(points: Sequence[Tuple[float, float]],
               price_lookup: Callable[[float], Optional[float]],
               side_at: Optional[Callable[[float], Tuple[float, str]]],
               mmr_at: Optional[Callable[[float], Optional[float]]],
               min_doi_rel: float = 0.0) -> List[dict]:
    """Приросты OI → строки позиций: масса, цена входа, сторона и выжившее.

    ``survive`` — какая часть набранного ещё жива к концу окна: если OI вырос
    вдвое и вернулся, старая порция наполовину закрыта, и её уровни слабее.
    Строка без цены входа не теряется: у неё ``entry=None`` и пометка
    ``missing``, чтобы покрытие честно сказало, сколько таких.
    """
    rows: List[dict] = []
    pts: List[Tuple[float, float]] = []
    for t, v in points or []:
        tv, ov = _num(t), _num(v)
        if tv is None or ov is None or ov <= 0:
            continue
        if pts and tv <= pts[-1][0]:
            if tv == pts[-1][0]:
                pts[-1] = (tv, ov)          # дубль времени — последнее слово
            continue
        pts.append((tv, ov))
    if len(pts) < 2:
        return rows
    last_oi = pts[-1][1]
    floor_rel = max(_fnum(min_doi_rel), 0.0)
    for i in range(1, len(pts)):
        ts, oi = pts[i]
        doi = oi - pts[i - 1][1]
        if doi <= 0:
            continue                     # падение OI — закрытия, не новые позиции
        if floor_rel and doi < floor_rel * oi:
            continue                     # прирост меньше порога — округление
        entry = None
        try:
            entry = price_lookup(ts) if callable(price_lookup) else None
        except Exception:                # noqa: BLE001
            entry = None
        share, src = 0.5, "none"
        if callable(side_at):
            try:
                got = side_at(ts)
                if got:
                    share, src = float(got[0]), str(got[1] or "none")
            except Exception:            # noqa: BLE001
                share, src = 0.5, "none"
        share = min(max(_fnum(share, 0.5), 0.0), 1.0)
        mmr = 0.0
        if callable(mmr_at):
            try:
                mmr = max(_fnum(mmr_at(ts)), 0.0)
            except Exception:            # noqa: BLE001
                mmr = 0.0
        survive = min(1.0, last_oi / oi) if oi > 0 else 1.0
        mass = doi * survive
        row = {"ts": ts, "entry": _num(entry) if entry else None,
               "usd": mass, "long_usd": mass * share, "short_usd": mass * (1 - share),
               "share": share, "side_src": src, "mmr": mmr, "survive": survive}
        if row["entry"] is None:
            row["missing"] = "entry"
        rows.append(row)
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _cell(ladder: Dict[float, dict], price: float) -> dict:
    cell = ladder.get(price)
    if cell is None:
        cell = {"usd": 0.0, "long_usd": 0.0, "short_usd": 0.0, "lev": {}}
        ladder[price] = cell
    return cell


def agg_step_rel(settings: Optional[Dict[str, Any]]) -> float:
    """Шаг корзины схлопывания: шаг сетки лестницы × ``AGG_BUCKET``."""
    return max(_fnum((settings or {}).get("step_rel"), 0.001), 1e-6) * AGG_BUCKET


def aggregate_rows(rows: Sequence[dict], price: Any,
                   step_rel: Any = None) -> List[dict]:
    """Сложить строки позиций с близкими ценами входа в одну.

    Лестнице важна масса на цене ликвидации, а не каждая точка OI: окно в 30
    суток по пятиминуткам даёт тысячи строк, но цены входа в них повторяются —
    соседние точки стоят почти на тех же уровнях. Строки с входом в пределах
    шага сетки (0.1 % цены) складываются в одну с суммарной массой и входом в
    центре тяжести масс; ключ включает MMR, потому что он сдвигает цену
    ликвидации независимо от входа.

    Точность не теряется по смыслу: квантование входа в один шаг сетки в
    ``KERNEL_RADIUS`` (4) раз уже колокола размазывания, а цена ликвидации
    линейна по входу, поэтому уход уровня ограничен тем же шагом сетки.
    Масса сохраняется точно — это проверяет тест.
    """
    p0 = _num(price)
    step = grid_step(p0, step_rel)
    if p0 is None or p0 <= 0 or step <= 0 or not rows:
        return list(rows or [])
    agg: Dict[Tuple[int, float], List[float]] = {}
    for row in rows:
        entry = _num(row.get("entry"))
        if not entry or entry <= 0:
            continue
        long_m = max(_fnum(row.get("long_usd")), 0.0)
        short_m = max(_fnum(row.get("short_usd")), 0.0)
        if long_m <= 0 and short_m <= 0:
            continue
        mmr = max(_fnum(row.get("mmr")), 0.0)
        key = (int(math.floor(entry / step + 0.5)), mmr)
        cur = agg.get(key)
        if cur is None:
            agg[key] = [entry * (long_m + short_m), long_m, short_m]
        else:
            cur[0] += entry * (long_m + short_m)
            cur[1] += long_m
            cur[2] += short_m
    out: List[dict] = []
    for (idx, mmr), (weighted, long_m, short_m) in agg.items():
        total = long_m + short_m
        entry = (weighted / total) if total > 0 else idx * step
        out.append({"entry": entry, "long_usd": long_m, "short_usd": short_m,
                    "mmr": mmr})
    return out


def build_ladder(rows: Sequence[dict], settings: Dict[str, Any],
                 price: Any) -> Dict[float, dict]:
    """Лестница уровней: масса позиций по цене ликвидации.

    Каждая строка раскладывается по гипотезе о плечах: для каждого плеча
    считается цена ликвидации лонга (ниже входа) и шорта (выше входа), а
    набранная масса делится между сторонами по перевесу. Вокруг цены уровня
    масса размазывается колоколом — получается полоса, а не игла.

    Ключ лестницы — цена, выровненная по сетке; ячейка знает общую массу,
    стороны и разбивку по плечам (``lev``) — из неё потом берётся «плечо ~Nx».
    """
    p0 = _num(price)
    step = grid_step(p0, (settings or {}).get("step_rel"))
    if p0 is None or p0 <= 0 or step <= 0:
        return {}
    step_rel = max(_fnum((settings or {}).get("step_rel"), 0.001), 1e-6)
    spread_rel = max(_fnum((settings or {}).get("spread_rel"), 0.01), 0.0) * \
        max(_fnum((settings or {}).get("spread_scale"), 1.0), 0.05)
    kernel = spread_kernel(spread_rel, step_rel)
    # Смещения колокола в шагах сетки не зависят ни от строки, ни от плеча:
    # считаем один раз, иначе round() звался на каждой итерации самого
    # внутреннего цикла (строк × плеч × сторон × отсчётов колокола).
    ksteps = [(int(round(off_rel / step_rel)), kw) for off_rel, kw in kernel]
    dist = normalize_dist((settings or {}).get("lev_dist"))
    lev_scale = max(_fnum((settings or {}).get("lev_scale"), 1.0), 0.05)
    floor = p0 * (1.0 - MAX_DISTANCE_REL)
    ceil = p0 * (1.0 + MAX_DISTANCE_REL)
    ladder: Dict[float, dict] = {}
    for row in rows or []:
        entry = _num(row.get("entry"))
        if not entry or entry <= 0:
            continue
        long_mass = max(_fnum(row.get("long_usd")), 0.0)
        short_mass = max(_fnum(row.get("short_usd")), 0.0)
        mmr = max(_fnum(row.get("mmr")), 0.0)
        for lev, weight in dist:
            eff = lev * lev_scale
            if eff <= 1.0:
                continue
            eff_key = round(eff, 1)
            for is_long, mass in ((True, long_mass * weight),
                                  (False, short_mass * weight)):
                if mass <= 0:
                    continue
                liq = liq_price(entry, is_long, eff, mmr)
                if liq is None or liq < floor or liq > ceil:
                    continue          # абсурдные цены — не наш рынок
                # было snap(liq, step) и level_index(base, step) ВНУТРИ цикла по
                # колоколу: номер корзины от смещения не зависит, а переход
                # «цена -> корзина -> цена -> корзина» даёт тот же номер
                idx = level_index(liq, step)
                if idx is None:
                    continue
                key = "long_usd" if is_long else "short_usd"
                for k, kweight in ksteps:
                    # смещение колокола — доля цены, поэтому в шагах сетки оно
                    # то же самое, что и в абсолютной цене
                    price_key = round((idx + k) * step, 10)
                    cell = ladder.get(price_key)
                    if cell is None:
                        cell = {"usd": 0.0, "long_usd": 0.0, "short_usd": 0.0,
                                "lev": {}}
                        ladder[price_key] = cell
                    part = mass * kweight
                    cell["usd"] += part
                    cell[key] += part
                    levs = cell["lev"]
                    levs[eff_key] = levs.get(eff_key, 0.0) + part
    return ladder


def dominant_lev(cell: Optional[dict]) -> Optional[float]:
    """Плечо ячейки: на какую ступень пришлась основная масса."""
    levs = (cell or {}).get("lev") or {}
    best, best_mass = None, 0.0
    for lev, mass in levs.items():
        if _fnum(mass) > best_mass:
            best, best_mass = _num(lev), _fnum(mass)
    return best


def _is_long_event(ev: dict) -> bool:
    """Кого вынесло событие: SELL ленты = ликвидация лонга, BUY = шорта."""
    side = str(ev.get("side") or "").upper()
    position = str(ev.get("position") or "").upper()
    if position in ("LONG", "SHORT"):
        return position == "LONG"
    return side == "SELL"


def _match_cell(ladder: Dict[float, dict], price: float, step: Optional[float],
                key: str, tolerance_rel: float,
                levels: Optional[Sequence[float]] = None) -> Optional[float]:
    """Ближайшая корзина к цене события — той же стороны и в пределах допуска.

    ``levels`` — цены лестницы по возрастанию: с ними поиск идёт бинарным
    спуском к точке вставки и коротким проходом в стороны, а не перебором всех
    ячеек. Перебор стоил «событий × ячеек»: 20000 событий и 170 уровней — 3.4 млн
    сравнений и 1.2 с на одну монету (замер 29.09.2026), при том что допуск
    2 % покрывает от силы пару десятков корзин.

    Первая же найденная по ходу корзина с массой — и есть ближайшая: стороны
    перебираются в порядке возрастания зазора. При равном зазоре слева и справа
    берётся нижняя цена (раньше решал порядок вставки в словарь — он не был
    детерминирован по смыслу, зато делал поиск линейным).
    """
    if not ladder:
        return None
    exact = snap(price, step) if step else price
    if exact is not None and exact in ladder and _fnum(ladder[exact].get(key)) > 0:
        return exact
    tol = abs(price) * max(_fnum(tolerance_rel, EXEC_TOLERANCE_REL), 0.0)
    keys = sorted(ladder) if levels is None else levels
    n = len(keys)
    i = bisect.bisect_left(keys, price)
    lo, hi = i - 1, i
    while lo >= 0 or hi < n:
        gap_lo = (price - keys[lo]) if lo >= 0 else None
        gap_hi = (keys[hi] - price) if hi < n else None
        if gap_hi is None or (gap_lo is not None and gap_lo <= gap_hi):
            gap, k, go_left = gap_lo, keys[lo], True
        else:
            gap, k, go_left = gap_hi, keys[hi], False
        if gap > tol:
            break           # дальше с обеих сторон только дальше — допуска нет
        if _fnum(ladder[k].get(key)) > 0:
            return k
        if go_left:
            lo -= 1
        else:
            hi += 1
    return None


def apply_executed(ladder: Dict[float, dict], events: Iterable[dict],
                   step: Any, tolerance_rel: float = EXEC_TOLERANCE_REL) -> dict:
    """Вычесть из лестницы то, что уже отработало.

    Факт ликвидации означает, что позиции на этом уровне больше нет: иначе
    картинка вечно показывала бы уже съеденные уровни. Вычитаем из ближайшей
    корзины той же стороны, не глубже нуля; опустевшие корзины уходят из
    лестницы совсем.

    Возвращает ``{"usd", "events", "skipped"}``: сколько вычли, сколько
    событий нашли свой уровень и сколько осталось без пары (уровень слишком
    далеко или сторона не та).
    """
    st = _num(step)
    # цены лестницы не меняются внутри цикла (пустые корзины удаляются после),
    # поэтому сортировка одна на все события, а не на каждое
    levels = sorted(ladder) if ladder else []
    taken, used, skipped = 0.0, 0, 0
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        price, usd = _num(ev.get("price")), _fnum(ev.get("usd"))
        if price is None or price <= 0 or usd <= 0:
            continue
        key = "long_usd" if _is_long_event(ev) else "short_usd"
        level = _match_cell(ladder, price, st, key, tolerance_rel, levels)
        if level is None:
            skipped += 1
            continue
        cell = ladder[level]
        free = _fnum(cell.get(key))
        if free <= 0:
            skipped += 1
            continue
        take = min(usd, free)
        before = _fnum(cell.get("usd"))
        cell[key] = free - take
        cell["usd"] = _fnum(cell.get("long_usd")) + _fnum(cell.get("short_usd"))
        # разбивка по плечам уменьшается вместе с массой — иначе «плечо ~Nx»
        # осталось бы от уже отработавшей части
        after = _fnum(cell.get("usd"))
        if before > 0 and after < before:
            k = after / before
            levs = cell.get("lev") or {}
            cell["lev"] = {lev: _fnum(m) * k for lev, m in levs.items()}
        taken += take
        used += 1
    for level in [p for p, c in ladder.items() if _fnum(c.get("usd")) <= 0]:
        ladder.pop(level, None)
    return {"usd": round(taken, 2), "events": used, "skipped": skipped}


# ---------------------------------------------------------------------------
#  Что показать: кумулятив, магниты, строки для панели
# ---------------------------------------------------------------------------

def _levels_sorted(ladder: Dict[float, dict]) -> List[float]:
    return sorted(ladder)


def cumulative(ladder: Dict[float, dict], price: Any) -> Tuple[List[dict], List[dict]]:
    """Кумулятив боли: сколько снесёт, если цена дойдёт до уровня.

    Вниз идёт масса лонгов (их ликвидации топят цену дальше), вверх — масса
    шортов. Считаем накопление по пути от текущей цены: у каждой точки видно,
    сколько всего вынесет по дороге к ней.
    """
    p = _num(price) or 0.0
    up: List[dict] = []
    down: List[dict] = []
    total = 0.0
    for level in sorted((x for x in ladder if x > p)):
        cell = ladder[level]
        total += _fnum(cell.get("short_usd"))
        up.append({"price": level, "usd": round(total, 2),
                   "cum_usd": round(total, 2),
                   "level_usd": round(_fnum(cell.get("usd")), 2),
                   "side": "short", "distance_pct": round((level / p - 1) * 100, 3)})
    total = 0.0
    # Вниз список идёт по возрастанию цены (на графике — снизу вверх), а
    # накопление — от дальнего края к текущей цене: у каждой точки видно,
    # сколько всего ликвидаций набралось от самого дальнего уровня до неё.
    for level in sorted(x for x in ladder if x < p):
        cell = ladder[level]
        total += _fnum(cell.get("long_usd"))
        down.append({"price": level, "usd": round(total, 2),
                     "cum_usd": round(total, 2),
                     "level_usd": round(_fnum(cell.get("usd")), 2),
                     "side": "long", "distance_pct": round((level / p - 1) * 100, 3)})
    return up, down


def pick_magnets(ladder: Dict[float, dict], price: Any,
                 min_share: float = MAGNET_MIN_SHARE) -> Dict[str, Optional[dict]]:
    """Магниты: ближний значимый уровень сверху и снизу плюс крупнейшие.

    «Значимый» — не меньше ``min_share`` от массы самого крупного уровня
    лестницы, а не от всей массы: уровней сотни, масса по ним размазана, и
    доля «от всего рынка» не набирается почти никогда. ``top_up``/``top_down``
    — просто самые тяжёлые уровни сверху и снизу от цены.
    """
    p = _num(price)
    out: Dict[str, Optional[dict]] = {"up": None, "down": None,
                                      "top_up": None, "top_down": None}
    if p is None or not ladder:
        return out
    total = sum(_fnum(c.get("usd")) for c in ladder.values())
    top_mass = max((_fnum(c.get("usd")) for c in ladder.values()), default=0.0)
    thr = max(0.0, _fnum(min_share, MAGNET_MIN_SHARE)) * top_mass
    best_up = best_down = None
    for level in sorted(ladder):
        usd = _fnum(ladder[level].get("usd"))
        if level > p and (best_up is None or usd > best_up[1]):
            best_up = (level, usd)
        if level < p and (best_down is None or usd > best_down[1]):
            best_down = (level, usd)
    for item in (best_up, best_down):
        if item is None:
            continue
        level, usd = item
        key = "top_up" if level > p else "top_down"
        out[key] = {"price": level, "usd": usd}
    for level in sorted(x for x in ladder if x > p):
        if _fnum(ladder[level].get("usd")) >= thr:
            out["up"] = {"price": level, "usd": _fnum(ladder[level].get("usd"))}
            break
    for level in sorted((x for x in ladder if x < p), reverse=True):
        if _fnum(ladder[level].get("usd")) >= thr:
            out["down"] = {"price": level, "usd": _fnum(ladder[level].get("usd"))}
            break
    return _magnet_detail(ladder, p, out, total)


def _magnet_detail(ladder: Dict[float, dict], price: float,
                   out: Dict[str, Optional[dict]], total: float) -> Dict[str, Optional[dict]]:
    """Дополнить магниты стороной, плечом и расстоянием — для карточки и API."""
    for name, mag in list(out.items()):
        if not mag:
            continue
        level = mag["price"]
        cell = ladder[level]
        long_usd = _fnum(cell.get("long_usd"))
        short_usd = _fnum(cell.get("short_usd"))
        mag.update({
            "usd": round(_fnum(cell.get("usd")), 2),
            "long_usd": round(long_usd, 2),
            "short_usd": round(short_usd, 2),
            "side": "long" if long_usd >= short_usd else "short",
            "share": round(_fnum(cell.get("usd")) / total * 100, 2) if total else 0.0,
            "distance_pct": round((level / price - 1) * 100, 3) if price else 0.0,
            "lev": dominant_lev(cell),
        })
    return out


def magnet_rows(magnets: Dict[str, Optional[dict]], price: Any) -> List[dict]:
    """Магниты — плоским списком для алертов и слоя (без повторов по цене)."""
    out: List[dict] = []
    seen = set()
    for name in ("up", "down", "top_up", "top_down"):
        mag = (magnets or {}).get(name)
        if not mag or mag.get("price") in seen:
            continue
        seen.add(mag.get("price"))
        row = dict(mag)
        row["kind"] = name
        out.append(row)
    return out


def ladder_rows(ladder: Dict[float, dict], price: Any, limit: int = MAX_LEVELS,
                min_usd: float = 0.0) -> List[dict]:
    """Строки лестницы для графика и панели: цена, масса, сторона, кумулятив.

    ``share`` и ``long_share``/``short_share`` — проценты от массы своей
    стороны, а не от всего рынка: так видно, какой из уровней лонгов главный,
    даже если шортов в лестнице больше. ``cum_usd`` — та же дорога от текущей
    цены, что и в :func:`cumulative`.
    """
    p = _num(price)
    if p is None or not ladder:
        return []
    total_long = sum(_fnum(c.get("long_usd")) for c in ladder.values())
    total_short = sum(_fnum(c.get("short_usd")) for c in ladder.values())
    total = total_long + total_short
    # кумулятив в строках считается от текущей цены наружу: сколько снесёт
    # по дороге именно к этому уровню (сумма всех уровней между ними)
    cum: Dict[float, float] = {}
    total = 0.0
    for level in sorted(x for x in ladder if x > p):
        total += _fnum(ladder[level].get("short_usd"))
        cum[level] = total
    total = 0.0
    for level in sorted((x for x in ladder if x < p), reverse=True):
        total += _fnum(ladder[level].get("long_usd"))
        cum[level] = total
    rows: List[dict] = []
    for level in sorted(ladder):
        cell = ladder[level]
        usd = _fnum(cell.get("usd"))
        if usd < _fnum(min_usd):
            continue
        long_usd, short_usd = _fnum(cell.get("long_usd")), _fnum(cell.get("short_usd"))
        if long_usd > 0 and short_usd > 0:
            side = "both"
        elif long_usd > 0:
            side = "long"
        else:
            side = "short"
        rows.append({
            "price": level,
            "usd": round(usd, 2),
            "long_usd": round(long_usd, 2),
            "short_usd": round(short_usd, 2),
            "side": side,
            "share": round(usd / total * 100, 2) if total else 0.0,
            "long_share": round(long_usd / total_long * 100, 2) if total_long else 0.0,
            "short_share": round(short_usd / total_short * 100, 2) if total_short else 0.0,
            "cum_usd": round(cum.get(level, 0.0), 2),
            "distance_pct": round((level / p - 1) * 100, 3) if p else 0.0,
            "lev": dominant_lev(cell),
        })
    if limit and len(rows) > int(limit):
        # оставляем самые тяжёлые уровни, но возвращаем их по порядку цен
        keep = sorted(rows, key=lambda r: r["usd"], reverse=True)[:int(limit)]
        keep.sort(key=lambda r: r["price"])
        rows = keep
    return rows


def filter_ladder(ladder: Dict[float, dict], min_usd: float = 0.0,
                  side: str = "") -> Dict[float, dict]:
    """Отсечка лестницы для показа: минимальная масса и сторона."""
    want = str(side or "").lower().strip()
    out: Dict[float, dict] = {}
    for level, cell in ladder.items():
        if _fnum(cell.get("usd")) < _fnum(min_usd):
            continue
        if want in ("long", "down") and _fnum(cell.get("long_usd")) <= 0:
            continue
        if want in ("short", "up") and _fnum(cell.get("short_usd")) <= 0:
            continue
        out[level] = cell
    return out


# ---------------------------------------------------------------------------
#  Калибровка по факту
# ---------------------------------------------------------------------------

def actual_histogram(events: Iterable[dict], price: Any, step: Any) -> Dict[float, dict]:
    """Факт ликвидаций → гистограмма по той же сетке, что и модель."""
    st = _num(step)
    out: Dict[float, dict] = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        p, usd = _num(ev.get("price")), _fnum(ev.get("usd"))
        if p is None or p <= 0 or usd <= 0:
            continue
        key = snap(p, st)
        if key is None:
            continue
        cell = out.setdefault(key, {"usd": 0.0, "long_usd": 0.0, "short_usd": 0.0})
        cell["usd"] += usd
        cell["long_usd" if _is_long_event(ev) else "short_usd"] += usd
    return out


def _hist_usd(row: Any) -> float:
    if isinstance(row, dict):
        return _fnum(row.get("usd"))
    return _fnum(row)


def overlap_score(model: Any, actual: Any, step_rel: float = CALIB_COARSE_REL) -> float:
    """Насколько форма модели совпала с фактом (0…1), без учёта объёма.

    Сравниваем нормированные распределения масс: «мало, но туда же» — это
    попадание. Обе гистограммы огрубляются до ~процента цены: у модели уровень
    размазан колоколом, у факта он точкой, и на мелкой сетке верная модель
    проигрывала бы собственному разбросу.
    """
    m = {float(k): _hist_usd(v) for k, v in (model or {}).items() if _hist_usd(v) > 0}
    a = {float(k): _hist_usd(v) for k, v in (actual or {}).items() if _hist_usd(v) > 0}
    if not m or not a:
        return 0.0
    mass = sum(m.values()) + sum(a.values())
    anchor = (sum(p * v for p, v in m.items()) + sum(p * v for p, v in a.items())) / mass
    rel = max(_fnum(step_rel, CALIB_COARSE_REL), 1e-4)
    cell = max(anchor * rel, 1e-9)

    def bins(hist: Dict[float, float]) -> Dict[int, float]:
        out: Dict[int, float] = {}
        for price, usd in hist.items():
            idx = int(round(price / cell))
            out[idx] = out.get(idx, 0.0) + usd
        total = sum(out.values()) or 1.0
        return {k: v / total for k, v in out.items()}

    bm, ba = bins(m), bins(a)
    return round(sum(min(v, ba.get(k, 0.0)) for k, v in bm.items()), 6)


def base_mass_histogram(rows: Sequence[dict], settings: Dict[str, Any],
                        price: Any, lev_scale: Optional[float] = None
                        ) -> Tuple[Dict[int, float], float]:
    """Масса позиций по корзинам сетки ДО размазывания колоколом.

    Калибровке не нужны ячейки лестницы с разбивкой по сторонам и плечам:
    ``overlap_score`` смотрит только на массу по цене (``_hist_usd``). Поэтому
    здесь считается лишь «корзина -> доллары», без словарей ячеек, — а колокол
    применяется уже к гистограмме, и его можно менять, не пересчитывая строки.

    Возвращает ``(гистограмма, шаг_сетки)``; пустая гистограмма — считать нечего.
    """
    p0 = _num(price)
    settings = settings or {}
    step = grid_step(p0, settings.get("step_rel"))
    if p0 is None or p0 <= 0 or step <= 0:
        return {}, 0.0
    dist = normalize_dist(settings.get("lev_dist"))
    scale = max(_fnum(lev_scale if lev_scale is not None
                      else settings.get("lev_scale"), 1.0), 0.05)
    floor = p0 * (1.0 - MAX_DISTANCE_REL)
    ceil = p0 * (1.0 + MAX_DISTANCE_REL)
    hist: Dict[int, float] = {}
    get = hist.get
    for row in rows or []:
        entry = _num(row.get("entry"))
        if not entry or entry <= 0:
            continue
        long_mass = max(_fnum(row.get("long_usd")), 0.0)
        short_mass = max(_fnum(row.get("short_usd")), 0.0)
        if long_mass <= 0 and short_mass <= 0:
            continue
        mmr = max(_fnum(row.get("mmr")), 0.0)
        for lev, weight in dist:
            eff = lev * scale
            if eff <= 1.0:
                continue
            if long_mass > 0:
                liq = liq_price(entry, True, eff, mmr)
                if liq is not None and floor <= liq <= ceil:
                    idx = level_index(liq, step)
                    if idx is not None:
                        hist[idx] = get(idx, 0.0) + long_mass * weight
            if short_mass > 0:
                liq = liq_price(entry, False, eff, mmr)
                if liq is not None and floor <= liq <= ceil:
                    idx = level_index(liq, step)
                    if idx is not None:
                        hist[idx] = get(idx, 0.0) + short_mass * weight
    return hist, step


def spread_mass(hist: Dict[int, float], step: float,
                ksteps: Sequence[Tuple[int, float]]) -> Dict[float, float]:
    """Размазать массу гистограммы колоколом — то же, что делает build_ladder.

    Свёртка линейна, поэтому результат совпадает с лестницей, собранной по
    строкам, но стоимость зависит от числа ЗАНЯТЫХ корзин, а не от числа строк:
    на сетке калибровки это сотни ячеек против тысяч строк.
    """
    out: Dict[float, float] = {}
    get = out.get
    for idx, mass in (hist or {}).items():
        if mass <= 0:
            continue
        for k, w in ksteps:
            price_key = round((idx + k) * step, 10)
            out[price_key] = get(price_key, 0.0) + mass * w
    return out


def calibrate(rows: Sequence[dict], actual_events: Sequence[dict], price: Any,
              settings: Optional[Dict[str, Any]] = None) -> dict:
    """Подобрать масштаб плеч и ширину колокола по фактическим ликвидациям.

    Факт — единственная обратная связь: если рынок выносило на других
    ступенях, значит, гипотеза о плечах сдвинута. Перебираем сетку масштабов и
    оставляем лучший по совпадению формы; при малом факте честно отказываемся.

    Возвращает ``{"applied", "lev_scale", "spread_scale", "score", "events",
    "reason", "ts"}`` — этот же словарь уходит в API, чтобы пользователь видел,
    насколько модели можно верить.
    """
    settings = dict(settings or DEFAULT_SETTINGS)
    events = [ev for ev in (actual_events or [])
              if _num(ev.get("price")) and _fnum(ev.get("usd")) > 0]
    if len(events) < MIN_CALIB_EVENTS:
        return {"applied": False, "reason": f"мало факта: {len(events)} "
                f"из {MIN_CALIB_EVENTS}", "events": len(events), "ts": time.time()}
    step = grid_step(price, settings.get("step_rel"))
    actual = actual_histogram(events, price, step)
    if not actual:
        return {"applied": False, "reason": "факт без цен",
                "events": len(events), "ts": time.time()}
    # Сетка перебора стоила 25 полных лестниц: 98.9 с на 8639 строках и
    # 20000 событиях (замер 29.09.2026) — столько длится один проход фона
    # уровней, и всё это время единственный воркер обслуживает запросы вместе
    # с расчётом. Теперь строки обходятся один раз на масштаб плеч (5 вместо
    # 25), а ширина колокола применяется уже к готовой гистограмме масс:
    # свёртка линейна, поэтому оценка совпадает с прежней.
    step_rel = max(_fnum(settings.get("step_rel"), 0.001), 1e-6)
    spread_rel = max(_fnum(settings.get("spread_rel"), 0.01), 0.0)
    best: Optional[dict] = None
    for lev_scale in CALIB_LEV:
        hist, step = base_mass_histogram(rows, settings, price, lev_scale)
        if not hist:
            continue
        for spread_scale in CALIB_SPREAD:
            kernel = spread_kernel(
                spread_rel * max(_fnum(spread_scale, 1.0), 0.05), step_rel)
            ksteps = [(int(round(off / step_rel)), w) for off, w in kernel]
            score = overlap_score(spread_mass(hist, step, ksteps), actual,
                                  CALIB_COARSE_REL)
            if best is None or score > best["score"]:
                best = {"lev_scale": lev_scale, "spread_scale": spread_scale,
                        "score": score}
    if best is None:
        return {"applied": False, "reason": "нет массы в окне цены",
                "events": len(events), "ts": time.time()}
    if best["score"] < MIN_CALIB_SCORE:
        return {"applied": False, "reason": f"совпадение слабое: {best['score']:.2f}",
                "score": best["score"], "events": len(events), "ts": time.time()}
    return {"applied": True, "lev_scale": best["lev_scale"],
            "spread_scale": best["spread_scale"], "score": best["score"],
            "events": len(events), "reason": "", "ts": time.time()}


# ---------------------------------------------------------------------------
#  Движок
# ---------------------------------------------------------------------------

def _current_price(rows: Sequence[dict], points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Текущая цена: последняя известная цена входа, иначе — не нужна."""
    for row in reversed(list(rows or [])):
        price = _num(row.get("entry"))
        if price:
            return price
    return None


class LevelsEngine:
    """Расчёт уровней ликвидаций по монете: чистое чтение уже собранных рядов.

    Все внешние источники приходят извне (``oi``, ``profile``, ``side``,
    ``risk``, ``hist``, ``klines``): движок сам в сеть не ходит, поэтому его
    можно звать прямо из запроса, а прогревать данные — фоновой задачей.
    """

    def __init__(self, *, oi: Any = None, profile: Any = None, side: Any = None,
                 risk: Any = None, hist: Any = None, klines: Any = None,
                 path: Optional[str] = None, enabled: bool = True):
        self.oi = oi
        self.profile = profile
        self.side = side
        self.risk = risk
        self.hist = hist
        self.klines = klines
        self.path = path if path is not None else SETTINGS_FILE
        self._settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._settings["enabled"] = bool(enabled)
        self._calib: Dict[str, dict] = {}
        # токены бюджета пересчётов калибровки (см. CALIB_BUDGET)
        self._calib_tokens = float(CALIB_BUDGET)
        self._calib_tokens_at = time.time()
        self._calib_deferred = 0
        # сколько строк вошло в расчёт и сколько осталось после схлопывания
        self._rows_in = 0
        self._rows_agg = 0
        self._cache: Dict[tuple, Tuple[float, dict]] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        # события истории для калибровки/вычитания исполненного: окно
        # «calib_days» перечитывалось с диска на каждом проходе фона (каждые
        # LEVELS_SNAP_SEC по каждой монете батча), а разбор дневных шардов —
        # это построчный json.loads десятков мегабайт
        self._events_cache: Dict[str, Tuple[float, float, List[dict]]] = {}
        self._events_cache_events = 0
        # попадания/промахи окна калибровки: по ним видно, действительно ли
        # кэш перестал перечитывать шарды на каждом проходе фона
        self._events_cache_hits = 0
        self._events_cache_misses = 0
        self._candles: Dict[str, Tuple[float, List[Tuple[float, float]]]] = {}
        self._lock = threading.RLock()
        self.builds = 0
        self.saved_at = 0.0
        self.load()

    # ----- настройки -------------------------------------------------------
    def load(self) -> bool:
        """Настройки и калибровка с диска: переживают рестарт сервера."""
        if not self.path or not os.path.exists(self.path):
            return False
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:              # noqa: BLE001
            log.warning("настройки уровней не прочитались: %s", e)
            return False
        if isinstance(data, dict):
            self._settings.update(self._clean(data.get("settings") or {}))
            calib = data.get("calibration")
            if isinstance(calib, dict):
                self._calib = {str(k): v for k, v in calib.items()
                               if isinstance(v, dict)}
            self.saved_at = _fnum(data.get("saved_at"))
        return True

    def save(self, force: bool = False) -> bool:
        """Записать настройки и калибровку (не чаще раза в минуту, кроме force)."""
        if not self.path or (not force and time.time() - self.saved_at < 60.0):
            return False
        data = {"settings": dict(self._settings), "calibration": self._calib,
                "saved_at": time.time()}
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:              # noqa: BLE001
            log.warning("настройки уровней не сохранились: %s", e)
            return False
        self.saved_at = time.time()
        return True

    def _clean(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Оставить только известные ключи и привести их к типу."""
        out: Dict[str, Any] = {}
        for key, value in (patch or {}).items():
            if key not in DEFAULT_SETTINGS:
                continue
            base = DEFAULT_SETTINGS[key]
            if key == "lev_dist":
                dist = normalize_dist(value)
                out[key] = [[lev, round(share, 6)] for lev, share in dist]
            elif isinstance(base, bool):
                out[key] = bool(value)
            elif key == "max_levels":
                out[key] = int(max(20, min(int(_fnum(value, base)), 2000)))
            elif key == "window_hours":
                out[key] = float(max(1.0, min(_fnum(value, base), 24 * 60.0)))
            elif key == "calib_days":
                out[key] = float(max(0.5, min(_fnum(value, base), 60.0)))
            else:
                out[key] = _fnum(value, _fnum(base))
        return out

    def update_settings(self, patch: Optional[Dict[str, Any]] = None) -> dict:
        """Правка настроек (админка) с записью на диск."""
        with self._lock:
            self._settings.update(self._clean(patch or {}))
            self._cache.clear()
            saved = dict(self._settings)
        self.save(force=True)
        return saved

    def settings(self, symbol: Optional[str] = None) -> dict:
        """Настройки с учётом калибровки монеты (она сильнее умолчаний)."""
        out = dict(self._settings)
        calib = self._calib.get(str(symbol or "").upper()) if symbol else None
        if calib and calib.get("applied"):
            out["lev_scale"] = _fnum(calib.get("lev_scale"), out["lev_scale"])
            out["spread_scale"] = _fnum(calib.get("spread_scale"), out["spread_scale"])
        return out

    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("enabled", True))

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "settings": dict(self._settings),
                "calibration": {sym: dict(row) for sym, row in self._calib.items()},
                "builds": self.builds,
                "cache": len(self._cache),
                "path": self.path,
                "sources": {"oi": self.oi is not None, "profile": self.profile is not None,
                            "side": self.side is not None, "risk": self.risk is not None,
                            "hist": self.hist is not None, "klines": self.klines is not None},
            }

    # ----- данные ----------------------------------------------------------
    def _oi_points(self, symbol: str, since: float) -> List[Tuple[float, float]]:
        oi = self.oi
        if oi is None:
            return []
        pts: Any = None
        fn = getattr(oi, "points", None)
        if callable(fn):
            try:
                pts = fn(symbol, since)
            except TypeError:
                pts = fn(symbol)
            except Exception as e:          # noqa: BLE001
                log.debug("ряд OI %s: %s", symbol, e)
                pts = None
        if pts is None:
            series = getattr(oi, "series", None)
            if callable(series):
                try:
                    rows = series(symbol) or {}
                    pts = [(t, v) for t, v in rows.items() if float(t) >= since]
                except Exception as e:      # noqa: BLE001
                    log.debug("серия OI %s: %s", symbol, e)
                    pts = []
        return list(pts or [])

    def _venue_weights(self, symbol: str) -> Dict[str, float]:
        """Доли бирж в OI монеты: по ним взвешивается средняя маржа."""
        oi = self.oi
        if oi is None:
            return {}
        snap = None
        fn = getattr(oi, "snapshot", None)
        if callable(fn):
            try:
                snap = fn(symbol) or {}
            except Exception as e:          # noqa: BLE001
                log.debug("снимок OI %s: %s", symbol, e)
        legs = (snap or {}).get("per_exchange")
        if isinstance(legs, dict) and legs:
            values = {str(k): max(_fnum(v.get("usd") if isinstance(v, dict) else v), 0.0)
                      for k, v in legs.items()}
            total = sum(values.values())
            if total > 0:
                return {k: v / total for k, v in values.items() if v > 0}
        return {}

    def _mmr(self, symbol: str, weights: Dict[str, float]) -> Tuple[Callable[[float], float], bool]:
        risk = self.risk
        if risk is None:
            return (lambda ts: 0.0), False
        rate, estimated = 0.0, True
        try:
            if hasattr(risk, "mmr_or_default"):
                try:
                    rate, estimated = risk.mmr_or_default(symbol, weights=weights)
                except TypeError:
                    rate, estimated = risk.mmr_or_default(symbol)
        except Exception as e:              # noqa: BLE001
            log.debug("маржа %s: %s", symbol, e)
        rate = max(_fnum(rate), 0.0)
        return (lambda ts: rate), bool(estimated)

    def _side_fn(self, symbol: str) -> Callable[[float], Tuple[float, str]]:
        side, profile = self.side, self.profile

        def share_at(ts: float) -> Tuple[float, str]:
            tick = None
            if profile is not None:
                try:
                    tick = profile.side_ratio(symbol, ts, ts + BUCKET)
                except Exception:           # noqa: BLE001
                    tick = None
            if side is None:
                if tick is None:
                    return 0.5, "none"
                return float(tick), "ticks"
            try:
                share, src = side.side_at(symbol, ts, tick_share=tick)
                return float(share), str(src or "none")
            except Exception as e:          # noqa: BLE001
                log.debug("перевес %s: %s", symbol, e)
            if tick is None:
                return 0.5, "none"
            return float(tick), "ticks"

        return share_at

    async def _candles_for(self, symbol: str, session: Any) -> List[Tuple[float, float]]:
        """Свечи как запасная цена входа: 15 минут свежо, 4 часа — глубоко."""
        fetch = self.klines
        if not callable(fetch):
            return []
        cached = self._candles.get(symbol)
        if cached and time.time() - cached[0] < PRICE_TTL:
            return cached[1]
        rows: List[Tuple[float, float]] = []
        for tf_min, limit in ((15, 240), (240, 300)):
            try:
                if session is not None:
                    data = await fetch(symbol, tf_min, limit, session)
                else:
                    data = await fetch(symbol, tf_min, limit)
            except TypeError:
                try:
                    data = await fetch(symbol, tf_min, limit)
                except Exception as e:      # noqa: BLE001
                    log.debug("свечи %s/%s: %s", symbol, tf_min, e)
                    continue
            except Exception as e:          # noqa: BLE001
                log.debug("свечи %s/%s: %s", symbol, tf_min, e)
                continue
            for row in data or []:
                ts, close = _num(row.get("time")), _num(row.get("close"))
                if ts and close:
                    rows.append((float(ts), close))
        rows.sort()
        self._candles[symbol] = (time.time(), rows)
        return rows

    def _price_lookup(self, symbol: str, candles: Sequence[Tuple[float, float]]
                      ) -> Callable[[float], Optional[float]]:
        """Цена входа на момент: VWAP слота профиля, иначе ближайшая свеча слева."""
        profile = self.profile
        times = [t for t, _ in candles]
        closes = [c for _, c in candles]

        def lookup(ts: float) -> Optional[float]:
            if profile is not None:
                try:
                    vwap = profile.bucket_vwap(symbol, ts)
                except Exception:           # noqa: BLE001
                    vwap = None
                if vwap:
                    return float(vwap)
            if not times:
                return None
            i = bisect.bisect_right(times, float(ts)) - 1
            if i < 0:
                return None
            return closes[i]

        return lookup

    # Разбор дневных шардов истории стоит сотен миллисекунд на монету, поэтому
    # он не должен идти в event loop (замер на бою 29.09.2026: пауза 4480 мс со
    # стеком history.py:330:iter_events <- liq_levels.py:_events <- payload <-
    # server.py:liq_levels_task, а снаружи p95 336 мс при серверных 5.5 мс).
    # Монета из круга алертов возвращается в батч раз в (число_монет /
    # LEVELS_BATCH) проходов: при 17 монетах и пачке 4 — примерно 140 с, при 40
    # — около 330 с. Прежние 60 с означали, что кэш окна калибровки
    # промахивался ВСЕГДА и каждый проход перечитывал с диска до 336 МБ шардов
    # (7 дней по 48 МБ): на бою 29.09.2026 это держало payload на ~1000 мс даже
    # после того, как лестница подешевела в 14 раз.
    #
    # Замер серии из 12 проходов (17 монет, батч 4, период 33 с): при 60 с —
    # 42 чтения диска на 48 расчётов окна (6 попаданий), при 300 с — 25, при
    # 600 с — 17, то есть по одному чтению на монету; 900 с даёт те же 17,
    # поэтому расти дальше некуда. Трафик на серию: 13.8 ГБ -> 5.6 ГБ.
    #
    # Плата за свежесть: уже отработавший уровень может остаться невычтенным до
    # 10 минут. Для семидневного окна калибровки это несущественно, а сама
    # лестница каждый раз строится по свежим OI и цене — у них свой кэш на 20 с.
    EVENTS_TTL_SEC = max(5.0, float(os.getenv("LIQSCOPE_LEVELS_EVENTS_TTL_SEC",
                                              "600") or 600))
    # Прежний потолок «64 записи» оказался бомбой: запись — это разобранный
    # список событий окна калибровки, а MAX_EVENTS = 20000 словарей по ~830
    # байт в куче. 64 × 20000 × 830 Б ≈ 1.06 ГБ — ровно пик RSS 1224 МБ,
    # который замер на бою 29.09.2026. На такой куче каждая сборка мусора
    # второго поколения останавливает единственный воркер на 1-2.3 с (замер
    # прогона по внешнему IP: 11 пауз, максимум 2322 мс), а трассировка
    # называет кадр, который в этот момент аллоцировал память, — gzip, а не
    # настоящую причину. Поэтому держим потолок и по числу записей, и по
    # суммарному числу событий: память важнее количества монет.
    EVENTS_CACHE_MAX = max(1, int(os.getenv("LIQSCOPE_LEVELS_EVENTS_CACHE_MAX",
                                            "24") or 24))
    EVENTS_CACHE_MAX_EVENTS = max(1000, int(os.getenv(
        "LIQSCOPE_LEVELS_EVENTS_CACHE_EVENTS", "200000") or 200000))

    async def _events_cached(self, symbol: str, since: float, until: float,
                             min_usd: float) -> List[dict]:
        """События истории: вне цикла и с коротким кэшем на окно калибровки.

        Новые ликвидации меняют семидневное окно несущественно, а перечитывать
        шарды каждые 20 с на каждую монету — это диск и CPU единственного
        воркера. TTL задаётся ``LIQSCOPE_LEVELS_EVENTS_TTL_SEC``.
        """
        sym = str(symbol or "").upper()
        hit = self._events_cache.get(sym)
        # Прежняя схема сравнивала бакет int(until // TTL), то есть попадание
        # случалось только внутри одного бакета. Монета возвращается в батч раз
        # в (число_монет / LEVELS_BATCH) проходов: при 17 монетах и пачке 4 это
        # ~140 с, при 40 — ~330 с, а бакет жил 60 с. Кэш окна калибровки не
        # срабатывал НИКОГДА, и каждый проход перечитывал с диска до 336 МБ
        # шардов (7 дней × 48 МБ) — на бою 29.09.2026 это держало payload на
        # ~1000 мс на монету даже после того, как лестница подешевела в 14 раз.
        # Теперь окно пригодно, пока его конец уехал не дальше TTL: при проходе
        # раз в 33 с это попадание каждый раз, а сильно другое окно (например,
        # другой запрос с явным window_hours) по-прежнему перечитывается.
        # Сверяем ОБА конца окна: `since` зависит от `window_hours`, поэтому
        # запрос с другим окном (например, REST с window_hours=24 против
        # семидневного окна калибровки фона) не должен получить чужой набор.
        # У проходов фона оба конца уезжают вместе, поэтому попадания не теряются.
        if hit is not None and \
                abs(float(until) - _fnum(hit[0])) < self.EVENTS_TTL_SEC and \
                abs(float(since) - _fnum(hit[1])) < self.EVENTS_TTL_SEC:
            self._events_cache_hits += 1
            return hit[2]
        self._events_cache_misses += 1
        try:
            events = await asyncio.to_thread(self._events, sym, since, until,
                                             min_usd)
        except Exception:                        # noqa: BLE001
            events = self._events(sym, since, until, min_usd)
        self._events_cache_put(sym, until, events, since)
        return events

    def _events_cache_put(self, sym: str, until: float,
                          events: List[dict],
                          since: Optional[float] = None) -> bool:
        """Положить окно в кэш, не превысив потолок по числу событий.

        Возвращает False, если окно не влезает само по себе — такую монету
        дешевле перечитывать с диска, чем держать в куче гигабайт: именно
        большая куча даёт секундные паузы сборки мусора на одном воркере.
        """
        n = len(events or ())
        if n > self.EVENTS_CACHE_MAX_EVENTS:
            return False
        old = self._events_cache.pop(sym, None)
        if old is not None:
            self._events_cache_events -= len(old[2] or ())
        # пока не влезает — выбрасываем самые крупные записи: они же и самые
        # дорогие для сборщика мусора
        while (self._events_cache_events + n > self.EVENTS_CACHE_MAX_EVENTS
               or len(self._events_cache) >= self.EVENTS_CACHE_MAX):
            if not self._events_cache:
                break
            big = max(self._events_cache, key=lambda k: len(self._events_cache[k][2] or ()))
            gone = self._events_cache.pop(big)
            self._events_cache_events -= len(gone[2] or ())
        self._events_cache[sym] = (float(until),
                                   float(since) if since is not None else 0.0,
                                   events)
        self._events_cache_events += n
        return True

    def cache_stats(self) -> Dict[str, int]:
        """Кэш готовых лестниц: попадания/промахи — видно, работает ли бакет цены."""
        with self._lock:
            return {"entries": len(self._cache), "hits": int(self._cache_hits),
                    "misses": int(self._cache_misses),
                    "ttl_sec": int(PAYLOAD_TTL),
                    "price_key_step": PRICE_KEY_STEP}

    def events_cache_stats(self) -> Dict[str, int]:
        """Сколько событий и записей держит кэш — видно в /api/health."""
        return {"entries": len(self._events_cache),
                "events": int(self._events_cache_events),
                "max_entries": int(self.EVENTS_CACHE_MAX),
                "max_events": int(self.EVENTS_CACHE_MAX_EVENTS),
                "hits": int(self._events_cache_hits),
                "misses": int(self._events_cache_misses),
                "ttl_sec": float(self.EVENTS_TTL_SEC)}

    @staticmethod
    def _slim_event(ev: dict) -> dict:
        """Событие истории без полей, которые расчёту уровней не нужны.

        Калибровка (``actual_histogram``) и вычитание отработавшего
        (``apply_executed``) читают ровно три вещи: цену, объём и сторону через
        ``_is_long_event``. Полное событие с id, биржей, монетой, количеством и
        отметкой времени весит ~830 Б — втрое больше урезанного, и именно этот
        вес не давал держать в кэше окна всех монет сразу: потолок
        100000 событий — это 5 монет по 20000, а в батче их десятки, поэтому
        кэш промахивался и каждый проход перечитывал шарды с диска.
        """
        out = {"price": ev.get("price"), "usd": ev.get("usd")}
        side = ev.get("side")
        if side is not None:
            out["side"] = side
        position = ev.get("position")
        if position is not None:
            out["position"] = position
        return out

    def _events(self, symbol: str, since: float, until: float,
                min_usd: float) -> List[dict]:
        hist = self.hist
        if hist is None:
            return []
        fn = getattr(hist, "iter_events", None)
        if not callable(fn):
            return []
        out: List[dict] = []
        try:
            it = fn(since, until, symbol)
        except TypeError:
            try:
                it = fn(since, until, symbol=symbol)
            except Exception as e:          # noqa: BLE001
                log.debug("события %s: %s", symbol, e)
                return []
        except Exception as e:              # noqa: BLE001
            log.debug("события %s: %s", symbol, e)
            return []
        for ev in it or []:
            if not isinstance(ev, dict):
                continue
            if _fnum(ev.get("usd")) < _fnum(min_usd):
                continue
            out.append(self._slim_event(ev))
            if len(out) >= MAX_EVENTS:
                break
        return out

    # ----- калибровка ------------------------------------------------------
    def _calib_budget_take(self) -> bool:
        """Есть ли токен на очередной пересчёт калибровки (бакет с пополнением).

        Бюджет 0 означает «без ограничения»: тогда пересчёт происходит всякий
        раз, как калибровка устарела, — прежнее поведение.
        """
        if CALIB_BUDGET <= 0:
            return True
        now = time.time()
        elapsed = max(0.0, now - self._calib_tokens_at)
        self._calib_tokens = min(float(CALIB_BUDGET), self._calib_tokens
                                 + elapsed * (float(CALIB_BUDGET) / CALIB_BUDGET_SEC))
        self._calib_tokens_at = now
        if self._calib_tokens < 1.0:
            return False
        self._calib_tokens -= 1.0
        return True

    def agg_stats(self) -> Dict[str, Any]:
        """Схлопывание строк: во сколько раз меньше работы на лестницу."""
        with self._lock:
            src, dst = int(self._rows_in), int(self._rows_agg)
        return {"on": bool(AGG_ROWS), "bucket_of_step": round(AGG_BUCKET, 3),
                "rows_in": src, "rows_out": dst,
                "ratio": round(src / dst, 2) if dst else 0.0}

    def calib_stats(self) -> Dict[str, Any]:
        """Состояние бюджета пересчётов — видно в /api/health."""
        return {"budget": int(CALIB_BUDGET), "budget_sec": round(CALIB_BUDGET_SEC, 1),
                "tokens": round(float(self._calib_tokens), 2),
                "deferred": int(self._calib_deferred),
                "symbols": len(self._calib)}

    def _calibration(self, symbol: str, rows: Sequence[dict], events: Sequence[dict],
                     price: float, settings: Dict[str, Any],
                     recalibrate: Optional[bool]) -> dict:
        """Калибровка монеты: из кэша, если свежая, иначе пересчёт по факту."""
        sym = str(symbol or "").upper()
        cur = self._calib.get(sym)
        fresh = bool(cur) and time.time() - _fnum(cur.get("ts")) < CALIB_TTL_SEC
        if not settings.get("calibrate", True):
            return cur if cur else {"applied": False, "reason": "калибровка выключена"}
        if recalibrate is True or (not fresh and len(events) >= MIN_CALIB_EVENTS):
            if recalibrate is not True and not self._calib_budget_take():
                # пересчёт отложен: отдаём прежнее (или честный отказ), но НЕ
                # запоминаем и не обновляем ts — иначе монета стала бы «свежей»
                # и следующего шанса не получила бы
                self._calib_deferred += 1
                out = dict(cur) if cur else {"applied": False,
                                             "events": len(events)}
                out["deferred"] = True
                out["reason"] = "отложена: лимит пересчётов калибровки"
                return out
            got = calibrate(rows, events, price, settings)
            # даже отказ запоминаем: считать перебор на каждом запросе незачем
            self._calib[sym] = got
            return got
        return cur or {"applied": False, "reason": "ещё не считалась"}

    # ----- основной ответ --------------------------------------------------
    async def payload(self, symbol: str, session: Any = None, *,
                      now: Optional[float] = None, price: Optional[float] = None,
                      min_usd: float = 0.0, side: str = "",
                      window_hours: Optional[float] = None,
                      force: bool = False, recalibrate: Optional[bool] = None) -> dict:
        """Лестница уровней по монете — готовый ответ для API и слоя графика."""
        import os as _os, time as _time
        _perf = _os.getenv("LIQSCOPE_PERF_LOG", "").lower() in ("1","true","yes")
        _t0 = _time.monotonic() if _perf else 0.0
        sym = str(symbol or "").upper()
        ts = float(now if now is not None else time.time())
        settings = self.settings(sym)
        win = float(window_hours or settings.get("window_hours") or 720.0)
        key = (sym, round(float(min_usd or 0.0), 2), str(side or "").lower(),
               round(win, 3), price_key(price))
        with self._lock:
            cached = self._cache.get(key)
            if cached and not force and ts - cached[0] < PAYLOAD_TTL:
                self._cache_hits += 1
                return cached[1]
            self._cache_misses += 1
        if not settings.get("enabled", True):
            return self._empty(sym, ts, win, settings, "инструмент выключен")
        since = ts - win * HOUR
        points = self._oi_points(sym, since)
        # Быстрый путь: без OI точек считать нечего — не дергаем сеть вообще
        if not points:
            if _perf:
                import logging as _logging
                _logging.getLogger("liqscore.levels").info("[perf] levels %s no OI points -> empty in %.1fms", sym, (_time.monotonic()-_t0)*1000 if _t0 else 0)
            return self._empty(sym, ts, win, settings, "нет OI за окно")
        if session is not None:
            # сеть — только здесь и только по необходимости, после проверки OI
            if self.side is not None:
                try:
                    await self.side.ensure(session, sym)
                except Exception as e:      # noqa: BLE001
                    log.debug("перевес %s: %s", sym, e)
            if self.risk is not None:
                try:
                    await self.risk.ensure(session, sym)
                except Exception as e:      # noqa: BLE001
                    log.debug("риск-лимиты %s: %s", sym, e)
        # Если цена уже дана клиентом, свечи нужны только для VWAP fallback,
        # а не для определения текущей цены — можно пропустить сетевой запрос
        # если есть профиль объёма
        if price and self.profile is not None:
            try:
                # пробуем VWAP из профиля без свечей
                has_profile = bool(self.profile.coverage(sym))
            except Exception:
                has_profile = False
            if has_profile:
                candles = []
                lookup = self._price_lookup(sym, candles)
            else:
                candles = await self._candles_for(sym, session)
                lookup = self._price_lookup(sym, candles)
        else:
            candles = await self._candles_for(sym, session)
            lookup = self._price_lookup(sym, candles)
        weights = self._venue_weights(sym)
        mmr_fn, mmr_estimated = self._mmr(sym, weights)
        # Приросты OI → строки позиций: на каждую точку окна зовётся side_at
        # (ряды биржи), а точек за 30 дней — тысячи. Только в потоке, иначе
        # единственный воркер стоит сотни миллисекунд: замер на бою 29.09.2026
        # дал паузу 652 мс со стеком build_rows <- payload <- liq_levels_task.
        try:
            rows = await asyncio.to_thread(build_rows, points, lookup,
                                           self._side_fn(sym), mmr_fn,
                                           _fnum(settings.get("min_doi_rel")))
        except Exception:                        # noqa: BLE001
            rows = build_rows(points, lookup, self._side_fn(sym), mmr_fn,
                              _fnum(settings.get("min_doi_rel")))
        cur = _num(price) or _current_price(rows, points)
        if cur is None or cur <= 0:
            return self._empty(sym, ts, win, settings, "нет цены для расчёта")
        # чтение дневных шардов — только вне цикла (см. _events_cached)
        events = await self._events_cached(
            sym, max(since, ts - _fnum(settings.get("calib_days"), 7.0) * 24 * HOUR),
            ts, _fnum(settings.get("min_liq_usd")))
        # Строки с близкими входами складываются ДО калибровки и лестницы:
        # оба обходят их по несколько раз (сетка калибровки — 5 масштабов,
        # лестница — плечи × стороны × отсчёты колокола), а различных цен входа
        # в окне на порядок меньше, чем точек OI. На бою 29.09.2026 лестница
        # BTC_USDT стоила 966-1331 мс и держала проход фона на 2.8-3.9 с.
        if AGG_ROWS:
            work_rows = aggregate_rows(rows, cur, agg_step_rel(settings))
        else:
            work_rows = list(rows)
        with self._lock:
            self._rows_in += len(rows)
            self._rows_agg += len(work_rows)
        calib = self._calibration(sym, work_rows, events, cur, settings,
                                  recalibrate)
        eff = dict(settings)
        if calib.get("applied"):
            eff["lev_scale"] = _fnum(calib.get("lev_scale"), eff.get("lev_scale", 1.0))
            eff["spread_scale"] = _fnum(calib.get("spread_scale"),
                                        eff.get("spread_scale", 1.0))
        step = grid_step(cur, eff.get("step_rel"))
        # Тяжёлые CPU-расчёты — в threadpool, чтобы не блокировать event loop
        # (иначе WS и REST висят по 1-2 сек на каждом запросе уровней)
        try:
            ladder = await asyncio.to_thread(build_ladder, work_rows, eff, cur)
        except Exception:
            ladder = build_ladder(work_rows, eff, cur)
        applied = {"usd": 0.0, "events": 0, "skipped": 0}
        if eff.get("subtract_executed", True):
            try:
                applied = await asyncio.to_thread(apply_executed, ladder, events, step)
            except Exception:
                applied = apply_executed(ladder, events, step)
        with self._lock:
            self.builds += 1
        shown = filter_ladder(ladder, min_usd, side)
        levels = ladder_rows(shown, cur, limit=int(eff.get("max_levels") or MAX_LEVELS))
        magnets = pick_magnets(shown, cur, _fnum(eff.get("magnet_min_share"),
                                                 MAGNET_MIN_SHARE))
        up, down = cumulative(shown, cur)
        long_usd = sum(_fnum(c.get("long_usd")) for c in shown.values())
        short_usd = sum(_fnum(c.get("short_usd")) for c in shown.values())
        data = {
            "symbol": sym,
            "ts": ts,
            "enabled": True,
            "estimate": True,
            "price": round(cur, 8),
            "step": round(step, 10),
            "step_rel": _fnum(eff.get("step_rel")),
            "spread_rel": _fnum(eff.get("spread_rel")) * _fnum(eff.get("spread_scale"), 1.0),
            "window_hours": win,
            "levels": levels,
            "magnets": magnets,
            "magnets_list": magnet_rows(magnets, cur),
            "cumulative": {"up": up, "down": down},
            "applied": applied,
            "total_usd": round(long_usd + short_usd, 2),
            "long_usd": round(long_usd, 2),
            "short_usd": round(short_usd, 2),
            "totals": {"usd": round(long_usd + short_usd, 2),
                       "long_usd": round(long_usd, 2),
                       "short_usd": round(short_usd, 2)},
            "coverage": self._coverage(sym, points, rows, events, weights,
                                       mmr_estimated, lookup, candles),
            "calibration": _calib_out(calib),
            "settings": dict(eff),
        }
        data["notes"] = _notes(data, rows, events, mmr_estimated)
        with self._lock:
            self._cache[key] = (ts, data)
            if len(self._cache) > 64:
                for old in sorted(self._cache, key=lambda k: self._cache[k][0])[:16]:
                    self._cache.pop(old, None)
        return data

    def _empty(self, symbol: str, ts: float, win: float, settings: Dict[str, Any],
               reason: str) -> dict:
        """Честная пустота: инструмент выключен или данных нет."""
        return {"symbol": symbol, "ts": ts, "enabled": bool(settings.get("enabled", True)),
                "estimate": True, "price": None, "step": 0.0,
                "window_hours": win, "levels": [], "magnets": pick_magnets({}, None),
                "magnets_list": [], "cumulative": {"up": [], "down": []},
                "applied": {"usd": 0.0, "events": 0, "skipped": 0},
                "total_usd": 0.0, "long_usd": 0.0, "short_usd": 0.0,
                "totals": {"usd": 0.0, "long_usd": 0.0, "short_usd": 0.0},
                "coverage": {"rows": 0, "oi_points": 0, "events": 0},
                "calibration": {"applied": False, "reason": reason},
                "settings": dict(settings), "notes": [reason]}

    def _coverage(self, symbol: str, points: Sequence[Tuple[float, float]],
                  rows: Sequence[dict], events: Sequence[dict],
                  weights: Dict[str, float], mmr_estimated: bool,
                  lookup: Callable[[float], Optional[float]],
                  candles: Sequence[Tuple[float, float]]) -> dict:
        """Покрытие: из чего собрана картинка и чего в ней не хватает."""
        src: Dict[str, int] = {}
        missing = 0
        for row in rows:
            src[str(row.get("side_src") or "none")] = \
                src.get(str(row.get("side_src") or "none"), 0) + 1
            if row.get("entry") is None:
                missing += 1
        with_profile = 0
        if self.profile is not None:
            try:
                cov = self.profile.coverage(symbol, (points[0][0] if points else None))
                with_profile = int((cov or {}).get("buckets") or 0)
            except Exception:               # noqa: BLE001
                with_profile = 0
        return {
            "oi_points": len(points),
            "oi_from": (points[0][0] if points else None),
            "oi_to": (points[-1][0] if points else None),
            "rows": len(rows),
            "rows_missing_entry": missing,
            "side_sources": src,
            "events": len(events),
            "venues": {k: round(v, 4) for k, v in (weights or {}).items()},
            "mmr_estimated": bool(mmr_estimated),
            "profile_buckets": with_profile,
            "candles": len(candles),
            "price_source": "profile" if with_profile else ("klines" if candles else "none"),
        }

    # ----- фон -------------------------------------------------------------
    async def warm(self, session: Any, symbols: Iterable[str], limit: int = 4) -> int:
        """Прогреть данные монет (сеть трогаем только здесь)."""
        n = 0
        for sym in list(symbols)[:max(0, int(limit))]:
            if self.side is not None:
                try:
                    await self.side.ensure(session, sym)
                except Exception as e:      # noqa: BLE001
                    log.debug("перевес %s: %s", sym, e)
            if self.risk is not None:
                try:
                    await self.risk.ensure(session, sym)
                except Exception as e:      # noqa: BLE001
                    log.debug("риск-лимиты %s: %s", sym, e)
            n += 1
        return n

    def clear(self) -> None:
        """Сбросить кэш ответов (после правки настроек или данных)."""
        with self._lock:
            self._cache.clear()
            self._candles.clear()


def _calib_out(calib: Optional[dict]) -> dict:
    """Калибровка в ответ: только то, что видит клиент."""
    if not calib:
        return {"applied": False, "reason": "ещё не считалась"}
    return {k: calib.get(k) for k in ("applied", "score", "lev_scale", "spread_scale",
                                      "events", "ts", "reason")}


def _notes(data: dict, rows: Sequence[dict], events: Sequence[dict],
           mmr_estimated: bool) -> List[str]:
    """Честные оговорки к картинке: что в ней оценка, а что — факт."""
    notes = ["оценка, а не факт: позиции приватны, модель разлагает OI"]
    cov = data.get("coverage") or {}
    if not data.get("levels"):
        notes.append("уровней нет: по монете мало данных за окно")
    if cov.get("rows_missing_entry"):
        notes.append(f"часть приростов OI без цены входа: {cov['rows_missing_entry']} — "
                     "уровни по ним не считаны")
    if cov.get("oi_points", 0) < 12:
        notes.append("истории OI мало (меньше часа) — картинка ещё не набралась")
    if not events:
        notes.append("фактических ликвидаций за окно нет: уровни не выверены")
    if mmr_estimated:
        notes.append("поддерживающая маржа взята по умолчанию: биржа не отдала "
                     "ступени — точность ниже")
    srcs = cov.get("side_sources") or {}
    if srcs and not (srcs.get("taker") or srcs.get("lsr") or srcs.get("ticks")):
        notes.append("сторона позиций взята из фандинга (грубо) или неизвестна")
    calib = data.get("calibration") or {}
    if not calib.get("applied"):
        notes.append(f"калибровка по факту не применена: {calib.get('reason') or '—'}")
    return notes
