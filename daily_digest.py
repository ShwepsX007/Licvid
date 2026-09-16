"""Дневной дайджест: сбор фактов за сутки, текст для канала и сайта, архив.

Модуль чистый (никакой сети): данные приносит server.py, публикует бот.
Один дайджест = одна запись в архиве (`DigestStore`), из неё же собираются:
    * пост в русский канал (Telegram HTML, ≤ 3900 знаков);
    * пост в английский канал (тот же состав данных, английский язык);
    * статья на сайте (HTML для страницы /digest, архив за прошлые дни).

Факты дня (то, что просил показать):
    * максимальная ликвидация за сутки (сумма, монета, биржа, время, сторона);
    * число ликвидаций и суммы по биржам;
    * монеты, где открытый интерес максимально велик **относительно объёма**
      (OI / дневной оборот) + изменение OI за сутки;
    * изменение цены топ-5 монет по обороту за сутки;
    * общее настроение рынка (счёт из цены, тейкер-потока, ликвидаций и OI).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from channel_digest import coin, exch, liqs_word, money
from hour_board import hour_hhmm, tz_offset

HERE = os.path.dirname(os.path.abspath(__file__))
DAY_SEC = 86400
TEXT_LIMIT = 3900               # сообщение Telegram (лимит 4096, оставляем запас)
NARRATIVE_MIN = 320             # короче — считаем текстом-заглушкой
DEFAULT_KEEP = 400              # сколько дайджестов хранить в архиве

MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
             "августа", "сентября", "октября", "ноября", "декабря")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December")
WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница",
               "суббота", "воскресенье")
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
               "Saturday", "Sunday")


# ---------------------------------------------------------------------------
#  Мелкие помощники
# ---------------------------------------------------------------------------
def is_en(lang: Any) -> bool:
    return str(lang or "ru").lower().startswith("en")


def day_key(ts: float, tz: Optional[int] = None) -> str:
    """Календарная дата (YYYY-MM-DD) в часовом поясе сводок (по умолчанию МСК).

    Сдвиг пояса — в СЕКУНДАХ, как в hour_board.tz_offset().
    """
    tz = tz_offset() if tz is None else int(tz)
    return time.strftime("%Y-%m-%d", time.gmtime(float(ts) + tz))


def day_label(day: str, lang: str = "ru") -> str:
    """«17 сентября» / «17 September» (пустая дата — просто пусто)."""
    try:
        tm = time.strptime(str(day), "%Y-%m-%d")
    except (TypeError, ValueError):
        return str(day or "")
    months = MONTHS_EN if is_en(lang) else MONTHS_RU
    return f"{tm.tm_mday} {months[tm.tm_mon - 1]}"


def weekday_label(day: str, lang: str = "ru") -> str:
    try:
        tm = time.strptime(str(day), "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    names = WEEKDAYS_EN if is_en(lang) else WEEKDAYS_RU
    return names[tm.tm_wday]


def pct_txt(v: Any, lang: str = "ru", digits: int = 1) -> str:
    """«+1.8%» / «−3.2%» / «0.0%» (минус типографский, как в остальных сводках)."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(n) < 0.05:
        return "0.0%"
    sign = "−" if n < 0 else "+"
    return f"{sign}{abs(n):.{digits}f}%"


