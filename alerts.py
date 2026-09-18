"""Алерты по объёму: ликвидации, CVD, OI.

Чистые функции без сети. Сервер подставляет снимок рынка,
бот и кабинет читают один и тот же конфиг.

Окно агрегации у каждой метрики своё (``windows``): у ликвидаций обычно
минуты, у CVD — четверть часа, у OI — часы. Плюс главное правило против
повторов: **после сигнала окно метрики начинается заново**. Данные, из
которых сигнал уже собрался, в следующее окно не попадают — второй сигнал
потребует столько же НОВОГО объёма (см. ``since`` в :func:`evaluate` и
:func:`top_per_metric`, который оставляет одну карточку на метрику с
лидером и парой «ещё в волне»).
"""
from __future__ import annotations

import html
from typing import Any, Dict, Iterable, List, Optional

METRICS = ("liq", "cvd", "oi")
METRIC_TITLE = {
    "liq": "ликвидации",
    "cvd": "CVD",
    "oi": "OI",
}
METRIC_ICON = {"liq": "💥", "cvd": "🌊", "oi": "📊"}

# Окна агрегации: ровно пять кнопок — и в боте, и в кабинете (в кабинете
# они в одну строку, шестая кнопка ломала раскладку). Пятёрка больше не
# предлагается: на ней сигналы приходили почти как на минутной.
WINDOW_PRESETS = (1, 15, 30, 60, 240)
THRESHOLD_PRESETS = (50_000, 100_000, 250_000, 500_000, 1_000_000, 5_000_000)
THRESHOLD_PRESETS_LIQ = THRESHOLD_PRESETS
THRESHOLD_PRESETS_FLOW = (10_000, 100_000, 1_000_000, 10_000_000, 100_000_000)
MIN_PRESETS = (0, 10_000, 25_000, 50_000, 100_000, 250_000)
COIN_PRESETS = ("ALL", "BTC_USDT", "ETH_USDT", "SOL_USDT")

# OI в трекере лежит готовыми окнами — берём ближайшее ≤ окну пользователя.
OI_WIN_BY_MIN = (
    (1, "m1"), (5, "m5"), (15, "m15"), (30, "m30"),
    (60, "h1"), (240, "h4"), (1440, "h24"),
)

#: окно по умолчанию, минуты
DEFAULT_WINDOW_MIN = 5
#: окна по метрикам: у ликвидаций минуты, у CVD и OI — крупнее
DEFAULT_WINDOWS: Dict[str, int] = {"liq": 5, "cvd": 15, "oi": 60}
#: монета по умолчанию — у каждой метрики своя: можно слушать ликвидации
#: BTC, CVD эфира и OI сола сразу, и сигналы придут независимо
DEFAULT_COINS: Dict[str, str] = {m: "ALL" for m in METRICS}
#: минимальная пауза между сигналами одной метрики, сек: окно может
#: перезапуститься и сразу переполниться (крупный удар пришёл одним событием),
#: и один сигнал не должен разъезжаться на два сообщения
MIN_GAP_SEC = 60

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "watch": ["liq"],
    "symbol": "ALL",
    "window_min": 5,                       # для старых настроек и клиентов
    "windows": dict(DEFAULT_WINDOWS),
    "coins": dict(DEFAULT_COINS),          # монета каждой метрики
    "threshold": {"liq": 500_000, "cvd": 1_000_000, "oi": 1_000_000},
    "min_event": {"liq": 0, "cvd": 0, "oi": 0},
}


def canon_symbol(symbol: str) -> str:
    s = str(symbol or "").upper().replace("-SWAP", "").replace("-", "_").strip()
    if s in ("ALL", "*", ""):
        return "ALL"
    if "_" not in s:
        for quote in ("USDT", "USDC", "USD"):
            if s.endswith(quote) and len(s) > len(quote):
                s = f"{s[:-len(quote)]}_{quote}"
                break
    return s or "ALL"


def coin_name(symbol: str) -> str:
    s = canon_symbol(symbol)
    if s == "ALL":
        return "все монеты"
    return (s.split("_")[0] or s).upper()


