"""Месячный архив как источник цифр: лента, стенд, боксы статистики, CVD.

Сырые события и часовые свёртки уже лежат на диске (``HistoryStore``, не меньше
месяца). После перезагрузки память процесса пустая, и лента со стендом смотрели
только в неё — страница открывалась пустой. Здесь те же свёртки снова
становятся фактами.

Публикации (выпуск дня и сводка по часам) сюда НЕ относятся: на сайте видно
только то, что реально вышло. Свёртка месяца — это цифры за час или сутки, а
не пост: показывать её как «сводку» значит выдавать за публикацию то, чего в
канале не было.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from daily_digest import mood_of
from history import aggregate_hours
from hour_board import HOUR as BOARD_HOUR, apply_archive_span

Cell = Tuple[int, dict]


def _num(v, default: float = 0.0) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if n == n else default


def merge_events(memory: Sequence[dict], archived: Sequence[dict],
                 since: float, until: float) -> List[dict]:
    """События окна: архив плюс то, что ещё только в памяти (не сброшено на диск)."""
    out: Dict[str, dict] = {}
    order: List[str] = []

    def take(ev: dict) -> None:
        if not isinstance(ev, dict):
            return
        try:
            ts = float(ev.get("timestamp") or 0)
        except (TypeError, ValueError):
            return
        if ts < since or ts > until:
            return
        key = str(ev.get("id") or "") or f"{ts}:{ev.get('symbol')}:{ev.get('usd')}"
        if key in out:
            return
        out[key] = ev
        order.append(key)

    for ev in archived or []:
        take(ev)
    for ev in memory or []:
        take(ev)
    return [out[k] for k in order]


def flows_from_cells(cells: Iterable[Cell],
                     symbols: Optional[Iterable[str]] = None) -> Dict[str, dict]:
    """Часовые свёртки → потоки дайджеста {монета: {час: {vol, cvd, has_cvd}}}.

    CVD берётся из архива, а не из поля ``cvd`` свечи: после перезагрузки
    свечей ещё нет, а свёртка месяца уже есть.
    """
    wanted = {s for s in (symbols or []) if s}
    flows: Dict[str, dict] = {}
    for h, cell in cells or []:
        syms = (cell or {}).get("sym") or {}
        names = wanted or set(syms)
        for sym in names:
            val = syms.get(sym) or {}
            if not isinstance(val, dict):
                continue
            vol = _num(val.get("vol"))
            cvd = _num(val.get("cvd"))
            has = bool(cvd) or bool(val.get("cvd") is not None and (cell or {}).get("has_cvd"))
            if not vol and not cvd and not has:
                continue
            slot = flows.setdefault(sym, {}).setdefault(
                str(int(h)), {"vol": 0.0, "cvd": 0.0, "has_cvd": False})
            slot["vol"] += vol
            if cvd or (cell or {}).get("has_cvd"):
                slot["cvd"] += cvd
                slot["has_cvd"] = True
    return flows


def _coins_from_cells(cells: Iterable[Cell]) -> List[dict]:
    coins: Dict[str, dict] = {}
    for _h, cell in cells or []:
        for sym, val in ((cell or {}).get("sym") or {}).items():
            if not isinstance(val, dict):
                continue
            row = coins.setdefault(sym, {"symbol": sym, "usd": 0.0, "count": 0,
                                         "longs": 0.0, "shorts": 0.0})
            row["usd"] += _num(val.get("usd"))
            row["count"] += int(_num(val.get("n")))
            row["longs"] += _num(val.get("long"))
            row["shorts"] += _num(val.get("short"))
    return sorted(coins.values(), key=lambda r: r["usd"], reverse=True)


def _max_from_cells(cells: Iterable[Cell]) -> Optional[dict]:
    best = None
    for h, cell in cells or []:
        usd = _num((cell or {}).get("max_usd"))
        if usd <= 0:
            continue
        if best is None or usd > float(best.get("usd") or 0):
            best = {"symbol": (cell or {}).get("max_symbol") or "",
                    "usd": usd, "exchange": "", "side": "",
                    "timestamp": float(h)}
    return best


def facts_from_cells(cells: Iterable[Cell], window_sec: int = 86400) -> dict:
    """Факты окна в форме ``collect_day`` — из часовых свёрток, не из RAM."""
    cells = list(cells or [])
    agg = aggregate_hours(cells)
    coins = _coins_from_cells(cells)
    exchanges = [{"name": name, "usd": usd, "count": 0}
                 for name, usd in (agg.get("by_exchange") or {}).items()]
    longs = _num(agg.get("long_usd"))
    shorts = _num(agg.get("short_usd"))
    cvd = _num(agg.get("cvd"))
    vol = _num(agg.get("vol"))
    mood = mood_of(longs, shorts, cvd, vol, [], [])
    return {
        "window_h": int(round(window_sec / 3600)) or 24,
        "liq_total_usd": _num(agg.get("usd")),
        "liq_count": int(agg.get("count") or 0),
        "longs_usd": longs,
        "shorts_usd": shorts,
        "max": _max_from_cells(cells),
        "exchanges": exchanges[:8],
        "coins": [{"symbol": c["symbol"], "usd": c["usd"], "count": c["count"],
                   "longs": c["longs"], "shorts": c["shorts"]}
                  for c in coins[:8]],
        "oi_top": [],
        "prices": [],
        "mood": mood,
        "cvd_usd": round(cvd, 2) if cvd or vol else None,
        "vol_usd": round(vol, 2) if vol else None,
        "source": "archive",
    }


def overlay_archive_facts(facts: dict, cells: Iterable[Cell],
                          window_sec: int = 86400) -> dict:
    """Если свёртка полнее урезанного буфера событий — берём её суммы и CVD."""
    facts = dict(facts or {})
    arch = facts_from_cells(cells, window_sec=window_sec)
    if not arch.get("liq_count") and not arch.get("liq_total_usd") and not arch.get("vol_usd"):
        if arch.get("cvd_usd") and not facts.get("cvd_usd"):
            facts["cvd_usd"] = arch["cvd_usd"]
            facts["vol_usd"] = arch.get("vol_usd")
        return facts
    ram = _num(facts.get("liq_total_usd"))
    ram_n = int(facts.get("liq_count") or 0)
    # Не подменяем точное окно чуть более широкой часовой границей.
    # Архив нужен, когда буфер событий заметно беднее свёртки.
    richer = (_num(arch.get("liq_total_usd")) > ram * 1.02 + 1.0
              or int(arch.get("liq_count") or 0) > ram_n * 1.05 + 2)
    if richer:
        for key in ("liq_total_usd", "liq_count", "longs_usd", "shorts_usd",
                    "exchanges", "coins", "mood"):
            facts[key] = arch.get(key)
        mx = arch.get("max") or {}
        cur = facts.get("max") or {}
        if _num(mx.get("usd")) > _num(cur.get("usd")):
            facts["max"] = mx
        facts["totals_source"] = "archive"
    if arch.get("cvd_usd") is not None and not facts.get("cvd_usd"):
        facts["cvd_usd"] = arch["cvd_usd"]
    if arch.get("vol_usd") and not facts.get("vol_usd"):
        facts["vol_usd"] = arch["vol_usd"]
    return facts


def leaders_from_cells(cells: Iterable[Cell]) -> List[dict]:
    """Лидеры бокса статистики: та же форма, что у ``compute_stats``."""
    return _coins_from_cells(cells)[:12]


def cvd_from_cells(cells: Iterable[Cell], symbol: Optional[str] = None) -> Optional[float]:
    """Тейкер-дельта окна из свёрток. None — в архиве этого окна нет."""
    cells = list(cells or [])
    if not cells:
        return None
    agg = aggregate_hours(cells, symbol=symbol if symbol and symbol != "ALL" else None)
    if not agg.get("hours"):
        return None
    if not _num(agg.get("vol")) and not _num(agg.get("cvd")):
        return None
    return round(_num(agg.get("cvd")), 2)


def slot_flows_from_cells(cells: Iterable[Cell]) -> Dict[str, dict]:
    """Час архива → слот начала часа: {монета: {слот: {cvd, vol, has_cvd}}}.

    Почасовой блок поста складывает слоты часа. Масса часа на его первом слоте
    даёт тот же итог, что и четыре четверти, и не требует минуток в памяти.
    """
    out: Dict[str, dict] = {}
    for h, cell in cells or []:
        for sym, val in ((cell or {}).get("sym") or {}).items():
            if not isinstance(val, dict):
                continue
            vol = _num(val.get("vol"))
            cvd = _num(val.get("cvd"))
            has = bool((cell or {}).get("has_cvd") or cvd)
            if not vol and not has:
                continue
            slot = out.setdefault(str(sym), {}).setdefault(
                int(h), {"cvd": 0.0, "vol": 0.0, "has_cvd": False})
            slot["vol"] += vol
            if has:
                slot["cvd"] += cvd
                slot["has_cvd"] = True
    return out


def fill_boards_from_cells(board, slots, cells: Iterable[Cell]) -> int:
    """Дыры стенда закрыть свёртками. Доску надо расширить до месяца заранее:
    иначе ``apply`` сам обрежет старые часы.
    """
    n = 0
    for h, cell in cells or []:
        if apply_archive_span(board, int(h), cell, span=BOARD_HOUR):
            n += 1
        if slots is not None and apply_archive_span(slots, int(h), cell, span=BOARD_HOUR):
            n += 1
    return n


def window_facts_from_cells(cells: Iterable[Cell]) -> dict:
    """Итог окна поста: суммы и монеты, если стенд после рестарта ещё пуст."""
    facts = facts_from_cells(cells)
    return {
        "total_usd": facts.get("liq_total_usd") or 0.0,
        "longs_usd": facts.get("longs_usd") or 0.0,
        "shorts_usd": facts.get("shorts_usd") or 0.0,
        "count": facts.get("liq_count") or 0,
        "top_coins": facts.get("coins") or [],
        "biggest": facts.get("max"),
        "exchanges": {row["name"]: row["usd"] for row in (facts.get("exchanges") or [])},
    }
