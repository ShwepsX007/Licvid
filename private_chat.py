"""🔒 Приватные диалоги: личный чат двух пользователей, 3 дня истории.

Идентификатор собеседника — ``user_id``: смена ника на диалог не влияет,
имена подставляются актуальные из профиля при чтении.

Порядок: один юзер приглашает другого (``POST /api/chat/dm/invite``), второй
принимает приглашение кнопкой или просто ответным сообщением — комната
становится ``active``. Пока только приглашение, писать может инициатор.

Уведомления:
  * на сайте — счётчик непрочитанных (бейдж на кнопке чата, подсветка панели);
  * в Telegram — если получатель зарегистрирован в боте, сидит не на сайте
    и не ответил, через ``chat_dm_tg_delay_min`` минут (настройка в админке,
    0 — выключено) бот пришлёт одно напоминание на сообщение.

Эндпоинты:
    GET  /api/chat/dm/rooms                       — мои комнаты
    GET  /api/chat/dm/notify                      — счётчики для бейджа
    POST /api/chat/dm/invite {peer_id|name}       — пригласить (или найти пару)
    POST /api/chat/dm/{room}/accept {accept}      — принять / отклонить
    GET  /api/chat/dm/{room}/messages?after_id=   — история (вход = «прочитано»)
    POST /api/chat/dm/{room}/messages {text}      — отправить
    GET  /api/chat/users?q=                       — кандидаты для приглашения

``scan_reminders()`` зовёт фоновый цикл server.py раз в ~минуту.
"""

from __future__ import annotations

import html
import logging
import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from terminal_chat import _clean_text, _current_user, _need_auth, ctx as public_ctx

log = logging.getLogger("liqscope.private_chat")


class Ctx:
    store = None          # accounts.Store (приватные комнаты живут там же)
    bot = None            # tg_bot.TelegramBot — для напоминаний в личку
    hub = None            # WS hub server.py — адресные рассылки участникам
    public_url = ""


ctx = Ctx()

# Настройка админки: через сколько минут бот напоминает о безответном ЛС.
DELAY_SETTING = "chat_dm_tg_delay_min"
DELAY_DEFAULT_MIN = 10.0
DM_MAX_LEN = 500

# флуд-контроль ЛС: строже публичного чата — приватность не должна
# превращаться в спам-пушку
_RATE_LOCK = threading.Lock()
_RATE: Dict[int, List[float]] = {}
RATE_LIMIT = 20          # сообщений
RATE_WINDOW = 60.0       # секунд
RATE_SAME_SEC = 1.2      # пауза между сообщениями одного юзера

# приглашения: не больше нескольких в час на пользователя
_INV_LOCK = threading.Lock()
_INVITES: Dict[int, List[float]] = {}
INVITE_LIMIT = 10
INVITE_WINDOW = 3600.0


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


def _invite_ok(user_id: int) -> bool:
    now = _now()
    uid = int(user_id)
    with _INV_LOCK:
        hits = [t for t in _INVITES.get(uid, []) if now - t < INVITE_WINDOW]
        if len(hits) >= INVITE_LIMIT:
            _INVITES[uid] = hits
            return False
        hits.append(now)
        _INVITES[uid] = hits
    return True


def _store():
    return ctx.store or public_ctx.store


def _err(code: str, status: int, hint: str = "") -> JSONResponse:
    body = {"ok": False, "error": code}
    if hint:
        body["hint"] = hint
    return JSONResponse(body, status_code=status)


def _room_json(store, room: dict, me_id: int) -> dict:
    """Карточка комнаты для клиента: peer, статус, последнее сообщение."""
    other = store.private_room_other(room, me_id)
    u = store.get_user(other) or {}
    return {
        "id": int(room["id"]),
        "status": room.get("status") or "pending",
        "invited_by": int(room.get("invited_by") or 0),
        "peer": {
            "id": other,
            "name": u.get("display_name") or f"id{other}",
            "banned": bool(u.get("is_banned")),
        },
        "updated_at": float(room.get("updated_at") or 0),
        "created_at": float(room.get("created_at") or 0),
    }


