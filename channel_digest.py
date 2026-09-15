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


def _num(v: Any) -> str:
    """Цифра в моноширинном <code>, чтобы сумма читалась из абзаца."""
    return f"<code>{money(v)}</code>"


def _anum(v: Any) -> str:
    try:
        n = abs(float(v or 0))
    except (TypeError, ValueError):
        n = 0.0
    return _num(n)


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


def _leaders(snap: dict, n: int = 5) -> str:
    coins = list(snap.get("top_coins") or [])[:n]
    if not coins:
        return "лидеров нет"
    rows = []
    for i, c in enumerate(coins):
        mark = _MEDALS[i] if i < len(_MEDALS) else f"{i + 1}."
        rows.append(
            f"{mark} <b>{coin(c['symbol'])}</b>  {_num(c['usd'])}  "
            f"{_side_dot(c['longs'], c['shorts'])}"
        )
    return "\n".join(rows)


def _ex_lines(snap: dict, n: int = 4) -> str:
    items = list((snap.get("exchanges") or {}).items())[:n]
    if not items:
        return "биржи молчали"
    return "\n".join(f"• {exch(k)}  {_num(v)}" for k, v in items)


def _ex_inline(snap: dict, n: int = 4) -> str:
    items = list((snap.get("exchanges") or {}).items())[:n]
    if not items:
        return "биржи молчали"
    return " · ".join(f"{exch(k)} {_num(v)}" for k, v in items)


def _biggest(snap: dict) -> str:
    b = snap.get("biggest") or {}
    if not b:
        return ""
    side = "🔴 лонг" if b.get("side") == "SELL" else "🟢 шорт"
    return (f"🐋 <b>{coin(b.get('symbol'))}</b>  {_num(b.get('usd'))}"
            f"  ·  {exch(b.get('exchange'))}  ·  {side}")


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
                parts.append(f"{coin(sym)} {_num(tot)}")
            continue
        try:
            n = float(usd)
        except (TypeError, ValueError):
            continue
        arrow = "↑" if n >= 0 else "↓"
        extra = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
        parts.append(f"{coin(sym)} {arrow}{_anum(n)}{extra}")
    return " · ".join(parts)


def _cvd_line(snap: dict) -> str:
    parts = []
    for c in (snap.get("top_coins") or [])[:3]:
        sym = c.get("symbol")
        v = (snap.get("cvd") or {}).get(sym)
        if v is None:
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        side = "покупки" if n >= 0 else "продажи"
        mark = "🟢" if n >= 0 else "🔴"
        parts.append(f"{coin(sym)} {mark} {side} {_anum(n)}")
    return " · ".join(parts)


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
    oi = _oi_line(snap)
    cvd = _cvd_line(snap)
    return {
        "h": h,
        "n": int(snap.get("count") or 0),
        "total": _num(snap.get("total_usd")),
        "longs": _num(longs),
        "shorts": _num(shorts),
        "bias_e": bemoji,
        "bias": btxt,
        "leaders": _leaders(snap),
        "ex": _ex_lines(snap),
        "ex_in": _ex_inline(snap),
        "big": _biggest(snap),
        "oi": f"📊 OI  {oi}" if oi else "",
        "cvd": f"🌊 CVD  {cvd}" if cvd else "",
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
                site_url: str = "https://liqscope.online") -> str:
    """Сводка: живая шапка + читаемые цифры. variant крутит и то и другое."""
    f = _facts(snap)
    h, n = f["h"], f["n"]
    site = (site_url or "https://liqscope.online").rstrip("/")
    tail = f"— <i>LiqScope</i>\n{site}"
    heads = [x for x in (headlines or []) if x] or list(_headlines(h))
    v = int(variant) % max(1, len(heads))
    head = heads[v]

    bodies = (
        [  # 0 — KPI-карточка
            f"💥  {f['total']}\n"
            f"🔴  {f['longs']}  лонги\n"
            f"🟢  {f['shorts']}  шорты\n"
            f"{f['bias_e']}  {n} ликв. · {f['bias']}",
            f"<b>Лидеры</b>\n{f['leaders']}",
            f"🏛 {f['ex_in']}",
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 1 — рейтинг монет
            f['leaders'],
            f"итого  {f['total']}  из  {n}\n"
            f"🔴 лонги  {f['longs']}\n"
            f"🟢 шорты  {f['shorts']}\n"
            f"{f['bias_e']} {f['bias']}",
            f"🏛 {f['ex_in']}",
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 2 — терминальный лог
            f"TOTAL  {f['total']}    N={n}\n"
            f"LONG   {f['longs']}\n"
            f"SHORT  {f['shorts']}\n"
            f"{f['bias_e']} {f['bias']}",
            f['leaders'],
            f['ex'],
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 3 — кит первым
            f['big'] or f"{f['bias_e']} {f['bias']}",
            f"касса  {f['total']}  ·  {n} ликв.\n"
            f"🔴 {f['longs']}    🟢 {f['shorts']}",
            f"<b>Дальше по ленте</b>\n{f['leaders']}",
            f"🏛 {f['ex_in']}",
            f['oi'], f['cvd'],
        ],
        [  # 4 — биржи первыми
            f['ex'],
            f"💥 {f['total']}  ·  {n} шт.\n"
            f"🔴 лонги  {f['longs']}\n"
            f"🟢 шорты  {f['shorts']}",
            f"<b>Монеты</b>\n{f['leaders']}",
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 5 — касса
            f"касса  {f['total']}\n"
            f"ударов  <code>{n}</code>\n"
            f"🔴 {f['longs']}   🟢 {f['shorts']}\n"
            f"{f['bias_e']} {f['bias']}",
            f['leaders'],
            f"🏛 {f['ex_in']}",
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 6 — табло
            f"💥 {f['total']}\n"
            f"🔴 лонги   {f['longs']}\n"
            f"🟢 шорты   {f['shorts']}\n"
            f"матчей     <code>{n}</code>",
            f['leaders'],
            f['ex'],
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 7 — вынесло
            f"вынесло  {f['total']}\n"
            f"🔴 {f['longs']}  лонги\n"
            f"🟢 {f['shorts']}  шорты\n"
            f"{f['bias_e']} {f['bias']}",
            f"<b>Лидеры</b>\n{f['leaders']}",
            f"🏛 {f['ex_in']}",
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 8 — температура
            f"{f['bias_e']} {f['bias']}\n"
            f"жар  {f['total']}  /  {n} ликв.",
            f"🔴 лонги  {f['longs']}\n🟢 шорты  {f['shorts']}",
            f['leaders'],
            f"🏛 {f['ex_in']}",
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 9 — плитка
            f"💥 {f['total']}\n"
            f"🔴 {f['longs']}   🟢 {f['shorts']}   ·  {n} шт.\n"
            f"{f['bias_e']} {f['bias']}",
            f['leaders'],
            f['ex'],
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 10 — сначала лидеры
            f['leaders'],
            f"итого  {f['total']}  в  {n} ликвидациях\n"
            f"🔴 лонги  {f['longs']}\n"
            f"🟢 шорты  {f['shorts']}",
            f"🏛 {f['ex_in']}",
            f['big'], f['oi'], f['cvd'],
        ],
        [  # 11 — брифинг
            f"{f['bias_e']} {f['bias'].capitalize()}. "
            f"Касса {f['total']}, {n} ударов.",
            f"🔴 лонги  {f['longs']}\n🟢 шорты  {f['shorts']}",
            f"<b>Лидеры</b>\n{f['leaders']}",
            f"<b>Биржи</b>\n{f['ex']}",
            f['big'], f['oi'], f['cvd'],
        ],
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
