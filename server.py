"""
LiqScope Web Server — терминал ликвидаций в реальном времени.

Что отдаёт наружу:
    GET  /                  — лендинг (посадочная страница)
    GET  /terminal          — сам терминал
    GET  /login /cabinet /admin — вход через Telegram, кабинет, админка
    GET  /api/symbols       — список монет (авто-подбор по обороту) + цены
    GET  /api/klines        — реальные свечи (Binance → Bybit → OKX)
    GET  /api/liquidations  — история ликвидаций из памяти
    GET  /api/liq_clusters  — кластеры по свечам из сохранённой истории (месяц),
                              фильтры min_usd и exchanges — как у ленты
    GET  /api/stats         — агрегаты (лонги/шорты, топ монет, биржи)
    GET  /api/health        — состояние каждого WS-источника (для диагностики)
    WS   /ws                — живой поток: ликвидации, цены, свечи, статистика

Все данные — настоящие, с публичных WS бирж (ключи не нужны).
Демо-режим (синтетические события) включается только явно:
    LIQSCOPE_DEMO=1  — и тогда фронтенд честно рисует бейдж «DEMO».

Переменные окружения:
    LIQSCOPE_SYMBOLS_LIMIT  сколько монет держать в списке (по умолчанию 40)
    LIQSCOPE_EXCHANGES      binance,bybit,okx,gate,bitget,htx,hyperliquid,
                            dydx,kraken,bitfinex (по умолчанию все)
    LIQSCOPE_TICK_SOURCE    порядок источников тиков (CVD):
                            binance,binance-raw,bybit,dydx,kraken,
                            bitfinex,hyperliquid
    LIQSCOPE_DEMO           1 — генерировать тестовый поток вместо биржевого
    LIQSCOPE_HISTORY_MAX    сколько событий держать в памяти (по умолчанию 60000)
    LIQSCOPE_CLUSTER_MEM_SEC  сколько секунд кластеров брать из памяти, а не с
                            диска (по умолчанию 300: диск пишется очередью)
    LIQSCOPE_CHANNEL_URL    инвайт канала (по умолчанию https://t.me/+4S1LsZtH1Pc5YWZi)
    LIQSCOPE_CHANNEL_ID     numeric id канала (-100…) — чтобы проверять подписку и постить;
                            если пусто, бот запомнит id, когда его добавят админом канала
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Deque, Dict, List, Optional, Set

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import seo_pages
from fastapi.staticfiles import StaticFiles

from market_feed import GATE_REST, MarketFeed, TF_MINUTES, base_of, canon, _get_json
from timeframes import parse_tf
from oi_feed import map_candles_to_oi
from hour_board import BOARD, OI, SLOTS, build_snapshot
from accounts import Store
from flow_feed import FlowFeed
from history import HistoryStore, MONTH_HOURS
from pump_scan import PumpScanner, filter_new as pump_filter_new
from pump_scan import format_signal_html as pump_signal_html
from refs import gate_url
from mailer import build_mailer
import ai_text
from ai_text import build_ai, prompt_setting
import api_digest
from api_digest import DigestScheduler, ctx as digest_ctx, register_digest_routes
from api_hourly import ctx as hourly_ctx, register_hourly_routes
from hourly_posts import PostStore as HourlyStore, post_id as hourly_id
from daily_digest import DigestStore
from tg_bot import TelegramBot, normalize_public_url
from web_account import ctx as account_ctx, register_account_routes
import web_bot_admin
from web_bot_admin import register_bot_admin_routes
import ads as ads_mod
from ads import AdService, register_ad_routes
import feedback as feedback_mod
from feedback import register_feedback_routes
import geoip
import web_geo
import web_layers
import terminal_chat as terminal_chat_mod
from terminal_chat import register_chat_routes as register_terminal_chat_routes
import content_comments as content_comments_mod
from content_comments import register_comment_routes as register_content_comment_routes

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("liqscope.server")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

SYMBOLS_LIMIT = int(os.getenv("LIQSCOPE_SYMBOLS_LIMIT", "40"))
EXCHANGES = [e.strip().lower() for e in
             os.getenv("LIQSCOPE_EXCHANGES",
                       "binance,bybit,okx,gate,bitget,htx,hyperliquid,"
                       "dydx,kraken,bitfinex").split(",")
             if e.strip()]
# Порядок источников потиковых данных для графика (первый рабочий побеждает)
TICK_SOURCES = [x.strip().lower() for x in
                os.getenv("LIQSCOPE_TICK_SOURCE", "binance,binance-raw,bybit").split(",")
                if x.strip()]
DEMO_MODE = os.getenv("LIQSCOPE_DEMO", "0").strip() in ("1", "true", "yes", "on")
DEMO_PUMP_VOL: Dict[str, float] = {}   # демо-оборот 24ч по монетам для сторожа
HISTORY_MAX = int(os.getenv("LIQSCOPE_HISTORY_MAX", "60000"))
# Дисковое сохранение истории ликвидаций (переживает рестарт сервера).
# Путь — каталог дневных файлов ("" / "0" / "off" — не хранить вовсе),
# TTL — сколько часов держать историю.
HISTORY_FILE = os.getenv("LIQSCOPE_HISTORY_FILE",
                         os.path.join(HERE, "data", "liq_history.jsonl")).strip()
if HISTORY_FILE.lower() in ("0", "none", "off", "false"):
    HISTORY_FILE = ""
# Сколько истории держать на диске. По умолчанию — 31 сутки: ликвидации,
# CVD, объём и OI должны быть доступны за месяц, а не за сутки.
HISTORY_TTL_HOURS = float(os.getenv("LIQSCOPE_HISTORY_TTL_HOURS", str(MONTH_HOURS)))
# Лимит одного дневного файла сырых ликвидаций: мельче $50k в переполненный
# день не пишем (часовые свёртки при этом остаются полными)
HISTORY_SHARD_MAX_MB = float(os.getenv("LIQSCOPE_HISTORY_SHARD_MB", "48"))
# Как часто свёртки дня и уборка старых дней уходят на диск
HISTORY_FLUSH_SEC = max(30.0, float(os.getenv("LIQSCOPE_HISTORY_FLUSH_SEC", "180")))
# Кластеры ликвидаций на графике: сколько уровней цены внутри свечи (столько
# же было в терминале) и на сколько секунд назад события берём из памяти, а
# не с диска (диск пишется очередью, окно закрывает её задержку).
LIQ_CLUSTER_LEVELS = 6
LIQ_CLUSTER_MEM_SEC = max(60.0, float(os.getenv("LIQSCOPE_CLUSTER_MEM_SEC", "300")))
#: Кластеры истории в памяти: (монета, тф, порог) -> свёртка по свечам.
#: Живут столько же, сколько кэш свечей: за это время меняется только
#: последняя свеча, а она всё равно пересчитывается.
LIQ_CLUSTER_CACHE: Dict[tuple, dict] = {}
LIQ_CLUSTER_CACHE_MAX = 64

BOT_TOKEN = os.getenv("LIQSCOPE_BOT_TOKEN", "").strip()
PUBLIC_URL = normalize_public_url(os.getenv("LIQSCOPE_PUBLIC_URL", ""))
CHANNEL_URL = os.getenv("LIQSCOPE_CHANNEL_URL", "https://t.me/+4S1LsZtH1Pc5YWZi").strip()
CHANNEL_ID = os.getenv("LIQSCOPE_CHANNEL_ID", "").strip()
SECRET = os.getenv("LIQSCOPE_SECRET", "").strip() or "liqscope-change-me"
ADMIN_IDS = []
for _x in os.getenv("LIQSCOPE_ADMIN_IDS", "").replace(";", ",").split(","):
    _x = _x.strip()
    if _x.isdigit():
        ADMIN_IDS.append(int(_x))
# Админы, которые регистрируются по почте (через запятую или точку с запятой)
ADMIN_EMAILS = [x.strip().lower() for x in
                os.getenv("LIQSCOPE_ADMIN_EMAILS", "").replace(";", ",").split(",")
                if x.strip()]
# Жёсткий режим: без подтверждения почты кабинет закрыт (по умолчанию так)
REQUIRE_EMAIL_VERIFICATION = os.getenv(
    "LIQSCOPE_REQUIRE_EMAIL_VERIFICATION", "1").strip().lower() not in ("0", "false", "no")
ACCOUNTS_DB = os.getenv("LIQSCOPE_ACCOUNTS_DB",
                        os.path.join(HERE, "data", "accounts.db"))
# Дневной дайджест: архив выпусков и время вечерней публикации (МСК).
# LIQSCOPE_DIGEST_FILE="" — не хранить историю (страница будет пустой).
DIGEST_FILE = os.getenv("LIQSCOPE_DIGEST_FILE",
                        os.path.join(HERE, "data", "digests.json")).strip()
if DIGEST_FILE.lower() in ("0", "none", "off", "false"):
    DIGEST_FILE = ""
# Сводки по часам — раздел сайта: архив постов канала (то же, что ушло в
# Telegram, плюс фото). LIQSCOPE_HOURLY_FILE="" — не хранить (раздел пустой).
HOURLY_FILE = os.getenv("LIQSCOPE_HOURLY_FILE",
                        os.path.join(HERE, "data", "channel_posts.json")).strip()
if HOURLY_FILE.lower() in ("0", "none", "off", "false"):
    HOURLY_FILE = ""
try:
    HOURLY_KEEP = max(50, int(os.getenv("LIQSCOPE_HOURLY_KEEP", "1200") or 1200))
except ValueError:
    HOURLY_KEEP = 1200
DIGEST_HOUR = int(os.getenv("LIQSCOPE_DIGEST_HOUR", "22") or 22)
DIGEST_MINUTE = int(os.getenv("LIQSCOPE_DIGEST_MIN", "0") or 0)
DIGEST_JITTER_MIN = int(os.getenv("LIQSCOPE_DIGEST_JITTER_MIN", "10") or 10)
DIGEST_SCHED = os.getenv("LIQSCOPE_DIGEST_SCHED", "1").strip().lower() \
    not in ("0", "false", "no", "off")
# Частота постов в канал: раз в N часов (1…10), по умолчанию 4. Живёт в
# настройках (меняется из админки сайта и бота без перезапуска), окружение —
# стартовое значение. Блок анализа поста = N/4 часа.
from channel_digest import DEFAULT_INTERVAL_H, clamp_interval  # noqa: E402
POST_INTERVAL_H = clamp_interval(os.getenv("LIQSCOPE_POST_INTERVAL_H") or
                                 DEFAULT_INTERVAL_H)
account_store = Store(ACCOUNTS_DB, SECRET, ADMIN_IDS, ADMIN_EMAILS)
# Письма: SMTP из окружения; без настроек сервер работает, письма не уходят
mailer = build_mailer(PUBLIC_URL)
tg_bot = TelegramBot(BOT_TOKEN, account_store, PUBLIC_URL,
                     channel_url=CHANNEL_URL, channel_id=CHANNEL_ID)
# ИИ-шапки для постов в канал: Gemini → Groq → OpenRouter (ключи из окружения).
# Без ключей None — сводка уходит с шаблонными шапками, как раньше.
tg_bot.ai = build_ai()
# Промты ИИ (шапка поста и дневной дайджест) можно переписать в админке сайта:
# они лежат настройками ai_prompt_head_ru / ai_prompt_digest_en и т.д., а
# встроенные шаблоны остаются образцом, пока админ своего текста не сохранил.
ai_text.set_prompt_source(
    lambda kind, lang="ru": account_store.get_setting(prompt_setting(kind, lang)))

KLINE_TTL = 20.0            # сек: как часто перезапрашивать историю с биржи
# Ликвидации уходят клиенту сразу; интервал — только предохранитель от флуда
# при лавине событий (0 = слать каждое событие немедленно).
BROADCAST_INTERVAL = float(os.getenv("LIQSCOPE_LIQ_FLUSH_MS", "0")) / 1000.0
# Минимальный зазор между тиками графика по одной монете (0 = каждый тик).
TICK_MIN_GAP = float(os.getenv("LIQSCOPE_TICK_MIN_GAP_MS", "0")) / 1000.0
PRICE_INTERVAL = float(os.getenv("LIQSCOPE_PRICE_INTERVAL_MS", "1000")) / 1000.0
STATS_INTERVAL = float(os.getenv("LIQSCOPE_STATS_INTERVAL_MS", "2000")) / 1000.0


# =============================================================================
#  Состояние
# =============================================================================
LIQUIDATIONS: Deque[dict] = deque(maxlen=HISTORY_MAX)
# Месячная история: сырые события по дням + часовые свёртки (ликвидации,
# CVD, объём). В памяти — только свежий хвост, всё остальное на диске.
HIST = HistoryStore(HISTORY_FILE, ttl_hours=HISTORY_TTL_HOURS,
                    shard_max_mb=HISTORY_SHARD_MAX_MB)
# Что получилось при восстановлении истории на старте. Нужен диагностике: если
# после перезапуска в памяти ноль событий, дневной дайджест собирается «пустым»
# («$0 · 0 ликвидаций»), и по этому полю сразу видно, в чём дело.
HISTORY_RESTORE: Dict[str, Any] = {"events": 0, "error": "", "at": 0.0}
# Сторож пампов и дампов: минутные цены всех монет Gate + сигналы.
PUMPS = PumpScanner(keep_min=int(os.getenv("LIQSCOPE_PUMP_KEEP_MIN",
                                         str(3 * 24 * 60)) or 3 * 24 * 60))
PUMP_SIGNALS: Deque[dict] = deque(maxlen=300)      # последние сработавшие сигналы
PUMP_LAST_FIRED: Dict[str, float] = {}             # "символ|режим" -> когда сообщили
CORR_SIGNALS: Deque[dict] = deque(maxlen=300)      # сигналы по корреляции
CORR_LAST_FIRED: Dict[str, float] = {}             # "юзер|метрика|пара|вид" -> когда
PUMP_POLL_SEC = max(10.0, float(os.getenv("LIQSCOPE_PUMP_POLL_SEC", "25") or 25))
PUMP_OFF = os.getenv("LIQSCOPE_PUMP_OFF", "").strip() in ("1", "true", "yes", "on")
# Заполняются из pump_scan при старте (так их видит и кабинет, и бот).
PUMP_PERIODS: list = []
PUMP_THRESHOLDS: list = []
PUMP_CANDLES: list = []
# История открытого интереса: уровень пишется каждые OI_SNAP_SEC секунд,
# лежит на диске — после рестарта стенд в канале не пустует первые часы.
OI_HISTORY_FILE = os.getenv("LIQSCOPE_OI_HISTORY",
                            os.path.join(HERE, "data", "oi_history.json"))
OI_SNAP_SEC = max(60.0, float(os.getenv("LIQSCOPE_OI_SNAP_SEC", "300")))
# OI: история снимков тоже за месяц (снимок раз в OI_SNAP_SEC ≈ 5 минут)
OI_KEEP_MIN = int(os.getenv("LIQSCOPE_OI_KEEP_MIN", str(MONTH_HOURS * 60)))
CANDLES: Dict[str, dict] = {}            # "SYM|tf" -> {"candles": [...], "ts", "source"}
MINUTE_VOL: Dict[str, Dict[int, float]] = {}   # symbol -> {minute_ts: volume}
# Минутные потоки по всем монетам: лента «ВСЕ» (CVD/OI), сводки и сервисы.
FLOWS = FlowFeed(keep_min=int(os.getenv("LIQSCOPE_FLOW_KEEP_MIN", "720") or 720))
# Сколько монет потока держать в тиках, когда кто-то смотрит ленту «ВСЕ»
FLOW_SYMBOLS_MAX = int(os.getenv("LIQSCOPE_FLOW_SYMBOLS_MAX", "12") or 0)
# Окно ленты «ВСЕ» (минуты) и частота рассылки строк по WS
FLOW_WINDOW_MIN = max(1, int(os.getenv("LIQSCOPE_FLOW_WINDOW_MIN", "5") or 5))
FLOW_WS_SEC = max(1.0, float(os.getenv("LIQSCOPE_FLOW_WS_SEC", "3") or 3))
# Как часто обновлять OI монет потока (секунды, одна биржа — Binance)
FLOW_OI_SEC = max(10.0, float(os.getenv("LIQSCOPE_FLOW_OI_SEC", "30") or 30))
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
        self.feed = "liq"        # лента клиента: liq | cvd | oi
        self.alive = True

    @property
    def wants_flow(self) -> bool:
        """Нужен ли поток по ВСЕМ монетам: лента CVD/OI в режиме «ВСЕ».

        По свечам графика такая лента не строится — она про одну монету,
        поэтому сервер шлёт минутные потоки по всем, а клиент показывает их
        в тех же колонках.
        """
        return self.symbol == "ALL" and self.feed in ("cvd", "oi")

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
            # Медленный/зависший клиент не должен подвешивать читателей
            # биржевых сокетов: send_json внутри ждёт drain() без лимита, а
            # TCP-буфер забитого клиента может не освобождаться минутами.
            # Всё, что ушло в транспорт до таймаута, остаётся валидным кадром,
            # так что отмена безопасна. Застряли — клиент мёртв, выкидываем.
            await asyncio.wait_for(self.ws.send_json(msg),
                                   timeout=self.SEND_TIMEOUT)
            return True
        except Exception:
            self.alive = False
            return False

    # см. send(): заведомо больше любого нормального сетевого хода
    SEND_TIMEOUT = 5.0


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
# Очередь «тяжёлой» обработки ликвидаций (диск + рассылка клиентам).
# Задачи-читатели бирж только кладут событие в очередь и мгновенно идут
# дальше читать свой сокет: всплеск событий (например, кадр trades HL на
# 8 КБ) больше не подвешивает чтение сокета — раньше burst обрабатывался
# прямо в читателе, тот переставал читать (задержка ack видна в журнале
# инцидента: 2.3с вместо 0.3с), и соединение с биржей умирало 1006-сбросом.
_liq_queue: asyncio.Queue = asyncio.Queue(maxsize=20000)
_liq_dropped = 0
_liq_drop_warn_at = 0.0


# =============================================================================
#  Обработка событий от бирж
# =============================================================================
async def on_liquidation(ev: dict):
    """Пришла ликвидация с биржи → в историю памяти и в очередь воркера.

    ДЕШЁВАЯ функция по контракту: её await'ит читатель биржевого сокета,
    поэтому здесь только словарь, append в память и put_nowait. Запись на
    диск и рассылку клиентам делает liq_event_worker (см. ниже).
    """
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
    # kind="tape" — событие выведено из ленты сделок (Hyperliquid не помечает
    # ликвидации публично, см. hl_infer.py). Едем с событием дальше: в ленте и
    # в окне деталей его надо видеть, иначе вывод выдаётся за факт биржи.
    if ev.get("kind"):
        event["kind"] = ev["kind"]
        if ev.get("liquidation"):
            event["liq"] = {k: v for k, v in ev["liquidation"].items()
                            if k in ("liquidatedUser", "markPx", "method")}
    LIQUIDATIONS.append(event)
    BOARD.add_liq(event)          # часовой стенд (история в терминале)
    SLOTS.add_liq(event)          # 15-минутная сетка постов в канал
    FLOWS.add_liq(event)          # минутный поток по всем монетам (лента «ВСЕ»)
    try:
        _liq_queue.put_nowait(event)
    except asyncio.QueueFull:
        global _liq_dropped, _liq_drop_warn_at
        _liq_dropped += 1
        now = time.time()
        if now - _liq_drop_warn_at > 10:
            _liq_drop_warn_at = now
            log.warning("очередь ликвидаций переполнена: пропущено %d событий",
                        _liq_dropped)


async def history_task() -> None:
    """Раз в несколько минут: свёртки дня на диск и уборка старых дней.

    Свёртки держат ликвидации, CVD и объём по часам, поэтому их потеря при
    рестарте означала бы дырку в месячной истории — пишем часто.
    """
    while True:
        try:
            saved = await asyncio.to_thread(HIST.flush)
            removed = await asyncio.to_thread(HIST.cleanup)
            if removed:
                log.info("История: удалено старых файлов — %d (TTL %.0f ч)",
                         removed, HISTORY_TTL_HOURS)
            if saved:
                log.debug("История: свёртки дней сохранены (%d)", saved)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("история: %s", e)
        await asyncio.sleep(HISTORY_FLUSH_SEC)


async def flow_oi_task() -> None:
    """Открытый интерес монет потока «ВСЕ» (лента OI в режиме всех монет).

    Берём одну биржу — так уровни сравнимы между монетами, а столбик OI в
    ленте не пустует, пока по монете нет полного опроса по всем биржам.
    """
    await asyncio.sleep(8)          # дать ценам и спецификациям подтянуться
    while True:
        try:
            if feed:
                tracker = getattr(feed, "oi", None)
                flow_syms = sorted(getattr(feed, "flow_symbols", ()) or ())
                if tracker is not None and flow_syms:
                    for sym in flow_syms:
                        try:
                            usd = await tracker.binance_usd(sym)
                            if usd:
                                FLOWS.add_oi(sym, time.time(), usd)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:  # noqa: BLE001
                            log.debug("OI потока %s: %s", sym, e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("OI потока: %s", e)
        await asyncio.sleep(FLOW_OI_SEC)


async def oi_history_task() -> None:
    """Каждые OI_SNAP_SEC кладём уровни OI по монетам в историю.

    Источник — трекер бирж (``feed.oi``), тот же, что питает индикатор OI в
    терминале. Монеты, которых нет в трекере, в срез не попадают: лучше
    прочерк в стенде, чем выдуманный уровень.
    """
    loaded = 0
    if OI_HISTORY_FILE:
        try:
            loaded = await asyncio.to_thread(OI.load, OI_HISTORY_FILE)
        except Exception as e:  # noqa: BLE001
            log.debug("история OI не загрузилась: %s", e)
    if loaded:
        log.info("История OI восстановлена: монет %d", loaded)
    while True:
        try:
            tracker = getattr(feed, "oi", None) if feed else None
            if tracker is not None:
                symbols = set()
                try:
                    symbols.update(getattr(tracker, "_series", {}).keys() or [])
                except Exception:  # noqa: BLE001
                    pass
                for sym in list(symbols)[:80]:
                    try:
                        payload = tracker.payload(sym)
                        OI.add(sym, payload.get("total_usd"), payload.get("ts"))
                    except Exception as e:  # noqa: BLE001
                        log.debug("OI-срез %s: %s", sym, e)
            if OI_HISTORY_FILE:
                await asyncio.to_thread(OI.save, OI_HISTORY_FILE)
        except Exception as e:  # noqa: BLE001
            log.warning("история OI: %s", e)
        await asyncio.sleep(OI_SNAP_SEC)


async def liq_event_worker():
    """Один обработчик очереди ликвидаций: диск + рассылка.

    Живёт отдельной задачей (запуск в lifespan), поэтому никакие задержки
    диска или медленного клиента не блокируют читателей биржевых сокетов.
    """
    while True:
        event = await _liq_queue.get()
        try:
            HIST.add(event)          # дневной файл + часовая свёртка
            if BROADCAST_INTERVAL <= 0:
                # без буферизации: событие уходит в сокеты в тот же момент
                await send_liquidations([event])
            else:
                async with _pending_lock:
                    _pending.append(event)
        except Exception as e:  # noqa: BLE001 — ошибка одного события не убивает воркер
            log.warning("обработка ликвидации упала: %s", e)
        finally:
            _liq_queue.task_done()


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
        FLOWS.set_price(symbol, price)
        if side in ("BUY", "SELL") and price > 0 and qty > 0:
            signed = float(price) * float(qty) * (1 if side == "BUY" else -1)
            tick_ts = float(ts) if ts else time.time()
            _cvd_add(symbol, tick_ts, signed)
            BOARD.add_cvd(symbol, tick_ts, signed)
            SLOTS.add_cvd(symbol, tick_ts, signed)
            FLOWS.add_trade(symbol, tick_ts, signed)
            HIST.add_flow(symbol, tick_ts, cvd=signed)
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
        if delta:
            FLOWS.add_volume(symbol, minute, delta)
            HIST.add_flow(symbol, minute, vol=delta)
        if len(vols) > 400:
            for old in sorted(vols)[:200]:
                vols.pop(old, None)

    if time.time() - LAST_TICK_TS.get(symbol, 0.0) < 2.0:
        # тиковая цена свежее снимка kline — берём её
        price = LAST_TICK_PRICE.get(symbol, price)
    FLOWS.set_price(symbol, price)

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


def _round_half_up(v: float) -> int:
    """Math.round из JavaScript: половина всегда вверх (0.5 -> 1, -0.5 -> 0)."""
    return int(math.floor(float(v) + 0.5))


def _liq_cluster_bars(candles: list, tf: int) -> dict:
    """Свечи по времени: {начало свечи: (низ, верх)}, шаг сетки — tf в секундах."""
    bars: Dict[int, tuple] = {}
    for c in candles:
        try:
            t = int(c["time"])
            lo = float(c["low"])
            hi = float(c["high"])
        except (KeyError, TypeError, ValueError):
            continue
        bars[t] = (min(lo, hi), max(lo, hi))
    return bars


def _liq_cluster_add(bars: dict, tf: int, ev: dict, acc: dict) -> None:
    """Событие → плашка свечи: шесть уровней внутри диапазона свечи.

    Ровно тот же расклад, что был у клиента (``drawLiqRects``): плашка стоит
    там, где была ликвидация, а не растянута по телу свечи. Считает сервер —
    значит кластеры видит любой браузер и за всю сохранённую историю, а не
    только за те события, что успели прилететь в открытый терминал.
    """
    ts = _fnum(ev.get("timestamp"))
    if ts <= 0:
        return
    step = max(60, int(tf) * 60)
    bucket = int(ts // step * step)
    bar = bars.get(bucket)
    if bar is None:
        return
    usd = _fnum(ev.get("usd"))
    if usd <= 0:
        return
    lo, hi = bar
    price = _fnum(ev.get("price"))
    if not price > 0:
        price = (lo + hi) / 2 if hi > lo else lo
    price = min(max(price, lo), hi)
    span = hi - lo
    # Округление как у клиента (Math.round), а не банковское из Python:
    # иначе плашка на границе уровня встала бы не туда, где её ждёт терминал
    level = _round_half_up(((price - lo) / span) * (LIQ_CLUSTER_LEVELS - 1)) if span > 0 else 0
    level = min(max(level, 0), LIQ_CLUSTER_LEVELS - 1)
    cell = acc.setdefault(bucket, {"levels": {}, "ts": 0.0})
    if ts > cell["ts"]:
        cell["ts"] = ts
    row = cell["levels"].get(level)
    if row is None:
        # [лонги, шорты, число лонгов, число шортов, сумма цена×деньги, биржи]
        row = [0.0, 0.0, 0, 0, 0.0, {}]
        cell["levels"][level] = row
    if str(ev.get("side") or "") == "SELL":     # SELL = вынесли лонг
        row[0] += usd
        row[2] += 1
    else:
        row[1] += usd
        row[3] += 1
    row[4] += price * usd
    exch = str(ev.get("exchange") or "?").upper()
    slot = row[5].setdefault(exch, [0, 0.0])
    slot[0] += 1
    slot[1] += usd


def _liq_cluster_map(candles: list, tf: int, symbol: str,
                     min_usd: float = 0.0,
                     exchanges: Optional[list] = None) -> tuple:
    """Кластеры ликвидаций по свечам — из сохранённой истории, как OI и CVD.

    Плашки на графике раньше жили только на том, что успел скачать браузер:
    2000 последних событий из памяти сервера и сутки в IndexedDB. Вся история
    при этом лежит на диске (дневные шарды ``HistoryStore``) и пишется туда
    независимо от того, открыт ли у кого-то терминал. Здесь из неё собираются
    кластеры по свечам и уровням цены — клиенту остаётся только нарисовать.

    Свежий хвост (``LIQ_CLUSTER_MEM_SEC``) берём из памяти: события попадают
    туда сразу, а на диск уходят через очередь — так в плашках нет ни дырки,
    ни двойного счёта. Возвращает ``({время свечи: {"l": [уровни], "t": ts}},
    время, докуда посчитана история)``.

    ``exchanges`` — включённые в фильтре биржи: свёртка считается по ним, как и
    живая лента. Пусто/None — все биржи.
    """
    bars = _liq_cluster_bars(candles, tf)
    if not bars or not symbol:
        return {}, 0.0
    sym = canon(symbol)
    now = time.time()
    on_exch = {str(e).strip().upper() for e in (exchanges or []) if str(e).strip()}
    on_exch = on_exch or None
    key = (sym, int(tf), round(float(min_usd or 0.0), 2),
           tuple(sorted(on_exch)) if on_exch else None)
    hit = LIQ_CLUSTER_CACHE.get(key)
    first, last = min(bars), max(bars)
    if (hit and now - hit["ts"] < KLINE_TTL
            and hit["first"] == first and hit["last"] == last):
        return hit["rows"], hit["cut"]
    step = max(60, int(tf) * 60)
    cut = now - LIQ_CLUSTER_MEM_SEC
    since = first
    until = last + step
    floor = max(0.0, float(min_usd or 0.0))
    acc: dict = {}
    if HISTORY_FILE:
        # Читает диск: вызывающий уводит это в отдельный поток (to_thread).
        for ev in HIST.iter_events(since, min(cut, until), sym):
            if floor and _fnum(ev.get("usd")) < floor:
                continue
            if on_exch and str(ev.get("exchange") or "?").upper() not in on_exch:
                continue
            _liq_cluster_add(bars, tf, ev, acc)
    # Без диска источник один — память: берём её целиком, иначе события старше
    # окна свежести потерялись бы совсем (на диск их никто не писал)
    mem_since = max(cut, since) if HISTORY_FILE else since
    for ev in list(LIQUIDATIONS):
        ts = _fnum(ev.get("timestamp"))
        if ts < mem_since or ts > until:
            continue
        if ev.get("symbol") != sym:
            continue
        if floor and _fnum(ev.get("usd")) < floor:
            continue
        if on_exch and str(ev.get("exchange") or "?").upper() not in on_exch:
            continue
        _liq_cluster_add(bars, tf, ev, acc)
    rows: dict = {}
    for bucket, cell in acc.items():
        bar = bars.get(bucket)
        if bar is None:
            continue
        levels = []
        for level in sorted(cell["levels"]):
            lon, sho, nlon, nsho, pxsum, exchs = cell["levels"][level]
            total = lon + sho
            levels.append([level, round(lon, 2), round(sho, 2), nlon, nsho,
                           round(pxsum / total, 8) if total else round(bar[0], 8),
                           {k: [v[0], round(v[1], 2)] for k, v in exchs.items()}])
        rows[str(bucket)] = {"l": levels, "t": round(cell["ts"], 3)}
    if len(LIQ_CLUSTER_CACHE) >= LIQ_CLUSTER_CACHE_MAX:
        for old_key in sorted(LIQ_CLUSTER_CACHE, key=lambda k: LIQ_CLUSTER_CACHE[k]["ts"])[:8]:
            LIQ_CLUSTER_CACHE.pop(old_key, None)
    LIQ_CLUSTER_CACHE[key] = {"ts": now, "first": first, "last": last,
                              "rows": rows, "cut": min(cut, until)}
    # пусто — значит по этой монете в истории ничего нет: клиент продолжает
    # рисовать тем, что успел накопить сам (как и до истории на сервере)
    return (rows, min(cut, until)) if rows else ({}, 0.0)


async def get_candles(symbol: str, tf: int, force: bool = False) -> dict:
    symbol = canon(symbol)
    parsed = parse_tf(tf)
    tf = parsed if parsed is not None else 5
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


def alert_watch_symbols() -> Set[str]:
    """Монеты, которые смотрят алерты — их нужно держать в тиках/OI."""
    from alerts import normalize_config, symbol_of
    out: Set[str] = set()
    need_all = False
    try:
        subs = account_store.list_alert_subscribers()
    except Exception:
        return out
    for s in subs:
        cfg = normalize_config(s.get("config"))
        if not cfg.get("enabled"):
            continue
        # монета у каждой метрики своя — держим в тиках все выбранные
        for metric in cfg["watch"]:
            coin = symbol_of(cfg, metric)
            if coin == "ALL":
                need_all = True
            else:
                out.add(coin)
    if need_all:
        now = time.time()
        totals: Dict[str, float] = {}
        for x in LIQUIDATIONS:
            if now - float(x.get("timestamp") or 0) <= 3600:
                totals[x["symbol"]] = totals.get(x["symbol"], 0.0) + float(x.get("usd") or 0)
        out.update(sorted(totals, key=totals.get, reverse=True)[:8])
        if feed and feed.symbols:
            out.add(feed.symbols[0])
    return out


def sync_hot_symbols():
    """Сообщаем фиду, чьи графики сейчас открыты — по ним нужен каждый тик."""
    if not feed:
        return
    hot = {c.chart_symbol for c in hub.clients}
    hot |= alert_watch_symbols()
    if not hot and feed.symbols:
        hot = {feed.symbols[0]}          # держим BTC тёплым для быстрого старта
    feed.set_hot_symbols(hot)
    # Лента «ВСЕ» в CVD/OI: её монетам нужны тики сделок (CVD). Графики у
    # этих монет не открыты, поэтому в hot_symbols они не попадают — иначе
    # заодно вырос бы опрос OI по всем биржам.
    want_flow = FLOW_SYMBOLS_MAX > 0 and any(c.wants_flow for c in hub.clients)
    flow = []
    if want_flow:
        flow = [s for s in (feed.symbols or []) if s not in hot][:FLOW_SYMBOLS_MAX]
    feed.set_flow_symbols(flow)


# Снимки для кабинета (корреляции, сторож) собираются по истории и опросам.
# Один и тот же ответ нужен всем открытым вкладкам, а опрос идёт каждые 30 и
# 12 секунд: без кэша сервер пересчитывал матрицы корреляций на каждый запрос
# в потоке событий — из-за этого кнопки в кабинете отзывались «туго».
_SNAP_CACHE: Dict[str, tuple] = {}
SNAP_CACHE_TTL = float(os.getenv("LIQSCOPE_SNAP_CACHE_SEC", "12") or 12)


def cached_snapshot(key: str, build, ttl: Optional[float] = None):
    """Готовый снимок из кэша: считаем не чаще, чем раз в ``ttl`` секунд."""
    ttl = SNAP_CACHE_TTL if ttl is None else float(ttl)
    now = time.time()
    hit = _SNAP_CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    data = build()
    _SNAP_CACHE[key] = (now, data)
    if len(_SNAP_CACHE) > 64:                 # ключи редкие, но мусор не копим
        for k in sorted(_SNAP_CACHE, key=lambda k: _SNAP_CACHE[k][0])[:-32]:
            _SNAP_CACHE.pop(k, None)
    return data


def correlations_snapshot(window: str = "24h", metric: str = "liq") -> dict:
    """Картина корреляций по месячной истории: часовые свёртки + ряды OI.

    Данные берём из истории (history.HistoryStore) и снимков OI — за окно от
    часа до недели, поэтому сервис видит больше, чем минутный поток в памяти.
    Матрицы считаются по всем метрикам сразу, поэтому кэш — по окну: смена
    метрики в кабинете обходится без пересчёта и отвечает мгновенно.
    """
    minutes = 0
    try:
        from correlations import window_minutes as _wm
        minutes = _wm(window)
    except Exception:  # noqa: BLE001
        minutes = 0
    data = cached_snapshot(f"corr|{window}", lambda: _correlations_build(window))
    if isinstance(data, dict):
        out = dict(data)
        out["metric"] = metric or out.get("metric")
        return out
    return data


def _correlations_build(window: str = "24h") -> dict:
    from correlations import build, window_minutes
    now = time.time()
    minutes = window_minutes(window)
    cells = HIST.hours_range(now - minutes * 60, now, now)
    oi_series: Dict[str, list] = {}
    tracker = getattr(OI, "_series", None)
    if isinstance(tracker, dict):
        for sym, series in list(tracker.items()):
            try:
                oi_series[sym] = [(t, v) for t, v in (series or [])]
            except Exception:  # noqa: BLE001
                continue
    oi_flat = getattr(OI, "_flat", None)
    if isinstance(oi_flat, dict):
        for sym, rows in list(oi_flat.items()):
            oi_series.setdefault(sym, list(rows or []))
    prices = dict(feed.prices) if feed else {}
    return build(cells, oi_series, prices, window=window, now=now)


def _oi_row(tracker, sym: str) -> dict:
    """Строка OI для алертов: готовые окна + ряд уровней для перезапуска окна.

    Окна трекера (m5/h1/…) посчитаны от «сейчас минус окно», а после сигнала
    окно метрики должно начаться заново — для этого рядом кладём непрерывный
    ряд уровней: по нему движок сам считает ΔOI от прошлого сигнала.
    """
    row = tracker.payload(sym)
    try:
        row["_series"] = tracker.series(sym)
    except Exception:
        row["_series"] = {}
    # ряд живых опросов (десятки секунд): по нему микрографик OI двигается,
    # а не стоит картинкой до следующего 5-минутного бакета
    try:
        row["_live"] = tracker.live_series(sym)
    except Exception:
        row["_live"] = {}
    return row


def alerts_market_snapshot() -> dict:
    """Снимок для движка алертов и кабинета."""
    now = time.time()
    events = [x for x in LIQUIDATIONS
              if now - float(x.get("timestamp") or 0) <= 4 * 3600]
    cvd = {k: dict(v) for k, v in CVD_ACC.items()}
    oi: Dict[str, dict] = {}
    tracker = getattr(feed, "oi", None) if feed else None
    if tracker is not None:
        for sym in list(alert_watch_symbols())[:16]:
            try:
                oi[sym] = _oi_row(tracker, sym)
            except Exception:
                pass
        if not oi:
            for sym in list(getattr(tracker, "_watched", {}) or {})[:8]:
                try:
                    oi[sym] = _oi_row(tracker, sym)
                except Exception:
                    pass
    def _oi_has_data(rows: Dict[str, dict]) -> bool:
        for r in (rows or {}).values():
            for ch in ((r or {}).get("changes") or {}).values():
                if ch and ch.get("usd") is not None:
                    return True
        return False

    if DEMO_MODE and not _oi_has_data(oi):
        # трекер бирж в демо пуст — берём демо-ряд: без него сервис «Алерты по
        # объёму» показывал OI нулём, а лента и график стояли пустыми
        demo = {}
        for sym in sorted(oi) or list(alert_watch_symbols())[:8]:
            payload = _demo_oi_payload_from_series(sym)
            demo[sym] = {"symbol": sym, "total_usd": payload["total_usd"],
                         "changes": payload["changes"],
                         "_series": payload["_series"]}
        if not demo:
            for sym in ("BTC_USDT", "ETH_USDT", "SOL_USDT"):
                payload = _demo_oi_payload_from_series(sym)
                demo[sym] = {"symbol": sym, "total_usd": payload["total_usd"],
                             "changes": payload["changes"],
                             "_series": payload["_series"]}
        oi = demo
    return {"now": now, "events": events, "cvd": cvd, "oi": oi}


def pump_watchers() -> List[dict]:
    """Кто следит за пампами: подписчики сервиса «Сторож монет»."""
    out = []
    for sub in account_store.list_service_subscribers("watchlist"):
        cfg = pump_normalize(sub.get("config") or {})
        if cfg.get("enabled"):
            row = dict(sub)
            row["pump"] = cfg
            # язык сигнала: выбор человека или локаль Telegram, а если их нет —
            # язык по умолчанию (сейчас английский)
            from bot_i18n import normalize_lang
            row["lang"] = normalize_lang(sub.get("language"))
            out.append(row)
    return out


def pump_normalize(config: Optional[dict]) -> dict:
    from pump_scan import normalize
    return normalize(config)


def pump_snapshot(config: Optional[dict] = None, lang: str = "ru") -> dict:
    """Картина для кабинета и бота: настройки, резкие движения, сигналы.

    Перебор всех контрактов Gate — самая тяжёлая часть ответа, а спрашивают его
    все открытые кабинеты каждые 12 секунд (и бот — при каждом нажатии). Держим
    короткий кэш на настройки, а живые поля (время, счётчики, лента сигналов)
    считаем заново: админ не должен ждать, пока доска дорисуется.
    """
    from pump_scan import CANDLE_PRESETS as _CANDS, PERIODS as _PERIODS
    from pump_scan import THRESHOLDS as _THR
    cfg = pump_normalize(config or {})

    def build() -> dict:
        return {
            "config": cfg,
            "settings_text": pump_settings_text(cfg),
            "movers": PUMPS.movers(cfg, limit=12),
            "hits": PUMPS.scan(cfg)[:12],
            "periods": [{"key": k, "minutes": m} for k, m in (PUMP_PERIODS or _PERIODS)],
            "thresholds": list(PUMP_THRESHOLDS or _THR),
            "candles": list(PUMP_CANDLES or _CANDS),
        }

    key = "pump|" + json.dumps(cfg, sort_keys=True) + "|" + str(lang)
    data = dict(cached_snapshot(key, build, ttl=4))
    now = time.time()
    upd = PUMPS.last_update()
    data["now"] = now
    data["config"] = cfg
    data["status"] = {
        "coins": PUMPS.known(),
        "updated": upd,
        "age_sec": (now - upd) if upd else None,
        "poll_sec": PUMP_POLL_SEC,
        "signals_total": len(PUMP_SIGNALS),
        "lang": lang,
    }
    data["signals"] = list(PUMP_SIGNALS)[-30:][::-1]
    return data


def pump_settings_text(cfg: dict) -> str:
    from pump_scan import format_settings_text
    return format_settings_text(cfg)


async def gate_ticker_snapshot() -> dict:
    """Один запрос Gate за всеми USDT-контрактами: цены, оборот, изменение."""
    session = getattr(feed, "_session", None) if feed else None
    if session is None:
        return {}
    data = await _get_json(session, f"{GATE_REST}/tickers", timeout=15)
    out = {"prices": {}, "volume": {}, "change24": {}}
    for t in data or []:
        contract = str(t.get("contract") or "")
        if not contract.endswith("_USDT"):
            continue
        sym = canon(contract)
        out["prices"][sym] = float(t.get("last") or 0)
        out["volume"][sym] = float(t.get("volume_24h_quote")
                                   or t.get("volume_24h_usd")
                                   or t.get("volume_24h") or 0)
        out["change24"][sym] = float(t.get("change_percentage") or 0)
    return out


async def pump_loop():
    """Сторож монет: минутные цены всех монет Gate и сигналы в Telegram.

    Один REST-запрос отдаёт все USDT-контракты сразу, поэтому следить за всем
    рынком дешевле, чем за подписками по каждой паре. Сигнал уходит тем, кто
    включил сервис «Сторож монет», и предлагает посмотреть монету на Gate
    по партнёрской ссылке (refs.GATE_REF).
    """
    from pump_scan import CANDLE_PRESETS as _CANDS, THRESHOLDS as _THR
    global PUMP_CANDLES, PUMP_PERIODS, PUMP_THRESHOLDS
    PUMP_CANDLES, PUMP_THRESHOLDS = _CANDS, _THR
    if PUMP_OFF:
        log.info("Сторож монет выключен (LIQSCOPE_PUMP_OFF=1)")
        return
    from pump_scan import PERIODS
    PUMP_PERIODS = PERIODS
    await asyncio.sleep(10)
    fails = 0
    while True:
        try:
            snap = await gate_ticker_snapshot()
            if snap.get("prices"):
                added = PUMPS.add_prices(snap["prices"], volume=snap["volume"],
                                         change24=snap["change24"])
                fails = 0
                if added:
                    log.debug("Сторож монет: %d монет, новых минуток %d",
                              PUMPS.known(), added)
                await pump_notify()
            else:
                fails += 1
                if fails in (1, 5, 30):
                    log.info("Сторож монет: Gate не отдал тикеры (попытка %d) — "
                             "в демо-режиме это нормально", fails)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            fails += 1
            if fails in (1, 10):
                log.warning("Сторож монет: %s", e)
        try:
            await asyncio.sleep(PUMP_POLL_SEC)
        except asyncio.CancelledError:
            break


async def pump_notify():
    """Разослать сигналы подписчикам: один сигнал — одна монета и режим."""
    watchers = pump_watchers()
    if not watchers:
        return
    now = time.time()
    by_user: Dict[int, list] = {}
    for w in watchers:
        hits = PUMPS.scan(w["pump"])
        if not hits:
            continue
        fresh = pump_filter_new(hits, PUMP_LAST_FIRED, now,
                                w["pump"].get("cooldown_min", 10))
        if fresh:
            by_user[int(w["user_id"])] = fresh
    for user_id, fresh in by_user.items():
        for hit in fresh:
            event = dict(hit)
            event["ts"] = now
            event["user_id"] = user_id
            PUMP_SIGNALS.append(event)
    if not by_user:
        return
    for w in watchers:
        fresh = by_user.get(int(w["user_id"]))
        if not fresh:
            continue
        tg_id = int(w.get("tg_id") or 0)
        if not tg_id or not tg_bot.running:
            continue
        for hit in fresh[:3]:
            lang = w.get("lang") or "ru"
            tg_bot.remember_lang(tg_id, lang)
            await tg_bot.send(
                tg_id, pump_signal_html(hit, lang),
                markup={"inline_keyboard": [[
                    {"text": "💠 Открыть на Gate", "url": gate_url(lang)},
                    {"text": "🌐 Терминал", "url": PUBLIC_URL + "/terminal"},
                ]]})


async def alerts_oi_warmup():
    tracker = getattr(feed, "oi", None) if feed else None
    if tracker is None:
        return
    for sym in list(alert_watch_symbols())[:12]:
        try:
            await tracker.ensure_symbol(sym)
        except Exception as e:
            log.debug("alerts oi %s: %s", sym, e)


def alerts_due(cfg: dict, market: dict, last_fire, now: float) -> List[dict]:
    """Что из пороговых сигналов реально уходит в чат.

    Три правила против повторов:

    * у каждой метрики своё окно (``windows``);
    * окно метрики начинается заново после её прошлого сигнала — данные, из
      которых сигнал уже ушёл, в следующее сообщение не попадают
      (даёт ``since`` в :func:`alerts.evaluate`);
    * пауза ``MIN_GAP_SEC`` и одна карточка на метрику (лидер, остальные
      монеты — строкой «ещё в волне»), иначе одна волна рассыпается на
      пачку сообщений.

    ``last_fire(metric, symbol)`` — время прошлого сигнала у пользователя.
    """
    from alerts import (MIN_GAP_SEC, evaluate, normalize_config, should_fire,
                        top_per_metric)
    cfg = normalize_config(cfg)
    out: List[dict] = []
    for hit in top_per_metric(evaluate(cfg, market, since=last_fire)):
        last = last_fire(hit["metric"], hit["symbol"])
        if not should_fire(last, now, MIN_GAP_SEC / 60.0):
            continue
        out.append(hit)
        if len(out) >= 3:
            break
    return out


async def alert_loop():
    """Раз в несколько секунд проверяет пороги и шлёт в Telegram."""
    from alerts import format_alert_html, normalize_config
    try:
        await asyncio.sleep(20)
    except asyncio.CancelledError:
        return
    while True:
        try:
            await alerts_oi_warmup()
            market = alerts_market_snapshot()
            now = market["now"]
            subs = account_store.list_alert_subscribers()
            # Язык подписчика — до отправки: сигнал уходит на его языке
            tg_bot.warm_langs(subs)
            for sub in subs:
                cfg = normalize_config(sub.get("config"))
                if not cfg.get("enabled"):
                    continue
                uid = sub["user_id"]

                def last_fire(metric, symbol, _uid=uid):
                    """Когда метрика последний раз сигналила у юзера.

                    Для подписки на все монеты сигналы лежат под конкретными
                    монетами — берём самый свежий по метрике.
                    """
                    if not symbol or str(symbol).upper() in ("ALL", ""):
                        return account_store.last_alert_any(_uid, metric)
                    return account_store.last_alert_ts(_uid, metric, symbol)

                for hit in alerts_due(cfg, market, last_fire, now):
                    account_store.add_alert_event(uid, hit)
                    tg_id = int(sub.get("tg_id") or 0)
                    if tg_id and tg_bot.running:
                        await tg_bot.send(
                            tg_id,
                            format_alert_html(hit, tg_bot.site_url()),
                            markup=tg_bot.site_link_kb("посмотреть в терминале"),
                        )

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("alerts: %s", e)
        try:
            await asyncio.sleep(8)
        except asyncio.CancelledError:
            break


def corr_alert_pictures(alerts_cfg: dict) -> Dict[str, dict]:
    """Картины корреляций по окнам, которые включены в алертах.

    Матрицы в снимке считаются сразу по всем метрикам, поэтому разных окон
    ровно столько, сколько включено у пользователя, — пересчёт не на каждый
    сигнал, а один раз на окно (и всё это под общим кэшем снимков).
    """
    from correlations import normalize_alerts
    pics: Dict[str, dict] = {}
    cfg = normalize_alerts(alerts_cfg)
    for key, row in cfg.items():
        if not row.get("enabled"):
            continue
        win = row.get("window") or "24h"
        if win in pics:
            continue
        try:
            pics[win] = correlations_snapshot(win, key) or {}
        except Exception as e:                             # noqa: BLE001
            log.debug("corr alerts %s/%s: %s", key, win, e)
            pics[win] = {}
    return pics


def corr_alerts_due(alerts_cfg: dict, pictures: Dict[str, dict], last_fire,
                    now: float, limit: int = 3) -> List[dict]:
    """Сигналы по корреляции к отправке: пауза по паре, не больше трёх карточек.

    ``last_fire(metric, symbol)`` — когда пара последний раз сигналила у
    пользователя: одна и та же связь не должна приходить каждые двадцать
    секунд, пауза считается от окна метрики (:func:`correlations.alert_gap_sec`).
    """
    from correlations import alert_gap_sec, evaluate_alerts
    out: List[dict] = []
    for hit in evaluate_alerts(alerts_cfg, pictures):
        last = last_fire(hit.get("metric"), hit.get("symbol"))
        if last and now - last < alert_gap_sec(hit.get("window_min") or 0):
            continue
        out.append(hit)
        if len(out) >= max(1, int(limit)):
            break
    return out


async def corr_alert_loop():
    """Алерты по корреляции: у каждой метрики своё окно и свои пороги.

    Сигнал — пара монет, у которой связь перешагнула порог: «в противофазе»
    (минус, например −0.5 на часе) или «в одну сторону» (плюс: 0.5 значит
    «коэффициент 0.5 и выше»). Повтор по той же паре молчит четверть окна,
    иначе одна и та же связь уходила бы сообщением каждые восемь секунд.
    """
    from correlations import format_alert_html, normalize_alerts
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        return
    while True:
        try:
            subs = account_store.list_service_subscribers("correlations")
            if subs:
                tg_bot.warm_langs(subs)
            for sub in subs:
                cfg = normalize_alerts(sub.get("config"))
                if not any(r.get("enabled") for r in cfg.values()):
                    continue
                uid = sub["user_id"]
                now = time.time()
                pics = corr_alert_pictures(cfg)

                def last_fire(metric, symbol, _uid=uid):
                    return account_store.last_alert_ts(
                        _uid, f"corr_{metric}", str(symbol or ""))

                for hit in corr_alerts_due(cfg, pics, last_fire, now):
                    metric = str(hit.get("metric") or "")
                    anchor_metric = f"corr_{metric}"
                    account_store.add_alert_event(uid, dict(hit, metric=anchor_metric))
                    CORR_SIGNALS.append(dict(hit, ts=now, user_id=uid))
                    tg_id = int(sub.get("tg_id") or 0)
                    if tg_id and tg_bot.running:
                        await tg_bot.send(
                            tg_id,
                            format_alert_html(hit, tg_bot.site_url()),
                            markup=tg_bot.site_link_kb("тепловая карта"),
                        )
        except asyncio.CancelledError:
            break
        except Exception as e:                                 # noqa: BLE001
            log.warning("corr alerts: %s", e)
        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            break


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


def _cvd_window(symbol: str, sec: float = 14400.0) -> Optional[float]:
    """Сумма тейкер-дельты по монете за окно. None — нет тиков."""
    now = time.time()
    for tf in (5, 15, 60, 240):
        acc = CVD_ACC.get(f"{symbol}|{tf}") or {}
        if not acc:
            continue
        try:
            return round(sum(float(v) for b, v in acc.items()
                             if now - float(b) <= sec), 2)
        except (TypeError, ValueError):
            continue
    entry = CANDLES.get(f"{symbol}|240") or {}
    series = entry.get("candles") or []
    if not series:
        return None
    last = series[-1] or {}
    try:
        if now - float(last.get("time") or 0) <= sec and last.get("cvd") is not None:
            return float(last["cvd"])
    except (TypeError, ValueError):
        return None
    return None


async def slot_flows(need_slots: int = 40) -> Dict[str, dict]:
    """Потоки по слотам постов: {монета: {начало слота: {cvd, vol, has_cvd}}}.

    Слот — базовая сетка постов (15 минут), поэтому блок анализа поста
    (N/4 часа при частоте раз в N часов) складывается из целых слотов и доля
    CVD в объёме рынка честная и на четверти часа, и на двух с половиной.

    Источник первый — минутные потоки процесса (``FLOWS``): по ним объём и
    тейкер-дельта уже сведены по минутам без походов в сеть, и после рестарта
    они наполняются с нуля. Если по монете минут нет (например, сервис только
    поднялся), слоты добираются 15-минутными свечами биржи.
    """
    from hour_board import SLOT_SEC
    need_slots = max(4, int(need_slots))
    out: Dict[str, dict] = {}
    try:
        out = FLOWS.by_slot(SLOT_SEC, need_slots)
    except Exception as e:                       # noqa: BLE001
        log.debug("потоки постов: минутки не собрались: %s", e)
        out = {}
    # Монеты, по которым минут нет вовсе — тем нужны свечи
    cells = []
    try:
        cells = SLOTS.slots(need_slots)
    except Exception as e:                       # noqa: BLE001
        log.debug("потоки постов: слоты не собрались: %s", e)
    volume: Dict[str, float] = {}
    for cell in cells:
        for sym, usd in (cell.get("coins") or {}).items():
            volume[sym] = volume.get(sym, 0.0) + float(usd or 0)
    coins = [s for s, _v in sorted(volume.items(), key=lambda kv: kv[1],
                                   reverse=True)[:16]]
    missing = [s for s in coins if not out.get(s)]
    if missing:
        sem = asyncio.Semaphore(5)

        async def one(sym: str):
            async with sem:
                try:
                    entry = await asyncio.wait_for(get_candles(sym, 15), timeout=20)
                except Exception as e:           # noqa: BLE001
                    log.debug("потоки постов %s: %s", sym, e)
                    return sym, None
                return sym, entry

        for sym, entry in await asyncio.gather(*(one(s) for s in missing)):
            rows = (entry or {}).get("candles") or []
            by_slot: Dict[int, dict] = {}
            for c in rows[-max(need_slots + 2, 8):]:
                try:
                    h = int(float(c.get("time") or 0))
                except (TypeError, ValueError):
                    continue
                if not h:
                    continue
                cell = by_slot.setdefault(h, {"cvd": 0.0, "vol": 0.0,
                                              "has_cvd": False})
                try:
                    cell["vol"] += float(c.get("volume") or 0)
                except (TypeError, ValueError):
                    pass
                if c.get("cvd") is not None:
                    try:
                        cell["cvd"] += float(c["cvd"])
                    except (TypeError, ValueError):
                        continue
                    cell["has_cvd"] = True
            if by_slot:
                out[sym] = by_slot
    return out


def post_interval_hours() -> int:
    """Частота постов в канал прямо сейчас: настройка админки, иначе окружение."""
    from channel_digest import interval_hours
    return interval_hours(account_store, default=POST_INTERVAL_H)


def set_post_interval_hours(value, actor_id: Optional[int] = None) -> int:
    """Сохранить частоту постов (1…10 часов). Возвращает применённое значение."""
    from channel_digest import INTERVAL_SETTING, clamp_interval
    hours = clamp_interval(value)
    account_store.set_setting(INTERVAL_SETTING, str(hours), actor_id=actor_id)
    return hours


def _board_window_facts(board: dict) -> dict:
    """Итоги окна поста из блоков стенда: суммы, монеты и крупнейший удар.

    Кольцевой буфер событий держит 60 тысяч ликвидаций — на окне в 10 часов
    (пост раз в 10 ч) он обрезается, и в посте выходила заниженная касса. Блоки
    стенда копят те же события без обрезки, поэтому итог берём по ним.
    """
    hours = (board or {}).get("hours") or []
    total = longs = shorts = 0.0
    count = 0
    coins: Dict[str, dict] = {}
    best: Optional[dict] = None
    for hr in hours:
        total += float(hr.get("total") or 0)
        longs += float(hr.get("longs") or 0)
        shorts += float(hr.get("shorts") or 0)
        count += int(hr.get("count") or 0)
        for sym, usd in (hr.get("coins") or {}).items():
            c = coins.setdefault(sym, {"symbol": sym, "usd": 0.0, "longs": 0.0,
                                       "shorts": 0.0, "count": 0})
            c["usd"] += float(usd or 0)
        for it in (hr.get("top") or []):
            try:
                usd = float(it.get("usd") or 0)
            except (TypeError, ValueError):
                continue
            if usd > 0 and (best is None or usd > float(best.get("usd") or 0)):
                best = dict(it, usd=usd)
    top_coins = sorted(coins.values(), key=lambda c: c["usd"], reverse=True)
    return {
        "total_usd": total,
        "longs_usd": longs,
        "shorts_usd": shorts,
        "count": count,
        "top_coins": top_coins[:8],
        "biggest": best,
    }


def _window_exchanges(now: float, window_sec: float) -> Dict[str, float]:
    """Раскладка окна по биржам из месячной истории (часовые свёртки)."""
    try:
        cells = HIST.hours_range(now - float(window_sec), now, now)
        from history import aggregate_hours
        agg = aggregate_hours(cells) or {}
        return dict(agg.get("by_exchange") or {})
    except Exception as e:                       # noqa: BLE001
        log.debug("биржи окна: %s", e)
        return {}


async def build_channel_digest() -> dict:
    """Снимок рынка за окно поста в канал: лидеры, биржи, OI, CVD.

    Окно равно промежутку между постами (частота задаётся в админке), а блок
    анализа внутри поста — четверти окна: пост раз в час разбирает по 15 минут,
    раз в 4 часа — по часу, раз в 10 часов — по 2.5 часа.
    """
    from channel_digest import block_secs, collect_digest
    interval = post_interval_hours()
    window_sec = interval * 3600
    now = time.time()
    events = list(LIQUIDATIONS)
    preview = collect_digest(events, window_sec=window_sec, now=now)
    oi: Dict[str, dict] = {}
    cvd: Dict[str, float] = {}
    tracker = getattr(feed, "oi", None) if feed else None
    # шесть монет: в компактной сводке таблица «Монеты» — пять строк,
    # и каждая должна получить свою метрику (OI или CVD)
    for c in (preview.get("top_coins") or [])[:6]:
        sym = c.get("symbol")
        if not sym:
            continue
        v = _cvd_window(sym)
        if v is not None:
            cvd[sym] = v
        if tracker is None:
            continue
        try:
            await tracker.ensure_symbol(sym)
            oi[sym] = tracker.payload(sym)
        except Exception as e:
            log.debug("digest oi %s: %s", sym, e)
    snap = collect_digest(events, window_sec=window_sec, now=now, oi=oi, cvd=cvd)
    snap["interval_h"] = interval
    snap["block_sec"] = block_secs(interval)
    snap["window_sec"] = window_sec
    # Стенд постов: блоки по N/4 часа на 15-минутной сетке (ликвы, перекос CVD,
    # OI), в посте он идёт сразу после шапки.
    try:
        flows = await slot_flows((2 * 4 + 2) * interval)
        board = build_snapshot(SLOTS, OI, now=now, span=4, group=interval,
                               flows=flows)
        snap["board"] = board
        # Итог окна — по блокам стенда: он не зависит от того, обрезался ли
        # буфер событий (для частых постов это неважно, для раз в 10 часов —
        # принципиально). Если стенд ещё пуст, остаются цифры по событиям.
        facts = _board_window_facts(board)
        if facts["total_usd"] > float(snap.get("total_usd") or 0):
            snap["total_usd"] = facts["total_usd"]
            snap["longs_usd"] = facts["longs_usd"]
            snap["shorts_usd"] = facts["shorts_usd"]
            snap["count"] = facts["count"]
            if facts["top_coins"]:
                snap["top_coins"] = facts["top_coins"]
            if facts["biggest"]:
                snap["biggest"] = facts["biggest"]
            snap["totals_source"] = "board"
        exchanges = _window_exchanges(now, window_sec)
        if exchanges:
            snap["exchanges"] = exchanges
    except Exception as e:
        log.warning("стенд для поста не собрался: %s", e)
    return snap


async def oi_payload(symbol: str) -> Optional[dict]:
    """Снимок открытого интереса по монете (для дайджеста и кабинета)."""
    tracker = getattr(feed, "oi", None) if feed else None
    if tracker is None:
        return None
    try:
        await tracker.ensure_symbol(symbol)
    except Exception as e:
        log.debug("oi %s: %s", symbol, e)
        return None
    try:
        return tracker.payload(symbol)
    except Exception as e:
        log.debug("oi payload %s: %s", symbol, e)
        return None


async def digest_ai(facts: dict, lang: str = "ru") -> Optional[str]:
    """Рассказ для дневного дайджеста: ИИ, если ключи есть."""
    ai = getattr(tg_bot, "ai", None)
    if ai is None or not getattr(ai, "enabled", False):
        return None
    try:
        return await ai.narrative(facts, lang)
    except Exception as e:
        log.warning("ИИ-дайджест (%s): %s", lang, e)
        return None


# =============================================================================
#  Фоновые рассылки
# =============================================================================
def flow_snapshot(now: Optional[float] = None, limit: int = 60) -> dict:
    """Строки ленты «ВСЕ»: CVD, OI и ликвидации по всем монетам за окно."""
    now = float(now if now is not None else time.time())
    return {
        "type": "flow_all",
        "window_min": FLOW_WINDOW_MIN,
        "ts": now,
        "cvd": FLOWS.rows("cvd", FLOW_WINDOW_MIN, now, limit=limit),
        "oi": FLOWS.rows("oi", FLOW_WINDOW_MIN, now, limit=limit),
        "liq": FLOWS.rows("liq", FLOW_WINDOW_MIN, now, limit=limit),
        "summary": FLOWS.summary(60, now),
    }


async def flow_broadcaster():
    """Рассылка минутных потоков тем, у кого лента CVD/OI в режиме «ВСЕ».

    Пока таких клиентов нет, ничего не считаем: строки собираются по запросу.
    """
    while True:
        try:
            await asyncio.sleep(FLOW_WS_SEC)
            async with hub._lock:
                clients = [c for c in hub.clients if c.wants_flow]
            if not clients:
                continue
            payload = flow_snapshot()
            for c in clients:
                await c.send(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("flow broadcaster: %s", e)


async def liquidation_broadcaster():
    """Работает только если включена буферизация (LIQSCOPE_LIQ_FLUSH_MS > 0)."""
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
#  Демо-генератор (только при LIQSCOPE_DEMO=1)
# =============================================================================
async def demo_generator():
    log.warning("ВКЛЮЧЁН ДЕМО-РЕЖИМ: поток ликвидаций синтетический (LIQSCOPE_DEMO=1)")
    exchanges = ["binance", "bybit", "okx", "gate", "bitget", "htx",
                 "hyperliquid", "dydx", "kraken", "bitfinex"]
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
def demo_pump_seed(minutes: int = 20) -> None:
    """Демо-история сторожа: минутки по всем монетам, чтобы панель была живой.

    Без сети Gate тикеры не приходят, и «Сторож монет» показывал бы пустую
    панель. Наполняем минутную историю случайным ходом, а паре монет даём
    заметный памп и дамп — как это выглядит на живом рынке.
    """
    syms = list((feed.symbols if feed else []) or list(DEMO_SEED_PRICES))[:120]
    if not syms:
        syms = list(DEMO_SEED_PRICES)
    now = time.time()
    minute = int(now // 60) * 60
    up_sym = random.choice(syms)
    others = [x for x in syms if x != up_sym] or syms
    down_sym = random.choice(others)
    for sym in syms:
        base = feed.prices.get(sym) if feed else None
        base = base or DEMO_SEED_PRICES.get(sym) or random.uniform(1, 100)
        DEMO_PUMP_VOL[sym] = random.uniform(4e5, 6e7)
        price = base
        series = []
        for i in range(minutes - 1, -1, -1):
            price = max(price * (1 + random.gauss(0.0, 0.004)), 1e-12)
            series.append((minute - 60 * i, price))
        kind = 0.32 if sym == up_sym else (-0.28 if sym == down_sym else 0.0)
        if kind:
            tail = 6
            for j in range(tail):
                idx = len(series) - tail + j
                ts, p = series[idx]
                series[idx] = (ts, max(p * (1 + kind * (j + 1) / tail), 1e-12))
        for ts, p in series:
            PUMPS.add_prices({sym: p}, ts=ts)
        last = series[-1][1]
        PUMPS.add_prices({sym: last}, ts=now, volume={sym: DEMO_PUMP_VOL[sym]},
                         change24={sym: round((last / series[0][1] - 1) * 100, 2)})


async def demo_pump_walk():
    """Демо-цены сторожа: живые минутки всех монет и редкие всплески."""
    await asyncio.sleep(2.0)
    while True:
        try:
            await asyncio.sleep(5.0)
            now = time.time()
            syms = list((feed.symbols if feed else []) or list(DEMO_SEED_PRICES))[:120]
            prices = {}
            for sym in syms:
                p = (PUMPS.price_now(sym) or DEMO_SEED_PRICES.get(sym)
                     or random.uniform(1, 100))
                if random.random() < 0.01:
                    p *= random.choice((1.10, 1.22, 0.90, 0.78))
                else:
                    p *= 1 + random.gauss(0, 0.0025)
                prices[sym] = max(p, 1e-12)
                if feed and sym in feed.prices:
                    feed.prices[sym] = prices[sym]
            PUMPS.add_prices(prices, ts=now, volume=DEMO_PUMP_VOL)
            if PUMPS.known():
                await pump_notify()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            log.debug("demo pumps: %s", e)


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
            if not feed:
                continue
            # монеты потока «ВСЕ» тоже ходят демо-тиками: без бирж CVD-лента
            # в режиме всех монет иначе осталась бы пустой
            walk = set(feed.hot_symbols) | set(feed.flow_symbols)
            if not walk:
                continue
            for symbol in list(walk):
                p = feed.prices.get(symbol) or DEMO_SEED_PRICES.get(symbol) or 100.0
                p = max(p * (1 + random.gauss(0, 0.00025)), 1e-12)
                feed.prices[symbol] = p
                await on_trade(symbol, p, random.uniform(0.01, 3.0), time.time(),
                               random.choice(("BUY", "SELL")))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("demo ticks: %s", e)


async def demo_flow_walk():
    """Демо-наполнение потока «ВСЕ»: объём и OI по монетам без бирж.

    Нужен только для демо-режима: там тики синтетические, и лента CVD/OI в
    режиме всех монет иначе показывала бы перекос, но пустой объём и OI.
    """
    levels: Dict[str, float] = {}
    while True:
        try:
            await asyncio.sleep(1.0)
            if not feed:
                continue
            syms = (set(feed.flow_symbols) | set(feed.hot_symbols)
                    or set(feed.symbols[:FLOW_SYMBOLS_MAX or 12]))
            now = time.time()
            for symbol in list(syms)[:24]:
                base = levels.get(symbol)
                if base is None:
                    base = levels[symbol] = random.uniform(2e7, 4e9)
                base = max(base * (1 + random.gauss(0, 0.0015)), 1e6)
                levels[symbol] = base
                FLOWS.add_oi(symbol, now, base)
                vol = random.uniform(5e4, 9e5)
                FLOWS.add_volume(symbol, now, vol)
                HIST.add_flow(symbol, now, vol=vol)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug("demo flow: %s", e)


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
            # сначала читаем дневные шарды (месяц), затем одиночный файл
            # прежних версий — иначе после обновления история обрывалась бы
            loaded = await asyncio.to_thread(
                HIST.query, time.time() - HISTORY_TTL_HOURS * 3600, None,
                None, 0.0, HISTORY_MAX, False)
            legacy = await asyncio.to_thread(
                load_history_file, HISTORY_FILE, HISTORY_MAX, HISTORY_TTL_HOURS)
            seen = {e.get("id") for e in loaded}
            loaded.extend(e for e in legacy if e.get("id") not in seen)
            loaded.sort(key=lambda e: float(e.get("timestamp") or 0))
            loaded = loaded[-HISTORY_MAX:]
            # Старый одиночный файл больше не пишем: события из него переезжают
            # в дневные шарды вместе с часовыми свёртками, файл убираем.
            moved = await asyncio.to_thread(HIST.import_legacy, seen)
            if moved:
                log.info("История: перенесено в дневные файлы — %d событий", moved)
            await asyncio.to_thread(HIST.cleanup)
            for ev in loaded:
                LIQUIDATIONS.append(ev)
                BOARD.add_liq(ev)
            HISTORY_RESTORE.update({"events": len(loaded), "error": "",
                                    "at": time.time()})
            if loaded:
                log.info("История ликвидаций восстановлена с диска: %d событий", len(loaded))
            else:
                log.warning("История ликвидаций на диске пуста: лента и дневной "
                            "дайджест будут считать только новые события")
        except Exception as e:
            HISTORY_RESTORE.update({"events": 0, "error": str(e)[:200],
                                    "at": time.time()})
            log.warning("Не удалось загрузить историю с диска: %s", e)

    # Хранить историю надо за месяц — и OI-снимки, и часовые ячейки стенда
    # (стенд кормит посты и дайджест, ему нужен весь день, а не 12 часов).
    OI.keep = OI_KEEP_MIN * 60
    BOARD.keep_hours = max(BOARD.keep_hours, int(HISTORY_TTL_HOURS))

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
        asyncio.create_task(liq_event_worker(), name="liq-worker"),
        asyncio.create_task(oi_history_task(), name="oi-history"),
        asyncio.create_task(liquidation_broadcaster(), name="liq-broadcast"),
        asyncio.create_task(flow_broadcaster(), name="flow-broadcast"),
        asyncio.create_task(flow_oi_task(), name="flow-oi"),
        asyncio.create_task(history_task(), name="history"),
        asyncio.create_task(pump_loop(), name="pumps"),
        asyncio.create_task(price_broadcaster(), name="price-broadcast"),
        asyncio.create_task(stats_broadcaster(), name="stats-broadcast"),
        asyncio.create_task(kline_refresher(), name="kline-refresh"),
        asyncio.create_task(hot_symbols_watcher(), name="hot-symbols"),
        asyncio.create_task(alert_loop(), name="alerts"),
        asyncio.create_task(corr_alert_loop(), name="corr-alerts"),
    ]
    # Дневной дайджест: вечерний выпуск в оба канала и в архив на сайте
    digest_sched = DigestScheduler(hour=DIGEST_HOUR, minute=DIGEST_MINUTE,
                                   jitter_min=DIGEST_JITTER_MIN,
                                   enabled=DIGEST_SCHED)
    app.state.digest_scheduler = digest_sched
    tasks.append(asyncio.create_task(api_digest.scheduler_loop(digest_sched),
                                     name="digest"))
    # 📣 Реклама: отправка по выбранному времени и автоудаление по сроку
    tasks.append(asyncio.create_task(ads_mod.scheduler_loop(ad_service),
                                     name="ads"))
    if DEMO_MODE:
        tasks.append(asyncio.create_task(demo_generator(), name="demo"))
        tasks.append(asyncio.create_task(demo_price_walk(), name="demo-prices"))
        tasks.append(asyncio.create_task(demo_tick_walk(), name="demo-ticks"))
        tasks.append(asyncio.create_task(demo_flow_walk(), name="demo-flow"))
        # сторож монет в демо: минутная история всех монет и редкие всплески
        demo_pump_seed()
        tasks.append(asyncio.create_task(demo_pump_walk(), name="demo-pumps"))

    # прогреваем свечи популярных монет, чтобы первый клиент увидел график сразу
    async def warmup():
        for sym in (feed.symbols[:3] if feed else []):
            try:
                await get_candles(sym, 5, force=True)
            except Exception:
                pass
    tasks.append(asyncio.create_task(warmup(), name="warmup"))

    tg_bot.health_fn = health_summary
    tg_bot.stats_fn = compute_stats
    # Лента бота: как можно больше событий — текст сам упрётся в лимит Telegram
    tg_bot.liqs_fn = lambda: list(LIQUIDATIONS)[-400:]
    tg_bot.ws_clients_fn = lambda: len(hub.clients)
    tg_bot.digest_fn = build_channel_digest
    tg_bot.alerts_market_fn = alerts_market_snapshot
    tg_bot.correlations_fn = correlations_snapshot
    tg_bot.pump_snapshot_fn = pump_snapshot
    tg_bot.public_url = PUBLIC_URL
    await tg_bot.start()

    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        try:
            await asyncio.to_thread(HIST.flush, True)
        except Exception:  # noqa: BLE001
            pass
        await tg_bot.stop()
        await feed.stop()


app = FastAPI(title="LiqScope — Live Crypto Liquidation Terminal",
              version="4.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

account_ctx.store = account_store
account_ctx.bot = tg_bot
account_ctx.public_url = PUBLIC_URL
account_ctx.secret = SECRET
# свои хосты для «откуда пришёл»: переход внутри сайта — не источник
account_ctx.site_hosts = tuple(x for x in (PUBLIC_URL, seo_pages.SITE_URL) if x)
account_ctx.cookie_secure = os.getenv("LIQSCOPE_COOKIE_SECURE", "").strip() in ("1", "true", "yes")
account_ctx.dev_login = os.getenv("LIQSCOPE_DEV_LOGIN", "").strip() in ("1", "true", "yes")
account_ctx.mailer = mailer
# Бот подтверждает почту теми же письмами, что и сайт
tg_bot.mailer = mailer
account_ctx.require_email_verification = REQUIRE_EMAIL_VERIFICATION
account_ctx.health_fn = health_summary
account_ctx.stats_fn = compute_stats
account_ctx.liqs_fn = lambda: list(LIQUIDATIONS)[-8:]
account_ctx.ws_clients_fn = lambda: len(hub.clients)
account_ctx.alerts_market_fn = alerts_market_snapshot
account_ctx.correlations_fn = correlations_snapshot
account_ctx.pump_snapshot_fn = pump_snapshot
account_ctx.symbols_fn = lambda: list((feed.symbols if feed else [])[:40])
register_account_routes(app)

# Настройки вечернего выпуска: значения из окружения замораживаем, остальные
# (час, минуты, разброс, авто-публикация) админ сайта может менять на лету.
DIGEST_ENV = {
    "hour": ("LIQSCOPE_DIGEST_HOUR", DIGEST_HOUR),
    "minute": ("LIQSCOPE_DIGEST_MIN", DIGEST_MINUTE),
    "jitter_min": ("LIQSCOPE_DIGEST_JITTER_MIN", DIGEST_JITTER_MIN),
}
digest_ctx.env_locked = {k: name for k, (name, _v) in DIGEST_ENV.items()
                         if os.getenv(name) not in (None, "")}
if os.getenv("LIQSCOPE_DIGEST_SCHED") not in (None, ""):
    digest_ctx.env_locked["enabled"] = "LIQSCOPE_DIGEST_SCHED"


def digest_settings() -> dict:
    """Настройки планировщика: где молчит окружение — берём из базы (сайт)."""
    out: dict = {}
    for key, (_env_name, default) in DIGEST_ENV.items():
        if key in digest_ctx.env_locked:
            out[key] = default
            continue
        raw = account_store.get_setting(f"digest_{key}", "")
        try:
            out[key] = int(float(raw)) if str(raw).strip() != "" else default
        except (TypeError, ValueError):
            out[key] = default
    if "enabled" in digest_ctx.env_locked:
        out["enabled"] = DIGEST_SCHED
    else:
        raw = str(account_store.get_setting("digest_enabled", "") or "").strip().lower()
        out["enabled"] = DIGEST_SCHED if not raw else raw not in ("0", "false", "no", "off")
    return out


digest_ctx.settings_fn = digest_settings
digest_ctx.set_setting_fn = (
    lambda key, val, actor=None:
    account_store.set_setting(str(key), str(val), actor_id=actor))

# Дневной дайджест: архив выпусков, данные с сервера и публикация через бота
digest_ctx.store = DigestStore(DIGEST_FILE)
digest_ctx.liqs_fn = lambda: list(LIQUIDATIONS)
digest_ctx.symbols_fn = lambda: list(feed.symbols if feed else [])
digest_ctx.candles_fn = get_candles
digest_ctx.oi_fn = oi_payload
digest_ctx.ai_fn = digest_ai
digest_ctx.publish_fn = tg_bot.publish_daily_digest
digest_ctx.public_url = PUBLIC_URL
# Обложку выпуска выбираем при сборке дайджеста: то же фото уходит в канал и
# показывается на странице /digest (фото рубрик живут в базе аккаунтов).
digest_ctx.photo_store = account_store
register_digest_routes(app)
# Кнопка «🗞 Дайджест за сутки» в админке бота собирает выпуск прямо сейчас
tg_bot.daily_run_fn = api_digest.publish_digest

# Сводки по часам: посты канала живут ещё и на сайте. Архив наполняет бот
# (каждая удачная публикация), а эта функция собирает сводку прямо сейчас —
# её зовёт админская кнопка, чтобы раздел можно было наполнить не дожидаясь
# поста в канал.
async def collect_hourly_post() -> dict:
    """Собрать сводку (RU и EN) и положить её в архив раздела «Сводки по часам»."""
    from channel_digest import active_headlines, cover_info, pick_active_image, render_post
    snap = await build_channel_digest()
    if not isinstance(snap, dict) or not snap:
        return {}
    try:
        n = int(account_store.get_setting("channel_digest_n") or 0)
    except (TypeError, ValueError):
        n = 0
    try:
        hours = int(snap.get("window_h") or POST_INTERVAL_H)
    except (TypeError, ValueError):
        hours = POST_INTERVAL_H
    texts: Dict[str, str] = {}
    for lang in ("ru", "en"):
        head = ""
        try:
            head, _note = await tg_bot._ai_headline(snap, variant=n, lang=lang)
        except Exception as e:                        # noqa: BLE001
            log.debug("сводки по часам: ИИ-шапка (%s) не ответила: %s", lang, e)
        # На сайт идёт полный текст: ИИ-шапка без обрезки под подпись
        # Telegram (_ai_head_full хранит её целиком после _ai_headline).
        key = "en" if str(lang).startswith("en") else "ru"
        full_head = (getattr(tg_bot, "_ai_head_full", {}) or {}).get(key) or head
        texts[lang] = render_post(
            snap, n,
            headlines=active_headlines(account_store, hours, lang=lang),
            head_override=full_head or None,
            site_url=PUBLIC_URL,
            bot_url=tg_bot.bot_url(),
            lang=lang,
            limit=10 ** 9,
            head_full=True,
        )
    img = pick_active_image(account_store, "post", variant=n)
    now = time.time()
    rec = {
        "id": hourly_id(now),
        "ts": now,
        "window_h": hours,
        "interval_h": POST_INTERVAL_H,
        "total_usd": snap.get("total_usd"),
        "liq_count": snap.get("count"),
        "longs_usd": snap.get("longs_usd"),
        "shorts_usd": snap.get("shorts_usd"),
        "texts": {k: v for k, v in texts.items() if v},
        "sent": {},
        "n": n,
        "manual": True,
    }
    if img and os.path.isfile(img):
        rec["photo"] = cover_info(img, account_store)
    return hourly_ctx.store.add(rec)


hourly_ctx.store = HourlyStore(HOURLY_FILE, keep=HOURLY_KEEP)
hourly_ctx.collect_fn = collect_hourly_post
hourly_ctx.public_url = PUBLIC_URL
# Посты раздела выходят в английском канале — на странице ссылка на него
hourly_ctx.channel_url_fn = tg_bot.channel_url_en
register_hourly_routes(app)
# Бот складывает в этот же архив каждый пост, который реально ушёл в канал
tg_bot.hourly_store = hourly_ctx.store

# Админка бота на сайте: каналы, публикация постов, контроль, здоровье бирж
web_bot_admin.ctx.bot = tg_bot
web_bot_admin.ctx.store = account_store
web_bot_admin.ctx.health_fn = health_summary
web_bot_admin.ctx.ws_clients_fn = lambda: len(hub.clients)
web_bot_admin.ctx.public_url = PUBLIC_URL
register_bot_admin_routes(app)

# 📣 Рекламные посты: панель в админке, рассылка ботом/в каналы и баннер на главной
ads_mod.ctx.store = account_store
ads_mod.ctx.bot = tg_bot
ads_mod.ctx.public_url = PUBLIC_URL
ads_mod.ctx.site_url = PUBLIC_URL
ad_service = AdService(store=account_store, bot=tg_bot, site_url=PUBLIC_URL)
app.state.ad_service = ad_service
register_ad_routes(app, ad_service)

# 💬 Обратная связь: «по всем вопросам» из кабинета, ответы из админки
feedback_mod.ctx.store = account_store
feedback_mod.ctx.bot = tg_bot
feedback_mod.ctx.public_url = PUBLIC_URL
register_feedback_routes(app)

# 🌍 География посетителей: страна по IP (заголовок CDN → кэш → внешний
# сервис), источник перехода и «сколько уже на сайте». Пишет middleware
# визитов в web_account, а «я ещё здесь» присылает presence.js.
geoip.ctx.store = account_store
geoip.ctx.secret = SECRET
web_geo.ctx.store = account_store
web_geo.ctx.secret = SECRET
web_geo.ctx.public_url = PUBLIC_URL
web_geo.register_geo_routes(app)

# ☰ Слои графика: гостю без регистрации — 30 минут пробного доступа,
# дальше сайт предлагает зарегистрироваться (web_layers).
web_layers.ctx.store = account_store
web_layers.ctx.secret = SECRET
web_layers.ctx.public_url = PUBLIC_URL
web_layers.register_layer_routes(app)

# 💬 Мини-чат терминала: 3 дня истории, пишет любой зарегистрированный
terminal_chat_mod.ctx.store = account_store
terminal_chat_mod.ctx.secret = SECRET
register_terminal_chat_routes(app, hub=hub)

# 💬 Комментарии к дайджесту и сводке по часам: читают все, пишут зарегистрированные
content_comments_mod.ctx.store = account_store
content_comments_mod.ctx.secret = SECRET
register_content_comment_routes(app)


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
    tf = parse_tf(timeframe) or 5
    entry = await get_candles(symbol, tf)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "source": entry["source"],
        "candles": entry["candles"],
    }


@app.get("/api/liq_clusters")
async def api_liq_clusters(symbol: str = Query("BTC_USDT"),
                           timeframe: int = Query(5),
                           min_usd: float = Query(0.0),
                           exchanges: str = Query("", max_length=400)):
    """Кластеры ликвидаций по свечам за всю сохранённую историю.

    Свечи графика — это 300 свечей выбранного ТФ; история при этом хранится
    месяцем и пишется всегда, даже когда терминал закрыт. Эндпоинт отдаёт
    готовые плашки по каждой свече (шесть уровней цены внутри свечи), поэтому
    кластеры видны за любые часы и дни, а не только за те, что успели прийти
    в открытое окно. Порог ``min_usd`` и список ``exchanges`` — те же фильтры,
    что у ленты: иначе выключенная биржа осталась бы в плашках за прошлые часы.
    """
    symbol = canon(symbol)
    tf = parse_tf(timeframe) or 5
    entry = await get_candles(symbol, tf)
    only = [x.strip() for x in str(exchanges or "").split(",") if x.strip()]
    try:
        rows, cut = await asyncio.to_thread(_liq_cluster_map, entry["candles"],
                                            tf, symbol, float(min_usd or 0.0),
                                            only or None)
    except Exception as e:              # noqa: BLE001 — график важнее кластеров
        log.warning("кластеры истории %s: %s", symbol, e)
        rows, cut = {}, 0.0
    return {"symbol": symbol, "timeframe": tf, "cut": cut,
            "ttl_hours": HISTORY_TTL_HOURS, "candles": rows}


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
        # демо: ряд уровней ведёт себя как настоящий — цифра и график живут
        out = _demo_oi_payload_from_series(symbol)
    return out


# Демо-ряд OI: один на процесс и по монете. Раньше демо-OI был случайной
# картинкой на каждый запрос, а в «Алертах по объёму» его вообще не было —
# трекер бирж в демо-режиме пуст, поэтому OI показывал ноль, гребёнка стояла
# пустой, и лента метрики выглядела мёртвой. Ряд тикает сам: уровни копятся
# шагом DEMO_OI_STEP_SEC, изменения окон считаются по нему же.
DEMO_OI_STEP_SEC = 300
DEMO_OI_POINTS = 288                    # сутки шагом 5 минут
DEMO_OI_LIVE_SEC = 15                   # живой опрос демо-ряда (как у трекера)
DEMO_OI_LIVE_POINTS = 40                # столько живых точек держим для графика
_DEMO_OI_LOCK = threading.Lock()
_DEMO_OI_SERIES: Dict[str, Dict[int, float]] = {}
_DEMO_OI_LIVE: Dict[str, Dict[int, float]] = {}


def _demo_oi_level(prev: float) -> float:
    return max(1e6, float(prev) * (1.0 + random.gauss(0, 0.0015)))


def _demo_oi_series(symbol: str) -> Dict[int, float]:
    """Непрерывный демо-ряд уровней OI по монете (обновляется на месте)."""
    now = time.time()
    bucket = int(now // DEMO_OI_STEP_SEC) * DEMO_OI_STEP_SEC
    with _DEMO_OI_LOCK:
        series = _DEMO_OI_SERIES.get(symbol)
        if not series:
            series = {}
            level = 4e8 * (1 - random.uniform(0, 0.02))
            start = bucket - (DEMO_OI_POINTS - 1) * DEMO_OI_STEP_SEC
            for i in range(DEMO_OI_POINTS):
                level = _demo_oi_level(level)
                series[start + i * DEMO_OI_STEP_SEC] = round(level, 2)
            _DEMO_OI_SERIES[symbol] = series
        last = max(series)
        while last < bucket:
            last += DEMO_OI_STEP_SEC
            series[last] = round(_demo_oi_level(series[last - DEMO_OI_STEP_SEC]), 2)
        if len(series) > DEMO_OI_POINTS:
            for old_key in sorted(series)[:len(series) - DEMO_OI_POINTS]:
                series.pop(old_key, None)
        # Живой ряд: как кольцо опросов настоящего трекера — тик раз в
        # DEMO_OI_LIVE_SEC, а 5-минутный бакет догоняет последний уровень.
        live = _DEMO_OI_LIVE.setdefault(symbol, {})
        tick = int(now // DEMO_OI_LIVE_SEC) * DEMO_OI_LIVE_SEC
        if not live:
            live[tick] = round(max(1e6, series[max(series)]), 2)
        while max(live) < tick:
            prev = live[max(live)]
            live[max(live) + DEMO_OI_LIVE_SEC] = round(
                max(1e6, prev * (1.0 + random.gauss(0, 0.0004))), 2)
        for old_key in sorted(live)[:max(0, len(live) - DEMO_OI_LIVE_POINTS)]:
            live.pop(old_key, None)
        series[bucket] = live[max(live)]        # бакет догоняет живой уровень
        return dict(series)


def _demo_oi_payload_from_series(symbol: str) -> dict:
    """Демо-ответ OI: тотал и окна — по тому же ряду, что рисует график."""
    from oi_feed import OI_WINDOWS
    series = _demo_oi_series(symbol)
    keys = sorted(series)
    total = float(series[keys[-1]])
    legs = ["binance", "bybit", "okx", "gate", "bitget", "htx"]
    weights = [0.35, 0.22, 0.14, 0.12, 0.10, 0.07]
    per = {e: round(total * w, 2) for e, w in zip(legs, weights)}
    now = time.time()
    changes: Dict[str, Optional[dict]] = {}
    for name, window in OI_WINDOWS:
        base_key = None
        for t in keys:
            if t <= now - window:
                base_key = t
            else:
                break
        base = float(series[base_key]) if base_key is not None else None
        usd = round(total - base, 2) if base else None
        changes[name] = ({"usd": usd, "pct": round(usd / base * 100, 3)}
                         if base else None)
    # m1 — по живому ряду (как у трекера по кольцу опросов), иначе окно
    # «минута» показывало то же, что 5-минутный бакет
    live = dict(_DEMO_OI_LIVE.get(symbol) or {})
    lkeys = sorted(live)
    if len(lkeys) >= 2:
        cur_ts = lkeys[-1]
        ref = min(lkeys[:-1], key=lambda t: abs(t - (cur_ts - 60)))
        base_l = float(live[ref])
        if base_l > 0:
            usd_l = round(float(live[cur_ts]) - base_l, 2)
            changes["m1"] = {"usd": usd_l, "pct": round(usd_l / base_l * 100, 3)}
    return {"symbol": symbol, "total_usd": round(total, 2),
            "per_exchange": per, "live_exchanges": legs,
            "hist_exchanges": ["binance", "bybit", "gate"],
            "changes": changes, "partial": {k: False for k, _ in OI_WINDOWS},
            "ts": now, "stale_sec": 0.0, "_series": series, "_live": live}


def _demo_oi_payload(symbol: str) -> dict:
    total = 4e8 + random.uniform(-2e7, 2e7)
    legs = ["binance", "bybit", "okx", "gate", "bitget", "htx"]
    weights = [0.35, 0.22, 0.14, 0.12, 0.10, 0.07]
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


def _fnum(v, default: float = 0.0) -> float:
    """Число из чего угодно: история может отдать None, строку или NaN."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if n == n else default


