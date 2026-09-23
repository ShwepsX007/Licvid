"""💬 Комментарии к дайджесту и сводке по часам.

Читать могут все, писать — только зарегистрированные (сессия COOKIE_SID).
Комментарий привязан к конкретному выпуску:

    kind = digest | hourly | article
    item_id = YYYY-MM-DD (день дайджеста/часовки) или post-id часовки

Эндпоинты:
    GET  /api/comments?kind=&item=&limit=&after_id= — список комментариев
    GET  /api/comments/count?kind=&item= — счётчик (для бейджей)
    GET  /api/comments/me — кто я для комментариев (auth state)
    POST /api/comments {kind, item, text} — отправить (auth)
    DELETE /api/comments/{id} — удалить (admin)
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Dict, List

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("liqscope.content_comments")


class Ctx:
    store = None
    secret = ""


ctx = Ctx()

_RATE_LOCK = threading.Lock()
_RATE: Dict[int, List[float]] = {}
RATE_LIMIT = 15          # сообщений
RATE_WINDOW = 120.0      # секунд
RATE_SAME_SEC = 3.0      # пауза между сообщениями одного юзера

_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def _now() -> float:
    return time.time()


def _rate_ok(user_id: int) -> bool:
    now = _now()
    uid = int(user_id)
    with _RATE_LOCK:
        hits = [t for t in _RATE.get(uid, []) if now - t < RATE_WINDOW]
        if hits and now - hits[-1] < RATE_SAME_SEC:
            return False
        if len(hits) >= RATE_LIMIT:
            _RATE[uid] = hits
            return False
        hits.append(now)
        _RATE[uid] = hits
        if len(_RATE) > 2000:
            for k in list(_RATE.keys())[:500]:
                if not [t for t in _RATE.get(k, []) if now - t < RATE_WINDOW]:
                    _RATE.pop(k, None)
    return True


def _current_user(request: Request):
    try:
        from web_account import current_user as _cu
        return _cu(request)
    except Exception:
        return None


def _need_auth():
    return JSONResponse({"ok": False, "error": "auth",
                         "hint": "Войдите, чтобы комментировать"}, status_code=401)


def _need_admin():
    return JSONResponse({"ok": False, "error": "admin"}, status_code=403)


def _clean_text(raw: str, limit: int = 1000) -> str:
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
        "kind": str(m.get("kind") or ""),
        "item": str(m.get("item_id") or m.get("item") or ""),
        "user_id": int(m.get("user_id") or 0),
        "name": str(m.get("display_name") or "")[:80],
        "text": str(m.get("text") or "")[:1000],
        "ts": float(m.get("created_at") or 0),
        "admin": bool(m.get("is_admin")),
    }


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def register_comment_routes(app) -> None:
    router = APIRouter()

    @router.get("/api/comments")
    async def api_list(request: Request, kind: str = "", item: str = "",
                       limit: int = 100, after_id: int = 0):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        k = str(kind or "").strip().lower()
        it = str(item or "").strip()[:120]
        if k not in ("digest", "hourly", "article") or not it:
            return JSONResponse({"ok": False, "error": "bad_params",
                                 "hint": "Нужны kind=digest|hourly|article и item (дата, id поста или адрес статьи)"},
                                status_code=400)
        try:
            lim = max(1, min(int(limit), 200))
        except Exception:
            lim = 100
        try:
            aft = max(0, int(after_id))
        except Exception:
            aft = 0
        rows = ctx.store.list_content_comments(kind=k, item_id=it, limit=lim, after_id=aft)
        return {"ok": True, "messages": [_msg_public(r) for r in rows],
                "now": _now(), "kind": k, "item": it}

    @router.get("/api/comments/count")
    async def api_count(request: Request, kind: str = "", item: str = ""):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        k = str(kind or "").strip().lower()
        it = str(item or "").strip()[:120]
        if k and k not in ("digest", "hourly", "article"):
            return JSONResponse({"ok": False, "error": "bad_kind"}, status_code=400)
        cnt = ctx.store.content_comments_count(kind=k, item_id=it)
        return {"ok": True, "count": cnt, "kind": k, "item": it}

    @router.get("/api/comments/me")
    async def api_me(request: Request):
        u = _current_user(request)
        if not u:
            return {"ok": True, "user": None, "can_write": False}
        return {"ok": True,
                "user": {"id": u["id"],
                         "name": u.get("display_name") or u.get("first_name") or "",
                         "is_admin": bool(u.get("is_admin"))},
                "can_write": True}

    @router.post("/api/comments")
    async def api_post(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u:
            return _need_auth()
        body = await _json_body(request)
        k = str(body.get("kind") or body.get("type") or "").strip().lower()
        it = str(body.get("item") or body.get("item_id") or "").strip()[:120]
        txt = _clean_text(body.get("text") or "", limit=ctx.store.CONTENT_COMMENT_MAX_LEN)
        if k not in ("digest", "hourly", "article"):
            return JSONResponse({"ok": False, "error": "bad_kind",
                                 "hint": "kind должен быть digest, hourly или article"},
                                status_code=400)
        if not it:
            return JSONResponse({"ok": False, "error": "bad_item",
                                 "hint": "Укажите item — дату выпуска, id поста или адрес статьи"},
                                status_code=400)
        if not txt:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        if not _rate_ok(u["id"]):
            return JSONResponse({"ok": False, "error": "rate",
                                 "hint": "Слишком часто — подождите пару секунд"},
                                status_code=429)
        name = (u.get("display_name") or u.get("first_name") or
                u.get("username") or u.get("email") or f"id{u['id']}")[:80]
        r = ctx.store.add_content_comment(int(u["id"]), name, k, it, txt,
                                          is_admin=bool(u.get("is_admin")))
        if not r.get("ok"):
            return JSONResponse(r, status_code=400)
        msg = {
            "id": r["id"],
            "kind": k,
            "item_id": it,
            "user_id": int(u["id"]),
            "display_name": name,
            "text": txt,
            "created_at": r["created_at"],
            "is_admin": 1 if u.get("is_admin") else 0,
        }
        return {"ok": True, "message": _msg_public(msg)}

    @router.delete("/api/comments/{comment_id}")
    async def api_delete(request: Request, comment_id: int):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u:
            return _need_auth()
        if not u.get("is_admin"):
            return _need_admin()
        ok = ctx.store.delete_content_comment(int(comment_id))
        if not ok:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True, "id": int(comment_id)}

    app.include_router(router)
