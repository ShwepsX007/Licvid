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
import atexit
import gc
import inspect
import logging
import logging.handlers
import math
import os
import queue
import random
try:
    import resource  # Unix: RSS процесса для /api/metrics
except ImportError:  # Windows: модуля нет — метрика отдаст 0
    resource = None  # type: ignore
import secrets
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Deque, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# Быстрый JSON. orjson сериализует ответы в 3-6 раз быстрее стандартного
# json.dumps, а на одном воркере uvicorn сериализация — это заметная доля CPU
# (свечи, статистика, снимки ленты уходят сотням клиентов). Пакет не
# обязателен: без него приложение работает на прежнем JSONResponse.
try:                                   # pragma: no cover — зависит от окружения
    import orjson as _orjson
    from fastapi.responses import ORJSONResponse as _DEFAULT_RESPONSE_CLASS
    FAST_JSON = True
except ImportError:                    # pragma: no cover
    _orjson = None
    _DEFAULT_RESPONSE_CLASS = JSONResponse
    FAST_JSON = False


def json_dumps_text(obj) -> str:
    """Компактная JSON-строка: WS-кадры клиентам (самая горячая сериализация)."""
    if _orjson is None:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return _orjson.dumps(obj).decode("utf-8")


# Явные `return JSONResponse(...)` в этом модуле (/api/health, /api/metrics,
# отказы /api/symbols/add) должны отдавать тем же быстрым сериализатором, что
# и остальное приложение — поэтому имя указывает на выбранный класс. Без
# orjson это прежний starlette JSONResponse, поведение не меняется.
JSONResponse = _DEFAULT_RESPONSE_CLASS

import archive_hide
import archive_restore
import seo_pages
from fastapi.staticfiles import StaticFiles

import market_feed
from market_feed import GATE_REST, MarketFeed, TF_MINUTES, base_of, canon, _get_json
from timeframes import parse_tf
from book_feed import (chat_text as book_chat_text, chat_meta as book_chat_meta)
from book_feed import (BookFeed, format_wall_html, normalize_book_cfg,
                       register_book_routes)
from oi_feed import map_candles_to_oi
from hour_board import BOARD, OI, SLOTS, build_snapshot, HOUR, SLOT_SEC
from accounts import COOKIE_SID, Store
from flow_feed import FlowFeed
from volume_profile import VolumeProfile
from risk_limit import RiskLimits
from side_feed import SideFeed
from liq_levels import LevelsEngine
from history import HistoryStore, MONTH_HOURS
from pump_scan import PumpScanner, filter_new as pump_filter_new
from pump_scan import format_signal_html as pump_signal_html
from pump_scan import chat_text as pump_chat_text, chat_meta as pump_chat_meta
from refs import gate_url
from mailer import build_mailer
import ai_text
from ai_text import build_ai, prompt_setting
import api_digest
import api_articles
from api_digest import DigestScheduler, ctx as digest_ctx, register_digest_routes
from api_hourly import ctx as hourly_ctx, register_hourly_routes
from api_articles import ctx as articles_ctx, register_article_routes
from hourly_posts import PostStore as HourlyStore, post_id as hourly_id
from articles import ArticleStore
from daily_digest import DigestStore
from tg_bot import TelegramBot, normalize_public_url
from web_account import (ctx as account_ctx, register_account_routes,
                         current_user, RateLimiter)
import web_bot_admin
from web_bot_admin import register_bot_admin_routes
import ads as ads_mod
from ads import AdService, register_ad_routes
import feedback as feedback_mod
from feedback import register_feedback_routes
import geoip
import web_geo
import web_layers
from web_liq_levels import register_liq_level_routes
import terminal_chat as terminal_chat_mod
from terminal_chat import register_chat_routes as register_terminal_chat_routes
import web_cache
import private_chat as private_chat_mod
from private_chat import register_private_chat_routes
import support_chat as support_chat_mod
from support_chat import register_support_chat_routes
import service_chat as service_chat_mod
from service_chat import register_service_chat_routes
import content_comments as content_comments_mod
from content_comments import register_comment_routes as register_content_comment_routes

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("liqscope.server")


# --- логирование вне event loop -------------------------------------------
# Замер на бою 29.09.2026 (50 зрителей + 100 rps, мимо nginx) назвал паузу
# 1282 мс со стеком logging/__init__.py:1103:emit <- ... <- 1477:info <-
# websockets_sansio_impl.py:440:send <- websockets.py:110:accept <-
# server.py:ws_endpoint: uvicorn пишет в журнал КАЖДОЕ принятое WS-соединение и
# каждый HTTP-запрос (access log), а журнал — это stdout, который под systemd
# уходит в journald. Когда journald не успевает, буфер трубы заполняется и
# write() блокирует единственный воркер. Поэтому пишем в очередь, а держит её
# отдельный поток: переполнение роняет запись (счётчик виден в /api/health),
# но не цикл.
LOG_ASYNC = os.getenv("LIQSCOPE_LOG_ASYNC", "1").strip() not in ("", "0", "false", "no")
LOG_QUEUE_MAX = max(100, int(os.getenv("LIQSCOPE_LOG_QUEUE", "20000") or 20000))
_LOG_QUEUE: "queue.Queue" = queue.Queue(maxsize=LOG_QUEUE_MAX)
_LOG_STATS: Dict[str, float] = {"dropped": 0.0, "queued": 0.0}
_log_listener: Optional[logging.handlers.QueueListener] = None
# uvicorn вешает собственные хендлеры на свои логгеры с propagate=False, поэтому
# одного корневого мало — обходим их явно
_LOG_WRAPPED = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "websockets")


class _DropQueueHandler(logging.handlers.QueueHandler):
    """Очередь вместо журнала: переполнение теряет запись, а не воркер."""

    def enqueue(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.queue.put_nowait(record)
            _LOG_STATS["queued"] += 1
        except Exception:                        # noqa: BLE001 — очередь полна
            _LOG_STATS["dropped"] += 1


def install_async_logging() -> bool:
    """Перевести хендлеры логов в фоновый поток. Идемпотентно."""
    global _log_listener
    if not LOG_ASYNC or _log_listener is not None:
        return _log_listener is not None
    originals: List[logging.Handler] = []
    seen: Set[int] = set()
    for name in _LOG_WRAPPED:
        lg = logging.getLogger(name)
        own = [h for h in lg.handlers if not isinstance(h, _DropQueueHandler)]
        for h in own:
            if id(h) not in seen:
                seen.add(id(h))
                originals.append(h)
            lg.removeHandler(h)
        if own or not lg.propagate:
            lg.addHandler(_DropQueueHandler(_LOG_QUEUE))
    if not originals:                            # переносить нечего
        return False
    try:
        _log_listener = logging.handlers.QueueListener(
            _LOG_QUEUE, *originals, respect_handler_level=True)
        _log_listener.start()
    except Exception as e:                       # noqa: BLE001
        _log_listener = None
        for h in originals:                      # вернуть как было
            logging.getLogger("").addHandler(h)
        log.warning("асинхронное логирование не поднялось: %s", e)
        return False
    atexit.register(stop_async_logging)
    return True


def stop_async_logging() -> None:
    """Допisać очередь в журнал перед выходом (иначе хвост лога потеряется)."""
    global _log_listener
    if _log_listener is None:
        return
    try:
        _log_listener.stop()
    except Exception:                            # noqa: BLE001
        pass
    _log_listener = None


# --- сборка мусора: паузы на большой куче ---------------------------------
# P95 в секундах при 6 мс на стороне обработчика и стек gzip в окне паузы —
# это не сжатие: на куче в гигабайт каждая сборка второго поколения
# останавливает процесс на 1-2 с, а сэмплер называет кадр, который в этот
# момент аллоцировал память. Меряем сборку явно (gc.callbacks дёшевы: срабаты-
# вают только на самой сборке) и говорим вслух, если пауза цикла совпала с ней.
GC_LOG_MS = max(10.0, float(os.getenv("LIQSCOPE_GC_LOG_MS", "200") or 200))
GC_FREEZE = os.getenv("LIQSCOPE_GC_FREEZE", "1").strip() not in ("", "0", "false", "no")
_GC_STATS: Dict[str, float] = {"max_ms": 0.0, "last_ms": 0.0, "total_ms": 0.0,
                               "count": 0.0, "gen2": 0.0, "frozen": 0.0}
_GC_RECENT: Deque[Tuple[float, float, int]] = deque(maxlen=32)
_gc_started = 0.0


def _gc_callback(phase: str, info: Dict[str, object]) -> None:
    """Сколько воркер стоял на сборке мусора и какого поколения."""
    global _gc_started
    try:
        if phase == "start":
            _gc_started = time.monotonic()
            return
        if phase != "stop" or not _gc_started:
            return
        dt_ms = (time.monotonic() - _gc_started) * 1000.0
        _gc_started = 0.0
        gen = int(info.get("generation", -1))
        _GC_RECENT.append((time.monotonic(), dt_ms, gen))
        _GC_STATS["last_ms"] = round(dt_ms, 1)
        _GC_STATS["count"] += 1
        _GC_STATS["total_ms"] = round(_GC_STATS["total_ms"] + dt_ms, 1)
        if dt_ms > _GC_STATS["max_ms"]:
            _GC_STATS["max_ms"] = round(dt_ms, 1)
        if gen == 2:
            _GC_STATS["gen2"] += 1
        if dt_ms >= GC_LOG_MS:
            log.warning("[gc] сборка %d поколения остановила воркер на %.0f мс "
                        "(собрано %s объектов); всего таких пауз %.0f, "
                        "максимум %.0f мс — на большой куче это и есть p95 в "
                        "секундах при быстром обработчике",
                        gen, dt_ms, info.get("collected"),
                        _GC_STATS["count"], _GC_STATS["max_ms"])
    except Exception:                            # noqa: BLE001 — замер не должен ронять
        _gc_started = 0.0


def gc_during(since_mono: float, until_mono: float) -> str:
    """Была ли сборка мусора внутри окна паузы цикла (для строки сторожа)."""
    hits = [(ms, gen) for ts, ms, gen in _GC_RECENT
            if since_mono - 0.05 <= ts <= until_mono + 0.05]
    if not hits:
        return ""
    ms, gen = max(hits)
    return (f"во время паузы шла сборка мусора {gen} поколения "
            f"({ms:.0f} мс) — кадр в стеке просто аллоцировал память")


def freeze_gc_heap() -> None:
    """Убрать кучу старта из поколений: сборщики её больше не перебирают."""
    if not GC_FREEZE:
        return
    try:
        gc.collect()
        gc.freeze()   # возвращает None — число даёт get_freeze_count()
        n = gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else 0
        _GC_STATS["frozen"] = float(n or 0)
        log.info("[gc] куча старта заморожена: %d объектов вне поколений, "
                 "сборки будут перебирать только новые (LIQSCOPE_GC_FREEZE=0 "
                 "отключает)", n or 0)
    except Exception as e:                       # noqa: BLE001
        log.warning("[gc] заморозка кучи не удалась: %s", e)


try:
    gc.callbacks.append(_gc_callback)
except Exception:                                # noqa: BLE001
    pass
install_async_logging()

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

# Доверенные прокси для X-Forwarded-For. По умолчанию — только локальный nginx.
# Если запрос пришёл не от доверенного прокси, заголовки XFF игнорируются,
# чтобы rate limit нельзя было обойти подменой первого IP.
_TRUSTED_PROXIES_RAW = os.getenv("LIQSCOPE_TRUSTED_PROXIES", "127.0.0.1,::1").strip()
_TRUSTED_PROXY_SET = {x.strip() for x in _TRUSTED_PROXIES_RAW.replace(";", ",").split(",") if x.strip()}
_TRUSTED_PROXY_NETS = []
try:
    import ipaddress
    for item in list(_TRUSTED_PROXY_SET):
        if "/" in item:
            try:
                _TRUSTED_PROXY_NETS.append(ipaddress.ip_network(item, strict=False))
                _TRUSTED_PROXY_SET.discard(item)
            except ValueError:
                pass
except Exception:
    _TRUSTED_PROXY_NETS = []


def _is_trusted_proxy(ip: str) -> bool:
    ip = (ip or "").strip()
    if not ip:
        return False
    if ip in _TRUSTED_PROXY_SET:
        return True
    if not _TRUSTED_PROXY_NETS:
        return False
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _TRUSTED_PROXY_NETS)
    except Exception:
        return False
HISTORY_MAX = int(os.getenv("LIQSCOPE_HISTORY_MAX", "60000"))
PERF_LOG = os.getenv("LIQSCOPE_PERF_LOG", "").strip().lower() in ("1", "true", "yes")
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
# 📖 Стакан: куда класть шейрды событий стен ("" — только память) и порог стены.
BOOK_DIR = os.getenv("LIQSCOPE_BOOK_DIR", os.path.join(HERE, "data", "book_walls")).strip()
if BOOK_DIR.lower() in ("0", "none", "off", "false"):
    BOOK_DIR = ""
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
# Публично известная соль. На ней считались HMAC IP — полный перебор IPv4
# снимает «анонимность» статистики. Такое значение больше не принимаем.
_KNOWN_BAD_SECRETS = frozenset({
    "liqscope-change-me", "liqscope", "change-me", "changeme", "secret",
})


def _resolve_secret() -> str:
    """Секрет процесса: из окружения, иначе свой файл в data/, но не дефолт.

    LIQSCOPE_REQUIRE_SECRET=1 (стоит в deploy/licvid.service) — без переменной
    процесс не стартует. В разработке и тестах секрет генерируется один раз
    и лежит в data/secret, чтобы хеши IP не прыгали между рестартами.

    В PROD отсутствие секрета или дефолтный — fail-fast. В DEMO
    (LIQSCOPE_DEMO=1) можно работать с сгенерированным, но с предупреждением.
    """
    env = os.getenv("LIQSCOPE_SECRET", "").strip()
    require = os.getenv("LIQSCOPE_REQUIRE_SECRET", "").strip().lower() in (
        "1", "true", "yes")
    if env and env not in _KNOWN_BAD_SECRETS and len(env) >= 16:
        return env
    if env:
        log.warning("LIQSCOPE_SECRET — опубликованный дефолт или короче 16 "
                    "знаков, игнорируем")
    if require and not env:
        if DEMO_MODE:
            log.warning("LIQSCOPE_SECRET обязателен (LIQSCOPE_REQUIRE_SECRET=1), "
                        "но не задан — DEMO режим, использую авто-секрет. "
                        "На проде это должно падать.")
        else:
            log.error("LIQSCOPE_SECRET обязателен (LIQSCOPE_REQUIRE_SECRET=1) "
                      "и не задан. Сгенерируйте: openssl rand -hex 32 и "
                      "задайте в systemd drop-in.")
            raise SystemExit(2)
    path = os.getenv("LIQSCOPE_SECRET_FILE",
                     os.path.join(HERE, "data", "secret")).strip()
    try:
        if path and os.path.isfile(path):
            saved = open(path, encoding="utf-8").read().strip()
            if saved and saved not in _KNOWN_BAD_SECRETS and len(saved) >= 24:
                if require:
                    log.warning("LIQSCOPE_SECRET не задан, но есть %s — "
                                "использую его, хотя в проде нужен env", path)
                else:
                    log.warning("LIQSCOPE_SECRET не задан — берём сохранённый %s", path)
                return saved
            if saved:
                log.warning("Сохранённый секрет в %s — дефолт или короткий, игнорируем", path)
    except OSError:
        pass
    if not DEMO_MODE:
        log.error("LIQSCOPE_SECRET обязателен в PROD (отсутствует или дефолт). "
                  "Сгенерируйте: openssl rand -hex 32 и задайте в systemd. "
                  "В DEMO (LIQSCOPE_DEMO=1) можно работать с авто-секретом.")
        raise SystemExit(2)
    generated = secrets.token_urlsafe(32)
    if path:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(generated + "\n")
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            log.warning("LIQSCOPE_SECRET не задан — сгенерирован и записан в %s. "
                        "На проде задайте переменную в systemd.", path)
            return generated
        except OSError as exc:
            log.error("не удалось сохранить секрет (%s) — держим его только "
                      "в памяти этого процесса", exc)
    return generated


SECRET = _resolve_secret()
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
# LIQSCOPE_DIGEST_FILE="0" — не хранить историю (страница будет пустой). Пустая строка → default.
_raw_digest = os.getenv("LIQSCOPE_DIGEST_FILE", "").strip()
if not _raw_digest:
    DIGEST_FILE = os.path.join(HERE, "data", "digests.json")
elif _raw_digest.lower() in ("0", "none", "off", "false"):
    DIGEST_FILE = ""
else:
    DIGEST_FILE = _raw_digest
