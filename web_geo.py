"""🌍 Откуда приходят гости, как долго смотрят и кто сейчас на сайте.

Ручки:

    POST /api/visit/ping          — «я ещё здесь» (сердцебиение страницы)
    GET  /api/admin/geo?period=   — картина для админки: точки, страны, источники
    POST /api/admin/geo/dots/clear — убрать старые точки с карты посетителей
    POST /api/admin/geo/dots/reset — вернуть точки на карту
    POST /api/admin/visits/clear  — стереть статистику посещений (только админ)

Сердцебиение шлёт ``static/presence.js`` со всех страниц: одна маленькая
запись на гостя (``first_ts`` — когда пришёл, ``ts`` — когда последний раз
подал признаки жизни). Из неё получаются и «кто онлайн» (последние 5 минут),
и «сколько времени провёл на сайте». Без JS гость всё равно попадает в
счётчики визитов — просто без времени и без точки «онлайн».

Админская ручка — только для админов и отдаёт готовые для карты координаты:
широту и долготу центра страны подставляет ``geoip`` (справочник центроидов
рядом с контурами карты, ``static/world-map.js``).

Точки на карте копятся за выбранный период, и старые со временем мешают
смотреть, кто приходит сейчас. Кнопка «убрать старые точки» ставит отметку
времени (``geo_dots_after`` в настройках): карта показывает только гостей,
замеченных позже неё, а статистика, страны и источники остаются целыми —
уборка не стирает данные и снимается той же кнопкой.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

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

#: Настройка «показывать точки только после этого времени» (unix-секунды).
DOTS_SETTING = "geo_dots_after"
#: Сколько старых точек оставлять: по умолчанию тех, кто на сайте сейчас.
DOTS_KEEP_SEC = ONLINE_SEC
#: Дальше месяца убирать нечего.
DOTS_KEEP_MAX = 30 * 86400.0

#: Слово-подтверждение для стирания статистики. Не защита от взлома (ручка и
#: так только для админа), а защита от случайного вызова: без него статистика
#: не исчезнет ни от опечатки в коде, ни от чужого скрипта.
CLEAR_WORD = "clear"



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


def dots_after() -> float:
    """С какого времени показывать точки на карте: 0 — показывать все.

    Значение живёт в настройках сайта (``geo_dots_after``) и меняется кнопкой
    «убрать старые точки» в админке. Это не удаление данных: статистика, страны
    и источники считаются как раньше, прячутся только точки карты.
    """
    if ctx.store is None:
        return 0.0
    try:
        raw = str(ctx.store.get_setting(DOTS_SETTING, "") or "").strip()
    except Exception as e:                                # noqa: BLE001
        log.debug("гео: отметка точек не прочиталась: %s", e)
        return 0.0
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _set_dots_after(ts: float, actor_id: int = 0) -> bool:
    """Запомнить отметку времени для точек карты (0 — вернуть все точки)."""
    if ctx.store is None:
        return False
    try:
        ctx.store.set_setting(DOTS_SETTING, ("%.1f" % float(ts)) if ts > 0 else "",
                              actor_id)
    except Exception as e:                                # noqa: BLE001
        log.warning("гео: отметка точек не сохранилась: %s", e)
        return False
    return True


def _with_trial(rows: List[Dict[str, Any]], now: float) -> List[Dict[str, Any]]:
    """Дописать живым гостям остаток пробного доступа к слоям.

    Админке это нужно рядом с кнопкой «дать ещё»: видно, кто знакомится со
    слоями и у кого время уже вышло. Пробник считается только для гостей — у
    вошедших в кабинет слои без ограничений.
    """
    if ctx.store is None or not rows:
        return rows
    try:
        from web_layers import trial_limit_sec
        limit = trial_limit_sec()
    except Exception:                                     # noqa: BLE001
        limit = 0
    if limit <= 0:
        return rows
    keys = ["v:" + str(r.get("vid") or "")[:60] for r in rows if r.get("vid")]
    try:
        trials = ctx.store.layer_trials_of(keys)
    except Exception as e:                                # noqa: BLE001
        log.debug("гео: пробники гостей не прочитались: %s", e)
        return rows
    for row in rows:
        vid = str(row.get("vid") or "")
        found = trials.get("v:" + vid[:60]) if vid else None
        if not found or row.get("user"):
            continue
        left = max(0.0, float(found["started_at"]) + limit - now)
        row["trial"] = {"who": "v:" + vid[:60], "left_sec": round(left, 1),
                        "hits": int(found.get("hits") or 0),
                        "expired": left <= 0, "minutes": limit // 60}
    return rows


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

    @router.post("/api/admin/visits/clear")
    async def admin_visits_clear(request: Request,
                                 payload: Dict[str, Any] = Body(default={})):
        """Стереть статистику посещений: визиты, присутствие, кэш стран.

        Только для админов, и только с подтверждением (``{"confirm": "clear"}``):
        действие необратимое, поэтому его нельзя выстрелить одной случайной
        кнопкой. Аккаунты, сервисы и подписки не трогаем — стирается ровно
        статистика посещаемости.
        """
        from feedback import _admin
        user, err = _admin(request)
        if err:
            return err
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = payload or {}
        if str(body.get("confirm") or "").strip().lower() != CLEAR_WORD:
            return JSONResponse({"ok": False, "error": "confirm"}, status_code=400)
        include_cache = bool(body.get("cache"))
        before = {}
        try:
            before = ctx.store.visits_volume()
            counts = ctx.store.clear_visits(include_cache=include_cache)
        except Exception as e:                                # noqa: BLE001
            log.warning("визиты: статистика не стёрлась: %s", e)
            return JSONResponse({"ok": False, "error": "clear"}, status_code=500)
        # след в журнале админки: кто и сколько строк стёр (detail — строка)
        try:
            ctx.store.audit(int((user or {}).get("id") or 0), "visits_clear",
                            f"visits={counts['visits']} presence={counts['presence']}"
                            f" cache={counts['geo_cache']}")
        except Exception as e:                                # noqa: BLE001
            log.debug("визиты: след в журнале не записался: %s", e)
        log.info("визиты: статистика стёрта админом %s — %s (кэш: %s)",
                 (user or {}).get("id"), counts, include_cache)
        return {"ok": True, "deleted": counts, "before": before,
                "cache": include_cache, "now": time.time()}

    @router.post("/api/admin/geo/dots/clear")
    async def admin_geo_dots_clear(request: Request,
                                   payload: Optional[dict] = Body(default=None)) -> Any:
        """Убрать старые точки с карты посетителей (только админ).

        Ставим отметку времени: карта показывает гостей, замеченных позже неё.
        Данные не стираются — страны, источники, время на сайте и цифры за
        период остаются на месте, а кнопка «вернуть точки» снимает отметку.
        Сколько времени оставить видимым, решает ``keep_sec``: по умолчанию
        тех, кто на сайте прямо сейчас.
        """
        from feedback import _admin
        user, err = _admin(request)
        if err:
            return err
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = payload or {}
        try:
            keep = float(body.get("keep_sec", DOTS_KEEP_SEC))
        except (TypeError, ValueError):
            keep = DOTS_KEEP_SEC
        keep = max(0.0, min(keep, DOTS_KEEP_MAX))
        now = time.time()
        cutoff = now - keep
        if not _set_dots_after(cutoff, int((user or {}).get("id") or 0)):
            return JSONResponse({"ok": False, "error": "save"}, status_code=500)
        try:
            ctx.store.audit(int((user or {}).get("id") or 0), "geo_dots_clear",
                            f"after={int(cutoff)} keep={int(keep)}")
        except Exception as e:                            # noqa: BLE001
            log.debug("гео: след в журнале не записался: %s", e)
        log.info("гео: старые точки убраны с карты (оставлены за %s с)", int(keep))
        return {"ok": True, "dots_after": cutoff, "keep_sec": keep, "now": now}

    @router.post("/api/admin/geo/dots/reset")
    async def admin_geo_dots_reset(request: Request) -> Any:
        """Вернуть на карту все точки: снять отметку времени (только админ)."""
        from feedback import _admin
        user, err = _admin(request)
        if err:
            return err
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        if not _set_dots_after(0.0, int((user or {}).get("id") or 0)):
            return JSONResponse({"ok": False, "error": "save"}, status_code=500)
        try:
            ctx.store.audit(int((user or {}).get("id") or 0), "geo_dots_reset", "")
        except Exception as e:                            # noqa: BLE001
            log.debug("гео: след в журнале не записался: %s", e)
        log.info("гео: точки карты вернулись целиком")
        return {"ok": True, "dots_after": 0.0, "now": time.time()}

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
        # Точки карты: старые прячем по отметке из админки, статистика остаётся.
        # Кнопка «стереть точки» теперь прячет и точки, и круги (страны): карта
        # показывает только гостей, замеченных позже отметки.
        cutoff = dots_after()
        points = enrich(data["points"])
        if cutoff > 0:
            visible = [p for p in points
                       if float(p.get("last_ts") or p.get("first_ts") or 0) >= cutoff]
        else:
            visible = points
        countries_all = enrich(data["countries"])
        if cutoff > 0:
            countries = [c for c in countries_all
                         if float(c.get("last") or 0) >= cutoff]
        else:
            countries = countries_all
        online = _with_trial(enrich(data["online"]), float(data["now"]))
        try:
            from web_layers import trial_limit_minutes, trial_locked
            layers = {"minutes": trial_limit_minutes(), "locked": trial_locked()}
            layers["enabled"] = layers["minutes"] > 0
        except Exception as e:                            # noqa: BLE001
            log.debug("гео: настройка слоёв недоступна: %s", e)
            layers = {"enabled": False}
        return {
            "ok": True,
            "period": key,
            "periods": list(PERIODS),
            "hours": data["hours"],
            "online_sec": data["online_sec"],
            "online": online,
            "points": visible,
            "countries": countries,
            "sources": data["sources"],
            "long": enrich(data["long"]),
            "paths": data["paths"],
            "totals": data["totals"],
            # Сколько точек спрятано кнопкой «убрать старые»: по этим числам
            # карточка пишет, что именно скрыто и как вернуть всё назад.
            "dots_after": cutoff,
            "dots_shown": len(visible),
            "dots_hidden": max(0, len(points) - len(visible)),
            "layers": layers,
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