def _msg_json(m: dict, me_id: int) -> dict:
    return {
        "id": int(m.get("id") or 0),
        "room_id": int(m.get("room_id") or 0),
        "user_id": int(m.get("user_id") or 0),
        "name": str(m.get("name") or "")[:80],
        "text": str(m.get("text") or "")[:DM_MAX_LEN],
        "ts": float(m.get("created_at") or 0),
        "admin": bool(m.get("is_admin")),
        "mine": int(m.get("user_id") or 0) == int(me_id),
    }


async def _bcast_user(user_id: int, payload: dict) -> None:
    """WS-событие только клиентам конкретного пользователя (если hub есть)."""
    hub = ctx.hub
    if hub is None:
        return
    uid = int(user_id)
    try:
        await hub.broadcast(payload, predicate=lambda c: getattr(c, "user_id", None) == uid)
    except Exception as e:  # noqa: BLE001
        log.debug("chat_dm bcast: %s", e)


def delay_min() -> float:
    """Задержка TG-напоминания в минутах из настроек сайта (0 = выключено)."""
    store = _store()
    if store is None:
        return DELAY_DEFAULT_MIN
    try:
        raw = str(store.get_setting(DELAY_SETTING, str(DELAY_DEFAULT_MIN)) or "")
    except Exception:  # noqa: BLE001
        return DELAY_DEFAULT_MIN
    try:
        v = float(raw.strip() or DELAY_DEFAULT_MIN)
    except ValueError:
        return DELAY_DEFAULT_MIN
    return max(0.0, min(v, 24 * 60.0))


async def scan_reminders() -> int:
    """Разослать зависевшиеся напоминания о безответных личных сообщениях.

    Зовётся фоном из server.py. Правила:
      * получатель привязал Telegram (зарегистрирован в боте);
      * с момента сообщения прошло >= delay минут;
      * получатель не читал и не ответил в комнате;
      * получателя нет на сайте прямо сейчас (presence чата) — иначе бейджид
        и так жжётся, а TG-письмо было бы шумом;
      * об этом конкретном сообщении напоминаем ровно один раз.
    """
    store = _store()
    bot = ctx.bot
    if store is None:
        return 0
    dmin = delay_min()
    if dmin <= 0 or bot is None or not getattr(bot, "running", False):
        return 0
    edge = _now() - dmin * 60.0
    try:
        due = await _to_thread(store.private_rooms_due, edge)
    except Exception as e:  # noqa: BLE001
        log.debug("scan reminders: %s", e)
        return 0
    sent = 0
    for row in due:
        member = int(row["member"])
        u = store.get_user(member)
        tg_id = (u or {}).get("tg_id")
        if not u or not tg_id or u.get("is_banned"):
            continue
        # не беспокоим, если человек прямо сейчас на сайте с чатом:
        # бейдж у него и так горит, TG-письмо было бы шумом
        if is_chat_online(member):
            continue
        sender = store.get_user(int(row["sender_id"])) or {}
        sname = sender.get("display_name") or f"id{row['sender_id']}"
        preview = html.escape(str(row.get("text") or "")[:180])
        link = (ctx.public_url or "").rstrip("/") + f"/cabinet?chat=1&room={int(row['room_id'])}"
        body = ("<b>💬 Личное сообщение — LiqScope</b>\n\n"
                f"<b>{html.escape(str(sname))}</b>: {preview}\n\n"
                f'Ответьте на сайте, и напоминание не понадобится: <a href="{link}">открыть чат</a>')
        try:
            markup = None
            try:
                if callable(getattr(bot, "site_link_kb", None)):
                    markup = bot.site_link_kb(
                        "открыть чат", f"/cabinet?chat=1&room={int(row['room_id'])}")
            except Exception:  # noqa: BLE001
                markup = None
            ok = await bot.send(int(tg_id), body, markup=markup, raw=True)
        except Exception as e:  # noqa: BLE001
            log.debug("tg reminder %s→%s: %s", row["sender_id"], tg_id, e)
            continue
        if ok:
            await _to_thread(store.private_reminder_mark, int(row["room_id"]),
                             member, int(row["message_id"]))
            sent += 1
    return sent