# Сводки по часам — раздел сайта: архив постов канала (то же, что ушло в
# Telegram, плюс фото). LIQSCOPE_HOURLY_FILE="0" — не хранить (раздел пустой). Пустая строка → default.
_raw_hourly = os.getenv("LIQSCOPE_HOURLY_FILE", "").strip()
if not _raw_hourly:
    HOURLY_FILE = os.path.join(HERE, "data", "channel_posts.json")
elif _raw_hourly.lower() in ("0", "none", "off", "false"):
    HOURLY_FILE = ""
else:
    HOURLY_FILE = _raw_hourly
try:
    HOURLY_KEEP = max(50, int(os.getenv("LIQSCOPE_HOURLY_KEEP", "1200") or 1200))
except ValueError:
    HOURLY_KEEP = 1200
# 📰 Статьи: материалы, которые админ пишет сам (заголовок, текст, обложка).
# LIQSCOPE_ARTICLES_FILE="0" — не хранить архив (раздел будет пустым). Пустая строка → default.
_raw_articles = os.getenv("LIQSCOPE_ARTICLES_FILE", "").strip()
if not _raw_articles:
    ARTICLES_FILE = os.path.join(HERE, "data", "articles.json")
elif _raw_articles.lower() in ("0", "none", "off", "false"):
    ARTICLES_FILE = ""
else:
    ARTICLES_FILE = _raw_articles
ARTICLES_DIR = os.getenv("LIQSCOPE_ARTICLES_DIR",
                         os.path.join(HERE, "data", "articles")).strip()
try:
    ARTICLES_KEEP = max(20, int(os.getenv("LIQSCOPE_ARTICLES_KEEP", "500") or 500))
except ValueError:
    ARTICLES_KEEP = 500
DIGEST_HOUR = int(os.getenv("LIQSCOPE_DIGEST_HOUR", "22") or 22)
DIGEST_MINUTE = int(os.getenv("LIQSCOPE_DIGEST_MIN", "0") or 0)
DIGEST_JITTER_MIN = int(os.getenv("LIQSCOPE_DIGEST_JITTER_MIN", "10") or 10)
DIGEST_SCHED = os.getenv("LIQSCOPE_DIGEST_SCHED", "1").strip().lower() \
    not in ("0", "false", "no", "off")
