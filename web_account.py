"""HTTP: вход через Telegram, кабинет, админка, учёт визитов."""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from accounts import COOKIE_SID, COOKIE_VID, hash_ip, verify_telegram_widget

log = logging.getLogger("liqscope.account")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Ctx:
    """Связка сервера и аккаунтов — заполняется в server.py."""
    store = None
    bot = None
    public_url = ""
    secret = ""
    cookie_secure = False
    dev_login = False
    health_fn = staticmethod(lambda: {})
    stats_fn = staticmethod(lambda: {})
    liqs_fn = staticmethod(lambda: [])
    ws_clients_fn = staticmethod(lambda: 0)


ctx = Ctx()


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _set_sid(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_SID, token,
        httponly=True, samesite="lax",
        max_age=30 * 86400, path="/",
        secure=ctx.cookie_secure,
    )


def _set_vid(response: Response, vid: str) -> None:
    response.set_cookie(
        COOKIE_VID, vid,
        httponly=True, samesite="lax",
        max_age=400 * 86400, path="/",
        secure=ctx.cookie_secure,
    )


def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_SID)
    if not token or not ctx.store:
        return None
    user = ctx.store.user_by_session(token)
    if user:
        ctx.store.touch_user(user["id"])
    return user


def _need_auth() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "auth"}, status_code=401)


def _need_admin() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "admin"}, status_code=403)


# Публичное имя бота, если getMe ещё не ответил или токен не подхватился.
DEFAULT_BOT_USERNAME = (os.getenv("LIQSCOPE_BOT_USERNAME") or "LiqScopeBot").lstrip("@")


def _bot_username() -> str:
    bot = ctx.bot
    name = ((bot.username if bot else "") or "").lstrip("@")
    return name or DEFAULT_BOT_USERNAME


def _bot_link(start: str = "") -> str:
    user = _bot_username()
    if start:
        return f"https://t.me/{user}?start={quote(start)}"
    return f"https://t.me/{user}"


