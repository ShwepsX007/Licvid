"""
market_feed.py — реальные рыночные данные для LiqScope Terminal.

Модуль полностью самодостаточный (нужен только aiohttp) и НЕ зависит от
телеграм-бота. Он даёт три вещи:

  1. Список торгуемых монет (USDT-перпетуалы), автоматически подтянутый
     с биржи и отсортированный по обороту за 24ч.
  2. Живой поток ликвидаций с четырёх бирж по публичным WebSocket:
        Binance  — !forceOrder@arr           (все символы одним стримом)
        Bybit    — allLiquidation.<SYMBOL>   (подписка по символам)
        OKX      — liquidation-orders SWAP   (все свопы одним каналом)
        Gate.io  — futures.public_liquidates (подписка по контрактам)
  3. Живые цены и 1-минутные свечи (Binance kline_1m стрим,
     резерв — опрос REST-тикеров Bybit), плюс исторические свечи
     через REST (Binance → Bybit → OKX, что первым ответит).

Всё общение с внешним миром обёрнуто в переподключения с backoff,
а состояние каждого источника видно через .status() — оно отдаётся
в /api/health, чтобы на сервере было сразу видно, кто именно молчит.

Направление ликвидации нормализуем к позиции, которую вынесло:
    side = "LONG"  — ликвидировали лонг  (принудительная ПРОДАЖА)
    side = "SHORT" — ликвидировали шорт  (принудительная ПОКУПКА)
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import socket
import time
from typing import Awaitable, Callable, Dict, Iterable, List, Optional

import aiohttp
from oi_feed import OpenInterestTracker

log = logging.getLogger("liqscope.feed")

HERE = os.path.dirname(os.path.abspath(__file__))
# Часовые срезы оборота монет — для среднего за неделю (volAvg7d).
# От него клиент масштабирует пороги «крупности» ликвидаций/CVD/OI:
# что для BTC пыль, для GRAM — кит.
VOL_HIST_FILE = os.getenv("LIQSCOPE_VOL_HISTORY_FILE",
                          os.path.join(HERE, "data", "vol_history.json")).strip()
if VOL_HIST_FILE.lower() in ("0", "none", "off", "false"):
    VOL_HIST_FILE = ""
VOL_HIST_KEEP_SEC = 7 * 86400 + 3600   # ~7 суток + допуск
VOL_HIST_KEEP_N = 200                  # не больше срезов на монету
VOL_HIST_MIN_SAMPLES = 3               # меньше — шлём текущий volume24h

# ----------------------------------------------------------------------------
# Эндпоинты
# ----------------------------------------------------------------------------
BINANCE_REST = "https://fapi.binance.com"
BINANCE_WS = "wss://fstream.binance.com/stream?streams="
BINANCE_WS_RAW = "wss://fstream.binance.com/ws"   # для SUBSCRIBE/UNSUBSCRIBE на лету
BYBIT_REST = "https://api.bybit.com"
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
OKX_REST = "https://www.okx.com"
OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"
GATE_REST = "https://api.gateio.ws/api/v4/futures/usdt"
GATE_WS = "wss://fx-ws.gateio.ws/v4/ws/usdt"
# Bitget: публичный канал ликвидаций появился в UTA v3 (ноябрь 2025)
BITGET_WS = "wss://ws.bitget.com/v3/ws/public"
BITGET_REST = "https://api.bitget.com"
# HTX (Huobi) USDT-M: public.*.liquidation_orders, кадры gzip
HTX_WS = "wss://api.hbdm.com/linear-swap-notification"
# BitMEX: таблица liquidation
BITMEX_WS = "wss://ws.bitmex.com/realtime?subscribe=liquidation"
BITMEX_REST = "https://www.bitmex.com/api/v1"
HL_REST = "https://api.hyperliquid.xyz"
HL_WS = "wss://api.hyperliquid.xyz/ws"
# Hyperliquid закрывает соединение, если в течение ~60 секунд не было обмена
# сообщениями. Пинг шлём раз в 50 секунд (как официальный python-sdk) ОТДЕЛЬНОЙ
# задачей, а не «по простою»: на топ-40 монет поток trades почти не замолкает,
# receive-таймаут не срабатывает, и пинг, завязанный на него, не уходит вовсе.
HL_PING_INTERVAL = float(os.getenv("LIQSCOPE_HL_PING_SEC", "50"))
# Сторож зомби-сокетов: если от биржи нет НИЧЕГО (ни ленты, ни subscriptionResponse,
# ни pong на наши пинги) дольше HL_STALE_AFTER секунд — TCP почти наверняка тихо
# потерян (обрыв на CF-эдже/NAT, смена маршрута). aiohttp такой сокет от «тихой
# ленты» не отличает: receive() просто таймаутит, а send() пишет в буфер без
# ошибок, и слушатель висит на мёртвом соединении вечно. Поэтому при превышении
# порога соединение закрываем принудительно — супервайзер переподключится.
# Pong на наш пинг — входящее сообщение, так что живое соединение порога не
# достигнет (молчание максимум HL_PING_INTERVAL секунд).
HL_STALE_AFTER = float(os.getenv("LIQSCOPE_HL_STALE_SEC",
                                 str(HL_PING_INTERVAL * 3)))

# Запасной список монет, если ни одна биржа не ответила на REST
FALLBACK_SYMBOLS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "DOGE_USDT", "BNB_USDT",
    "ADA_USDT", "AVAX_USDT", "LINK_USDT", "TON_USDT", "TRX_USDT", "DOT_USDT",
    "MATIC_USDT", "NEAR_USDT", "LTC_USDT", "BCH_USDT", "APT_USDT", "SUI_USDT",
    "ARB_USDT", "OP_USDT", "PEPE_USDT", "SHIB_USDT", "WIF_USDT", "INJ_USDT",
    "FIL_USDT", "ATOM_USDT", "UNI_USDT", "AAVE_USDT", "ETC_USDT", "HBAR_USDT",
    "SEI_USDT", "TIA_USDT", "RUNE_USDT", "ORDI_USDT", "FTM_USDT", "GALA_USDT",
    "CRV_USDT", "LDO_USDT", "STX_USDT", "ENA_USDT",
]

TF_MINUTES = [1, 5, 15, 60, 240]


# ----------------------------------------------------------------------------
# Символы: канонический вид "BTC_USDT"
# ----------------------------------------------------------------------------
def canon(symbol: str) -> str:
    """Приводит символ любой биржи к каноническому 'BTC_USDT'."""
    s = str(symbol or "").upper().replace("-SWAP", "").replace("-", "_")
    if "_" not in s:
        for quote in ("USDT", "USDC", "USD"):
            if s.endswith(quote) and len(s) > len(quote):
                s = f"{s[:-len(quote)]}_{quote}"
                break
    return s


def canon_bitmex(symbol: str) -> str:
    """XBTUSD / XBTUSDT / ETHUSD_250627 -> BTC_USDT / ETH_USDT.

    Инверсные USD-контракты сводим к _USDT: это тот же рынок того же актива,
    и в общей ленте их логично считать вместе.
    """
    s = str(symbol or "").upper().split("_")[0]
    if not s:
        return ""
    s = s.replace("XBT", "BTC")
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}_USDT"
    return canon(s)


def to_binance(symbol: str) -> str:
    return canon(symbol).replace("_", "")


def to_bybit(symbol: str) -> str:
    return canon(symbol).replace("_", "")


def to_gate(symbol: str) -> str:
    return canon(symbol)


def to_okx(symbol: str) -> str:
    return canon(symbol).replace("_", "-") + "-SWAP"


def hl_coin_map(symbols: Iterable[str], universe: Iterable[str]) -> Dict[str, str]:
    """Монета Hyperliquid -> канонический символ.

    Дешёвые токены у HL торгуются с префиксом k (kPEPE, kSHIB): сначала ищем
    точное совпадение базы, потом k+база. Вслепую префикс не срезаем —
    есть монеты, реально начинающиеся на K (KAS).
    """
    # Ключи — в оригинальном регистре universe (kPEPE с маленькой k):
    # именно так монета приходит в подписках и сделках.
    by_upper: Dict[str, str] = {}
    for c in universe:
        by_upper.setdefault(str(c or "").upper(), str(c or ""))
    out = {}
    for sym in symbols:
        base = base_of(sym)
        if base in by_upper:
            out[by_upper[base]] = canon(sym)
        elif "K" + base in by_upper:
            out[by_upper["K" + base]] = canon(sym)
    return out


def base_of(symbol: str) -> str:
    return canon(symbol).split("_")[0]


def pretty_symbol(symbol: str) -> str:
    """BTC_USDT -> BTC/USDT."""
    return canon(symbol).replace("_", "/")


def _search_key(text) -> str:
    """GRAM_USDT, GRAMUSDT, gram/USDT → GRAMUSDT (для поиска)."""
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


# ----------------------------------------------------------------------------
# Вспомогательное
# ----------------------------------------------------------------------------
async def _get_json(session: aiohttp.ClientSession, url: str, timeout: float = 12.0):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return await resp.json(content_type=None)


def _chunks(items: List, size: int) -> Iterable[List]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ----------------------------------------------------------------------------
# CVD (cumulative volume delta) — разница объёмов агрессивных покупок/продаж
# ----------------------------------------------------------------------------
# OKX rubik принимает только конкретные окна агрегации (секунды):
OKX_CVD_SEC = {1: 60, 5: 300, 15: 900, 60: 3600, 240: 14400}


def binance_kline_cvd(row) -> Optional[float]:
    """Тейкер-дельта одной свечи Binance Futures в USDT.

    В ответе /fapi/v1/klines поле [7] — объём в котировке (USDT),
    поле [10] — taker buy quote volume (агрессивные покупки). Продажи —
    остаток. delta = buy - sell = 2*tb - total.
    """
    try:
        total = float(row[7])
        tb = float(row[10])
    except (TypeError, ValueError, IndexError):
        return None
    if total <= 0:
        return None
    return round(2.0 * tb - total, 2)


def cvd_map_from_okx_taker(rows, price_map: Dict[int, float], tf_sec: int) -> Dict[int, float]:
    """OKX /api/v5/rubik/stat/taker-volume-contract: строки [ts, sellVol, buyVol].

    Объёмы в базовой монете — переводим в USDT по close соответствующей
    свечи (price_map: время свечи -> цена). Возвращает {bucket: delta_usd}.
    """
    out: Dict[int, float] = {}
    for r in rows or []:
        try:
            ts = int(r[0]) // 1000
            sell = float(r[1])
            buy = float(r[2])
        except (TypeError, ValueError, IndexError):
            continue
        bucket = (ts // tf_sec) * tf_sec if tf_sec > 0 else ts
        px = price_map.get(bucket)
        if not px or px <= 0:
            continue
        out[bucket] = round(out.get(bucket, 0.0) + (buy - sell) * px, 2)
    return out


# ----------------------------------------------------------------------------
# Чистые парсеры сообщений бирж (без сети — можно тестировать офлайн)
# Каждый возвращает список словарей:
#   {"symbol", "side" ("LONG"/"SHORT"), "price", "qty", "ts"}
# side — какую ПОЗИЦИЮ вынесло:
#   LONG  = принудительная продажа
#   SHORT = принудительная покупка
# ----------------------------------------------------------------------------
def parse_binance_msg(payload: dict) -> List[dict]:
    """!forceOrder@arr: o.S == SELL → ликвидирован LONG."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict) or data.get("e") != "forceOrder":
        return []
    o = data.get("o") or {}
    try:
        qty = float(o.get("z") or o.get("l") or o.get("q") or 0)
        price = float(o.get("ap") or o.get("p") or 0)
        ts = float(o.get("T") or data.get("E") or 0) / 1000.0
    except (TypeError, ValueError):
        return []
    if qty <= 0 or price <= 0:
        return []
    return [{
        "symbol": canon(o.get("s", "")),
        "side": "LONG" if str(o.get("S", "")).upper() == "SELL" else "SHORT",
        "price": price, "qty": qty, "ts": ts or time.time(),
    }]


