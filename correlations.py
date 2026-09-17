"""Корреляции валют: кто ходит вместе с кем и куда.

Сервис отвечает на вопросы, которые не видно в ленте одной монеты:

    * у каких пар за выбранное окно совпадали ликвидации, объём, CVD и рост OI;
    * где выносило лонги, а где шорты;
    * где перекос CVD на сторону продавцов, а где покупателей;
    * где открытый интерес растёт, а где падает.

Считается по часовым свёрткам истории (history.HistoryStore) — они копятся
месяц, поэтому окно можно брать от часа до недели. OI приходит рядами снимков
(hour_board.OiHistory): внутри часа берём средний уровень, а для корреляций —
изменение за час.

Модуль не знает про сеть и время: данные подаёт сервер, а он же складывает
готовые структуры в кабинет и в бота.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HOUR = 3600

#: окна сервиса: ключ → минуты
WINDOWS: Tuple[Tuple[str, int], ...] = (
    ("1h", 60), ("4h", 240), ("12h", 720), ("24h", 1440),
    ("3d", 4320), ("7d", 10080),
)
#: метрики сервиса: ключ → (подпись, что считаем)
METRICS: Tuple[Tuple[str, str, str], ...] = (
    ("liq", "Ликвидации", "сумма ликвидаций за час, $"),
    ("vol", "Объём", "оборот монеты за час, $"),
    ("cvd", "CVD", "перекос покупок и продаж за час, $"),
    ("oi", "OI", "изменение открытого интереса за час, $"),
)
DEFAULT_WINDOW = "24h"
DEFAULT_METRIC = "liq"
#: сколько монет показываем в матрице — больше смысла не имеет, только шум
TOP_SYMBOLS = 10
#: минимум часовых точек с движением, иначе коэффициент не считаем
MIN_POINTS = 6


def window_minutes(key: str) -> int:
    for k, minutes in WINDOWS:
        if k == key:
            return minutes
    return dict(WINDOWS).get(DEFAULT_WINDOW, 1440)


def window_key(value) -> str:
    """Ключ окна из ключа или из минут: «4h» → «4h», 240 → «4h».

    Наружу (в кабинет и в бота) окна выходят ключами, а из старого конфига
    может прийти число минут — понимаем оба вида, иначе выбор пользователя
    молча сбрасывался бы на окно по умолчанию.
    """
    keys = {k for k, _ in WINDOWS}
    text = str(value or "").strip()
    if text in keys:
        return text
    try:
        m = int(float(text))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW
    return min(WINDOWS, key=lambda kv: abs(kv[1] - m))[0]


def window_label(key: str) -> str:
    minutes = window_minutes(key)
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 1440:
        return f"{minutes // 60} ч"
    days = minutes // 1440
    if minutes % 1440 == 0:
        return f"{days} дн"
    return f"{minutes // 60} ч"


def metric_title(key: str) -> str:
    for k, title, _ in METRICS:
        if k == key:
            return title
    return METRICS[0][1]


def metric_hint(key: str) -> str:
    for k, _, hint in METRICS:
        if k == key:
            return hint
    return METRICS[0][2]


def _num(v, default: float = 0.0) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if n == n else default


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Коэффициент Пирсона по двум рядам. None — если связь не определена.

    Ряд без движения (все значения одинаковые) корреляцией не описывается:
    честнее показать прочерк, чем выдать случайные 0 или 1.
    """
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    dx = [x - mx for x in xs[:n]]
    dy = [y - my for y in ys[:n]]
    sx = sum(v * v for v in dx)
    sy = sum(v * v for v in dy)
    if sx <= 0 or sy <= 0:
        return None
    cov = sum(dx[i] * dy[i] for i in range(n))
    r = cov / (sx ** 0.5 * sy ** 0.5)
    if r > 1:
        r = 1.0
    if r < -1:
        r = -1.0
    return round(r, 3)


