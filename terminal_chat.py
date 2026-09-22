"""💬 Мини-чат терминала: верхняя правая часть /terminal, collapsible, 3 дня истории.

Писать может любой зарегистрировавшийся пользователь (сессия COOKIE_SID),
читать — все. История — 3 дня, хранится в SQLite ``terminal_chat``.

Эндпоинты:
    GET  /api/terminal/chat?limit=&after_id= — последние сообщения (публично)
    POST /api/terminal/chat {text} — отправить (auth)
    DELETE /api/terminal/chat/{id} — удалить (admin)
    GET  /api/terminal/chat/me — кто я для чата (auth state для виджета)
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Dict, List

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("liqscope.terminal_chat")


class Ctx:
    store = None
    secret = ""
    # hub для WS broadcast (опционально, ставится из server.py)
    hub = None


ctx = Ctx()

# Лимит флуда: не больше N сообщений за окно
_RATE_LOCK = threading.Lock()
_RATE: Dict[int, List[float]] = {}
RATE_LIMIT = 10          # сообщений
RATE_WINDOW = 60.0       # секунд
RATE_SAME_SEC = 2.0      # пауза между сообщениями одного юзера


def _now() -> float:
    return time.time()


def _rate_ok(user_id: int) -> bool:
    now = _now()
    uid = int(user_id)
    with _RATE_LOCK:
        hits = [t for t in _RATE.get(uid, []) if now - t < RATE_WINDOW]
        # частая отправка подряд
        if hits and now - hits[-1] < RATE_SAME_SEC:
            return False
        if len(hits) >= RATE_LIMIT:
            _RATE[uid] = hits
            return False
        hits.append(now)
        _RATE[uid] = hits
        # чистка редкая
        if len(_RATE) > 2000:
            for k in list(_RATE.keys())[:500]:
                if not [t for t in _RATE.get(k, []) if now - t < RATE_WINDOW]:
                    _RATE.pop(k, None)
    return True


def _current_user(request: Request):
    """Копия current_user из web_account — без циклического импорта."""
    try:
        from web_account import current_user as _cu
        return _cu(request)
    except Exception:
        return None


def _need_auth():
    return JSONResponse({"ok": False, "error": "auth",
                         "hint": "Войдите, чтобы писать в чат"}, status_code=401)


def _need_admin():
    return JSONResponse({"ok": False, "error": "admin"}, status_code=403)


# Базовая чистка текста: не пускаем control-символы, оставляем эмодзи/кириллицу
_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def _clean_text(raw: str, limit: int = 500) -> str:
    s = str(raw or "").strip()
    s = _CTRL_RE.sub("", s)
    # схлопываем длинные пробелы/переносы, но оставляем одиночные переносы
    s = re.sub(r"[ \t]{3,}", "  ", s)
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    if len(s) > limit:
        s = s[:limit].rstrip()
    return s


def _msg_public(m: dict) -> dict:
    """То, что отдаём наружу — без внутренних полей."""
    return {
        "id": int(m.get("id") or 0),
        "user_id": int(m.get("user_id") or 0),
        "name": str(m.get("display_name") or "")[:80],
        "text": str(m.get("text") or "")[:500],
        "ts": float(m.get("created_at") or 0),
        "admin": bool(m.get("is_admin")),
    }


# ---------------------------------------------------------------------------
# 💚 «Онлайн в чате»: виджет (терминал и кабинет) тихо пингует сервер,
# пока страница открыта. TTL с запасом: мигнувший интернет не должен
# «выкидывать» человека из онлайн-списка на кнопке.
# ---------------------------------------------------------------------------
_PRESENCE_LOCK = threading.Lock()
_PRESENCE: Dict[int, float] = {}
PRESENCE_TTL = 90.0            # секунд, что отметка считается живой
PRESENCE_PING_SEC = 30         # с какой частотой виджет должен пинговать


def _now_presence() -> float:
    return time.time()


def chat_presence_touch(user_id: int) -> None:
    with _PRESENCE_LOCK:
        _PRESENCE[int(user_id)] = _now_presence()
        if len(_PRESENCE) > 4000:      # редкая уборка старых отметок
            cut = _now_presence() - PRESENCE_TTL
            for k in [k for k, v in _PRESENCE.items() if v < cut]:
                _PRESENCE.pop(k, None)


def chat_presence_online(user_id: int) -> bool:
    with _PRESENCE_LOCK:
        ts = _PRESENCE.get(int(user_id), 0.0)
    return (_now_presence() - ts) < PRESENCE_TTL


def chat_presence_ids() -> List[int]:
    cut = _now_presence() - PRESENCE_TTL
    with _PRESENCE_LOCK:
        return sorted(int(k) for k, v in _PRESENCE.items() if v >= cut)


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def register_chat_routes(app, hub=None) -> None:
    """Подключить /api/terminal/chat к FastAPI."""
    if hub is not None:
        ctx.hub = hub
    router = APIRouter()

    @router.get("/api/terminal/chat")
    async def api_chat_list(request: Request, limit: int = 100, after_id: int = 0):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        # паблик: читать могут все, даже гости
        try:
            lim = max(1, min(int(limit), 200))
        except Exception:
            lim = 100
        try:
            aft = max(0, int(after_id))
        except Exception:
            aft = 0
        rows = ctx.store.list_chat_messages(limit=lim, after_id=aft)
        return {"ok": True, "messages": [_msg_public(r) for r in rows],
                "now": _now(), "keep_days": 3}

    @router.get("/api/terminal/chat/me")
    async def api_chat_me(request: Request):
        u = _current_user(request)
        if not u:
            return {"ok": True, "user": None, "can_write": False}
        return {"ok": True, "user": {"id": u["id"], "name": u.get("display_name") or u.get("first_name") or "",
                                     "is_admin": bool(u.get("is_admin"))},
                "can_write": True}

    @router.post("/api/terminal/chat/ping")
    async def api_chat_ping(request: Request):
        """«Я у чата» — виджет зовёт каждые PRESENCE_PING_SEC секунд."""
        u = _current_user(request)
        if not u:
            return {"ok": True, "online": len(chat_presence_ids())}
        chat_presence_touch(u["id"])
        return {"ok": True, "online": len(chat_presence_ids())}

    @router.get("/api/terminal/chat/online")
    async def api_chat_online(request: Request):
        ids = chat_presence_ids()
        u = _current_user(request)
        me = int(u["id"]) if u else 0
        # счётчик на кнопке — сколько людей онлайн, кроме меня
        others = [i for i in ids if i != me]
        body = {"ok": True, "count": len(others)}
        if u:
            body["ids"] = ids
        return body

    @router.get("/api/terminal/chat/participants")
    async def api_chat_participants(request: Request):
        """Кто писал в общий чат за окно истории + кто сейчас онлайн."""
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        try:
            parts = ctx.store.chat_participants(limit=50)
        except Exception as e:  # noqa: BLE001
            log.debug("participants: %s", e)
            return JSONResponse({"ok": False, "error": "db"}, status_code=500)
        online = set(chat_presence_ids())
        for p in parts:
            p["online"] = int(p["id"]) in online
        return {"ok": True, "participants": parts, "now": _now()}

    @router.post("/api/terminal/chat")
    async def api_chat_post(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u:
            return _need_auth()
        # раз пишет — значит у чата; обновляем отметку онлайн
        chat_presence_touch(u["id"])
        body = await _json_body(request)
        txt = _clean_text(body.get("text") or "", limit=ctx.store.CHAT_MAX_LEN)
        if not txt:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        if not _rate_ok(u["id"]):
            return JSONResponse({"ok": False, "error": "rate",
                                 "hint": "Слишком часто — подождите пару секунд"},
                                status_code=429)
        # display_name: из профиля, но не пустой
        name = (u.get("display_name") or u.get("first_name") or
                u.get("username") or u.get("email") or f"id{u['id']}")[:80]
        r = ctx.store.add_chat_message(int(u["id"]), name, txt,
                                       is_admin=bool(u.get("is_admin")))
        if not r.get("ok"):
            return JSONResponse(r, status_code=400)
        msg = {
            "id": r["id"],
            "user_id": int(u["id"]),
            "display_name": name,
            "text": txt,
            "created_at": r["created_at"],
            "is_admin": 1 if u.get("is_admin") else 0,
        }
        pub = _msg_public(msg)
        # WS broadcast если hub подключен
        try:
            if ctx.hub is not None:
                import asyncio
                # шлём всем клиентам чат-сообщение как отдельный тип
                async def _bcast():
                    try:
                        await ctx.hub.broadcast({"type": "terminal_chat", "message": pub})
                    except Exception as e:
                        log.debug("chat broadcast: %s", e)
                # не блокируем ответ
                asyncio.create_task(_bcast())
        except Exception as e:
            log.debug("chat broadcast setup: %s", e)
        return {"ok": True, "message": pub}

    @router.delete("/api/terminal/chat/{msg_id}")
    async def api_chat_delete(request: Request, msg_id: int):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u:
            return _need_auth()
        if not u.get("is_admin"):
            return _need_admin()
        ok = ctx.store.delete_chat_message(int(msg_id))
        if not ok:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        # broadcast удаления
        try:
            if ctx.hub is not None:
                import asyncio
                async def _bcast_del():
                    try:
                        await ctx.hub.broadcast({"type": "terminal_chat_del",
                                                 "id": int(msg_id)})
                    except Exception:
                        pass
                asyncio.create_task(_bcast_del())
        except Exception:
            pass
        return {"ok": True, "id": int(msg_id)}

    app.include_router(router)
