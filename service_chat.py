"""🔔 Сервисный чат — персональные сигналы per-user.

Пишет только сервер (alert_loop, corr_alert_loop, pump_notify, book_alert_loop):
    Store.add_user_service_message(user_id, kind, text, meta) + broadcast via hub per-user.

Читает только зарегистрированный пользователь — свой личный канал.
Гостям таб «сервисы» не доступен (проверяется и на бэке, и на фронте).

Эндпоинты:
    GET  /api/chat/services?limit=&after_id= — личные сигналы пользователя
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("liqscope.service_chat")


class Ctx:
    store = None
    hub = None


ctx = Ctx()

_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\\x1F\\x7F]")


def _now() -> float:
    return time.time()


def _current_user(request: Request):
    try:
        from web_account import current_user as _cu
        return _cu(request)
    except Exception:
        return None


def _need_auth():
    return JSONResponse({"ok": False, "error": "auth"}, status_code=401)


def _msg_public(m: dict) -> dict:
    meta = m.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": int(m.get("id") or 0),
        "kind": str(m.get("kind") or "alert")[:32],
        "text": str(m.get("text") or "")[:4000],
        "ts": float(m.get("created_at") or 0),
        "meta": meta,
    }


def register_service_chat_routes(app, hub=None) -> None:
    if hub is not None:
        ctx.hub = hub
    router = APIRouter()

    @router.get("/api/chat/services")
    async def api_list(request: Request, limit: int = 200, after_id: int = 0):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u:
            return _need_auth()
        try:
            lim = max(1, min(int(limit), 500))
        except Exception:
            lim = 200
        try:
            aft = max(0, int(after_id))
        except Exception:
            aft = 0
        try:
            rows = ctx.store.list_user_service_messages(user_id=int(u["id"]), limit=lim, after_id=aft)
        except AttributeError:
            # fallback legacy global
            rows = ctx.store.list_service_messages(limit=lim, after_id=aft)
        return {"ok": True, "messages": [_msg_public(r) for r in rows], "now": _now(), "user_id": int(u["id"])}

    @router.get("/api/chat/services/me")
    async def api_me(request: Request):
        u = _current_user(request)
        if not u:
            return {"ok": True, "user": None}
        return {"ok": True, "user": {"id": u["id"], "name": u.get("display_name") or "", "is_admin": bool(u.get("is_admin"))}}

    app.include_router(router)


async def broadcast_service_user(user_id: int, kind: str, text: str, meta: Dict | None = None):
    """Сохранить в персональную таблицу и разослать только этому пользователю по WS."""
    if not ctx.store:
        return
    try:
        rec = ctx.store.add_user_service_message(int(user_id), kind, text, meta or {})
        if not rec.get("ok"):
            return
        msg = _msg_public(rec)
        if ctx.hub is not None:
            import asyncio
            uid = int(user_id)

            async def _bcast():
                try:
                    await ctx.hub.broadcast({"type": "service_chat", "message": msg},
                                            predicate=lambda c: int(getattr(c, "user_id", 0) or 0) == uid)
                except Exception as e:
                    log.debug("service broadcast user %s: %s", uid, e)

            asyncio.create_task(_bcast())
    except Exception as e:
        log.debug("service add user %s: %s", user_id, e)


async def broadcast_service(kind: str, text: str, meta: Dict | None = None):
    """Legacy global broadcast — теперь не используется, оставлен для совместимости.
    Не спамит всех: просто сохраняет в глобальную таблицу, но не рассылает.
    Новый код должен использовать broadcast_service_user."""
    if not ctx.store:
        return
    try:
        rec = ctx.store.add_service_message(kind, text, meta or {})
        if not rec.get("ok"):
            return
        # не рассылаем глобально — персональные сигналы только per-user
        # если кто-то всё ещё слушает глобальный канал, можно включить, но по ТЗ нельзя
        # оставляем закомментированным:
        # msg = _msg_public(rec)
        # if ctx.hub is not None:
        #     await ctx.hub.broadcast({"type": "service_chat", "message": msg})
    except Exception as e:
        log.debug("service add global: %s", e)
