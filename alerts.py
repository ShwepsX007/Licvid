"""Алерты по объёму: ликвидации, CVD, OI.

Чистые функции без сети. Сервер подставляет снимок рынка,
бот и кабинет читают один и тот же конфиг.
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

WINDOW_PRESETS = (1, 5, 15, 30, 60, 240)
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

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "watch": ["liq"],
    "symbol": "ALL",
    "window_min": 5,
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
    window = int(_num(src.get("window_min") or src.get("window"), 5))
    window = max(1, min(window, 1440))
    thr = _metric_map(src.get("threshold"), DEFAULT_CONFIG["threshold"])
    mins = _metric_map(src.get("min_event"), DEFAULT_CONFIG["min_event"])
    enabled = src.get("enabled")
    if enabled is None:
        enabled = src.get("on")
    return {
        "enabled": bool(enabled),
        "watch": watch,
        "symbol": canon_symbol(str(src.get("symbol") or "ALL")),
        "window_min": window,
        "threshold": thr,
        "min_event": mins,
    }


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
                  now: float, symbol: str = "ALL") -> Dict[str, dict]:
    """Сумма ликвидаций в окне. min_usd — отсечка одного удара."""
    want = canon_symbol(symbol)
    out: Dict[str, dict] = {}
    for x in events or []:
        try:
            ts = float(x.get("timestamp") or 0)
            usd = float(x.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if usd < min_usd or now - ts > window_sec or now - ts < 0:
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
                  min_bucket: float, now: float,
                  symbol: str = "ALL") -> Dict[str, dict]:
    """Сумма тейкер-дельты по бакетам. min_bucket — |дельта бакета| для учёта."""
    want = canon_symbol(symbol)
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
            if now - ts > window_sec or now - ts < -60:
                continue
            if abs(val) < min_bucket:
                continue
            total += val
            n += 1
        if n or want != "ALL":
            out[sym] = {"symbol": sym, "usd": total, "count": n, "abs": abs(total)}
    return out


def oi_by_symbol(oi_map: Dict[str, dict], window_min: int,
                 min_usd: float, symbol: str = "ALL") -> Dict[str, dict]:
    """ΔOI за ближайшее окно трекера."""
    want = canon_symbol(symbol)
    key = oi_window_key(window_min)
    out: Dict[str, dict] = {}
    for sym, payload in (oi_map or {}).items():
        sym = canon_symbol(sym)
        if want != "ALL" and sym != want:
            continue
        ch = ((payload or {}).get("changes") or {}).get(key) or {}
        usd = ch.get("usd")
        if usd is None:
            continue
        val = _num(usd)
        if abs(val) < min_usd:
            continue
        out[sym] = {
            "symbol": sym,
            "usd": val,
            "pct": _num(ch.get("pct"), 0.0),
            "total": _num((payload or {}).get("total_usd"), 0.0),
            "abs": abs(val),
        }
    return out


def live_snapshot(cfg: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    """Текущие значения окна для UI: по каждой метрике — лидер и топ."""
    cfg = normalize_config(cfg)
    now = _num(market.get("now"))
    window_sec = cfg["window_min"] * 60
    sym = cfg["symbol"]
    out: Dict[str, Any] = {}
    liq = liq_by_symbol(market.get("events") or [], window_sec,
                        cfg["min_event"]["liq"], now, sym)
    cvd = cvd_by_symbol(market.get("cvd") or {}, window_sec,
                        cfg["min_event"]["cvd"], now, sym)
    oi = oi_by_symbol(market.get("oi") or {}, cfg["window_min"],
                      cfg["min_event"]["oi"], sym)

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
    for x in (market.get("events") or []):
        try:
            ts = float(x.get("timestamp") or 0)
            usd = float(x.get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if usd < cfg["min_event"]["liq"] or now - ts > window_sec or now - ts < 0:
            continue
        ev_sym = canon_symbol(str(x.get("symbol") or ""))
        if want != "ALL" and ev_sym != want:
            continue
        liq_pts[int(ts)] = liq_pts.get(int(ts), 0.0) + usd
    cvd_pts: Dict[Any, float] = {}
    for key, acc in (market.get("cvd") or {}).items():
        if "|" not in str(key):
            continue
        csym, _tf = str(key).rsplit("|", 1)
        csym = canon_symbol(csym)
        if want != "ALL" and csym != want:
            continue
        for b, v in (acc or {}).items():
            try:
                ts = float(b)
                val = float(v)
            except (TypeError, ValueError):
                continue
            if now - ts > window_sec or now - ts < -60:
                continue
            cvd_pts[int(ts)] = cvd_pts.get(int(ts), 0.0) + val
    out["liq"]["spark"] = sparkline(liq_pts, now, window_sec)
    out["cvd"]["spark"] = sparkline(cvd_pts, now, window_sec)
    out["oi"]["spark"] = []
    out["window_min"] = cfg["window_min"]
    out["symbol"] = cfg["symbol"]
    return out


def evaluate(cfg: Dict[str, Any], market: Dict[str, Any],
             limit: int = 3) -> List[dict]:
    """Кто пересёк порог. Для ALL — до `limit` монет на метрику."""
    cfg = normalize_config(cfg)
    now = _num(market.get("now"))
    window_sec = cfg["window_min"] * 60
    hits: List[dict] = []
    for metric in cfg["watch"]:
        thr = float(cfg["threshold"].get(metric) or 0)
        if thr <= 0:
            continue
        if metric == "liq":
            rows = liq_by_symbol(market.get("events") or [], window_sec,
                                 cfg["min_event"]["liq"], now, cfg["symbol"])
        elif metric == "cvd":
            rows = cvd_by_symbol(market.get("cvd") or {}, window_sec,
                                 cfg["min_event"]["cvd"], now, cfg["symbol"])
        else:
            rows = oi_by_symbol(market.get("oi") or {}, cfg["window_min"],
                                cfg["min_event"]["oi"], cfg["symbol"])
        ranked = sorted(rows.values(),
                        key=lambda r: abs(_num(r.get("usd"))), reverse=True)
        n = 0
        for row in ranked:
            val = _num(row.get("usd"))
            if abs(val) < thr:
                continue
            hits.append({
                "metric": metric,
                "symbol": row.get("symbol") or cfg["symbol"],
                "value": val,
                "abs": abs(val),
                "threshold": thr,
                "window_min": cfg["window_min"],
                "count": int(row.get("count") or 0),
                "longs": _num(row.get("longs")),
                "shorts": _num(row.get("shorts")),
                "pct": _num(row.get("pct")),
            })
            n += 1
            if n >= limit:
                break
    hits.sort(key=lambda h: h["abs"], reverse=True)
    return hits


def format_alert_html(hit: dict, site_url: str = "https://liqscope.online") -> str:
    metric = str(hit.get("metric") or "liq")
    icon = METRIC_ICON.get(metric, "🔔")
    title = METRIC_TITLE.get(metric, metric)
    sym = coin_name(str(hit.get("symbol") or ""))
    win = window_label(int(hit.get("window_min") or 5))
    val = money(hit.get("value"))
    thr = money(hit.get("threshold"))
    lines = [
        f"{icon} <b>Алерт · {html.escape(title)}</b>",
        f"<b>{html.escape(sym)}</b>  <code>{html.escape(val)}</code>  за {html.escape(win)}",
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
    site = (site_url or "https://liqscope.online").rstrip("/")
    href = html.escape(f"{site}/terminal", quote=True)
    lines.append(f'<a href="{href}">посмотреть в терминале</a>')
    lines.append("──────────────")
    lines.append(f'🌐 <a href="{html.escape(site, quote=True)}">LiqScope</a>'
                 " — живой поток ликвидаций")
    return "\n".join(lines)


def format_config_text(cfg: Dict[str, Any]) -> str:
    cfg = normalize_config(cfg)
    on = "включён" if cfg["enabled"] else "выключен"
    watch = ", ".join(METRIC_TITLE[m] for m in cfg["watch"]) or "ничего"
    lines = [
        f"сигнал: <b>{on}</b>",
        f"смотрю: {html.escape(watch)}",
        f"монета: <b>{html.escape(coin_name(cfg['symbol']))}</b>",
        f"окно: {html.escape(window_label(cfg['window_min']))}",
    ]
    for m in cfg["watch"]:
        lines.append(
            f"порог {METRIC_TITLE[m]}: <code>{html.escape(money(cfg['threshold'][m]))}</code>"
            f" · мин. <code>{html.escape(money(cfg['min_event'][m]))}</code>"
        )
    return "\n".join(lines)


def should_fire(last_ts: Optional[float], now: float, window_min: int) -> bool:
    if not last_ts:
        return True
    return (now - float(last_ts)) >= cooldown_sec(window_min)
