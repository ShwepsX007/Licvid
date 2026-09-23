"""🆘 Поддержка — персональные чаты per-user + guest.

- Каждый пользователь (или гость с guest_token) имеет свой личный тред.
- Гость обязан ввести временный ник (display_name) — это и есть идентификатор для ответа.
- Админ видит все треды через /api/chat/support/threads и отвечает указывая thread_key.
- Бот шлёт уведомление админам при новом сообщении от пользователя/гостя.

Эндпоинты:
    GET  /api/chat/support?limit=&after_id=&guest_token=&thread_key= — личные сообщения
    GET  /api/chat/support/threads — список тредов (admin)
    POST /api/chat/support {text, name?, guest_token?, thread_key?} — написать
    DELETE /api/chat/support/{id} — удалить (admin)
    GET  /api/chat/support/me — кто я
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
    bot = None
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
    # схлопываем длинные пробелы/переносы, но оставляем одиночные пробелы и переносы
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
        "guest_token": str(m.get("guest_token") or "")[:80],
        "thread_key": f"u:{int(m.get('user_id'))}" if m.get("user_id") else f"g:{str(m.get('guest_token') or '')}" if m.get("guest_token") else "",
    }


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_thread_key(tk: str):
    tk = str(tk or "").strip()
    if not tk:
        return None, ""
    if tk.startswith("u:"):
        try:
            uid = int(tk[2:])
            return uid, ""
        except Exception:
            return None, ""
    if tk.startswith("g:"):
        gt = tk[2:].strip()[:80]
        return None, gt
    return None, ""


async def _notify_admins_via_bot(text: str, thread_key: str, display_name: str, is_guest: bool):
    bot = ctx.bot
    store = ctx.store
    if not bot or not store:
        return
    try:
        if not getattr(bot, "running", False):
            return
    except Exception:
        pass
    try:
        admin_ids = store.admin_tg_ids() if hasattr(store, "admin_tg_ids") else []
    except Exception:
        admin_ids = []
    if not admin_ids:
        return
    who = f"👤 {display_name}" + (" (гость)" if is_guest else "")
    preview = text[:400]
    body = f"🆘 <b>Новое в поддержке</b>\n{who}\nТред: <code>{thread_key}</code>\n\n{preview}"
    for tg_id in admin_ids:
        try:
            await bot.send(int(tg_id), body, raw=True)
        except Exception as e:
            log.debug("support bot notify %s: %s", tg_id, e)


async def _broadcast_support(msg_public: dict, target_user_id=None, target_guest_token=""):
    hub = ctx.hub
    store = ctx.store
    if hub is None:
        return
    # determine admin user_ids for broadcast
    admin_user_ids = set()
    try:
        if store:
            # fetch all admins from users table? Use admin_tg_ids not enough; need user ids.
            # We'll try list_users and filter is_admin, or query directly.
            # Simple: get all users where is_admin=1 via store._db if available, else fallback to broadcast to all with admin check.
            with store._lock:
                rows = store._db.execute("SELECT id FROM users WHERE is_admin=1 AND is_banned=0").fetchall()
                admin_user_ids = {int(r["id"]) for r in rows}
    except Exception:
        admin_user_ids = set()

    payload = {"type": "support_chat", "message": msg_public}
    if target_user_id:
        uid = int(target_user_id)
        # also include thread_key in payload for admin UI
        payload["thread_key"] = f"u:{uid}"

        async def _pred(c):
            try:
                cid = int(getattr(c, "user_id", 0) or 0)
                if cid == uid:
                    return True
                if cid in admin_user_ids:
                    return True
                # also check if client user is admin via store lookup (fallback)
                if store and cid:
                    u = store.get_user(cid)
                    if u and u.get("is_admin"):
                        return True
                return False
            except Exception:
                return False

        try:
            await hub.broadcast(payload, predicate=lambda c: True if False else False)  # placeholder to avoid lambda capture issues
            # actually we need async predicate? hub.broadcast expects sync predicate.
            # We'll use sync version checking user_id in closure.
            await hub.broadcast(payload, predicate=lambda c, _uid=uid, _admins=admin_user_ids: (
                int(getattr(c, "user_id", 0) or 0) == _uid or int(getattr(c, "user_id", 0) or 0) in _admins
            ))
        except Exception as e:
            log.debug("support broadcast user: %s", e)
    elif target_guest_token:
        gt = str(target_guest_token)[:80]
        payload["thread_key"] = f"g:{gt}"
        # for guest, broadcast to admins + optionally to all (guest clients have no user_id)
        # we broadcast to admins via predicate, and also broadcast to all with guest_token match?
        # simplest: broadcast to admins only via hub, guest will poll via REST.
        try:
            await hub.broadcast(payload, predicate=lambda c, _admins=admin_user_ids: (
                int(getattr(c, "user_id", 0) or 0) in _admins
            ))
            # also broadcast to all for guest realtime if they have WS (user_id None)
            # we send second broadcast to clients with user_id None and include guest_token
            await hub.broadcast(payload, predicate=lambda c: not getattr(c, "user_id", None))
        except Exception as e:
            log.debug("support broadcast guest: %s", e)
    else:
        # fallback: broadcast to admins only
        try:
            await hub.broadcast(payload, predicate=lambda c, _admins=admin_user_ids: (
                int(getattr(c, "user_id", 0) or 0) in _admins
            ))
        except Exception as e:
            log.debug("support broadcast fallback: %s", e)


async def scan_support_reminders() -> int:
    """Напоминания админам о безответных обращениях в поддержку.

    Логика как у личных чатов: если пользователь/гость написал, а админ не прочитал
    и не ответил за delay минут (настройка chat_dm_tg_delay_min), шлём напоминание
    в Telegram админам один раз на сообщение.
    """
    store = ctx.store
    bot = ctx.bot
    if store is None or bot is None:
        return 0
    try:
        # задержка из настроек, 0 = выкл
        raw = str(store.get_setting("chat_dm_tg_delay_min", "10") or "10")
        dmin = float(raw.strip() or 10)
    except Exception:
        dmin = 10.0
    dmin = max(0.0, min(dmin, 24 * 60.0))
    if dmin <= 0 or not getattr(bot, "running", False):
        return 0
    edge = _now() - dmin * 60.0
    try:
        import asyncio
        due = await asyncio.to_thread(store.support_rooms_due, edge)
    except Exception as e:
        log.debug("support reminders due: %s", e)
        return 0
    if not due:
        return 0
    try:
        admin_ids = store.admin_tg_ids() if hasattr(store, "admin_tg_ids") else []
    except Exception:
        admin_ids = []
    if not admin_ids:
        return 0
    # check if any admin is online in chat — если админ онлайн, не спамим, как в ЛС
    try:
        from terminal_chat import chat_presence_online
        # если хоть один админ онлайн — пропускаем напоминание (бейдж и так горит)
        for aid in admin_ids:
            # admin_ids are tg_id, not user_id; we need user ids of admins
            pass
        # попробуем по user_id админов
        with store._lock:
            admin_user_ids = [int(r["id"]) for r in store._db.execute("SELECT id FROM users WHERE is_admin=1 AND is_banned=0").fetchall()]
        if any(chat_presence_online(int(uid)) for uid in admin_user_ids):
            # админ на сайте — не шлём TG, только WS бейдж
            return 0
    except Exception:
        pass

    sent = 0
    for row in due:
        tkey = row.get("thread_key") or ""
        display_name = row.get("display_name") or "Гость" if row.get("guest_token") else f"id{row.get('user_id')}"
        is_guest = bool(row.get("guest_token"))
        preview = str(row.get("text") or "")[:300]
        who = f"👤 {display_name}" + (" (гость)" if is_guest else "")
        body = f"⏰ <b>Поддержка без ответа</b>\n{who}\nТред: <code>{tkey}</code>\n\n{preview}\n\n<i>Админ не ответил {int(dmin)} мин — напоминание</i>"
        for tg_id in admin_ids:
            try:
                await bot.send(int(tg_id), body, raw=True)
            except Exception as e:
                log.debug("support reminder %s: %s", tg_id, e)
                continue
        # помечаем как напомнили
        try:
            import asyncio
            await asyncio.to_thread(store.support_reminder_mark, tkey, int(row.get("message_id") or 0))
        except Exception:
            pass
        sent += 1
    return sent


def register_support_chat_routes(app, hub=None) -> None:
    if hub is not None:
        ctx.hub = hub
    router = APIRouter()

    @router.get("/api/chat/support")
    async def api_list(request: Request, limit: int = 100, after_id: int = 0,
                       guest_token: str = "", thread_key: str = "", user_id: int = 0):
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
        u = _current_user(request)
        gt_q = str(guest_token or request.query_params.get("guest_token") or "")[:80]
        tk_q = str(thread_key or request.query_params.get("thread_key") or "").strip()
        uid_q = 0
        try:
            uid_q = int(user_id or request.query_params.get("user_id") or 0)
        except Exception:
            uid_q = 0

        # admin viewing specific thread
        if u and u.get("is_admin") and tk_q:
            t_uid, t_gt = _parse_thread_key(tk_q)
            rows = ctx.store.list_support_messages(limit=lim, after_id=aft,
                                                   user_id=t_uid, guest_token=t_gt, thread_key=tk_q)
            # mark admin read
            try:
                if rows:
                    last_id = max(int(r.get("id") or 0) for r in rows)
                    ctx.store.support_mark_read(tk_q, who="admin", last_id=last_id)
            except Exception:
                pass
            return {"ok": True, "messages": [_msg_public(r) for r in rows], "now": _now(), "thread_key": tk_q}

        if u and u.get("is_admin") and (uid_q or gt_q):
            rows = ctx.store.list_support_messages(limit=lim, after_id=aft,
                                                   user_id=uid_q if uid_q else None,
                                                   guest_token=gt_q, thread_key="")
            tk = f"u:{uid_q}" if uid_q else f"g:{gt_q}" if gt_q else ""
            try:
                if rows and tk:
                    last_id = max(int(r.get("id") or 0) for r in rows)
                    ctx.store.support_mark_read(tk, who="admin", last_id=last_id)
            except Exception:
                pass
            return {"ok": True, "messages": [_msg_public(r) for r in rows], "now": _now(), "thread_key": tk}

        # registered user — only own thread
        if u:
            rows = ctx.store.list_support_messages(limit=lim, after_id=aft,
                                                   user_id=int(u["id"]), guest_token="", thread_key="")
            try:
                if rows:
                    last_id = max(int(r.get("id") or 0) for r in rows)
                    ctx.store.support_mark_read(f"u:{int(u['id'])}", who="user", last_id=last_id)
            except Exception:
                pass
            return {"ok": True, "messages": [_msg_public(r) for r in rows], "now": _now(), "thread_key": f"u:{int(u['id'])}"}

        # guest — requires guest_token
        if gt_q:
            rows = ctx.store.list_support_messages(limit=lim, after_id=aft,
                                                   user_id=None, guest_token=gt_q, thread_key="")
            try:
                if rows:
                    last_id = max(int(r.get("id") or 0) for r in rows)
                    ctx.store.support_mark_read(f"g:{gt_q}", who="user", last_id=last_id)
            except Exception:
                pass
            return {"ok": True, "messages": [_msg_public(r) for r in rows], "now": _now(), "thread_key": f"g:{gt_q}"}

        # no auth and no guest_token — forbid, since support is personal
        return JSONResponse({"ok": False, "error": "need_guest_token", "hint": "Укажите ник гостя"}, status_code=401)

    @router.get("/api/chat/support/threads")
    async def api_threads(request: Request, limit: int = 100):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u or not u.get("is_admin"):
            return JSONResponse({"ok": False, "error": "admin"}, status_code=403)
        try:
            lim = max(1, min(int(limit), 200))
        except Exception:
            lim = 100
        try:
            threads = ctx.store.list_support_threads(limit=lim)
        except Exception as e:
            log.debug("threads list: %s", e)
            threads = []
        return {"ok": True, "threads": threads, "now": _now()}

    @router.post("/api/chat/support")
    async def api_post(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = await _json_body(request)
        txt = _clean_text(body.get("text") or "", limit=MAX_LEN)
        if not txt:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        u = _current_user(request)
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

        # admin reply path
        if u and u.get("is_admin"):
            tk = str(body.get("thread_key") or body.get("thread") or "")[:100]
            target_uid = 0
            target_gt = ""
            if tk:
                t_uid, t_gt = _parse_thread_key(tk)
                target_uid = t_uid or 0
                target_gt = t_gt or ""
            else:
                # alternative: explicit user_id / guest_token in body
                try:
                    target_uid = int(body.get("user_id") or body.get("target_user_id") or 0)
                except Exception:
                    target_uid = 0
                target_gt = str(body.get("guest_token") or body.get("target_guest_token") or "")[:80]

            if target_uid or target_gt:
                # admin replying to a specific thread
                admin_name = (u.get("display_name") or u.get("first_name") or "Админ")[:80]
                rec = ctx.store.add_support_message(
                    target_uid if target_uid else None,
                    admin_name,
                    txt,
                    is_admin=True,
                    guest_token=target_gt,
                )
                if not rec.get("ok"):
                    return JSONResponse(rec, status_code=400)
                msg = _msg_public(rec)
                # broadcast to target user/guest + admins
                import asyncio
                asyncio.create_task(_broadcast_support(msg,
                                                       target_user_id=target_uid if target_uid else None,
                                                       target_guest_token=target_gt))
                return {"ok": True, "message": msg, "thread_key": rec.get("thread_key") or tk}

            # admin writing as self? treat as own thread? but admin's own support is also personal
            # fallback to personal
            name = (u.get("display_name") or u.get("first_name") or f"id{u['id']}")[:80]
            rec = ctx.store.add_support_message(int(u["id"]), name, txt, is_admin=True, guest_token="")
            if not rec.get("ok"):
                return JSONResponse(rec, status_code=400)
            msg = _msg_public(rec)
            import asyncio
            asyncio.create_task(_broadcast_support(msg, target_user_id=int(u["id"])))
            return {"ok": True, "message": msg}

        if u:
            name = (u.get("display_name") or u.get("first_name") or u.get("username") or f"id{u['id']}")[:80]
            guest_token = ""
            user_id = int(u["id"])
            is_admin = False
            rec = ctx.store.add_support_message(user_id, name, txt, is_admin, guest_token)
            if not rec.get("ok"):
                return JSONResponse(rec, status_code=400)
            msg = _msg_public(rec)
            import asyncio
            asyncio.create_task(_broadcast_support(msg, target_user_id=user_id))
            asyncio.create_task(_notify_admins_via_bot(txt, f"u:{user_id}", name, False))
            return {"ok": True, "message": msg, "thread_key": f"u:{user_id}"}
        else:
            # guest: must provide name (temporary nick) and guest_token
            raw_name = str(body.get("name") or body.get("display_name") or "").strip()[:40]
            if not raw_name:
                return JSONResponse({"ok": False, "error": "need_name", "hint": "Введите временный ник"}, status_code=400)
            name = raw_name
            guest_token = str(body.get("guest_token") or "")[:80]
            if not guest_token:
                guest_token = secrets.token_hex(6)
            # basic validation: guest_token alphanumeric?
            if len(guest_token) < 4:
                guest_token = secrets.token_hex(6)
            user_id = None
            is_admin = False
            rec = ctx.store.add_support_message(user_id, name, txt, is_admin, guest_token)
            if not rec.get("ok"):
                return JSONResponse(rec, status_code=400)
            msg = _msg_public(rec)
            import asyncio
            asyncio.create_task(_broadcast_support(msg, target_guest_token=guest_token))
            asyncio.create_task(_notify_admins_via_bot(txt, f"g:{guest_token}", name, True))
            return {"ok": True, "message": msg, "thread_key": f"g:{guest_token}", "guest_token": guest_token}

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
            # guest — return guest_token from query if any?
            gt = request.query_params.get("guest_token") or ""
            return {"ok": True, "user": None, "guest_token": gt}
        return {
            "ok": True,
            "user": {
                "id": u["id"],
                "name": u.get("display_name") or u.get("first_name") or "",
                "is_admin": bool(u.get("is_admin")),
            },
        }

    @router.get("/api/chat/support/unread")
    async def api_unread(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        u = _current_user(request)
        if not u or not u.get("is_admin"):
            return JSONResponse({"ok": False, "error": "admin"}, status_code=403)
        try:
            cnt = ctx.store.support_unread_counts()
        except Exception:
            cnt = {"threads": 0}
        return {"ok": True, **cnt}

    app.include_router(router)