def aggregate_hour_cell(h: float, cell: dict, symbol: Optional[str] = None) -> dict:
    """Часовой итог по одной монете или по всему рынку (для /api/history)."""
    syms = cell.get("sym") or {}
    if symbol:
        val = syms.get(symbol) or {}
        return {
            "h": int(h), "usd": round(_fnum(val.get("usd")), 2),
            "count": int(_fnum(val.get("n"))),
            "long_usd": round(_fnum(val.get("long")), 2),
            "short_usd": round(_fnum(val.get("short")), 2),
            "cvd": round(_fnum(val.get("cvd")), 2),
            "vol": round(_fnum(val.get("vol")), 2),
            "by_symbol": {}, "by_exchange": {},
        }
    return {
        "h": int(h), "usd": round(_fnum(cell.get("liq_usd")), 2),
        "count": int(_fnum(cell.get("liq_count"))),
        "long_usd": round(_fnum(cell.get("liq_long")), 2),
        "short_usd": round(_fnum(cell.get("liq_short")), 2),
        "cvd": round(_fnum(cell.get("cvd")), 2),
        "vol": round(_fnum(cell.get("vol")), 2),
        "max_symbol": cell.get("max_symbol") or "",
        "by_symbol": cell.get("sym") or {},
        "by_exchange": cell.get("exch") or {},
    }


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


