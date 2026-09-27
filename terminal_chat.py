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

MAX_CHAT_HISTORY = 50    # A4: не отдавать всю ленту на логин, только последние N
MAX_CHAT_BYTES = 128 * 1024  # лимит по байтам ответа


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
    async def api_chat_list(request: Request, limit: int = 50, after_id: int = 0):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        # паблик: читать могут все, даже гости — A4 лимит 50
        try:
            lim = max(1, min(int(limit), 100))
        except Exception:
            lim = MAX_CHAT_HISTORY
        # если after_id==0 — отдаём последние lim как контекст, а не 100
        if lim > MAX_CHAT_HISTORY and int(after_id) == 0:
            lim = MAX_CHAT_HISTORY
        try:
            aft = max(0, int(after_id))
        except Exception:
            aft = 0
        rows = ctx.store.list_chat_messages(limit=lim, after_id=aft)
        # защита от слишком большого ответа по байтам
        msgs = [_msg_public(r) for r in rows]
        # если after_id==0 и сообщений больше лимита — уже ограничено, но на всякий случай режем хвост
        if aft == 0 and len(msgs) > MAX_CHAT_HISTORY:
            msgs = msgs[-MAX_CHAT_HISTORY:]
        return {"ok": True, "messages": msgs,
                "now": _now(), "keep_days": 3, "limit": lim}

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

    # --- A2: общий last_read per-user per-chat ---
    @router.get("/api/chat/read_state")
    async def api_chat_read_state(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u:
            return {"ok": True, "reads": {}, "guest": True}
        try:
            # отдаём last_read для всех чатов пользователя
            reads = {}
            with ctx.store._lock:
                rows = ctx.store._db.execute(
                    "SELECT chat_id, last_read_id, last_read_ts FROM chat_reads WHERE user_id=?",
                    (int(u["id"]),)).fetchall()
            for r in rows:
                reads[str(r["chat_id"])] = {"last_read_id": int(r["last_read_id"] or 0),
                                            "last_read_ts": float(r["last_read_ts"] or 0.0)}
            # unread counts
            unread = {}
            try:
                unread = ctx.store.chat_unread_counts(int(u["id"]))
            except Exception:
                pass
            return {"ok": True, "reads": reads, "unread": unread, "now": _now()}
        except Exception as e:
            log.debug("read_state: %s", e)
            return JSONResponse({"ok": False, "error": "db"}, status_code=500)

    @router.post("/api/chat/read")
    async def api_chat_mark_read(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u:
            return _need_auth()
        body = await _json_body(request)
        chat_id = str(body.get("chat_id") or body.get("chat") or "public").strip()[:64] or "public"
        # допускаем как id, так и ts
        try:
            last_id = int(body.get("last_read_id") or body.get("last_id") or body.get("id") or 0)
        except Exception:
            last_id = 0
        try:
            last_ts = float(body.get("last_read_ts") or body.get("ts") or 0.0)
        except Exception:
            last_ts = 0.0
        # ts обязателен для сортировки, если нет id — ставим now
        if last_ts <= 0:
            last_ts = _now()
        try:
            ctx.store.chat_mark_read(int(u["id"]), chat_id, last_id=last_id, last_ts=last_ts)
        except AttributeError:
            # старый store без метода — создаём таблицу на лету
            try:
                with ctx.store._lock:
                    ctx.store._db.execute(
                        "CREATE TABLE IF NOT EXISTS chat_reads (user_id INTEGER NOT NULL, chat_id TEXT NOT NULL, "
                        "last_read_id INTEGER NOT NULL DEFAULT 0, last_read_ts REAL NOT NULL DEFAULT 0, "
                        "updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (user_id, chat_id))")
                    ctx.store._db.execute(
                        "INSERT INTO chat_reads(user_id, chat_id, last_read_id, last_read_ts, updated_at) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(user_id, chat_id) DO UPDATE SET "
                        "last_read_id=MAX(last_read_id, excluded.last_read_id), "
                        "last_read_ts=MAX(last_read_ts, excluded.last_read_ts), updated_at=excluded.updated_at",
                        (int(u["id"]), chat_id, last_id, last_ts, _now()))
                    ctx.store._db.commit()
            except Exception as e:
                log.debug("chat_mark_read fallback: %s", e)
                return JSONResponse({"ok": False, "error": "db"}, status_code=500)
        return {"ok": True, "chat_id": chat_id, "last_read_id": last_id, "last_read_ts": last_ts}

    @router.get("/api/chat/history")
    async def api_chat_history(request: Request, chat_id: str = "public",
                               before_id: int = 0, before_ts: float = 0.0, limit: int = 50):
        """A4: пагинация старше — кнопка 'загрузить ещё'."""
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        chat_id = (chat_id or "public").strip()[:64] or "public"
        try:
            lim = max(1, min(int(limit), MAX_CHAT_HISTORY))
        except Exception:
            lim = MAX_CHAT_HISTORY
        try:
            b_id = max(0, int(before_id))
        except Exception:
            b_id = 0
        try:
            b_ts = float(before_ts or 0.0)
        except Exception:
            b_ts = 0.0

        if chat_id == "public":
            # отдаём сообщения с id < before_id если указан, иначе последние lim
            if b_id > 0:
                # прямой запрос к таблице
                try:
                    with ctx.store._lock:
                        rows = ctx.store._db.execute(
                            "SELECT id, user_id, display_name, text, created_at, is_admin "
                            "FROM terminal_chat WHERE id < ? ORDER BY id DESC LIMIT ?",
                            (b_id, lim)).fetchall()
                    rows = list(reversed([dict(r) for r in rows]))
                except Exception:
                    rows = ctx.store.list_chat_messages(limit=lim, after_id=0)
                    rows = [r for r in rows if int(r.get("id") or 0) < b_id][-lim:]
            else:
                rows = ctx.store.list_chat_messages(limit=lim, after_id=0)
                # list_chat_messages возвращает ORDER BY id ASC limit last? Actually returns last? Let's ensure slice
                if len(rows) > lim:
                    rows = rows[-lim:]
            return {"ok": True, "chat_id": chat_id, "messages": [_msg_public(r) for r in rows],
                    "has_more": len(rows) >= lim}
        elif chat_id in ("services", "service", "svc"):
            u = _current_user(request)
            if not u:
                return _need_auth()
            try:
                # для пагинации старше используем прямой SQL: id < before_id
                if b_id > 0:
                    with ctx.store._lock:
                        rows = ctx.store._db.execute(
                            "SELECT id, user_id, kind, text, meta, created_at FROM user_service_chat "
                            "WHERE user_id=? AND id < ? ORDER BY id DESC LIMIT ?",
                            (int(u["id"]), b_id, lim)).fetchall()
                    rows = list(reversed([dict(r) for r in rows]))
                else:
                    rows = ctx.store.list_user_service_messages(user_id=int(u["id"]), limit=lim, after_id=0)
                    if len(rows) > lim:
                        rows = rows[-lim:]
            except Exception as e:
                log.debug("history services: %s", e)
                rows = []
            # формат как в service_chat
            def _pub(m):
                meta = m.get("meta") or {}
                if isinstance(meta, str):
                    try:
                        import json as _js
                        meta = _js.loads(meta)
                    except Exception:
                        meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                return {"id": int(m.get("id") or 0), "kind": str(m.get("kind") or "alert")[:32],
                        "text": str(m.get("text") or "")[:4000], "ts": float(m.get("created_at") or 0), "meta": meta}
            return {"ok": True, "chat_id": "services", "messages": [_pub(r) for r in rows],
                    "has_more": len(rows) >= lim}
        else:
            # для DM и support история уже есть в их модулях, но отдадим пусто чтобы не ломать фронт
            return {"ok": True, "chat_id": chat_id, "messages": [], "has_more": False}

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
