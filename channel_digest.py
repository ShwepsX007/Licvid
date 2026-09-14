"""Сводки в Telegram-канал: лидеры ликвидаций, OI, CVD.

Чистые функции — без сети. Бот только публикует готовый текст + картинку.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(HERE, "static", "channel")

WINDOW_SEC = 4 * 3600
VARIANT_COUNT = 6

EXCH_NAMES = {
    "binance": "Binance",
    "bybit": "Bybit",
    "okx": "OKX",
    "gate": "Gate",
    "bitget": "Bitget",
    "htx": "HTX",
    "bitmex": "BitMEX",
    "hyperliquid": "Hyperliquid",
    "dydx": "dYdX",
    "kraken": "Kraken",
    "bitfinex": "Bitfinex",
}


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


def coin(sym: Any) -> str:
    s = str(sym or "").replace("-", "_")
    return (s.split("_")[0] or s).upper() or "—"


def exch(name: Any) -> str:
    k = str(name or "").strip().lower()
    return EXCH_NAMES.get(k, (k or "—").title())


def collect_digest(
    events: List[dict],
    window_sec: int = WINDOW_SEC,
    now: Optional[float] = None,
    oi: Optional[Dict[str, dict]] = None,
    cvd: Optional[Dict[str, float]] = None,
) -> dict:
    """Агрегат за окно: лидеры монет, биржи, крупнейший удар, OI/CVD если дали."""
    import time
    now = float(now if now is not None else time.time())
    rows = []
    for x in events or []:
        try:
            ts = float(x.get("timestamp") or 0)
        except (TypeError, ValueError):
            continue
        if now - ts > window_sec or now - ts < 0:
            continue
        rows.append(x)

    def usd_of(x: dict) -> float:
        try:
            return float(x.get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    longs = shorts = 0.0
    coins: Dict[str, dict] = {}
    exchanges: Dict[str, float] = {}
    for x in rows:
        u = usd_of(x)
        side = str(x.get("side") or "")
        # side SELL = вынесли лонг, BUY = вынесли шорт (как в server.py)
        if side == "SELL":
            longs += u
        else:
            shorts += u
        sym = str(x.get("symbol") or "")
        c = coins.setdefault(sym, {"symbol": sym, "usd": 0.0,
                                   "longs": 0.0, "shorts": 0.0, "count": 0})
        c["usd"] += u
        c["count"] += 1
        if side == "SELL":
            c["longs"] += u
        else:
            c["shorts"] += u
        ex = str(x.get("exchange") or "")
        if ex:
            exchanges[ex] = exchanges.get(ex, 0.0) + u

    top_coins = sorted(coins.values(), key=lambda c: c["usd"], reverse=True)
    top_ex = sorted(exchanges.items(), key=lambda kv: kv[1], reverse=True)
    biggest = max(rows, key=usd_of, default=None)

    return {
        "window_h": max(1, int(round(window_sec / 3600))),
        "total_usd": longs + shorts,
        "longs_usd": longs,
        "shorts_usd": shorts,
        "count": len(rows),
        "top_coins": top_coins[:8],
        "exchanges": dict(top_ex),
        "biggest": biggest,
        "oi": dict(oi or {}),
        "cvd": {k: float(v) for k, v in (cvd or {}).items()
                if isinstance(v, (int, float))},
    }


def _bias_words(longs: float, shorts: float) -> str:
    tot = longs + shorts
    if tot <= 0:
        return "лента почти молчала"
    if longs > shorts * 1.25:
        return "резали лонги — рынок сбрасывал оптимистов"
    if shorts > longs * 1.25:
        return "выносили шорты — отскок кормил медведей вверх"
    return "били с обеих сторон, без явного перекоса"


def _oi_line(snap: dict) -> str:
    parts = []
    for c in (snap.get("top_coins") or [])[:3]:
        sym = c.get("symbol")
        payload = (snap.get("oi") or {}).get(sym) or {}
        ch = (payload.get("changes") or {}).get("h4") or {}
        usd = ch.get("usd")
        pct = ch.get("pct")
        if usd is None:
            tot = payload.get("total_usd")
            if tot:
                parts.append(f"{coin(sym)} {money(tot)}")
            continue
        arrow = "↑" if float(usd) >= 0 else "↓"
        extra = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
        parts.append(f"{coin(sym)} {arrow}{money(usd)}{extra}")
    return " · ".join(parts)


def _cvd_line(snap: dict) -> str:
    parts = []
    for c in (snap.get("top_coins") or [])[:3]:
        sym = c.get("symbol")
        v = (snap.get("cvd") or {}).get(sym)
        if v is None:
            continue
        side = "покупки" if float(v) >= 0 else "продажи"
        parts.append(f"{coin(sym)} {side} {money(v)}")
    return " · ".join(parts)


def _leaders(snap: dict, n: int = 5) -> str:
    rows = []
    for i, c in enumerate((snap.get("top_coins") or [])[:n], 1):
        who = "лонги" if c["longs"] >= c["shorts"] else "шорты"
        rows.append(
            f"{i}. <b>{coin(c['symbol'])}</b> — {money(c['usd'])} "
            f"({c['count']} ликв., в основном {who})"
        )
    return "\n".join(rows) or "пока тихо — лидеров нет"


def _exchanges(snap: dict, n: int = 6) -> str:
    items = list((snap.get("exchanges") or {}).items())[:n]
    if not items:
        return "биржи молчали"
    return "\n".join(f"· {exch(k)} — {money(v)}" for k, v in items)


def _biggest_line(snap: dict) -> str:
    b = snap.get("biggest") or {}
    if not b:
        return ""
    side = "лонг" if b.get("side") == "SELL" else "шорт"
    return (f"Самый жирный удар: <b>{coin(b.get('symbol'))}</b> "
            f"{money(b.get('usd'))} ({side}, {exch(b.get('exchange'))}).")


def render_post(snap: dict, variant: int = 0) -> str:
    """Художественная сводка. variant крутится 0..5, чтобы посты не копировали друг друга."""
    v = int(variant) % VARIANT_COUNT
    h = int(snap.get("window_h") or 4)
    total = money(snap.get("total_usd"))
    n = int(snap.get("count") or 0)
    longs = float(snap.get("longs_usd") or 0)
    shorts = float(snap.get("shorts_usd") or 0)
    bias = _bias_words(longs, shorts)
    leaders = _leaders(snap)
    venues = _exchanges(snap)
    big = _biggest_line(snap)
    oi = _oi_line(snap)
    cvd = _cvd_line(snap)

    oi_block = f"\n\nOI за {h}ч: {oi}" if oi else ""
    cvd_block = f"\nCVD: {cvd}" if cvd else ""
    big_block = f"\n{big}" if big else ""

    heads = [
        f"Ночная смена на ленте. {h} часа, и рынок снова кого-то съел.",
        f"Разбор полётов за {h}ч — без розовых очков.",
        f"Кто кормил ленту последние {h} часа.",
        f"{h} часа огня. Коротко, по фактам, с характером.",
        f"Дневник терминала. Окно {h}ч.",
        f"Сводка с передовой. Ликвидации за {h} часа.",
    ]
    tails = [
        "Плечи короче, чем кажется. Увидимся через четыре часа. — <i>LiqScope</i>",
        "Это не сигнал. Это рентген. — <i>LiqScope</i>",
        "Кто не подписан на ленту — читает новости с опозданием. — <i>LiqScope</i>",
        "Рынок не обязан быть вежливым. Мы — тоже. — <i>LiqScope</i>",
        "Сохранил, перечитал, уменьшил плечо. — <i>LiqScope</i>",
        "Держите стопы там, где они ещё имеют смысл. — <i>LiqScope</i>",
    ]
    middles = [
        (
            f"За окно вынесло <b>{total}</b> в {n} ликвидациях: "
            f"лонги {money(longs)}, шорты {money(shorts)}. {bias.capitalize()}."
        ),
        (
            f"Счётчик: <b>{total}</b> / {n} событий. "
            f"Лонги {money(longs)} × шорты {money(shorts)}. {bias.capitalize()}."
        ),
        (
            f"Касса боли — <b>{total}</b>. {n} ударов. "
            f"{bias.capitalize()}. Лонги {money(longs)}, шорты {money(shorts)}."
        ),
        (
            f"<b>{total}</b> сгорело за {h}ч ({n} ликв.). "
            f"Лонги {money(longs)}, шорты {money(shorts)} — {bias}."
        ),
        (
            f"Итого <b>{total}</b> и {n} ликвидаций. "
            f"{bias.capitalize()}. Лонги {money(longs)}, шорты {money(shorts)}."
        ),
        (
            f"За {h} часа лента намолотила <b>{total}</b> ({n} шт.). "
            f"Лонги {money(longs)} против шортов {money(shorts)}. {bias.capitalize()}."
        ),
    ]

    text = (
        f"<b>{heads[v]}</b>\n\n"
        f"{middles[v]}\n\n"
        f"<b>Лидеры</b>\n{leaders}\n\n"
        f"<b>Биржи</b>\n{venues}"
        f"{big_block}{oi_block}{cvd_block}\n\n"
        f"{tails[v]}"
    )
    return text[:3900]


def list_images() -> List[str]:
    if not os.path.isdir(IMAGES_DIR):
        return []
    out = []
    for name in sorted(os.listdir(IMAGES_DIR)):
        if name.lower().endswith((".jpg", ".jpeg", ".png")):
            out.append(os.path.join(IMAGES_DIR, name))
    return out


def pick_image(variant: int = 0) -> Optional[str]:
    imgs = list_images()
    if not imgs:
        return None
    return imgs[int(variant) % len(imgs)]
