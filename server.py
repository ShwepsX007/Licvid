"""
Licvid Web Server — терминал ликвидаций в реальном времени.

Что отдаёт наружу:
    GET  /                  — лендинг (посадочная страница)
    GET  /terminal          — сам терминал
    GET  /api/symbols       — список монет (авто-подбор по обороту) + цены
    GET  /api/klines        — реальные свечи (Binance → Bybit → OKX)
    GET  /api/liquidations  — история ликвидаций из памяти
    GET  /api/stats         — агрегаты (лонги/шорты, топ монет, биржи)
    GET  /api/health        — состояние каждого WS-источника (для диагностики)
    WS   /ws                — живой поток: ликвидации, цены, свечи, статистика

Все данные — настоящие, с публичных WS бирж (ключи не нужны).
Демо-режим (синтетические события) включается только явно:
    LICVID_DEMO=1  — и тогда фронтенд честно рисует бейдж «DEMO».

Переменные окружения:
    LICVID_SYMBOLS_LIMIT  сколько монет держать в списке (по умолчанию 40)
    LICVID_EXCHANGES      binance,bybit,okx,gate,bitget,htx,bitmex (по умолчанию все)
    LICVID_TICK_SOURCE    порядок источников тиков: binance,binance-raw,bybit
    LICVID_DEMO           1 — генерировать тестовый поток вместо биржевого
    LICVID_HISTORY_MAX    сколько событий держать в памяти (по умолчанию 60000)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Deque, Dict, List, Optional, Set

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from market_feed import MarketFeed, TF_MINUTES, base_of, canon
from oi_feed import map_candles_to_oi

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("licvid.server")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

SYMBOLS_LIMIT = int(os.getenv("LICVID_SYMBOLS_LIMIT", "40"))
EXCHANGES = [e.strip().lower() for e in
             os.getenv("LICVID_EXCHANGES",
                       "binance,bybit,okx,gate,bitget,htx,bitmex").split(",")
             if e.strip()]
# Порядок источников потиковых данных для графика (первый рабочий побеждает)
TICK_SOURCES = [x.strip().lower() for x in
                os.getenv("LICVID_TICK_SOURCE", "binance,binance-raw,bybit").split(",")
                if x.strip()]
DEMO_MODE = os.getenv("LICVID_DEMO", "0").strip() in ("1", "true", "yes", "on")
HISTORY_MAX = int(os.getenv("LICVID_HISTORY_MAX", "60000"))
# Дисковое сохранение истории ликвидаций (переживает рестарт сервера).
# Путь: LICVID_HISTORY_FILE ("" / "0" / "off" — отключить), TTL — сколько часов
# держать при загрузке/урезании файла.
HISTORY_FILE = os.getenv("LICVID_HISTORY_FILE",
                         os.path.join(HERE, "data", "liq_history.jsonl")).strip()
if HISTORY_FILE.lower() in ("0", "none", "off", "false"):
    HISTORY_FILE = ""
HISTORY_TTL_HOURS = float(os.getenv("LICVID_HISTORY_TTL_HOURS", "24"))
HISTORY_FILE_MAX_BYTES = 64 * 1024 * 1024   # страховка: урезаем файл при разрастании

KLINE_TTL = 20.0            # сек: как часто перезапрашивать историю с биржи
# Ликвидации уходят клиенту сразу; интервал — только предохранитель от флуда
# при лавине событий (0 = слать каждое событие немедленно).
BROADCAST_INTERVAL = float(os.getenv("LICVID_LIQ_FLUSH_MS", "0")) / 1000.0
# Минимальный зазор между тиками графика по одной монете (0 = каждый тик).
TICK_MIN_GAP = float(os.getenv("LICVID_TICK_MIN_GAP_MS", "0")) / 1000.0
PRICE_INTERVAL = float(os.getenv("LICVID_PRICE_INTERVAL_MS", "1000")) / 1000.0
STATS_INTERVAL = float(os.getenv("LICVID_STATS_INTERVAL_MS", "2000")) / 1000.0


# =============================================================================
#  Состояние
# =============================================================================
LIQUIDATIONS: Deque[dict] = deque(maxlen=HISTORY_MAX)
CANDLES: Dict[str, dict] = {}            # "SYM|tf" -> {"candles": [...], "ts", "source"}
MINUTE_VOL: Dict[str, Dict[int, float]] = {}   # symbol -> {minute_ts: volume}
# Живая CVD: "SYM|tf" -> {время_начала_свечи: дельта USDT (покупки-продажи)}.
# Считается из ленты сделок (тейкер-сторона) и дополняет исторические свечи.
CVD_ACC: Dict[str, Dict[int, float]] = {}
LAST_TICK_TS: Dict[str, float] = {}            # symbol -> время последней сделки
LAST_TICK_PRICE: Dict[str, float] = {}         # symbol -> цена последней сделки
_last_tick_sent: Dict[str, float] = {}         # symbol -> когда последний раз слали
_event_seq = 0
TICKS_SEEN = 0                                 # счётчик сделок (для /api/health)


def _key(symbol: str, tf: int) -> str:
    return f"{symbol}|{tf}"


# =============================================================================
#  Дисковая история ликвидаций (JSONL): переживает рестарт сервера
# =============================================================================
_hist_warned = False


def load_history_file(path: str, maxlen: int, ttl_hours: float) -> List[dict]:
    """Вернуть события из JSONL-файла (не старше TTL) в хронологическом порядке."""
    if not path or not os.path.exists(path):
        return []
    since = time.time() - max(ttl_hours, 0.0) * 3600.0
    rows: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue  # битая строка (например, обрыв при падении) — пропускаем
                if not isinstance(ev, dict) or "id" not in ev:
                    continue
                if float(ev.get("timestamp") or 0) < since:
                    continue
                rows.append(ev)
                if len(rows) > maxlen:
                    rows.pop(0)
    except OSError:
        return []
    return rows


def append_history_event(event: dict, path: str) -> None:
    """Дописать одно событие в JSONL (append). Ошибки не роняют поток данных."""
    global _hist_warned
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
        _hist_warned = False
    except OSError:
        if not _hist_warned:
            _hist_warned = True
            log.warning("Не удаётся писать историю ликвидаций в %s — продолжаем без диска",
                        path)


def trim_history_file(path: str, maxlen: int, ttl_hours: float) -> None:
    """Если файл разросся — переписать его свежим хвостом (последние maxlen)."""
    if not path or not os.path.exists(path):
        return
    try:
        if os.path.getsize(path) <= HISTORY_FILE_MAX_BYTES:
            return
        keep = load_history_file(path, maxlen, ttl_hours)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for ev in keep:
                f.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _fmt_id() -> str:
    global _event_seq
    _event_seq += 1
    return f"{int(time.time() * 1000)}-{_event_seq}"


# =============================================================================
#  Клиенты WebSocket
# =============================================================================
class Client:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.symbol = "ALL"     # фильтр ленты (монета или ALL)
        self.chart = ""         # символ графика — независим от фильтра ленты
        self.tf = 5
        self.min_usd = 0.0
        self.exchange = "ALL"
        self.alive = True

    @property
    def chart_symbol(self) -> str:
        return self.chart or ("BTC_USDT" if self.symbol == "ALL" else self.symbol)

    def wants(self, ev: dict) -> bool:
        if ev["usd"] < self.min_usd:
            return False
        if self.exchange != "ALL" and ev["exchange"] != self.exchange:
            return False
        return True

    async def send(self, msg: dict) -> bool:
        try:
            await self.ws.send_json(msg)
            return True
        except Exception:
            self.alive = False
            return False


class Hub:
    def __init__(self):
        self.clients: Set[Client] = set()
        self._lock = asyncio.Lock()

    async def add(self, c: Client):
        async with self._lock:
            self.clients.add(c)
        log.info("Клиент подключился. Всего: %d", len(self.clients))

    async def remove(self, c: Client):
        async with self._lock:
            self.clients.discard(c)
        log.info("Клиент отключился. Всего: %d", len(self.clients))

    async def broadcast(self, msg: dict, predicate=None):
        async with self._lock:
            targets = list(self.clients)
        dead = []
        for c in targets:
            if predicate and not predicate(c):
                continue
            if not await c.send(msg):
                dead.append(c)
        if dead:
            async with self._lock:
                for c in dead:
                    self.clients.discard(c)

    def viewed_pairs(self) -> Set[tuple]:
        return {(c.chart_symbol, c.tf) for c in self.clients}


hub = Hub()
feed: Optional[MarketFeed] = None
_pending: List[dict] = []
_pending_lock = asyncio.Lock()


# =============================================================================
#  Обработка событий от бирж
# =============================================================================
async def on_liquidation(ev: dict):
    """Пришла ликвидация с биржи → в историю и в очередь на рассылку."""
    event = {
        "id": _fmt_id(),
        "symbol": ev["symbol"],
        "exchange": ev["exchange"],
        # side в терминах ордера: SELL = вынесли лонг, BUY = вынесли шорт
        "side": "SELL" if ev["side"] == "LONG" else "BUY",
        "position": ev["side"],
        "price": round(float(ev["price"]), 8),
        "qty": round(float(ev["qty"]), 8),
        "usd": round(float(ev["usd"]), 2),
        "timestamp": float(ev["timestamp"]),
    }
    LIQUIDATIONS.append(event)
    append_history_event(event, HISTORY_FILE)
    if len(LIQUIDATIONS) % 1000 == 0:
        trim_history_file(HISTORY_FILE, HISTORY_MAX, HISTORY_TTL_HOURS)
    if BROADCAST_INTERVAL <= 0:
        # без буферизации: событие уходит в сокеты в тот же момент
        await send_liquidations([event])
    else:
        async with _pending_lock:
            _pending.append(event)


async def send_liquidations(batch: List[dict]):
    """Разослать ликвидации всем клиентам с учётом их фильтров."""
    async with hub._lock:
        clients = list(hub.clients)
    for c in clients:
        rows = [e for e in batch if c.wants(e)]
        if rows:
            await c.send({"type": "liqs", "data": rows})


def viewed_tfs(symbol: str) -> Set[int]:
    """Какие таймфреймы этой монеты сейчас реально смотрят клиенты."""
    return {c.tf for c in hub.clients if c.chart_symbol == symbol}


async def on_trade(symbol: str, price: float, qty: float, ts: float,
                   side: str = ""):
    """Каждая сделка по открытому графику: сразу двигаем свечу и шлём тик.

    side — сторона ТЕЙКЕРА ("BUY" бьёт по аску, "SELL" — по биду). По ней
    копит живая CVD: сумма (покупки - продажи) в USDT внутри каждой свечи.
    """
    global TICKS_SEEN
    TICKS_SEEN += 1
    LAST_TICK_TS[symbol] = time.time()
    LAST_TICK_PRICE[symbol] = price
    try:
        if side in ("BUY", "SELL") and price > 0 and qty > 0:
            signed = float(price) * float(qty) * (1 if side == "BUY" else -1)
            _cvd_add(symbol, float(ts) if ts else time.time(), signed)
    except (TypeError, ValueError):
        pass

    if TICK_MIN_GAP > 0:
        last = _last_tick_sent.get(symbol, 0.0)
        if time.time() - last < TICK_MIN_GAP:
            _apply_price_to_candles(symbol, price)
            return
        _last_tick_sent[symbol] = time.time()

    tfs = viewed_tfs(symbol)
    if not tfs:
        _apply_price_to_candles(symbol, price)
        return

    updated = _apply_price_to_candles(symbol, price, only_tfs=tfs)
    for tf, candle in updated:
        await hub.broadcast(
            {"type": "tick", "symbol": symbol, "tf": tf,
             "price": price, "ts": ts, "candle": candle},
            predicate=lambda c, sym=symbol, t=tf: c.chart_symbol == sym and c.tf == t,
        )


def _cvd_add(symbol: str, ts: float, signed_usd: float) -> None:
    """Копит тейкер-дельту по всем таймфреймам: bucket -> накопленная USDT."""
    for tf in TF_MINUTES:
        tf_sec = tf * 60
        bucket = int(ts // tf_sec) * tf_sec
        acc = CVD_ACC.setdefault(_key(symbol, tf), {})
        acc[bucket] = round(acc.get(bucket, 0.0) + signed_usd, 2)
        if len(acc) > 400:                      # чистим древние buckets
            for old_b in sorted(acc)[:200]:
                acc.pop(old_b, None)


def _cvd_live_of(symbol: str, tf: int, bucket: int) -> Optional[float]:
    acc = CVD_ACC.get(_key(symbol, tf))
    if not acc:
        return None
    return acc.get(bucket)


def _cvd_apply_live(entry: dict, symbol: str, tf: int, bucket: int,
                    candle: dict) -> None:
    """CVD текущей свечи = биржевая база (на момент загрузки kline) + новые тики.

    База нужна, чтобы не считать одни и те же сделки дважды: REST-свеча
    Binance уже включает тикеры с начала свечи, а сверху кладём только тики,
    прилетевшие после загрузки (их база вычтена в _cvd_seed_base).
    """
    live = _cvd_live_of(symbol, tf, bucket)
    if live is None:
        return
    candle["cvd"] = round((entry.get("cvd_base") or 0.0) + live, 2)


def _cvd_seed_base(entry: dict, symbol: str, tf: int) -> None:
    """После загрузки серии свечей фиксируем «базу» для текущей свечи."""
    series = entry.get("candles") or []
    if not series:
        return
    tf_sec = tf * 60
    now_bucket = int(time.time() // tf_sec) * tf_sec
    last = series[-1]
    if last.get("time") != now_bucket:
        return
    rest = last.get("cvd")
    acc = CVD_ACC.get(_key(symbol, tf), {})
    entry["cvd_base"] = round(
        (float(rest) if rest is not None else 0.0) - acc.get(now_bucket, 0.0), 2)


def _apply_price_to_candles(symbol: str, price: float,
                            vol_delta: float = 0.0,
                            only_tfs: Optional[Set[int]] = None) -> List[tuple]:
    """Двигает последнюю свечу каждого ТФ. Возвращает [(tf, candle), ...]."""
    updated = []
    for tf in (only_tfs or TF_MINUTES):
        k = _key(symbol, tf)
        entry = CANDLES.get(k)
        if not entry or not entry["candles"]:
            continue
        tf_sec = tf * 60
        bucket = int(time.time() // tf_sec) * tf_sec
        series = entry["candles"]
        last = series[-1]
        if last["time"] == bucket:
            last["high"] = max(last["high"], price)
            last["low"] = min(last["low"], price)
            last["close"] = price
            if vol_delta:
                last["volume"] = round(last["volume"] + vol_delta, 2)
            _cvd_apply_live(entry, symbol, tf, bucket, last)
        elif bucket > last["time"]:
            series.append({
                "time": bucket,
                "open": last["close"],
                "high": max(last["close"], price),
                "low": min(last["close"], price),
                "close": price,
                "volume": round(vol_delta, 2),
                "cvd": 0.0,
            })
            if len(series) > 600:
                del series[:len(series) - 600]
            last = series[-1]
            entry["cvd_base"] = 0.0      # новая свеча — считаем только с её начала
            _cvd_apply_live(entry, symbol, tf, bucket, last)
        else:
            continue
        updated.append((tf, dict(last)))
    return updated


async def on_price(symbol: str, price: float, candle1m: Optional[dict]):
    """Минутная свеча с биржи: авторитетный объём + OHLC.

    Если по монете только что был тик (aggTrade), цену закрытия оставляем
    тиковую — она свежее, чем снимок kline (тот приходит раз в ~250 мс).
    """
    delta = 0.0
    if candle1m:
        minute = candle1m["time"]
        vols = MINUTE_VOL.setdefault(symbol, {})
        prev = vols.get(minute, 0.0)
        delta = max(candle1m["volume"] - prev, 0.0)
        vols[minute] = candle1m["volume"]
        if len(vols) > 400:
            for old in sorted(vols)[:200]:
                vols.pop(old, None)

    if time.time() - LAST_TICK_TS.get(symbol, 0.0) < 2.0:
        # тиковая цена свежее снимка kline — берём её
        price = LAST_TICK_PRICE.get(symbol, price)

    updated = _apply_price_to_candles(symbol, price, vol_delta=delta)
    for tf, candle in updated:
        await hub.broadcast(
            {"type": "candle", "symbol": symbol, "tf": tf, "candle": candle},
            predicate=lambda c, sym=symbol, t=tf: c.chart_symbol == sym and c.tf == t,
        )


# =============================================================================
#  Свечи
# =============================================================================
def _attach_oi(candles: list, tf: int, levels: dict, chgs: dict) -> None:
    if not levels:
        return
    mapping = map_candles_to_oi([c["time"] for c in candles], tf, levels, chgs)
    for c in candles:
        m = mapping.get(c["time"])
        if not m:
            continue
        if m["oi"] is not None:
            c["oi"] = m["oi"]
        if m["oiChg"] is not None:
            c["oiChg"] = m["oiChg"]


async def get_candles(symbol: str, tf: int, force: bool = False) -> dict:
    symbol = canon(symbol)
    if tf not in TF_MINUTES:
        tf = 5
    k = _key(symbol, tf)
    entry = CANDLES.get(k)
    fresh = entry and (time.time() - entry["ts"] < KLINE_TTL) and not force
    if fresh:
        return entry

    real = None
    if feed:
        real = await feed.fetch_klines(symbol, tf, limit=300)
        if real and any(c.get("cvd") is None for c in real[-30:]):
            # источник свечей (Bybit/OKX) тейкер-полей не даёт —
            # тянем историю CVD с Binance → OKX и подшиваем по времени свечи
            try:
                cvd_map = await feed.fetch_cvd(symbol, tf, candles=real,
                                               limit=max(len(real), 300))
            except Exception:
                cvd_map = None
            if cvd_map:
                for c in real:
                    if c.get("cvd") is None:
                        d = cvd_map.get(c["time"])
                        if d is not None:
                            c["cvd"] = d
    if real:
        # подшиваем открытый интерес: oi — уровень на конец свечи,
        # oiChg — изменение за свечу (для треугольников на графике)
        tracker = getattr(feed, "oi", None)
        if tracker is not None:
            try:
                await tracker.ensure_symbol(symbol)
                _attach_oi(real, tf, tracker.series(symbol),
                           tracker.bucket_chg(symbol))
            except Exception as e:
                log.debug("oi attach %s: %s", symbol, e)
        # сохраняем «живой» хвост, если биржа ещё не закрыла текущую свечу
        CANDLES[k] = {"candles": real, "ts": time.time(), "source": "exchange"}
        _cvd_seed_base(CANDLES[k], symbol, tf)
        return CANDLES[k]

    if entry:
        entry["ts"] = time.time() - KLINE_TTL / 2   # отдадим старое, попробуем позже
        return entry

    # Совсем нет связи с биржами — строим заготовку от последней цены,
    # чтобы график не падал; источник помечен как "unavailable"
    # (в демо-режиме рисуем случайное блуждание, чтобы было что смотреть).
    price = (feed.prices.get(symbol) if feed else None) or DEMO_SEED_PRICES.get(symbol) or 100.0
    tf_sec = tf * 60
    now_bucket = int(time.time() // tf_sec) * tf_sec
    series = []
    p = price
    for n in range(120, -1, -1):
        if DEMO_MODE:
            p = max(p * (1 + random.gauss(0, 0.0012)), 1e-12)
            o = p
            c = max(p * (1 + random.gauss(0, 0.0009)), 1e-12)
            series.append({"time": now_bucket - (n * tf_sec), "open": o,
                           "high": max(o, c) * 1.001, "low": min(o, c) * 0.999,
                           "close": c, "volume": random.uniform(1e4, 5e5)})
            p = c
        else:
            series.append({"time": now_bucket - (n * tf_sec), "open": price,
                           "high": price, "low": price, "close": price, "volume": 0.0})
    if DEMO_MODE:
        for c in series:
            c["cvd"] = round((c.get("volume") or 1e4) * random.uniform(-0.35, 0.35), 2)
        _demo_oi(series)
    CANDLES[k] = {"candles": series, "ts": time.time(),
                  "source": "demo" if DEMO_MODE else "unavailable"}
    _cvd_seed_base(CANDLES[k], symbol, tf)
    return CANDLES[k]


def _demo_oi(series: list) -> None:
    """Синтетический OI для демо-режима: случайное блуждание уровня."""
    oi = 5e8
    for c in series:
        oi = max(oi * (1 + random.gauss(0, 0.004)), 1e6)
        c["oi"] = round(oi, 2)
    for i, c in enumerate(series):
        prev = series[i - 1]["oi"] if i else c["oi"]
        c["oiChg"] = round(c["oi"] - prev, 2)


def sync_hot_symbols():
    """Сообщаем фиду, чьи графики сейчас открыты — по ним нужен каждый тик."""
    if not feed:
        return
    hot = {c.chart_symbol for c in hub.clients}
    if not hot and feed.symbols:
        hot = {feed.symbols[0]}          # держим BTC тёплым для быстрого старта
    feed.set_hot_symbols(hot)


async def hot_symbols_watcher():
    while True:
        try:
            await asyncio.sleep(3)
            sync_hot_symbols()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("hot symbols: %s", e)


async def kline_refresher():
    """Периодически обновляет с биржи те серии, которые кто-то смотрит."""
    while True:
        try:
            await asyncio.sleep(15)
            for symbol, tf in list(hub.viewed_pairs()):
                await get_candles(symbol, tf, force=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("kline refresher: %s", e)


# =============================================================================
#  Статистика
# =============================================================================
# Окна статистики для боксов шапки (суффикс ключа -> секунд).
# Старые ключи 24h/1h/5m сохранены как есть — их едят лендинг и клиенты.
STAT_WINDOWS = (("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800),
                ("1h", 3600), ("4h", 14400), ("24h", 86400))


def compute_stats(symbol: Optional[str] = None, exchange: Optional[str] = None) -> dict:
    now = time.time()
    items = list(LIQUIDATIONS)
    if symbol and symbol != "ALL":
        items = [x for x in items if x["symbol"] == symbol]
    if exchange and exchange != "ALL":
        items = [x for x in items if x["exchange"] == exchange]

    def split(rows):
        longs = sum(x["usd"] for x in rows if x["side"] == "SELL")
        shorts = sum(x["usd"] for x in rows if x["side"] == "BUY")
        return longs, shorts

    d24 = [x for x in items if now - x["timestamp"] <= 86400]
    wins = {}
    for suffix, sec in STAT_WINDOWS:
        rows = d24 if sec == 86400 else [x for x in items if now - x["timestamp"] <= sec]
        longs, shorts = split(rows)
        wins[f"total_usd_{suffix}"] = longs + shorts
        wins[f"longs_usd_{suffix}"] = longs
        wins[f"shorts_usd_{suffix}"] = shorts

    # Лидеры — всегда по ВСЕМ монетам (фильтр монеты их не схлопывает):
    # иначе при выборе монеты в блоке оставалась бы только она одна.
    # Фильтр биржи уважаем: лидеры внутри выбранной биржи осмысленны.
    pool = list(LIQUIDATIONS)
    if exchange and exchange != "ALL":
        pool = [x for x in pool if x["exchange"] == exchange]
    pool24 = [x for x in pool if now - x["timestamp"] <= 86400]
    coin_totals: Dict[str, dict] = {}
    for x in pool24:
        c = coin_totals.setdefault(x["symbol"], {"symbol": x["symbol"], "usd": 0.0,
                                                 "longs": 0.0, "shorts": 0.0, "count": 0})
        c["usd"] += x["usd"]
        c["count"] += 1
        if x["side"] == "SELL":
            c["longs"] += x["usd"]
        else:
            c["shorts"] += x["usd"]
    top_coins = sorted(coin_totals.values(), key=lambda c: c["usd"], reverse=True)

    exch_totals: Dict[str, float] = {}
    for x in pool24:
        exch_totals[x["exchange"]] = exch_totals.get(x["exchange"], 0.0) + x["usd"]

    biggest = max(d24, key=lambda x: x["usd"], default=None)

    out = dict(wins)
    out.update({
        "top_coins": top_coins[:12],
        "exchanges": exch_totals,
        "total_count": len(items),
        "biggest_24h": biggest,
        "demo": DEMO_MODE,
    })
    return out


# =============================================================================
#  Фоновые рассылки
# =============================================================================
async def liquidation_broadcaster():
    """Работает только если включена буферизация (LICVID_LIQ_FLUSH_MS > 0)."""
    if BROADCAST_INTERVAL <= 0:
        return
    while True:
        try:
            await asyncio.sleep(BROADCAST_INTERVAL)
            async with _pending_lock:
                if not _pending:
                    continue
                batch = _pending[:]
                _pending.clear()
            await send_liquidations(batch)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("broadcaster: %s", e)


async def price_broadcaster():
    while True:
        try:
            await asyncio.sleep(PRICE_INTERVAL)
            if not feed or not hub.clients:
                continue
            prices = {s: p for s, p in feed.prices.items() if p}
            if prices:
                await hub.broadcast({"type": "prices", "data": prices})
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("price broadcaster: %s", e)


async def stats_broadcaster():
    while True:
        try:
            await asyncio.sleep(STATS_INTERVAL)
            if not hub.clients:
                continue
            global_stats = compute_stats()
            cache = {"ALL": global_stats}
            health = health_summary()
            async with hub._lock:
                clients = list(hub.clients)
            for c in clients:
                key = c.symbol
                if key not in cache:
                    cache[key] = compute_stats(key)
                await c.send({"type": "stats", "data": cache[key], "health": health})
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("stats broadcaster: %s", e)


# =============================================================================
#  Демо-генератор (только при LICVID_DEMO=1)
# =============================================================================
async def demo_generator():
    log.warning("ВКЛЮЧЁН ДЕМО-РЕЖИМ: поток ликвидаций синтетический (LICVID_DEMO=1)")
    exchanges = ["binance", "bybit", "okx", "gate", "bitget", "htx", "bitmex"]
    while True:
        try:
            await asyncio.sleep(random.uniform(0.15, 0.9))
            if not feed or not feed.symbols:
                continue
            symbol = random.choice(feed.symbols[:20])
            price = feed.prices.get(symbol) or 100.0
            price = round(price * (1 + random.gauss(0, 0.0004)), 8)
            r = random.random()
            if r < 0.6:
                usd = random.uniform(500, 20000)
            elif r < 0.9:
                usd = random.uniform(20000, 150000)
            elif r < 0.98:
                usd = random.uniform(150000, 700000)
            else:
                usd = random.uniform(700000, 4000000)
            await on_liquidation({
                "symbol": symbol,
                "exchange": random.choice(exchanges),
                "side": "LONG" if random.random() < 0.53 else "SHORT",
                "price": price,
                "qty": usd / price if price else 1.0,
                "usd": usd,
                "timestamp": time.time(),
            })
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("demo: %s", e)


# Ориентировочные цены только для демо-режима (когда биржи недоступны)
DEMO_SEED_PRICES = {
    "BTC_USDT": 96000.0, "ETH_USDT": 3300.0, "SOL_USDT": 190.0, "XRP_USDT": 2.3,
    "DOGE_USDT": 0.32, "BNB_USDT": 690.0, "ADA_USDT": 0.95, "AVAX_USDT": 38.0,
    "LINK_USDT": 22.0, "TON_USDT": 5.4, "TRX_USDT": 0.24, "DOT_USDT": 7.2,
    "MATIC_USDT": 0.52, "NEAR_USDT": 5.1, "LTC_USDT": 105.0, "BCH_USDT": 450.0,
    "APT_USDT": 9.3, "SUI_USDT": 4.4, "ARB_USDT": 0.78, "OP_USDT": 1.8,
    "PEPE_USDT": 0.000019, "SHIB_USDT": 0.000022, "WIF_USDT": 2.1,
    "INJ_USDT": 23.0, "FIL_USDT": 5.3, "ATOM_USDT": 6.7, "UNI_USDT": 13.5,
    "AAVE_USDT": 330.0, "ETC_USDT": 27.0, "HBAR_USDT": 0.29, "SEI_USDT": 0.45,
    "TIA_USDT": 5.0, "RUNE_USDT": 4.8, "ORDI_USDT": 34.0, "FTM_USDT": 0.9,
    "GALA_USDT": 0.04, "CRV_USDT": 0.95, "LDO_USDT": 1.9, "STX_USDT": 1.8,
    "ENA_USDT": 0.9,
}


async def demo_tick_walk():
    """Демо-тики ~20/сек по открытым графикам, чтобы видеть живую свечу."""
    while True:
        try:
            await asyncio.sleep(0.05)
            if not feed or not feed.hot_symbols:
                continue
            for symbol in list(feed.hot_symbols):
                p = feed.prices.get(symbol) or DEMO_SEED_PRICES.get(symbol) or 100.0
                p = max(p * (1 + random.gauss(0, 0.00025)), 1e-12)
                feed.prices[symbol] = p
                await on_trade(symbol, p, random.uniform(0.01, 3.0), time.time(),
                               random.choice(("BUY", "SELL")))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("demo ticks: %s", e)


async def demo_price_walk():
    """В демо-режиме двигаем цены, если биржи недоступны."""
    while True:
        try:
            await asyncio.sleep(1.0)
            if not feed:
                continue
            for symbol in list(feed.symbols)[:20]:
                p = feed.prices.get(symbol)
                if not p:
                    feed.prices[symbol] = p = DEMO_SEED_PRICES.get(symbol) or random.uniform(1, 100)
                p = round(p * (1 + random.gauss(0, 0.0006)), 8)
                feed.prices[symbol] = p
                await on_price(symbol, p, None)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("demo prices: %s", e)


# =============================================================================
#  Приложение
# =============================================================================
def health_summary() -> dict:
    if not feed:
        return {"ready": False, "demo": DEMO_MODE, "sources": {}}
    h = feed.health()
    live = [name for name, s in h["sources"].items()
            if name not in ("prices", "ticks") and s["connected"]]
    return {
        "ready": bool(live) or DEMO_MODE,
        "demo": DEMO_MODE,
        "live_exchanges": live,
        "symbols_source": h["symbols_source"],
        "events_total": sum(s["events"] for n, s in h["sources"].items()
                            if n not in ("prices", "ticks")),
        "ticks_total": TICKS_SEEN,
        "sources": h["sources"],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    # восстанавливаем дисковую историю до старта биржевых потоков,
    # чтобы первый клиент сразу увидел вчерашние ликвидации
    if HISTORY_FILE:
        try:
            loaded = await asyncio.to_thread(
                load_history_file, HISTORY_FILE, HISTORY_MAX, HISTORY_TTL_HOURS)
            for ev in loaded:
                LIQUIDATIONS.append(ev)
            if loaded:
                log.info("История ликвидаций восстановлена с диска: %d событий", len(loaded))
        except Exception as e:
            log.warning("Не удалось загрузить историю с диска: %s", e)

    global feed
    feed = MarketFeed(on_liquidation=on_liquidation,
                      on_price=on_price,
                      on_trade=on_trade,
                      symbols_limit=SYMBOLS_LIMIT,
                      exchanges=EXCHANGES,
                      tick_sources=TICK_SOURCES)
    await feed.start()
    sync_hot_symbols()      # чтобы тики пошли сразу, не дожидаясь клиента

    tasks = [
        asyncio.create_task(liquidation_broadcaster(), name="liq-broadcast"),
        asyncio.create_task(price_broadcaster(), name="price-broadcast"),
        asyncio.create_task(stats_broadcaster(), name="stats-broadcast"),
        asyncio.create_task(kline_refresher(), name="kline-refresh"),
        asyncio.create_task(hot_symbols_watcher(), name="hot-symbols"),
    ]
    if DEMO_MODE:
        tasks.append(asyncio.create_task(demo_generator(), name="demo"))
        tasks.append(asyncio.create_task(demo_price_walk(), name="demo-prices"))
        tasks.append(asyncio.create_task(demo_tick_walk(), name="demo-ticks"))

    # прогреваем свечи популярных монет, чтобы первый клиент увидел график сразу
    async def warmup():
        for sym in (feed.symbols[:3] if feed else []):
            try:
                await get_candles(sym, 5, force=True)
            except Exception:
                pass
    tasks.append(asyncio.create_task(warmup(), name="warmup"))

    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await feed.stop()


app = FastAPI(title="Licvidation — Live Crypto Liquidation Terminal",
              version="4.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/symbols")
async def api_symbols():
    symbols = feed.symbols if feed else []
    prices = feed.prices if feed else {}
    meta = feed.symbol_meta if feed else {}

    liq24: Dict[str, float] = {}
    now = time.time()
    for x in LIQUIDATIONS:
        if now - x["timestamp"] <= 86400:
            liq24[x["symbol"]] = liq24.get(x["symbol"], 0.0) + x["usd"]

    rows = []
    custom = set(feed.custom_symbols) if feed else set()
    for s in symbols:
        m = meta.get(s, {})
        rows.append({
            "symbol": s,
            "base": base_of(s),
            "price": prices.get(s) or m.get("price") or 0.0,
            "change24h": m.get("change24h", 0.0),
            "volume24h": m.get("volume24h", 0.0),
            "volAvg7d": round(feed.vol_avg7d(s), 2) if feed else 0.0,
            "liq24h": round(liq24.get(s, 0.0), 2),
            "custom": s in custom,
            "exchanges": m.get("exchanges", []),
        })
    return {
        "symbols": symbols,
        "details": rows,
        "prices": prices,
        "exchanges": EXCHANGES,
        "custom_symbols": sorted(custom),
        "catalog_count": len(feed.symbol_index) if feed else 0,
        "catalog_source": feed.symbol_index_source if feed else "none",
        "timeframes": TF_MINUTES,
        "demo": DEMO_MODE,
        "source": feed.symbols_source if feed else "none",
    }


def _enrich_symbol_rows(rows: List[dict]) -> List[dict]:
    """Добавляет к найденным парам объём ликвидаций за 24ч из памяти."""
    now = time.time()
    liq24: Dict[str, float] = {}
    for x in LIQUIDATIONS:
        if now - x["timestamp"] <= 86400:
            liq24[x["symbol"]] = liq24.get(x["symbol"], 0.0) + x["usd"]
    out = []
    for r in rows:
        d = dict(r)
        d["liq24h"] = round(liq24.get(d.get("symbol", ""), 0.0), 2)
        out.append(d)
    return out


@app.get("/api/symbols/search")
async def api_symbol_search(q: str = Query("", max_length=40),
                            limit: int = Query(20, ge=1, le=50)):
    if not feed:
        return {"query": q, "results": [], "total": 0,
                "source": "none", "index_size": 0}
    res = await feed.search_symbols(q, limit)
    res["results"] = _enrich_symbol_rows(res["results"])
    return res


@app.post("/api/symbols/add")
async def api_symbol_add(symbol: str = Query(..., max_length=40),
                         force: bool = Query(False)):
    if not feed:
        return JSONResponse({"added": False, "found": False,
                             "error": "сервер ещё не готов"}, status_code=503)
    res = await feed.add_symbol(symbol, force=force)
    if not res.get("found") and not res.get("added"):
        return JSONResponse(res, status_code=404)
    res["details"] = (_enrich_symbol_rows([res.get("details") or {}])[0]
                      if res.get("details") else {})
    return res


@app.get("/api/klines")
async def api_klines(symbol: str = Query("BTC_USDT"), timeframe: int = Query(5)):
    symbol = canon(symbol)
    # Любую монету можно открыть на графике, даже если её нет в дефолтном
    # топ-списке: свечи берутся напрямую с бирж, а не из локального списка.
    entry = await get_candles(symbol, timeframe)
    return {
        "symbol": symbol,
        "timeframe": timeframe if timeframe in TF_MINUTES else 5,
        "source": entry["source"],
        "candles": entry["candles"],
    }


@app.get("/api/oi")
async def api_oi(symbol: str = Query("BTC_USDT")):
    """Открытый интерес: текущий тотал по живым ногам + изменения m5/h1/h24
    по непрерывному ряду (биржи с историей)."""
    symbol = canon(symbol)
    tracker = getattr(feed, "oi", None)
    if tracker is None:
        from oi_feed import OI_WINDOWS
        return {"symbol": symbol, "total_usd": None, "per_exchange": {},
                "live_exchanges": [], "hist_exchanges": [],
                "changes": {k: None for k, _ in OI_WINDOWS},
                "partial": {k: True for k, _ in OI_WINDOWS},
                "ts": None, "stale_sec": None}
    try:
        await tracker.ensure_symbol(symbol)
    except Exception as e:
        log.debug("oi %s: %s", symbol, e)
    out = tracker.payload(symbol)
    if DEMO_MODE and out["total_usd"] is None:
        out = _demo_oi_payload(symbol)
    return out


def _demo_oi_payload(symbol: str) -> dict:
    total = 4e8 + random.uniform(-2e7, 2e7)
    legs = ["binance", "bybit", "okx", "gate", "bitget", "htx", "bitmex"]
    weights = [0.34, 0.21, 0.13, 0.12, 0.09, 0.06, 0.05]
    per = {e: round(total * w, 2) for e, w in zip(legs, weights)}

    def _chg(scale):
        usd = random.gauss(0, total * scale)
        return {"usd": round(usd, 2), "pct": round(usd / total * 100, 3)}

    from oi_feed import OI_WINDOWS
    scales = {"m1": 0.0002, "m5": 0.001, "m15": 0.0016, "m30": 0.0022,
              "h1": 0.004, "h4": 0.009, "h24": 0.02}
    return {"symbol": symbol, "total_usd": round(total, 2),
            "per_exchange": per, "live_exchanges": legs,
            "hist_exchanges": ["binance", "bybit", "gate"],
            "changes": {k: _chg(scales[k]) for k, _ in OI_WINDOWS},
            "partial": {k: False for k, _ in OI_WINDOWS},
            "ts": time.time(), "stale_sec": 0.0}


@app.get("/api/liquidations")
async def api_liquidations(symbol: Optional[str] = None,
                           exchange: Optional[str] = None,
                           min_usd: float = 0.0,
                           limit: int = 300):
    res = list(LIQUIDATIONS)
    if symbol and symbol != "ALL":
        sym = canon(symbol)
        res = [x for x in res if x["symbol"] == sym]
    if exchange and exchange != "ALL":
        res = [x for x in res if x["exchange"] == exchange.lower()]
    if min_usd > 0:
        res = [x for x in res if x["usd"] >= min_usd]
    return {"liquidations": res[-min(limit, 2000):], "total": len(res)}


@app.get("/api/stats")
async def api_stats(symbol: Optional[str] = None, exchange: Optional[str] = None):
    return compute_stats(symbol, exchange)


@app.get("/api/health")
async def api_health():
    data = {
        "status": "ok",
        "server_time": time.time(),
        "clients": len(hub.clients),
        "liquidations_in_memory": len(LIQUIDATIONS),
        "demo": DEMO_MODE,
        "config": {
            "symbols_limit": SYMBOLS_LIMIT,
            "exchanges": EXCHANGES,
            "tick_sources": TICK_SOURCES,
            "history_max": HISTORY_MAX,
            "history_persist": bool(HISTORY_FILE),
            "history_ttl_hours": HISTORY_TTL_HOURS,
        },
    }
    data.update(feed.health() if feed else {"sources": {}})
    _srcs = data.get("sources") or {}
    data["live_exchanges"] = sorted(
        name for name, s in _srcs.items()
        if name not in ("prices", "ticks") and isinstance(s, dict) and s.get("connected")
    )
    data["exchanges_total"] = (
        sum(1 for name in _srcs if name not in ("prices", "ticks")) or len(EXCHANGES)
    )
    data["ticks_seen"] = TICKS_SEEN
    data["hot_symbols"] = sorted(feed.hot_symbols) if feed else []
    data["tick_subscriptions"] = sorted(feed.tick_subscriptions) if feed else []
    data["tick_source"] = feed.status["ticks"].name if feed else None
    data["kline_source_preferred"] = feed.preferred_kline_source if feed else None
    data["cvd_source"] = getattr(feed, "cvd_source", None) if feed else None
    last_tick = max(LAST_TICK_TS.values(), default=None)
    data["seconds_since_last_tick"] = (round(time.time() - last_tick, 2)
                                       if last_tick else None)
    last_event = LIQUIDATIONS[-1]["timestamp"] if LIQUIDATIONS else None
    data["last_liquidation_ts"] = last_event
    data["seconds_since_last_liquidation"] = (round(time.time() - last_event, 1)
                                              if last_event else None)
    return JSONResponse(data)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    client = Client(websocket)
    await hub.add(client)
    try:
        sym_data = await api_symbols() if feed else {"details": [], "custom_symbols": []}
        await client.send({
            "type": "init",
            "symbols": feed.symbols if feed else [],
            "details": sym_data["details"],
            "prices": feed.prices if feed else {},
            "exchanges": EXCHANGES,
            "custom_symbols": sym_data.get("custom_symbols", []),
            "timeframes": TF_MINUTES,
            "demo": DEMO_MODE,
            "health": health_summary(),
            "recent_liquidations": list(LIQUIDATIONS)[-200:],
            "stats": compute_stats(),
        })
        while True:
            raw = await websocket.receive_json()
            action = raw.get("action") or raw.get("type")
            if action in ("sub", "subscribe", "config"):
                sym = raw.get("symbol")
                if sym:
                    client.symbol = "ALL" if sym == "ALL" else canon(sym)
                chart = raw.get("chart") or raw.get("chart_symbol")
                if chart:
                    client.chart = canon(chart)
                if raw.get("tf") in TF_MINUTES:
                    client.tf = int(raw["tf"])
                if raw.get("min_usd") is not None:
                    try:
                        client.min_usd = float(raw["min_usd"])
                    except (TypeError, ValueError):
                        pass
                if raw.get("exchange"):
                    client.exchange = str(raw["exchange"])
                sync_hot_symbols()
                entry = await get_candles(client.chart_symbol, client.tf)
                await client.send({
                    "type": "candles",
                    "symbol": client.chart_symbol,
                    "tf": client.tf,
                    "source": entry["source"],
                    "candles": entry["candles"],
                })
            elif action == "ping":
                await client.send({"type": "pong", "t": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug("ws error: %s", e)
    finally:
        await hub.remove(client)
        sync_hot_symbols()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    """Лендинг: красивый вход в терминал."""
    return FileResponse(os.path.join(STATIC_DIR, "landing.html"))


@app.get("/terminal")
async def terminal():
    """Сам терминал (страница приложения)."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
