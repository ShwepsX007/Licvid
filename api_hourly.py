"""HTTP-часть раздела «Сводки по часам»: страница, архив и фото постов.

Посты собирает и публикует бот (``tg_bot``), складывает их в архив
``hourly_posts.PostStore``, а сюда приходит только показ:

    GET  /hourly                — страница раздела (static/hourly.html)
    GET  /api/hourly            — посты за день или свежие + индекс дней
    GET  /api/hourly/photo/{id} — фото поста (оно лежит вне static)
    POST /api/hourly/collect    — собрать сводку сейчас и положить в архив — админ

Раздел показывает те же посты, что ушли в канал: подпись целиком (разметка
Telegram переводится в безопасный HTML) и то же фото. Дни и календарь — по
часовому поясу сводок (МСК по умолчанию), время поста страница показывает в
поясе посетителя.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, JSONResponse

import seo_pages
from hourly_posts import DEFAULT_KEEP, PostStore, day_index, public_post
from hour_board import tz_offset

log = logging.getLogger("liqscope.hourly")


class Ctx:
    """Всё, что нужно разделу; вешает server.py."""

    def __init__(self) -> None:
        self.store: PostStore = PostStore("")
        #: async () -> запись архива: собрать сводку сейчас без Telegram
        self.collect_fn = None
        #: tg_bot: удаление уже вышедших постов из Telegram
        self.bot = None
        #: () -> адрес английского канала (там выходят эти сводки)
        self.channel_url_fn = None
        self.page_ok = True
        self.public_url = ""
        self.last: Dict[str, Any] = {}


ctx = Ctx()


def _limit(value: Any, hi: int = 60, default: int = 12) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(hi, n))


def bot_running() -> bool:
    """Работает ли Telegram-бот: без него удалять посты в канале нечем."""
    bot = ctx.bot
    if bot is None:
        return False
    running = getattr(bot, "running", False)
    try:
        return bool(running() if callable(running) else running)
    except Exception:                                    # noqa: BLE001
        return False


def sent_ids(rec: dict) -> Dict[str, dict]:
    """Где пост лежит в Telegram: ``{язык: {chat, message_id, extra}}``."""
    out: Dict[str, dict] = {}
    for lang, info in ((rec or {}).get("tg") or {}).items():
        if not isinstance(info, dict):
            continue
        try:
            mid = int(info.get("message_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid > 0:
            out[str(lang)] = {"chat": str(info.get("chat") or ""), "message_id": mid,
                              "extra": list(info.get("extra") or [])}
    return out


def archive_row(rec: dict) -> dict:
    """Строка архива для админки: когда вышел, что внутри, где в Telegram."""
    rec = rec or {}
    rid = str(rec.get("id") or "")
    day = str(rec.get("day") or "")
    sent = sent_ids(rec)
    return {
        "id": rid,
        "day": day,
        "ts": float(rec.get("ts") or 0),
        "window_h": int(rec.get("window_h") or rec.get("interval_h") or 0),
        "n": int(rec.get("n") or 0),
        "total_usd": float(rec.get("total_usd") or 0),
        "liq_count": int(rec.get("liq_count") or 0),
        "langs": sorted((rec.get("texts") or {}).keys()),
        "tg": sorted(sent),
        "tg_ready": bool(sent),
        "cover": bool(((rec.get("photo") or {}).get("path"))),
        "url": f"/hourly?post={rid}" if rid else "/hourly",
    }


def delete_fn():
    """Функция удаления сообщения у бота (None — бота нет)."""
    bot = ctx.bot
    return getattr(bot, "delete_message", None) if bot is not None else None


def _bot_off() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "bot_off",
         "message": "Telegram-бот выключен — посты в канале не удалены. "
                    "Включите бота или снимите галочку «и из Telegram»."},
        status_code=409)


def _photo_response(path: str) -> Any:
    """Файл картинки с типом по расширению и суточным кэшем."""
    ext = os.path.splitext(path)[1].lower()
    media = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "image/jpeg")
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "public, max-age=86400"})


def register_hourly_routes(app) -> None:
    router = APIRouter()

    @router.get("/hourly")
    async def page_hourly(request: Request, day: str = "", post: str = ""):
        """Страница раздела. Превью ссылки строим на фото свежего поста."""
        if not ctx.page_ok:
            return JSONResponse({"ok": False, "error": "off"}, status_code=404)
        lang, auto = seo_pages.lang_of(request)
        items = ctx.store.list()
        rec: Optional[dict] = None
        if post:
            rec = ctx.store.get(post)
        if rec is None:
            rec = next((x for x in items if str(x.get("day")) == day), None) if day else None
        if rec is None:
            rec = items[0] if items else None
        photo = public_post(rec, lang, with_text=False).get("photo") if rec else None
        og_image = ""
        if photo:
            og_image = (ctx.public_url or seo_pages.SITE_URL).rstrip("/") + photo["url"]
        # Каноникал с днём/постом — у каждого дня свой URL
        if post:
            canon_path = f"/hourly?post={post}"
        elif day:
            canon_path = f"/hourly?day={day}"
        else:
            canon_path = "/hourly"
        # Article для JSON-LD
        article = None
        try:
            if rec:
                d = str((rec or {}).get("day") or day or "")
                # Заголовок вида "Сводки за 2026-09-20" / "Hourly — 2026-09-20"
                title = f"Сводки за {d}" if lang == "ru" else f"Hourly — {d}" if d else None
                # Описание из поста: первые 160 знаков текста
                raw_text = str((rec or {}).get("text") or (rec or {}).get("caption") or "")[:180]
                article = {
                    "day": d,
                    "title": title,
                    "desc": raw_text or None,
                    "date": d,
                }
            elif day:
                article = {
                    "day": str(day),
                    "title": f"Сводки за {day}" if lang == "ru" else f"Hourly — {day}",
                    "date": str(day),
                }
        except Exception:
            article = {"day": str(day or "")}
        return seo_pages.render(
            "hourly.html", lang, canon_path,
            extra_head=seo_pages.jsonld("hourly", lang, image=og_image, article=article),
            og_image=og_image, auto=auto,
        )

    @router.get("/api/hourly")
    async def api_list(lang: str = seo_pages.DEFAULT_LANG, day: str = "",
                     limit: int = 12):
        """Посты раздела: за конкретный день или свежие.

        ``days`` — лёгкий индекс для календаря: по нему видно, за какие дни
        посты есть и сколько их было, чтобы страница не тянула всё сразу.
        """
        items = ctx.store.list()
        days = ctx.store.days()
        if day:
            posts = ctx.store.by_day(day)
        else:
            posts = ctx.store.recent(_limit(limit))
        return {
            "ok": True,
            "items": [public_post(r, lang) for r in posts],
            "days": [day_index(d, lang) for d in days],
            "count": len(items),
            "days_count": len(days),
            "keep": ctx.store.keep or DEFAULT_KEEP,
            "now": time.strftime("%Y-%m-%d", time.gmtime(time.time() + tz_offset())),
            "tz_hours": round(tz_offset() / 3600.0, 2),
            "stats": ctx.store.stats(),
            "interval_h": _interval_hours(items),
            "channel_url": _channel_url(),
        }

    @router.get("/api/hourly/photo/{pid}")
    async def api_photo(pid: str):
        """Фото поста: картинка лежит вне static (data/channel), отдаём её здесь.

        Если файл удалён (data очищена), отдаём fallback из комплекта
        static/channel, чтобы старые посты не остались без картинки.
        """
        rec = ctx.store.get(pid)
        path = str(((rec or {}).get("photo") or {}).get("path") or "")
        if not path or not os.path.isfile(path):
            try:
                from hourly_posts import _hourly_bundle_fallback
                fb = _hourly_bundle_fallback(pid)
                if fb and os.path.isfile(fb):
                    return _photo_response(fb)
            except Exception:
                pass
            return JSONResponse({"ok": False, "error": "no_photo"}, status_code=404)
        return _photo_response(path)

    @router.get("/api/hourly/status")
    async def api_status():
        return {"ok": True, "store_error": ctx.store.error, "stats": ctx.store.stats()}

    def _admin(request: Request):
        from web_account import current_user
        user = current_user(request)
        if not user:
            return None, JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        if not user.get("is_admin"):
            return None, JSONResponse({"ok": False, "error": "admin"}, status_code=403)
        return user, None

    @router.post("/api/hourly/collect")
    async def api_collect(request: Request, body: Optional[dict] = Body(default=None)):
        """Собрать сводку сейчас и положить её в архив раздела (без Telegram).

        Нужно, чтобы раздел можно было наполнить и посмотреть, не дожидаясь
        поста в канал, и чтобы после сбоя публикации пост не терялся.
        """
        user, err = _admin(request)
        if err:
            return err
        if ctx.collect_fn is None:
            return JSONResponse({"ok": False, "error": "no_collect"}, status_code=503)
        try:
            rec = ctx.collect_fn()
            if hasattr(rec, "__await__"):
                rec = await rec
        except Exception as e:                        # noqa: BLE001
            log.warning("сводки по часам: сборка не удалась: %s", e)
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"},
                                status_code=500)
        if not rec:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=409)
        ctx.last = {"at": time.time(), "id": rec.get("id"), "by": user.get("id")}
        return {"ok": True, "item": public_post(rec, "ru"), "stats": ctx.store.stats()}

    # --- архив для админки: старые сводки можно снять ----------------------
    @router.get("/api/admin/hourly/archive")
    async def api_admin_archive(request: Request, limit: int = 60):
        """Архив сводок: дни с количеством постов и сами посты для удаления."""
        user, err = _admin(request)
        if err:
            return err
        try:
            lim = max(1, min(500, int(limit)))
        except (TypeError, ValueError):
            lim = 60
        items = ctx.store.list()
        return {"ok": True, "items": [archive_row(r) for r in items[:lim]],
                "days": ctx.store.days(), "count": len(items),
                "keep": ctx.store.keep, "stats": ctx.store.stats(),
                "store_error": ctx.store.error, "bot": bot_running(),
                "tz_hours": round(tz_offset() / 3600.0, 2)}

    @router.post("/api/admin/hourly/{pid}/delete")
    async def api_admin_delete_post(request: Request, pid: str,
                                    body: Optional[dict] = Body(default=None)):
        """Удалить одну сводку: из архива сайта и, если просили, из Telegram."""
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        rec = ctx.store.get(pid)
        if rec is None:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        want_tg = bool(body.get("tg"))
        sent = sent_ids(rec)
        result: Dict[str, Any] = {}
        if want_tg and sent:
            if not bot_running():
                return _bot_off()
            from channel_digest import drop_channel_posts
            result = await drop_channel_posts(sent, delete_fn())
        ctx.store.remove(str(rec.get("id") or pid))
        log.info("сводка %s удалена админом %s (tg=%s)", pid, user.get("id"), want_tg)
        return {"ok": True, "removed": archive_row(rec), "tg": result,
                "count": len(ctx.store.list())}

    @router.post("/api/admin/hourly/day/{day}/delete")
    async def api_admin_delete_day(request: Request, day: str,
                                   body: Optional[dict] = Body(default=None)):
        """Удалить все сводки дня: так чистят старый архив пачкой."""
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        posts = ctx.store.by_day(day)
        if not posts:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        want_tg = bool(body.get("tg"))
        # За день в канал ушло несколько сводок: собираем id всех, иначе в
        # канале останутся прошлые посты дня, а на сайте их уже нет.
        sent: Dict[str, dict] = {}
        for rec in posts:
            for lang, info in sent_ids(rec).items():
                row = sent.get(lang)
                ids = [int(info["message_id"])] + [int(x) for x in info.get("extra") or []]
                if row is None:
                    sent[lang] = {"chat": info.get("chat"), "message_id": ids[0],
                                  "extra": ids[1:]}
                else:
                    if not row.get("chat"):
                        row["chat"] = info.get("chat")
                    row["extra"] = list(row.get("extra") or []) + ids
        result: Dict[str, Any] = {}
        if want_tg and sent:
            if not bot_running():
                return _bot_off()
            from channel_digest import drop_channel_posts
            result = await drop_channel_posts(sent, delete_fn())
        gone = ctx.store.remove_day(day)
        log.info("сводки за %s удалены админом %s: %d постов (tg=%s)",
                 day, user.get("id"), len(gone), want_tg)
        return {"ok": True, "day": day, "deleted": len(gone), "tg": result,
                "count": len(ctx.store.list())}

    app.include_router(router)


def _channel_url() -> str:
    """Адрес английского канала: посты раздела выходят именно там."""
    fn = ctx.channel_url_fn
    if fn is None:
        return ""
    try:
        return str(fn() or "")
    except Exception as e:                              # noqa: BLE001
        log.debug("сводки по часам: адрес канала не получен: %s", e)
        return ""


def _interval_hours(items: List[dict]) -> int:
    """Через сколько часов выходят сводки: по последним постам архива."""
    for rec in items or []:
        try:
            h = int(rec.get("interval_h") or 0)
        except (TypeError, ValueError):
            h = 0
        if h > 0:
            return h
        try:
            h = int(rec.get("window_h") or 0)
        except (TypeError, ValueError):
            h = 0
        if h > 0:
            return h
    return 4