def symbol_of(cfg: Dict[str, Any], metric: str) -> str:
    """Монета метрики: своя из ``coins``, иначе общее поле ``symbol``.

    Старые настройки хранили одну монету на все метрики (``symbol``) — она
    читается как значение по умолчанию, чтобы выбор пользователя не потерялся.
    """
    metric = str(metric or "liq").lower()
    coins = cfg.get("coins") if isinstance(cfg.get("coins"), dict) else {}
    if coins.get(metric):
        return canon_symbol(str(coins.get(metric)))
    return canon_symbol(str(cfg.get("symbol") or "ALL"))


def money(v: Any) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        n = 0.0
    sign = "−" if n < 0 else ""
    n = abs(n)
    if n >= 1e9:
        return f"{sign}${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{sign}${n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{sign}${n / 1e3:.1f}K"
    return f"{sign}${n:.0f}"


def window_label(minutes: int) -> str:
    m = int(minutes or 0)
    if m >= 1440 and m % 1440 == 0:
        return f"{m // 1440}д"
    if m >= 60 and m % 60 == 0:
        return f"{m // 60}ч"
    return f"{m}м"


def oi_window_key(window_min: int) -> str:
    pick = "m5"
    for m, key in OI_WIN_BY_MIN:
        if int(window_min or 0) >= m:
            pick = key
    return pick


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _metric_map(raw: Any, defaults: Dict[str, float]) -> Dict[str, float]:
    out = {k: float(defaults[k]) for k in METRICS}
    if isinstance(raw, dict):
        for k in METRICS:
            if raw.get(k) is not None:
                out[k] = max(0.0, _num(raw.get(k), out[k]))
    elif raw is not None:
        n = max(0.0, _num(raw, 0.0))
        for k in METRICS:
            out[k] = n
    return out


def normalize_config(raw: Any) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    watch = []
    given = src.get("watch") or src.get("metrics") or []
    if isinstance(given, str):
        given = [given]
    for m in given:
        m = str(m or "").lower().strip()
        if m in ("io", "oi", "open_interest"):
            m = "oi"
        if m in ("liquidation", "liquidations", "liq"):
            m = "liq"
        if m in METRICS and m not in watch:
            watch.append(m)
    if "watch" not in src and "metrics" not in src:
        watch = ["liq"]
    # окно: старое единое число (window_min/window) — раскладка на все
    # метрики; новые настройки присылают windows {"liq":5,"cvd":15,"oi":60}
    flat = src.get("window_min") or src.get("window")
    single = window_minutes(_num(flat, DEFAULT_WINDOW_MIN))
    raw_windows = src.get("windows") if isinstance(src.get("windows"), dict) else {}
    windows = {}
    for m in METRICS:
        given = raw_windows.get(m)
        if given is not None:
            windows[m] = window_minutes(_num(given, single))
        elif flat is not None:
            windows[m] = single                  # старое единое окно на все метрики
        else:
            windows[m] = window_minutes(DEFAULT_WINDOWS[m])
    window = windows[watch[0]] if watch else windows["liq"]
    legacy_symbol = canon_symbol(str(src.get("symbol") or "ALL"))
    raw_coins = src.get("coins") if isinstance(src.get("coins"), dict) else {}
    coins = {}
    for m in METRICS:
        given = raw_coins.get(m)
        coins[m] = canon_symbol(str(given)) if given else legacy_symbol
    thr = _metric_map(src.get("threshold"), DEFAULT_CONFIG["threshold"])
    mins = _metric_map(src.get("min_event"), DEFAULT_CONFIG["min_event"])
    enabled = src.get("enabled")
    if enabled is None:
        enabled = src.get("on")
    return {
        "enabled": bool(enabled),
        "watch": watch,
        # старое единое поле остаётся в конфиге (его читают клиенты), но
        # движок берёт монету метрики из coins — они могут быть разными
        "symbol": legacy_symbol,
        "coins": coins,
        "window_min": window,
        "windows": windows,
        "threshold": thr,
        "min_event": mins,
    }


def window_minutes(v: Any) -> int:
    """Окно в минутах: 1…1440 (сутки)."""
    return max(1, min(int(_num(v, DEFAULT_WINDOW_MIN)), 1440))


def window_of(cfg: Dict[str, Any], metric: str) -> int:
    """Окно метрики: своё из windows, иначе единое window_min."""
    metric = str(metric or "liq").lower()
    wins = cfg.get("windows") if isinstance(cfg.get("windows"), dict) else {}
    if wins.get(metric) is not None:
        return window_minutes(wins.get(metric))
    return window_minutes(cfg.get("window_min") or DEFAULT_WINDOW_MIN)


