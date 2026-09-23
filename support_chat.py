"""🆘 Поддержка — чат для всех, включая незарегистрированных.

Перенесён из кабинета (feedback) в отдельный таб чата.

Эндпоинты:
    GET  /api/chat/support?limit=&after_id= — история (публично)
    POST /api/chat/support {text, name?} — написать (гость или auth)
    DELETE /api/chat/support/{id} — удалить (admin)
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("liqscope.support_chat")

MAX_LEN = 2000
RATE_LIMIT = 15
RATE_WINDOW = 300.0

_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


class Ctx:
    store = None
    hub = None
    secret = ""


ctx = Ctx()


class _Limiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW):
        self.limit = limit
        self.window = window
        self.hits: Dict[str, list] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        stamps = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(stamps) >= self.limit:
            self.hits[key] = stamps
            return False
        stamps.append(now)
        self.hits[key] = stamps
        return True


limiter = _Limiter()


def _now() -> float:
    return time.time()


def _current_user(request: Request):
    try:
        from web_account import current_user as _cu

        return _cu(request)
    except Exception:
        return None


def _clean_text(raw: str, limit: int = MAX_LEN) -> str:
    s = str(raw or "").strip()
    s = _CTRL_RE.sub("", s)
    s = re.sub(r"[ \t]{3,}", "  ", s)
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    if len(s) > limit:
        s = s[:limit].rstrip()
    return s


def _msg_public(m: dict) -> dict:
    return {
        "id": int(m.get("id") or 0),
        "user_id": int(m.get("user_id") or 0) if m.get("user_id") else 0,
        "name": str(m.get("display_name") or "")[:80],
        "text": str(m.get("text") or "")[:2000],
        "ts": float(m.get("created_at") or 0),
        "admin": bool(m.get("is_admin")),
        "guest": bool(not m.get("user_id")),
    }


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def register_support_chat_routes(app, hub=None) -> None:
    if hub is not None:
        ctx.hub = hub
    router = APIRouter()

    @router.get("/api/chat/support")
    async def api_list(request: Request, limit: int = 100, after_id: int = 0):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        try:
            lim = max(1, min(int(limit), 200))
        except Exception:
            lim = 100
        try:
            aft = max(0, int(after_id))
        except Exception:
            aft = 0
        rows = ctx.store.list_support_messages(limit=lim, after_id=aft)
        return {"ok": True, "messages": [_msg_public(r) for r in rows], "now": _now()}

    @router.post("/api/chat/support")
    async def api_post(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = await _json_body(request)
        txt = _clean_text(body.get("text") or "", limit=MAX_LEN)
        if not txt:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        u = _current_user(request)
        # rate by user_id or ip
        ip = ""
        try:
            ip = (request.client.host or "") if request.client else ""
        except Exception:
            ip = ""
        key = f"u{u['id']}" if u else f"ip:{ip or 'anon'}"
        if not limiter.allow(key):
            return JSONResponse(
                {"ok": False, "error": "rate", "hint": "Слишком часто — подождите"},
                status_code=429,
            )
        if u:
            name = (u.get("display_name") or u.get("first_name") or u.get("username") or f"id{u['id']}")[:80]
            guest_token = ""
            user_id = int(u["id"])
            is_admin = bool(u.get("is_admin"))
        else:
            # гость: имя из body или Гость
            raw_name = str(body.get("name") or body.get("display_name") or "").strip()[:40]
            name = raw_name or "Гость"
            guest_token = str(body.get("guest_token") or "")[:80] or secrets.token_hex(6)
            user_id = None
            is_admin = False

        rec = ctx.store.add_support_message(user_id, name, txt, is_admin, guest_token)
        if not rec.get("ok"):
            return JSONResponse(rec, status_code=400)
        msg = _msg_public(rec)
        # broadcast
        try:
            if ctx.hub is not None:
                import asyncio

                async def _bcast():
                    try:
                        await ctx.hub.broadcast({"type": "support_chat", "message": msg})
                    except Exception as e:
                        log.debug("support broadcast: %s", e)

                asyncio.create_task(_bcast())
        except Exception as e:
            log.debug("support broadcast setup: %s", e)
        return {"ok": True, "message": msg}

    @router.delete("/api/chat/support/{msg_id}")
    async def api_del(request: Request, msg_id: int):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u or not u.get("is_admin"):
            return JSONResponse({"ok": False, "error": "admin"}, status_code=403)
        ok = ctx.store.delete_support_message(int(msg_id))
        if not ok:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        try:
            if ctx.hub is not None:
                import asyncio

                async def _bcast_del():
                    try:
                        await ctx.hub.broadcast({"type": "support_chat_del", "id": int(msg_id)})
                    except Exception:
                        pass

                asyncio.create_task(_bcast_del())
        except Exception:
            pass
        return {"ok": True, "id": int(msg_id)}

    @router.get("/api/chat/support/me")
    async def api_me(request: Request):
        u = _current_user(request)
        if not u:
            return {"ok": True, "user": None}
        return {
            "ok": True,
            "user": {
                "id": u["id"],
                "name": u.get("display_name") or u.get("first_name") or "",
                "is_admin": bool(u.get("is_admin")),
            },
        }

    app.include_router(router)