def parse_bybit_msg(payload: dict) -> List[dict]:
    """allLiquidation.<SYM>: поле S — сторона ПОЗИЦИИ, Buy → вынесли LONG."""
    if not isinstance(payload, dict):
        return []
    if not str(payload.get("topic") or "").startswith("allLiquidation"):
        return []
    items = payload.get("data") or []
    if isinstance(items, dict):
        items = [items]
    out = []
    for it in items:
        try:
            price = float(it.get("p") or 0)
            qty = float(it.get("v") or 0)
            ts = float(it.get("T") or payload.get("ts") or 0) / 1000.0
        except (TypeError, ValueError):
            continue
        if price <= 0 or qty <= 0:
            continue
        out.append({
            "symbol": canon(it.get("s", "")),
            "side": "LONG" if str(it.get("S", "")).lower() == "buy" else "SHORT",
            "price": price, "qty": qty, "ts": ts or time.time(),
        })
    return out


def parse_okx_msg(payload: dict, contract_values: Optional[Dict[str, float]] = None) -> List[dict]:
    """liquidation-orders: posSide=long → вынесли LONG; sz — в контрактах."""
    if not isinstance(payload, dict):
        return []
    contract_values = contract_values or {}
    out = []
    for row in payload.get("data", []) or []:
        inst = str(row.get("instId") or "")
        mult = contract_values.get(inst) or 1.0
        for d in row.get("details", []) or []:
            try:
                price = float(d.get("bkPx") or 0)
                sz = float(d.get("sz") or 0)
                ts = float(d.get("ts") or 0) / 1000.0
            except (TypeError, ValueError):
                continue
            if price <= 0 or sz <= 0:
                continue
            pos = str(d.get("posSide") or "").lower()
            if pos in ("long", "short"):
                side = pos.upper()
            else:
                side = "LONG" if str(d.get("side", "")).lower() == "sell" else "SHORT"
            out.append({
                "symbol": canon(inst),
                "side": side,
                "price": price, "qty": sz * mult, "ts": ts or time.time(),
            })
    return out


def parse_gate_msg(payload: dict, multipliers: Optional[Dict[str, float]] = None) -> List[dict]:
    """futures.public_liquidates: size < 0 → принудительная продажа → LONG."""
    if not isinstance(payload, dict):
        return []
    if payload.get("channel") != "futures.public_liquidates":
        return []
    multipliers = multipliers or {}
    result = payload.get("result")
    if isinstance(result, dict):
        result = [result]
    out = []
    for it in result or []:
        contract = str(it.get("contract") or "").upper()
        mult = multipliers.get(contract)
        if not mult:
            continue
        try:
            size = float(it.get("size") or 0)
            price = float(it.get("price") or 0)
            ts_ms = float(it.get("time_ms") or payload.get("time_ms") or 0)
            ts = (ts_ms / 1000.0 if ts_ms
                  else float(it.get("time") or payload.get("time") or 0))
        except (TypeError, ValueError):
            continue
        if size == 0 or price <= 0:
            continue
        out.append({
            "symbol": canon(contract),
            "side": "LONG" if size < 0 else "SHORT",
            "price": price, "qty": abs(size) * mult, "ts": ts or time.time(),
        })
    return out



def parse_bitget_msg(payload: dict) -> List[dict]:
    """Bitget UTA: topic=liquidation. side=buy → ликвидирован LONG.

    amount приходит в котируемой монете (USDT), поэтому qty считаем сами.
    Пуш раз в секунду: максимум 2 записи на пару (самая крупная по лонгам
    и по шортам), поэтому это агрегат, а не каждое отдельное событие.
    """
    if not isinstance(payload, dict):
        return []
    arg = payload.get("arg") or {}
    if arg.get("topic") != "liquidation":
        return []
    out = []
    for it in payload.get("data") or []:
        try:
            price = float(it.get("price") or 0)
            amount = float(it.get("amount") or 0)     # в USDT
            ts = float(it.get("ts") or payload.get("ts") or 0) / 1000.0
        except (TypeError, ValueError):
            continue
        if price <= 0 or amount <= 0:
            continue
        out.append({
            "symbol": canon(it.get("symbol", "")),
            "side": "LONG" if str(it.get("side", "")).lower() == "buy" else "SHORT",
            "price": price,
            "qty": amount / price,
            "usd": amount,
            "ts": ts or time.time(),
        })
    return out


def parse_htx_msg(payload: dict) -> List[dict]:
    """HTX: public.*.liquidation_orders. direction=sell → ликвидирован LONG."""
    if not isinstance(payload, dict):
        return []
    if ".liquidation_orders" not in str(payload.get("topic") or ""):
        return []
    out = []
    for it in payload.get("data") or []:
        try:
            price = float(it.get("price") or 0)
            amount = float(it.get("amount") or 0)              # в монете
            turnover = float(it.get("trade_turnover") or 0)    # в USDT
            ts = float(it.get("created_at") or payload.get("ts") or 0) / 1000.0
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        qty = amount or (turnover / price if turnover else 0)
        if qty <= 0:
            continue
        out.append({
            "symbol": canon(it.get("contract_code") or it.get("symbol") or ""),
            # direction — сторона ордера ликвидации: sell = закрывали ЛОНГ
            "side": "LONG" if str(it.get("direction", "")).lower() == "sell" else "SHORT",
            "price": price,
            "qty": qty,
            "usd": turnover or None,
            "ts": ts or time.time(),
        })
    return out


def parse_bitmex_msg(payload: dict,
                     instruments: Optional[Dict[str, dict]] = None) -> List[dict]:
    """BitMEX: table=liquidation.

    Берём только action=insert — это новая заявка на ликвидацию. Дальнейшие
    update/delete по тому же orderID это её исполнение, их учитывать нельзя,
    иначе объём задвоится.
    Размер в контрактах: у инверсных (XBTUSD) 1 контракт = 1 USD,
    у линейных пересчитываем через underlyingToPositionMultiplier.
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("table") != "liquidation" or payload.get("action") != "insert":
        return []
    instruments = instruments or {}
    out = []
    for it in payload.get("data") or []:
        raw_symbol = str(it.get("symbol") or "")
        try:
            price = float(it.get("price") or 0)
            contracts = float(it.get("leavesQty") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or contracts <= 0:
            continue
        meta = instruments.get(raw_symbol) or {}
        if meta.get("inverse"):
            qty = contracts / price          # контракты номинированы в USD
            usd = contracts
        else:
            mult = meta.get("multiplier") or 0
            if mult <= 0:
                continue                     # без метаданных не гадаем
            qty = contracts * mult
            usd = qty * price
        out.append({
            "symbol": canon_bitmex(raw_symbol),
            # side — сторона заявки: Sell = вынесли ЛОНГ
            "side": "LONG" if str(it.get("side", "")).lower() == "sell" else "SHORT",
            "price": price, "qty": qty, "usd": usd,
            "ts": time.time(),
        })
    return out


def parse_hyperliquid_msg(payload: dict,
                          coin_map: Optional[Dict[str, str]] = None) -> List[dict]:
    """Hyperliquid: channel=trades, data — список сделок.

    Ликвидации — это те же сделки, но с объектом 'liquidation'
    (liquidatedUser/markPx/method); остальные игнорируем.
    side — сторона тейкера: 'A' (ask/продажа) → вынесли LONG.
    coin_map: 'BTC' -> 'BTC_USDT' (строится из universe, см. hl_coin_map).
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("channel") != "trades":
        return []
    coin_map = coin_map or {}
    out = []
    for t in payload.get("data") or []:
        if not isinstance(t, dict):
            continue
        if "liquidation" not in t:
            continue
        raw_coin = str(t.get("coin") or "")
        try:
            price = float(t.get("px") or 0)
            qty = float(t.get("sz") or 0)
            ts = float(t.get("time") or 0) / 1000.0
        except (TypeError, ValueError):
            continue
        if price <= 0 or qty <= 0:
            continue
        out.append({
            "symbol": (coin_map.get(raw_coin) or coin_map.get(raw_coin.upper()) or
                       canon(raw_coin.upper() + "_USDT")),
            "side": "LONG" if str(t.get("side") or "").upper() == "A" else "SHORT",
            "price": price, "qty": qty, "ts": ts or time.time(),
        })
    return out


def hl_close_reason(msg_type, close_code, exc, data) -> str:
    """Текст причины закрытия HL-сокета: код + исключение + данные кадра."""
    # Имя вместо числа: на Python 3.11+ str(IntEnum) печатает «257», а не «CLOSED»
    kind = getattr(msg_type, "name", None) or str(msg_type)
    return (f"hyperliquid ws {kind}: close_code={close_code} "
            f"exc={exc!r} data={str(data)[:150]}")


