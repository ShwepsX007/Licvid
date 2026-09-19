"""🌍 Откуда приходят гости, как долго смотрят и кто сейчас на сайте.

Две ручки:

    POST /api/visit/ping        — «я ещё здесь» (сердцебиение страницы)
    GET  /api/admin/geo?period= — картина для админки: точки, страны, источники

Сердцебиение шлёт ``static/presence.js`` со всех страниц: одна маленькая
запись на гостя (``first_ts`` — когда пришёл, ``ts`` — когда последний раз
подал признаки жизни). Из неё получаются и «кто онлайн» (последние 5 минут),
и «сколько времени провёл на сайте». Без JS гость всё равно попадает в
счётчики визитов — просто без времени и без точки «онлайн».

Админская ручка — только для админов и отдаёт готовые для карты координаты:
широту и долготу центра страны подставляет ``geoip`` (справочник центроидов
рядом с контурами карты, ``static/world-map.js``).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

import geoip
from accounts import COOKIE_VID

log = logging.getLogger("liqscope.geo")

#: Окно «онлайн»: столько секунд без сердцебиения гость ещё считается на сайте.
ONLINE_SEC = 300.0
#: Периоды для таблиц и точек карты: подпись → часы.
PERIODS: Dict[str, float] = {"24h": 24.0, "7d": 168.0, "30d": 720.0}
DEFAULT_PERIOD = "24h"
#: Сколько точек рисуем: больше на карте всё равно не различить.
MAX_DOTS = 600
#: Ограничение частоты сердцебиений: гостей много, а «я здесь» шлётся раз в минуту.
RATE_LIMIT = 40
RATE_WINDOW = 60.0


class Ctx:
    """Связка с сервером: база аккаунтов и признак «страна по заголовку CDN»."""

    store = None
    secret = ""
    public_url = ""


ctx = Ctx()


class _Limiter:
    """Сколько пингов с одного ключа за окно (ключ — хэш адреса)."""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self.hits: Dict[str, List[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        stamps = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(stamps) >= self.limit:
            self.hits[key] = stamps
            return False
        stamps.append(now)
        self.hits[key] = stamps
        if len(self.hits) > 5000:                 # редкая уборка памяти
            self.hits = {k: v for k, v in self.hits.items() if v}
        return True


limiter = _Limiter()


def _client_ip(request: Request) -> str:
    """Адрес гостя: за прокси — из ``X-Forwarded-For`` (как в web_account)."""
    from web_account import _client_ip as impl
    return impl(request)


def _site_hosts(request: Request) -> List[str]:
    """Свои хосты: переходы внутри сайта источником не считаются."""
    hosts = ["liqscope.online", "www.liqscope.online"]
    try:
        hosts.append(str(request.headers.get("host") or ""))
    except Exception:                                     # noqa: BLE001
        pass
    url = (ctx.public_url or "").strip()
    if url:
        hosts.append(url)
    return hosts


def _public_user(request: Request) -> Dict[str, Any]:
    """Кто гость в базе (если вошёл) — в карточке присутствия видно участника."""
    try:
        from web_account import current_user
        user = current_user(request)
    except Exception:                                     # noqa: BLE001
        return {}
    if not user:
        return {}
    from accounts import display_name
    return {"id": user.get("id"),
            "name": display_name(user) or ("#" + str(user.get("id"))),
            "admin": bool(user.get("is_admin"))}


def enrich(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Дописать каждой записи страну и точку на карте (код → центроид).

    Заодно подтягиваем имя аккаунта: в списке «кто сейчас на сайте» видно не
    только страну, но и кто это — если гость вошёл в кабинет.
    """
    names: Dict[int, str] = {}
    store = ctx.store
    for row in blocks or []:
        uid = int(row.get("user_id") or 0)
        if not uid or uid in names or store is None:
            continue
        try:
            user = store.get_user(uid) or {}
        except Exception:                                 # noqa: BLE001
            user = {}
        from accounts import display_name
        names[uid] = display_name(user) or ("#" + str(uid))
    out = []
    for row in blocks or []:
        info = geoip.info(row.get("country") or "", row.get("name") or "")
        row = dict(row)
        uid = int(row.get("user_id") or 0)
        row["country"] = info["cc"]
        row["name"] = info["name"]
        row["lat"] = info["lat"]
        row["lon"] = info["lon"]
        row["user"] = ({"id": uid, "name": names.get(uid, "#" + str(uid))} if uid else None)
        out.append(row)
    return out