def _public_url(request: Request) -> str:
    if ctx.public_url:
        return ctx.public_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def register_account_routes(app) -> None:
    router = APIRouter()

    @router.get("/login")
    async def page_login():
        return FileResponse(os.path.join(STATIC_DIR, "login.html"))

    @router.get("/cabinet")
    async def page_cabinet():
        return FileResponse(os.path.join(STATIC_DIR, "cabinet.html"))

    @router.get("/admin")
    async def page_admin():
        return FileResponse(os.path.join(STATIC_DIR, "admin.html"))

    @router.get("/api/auth/me")
    async def api_me(request: Request):
        user = current_user(request)
        bot_user = _bot_username()
        notice = ctx.store.get_setting("site_notice", "") if ctx.store else ""
        return {
            "user": user,
            "bot_username": bot_user,
            "bot_ready": bool(bot_user),
            "public_url": _public_url(request),
            "site_notice": notice,
        }

    @router.post("/api/auth/logout")
    async def api_logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE_SID)
        if token and ctx.store:
            ctx.store.drop_session(token)
        response.delete_cookie(COOKIE_SID, path="/")
        return {"ok": True}

    @router.post("/api/auth/telegram/start")
    async def api_tg_start(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        bot_user = _bot_username()
        if not ctx.bot or not getattr(ctx.bot, "token", ""):
            return JSONResponse({
                "ok": False,
                "error": "no_bot",
                "hint": "Задайте LIQSCOPE_BOT_TOKEN и перезапустите сервер.",
            }, status_code=503)
        nonce = ctx.store.new_nonce()
        payload = "login_" + nonce
        return {
            "ok": True,
            "nonce": nonce,
            "bot_username": bot_user,
            "bot_link": _bot_link(payload),
            "expires_in": 300,
        }

    @router.get("/api/auth/telegram/wait")
    async def api_tg_wait(request: Request, response: Response, nonce: str = ""):
        if not ctx.store or not nonce:
            return {"ok": False, "pending": True}
        st = ctx.store.nonce_status(nonce)
        if not st.get("ok"):
            return st
        user = ctx.store.get_user(st["user_id"])
        if not user:
            return {"ok": False, "error": "user"}
        if user["is_banned"]:
            return JSONResponse({"ok": False, "error": "banned"}, status_code=403)
        iph = hash_ip(ctx.secret, _client_ip(request))
        token = ctx.store.create_session(user["id"], iph, request.headers.get("user-agent") or "")
        _set_sid(response, token)
        return {"ok": True, "user": user}

    @router.post("/api/auth/telegram/widget")
    async def api_tg_widget(request: Request, response: Response):
        if not ctx.store or not ctx.bot or not ctx.bot.token:
            return JSONResponse({"ok": False, "error": "no_bot"}, status_code=503)
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not verify_telegram_widget(data, ctx.bot.token):
            return JSONResponse({"ok": False, "error": "bad_hash"}, status_code=401)
        user = ctx.store.upsert_telegram_user(data)
        if user["is_banned"]:
            return JSONResponse({"ok": False, "error": "banned"}, status_code=403)
        iph = hash_ip(ctx.secret, _client_ip(request))
        token = ctx.store.create_session(user["id"], iph, request.headers.get("user-agent") or "")
        _set_sid(response, token)
        return {"ok": True, "user": user}

    @router.post("/api/auth/dev")
    async def api_dev_login(request: Request, response: Response):
        """Только при LIQSCOPE_DEV_LOGIN=1 — вход без Telegram (разработка/тесты)."""
        if not ctx.dev_login or not ctx.store:
            return JSONResponse({"ok": False, "error": "disabled"}, status_code=404)
        try:
            data = await request.json()
        except Exception:
            data = {}
        tg_id = int(data.get("tg_id") or 0)
        if not tg_id:
            return JSONResponse({"ok": False, "error": "tg_id"}, status_code=400)
        user = ctx.store.upsert_telegram_user({
            "id": tg_id,
            "username": data.get("username") or "dev",
            "first_name": data.get("first_name") or "Dev",
            "last_name": data.get("last_name") or "",
            "language_code": data.get("language") or "ru",
        })
        if data.get("is_admin"):
            user = ctx.store.set_admin(user["id"], True) or user
        token = ctx.store.create_session(user["id"], "dev", "dev")
        _set_sid(response, token)
        return {"ok": True, "user": user}

    @router.get("/api/account/services")
    async def api_services(request: Request):
        user = current_user(request)
        if not user:
            return _need_auth()
        have = set(ctx.store.user_service_slugs(user["id"]))
        out = []
        for s in ctx.store.list_services(include_disabled=False):
            item = dict(s)
            item["subscribed"] = s["slug"] in have
            out.append(item)
        return {"ok": True, "services": out}

    @router.post("/api/account/services/{slug}")
    async def api_service_toggle(request: Request, slug: str):
        user = current_user(request)
        if not user:
            return _need_auth()
        try:
            body = await request.json()
        except Exception:
            body = {}
        enabled = bool(body.get("enabled", True))
        return ctx.store.toggle_user_service(user["id"], slug, enabled)

    # ----- admin ----------------------------------------------------------
    def _admin(request: Request):
        user = current_user(request)
        if not user:
            return None, _need_auth()
        if not user["is_admin"]:
            return None, _need_admin()
        return user, None

    @router.get("/api/admin/overview")
    async def admin_overview(request: Request):
        user, err = _admin(request)
        if err:
            return err
        c = ctx.store.user_counts()
        v = ctx.store.visit_stats(14)
        h = ctx.health_fn() or {}
        bot_user = _bot_username()
        return {
            "ok": True,
            "me": user,
            "users": c,
            "visits": v,
            "ws_clients": ctx.ws_clients_fn(),
            "bot": {
                "username": bot_user,
                "ready": bool(bot_user),
                "running": bool(ctx.bot and ctx.bot.running),
            },
            "health": {
                "live_exchanges": h.get("live_exchanges") or [],
                "demo": h.get("demo"),
                "ready": h.get("ready"),
            },
            "settings": {
                "bot_welcome": ctx.store.get_setting("bot_welcome", ""),
                "site_notice": ctx.store.get_setting("site_notice", ""),
            },
            "services": ctx.store.list_services(True),
            "audit": ctx.store.recent_audit(20),
        }

    @router.get("/api/admin/users")
    async def admin_users(request: Request, q: str = "", limit: int = 50, offset: int = 0):
        user, err = _admin(request)
        if err:
            return err
        return {"ok": True, **ctx.store.list_users(q=q, limit=limit, offset=offset)}

    @router.post("/api/admin/users/{user_id}/ban")
    async def admin_ban(request: Request, user_id: int):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        banned = bool(body.get("banned", True))
        u = ctx.store.set_banned(user_id, banned, actor_id=actor["id"])
        if not u:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True, "user": u}

    @router.post("/api/admin/broadcast")
    async def admin_broadcast(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        if not ctx.bot or not ctx.bot.running:
            return JSONResponse({"ok": False, "error": "no_bot"}, status_code=503)
        st = await ctx.bot.broadcast(text, actor_id=actor["id"])
        return {"ok": True, **st}

    @router.post("/api/admin/settings")
    async def admin_settings(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        allowed = {"bot_welcome", "site_notice"}
        saved = {}
        for k, v in body.items():
            if k in allowed and isinstance(v, str):
                ctx.store.set_setting(k, v[:2000], actor_id=actor["id"])
                saved[k] = v[:2000]
        return {"ok": True, "saved": saved}

    @router.post("/api/admin/services/{slug}")
    async def admin_service(request: Request, slug: str):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        fields = {}
        if "enabled" in body:
            fields["enabled"] = 1 if body["enabled"] else 0
        if "coming_soon" in body:
            fields["coming_soon"] = 1 if body["coming_soon"] else 0
        row = ctx.store.update_service(slug, **fields)
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        ctx.store.audit(actor["id"], "service", f"{slug} {fields}")
        return {"ok": True, "service": row}

    @router.get("/api/admin/stats")
    async def admin_market(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        return {"ok": True, "stats": ctx.stats_fn(), "health": ctx.health_fn()}

    app.include_router(router)

    @app.middleware("http")
    async def visit_and_vid(request: Request, call_next):
        path = request.url.path or "/"
        vid = request.cookies.get(COOKIE_VID) or ""
        new_vid = ""
        if not vid:
            new_vid = secrets.token_urlsafe(12)
            vid = new_vid
        response = await call_next(request)
        if new_vid:
            _set_vid(response, new_vid)
        # страницы, не статика/апи/ws
        if (
            ctx.store
            and request.method in ("GET", "HEAD")
            and response.status_code < 400
            and not path.startswith("/static")
            and not path.startswith("/api")
            and path != "/ws"
        ):
            try:
                user = current_user(request)
                iph = hash_ip(ctx.secret, _client_ip(request))
                ctx.store.record_visit(path, vid, user["id"] if user else None, iph)
            except Exception as e:
                log.debug("visit: %s", e)
        return response