def _slots(window_min: int, now: float) -> List[int]:
    """Часы окна по возрастанию: тихие часы тоже в списке — там будет ноль."""
    start = int((now - window_min * 60) // HOUR * HOUR)
    end = int(now // HOUR * HOUR)
    if end < start:
        start, end = end, start
    return list(range(start, end + HOUR, HOUR))


def _sym_value(by_hour: Dict[int, dict], slot: int, sym: str, metric: str) -> float:
    """Значение монеты за час из свёртки: ликвидации, объём или CVD."""
    val = (by_hour.get(slot, {}).get("sym") or {}).get(sym) or {}
    if metric == "liq":
        return _num(val.get("usd"))
    if metric == "vol":
        return _num(val.get("vol"))
    if metric == "cvd":
        return _num(val.get("cvd"))
    return 0.0
    return 0.0


def oi_hourly(series: Iterable[Tuple[float, float]]) -> List[Tuple[int, float]]:
    """Снимки OI (время, USD) → средний уровень по часам (по возрастанию)."""
    buckets: Dict[int, List[float]] = {}
    for ts, usd in series or ():
        u = _num(usd)
        t = _num(ts)
        if u <= 0 or t <= 0:
            continue
        buckets.setdefault(int(t // HOUR * HOUR), []).append(u)
    return [(h, sum(v) / len(v)) for h, v in sorted(buckets.items()) if v]


def _oi_deltas(series: Iterable[Tuple[float, float]], slots: Sequence[int]) -> List[float]:
    """Изменение OI по часам окна: час минус предыдущий час."""
    hourly = dict(oi_hourly(series))
    out: List[float] = []
    prev: Optional[float] = None
    for h in slots:
        cur = hourly.get(h)
        if cur is None:
            out.append(0.0)
            continue
        out.append(0.0 if prev is None else cur - prev)
        prev = cur
    return out


def _matrix(vectors: Dict[str, List[float]], min_points: int) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    syms = sorted(vectors)
    for a in syms:
        row: Dict[str, Optional[float]] = {}
        for b in syms:
            if a == b:
                row[b] = 1.0
                continue
            va, vb = vectors[a], vectors[b]
            active = sum(1 for i in range(len(va))
                         if va[i] or vb[i])
            r = pearson(va, vb) if active >= min_points else None
            row[b] = r
        out[a] = row
    return out


def _pairs(matrix: Dict[str, dict], limit: int = 6) -> List[dict]:
    seen = set()
    out: List[dict] = []
    for a, row in matrix.items():
        for b, r in row.items():
            if a >= b or r is None or abs(r) <= 0:
                continue
            key = (a, b)
            if key in seen:
                continue
            seen.add(key)
            out.append({"a": a, "b": b, "r": r})
    out.sort(key=lambda p: abs(p["r"]), reverse=True)
    return out[:limit]


def build(cells: Iterable[Tuple[int, dict]], oi_series: Optional[Dict[str, List]] = None,
          prices: Optional[Dict[str, float]] = None, window: str = DEFAULT_WINDOW,
          metric: str = DEFAULT_METRIC, now: Optional[float] = None,
          top: int = TOP_SYMBOLS, min_points: int = MIN_POINTS) -> dict:
    """Собрать картину корреляций по часовым свёрткам истории.

    cells — [(час, свёртка)] из HistoryStore.hours_range;
    oi_series — {монета: [(время, USD), ...]} снимков OI;
    prices — {монета: цена} для справки.
    """
    cells = list(cells)
    now = _num(now, 0.0) or 0.0
    if not now:
        now = max([h for h, _ in cells], default=0) + HOUR
    minutes = window_minutes(window)
    oi_series = oi_series or {}
    prices = prices or {}
    by_hour: Dict[int, dict] = {}
    for h, cell in cells:
        by_hour.setdefault(int(h), cell)
    slots = _slots(minutes, now)

    # монеты окна: по сумме ликвидаций и объёму — кто реально шумел
    score: Dict[str, float] = {}
    coins: Dict[str, dict] = {}
    for h, cell in cells:
        if not slots or h < slots[0]:
            continue
        for sym, val in (cell.get("sym") or {}).items():
            usd = _num(val.get("usd"))
            vol = _num(val.get("vol"))
            cvd = _num(val.get("cvd"))
            score[sym] = score.get(sym, 0.0) + usd + vol * 0.02
            c = coins.setdefault(sym, {"symbol": sym, "liq_usd": 0.0, "liq_long": 0.0,
                                       "liq_short": 0.0, "count": 0, "vol": 0.0,
                                       "cvd": 0.0})
            c["liq_usd"] += usd
            c["liq_long"] += _num(val.get("long"))
            c["liq_short"] += _num(val.get("short"))
            c["count"] += int(_num(val.get("n")))
            c["vol"] += vol
            c["cvd"] += cvd
    symbols = [s for s in sorted(score, key=lambda s: score[s], reverse=True)[:top]]

    # OI по монетам: уровень и изменение за окно
    for sym in list(coins) + symbols:
        series = oi_series.get(sym) or []
        hourly = oi_hourly(series)
        first = last = None
        for h, usd in hourly:
            if not slots or h < slots[0]:
                continue
            if first is None:
                first = usd
            last = usd
        c = coins.setdefault(sym, {"symbol": sym, "liq_usd": 0.0, "liq_long": 0.0,
                                   "liq_short": 0.0, "count": 0, "vol": 0.0,
                                   "cvd": 0.0})
        c["oi_usd"] = round(last, 2) if last else None
        c["oi_delta"] = round(last - first, 2) if (first and last) else None
        c["oi_pct"] = (round((last - first) / first * 100.0, 2)
                       if (first and last and first) else None)

    for sym in symbols:
        c = coins[sym]
        c["cvd_share"] = round(c["cvd"] / c["vol"] * 100.0, 1) if c["vol"] else None
        c["price"] = prices.get(sym)
        c["liq_share_long"] = (round(c["liq_long"] / c["liq_usd"] * 100.0, 1)
                               if c["liq_usd"] else None)

    # матрицы по всем метрикам — в кабинете можно переключать
    matrices: Dict[str, dict] = {}
    best_pairs: Dict[str, list] = {}
    for key, _title, _hint in METRICS:
        vectors: Dict[str, List[float]] = {}
        for sym in symbols:
            if key == "oi":
                vectors[sym] = _oi_deltas(oi_series.get(sym) or [], slots)
            else:
                vectors[sym] = [_sym_value(by_hour, slot, sym, key) for slot in slots]
        matrices[key] = _matrix(vectors, min_points) if len(symbols) >= 2 else {}
        best_pairs[key] = _pairs(matrices[key])

    flows = {
        "liquidated_long": [c["symbol"] for c in
                            sorted([coins[s] for s in symbols if s in coins],
                                   key=lambda c: c["liq_long"], reverse=True)
                            if c["liq_long"] > 0][:5],
        "liquidated_short": [c["symbol"] for c in
                             sorted([coins[s] for s in symbols if s in coins],
                                    key=lambda c: c["liq_short"], reverse=True)
                             if c["liq_short"] > 0][:5],
        "cvd_sellers": [c["symbol"] for c in
                        sorted([coins[s] for s in symbols if s in coins],
                               key=lambda c: c["cvd"]) if c["cvd"] < 0][:5],
        "cvd_buyers": [c["symbol"] for c in
                       sorted([coins[s] for s in symbols if s in coins],
                              key=lambda c: c["cvd"], reverse=True)
                       if c["cvd"] > 0][:5],
        "oi_up": [c["symbol"] for c in
                  sorted([coins[s] for s in symbols if s in coins],
                         key=lambda c: _num(c.get("oi_delta")), reverse=True)
                  if _num(c.get("oi_delta")) > 0][:5],
        "oi_down": [c["symbol"] for c in
                    sorted([coins[s] for s in symbols if s in coins],
                           key=lambda c: _num(c.get("oi_delta")))
                    if _num(c.get("oi_delta")) < 0][:5],
    }
    return {
        "window": window_key(minutes),
        "window_min": minutes,
        "window_label": window_label(window_key(minutes)),
        "metric": metric if metric in {k for k, _, _ in METRICS} else DEFAULT_METRIC,
        "since": slots[0] if slots else None,
        "until": now,
        "hours": len(slots),
        "min_points": min_points,
        "symbols": symbols,
        "coins": {s: coins[s] for s in symbols if s in coins},
        "matrices": matrices,
        "pairs": best_pairs,
        "flows": flows,
        "metrics": [{"key": k, "title": t, "hint": h} for k, t, h in METRICS],
        "windows": [{"key": k, "minutes": m, "label": window_label(k)}
                    for k, m in WINDOWS],
    }


def _fmt_usd(v) -> str:
    n = _num(v, 0.0)
    sign = "−" if n < 0 else ""
    n = abs(n)
    if n >= 1e9:
        return f"{sign}${n / 1e9:.2f} млрд"
    if n >= 1e6:
        return f"{sign}${n / 1e6:.1f} млн"
    if n >= 1e3:
        return f"{sign}${n / 1e3:.0f} тыс"
    return f"{sign}${n:.0f}"


def _coin_list(symbols: Sequence[str]) -> str:
    return ", ".join(s.replace("_USDT", "") for s in symbols) if symbols else "—"


def format_text(res: dict, lang: str = "ru", site: str = "") -> str:
    """Текст сводки для Telegram (и подпись на сайте)."""
    en = str(lang).lower().startswith("en")
    pairs = (res.get("pairs") or {}).get(res.get("metric") or DEFAULT_METRIC) or []
    pos = [p for p in pairs if p["r"] >= 0.6][:3]
    neg = [p for p in pairs if p["r"] <= -0.6][:2]
    flows = res.get("flows") or {}
    lines: List[str] = []
    if en:
        lines.append(f"<b>🔗 Coin correlations · {res.get('window_label')}</b>")
        lines.append(f"Window: {res.get('hours')} hourly points, coins: {len(res.get('symbols') or [])}")
    else:
        lines.append(f"<b>🔗 Корреляции валют · окно {res.get('window_label')}</b>")
        lines.append(f"Точек по часам: {res.get('hours')} · монет в расчёте: "
                     f"{len(res.get('symbols') or [])}")
    if pos:
        head = "Moving together:" if en else "Шли вместе:"
        lines.append("\n<b>" + head + "</b>")
        for p in pos:
            lines.append(f"· {_coin_list([p['a'], p['b']])} — r = {p['r']:+.2f}")
    if neg:
        head = "Opposite moves:" if en else "В противофазе:"
        lines.append("\n<b>" + head + "</b>")
        for p in neg:
            lines.append(f"· {_coin_list([p['a'], p['b']])} — r = {p['r']:+.2f}")
    if not pos and not neg:
        lines.append("\n" + ("No stable links yet: the window is too quiet."
                             if en else
                             "Устойчивых связей пока нет: окно слишком тихое."))
    if en:
        lines.append("\n<b>Where shorts burned:</b> " + _coin_list(flows.get("liquidated_short") or []))
        lines.append("<b>Where longs burned:</b> " + _coin_list(flows.get("liquidated_long") or []))
        lines.append("<b>CVD to sellers:</b> " + _coin_list(flows.get("cvd_sellers") or []))
        lines.append("<b>CVD to buyers:</b> " + _coin_list(flows.get("cvd_buyers") or []))
        lines.append("<b>OI growing:</b> " + _coin_list(flows.get("oi_up") or []))
        lines.append("<b>OI falling:</b> " + _coin_list(flows.get("oi_down") or []))
    else:
        lines.append("\n<b>Где выносило шорты:</b> " + _coin_list(flows.get("liquidated_short") or []))
        lines.append("<b>Где выносило лонги:</b> " + _coin_list(flows.get("liquidated_long") or []))
        lines.append("<b>CVD на сторону продавцов:</b> " + _coin_list(flows.get("cvd_sellers") or []))
        lines.append("<b>CVD на сторону покупателей:</b> " + _coin_list(flows.get("cvd_buyers") or []))
        lines.append("<b>OI растёт:</b> " + _coin_list(flows.get("oi_up") or []))
        lines.append("<b>OI падает:</b> " + _coin_list(flows.get("oi_down") or []))
    coins = res.get("coins") or {}
    top = sorted(coins.values(), key=lambda c: _num(c.get("liq_usd")), reverse=True)[:3]
    if top:
        head = "Biggest liquidations:" if en else "Крупнейшие ликвидации окна:"
        lines.append("\n<b>" + head + "</b>")
        for c in top:
            lines.append(f"· {c['symbol'].replace('_USDT', '')}: "
                         f"{_fmt_usd(c.get('liq_usd'))} всего · "
                         f"лонги {_fmt_usd(c.get('liq_long'))} · "
                         f"шорты {_fmt_usd(c.get('liq_short'))}")
    if site:
        lines.append(f"\n{'Cards and heat map' if en else 'Карточки и тепловая карта'}: {site}")
    return "\n".join(lines)