def register_geo_routes(app) -> None:
    router = APIRouter()

    @router.post("/api/visit/ping")
    async def visit_ping(request: Request, payload: Dict[str, Any] = Body(default={})):
        """«Я ещё здесь»: обновляем время гостя и, если надо, доспрашиваем страну."""
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        vid = ""
        try:
            vid = str(request.cookies.get(COOKIE_VID) or "")
        except Exception:                                 # noqa: BLE001
            vid = ""
        ip = _client_ip(request)
        iph = geoip.hash_ip(ctx.secret, ip) if ctx.secret else ""
        if not limiter.allow(iph or (vid or "anon")):
            return JSONResponse({"ok": False, "error": "rate"}, status_code=429)
        if not vid:
            # Middleware выдаёт cookie гостя до страницы; сюда без неё попадают
            # только посторонние запросы — молча соглашаемся и ничего не пишем.
            return {"ok": True, "counted": False}
        headers = getattr(request, "headers", {}) or {}
        ua = str(headers.get("user-agent") or "")
        user = _public_user(request)
        meta = geoip.visit_meta(request, _site_hosts(request))
        path = ""
        try:
            body = payload or {}
            path = str(body.get("path") or "")[:120]
        except Exception:                                 # noqa: BLE001
            path = ""
        if not path:
            try:
                path = str(request.url.path or "/")
            except Exception:                             # noqa: BLE001
                path = "/"
        row = ctx.store.touch_presence(
            vid, user_id=(user.get("id") if user else None),
            country=meta["cc"], country_name=meta["name"],
            # источник пишем только у первого «сердцебиения» гостя: у пинга
            # реферера уже нет, и метка кампании потерялась бы
            source="" if meta["source_kind"] == "internal" else meta["source"],
            source_kind=meta["source_kind"], path=path, ua=ua,
        )
        if not meta["cc"] and not geoip.header_country(headers):
            geoip.schedule_country(ip, iph, 0, vid)       # страна придёт позже
        info = geoip.info(row.get("country") or "", row.get("country_name") or "")
        return {"ok": True, "counted": True, "cc": info["cc"], "name": info["name"],
                "lat": info["lat"], "lon": info["lon"],
                "sec": round(float(row.get("sec") or 0.0), 1),
                "online": len(ctx.store.geo_online(ONLINE_SEC))}

    @router.get("/api/admin/geo")
    async def admin_geo(request: Request, period: str = DEFAULT_PERIOD):
        """Картина посещаемости для админки: точки, страны, источники, время."""
        from feedback import _admin
        user, err = _admin(request)
        if err:
            return err
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        key = str(period or DEFAULT_PERIOD).strip().lower()
        if key not in PERIODS:
            key = DEFAULT_PERIOD
        try:
            data = ctx.store.geo_stats(hours=PERIODS[key], online_sec=ONLINE_SEC,
                                       dots=MAX_DOTS)
        except Exception as e:                            # noqa: BLE001
            log.warning("гео: статистика не собралась: %s", e)
            return JSONResponse({"ok": False, "error": "stats"}, status_code=500)
        header_cc = geoip.header_country(getattr(request, "headers", {}) or {})
        return {
            "ok": True,
            "period": key,
            "periods": list(PERIODS),
            "hours": data["hours"],
            "online_sec": data["online_sec"],
            "online": enrich(data["online"]),
            "points": enrich(data["points"]),
            "countries": enrich(data["countries"]),
            "sources": data["sources"],
            "long": enrich(data["long"]),
            "paths": data["paths"],
            "totals": data["totals"],
            # Откуда берётся страна: заголовок CDN (точно) или внешний сервис.
            # Админу это важно знать: без первого страны появляются с задержкой.
            "geo": {
                "edge": bool(header_cc),
                "enabled": geoip.enabled(),
                "provider": geoip.provider_urls("0.0.0.0")[0][0] if geoip.enabled() else "",
                "cache_days": round(geoip.CACHE_TTL / 86400.0),
            },
            "now": data["now"],
        }

    app.include_router(router)
