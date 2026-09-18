"""💬 Обратная связь: «по всем вопросам» из кабинета и ответы из админки.

Переписка живёт в базе, поэтому админ отвечает не «если сейчас онлайн», а
когда удобно: диалог и история никуда не пропадают. Один диалог на
пользователя — так не теряется контекст и видно, кто ждёт ответа.

    GET  /api/feedback              — диалог пользователя (и отметка «прочитано»)
    GET  /api/feedback/unread       — только счётчик непрочитанных ответов
    POST /api/feedback              — сообщение от пользователя
    GET  /api/admin/feedback        — список диалогов для админки
    GET  /api/admin/feedback/{id}   — переписка целиком
    POST /api/admin/feedback/{id}   — ответ админа

Уведомления — приятный бонус, а не условие работы: если бот подключён, админу
приходит сообщение в Telegram, а пользователю — ответ на его вопрос. Без бота
всё то же самое видно на сайте (в админке — счётчик непрочитанных).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("liqscope.feedback")

MAX_LEN = 2000
NOTIFY_GAP_SEC = 60          # не частим уведомлениями по одному диалогу
RATE_LIMIT = 12              # сообщений
RATE_WINDOW = 300.0          # за пять минут


class Ctx:
    """Связка с сервером: база, бот и адрес сайта — заполняет server.py."""

    store = None
    bot = None
    public_url = ""


ctx = Ctx()


class _Limiter:
    """Простое ограничение частоты: список времён на ключ."""

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
        return True


limiter = _Limiter()
_notified: Dict[int, float] = {}


def user_of(request: Request) -> Optional[dict]:
    from web_account import current_user
    return current_user(request)


def _admin(request: Request):
    user = user_of(request)
    if not user:
        return None, JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not user.get("is_admin"):
        return None, JSONResponse({"ok": False, "error": "admin"}, status_code=403)
    return user, None


def _public(user: Dict[str, Any]) -> Dict[str, Any]:
    """Публичная карточка автора: имя, @имя и почта — без служебных полей."""
    from accounts import display_name
    return {"id": user.get("id"),
            "name": display_name(user) or f"#{user.get('id')}",
            "username": user.get("username") or "",
            "admin": bool(user.get("is_admin"))}


def message_json(msg: Dict[str, Any], me_id: int) -> Dict[str, Any]:
    """Сообщение для интерфейса: видно, кто писал — я, админ или поддержка."""
    return {"id": msg.get("id"),
            "text": msg.get("text") or "",
            "at": msg.get("created_at") or 0,
            "mine": bool(msg.get("author_id")) and int(msg.get("author_id")) == int(me_id),
            "admin": bool(msg.get("is_admin"))}


async def notify_admin(thread: Dict[str, Any], user: Dict[str, Any],
                       text: str, first: bool) -> None:
    """Стукнуть админов в Telegram: пришёл вопрос, ответить можно в админке."""
    bot = ctx.bot
    if bot is None or not getattr(bot, "enabled", False):
        return
    tid = int(thread.get("id") or 0)
    now = time.time()
    if not first and now - _notified.get(tid, 0.0) < NOTIFY_GAP_SEC:
        return
    _notified[tid] = now
    who = _public(user)
    from tg_bot import _esc
    link = (ctx.public_url or "").rstrip("/") + "/admin"
    body = (f"💬 <b>Обратная связь</b> · {_esc(who['name'])}"
            + (f" ({_esc(who['username'])})" if who["username"] else "")
            + f"\n\n{_esc(text[:600])}"
            + f'\n\n<a href="{link}">Ответить в админке</a>')
    try:
        ids = list(bot._admin_tg_ids() or [])
    except Exception as e:                              # noqa: BLE001
        log.debug("админы бота: %s", e)
        return
    for tg_id in ids:
        try:
            await bot.send(tg_id, body, raw=True)
        except Exception as e:                          # noqa: BLE001
            log.debug("уведомление админу %s: %s", tg_id, e)


async def notify_user(user: Dict[str, Any], text: str) -> None:
    """Ответ ушёл — скажем об этом в Telegram, если аккаунт туда привязан."""
    bot = ctx.bot
    tg_id = user.get("tg_id")
    if bot is None or not tg_id or not getattr(bot, "enabled", False):
        return
    from tg_bot import _esc
    link = (ctx.public_url or "").rstrip("/") + "/cabinet"
    body = (f"💬 <b>Ответ от LiqScope</b>\n\n{_esc(text[:800])}"
            f'\n\n<a href="{link}">Продолжить в кабинете</a>')
    try:
        await bot.send(int(tg_id), body, raw=True)
    except Exception as e:                              # noqa: BLE001
        log.debug("уведомление пользователю %s: %s", tg_id, e)


def register_feedback_routes(app) -> None:
    router = APIRouter()

    # --- пользователь ------------------------------------------------------
    @router.get("/api/feedback")
    async def api_feedback(request: Request):
        """Диалог пользователя. Открытие диалога = ответы прочитаны."""
        user = user_of(request)
        if not user:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        store = ctx.store
        if store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        data = store.feedback_for_user(user["id"])
        if data.get("thread"):
            store.feedback_mark_read(data["thread"]["id"], "user")
        me = _public(user)
        return {"ok": True, "me": me,
                "messages": [message_json(m, user["id"]) for m in data["messages"]],
                # диалог открыт — ответы прочитаны этим же запросом
                "unread": 0 if data.get("thread") else data["unread"],
                "max_len": MAX_LEN}

    @router.get("/api/feedback/unread")
    async def api_feedback_unread(request: Request):
        user = user_of(request)
        if not user:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        return {"ok": True, "unread": ctx.store.feedback_unread(user["id"])}

    @router.post("/api/feedback")
    async def api_feedback_send(request: Request, body: Optional[dict] = Body(default=None)):
        user = user_of(request)
        if not user:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        store = ctx.store
        if store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        text = str((body or {}).get("text") or "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty",
                                 "hint": "Пустое сообщение отправлять нечего."},
                                status_code=400)
        if len(text) > MAX_LEN:
            return JSONResponse({"ok": False, "error": "too_long",
                                 "hint": f"Слишком длинное сообщение (до {MAX_LEN} знаков)."},
                                status_code=400)
        if not limiter.allow(f"u{user['id']}"):
            return JSONResponse({"ok": False, "error": "too_fast",
                                 "hint": "Слишком часто: подождите пару минут."},
                                status_code=429)
        thread = store.feedback_thread(user["id"])
        first = not store.feedback_messages(thread["id"])
        msg = store.feedback_add(thread["id"], text, author_id=user["id"])
        if not msg.get("ok"):
            return JSONResponse(msg, status_code=400)
        try:
            await notify_admin(thread, user, text, first)
        except Exception as e:                          # noqa: BLE001
            log.debug("уведомление админов: %s", e)
        return {"ok": True, "message": message_json(msg, user["id"]),
                "unread": store.feedback_unread(user["id"])}

    # --- админ -------------------------------------------------------------
    @router.get("/api/admin/feedback")
    async def api_admin_feedback(request: Request):
        admin, err = _admin(request)
        if err:
            return err
        store = ctx.store
        if store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        threads = store.feedback_threads()
        return {"ok": True, "threads": threads,
                "unread": store.feedback_admin_unread(),
                "me": _public(admin), "max_len": MAX_LEN}

    @router.get("/api/admin/feedback/{thread_id}")
    async def api_admin_thread(request: Request, thread_id: int):
        admin, err = _admin(request)
        if err:
            return err
        store = ctx.store
        threads = {int(t["id"]): t for t in store.feedback_threads()}
        thread = threads.get(int(thread_id))
        if not thread:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        store.feedback_mark_read(thread_id, "admin")
        return {"ok": True, "thread": thread,
                "messages": [message_json(m, admin["id"])
                             for m in store.feedback_messages(thread_id)],
                "unread": store.feedback_admin_unread()}

    @router.post("/api/admin/feedback/{thread_id}")
    async def api_admin_reply(request: Request, thread_id: int,
                              body: Optional[dict] = Body(default=None)):
        admin, err = _admin(request)
        if err:
            return err
        store = ctx.store
        threads = {int(t["id"]): t for t in store.feedback_threads()}
        thread = threads.get(int(thread_id))
        if not thread:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        text = str((body or {}).get("text") or "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty",
                                 "hint": "Пустой ответ отправить нельзя."}, status_code=400)
        if len(text) > MAX_LEN:
            return JSONResponse({"ok": False, "error": "too_long",
                                 "hint": f"Слишком длинный ответ (до {MAX_LEN} знаков)."},
                                status_code=400)
        if not limiter.allow(f"a{admin['id']}"):
            return JSONResponse({"ok": False, "error": "too_fast",
                                 "hint": "Слишком часто: подождите пару минут."},
                                status_code=429)
        msg = store.feedback_add(thread_id, text, author_id=admin["id"], is_admin=True)
        if not msg.get("ok"):
            return JSONResponse(msg, status_code=400)
        user = None
        try:
            user = store.get_user(int(thread.get("user_id") or 0))
        except Exception as e:                          # noqa: BLE001
            log.debug("пользователь диалога: %s", e)
        if user:
            try:
                await notify_user(user, text)
            except Exception as e:                      # noqa: BLE001
                log.debug("уведомление пользователя: %s", e)
        return {"ok": True, "message": message_json(msg, admin["id"]),
                "unread": store.feedback_admin_unread()}

    app.include_router(router)
