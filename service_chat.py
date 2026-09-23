"""🔔 Сервисный чат — все сигналы, что уходят в Telegram.

Пишет только сервер (alert_loop, corr_alert_loop, pump_notify, book_alert_loop):
    Store.add_service_message(kind, text, meta) + broadcast via hub.

Читает любой посетитель сайта, даже гость.

Эндпоинты:
    GET  /api/chat/services?limit=&after_id= — последние сигналы (публично)
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

_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def _now() -> float:
    return time.time()


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
    async def api_list(request: Request, limit: int = 100, after_id: int = 0):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        try:
            lim = max(1, min(int(limit), 300))
        except Exception:
            lim = 100
        try:
            aft = max(0, int(after_id))
        except Exception:
            aft = 0
        rows = ctx.store.list_service_messages(limit=lim, after_id=aft)
        return {"ok": True, "messages": [_msg_public(r) for r in rows], "now": _now()}

    app.include_router(router)


async def broadcast_service(kind: str, text: str, meta: Dict | None = None):
    """Сохранить в базу и разослать по WS (если hub есть)."""
    if not ctx.store:
        return
    try:
        rec = ctx.store.add_service_message(kind, text, meta or {})
        if not rec.get("ok"):
            return
        msg = _msg_public(rec)
        if ctx.hub is not None:
            import asyncio

            async def _bcast():
                try:
                    await ctx.hub.broadcast({"type": "service_chat", "message": msg})
                except Exception as e:
                    log.debug("service broadcast: %s", e)

            asyncio.create_task(_bcast())
    except Exception as e:
        log.debug("service add: %s", e)
