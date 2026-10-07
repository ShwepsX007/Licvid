"""🐋 Signal Screener — персональные сигналы по крупным переводам.

Сервис в личном кабинете: пользователь задаёт фильтры Скринера китов (биржа,
направление, сеть, порог в долларах) и включает отправку совпадений себе в
Telegram. Логика здесь — только «нормализовать настройки → проверить событие →
собрать текст»: ни базы, ни сети, ни Telegram. Рассылкой занимается
``screener_signal_loop`` в ``server.py``, настройки сохраняет ``web_account``.

Модуль намеренно не зависит от aiohttp/БД: его можно импортировать и тестировать
как чистые функции, а Скринер при этом может быть недоступен (опциональный
модуль ``whale_screener``) — тогда фильтры продолжают работать, просто без
красивых имён площадок.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

#: Площадки, которые знает скринер, берутся оттуда же, откуда и графики:
#: реестр CEX-кошельков меняется, а список бирж в фильтре — вместе с ним.
try:                                            # noqa: SIM105 — опциональный модуль
    from whale_screener import event_exchange, venue_display_name, venue_of_label
except Exception:                               # noqa: BLE001
    event_exchange = None
    venue_display_name = None
    venue_of_label = None

#: Сеть события: те же идентификаторы, что у Скринера и его фильтров.
SIGNAL_NETWORKS = ("ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA",
                   "TRON", "HYPERLIQUID")
SIGNAL_NETWORK_TITLES = {
    "ETH": "Ethereum", "BNB": "BNB Chain", "POLYGON": "Polygon",
    "ARBITRUM": "Arbitrum", "BASE": "Base", "SOLANA": "Solana", "TRON": "TRON",
    "HYPERLIQUID": "Hyperliquid",
}
#: Направление биржевого потока. ``all`` — и приход, и уход (и переводы без
#: направления, если они прошли порог): сигнал про сам факт крупного движения.
SIGNAL_DIRECTIONS = ("all", "inflow", "outflow")
#: Порог можно выбрать из списка или ввести свой — ``presets`` только подсказка,
#: ``normalize_signal`` принимает любое число.
SIGNAL_VOLUME_PRESETS = (50_000, 100_000, 250_000, 500_000, 1_000_000, 5_000_000)
SIGNAL_COOLDOWN_PRESETS = (0, 5, 15, 30, 60)
#: сколько сигналов за один проход отправляем одному человеку: киты идут
#: пачками, а flood-wait в Telegram зря трогать нельзя
SIGNAL_MAX_PER_CYCLE = 5
#: сколько событий просматриваем за проход: после простоя окно может быть
#: большим, а рассылка не имеет права блокировать цикл на весь буфер истории
SIGNAL_MAX_SCAN = 5_000
#: жёсткий потолок ручного порога: 1 трлн — за пределами любой реальной китовой
#: переводки, зато завышенное число из запроса не сломает сравнение
SIGNAL_MAX_USD = 1_000_000_000_000.0

DEFAULT_SIGNAL: Dict[str, Any] = {
    "notify": False,            # Telegram-тумблер: по умолчанию выключен
    "exchange": "ALL",          # или идентификатор площадки («binance»)
    "direction": "all",         # «inflow» — на биржу, «outflow» — с биржи
    "chain": "ALL",             # или одна из SIGNAL_NETWORKS
    "min_usd": 250_000,         # порог в долларах
    "cooldown_min": 15,         # пауза на монету, 0 — без паузы
}

_SYMBOL_RE = re.compile(r"[^A-Za-z0-9.$_\-]")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def signal_money(value: Any) -> str:
    """Компактная сумма в долларах — тот же стиль, что в алертах кабинета."""
    n = _num(value)
    sign = "−" if n < 0 else ""
    n = abs(n)
    if n >= 1e9:
        return f"{sign}${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{sign}${n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{sign}${n / 1e3:.0f}K"
    return f"{sign}${n:.0f}"


def signal_symbol(value: Any) -> str:
    """Тикер из события: только безопасные знаки, до 24 символов."""
    sym = _SYMBOL_RE.sub("", str(value or "").strip().upper())[:24]
    return sym or "TOKEN"


def normalize_signal(raw: Any) -> Dict[str, Any]:
    """Настройки сервиса: то, что пришло из кабинета/бота, в безопасном виде.

    Чужие ключи выбрасываем, неизвестные значения сворачиваем в умолчания —
    конфиг читает цикл рассылки на каждом проходе, и падать он не права не имеет.
    """
    src = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    out["notify"] = bool(src.get("notify"))

    # площадка приходит меткой из реестра («Binance 14») или идентификатором
    # графика («binance»); словарь/список из запроса молча превращается в «все»,
    # иначе фильтр молча не ловил бы ничего
    exchange_raw = src.get("exchange")
    exchange = (str(exchange_raw).strip()
                if isinstance(exchange_raw, (str, int, float)) else "")
    if not exchange or exchange.upper() == "ALL":
        out["exchange"] = "ALL"
    else:
        venue = (venue_of_label(exchange) if venue_of_label
                 else re.split(r"[\s\-–—_(/[:,|]+", exchange.casefold(), maxsplit=1)[0])
        venue = re.sub(r"[^a-z0-9.\-_]", "", str(venue or exchange).casefold())[:32]
        out["exchange"] = venue or "ALL"

    direction = str(src.get("direction") or "").strip().lower()
    out["direction"] = direction if direction in SIGNAL_DIRECTIONS else "all"

    chain = str(src.get("chain") or "").strip().upper()
    out["chain"] = chain if chain in SIGNAL_NETWORKS else "ALL"

    min_usd = _num(src.get("min_usd", DEFAULT_SIGNAL["min_usd"]),
                   float(DEFAULT_SIGNAL["min_usd"]))
    out["min_usd"] = round(max(0.0, min(min_usd, SIGNAL_MAX_USD)), 2)

    try:
        cooldown = int(_num(src.get("cooldown_min", DEFAULT_SIGNAL["cooldown_min"]),
                            float(DEFAULT_SIGNAL["cooldown_min"])))
    except (TypeError, ValueError, OverflowError):
        cooldown = int(DEFAULT_SIGNAL["cooldown_min"])
    out["cooldown_min"] = max(0, min(cooldown, 1440))
    return out


def signal_presets() -> Dict[str, Any]:
    """Подсказки интерфейса: список порогов, направления, сети, паузы."""
    return {
        "volume_usd": list(SIGNAL_VOLUME_PRESETS),
        "directions": list(SIGNAL_DIRECTIONS),
        "chains": list(SIGNAL_NETWORKS),
        "network_titles": dict(SIGNAL_NETWORK_TITLES),
        "cooldown_min": list(SIGNAL_COOLDOWN_PRESETS),
        "default": dict(DEFAULT_SIGNAL),
    }


def signal_exchanges(screener: Any = None, events: Iterable[dict] = ()) -> List[dict]:
    """Биржи, по которым у Скринера есть кошельки (плюс те, что в событиях).

    Фиксированный перечень расходится с реестром: биржа без размеченных
    кошельков даёт пустой фильтр, а новая биржа из реестра в фильтре не
    появляется. Здесь список строится от самих данных.
    """
    venues: Dict[str, dict] = {}

    def remember(label: Any) -> None:
        text = str(label or "").strip()
        if not text:
            return
        venue = venue_of_label(text) if venue_of_label else ""
        if not venue:
            return
        row = venues.get(venue)
        if row is None:
            venues[venue] = {"id": venue,
                             "name": (venue_display_name(venue) if venue_display_name
                                      else text[:32]),
                             "labels": {text}}
        else:
            row["labels"].add(text)

    wallets = getattr(screener, "wallets", None) or {}
    try:
        for label in wallets.values():
            remember(label)
    except AttributeError:            # не dict — реестр другой формы: не падаем
        pass
    for event in events or ():
        if isinstance(event, dict):
            remember(event.get("from_label") or event.get("to_label"))
    return sorted(({"id": row["id"], "name": row["name"], "labels": sorted(row["labels"])[:12]}
                   for row in venues.values()), key=lambda item: item["name"])


def event_key(event: dict) -> str:
    """Устойчивый ключ события для дедупликации рассылки."""
    if not isinstance(event, dict):
        return ""
    chain = str(event.get("chain") or "").upper()
    digest = str(event.get("hash") or event.get("tx") or "").lower()
    index = str(event.get("log_index") or event.get("index") or "")
    return f"{chain}:{digest}:{index}"


def event_direction(event: dict) -> str:
    """Направление биржевого потока события (inflow/outflow/trade/transfer)."""
    direction = str(event.get("direction") or "").strip().lower()
    if direction:
        return direction
    if event.get("to_label") and not event.get("from_label"):
        return "inflow"
    if event.get("from_label") and not event.get("to_label"):
        return "outflow"
    return "transfer"


def event_venue_id(event: dict) -> str:
    """Идентификатор площадки события: та же нормализация, что у графиков."""
    label = (event_exchange(event) if event_exchange
             else str(event.get("from_label") or event.get("to_label") or ""))
    if not venue_of_label:
        return str(label or "").strip().casefold()
    return venue_of_label(label)


def match_signal(event: dict, cfg: Dict[str, Any]) -> Optional[dict]:
    """Совпадение события с фильтром → карточка сигнала (или ``None``).

    Порядок проверок — от дешёвых к дорогим: сначала порог (он отсекает почти
    всё), потом направление и сеть, и только потом площадка.
    """
    if not isinstance(event, dict) or not isinstance(cfg, dict):
        return None
    usd = _num(event.get("usd"))
    try:
        threshold = float(cfg.get("min_usd") or 0)
    except (TypeError, ValueError):
        threshold = 0.0
    if usd <= 0 or usd < threshold:
        return None
    want_direction = str(cfg.get("direction") or "all")
    direction = event_direction(event)
    if want_direction != "all" and direction != want_direction:
        return None
    chain = str(event.get("chain") or "").strip().upper()
    want_chain = str(cfg.get("chain") or "ALL")
    if want_chain != "ALL" and chain != want_chain:
        return None
    venue = event_venue_id(event)
    want_exchange = str(cfg.get("exchange") or "ALL")
    if want_exchange != "ALL" and venue != want_exchange:
        return None
    # метка той стороны, которая задаёт направление: уходят — отправитель,
    # приходят — получатель; перевод без направления — любая известная сторона
    if direction == "outflow":
        label = event.get("from_label") or event.get("to_label") or ""
    elif direction == "inflow":
        label = event.get("to_label") or event.get("from_label") or ""
    else:
        label = event_exchange(event) if event_exchange else (
            event.get("from_label") or event.get("to_label") or "")
    label = str(label or "")[:60]
    return {
        "metric": "whale",
        "key": event_key(event),
        "chain": chain or "—",
        "direction": direction,
        "venue": venue,
        "venue_name": (venue_display_name(venue) if venue_display_name and venue
                       else (label[:32] if label else venue)),
        "label": label,
        "symbol": signal_symbol(event.get("symbol")),
        "amount": round(_num(event.get("amount")), 6),
        "usd": round(usd, 2),
        "threshold": round(_num(cfg.get("min_usd")), 2),
        "ts": _num(event.get("timestamp")) or time.time(),
        "hash": str(event.get("hash") or "")[:120],
        "log_index": str(event.get("log_index") or "")[:24],
        "from": str(event.get("from") or "")[:120],
        "to": str(event.get("to") or "")[:120],
        "source": str(event.get("source") or "")[:16],
        "side": str(event.get("side") or "")[:8],
        "cooldown_min": int(_num(cfg.get("cooldown_min"), 0)),
    }


def signal_hits(events: Iterable[dict], cfg: Dict[str, Any], *,
                now: Optional[float] = None,
                last_fire: Optional[Callable[[str], float]] = None,
                limit: int = SIGNAL_MAX_PER_CYCLE) -> List[dict]:
    """Совпавшие события с паузой на монету и ограничением на проход.

    ``last_fire(symbol)`` возвращает время последней отправки по монете — цикл
    рассылки хранит его сам, так что фильтр не обязан молчать после перезапуска
    процесса, но и не обязан держать историю: без него пауза считается только
    внутри одного прохода.
    """
    cfg = normalize_signal(cfg)
    moment = time.time() if now is None else float(now)
    pause = int(cfg["cooldown_min"]) * 60
    hits: List[dict] = []
    fired: Dict[str, float] = {}
    try:
        for index, event in enumerate(events or ()):
            if index >= SIGNAL_MAX_SCAN:
                break                       # окно слишком большое — бережём цикл
            hit = match_signal(event, cfg)
            if hit is None:
                continue
            stamp = _num(hit.get("ts")) or moment
            if stamp > moment + 300:        # часы разошлись — не считаем futures
                continue
            coin = hit["symbol"]
            last = max(_num(fired.get(coin)),
                       _num(last_fire(coin)) if last_fire else 0.0)
            if pause and last and moment - last < pause:
                continue
            fired[coin] = moment
            hits.append(hit)
    except TypeError:                       # events не итерируемые — сигнал не цикл
        return []
    # самые крупные переводы важнее первых по времени: сортируем и режем
    hits.sort(key=lambda item: -_num(item.get("usd")))
    return hits[:max(1, int(limit))]


def signal_chat_text(hit: dict) -> str:
    """Строка сигнала для ленты «Сервисы» на сайте (без разметки)."""
    place = hit.get("venue_name") or hit.get("label") or "без метки биржи"
    arrow = {"inflow": "приток", "outflow": "отток"}.get(
        str(hit.get("direction")), "перевод")
    lines = [f"🐋 {place} · {arrow} {signal_money(hit.get('usd'))} "
             f"{hit.get('symbol')} · {SIGNAL_NETWORK_TITLES.get(hit.get('chain'), hit.get('chain'))}"]
    details = [f"порог {signal_money(hit.get('threshold'))}"]
    if hit.get("cooldown_min"):
        details.append(f"пауза {int(hit['cooldown_min'])} мин")
    if hit.get("hash"):
        details.append(str(hit["hash"])[:10] + "…" + str(hit["hash"])[-6:])
    lines.append(" · ".join(details))
    return "\n".join(lines)


def signal_chat_meta(hit: dict) -> dict:
    """Части сигнала — кабинет собирает из них строку на языке посетителя."""
    return {
        "metric": "whale",
        "chain": str(hit.get("chain") or ""),
        "direction": str(hit.get("direction") or ""),
        "venue": str(hit.get("venue") or ""),
        "venue_name": str(hit.get("venue_name") or ""),
        "symbol": str(hit.get("symbol") or ""),
        "usd": _num(hit.get("usd")),
        "amount": _num(hit.get("amount")),
        "threshold": _num(hit.get("threshold")),
        "cooldown_min": int(_num(hit.get("cooldown_min"))),
        "hash": str(hit.get("hash") or ""),
        "ts": _num(hit.get("ts")),
    }


def _escape(value: Any) -> str:
    """HTML-разметка Telegram: экранируем всё, что пришло из внешних меток."""
    text = str(value if value is not None else "")
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def signal_html(hit: dict, site_url: str = "") -> str:
    """Письмо в Telegram. Только теги, которые принимает бот (parse_mode=HTML)."""
    place = hit.get("venue_name") or hit.get("label") or "Без метки биржи"
    arrow = {"inflow": "приток на биржу", "outflow": "отток с биржи"}.get(
        str(hit.get("direction")), "перевод")
    network = SIGNAL_NETWORK_TITLES.get(hit.get("chain"), hit.get("chain"))
    rows = [f"🐋 <b>{_escape(place)} · {arrow}</b>",
            f"<b>{signal_money(hit.get('usd'))}</b> · {_escape(hit.get('symbol'))} "
            f"({_escape(signal_amount(hit.get('amount')))}) · {_escape(network)}",
            f"порог {signal_money(hit.get('threshold'))}"]
    if hit.get("cooldown_min"):
        rows[-1] += f" · пауза {int(hit['cooldown_min'])} мин"
    tx = str(hit.get("hash") or "")
    if tx:
        short = tx[:10] + "…" + tx[-6:] if len(tx) > 18 else tx
        link = tx_link(hit, site_url)
        rows.append(f'<a href="{_escape(link)}">{_escape(short)}</a>' if link
                    else f"<code>{_escape(short)}</code>")
    return "\n".join(rows)


def signal_amount(value: Any) -> str:
    n = _num(value)
    if not n:
        return "0"
    if abs(n) >= 1_000:
        return f"{n:,.0f}"
    return f"{n:,.4f}".rstrip("0").rstrip(".")


def tx_link(hit: dict, site_url: str = "") -> str:
    """Ссылка на перевод: страница Скринера с фильтром по сети, если есть."""
    base = str(site_url or "").rstrip("/")
    if not base:
        return ""
    chain = str(hit.get("chain") or "")
    return f"{base}/screener" + (f"?chain={chain}" if chain in SIGNAL_NETWORKS else "")