@app.get("/api/history")
async def api_history(since: Optional[float] = None, until: Optional[float] = None,
                      hours: float = 0.0, symbol: Optional[str] = None,
                      min_usd: float = 0.0, limit: int = 2000,
                      bucket: str = "raw", step_hours: int = 1):
    """История рынка за месяц: сырые ликвидации или часовые/дневные свёртки.

    bucket=raw    — события (для списков и модалок);
    bucket=hour   — часовые итоги (ликвидации, CVD, объём, биржи, монеты);
    bucket=day    — то же по суткам (месячные графики);
    bucket=series — ряды по часам для графиков на сайте.
    """
    now = time.time()
    until = float(until) if until else now
    if since:
        start = float(since)
    elif hours:
        start = until - float(hours) * 3600
    else:
        start = until - 24 * 3600
    start = max(start, until - HISTORY_TTL_HOURS * 3600)
    sym = canon(symbol) if symbol and symbol != "ALL" else None
    bucket = (bucket or "raw").strip().lower()
    if bucket == "hour":
        cells = await asyncio.to_thread(HIST.hours_range, start, until)
        rows = []
        for h, cell in cells:
            agg = aggregate_hour_cell(h, cell, sym)
            if agg["count"] or agg["vol"] or agg["cvd"]:
                rows.append(agg)
        return {"bucket": "hour", "since": start, "until": until,
                "ttl_hours": HISTORY_TTL_HOURS, "hours": rows}
    if bucket == "day":
        rows = await asyncio.to_thread(HIST.days, start, until, sym)
        return {"bucket": "day", "since": start, "until": until,
                "ttl_hours": HISTORY_TTL_HOURS, "days": rows}
    if bucket == "series":
        data = await asyncio.to_thread(HIST.series, start, until, None,
                                       int(step_hours or 1))
        return {"bucket": "series", "since": start, "until": until,
                "ttl_hours": HISTORY_TTL_HOURS, **data}
    rows = await asyncio.to_thread(HIST.query, start, until, sym, float(min_usd),
                                   int(limit))
    return {"bucket": "raw", "since": start, "until": until,
            "ttl_hours": HISTORY_TTL_HOURS, "total": len(rows),
            "liquidations": rows}


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
        "tg": tg_bot.poll_status(),
        "config": {
            "symbols_limit": SYMBOLS_LIMIT,
            "exchanges": EXCHANGES,
            "tick_sources": TICK_SOURCES,
            "history_max": HISTORY_MAX,
            "history_persist": bool(HISTORY_FILE),
            "history_ttl_hours": HISTORY_TTL_HOURS,
            "history": HIST.stats(),
            "history_restore": dict(HISTORY_RESTORE),
            "oi_history_keep_min": OI_KEEP_MIN,
        },
    }
    data.update(feed.health() if feed else {"sources": {}})
    _srcs = data.get("sources") or {}
    # oxa — это транспорт для ликвидаций Hyperliquid, а не отдельная биржа:
    # её события приходят с биржей hyperliquid, поэтому в счётчик бирж она не
    # идёт (иначе на лендинге было бы «11 бирж» при десяти площадках)
    _not_venue = ("prices", "ticks", "oxa")
    data["live_exchanges"] = sorted(
        name for name, s in _srcs.items()
        if name not in _not_venue and isinstance(s, dict) and s.get("connected")
    )
    data["exchanges_total"] = (
        sum(1 for name in _srcs if name not in _not_venue) or len(EXCHANGES)
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
            # Лента «ВСЕ» в CVD/OI строится не по свечам графика, а по
            # минутным потокам всех монет — отдаём их сразу, чтобы не ждать
            # первой рассылки.
            "flow": flow_snapshot(),
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
                parsed_tf = parse_tf(raw.get("tf"))
                if parsed_tf is not None:
                    client.tf = parsed_tf
                if raw.get("min_usd") is not None:
                    try:
                        client.min_usd = float(raw["min_usd"])
                    except (TypeError, ValueError):
                        pass
                if raw.get("exchange"):
                    client.exchange = str(raw["exchange"])
                feed_tab = str(raw.get("feed") or "").strip().lower()
                if feed_tab in ("liq", "cvd", "oi"):
                    client.feed = feed_tab
                sync_hot_symbols()
                entry = await get_candles(client.chart_symbol, client.tf)
                await client.send({
                    "type": "candles",
                    "symbol": client.chart_symbol,
                    "tf": client.tf,
                    "source": entry["source"],
                    "candles": entry["candles"],
                })
            elif action == "feed":
                # переключение ленты в терминале: «ВСЕ» + CVD/OI → клиент ждёт
                # поток по всем монетам, поэтому сразу отдаём срез, не дожидаясь
                # следующего тика рассылки
                feed_tab = str(raw.get("feed") or "").strip().lower()
                if feed_tab in ("liq", "cvd", "oi"):
                    client.feed = feed_tab
                sync_hot_symbols()
                if client.wants_flow:
                    await client.send(flow_snapshot())
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
async def root(request: Request):
    """Лендинг: красивый вход в терминал.

    Язык подставляем сразу в head (``seo_pages.render``) — поисковик должен
    видеть язык в ``<html lang>``, заголовке и описании, а не только после
    выполнения скриптов.
    """
    lang, auto = seo_pages.lang_of(request)
    return seo_pages.render(
        "landing.html", lang, "/", extra_head=seo_pages.jsonld("landing", lang),
        auto=auto,
    )


@app.get("/terminal")
async def terminal(request: Request):
    """Сам терминал (страница приложения)."""
    lang, auto = seo_pages.lang_of(request)
    return seo_pages.render(
        "index.html", lang, "/terminal", extra_head=seo_pages.jsonld("terminal", lang),
        auto=auto,
    )


@app.get("/robots.txt")
async def robots_txt():
    """Правила обхода: служебное закрыто, карта сайта указана."""
    return seo_pages.robots_txt()


@app.get("/sitemap.xml")
async def sitemap_xml():
    """Карта сайта: основные страницы во всех языках (hreflang-альтернативы) + архив."""
    digest_items = []
    hourly_items = []
    try:
        # digest_ctx и hourly_ctx живут в модулях api_digest / api_hourly —
        # берём их напрямую, чтобы не тянуть app.state
        from api_digest import ctx as dctx
        from api_hourly import ctx as hctx
        try:
            digest_items = list((getattr(dctx, "store", None) or {}).list() if hasattr(getattr(dctx, "store", None), "list") else [])
        except Exception:
            digest_items = []
        try:
            hourly_items = list((getattr(hctx, "store", None) or {}).list() if hasattr(getattr(hctx, "store", None), "list") else [])
        except Exception:
            hourly_items = []
    except Exception:
        pass
    return seo_pages.sitemap_xml(digest_items=digest_items, hourly_items=hourly_items)


@app.get("/manifest.webmanifest")
async def manifest_webmanifest(request: Request):
    """Манифест приложения: имя, иконка, цвета — на языке гостя."""
    lang = seo_pages.detect_lang(request)
    return seo_pages.manifest(lang=lang)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """404 — страница не найдена: отдаём красивую страницу на языке гостя, noindex."""
    try:
        lang, auto = seo_pages.lang_of(request)
    except Exception:
        lang, auto = seo_pages.DEFAULT_LANG, False
    # Для 404 — noindex, но hreflang и язык всё равно нужны
    return seo_pages.render(
        "404.html", lang, "/404",
        extra_head=seo_pages.jsonld("404", lang),
        auto=auto,
        status_code=404,
    )


@app.get("/favicon.ico")
async def favicon():
    """Фавиконка в корне сайта: её спрашивают браузер и роботы поисковиков.

    Раньше этот адрес отдавал 404 — в выдаче вместо логотипа был пустой
    значок, хотя ``<link rel="icon">`` на страницах стоял. Файл собирается
    из ``static/logo.png`` скриптом ``tools/build_icons.py``.
    """
    path = os.path.join(STATIC_DIR, "favicon.ico")
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "error": "no_favicon"}, status_code=404)
    return FileResponse(path, media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