def since_of(since: Any, metric: str, symbol: str) -> Optional[float]:
    """Когда эта метрика последний раз сигналила — с этого момента новое окно.

    ``since`` — либо функция ``(metric, symbol) -> ts``, либо словарь с ключом
    ``"ликвидации|BTC_USDT"`` / ``(metric, symbol)`` / ``metric``.
    """
    if since is None:
        return None
    val = None
    if callable(since):
        try:
            val = since(str(metric), str(symbol))
        except TypeError:
            val = None
        except Exception:                                  # noqa: BLE001
            val = None
    elif isinstance(since, dict):
        for key in (f"{metric}|{symbol}", (str(metric), str(symbol)), str(metric)):
            if since.get(key):
                val = since.get(key)
                break
    ts = _num(val, 0.0)
    return ts if ts > 0 else None


def window_start(now: float, window_sec: float, since_ts: Optional[float]) -> float:
    """Начало окна: ``now - окно``, но не раньше прошлого сигнала метрики.

    В этом вся суть: данные, из которых сигнал уже ушёл, в следующем окне не
    участвуют — окно считается заново.
    """
    start = float(now) - max(1.0, float(window_sec))
    if since_ts and float(since_ts) > start and float(since_ts) <= float(now):
        return float(since_ts)
    return start


def span_minutes(now: float, window_sec: float, since_ts: Optional[float]) -> int:
    """Сколько минут реально покрывает окно (после перезапуска — меньше)."""
    start = window_start(now, window_sec, since_ts)
    return max(1, int(round((float(now) - start) / 60.0)) or 1)


def threshold_presets(metric: str) -> List[int]:
    if str(metric or "").lower() in ("cvd", "oi"):
        return list(THRESHOLD_PRESETS_FLOW)
    return list(THRESHOLD_PRESETS_LIQ)


def sparkline(points: Dict[Any, float], now: float, window_sec: float,
              n: int = 24) -> List[float]:
    n = max(2, int(n or 24))
    win = max(1.0, float(window_sec or 1))
    step = win / n
    bins = [0.0] * n
    for ts, val in (points or {}).items():
        try:
            t = float(ts)
            v = float(val)
        except (TypeError, ValueError):
            continue
        age = now - t
        if age < 0 or age > win:
            continue
        idx = min(n - 1, max(0, int((win - age) / step)))
        bins[idx] += v
    return bins


def presets() -> Dict[str, Any]:
    return {
        "metrics": list(METRICS),
        "windows": list(WINDOW_PRESETS),
        "thresholds": list(THRESHOLD_PRESETS),
        "thresholds_liq": list(THRESHOLD_PRESETS_LIQ),
        "thresholds_flow": list(THRESHOLD_PRESETS_FLOW),
        "min_event": list(MIN_PRESETS),
        "coins": list(COIN_PRESETS),
    }


def cooldown_sec(window_min: int) -> float:
    return float(max(60, int(window_min or 1) * 60))


