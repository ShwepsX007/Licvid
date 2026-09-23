"""Сторож монет: пампы и дампы всех монет Gate.

Раньше сервис планировался как «личный список пар для всплесков ликвидаций».
Теперь это сторож резких движений по ВСЕМУ рынку Gate: раз в несколько секунд
приходит один вызов тикеров (все USDT-контракты сразу), из него собирается
минутная история цен, и по ней считаются пампы и дампы.

Настройки пользователя:

    * режим — пампы, дампы или оба;
    * порог в % (например 30);
    * период свечей — 1м, 5м, 15м, 1ч…;
    * количество свечей (например 3);
    * минимальный оборот за сутки (чтобы не ловить неликвид).

Считаем от цены ``candles × период`` минут назад до цены прямо сейчас, поэтому
текущая, ещё не закрытая свеча учитывается. Сигнал уходит в Telegram и
предлагает посмотреть монету на Gate по вшитой реферальной ссылке.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Tuple

from alerts import footer_html

MINUTE = 60
#: период свечи → минуты
PERIODS: Tuple[Tuple[str, int], ...] = (
    ("1m", 1), ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60), ("4h", 240),
)
#: пресеты порога, %
THRESHOLDS: Tuple[float, ...] = (3, 5, 10, 20, 30, 50)
#: пресеты количества свечей
CANDLE_PRESETS: Tuple[int, ...] = (1, 2, 3, 5, 10)
MODES = (("pump", "Пампы", "🚀"), ("dump", "Дампы", "🩸"))
DEFAULT_PERIOD = "5m"
DEFAULT_CANDLES = 3
DEFAULT_THRESHOLD = 10.0
DEFAULT_COOLDOWN_MIN = 10
#: сколько минут цен держим в памяти (сутки — с запасом на любое окно)
# Держим минутную историю с запасом на самое длинное окно настроек:
# 4 часа × 10 свечей = 40 часов, плюс запас на пропуски опросов.
DEFAULT_KEEP_MIN = 3 * 24 * 60


def period_minutes(key: str) -> int:
    for k, m in PERIODS:
        if k == key:
            return m
    for _k, m in PERIODS:
        if _k == DEFAULT_PERIOD:
            return m
    return 5


def period_label(key: str) -> str:
    return key if key in {k for k, _ in PERIODS} else DEFAULT_PERIOD


def mode_label(mode: str) -> str:
    for key, title, _icon in MODES:
        if key == mode:
            return title
    return "Пампы и дампы"


def _num(v, default: float = 0.0) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if n == n else default


def normalize(config: Optional[dict]) -> dict:
    """Настройки сторожа с безопасными значениями по умолчанию."""
    cfg = dict(config or {})
    mode = str(cfg.get("mode") or "both").lower()
    if mode not in ("pump", "dump", "both"):
        mode = "both"
    try:
        threshold = float(cfg.get("threshold") or DEFAULT_THRESHOLD)
    except (TypeError, ValueError):
        threshold = DEFAULT_THRESHOLD
    try:
        candles = int(cfg.get("candles") or DEFAULT_CANDLES)
    except (TypeError, ValueError):
        candles = DEFAULT_CANDLES
    try:
        cooldown = int(cfg.get("cooldown_min") or DEFAULT_COOLDOWN_MIN)
    except (TypeError, ValueError):
        cooldown = DEFAULT_COOLDOWN_MIN
    try:
        min_vol = float(cfg.get("min_vol") or 0)
    except (TypeError, ValueError):
        min_vol = 0.0
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "mode": mode,
        "period": period_label(str(cfg.get("period") or DEFAULT_PERIOD)),
        "candles": max(1, min(candles, 60)),
        "threshold": max(0.5, min(threshold, 500.0)),
        "min_vol": max(0.0, min_vol),
        "cooldown_min": max(1, min(cooldown, 240)),
    }


class PumpScanner:
    """Минутная история цен по всем монетам и поиск пампов с дампами."""

    def __init__(self, keep_min: int = DEFAULT_KEEP_MIN):
        self.keep = max(30, int(keep_min))
        self._prices: Dict[str, deque] = {}     # символ -> минутные закрытия
        self._last_minute: Dict[str, int] = {}  # символ -> минута последнего среза
        self._volume: Dict[str, float] = {}     # оборот 24ч, как его отдал Gate
        self._change24: Dict[str, float] = {}   # изменение за сутки, %
        self._updated = 0.0
        self._source_ts = 0.0

    # ---- наполнение -------------------------------------------------------
    def add_prices(self, prices: Dict[str, float], ts: Optional[float] = None,
                   volume: Optional[Dict[str, float]] = None,
                   change24: Optional[Dict[str, float]] = None) -> int:
        """Срез цен по всем монетам (Gate отдаёт их одним запросом).

        Внутри минуты цена обновляется на месте — история остаётся минутной,
        а сравнивать мы умеем с живой ценой, поэтому формирующаяся свеча
        учитывается сама собой.
        """
        import time as _time
        now = float(ts if ts is not None else _time.time())
        minute = int(now // MINUTE)
        added = 0
        for sym, price in (prices or {}).items():
            p = _num(price)
            if p <= 0 or not sym:
                continue
            last = self._last_minute.get(sym)
            series = self._prices.get(sym)
            if series is None:
                series = self._prices[sym] = deque(maxlen=self.keep)
            if last == minute and series:
                series[-1] = p            # цена внутри минуты уточняется
            else:
                series.append(p)
                self._last_minute[sym] = minute
                added += 1
        if volume:
            for sym, v in volume.items():
                self._volume[sym] = _num(v)
        if change24:
            for sym, v in change24.items():
                self._change24[sym] = _num(v)
        if prices:
            self._updated = now
            self._source_ts = now
        return added

    def last_update(self) -> float:
        return self._updated

    def known(self) -> int:
        return len(self._prices)

    # ---- чтение -----------------------------------------------------------
    def price_now(self, symbol: str) -> Optional[float]:
        series = self._prices.get(symbol)
        return series[-1] if series else None

    def change_pct(self, symbol: str, minutes: float,
                   price_now: Optional[float] = None) -> Optional[float]:
        """Изменение цены в % за span минут (с учётом текущей свечи)."""
        series = self._prices.get(symbol)
        if not series:
            return None
        now_price = _num(price_now if price_now is not None else series[-1])
        if now_price <= 0:
            return None
        bars = max(1, int(round(minutes)))
        if len(series) <= bars:
            return None
        base = _num(series[-1 - bars])
        if base <= 0:
            return None
        return (now_price - base) / base * 100.0

    def span_minutes(self, cfg: dict) -> int:
        return max(1, period_minutes(cfg.get("period", DEFAULT_PERIOD))
                   * int(cfg.get("candles") or DEFAULT_CANDLES))

    def movers(self, cfg: Optional[dict] = None, limit: int = 12) -> List[dict]:
        """Самые резкие движения рынка — для гистограммы на сайте."""
        cfg = normalize(cfg or {})
        span = self.span_minutes(cfg)
        out: List[dict] = []
        for sym in self._prices:
            pct = self.change_pct(sym, span)
            if pct is None:
                continue
            vol = self._volume.get(sym, 0.0)
            if cfg.get("min_vol") and vol < cfg["min_vol"]:
                continue
            out.append({
                "symbol": sym,
                "change_pct": round(pct, 2),
                "price": self.price_now(sym),
                "volume24h": round(vol, 2),
                "change24h": round(self._change24.get(sym, 0.0), 2),
                "span_min": span,
                "period": cfg["period"],
                "candles": cfg["candles"],
            })
        out.sort(key=lambda r: abs(r["change_pct"]), reverse=True)
        return out[:max(1, int(limit))]

    def scan(self, cfg: Optional[dict] = None) -> List[dict]:
        """Сработавшие пороги: список пампов и дампов по всем монетам."""
        cfg = normalize(cfg or {})
        if not cfg.get("enabled"):
            return []
        span = self.span_minutes(cfg)
        mode = cfg["mode"]
        threshold = float(cfg["threshold"])
        hits: List[dict] = []
        for row in self.movers(cfg, limit=10_000):
            pct = row["change_pct"]
            if pct >= threshold and mode in ("pump", "both"):
                kind = "pump"
            elif pct <= -threshold and mode in ("dump", "both"):
                kind = "dump"
            else:
                continue
            hit = dict(row)
            hit["kind"] = kind
            hit["threshold"] = threshold
            series = self._prices.get(row["symbol"]) or []
            if len(series) > span:
                hit["price_from"] = _num(series[-1 - span])
            hits.append(hit)
        hits.sort(key=lambda r: abs(r["change_pct"]), reverse=True)
        return hits


def filter_new(hits: Iterable[dict], last_fired: Dict[str, float], now: float,
               cooldown_min: int) -> List[dict]:
    """Оставить сигналы, о которых ещё не сообщали (кулдаун на монету+режим)."""
    out: List[dict] = []
    cooldown = max(1, int(cooldown_min)) * 60
    for hit in hits or []:
        key = f"{hit.get('symbol')}|{hit.get('kind')}"
        prev = _num(last_fired.get(key), 0.0)
        if prev and now - prev < cooldown:
            continue
        last_fired[key] = now
        out.append(hit)
    return out


def money(value, lang: str = "ru") -> str:
    """Сумма в коротком виде. ``lang='en'`` — без русских суффиксов."""
    v = _num(value)
    sign = "−" if v < 0 else ""
    v = abs(v)
    if str(lang).lower().startswith("en"):
        if v >= 1e9:
            return f"{sign}${v / 1e9:.2f}B"
        if v >= 1e6:
            return f"{sign}${v / 1e6:.2f}M"
        if v >= 1e3:
            return f"{sign}${v / 1e3:.1f}K"
        return f"{sign}${v:.0f}"
    if v >= 1e9:
        return f"{sign}${v / 1e9:.2f} млрд"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.2f} млн"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.1f} тыс"
    return f"{sign}${v:.0f}"


def price_str(p) -> str:
    v = _num(p)
    if v <= 0:
        return "—"
    digits = 6 if v < 0.01 else 4 if v < 1 else 2
    return f"${v:,.{digits}f}"


def chat_text(hit: dict) -> str:
    """Короткая строка сигнала сторожа для чата на сайте (без разметки)."""
    coin = str(hit.get("symbol") or "").replace("_USDT", "")
    is_pump = str(hit.get("kind")) == "pump"
    pct = _num(hit.get("change_pct"))
    arrow = "🚀" if is_pump else "🩸"
    span = int(hit.get("span_min") or 0)
    candles = int(hit.get("candles") or 1)
    period = hit.get("period") or DEFAULT_PERIOD
    lines = [
        f"{arrow} {'Памп' if is_pump else 'Дамп'} · {coin}",
        f"{pct:+.2f}% за {span} мин · {candles} × {period}",
        f"цена {price_str(hit.get('price'))} {'→' if is_pump else '←'} "
        f"{price_str(hit.get('price_from'))}",
        f"оборот 24ч {money(hit.get('volume24h'))}",
    ]
    return "\n".join(lines)


def chat_meta(hit: dict) -> dict:
    """Части сигнала сторожа — для перевода в кабинете (см. alerts.chat_meta)."""
    return {
        "direction": "pump" if str(hit.get("kind")) == "pump" else "dump",
        "symbol": str(hit.get("symbol") or ""),
        "change_pct": _num(hit.get("change_pct")),
        "span_min": int(hit.get("span_min") or 0),
        "candles": int(hit.get("candles") or 1),
        "period": str(hit.get("period") or DEFAULT_PERIOD),
        "price": _num(hit.get("price")),
        "price_from": _num(hit.get("price_from")),
        "volume24h": _num(hit.get("volume24h")),
    }


def format_signal_html(hit: dict, lang: str = "ru",
                       site_url: str = "https://liqscope.online") -> str:
    """Сигнал пампа или дампа.

    Оформление то же, что у алертов по объёму: шапка с иконкой, крупные числа
    в разметке (``<b>``/``<code>``), подпись и общий подвал LiqScope.
    """
    en = str(lang).lower().startswith("en")
    coin = str(hit.get("symbol") or "").replace("_USDT", "")
    is_pump = str(hit.get("kind")) == "pump"
    pct = _num(hit.get("change_pct"))
    arrow = "🚀" if is_pump else "🩸"
    span = int(hit.get("span_min") or 0)
    candles = int(hit.get("candles") or 1)
    period = hit.get("period") or DEFAULT_PERIOD
    if en:
        title = f"{arrow} <b>{'Pump' if is_pump else 'Dump'} · {coin}</b>"
        window = (f"<b>{pct:+.2f}%</b> over {span} min · {candles} × {period}")
        price = (f"price <code>{price_str(hit.get('price'))}</code>"
                 f" ← <code>{price_str(hit.get('price_from'))}</code>")
        extra = f"24h volume <code>{money(hit.get('volume24h'), 'en')}</code>"
        note = "<i>open candle counted</i>"
    else:
        title = f"{arrow} <b>{'Памп' if is_pump else 'Дамп'} · {coin}</b>"
        window = f"<b>{pct:+.2f}%</b> за {span} мин · {candles} × {period}"
        price = (f"цена <code>{price_str(hit.get('price'))}</code>"
                 f" → <code>{price_str(hit.get('price_from'))}</code>")
        extra = f"оборот 24ч <code>{money(hit.get('volume24h'))}</code>"
        note = "<i>текущая свеча учтена</i>"
    return "\n".join([title, window, price, extra, note,
                       footer_html(site_url, lang)])


def format_settings_text(cfg: dict, lang: str = "ru") -> str:
    """Строка настроек для кабинета и бота."""
    cfg = normalize(cfg)
    span = period_minutes(cfg["period"]) * cfg["candles"]
    mode = {"pump": "пампы", "dump": "дампы", "both": "пампы и дампы"}[cfg["mode"]]
    # числа — в разметке: сообщения бота должны выглядеть одинаково
    return (f"режим: {mode} · порог <b>{cfg['threshold']:g}%</b>"
            f" · <code>{cfg['candles']} × {cfg['period']} ≈ {span} мин</code>"
            f" · пауза <code>{cfg['cooldown_min']} мин</code>"
            + (f" · оборот от <code>{money(cfg['min_vol'])}</code>"
               if cfg.get("min_vol") else ""))