class SourceStatus:
    """Диагностика одного WS-источника (видна в /api/health)."""

    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.enabled = True
        self.events = 0
        self.last_event_ts = 0.0
        self.last_error = ""
        self.connected_since = 0.0
        self.reconnects = 0
        self.attempts = 0   # попыток коннекта (в т.ч. неудачных до первого up)

    def up(self):
        self.connected = True
        self.connected_since = time.time()
        self.last_error = ""

    def down(self, err: str = ""):
        if self.connected:
            self.reconnects += 1
        self.connected = False
        if err:
            self.last_error = err[:300]

    def hit(self, n: int = 1):
        self.events += n
        self.last_event_ts = time.time()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "connected": self.connected,
            "events": self.events,
            "last_event_ts": round(self.last_event_ts, 3),
            "seconds_since_event": (round(time.time() - self.last_event_ts, 1)
                                    if self.last_event_ts else None),
            "uptime_sec": (round(time.time() - self.connected_since, 1)
                           if self.connected else 0),
            "reconnects": self.reconnects,
            "attempts": self.attempts,
            "last_error": self.last_error,
        }


# ----------------------------------------------------------------------------
# Основной класс
# ----------------------------------------------------------------------------
LiqCallback = Callable[[dict], Awaitable[None]]
PriceCallback = Callable[[str, float, Optional[dict]], Awaitable[None]]
# (symbol, price, qty, timestamp) — сделка, каждый тик
# on_trade(symbol, price, qty, ts, side) — side: сторона ТЕЙКЕРА ("BUY"/"SELL"),
# для CVD; может быть "" если биржа её не сообщает.
TradeCallback = Callable[..., Awaitable[None]]