def liq_by_symbol(events: Iterable[dict], window_sec: float, min_usd: float,
                  now: float, symbol: str = "ALL",
                  since: Optional[float] = None) -> Dict[str, dict]:
    """Сумма ликвидаций в окне. min_usd — отсечка одного удара.

    ``since`` — время прошлого сигнала метрики: события до него в окно уже не
    попадают, окно считается заново.
    """
    want = canon_symbol(symbol)
    start = window_start(now, window_sec, since)
    out: Dict[str, dict] = {}
    for x in events or []:
        try:
            ts = float(x.get("timestamp") or 0)
            usd = float(x.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if usd < min_usd or ts < start or ts > now:
            continue
        sym = canon_symbol(str(x.get("symbol") or ""))
        if not sym or sym == "ALL":
            continue
        if want != "ALL" and sym != want:
            continue
        row = out.setdefault(sym, {"symbol": sym, "usd": 0.0, "longs": 0.0,
                                   "shorts": 0.0, "count": 0})
        row["usd"] += usd
        row["count"] += 1
        if str(x.get("side") or "") == "SELL":
            row["longs"] += usd
        else:
            row["shorts"] += usd
    return out


def cvd_by_symbol(cvd_acc: Dict[str, Dict[int, float]], window_sec: float,
                  min_bucket: float, now: float, symbol: str = "ALL",
                  since: Optional[float] = None) -> Dict[str, dict]:
    """Сумма тейкер-дельты по бакетам. min_bucket — |дельта бакета| для учёта.

    Бакеты с временем до прошлого сигнала метрики не считаем (``since``):
    окно после сигнала начинается заново.
    """
    want = canon_symbol(symbol)
    start = window_start(now, window_sec, since)
    grouped: Dict[str, Dict[int, float]] = {}
    for key, acc in (cvd_acc or {}).items():
        if "|" not in str(key):
            continue
        sym, tf_s = str(key).rsplit("|", 1)
        try:
            tf = int(tf_s)
        except (TypeError, ValueError):
            continue
        sym = canon_symbol(sym)
        if want != "ALL" and sym != want:
            continue
        # чем мельче бакет, тем точнее окно — берём самый мелкий доступный
        prev = grouped.get(sym)
        if prev is None or tf < prev[0]:
            grouped[sym] = (tf, acc)
    out: Dict[str, dict] = {}
    for sym, (_tf, acc) in grouped.items():
        total = 0.0
        n = 0
        for b, v in (acc or {}).items():
            try:
                ts = float(b)
                val = float(v)
            except (TypeError, ValueError):
                continue
            if ts < start or ts > now + 60:
                continue
            if abs(val) < min_bucket:
                continue
            total += val
            n += 1
        if n or want != "ALL":
            out[sym] = {"symbol": sym, "usd": total, "count": n, "abs": abs(total)}
    return out


def oi_from_series(series: Dict[Any, float], since: float,
                   now: float) -> Optional[dict]:
    """ΔOI от прошлого сигнала по ряду уровней (по бакетам трекера).

    Готовые окна OI приходят посчитанными на стороне трекера, и «начать окно
    заново» ими не сделать. Если в снимке есть ряд уровней — считаем сами:
    последний уровень минус уровень на бакете не позже ``since``.
    """
    if not isinstance(series, dict) or len(series) < 2:
        return None
    pts = []
    for ts, val in series.items():
        v = _num(val, 0.0)
        if v > 0:
            pts.append((_num(ts, 0.0), v))
    if len(pts) < 2:
        return None
    pts.sort()
    if pts[-1][0] < now - 2 * 3600:                 # ряд устарел
        return None
    ref = None
    for ts, v in pts:
        if ts <= since:
            ref = (ts, v)
        else:
            break
    if ref is None or ref[0] < since - 3600:
        return None
    base = ref[1]
    if base <= 0:
        return None
    delta = pts[-1][1] - base
    return {"usd": delta, "pct": delta / base * 100.0, "from_ts": ref[0],
            "to_ts": pts[-1][0]}


def oi_by_symbol(oi_map: Dict[str, dict], window_min: int,
                 min_usd: float, symbol: str = "ALL",
                 since: Optional[float] = None, now: Optional[float] = None,
                 ) -> Dict[str, dict]:
    """ΔOI за окно метрики; после сигнала — только новый рост/падение."""
    want = canon_symbol(symbol)
    key = oi_window_key(window_min)
    out: Dict[str, dict] = {}
    for sym, payload in (oi_map or {}).items():
        sym = canon_symbol(sym)
        if want != "ALL" and sym != want:
            continue
        ch = ((payload or {}).get("changes") or {}).get(key) or {}
        usd = ch.get("usd")
        val = _num(usd, 0.0)
        pct = _num(ch.get("pct"), 0.0)
        rebased = False
        if since and now:
            fresh = oi_from_series((payload or {}).get("_series") or {}, since, now)
            if fresh is not None:
                val, pct, rebased = fresh["usd"], fresh["pct"], True
            else:
                # окно перезапустить нечем (нет ряда) — значение остаётся
                # прежним, повторы отсекает пауза на стороне сервера
                rebased = False
        if usd is None and not rebased:
            continue
        if abs(val) < min_usd:
            continue
        out[sym] = {
            "symbol": sym,
            "usd": val,
            "pct": pct,
            "total": _num((payload or {}).get("total_usd"), 0.0),
            "abs": abs(val),
            "rebased": rebased,
        }
    return out


def live_snapshot(cfg: Dict[str, Any], market: Dict[str, Any],
                  since: Any = None) -> Dict[str, Any]:
    """Текущие значения окна для UI: по каждой метрике — лидер и топ.

    ``since`` — времена прошлых сигналов (как в :func:`evaluate`): превью в
    кабинете должно показывать ровно то, из чего соберётся следующий сигнал.
    """
    cfg = normalize_config(cfg)
    now = _num(market.get("now"))
    # монета каждой метрики своя — превью в кабинете считает то же, что уйдёт
    syms = {m: symbol_of(cfg, m) for m in METRICS}
    sym = syms["liq"]
    out: Dict[str, Any] = {}
    w_liq = window_of(cfg, "liq")
    w_cvd = window_of(cfg, "cvd")
    w_oi = window_of(cfg, "oi")
    liq = liq_by_symbol(market.get("events") or [], w_liq * 60,
                        cfg["min_event"]["liq"], now, sym,
                        since_of(since, "liq", sym))
    cvd = cvd_by_symbol(market.get("cvd") or {}, w_cvd * 60,
                        cfg["min_event"]["cvd"], now, syms["cvd"],
                        since_of(since, "cvd", syms["cvd"]))
    oi = oi_by_symbol(market.get("oi") or {}, w_oi,
                      cfg["min_event"]["oi"], syms["oi"],
                      since_of(since, "oi", syms["oi"]), now)

    def pack(rows: Dict[str, dict], signed: bool) -> dict:
        ranked = sorted(rows.values(),
                        key=lambda r: abs(_num(r.get("usd"))), reverse=True)
        top = ranked[:5]
        lead = top[0] if top else None
        total = sum(abs(_num(r.get("usd"))) for r in rows.values()) if not signed \
            else sum(_num(r.get("usd")) for r in rows.values())
        return {
            "value": _num(lead.get("usd")) if lead else 0.0,
            "abs": abs(_num(lead.get("usd"))) if lead else 0.0,
            "symbol": (lead or {}).get("symbol") or sym,
            "count": int((lead or {}).get("count") or 0),
            "longs": _num((lead or {}).get("longs")),
            "shorts": _num((lead or {}).get("shorts")),
            "pct": _num((lead or {}).get("pct")),
            "total": total,
            "top": [{"symbol": r["symbol"], "usd": round(_num(r.get("usd")), 2),
                     "count": int(r.get("count") or 0)} for r in top],
        }

    out["liq"] = pack(liq, signed=False)
    if liq and sym == "ALL":
        # для ликвидаций «все» показываем и рынок целиком
        tot = sum(_num(r.get("usd")) for r in liq.values())
        lng = sum(_num(r.get("longs")) for r in liq.values())
        sht = sum(_num(r.get("shorts")) for r in liq.values())
        n = sum(int(r.get("count") or 0) for r in liq.values())
        out["liq"]["market"] = {"usd": tot, "longs": lng, "shorts": sht, "count": n}
    out["cvd"] = pack(cvd, signed=True)
    out["oi"] = pack(oi, signed=True)

    liq_pts: Dict[Any, float] = {}
    want = canon_symbol(sym)
    liq_start = window_start(now, w_liq * 60, since_of(since, "liq", sym))
    for x in (market.get("events") or []):
        try:
            ts = float(x.get("timestamp") or 0)
            usd = float(x.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if usd < cfg["min_event"]["liq"] or ts < liq_start or ts > now:
            continue
        ev_sym = canon_symbol(str(x.get("symbol") or ""))
        if want != "ALL" and ev_sym != want:
            continue
        liq_pts[int(ts)] = liq_pts.get(int(ts), 0.0) + usd
    cvd_pts: Dict[Any, float] = {}
    cvd_want = canon_symbol(syms["cvd"])
    for key, acc in (market.get("cvd") or {}).items():
        if "|" not in str(key):
            continue
        csym, _tf = str(key).rsplit("|", 1)
        csym = canon_symbol(csym)
        if cvd_want != "ALL" and csym != cvd_want:
            continue
        for b, v in (acc or {}).items():
            try:
                ts = float(b)
                val = float(v)
            except (TypeError, ValueError):
                continue
            if ts < window_start(now, w_cvd * 60, since_of(since, "cvd", sym)):
                continue
            if ts > now + 60:
                continue
            cvd_pts[int(ts)] = cvd_pts.get(int(ts), 0.0) + val
    out["liq"]["spark"] = sparkline(liq_pts, now, w_liq * 60)
    out["cvd"]["spark"] = sparkline(cvd_pts, now, w_cvd * 60)
    out["oi"]["spark"] = []
    for m, win, lr in (("liq", w_liq, liq), ("cvd", w_cvd, cvd),
                       ("oi", w_oi, oi)):
        out[m]["window_min"] = win
        out[m]["span_min"] = span_minutes(now, win * 60,
                                          since_of(since, m, syms[m]))
        out[m]["coin"] = syms[m]
    out["window_min"] = cfg["window_min"]
    out["windows"] = dict(cfg["windows"])
    out["symbol"] = cfg["symbol"]
    out["coins"] = dict(syms)
    return out


def evaluate(cfg: Dict[str, Any], market: Dict[str, Any],
             limit: int = 3, since: Any = None) -> List[dict]:
    """Кто пересёк порог. Для ALL — до `limit` монет на метрику.

    У каждой метрики своё окно (``windows``), и после сигнала окно метрики
    начинается заново: считаются только данные позже прошлого сигнала
    (``since``), поэтому один и тот же объём не превращается в два сообщения.
    """
    cfg = normalize_config(cfg)
    now = _num(market.get("now"))
    hits: List[dict] = []
    for metric in cfg["watch"]:
        thr = float(cfg["threshold"].get(metric) or 0)
        if thr <= 0:
            continue
        win = window_of(cfg, metric)
        # монета у каждой метрики своя: ликвидации BTC и CVD эфира слушаются
        # одновременно и сигналят независимо друг от друга
        coin = symbol_of(cfg, metric)
        start = since_of(since, metric, coin)
        if metric == "liq":
            rows = liq_by_symbol(market.get("events") or [], win * 60,
                                 cfg["min_event"]["liq"], now, coin, start)
        elif metric == "cvd":
            rows = cvd_by_symbol(market.get("cvd") or {}, win * 60,
                                 cfg["min_event"]["cvd"], now, coin, start)
        else:
            rows = oi_by_symbol(market.get("oi") or {}, win,
                                cfg["min_event"]["oi"], coin, start, now)
        ranked = sorted(rows.values(),
                        key=lambda r: abs(_num(r.get("usd"))), reverse=True)
        n = 0
        for row in ranked:
            val = _num(row.get("usd"))
            if abs(val) < thr:
                continue
            span = span_minutes(now, win * 60, start)
            hits.append({
                "metric": metric,
                "symbol": row.get("symbol") or coin,
                "value": val,
                "abs": abs(val),
                "threshold": thr,
                "window_min": win,
                "span_min": span,
                "reset": span < win,
                "rebased": bool(row.get("rebased")),
                "count": int(row.get("count") or 0),
                "longs": _num(row.get("longs")),
                "shorts": _num(row.get("shorts")),
                "pct": _num(row.get("pct")),
            })
            n += 1
            if n >= limit:
                break
    hits.sort(key=lambda h: (METRICS.index(h["metric"]), -h["abs"]))
    return hits


def top_per_metric(hits: Iterable[dict], peers: int = 2) -> List[dict]:
    """Одна карточка на метрику: сигнал — это метрика, а не список монет.

    Из-за скользящего окна одна волна легко давала по сообщению на каждую
    монету — выглядело как дубли. Берём лидера метрики, а остальные монеты,
    которые тоже прошли порог, показываем короткой строкой «ещё в волне».
    """
    best: Dict[str, dict] = {}
    for hit in hits or []:
        m = str(hit.get("metric") or "")
        if m and m not in best:
            best[m] = dict(hit)
    for m, hit in best.items():
        lead_sym = canon_symbol(hit.get("symbol"))
        rest = [h for h in (hits or [])
                if str(h.get("metric") or "") == m
                and canon_symbol(h.get("symbol")) != lead_sym]
        hit["peers"] = [{"symbol": h.get("symbol"), "usd": _num(h.get("value")),
                         "count": int(h.get("count") or 0)}
                        for h in rest[:max(0, int(peers))]]
    out = [best[m] for m in METRICS if m in best]
    if len(out) < len(best):                       # метрика вне METRICS — как есть
        out += [best[m] for m in best if m not in METRICS]
    return out


def footer_html(site_url: str = "https://liqscope.online", lang: str = "ru") -> str:
    """Подвал сообщений бота — один и тот же у алертов и у сигналов.

    Русский вариант переводится общей таблицей (``bot_i18n``), английский
    собран здесь: сигналы пампов собираются сразу на языке подписчика.
    """
    site = (site_url or "https://liqscope.online").rstrip("/")
    tail = (" — a live liquidation stream"
            if str(lang).lower().startswith("en")
            else " — живой поток ликвидаций")
    return ("──────────────\n"
            f'🌐 <a href="{html.escape(site, quote=True)}">LiqScope</a>' + tail)


def peers_line(peers: Iterable[dict], n: int = 2) -> str:
    """«ещё в волне»: другие монеты, которые тоже прошли порог."""
    parts = []
    for p in list(peers or [])[:max(0, int(n))]:
        sym = coin_name(str(p.get("symbol") or ""))
        if not sym:
            continue
        parts.append(f"{html.escape(sym)} {html.escape(money(p.get('usd')))}")
    return ", ".join(parts)


def format_alert_html(hit: dict, site_url: str = "https://liqscope.online") -> str:
    """Сигнал алерта. Это же оформление — образец для всех сообщений бота."""
    metric = str(hit.get("metric") or "liq")
    icon = METRIC_ICON.get(metric, "🔔")
    title = METRIC_TITLE.get(metric, metric)
    sym = coin_name(str(hit.get("symbol") or ""))
    win = int(hit.get("window_min") or 5)
    span = int(hit.get("span_min") or win)
    val = money(hit.get("value"))
    thr = money(hit.get("threshold"))
    # «за 1м из 5м» вместо «за 5м» — сразу видно, что окно после сигнала
    # начато заново и в сообщении только новые данные.
    if span < win:
        tail = (f"за {window_label(span)}"
                f" · окно {window_label(win)}")
    else:
        tail = f"за {window_label(win)}"
    lines = [
        f"{icon} <b>Алерт · {html.escape(title)}</b>",
        f"<b>{html.escape(sym)}</b>  <code>{html.escape(val)}</code>  {html.escape(tail)}",
        f"порог <code>{html.escape(thr)}</code>",
    ]
    if metric == "liq":
        n = int(hit.get("count") or 0)
        lines.append(
            f"{n} ударов · 🔴 {html.escape(money(hit.get('longs')))}  "
            f"🟢 {html.escape(money(hit.get('shorts')))}"
        )
    elif metric == "cvd":
        side = "покупки" if _num(hit.get("value")) >= 0 else "продажи"
        lines.append(side)
    elif metric == "oi":
        pct = hit.get("pct")
        extra = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) and pct else ""
        arrow = "↑" if _num(hit.get("value")) >= 0 else "↓"
        lines.append(f"изменение OI {arrow}{extra}")
    extra_peers = peers_line(hit.get("peers") or [])
    if extra_peers:
        lines.append(f"ещё в волне: {extra_peers}")
    if span < win:
        lines.append("<i>окно после сигнала начато заново</i>")
    site = (site_url or "https://liqscope.online").rstrip("/")
    href = html.escape(f"{site}/terminal", quote=True)
    lines.append(f'<a href="{href}">посмотреть в терминале</a>')
    lines.append(footer_html(site))
    return "\n".join(lines)


def format_config_text(cfg: Dict[str, Any]) -> str:
    cfg = normalize_config(cfg)
    on = "включён" if cfg["enabled"] else "выключен"
    watch = ", ".join(METRIC_TITLE[m] for m in cfg["watch"]) or "ничего"
    lines = [
        "🔔 <b>Алерты по объёму</b>",
        f"сигнал: <b>{on}</b>",
        f"смотрю: {html.escape(watch)}",
    ]
    # строку показываем у каждой метрики, даже выключенной: окно у неё своё,
    # и видно, с чего начнётся, если её включить
    for m in METRICS:
        mark = "" if m in cfg["watch"] else " · выкл"
        coin = coin_name(symbol_of(cfg, m))
        lines.append(
            f"{METRIC_ICON[m]} <b>{html.escape(METRIC_TITLE[m])}</b>{mark}"
            f" · монета <b>{html.escape(coin)}</b>"
            f" · окно <code>{html.escape(window_label(window_of(cfg, m)))}</code>"
            f" · порог <code>{html.escape(money(cfg['threshold'][m]))}</code>"
            f" · мин. <code>{html.escape(money(cfg['min_event'][m]))}</code>"
        )
    return "\n".join(lines)


def should_fire(last_ts: Optional[float], now: float, window_min: int) -> bool:
    if not last_ts:
        return True
    return (now - float(last_ts)) >= cooldown_sec(window_min)