def is_chat_online(user_id: int) -> bool:
    """Есть ли пользователь в числе «онлайн в чате» (см. terminal_chat)."""
    try:
        from terminal_chat import chat_presence_online
        return chat_presence_online(int(user_id))
    except Exception:  # noqa: BLE001
        return False


async def _to_thread(fn, *args):
    import asyncio
    return await asyncio.to_thread(fn, *args)


def register_private_chat_routes(app) -> None:
    router = APIRouter()

    @router.get("/api/chat/dm/rooms")
    async def api_rooms(request: Request):
        u = _current_user(request)
        if not u:
            return _need_auth()
        store = _store()
        if store is None:
            return _err("no_store", 503)
        try:
            rooms = store.private_rooms_for(u["id"])
        except Exception as e:  # noqa: BLE001
            log.debug("dm rooms: %s", e)
            return _err("db", 500)
        return {"ok": True, "rooms": rooms, "now": _now()}

    @router.get("/api/chat/dm/notify")
    async def api_notify(request: Request):
        u = _current_user(request)
        if not u:
            return {"ok": True, "unread": 0, "invites": 0, "auth": False}
        store = _store()
        if store is None:
            return _err("no_store", 503)
        c = store.private_notify_counts(u["id"])
        return {"ok": True, "unread": int(c["unread"]), "invites": int(c["invites"]),
                "auth": True}

    @router.get("/api/chat/users")
    async def api_chat_users(request: Request, q: str = ""):
        """Кандидаты в личный диалог: поиск по началу ника + кто писал в чат."""
        u = _current_user(request)
        if not u:
            return _need_auth()
        store = _store()
        if store is None:
            return _err("no_store", 503)
        q = (q or "").strip()
        found = store.find_chat_users(q, exclude_id=u["id"]) if len(q) >= 2 else []
        if not q:
            # без запроса — предложим участников общего чата за 3 дня
            found = [p for p in store.chat_participants(limit=30)
                     if p["id"] != int(u["id"])]
        elif len(q) >= 2 and not found:
            # не нашли по началу ника — поиск подстрокой по участникам чата
            found = [p for p in store.chat_participants(limit=50)
                     if p["id"] != int(u["id"]) and q.lower() in p["name"].lower()]
        return {"ok": True, "users": found[:12]}

    @router.post("/api/chat/dm/invite")
    async def api_invite(request: Request):
        u = _current_user(request)
        if not u:
            return _need_auth()
        store = _store()
        if store is None:
            return _err("no_store", 503)
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            data = {}
        peer_id = 0
        try:
            peer_id = int(data.get("peer_id") or 0)
        except (TypeError, ValueError):
            peer_id = 0
        if not peer_id:
            name = str(data.get("name") or "").strip()
            hits = store.find_chat_users(name, limit=1, exclude_id=u["id"])
            if not hits:
                return _err("not_found", 404, "Пользователь не найден")
            peer_id = int(hits[0]["id"])
        if peer_id == int(u["id"]):
            return _err("self", 400, "Себе писать неинтересно 🙂")
        peer = store.get_user(peer_id)
        if not peer or peer.get("is_banned"):
            return _err("not_found", 404, "Пользователь не найден")
        if not _invite_ok(u["id"]):
            return _err("rate", 429, "Слишком много приглашений — подождите")
        r = await _to_thread(store.private_room_open, u["id"], peer_id)
        if not r.get("ok"):
            return _err(r.get("error") or "fail", 400)
        room = r["room"]
        summary = _room_json(store, room, u["id"])
        last = None
        msgs = store.private_room_messages(room["id"], 0, 1)
        if msgs:
            last = _msg_json(msgs[-1], u["id"])
        summary["last"] = last
        summary["unread"] = 0
        # приглашение видно второму участнику: бейдж в кабинете и в терминале
        await _bcast_user(summary["peer"]["id"], {
            "type": "chat_dm_room", "room": summary, "event": "invite"})
        return {"ok": True, "room": summary, "created": bool(r.get("created"))}

    @router.post("/api/chat/dm/{room_id}/accept")
    async def api_accept(request: Request, room_id: int):
        u = _current_user(request)
        if not u:
            return _need_auth()
        store = _store()
        if store is None:
            return _err("no_store", 503)
        room = store.private_room_get(room_id)
        if not room or not store.private_room_member(room, u["id"]):
            return _err("not_found", 404)
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            data = {}
        accept = bool(data.get("accept", True))
        if room.get("status") != "pending":
            return _err("status", 400, "Приглашение уже обработано")
        if int(u["id"]) == int(room.get("invited_by") or 0):
            # принимать может только приглашённый
            return _err("forbidden", 403, "Принимать может только приглашённый")
        store.private_room_set_status(room_id, "active" if accept else "declined")
        room = store.private_room_get(room_id) or room
        summary = _room_json(store, room, u["id"])
        other = summary["peer"]["id"]
        await _bcast_user(other, {"type": "chat_dm_room", "room": summary,
                                  "event": "accepted" if accept else "declined"})
        return {"ok": True, "room": summary}

    @router.get("/api/chat/dm/{room_id}/messages")
    async def api_messages(request: Request, room_id: int, after_id: int = 0):
        u = _current_user(request)
        if not u:
            return _need_auth()
        store = _store()
        if store is None:
            return _err("no_store", 503)
        room = store.private_room_get(room_id)
        if not room or not store.private_room_member(room, u["id"]):
            return _err("not_found", 404)
        if room.get("status") == "declined":
            return _err("declined", 403, "Приглашение отклонено")
        rows = store.private_room_messages(room_id, after_id, 200)
        msgs = [_msg_json(m, u["id"]) for m in rows]
        last_id = int(max((m["id"] for m in msgs), default=after_id or 0))
        if last_id:
            store.private_mark_read(room_id, u["id"], last_id)
        summary = _room_json(store, store.private_room_get(room_id) or room, u["id"])
        return {"ok": True, "messages": msgs, "room": summary, "now": _now()}

    @router.post("/api/chat/dm/{room_id}/messages")
    async def api_send(request: Request, room_id: int):
        u = _current_user(request)
        if not u:
            return _need_auth()
        store = _store()
        if store is None:
            return _err("no_store", 503)
        room = store.private_room_get(room_id)
        if not room or not store.private_room_member(room, u["id"]):
            return _err("not_found", 404)
        status = room.get("status") or "pending"
        invited_by = int(room.get("invited_by") or 0)
        other = store.private_room_other(room, u["id"])
        if status == "declined":
            return _err("declined", 403, "Приглашение отклонено — отправьте новое")
        if status == "pending" and int(u["id"]) != invited_by:
            # ответ приглашённого = молчаливое согласие
            store.private_room_set_status(room_id, "active")
            status = "active"
            await _bcast_user(invited_by, {
                "type": "chat_dm_room",
                "room": _room_json(store, store.private_room_get(room_id) or room,
                                    invited_by),
                "event": "accepted"})
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            data = {}
        txt = _clean_text(data.get("text") or "", limit=DM_MAX_LEN)
        if not txt:
            return _err("empty", 400)
        if not _rate_ok(u["id"]):
            return _err("rate", 429, "Слишком часто — подождите пару секунд")
        r = await _to_thread(store.private_add_message, room_id, u["id"], txt)
        if not r.get("ok"):
            return _err(r.get("error") or "fail", 400)
        msg = {
            "id": r["id"], "room_id": int(room_id), "user_id": int(u["id"]),
            "name": u.get("display_name") or f"id{u['id']}",
            "text": r["text"], "created_at": r["created_at"],
            "is_admin": 1 if u.get("is_admin") else 0,
        }
        pub = _msg_json(msg, u["id"])
        # отправитель прочитал своё же сообщение до конца
        store.private_mark_read(room_id, u["id"], int(r["id"]))
        await _bcast_user(other, {"type": "chat_dm", "room_id": int(room_id),
                                  "message": pub, "event": "message"})
        await _bcast_user(u["id"], {"type": "chat_dm", "room_id": int(room_id),
                                    "message": pub, "event": "mine"})
        return {"ok": True, "message": pub,
                "unread": store.private_notify_counts(other)}

    app.include_router(router)