def arrow(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "•"
    if n > 0.05:
        return "▲"
    if n < -0.05:
        return "▼"
    return "→"


def price_txt(v: Any) -> str:
    """Цена монеты: крупные — с разрядами, мелкие — со значащими цифрами."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 10000:
        return f"${n:,.0f}"
    if n >= 1:
        return f"${n:,.2f}"
    if n >= 0.01:
        return f"${n:.4f}"
    return f"${n:.8f}".rstrip("0")


def pp(v: Any) -> str:
    """Проценты по модулю: «1.8%»."""
    try:
        return f"{abs(float(v)):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _sort_rows(rows: List[dict], key: str = "usd") -> List[dict]:
    return sorted([r for r in rows if r], key=lambda r: r.get(key) or 0,
                  reverse=True)


# ---------------------------------------------------------------------------
#  Сбор фактов за сутки
# ---------------------------------------------------------------------------
def collect_day(
    events: List[dict],
    now: Optional[float] = None,
    window_sec: int = DAY_SEC,
    oi: Optional[Dict[str, dict]] = None,
    prices: Optional[Dict[str, dict]] = None,
    flows: Optional[Dict[str, dict]] = None,
    oi_top: int = 5,
    price_top: int = 5,
    exch_top: int = 5,
    coin_top: int = 5,
) -> dict:
    """Факты за окно (по умолчанию сутки).

    ``oi``      — {монета: payload OiTracker (/api/oi)}: total_usd + changes.h24;
    ``prices``  — {монета: {price, pct, vol_usd}} — цена, изменение за сутки и
                  оборот за сутки (считает server.py по часовым свечам);
    ``flows``   — {монета: {начало часа: {cvd, vol}}} — часовые потоки, из них
                  берём суточную тейкер-дельту и оборот, если в prices их нет.
    """
    now = float(now if now is not None else time.time())
    rows: List[dict] = []
    for x in events or []:
        try:
            ts = float(x.get("timestamp") or 0)
        except (TypeError, ValueError):
            continue
        if now - ts > window_sec or now - ts < 0:
            continue
        rows.append(x)

    total = longs = shorts = 0.0
    per_ex: Dict[str, dict] = {}
    per_coin: Dict[str, dict] = {}
    biggest: Optional[dict] = None
    for x in rows:
        try:
            usd = float(x.get("usd") or 0)
        except (TypeError, ValueError):
            usd = 0.0
        total += usd
        if x.get("side") == "SELL":          # продавали принудительно — это лонги
            longs += usd
        else:
            shorts += usd
        ex = str(x.get("exchange") or "?").lower()
        cell = per_ex.setdefault(ex, {"name": ex, "usd": 0.0, "count": 0})
        cell["usd"] += usd
        cell["count"] += 1
        sym = str(x.get("symbol") or "")
        c = per_coin.setdefault(sym, {"symbol": sym, "usd": 0.0, "count": 0,
                                      "longs": 0.0, "shorts": 0.0})
        c["usd"] += usd
        c["count"] += 1
        if x.get("side") == "SELL":
            c["longs"] += usd
        else:
            c["shorts"] += usd
        if biggest is None or usd > float(biggest.get("usd") or 0):
            biggest = dict(x)

    # ---- оборот и тейкер-дельта за сутки из часовых потоков ----------------
    vol24: Dict[str, float] = {}
    cvd24: Dict[str, float] = {}
    for sym, hours in (flows or {}).items():
        vol = cvd = 0.0
        has_cvd = False
        for _h, cell in (hours or {}).items():
            try:
                vol += float(cell.get("vol") or 0)
            except (TypeError, ValueError):
                pass
            if cell.get("cvd") is not None and cell.get("has_cvd"):
                try:
                    cvd += float(cell.get("cvd") or 0)
                except (TypeError, ValueError):
                    continue
                has_cvd = True
        if vol > 0:
            vol24[sym] = vol
        if has_cvd:
            cvd24[sym] = cvd

    # ---- OI относительно объёма -------------------------------------------
    oi_rows: List[dict] = []
    for sym, payload in (oi or {}).items():
        if not isinstance(payload, dict):
            continue
        try:
            oi_usd = float(payload.get("total_usd") or 0)
        except (TypeError, ValueError):
            oi_usd = 0.0
        if oi_usd <= 0:
            continue
        ch = (payload.get("changes") or {}).get("h24") or {}
        pct = None
        if isinstance(ch, dict) and ch.get("pct") is not None:
            try:
                pct = float(ch["pct"])
            except (TypeError, ValueError):
                pct = None
        vol = float(vol24.get(sym) or (prices or {}).get(sym, {}).get("vol_usd") or 0)
        ratio = (oi_usd / vol) if vol > 0 else None
        oi_rows.append({"symbol": sym, "oi_usd": oi_usd, "vol_usd": vol,
                        "ratio": ratio, "pct": pct})
    oi_rows.sort(key=lambda r: (r.get("ratio") or 0, r.get("oi_usd") or 0),
                 reverse=True)
    oi_rows = [r for r in oi_rows if (r.get("ratio") or 0) > 0][:max(1, oi_top)]

    # ---- цена топ-5 за сутки ----------------------------------------------
    price_rows: List[dict] = []
    for sym, row in (prices or {}).items():
        if not isinstance(row, dict):
            continue
        price_rows.append({
            "symbol": sym,
            "price": row.get("price"),
            "pct": row.get("pct"),
            "vol_usd": row.get("vol_usd") or vol24.get(sym) or 0,
        })
    if not price_rows:
        # цен нет — показываем топ по ликвидациям, без процента
        price_rows = [{"symbol": c["symbol"], "price": None, "pct": None,
                       "vol_usd": vol24.get(c["symbol"]) or 0}
                      for c in _sort_rows(list(per_coin.values()))[:price_top]]
    price_rows = _sort_rows(price_rows, "vol_usd")[:max(1, price_top)]

    exchanges = _sort_rows(list(per_ex.values()), "usd")[:max(1, exch_top)]
    coins = [{"symbol": c["symbol"], "usd": c["usd"], "count": c["count"],
              "longs": c["longs"], "shorts": c["shorts"]}
             for c in _sort_rows(list(per_coin.values()), "usd")[:max(1, coin_top)]]

    cvd_sum = sum(cvd24.values())
    vol_sum = sum(vol24.values())
    mood = mood_of(longs, shorts, cvd_sum, vol_sum, price_rows, oi_rows)

    return {
        "window_h": int(round(window_sec / 3600)) or 24,
        "liq_total_usd": round(total, 2),
        "liq_count": len(rows),
        "longs_usd": round(longs, 2),
        "shorts_usd": round(shorts, 2),
        "max": biggest,
        "exchanges": exchanges,
        "coins": coins,
        "oi_top": oi_rows,
        "prices": price_rows,
        "mood": mood,
        "cvd_usd": round(cvd_sum, 2) if cvd24 else None,
        "vol_usd": round(vol_sum, 2) if vol_sum else None,
    }


def mood_of(longs: float, shorts: float, cvd_sum: float, vol_sum: float,
            prices: List[dict], oi_rows: List[dict]) -> dict:
    """Настроение рынка: счёт −1…+1 из цены, потока, ликвидаций и OI.

    Каждый компонент нормализован (tanh), поэтому выбросы не перевешивают:
        цена      — средний ход топ-5 монет;
        поток     — перевес покупок в тейкер-потоке (CVD / оборот);
        ликвидации— больше снесло шортов → рынок шёл вверх, и наоборот;
        OI        — подтверждение движения: растущий OI усиливает счёт.
    """
    import math

    def squash(x: float, scale: float) -> float:
        try:
            return math.tanh(float(x) / float(scale))
        except (TypeError, ValueError):
            return 0.0

    price_vals = [float(p["pct"]) for p in (prices or [])
                  if p.get("pct") is not None]
    price_score = squash(sum(price_vals) / len(price_vals), 3.0) if price_vals else 0.0
    cvd_share = (cvd_sum / vol_sum) if vol_sum else 0.0
    flow_score = squash(cvd_share, 0.08)
    liq_total = (longs or 0) + (shorts or 0)
    liq_skew = ((shorts - longs) / liq_total) if liq_total else 0.0
    liq_score = squash(liq_skew, 0.35)
    oi_vals = [float(o["pct"]) for o in (oi_rows or []) if o.get("pct") is not None]
    oi_mean = (sum(oi_vals) / len(oi_vals)) if oi_vals else 0.0
    oi_score = squash(oi_mean, 12.0) * (1.0 if price_score >= 0 else -1.0) * 0.5

    score = (0.35 * price_score + 0.30 * flow_score +
             0.22 * liq_score + 0.13 * oi_score)
    score = max(-1.0, min(1.0, score))

    if abs(score) < 0.12:
        kind = "flat"
    elif score > 0:
        kind = "bull" if score >= 0.35 else "bull_soft"
    else:
        kind = "bear" if score <= -0.35 else "bear_soft"

    reasons_ru: List[str] = []
    reasons_en: List[str] = []
    if price_vals:
        avg = sum(price_vals) / len(price_vals)
        reasons_ru.append(f"средний ход топ-5 {pct_txt(avg)} за сутки")
        reasons_en.append(f"the top-5 coins moved {pct_txt(avg, 'en')} on average")
    if vol_sum:
        if cvd_share > 0.01:
            reasons_ru.append("в тейкер-потоке перевес покупок")
            reasons_en.append("taker flow is dominated by buyers")
        elif cvd_share < -0.01:
            reasons_ru.append("в тейкер-потоке перевес продаж")
            reasons_en.append("taker flow is dominated by sellers")
        else:
            reasons_ru.append("тейкер-поток без перевеса")
            reasons_en.append("taker flow is balanced")
    if liq_total:
        if liq_skew > 0.08:
            reasons_ru.append(f"{liqs_word(2)} шортов больше — рынок выкупали")
            reasons_en.append("more shorts than longs were liquidated")
        elif liq_skew < -0.08:
            reasons_ru.append("снесли больше лонгов — продавцы давили")
            reasons_en.append("more longs than shorts were liquidated")
    if oi_vals:
        reasons_ru.append(f"открытый интерес в среднем {pct_txt(oi_mean)}")
        reasons_en.append(f"open interest {pct_txt(oi_mean, 'en')} on average")

    labels = {
        "bull": ("🟢 бычий настрой", "🟢 bullish"),
        "bull_soft": ("🟢 скорее бычий", "🟢 mildly bullish"),
        "flat": ("⚪ нейтральный", "⚪ neutral"),
        "bear_soft": ("🔴 скорее медвежий", "🔴 mildly bearish"),
        "bear": ("🔴 медвежий настрой", "🔴 bearish"),
    }
    ru, en = labels[kind]
    return {
        "score": round(score, 3),
        "kind": kind,
        "label": {"ru": ru, "en": en},
        "reasons": {"ru": reasons_ru, "en": reasons_en},
        "cvd_share": round(cvd_share, 5),
        "longs_share": round((longs / liq_total) if liq_total else 0.0, 4),
    }


# ---------------------------------------------------------------------------
#  Текст: живой рассказ (заглушка без ИИ и промпт для ИИ)
# ---------------------------------------------------------------------------
def fallback_narrative(facts: dict, lang: str = "ru") -> str:
    """Рассказ из данных — когда ИИ недоступен. Без выдумок, только цифры."""
    f = facts or {}
    en = is_en(lang)
    mood = (f.get("mood") or {})
    mood_txt = (mood.get("label") or {}).get("en" if en else "ru", "—")
    reasons = (mood.get("reasons") or {}).get("en" if en else "ru", [])
    total = money(f.get("liq_total_usd"))
    cnt = int(f.get("liq_count") or 0)
    longs = money(f.get("longs_usd"))
    shorts = money(f.get("shorts_usd"))
    mx = f.get("max") or {}
    parts: List[str] = []
    if en:
        parts.append(
            f"Over the last 24 hours the market wiped out {total} across "
            f"{cnt} liquidations: {longs} from longs and {shorts} from shorts.")
        if mx:
            parts.append(
                f"The biggest single hit was {money(mx.get('usd'))} on "
                f"{coin(mx.get('symbol'))} at {exch(mx.get('exchange'))} "
                f"({hour_hhmm(mx.get('timestamp'), tz_offset())}).")
    else:
        parts.append(
            f"За сутки рынок снёс {total} — это {cnt} {liqs_word(cnt, 'ru')}: "
            f"{longs} на лонгах и {shorts} на шортах.")
        if mx:
            parts.append(
                f"Самый крупный удар — {money(mx.get('usd'))} по "
                f"{coin(mx.get('symbol'))} на {exch(mx.get('exchange'))} "
                f"({hour_hhmm(mx.get('timestamp'), tz_offset())}).")
    oi_rows = f.get("oi_top") or []
    if oi_rows:
        first = oi_rows[0]
        ratio = first.get("ratio")
        rel = (f"×{ratio:.2f}" if isinstance(ratio, (int, float)) and ratio < 10
               else (f"{ratio:.0f}×" if isinstance(ratio, (int, float)) else "—"))
        if en:
            parts.append(
                f"Open interest is heaviest relative to volume in "
                f"{coin(first.get('symbol'))}: {money(first.get('oi_usd'))} "
                f"against {money(first.get('vol_usd'))} of daily turnover ({rel}).")
        else:
            parts.append(
                f"Открытый интерес относительно объёма самый тяжёлый в "
                f"{coin(first.get('symbol'))}: {money(first.get('oi_usd'))} "
                f"против {money(first.get('vol_usd'))} дневного оборота ({rel}).")
    price_rows = [p for p in (f.get("prices") or []) if p.get("pct") is not None]
    if price_rows:
        best = max(price_rows, key=lambda p: float(p["pct"]))
        worst = min(price_rows, key=lambda p: float(p["pct"]))
        if en:
            parts.append(
                f"Among the top coins {coin(best.get('symbol'))} held up best "
                f"({pct_txt(best.get('pct'), 'en')}), while "
                f"{coin(worst.get('symbol'))} was the weakest "
                f"({pct_txt(worst.get('pct'), 'en')}).")
        else:
            parts.append(
                f"Из топовых монет лучше всех держалась "
                f"{coin(best.get('symbol'))} ({pct_txt(best.get('pct'))}), "
                f"слабее всех — {coin(worst.get('symbol'))} "
                f"({pct_txt(worst.get('pct'))}).")
    tail = (f"Mood: {mood_txt}." if en else f"Настроение рынка — {mood_txt}.")
    if reasons:
        tail += " " + (", ".join(reasons[:3]).capitalize() + "." if en
                       else "Причины: " + ", ".join(reasons[:3]) + ".")
    parts.append(tail)
    return " ".join(parts)


def day_prompt(facts: dict, lang: str = "ru", variant: int = 0) -> str:
    """Промпт для ИИ: вечерний дайджест за сутки живым языком."""
    en = is_en(lang)
    f = facts or {}
    mx = f.get("max") or {}
    mood = (f.get("mood") or {})
    lines: List[str] = []
    if en:
        lines.append("Write tonight's crypto market digest for the day "
                     "(24 hours of liquidation data from our terminal).")
        lines.append("Facts to use (do not invent numbers):")
        lines.append(f"— liquidations: {money(f.get('liq_total_usd'))} in "
                     f"{int(f.get('liq_count') or 0)} events; longs "
                     f"{money(f.get('longs_usd'))}, shorts {money(f.get('shorts_usd'))}")
        if mx:
            lines.append(f"— biggest single liquidation: {money(mx.get('usd'))} "
                         f"{coin(mx.get('symbol'))} on {exch(mx.get('exchange'))} "
                         f"at {hour_hhmm(mx.get('timestamp'), tz_offset())}")
        ex = f.get("exchanges") or []
        if ex:
            lines.append("— by exchange: " + "; ".join(
                f"{exch(e.get('name'))} {money(e.get('usd'))} "
                f"({int(e.get('count') or 0)} events)" for e in ex[:4]))
        oi = f.get("oi_top") or []
        if oi:
            lines.append("— open interest vs daily turnover: " + "; ".join(
                f"{coin(o.get('symbol'))} OI {money(o.get('oi_usd'))} vs "
                f"{money(o.get('vol_usd'))} turnover"
                + (f" ({pct_txt(o.get('pct'), 'en')} per day)"
                   if o.get("pct") is not None else "")
                for o in oi[:4]))
        pr = [p for p in (f.get("prices") or []) if p.get("pct") is not None]
        if pr:
            lines.append("— price change (24h): " + "; ".join(
                f"{coin(p.get('symbol'))} {pct_txt(p.get('pct'), 'en')}"
                for p in pr))
        lines.append(f"— market mood from the data: "
                     f"{(mood.get('label') or {}).get('en', '')} "
                     f"(score {mood.get('score')}); reasons: "
                     + ", ".join((mood.get("reasons") or {}).get("en", [])[:4]))
        lines.append("")
        lines.append("Write 3-5 short paragraphs (900-1600 characters) in a live "
                     "human voice: an analyst who watched the tape all day. "
                     "Lead with the biggest liquidation and what it means, then "
                     "the exchanges, then open interest vs turnover, then prices "
                     "and the overall mood. Plain text only: no headings, no "
                     "markdown, no bullet lists, no hashtags, no links, no "
                     "emojis at the start of sentences.")
    else:
        lines.append("Напиши вечерний дайджест рынка криптовалют за сутки "
                     "(24 часа данных терминала ликвидаций).")
        lines.append("Факты — только они, ничего не выдумывай:")
        lines.append(f"— ликвидации: {money(f.get('liq_total_usd'))} за "
                     f"{int(f.get('liq_count') or 0)} {liqs_word(int(f.get('liq_count') or 0))}; "
                     f"лонги {money(f.get('longs_usd'))}, шорты {money(f.get('shorts_usd'))}")
        if mx:
            lines.append(f"— крупнейшая ликвидация: {money(mx.get('usd'))} "
                         f"{coin(mx.get('symbol'))} на {exch(mx.get('exchange'))} "
                         f"в {hour_hhmm(mx.get('timestamp'), tz_offset())}")
        ex = f.get("exchanges") or []
        if ex:
            lines.append("— по биржам: " + "; ".join(
                f"{exch(e.get('name'))} {money(e.get('usd'))} "
                f"({int(e.get('count') or 0)} шт)" for e in ex[:4]))
        oi = f.get("oi_top") or []
        if oi:
            lines.append("— открытый интерес против дневного оборота: " + "; ".join(
                f"{coin(o.get('symbol'))} OI {money(o.get('oi_usd'))} против "
                f"{money(o.get('vol_usd'))} оборота"
                + (f" ({pct_txt(o.get('pct'))} за сутки)"
                   if o.get("pct") is not None else "")
                for o in oi[:4]))
        pr = [p for p in (f.get("prices") or []) if p.get("pct") is not None]
        if pr:
            lines.append("— изменение цены за сутки: " + "; ".join(
                f"{coin(p.get('symbol'))} {pct_txt(p.get('pct'))}" for p in pr))
        lines.append(f"— настроение рынка по данным: "
                     f"{(mood.get('label') or {}).get('ru', '')} "
                     f"(счёт {mood.get('score')}); причины: "
                     + ", ".join((mood.get("reasons") or {}).get("ru", [])[:4]))
        lines.append("")
        lines.append("Напиши 3–5 коротких абзацев (900–1600 знаков) живым "
                     "человеческим языком: аналитик, который весь день смотрел "
                     "ленту. Начни с крупнейшей ликвидации и что она значит, "
                     "потом биржи, потом открытый интерес против оборота, потом "
                     "цены и общее настроение. Только обычный текст: без "
                     "заголовков, без markdown, без списков, без хештегов, без "
                     "ссылок, эмодзи — не в начале предложений.")
    if en:
        angles = ("", " Keep it calm and matter-of-fact, like an evening column.",
                  " Be a bit more emotional: a hot day, but no panic.",
                  " Keep it dry and precise: numbers over adjectives.")
    else:
        angles = ("", " Пиши спокойно и по делу, как вечерняя колонка аналитика.",
                  " Пиши чуть эмоциональнее: день был горячим, но без паники.",
                  " Пиши сухо и точно, цифры важнее эпитетов.")
    return "\n".join(lines) + angles[int(variant) % len(angles)]


# ---------------------------------------------------------------------------
#  Пост в канал
# ---------------------------------------------------------------------------
def headline_block(facts: dict, lang: str = "ru") -> str:
    """Шапка дайджеста: максимум дня крупно и общий итог."""
    f = facts or {}
    en = is_en(lang)
    mx = f.get("max") or {}
    total = money(f.get("liq_total_usd"))
    cnt = int(f.get("liq_count") or 0)
    rows: List[str] = []
    if en:
        rows.append("💥 <b>Biggest liquidation of the day</b>")
        if mx:
            rows.append(
                f"{money(mx.get('usd'))} · {coin(mx.get('symbol'))} · "
                f"{exch(mx.get('exchange'))} · "
                f"{'long' if mx.get('side') == 'SELL' else 'short'} · "
                f"{hour_hhmm(mx.get('timestamp'), tz_offset())}")
        else:
            rows.append("no single large liquidation")
        rows.append("")
        rows.append(f"📉 <b>Total: {total} · {cnt} liquidations</b>")
        rows.append(f"longs {money(f.get('longs_usd'))} · "
                    f"shorts {money(f.get('shorts_usd'))}")
    else:
        rows.append("💥 <b>Крупнейшая ликвидация дня</b>")
        if mx:
            side = "лонг" if mx.get("side") == "SELL" else "шорт"
            rows.append(
                f"{money(mx.get('usd'))} · {coin(mx.get('symbol'))} · "
                f"{exch(mx.get('exchange'))} · {side} · "
                f"{hour_hhmm(mx.get('timestamp'), tz_offset())}")
        else:
            rows.append("крупных одиночных ликвидаций не было")
        rows.append("")
        rows.append(f"📉 <b>Всего: {total} · {cnt} "
                    f"{liqs_word(cnt, 'ru')}</b>")
        rows.append(f"лонги {money(f.get('longs_usd'))} · "
                    f"шорты {money(f.get('shorts_usd'))}")
    return "\n".join(rows)


def exchanges_block(facts: dict, lang: str = "ru", limit: int = 5) -> str:
    en = is_en(lang)
    rows = (facts or {}).get("exchanges") or []
    if not rows:
        return ""
    medals = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣")
    out = ["🏦 <b>Биржи · кто и сколько</b>" if not en
           else "🏦 <b>Exchanges</b>"]
    for i, e in enumerate(rows[:limit]):
        mark = medals[i] if i < len(medals) else "•"
        cnt = int(e.get("count") or 0)
        word = f"{cnt} {liqs_word(cnt, lang)}"
        out.append(f"{mark} {exch(e.get('name'))} — {money(e.get('usd'))} · {word}")
    return "\n".join(out)


def oi_block(facts: dict, lang: str = "ru", limit: int = 5) -> str:
    """Открытый интерес относительно объёма: OI против дневного оборота."""
    en = is_en(lang)
    rows = (facts or {}).get("oi_top") or []
    if not rows:
        return ""
    out = ["📈 <b>Open interest vs turnover</b>" if en
           else "📈 <b>Открытый интерес против оборота</b>"]
    for i, o in enumerate(rows[:limit], 1):
        ratio = o.get("ratio")
        if isinstance(ratio, (int, float)) and ratio > 0:
            rel = (f"{ratio:.2f}×" if ratio < 10 else f"{ratio:.0f}×")
        else:
            rel = "—"
        pct = o.get("pct")
        tail = f" · {arrow(pct)} {pct_txt(pct, lang)}" if pct is not None else ""
        vol_word = "turnover" if en else "оборот"
        out.append(f"{i}. <b>{coin(o.get('symbol'))}</b> — "
                   f"OI {money(o.get('oi_usd'))} · {money(o.get('vol_usd'))} "
                   f"{vol_word} ({rel}){tail}")
    return "\n".join(out)


def prices_block(facts: dict, lang: str = "ru", limit: int = 5) -> str:
    en = is_en(lang)
    rows = (facts or {}).get("prices") or []
    if not rows:
        return ""
    out = ["💱 <b>Top coins · 24h price</b>" if en
           else "💱 <b>Топовые монеты · цена за сутки</b>"]
    for p in rows[:limit]:
        pv = price_txt(p.get("price"))
        if p.get("pct") is None:
            out.append(f"• <b>{coin(p.get('symbol'))}</b> — {pv}")
        else:
            out.append(f"{arrow(p.get('pct'))} <b>{coin(p.get('symbol'))}</b> — "
                       f"{pv} · {pct_txt(p.get('pct'), lang)}")
    return "\n".join(out)


def mood_block(facts: dict, lang: str = "ru") -> str:
    en = is_en(lang)
    mood = (facts or {}).get("mood") or {}
    label = (mood.get("label") or {}).get("en" if en else "ru", "—")
    reasons = (mood.get("reasons") or {}).get("en" if en else "ru", [])
    out = [f"🧭 <b>{'Market mood' if en else 'Настроение рынка'}: {label}</b>"
           f" <i>({mood.get('score')})</i>"]
    if reasons:
        out.append(("· " + ", ".join(reasons[:4]) + ".") if en
                   else ("Причины: " + ", ".join(reasons[:4]) + "."))
    return "\n".join(out)


def render_post(rec: dict, lang: str = "ru", site_url: str = "") -> str:
    """Пост в канал: живой рассказ + сухие блоки с фактами."""
    en = is_en(lang)
    facts = (rec or {}).get("facts") or {}
    day = (rec or {}).get("day") or ""
    narrative = ((rec or {}).get("ai") or {}).get("en" if en else "ru") or ""
    if len(str(narrative)) < NARRATIVE_MIN:
        narrative = fallback_narrative(facts, lang)
    head = (f"🧭 <b>{'Daily digest' if en else 'Дневной дайджест'} · "
            f"{day_label(day, lang)}</b>")
    blocks = [head, "", str(narrative).strip(), "",
              headline_block(facts, lang), "",
              exchanges_block(facts, lang), "",
              oi_block(facts, lang), "",
              prices_block(facts, lang), "",
              mood_block(facts, lang)]
    text = "\n".join([b for b in blocks if b is not None]).strip()
    text = _squeeze(text, TEXT_LIMIT)
    link = str(site_url or "").strip()
    if link.startswith("http"):
        text += (f"\n\n🌐 <a href=\"{link.rstrip('/')}/digest\">"
                 f"{'Full digest on the site' if en else 'Все дайджесты на сайте'}</a>")
    return text


def _squeeze(text: str, limit: int) -> str:
    """Подрезаем хвост по границам строк, чтобы сообщение точно ушло."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    i = cut.rfind("\n")
    return (cut[:i].rstrip() if i > 0 else cut.rstrip()) + " …"


# ---------------------------------------------------------------------------
#  Страница сайта
# ---------------------------------------------------------------------------
def _esc(text: Any) -> str:
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_article(rec: dict, lang: str = "ru") -> str:
    """HTML статьи для сайта (без Telegram-разметки)."""
    en = is_en(lang)
    facts = (rec or {}).get("facts") or {}
    narrative = ((rec or {}).get("ai") or {}).get("en" if en else "ru") or ""
    if len(str(narrative)) < NARRATIVE_MIN:
        narrative = fallback_narrative(facts, lang)
    parts: List[str] = []
    for para in str(narrative).split("\n"):
        para = para.strip()
        if para:
            parts.append(f"<p>{_esc(para)}</p>")
    mx = facts.get("max") or {}
    parts.append("<h3>💥 " + ("Biggest liquidation" if en else "Крупнейшая ликвидация")
                 + "</h3>")
    if mx:
        side = ("long" if mx.get("side") == "SELL" else "short") if en \
            else ("лонг" if mx.get("side") == "SELL" else "шорт")
        parts.append(
            f"<p><b>{money(mx.get('usd'))}</b> · {_esc(coin(mx.get('symbol')))} · "
            f"{_esc(exch(mx.get('exchange')))} · {side} · "
            f"{_esc(hour_hhmm(mx.get('timestamp'), tz_offset()))}</p>")
    else:
        parts.append("<p>—</p>")
    parts.append(
        f"<p>{'Total' if en else 'Всего за сутки'}: <b>{money(facts.get('liq_total_usd'))}</b>"
        f" · {int(facts.get('liq_count') or 0)} "
        f"{liqs_word(int(facts.get('liq_count') or 0), lang)} · "
        f"{'longs' if en else 'лонги'} {money(facts.get('longs_usd'))} · "
        f"{'shorts' if en else 'шорты'} {money(facts.get('shorts_usd'))}</p>")

    ex = facts.get("exchanges") or []
    if ex:
        parts.append("<h3>🏦 " + ("Exchanges" if en else "Биржи") + "</h3>")
        parts.append("<ul>" + "".join(
            f"<li><b>{_esc(exch(e.get('name')))}</b> — {money(e.get('usd'))} · "
            f"{int(e.get('count') or 0)} "
            f"{liqs_word(int(e.get('count') or 0), lang)}</li>" for e in ex) + "</ul>")
    oi = facts.get("oi_top") or []
    if oi:
        parts.append("<h3>📈 " + ("Open interest vs turnover" if en
                                  else "Открытый интерес против оборота") + "</h3>")
        rows = []
        for o in oi:
            ratio = o.get("ratio")
            rel = (f"×{ratio:.2f}" if isinstance(ratio, (int, float)) and ratio < 10
                   else (f"×{ratio:.0f}" if isinstance(ratio, (int, float)) else "—"))
            pct = o.get("pct")
            tail = f" · {pct_txt(pct, lang)}" if pct is not None else ""
            rows.append(
                f"<li><b>{_esc(coin(o.get('symbol')))}</b> — OI "
                f"{money(o.get('oi_usd'))} · {money(o.get('vol_usd'))} "
                f"{'turnover' if en else 'оборот'} ({rel}){tail}</li>")
        parts.append("<ul>" + "".join(rows) + "</ul>")
    pr = facts.get("prices") or []
    if pr:
        parts.append("<h3>💱 " + ("Top coins · 24h" if en
                                  else "Топовые монеты · сутки") + "</h3>")
        rows = []
        for p in pr:
            pv = price_txt(p.get("price"))
            pct = p.get("pct")
            tail = (f" · {arrow(pct)} {pct_txt(pct, lang)}"
                    if pct is not None else "")
            rows.append(f"<li><b>{_esc(coin(p.get('symbol')))}</b> — {pv}{tail}</li>")
        parts.append("<ul>" + "".join(rows) + "</ul>")
    mood = facts.get("mood") or {}
    parts.append("<h3>🧭 " + ("Market mood" if en else "Настроение рынка") + "</h3>")
    label = (mood.get("label") or {}).get("en" if en else "ru", "—")
    reasons = (mood.get("reasons") or {}).get("en" if en else "ru", [])
    parts.append(f"<p><b>{_esc(label)}</b> <i>({_esc(mood.get('score'))})</i>. "
                 + _esc(", ".join(reasons[:4])) + ".</p>")
    return "\n".join(parts)


def brief(rec: dict, lang: str = "ru") -> str:
    """Одна строка для списка архива: дата, суммы, настроение."""
    facts = (rec or {}).get("facts") or {}
    mood = (facts.get("mood") or {})
    label = (mood.get("label") or {}).get("en" if is_en(lang) else "ru", "")
    return (f"{money(facts.get('liq_total_usd'))} · "
            f"{int(facts.get('liq_count') or 0)} "
            f"{liqs_word(int(facts.get('liq_count') or 0), lang)} · {label}")


# ---------------------------------------------------------------------------
#  Архив дайджестов
# ---------------------------------------------------------------------------
class DigestStore:
    """Хранилище дайджестов: JSON-файл, свежие первыми.

    Пишем атомарно (tmp + rename): сервер может перезапуститься в момент
    записи, и архив не должен остаться битым.
    """

    def __init__(self, path: str = "", keep: int = DEFAULT_KEEP):
        self.path = str(path or "")
        self.keep = max(1, int(keep or DEFAULT_KEEP))
        self.items: List[dict] = []
        self.error = ""
        self.load()

    # --- чтение/запись -----------------------------------------------------
    def load(self) -> List[dict]:
        self.items = []
        self.error = ""
        if not self.path or not os.path.exists(self.path):
            return self.items
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data = data.get("items") or []
            self.items = [x for x in data if isinstance(x, dict)]
        except Exception as e:                      # битый файл не должен мешать
            self.error = f"{type(e).__name__}: {e}"
            self.items = []
        return self.items

    def _flush(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"items": self.items}, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"

    # --- операции ----------------------------------------------------------
    def save(self, rec: dict) -> dict:
        """Добавить дайджест (по id = дата заменяем, а не дублируем)."""
        rec = dict(rec or {})
        rid = str(rec.get("id") or rec.get("day") or "")
        if not rid:
            raise ValueError("у дайджеста нет id")
        rec["id"] = rid
        self.items = [x for x in self.items if str(x.get("id")) != rid]
        self.items.insert(0, rec)
        self.items = self.items[:self.keep]
        self._flush()
        return rec

    def get(self, rid: str) -> Optional[dict]:
        for x in self.items:
            if str(x.get("id")) == str(rid):
                return x
        return None

    def list(self) -> List[dict]:
        return list(self.items)

    def mark_published(self, rid: str, lang: str, chat: Any = None,
                       ok: bool = True) -> Optional[dict]:
        rec = self.get(rid)
        if rec is None:
            return None
        pub = dict(rec.get("published") or {})
        pub["en" if is_en(lang) else "ru"] = {
            "ok": bool(ok), "at": time.time(),
            "chat": str(chat or ""),
        }
        rec["published"] = pub
        self._flush()
        return rec
