"""Админка бота на сайте: каналы, публикация постов, контроль, здоровье.

Раньше эти действия жили только в Telegram-кнопках: чтобы выложить сводку,
посмотреть биржи или переключить контроль постов, нужно было открывать бота.
Здесь те же операции доступны из панели /admin — модуль дёргает **те же
методы TelegramBot**, поэтому поведение не расходится:

    * публикация поста уважает «🧪 Контроль постов» (черновик админу в TG);
    * перед отправкой сверяются роли каналов (RU/EN), как в боте;
    * шапки, фото и ИИ-текст берутся из общих шаблонов в базе.

Сайт работает, даже когда бот выключен: вкладка отвечает «бот не подключён»,
а не падает — чтобы админ видел состояние, а не пустой экран.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import Body, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

log = logging.getLogger("liqscope.botadmin")

SKIP_SOURCES = {"prices", "ticks", "oxa"}
CHAT_ID_RE = re.compile(r"^-?\d{5,20}$")
_bot_id_cache: Dict[str, int] = {}


class Ctx:
    """Связка с сервером: бот, база и справочники — заполняет server.py."""

    bot = None
    store = None
    health_fn = staticmethod(lambda: {})
    ws_clients_fn = staticmethod(lambda: 0)
    public_url = ""


ctx = Ctx()


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------
def _admin(request: Request):
    """Тот же страж, что у остальных маршрутов админки сайта."""
    from web_account import current_user
    user = current_user(request)
    if not user:
        return None, JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not user.get("is_admin"):
        return None, JSONResponse({"ok": False, "error": "admin"}, status_code=403)
    return user, None


def _no_bot(what: str = "бот") -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "no_bot",
         "message": f"Telegram-{what} не подключён: на сервере нет токена бота."},
        status_code=503)


def bot_state(bot=None) -> dict:
    """Состояние бота одной строкой и полями — то же, что /bot в чате."""
    bot = ctx.bot if bot is None else bot
    if bot is None:
        return {"present": False, "enabled": False, "running": False,
                "task_alive": False, "username": "", "line": "Бот не подключён",
                "status": {}}
    try:
        st = dict(bot.poll_status())
    except Exception as e:                              # бот без токена и т.п.
        return {"present": True, "enabled": False, "running": False,
                "task_alive": False, "username": "", "line": f"Бот: {e}",
                "status": {}}
    try:
        line = bot.poll_line()
    except Exception:
        line = "Бот: состояние недоступно"
    return {
        "present": True,
        "enabled": bool(st.get("enabled")),
        "running": bool(st.get("running")),
        "task_alive": bool(st.get("task_alive")),
        "username": str(getattr(bot, "username", "") or "").lstrip("@"),
        "line": line,
        "status": st,
    }


def channels_state(bot=None) -> dict:
    """Оба канала: id, название, откуда взялся id + предупреждения."""
    bot = ctx.bot if bot is None else bot
    out: Dict[str, Any] = {"ru": {}, "en": {}, "route": "", "warn": ""}
    if bot is None:
        return out
    for code in ("ru", "en"):
        try:
            role = dict(bot.channel_role(code) or {})
        except Exception:
            role = {}
        role["id"] = str(role.get("id") or "")
        role["source"] = bot.channel_source(code) if hasattr(bot, "channel_source") else ""
        role["source_label"] = {"bot": "выбрано в боте или на сайте",
                                "env": "из переменной окружения"}.get(role["source"], "")
        out[code] = role
    try:
        out["route"] = bot.channel_route_text()
    except Exception:
        out["route"] = ""
    ru, en = out["ru"].get("id"), out["en"].get("id")
    if not ru or not en:
        out["warn"] = ("Привязан один канал — посты уйдут только в него. "
                       "Впишите id второго канала и сохраните.")
    elif ru == en:
        out["warn"] = "Оба языка указывают на один канал — исправьте id."
    return out


def health_state() -> dict:
    """Биржи и бот так, как их видно в панели: строки со статусом."""
    h = ctx.health_fn() or {}
    srcs = h.get("sources") or {}
    rows: List[dict] = []
    live = 0
    events_total = 0
    for name, s in sorted(srcs.items()):
        if name in SKIP_SOURCES or not isinstance(s, dict):
            continue
        ev = int(s.get("events") or 0)
        events_total += ev
        if s.get("supervisor_alive") is False:
            state = "dead"
        elif s.get("connected"):
            state = "ok"
            live += 1
        else:
            state = "down"
        rows.append({
            "name": name,
            "state": state,
            "events": ev,
            "since": s.get("seconds_since_event"),
            "error": str(s.get("last_error") or "")[:160],
            "attempts": int(s.get("attempts") or 0),
            "respawns": int(s.get("respawns") or 0),
        })
    return {
        "rows": rows,
        "live": live,
        "total": len(rows),
        "events_total": events_total,
        "ws_clients": int(ctx.ws_clients_fn() or 0),
        "bot_line": bot_state()["line"],
    }


def daily_state(app=None, bot=None) -> dict:
    """Дневной дайджест: последний выпуск и расписание планировщика."""
    bot = ctx.bot if bot is None else bot
    out: Dict[str, Any] = {"last": None, "schedule": None, "store_error": "",
                           "review": False, "wired": False}
    try:
        from api_digest import ctx as digest_ctx
        items = digest_ctx.store.list()
        if items:
            rec = items[0]
            out["last"] = {
                "day": rec.get("day") or rec.get("id"),
                "created": rec.get("created"),
                "facts": {
                    "liq_total_usd": (rec.get("facts") or {}).get("liq_total_usd"),
                    "liq_count": (rec.get("facts") or {}).get("liq_count"),
                },
                "published": rec.get("published") or {},
            }
        out["store_error"] = digest_ctx.store.error
    except Exception as e:
        log.debug("состояние архива дайджестов: %s", e)
    sched = getattr(getattr(app, "state", None), "digest_scheduler", None)
    if sched is not None:
        try:
            out["schedule"] = sched.status()
        except Exception:
            out["schedule"] = None
    if bot is not None:
        try:
            st = bot.daily_status()
            out["review"] = bool(st.get("review"))
            out["wired"] = bool(st.get("wired"))
            if st.get("day"):
                out["last_run"] = {"day": st.get("day"), "ok": bool(st.get("ok")),
                                   "draft": bool(st.get("draft"))}
        except Exception:
            pass
    return out


def interval_state(bot=None) -> dict:
    """Частота постов в канал: раз в N часов и производная длина блока.

    Окно поста равно промежутку между постами, а блок анализа внутри поста —
    четверти окна. Панель показывает и то, и другое: админ выбирает часы, а
    видит, по сколько минут будет разбор.
    """
    from channel_digest import (DEFAULT_INTERVAL_H, MAX_INTERVAL_H,
                                MIN_INTERVAL_H, block_secs, window_word)
    hours = DEFAULT_INTERVAL_H
    try:
        if bot is not None and hasattr(bot, "channel_interval_h"):
            hours = int(bot.channel_interval_h())
        else:
            from channel_digest import interval_hours
            hours = int(interval_hours(ctx.store))
    except Exception as e:                       # noqa: BLE001
        log.debug("частота постов: %s", e)
    block = block_secs(hours)
    return {
        "hours": hours,
        "min": MIN_INTERVAL_H,
        "max": MAX_INTERVAL_H,
        "block_sec": block,
        "window": f"{hours}ч",
        "block": window_word(block),
        "text": (f"раз в {hours} ч · окно {hours} ч · анализ по "
                 f"{window_word(block)}"),
    }


AI_PROMPT_LIMIT = 4000      # символов: длиннее инструкция модели не нужна


def ai_prompts_state() -> dict:
    """Промты ИИ для админки: что стоит сейчас и какой шаблон встроен.

    Промтов два: инструкция для шапки поста в канал и для текста дневного
    дайджеста. У каждого — русский и английский вариант, потому что посты
    уходят в два канала; пустое поле значит «работает встроенный шаблон».
    """
    from ai_text import active_prompt, custom_prompt, default_prompt, prompt_setting
    out = {"kinds": {"head": "Шапка поста", "digest": "Дневной дайджест"},
           "langs": {"ru": "Русский", "en": "English"},
           "limit": AI_PROMPT_LIMIT, "blocks": []}
    for kind in ("head", "digest"):
        rows = []
        for lang in ("ru", "en"):
            rows.append({
                "lang": lang,
                "setting": prompt_setting(kind, lang),
                "text": active_prompt(kind, lang),
                "default": default_prompt(kind, lang),
                "custom": bool(custom_prompt(kind, lang)),
            })
        out["blocks"].append({"kind": kind, "title": out["kinds"][kind], "langs": rows})
    return out


async def _bot_user_id(bot) -> int:
    """id самого бота (нужен, чтобы спросить его права в канале)."""
    token = str(getattr(bot, "token", "") or "")
    if not token:
        return 0
    if token in _bot_id_cache:
        return _bot_id_cache[token]
    res = await bot._call("getMe", {})
    uid = int((((res or {}).get("result") or {}).get("id")) or 0)
    if uid:
        _bot_id_cache[token] = uid
    return uid


async def channel_probe(bot, cid: str) -> dict:
    """Что Telegram знает про канал: название и права бота на публикацию."""
    out = {"id": str(cid or ""), "title": "", "status": "", "can_post": None,
           "note": ""}
    if not out["id"] or bot is None or not getattr(bot, "token", ""):
        out["note"] = "бот выключен — проверка недоступна"
        return out
    res = await bot._call("getChat", {"chat_id": out["id"]})
    if not res or not res.get("ok"):
        out["note"] = str((res or {}).get("description") or "нет ответа Telegram")[:160]
        return out
    chat = res.get("result") or {}
    out["title"] = str(chat.get("title") or chat.get("username") or "")
    uid = await _bot_user_id(bot)
    if not uid:
        return out
    m = await bot._call("getChatMember", {"chat_id": out["id"], "user_id": uid})
    if not m or not m.get("ok"):
        out["note"] = str((m or {}).get("description") or "права не проверить")[:160]
        return out
    member = m.get("result") or {}
    status = str(member.get("status") or "")
    out["status"] = status
    if status in ("administrator", "creator"):
        out["can_post"] = bool(member.get("can_post_messages", True)
                               if status == "administrator" else True)
        if out["can_post"] is False:
            out["note"] = "бот админ, но «Публикация сообщений» выключена"
    else:
        out["can_post"] = False
        out["note"] = "бот не админ канала — посты не уйдут"
    return out


def _set_channel(bot, code: str, cid: str, title: str = "") -> None:
    """Привязать канал к языку (пустой id — отвязать)."""
    cid = str(cid or "").strip()
    if cid:
        bot.remember_channel_role(code, cid, title)
        return
    d = bot._channel_bindings()
    d[code] = ""
    d.setdefault("titles", {})[code] = ""
    bot._save_channel_bindings(d)


# ---------------------------------------------------------------------------
#  Маршруты
# ---------------------------------------------------------------------------
def register_bot_admin_routes(app) -> None:
    router = APIRouter()

    def _store():
        return ctx.store or getattr(ctx.bot, "store", None)

    def _audit(user, action: str, detail: str = "") -> None:
        st = _store()
        if st is not None and hasattr(st, "audit"):
            try:
                st.audit(user.get("id"), action, detail)
            except Exception as e:
                log.debug("аудит: %s", e)

    @router.get("/api/admin/bot")
    async def api_bot_state(request: Request):
        """Одно окно: состояние бота, каналы, контроль, здоровье, дайджест."""
        user, err = _admin(request)
        if err:
            return err
        bot = ctx.bot
        data = {
            "ok": True,
            "bot": bot_state(bot),
            "channels": channels_state(bot),
            "review": False,
            "templates": {"heads": 0, "photos": 0,
                          "using_default_heads": True, "using_default_photos": True},
            "ai": {},
            "health": health_state(),
            "daily": daily_state(app, bot),
            "interval": interval_state(bot),
        }
        # Права бота в каналах спрашиваем сразу: админ должен видеть, дойдёт
        # ли пост, не нажимая «Сверить роли». Без токена — пропускаем.
        if bot is not None and getattr(bot, "token", ""):
            probes = {}
            for code in ("ru", "en"):
                cid = ""
                try:
                    cid = bot.channel_chat_id_en() if code == "en" else bot.channel_chat_id()
                except Exception:
                    cid = ""
                try:
                    probes[code] = await channel_probe(bot, cid)
                except Exception as e:
                    probes[code] = {"id": str(cid or ""), "title": "", "can_post": None,
                                    "note": str(e)[:120]}
            data["probes"] = probes
        if bot is not None:
            try:
                data["review"] = bool(bot._review_on())
            except Exception:
                data["review"] = False
            try:
                data["ai"] = bot.ai_status()
            except Exception as e:
                data["ai"] = {"enabled": False, "error": str(e)[:120]}
        st = _store()
        if st is not None:
            heads = st.list_digest_heads() or []
            photos = st.list_digest_photos() or []
            data["templates"] = {
                "heads": len(heads), "photos": len(photos),
                "using_default_heads": not bool(heads),
                "using_default_photos": not bool(photos),
            }
        return data

    @router.post("/api/admin/bot/review")
    async def api_bot_review(request: Request,
                             body: Optional[dict] = Body(default=None)):
        """🧪 Контроль постов: черновик в Telegram вместо поста в канал."""
        user, err = _admin(request)
        if err:
            return err
        bot = ctx.bot
        if bot is None:
            return _no_bot()
        body = body or {}
        on = bool(body.get("on"))
        try:
            bot._set_review(on, actor_id=user.get("id"))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:160]}, status_code=500)
        _audit(user, "bot_review", "on" if on else "off")
        return {"ok": True, "review": on,
                "message": ("Контроль включён: посты уходят в Telegram черновиком."
                            if on else
                            "Контроль выключен: посты уходят сразу в канал.")}

    @router.post("/api/admin/bot/channels")
    async def api_bot_channels(request: Request,
                               body: Optional[dict] = Body(default=None)):
        """Привязать каналы по id: проверяем, что Telegram их видит."""
        user, err = _admin(request)
        if err:
            return err
        bot = ctx.bot
        if bot is None:
            return _no_bot()
        body = body or {}
        probes: Dict[str, dict] = {}
        for code in ("ru", "en"):
            if code not in body:
                continue
            cid = str(body.get(code) or "").strip()
            if cid and not CHAT_ID_RE.match(cid):
                return JSONResponse(
                    {"ok": False, "error": "bad_id", "code": code,
                     "message": ("id канала — число вида −1001234567890 "
                                 "(можно взять из пересланного бота поста)")},
                    status_code=400)
            if cid:
                probe = await channel_probe(bot, cid)
                if not probe.get("title") and getattr(bot, "token", ""):
                    # Бот живой, но канала не видит: значит, он не добавлен в
                    # него — сохранять такой id нельзя, посты уйдут в никуда.
                    return JSONResponse(
                        {"ok": False, "error": "not_visible", "code": code,
                         "message": (f"Telegram не показал канал {cid}: "
                                     f"{probe.get('note') or 'бот не добавлен в него'}")},
                        status_code=400)
                if not probe.get("title") and not getattr(bot, "token", ""):
                    probe["note"] = "сохранил без проверки: бот выключен (нет токена)"
                _set_channel(bot, code, cid, probe.get("title") or "")
                probes[code] = probe
            else:
                _set_channel(bot, code, "")
                probes[code] = {"id": "", "title": "", "note": "канал отвязан"}
        _audit(user, "bot_channels",
               " ".join(f"{k}={v.get('id') or '-'}" for k, v in probes.items()))
        return {"ok": True, "probes": probes, "channels": channels_state(bot)}

    @router.post("/api/admin/bot/channels/swap")
    async def api_bot_channels_swap(request: Request):
        """Поменять каналы местами (RU ↔ EN), как кнопка «🔄» в боте."""
        user, err = _admin(request)
        if err:
            return err
        bot = ctx.bot
        if bot is None:
            return _no_bot()
        ru, en = bot.channel_chat_id(), bot.channel_chat_id_en()
        if not ru or not en:
            return JSONResponse(
                {"ok": False, "error": "need_two",
                 "message": "Для обмена нужны оба канала."}, status_code=400)
        d = bot._channel_bindings()
        d["ru"], d["en"] = en, ru
        t = d.get("titles") or {}
        t["ru"], t["en"] = t.get("en", ""), t.get("ru", "")
        d["titles"] = t
        bot._save_channel_bindings(d)
        _audit(user, "bot_channels_swap", f"ru={en} en={ru}")
        return {"ok": True, "channels": channels_state(bot)}

    @router.post("/api/admin/bot/channels/verify")
    async def api_bot_channels_verify(request: Request):
        """Сверить роли каналов с названиями и проверить права бота."""
        user, err = _admin(request)
        if err:
            return err
        bot = ctx.bot
        if bot is None:
            return _no_bot()
        note = ""
        try:
            note = await bot.verify_channel_roles(force=True) or ""
        except Exception as e:
            note = f"сверка не удалась: {e}"
        probes = {}
        for code in ("ru", "en"):
            cid = bot.channel_chat_id_en() if code == "en" else bot.channel_chat_id()
            probes[code] = await channel_probe(bot, cid)
        _audit(user, "bot_channels_verify", note[:120])
        return {"ok": True, "note": note, "probes": probes,
                "channels": channels_state(bot),
                "message": note or "Роли каналов сверены."}

    @router.post("/api/admin/bot/interval")
    async def api_bot_interval(request: Request,
                               body: Optional[dict] = Body(default=None)):
        """🕒 Частота сводки: раз в N часов (1…10) с блоком анализа в четверть."""
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        st = _store()
        if st is None:
            return JSONResponse({"ok": False, "error": "no_store",
                                 "message": "База настроек недоступна."},
                                status_code=503)
        raw = body.get("hours", body.get("interval"))
        if raw in (None, ""):
            return JSONResponse({"ok": False, "error": "no_hours",
                                 "message": "Укажите частоту в часах (1…10)."},
                                status_code=400)
        try:
            from channel_digest import clamp_interval
            hours = clamp_interval(raw)
        except Exception as e:                   # noqa: BLE001
            return JSONResponse({"ok": False, "error": "bad_hours",
                                 "message": f"Не понял частоту: {e}"},
                                status_code=400)
        bot = ctx.bot
        try:
            if bot is not None and hasattr(bot, "set_channel_interval"):
                bot.set_channel_interval(hours, actor_id=user.get("id"))
            else:
                from channel_digest import INTERVAL_SETTING
                st.set_setting(INTERVAL_SETTING, str(hours), actor_id=user.get("id"))
        except Exception as e:                   # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(e)[:160]},
                                status_code=500)
        _audit(user, "bot_interval", f"{hours}h")
        return {"ok": True, "interval": interval_state(bot),
                "message": (f"Сводка раз в {hours} ч: блок анализа — "
                            f"{interval_state(bot)['block']}. Расписание "
                            "применяется сразу.")}

    @router.get("/api/admin/ai/prompts")
    async def api_ai_prompts(request: Request):
        """🤖 Промты ИИ: инструкция для шапки поста и для дневного дайджеста."""
        _user, err = _admin(request)
        if err:
            return err
        return {"ok": True, "prompts": ai_prompts_state()}

    @router.post("/api/admin/ai/prompts")
    async def api_ai_prompts_save(request: Request,
                                  body: Optional[dict] = Body(default=None)):
        """Сохранить промт ИИ; пустой текст — вернуть встроенный шаблон."""
        user, err = _admin(request)
        if err:
            return err
        st = _store()
        if st is None:
            return JSONResponse({"ok": False, "error": "no_store",
                                 "message": "База настроек недоступна."},
                                status_code=503)
        from ai_text import prompt_setting
        body = body or {}
        kind = str(body.get("kind") or "head").strip().lower()
        lang = str(body.get("lang") or "ru").strip().lower()
        if kind not in ("head", "digest") or lang not in ("ru", "en"):
            return JSONResponse({"ok": False, "error": "bad_target",
                                 "message": "Промт бывает только для шапки или "
                                            "дайджеста, на русском или английском."},
                                status_code=400)
        text = str(body.get("text") or "").strip()
        if len(text) > AI_PROMPT_LIMIT:
            return JSONResponse({"ok": False, "error": "too_long",
                                 "message": f"Слишком длинный промт: до "
                                            f"{AI_PROMPT_LIMIT} знаков."},
                                status_code=400)
        try:
            st.set_setting(prompt_setting(kind, lang), text,
                           actor_id=user.get("id"))
        except Exception as e:                   # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(e)[:160]},
                                status_code=500)
        where = ai_prompts_state()["kinds"][kind]
        _audit(user, "ai_prompt", f"{kind}/{lang}:{'set' if text else 'reset'}")
        return {"ok": True, "prompts": ai_prompts_state(),
                "message": (f"Промт для «{where}» ({lang}) сохранён."
                            if text else
                            f"Промт для «{where}» ({lang}) снова по шаблону.")}

    @router.post("/api/admin/bot/publish")
    async def api_bot_publish(request: Request,
                              body: Optional[dict] = Body(default=None)):
        """Выложить пост сейчас: ``kind=channel`` — сводку, ``daily`` — дайджест."""
        user, err = _admin(request)
        if err:
            return err
        bot = ctx.bot
        if bot is None or not getattr(bot, "token", ""):
            return _no_bot()
        body = body or {}
        kind = str(body.get("kind") or "channel").lower()
        review = False
        try:
            review = bool(bot._review_on())
        except Exception:
            review = False
        if kind == "daily":
            langs = body.get("langs") or ["ru", "en"]
            if isinstance(langs, str):
                langs = [x for x in langs.replace(",", " ").split() if x]
            langs = [x for x in langs if x in ("ru", "en")] or ["ru", "en"]
            ok = bool(await bot.post_daily_digest(force=True, langs=langs,
                                                  reason=f"site:{user.get('id')}"))
            err_txt = str(getattr(bot, "_digest_err", "") or "")
            stt = {}
            try:
                stt = dict(bot.daily_status() or {})
            except Exception:
                stt = {}
            day = str(stt.get("day") or "")
            if ok:
                msg = (f"Дневной дайджест за {day} отправлен: " +
                       ", ".join(("🇷🇺 RU" if x == "ru" else "🇬🇧 EN") for x in langs))
            elif review:
                msg = ("Черновик дневного дайджеста отправлен вам в Telegram — "
                       "нажмите «✅ Опубликовать» в боте.")
            else:
                msg = "Не удалось отправить дайджест: " + (err_txt or "нет каналов")
        else:
            ok = bool(await bot.post_channel_digest(force=True))
            err_txt = str(getattr(bot, "_digest_err", "") or "")
            if ok and review:
                msg = ("Черновик сводки отправлен вам в Telegram — "
                       "проверьте текст и нажмите «✅ Опубликовать».")
            elif ok:
                msg = "Сводка ушла в каналы. " + (channels_state(bot).get("route") or "")
            else:
                msg = "Не удалось отправить сводку: " + (err_txt or "неизвестная ошибка")
        _audit(user, "bot_publish", f"{kind} ok={ok}")
        # review=True — пост ушёл админу черновиком, а не в канал: панель
        # должна сказать об этом прямо, иначе кнопка выглядит сломанной.
        return {"ok": ok, "kind": kind, "draft": review,
                "message": msg, "error": err_txt,
                "channels": channels_state(bot)}

    @router.post("/api/admin/bot/ai-check")
    async def api_bot_ai_check(request: Request,
                               body: Optional[dict] = Body(default=None)):
        """🤖 Проверить ИИ: собрать шапку поста и показать текст админу."""
        user, err = _admin(request)
        if err:
            return err
        bot = ctx.bot
        if bot is None:
            return _no_bot()
        snap: dict = {}
        try:
            raw = bot.digest_fn() if getattr(bot, "digest_fn", None) else {}
            if hasattr(raw, "__await__"):
                raw = await raw
            snap = raw if isinstance(raw, dict) else {}
        except Exception as e:
            log.warning("проверка ИИ: снимок не собрался: %s", e)
        variant = 0
        st = _store()
        if st is not None:
            try:
                variant = int(st.get_setting("channel_digest_n") or 0)
            except (TypeError, ValueError):
                variant = 0
        lang = "en" if str(body.get("lang") or "").lower().startswith("en") else "ru"
        try:
            head, note = await bot._ai_headline(snap, variant=variant, lang=lang)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:160]}, status_code=500)
        _audit(user, "bot_ai_check", note[:80])
        return {"ok": True, "head": head or "", "note": note, "lang": lang}

    app.include_router(router)
