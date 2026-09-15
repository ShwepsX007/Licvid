"""Сводки в Telegram-канал: лидеры ликвидаций, OI, CVD.

Чистые функции — без сети. Бот только публикует готовый текст + картинку.
Один пост = одно сообщение (подпись к фото ≤ 1024).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

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


def _bias(longs: float, shorts: float) -> tuple:
    tot = longs + shorts
    if tot <= 0:
        return "😴", "лента почти молчала"
    if longs > shorts * 1.25:
        return "📉", "резали лонги"
    if shorts > longs * 1.25:
        return "📈", "выносили шорты"
    return "⚖️", "били с обеих сторон"


def _side_dot(longs: float, shorts: float) -> str:
    return "🔴" if longs >= shorts else "🟢"


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
            width[i] = max(width[i], len(txt))
    lines = []
    for r in rows:
        cells = []
        for i, (txt, align) in enumerate(r):
            pad = " " * max(0, width[i] - len(txt))
            cells.append((pad + txt) if align == "r" else (txt + pad))
        lines.append(gap.join(cells).rstrip())
    body = "\n".join(lines)
    body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre><code>{body}</code></pre>"


def _kpi_cells(snap: dict) -> List[str]:
    """Правая колонка таблицы бирж: касса, лонги, шорты, кит.

    Строк ровно четыре — по числу бирж в компактной таблице: каждая
    цифра окна стоит напротив своей биржи, а не висит отдельным абзацем.
    """
    b = snap.get("biggest") or {}
    cells = [
        f"💥 {money(snap.get('total_usd'))} · {int(snap.get('count') or 0)} шт.",
        f"🔴 лонги {money(snap.get('longs_usd'))}",
        f"🟢 шорты {money(snap.get('shorts_usd'))}",
    ]
    if b:
        mark = "🔴" if b.get("side") == "SELL" else "🟢"
        cells.append(f"🐋 {coin(b.get('symbol'))} {money(b.get('usd'))}"
                     f" · {exch(b.get('exchange'))} {mark}")
    return cells


def _kpi_line(snap: dict) -> str:
    """Одна строка вместо таблицы — для вариантов, где биржи не первыми."""
    return (f"💥 {money(snap.get('total_usd'))} · {int(snap.get('count') or 0)} шт."
            f"   🔴 {money(snap.get('longs_usd'))}"
            f"   🟢 {money(snap.get('shorts_usd'))}")


def _ex_block(snap: dict, n: int = 4) -> str:
    """Биржи слева, цифры окна справа — два столбика, как в терминале."""
    items = list((snap.get("exchanges") or {}).items())[:n]
    if not items:
        return ""
    right = _kpi_cells(snap)
    rows = []
    for i, (k, v) in enumerate(items):
        rows.append([
            (f"• {exch(k)}", "l"),
            (money(v), "r"),
            (right[i] if i < len(right) else "", "l"),
        ])
    return _mono(rows)


def _metric_cell(snap: dict, sym: Any) -> str:
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
            oi_txt = f"📊 OI {'↑' if n >= 0 else '↓'}{money(abs(n))}{extra}"
            oi_abs = abs(n)
    cvd_txt, cvd_abs = "", None
    cvd = (snap.get("cvd") or {}).get(sym)
    if cvd is not None:
        try:
            v = float(cvd)
        except (TypeError, ValueError):
            v = None
        if v is not None:
            cvd_txt = (f"🌊 CVD {'🟢' if v >= 0 else '🔴'} "
                       f"{'покупки' if v >= 0 else 'продажи'} {money(abs(v))}")
            cvd_abs = abs(v)
    if oi_txt and cvd_txt:
        return oi_txt if oi_abs >= cvd_abs else cvd_txt
    if oi_txt:
        return oi_txt
    if cvd_txt:
        return cvd_txt
    tot = payload.get("total_usd")
    if tot:
        return f"📊 OI {money(tot)}"
    return ""


def _coins_block(snap: dict, n: int = 5, header: str = "Монеты") -> str:
    """Монеты-лидеры: слева ранг/монета/сумма, справа метрика (OI или CVD)."""
    coins = list(snap.get("top_coins") or [])[:n]
    if not coins:
        return "лидеров нет"
    rows: List[List[tuple]] = [[(header, "l")]]
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
            (_metric_cell(snap, c.get("symbol")), "l"),
        ])
    return _mono(rows)


def _pack(parts: List[str], tail: str, limit: int = CAPTION_LIMIT) -> str:
    chunks = [p for p in parts if p]
    def join(cs: List[str]) -> str:
        body = "\n\n".join(cs)
        return f"{body}\n\n{tail}" if tail else body
    text = join(chunks)
    while len(text) > limit and chunks:
        chunks.pop()
        text = join(chunks)
    return text[:limit]


def _facts(snap: dict) -> dict:
    h = int(snap.get("window_h") or 4)
    longs = float(snap.get("longs_usd") or 0)
    shorts = float(snap.get("shorts_usd") or 0)
    bemoji, btxt = _bias(longs, shorts)
    return {
        "h": h,
        "n": int(snap.get("count") or 0),
        "total": money(snap.get("total_usd")),
        "longs": money(longs),
        "shorts": money(shorts),
        "bias_e": bemoji,
        "bias": btxt,
        "bias_line": f"{bemoji} {btxt} · окно {h}ч",
        "kpi": _kpi_line(snap),
        "ex": _ex_block(snap, 4),
        "ex6": _ex_block(snap, 6),
        "coins": _coins_block(snap, 5),
        "coins8": _coins_block(snap, 8),
    }


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


def _headlines(h: int) -> tuple:
    """Встроенные шапки (пока в БД нет своих)."""
    return tuple(format_headline(t, h) for t in DEFAULT_HEAD_TEMPLATES)


def active_headlines(store=None, h: int = 4) -> List[str]:
    """Шапки из админки; если таблица пустая — дефолт из кода."""
    rows = []
    if store is not None:
        try:
            rows = store.list_digest_heads()
        except Exception:
            rows = []
    out = [format_headline(r.get("text") or "", h) for r in (rows or [])]
    out = [x for x in out if x]
    return out or list(_headlines(h))


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
                bot_url: str = "https://t.me/LiqScopeBot") -> str:
    """Сводка: живая шапка + компактные блоки в два столбика.

    Компоновка — как в терминале: слева биржа/монета, справа цифры.
    variant крутит и шапку, и порядок блоков (тексты не повторяются).
    """
    import html as _html
    f = _facts(snap)
    h = f["h"]
    site = (site_url or "https://liqscope.online").rstrip("/")
    bot = (bot_url or "https://t.me/LiqScopeBot").rstrip("/")
    # ссылки прячем в слова: кликабельные, без голого URL в посте
    tail = (f"— <i>LiqScope</i>\n"
            f'🌐 <a href="{_html.escape(site, quote=True)}">liqscope.online</a> · '
            f'🤖 <a href="{_html.escape(bot, quote=True)}">бот</a>')
    heads = [x for x in (headlines or []) if x] or list(_headlines(h))
    # шапку и компоновку крутим независимо: своя единственная шапка из админки
    # не должна «замораживать» раскладку — блоки продолжают чередоваться
    v = int(variant)
    head = heads[v % max(1, len(heads))]

    ex, ex6 = f["ex"], f["ex6"]
    coins, coins8 = f["coins"], f["coins8"]
    kpi, bias = f["kpi"], f["bias_line"]

    bodies = (
        [ex, kpi, coins],                 # 0 — биржи, цифры окна, монеты
        [kpi, ex, coins, bias],           # 1 — цифры, таблицы, оценка
        [ex6, coins],                     # 2 — шесть бирж
        [ex, coins, bias],                # 3 — с оценкой рынка внизу
        [coins, ex],                      # 4 — монеты первыми
        [ex, coins, kpi],                 # 5 — касса строкой внизу
        [kpi, coins, ex],                 # 6 — касса, монеты, биржи
        [coins8, ex],                     # 7 — топ-8 монет
        [ex, bias, coins],                # 8 — оценка между таблицами
        [coins, ex, kpi],                 # 9 — монеты, биржи, касса
        [kpi, bias, ex, coins],           # 10 — цифры и оценка сверху
        [bias, ex, coins],                # 11 — с оценки рынка
    )
    parts = [head] + list(bodies[v % len(bodies)])
    return _pack(parts, tail)


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
