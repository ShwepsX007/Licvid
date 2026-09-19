"""🌍 География посетителей: страна по IP, источник перехода, центроиды.

Админке нужно знать, откуда приходят гости и как долго смотрят сайт. Страну
берём в таком порядке:

1. **Заголовок CDN** (``CF-IPCountry``, ``X-Country-Code`` и подобные) — если
   сайт стоит за Cloudflare или другим прокси, страна известна сразу и точно,
   без внешних запросов и без задержки;
2. **Кэш в базе** — тот же адрес спрашивают один раз (страна адреса меняется
   редко, IP меняется часто); справочник ``geo_cache`` живёт в той же SQLite,
   что и аккаунты;
3. **Внешний сервис** — если ни заголовка, ни кэша нет. Запрос уходит уже
   *после* ответа страницы (фоновая задача), поэтому на скорость сайта он не
   влияет: визит записывается сразу, а страна дописывается в него, когда
   сервис ответил (``set_visit_country``).

Сервисы перебираются по очереди, первый ответивший побеждает. Свой сервис
можно подставить переменной ``LIQSCOPE_GEOIP_URL`` (шаблон с ``{ip}``),
например локальный прокси к базе MaxMind; полностью выключить внешние запросы —
``LIQSCOPE_GEOIP=0``. По умолчанию пробуем бесплатные сервисы без ключей.

Сырой IP нигде не хранится: и в визитах, и в кэше лежит только HMAC-хэш
(``hash_ip``), поэтому по базе нельзя восстановить адреса гостей.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import aiohttp

import geo_countries
from accounts import hash_ip

log = logging.getLogger("liqscope.geo")

#: Сервис → шаблон запроса. Все бесплатные и без ключа; спрашиваем по очереди,
#: пока кто-нибудь не ответит: у каждого свои лимиты и свои простои.
PROVIDERS: Tuple[Tuple[str, str], ...] = (
    ("country.is", "https://api.country.is/{ip}"),
    ("ipwho.is", "https://ipwho.is/{ip}"),
    ("ipapi.co", "https://ipapi.co/{ip}/json/"),
    ("ip-api.com", "http://ip-api.com/json/{ip}?fields=status,countryCode"),
)

PROVIDER_TIMEOUT = 3.5          # секунд на один сервис
LOOKUP_BUDGET = 8.0             # секунд на все попытки вместе
CACHE_TTL = 30 * 86400.0        # страна адреса живёт месяц
MISS_TTL = 3600.0               # «страна неизвестна» — час, потом пробуем снова

#: Заголовки прокси/CDN, в которых уже лежит страна. Порядок — от точного к общему.
COUNTRY_HEADERS = ("cf-ipcountry", "x-country-code", "x-geo-country",
                   "x-vercel-ip-country", "x-appengine-country")

#: Не страна: Tor, локальная сеть, «неизвестно».
NOT_COUNTRY = {"XX", "T1", "A1", "A2", "O1", "EU", "AP", "ZZ", "00"}

#: Поисковики: переход из выдачи.
SEARCH_HOSTS = ("google.", "bing.com", "yandex.", "duckduckgo.com", "yahoo.",
                "baidu.com", "brave.com", "ecosia.org", "startpage.com",
                "seznam.cz", "naver.com", "qwant.com", "search.", "perplexity.ai",
                "chatgpt.com", "ya.ru", "rambler.ru", "m.baidu.com", "so.com")

#: Соцсети и мессенджеры: платные посты, каналы, чаты.
SOCIAL_HOSTS = ("t.me", "telegram.", "twitter.com", "x.com", "vk.com", "vk.ru",
                "facebook.com", "instagram.com", "reddit.com", "discord.",
                "youtube.com", "youtu.be", "linkedin.com", "whatsapp.com",
                "threads.net", "tiktok.com", "news.ycombinator.com", "medium.com",
                "dzen.ru", "ok.ru", "twitch.tv", "mastodon", "bsky.app")

#: Приложение (PWA, мобильный клиент) — переход не из браузера.
APP_AGENTS = ("telegram", "liqscope", "okhttp", "cfnetwork")


class Ctx:
    """Связка с сервером: база для кэша и хвост фоновых задач."""

    store = None
    secret = ""

    def __init__(self) -> None:
        self.tasks: set = set()


ctx = Ctx()


def enabled() -> bool:
    """Внешние запросы разрешены? ``LIQSCOPE_GEOIP=0`` выключает их."""
    return os.getenv("LIQSCOPE_GEOIP", "").strip().lower() not in ("0", "false", "no")


def clean_code(raw: Any) -> str:
    """Код страны из чего угодно: ``ru``/``«RU»``/``RU, FI`` → ``RU``."""
    code = str(raw or "").strip().upper().replace("'", "").replace('"', "")
    if not code:
        return ""
    code = code.split(",")[0].split(";")[0].strip()[:8]
    if len(code) != 2 or not code.isalpha():
        return ""
    return "" if code in NOT_COUNTRY else code


def header_country(headers: Any) -> str:
    """Страна из заголовков прокси/CDN — без обращения к внешним сервисам."""
    try:
        items = headers.items()
    except Exception:                                     # noqa: BLE001
        return ""
    found = {str(k).lower(): v for k, v in items}
    for name in COUNTRY_HEADERS:
        code = clean_code(found.get(name))
        if code:
            return code
    return ""


def country_name(cc: str) -> str:
    """Человеческое имя страны: берём из справочника, иначе сам код."""
    row = geo_countries.COUNTRIES.get((cc or "").upper())
    return row[0] if row else ""


def centroid(cc: str) -> Optional[Tuple[float, float]]:
    """Широта и долгота страны: точка на карте админки."""
    row = geo_countries.COUNTRIES.get((cc or "").upper())
    if not row:
        return None
    return (float(row[1]), float(row[2]))


def info(cc: str, name: str = "") -> Dict[str, Any]:
    """Карточка страны: код, имя и точка на карте (если страна нам известна)."""
    code = clean_code(cc)
    if not code:
        return {"cc": "", "name": "", "lat": None, "lon": None}
    point = centroid(code)
    return {"cc": code,
            "name": (name or country_name(code))[:60],
            "lat": point[0] if point else None,
            "lon": point[1] if point else None}


def is_public_ip(ip: str) -> bool:
    """Стоит ли вообще спрашивать страну: локальные адреса — нет."""
    try:
        addr = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_reserved
                or addr.is_link_local or addr.is_multicast or addr.is_unspecified)


def _host_of(url: str) -> str:
    """Хост из ссылки: без схемы, порта и ``www.``."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "//" in text:
        text = text.split("//", 1)[1]
    host = text.split("/", 1)[0].split("?", 1)[0].lower()
    host = host.split("@")[-1].split(":")[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    # домен без точки (и не localhost) — не ссылка, а мусор в заголовке
    if host != "localhost" and ("." not in host or " " in host):
        return ""
    return host[:60]


def classify(referer: str = "", utm: str = "", host: str = "",
             site_hosts: Iterable[str] = ()) -> Tuple[str, str]:
    """Откуда пришёл гость: ``(источник, вид)``.

    Виды: ``search`` — поисковик, ``social`` — соцсети и мессенджеры,
    ``referral`` — чужая ссылка, ``campaign`` — метка ``utm_source``,
    ``internal`` — переход внутри сайта (в таблицу источников не идёт),
    ``app`` — открыли из приложения (бот, PWA), ``direct`` — пришли напрямую.
    """
    src, kind = "", "direct"
    utm = str(utm or "").strip().lower().replace(" ", "-")[:40]
    ref_host = _host_of(referer)
    own = {_host_of(h) for h in site_hosts if _host_of(h)}
    if utm:
        return utm, "campaign"
    if not ref_host:
        return "", "direct"
    if ref_host in own or (host and ref_host == _host_of(host)):
        return "", "internal"
    for probe in SEARCH_HOSTS:
        if probe in ref_host:
            return ref_host, "search"
    for probe in SOCIAL_HOSTS:
        if probe in ref_host:
            return ref_host, "social"
    return ref_host, "referral"


def app_source(ua: str) -> bool:
    """Открыли из приложения, а не из браузера (телеграм-браузер, PWA)?"""
    low = (ua or "").lower()
    return any(mark in low for mark in APP_AGENTS)


def _code_from(payload: Any) -> str:
    """Код страны из ответа сервиса: у каждого своё имя поля."""
    if not isinstance(payload, dict):
        return ""
    for key in ("country", "country_code", "countryCode", "country_code2",
                "countryCode2", "isoCode", "iso_code", "countryCodeIso2"):
        code = clean_code(payload.get(key))
        if code:
            return code
    return ""


async def _fetch_json(url: str) -> Any:
    """Один запрос к сервису: короткий таймаут, ошибки — просто «не ответил»."""
    timeout = aiohttp.ClientTimeout(total=PROVIDER_TIMEOUT)
    headers = {"User-Agent": "LiqScope-Geo/1.0 (+https://liqscope.online)",
               "Accept": "application/json"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
        async with sess.get(url) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)


def provider_urls(ip: str) -> List[Tuple[str, str]]:
    """Очередь сервисов: свой (если задан) идёт первым."""
    custom = os.getenv("LIQSCOPE_GEOIP_URL", "").strip()
    out: List[Tuple[str, str]] = []
    if custom:
        out.append(("custom", custom))
    out.extend(PROVIDERS)
    quoted = quote(str(ip or ""), safe="")
    return [(name, url.replace("{ip}", quoted)) for name, url in out]


async def resolve(ip: str) -> Dict[str, str]:
    """Спросить страну у внешних сервисов: ``{"cc", "src"}`` (пусто — не вышло)."""
    if not enabled() or not is_public_ip(ip):
        return {}
    deadline = time.monotonic() + LOOKUP_BUDGET
    for name, url in provider_urls(ip):
        if time.monotonic() >= deadline:
            break
        try:
            payload = await _fetch_json(url)
        except asyncio.CancelledError:
            raise
        except Exception as e:                            # noqa: BLE001
            log.debug("гео: %s не ответил (%s)", name, e)
            continue
        code = _code_from(payload)
        if code:
            return {"cc": code, "name": country_name(code), "src": name}
    return {}


async def lookup(ip: str, ip_hash: str = "") -> Dict[str, Any]:
    """Страна адреса: кэш → внешний сервис. Ответ (в том числе пустой) кэшируем."""
    if not ip:
        return {"cc": "", "src": ""}
    iph = ip_hash or (hash_ip(ctx.secret, ip) if ctx.secret else "")
    if iph and ctx.store is not None:
        try:
            row = ctx.store.geo_cached(iph, CACHE_TTL)
        except Exception as e:                            # noqa: BLE001
            log.debug("гео: кэш недоступен: %s", e)
            row = None
        if row:
            return {"cc": row.get("cc") or "", "name": row.get("name") or "",
                    "src": row.get("src") or "cache", "cached": True}
    got = await resolve(ip)
    cc = got.get("cc") or ""
    name = got.get("name") or (country_name(cc) if cc else "")
    if iph and ctx.store is not None and (cc or enabled()):
        try:
            # пустой ответ помним недолго: сервис может ожить через час
            ctx.store.geo_remember(iph, cc, name, got.get("src") or "miss")
        except Exception as e:                            # noqa: BLE001
            log.debug("гео: не запомнили страну: %s", e)
    return {"cc": cc, "name": name, "src": got.get("src") or ""}


def _spawn(coro) -> None:
    """Фоновая задача с придержкой ссылки (иначе её соберёт сборщик мусора)."""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:                                  # нет цикла — не беда
        return
    ctx.tasks.add(task)
    task.add_done_callback(lambda t: (ctx.tasks.discard(t),
                                      t.exception() if not t.cancelled() else None))


async def attach_country(ip: str, ip_hash: str = "", visit_id: int = 0,
                         vid: str = "") -> Dict[str, Any]:
    """Узнать страну и дописать её в визит и/или строку присутствия.

    Вызывается фоном: страница уже отдана, гость уже посчитан — дописываем
    только географию, когда сервис ответил.
    """
    got = await lookup(ip, ip_hash)
    cc = got.get("cc") or ""
    if not cc:
        return got
    name = got.get("name") or country_name(cc)
    try:
        if ctx.store is not None:
            if visit_id:
                ctx.store.set_visit_country(visit_id, cc, name, got.get("src") or "")
            if vid:
                ctx.store.set_presence_country(vid, cc, name)
    except Exception as e:                                # noqa: BLE001
        log.debug("гео: не дописали страну: %s", e)
    return got


def schedule_country(ip: str, ip_hash: str = "", visit_id: int = 0,
                     vid: str = "", cc: str = "") -> None:
    """Поставить фоновое уточнение страны, если её ещё не знаем."""
    if cc or not ip or not enabled():
        return
    _spawn(attach_country(ip, ip_hash, visit_id, vid))


def visit_meta(request: Any, site_hosts: Iterable[str] = ()) -> Dict[str, Any]:
    """Что знаем о визите сразу, без сети: страна прокси, источник перехода.

    Заголовок CDN закрывает вопрос мгновенно; если его нет — отдаём пустую
    страну, а фоновая задача доспросит её позже (``schedule_country``).
    """
    headers = getattr(request, "headers", {}) or {}
    cc = header_country(headers)
    host = ""
    try:
        host = str(headers.get("host") or "")
    except Exception:                                     # noqa: BLE001
        host = ""
    ref = ""
    try:
        ref = str(headers.get("referer") or headers.get("referrer") or "")
    except Exception:                                     # noqa: BLE001
        ref = ""
    ua = ""
    try:
        ua = str(headers.get("user-agent") or "")
    except Exception:                                     # noqa: BLE001
        ua = ""
    utm = ""
    try:
        utm = str(request.query_params.get("utm_source") or "")
    except Exception:                                     # noqa: BLE001
        utm = ""
    src, kind = classify(ref, utm, host, site_hosts)
    if kind == "direct" and app_source(ua):
        kind = "app"
    return {"cc": cc, "name": country_name(cc), "src": "edge" if cc else "",
            "source": src, "source_kind": kind}