# Частота постов в канал: раз в N часов (1…24), по умолчанию 4. Живёт в
# настройках (меняется из админки сайта и бота без перезапуска), окружение —
# стартовое значение. Сводка поста почасовая: N последних часов, по часу на
# строку.
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
# Кэш для WS init: символы и статистика считаются раз в 1-2 сек, а не на каждый коннект
_SYMBOLS_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_STATS_CACHE: Dict[str, Any] = {}
_STATS_CACHE_TTL = 5.0  # секунды
_SYMBOLS_CACHE_TTL = 5.0
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
# Объём и VWAP по ценам: слои ликвидаций берут отсюда цену входа позиций
# (свеча — приблизительно, тик — точно), а панель показывает, где объём.
VP = VolumeProfile()
# Плечи и поддерживающая маржа бирж: без них цена ликвидации врёт в разы.
RISK = RiskLimits()
# Перевес покупателей/продавцов (тейкер, тики, фандинг) — сторона лестницы.
SIDE = SideFeed()
# 🎯 Расчётные уровни ликвидаций: OI × плечи × цена входа, минус отработавшее.
# Движок только читает данные (сеть трогают фиды), поэтому его можно звать из
# запроса; ответ кэшируется на 20 секунд.
LEVELS = LevelsEngine(oi=OI, profile=VP, side=SIDE, risk=RISK, hist=HIST)
# Снимок уровней для алертов: {"BTC_USDT": {"price", "ts", "magnets"}}.
# Его наполняет level_alert_loop, читает alerts_market_snapshot.
LEVELS_SNAP: Dict[str, dict] = {}
LEVELS_SNAP_SEC = max(10.0, float(os.getenv("LIQSCOPE_LEVELS_SNAP_SEC", "20") or 20))
LEVELS_SNAP_MAX = max(1, int(os.getenv("LIQSCOPE_LEVELS_SNAP_MAX", "8") or 8))
LEVEL_WARM_SEC = max(10.0, float(os.getenv("LIQSCOPE_LEVELS_WARM_SEC", "30") or 30))
LEVELS_WARM_MAX = max(1, int(os.getenv("LIQSCOPE_LEVELS_WARM_MAX", "40") or 40))
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
        # пользователь сессии (COOKIE_SID) — для адресных чат-событий:
        # личные сообщения и уведомления уходят только своим получателям
        self.user_id: Optional[int] = None
        self.chart = ""         # символ графика — независим от фильтра ленты
        self.tf = 5
        self.min_usd = 0.0
        self.exchange = "ALL"
        self.feed = "liq"        # лента клиента: liq | cvd | oi
        self.alive = True
        # Очередь исходящих кадров: рассылка кладёт готовый кадр и уходит, а
        # в сокет его пишет отдельная задача клиента. Забитый TCP-буфер одного
        # зрителя больше не держит event loop (а с ним и все HTTP-запросы)
        # секундами — см. _pump().
        self.out: Deque[str] = deque()
        self.out_bytes = 0
        self.dropped_frames = 0
        self._sending = False       # кадр уже вынут из очереди и идёт в сокет
        self._out_wake: Optional[asyncio.Event] = None
        self._pump_task: Optional[asyncio.Task] = None
        self._pump_loop = None

    # Капля медленного зрителя: столько кадров/байт он может накопить, пока
    # его писатель борется с сетью. Превысили — клиент безнадёжно отстал и
    # выкидывается (фронт переподключится и пересинхронизируется), иначе
    # очередь съела бы память воркера.
    OUT_MAX_FRAMES = 200
    OUT_MAX_BYTES = 512 * 1024

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

    async def send(self, msg: dict, text: Optional[str] = None) -> bool:
        """Отдать кадр клиенту. ``text`` — уже сериализованный кадр.

        Сериализуем сами и отдаём текстом: ws.send_json внутри зовёт
        json.dumps, а рассылка ликвидаций/свечей/статистики — самая
        частая сериализация процесса (orjson быстрее в разы). При
        широковещании ``Hub.broadcast`` сериализует кадр ОДИН раз и передаёт
        сюда готовый текст: 50 зрителей не должны платить 50 сериализаций
        одной и той же пачки свечей на единственном воркере.

        Медленный/зависший клиент не должен подвешивать читателей
        биржевых сокетов: send внутри ждёт drain() без лимита, а
        TCP-буфер забитого клиента может не освобождаться минутами.
        Всё, что ушло в транспорт до таймаута, остаётся валидным кадром,
        так что отмена безопасна. Застряли — клиент мёртв, выкидываем.
        """
        try:
            frame = text if text is not None else json_dumps_text(msg)
            await asyncio.wait_for(self.ws.send_text(frame),
                                   timeout=self.SEND_TIMEOUT)
            _metrics_ws_send()
            return True
        except Exception:
            self.alive = False
            return False

    # см. send(): заведомо больше любого нормального сетевого хода
    SEND_TIMEOUT = 5.0

    # --- очередь исходящих кадров -------------------------------------------
    def _wake(self) -> asyncio.Event:
        if self._out_wake is None:
            self._out_wake = asyncio.Event()
        return self._out_wake

    def offer(self, frame: str) -> bool:
        """Положить готовый кадр в очередь клиента, не ожидая сокет.

        Возвращает False, если клиент мёртв или безнадёжно отстал: вызывающий
        (``Hub.broadcast``) выкинет его из хаба. Именно это убирает хвост
        p95 в секундах: раньше рассылка стояла в ``await send_text`` на
        каждом зрителе по очереди, и один забитый TCP-буфер держал event loop
        до ``SEND_TIMEOUT`` — всё это время HTTP-запросы просто ждали в
        очереди цикла.
        """
        if not self.alive:
            return False
        if len(self.out) >= self.OUT_MAX_FRAMES or \
                self.out_bytes + len(frame) > self.OUT_MAX_BYTES:
            self.dropped_frames += len(self.out)
            self.out.clear()
            self.out_bytes = 0
            self.alive = False
            log.warning("WS-клиент отстал: очередь исходящих переполнена "
                        "(>%d кадров или %d КБ) — отключаем, фронт "
                        "переподключится", self.OUT_MAX_FRAMES,
                        self.OUT_MAX_BYTES // 1024)
            return False
        self.out.append(frame)
        self.out_bytes += len(frame)
        self._wake().set()
        return True

    async def _pump(self) -> None:
        """Единственный писатель очереди: порядок кадров у клиента сохранён."""
        wake = self._wake()
        while self.alive:
            if not self.out:
                wake.clear()
                if not self.out:            # кадр положили между проверкой и clear()
                    try:
                        await wake.wait()
                    except asyncio.CancelledError:
                        return
                    continue
            frame = self.out.popleft()
            self.out_bytes -= len(frame)
            self._sending = True
            try:
                await asyncio.wait_for(self.ws.send_text(frame),
                                       timeout=self.SEND_TIMEOUT)
                _metrics_ws_send()
            except asyncio.CancelledError:
                self.out.appendleft(frame)
                self.out_bytes += len(frame)
                raise
            except Exception as e:          # noqa: BLE001
                self.dropped_frames += len(self.out) + 1
                self.out.clear()
                self.out_bytes = 0
                self.alive = False
                log.debug("ws pump: сокет умер (%s), недоставлено %d кадров",
                          e, self.dropped_frames)
                return
            finally:
                self._sending = False

    def start_pump(self) -> None:
        """Завести писателя очереди (идемпотентно, в текущем event loop)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:                # вне цикла (тесты-двойники) — нечего заводить
            return
        if self._pump_loop is not loop:
            # Event и задача принадлежали прежнему циклу: сбрасываем оба
            self._pump_loop = loop
            self._out_wake = None
            self._pump_task = None
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = loop.create_task(self._pump(), name="ws-pump")

    async def stop_pump(self) -> None:
        t, self._pump_task = self._pump_task, None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception as e:          # noqa: BLE001
                log.debug("ws pump stop: %s", e)

    async def flush(self, timeout: float = 2.0) -> bool:
        """Дождаться, пока очередь уйдёт в сокет (тесты, аккуратное закрытие)."""
        end = time.monotonic() + timeout
        while (self.out or self._sending) and self.alive and time.monotonic() < end:
            await asyncio.sleep(0.005)
        return not self.out and not self._sending


# Потолок живых WS: каждый клиент получает широковещание, флуд соединениями
# — это DoS. nginx может резать раньше (limit_conn), это запасной кап процесса.
WS_MAX_CLIENTS = max(8, int(os.getenv("LIQSCOPE_WS_MAX_CLIENTS", "400")))


class Hub:
    def __init__(self):
        self.clients: Set[Client] = set()
        self._lock = asyncio.Lock()

    async def add(self, c: Client) -> bool:
        async with self._lock:
            if len(self.clients) >= WS_MAX_CLIENTS:
                log.warning("WS отклонён: уже %d клиентов (кап %d)",
                            len(self.clients), WS_MAX_CLIENTS)
                return False
            self.clients.add(c)
        start = getattr(c, "start_pump", None)
        if callable(start):
            start()             # писатель очереди исходящих кадров
        log.info("Клиент подключился. Всего: %d", len(self.clients))
        return True

    async def remove(self, c: Client):
        await self._stop_writer(c)
        async with self._lock:
            self.clients.discard(c)
        log.info("Клиент отключился. Всего: %d", len(self.clients))

    async def broadcast(self, msg: dict, predicate=None):
        async with self._lock:
            targets = list(self.clients)
        if predicate is not None:
            targets = [c for c in targets if predicate(c)]
        if not targets:
            return
        # Кадр сериализуется ОДИН раз на всех получателей: рассылка свечей и
        # статистики — десятки килобайт, и при 50 зрителях прежняя схема
        # (json.dumps внутри send каждого клиента) жгла 50× того же CPU на
        # единственном воркере.
        frame = json_dumps_text(msg)
        dead = []
        for c in targets:
            if not await self._deliver(c, msg, frame):
                dead.append(c)
        if dead:
            async with self._lock:
                for c in dead:
                    self.clients.discard(c)
            for c in dead:
                await self._stop_writer(c)

    @staticmethod
    async def _stop_writer(c) -> None:
        """Остановить писателя очереди, если он у клиента есть."""
        stop = getattr(c, "stop_pump", None)
        if callable(stop):
            try:
                await stop()
            except Exception as e:               # noqa: BLE001
                log.debug("hub: писатель очереди не остановился: %s", e)

    async def _deliver(self, c, msg: dict, frame: str) -> bool:
        """Положить кадр клиенту: в его очередь, а не в await на весь фан-аут.

        Порядок кадров у каждого зрителя сохраняет его собственный писатель
        (``Client._pump``), поэтому рассылка не обязана ждать сокет: медленный
        клиент копит кадры у себя и отключается по переполнению очереди, а не
        держит воркер (и всех остальных) до таймаута отправки.
        """
        try:
            offer = getattr(c, "offer", None)
            if callable(offer):
                c.start_pump()
                return bool(offer(frame))
            # двойники в тестах без очереди — прежняя адресная отправка
            return bool(await c.send(msg, text=frame))
        except Exception as e:                   # noqa: BLE001
            # один сломанный сокет не должен обрывать рассылку остальным:
            # на единственном воркере это минус свечи/ликвидации у всех
            log.debug("broadcast: отправка клиенту упала: %s", e)
            return False

    def viewed_pairs(self) -> Set[tuple]:
        return {(c.chart_symbol, c.tf) for c in self.clients}


hub = Hub()


# =============================================================================
#  Метрики процесса (для /api/metrics и внешней проверки)
# =============================================================================
# Счётчики по секундным корзинам: O(1) на запрос, память ограничена окном
# истории. Окно чтения — последние 60 секунд; корзины старше двух минут
# подчищаем при чтении, чтобы словари не росли.
_METRICS_KEEP_SEC = 120
_METRICS_WIN_SEC = 60
_HTTP_RPS: Dict[int, int] = {}          # секунда -> HTTP-запросов
_HTTP_STATUS: Dict[tuple, int] = {}     # (секунда, статус) -> запросов
_HTTP_LAT = deque(maxlen=5000)          # последние задержки HTTP (сек)
_WS_CONN: Dict[int, int] = {}           # секунда -> новых WS-подключений
_WS_SEND: Dict[int, int] = {}           # секунда -> сообщений клиентам
_METRICS_LOCK = threading.Lock()
_START_TS = time.time()


def _metrics_bump(bucket: Dict, key, amount: int = 1) -> None:
    with _METRICS_LOCK:
        bucket[key] = bucket.get(key, 0) + amount


def _metrics_http(status: int, latency_sec: float) -> None:
    now = int(time.time())
    with _METRICS_LOCK:
        _HTTP_RPS[now] = _HTTP_RPS.get(now, 0) + 1
        skey = (now, int(status))
        _HTTP_STATUS[skey] = _HTTP_STATUS.get(skey, 0) + 1
        _HTTP_LAT.append(max(0.0, float(latency_sec)))


def _metrics_ws_connect() -> None:
    _metrics_bump(_WS_CONN, int(time.time()))


def _metrics_ws_send() -> None:
    _metrics_bump(_WS_SEND, int(time.time()))


def _rate_last_minute(bucket: Dict) -> float:
    cutoff = int(time.time()) - _METRICS_WIN_SEC
    with _METRICS_LOCK:
        total = sum(v for k, v in bucket.items()
                    if isinstance(k, int) and k > cutoff)
    return round(total / _METRICS_WIN_SEC, 2)


def _status_rps_last_minute() -> Dict[str, float]:
    cutoff = int(time.time()) - _METRICS_WIN_SEC
    agg: Dict[str, int] = {}
    with _METRICS_LOCK:
        for (sec, status), cnt in _HTTP_STATUS.items():
            if sec > cutoff:
                key = str(status)
                agg[key] = agg.get(key, 0) + cnt
    return {k: round(v / _METRICS_WIN_SEC, 2) for k, v in sorted(agg.items())}


def _latency_p95_ms() -> Optional[float]:
    with _METRICS_LOCK:
        samples = sorted(_HTTP_LAT)
    if not samples:
        return None
    idx = min(len(samples) - 1, int(0.95 * len(samples)))
    return round(samples[idx] * 1000.0, 1)


def _ws_queue_stats(clients=None) -> Dict[str, int]:
    """Глубина очередей исходящих WS-кадров.

    Если кадры копятся — зрители не успевают читать, и раньше это означало бы
    паузы воркера на всю рассылку; теперь очередь локальна для клиента, а
    метрика показывает, кто именно отстаёт (``ws_slow_clients`` — те, у кого в
    очереди больше 10 кадров).
    """
    frames = 0
    nbytes = 0
    worst = 0
    slow = 0
    for c in list(hub.clients if clients is None else clients):
        n = len(getattr(c, "out", ()) or ())
        frames += n
        nbytes += int(getattr(c, "out_bytes", 0) or 0)
        if n > worst:
            worst = n
        if n > 10:
            slow += 1
    return {"ws_send_queue_frames": frames, "ws_send_queue_bytes": nbytes,
            "ws_send_queue_worst_frames": worst, "ws_slow_clients": slow}


def _metrics_prune() -> None:
    cutoff = int(time.time()) - _METRICS_KEEP_SEC
    with _METRICS_LOCK:
        for old in [k for k in _HTTP_RPS if k < cutoff]:
            _HTTP_RPS.pop(old, None)
        for old in [k for k in _HTTP_STATUS if k[0] < cutoff]:
            _HTTP_STATUS.pop(old, None)
        for old in [k for k in _WS_CONN if k < cutoff]:
            _WS_CONN.pop(old, None)
        for old in [k for k in _WS_SEND if k < cutoff]:
            _WS_SEND.pop(old, None)


# Паузы event loop. Единственный воркер обязан крутиться без остановок: если
# клиент видит p95 в секундах, а серверная метрика обработчика — 6 мс, значит
# запросы стоят в очереди цикла, пока кто-то держит его занятым (рассылка
# кадра медленному клиенту, разбор большой пачки свечей, синхронный диск).
# Сторож меряет фактическую задержку тика и пишет в лог всё, что заметно
# длиннее порога, — по времени в журнале видно, рядом с чем встало.
_LOOP_LAG: Dict[str, float] = {"max_ms": 0.0, "at": 0.0, "stalls": 0.0,
                               "last_ms": 0.0, "logged_at": 0.0}
LOOP_LAG_TICK = 0.2
LOOP_LAG_WARN_MS = max(50.0, float(os.getenv("LIQSCOPE_LOOP_LAG_MS", "500")))
LOOP_LAG_LOG_GAP = 5.0
# Диагностика «чем именно занят воркер»: debug-режим asyncio называет задачу,
# сэмплер стеков (LIQSCOPE_LOOP_TRACE) — файл, функцию и строку.
ASYNCIO_DEBUG = os.getenv("LIQSCOPE_ASYNCIO_DEBUG", "").strip() not in ("", "0", "false", "no")
SLOW_CALLBACK_SEC = max(0.05, float(os.getenv("LIQSCOPE_SLOW_CALLBACK_SEC", "0.25")))


async def loop_lag_watchdog():
    """Меряет паузы event loop и шумит, когда воркер надолго занят."""
    while True:
        try:
            t0 = time.monotonic()
            await asyncio.sleep(LOOP_LAG_TICK)
            lag_ms = max(0.0, (time.monotonic() - t0 - LOOP_LAG_TICK) * 1000.0)
            _LOOP_LAG["last_ms"] = round(lag_ms, 1)
            if lag_ms > _LOOP_LAG["max_ms"]:
                _LOOP_LAG["max_ms"] = round(lag_ms, 1)
                _LOOP_LAG["at"] = time.time()
            if lag_ms >= LOOP_LAG_WARN_MS:
                _LOOP_LAG["stalls"] += 1
                # виновника ищем на каждой паузе (не только на залогированной):
                # в /api/health должен лежать свежий стек, а не минутной давности
                if lag_ms >= LOOP_TRACE_MIN_SEC * 1000.0:
                    culprit = _loop_trace_explain(lag_ms / 1000.0)
                    # сборка мусора в том же окне объясняет паузу лучше стека:
                    # сэмплер называет кадр, который просто аллоцировал память
                    gc_note = gc_during(t0, time.monotonic())
                    if gc_note:
                        culprit = f"{culprit} | {gc_note}" if culprit else gc_note
                    if culprit:
                        _LOOP_TRACE["last"] = culprit
                        _LOOP_TRACE["held_ms"] = round(lag_ms, 1)
                        _LOOP_TRACE["stalls"] = int(_LOOP_TRACE["stalls"]) + 1
                now = time.monotonic()
                # не чаще раза в 5 с: на сильном лаге лог не должен тонуть
                if now - _LOOP_LAG["logged_at"] >= LOOP_LAG_LOG_GAP:
                    _LOOP_LAG["logged_at"] = now
                    culprit = str(_LOOP_TRACE["last"] or "")
                    log.warning("[loop] воркер был занят %.0f мс (порог %.0f мс); "
                                "пауз таких %d, максимум %.0f мс — клиенты в это "
                                "время стоят в очереди, а не обрабатываются%s",
                                lag_ms, LOOP_LAG_WARN_MS, int(_LOOP_LAG["stalls"]),
                                _LOOP_LAG["max_ms"],
                                f"; в это время он был в: {culprit}" if culprit
                                else ("; кто именно — не записано "
                                      "(LIQSCOPE_LOOP_TRACE=1)" if LOOP_TRACE
                                      else " (LIQSCOPE_LOOP_TRACE=1 назовёт стек)"))
        except asyncio.CancelledError:
            break
        except Exception as e:                       # noqa: BLE001
            log.debug("сторож пауз цикла: %s", e)
            await asyncio.sleep(1)


def _enable_asyncio_debug() -> None:
    """Включить штатную диагностику asyncio: цикл сам назовёт медленную задачу.

    ``PYTHONASYNCIODEBUG=1`` под uvicorn не срабатывает (проверено: цикл
    поднимается с ``debug=False``), поэтому флаг включаем из приложения.
    Дальше asyncio сам пишет ``Executing <Task … coro=<pump_loop()…>> took
    0.857 seconds`` — то есть называет виновника по имени задачи. Платим
    накладными расходами debug-режима, поэтому флаг диагностический.
    """
    if not ASYNCIO_DEBUG:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        loop.slow_callback_duration = SLOW_CALLBACK_SEC
        log.warning("[loop] включён debug-режим asyncio: медленнее "
                    "%.0f мс считается и логируется с именем задачи "
                    "(LIQSCOPE_ASYNCIO_DEBUG=1)", SLOW_CALLBACK_SEC * 1000)
    except Exception as e:                           # noqa: BLE001
        log.warning("[loop] debug-режим asyncio не включился: %s", e)


# --- трассировка стека: кто именно держал воркер ---------------------------
# Сторож пауз говорит «воркер был занят 857 мс», но не говорит КЕМ. Поток-
# сэмплер раз в 50 мс снимает стек главного потока (в нём живёт event loop) и
# пишет тот, который держится дольше порога: видно файл, функцию и строку.
# Механику самого цикла (ожидание epoll) не логируем — это свобода, а не занятость.
LOOP_TRACE = os.getenv("LIQSCOPE_LOOP_TRACE", "").strip() not in ("", "0", "false", "no")
LOOP_TRACE_MIN_SEC = max(0.1, float(os.getenv("LIQSCOPE_LOOP_TRACE_MS", "400")) / 1000.0)
LOOP_TRACE_INTERVAL = 0.05
LOOP_TRACE_FRAMES = 12
_LOOP_TRACE: Dict[str, object] = {"held_ms": 0.0, "stalls": 0, "last": "",
                                  "stop": False, "thread": None, "samples": 0}
# Граница «механики запуска цикла». С uvloop (его ставит uvicorn[standard])
# сам цикл живёт в Cython и в python-стеке не виден: самый внутренний кадр
# простоя — это runners.py:run / _compat.py:asyncio_run, а не selectors.select.
# Поэтому признаком простоя считаем «внутри цикла ни одного python-кадра нет»,
# а занятостью — кадры глубже границы: именно они называют виновника.
_LOOP_BOUNDARY = ("base_events.py", "selectors.py", "runners.py", "events.py",
                  "proactor_events.py", "kqueue.py", "epoll.pyx", "uvloop",
                  "_compat.py")


def _stack_signature(frame, limit: int = LOOP_TRACE_FRAMES) -> list:
    """Стек изнутри наружу: где застряли → кто позвал."""
    out = []
    f = frame
    while f is not None and len(out) < limit:
        co = f.f_code
        out.append(f"{os.path.basename(co.co_filename)}:{f.f_lineno}:{co.co_name}")
        f = f.f_back
    return out


def _stack_work(sig: list) -> list:
    """Кадры, выполнявшиеся ВНУТРИ цикла (всё, что глубже границы запуска)."""
    for i, fr in enumerate(sig):
        if any(b in fr for b in _LOOP_BOUNDARY):
            return sig[:i]
    return sig


def _stack_is_idle(sig: list) -> bool:
    """Свободен ли воркер: внутри цикла не выполняется ни один python-кадр.

    Регрессия на uvloop: прежнее правило «весь стек — внутренности asyncio»
    принимало любой простой за занятость (под циклом всегда лежит обвязка
    uvicorn/click/runpy, а с uvloop не видно и selectors.select) и писало в
    журнал каждые 300 мс при полностью свободном воркере.
    """
    return not _stack_work(sig)


# Кольцо сэмплов: (monotonic, рабочий стек). 256 × 50 мс = ~13 с окна —
# хватает, чтобы разобрать даже пятисекундную паузу.
_LOOP_SAMPLES: Deque[tuple] = deque(maxlen=256)


def _loop_trace_thread(main_tid: int) -> None:
    """Сэмплер: пишет в кольцо текущий рабочий стек и ничего не логирует.

    Признак «один и тот же стек держится N мс» сам по себе ненадёжен: короткий
    кадр, который вызывается постоянно (``ssl.py:read`` под uvloop на десяти
    биржевых лентах), попадает в каждый сэмпл и выглядит блокировкой — на бою
    это дало ложные 480 мс. Паузу достоверно знает сторож: он меряет, что цикл
    РЕАЛЬНО не крутился, и тогда берёт из кольца стек, преобладавший в окне
    этой паузы.
    """
    while not _LOOP_TRACE["stop"]:
        try:
            frame = sys._current_frames().get(main_tid)
            if frame is not None:
                work = tuple(_stack_work(_stack_signature(frame)))
                _LOOP_SAMPLES.append((time.monotonic(), work))
                _LOOP_TRACE["samples"] = int(_LOOP_TRACE["samples"]) + 1
        except Exception as e:                       # noqa: BLE001
            log.debug("сэмплер стеков: %s", e)
        time.sleep(LOOP_TRACE_INTERVAL)


def _loop_trace_explain(window_sec: float) -> str:
    """Стек, преобладавший в окне паузы: «чем именно был занят воркер»."""
    if not _LOOP_SAMPLES:
        return ""
    cutoff = time.monotonic() - max(0.05, window_sec) - LOOP_TRACE_INTERVAL * 2
    counts: Dict[tuple, int] = {}
    for ts, work in list(_LOOP_SAMPLES):
        if ts >= cutoff and work:
            counts[work] = counts.get(work, 0) + 1
    if not counts:
        return ""
    best, hits = max(counts.items(), key=lambda kv: kv[1])
    return f"{' <- '.join(best)}  ({hits} из {sum(counts.values())} сэмплов окна)"


def loop_trace_start() -> None:
    """Запустить сэмплер стеков (если включён LIQSCOPE_LOOP_TRACE)."""
    if not LOOP_TRACE or _LOOP_TRACE["thread"] is not None:
        return
    import threading
    _LOOP_TRACE["stop"] = False
    t = threading.Thread(target=_loop_trace_thread,
                         args=(threading.get_ident(),),
                         name="loop-trace", daemon=True)
    _LOOP_TRACE["thread"] = t
    t.start()
    log.warning("[loop-trace] сэмплер стеков включён: порог %.0f мс, шаг %.0f мс "
                "(LIQSCOPE_LOOP_TRACE=1)", LOOP_TRACE_MIN_SEC * 1000,
                LOOP_TRACE_INTERVAL * 1000)


def loop_trace_stop() -> None:
    _LOOP_TRACE["stop"] = True
    t, _LOOP_TRACE["thread"] = _LOOP_TRACE["thread"], None
    if t is not None:
        t.join(timeout=1.0)


def _rss_bytes() -> int:
    """ТЕКУЩИЙ RSS процесса в байтах.

    ``ru_maxrss`` — это пик за всё время жизни процесса: после прогрева
    (восстановление истории, каталог бирж) метрика навсегда оставалась высокой,
    даже когда память уже освободилась, и порог «RSS < 500 МБ» в
    ``tools/load_test.py`` срабатывал на процессе, который по факту занимает
    в разы меньше. Поэтому на Linux читаем ``VmRSS`` из ``/proc/self/status``,
    а пик отдаём отдельным полем :func:`_rss_peak_bytes`.
    """
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except Exception:  # noqa: BLE001 — нет /proc (macOS/Windows) или не прочитался
        pass
    return _rss_peak_bytes()


def _rss_peak_bytes() -> int:
    """Пиковый RSS с момента старта (``ru_maxrss``): Linux отдаёт КБ, macOS — байты."""
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(rss) * (1 if sys.platform == "darwin" else 1024)
    except Exception:  # noqa: BLE001 — метрика не должна ронять ручку
        return 0


def _wal_bytes() -> int:
    try:
        return int(os.path.getsize(account_store.path + "-wal"))
    except OSError:
        return 0


async def _sqlite_ping_ms() -> float:
    """Отклик SQLite: SELECT 1 в пуле потоков, чтобы не стопать loop."""
    try:
        return await asyncio.to_thread(account_store.ping)
    except Exception:  # noqa: BLE001
        return -1.0


feed: Optional[MarketFeed] = None
# 📖 Стакан: опрос L2 и детектор стен (заполняется в lifespan; в тестах — подмена).
book_feed_inst: Optional[BookFeed] = None
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


async def chat_notify_loop() -> None:
    """🔒 Чат по таймеру: напоминания о безответных ЛС и уборка истории.

    Раз в минуту: если получатель личного сообщения не читает и не отвечает
    (задержка настраивается в админке), а Telegram привязан — бот стукнет
    один раз. Для поддержки правило то же: сообщение в Telegram уходит не
    сразу, а через ``chat_dm_tg_delay_min`` минут, если админ так и не ответил
    и его нет на сайте. Заодно срезаем всё, что старше 3 дней (и ЛС, и общий
    чат).
    """
    while True:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        try:
            await private_chat_mod.scan_reminders()
        except Exception as e:  # noqa: BLE001
            log.debug("chat reminders: %s", e)
        try:
            await support_chat_mod.scan_support_reminders()
        except Exception as e:  # noqa: BLE001
            log.debug("support reminders: %s", e)
        try:
            await asyncio.to_thread(account_store.prune_private)
            await asyncio.to_thread(account_store.prune_chat)
        except Exception as e:  # noqa: BLE001
            log.debug("chat prune: %s", e)


async def history_task() -> None:
    """Раз в несколько минут: свёртки дня на диск и уборка старых дней.

    Свёртки держат ликвидации, CVD и объём по часам, поэтому их потеря при
    рестарте означала бы дырку в месячной истории — пишем часто.
    """
    while True:
        try:
            saved = await asyncio.to_thread(HIST.flush)
            removed = await asyncio.to_thread(HIST.cleanup)
            await asyncio.to_thread(VP.trim)
            await asyncio.to_thread(VP.save)
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


def level_alert_symbols() -> List[str]:
    """Кого считать для алертов подхода к уровню.

    Сначала монеты подписок с метрикой «уровни» (их ждут в боте), потом
    открытые графики: панель и слой берут готовый расчёт из кэша движка.
    """
    from alerts import normalize_config, symbol_of
    out: List[str] = []
    seen: Set[str] = set()

    def add(sym: str) -> None:
        s = str(sym or "").upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    try:
        subs = account_store.list_alert_subscribers()
    except Exception:                       # noqa: BLE001
        subs = []
    try:
        level_subs = account_store.list_service_subscribers("levels")
    except Exception:                       # noqa: BLE001
        level_subs = []
    from alerts import normalize_level_signal
    for sub in level_subs:
        try:
            sig = normalize_level_signal(sub.get("config"))
        except Exception:                   # noqa: BLE001
            continue
        if not sig.get("notify"):
            continue
        if sig.get("symbols"):
            for sym in sig["symbols"]:
                add(sym)
        else:
            for sym in list(feed.symbols if feed else [])[:LEVELS_SNAP_MAX]:
                add(sym)
    for sub in subs:
        try:
            cfg = normalize_config(sub.get("config"))
        except Exception:                   # noqa: BLE001
            continue
        if not cfg.get("enabled") or "level" not in (cfg.get("watch") or []):
            continue
        coin = symbol_of(cfg, "level")
        if coin and coin != "ALL":
            add(coin)
        else:
            for sym in list(feed.symbols if feed else [])[:LEVELS_SNAP_MAX]:
                add(sym)
    for c in hub.clients:
        add(c.chart_symbol)
    if not out:
        # никто ещё не включил метрику — держим тёплым первый экран терминала
        for sym in list(feed.symbols if feed else [])[:LEVELS_SNAP_MAX]:
            add(sym)
    return out[:LEVELS_WARM_MAX]


async def liq_levels_task() -> None:
    """Фон: греет данные уровней и обновляет снимок для алертов.

    Сеть здесь только на прогреве (перевес сторон и риск-лимиты) — сам расчёт
    читает то, что уже лежит в памяти и на диске. Идём по списку монет по
    кругу: за проход успеваем прогреть немногих, зато ни одна монета не
    остаётся без внимания и на биржи не летит залп. Снимок нужен движку
    алертов: он не должен считать лестницы в своём проходе.
    """
    await asyncio.sleep(20)             # пусть подтянутся символы и сессии
    asked: List[str] = []
    snap_at = 0.0
    while True:
        try:
            session = getattr(feed, "session", None) if feed else None
            want = level_alert_symbols()
            if session and LEVELS.enabled and want:
                # круг по списку: каждый проход — следующая четвёрка монет
                todo = [s for s in want if s not in asked] or want
                if not [s for s in want if s not in asked]:
                    asked = []
                batch = todo[:4]
                asked.extend(batch)
                await LEVELS.warm(session, batch, limit=len(batch))
                # считаем те монеты, которых ждут алерты и графики: их ответ
                # кэшируется движком, а снимок для алертов обновляем не чаще
                # LEVELS_SNAP_SEC — иначе лестницы считались бы зря
                if time.time() - snap_at >= LEVELS_SNAP_SEC:
                    snap_at = time.time()
                    for sym in batch:
                        price = float((feed.prices or {}).get(sym) or 0.0)
                        try:
                            data = await LEVELS.payload(sym, session=session,
                                                        price=(price or None))
                        except Exception as e:    # noqa: BLE001
                            log.debug("уровни %s: %s", sym, e)
                            continue
                        if not data.get("enabled") or not data.get("magnets"):
                            continue
                        LEVELS_SNAP[sym] = {
                            "symbol": sym, "price": data.get("price"),
                            "ts": data.get("ts"),
                            # список, а не словарь up/down: движок алертов и
                            # лента метрики ходят по магнитам циклом
                            "magnets": data.get("magnets_list") or [],
                            "totals": data.get("totals") or {},
                            "calibration": data.get("calibration") or {},
                            "estimate": True,
                        }
                    for gone in [s for s in LEVELS_SNAP if s not in want]:
                        LEVELS_SNAP.pop(gone, None)
            await asyncio.to_thread(LEVELS.save)
        except asyncio.CancelledError:
            raise
        except Exception as e:          # noqa: BLE001
            log.debug("фон уровней ликвидаций: %s", e)
        await asyncio.sleep(LEVEL_WARM_SEC)


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
            VP.add_trade(symbol, tick_ts, price, abs(signed), side)
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
    try:
        price = float(price)
        vol_delta = float(vol_delta or 0.0)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(price) or not math.isfinite(vol_delta):
        return []
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
        # Свеча — весь рынок (не только тейкерские сделки): даёт VWAP и объём
        # и по тем монетам, где тиков не было вовсе. Кладём ПРИРОСТ минуты:
        # кадры свечи приходят несколько раз в минуту, и накопленный объём
        # умножил бы профиль на число кадров.
        VP.add_candle(symbol, minute, candle1m.get("high"), candle1m.get("low"),
                      candle1m.get("close"), delta)
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


def _clean_candles(series):
    """Выбрасывает битые свечи: нечисла, NaN/Inf, дубли и беспорядок времени.

    Один битый бар роняет lightweight-charts setData() целиком — клиент
    получает пустой график при живых state.candles (тёмное поле, живая шапка).
    """
    if not series:
        return []
    out = []
    for c in series:
        if not isinstance(c, dict):
            continue
        try:
            t = int(c.get("time"))
        except (TypeError, ValueError):
            continue
        row = dict(c)
        row["time"] = t
        ok = True
        for k in ("open", "high", "low", "close"):
            try:
                v = float(row.get(k))
            except (TypeError, ValueError):
                ok = False
                break
            if not math.isfinite(v):
                ok = False
                break
            row[k] = v
        if not ok:
            continue
        try:
            v = float(row.get("volume") or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        row["volume"] = v if (math.isfinite(v) and v >= 0) else 0.0
        out.append(row)
    out.sort(key=lambda c: c["time"])
    dedup = []
    for c in out:
        if dedup and dedup[-1]["time"] == c["time"]:
            dedup[-1] = c
        else:
            dedup.append(c)
    return dedup


async def _fetch_candles_exchange(symbol: str, tf: int) -> Optional[list]:
    """Биржа + история CVD: тяжёлая часть загрузки свечей.

    Зовётся ТОЛЬКО из фоновой задачи (``market_feed.schedule_candles``) или из
    ``get_candles(force=True)`` у внутренних циклов сервера. В обработчике
    запроса её быть не должно: синхронный поход на Binance на каждого зрителя
    — это лаг единственного воркера и 429/418 (бан IP всего сервера) в придачу.
    """
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
    # чистим ДО подшивки OI: аттач идёт по совпадению time; пустой результат
    # честно проваливается в ветки «старый кэш» / «заготовка» у вызывающего
    return _clean_candles(real)


async def _apply_candles(symbol: str, tf: int, real: list,
                         source: str = "exchange") -> dict:
    """Готовые свечи — в кэш отдач и в буфер фида. Только локальная работа.

    OI берём из трекера: ``ensure_symbol`` умеет сходить на биржу, поэтому
    зовут эту функцию тоже только из фона (загрузка истории, kline_refresher).
    """
    k = _key(symbol, tf)
    tracker = getattr(feed, "oi", None)
    if tracker is not None:
        try:
            await tracker.ensure_symbol(symbol)
            _attach_oi(real, tf, tracker.series(symbol),
                       tracker.bucket_chg(symbol))
        except Exception as e:
            log.debug("oi attach %s: %s", symbol, e)
    # сохраняем «живой» хвост, если биржа ещё не закрыла текущую свечу
    CANDLES[k] = {"candles": real, "ts": time.time(), "source": source}
    _cvd_seed_base(CANDLES[k], symbol, tf)
    if feed is not None:
        # буфер фида — то, чем /api/klines отвечает, пока наш кэш пуст
        feed.cache_candles(symbol, tf, real, source)
    return CANDLES[k]


def _placeholder_entry(symbol: str, tf: int, k: str) -> dict:
    """Заготовка от последней известной цены: локально, мгновенно, без сети.

    Строим, когда истории нет ни в кэше, ни в буфере фида (монету только что
    открыли или биржи молчат): график не должен падать пустым полем. Источник
    помечен как "unavailable" (в демо-режиме рисуем случайное блуждание, чтобы
    было что смотреть), а ``pending`` говорит клиенту, что настоящие свечи уже
    грузятся в фоне и придут кадром WS или следующим опросом.
    """
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
                  "source": "demo" if DEMO_MODE else "unavailable",
                  "pending": bool(feed is not None and feed.candles_pending(symbol, tf))}
    _cvd_seed_base(CANDLES[k], symbol, tf)
    return CANDLES[k]


async def _candles_blocking(symbol: str, tf: int, k: str) -> dict:
    """``force=True``: дождаться биржи. Для внутренних фоновых циклов сервера.

    Прогрев старта, ``kline_refresher`` и потоки постов — не обработчики
    запросов: здесь ожидание сети безопасно, а данные нужны настоящие.
    """
    real = await _fetch_candles_exchange(symbol, tf)
    if real:
        return await _apply_candles(symbol, tf, real)

    entry = CANDLES.get(k)
    if entry:
        entry["ts"] = time.time() - KLINE_TTL / 2   # отдадим старое, попробуем позже
        entry["candles"] = _clean_candles(entry.get("candles"))
        if entry["candles"]:
            return entry
        # кэш состоял из одних битых свечей — строим заготовку ниже
    return _placeholder_entry(symbol, tf, k)


async def _on_candles_ready(symbol: str, tf: int, candles: list, source: str) -> None:
    """Колбэк фида: фоновая загрузка истории дошла (``market_feed.on_candles``).

    Кроме записи в кэш догоняем зрителей графика кадром ``candles``, если до
    этого они смотрели на заготовку: иначе настоящие свечи доехали бы только
    следующим опросом клиента (60 с в терминале, 15 с в embed-графике).
    """
    k = _key(symbol, tf)
    prev_source = str((CANDLES.get(k) or {}).get("source") or "")
    real = _clean_candles(candles)
    if not real:
        return
    entry = await _apply_candles(symbol, tf, real, source or "exchange")
    if prev_source != "exchange":
        await _push_candles_to_viewers(symbol, tf, entry)


async def _push_candles_to_viewers(symbol: str, tf: int, entry: dict) -> None:
    """Кадр candles только тем, у кого открыт этот график и ТФ."""
    candles = (entry or {}).get("candles") or []
    if not candles:
        return
    await hub.broadcast(
        {"type": "candles", "symbol": symbol, "tf": tf,
         "source": entry.get("source") or "exchange", "candles": candles},
        predicate=lambda c: c.chart_symbol == symbol and c.tf == tf)


async def get_candles(symbol: str, tf: int, force: bool = False) -> dict:
    """Свечи графика. Путь запроса — только чтение памяти, биржа всегда в фоне.

    ``force=False`` (REST ``/api/klines``, ``/api/liq_clusters``, WS ``sub``):
    отдаём то, что уже лежит локально — кэш отдач ``CANDLES`` → буфер фида
    ``market_feed.get_candles_cached`` → заготовка от последней цены, — и
    ставим загрузку истории фоновой задачей (``asyncio.create_task``). Ответ не
    ждёт сети, поэтому 50+ зрителей одного графика не превращаются в 50+
    походов на биржу и не вешают единственный воркер uvicorn.

    ``force=True`` — прежнее поведение «сходить на биржу и дождаться»: его
    зовут внутренние фоновые циклы (прогрев, ``kline_refresher``, потоки
    постов), где некому ждать HTTP-ответа.
    """
    symbol = canon(symbol)
    parsed = parse_tf(tf)
    tf = parsed if parsed is not None else 5
    k = _key(symbol, tf)
    entry = CANDLES.get(k)
    fresh = entry and (time.time() - entry["ts"] < KLINE_TTL) and not force
    if fresh:
        # кэш могли отравить живые тики — чиним на отдаче (и в самом кэше)
        entry["candles"] = _clean_candles(entry.get("candles"))
        return entry

    if force:
        return await _candles_blocking(symbol, tf, k)

    # --- путь запроса: ни одного ожидания биржи ------------------------------
    if feed is not None:
        # дедупликация и зазор внутри фида: сто зрителей — один поход на биржу
        feed.schedule_candles(symbol, tf, loader=_fetch_candles_exchange)
    if entry:
        entry["candles"] = _clean_candles(entry.get("candles"))
        if entry["candles"]:
            # отдаём то, что есть, пока фон догружает свежее
            entry["stale"] = True
            return entry
        # кэш состоял из одних битых свечей — смотрим буфер фида ниже
    cached = feed.get_candles_cached(symbol, tf) if feed is not None else None
    if cached and cached.get("candles"):
        CANDLES[k] = {"candles": _clean_candles(cached["candles"]),
                      "ts": float(cached.get("ts") or 0.0),
                      "source": cached.get("source") or "cache",
                      "stale": True}
        _cvd_seed_base(CANDLES[k], symbol, tf)
        return CANDLES[k]
    return _placeholder_entry(symbol, tf, k)


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


def book_snapshot(cfg: Optional[dict] = None) -> Dict[str, Any]:
    """Доска кабинета «Стакан»: живые стены по монетам из настроек + статус опроса."""
    book = book_feed_inst
    cfg = normalize_book_cfg(cfg or {})
    if book is None:
        return {"config": cfg, "walls_by_symbol": [], "status": {"mode": "off"}}
    rows = []
    for sym in cfg["symbols"][:4]:
        book.note_view(sym)   # открытая доска кабинета держит монету в опросе
        snap = book.snapshot(sym, cfg["min_usd"])
        try:
            met = book.metrics(sym)
        except Exception as e:                          # noqa: BLE001
            log.debug("book metrics %s: %s", sym, e)
            met = {}
        rows.append({"symbol": sym, "ts": snap.get("ts"), "mid": snap.get("mid"),
                     "spread_bps": snap.get("spread_bps"), "walls": snap.get("walls") or [],
                     "metrics": met})
    return {"config": cfg, "walls_by_symbol": rows, "poll_sec": book.poll_sec,
            "status": book.status_summary()}


def book_sub_symbols() -> Set[str]:
    """Монеты подписок «Стакан: стены» — их надо опрашивать, даже когда
    никто не смотрит график (ради сигнала в Telegram)."""
    out: Set[str] = set()
    try:
        for s in account_store.list_service_subscribers("book"):
            cfg = normalize_book_cfg(s.get("config"))
            if cfg["enabled"]:
                out.update(cfg["symbols"])
    except Exception as e:                          # noqa: BLE001
        log.debug("book subs: %s", e)
    return out


async def book_alert_loop():
    """📖 Стакан: сигнал в Telegram, когда на выбранной монете появляется
    новая стена крупнее порога. Не спамим при старте — всё, что уже висело
    в стакане до включения петли, считается «виденным»."""
    try:
        await asyncio.sleep(25)                     # дать фиду наполнить стакан
    except asyncio.CancelledError:
        return
    seen: Dict[int, Dict[str, int]] = {}            # user_id -> {sym: max id}
    while True:
        try:
            book = book_feed_inst
            if book is not None:
                subs = account_store.list_service_subscribers("book")
                now = time.time()
                for sub in subs:
                    uid = int(sub["user_id"])
                    cfg = normalize_book_cfg(sub.get("config"))
                    if not cfg["enabled"]:
                        continue
                    last = seen.get(uid)
                    if last is None:
                        last = {sym: max((w["id"] for w in store.values()),
                                         default=0)
                                for sym, store in book.walls.items()}
                        seen[uid] = last
                        continue
                    # Telegram — отдельный тумблер («TELEGRAM ВКЛ/ВЫКЛ»), а лента
                    # кабинета живёт по подписке: раньше сигнал уходил только
                    # тем, у кого привязан бот, и во вкладке «Сервисы» стены не
                    # появлялись вовсе
                    tg_id = int(sub.get("tg_id") or 0) if cfg["notify"] else 0
                    if not tg_bot.running:
                        tg_id = 0
                    lang = str(sub.get("language") or "ru")[:2]
                    for w in book.new_open_walls(cfg["symbols"], cfg["min_usd"],
                                                 cfg["side"], now - 600):
                        if w["id"] <= last.get(w["sym"], 0):
                            continue
                        last[w["sym"]] = w["id"]
                        await push_service_message(
                            uid, "book", book_chat_text(w),
                            {"symbol": w["sym"], "parts": book_chat_meta(w)})
                        if not tg_id:
                            continue
                        try:
                            await tg_bot.send(
                                tg_id, format_wall_html(w, lang),
                                markup=tg_bot.site_link_kb("📖 посмотреть"),
                            )
                        except Exception as e:      # noqa: BLE001
                            log.debug("book notify %s: %s", tg_id, e)
        except asyncio.CancelledError:
            break
        except Exception as e:                      # noqa: BLE001
            log.debug("book alerts: %s", e)
        try:
            await asyncio.sleep(15)
        except asyncio.CancelledError:
            break


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
    # Монеты терминала — в ярус уровней ликвидаций: OI по ним снимается редко,
    # но по всему списку, иначе расчёт уровней видит одни открытые графики.
    tracker = getattr(feed, "oi", None)
    if tracker is not None:
        try:
            tracker.watch_levels(feed.symbols or [])
        except Exception as e:  # noqa: BLE001
            log.debug("ярус уровней OI: %s", e)


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
    # Уровни ликвидаций для алертов: считает фон, здесь только снимок
    # (расчёт на 40 монет в каждом проходе движка алертов — это залп).
    return {"now": now, "events": events, "cvd": cvd, "oi": oi,
            "levels": dict(LEVELS_SNAP)}


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
        # лента кабинета: те же сигналы, что уходят в Telegram
        for hit in fresh[:3]:
            await push_service_message(
                int(w["user_id"]), "pump", pump_chat_text(hit),
                {"symbol": str(hit.get("symbol") or ""),
                 "parts": pump_chat_meta(hit)})
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
        # у подхода к уровню своя пауза (окно метрики): уровень стоит на месте,
        # и сигнал по нему не должен повторяться каждую минуту
        gap = float(hit.get("cooldown_min") or (MIN_GAP_SEC / 60.0))
        if not should_fire(last, now, gap):
            continue
        out.append(hit)
        if len(out) >= 3:
            break
    return out


async def levels_signal_loop():
    """Сигнал сервиса «Уровни ликвидаций»: цена подошла к расчётному магниту.

    Тумблер и фильтры живут в кабинете этого сервиса, не в алертах по объёму.
    Пока Telegram выключен, в бот ничего не уходит. Пауза — на монету, чтобы
    один и тот же магнит не писал каждые полминуты.
    """
    from alerts import (format_alert_html, level_signal_hits,
                        normalize_level_signal, should_fire)
    try:
        await asyncio.sleep(28)
    except asyncio.CancelledError:
        return
    while True:
        try:
            market = alerts_market_snapshot()
            now = float(market.get("now") or time.time())
            subs = account_store.list_service_subscribers("levels")
            tg_bot.warm_langs(subs)
            for sub in subs:
                cfg = normalize_level_signal(sub.get("config"))
                if not cfg.get("notify"):
                    continue
                uid = int(sub["user_id"])

                def last_fire(symbol, _uid=uid):
                    if not symbol or str(symbol).upper() in ("ALL", ""):
                        return account_store.last_alert_any(_uid, "level")
                    return account_store.last_alert_ts(_uid, "level", symbol)

                sent = 0
                for hit in level_signal_hits(market, cfg, limit=3):
                    if not should_fire(last_fire(hit.get("symbol")), now,
                                       cfg["cooldown_min"]):
                        continue
                    account_store.add_alert_event(uid, hit)
                    await push_service_message(
                        uid, "levels", alerts_chat_text(hit),
                        {"symbol": str(hit.get("symbol") or ""),
                         "metric": "level",
                         "parts": alerts_chat_meta(hit)})
                    tg_id = int(sub.get("tg_id") or 0)
                    if tg_id and tg_bot.running:
                        await tg_bot.send(
                            tg_id, format_alert_html(hit, tg_bot.site_url()),
                            markup=tg_bot.site_link_kb("посмотреть в терминале"))
                    sent += 1
                    if sent >= 3:
                        break
        except asyncio.CancelledError:
            break
        except Exception as e:              # noqa: BLE001
            log.warning("сигнал уровней: %s", e)
        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            break


def alerts_chat_text(hit: dict) -> str:
    from alerts import chat_text
    return chat_text(hit)


def alerts_chat_meta(hit: dict) -> list:
    from alerts import chat_meta
    return chat_meta(hit)


async def alert_loop():
    """Раз в несколько секунд проверяет пороги и шлёт в Telegram."""
    import alerts
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
                    # тот же сигнал — в личную ленту кабинета (вкладка «Сервисы»)
                    await push_service_message(
                        uid, "alert", alerts.chat_text(hit),
                        {"symbol": str(hit.get("symbol") or ""),
                         "metric": str(hit.get("metric") or ""),
                         "parts": alerts.chat_meta(hit)})
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
    import correlations
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
                    # и в ленту кабинета: в Telegram письмо, на сайте — строка
                    await push_service_message(
                        uid, "corr", correlations.chat_text(hit),
                        {"symbol": str(hit.get("symbol") or ""),
                         "metric": metric,
                         "parts": correlations.chat_meta(hit)})
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


_ARCHIVE_CELLS = {"at": 0.0, "cells": None}


def month_cells(now: Optional[float] = None) -> list:
    """Часовые свёртки за месяц. Кэш на полторы минуты: страница не читает диск на каждый пиксель."""
    now = float(now if now is not None else time.time())
    cached = _ARCHIVE_CELLS.get("cells")
    if cached is not None and now - float(_ARCHIVE_CELLS.get("at") or 0) < 90:
        if PERF_LOG:
            log.info("[perf] month_cells cache hit age=%.1fs len=%d", now - float(_ARCHIVE_CELLS.get("at") or 0), len(cached))
        return cached
    if not HISTORY_FILE:
        return []
    t0 = time.monotonic() if PERF_LOG else 0.0
    try:
        cells = HIST.hours_range(now - HISTORY_TTL_HOURS * 3600, now, now)
    except Exception as e:                           # noqa: BLE001
        log.debug("архив часов: %s", e)
        return []
    if PERF_LOG:
        dt = (time.monotonic() - t0) * 1000 if t0 else 0
        log.info("[perf] month_cells miss read %d cells in %.1fms", len(cells), dt)
    _ARCHIVE_CELLS["at"] = now
    _ARCHIVE_CELLS["cells"] = cells
    return cells


def restore_boards_from_archive() -> int:
    """Часы, которых нет в урезанном реплее событий, взять из свёрток месяца."""
    return archive_restore.fill_boards_from_cells(BOARD, SLOTS, month_cells())


def compute_stats(symbol: Optional[str] = None, exchange: Optional[str] = None) -> dict:
    _perf_t0 = time.monotonic() if PERF_LOG else 0.0
    now = time.time()
    cache_key = f"{symbol or 'ALL'}|{exchange or 'ALL'}"
    cached = _STATS_CACHE.get(cache_key)
    if cached and now - cached.get("_at", 0) < _STATS_CACHE_TTL:
        return cached["data"]

    # Оптимизация: один проход по LIQUIDATIONS вместо 5-7 копий и фильтров
    # Раньше делалось list(LIQUIDATIONS) + фильтрация по symbol/exchange + ещё
    # отдельный pool = list(LIQUIDATIONS) — всё это 60k*2 копий и сканов.
    # Теперь — один проход, считаем всё сразу.
    cutoff_24h = now - 86400
    # Для окон STAT_WINDOWS заранее считаем cutoff'ы
    window_cutoffs = [(suffix, now - sec) for suffix, sec in STAT_WINDOWS]
    # wins accumulators: suffix -> {longs, shorts}
    win_acc = {suffix: {"longs": 0.0, "shorts": 0.0} for suffix, _ in STAT_WINDOWS}

    items = []  # только если нужен фильтр по symbol/exchange для total_count/biggest
    # Для лидеров — всегда по всем монетам, но с фильтром биржи
    pool24 = []
    # Для d24 и biggest
    d24 = []

    # Флаги фильтров
    need_symbol_filter = bool(symbol and symbol != "ALL")
    need_exch_filter = bool(exchange and exchange != "ALL")
    sym_filter = symbol if need_symbol_filter else None
    exch_filter = exchange if need_exch_filter else None

    # Один проход по кольцу
    for ev in LIQUIDATIONS:
        # Фильтр для items (учитывает symbol и exchange)
        if need_symbol_filter and ev["symbol"] != sym_filter:
            # для pool24 символ не фильтруем, только биржу — проверяем отдельно ниже
            pass
        else:
            if need_exch_filter and ev["exchange"] != exch_filter:
                pass
            else:
                items.append(ev)
                # d24 — часть items в последние 24ч
                if ev["timestamp"] >= cutoff_24h:
                    d24.append(ev)

        # pool24 — всегда по всем монетам, но с фильтром биржи (для лидеров)
        if need_exch_filter and ev["exchange"] != exch_filter:
            pass
        else:
            if ev["timestamp"] >= cutoff_24h:
                pool24.append(ev)

        # Для окон — только если событие в items (с учётом фильтров)
        # Проверяем, входит ли в items по тем же фильтрам
        if need_symbol_filter and ev["symbol"] != sym_filter:
            continue
        if need_exch_filter and ev["exchange"] != exch_filter:
            continue
        # Теперь ev точно в items, проверяем окна
        ts = ev["timestamp"]
        is_long = ev["side"] == "SELL"
        usd = ev["usd"]
        for suffix, cutoff in window_cutoffs:
            if ts >= cutoff:
                if is_long:
                    win_acc[suffix]["longs"] += usd
                else:
                    win_acc[suffix]["shorts"] += usd

    wins = {}
    for suffix, _ in STAT_WINDOWS:
        longs = win_acc[suffix]["longs"]
        shorts = win_acc[suffix]["shorts"]
        wins[f"total_usd_{suffix}"] = longs + shorts
        wins[f"longs_usd_{suffix}"] = longs
        wins[f"shorts_usd_{suffix}"] = shorts

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

    # Сутки из часовых свёрток, если буфер событий их не покрывает (рестарт,
    # кольцо на 60 тысяч). Короткие окна остаются по событиям: у них точные секунды.
    # Оптимизация: архив читаем только если в памяти мало данных (после рестарта),
    # иначе это лишние 5-100ms на каждый WS connect и stats_broadcaster.
    _need_archive = (len(items) < 5000 or len(d24) < 50 or len(pool24) < 20)
    if _need_archive and (not exchange or exchange == "ALL"):
        try:
            from archive_restore import leaders_from_cells
            from history import aggregate_hours
            sym = symbol if symbol and symbol != "ALL" else None
            cells = [c for c in month_cells(now) if c[0] + 3600 > now - 86400 and c[0] <= now]
            agg = aggregate_hours(cells, symbol=sym)
            arch_usd = float(agg.get("usd") or 0)
            arch_n = int(agg.get("count") or 0)
            if arch_n > len(d24) + 2 and arch_usd > float(wins.get("total_usd_24h") or 0) * 1.02 + 1:
                wins["total_usd_24h"] = arch_usd
                wins["longs_usd_24h"] = float(agg.get("long_usd") or 0)
                wins["shorts_usd_24h"] = float(agg.get("short_usd") or 0)
                leaders = leaders_from_cells(cells)
                if leaders:
                    top_coins = leaders
                if agg.get("by_exchange"):
                    exch_totals = dict(agg["by_exchange"])
                mx = None
                for h, cell in cells:
                    try:
                        usd = float(cell.get("max_usd") or 0)
                    except (TypeError, ValueError):
                        continue
                    if usd > 0 and (mx is None or usd > float(mx.get("usd") or 0)):
                        mx = {"symbol": cell.get("max_symbol") or "", "usd": usd,
                              "exchange": "", "side": "", "timestamp": h}
                if mx and (biggest is None or float(mx.get("usd") or 0) > float(biggest.get("usd") or 0)):
                    biggest = mx
        except Exception as e:                       # noqa: BLE001
            log.debug("статистика из архива: %s", e)

    out = dict(wins)
    out.update({
        "top_coins": top_coins[:12],
        "exchanges": exch_totals,
        "total_count": len(items),
        "biggest_24h": biggest,
        "demo": DEMO_MODE,
    })
    # Сохраняем в кэш
    _STATS_CACHE[cache_key] = {"_at": now, "data": out}
    # Чистим старые ключи, чтобы не разрасталось
    if len(_STATS_CACHE) > 32:
        oldest = sorted(_STATS_CACHE.items(), key=lambda kv: kv[1].get("_at", 0))[:8]
        for k, _ in oldest:
            _STATS_CACHE.pop(k, None)
    if PERF_LOG:
        _perf_dt = (time.monotonic() - _perf_t0) * 1000 if _perf_t0 else 0
        log.info("[perf] compute_stats symbol=%s exchange=%s items=%d d24=%d pool24=%d total=%.1fms",
                 symbol, exchange, len(items), len(d24), len(pool24), _perf_dt)
    return out


def _cvd_window(symbol: str, sec: float = 14400.0) -> Optional[float]:
    """Сумма тейкер-дельты по монете за окно. None — нет ни тиков, ни архива.

    Живой аккумулятор точнее, но после перезагрузки он пуст. Тогда дельту
    берём из часовых свёрток месяца, а не из одной свечи.
    """
    now = time.time()
    live = None
    for tf in (5, 15, 60, 240):
        acc = CVD_ACC.get(f"{symbol}|{tf}") or {}
        if not acc:
            continue
        try:
            pts = [(float(b), float(v)) for b, v in acc.items() if now - float(b) <= sec]
        except (TypeError, ValueError):
            continue
        if not pts:
            continue
        oldest = min(b for b, _v in pts)
        live = round(sum(v for _b, v in pts), 2)
        if now - oldest <= max(sec * 0.25, 3600):
            return live
        break
    try:
        from archive_restore import cvd_from_cells
        cells = [c for c in month_cells(now) if c[0] + 3600 > now - sec and c[0] <= now]
        arch = cvd_from_cells(cells, symbol)
    except Exception as e:                           # noqa: BLE001
        log.debug("cvd архива %s: %s", symbol, e)
        arch = None
    if arch is not None:
        return arch
    if live is not None:
        return live
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
    # Минутки после рестарта пустые. Часовые свёртки закрывают дыры, не
    # затирая слот, который уже посчитан по живым тикам.
    try:
        from archive_restore import slot_flows_from_cells
        span = max(4, int(need_slots)) * SLOT_SEC
        now = time.time()
        cells = [c for c in month_cells(now) if c[0] + 3600 > now - span and c[0] <= now]
        for sym, by_slot in slot_flows_from_cells(cells).items():
            dst = out.setdefault(sym, {})
            for slot, cell in by_slot.items():
                if slot not in dst:
                    dst[slot] = cell
    except Exception as e:                       # noqa: BLE001
        log.debug("потоки постов: архив не подставился: %s", e)
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
                    # force=True: это фоновая задача постов, а не запрос
                    # пользователя — объёмы слотов нужны настоящие, заготовка
                    # от последней цены дала бы пост с нулевым оборотом
                    entry = await asyncio.wait_for(
                        get_candles(sym, 15, force=True), timeout=20)
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
    """Сохранить частоту постов (1…24 часов). Возвращает применённое значение."""
    from channel_digest import INTERVAL_SETTING, clamp_interval
    hours = clamp_interval(value)
    account_store.set_setting(INTERVAL_SETTING, str(hours), actor_id=actor_id)
    return hours


def _board_window_facts(board: dict) -> dict:
    """Итоги окна поста из блоков стенда: суммы, монеты и крупнейший удар.

    Кольцевой буфер событий держит 60 тысяч ликвидаций — на длинном окне
    (пост раз в 10…24 ч) он обрезается, и в посте выходила заниженная касса.
    Блоки стенда копят те же события без обрезки, поэтому итог берём по ним.
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

    Окно равно промежутку между постами (частота задаётся в админке, 1…24 ч).
    Сводка почасовая: внутри поста — по одной строке на каждый из N последних
    завершённых часов, независимо от частоты.
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
    # Стенд постов: почасовые блоки на 15-минутной сетке (ликвы, перекос CVD,
    # OI), в посте он идёт сразу после шапки. Блок — ровно 1 час, блоков —
    # по числу часов окна (равно частоте постов): раз в час → 1 час,
    # раз в 5 → 5 часов, вплоть до 24.
    try:
        flows = await slot_flows((2 * interval + 2) * (HOUR // SLOT_SEC))
        board = build_snapshot(SLOTS, OI, now=now, span=interval,
                               group=HOUR // SLOT_SEC, flows=flows)
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
        # Стенд после рестарта может не покрыть окно, если реплей упёрся в
        # лимит событий. Свёртки месяца полные — ими и закрываем кассу.
        from archive_restore import window_facts_from_cells
        cells = [c for c in month_cells(now)
                 if c[0] + 3600 > now - window_sec and c[0] <= now]
        arch = window_facts_from_cells(cells)
        if float(arch.get("total_usd") or 0) > float(snap.get("total_usd") or 0):
            snap["total_usd"] = arch["total_usd"]
            snap["longs_usd"] = arch["longs_usd"]
            snap["shorts_usd"] = arch["shorts_usd"]
            snap["count"] = arch["count"]
            if arch.get("top_coins"):
                snap["top_coins"] = arch["top_coins"]
            if arch.get("biggest"):
                snap["biggest"] = arch["biggest"]
            snap["totals_source"] = "archive"
        if arch.get("exchanges") and not snap.get("exchanges"):
            snap["exchanges"] = arch["exchanges"]
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
    # куча старта (модули, справочники, загруженная история) дальше не меняется:
    # убираем её из поколений, чтобы сборки мусора не перебирали гигабайт
    freeze_gc_heap()
    if not FAST_JSON:
        # Без orjson приложение работает, но сериализация свечей/статистики и
        # WS-кадров остаётся на медленном stdlib json — на одном воркере это
        # заметная доля CPU, поэтому отсутствие говорим вслух.
        log.warning("orjson не установлен — ответы и WS-кадры сериализует "
                    "стандартный json (в разы медленнее): "
                    "pip install -r requirements.txt")
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
            # Стенд режет часы в момент вставки. Лимит месяца ставим ДО реплея,
            # иначе add_liq оставит 12 часов и дайджест после рестарта опустеет.
            OI.keep = OI_KEEP_MIN * 60
            BOARD.keep_hours = max(BOARD.keep_hours, int(HISTORY_TTL_HOURS))
            SLOTS.keep_hours = max(
                SLOTS.keep_hours,
                int(HISTORY_TTL_HOURS * HOUR / SLOT_SEC) + 8)
            for ev in loaded:
                LIQUIDATIONS.append(ev)
                BOARD.add_liq(ev)
                SLOTS.add_liq(ev)
                FLOWS.add_liq(ev)
            try:
                filled = await asyncio.to_thread(restore_boards_from_archive)
                if filled:
                    log.info("Стенд дополнен часовыми свёртками архива: %d", filled)
            except Exception as e:               # noqa: BLE001
                log.debug("стенд из архива: %s", e)
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
    # Если диска не было, реплей выше не выполнился — лимит всё равно месяц,
    # чтобы живые события не обрезались через 12 часов.
    OI.keep = OI_KEEP_MIN * 60
    BOARD.keep_hours = max(BOARD.keep_hours, int(HISTORY_TTL_HOURS))
    SLOTS.keep_hours = max(SLOTS.keep_hours, int(HISTORY_TTL_HOURS * HOUR / SLOT_SEC) + 8)

    # Профиль «объём по ценам» — та же месячная глубина, что и у остальной
    # истории: он нужен слою ликвидаций, чтобы понять, где стояли входы.
    try:
        loaded_vp = await asyncio.to_thread(VP.load)
        if loaded_vp:
            log.info("Профиль объёма восстановлен: монет %d", loaded_vp)
    except Exception as e:  # noqa: BLE001
        log.debug("профиль объёма не загрузился: %s", e)

    global feed
    feed = MarketFeed(on_liquidation=on_liquidation,
                      on_price=on_price,
                      on_trade=on_trade,
                      symbols_limit=SYMBOLS_LIMIT,
                      exchanges=EXCHANGES,
                      tick_sources=TICK_SOURCES)
    # Фоновая загрузка истории свечей (её ставит /api/klines, не блокируя
    # запрос) сообщает сюда: дошиваем OI, пишем в кэш отдач и догоняем
    # зрителей графика кадром candles, если до этого они смотрели заготовку.
    feed.on_candles = _on_candles_ready
    await feed.start()
    sync_hot_symbols()      # чтобы тики пошли сразу, не дожидаясь клиента
    # Свечи как запасная цена входа: если профиля объёма по монете ещё нет,
    # уровни считаются по типичной цене свечей, а не остаются без входа.
    LEVELS.klines = feed.fetch_klines

    # 📖 Стакан: L2-опрос только для монет, на которые смотрят график или
    # которые включены в подписке кабинета. В демо walls рисуются синтетикой.
    global book_feed_inst
    book_feed_inst = BookFeed(BOOK_DIR or None, demo=DEMO_MODE)
    book_feed_inst.sub_symbols_fn = book_sub_symbols

    # Диагностика «чем занят воркер» — включается переменными, по умолчанию нет
    _enable_asyncio_debug()
    loop_trace_start()
    tasks = [
        asyncio.create_task(liq_event_worker(), name="liq-worker"),
        asyncio.create_task(book_feed_inst.run(), name="book"),
        asyncio.create_task(book_alert_loop(), name="book-alerts"),
        asyncio.create_task(oi_history_task(), name="oi-history"),
        asyncio.create_task(liquidation_broadcaster(), name="liq-broadcast"),
        asyncio.create_task(flow_broadcaster(), name="flow-broadcast"),
        asyncio.create_task(flow_oi_task(), name="flow-oi"),
        asyncio.create_task(history_task(), name="history"),
        asyncio.create_task(pump_loop(), name="pumps"),
        asyncio.create_task(price_broadcaster(), name="price-broadcast"),
        asyncio.create_task(stats_broadcaster(), name="stats-broadcast"),
        asyncio.create_task(kline_refresher(), name="kline-refresh"),
        asyncio.create_task(loop_lag_watchdog(), name="loop-lag"),
        asyncio.create_task(hot_symbols_watcher(), name="hot-symbols"),
        asyncio.create_task(liq_levels_task(), name="liq-levels"),
        asyncio.create_task(alert_loop(), name="alerts"),
        asyncio.create_task(levels_signal_loop(), name="levels-signal"),
        asyncio.create_task(corr_alert_loop(), name="corr-alerts"),
    ]
    # Дневной дайджест: вечерний выпуск в оба канала и в архив на сайте
    digest_sched = DigestScheduler(hour=DIGEST_HOUR, minute=DIGEST_MINUTE,
                                   jitter_min=DIGEST_JITTER_MIN,
                                   enabled=DIGEST_SCHED)
    app.state.digest_scheduler = digest_sched
    tasks.append(asyncio.create_task(api_digest.scheduler_loop(digest_sched),
                                     name="digest"))
    # 📰 Статьи: публикация на сайте по расписанию админа (в каналы — кнопкой)
    tasks.append(asyncio.create_task(api_articles.scheduler_loop(),
                                     name="articles"))
    # 📣 Реклама: отправка по выбранному времени и автоудаление по сроку
    tasks.append(asyncio.create_task(ads_mod.scheduler_loop(ad_service),
                                     name="ads"))    # 🔒 чат: TG-напоминания о безответных личных + чистка истории
    tasks.append(asyncio.create_task(chat_notify_loop(), name="chat-notify"))
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

    async def wal_checkpoint_loop():
        # Долгий читатель может держать WAL и растить accounts.db-wal без
        # предела. Периодический TRUNCATE отдаёт место, не блокируя запись.
        while True:
            await asyncio.sleep(10 * 60)
            try:
                await asyncio.to_thread(account_store.wal_checkpoint)
            except Exception as exc:  # noqa: BLE001
                log.debug("wal checkpoint: %s", exc)
    tasks.append(asyncio.create_task(wal_checkpoint_loop(), name="wal-checkpoint"))

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
        loop_trace_stop()
        stop_async_logging()          # дописать очередь в журнал перед выходом
        for t in tasks:
            t.cancel()
        try:
            await asyncio.to_thread(HIST.flush, True)
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.to_thread(account_store.wal_checkpoint)
        except Exception:  # noqa: BLE001
            pass
        await tg_bot.stop()
        await feed.stop()


def _cors_origins() -> List[str]:
    """Свои origin. Звёздочка вместе с credentials отражает любой сайт."""
    port = (os.getenv("PORT", "8000") or "8000").strip() or "8000"
    raw = [
        PUBLIC_URL,
        seo_pages.SITE_URL,
        "https://liqscope.online",
        "https://www.liqscope.online",
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    ]
    extra = os.getenv("LIQSCOPE_CORS_ORIGINS", "")
    raw.extend(x.strip() for x in extra.replace(";", ",").split(","))
    out: List[str] = []
    for item in raw:
        origin = (item or "").strip().rstrip("/")
        if not origin or origin == "*" or origin in out:
            continue
        out.append(origin)
    return out


CORS_ORIGINS = _cors_origins()
# Тело запроса, которое процесс согласен прочитать целиком. nginx режет 16m,
# прямой uvicorn без этого лимита читал бы тело до проверок без потолка.
MAX_BODY_BYTES = max(64 * 1024, int(os.getenv(
    "LIQSCOPE_MAX_BODY_BYTES", str(16 * 1024 * 1024))))


def _want_hsts(scope) -> bool:
    if os.getenv("LIQSCOPE_HSTS", "").strip().lower() in ("0", "false", "no"):
        return False
    if os.getenv("LIQSCOPE_COOKIE_SECURE", "").strip().lower() in (
            "1", "true", "yes"):
        return True
    headers = {}
    for key, val in scope.get("headers") or []:
        try:
            headers[key.decode("latin1").lower()] = val.decode("latin1").lower()
        except Exception:  # noqa: BLE001
            continue
    return scope.get("scheme") == "https" or headers.get("x-forwarded-proto") == "https"


_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    # SAMEORIGIN / 'self': чужой сайт встроить терминал не может, а свои
    # дополнительные сингл-графики открываются рядом через iframe того же origin.
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"content-security-policy", b"frame-ancestors 'self'"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)


class SecurityHeadersMiddleware:
    """nosniff, frame-ancestors, Referrer-Policy. Полный CSP — отдельно:
    страницы живут на инлайновых скриптах, nonce ещё не разведён.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hsts = _want_hsts(scope)

        async def send_wrap(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _ in headers}
                extra = list(_SECURITY_HEADERS)
                if hsts and b"strict-transport-security" not in have:
                    extra.append((b"strict-transport-security",
                                  b"max-age=31536000; includeSubDomains"))
                for key, val in extra:
                    if key not in have:
                        headers.append((key, val))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrap)


class MaxBodyMiddleware:
    """Не отдаём приложению тело больше MAX_BODY_BYTES.

    Content-Length выше потолка отсекается сразу. Чанки без длины читаются
    только до потолка, дальше — 413, а не неограниченный request.body().
    """

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        headers = {}
        for key, val in scope.get("headers") or []:
            try:
                headers[key.decode("latin1").lower()] = val.decode("latin1")
            except Exception:  # noqa: BLE001
                continue
        claimed = headers.get("content-length") or ""
        if claimed.isdigit() and int(claimed) > self.max_bytes:
            await _reject_too_large(send)
            return
        buffered = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                buffered.append(message)
                break
            chunk = message.get("body") or b""
            total += len(chunk)
            if total > self.max_bytes:
                await _reject_too_large(send)
                return
            buffered.append(message)
            if not message.get("more_body"):
                break

        async def replay():
            if buffered:
                return buffered.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)


async def _reject_too_large(send) -> None:
    body = b'{"ok":false,"error":"too_large"}'
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class MetricsMiddleware:
    """Считает HTTP-запросы и задержки для /api/metrics.

    Самый внешний слой: видит и 429 лимитера, и 413 резака тела.
    Задержка — до отправки заголовков ответа (время работы приложения).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            if (scope.get("path") or "") == "/ws":
                _metrics_ws_connect()
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()

        async def send_wrap(message):
            if message["type"] == "http.response.start":
                try:
                    _metrics_http(int(message.get("status", 0)),
                                  time.perf_counter() - t0)
                except Exception:  # noqa: BLE001 — счётчик не ломает ответ
                    pass
            await send(message)

        await self.app(scope, receive, send_wrap)


# Лимиты приложения поверх nginx: nginx режет флуд, но не различает гостя
# и залогиненного. Залогиненный шлёт больше (чат, кабинет, сигналы).
# Выключатель на всякий случай: LIQSCOPE_RATE_LIMIT=0.
_RATE_LIMIT_ON = os.getenv("LIQSCOPE_RATE_LIMIT", "1").strip() not in (
    "0", "false", "no", "off")
_WS_RATE = RateLimiter(5, 60)          # /ws: 5 подключений/минуту с IP
_WS_RATE_USER = RateLimiter(30, 60)   # ... залогиненному — 30
_MUT_RATE = RateLimiter(60, 10 * 60)  # мутирующие POST анонима
_MUT_RATE_USER = RateLimiter(600, 10 * 60)  # ... залогиненного
_RATE_EXEMPT = frozenset({"/api/health", "/api/metrics"})


def _scope_ip(scope) -> str:
    """IP клиента из ASGI-scope. Та же логика, что _request_ip: заголовкам
    верим, только если соединение пришло от доверенного прокси."""
    client = scope.get("client") or ()
    host = (client[0] if client else "") or ""
    if host and _is_trusted_proxy(host):
        xff = ""
        xri = ""
        for key, val in scope.get("headers") or []:
            try:
                name = key.decode("latin1").lower()
            except Exception:  # noqa: BLE001
                continue
            if name == "x-forwarded-for" and not xff:
                xff = val.decode("latin1")
            elif name == "x-real-ip" and not xri:
                xri = val.decode("latin1")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        if xri.strip():
            return xri.strip()
    return host or "0"


def _scope_sid(scope) -> str:
    """Сырой liqscope_sid из Cookie. Льготу даёт только _session_valid."""
    for key, val in scope.get("headers") or []:
        try:
            if key.decode("latin1").lower() != "cookie":
                continue
            raw = val.decode("latin1")
        except Exception:  # noqa: BLE001
            continue
        for part in raw.split(";"):
            name, _, value = part.partition("=")
            if name.strip() == COOKIE_SID and value.strip():
                return value.strip()[:128]
    return ""


_SID_VALID_CACHE = web_cache.TTLCache(ttl=30.0, maxsize=2048)


async def _session_valid(sid: str) -> bool:
    """Живая ли сессия liqscope_sid в account_store (не истекла, не бан).

    Сам по себе непустой cookie ничего не доказывает: льготный бакет
    лимитера выдаём только после этой проверки. Результат кэшируем
    на 30 с — сессии меняются редко, а проверка дёргается на каждый
    мутирующий POST и каждый WS-handshake. Ошибка стора = False:
    падаем в строгий anon-бакет, а не в льготный.
    """
    if not sid:
        return False
    hit = _SID_VALID_CACHE.get(sid)
    if hit is not None:
        return bool(hit)
    try:
        found = await asyncio.to_thread(account_store.user_by_session, sid)
        ok = found is not None
    except Exception:  # noqa: BLE001
        ok = False
    _SID_VALID_CACHE.set(sid, ok)
    return ok


class RateLimitMiddleware:
    """Режет флуд на уровне приложения: /ws и мутирующие /api/*.

    Стоит снаружи MaxBody: чужой флуд отбиваем до чтения его тела.
    Мониторинг (/api/health, /api/metrics) и чтение (GET) не трогаем.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        path = scope.get("path") or ""
        if not _RATE_LIMIT_ON:
            await self.app(scope, receive, send)
            return
        if stype == "websocket":
            if path != "/ws":
                await self.app(scope, receive, send)
                return
            sid = _scope_sid(scope)
            if sid and await _session_valid(sid):
                ok = _WS_RATE_USER.allow("ws:user:" + sid)
                who = "sid"
            else:
                ok = _WS_RATE.allow("ws:ip:" + _scope_ip(scope))
                who = "ip"
            if not ok:
                log.warning("WS отклонён лимитом (%s): %s", who, path)
                await send({"type": "websocket.close", "code": 1008,
                            "reason": "rate"})
                return
            await self.app(scope, receive, send)
            return
        if stype != "http":
            await self.app(scope, receive, send)
            return
        if (scope.get("method") not in ("POST", "PUT", "PATCH", "DELETE")
                or not path.startswith("/api/") or path in _RATE_EXEMPT):
            await self.app(scope, receive, send)
            return
        sid = _scope_sid(scope)
        if sid and await _session_valid(sid):
            ok = _MUT_RATE_USER.allow("mut:user:" + sid)
        else:
            ok = _MUT_RATE.allow("mut:ip:" + _scope_ip(scope))
        if not ok:
            body = b'{"ok":false,"error":"rate"}'
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"retry-after", b"60")],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


class CachedStaticFiles(StaticFiles):
    """Версионированные `?v=` можно держать год, остальное — час.

    Раньше статика отдавалась только с ETag: браузер ревалидировал каждый
    файл на каждой странице. Неизменённые картинки без `?v=` не помечаем
    immutable, иначе правка логотипа не доедет до гостя.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if getattr(response, "status_code", 0) != 200:
            return response
        query = (scope.get("query_string") or b"").decode("latin1", "replace")
        if re_v(query):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        return response


def re_v(query: str) -> bool:
    return any(part.startswith("v=") and len(part) > 2 for part in query.split("&"))


app = FastAPI(title="LiqScope — Live Crypto Liquidation Terminal",
              version="4.1.0", lifespan=lifespan,
              # orjson вместо stdlib json: на одном воркере сериализация
              # свечей/статистики/ленты — заметная доля CPU (см. PERF_NOTES).
              default_response_class=_DEFAULT_RESPONSE_CLASS)

# Последний add_middleware — внешний. Снаружи внутрь: считаем запросы,
# режем флуд (до чтения тела), режем тело, жмём ответ, заголовки, CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
# Сжатие ответов. Уровень 9 (default starlette) — самый медленный, а на одном
# воркере каждый миллисекунда сжатия умножается на RPS: замер на бою 29.09.2026
# показал gzip.py:_compress_body преобладающим стеком в окне паузы при 100 rps
# мимо nginx. Уровень 1 жмёт почти так же (на JSON разница единицы процентов),
# но в разы дешевле. Основной gzip в бою всё равно делает nginx
# (gzip_proxied any, gzip_comp_level 5), приложению остаются прямые заходы.
GZIP_LEVEL = min(9, max(1, int(os.getenv("LIQSCOPE_GZIP_LEVEL", "1") or 1)))
GZIP_MIN_SIZE = max(0, int(os.getenv("LIQSCOPE_GZIP_MIN_SIZE", "500") or 500))
# starlette жмёт тело прямо в event loop, пока оно короче thread_minimum_size
# (по умолчанию 128 КиБ) — на бою стек gzip.py:214:_compress_body <- :209:
# apply_compression это ровно тот inline-путь. В потоке zlib отпускает GIL,
# поэтому сжатие уходит на другие ядра и не держит воркер.
GZIP_THREAD_MIN_SIZE = max(0, int(os.getenv("LIQSCOPE_GZIP_THREAD_MIN_SIZE",
                                            str(32 * 1024)) or 32 * 1024))
GZIP_APPLIED: Dict[str, object] = {}
try:
    from starlette.middleware.gzip import GZipMiddleware
    # add_middleware не создаёт объект, поэтому TypeError за неверный параметр
    # прилетел бы уже на старте приложения — смотрим сигнатуру заранее
    _params = inspect.signature(GZipMiddleware.__init__).parameters
    _kw: Dict[str, object] = {"minimum_size": GZIP_MIN_SIZE}
    if "compresslevel" in _params:
        _kw["compresslevel"] = GZIP_LEVEL
    if "thread_minimum_size" in _params:
        _kw["thread_minimum_size"] = GZIP_THREAD_MIN_SIZE
    app.add_middleware(GZipMiddleware, **_kw)
    GZIP_APPLIED = dict(_kw)
except Exception as e:  # noqa: BLE001 — сжатие не должно ронять процесс
    log.warning("GZipMiddleware недоступен — ответы уйдут без сжатия: %s", e)
app.add_middleware(MaxBodyMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(MetricsMiddleware)

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
account_ctx.book_snapshot_fn = book_snapshot
account_ctx.symbols_fn = lambda: list((feed.symbols if feed else [])[:40])
register_account_routes(app)

# 📖 Стакан: снимок для графика, лента истории и диагностика опроса бирж.
register_book_routes(app, lambda: book_feed_inst)

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
_archive_hide = archive_hide.ArchiveHide(
    os.path.join(HERE, "data", "archive_hidden.json"))
digest_ctx.hide = _archive_hide
digest_ctx.liqs_fn = lambda: list(LIQUIDATIONS)
digest_ctx.symbols_fn = lambda: list(feed.symbols if feed else [])
# Дайджест публикуется в канал: ему нужны настоящие свечи, а не заготовка от
# последней цены. Это фоновая сборка (не запрос пользователя), поэтому force.
digest_ctx.candles_fn = lambda sym, tf: get_candles(sym, tf, force=True)
digest_ctx.hours_fn = lambda since, until: HIST.hours_range(since, until)
digest_ctx.events_fn = lambda since, until: HIST.query(
    since, until, None, 0.0, 20000, False)
digest_ctx.archive_days_fn = lambda have: archive_restore.digest_records_from_cells(
    month_cells(), have)
digest_ctx.oi_fn = oi_payload
digest_ctx.ai_fn = digest_ai
digest_ctx.publish_fn = tg_bot.publish_daily_digest
# Удаление старых выпусков: админка снимает их с сайта и из каналов, а id
# сообщений знает только бот — он помнит их после каждой публикации.
digest_ctx.bot = tg_bot
digest_ctx.sent_ids_fn = lambda: dict(getattr(tg_bot, "channel_sent", {}) or {})
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
    # materialize photo for web — копия в data/hourly_photos, чтобы не потерялась при удалении исходника
    photo_info = {}
    if img and os.path.isfile(img):
        try:
            from hourly_posts import materialize_hourly_photo
            # id уже известен
            pid = hourly_id(now)
            mat = materialize_hourly_photo(img, pid)
            # если копия удалась — используем её, иначе оригинал
            use_path = mat or img
            photo_info = cover_info(use_path, account_store)
            # сохраняем и оригинал для отладки, и материализованный путь
            if mat:
                photo_info["materialized"] = mat
                photo_info["original"] = img
        except Exception as e:
            log.debug("hourly photo materialize: %s", e)
            photo_info = cover_info(img, account_store)
        rec["photo"] = photo_info
    # каноническая запись для сайта — полный контент как в TG, плюс html/preview/symbols
    try:
        from hourly_posts import tg_html as _tg_html, plain_text as _plain
        html_full = {}
        preview = {}
        for lang_key, txt in texts.items():
            try:
                html_full[lang_key] = _tg_html(txt)
            except Exception:
                html_full[lang_key] = txt
            try:
                preview[lang_key] = _plain(txt)[:400]
            except Exception:
                preview[lang_key] = txt[:400]
        rec["html_full"] = html_full
        rec["preview"] = preview
        rec["content_full"] = dict(texts)  # полный текст
        try:
            top_coins = [c.get("symbol") for c in (snap.get("top_coins") or []) if c.get("symbol")]
            rec["symbols"] = top_coins[:10]
            rec["tags"] = ["liq", "cvd", "oi"]
        except Exception:
            pass
        rec["title"] = f"Сводка {rec['id']}"
    except Exception as e:
        log.debug("hourly canonical enrich: %s", e)
    return hourly_ctx.store.add(rec)


hourly_ctx.store = HourlyStore(HOURLY_FILE, keep=HOURLY_KEEP)
hourly_ctx.hide = _archive_hide
hourly_ctx.bot = tg_bot
hourly_ctx.collect_fn = collect_hourly_post
hourly_ctx.archive_fn = lambda: archive_restore.hourly_posts_from_cells(month_cells())
hourly_ctx.public_url = PUBLIC_URL
# Посты раздела выходят в английском канале — на странице ссылка на него
hourly_ctx.channel_url_fn = tg_bot.channel_url_en
register_hourly_routes(app)
# Бот складывает в этот же архив каждый пост, который реально ушёл в канал
tg_bot.hourly_store = hourly_ctx.store

# 📰 Статьи: раздел сайта, который наполняет админ. Английскую версию делает
# ИИ (перевод), а в каналы версии уходят по кнопке — русская в русский
# канал, английская в английский.
articles_ctx.store = ArticleStore(ARTICLES_FILE, keep=ARTICLES_KEEP)
articles_ctx.photo_dir = ARTICLES_DIR
articles_ctx.public_url = PUBLIC_URL
articles_ctx.bot = tg_bot
# ИИ для статей — тот же писатель, что делает шапки и дайджест: у него уже
# есть перебор сервисов, ключей и моделей, поэтому перевод просто идёт по той
# же цепи. Ключей нет — перевода нет, и статья ждёт ручной английской версии.
_articles_ai = getattr(tg_bot, "ai", None)
articles_ctx.ai_status_fn = (
    (lambda: _articles_ai.status()) if _articles_ai else
    (lambda: {"enabled": False, "providers": [], "last": {},
              "hint": "Ключей ИИ нет: переведите статью вручную."}))
if _articles_ai is not None:
    async def translate_article(text: str):
        """Перевод статьи RU → EN встроенным ИИ (None — ИИ не ответил)."""
        return await _articles_ai.translate(text, target="en", src="ru")
    articles_ctx.ai_fn = translate_article
register_article_routes(app)

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

# 🎯 Уровни ликвидаций: API, настройки и разбор в админке. Данные движка —
# те же, что у слоёв: OI, профиль объёма, перевес сторон, риск-лимиты.
register_liq_level_routes(app, get_engine=lambda: LEVELS,
                          get_feed=lambda: feed, store=account_store,
                          snapshot=lambda: LEVELS_SNAP)

# 💬 Мини-чат терминала: 3 дня истории, пишет любой зарегистрированный
terminal_chat_mod.ctx.store = account_store
terminal_chat_mod.ctx.secret = SECRET
register_terminal_chat_routes(app, hub=hub)

# 🔒 Приватные диалоги: та же база и тот же hub, плюс бот для напоминаний
private_chat_mod.ctx.store = account_store
private_chat_mod.ctx.bot = tg_bot
private_chat_mod.ctx.hub = hub
private_chat_mod.ctx.public_url = PUBLIC_URL
register_private_chat_routes(app)

# 🆘 Поддержка: персональный тред у каждого (и у гостя по временному нику).
# Модуль был написан, но не подключён: /api/chat/support отвечал 404, вкладка
# «Поддержка» в чате не работала и напоминания в Telegram не уходили.
support_chat_mod.ctx.store = account_store
support_chat_mod.ctx.bot = tg_bot
support_chat_mod.ctx.hub = hub
support_chat_mod.ctx.public_url = PUBLIC_URL
register_support_chat_routes(app, hub=hub)

# 🔔 Сервисы: личная лента сигналов в чате кабинета (алерты, корреляции,
# сторож монет, стены стакана). Модуль тоже был написан, но не подключён:
# /api/chat/services отвечал 404, поэтому вкладка «Сервисы» была пустой даже
# тогда, когда сигнал приходил в Telegram.
service_chat_mod.ctx.store = account_store
service_chat_mod.ctx.hub = hub
register_service_chat_routes(app, hub=hub)


async def push_service_message(user_id: int, kind: str, text: str,
                               meta: Optional[dict] = None) -> None:
    """Сигнал сервиса — в личную вкладку «Сервисы» чата на сайте.

    Копия того же сигнала, что уходит в Telegram: в боте он одним письмом,
    здесь — строкой в ленте (30 дней). Ошибка доставки не должна валить
    рассылку — сигнал уже отправлен в Telegram.
    """
    try:
        await service_chat_mod.broadcast_service_user(int(user_id), kind, text,
                                                      meta or {})
    except Exception as e:  # noqa: BLE001
        log.debug("service chat %s/%s: %s", kind, user_id, e)


# 💬 Комментарии к дайджесту и сводке по часам: читают все, пишут зарегистрированные
content_comments_mod.ctx.store = account_store
content_comments_mod.ctx.secret = SECRET
register_content_comment_routes(app)


@app.get("/api/symbols")
async def api_symbols():
    # Кэш на 1.5 сек: WS init не должен сканировать 60k событий на каждый коннект
    now = time.time()
    cached = _SYMBOLS_CACHE.get("data")
    if cached and now - _SYMBOLS_CACHE.get("at", 0) < _SYMBOLS_CACHE_TTL:
        return cached
    symbols = feed.symbols if feed else []
    prices = feed.prices if feed else {}
    meta = feed.symbol_meta if feed else {}

    liq24: Dict[str, float] = {}
    # Оптимизация: один проход по LIQUIDATIONS, только 24ч окно
    cutoff = now - 86400
    for x in LIQUIDATIONS:
        if x["timestamp"] >= cutoff:
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
    data = {
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
    _SYMBOLS_CACHE["at"] = now
    _SYMBOLS_CACHE["data"] = data
    return data


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


def _request_ip(request: Request) -> str:
    client_host = request.client.host if request.client else ""
    # Доверяем XFF только если запрос пришёл от доверенного прокси (обычно локальный nginx)
    if client_host and _is_trusted_proxy(client_host):
        xff = request.headers.get("x-forwarded-for") or ""
        if xff:
            # Берём первый IP из цепочки — это оригинальный клиент, но только если прокси доверенный
            first = xff.split(",")[0].strip()
            if first:
                return first
        # Также поддерживаем X-Real-IP от nginx
        xri = request.headers.get("x-real-ip") or ""
        if xri:
            return xri.strip() or client_host
    return client_host or "0"


# Добавление монеты — единственный публичный мутирующий роут. 20 попыток
# на IP за 10 минут: человеку хватает, флуду списка — нет.
_SYMBOL_ADD_RATE = RateLimiter(20, 10 * 60)


# Сколько монет может добавить ОДИН аккаунт (значение живёт в market_feed,
# чтобы кап был один на фид и на роут). Аноним — ни одной новой: без сессии
# счётчик некуда записать, а общий список, память и биржевые подписки общие.
# Админ — без счёта.
def _user_symbol_cap() -> int:
    return int(getattr(market_feed, "USER_SYMBOL_CAP", 5))


def _live_symbol_quota(user_id: int, cap: int) -> dict:
    """Квота аккаунта, сверенная с тем, что реально лежит в терминале.

    Счётчик живёт в базе и переживает рестарт — но ``custom_symbols`` фида
    живёт в памяти процесса, поэтому после перезапуска сервиса добавленные
    монеты исчезают, а строки в базе остаются. Без сверки человек навсегда
    потратил бы свои 5 слотов на монеты, которых в терминале уже нет (и квота
    превратилась бы в пожизненный лимит «5 монет на аккаунт»).

    Поэтому строки, чьей пары нет в пользовательском списке фида,
    освобождаются: квота считает только то, что прямо сейчас занимает подписки
    WS, память процесса и биржевые лимиты. Монета, которая осталась в списке,
    слот держит; та же пара, добавленная заново, слот не удваивает
    (``PRIMARY KEY (user_id, symbol)``).
    """
    quota = account_store.user_symbol_quota(user_id, cap)
    if feed is None or not quota.get("symbols"):
        return quota
    live = set(getattr(feed, "custom_symbols", ()) or ())
    stale = [s for s in quota["symbols"] if s not in live]
    if not stale:
        return quota
    for sym in stale:
        account_store.remove_user_symbol(user_id, sym)
    log.info("квота монет user=%s: освобождено %d (%s) — пар нет в списке терминала",
             user_id, len(stale), ", ".join(stale[:6]))
    return account_store.user_symbol_quota(user_id, cap)


@app.post("/api/symbols/add")
async def api_symbol_add(request: Request,
                         symbol: str = Query(..., max_length=40),
                         force: bool = Query(False)):
    """Добавить монету в общий список терминала.

    Лимиты (защита биржевых подписок и памяти процесса):

    * аноним — новую монету добавить нельзя (``error: "auth"``, 401). Уже
      лежащую в списке пару открыть можно: она ничего не прибавляет;
    * аккаунт — не больше ``USER_SYMBOL_CAP`` (5) монет; счётчик лежит в базе
      (``accounts.user_symbols``) и сверяется со списком фида: слот держит
      только та пара, которая реально занимает подписки WS и память
      (``_live_symbol_quota``). Поэтому квоту не обходят ни повторным
      добавлением той же монеты, ни рестартом сервиса — после рестарта список
      монет пуст, значит и слоты свободны, а не потрачены навсегда;
    * админ — без ограничений;
    * глобальный кап памяти ``MAX_CUSTOM_SYMBOLS`` (120) проверяет сам фид.
    """
    if not _SYMBOL_ADD_RATE.allow("add:" + _request_ip(request)):
        return JSONResponse({"added": False, "found": False, "error": "rate",
                             "symbol": "", "message": "слишком часто"},
                            status_code=429)
    if not feed:
        return JSONResponse({"added": False, "found": False,
                             "error": "сервер ещё не готов"}, status_code=503)
    # Сессия — это чтение SQLite, поэтому в потоке: единственный воркер не
    # должен стоять на диске, пока остальные ждут ответ.
    user = await asyncio.to_thread(current_user, request)
    is_admin = bool(user and user.get("is_admin"))
    # force обходит каталог бирж и кладёт фиктивную пару в общий список.
    # Это только для админа: иначе аноним подсовывает любое имя всем клиентам.
    if force:
        if not user or not user.get("is_admin"):
            return JSONResponse(
                {"added": False, "found": False, "error": "admin",
                 "symbol": "", "message": "force только для админа"},
                status_code=403)

    cap = -1 if is_admin else _user_symbol_cap()     # -1 = без счёта (админ)
    quota: Optional[dict] = None
    if not is_admin and user is not None and cap >= 0:
        try:
            quota = await asyncio.to_thread(_live_symbol_quota,
                                            int(user["id"]), cap)
        except Exception as e:                       # noqa: BLE001
            log.warning("квота монет user=%s: %s", user.get("id"), e)
            quota = None
        if quota and quota["used"] >= cap:
            return JSONResponse(
                {"added": False, "found": False, "error": "quota", "symbol": "",
                 "limit": cap, "used": quota["used"], "left": 0,
                 "symbols": quota["symbols"],
                 "message": f"Лимит {cap} монет на аккаунт исчерпан"},
                status_code=409)

    res = await feed.add_symbol(
        symbol, force=force, guest=user is None,
        owner="" if user is None else ("admin" if is_admin else f"u:{int(user['id'])}"),
        owner_cap=cap)
    if res.get("error") == "invalid":
        return JSONResponse(res, status_code=400)
    if res.get("error") == "auth":
        return JSONResponse(res, status_code=401)
    if res.get("error") == "quota":
        return JSONResponse(res, status_code=409)
    if res.get("error") == "full":
        return JSONResponse(res, status_code=409)
    if res.get("error") == "rate":
        return JSONResponse(res, status_code=429)
    if not res.get("found") and not res.get("added"):
        return JSONResponse(res, status_code=404)
    if res.get("added") and res.get("quota_charged") and user is not None and not is_admin:
        # Счётчик аккаунта — в базу: in-memory custom_owners переживает только
        # до рестарта, а квота должна считаться честно и после него.
        try:
            saved = await asyncio.to_thread(account_store.add_user_symbol,
                                            int(user["id"]), res.get("symbol") or "")
            res["quota_used"] = int(saved.get("count") or 0)
        except Exception as e:                       # noqa: BLE001
            log.warning("счётчик монет user=%s: %s", user.get("id"), e)
    if cap >= 0 and user is not None:
        used = res.get("quota_used")
        if used is None and quota is not None:
            used = int(quota["used"]) + (1 if res.get("quota_charged") else 0)
        if used is not None:
            res["limit"], res["used"], res["left"] = cap, int(used), max(0, cap - int(used))
    res.pop("quota_charged", None)
    res["details"] = (_enrich_symbol_rows([res.get("details") or {}])[0]
                      if res.get("details") else {})
    return res


@app.get("/api/klines")
async def api_klines(symbol: str = Query("BTC_USDT"), timeframe: int = Query(5)):
    """Свечи графика: читатель локального буфера, а не поход на биржу.

    Любую монету можно открыть на графике, даже если её нет в дефолтном
    топ-списке. Но запрос пользователя здесь НИКОГДА не ждёт Binance/Bybit/OKX:
    ``get_candles`` отдаёт то, что уже лежит в памяти (кэш отдач → буфер фида
    ``market_feed.get_candles_cached`` → заготовка от последней цены), а
    загрузку истории новых монет ставит фоновой задачей
    (``asyncio.create_task``). Иначе каждый зритель графика — это свой
    синхронный запрос к бирже: 50+ пользователей дают лаг единственного
    воркера и 429/418 (бан IP всего сервера).

    Клиенту видно, что данные догоняющие: ``pending``/``stale`` и ``age_sec``.
    Настоящие свечи приходят кадром WS ``candles`` сразу, как фон их догрузил
    (embed-графики дополнительно опрашивают ручку раз в 15 с).
    """
    symbol = canon(symbol)
    tf = parse_tf(timeframe) or 5
    entry = await get_candles(symbol, tf)
    ts = float(entry.get("ts") or 0.0)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "source": entry.get("source") or "unavailable",
        "candles": entry.get("candles") or [],
        "stale": bool(entry.get("stale")),
        "pending": bool(entry.get("pending"))
        or bool(feed is not None and feed.candles_pending(symbol, tf)),
        "age_sec": round(time.time() - ts, 1) if ts else None,
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
    cap = min(max(1, int(limit or 1)), 2000)
    res = list(LIQUIDATIONS)
    sym = canon(symbol) if symbol and symbol != "ALL" else None
    if sym:
        res = [x for x in res if x["symbol"] == sym]
    if exchange and exchange != "ALL":
        res = [x for x in res if x["exchange"] == exchange.lower()]
    if min_usd > 0:
        res = [x for x in res if x["usd"] >= min_usd]
    # Память после рестарта короче месяца. Лента добирает хвост из шардов,
    # не поднимая потолок живого запроса.
    if HISTORY_FILE and len(res) < cap:
        try:
            now = time.time()
            archived = await asyncio.to_thread(
                HIST.query, now - HISTORY_TTL_HOURS * 3600, now, sym,
                float(min_usd or 0), cap, True)
        except Exception as e:                       # noqa: BLE001
            log.debug("лента из архива: %s", e)
            archived = []
        if archived:
            by_id = {x.get("id"): x for x in res if x.get("id") is not None}
            for ev in archived:
                if exchange and exchange != "ALL" and str(ev.get("exchange") or "").lower() != exchange.lower():
                    continue
                key = ev.get("id")
                if key is not None and key not in by_id:
                    by_id[key] = ev
                    res.append(ev)
            res.sort(key=lambda e: float(e.get("timestamp") or 0))
    return {"liquidations": res[-cap:], "total": len(res)}


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


# Здоровье дёргают и мониторинг, и шапка сайта: 5 секунд кэша снимают
# повторные опросы. В тестах кэш выключен (LIQSCOPE_API_CACHE=0).
_HEALTH_CACHE = web_cache.TTLCache(ttl=5.0, maxsize=4)


@app.get("/api/health")
async def api_health():
    hit = _HEALTH_CACHE.get("h")
    if hit is not None:
        return JSONResponse(hit)
    data = {
        "status": "ok",
        "server_time": time.time(),
        "clients": len(hub.clients),
        "ws_max_clients": WS_MAX_CLIENTS,
        "rss_bytes": _rss_bytes(),
        "rss_peak_bytes": _rss_peak_bytes(),
        # паузы единственного воркера: если клиентский p95 в секундах,
        # а серверный p95 обработчика единицы мс — запросы стояли в
        # очереди цикла, пока кто-то держал его занятым
        "loop_lag_max_ms": _LOOP_LAG["max_ms"],
        "loop_lag_last_ms": _LOOP_LAG["last_ms"],
        "loop_stalls": int(_LOOP_LAG["stalls"]),
        # кто именно держал воркер (LIQSCOPE_LOOP_TRACE=1): стек «изнутри
        # наружу» — файл:строка:функция; пусто, если трассировка выключена
        "loop_trace": LOOP_TRACE,
        "loop_trace_stalls": int(_LOOP_TRACE["stalls"]),
        "loop_trace_held_ms": _LOOP_TRACE["held_ms"],
        "loop_trace_last": _LOOP_TRACE["last"],
        "asyncio_debug": bool(ASYNCIO_DEBUG),
        # сборка мусора: на большой куче именно она даёт паузы в секундах при
        # быстром обработчике, а трассировка в это время называет кадр, который
        # просто аллоцировал память (на бою так «виновником» вышел gzip)
        "gc_max_ms": _GC_STATS["max_ms"],
        "gc_last_ms": _GC_STATS["last_ms"],
        "gc_total_ms": _GC_STATS["total_ms"],
        "gc_collections": int(_GC_STATS["count"]),
        "gc_gen2_collections": int(_GC_STATS["gen2"]),
        "gc_frozen_objects": int(_GC_STATS["frozen"]),
        # журнал уходит в очередь и пишется в отдельном потоке: если journald
        # не успевает, теряются записи (счётчик), а не паузы воркера
        "log_async": _log_listener is not None,
        "log_queue_size": _LOG_QUEUE.qsize(),
        "log_queue_max": LOG_QUEUE_MAX,
        "log_dropped": int(_LOG_STATS["dropped"]),
        # кэш окна калибровки уровней: сколько событий держим в куче
        "levels_events_cache": _levels_cache_stats(),
        "wal_bytes": _wal_bytes(),
        "sqlite_ms": await _sqlite_ping_ms(),
        "uptime_sec": round(time.time() - _START_TS, 1),
        "liquidations_in_memory": len(LIQUIDATIONS),
        "demo": DEMO_MODE,
        "tg": tg_bot.poll_status(),
        "config": {
            "symbols_limit": SYMBOLS_LIMIT,
            # лимиты добавления монет и быстрый JSON — видно в мониторинге
            "max_custom_symbols": market_feed.MAX_CUSTOM_SYMBOLS,
            "user_symbol_cap": _user_symbol_cap(),
            "fast_json": FAST_JSON,
            # чем и как жмём ответы: видно, что на бою работает уровень 1 и
            # сжатие уходит в поток, а не держит event loop
            "gzip": dict(GZIP_APPLIED) if GZIP_APPLIED else None,
            "gzip_level": GZIP_LEVEL,
            "gzip_min_size": GZIP_MIN_SIZE,
            "gzip_thread_min_size": GZIP_THREAD_MIN_SIZE,
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
    _HEALTH_CACHE.set("h", data)
    return JSONResponse(data)


@app.get("/api/metrics")
async def api_metrics():
    """Метрики процесса для мониторинга: сокеты, RPS, задержки, база.

    Скорости — за последнюю минуту. Задержка — p95 времени приложения
    (до отправки заголовков) по последним 5000 запросам.
    """
    _metrics_prune()
    symbols = list(feed.symbols) if feed else []
    custom = list(feed.custom_symbols) if feed else []
    _cstats = (feed.candles_cache_stats() if feed is not None
               else {"series": 0, "inflight": 0, "cap": 0, "refetch_sec": 0.0})
    return JSONResponse({
        "ws_clients_total": len(hub.clients),
        "ws_max_clients": WS_MAX_CLIENTS,
        "ws_connects_per_sec": _rate_last_minute(_WS_CONN),
        "ws_messages_per_sec": _rate_last_minute(_WS_SEND),
        **_ws_queue_stats(),
        "http_requests_per_sec": _rate_last_minute(_HTTP_RPS),
        "http_requests_per_sec_by_status": _status_rps_last_minute(),
        "http_latency_p95_ms": _latency_p95_ms(),
        "sqlite_wal_size_bytes": _wal_bytes(),
        "sqlite_ms": await _sqlite_ping_ms(),
        "symbols_count": len(symbols),
        "custom_symbols_count": len(custom),
        "custom_symbols_cap": market_feed.MAX_CUSTOM_SYMBOLS,
        "user_symbol_cap": _user_symbol_cap(),
        # буфер истории свечей: видно, что /api/klines отвечает из памяти,
        # а на биржу ходит только фоновая загрузка
        "candles_cached_series": _cstats["series"],
        "candles_loading": _cstats["inflight"],
        "candles_cache_cap": _cstats["cap"],
        "fast_json": FAST_JSON,
        "rss_bytes": _rss_bytes(),
        "rss_peak_bytes": _rss_peak_bytes(),
        # паузы единственного воркера: если клиентский p95 в секундах,
        # а серверный p95 обработчика единицы мс — запросы стояли в
        # очереди цикла, пока кто-то держал его занятым
        "loop_lag_max_ms": _LOOP_LAG["max_ms"],
        "loop_lag_last_ms": _LOOP_LAG["last_ms"],
        "loop_stalls": int(_LOOP_LAG["stalls"]),
        # кто именно держал воркер (LIQSCOPE_LOOP_TRACE=1): стек «изнутри
        # наружу» — файл:строка:функция; пусто, если трассировка выключена
        "loop_trace": LOOP_TRACE,
        "loop_trace_stalls": int(_LOOP_TRACE["stalls"]),
        "loop_trace_held_ms": _LOOP_TRACE["held_ms"],
        "loop_trace_last": _LOOP_TRACE["last"],
        "asyncio_debug": bool(ASYNCIO_DEBUG),
        # сборка мусора: на большой куче именно она даёт паузы в секундах при
        # быстром обработчике, а трассировка в это время называет кадр, который
        # просто аллоцировал память (на бою так «виновником» вышел gzip)
        "gc_max_ms": _GC_STATS["max_ms"],
        "gc_last_ms": _GC_STATS["last_ms"],
        "gc_total_ms": _GC_STATS["total_ms"],
        "gc_collections": int(_GC_STATS["count"]),
        "gc_gen2_collections": int(_GC_STATS["gen2"]),
        "gc_frozen_objects": int(_GC_STATS["frozen"]),
        # журнал уходит в очередь и пишется в отдельном потоке: если journald
        # не успевает, теряются записи (счётчик), а не паузы воркера
        "log_async": _log_listener is not None,
        "log_queue_size": _LOG_QUEUE.qsize(),
        "log_queue_max": LOG_QUEUE_MAX,
        "log_dropped": int(_LOG_STATS["dropped"]),
        # кэш окна калибровки уровней: сколько событий держим в куче
        "levels_events_cache": _levels_cache_stats(),
        "uptime_sec": round(time.time() - _START_TS, 1),
    })


def _levels_cache_stats() -> Dict[str, int]:
    """Сколько событий калибровки уровней лежит в куче (память → паузы GC)."""
    try:
        fn = getattr(LEVELS, "events_cache_stats", None)
        return dict(fn()) if callable(fn) else {}
    except Exception:                            # noqa: BLE001
        return {}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    t_ws0 = time.monotonic() if PERF_LOG else 0.0
    await websocket.accept()
    t_accept = time.monotonic() if PERF_LOG else 0.0
    client = Client(websocket)
    if not await hub.add(client):
        await websocket.close(code=1013)
        return
    # кто это: та же сессия COOKIE_SID, что у кабинета и чата. Без куки
    # клиент остаётся «гостем» — адресные чат-события ему не придут
    try:
        _sid = websocket.cookies.get(COOKIE_SID)
        if _sid:
            _su = account_store.user_by_session(_sid)
            if _su:
                client.user_id = int(_su["id"])
                terminal_chat_mod.chat_presence_touch(client.user_id)
    except Exception as e:  # noqa: BLE001
        log.debug("ws user resolve: %s", e)
    try:
        t_stage = time.monotonic() if PERF_LOG else 0.0
        sym_data = await api_symbols() if feed else {"details": [], "custom_symbols": []}
        t_sym = time.monotonic() if PERF_LOG else 0.0
        health = health_summary()
        t_health = time.monotonic() if PERF_LOG else 0.0
        # Лимит init: раньше 200, теперь 100 — достаточно для ленты, остальное догружается REST
        # Защита от деградации: даже если LIQUIDATIONS 60k, init не разрастается
        _recent_limit = max(20, min(200, int(__import__("os").getenv("LIQSCOPE_WS_INIT_LIQ", "100"))))
        recent = list(LIQUIDATIONS)[-_recent_limit:]
        t_recent = time.monotonic() if PERF_LOG else 0.0
        stats = compute_stats()
        t_stats = time.monotonic() if PERF_LOG else 0.0
        flow = flow_snapshot()
        t_flow = time.monotonic() if PERF_LOG else 0.0
        init_payload = {
            "type": "init",
            "symbols": feed.symbols if feed else [],
            "details": sym_data["details"],
            "prices": feed.prices if feed else {},
            "exchanges": EXCHANGES,
            "custom_symbols": sym_data.get("custom_symbols", []),
            "timeframes": TF_MINUTES,
            "demo": DEMO_MODE,
            "health": health,
            "recent_liquidations": recent,
            "stats": stats,
            # Лента «ВСЕ» в CVD/OI строится не по свечам графика, а по
            # минутным потокам всех монет — отдаём их сразу, чтобы не ждать
            # первой рассылки.
            "flow": flow,
        }
        t_build = time.monotonic() if PERF_LOG else 0.0
        await client.send(init_payload)
        t_send = time.monotonic() if PERF_LOG else 0.0
        if PERF_LOG:
            import json as _json
            size = len(_json.dumps(init_payload, ensure_ascii=False))
            log.info("[perf] /ws accept=%.1fms symbols=%.1fms health=%.1fms recent=%.1fms stats=%.1fms flow=%.1fms build=%.1fms send=%.1fms total=%.1fms size=%d clients=%d",
                     (t_accept - t_ws0)*1000 if t_ws0 else 0,
                     (t_sym - t_stage)*1000 if t_stage else 0,
                     (t_health - t_sym)*1000 if t_sym else 0,
                     (t_recent - t_health)*1000 if t_health else 0,
                     (t_stats - t_recent)*1000 if t_recent else 0,
                     (t_flow - t_stats)*1000 if t_stats else 0,
                     (t_build - t_flow)*1000 if t_flow else 0,
                     (t_send - t_build)*1000 if t_build else 0,
                     (t_send - t_ws0)*1000 if t_ws0 else 0,
                     size, len(hub.clients))
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


app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")


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
    t0 = time.monotonic() if PERF_LOG else 0.0
    lang, auto = seo_pages.lang_of(request)
    t1 = time.monotonic() if PERF_LOG else 0.0
    resp = seo_pages.render(
        "index.html", lang, "/terminal", extra_head=seo_pages.jsonld("terminal", lang),
        auto=auto,
    )
    if PERF_LOG:
        dt_total = (time.monotonic() - t0) * 1000
        dt_lang = (t1 - t0) * 1000 if t0 else 0
        dt_render = (time.monotonic() - t1) * 1000 if t1 else 0
        log.info("[perf] /terminal lang=%s auto=%s lang_detect=%.1fms render=%.1fms total=%.1fms size=%d",
                 lang, auto, dt_lang, dt_render, dt_total, len(resp.body) if hasattr(resp, 'body') else 0)
    return resp


@app.get("/robots.txt")
async def robots_txt():
    """Правила обхода: служебное закрыто, карта сайта указана."""
    return seo_pages.robots_txt()


@app.get("/sitemap.xml")
async def sitemap_xml():
    """Карта сайта: основные страницы во всех языках (hreflang-альтернативы) + архив."""
    digest_items = []
    hourly_items = []
    article_items = []
    try:
        # digest_ctx и hourly_ctx живут в модулях api_digest / api_hourly —
        # берём их напрямую, чтобы не тянуть app.state
        from api_digest import ctx as dctx
        from api_hourly import ctx as hctx
        from api_articles import ctx as actx
        try:
            digest_items = list((getattr(dctx, "store", None) or {}).list() if hasattr(getattr(dctx, "store", None), "list") else [])
        except Exception:
            digest_items = []
        try:
            hourly_items = list((getattr(hctx, "store", None) or {}).list() if hasattr(getattr(hctx, "store", None), "list") else [])
        except Exception:
            hourly_items = []
        try:
            # в карту сайта идут только опубликованные статьи
            store = getattr(actx, "store", None)
            article_items = list(store.published() if store is not None else [])
        except Exception:
            article_items = []
    except Exception:
        pass
    return seo_pages.sitemap_xml(digest_items=digest_items, hourly_items=hourly_items,
                                 article_items=article_items)


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
