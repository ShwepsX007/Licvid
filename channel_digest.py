"""Сводки в Telegram-канал: лидеры ликвидаций, OI, CVD.

Чистые функции — без сети. Бот только публикует готовый текст + картинку.
Один пост = одно сообщение (подпись к фото ≤ 1024).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from hour_board import hour_hhmm, tz_offset
from refs import ex_link, gate_line

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(HERE, "static", "channel")

WINDOW_SEC = 4 * 3600
CAPTION_LIMIT = 1024

# Шаблоны шапок. {h} — окно в часах. Админ может удалить/добавить свои в БД.
DEFAULT_HEAD_TEMPLATES = (
    "🌙 Ночная смена на ленте. {h} часа — и рынок снова кого-то съел.",
    "🔥 Разбор полётов за {h}ч. Без розовых очков, как есть.",
    "👁 Кто кормил ленту последние {h} часа. Спойлер: не джедаи.",
    "💥 {h} часа огня. Коротко, по фактам, с характером.",
    "📓 Дневник терминала. Окно {h}ч, настроение рабочее.",
    "🪖 Сводка с передовой. Ликвидации за {h} часа — ниже.",
    "☕ Пока вы пили кофе, лента уже намолотила кассу.",
    "🛠 Зашёл проверить одну свечу. Ушёл со сводкой за {h}ч.",
    "😬 Опять кто-то забыл, что такое стоп. Разбор за {h} часа.",
    "🧪 Это не сигнал. Это рентген рынка за {h}ч.",
    "🗣 Пишу как есть: {h} часа, и плечи снова короче, чем казались.",
    "🩸 Касса боли за {h} часа. Кто виноват — тоже написал.",
    "🎬 Живой эфир с ленты. Без монтажа и без жалости.",
    "🧊 Тихий час? Нет. Просто резали не тех.",
    "📌 Вечерняя планёрка с лентой: кто кого вынес за {h}ч.",
    "⚠️ Если плечо было «чуть-чуть» — вот счёт за {h} часа.",
    "🧠 Рынок не обязан быть вежливым. За {h}ч это видно сразу.",
    "📉 Не паника. Просто цифры, которые не умеют врать.",
    "🃏 Короткий разбор. Длинные плечи сегодня плохо жили.",
    "📡 Лента не молчала. Я тоже не буду.",
    "🧹 Это не «коррекция». Это кто-то кормил стакан {h} часа.",
    "📸 Снял слепок рынка. Держитесь за стул.",
    "🕐 Утро начинается не с кофе. С трупов на графике.",
    "💬 Смотрел ленту {h} часа. Кому досталось — в цифрах ниже.",
)
VARIANT_COUNT = len(DEFAULT_HEAD_TEMPLATES)
HEAD_MAX_LEN = 240

# Английские шапки: тот же состав данных, другой язык (для второго канала).
DEFAULT_HEAD_TEMPLATES_EN = (
    "🌙 Night shift on the tape. {h} hours — the market ate someone again.",
    "🔥 {h}-hour post-mortem. No sugar-coating, just the numbers.",
    "👁 Who fed the tape for the last {h} hours. Spoiler: not the jedi.",
    "💥 {h} hours of fire. Short, factual, with character.",
    "📓 Terminal diary. Window: {h}h. Mood: working.",
    "🪖 Field report from the front line. Liquidations for {h}h below.",
    "☕ While you were having coffee, the tape was printing money.",
    "🛠 Came to check one candle. Left with a {h}h recap.",
    "😬 Someone forgot what a stop is. The {h}-hour breakdown.",
    "🧪 Not a signal. An X-ray of the market for {h}h.",
    "🗣 Straight talk: {h} hours, and leverage got shorter than it looked.",
    "🩸 The pain bill for {h} hours. With the names attached.",
    "🎬 Live from the tape. No editing, no mercy.",
    "🧊 Quiet hour? No. They just cut the wrong people.",
    "📌 Evening stand-up with the tape: who got wrecked in {h}h.",
    "⚠️ If your leverage was 'just a bit' — here is the {h}-hour invoice.",
    "🧠 The market owes you nothing. {h}h make that obvious.",
    "📉 Not panic. Just numbers that cannot lie.",
    "🃏 Short recap. Long leverage did not live long today.",
    "📡 The tape never went quiet. Neither will I.",
    "🧹 This is not a 'correction'. Someone fed the book for {h} hours.",
    "📸 Took a snapshot of the market. Hold on to your chair.",
    "🕐 Morning does not start with coffee. It starts with wreckage.",
    "💬 Watched the tape for {h} hours. Who paid — in numbers below.",
)

# Подписи блоков и стенда: ru — основной канал, en — второй.
LABELS = {
    "ru": {
        "exchanges": "Биржи",
        "coins": "Монеты",
        "no_leaders": "лидеров нет",
        "longs": "лонги",
        "shorts": "шорты",
        "pieces": "шт.",
        "whale": "кит",
        "oi": "OI",
        "cvd_buy": "покупки",
        "cvd_sell": "продажи",
        "window": "окно {h}ч",
        "bias_flat": "лента почти молчала",
        "bias_long": "резали лонги",
        "bias_short": "выносили шорты",
        "bias_both": "били с обеих сторон",
        "stand": "СТЕНД",
        "col_hour": "час",
        "col_liqs": "ликв.",
        "col_coins": "монеты",
        "col_bias": "перекос",
        "stand_hours": "{h} часа",
        "msk": "МСК",
        "total": "Всего",
        "liqs": "ликвидаций",
        "liq1": "ликвидация",
        "liq2": "ликвидации",
        "oi_4h": "OI за 4ч",
        "vs_prev": "к прошлым 4ч",
        "no_data": "—",
        "top_title": "крупнейшие за час",
        "top_hours": "Топ-7 по часам",
        "empty_hour": "тихо",
    },
    "en": {
        "exchanges": "Exchanges",
        "coins": "Coins",
        "no_leaders": "no leaders",
        "longs": "longs",
        "shorts": "shorts",
        "pieces": "fills",
        "whale": "whale",
        "oi": "OI",
        "cvd_buy": "buying",
        "cvd_sell": "selling",
        "window": "{h}h window",
        "bias_flat": "the tape was almost silent",
        "bias_long": "longs were cut",
        "bias_short": "shorts were wrecked",
        "bias_both": "both sides got hit",
        "stand": "BOARD",
        "col_hour": "hour",
        "col_liqs": "liqs",
        "col_coins": "coins",
        "col_bias": "bias",
        "stand_hours": "last {h} hours",
        "msk": "UTC+3",
        "total": "Total",
        "liqs": "liquidations",
        "liq1": "fill",
        "liq2": "fills",
        "oi_4h": "OI over 4h",
        "vs_prev": "vs previous 4h",
        "no_data": "—",
        "top_title": "biggest of the hour",
        "top_hours": "Top 7 by hour",
        "empty_hour": "quiet",
    },
}


def liqs_word(n: int, lang: str = "ru") -> str:
    """«1 ликвидация», «2 ликвидации», «5 ликвидаций» — без «40 ликвидаций»."""
    n = abs(int(n or 0))
    if str(lang).startswith("en"):
        return lbl(lang, "liq1") if n == 1 else lbl(lang, "liq2")
    if n % 10 == 1 and n % 100 != 11:
        return lbl(lang, "liq1")
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return lbl(lang, "liq2")
    return lbl(lang, "liqs")


def lbl(lang: str, key: str, **kw) -> str:
    """Подпись на нужном языке; неизвестный язык — русский."""
    table = LABELS.get("en" if str(lang).startswith("en") else "ru") or {}
    text = table.get(key) or LABELS["ru"].get(key) or key
    return text.format(**kw) if kw else text

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

_MEDALS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣")


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


def exch_ref(name: Any, lang: str = "ru") -> str:
    """Имя биржи в обычном тексте: с партнёрской ссылкой, если она есть.

    Внутри <pre>/<code> ссылок не бывает, поэтому таблицы остаются текстом,
    а строки вне таблиц ведут на биржу.
    """
    return ex_link(str(name or ""), fallback=exch(name), lang=lang)


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


def _bias(longs: float, shorts: float, lang: str = "ru") -> tuple:
    tot = longs + shorts
    if tot <= 0:
        return "😴", lbl(lang, "bias_flat")
    if longs > shorts * 1.25:
        return "📉", lbl(lang, "bias_long")
    if shorts > longs * 1.25:
        return "📈", lbl(lang, "col_bias")
    return "⚖️", lbl(lang, "bias_both")


def _side_dot(longs: float, shorts: float) -> str:
    return "🔴" if longs >= shorts else "🟢"


def short_money(v: Any) -> str:
    """Сумма покороче: $899.4K -> $899K, $1.25M -> $1.3M.

    В подписи к фото всего 1024 символа, а топ-7 по четырём часам — это
    28 строк: лишний знак в каждой строке съедает строку целиком.
    """
    try:
        x = abs(float(v or 0))
    except (TypeError, ValueError):
        return money(v)
    sgn = "-" if float(v or 0) < 0 else ""
    if x >= 1e9:
        return f"{sgn}${x / 1e9:.1f}B".replace(".0B", "B")
    if x >= 1e6:
        return f"{sgn}${x / 1e6:.1f}M".replace(".0M", "M")
    if x >= 1e3:
        return f"{sgn}${x / 1e3:.0f}K"
    return f"{sgn}${x:.0f}"


def _dwidth(text: str) -> int:
    """Ширина строки в знакоместах: эмодзи и иероглифы занимают два.

    Столбики в <pre> выравниваются по знакоместам, а len() считает эмодзи
    за один символ — из-за этого правые числа «съезжали» на строку и цифры
    визуально наезжали друг на друга. Считаем как считает шрифт.
    """
    import unicodedata
    w = 0
    for ch in text or "":
        if ch in ("\ufe0f", "\u200d", "\u20e3"):
            continue
        o = ord(ch)
        if o >= 0x1F000 or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF:
            w += 2
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _dwidth(text))


def _mono(rows: List[List[tuple]], gap: str = "  ") -> str:
    """Моноширинный блок «в два столбика»: ячейка — (текст, "l"|"r").

    Telegram выравнивает столбики только в <pre>/<code>, поэтому таблицы
    (биржи, монеты) уходят именно так: слева имя, справа цифры. Пустые
    строки и пустые ячейки не печатаем — подпись к фото ограничена 1024.
    """
    rows = [r for r in rows if r and any((c[0] or "").strip() for c in r)]
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    width = [0] * ncol
    for r in rows:
        for i, (txt, _a) in enumerate(r):
            width[i] = max(width[i], _dwidth(txt))
    lines = []
    for r in rows:
        cells = []
        for i, (txt, align) in enumerate(r):
            if align == "r":
                cells.append(" " * max(0, width[i] - _dwidth(txt)) + txt)
            else:
                cells.append(_pad(txt, width[i]))
        lines.append(gap.join(cells).rstrip())
    body = "\n".join(lines)
    body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre><code>{body}</code></pre>"


def _kpi_cells(snap: dict, lang: str = "ru") -> List[str]:
    """Правая колонка таблицы бирж: касса, лонги, шорты, кит.

    Строк ровно четыре — по числу бирж в компактной таблице: каждая
    цифра окна стоит напротив своей биржи, а не висит отдельным абзацем.
    """
    b = snap.get("biggest") or {}
    cells = [
        f"💥 {money(snap.get('total_usd'))} · {int(snap.get('count') or 0)} {lbl(lang, 'pieces')}",
        f"🔴 {lbl(lang, 'longs')} {money(snap.get('longs_usd'))}",
        f"🟢 {lbl(lang, 'shorts')} {money(snap.get('shorts_usd'))}",
    ]
    if b:
        mark = "🔴" if b.get("side") == "SELL" else "🟢"
        cells.append(f"🐋 {coin(b.get('symbol'))} {money(b.get('usd'))}"
                     f" · {exch_ref(b.get('exchange'), lang)} {mark}")
    return cells


def _kpi_line(snap: dict, lang: str = "ru") -> str:
    """Одна строка вместо таблицы — для вариантов, где биржи не первыми."""
    return (f"💥 {money(snap.get('total_usd'))} · {int(snap.get('count') or 0)} {lbl(lang, 'pieces')}"
            f"   🔴 {money(snap.get('longs_usd'))}"
            f"   🟢 {money(snap.get('shorts_usd'))}")


def _ex_block(snap: dict, n: int = 4, lang: str = "ru") -> str:
    """Биржи слева, цифры окна справа — два столбика, как в терминале."""
    items = list((snap.get("exchanges") or {}).items())[:n]
    if not items:
        return ""
    right = _kpi_cells(snap, lang)
    rows = []
    for i, (k, v) in enumerate(items):
        rows.append([
            (f"• {exch(k)}", "l"),
            (money(v), "r"),
            (right[i] if i < len(right) else "", "l"),
        ])
    return _mono(rows)


def _metric_cell(snap: dict, sym: Any, lang: str = "ru") -> str:
    """Правая колонка монеты — одной короткой строкой: OI или CVD.

    Показываем тот индикатор, который в этой монете сильнее по деньгам:
    движение открытого интереса или перевес тейкер-дельты. Так строка
    остаётся компактной (в терминале они живут в разных окнах).
    """
    payload = (snap.get("oi") or {}).get(sym) or {}
    ch = (payload.get("changes") or {}).get("h4") or {}
    oi_txt, oi_abs = "", None
    if ch.get("usd") is not None:
        try:
            n = float(ch.get("usd"))
        except (TypeError, ValueError):
            n = None
        if n is not None:
            pct = ch.get("pct")
            extra = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
            oi_txt = f"📊 {lbl(lang, 'oi')} {'↑' if n >= 0 else '↓'}{money(abs(n))}{extra}"
            oi_abs = abs(n)
    cvd_txt, cvd_abs = "", None
    cvd = (snap.get("cvd") or {}).get(sym)
    if cvd is not None:
        try:
            v = float(cvd)
        except (TypeError, ValueError):
            v = None
        if v is not None:
            side_txt = lbl(lang, "cvd_buy" if v >= 0 else "cvd_sell")
            cvd_txt = (f"🌊 CVD {'🟢' if v >= 0 else '🔴'} "
                       f"{side_txt} {money(abs(v))}")
            cvd_abs = abs(v)
    if oi_txt and cvd_txt:
        return oi_txt if oi_abs >= cvd_abs else cvd_txt
    if oi_txt:
        return oi_txt
    if cvd_txt:
        return cvd_txt
    tot = payload.get("total_usd")
    if tot:
        return f"📊 {lbl(lang, 'oi')} {money(tot)}"
    return ""


def _coins_block(snap: dict, n: int = 5, header: str = "", lang: str = "ru") -> str:
    """Монеты-лидеры: слева ранг/монета/сумма, справа метрика (OI или CVD)."""
    coins = list(snap.get("top_coins") or [])[:n]
    if not coins:
        # монет нет — блок просто не печатаем: стенд выше уже всё сказал
        return ""
    rows: List[List[tuple]] = [[(header or lbl(lang, "coins"), "l")]]
    for i, c in enumerate(coins):
        mark = _MEDALS[i] if i < len(_MEDALS) else f"{i + 1}."
        try:
            longs = float(c.get("longs") or 0)
            shorts = float(c.get("shorts") or 0)
        except (TypeError, ValueError):
            longs = shorts = 0.0
        rows.append([
            (f"{mark} {coin(c.get('symbol'))}", "l"),
            (money(c.get("usd")), "r"),
            (_side_dot(longs, shorts), "l"),
            (_metric_cell(snap, c.get("symbol"), lang), "l"),
        ])
    return _mono(rows)


def _arrow(pct, lang: str = "ru") -> str:
    """Стрелка и процент: рост — ▲, падение — ▼, ровно — без шума."""
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return ""
    if v > 0.05:
        return f"▲ {abs(v):.1f}%"
    if v < -0.05:
        return f"▼ {abs(v):.1f}%"
    return "→ 0.0%"


def _coin_cell(c: dict, lang: str = "ru") -> str:
    """Монета за час: сумма ликвидаций и перекос CVD по этой же монете."""
    if not c.get("symbol"):
        return ""
    text = f"{coin(c['symbol'])} {money(c.get('usd'))}"
    flow = c.get("flow")
    if flow is not None:
        text += " 🟢" if float(flow) >= 0 else " 🔴"
    return text


def hour_rows(hours: list, lang: str = "ru", with_oi: bool = True) -> List[List[tuple]]:
    """Строки стенда: час, ликвидации, топ монет часа, перекос CVD и OI.

    ``with_oi=False`` — история открытого интереса ещё не набралась: колонку
    не рисуем вовсе, чтобы в посте не было столбца из одних прочерков.
    """
    rows: List[List[tuple]] = []
    for hr in hours or []:
        hh = hour_hhmm(hr.get("h") or 0, tz_offset())
        total = float(hr.get("total") or 0)
        cnt = int(hr.get("count") or 0)
        if not total and not cnt:
            row = [(hh, "l"), (lbl(lang, "empty_hour"), "l"), ("", "l"), ("", "l")]
            if with_oi:
                row.append(("", "l"))
            rows.append(row)
            continue
        left = f"{money(total)} · {cnt} {lbl(lang, 'pieces')}"
        coins = (hr.get("coins") or [])
        top_coins = " ".join((_coin_cell(c, lang) for c in coins[:3] if c.get("symbol")))
        bias = hr.get("bias")
        bias_txt = ""
        if bias in ("long", "short"):
            side = lbl(lang, "longs" if bias == "long" else "shorts")
            bias_txt = f"{'🔴' if bias == 'long' else '🟢'} {side} {money(hr.get('side_sum'))}"
        cvd_sum = hr.get("cvd_sum")
        if cvd_sum is not None:
            flow = "🟢" if float(cvd_sum) >= 0 else "🔴"
            bias_txt = (bias_txt + " · " if bias_txt else "") + f"{flow} CVD {money(abs(float(cvd_sum)))}"
        oi_txt = ""
        oi = hr.get("oi") or {}
        if oi.get("value"):
            oi_txt = f"{money(oi['value'])}"
            if oi.get("pct") is not None:
                oi_txt = f"{oi_txt} {_arrow(oi['pct'], lang)}"
        row = [(hh, "l"), (left, "l"), (top_coins, "l"), (bias_txt, "l")]
        if with_oi:
            row.append((oi_txt, "l"))
        rows.append(row)
    return rows


def board_block(board: Optional[dict], lang: str = "ru") -> str:
    """«Информационный стенд» таблицей: часы, тотал, CVD и OI.

    Запасной вид (в пост идёт компактная раскладка с топ-7): таблицу удобно
    показать целиком, когда места хватает — например, в отдельном сообщении.

    Порядок важен: сначала общая касса за окно, потом по часам — что и на
    чём горело, затем крупнейшие удары часа и, наконец, открытый интерес
    за каждый час и за все четыре.
    """
    if not board:
        return ""
    hours = board.get("hours") or []
    if not hours:
        return ""
    span = board.get("span_hours") or len(hours)
    mark = "⚖️" if abs(float(board.get("diff_pct") or 0)) <= 0.5 else (
        "📈" if float(board.get("diff_pct") or 0) > 0 else "📉")
    parts: List[str] = []
    head = (f"<b>📊 {lbl(lang, 'stand')} · {lbl(lang, 'stand_hours', h=span)}"
            f" ({lbl(lang, 'msk')})</b>")
    total_line = (f"💥 {lbl(lang, 'total')}: <b>{money(board.get('total_usd'))}</b>"
                  f" · {int(board.get('count') or 0)}"
                  f" {liqs_word(board.get('count') or 0, lang)}")
    prev = board.get("prev_total")
    if prev:
        total_line += (f"   {mark} {_arrow(board.get('diff_pct'), lang)}"
                       f" ({lbl(lang, 'vs_prev')})")
    oi_rows = board.get("oi_hours") or []
    # OI показываем только если хоть что-то набралось: пустая колонка
    # прочерков в посте выглядит как поломка, а не как «данных ещё нет»
    with_oi = bool(board.get("oi_now_usd")) or any(
        cell.get("pct") is not None or cell.get("value") for cell in oi_rows)
    header = [(lbl(lang, "col_hour"), "l"), (lbl(lang, "col_liqs"), "l"),
              (lbl(lang, "col_coins"), "l"), (lbl(lang, "col_bias"), "l")]
    if with_oi:
        header.append((lbl(lang, "oi"), "l"))
    body = _mono([header] + hour_rows(hours, lang, with_oi))
    parts.append(head + "\n" + total_line + "\n" + body)

    oi_line = oi_line_of(board, lang) if (oi_rows and with_oi) else ""
    if oi_line:
        parts.append(oi_line)
    return "\n".join(p for p in parts if p)


def oi_line_of(board: Optional[dict], lang: str = "ru") -> str:
    """Строка OI: открытый интерес сейчас и ход по часам.

    Одна строка вместо столбца в таблице — значения по часам видно, а
    столбцы с эмодзи-стрелками больше не разъезжаются.
    """
    board = board or {}
    cells = []
    tz = board.get("tz") or tz_offset()
    for cell in board.get("oi_hours") or []:
        pct = cell.get("pct")
        if pct is None:
            # уровень без изменения ничего не говорит о часе — прочерк не пишем
            continue
        hh = hour_hhmm(cell.get("h") or 0, tz)
        cells.append(f"{hh} {_arrow(pct, lang)}")
    head4 = money(board.get("oi_now_usd")) if board.get("oi_now_usd") else ""
    if head4 and board.get("oi_4h_pct") is not None:
        head4 += f" {_arrow(board['oi_4h_pct'], lang)}"
    if not head4 and not cells:
        return ""
    line = f"📊 {lbl(lang, 'oi_4h')}: {head4}".rstrip()
    if cells:
        line += (" · " if head4 else "") + " · ".join(cells)
    return line


def top_hour_block(hour: dict, lang: str = "ru", compact: int = 0) -> str:
    """Один час в подписи поста: заголовок и крупнейшие ликвидации часа.

    ``compact`` > 0 — сжатый вид для случая, когда обычный блок уже не влезает
    в подпись: несколько позиций в одну строку, без нумерации.
    """
    items = hour.get("items") or []
    if not items:
        return ""
    hh = hour_hhmm(hour.get("h") or 0, hour.get("tz") or tz_offset())
    title = f"<b>🏆 {hh} · {lbl(lang, 'top_title')}</b>"
    if compact:
        bits = []
        for it in items[:int(compact)]:
            side = "🔴" if it.get("side") == "SELL" else "🟢"
            bits.append(f"{coin(it.get('symbol'))} {side}"
                        f" {short_money(it.get('usd'))}"
                        f" {exch(it.get('exchange'))}")
        return title + "\n" + " · ".join(bits)
    rows = []
    for i, it in enumerate(items):
        side = "🔴" if it.get("side") == "SELL" else "🟢"
        rows.append([
            (f"{i + 1}. {coin(it.get('symbol'))} {side}", "l"),
            (short_money(it.get("usd")), "r"),
            (exch(it.get("exchange")), "l"),
        ])
    return title + "\n" + _mono(rows)


def render_top7(board: Optional[dict], lang: str = "ru") -> str:
    """Резерв: топ-7 крупных ликвидаций по часам отдельным сообщением.

    Обычный пост укладывается в одну подпись (render_post), а это — запасной
    путь, если подпись исчерпана до последней строки: текст уходит обычным
    сообщением следом за фото.
    """
    if not board:
        return ""
    tz = board.get("tz") or tz_offset()
    blocks: List[str] = []
    for hr in board.get("top_hours") or []:
        items = hr.get("items") or []
        if not items:
            continue
        hh = hour_hhmm(hr.get("h") or 0, tz)
        rows = []
        for i, it in enumerate(items):
            side = "🔴" if it.get("side") == "SELL" else "🟢"
            rows.append([
                (f"{i + 1}. {coin(it.get('symbol'))} {side}", "l"),
                (money(it.get("usd")), "r"),
                (exch(it.get("exchange")), "l"),
            ])
        title = f"<b>— {hh} · {lbl(lang, 'top_title')}</b>"
        blocks.append(title + "\n" + _mono(rows))
    if not blocks:
        return ""
    head = f"<b>🏆 {lbl(lang, 'top_hours')}</b>"
    return head + "\n\n" + "\n\n".join(blocks) + "\n" + gate_line(lang)


def build_board(board: Optional[dict], lang: str = "ru") -> str:
    """Публичная обёртка: стенд или пустая строка, если данных ещё нет."""
    try:
        return board_block(board, lang)
    except Exception:      # noqa: BLE001 — пост важнее стенда
        return ""


_TAG_RE = re.compile(r"</?(pre|code|b|i|a|u|s)(?:\s[^>]*)?>", re.I)


def _close_tags(text: str) -> str:
    """Закрывает теги, оставшиеся открытыми после обрезки по лимиту.

    Telegram отвергает сообщение с незакрытым <pre>/<a> («can't parse
    entities»), а обрезка длинного поста — обычное дело на горячем рынке.
    """
    stack: List[str] = []
    for m in _TAG_RE.finditer(text or ""):
        tag = m.group(1).lower()
        if m.group(0).startswith("</"):
            if tag in stack:
                stack.reverse()
                stack.remove(tag)
                stack.reverse()
        else:
            stack.append(tag)
    return text + "".join(f"</{t}>" for t in reversed(stack))


def _pack(parts: List[str], tail: str, limit: int = CAPTION_LIMIT) -> str:
    chunks = [p for p in parts if p]
    def join(cs: List[str]) -> str:
        body = "\n\n".join(cs)
        return f"{body}\n\n{tail}" if tail else body
    text = join(chunks)
    # Режем хвост, но первый блок (шапка + стенд) не выбрасываем: пустой пост
    # хуже длинного. Если и он не влезает — обрежется по лимиту строкой ниже.
    while len(text) > limit and len(chunks) > 1:
        chunks.pop()
        text = join(chunks)
    if len(text) > limit:
        cut = text[:limit]
        # режем по целой строке: половина строки таблицы читается как сбой
        nl = cut.rfind("\n")
        if nl > limit // 2:
            cut = cut[:nl]
        text = _close_tags(cut)
    return text


def _facts(snap: dict, lang: str = "ru", tables: bool = False) -> dict:
    """Цифры окна. ``tables=True`` добавляет старые табличные блоки.

    В посте они больше не участвуют (их заменил топ-7 по часам), но
    оставлены как запасной вид: собрать их можно одним флагом.
    """
    h = int(snap.get("window_h") or 4)
    longs = float(snap.get("longs_usd") or 0)
    shorts = float(snap.get("shorts_usd") or 0)
    bemoji, btxt = _bias(longs, shorts, lang)
    out = {
        "h": h,
        "n": int(snap.get("count") or 0),
        "total": money(snap.get("total_usd")),
        "longs": money(longs),
        "shorts": money(shorts),
        "bias_e": bemoji,
        "bias": btxt,
        "bias_line": f"{bemoji} {btxt} · {lbl(lang, 'window', h=h)}",
    }
    if tables:
        out.update({
            "kpi": _kpi_line(snap, lang),
            "ex": _ex_block(snap, 4, lang),
            "ex6": _ex_block(snap, 6, lang),
            "coins": _coins_block(snap, 5, lang=lang),
            "coins8": _coins_block(snap, 8, lang=lang),
        })
    return out


def format_headline(tpl: str, h: int = 4) -> str:
    """Подставляет {h} и оборачивает в <b>, если админ прислал голый текст."""
    import html as _html
    s = (tpl or "").strip()
    if not s:
        return ""
    s = s.replace("{h}", str(int(h))).replace("{H}", str(int(h)))
    s = s[:HEAD_MAX_LEN + 32]
    if "<" in s:
        return s
    return f"<b>{_html.escape(s, quote=False)}</b>"


def format_ai_head(text: str, h: int = 4) -> str:
    """Шапка от ИИ: всегда экранируем и оборачиваем в <b>.

    В отличие от шаблонов из админки (там можно прислать готовый HTML),
    текст модели — это только текст: случайный «<» сломает разметку Telegram.
    """
    import html as _html
    s = (text or "").strip().replace("{h}", str(int(h)))
    s = s[:HEAD_MAX_LEN]
    return f"<b>{_html.escape(s, quote=False)}</b>" if s else ""


def _headlines(h: int, lang: str = "ru") -> tuple:
    """Встроенные шапки (пока в БД нет своих)."""
    table = DEFAULT_HEAD_TEMPLATES_EN if str(lang).startswith("en") else DEFAULT_HEAD_TEMPLATES
    return tuple(format_headline(t, h) for t in table)


def active_headlines(store=None, h: int = 4, lang: str = "ru") -> List[str]:
    """Шапки из админки; если таблица пустая — дефолт из кода.

    Шапки из админки написаны по-русски, поэтому для английского канала они
    не подходят: там либо шаблоны из кода, либо ИИ-шапка на английском.
    """
    if str(lang).startswith("en"):
        return list(_headlines(h, lang))
    rows = []
    if store is not None:
        try:
            rows = store.list_digest_heads()
        except Exception:
            rows = []
    out = [format_headline(r.get("text") or "", h) for r in (rows or [])]
    out = [x for x in out if x]
    return out or list(_headlines(h, lang))


def active_images(store=None) -> List[str]:
    """Фото из админки; если пусто — bundled static/channel."""
    rows = []
    if store is not None:
        try:
            rows = store.list_digest_photos()
        except Exception:
            rows = []
    out = []
    for r in rows or []:
        p = r.get("path") or ""
        if p and os.path.isfile(p):
            out.append(p)
    return out or list_images()


def render_post(snap: dict, variant: int = 0, headlines: Optional[List[str]] = None,
                site_url: str = "https://liqscope.online",
                bot_url: str = "https://t.me/LiqScopeBot",
                head_override: Optional[str] = None,
                lang: str = "ru") -> str:
    """Сводка одним сообщением: шапка, стенд и топ-7 ударов по часам.

    Блоков ровно столько, сколько нужно: дублирующие таблицы (биржи, монеты,
    KPI) убраны — их цифры спорили друг с другом и наезжали на соседние
    столбцы. Раскладка собирается по лимиту подписи (1024): часы идут от
    свежего к старому, блок добавляется, только если целиком влезает. Если
    не влезает ни один — берётся сжатый вид (три удара часа строкой).
    head_override — шапка от ИИ: та же раскладка, меняется только текст.
    """
    import html as _html
    f = _facts(snap, lang)
    h = f["h"]
    site = (site_url or "https://liqscope.online").rstrip("/")
    bot = (bot_url or "https://t.me/LiqScopeBot").rstrip("/")
    # ссылки прячем в слова: кликабельные, без голого URL в посте
    bot_word = "bot" if str(lang).startswith("en") else "бот"
    tail = (f"— <i>LiqScope</i>\n"
            f'🌐 <a href="{_html.escape(site, quote=True)}">liqscope.online</a> · '
            f'🤖 <a href="{_html.escape(bot, quote=True)}">{bot_word}</a>')
    # Реферальная ссылка Gate: строкой внизу и кликабельным словом в таблицах,
    # которые уходят обычным текстом (внутри <pre> ссылок не бывает).
    tail += "\n" + gate_line(lang)
    heads = [x for x in (headlines or []) if x] or list(_headlines(h, lang))
    # шапку и компоновку крутим независимо: своя единственная шапка из админки
    # не должна «замораживать» раскладку — блоки продолжают чередоваться
    v = int(variant)
    head = (format_ai_head(head_override, h) if head_override
            else heads[v % max(1, len(heads))])

    # Компоновка поста: шапка → строка итога → топ-7 по часам (свежие часы
    # первыми, сколько влезет) → хвост. Таблицы в два столбика убраны: в
    # Telegram колонки разъезжались на эмодзи и цифрах, и пост читался плохо.
    board = snap.get("board") or {}
    hours = [dict(x) for x in (board.get("top_hours") or [])]
    tz = board.get("tz") or tz_offset()
    for x in hours:
        x.setdefault("tz", tz)

    def fits(parts_: List[str]) -> bool:
        text = "\n\n".join([p for p in parts_ if p] + [tail])
        return len(text) <= CAPTION_LIMIT

    parts: List[str] = [head, _total_line(snap, lang)]
    # OI — короткая строка, ставим её до часов: иначе топы съедают подпись
    # целиком, и открытый интерес до поста не доезжает
    oi_line = oi_line_of(board, lang)
    if oi_line and fits(parts + [oi_line]):
        parts.append(oi_line)
    full: List[int] = []
    for i, x in enumerate(hours):          # свежие часы первыми
        block = top_hour_block(x, lang)
        if block and fits(parts + [block]):
            parts.append(block)
            full.append(i)
    # остатки часов — сжатыми строками: семь ударов в подпись не влезают,
    # а три самых крупных дают картину часа, если место ещё осталось
    if full:
        for i, x in enumerate(hours):
            if i in full:
                continue
            block = top_hour_block(x, lang, compact=3)
            if block and fits(parts + [block]):
                parts.append(block)
    if len(parts) == 2:
        # ни один час с семёркой не влез (очень длинная шапка) — сжатый вид:
        # три крупнейших удара часа одной строкой
        for x in hours:
            block = top_hour_block(x, lang, compact=3)
            if block and fits(parts + [block]):
                parts.append(block)
                break
    if len(parts) == 2 and not hours:
        # часовой стенд ещё не собрался (первый запуск): показываем хотя бы
        # настроение ленты, чтобы пост не состоял из одной суммы
        parts.append(_board_bias_line(board, lang) or f["bias_line"])
    return _pack(parts, tail)


def _board_bias_line(board: Optional[dict], lang: str = "ru") -> str:
    """Перекос окна одной строкой — по суммам часов стенда."""
    hours = (board or {}).get("hours") or []
    longs = sum(float(h.get("longs") or 0) for h in hours)
    shorts = sum(float(h.get("shorts") or 0) for h in hours)
    if longs + shorts <= 0:
        return ""
    bemoji, btxt = _bias(longs, shorts, lang)
    span = (board or {}).get("span_hours") or len(hours)
    return f"{bemoji} {btxt} · {lbl(lang, 'window', h=span)}"


def _total_line(snap: dict, lang: str = "ru") -> str:
    """Итог окна одной строкой: касса, число ликвидаций и сравнение с прошлым."""
    board = snap.get("board") or {}
    total = board.get("total_usd")
    count = board.get("count")
    if not total:
        total = snap.get("total_usd")
        count = count or snap.get("count")
    line = f"💥 <b>{money(total)}</b> · {int(count or 0)} {liqs_word(count or 0, lang)}"
    diff = board.get("diff_pct")
    if diff is not None and board.get("prev_total"):
        mark = "⚖️" if abs(float(diff)) <= 0.5 else ("📈" if float(diff) > 0 else "📉")
        line += f"   {mark} {_arrow(diff, lang)} ({lbl(lang, 'vs_prev')})"
    return line


def list_images() -> List[str]:
    if not os.path.isdir(IMAGES_DIR):
        return []
    out = []
    for name in sorted(os.listdir(IMAGES_DIR)):
        if name.lower().endswith((".jpg", ".jpeg", ".png")):
            out.append(os.path.join(IMAGES_DIR, name))
    return out


def pick_image(variant: int = 0, images: Optional[List[str]] = None) -> Optional[str]:
    imgs = [p for p in (images if images is not None else list_images())
            if p and os.path.isfile(p)]
    if not imgs:
        imgs = list_images()
    if not imgs:
        return None
    return imgs[int(variant) % len(imgs)]