class MarketFeed:
    """Собирает реальные ликвидации и цены со всех бирж."""

    def __init__(self,
                 on_liquidation: LiqCallback,
                 on_price: PriceCallback,
                 on_trade: Optional[TradeCallback] = None,
                 symbols_limit: int = 40,
                 exchanges: Optional[Iterable[str]] = None,
                 tick_sources: Optional[Iterable[str]] = None):
        self.on_liquidation = on_liquidation
        self.on_price = on_price
        self.on_trade = on_trade
        self.symbols_limit = max(4, int(symbols_limit))
        self.enabled_exchanges = {e.lower() for e in
                                  (exchanges or ("binance", "bybit", "okx", "gate"))}

        self.symbols: List[str] = list(FALLBACK_SYMBOLS[:self.symbols_limit])
        self.symbol_meta: Dict[str, dict] = {}   # symbol -> {volume24h, price, change24h}
        # symbol -> [[ts, volume24h], ...] — часовые срезы оборота (~неделя)
        self.vol_hist: Dict[str, List[List[float]]] = {}
        self._load_vol_hist()
        self.prices: Dict[str, float] = {}
        # Пользовательские монеты, добавленные через поиск: они не выпадают
        # из списка при периодическом обновлении топа по обороту.
        self.custom_symbols: List[str] = []
        # Полный каталог всех USDT-перпетуалов (не только топ) для поиска пар,
        # которых нет в дефолтном списке (например, GRAM_USDT).
        self.symbol_index: Dict[str, dict] = {}
        self.symbol_index_source = "none"
        self._index_ready = asyncio.Event()
        self.gate_multipliers: Dict[str, float] = {}
        self.bitmex_instruments: Dict[str, dict] = {}
        # «Горячие» монеты — те, чей график сейчас открыт у клиентов.
        # По ним идёт потиковый поток сделок (aggTrade / publicTrade).
        self.hot_symbols: set = set()
        self.tick_subscriptions: set = set()
        # Порядок источников тиков; можно задать через LIQSCOPE_TICK_SOURCE,
        # если известно, что какая-то биржа на этом сервере молчит.
        self.tick_sources = [x.strip().lower() for x in
                             (tick_sources or ["binance", "binance-raw", "bybit"]) if x]
        # Биржа, с которой реально идут тики — с неё же берём и свечи,
        # чтобы цена на графике не расходилась с последним тиком.
        self.preferred_kline_source: Optional[str] = None
        self.cvd_source: Optional[str] = None   # откуда берём историю CVD (binance-kline/okx-rubik)
        self._hot_version = 0
        self._hot_changed = asyncio.Event()

        self.status: Dict[str, SourceStatus] = {
            name: SourceStatus(name)
            for name in ("binance", "bybit", "okx", "gate", "bitget", "htx",
                         "bitmex", "hyperliquid", "prices", "ticks")
        }
        for name, st in self.status.items():
            if name not in ("prices", "ticks"):
                st.enabled = name in self.enabled_exchanges

        self.oi = OpenInterestTracker(
            price_fn=lambda sym: self.prices.get(sym),
            bitmex_meta_fn=lambda: self.bitmex_instruments)

        self.hl_coin_map: Dict[str, str] = {}   # монета HL -> канон (из universe)
        self.started_at = time.time()
        self.symbols_source = "fallback"
        self._session: Optional[aiohttp.ClientSession] = None
        self._tasks: List[asyncio.Task] = []
        self._stop = asyncio.Event()

    # -- жизненный цикл ------------------------------------------------------
    async def start(self):
        connector = None
        fam = os.getenv("LIQSCOPE_FAMILY", "").strip()
        if fam in ("4", "6"):
            # Гвоздь для диагностики: бывает, хостер/промежуточная сеть режет
            # одно из семейств адресов (чаще IPv6 до Cloudflare, за которой
            # сидит Hyperliquid) — тогда каждый WS умирает 1006-сбросом, а
            # curl (happy eyeballs) при этом работает. Ограничиваем семейство
            # для всей сессии: LIQSCOPE_FAMILY=4 или LIQSCOPE_FAMILY=6.
            connector = aiohttp.TCPConnector(
                family=socket.AF_INET if fam == "4" else socket.AF_INET6)
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": "LiqScope-Terminal/4.1"},
            timeout=aiohttp.ClientTimeout(total=20),
            connector=connector,
        )
        await self.refresh_symbols()
        self._record_volumes()

        spawn = {
            "binance": self._binance_liquidations,
            "bybit": self._bybit_liquidations,
            "okx": self._okx_liquidations,
            "gate": self._gate_liquidations,
            "bitget": self._bitget_liquidations,
            "htx": self._htx_liquidations,
            "bitmex": self._bitmex_liquidations,
            "hyperliquid": self._hyperliquid_liquidations,
        }
        for name, coro in spawn.items():
            if name in self.enabled_exchanges:
                # hyperliquid жёстко режет частые переподключения (RST), поэтому
                # стартуем с длинной паузы, чтобы не продлевать лимит долбёжкой
                base = 15.0 if name == "hyperliquid" else 2.0
                kw = {"base_delay": base}
                if name == "hyperliquid":
                    # «стабильным» считаем только соединение, прожившее больше
                    # трёх «тихих» окон HL (3 × 60с); коннект — не в момент
                    # бута, а через 10с, когда остальные слушатели уже встали
                    kw["stable_uptime"] = 180.0
                    kw["initial_delay"] = 10.0
                self._tasks.append(asyncio.create_task(
                    self._supervise(name, coro, **kw), name=f"liq-{name}"))

        self.oi.bind(self._session)
        self._tasks.append(asyncio.create_task(self._price_engine(), name="prices"))
        self._tasks.append(asyncio.create_task(self._oi_engine(), name="oi"))
        if self.on_trade is not None:
            self._tasks.append(asyncio.create_task(self._trade_engine(), name="ticks"))
        self._tasks.append(asyncio.create_task(self._symbols_refresher(), name="symbols"))
        log.info("MarketFeed запущен: биржи=%s, монет=%d",
                 ",".join(sorted(self.enabled_exchanges)), len(self.symbols))

    async def stop(self):
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        if self._session and not self._session.closed:
            await self._session.close()

    def _start_syncer(self, ws, sync_fn, interval: float = 0.5, wake=None):
        """Фоновая пересинхронизация подписок.

        ВАЖНО: делать это внутри цикла чтения сообщений нельзя — пока
        подписок нет (например, при старте никто ещё не открыл график),
        сообщений тоже нет, цикл не крутится и подписка не появится
        никогда. Поэтому синхронизация живёт отдельной задачей.
        """
        async def runner():
            while not ws.closed and not self._stop.is_set():
                try:
                    await sync_fn()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.debug("syncer stopped: %s", e)
                    return
                if wake is not None:
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=interval)
                        wake.clear()
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(interval)
        return asyncio.create_task(runner())

    async def _supervise(self, name: str, factory, base_delay: float = 2.0,
                         stable_uptime: float = 90.0, initial_delay: float = 0.0):
        """Перезапускает слушателя при любой ошибке с нарастающей паузой.

        Пауза растёт до 60с, но сбрасывается к base_delay ТОЛЬКО если
        соединение прожило >= stable_uptime секунд. Раньше любой «успешный»
        коннект (даже на 2 секунды) сбрасывал паузу на минимум: биржа,
        которая рвёт соединения пачкой (Hyperliquid за частые переподключения
        отвечает RST), попадала в цикл «подключились → оборвалось → через
        base_delay снова» и не поднималась, пока долбёжка не прекращался.

        initial_delay: пауза перед ПЕРВОЙ попыткой (не влияет на ретраи) —
        чтобы слушатель коннектился не в момент бута, когда event loop
        занят рестом остальных бирж и загрузкой истории, а после него:
        наблюдаемое на старте 1006-обрывы первого коннекта HL.
        """
        delay = base_delay
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
            if self._stop.is_set():
                return
        while not self._stop.is_set():
            self.status[name].attempts += 1   # попытки видно и до первого up
            run_start = time.monotonic()
            try:
                await factory()
                uptime = time.monotonic() - run_start
                self.status[name].down("stream closed")
                if uptime >= stable_uptime:
                    delay = base_delay
                log.info("[%s] поток закрыт (проработал %.0fс)", name, uptime)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                uptime = time.monotonic() - run_start
                self.status[name].down(f"{type(e).__name__}: {e}")
                log.warning("[%s] обрыв после %.0fс работы: %s — "
                            "переподключение через %.0fс", name, uptime, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.6, 60.0)

    # -- монеты --------------------------------------------------------------
    async def refresh_symbols(self):
        """Тянет список USDT-перпетуалов, сортирует по обороту за 24ч.

        Сначала собирается ПОЛНЫЙ каталог пар (не только топ) — он нужен для
        поиска монет, которых нет в дефолтном списке. Дефолтный список — это
        верх symbols_limit по обороту, к нему всегда добавляются пользовательские.
        """
        if not self.symbol_index:
            await self._load_symbol_index()
        if self.symbol_index:
            rows = sorted(self.symbol_index.values(),
                          key=lambda r: float(r.get("volume24h") or 0),
                          reverse=True)
            top = rows[:self.symbols_limit]
            self.symbols = [r["symbol"] for r in top]
            self.symbol_meta = {r["symbol"]: dict(r) for r in top}
            for r in top:
                if float(r.get("price") or 0) > 0:
                    self.prices.setdefault(r["symbol"], float(r["price"]))
            self._merge_custom_symbols()
            self.symbols_source = f"index:{self.symbol_index_source}"
            log.info("Список монет получен (%s): %d шт (%s ...)",
                     self.symbols_source, len(self.symbols),
                     ", ".join(self.symbols[:6]))
            return

        # Каталог не собрался ни с одной биржи — запасной путь по одному источнику
        for loader, src in ((self._symbols_binance, "binance"),
                            (self._symbols_bybit, "bybit"),
                            (self._symbols_okx, "okx")):
            try:
                rows = await loader()
                if rows and len(rows) >= 10:
                    rows.sort(key=lambda r: r["volume24h"], reverse=True)
                    rows = rows[:self.symbols_limit]
                    self.symbols = [r["symbol"] for r in rows]
                    self.symbol_meta = {r["symbol"]: r for r in rows}
                    for r in rows:
                        if r.get("price"):
                            self.prices.setdefault(r["symbol"], r["price"])
                    self._merge_custom_symbols()
                    self.symbols_source = src
                    log.info("Список монет получен с %s: %d шт (%s ...)",
                             src, len(self.symbols), ", ".join(self.symbols[:6]))
                    return
            except Exception as e:
                log.warning("Не удалось получить монеты с %s: %s", src, e)
        log.warning("Ни одна биржа не отдала список монет — работаем на встроенном списке")

    def _merge_custom_symbols(self):
        """Добавляет пользовательские монеты к дефолтному топу."""
        for c in list(self.custom_symbols):
            if c not in self.symbols:
                self.symbols.append(c)
            entry = self.symbol_index.get(c)
            if entry and c not in self.symbol_meta:
                self.symbol_meta[c] = dict(entry)
            if entry and float(entry.get("price") or 0) > 0:
                self.prices.setdefault(c, float(entry["price"]))

    async def _load_symbol_index(self):
        """Собирает полный каталог USDT-перпетуалов со всех доступных бирж.

        Нужен для поиска пар, которых нет в дефолтном топ-списке (например,
        GRAM_USDT на Binance). Один доступ = максимум одна пачка REST-запросов.
        """
        loaders = (("binance", self._symbols_binance),
                   ("bybit", self._symbols_bybit),
                   ("okx", self._symbols_okx),
                   ("gate", self._symbols_gate),
                   ("bitget", self._symbols_bitget))
        index: Dict[str, dict] = {}
        loaded = []
        async def collect(name, loader):
            try:
                rows = await loader()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("[index] %s: %s", name, e)
                return None
            return rows or []

        tasks = [asyncio.create_task(collect(n, l)) for n, l in loaders]
        for (name, _), task in zip(loaders, tasks):
            try:
                rows = await task
            except asyncio.CancelledError:
                raise
            if not rows:
                continue
            loaded.append(name)
            for r in rows:
                sym = str(r.get("symbol") or "")
                if not sym:
                    continue
                row_vol = float(r.get("volume24h") or 0)
                entry = index.get(sym)
                if entry is None:
                    entry = {"symbol": sym, "base": base_of(sym), "price": 0.0,
                             "volume24h": 0.0, "change24h": 0.0, "exchanges": []}
                    index[sym] = entry
                if name not in entry["exchanges"]:
                    entry["exchanges"].append(name)
                # цена/изменение — с самой торгуемой площадки (макс. оборот)
                if float(entry["volume24h"] or 0) <= 0 or row_vol >= float(entry["volume24h"] or 0):
                    if float(r.get("price") or 0) > 0:
                        entry["price"] = float(r["price"])
                        entry["change24h"] = float(r.get("change24h") or 0)
                if row_vol > float(entry["volume24h"] or 0):
                    entry["volume24h"] = row_vol
                entry["exchanges"].sort()
        if loaded:
            self.symbol_index = index
            self.symbol_index_source = "+".join(loaded)
            log.info("[index] каталог для поиска: %d пар с бирж %s",
                     len(index), self.symbol_index_source)
        self._index_ready.set()
        return index

    async def search_symbols(self, query: str, limit: int = 20) -> dict:
        """Ищет пару по любому виду: GRAM, GRAM_USDT, GRAMUSDT, gram/usdt."""
        q = _search_key(query)
        if not self.symbol_index:
            await self._load_symbol_index()
        q_base = q[:-4] if q.endswith("USDT") else q
        results = []
        for sym, entry in self.symbol_index.items():
            skey = _search_key(sym)
            basekey = _search_key(entry.get("base") or "")
            if q in skey or q in basekey or (q_base and q_base in skey):
                results.append(entry)
        results.sort(key=lambda r: float(r.get("volume24h") or 0), reverse=True)
        return {
            "query": query,
            "results": results[:max(1, int(limit))],
            "total": len(results),
            "source": self.symbol_index_source,
            "index_size": len(self.symbol_index),
        }

    async def add_symbol(self, symbol: str, force: bool = False) -> dict:
        """Добавляет пару (например GRAM_USDT) в список и возвращает её данные.

        Пара ищется в полном каталоге бирж. Если биржа подтянула её только что
        или пара существует, она попадает и в дефолтный список, и в personal.
        force — добавить даже без подтверждения от каталога (график будет
        собран из ближайших источников, а лента начнёт принимать события).
        """
        sym = canon(symbol)
        if not sym or "_" not in sym:
            sym = f"{sym}_USDT" if sym else ""
        if not sym:
            return {"added": False, "found": False, "symbol": str(symbol or ""),
                    "message": "не указана пара"}
        if not self.symbol_index:
            await self._load_symbol_index()
        entry = self.symbol_index.get(sym)
        if entry is None:
            # запрос «GRAM» без валюты — ищем по базовой монете
            base = base_of(sym)
            cands = [e for k, e in self.symbol_index.items()
                     if _search_key(base_of(k)) == _search_key(base)]
            cands.sort(key=lambda e: float(e.get("volume24h") or 0), reverse=True)
            if cands:
                entry = cands[0]
                sym = entry["symbol"]
        if entry is None and not force:
            return {"added": False, "found": False, "symbol": sym,
                    "message": f"пара {pretty_symbol(sym)} не найдена ни на одной бирже "
                               f"({self.symbol_index_source or 'каталог пуст'})"}
        if entry is None:
            entry = {"symbol": sym, "base": base_of(sym), "price": 0.0,
                     "volume24h": 0.0, "change24h": 0.0, "exchanges": []}
        if sym not in self.symbols:
            self.symbols.append(sym)
        if sym not in self.custom_symbols:
            self.custom_symbols.append(sym)
        self.symbol_meta[sym] = dict(entry)
        if float(entry.get("price") or 0) > 0:
            self.prices[sym] = float(entry["price"])
        log.info("[index] добавлена монета %s (биржи: %s, объём 24ч: %.0f)",
                 sym, ",".join(entry.get("exchanges") or []),
                 float(entry.get("volume24h") or 0))
        return {"added": True, "found": True, "symbol": sym, "details": dict(entry)}

    async def _symbols_binance(self) -> List[dict]:
        info = await _get_json(self._session, f"{BINANCE_REST}/fapi/v1/exchangeInfo")
        perp = {
            s["symbol"] for s in info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
        }
        tickers = await _get_json(self._session, f"{BINANCE_REST}/fapi/v1/ticker/24hr")
        rows = []
        for t in tickers:
            sym = t.get("symbol")
            if sym not in perp:
                continue
            rows.append({
                "symbol": canon(sym),
                "volume24h": float(t.get("quoteVolume") or 0),
                "price": float(t.get("lastPrice") or 0),
                "change24h": float(t.get("priceChangePercent") or 0),
            })
        return rows

    async def _symbols_bybit(self) -> List[dict]:
        data = await _get_json(self._session, f"{BYBIT_REST}/v5/market/tickers?category=linear")
        rows = []
        for t in (data.get("result") or {}).get("list", []):
            sym = str(t.get("symbol") or "")
            if not sym.endswith("USDT"):
                continue
            rows.append({
                "symbol": canon(sym),
                "volume24h": float(t.get("turnover24h") or 0),
                "price": float(t.get("lastPrice") or 0),
                "change24h": float(t.get("price24hPcnt") or 0) * 100,
            })
        return rows

    async def _symbols_okx(self) -> List[dict]:
        data = await _get_json(self._session, f"{OKX_REST}/api/v5/market/tickers?instType=SWAP")
        rows = []
        for t in data.get("data", []):
            inst = str(t.get("instId") or "")
            if not inst.endswith("-USDT-SWAP"):
                continue
            last = float(t.get("last") or 0)
            rows.append({
                "symbol": canon(inst),
                "volume24h": float(t.get("volCcy24h") or 0) * (last or 1),
                "price": last,
                "change24h": 0.0,
            })
        return rows

    async def _symbols_gate(self) -> List[dict]:
        """Gate USDT-фьючерсы: тикеры содержат все контракты сразу."""
        data = await _get_json(self._session, f"{GATE_REST}/tickers")
        rows = []
        for t in data or []:
            contract = str(t.get("contract") or "")
            if not contract.endswith("_USDT"):
                continue
            rows.append({
                "symbol": canon(contract),
                "volume24h": float(t.get("volume_24h_quote")
                                   or t.get("volume_24h_usd")
                                   or t.get("volume_24h") or 0),
                "price": float(t.get("last") or 0),
                "change24h": float(t.get("change_percentage") or 0),
            })
        return rows

    async def _symbols_bitget(self) -> List[dict]:
        """Bitget USDT-фьючерсы: v2 tickers, ориентация на usdtVolume."""
        data = await _get_json(
            self._session,
            f"{BITGET_REST}/api/v2/mix/market/tickers?productType=USDT-FUTURES")
        rows = []
        for t in (data.get("data") or []):
            sym = str(t.get("symbol") or "")
            if not sym.endswith("USDT"):
                continue
            rows.append({
                "symbol": canon(sym),
                "volume24h": float(t.get("usdtVolume") or 0),
                "price": float(t.get("lastPr") or 0),
                "change24h": float(t.get("change24h") or 0),
            })
        return rows

    def vol_avg7d(self, symbol: str) -> float:
        """Среднесуточный оборот монеты за ~неделю (среднее часовых срезов).

        Каждый срез — скользящий оборот за 24ч, их среднее за неделю и есть
        типичный дневной оборот. Пока срезов мало — текущий volume24h,
        чтобы масштаб графики не прыгал на свежем сервере.
        """
        samples = self.vol_hist.get(symbol) or []
        if len(samples) >= VOL_HIST_MIN_SAMPLES:
            return sum(v for _, v in samples) / len(samples)
        try:
            return float((self.symbol_meta.get(symbol) or {}).get("volume24h") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _record_volumes(self):
        """Часовой срез оборотов топа и пользовательских монет."""
        if not self.symbol_meta:
            return
        now = time.time()
        cutoff = now - VOL_HIST_KEEP_SEC
        for sym, m in self.symbol_meta.items():
            try:
                v = float(m.get("volume24h") or 0)
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
            lst = self.vol_hist.setdefault(sym, [])
            lst.append([now, v])
            lst[:] = [p for p in lst if p[0] >= cutoff][-VOL_HIST_KEEP_N:]
        self._save_vol_hist()

    def _save_vol_hist(self):
        if not VOL_HIST_FILE:
            return
        try:
            os.makedirs(os.path.dirname(VOL_HIST_FILE), exist_ok=True)
            tmp = VOL_HIST_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.vol_hist, f)
            os.replace(tmp, VOL_HIST_FILE)
        except Exception as e:
            log.debug("vol history save: %s", e)

    def _load_vol_hist(self):
        if not VOL_HIST_FILE:
            return
        try:
            with open(VOL_HIST_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            log.debug("vol history load: %s", e)
            return
        now = time.time()
        for sym, samples in (raw or {}).items():
            try:
                keep = [[float(ts), float(v)] for ts, v in samples
                        if float(v) > 0 and now - float(ts) <= VOL_HIST_KEEP_SEC]
            except (TypeError, ValueError):
                continue
            if keep:
                self.vol_hist[str(sym)] = keep[-VOL_HIST_KEEP_N:]

    async def _symbols_refresher(self):
        while not self._stop.is_set():
            await asyncio.sleep(3600)
            try:
                # сначала обновляем полный каталог, затем — дефолтный топ
                await self._load_symbol_index()
                await self.refresh_symbols()
                self._record_volumes()
            except Exception as e:
                log.debug("symbols refresh: %s", e)

    # -- нормализация и выдача события --------------------------------------
    async def _emit(self, source: str, symbol: str, side: str,
                    price: float, qty: float, ts: float, usd: Optional[float] = None):
        sym = canon(symbol)
        if sym not in self.symbol_set:
            return
        if price <= 0 or qty <= 0:
            return
        usd_value = float(usd) if usd else price * qty
        if usd_value <= 0:
            return
        self.status[source].hit()
        event = {
            "symbol": sym,
            "exchange": source,
            "side": side,                       # LONG / SHORT — какую позицию вынесло
            "price": price,
            "qty": qty,
            "usd": usd_value,
            "timestamp": ts or time.time(),
        }
        await self.on_liquidation(event)

    @property
    def symbol_set(self) -> set:
        return set(self.symbols)

    # -- Binance -------------------------------------------------------------
    async def _binance_liquidations(self):
        url = BINANCE_WS + "!forceOrder@arr"
        st = self.status["binance"]
        async with self._session.ws_connect(url, heartbeat=20, timeout=25) as ws:
            st.up()
            log.info("[binance] подключён к !forceOrder@arr")
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue
                for ev in parse_binance_msg(payload):
                    await self._emit("binance", ev["symbol"], ev["side"],
                                     ev["price"], ev["qty"], ev["ts"])

    # -- Bybit ---------------------------------------------------------------
    async def _bybit_liquidations(self):
        st = self.status["bybit"]
        async with self._session.ws_connect(BYBIT_WS, heartbeat=20, timeout=25) as ws:
            st.up()
            subscribed = set()

            async def sync_subs():
                want = {to_bybit(s) for s in self.symbols}
                new = sorted(want - subscribed)
                for chunk in _chunks(new, 10):
                    await ws.send_json({"op": "subscribe",
                                        "args": [f"allLiquidation.{s}" for s in chunk]})
                    subscribed.update(chunk)
                    await asyncio.sleep(0.2)

            await sync_subs()
            log.info("[bybit] подписка allLiquidation на %d символов", len(subscribed))
            syncer = self._start_syncer(ws, sync_subs, 30.0)
            try:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    try:
                        payload = json.loads(msg.data)
                    except Exception:
                        continue
                    if payload.get("op") == "subscribe" and not payload.get("success", True):
                        st.last_error = str(payload.get("ret_msg"))[:200]
                        log.warning("[bybit] отказ подписки: %s", st.last_error)
                        continue
                    for ev in parse_bybit_msg(payload):
                        await self._emit("bybit", ev["symbol"], ev["side"],
                                         ev["price"], ev["qty"], ev["ts"])
            finally:
                syncer.cancel()

    # -- OKX -----------------------------------------------------------------
    async def _okx_liquidations(self):
        st = self.status["okx"]
        async with self._session.ws_connect(OKX_WS, heartbeat=20, timeout=25) as ws:
            st.up()
            await ws.send_json({"op": "subscribe",
                                "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]})
            log.info("[okx] подписка liquidation-orders SWAP")
            ct_val = await self._okx_contract_values()

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                if msg.data == "pong":
                    continue
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue
                if payload.get("event") == "error":
                    st.last_error = str(payload.get("msg"))[:200]
                    continue
                for ev in parse_okx_msg(payload, ct_val):
                    await self._emit("okx", ev["symbol"], ev["side"],
                                     ev["price"], ev["qty"], ev["ts"])

    async def _okx_contract_values(self) -> Dict[str, float]:
        """ctVal — сколько базовой монеты в одном контракте OKX."""
        out: Dict[str, float] = {}
        try:
            data = await _get_json(self._session,
                                   f"{OKX_REST}/api/v5/public/instruments?instType=SWAP")
            for it in data.get("data", []):
                try:
                    out[it["instId"]] = float(it.get("ctVal") or 1.0)
                except (TypeError, ValueError, KeyError):
                    continue
        except Exception as e:
            log.debug("okx instruments: %s", e)
        return out

    # -- Gate.io -------------------------------------------------------------
    async def _gate_liquidations(self):
        st = self.status["gate"]
        if not self.gate_multipliers:
            await self._gate_load_specs()
        async with self._session.ws_connect(GATE_WS, heartbeat=20, timeout=25) as ws:
            st.up()
            subscribed = set()

            async def sync_subs():
                want = {to_gate(s) for s in self.symbols if to_gate(s) in self.gate_multipliers}
                new = sorted(want - subscribed)
                for chunk in _chunks(new, 20):
                    await ws.send_json({
                        "time": int(time.time()),
                        "channel": "futures.public_liquidates",
                        "event": "subscribe",
                        "payload": chunk,
                    })
                    subscribed.update(chunk)
                    await asyncio.sleep(0.2)

            await sync_subs()
            log.info("[gate] подписка public_liquidates на %d контрактов", len(subscribed))
            syncer = self._start_syncer(ws, sync_subs, 30.0)
            try:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    try:
                        payload = json.loads(msg.data)
                    except Exception:
                        continue
                    if payload.get("error"):
                        st.last_error = str(payload["error"])[:200]
                        log.warning("[gate] ошибка канала: %s", st.last_error)
                        continue
                    for ev in parse_gate_msg(payload, self.gate_multipliers):
                        await self._emit("gate", ev["symbol"], ev["side"],
                                         ev["price"], ev["qty"], ev["ts"])
            finally:
                syncer.cancel()

    async def _gate_load_specs(self):
        try:
            data = await _get_json(self._session, f"{GATE_REST}/contracts")
            for c in data:
                try:
                    self.gate_multipliers[str(c["name"]).upper()] = float(c.get("quanto_multiplier") or 0)
                except (TypeError, ValueError, KeyError):
                    continue
            self.gate_multipliers = {k: v for k, v in self.gate_multipliers.items() if v > 0}
            log.info("[gate] загружено %d спецификаций контрактов", len(self.gate_multipliers))
        except Exception as e:
            log.warning("[gate] не удалось загрузить контракты: %s", e)

    # -- Цены ----------------------------------------------------------------
    async def _price_engine(self):
        """Живые цены: WS-свечи Binance, при недоступности — REST Bybit/OKX."""
        while not self._stop.is_set():
            try:
                clean_restart = await self._binance_kline_stream()
                if clean_restart:
                    # список монет изменился — сразу переподписываемся
                    continue
                self.status["prices"].down("stream closed")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status["prices"].down(f"binance ws: {e}")
                log.warning("[prices] Binance kline стрим недоступен (%s) — перехожу на REST", e)
            # Резервный источник — опрос REST-тикеров, пока не оживёт WS
            try:
                await self._rest_price_poll(duration=60)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("[prices] REST-опрос не удался: %s", e)
                await asyncio.sleep(5)

    async def _binance_kline_stream(self) -> bool:
        """Возвращает True, если поток закрыт из-за смены списка монет."""
        syms = [to_binance(s).lower() for s in self.symbols]
        if not syms:
            raise RuntimeError("нет символов")
        streams = "/".join(f"{s}@kline_1m" for s in syms)
        st = self.status["prices"]
        async with self._session.ws_connect(BINANCE_WS + streams, heartbeat=20, timeout=25) as ws:
            st.up()
            st.name = "prices:binance-ws"
            log.info("[prices] Binance kline_1m: %d символов", len(syms))
            symbols_snapshot = list(self.symbols)
            connected_at = time.time()
            got = 0
            while not self._stop.is_set():
                # список монет обновился — пересоздаём подписку
                if symbols_snapshot != self.symbols:
                    log.info("[prices] список монет изменился, переподписываюсь")
                    return True
                try:
                    msg = await ws.receive(timeout=1.0)
                except asyncio.TimeoutError:
                    # сокет открыт, но биржа молчит — уходим на REST
                    if not got and time.time() - connected_at > 30:
                        log.warning("[prices] Binance kline молчит 30с — перехожу на REST")
                        return False
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR):
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue
                data = payload.get("data", payload)
                k = data.get("k") or {}
                if not k:
                    continue
                try:
                    sym = canon(data.get("s") or k.get("s") or "")
                    candle = {
                        "time": int(k["t"]) // 1000,
                        "open": float(k["o"]),
                        "high": float(k["h"]),
                        "low": float(k["l"]),
                        "close": float(k["c"]),
                        "volume": float(k.get("q") or 0),   # оборот в USDT
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                st.hit()
                got += 1
                self.prices[sym] = candle["close"]
                await self.on_price(sym, candle["close"], candle)
        return False

    async def _rest_price_poll(self, duration: float = 60):
        """Пока WS недоступен — берём цены пачкой через REST (Bybit → Binance → OKX)."""
        deadline = time.time() + duration
        st = self.status["prices"]
        while time.time() < deadline and not self._stop.is_set():
            got = False
            for loader in (self._symbols_bybit, self._symbols_binance, self._symbols_okx):
                try:
                    rows = await loader()
                except Exception:
                    continue
                if not rows:
                    continue
                wanted = self.symbol_set
                for r in rows:
                    if r["symbol"] in wanted and r["price"] > 0:
                        self.prices[r["symbol"]] = r["price"]
                        await self.on_price(r["symbol"], r["price"], None)
                got = True
                st.hit()
                st.name = f"prices:{loader.__name__.replace('_symbols_', '')}-rest"
                break
            if not got:
                st.down("нет доступных источников цены")
            await asyncio.sleep(3)

    # -- Bitget ----------------------------------------------------------------
    async def _bitget_liquidations(self):
        """UTA v3: {"instType":"usdt-futures","topic":"liquidation"}."""
        st = self.status["bitget"]
        async with self._session.ws_connect(BITGET_WS, heartbeat=25, timeout=25) as ws:
            await ws.send_json({"op": "subscribe", "args": [
                {"instType": "usdt-futures", "topic": "liquidation"}]})
            st.up()
            log.info("[bitget] подписка на канал liquidation")
            last_ping = time.time()
            while not self._stop.is_set():
                # Bitget рвёт соединение, если клиент молчит 30 с, —
                # шлём текстовый ping независимо от входящего потока
                if time.time() - last_ping > 25:
                    await ws.send_str("ping")
                    last_ping = time.time()
                try:
                    msg = await ws.receive(timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR):
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                if msg.data == "pong":
                    continue
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue
                if payload.get("code") not in (None, 0, "0"):
                    st.last_error = str(payload.get("msg"))[:200]
                    log.warning("[bitget] ошибка подписки: %s", st.last_error)
                    continue
                for ev in parse_bitget_msg(payload):
                    await self._emit("bitget", ev["symbol"], ev["side"],
                                     ev["price"], ev["qty"], ev["ts"], usd=ev.get("usd"))

    # -- HTX (Huobi) -----------------------------------------------------------
    async def _htx_liquidations(self):
        """public.*.liquidation_orders; кадры приходят в gzip."""
        st = self.status["htx"]
        async with self._session.ws_connect(HTX_WS, heartbeat=None, timeout=25) as ws:
            await ws.send_json({"op": "sub", "cid": "liqscope",
                                "topic": "public.*.liquidation_orders"})
            st.up()
            log.info("[htx] подписка на public.*.liquidation_orders")
            while not self._stop.is_set():
                try:
                    msg = await ws.receive(timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR):
                    break
                if msg.type == aiohttp.WSMsgType.BINARY:
                    try:
                        raw = gzip.decompress(msg.data).decode("utf-8")
                    except Exception:
                        continue
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    raw = msg.data
                else:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if payload.get("op") == "ping":
                    await ws.send_json({"op": "pong", "ts": payload.get("ts")})
                    continue
                if payload.get("op") == "sub" and payload.get("err-code") not in (None, 0):
                    st.last_error = str(payload.get("err-msg"))[:200]
                    log.warning("[htx] отказ подписки: %s", st.last_error)
                    continue
                for ev in parse_htx_msg(payload):
                    await self._emit("htx", ev["symbol"], ev["side"],
                                     ev["price"], ev["qty"], ev["ts"], usd=ev.get("usd"))

    # -- BitMEX ----------------------------------------------------------------
    async def _bitmex_load_instruments(self):
        """Множители контрактов: без них не перевести контракты в монеты."""
        try:
            rows = await _get_json(self._session,
                                   f"{BITMEX_REST}/instrument/active", timeout=10)
        except Exception as e:
            log.warning("[bitmex] не удалось загрузить инструменты: %s", e)
            return
        meta = {}
        for r in rows or []:
            sym = r.get("symbol")
            if not sym:
                continue
            u2p = r.get("underlyingToPositionMultiplier") or 0
            meta[sym] = {
                "inverse": bool(r.get("isInverse")),
                "multiplier": (1.0 / u2p) if u2p else 0.0,
            }
        if meta:
            self.bitmex_instruments = meta
            log.info("[bitmex] загружено инструментов: %d", len(meta))

    async def _bitmex_liquidations(self):
        st = self.status["bitmex"]
        if not self.bitmex_instruments:
            await self._bitmex_load_instruments()
        async with self._session.ws_connect(BITMEX_WS, heartbeat=20, timeout=25) as ws:
            st.up()
            log.info("[bitmex] подписка на таблицу liquidation")
            while not self._stop.is_set():
                try:
                    msg = await ws.receive(timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR):
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue
                if payload.get("error"):
                    st.last_error = str(payload["error"])[:200]
                    log.warning("[bitmex] ошибка: %s", st.last_error)
                    continue
                for ev in parse_bitmex_msg(payload, self.bitmex_instruments):
                    await self._emit("bitmex", ev["symbol"], ev["side"],
                                     ev["price"], ev["qty"], ev["ts"], usd=ev.get("usd"))

    # -- Hyperliquid ---------------------------------------------------------
    async def _hyperliquid_load_universe(self) -> set:
        """Имена перпетуумов HL (universe из POST /info {"type": "meta"})."""
        try:
            async with self._session.post(f"{HL_REST}/info", json={"type": "meta"},
                                          timeout=12) as resp:
                data = await resp.json()
        except Exception as e:
            log.warning("[hyperliquid] не удалось загрузить universe: %s", e)
            return set()
        names = set()
        for row in (data or {}).get("universe") or []:
            name = str((row or {}).get("name") or "").upper()
            if name:
                names.add(name)
        return names

    async def _hyperliquid_liquidations(self):
        """trades-подписка на каждую монету; ликвидации помечены объектом.

        Heartbeat: Hyperliquid рвёт «тихие» соединения через ~60 секунд.
        Пинг шлётся отдельной задачей строго по таймеру (HL_PING_INTERVAL),
        независимо от входящего потока, — иначе на активной ленте trades
        receive-таймаут не срабатывает и пинг не отправляется вообще.
        """
        st = self.status["hyperliquid"]
        universe = await self._hyperliquid_load_universe()
        if universe:
            log.info("[hyperliquid] universe перпетуумов: %d", len(universe))

        def rebuild_map():
            if universe:
                self.hl_coin_map = hl_coin_map(self.symbols, universe)
            else:   # universe недоступен — подписываемся на базы как есть
                self.hl_coin_map = {base_of(s): canon(s) for s in self.symbols}

        rebuild_map()
        # Некоторые бот-фильтры перед Hyperliquid (Cloudflare) режут WS с
        # нестандартным User-Agent: если LIQSCOPE_HL_UA задан — этот участок
        # ходит под ним (заголовок перекрывает сессионный только здесь).
        hl_headers = ({"User-Agent": os.environ["LIQSCOPE_HL_UA"]}
                      if os.getenv("LIQSCOPE_HL_UA") else None)
        async with self._session.ws_connect(HL_WS, heartbeat=None, timeout=25,
                                            headers=hl_headers) as ws:
            subscribed: set = set()
            acked: set = set()
            seen_msgs = 0
            # Возраст входящего трафика: обновляется любым сообщением от биржи
            # (лента, subscriptionResponse, pong, кадры ping). Сторож в heartbeat
            # сравнивает метку с HL_STALE_AFTER — см. heartbeat().
            last_inbound = time.monotonic()
            # Причина принудительного закрытия (заполняет сторож) — попадает в
            # текст ConnectionError, чтобы в логе было видно, ПОЧЕМУ рвём.
            dead_reason = ""
            # Подписки (sync_subs) и heartbeat-пинги шлём через общий замок:
            # aiohttp не гарантирует безопасность параллельных send_json.
            send_lock = asyncio.Lock()

            async def send(payload: dict):
                async with send_lock:
                    await ws.send_json(payload)

            async def handle_text(raw: str):
                """Разобрать одно текстовое сообщение; вернуть монету, если это
                subscriptionResponse на subscribe (иначе None)."""
                nonlocal seen_msgs
                try:
                    payload = json.loads(raw)
                except Exception:
                    return None
                if seen_msgs < 3:
                    # диагностика первых секунд соединения: видно, долетает
                    # ли вообще что-то до разрыва (канал + размер + голова)
                    seen_msgs += 1
                    log.info("[hyperliquid] msg#%d: channel=%s size=%d %.150s",
                             seen_msgs, payload.get("channel", "?"),
                             len(raw), raw)
                if payload.get("channel") == "subscriptionResponse":
                    data = payload.get("data") or {}
                    blob = json.dumps(payload, ensure_ascii=False)[:300]
                    if "error" in blob.lower() or "fail" in blob.lower():
                        log.warning("[hyperliquid] ошибка подписки: %s", blob)
                    sub = data.get("subscription") or {}
                    coin = sub.get("coin")
                    if data.get("method") == "subscribe" and coin:
                        acked.add(coin)
                    return coin
                for ev in parse_hyperliquid_msg(payload, self.hl_coin_map):
                    await self._emit("hyperliquid", ev["symbol"], ev["side"],
                                     ev["price"], ev["qty"], ev["ts"])
                return None

            def raise_on_close(msg):
                # Громкий разрыв вместо тихого: код закрытия попадёт в лог
                # и в подсказку плашки, а backoff супервайзера начнёт расти
                # и перестанет долбить биржу частыми реконнектами.
                err_data = msg.data if msg.type == aiohttp.WSMsgType.ERROR else ""
                text = hl_close_reason(
                    msg.type, ws.close_code, ws.exception(), err_data)
                if dead_reason:
                    text = f"{text} — {dead_reason}"
                raise ConnectionError(text)

            async def sync_subs():
                nonlocal last_inbound
                want = set(self.hl_coin_map)
                for coin in sorted(want - subscribed):
                    await send({"method": "subscribe",
                                "subscription": {"type": "trades", "coin": coin}})
                    subscribed.add(coin)
                    # Каждую подписку подтверждаем ответом биржи (темп ~1.5/с,
                    # сокет постоянно читается — как в зонде tools/check_hyperliquid.py
                    # --bisect, который выживает 23/23, в отличие от всплеска
                    # подписок вслепую: его гейтвей HL рвёт примерно через секунду).
                    # Всё приходящее мимоходом обрабатывается штатно.
                    t_end = time.monotonic() + 2.5
                    while coin not in acked:
                        try:
                            msg = await ws.receive(
                                timeout=max(0.1, t_end - time.monotonic()))
                        except asyncio.TimeoutError:
                            break
                        last_inbound = time.monotonic()
                        if msg.type in (aiohttp.WSMsgType.CLOSED,
                                        aiohttp.WSMsgType.CLOSING,
                                        aiohttp.WSMsgType.ERROR):
                            raise_on_close(msg)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await handle_text(msg.data)
                    if coin not in acked:
                        log.debug("[hyperliquid] %s: нет subscriptionResponse "
                                  "за 2.5с — иду дальше", coin)
                for coin in sorted(subscribed - want):
                    try:
                        await send({"method": "unsubscribe",
                                    "subscription": {"type": "trades", "coin": coin}})
                    except Exception:
                        pass
                    subscribed.discard(coin)
                    acked.discard(coin)

            async def heartbeat():
                """Пинг раз в HL_PING_INTERVAL секунд, независимо от потока.

                Заодно сторож зомби-сокетов: если входящих нет уже
                HL_STALE_AFTER секунд (pong не приходит, лента молчит), TCP,
                скорее всего, тихо потерян — aiohttp не отличит такой сокет
                от «тихой ленты», поэтому закрываем принудительно: главная
                петля получит CLOSED/CLOSING, поднимет громкий ConnectionError
                (с причиной от сторожа), и супервайзер переподключится.
                """
                nonlocal last_inbound, dead_reason
                while not ws.closed and not self._stop.is_set():
                    try:
                        await asyncio.sleep(HL_PING_INTERVAL)
                    except asyncio.CancelledError:
                        raise
                    if ws.closed or self._stop.is_set():
                        return
                    age = time.monotonic() - last_inbound
                    if age > HL_STALE_AFTER:
                        dead_reason = (f"входящих нет {age:.0f}с — ни pong на "
                                       f"пинги, ни ленты; сокет мёртв")
                        log.warning("[hyperliquid] %s — принудительно закрываю",
                                    dead_reason)
                        try:
                            # close() из сторонней задачи безопасен: aiohttp
                            # сам разбудит висящий receive() (см. client_ws)
                            await ws.close()
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass
                        return
                    try:
                        await send({"method": "ping"})
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        # сокет умер — цикл чтения сам поднимет громкую ошибку
                        log.debug("[hyperliquid] heartbeat ping не ушёл: %s", e)
                        return

            hb = asyncio.create_task(heartbeat(), name="hl-heartbeat")
            try:
                await sync_subs()
                st.up()
                log.info("[hyperliquid] подписка trades на %d монет", len(subscribed))
                loops = 0
                while not self._stop.is_set():
                    try:
                        msg = await ws.receive(timeout=30.0)
                    except asyncio.TimeoutError:
                        loops += 1
                        if loops % 120 == 1:   # ~раз в час: новые HIP-3 маркеты
                            universe = await self._hyperliquid_load_universe() or universe
                        rebuild_map()
                        try:
                            await sync_subs()
                        except Exception:
                            pass
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                    aiohttp.WSMsgType.ERROR):
                        raise_on_close(msg)
                    # любой входящий кадр — признак живой трубы (включая
                    # протокольные ping/pong, не только TEXT)
                    last_inbound = time.monotonic()
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    await handle_text(msg.data)
            finally:
                hb.cancel()
                try:
                    await hb
                except (asyncio.CancelledError, Exception):
                    pass

    # -- Потиковый поток сделок (для графика) --------------------------------
    def set_hot_symbols(self, symbols: Iterable[str]):
        """Монеты, чьи графики открыты у клиентов — по ним нужен каждый тик."""
        new = {canon(s) for s in (symbols or []) if s}
        if new != self.hot_symbols:
            self.hot_symbols = new
            self._hot_version += 1
            self._hot_changed.set()
            log.info("[ticks] горячие монеты: %s",
                     ", ".join(sorted(new)) if new else "нет")


    async def _oi_engine(self):
        """Живой опрос OI всех 7 бирж по тёплым символам (график/статистика).

        История подтягивается лениво (TTL 10 мин), опрос — раз в 30 c;
        символы опрашиваем по очереди, биржи внутри символа — параллельно.
        """
        from oi_feed import SAMPLE_INTERVAL
        await asyncio.sleep(5)   # дать ценам и инструментам подтянуться
        while not self._stop.is_set():
            try:
                for sym in list(self.hot_symbols)[:6]:
                    self.oi.watch(sym)
                for sym in self.oi.watched_symbols():
                    if self._stop.is_set():
                        break
                    try:
                        await self.oi.backfill_symbol(sym)   # TTL-сторож внутри
                        await self.oi.sample_symbol(sym)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.debug("oi engine %s: %s", sym, e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug("oi engine: %s", e)
            try:
                await asyncio.sleep(SAMPLE_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _trade_engine(self):
        """Каждая сделка по открытым графикам.

        Источники пробуются по кругу, пока какой-нибудь реально не начнёт
        отдавать сделки:
          1) Binance combined  wss://fstream.binance.com/stream?streams=..@aggTrade
             (тот же механизм, что и у работающего стрима ликвидаций)
          2) Binance raw + SUBSCRIBE  wss://fstream.binance.com/ws
          3) Bybit publicTrade
        Если источник подключился, но за NO_DATA_TIMEOUT секунд не прислал
        ни одной сделки — он считается «молчащим», и движок переходит к
        следующему. Это защищает от ситуации «сокет открыт, данных нет».
        """
        available = {
            "binance": ("binance-combined", self._binance_trade_combined, "binance"),
            "binance-raw": ("binance-raw", self._binance_trade_raw, "binance"),
            "bybit": ("bybit-publicTrade", self._bybit_trade_stream, "bybit"),
        }
        sources = [available[x] for x in self.tick_sources if x in available]
        if not sources:
            sources = list(available.values())
        log.info("[ticks] порядок источников: %s", ", ".join(x[0] for x in sources))
        idx = 0
        delay = 1.0
        while not self._stop.is_set():
            name, fn, exchange = sources[idx % len(sources)]
            result = "error"
            try:
                result = await fn()
                if result in ("closed", "restart"):
                    self.preferred_kline_source = exchange
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status["ticks"].down(f"{name}: {e}")
                log.warning("[ticks] %s недоступен: %s", name, e)

            if result == "restart":          # сменились монеты — тот же источник
                delay = 1.0
                continue
            if result == "closed":           # источник рабочий, просто оборвался
                self.status["ticks"].down(f"{name}: соединение закрыто")
                delay = 1.0
                await asyncio.sleep(1.0)
                continue

            if result == "nodata":
                log.warning("[ticks] %s: подключились, но сделок нет — "
                            "переключаюсь на следующий источник", name)
                self.status["ticks"].down(f"{name}: молчит")
            idx += 1
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 20.0)

    # Сколько ждать первую сделку, прежде чем признать источник молчащим
    NO_DATA_TIMEOUT = 20.0

    def _handle_trade_payload(self, payload) -> List[tuple]:
        """Достаёт сделки из сообщения Binance (raw и combined формат).

        Возвращает (symbol, price, qty, ts, side) — side это сторона
        ТЕЙКЕРА: "BUY" = били по аску (агрессивная покупка), "SELL" = били
        по биду. Поле m — «покупатель был мейкером»: если m=true, значит
        агрессор — продавец. Нужна для CVD (разницы покупок и продаж).
        """
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") if "data" in payload else payload
        if not isinstance(data, dict):
            return []
        if data.get("e") not in ("aggTrade", "trade"):
            return []
        try:
            sym = canon(data.get("s") or "")
            price = float(data.get("p") or 0)
            qty = float(data.get("q") or 0)
            ts = float(data.get("T") or data.get("E") or 0) / 1000.0
        except (TypeError, ValueError):
            return []
        if not sym or price <= 0:
            return []
        side = "SELL" if data.get("m") else "BUY"
        return [(sym, price, qty, ts or time.time(), side)]

    async def _binance_trade_combined(self) -> str:
        """aggTrade через combined-стрим; при смене монет — переподключение."""
        st = self.status["ticks"]
        syms = sorted(to_binance(x).lower() for x in self.hot_symbols)
        if not syms:
            # графиков нет — ждём появления, соединение не держим
            self._hot_changed.clear()
            try:
                await asyncio.wait_for(self._hot_changed.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            return "restart"

        url = BINANCE_WS + "/".join(f"{x}@aggTrade" for x in syms)
        version = self._hot_version
        got = 0
        async with self._session.ws_connect(url, heartbeat=20, timeout=25) as ws:
            st.up()
            st.name = "ticks:binance-combined"
            self.tick_subscriptions = set(syms)
            log.info("[ticks] Binance combined aggTrade: %s", ", ".join(syms))
            connected_at = time.time()
            while not self._stop.is_set():
                if version != self._hot_version:
                    log.info("[ticks] список графиков изменился — переподписка")
                    return "restart"
                try:
                    msg = await ws.receive(timeout=1.0)
                except asyncio.TimeoutError:
                    if not got and time.time() - connected_at > self.NO_DATA_TIMEOUT:
                        return "nodata"
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR):
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue
                trades = self._handle_trade_payload(payload)
                if not trades:
                    self._log_unexpected("binance-combined", payload)
                    continue
                for sym, price, qty, ts, side in trades:
                    got += 1
                    st.hit()
                    self.prices[sym] = price
                    await self.on_trade(sym, price, qty, ts, side)
        return "closed" if got else "nodata"

    async def _binance_trade_raw(self) -> str:
        """aggTrade через raw-эндпоинт с динамическими SUBSCRIBE/UNSUBSCRIBE."""
        st = self.status["ticks"]
        req_id = 1
        subscribed: set = set()
        got = 0

        async with self._session.ws_connect(BINANCE_WS_RAW, heartbeat=20, timeout=25) as ws:
            st.up()
            st.name = "ticks:binance-raw"
            log.info("[ticks] Binance raw aggTrade подключён")
            connected_at = time.time()

            async def sync():
                nonlocal req_id
                want = {to_binance(x).lower() for x in self.hot_symbols}
                add, drop = want - subscribed, subscribed - want
                if add:
                    await ws.send_json({"method": "SUBSCRIBE",
                                        "params": [f"{x}@aggTrade" for x in sorted(add)],
                                        "id": req_id})
                    req_id += 1
                    subscribed.update(add)
                    log.info("[ticks] SUBSCRIBE %s", ", ".join(sorted(add)))
                if drop:
                    await ws.send_json({"method": "UNSUBSCRIBE",
                                        "params": [f"{x}@aggTrade" for x in sorted(drop)],
                                        "id": req_id})
                    req_id += 1
                    subscribed.difference_update(drop)
                self.tick_subscriptions = set(subscribed)

            syncer = self._start_syncer(ws, sync, 1.0, wake=self._hot_changed)
            try:
                while not self._stop.is_set():
                    try:
                        msg = await ws.receive(timeout=1.0)
                    except asyncio.TimeoutError:
                        if (not got and subscribed
                                and time.time() - connected_at > self.NO_DATA_TIMEOUT):
                            return "nodata"
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                    aiohttp.WSMsgType.ERROR):
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        payload = json.loads(msg.data)
                    except Exception:
                        continue
                    trades = self._handle_trade_payload(payload)
                    if not trades:
                        self._log_unexpected("binance-raw", payload)
                        continue
                    for sym, price, qty, ts, side in trades:
                        got += 1
                        st.hit()
                        self.prices[sym] = price
                        await self.on_trade(sym, price, qty, ts, side)
            finally:
                syncer.cancel()
                self.tick_subscriptions = set()
        return "closed" if got else "nodata"

    def _log_unexpected(self, source: str, payload):
        """Первые несколько «непонятных» ответов биржи — в лог, для диагностики."""
        key = f"_unexpected_{source}"
        seen = getattr(self, key, 0)
        if seen >= 3:
            return
        setattr(self, key, seen + 1)
        text = json.dumps(payload, ensure_ascii=False)[:300]
        if "error" in text or "msg" in text:
            log.warning("[ticks] %s ответил: %s", source, text)
        else:
            log.info("[ticks] %s служебное сообщение: %s", source, text)

    async def _bybit_trade_stream(self) -> str:
        """Резерв: publicTrade.<SYMBOL> на Bybit."""
        st = self.status["ticks"]
        subscribed: set = set()
        got = 0
        async with self._session.ws_connect(BYBIT_WS, heartbeat=20, timeout=25) as ws:
            st.up()
            st.name = "ticks:bybit-publicTrade"
            log.info("[ticks] Bybit publicTrade подключён")
            connected_at = time.time()

            async def sync():
                want = {to_bybit(x) for x in self.hot_symbols}
                add, drop = want - subscribed, subscribed - want
                for chunk in _chunks(sorted(add), 10):
                    await ws.send_json({"op": "subscribe",
                                        "args": [f"publicTrade.{x}" for x in chunk]})
                    subscribed.update(chunk)
                for chunk in _chunks(sorted(drop), 10):
                    await ws.send_json({"op": "unsubscribe",
                                        "args": [f"publicTrade.{x}" for x in chunk]})
                    subscribed.difference_update(chunk)
                self.tick_subscriptions = set(subscribed)

            syncer = self._start_syncer(ws, sync, 1.0, wake=self._hot_changed)
            try:
                while not self._stop.is_set():
                    try:
                        msg = await ws.receive(timeout=1.0)
                    except asyncio.TimeoutError:
                        if (not got and subscribed
                                and time.time() - connected_at > self.NO_DATA_TIMEOUT):
                            return "nodata"
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                    aiohttp.WSMsgType.ERROR):
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        payload = json.loads(msg.data)
                    except Exception:
                        continue
                    if not str(payload.get("topic") or "").startswith("publicTrade"):
                        if payload.get("success") is False:
                            log.warning("[ticks] Bybit отказ: %s", payload.get("ret_msg"))
                        continue
                    now = time.time()
                    for it in payload.get("data") or []:
                        try:
                            sym = canon(it.get("s") or "")
                            price = float(it.get("p") or 0)
                            qty = float(it.get("v") or 0)
                            ts = float(it.get("T") or 0) / 1000.0 or now
                        except (TypeError, ValueError):
                            continue
                        if price <= 0:
                            continue
                        side = ("BUY" if str(it.get("S") or "").upper() == "BUY"
                                else "SELL" if str(it.get("S") or "").upper() == "SELL" else "")
                        got += 1
                        st.hit()
                        self.prices[sym] = price
                        await self.on_trade(sym, price, qty, ts, side)
            finally:
                syncer.cancel()
                self.tick_subscriptions = set()
        return "closed" if got else "nodata"

    # -- Исторические свечи ---------------------------------------------------
    async def fetch_klines(self, symbol: str, tf_min: int, limit: int = 300) -> Optional[List[dict]]:
        """Реальные свечи: Binance → Bybit → OKX, что первым отдаст.

        Если тики идут с конкретной биржи, её же ставим первой — иначе
        свечи одной биржи и тики другой дают небольшое расхождение цены.
        """
        loaders = {"binance": self._klines_binance,
                   "bybit": self._klines_bybit,
                   "okx": self._klines_okx}
        order = [self._klines_binance, self._klines_bybit, self._klines_okx]
        preferred = loaders.get(self.preferred_kline_source or "")
        if preferred is not None:
            order = [preferred] + [x for x in order if x is not preferred]
        for loader in order:
            try:
                candles = await loader(symbol, tf_min, limit)
                if candles:
                    return candles
            except Exception as e:
                log.debug("klines %s %s: %s", loader.__name__, symbol, e)
        return None

    async def fetch_cvd(self, symbol: str, tf_min: int,
                        candles: Optional[List[dict]] = None,
                        limit: int = 300) -> Optional[Dict[int, float]]:
        """История тейкер-дельты {время_свечи: delta_usd}, USDT.

        Источники (по порядку доступности):
          1. Binance — родные поля taker buy volume в ответе kline, любой ТФ;
          2. OKX     — публичная статистика rubik/taker-volume-contract
                       (объёмы в базовой монете → пересчёт по close свечей).
        Bybit/Bitget/HTX/Gate публичной исторической CVD-статистики не дают —
        по ним дельта текущей свечи накапливается из ленты сделок (on_trade).
        """
        # 1) Binance
        try:
            tf_map = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h"}
            url = (f"{BINANCE_REST}/fapi/v1/klines?symbol={to_binance(symbol)}"
                   f"&interval={tf_map.get(tf_min, '5m')}&limit={min(max(limit, 1), 1000)}")
            rows = await _get_json(self._session, url, timeout=8)
            if isinstance(rows, list) and rows:
                out = {}
                for r in rows:
                    if not isinstance(r, (list, tuple)):
                        continue
                    d = binance_kline_cvd(r)
                    if d is not None:
                        out[int(r[0]) // 1000] = d
                if out:
                    self.cvd_source = "binance-kline"
                    return out
        except Exception as e:
            log.debug("cvd binance %s: %s", symbol, e)
        # 2) OKX
        try:
            sec = OKX_CVD_SEC.get(tf_min)
            if sec:
                url = (f"{OKX_REST}/api/v5/rubik/stat/taker-volume-contract"
                       f"?instId={to_okx(symbol)}&sec={sec}")
                data = await _get_json(self._session, url, timeout=8)
                rows = (data or {}).get("data") or []
                price_map = {int(c["time"]): float(c.get("close") or 0)
                             for c in (candles or [])}
                out = cvd_map_from_okx_taker(rows, price_map, sec)
                if out:
                    self.cvd_source = "okx-rubik"
                    return out
        except Exception as e:
            log.debug("cvd okx %s: %s", symbol, e)
        return None

    async def _klines_binance(self, symbol: str, tf_min: int, limit: int) -> Optional[List[dict]]:
        tf_map = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h"}
        url = (f"{BINANCE_REST}/fapi/v1/klines?symbol={to_binance(symbol)}"
               f"&interval={tf_map.get(tf_min, '5m')}&limit={min(limit, 1000)}")
        rows = await _get_json(self._session, url, timeout=8)
        out = [{
            "time": int(r[0]) // 1000,
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]),
            "volume": float(r[7]),      # quote volume (USDT)
            "cvd": binance_kline_cvd(r),   # тейкер-дельта в USDT (Buy-Sell)
        } for r in rows]
        if any(c["cvd"] is not None for c in out):
            self.cvd_source = "binance-kline"
        return out

    async def _klines_bybit(self, symbol: str, tf_min: int, limit: int) -> Optional[List[dict]]:
        url = (f"{BYBIT_REST}/v5/market/kline?category=linear&symbol={to_bybit(symbol)}"
               f"&interval={tf_min}&limit={min(limit, 1000)}")
        data = await _get_json(self._session, url, timeout=8)
        rows = (data.get("result") or {}).get("list") or []
        out = [{
            "time": int(r[0]) // 1000,
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]),
            "volume": float(r[6]),
        } for r in rows]
        out.sort(key=lambda c: c["time"])
        return out

    async def _klines_okx(self, symbol: str, tf_min: int, limit: int) -> Optional[List[dict]]:
        tf_map = {1: "1m", 5: "5m", 15: "15m", 60: "1H", 240: "4H"}
        url = (f"{OKX_REST}/api/v5/market/candles?instId={to_okx(symbol)}"
               f"&bar={tf_map.get(tf_min, '5m')}&limit={min(limit, 300)}")
        data = await _get_json(self._session, url, timeout=8)
        rows = data.get("data") or []
        out = [{
            "time": int(r[0]) // 1000,
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]),
            "volume": float(r[7]) if len(r) > 7 else float(r[5]),
        } for r in rows]
        out.sort(key=lambda c: c["time"])
        return out

    # -- Диагностика ---------------------------------------------------------
    def health(self) -> dict:
        return {
            "uptime_sec": round(time.time() - self.started_at, 1),
            "symbols_count": len(self.symbols),
            "symbols_source": self.symbols_source,
            "custom_symbols": list(self.custom_symbols),
            "catalog_count": len(self.symbol_index),
            "catalog_source": self.symbol_index_source,
            "cvd_source": self.cvd_source,
            "sources": {k: v.as_dict() for k, v in self.status.items()},
        }
