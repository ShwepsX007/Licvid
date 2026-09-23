"""📰 Статьи: страницы раздела, админский редактор и отправка в каналы.

Админ пишет статью в админке (заголовок, текст, одна обложка), назначает дату
публикации и получает английскую версию — переводом ИИ или руками. Здесь
живёт всё, что делает статью видимой:

    GET  /articles                     — список статей (static/articles.html)
    GET  /articles/{slug}              — страница статьи (static/article.html)
    GET  /api/articles                 — опубликованные статьи для страницы
    GET  /api/articles/{slug}          — одна статья на языке посетителя
    GET  /api/articles/photo/{slug}    — обложка (файл лежит вне static)

Админка:

    GET  /api/admin/articles           — все статьи со статусами, каналы, ИИ
    POST /api/admin/articles/save      — создать/сохранить (текст, фото, дата)
    POST /api/admin/articles/{id}/translate — перевести RU → EN через ИИ
    POST /api/admin/articles/{id}/publish   — опубликовать сейчас
    POST /api/admin/articles/{id}/unpublish — снять с сайта
    POST /api/admin/articles/{id}/send      — отправить в каналы (RU → RU, EN → EN)
    POST /api/admin/articles/{id}/delete

Два правила раздела, из которых следует всё остальное:

    * **без английской версии публикации нет.** Русский текст — источник,
      английский даёт ИИ; если ИИ недоступен, публикация не происходит, а
      админка честно говорит: «переведите руками в правом окне». Так на
      английской странице не появляется русская статья;
    * **в каналы — только по кнопке.** Планировщик публикует статью на сайте
      (по выбранной админом дате и времени) и ничего не отправляет в Telegram:
      рассылку по каналам админ запускает сам, когда текст вычитан.

Расписание проверяет фоновая петля ``scheduler_loop``: раз в полминуты
смотрит, не наступило ли время, и повторяет попытку не чаще, чем раз в
``RETRY_SEC`` (если ИИ в тот момент не ответил).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, JSONResponse

import seo_pages
from articles import (ArticleStore, DEFAULT_KEEP, TITLE_MAX, article_path,
                      excerpt, index_row, public_article, slugify, text_problem,
                      unique_slug)
from hour_board import tz_offset

log = logging.getLogger("liqscope.articles")

RETRY_SEC = 900.0        # повтор неудачной публикации — не чаще, чем раз в 15 минут
MAX_PHOTO = 12_000_000   # столько же, сколько у фото канала и рекламы
AI_MIN = 40              # короче этого переводить нечего


class Ctx:
    """Связка с сервером: архив, ИИ, бот и адрес сайта — вешает server.py."""

    def __init__(self) -> None:
        self.store: ArticleStore = ArticleStore("")
        #: async (text) -> Optional[str]: перевод RU → EN (None — ИИ недоступен)
        self.ai_fn = None
        #: () -> dict: состояние ИИ для админки (какие сервисы отвечают)
        self.ai_status_fn = None
        #: tg_bot: каналы раздела
        self.bot = None
        self.public_url = ""
        self.photo_dir = ""
        self.page_ok = True
        self.busy = False
        self.last: Dict[str, Any] = {}


ctx = Ctx()


# ---------------------------------------------------------------------------
#  Мелочи
# ---------------------------------------------------------------------------
def _now() -> float:
    return time.time()


def _admin(request: Request):
    from web_account import current_user
    user = current_user(request)
    if not user:
        return None, JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not user.get("is_admin"):
        return None, JSONResponse({"ok": False, "error": "admin"}, status_code=403)
    return user, None


def _clean_title(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:TITLE_MAX]


def _clean_text(raw: Any) -> str:
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _photo_response(path: str) -> Any:
    ext = os.path.splitext(path)[1].lower()
    media = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "image/jpeg")
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "public, max-age=3600"})


def save_photo(slug: str, blob: bytes, filename: str = "") -> Dict[str, Any]:
    """Обложка статьи: проверяем формат и размер, кладём рядом с архивом.

    Имя файла уникально (со временем): заменив фото, админ сразу видит новое,
    а не закешированное браузером старое.
    """
    blob = blob or b""
    if len(blob) < 24:
        return {"ok": False, "error": "empty"}
    if len(blob) > MAX_PHOTO:
        return {"ok": False, "error": "too_big"}
    ext = ""
    if blob[:2] == b"\xff\xd8":
        ext = ".jpg"
    elif blob[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        ext = ".webp"
    elif blob[:6] in (b"GIF87a", b"GIF89a"):
        ext = ".gif"
    if not ext:
        return {"ok": False, "error": "not_image"}
    folder = ctx.photo_dir or os.path.join(os.path.dirname(__file__), "data", "articles")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:                                # noqa: BLE001
        return {"ok": False, "error": str(e)[:80]}
    # Время плюс случайный хвост: часы в контейнере могут отдать одну и ту же
    # микросекунду дважды, и тогда новое фото легло бы на место старого —
    # а старое мы после сохранения удаляем, осталась бы битая картинка
    name = (f"{slugify(slug) or 'article'}_{int(_now() * 1_000_000)}"
            f"{os.urandom(2).hex()}{ext}")
    path = os.path.join(folder, name)
    try:
        with open(path, "wb") as fh:
            fh.write(blob)
    except OSError as e:                                # noqa: BLE001
        return {"ok": False, "error": str(e)[:80]}
    return {"ok": True, "photo": {"path": path, "name": name, "source": "admin"}}


def _drop_photo(rec: dict) -> None:
    """Старое фото стираем с диска: иначе папка пухнет от замен."""
    path = str(((rec or {}).get("photo") or {}).get("path") or "")
    if not path or not os.path.isfile(path):
        return
    folder = os.path.abspath(ctx.photo_dir or os.path.dirname(path))
    if os.path.abspath(os.path.dirname(path)) != folder:
        return                       # чужой файл (комплект) — не трогаем
    try:
        os.remove(path)
    except OSError as e:                                 # noqa: BLE001
        log.debug("статьи: старое фото не удалилось: %s", e)


# ---------------------------------------------------------------------------
#  Публикация: сайт по расписанию, каналы — по кнопке
# ---------------------------------------------------------------------------
def _english_gap(rec: dict) -> bool:
    """Есть ли чем закрыть английскую версию: текст уже переведён?"""
    texts = (rec or {}).get("texts") or {}
    return not str(texts.get("en") or "").strip()


def _needs_translation(rec: dict) -> bool:
    """Не хватает английской версии (текста или заголовка)?"""
    titles = (rec or {}).get("titles") or {}
    return _english_gap(rec) or not _clean_title(titles.get("en"))


async def _ask_ai(text: str) -> Dict[str, Any]:
    """Один запрос перевода к ИИ: {"ok", "text", "reason"}."""
    if ctx.ai_fn is None:
        return {"ok": False, "reason": "no_ai"}
    try:
        out = await ctx.ai_fn(text)
    except Exception as e:                               # noqa: BLE001
        log.warning("статьи: ИИ-перевод не удался: %s", e)
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"[:160]}
    out = str(out or "").strip()
    if not out:
        return {"ok": False, "reason": "no_answer"}
    return {"ok": True, "text": out, "reason": ""}


async def _translate_into(rec: dict, *, target: str = "en") -> Dict[str, Any]:
    """Перевести заголовок и текст статьи, положить перевод в запись.

    Переводятся оба: заголовок виден в карточке списка и в посте канала, и
    русская строка в английском канале смотрелась бы браком. Возвращает
    ``{"ok": bool, "reason": str}``: ``ok=False`` — ИИ не ответил, и это не
    ошибка статьи, а повод перевести вручную (публикация подождёт).
    """
    texts = dict((rec or {}).get("texts") or {})
    titles = dict((rec or {}).get("titles") or {})
    src = str(texts.get("ru") or "").strip()
    if len(src) < AI_MIN:
        return {"ok": False, "reason": "empty_source"}
    res = await _ask_ai(src)
    if not res.get("ok"):
        return {"ok": False, "reason": str(res.get("reason") or "")}
    text = res["text"]
    title_en = str(titles.get(target) or "").strip()
    if not title_en and str(titles.get("ru") or "").strip():
        tres = await _ask_ai(str(titles.get("ru") or ""))
        if not tres.get("ok"):
            return {"ok": False, "reason": str(tres.get("reason") or "")}
        # Заголовок — одна строка: модель иногда отвечает с переносом и точкой
        title_en = " ".join(tres["text"].split()).strip().strip('"“”')
        if len(title_en) > TITLE_MAX:
            title_en = title_en[:TITLE_MAX].rstrip(" ,.;:-")
    if title_en:
        titles[target] = title_en
    texts[target] = text
    rec["titles"] = titles
    rec["texts"] = texts
    rec["ai"] = {"at": _now(), "target": target, "chars": len(text),
                 "title": bool(title_en)}
    return {"ok": True, "reason": ""}


def content_problem(rec: dict) -> str:
    """Что мешает статье выйти, если не считать английскую версию."""
    titles = (rec or {}).get("titles") or {}
    title = _clean_title(titles.get("ru") or titles.get("en"))
    if not title:
        return "Нет заголовка."
    # text_problem проверяет и заголовок, поэтому передаём его сюда же
    problem = text_problem(((rec or {}).get("texts") or {}).get("ru"), title)
    if problem:
        return problem.replace("Нет текста статьи.", "Нет русского текста.")
    return ""


def publish_problem(rec: dict) -> str:
    """Почему статью нельзя показать на сайте. Пусто — можно.

    Кроме текста нужна английская версия: заголовок и текст. Русский заголовок
    на английской странице выглядит поломкой, поэтому без перевода не выходим.
    """
    problem = content_problem(rec)
    if problem:
        return problem
    titles = (rec or {}).get("titles") or {}
    if not _clean_title(titles.get("en")):
        return ("Нет английского заголовка. Нажмите «🌐 Перевести ИИ» или "
                "впишите перевод в правое окно.")
    if _english_gap(rec):
        return ("Нет английской версии. Нажмите «🌐 Перевести ИИ» или "
                "впишите перевод в правое окно.")
    return ""


async def publish_now(aid: str, *, by: Optional[int] = None,
                      allow_ai: bool = True) -> Dict[str, Any]:
    """Опубликовать статью на сайте. Английскую версию добудет ИИ.

    Публикация не происходит, если английской версии нет и ИИ не смог её
    сделать: требование раздела — на английской странице английский текст.
    Админ в этом случае переводит руками и публикует снова.
    """
    rec = ctx.store.get(aid)
    if rec is None:
        return {"ok": False, "error": "not_found"}
    # ИИ зовём только тогда, когда не хватает именно английской версии: если
    # текст пустой или короткий, переводом это не лечится — пусть админ
    # сначала напишет статью, а не ждёт ответа сервиса
    problem = content_problem(rec)
    ai_note = ""
    if not problem and _needs_translation(rec) and allow_ai:
        res = await _translate_into(rec)
        if res.get("ok"):
            ai_note = "translated"
            problem = publish_problem(rec)
        else:
            ctx.store.add(rec)                     # сохраняем то, что уже есть
            return {"ok": False, "error": "ai_unavailable",
                    "hint": ("ИИ не смог перевести статью — впишите английскую "
                             "версию вручную (правое окно), публикация подождёт."),
                    "reason": str(res.get("reason") or "")}
    elif not problem and _needs_translation(rec):
        problem = publish_problem(rec)             # просили без ИИ — говорим, чего нет
    if problem:
        return {"ok": False, "error": "not_ready", "hint": problem}
    now = _now()
    rec["status"] = "published"
    rec["published_at"] = float(rec.get("published_at") or 0) or now
    rec["retry_at"] = 0.0
    rec["last_error"] = ""
    if by:
        rec["published_by"] = int(by)
    rec = ctx.store.add(rec)
    ctx.last = {"id": rec.get("id"), "at": now, "event": "publish", "by": by,
                "ai": ai_note}
    log.info("статьи: «%s» опубликована%s", rec.get("id"),
             " (перевод ИИ)" if ai_note else "")
    return {"ok": True, "item": admin_article(rec), "ai": ai_note}


def caption_for(rec: dict, lang: str = "ru", *, url: str = "") -> str:
    """Пост в канал: заголовок, начало статьи и ссылка на полный текст.

    В канал уходит небольшой пост — Telegram разрешает 1024 знака под фото.
    Полный текст живёт на сайте, поэтому в подписи только лид и ссылка.
    """
    code = "ru" if str(lang).startswith("ru") else "en"
    titles = rec.get("titles") or {}
    title = str(titles.get(code) or titles.get("ru") or titles.get("en") or "").strip()
    texts = rec.get("texts") or {}
    body = str(texts.get(code) or texts.get("ru") or "")
    lead = _lead(body, 260)
    link = url or (ctx.public_url.rstrip("/") + article_path(str(rec.get("id") or "")))
    more = "Читать на сайте →" if code == "ru" else "Read on the site →"
    head = f"📰 <b>{_esc_html(title)}</b>"
    parts = [head]
    if lead:
        parts.append(_esc_html(lead))
    parts.append(f'<a href="{link}">{more}</a>')
    return "\n\n".join(parts)


def _lead(text: Any, limit: int = 260) -> str:
    """Лид для поста в канал: первый нормальный абзац, а не заголовок и список.

    Статья начинается с «## Что такое стена» и пунктов списка — если брать
    просто первые 260 знаков, в канале получается склейка заголовка с пунктами.
    Поэтому идём по абзацам и берём первый «обычный»: он и читается как лид.
    """
    for block in str(text or "").split("\n\n"):
        body = block.strip()
        if not body or body[0] in "#>-*|" or body.startswith("---"):
            continue
        if body[:2].rstrip(".").isdigit():            # нумерованный список
            continue
        return excerpt(body, limit)
    return excerpt(text, limit)


def _esc_html(text: Any) -> str:
    """Экранируем текст для подписи канала (разметка Telegram — только наша)."""
    import html as html_mod
    return html_mod.escape(str(text if text is not None else ""), quote=False)


async def send_to_channels(aid: str, langs=("ru", "en"),
                           by: Optional[int] = None) -> Dict[str, Any]:
    """Отправить статью в каналы: русская версия в русский, английская — в английский.

    Кнопку нажимает админ: сразу после публикации на сайте ничего не уходит,
    чтобы вычитать текст можно было спокойно. Повторная отправка разрешена —
    «уже уходило» в ответе видно, решает админ.
    """
    rec = ctx.store.get(aid)
    if rec is None:
        return {"ok": False, "error": "not_found"}
    if str(rec.get("status")) != "published":
        return {"ok": False, "error": "not_published",
                "hint": "Сначала опубликуйте статью на сайте."}
    bot = ctx.bot
    if bot is None:
        return {"ok": False, "error": "no_bot", "hint": "Telegram-бот недоступен."}
    running = getattr(bot, "running", False)
    try:
        running = bool(running() if callable(running) else running)
    except Exception:                                    # noqa: BLE001
        running = False
    if not running:
        return {"ok": False, "error": "bot_off",
                "hint": "Бот выключен: укажите токен — и каналы снова доступны."}
    langs = tuple(x for x in ("ru", "en") if x in set(langs or ("ru", "en"))) or ("ru", "en")
    url = ctx.public_url.rstrip("/") + "/articles/" + str(rec.get("id"))
    photo = str(((rec.get("photo") or {}).get("path")) or "")
    has_photo = bool(photo and os.path.isfile(photo))
    results: Dict[str, Any] = {}
    sent = dict(rec.get("sent") or {})
    tg = dict(rec.get("tg") or {})
    for lang in langs:
        cid = str(bot.channel_chat_id_en() if lang == "en" else bot.channel_chat_id()).strip()
        if not cid:
            results[lang] = {"ok": False, "error": "no_channel",
                             "hint": ("Английский канал не привязан."
                                      if lang == "en" else
                                      "Русский канал не привязан — перешлите боту пост из канала.")}
            continue
        chats = cid if cid.lstrip("-").isdigit() else cid
        caption = caption_for(rec, lang, url=url)
        already = bool(sent.get(lang))
        try:
            if has_photo:
                mid = await bot.send_photo(chats, photo, caption, raw=True)
            else:
                mid = await bot.send(chats, caption, raw=True)
        except Exception as e:                           # noqa: BLE001
            log.warning("статьи: отправка в канал (%s): %s", lang, e)
            results[lang] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
            continue
        if not mid:
            err = str(getattr(bot, "_last_tg_err", "") or "Telegram не принял пост")
            results[lang] = {"ok": False, "error": err[:160], "channel": cid}
            continue
        sent[lang] = _now()
        tg[lang] = {"at": _now(), "chat": cid, "message_id": int(mid),
                    "photo": has_photo}
        results[lang] = {"ok": True, "channel": cid, "message_id": int(mid),
                         "again": already}
    rec["sent"] = sent
    rec["tg"] = tg
    if any(v.get("ok") for v in results.values()):
        ctx.last = {"id": rec.get("id"), "at": _now(), "event": "send", "by": by,
                    "langs": [k for k, v in results.items() if v.get("ok")]}
    ctx.store.add(rec)
    ok = any(v.get("ok") for v in results.values())
    return {"ok": ok, "results": results, "item": admin_article(rec),
            "errors": [f"{k}: {v.get('hint') or v.get('error')}"
                       for k, v in results.items() if not v.get("ok")]}


def admin_article(rec: dict, channels: Optional[dict] = None) -> dict:
    """Статья для админки: обе языковые версии, статус, метки отправки."""
    rec = rec or {}
    out = public_article(rec, "ru", with_text=True)
    out.update({
        "titles": {"ru": str((rec.get("titles") or {}).get("ru") or ""),
                   "en": str((rec.get("titles") or {}).get("en") or "")},
        "texts": {"ru": str((rec.get("texts") or {}).get("ru") or ""),
                  "en": str((rec.get("texts") or {}).get("en") or "")},
        "publish_at": float(rec.get("publish_at") or 0),
        "published_at": float(rec.get("published_at") or 0),
        "created": float(rec.get("created") or 0),
        "updated": float(rec.get("updated") or 0),
        "sent": {k: float(v or 0) for k, v in (rec.get("sent") or {}).items()},
        "ai": dict(rec.get("ai") or {}),
        "last_error": str(rec.get("last_error") or ""),
        "ready": not publish_problem(rec),
        "problem": publish_problem(rec),
        "preview": {"ru": public_article(rec, "ru").get("html") or "",
                    "en": public_article(rec, "en").get("html") or ""},
        "channels": channels or {},
    })
    return out


#: Подписи для страниц: слова живут на сервере, потому что карточки списка и
#: текст статьи приходят в HTML, а не рисуются скриптом.
_PAGE_TEXT = {
    "ru": {"minutes": "мин чтения", "in": "Опубликовано", "all": "Все статьи",
           "empty": "Статей пока нет — первая появится здесь после публикации.",
           "channel": "Канал в Telegram",
           "draft": "Черновик — статью видите только вы, гости её не откроют.",
           "planned": "Запланирована к публикации:",
           "unpublished": "Снята с публикации — гости её не видят."},
    "en": {"minutes": "min read", "in": "Published", "all": "All articles",
           "empty": "No articles yet — the first one will appear here.",
           "channel": "Telegram channel",
           "draft": "Draft — only you can see it, visitors cannot open the page.",
           "planned": "Scheduled for publication:",
           "unpublished": "Unpublished — visitors cannot see it."},
}


def _page_text(lang: str) -> Dict[str, str]:
    return _PAGE_TEXT["ru" if str(lang).startswith("ru") else "en"]


def _reading_minutes(text: str) -> int:
    """Сколько минут читать: по 180 слов в минуту, минимум одна."""
    words = len(str(text or "").split())
    return max(1, int(round(words / 180.0)))


def _card_html(item: dict, lang: str) -> str:
    """Карточка статьи для списка: обложка, дата, заголовок и лид."""
    words = _page_text(lang)
    ts = float(item.get("published_at") or 0)
    photo = item.get("photo") or {}
    img = ""
    if photo.get("url"):
        img = ('<img class="ac-photo" src="' + _attr(photo["url"]) + '" alt="'
               + _attr(item.get("title") or "") + '" loading="lazy" width="1200" height="630">')
    date = ('<time class="ac-date" data-ts="' + str(int(ts)) + '">'
            + _esc_html(_date_label(ts, lang)) + "</time>") if ts else ""
    title_attr = _lang_attr(item.get("title_lang"), lang)
    text_attr = _lang_attr(item.get("text_lang"), lang)
    # «Читать →» на карточке: у zh/hi/es тоже свой язык, а не английский
    more = seo_pages.text(lang, "art.read", t_item(lang, "Читать", "Read"))
    return (
        '<a class="art-card" href="/articles/' + _attr(str(item.get("id") or "")) + '">'
        + img
        + '<div class="ac-body">' + date
        + "<h2" + title_attr + ">" + _esc_html(item.get("title") or "") + "</h2>"
        + ('<p class="ac-sum"' + text_attr + ">"
           + _esc_html(item.get("excerpt") or "") + "</p>"
           if item.get("excerpt") else "")
        + '<span class="ac-more">' + _esc_html(more)
        + " →</span></div></a>"
    )


def t_item(lang: str, ru: str, en: str) -> str:
    return ru if str(lang).startswith("ru") else en


def _date_label(ts: float, lang: str) -> str:
    """Дата в поясе выпусков (МСК) — как у дайджеста; браузер пересчитает в свой."""
    months_ru = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                 "августа", "сентября", "октября", "ноября", "декабря")
    months_en = ("January", "February", "March", "April", "May", "June", "July",
                 "August", "September", "October", "November", "December")
    try:
        tm = time.gmtime(float(ts) + tz_offset())
        if str(lang).startswith("ru"):
            return f"{tm.tm_mday} {months_ru[tm.tm_mon - 1]} {tm.tm_year}"
        return f"{months_en[tm.tm_mon - 1]} {tm.tm_mday}, {tm.tm_year}"
    except Exception:                                    # noqa: BLE001
        return ""


def _lang_attr(content_lang: str, page_lang: str) -> str:
    """`` lang="ru"`` для русского текста на странице другого языка.

    Опубликовать статью без английской версии нельзя, но у старых материалов
    её может не быть — тогда на английской странице стоит русский текст, и об
    этом надо сказать разметкой: иначе поисковик считает его английским, а
    скринридер читает с неверным произношением.
    """
    code = str(content_lang or "").lower()
    page = "ru" if str(page_lang or "").startswith("ru") else "en"
    if not code or code == page:
        return ""
    return f' lang="{_attr(code)}"'


def _attr(value: Any) -> str:
    import html as html_mod
    return html_mod.escape(str(value if value is not None else ""), quote=True)


def _channel_url(lang: str = "ru") -> str:
    """Адрес канала для ссылки на странице: на английском сайте — английский."""
    bot = ctx.bot
    if bot is None:
        return ""
    try:
        if str(lang).startswith("ru"):
            return str(getattr(bot, "channel_url", "") or "")
        return str(bot.channel_url_en() or getattr(bot, "channel_url", "") or "")
    except Exception:                                    # noqa: BLE001
        return ""


def _channels() -> Dict[str, Any]:
    """Куда пойдут версии статьи: русский канал и английский."""
    bot = ctx.bot
    if bot is None:
        return {"ru": {"id": "", "name": "", "ready": False},
                "en": {"id": "", "name": "", "ready": False}}
    out: Dict[str, Any] = {}
    for code in ("ru", "en"):
        try:
            cid = str(bot.channel_chat_id_en() if code == "en" else bot.channel_chat_id()).strip()
        except Exception:                                # noqa: BLE001
            cid = ""
        name = ""
        try:
            name = str(bot.channel_url_en() if code == "en" else bot.channel_url or "")
        except Exception:                                # noqa: BLE001
            name = ""
        out[code] = {"id": cid, "name": name, "ready": bool(cid)}
    return out


def _ai_state() -> Dict[str, Any]:
    if ctx.ai_status_fn is None:
        return {"enabled": ctx.ai_fn is not None, "providers": [], "last": {}}
    try:
        return dict(ctx.ai_status_fn() or {})
    except Exception as e:                               # noqa: BLE001
        log.debug("статьи: состояние ИИ не прочиталось: %s", e)
        return {"enabled": False, "providers": [], "last": {}}


# ---------------------------------------------------------------------------
#  Маршруты
# ---------------------------------------------------------------------------
def register_article_routes(app) -> None:
    router = APIRouter()

    # --- страницы -----------------------------------------------------------
    @router.get("/articles")
    async def page_articles(request: Request):
        """Список статей: обложки, заголовки и даты на языке посетителя."""
        if not ctx.page_ok:
            return JSONResponse({"ok": False, "error": "off"}, status_code=404)
        lang, auto = seo_pages.lang_of(request)
        items = ctx.store.published()
        og_image = ""
        if items:
            photo = index_row(items[0], lang).get("photo")
            if photo:
                og_image = (ctx.public_url or seo_pages.SITE_URL).rstrip("/") + photo["url"]
        words = _page_text(lang)
        cards = "".join(_card_html(index_row(r, lang), lang) for r in items[:60])
        if not cards:
            cards = '<div class="art-empty">' + _esc_html(words["empty"]) + "</div>"
        fresh_ts = float((items[0].get("published_at") if items else 0) or 0)
        channel = ""
        url = _channel_url(lang)
        if url:
            channel = ('<a class="chip link" id="art-channel" href="' + _attr(url)
                       + '" target="_blank" rel="noopener">'
                       + _esc_html(words["channel"]) + " →</a>")
        # Заголовок и описание списка берём из словаря (ключи seo.articles.*):
        # у всех пяти языков свои строки, а подставленные «на лету» русский с
        # английским оставляли zh/hi/es без локализации и расходились с og:*
        return seo_pages.render(
            "articles.html", lang, "/articles",
            extra_head=seo_pages.jsonld("articles", lang, image=og_image),
            og_image=og_image, auto=auto,
            body={"articles": cards, "total": str(len(items)),
                  "fresh": _esc_html(_date_label(fresh_ts, lang)) or "—",
                  "channel": channel},
        )

    @router.get("/articles/{slug}")
    async def page_article(request: Request, slug: str):
        """Страница статьи: текст на языке посетителя, чужой адрес — 404."""
        if not ctx.page_ok:
            return JSONResponse({"ok": False, "error": "off"}, status_code=404)
        lang, auto = seo_pages.lang_of(request)
        rec = ctx.store.get(slug)
        if rec is None or not _visible(rec, request):
            return seo_pages.render("404.html", lang, "/articles", status_code=404)
        item = public_article(rec, lang)
        photo = item.get("photo")
        og_image = ""
        if photo:
            og_image = (ctx.public_url or seo_pages.SITE_URL).rstrip("/") + photo["url"]
        url = (ctx.public_url or seo_pages.SITE_URL).rstrip("/") + article_path(str(slug))
        ts = float(item.get("published_at") or 0)
        articles = {
            "day": "", "title": item.get("title") or "",
            "desc": item.get("excerpt") or "",
            "url": url,
            "date": time.strftime("%Y-%m-%d", time.gmtime(ts or _now())),
        }
        words = _page_text(lang)
        minutes = _reading_minutes(str((rec.get("texts") or {}).get(
            "ru" if str(lang).startswith("ru") else "en")
            or (rec.get("texts") or {}).get("ru") or ""))
        note = ""
        if not _is_public(rec):
            # Статью видит только админ: без пометки он решит, что она на сайте
            if str(rec.get("status")) == "scheduled":
                when = _date_label(float(rec.get("publish_at") or 0), lang)
                note = words["planned"] + " " + when
            elif str(rec.get("status")) == "draft":
                note = words["draft"]
            else:
                note = words["unpublished"]
        meta = ""
        if ts:
            meta = ('<time data-ts="' + str(int(ts)) + '">' + _esc_html(_date_label(ts, lang))
                    + "</time>")
        meta += (' <span class="dot">•</span> <span>'
                 + _esc_html(f"{minutes} {words['minutes']}") + "</span>")
        cover = ""
        if photo and photo.get("url"):
            cover = ('<img class="art-cover" id="art-cover" src="' + _attr(photo["url"])
                     + '" alt="' + _attr(item.get("title") or "")
                     + '" loading="eager" width="1200" height="630">')
        return seo_pages.render(
            "article.html", lang, "/articles/" + str(slug),
            extra_head=seo_pages.jsonld("article", lang, image=og_image,
                                        article=articles),
            og_image=og_image, auto=auto,
            title=item.get("title") or "",
            desc=item.get("excerpt") or "",
            body={"title": _esc_html(item.get("title") or ""),
                  "title_lang": _lang_attr(item.get("title_lang"), lang),
                  "text_lang": _lang_attr(item.get("text_lang"), lang),
                  "meta": meta,
                  "note": (_esc_html(note) if note else ""),
                  "body": item.get("html") or "",
                  "cover": cover,
                  "date_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime(ts or _now()))},
        )

    # --- публичные данные ---------------------------------------------------
    @router.get("/api/articles")
    async def api_list(lang: str = seo_pages.DEFAULT_LANG, limit: int = 24):
        items = ctx.store.published()
        try:
            lim = max(1, min(100, int(limit)))
        except (TypeError, ValueError):
            lim = 24
        return {"ok": True,
                "items": [index_row(r, lang) for r in items[:lim]],
                "count": len(items),
                "stats": ctx.store.stats(),
                "now": _now(),
                "tz_hours": round(tz_offset() / 3600.0, 2)}

    @router.get("/api/articles/photo/{slug}")
    async def api_photo(slug: str, request: Request):
        """Обложка статьи: публично — у опубликованных, админу — у любой."""
        rec = ctx.store.get(slug)
        path = str(((rec or {}).get("photo") or {}).get("path") or "")
        if not path or not os.path.isfile(path):
            return JSONResponse({"ok": False, "error": "no_photo"}, status_code=404)
        if not _visible(rec, request):
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return _photo_response(path)

    @router.get("/api/articles/status")
    async def api_status():
        return {"ok": True, "store_error": ctx.store.error, "stats": ctx.store.stats(),
                "last": dict(ctx.last), "busy": ctx.busy}

    @router.get("/api/articles/{slug}")
    async def api_one(slug: str, lang: str = seo_pages.DEFAULT_LANG, request: Request = None):
        rec = ctx.store.get(slug)
        if rec is None or not _visible(rec, request):
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True, "item": public_article(rec, lang), "now": _now()}

    # --- админка ------------------------------------------------------------
    @router.get("/api/admin/articles")
    async def api_admin_list(request: Request):
        user, err = _admin(request)
        if err:
            return err
        channels = _channels()
        items = [admin_article(r, channels) for r in ctx.store.list()]
        return {"ok": True, "items": items, "channels": channels,
                "stats": ctx.store.stats(), "ai": _ai_state(),
                "bot": bool(ctx.bot is not None),
                "limits": {"title": TITLE_MAX, "photo": MAX_PHOTO,
                           "keep": ctx.store.keep or DEFAULT_KEEP},
                "now": _now(),
                "tz_hours": round(tz_offset() / 3600.0, 2),
                "site": ctx.public_url}

    @router.post("/api/admin/articles/save")
    async def api_admin_save(request: Request,
                             body: Optional[dict] = Body(default=None)):
        """Создать или обновить статью: тексты, обложка, дата публикации.

        Черновик можно сохранять как угодно часто. Если у статьи уже стоит
        дата публикации в будущем, она остаётся запланированной: правки текста
        не отменяют расписание, а «Сохранить и опубликовать» — отменяет.
        """
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        rid = str(body.get("id") or "").strip()
        rec = dict(ctx.store.get(rid) or {}) if rid else {}
        # Поля, которых нет в запросе, не стираем: панель может прислать только
        # фото или только дату — остальное должно остаться как было
        was = {"titles": dict(rec.get("titles") or {}),
               "texts": dict(rec.get("texts") or {})}

        def keep(key: str, lang: str, clean) -> str:
            if body.get(key) is None:
                return str((was["titles" if key.startswith("title") else "texts"]
                            .get(lang)) or "")
            return clean(body.get(key))

        title_ru = keep("title_ru", "ru", _clean_title)
        title_en = keep("title_en", "en", _clean_title)
        text_ru = keep("text_ru", "ru", _clean_text)
        text_en = keep("text_en", "en", _clean_text)
        if not title_ru and not title_en:
            return JSONResponse({"ok": False, "error": "no_title",
                                 "hint": "Нужен заголовок — хотя бы русский."},
                                status_code=400)
        if not rec and not text_ru:
            return JSONResponse({"ok": False, "error": "no_text",
                                 "hint": "Нужен русский текст статьи."},
                                status_code=400)
        problem = text_problem(text_ru, title_ru) if text_ru else ""
        if problem and problem.startswith("Слишком длинный"):
            return JSONResponse({"ok": False, "error": "too_long", "hint": problem},
                                status_code=400)
        rec["titles"] = {"ru": title_ru, "en": title_en}
        rec["texts"] = {"ru": text_ru, "en": text_en}
        # Адрес статьи: из английского заголовка (его читают в поиске), иначе
        # из русского — и больше не меняется, иначе ссылки в каналах сломаются.
        if not rec.get("id"):
            base = slugify(title_en or title_ru)
            rec["id"] = unique_slug(base, ctx.store.slugs())
        rec.setdefault("created", _now())
        rec["status"] = str(rec.get("status") or "draft")
        # Фото: новое из формы, «убрать» — стираем файл и ссылку
        if body.get("remove_photo"):
            _drop_photo(rec)
            rec.pop("photo", None)
        blob, filename = _body_photo(body)
        if not blob and str(body.get("photo") or "").strip():
            # Клиент прислал фото, но прочитать его не вышло (битый data-URL или
            # слишком большой файл). Молча оставить старое фото нельзя: админ
            # решит, что новое сохранилось
            return JSONResponse(
                {"ok": False, "error": "bad_photo",
                 "hint": ("Фото не удалось прочитать — попробуйте ещё раз: "
                          "jpg, png или webp до 12 МБ.")},
                status_code=400)
        if blob:
            up = save_photo(rec["id"], blob, filename)
            if not up.get("ok"):
                from web_upload import PHOTO_ERR
                return JSONResponse(
                    {"ok": False, "error": "bad_photo",
                     "hint": PHOTO_ERR.get(str(up.get("error")), "фото не принято")},
                    status_code=400)
            _drop_photo(rec)
            rec["photo"] = up["photo"]
        else:
            rec.setdefault("photo", {})
            if not rec["photo"]:
                rec.pop("photo", None)
        # Дата публикации: пусто — черновик, будущее — план на это время
        publish_at = _parse_when(body.get("publish_at"))
        rec["publish_at"] = publish_at
        publish_now_flag = bool(body.get("publish"))
        if publish_now_flag:
            pass                                   # публикуем ниже, после сохранения
        elif str(rec.get("status")) == "published":
            pass                                   # правки уже опубликованной статьи
        elif publish_at > _now():
            rec["status"] = "scheduled"
            rec["retry_at"] = 0.0
        else:
            rec["status"] = "draft"
        saved = ctx.store.add(rec)
        if not publish_now_flag:
            return {"ok": True, "item": admin_article(saved, _channels()),
                    "saved": True}
        res = await publish_now(saved["id"], by=(user or {}).get("id"))
        if not res.get("ok"):
            return JSONResponse({**res, "item": admin_article(
                ctx.store.get(saved["id"]) or saved, _channels())}, status_code=409)
        return {"ok": True, "item": res.get("item"), "saved": True,
                "published": True, "ai": res.get("ai")}

    @router.post("/api/admin/articles/{aid}/translate")
    async def api_admin_translate(request: Request, aid: str,
                                  body: Optional[dict] = Body(default=None)):
        """Перевод русской версии на английский — по кнопке в редакторе."""
        user, err = _admin(request)
        if err:
            return err
        rec = ctx.store.get(aid)
        if rec is None:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        body = body or {}
        src = _clean_text(body.get("text_ru")) or str((rec.get("texts") or {}).get("ru") or "")
        if len(src) < AI_MIN:
            return JSONResponse({"ok": False, "error": "no_text",
                                 "hint": "Сначала напишите русский текст."},
                                status_code=400)
        rec = dict(rec)
        rec["texts"] = {"ru": src, "en": str((rec.get("texts") or {}).get("en") or "")}
        if body.get("title_ru") is not None:
            titles = dict(rec.get("titles") or {})
            titles["ru"] = _clean_title(body.get("title_ru"))
            if body.get("title_en") is not None:
                titles["en"] = _clean_title(body.get("title_en"))
            rec["titles"] = titles
        res = await _translate_into(rec)
        if not res.get("ok"):
            reason = str(res.get("reason") or "")
            hint = ("ИИ недоступен — впишите английскую версию вручную."
                    if reason in ("no_ai", "no_answer", "") else
                    f"ИИ не справился ({reason[:80]}) — переведите вручную.")
            ctx.store.add(rec)
            return JSONResponse({"ok": False, "error": "ai_unavailable",
                                 "hint": hint, "ai": _ai_state()}, status_code=503)
        saved = ctx.store.add(rec)
        item = admin_article(saved, _channels())
        return {"ok": True, "item": item, "text_en": item["texts"]["en"],
                "title_en": item["titles"]["en"], "ai": _ai_state()}

    @router.post("/api/admin/articles/{aid}/publish")
    async def api_admin_publish(request: Request, aid: str,
                                body: Optional[dict] = Body(default=None)):
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        if body.get("title_ru") is not None or body.get("text_ru") is not None:
            # Правки могли не сохранить: публикуем то, что сейчас в форме
            rec = dict(ctx.store.get(aid) or {})
            if rec:
                titles = dict(rec.get("titles") or {})
                if body.get("title_ru") is not None:
                    titles["ru"] = _clean_title(body.get("title_ru"))
                if body.get("title_en") is not None:
                    titles["en"] = _clean_title(body.get("title_en"))
                rec["titles"] = titles
                texts = dict(rec.get("texts") or {})
                if body.get("text_ru") is not None:
                    texts["ru"] = _clean_text(body.get("text_ru"))
                if body.get("text_en") is not None:
                    texts["en"] = _clean_text(body.get("text_en"))
                rec["texts"] = texts
                ctx.store.add(rec)
        res = await publish_now(aid, by=(user or {}).get("id"),
                                allow_ai=not bool(body.get("no_ai")))
        if not res.get("ok"):
            return JSONResponse({**res, "ai": _ai_state()}, status_code=409)
        return {**res, "ai_state": _ai_state(), "channels": _channels()}

    @router.post("/api/admin/articles/{aid}/unpublish")
    async def api_admin_unpublish(request: Request, aid: str):
        """Снять статью с сайта: остаётся черновиком со своим планом."""
        user, err = _admin(request)
        if err:
            return err
        rec = ctx.store.get(aid)
        if rec is None:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        rec = dict(rec)
        rec["published_at"] = 0.0
        rec["status"] = "scheduled" if float(rec.get("publish_at") or 0) > _now() else "draft"
        saved = ctx.store.add(rec)
        return {"ok": True, "item": admin_article(saved, _channels())}

    @router.post("/api/admin/articles/{aid}/send")
    async def api_admin_send(request: Request, aid: str,
                             body: Optional[dict] = Body(default=None)):
        """Отправить версии статьи в каналы: RU → русский, EN → английский."""
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        langs = body.get("langs")
        if not isinstance(langs, list) or not langs:
            langs = ("ru", "en")
        res = await send_to_channels(aid, tuple(str(x) for x in langs),
                                     by=(user or {}).get("id"))
        if not res.get("ok"):
            code = 404 if res.get("error") == "not_found" else 409
            return JSONResponse(res, status_code=code)
        return {**res, "channels": _channels()}

    @router.post("/api/admin/articles/{aid}/delete")
    async def api_admin_delete(request: Request, aid: str):
        user, err = _admin(request)
        if err:
            return err
        rec = ctx.store.get(aid)
        if rec is None:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        _drop_photo(rec)
        ctx.store.delete(aid)
        return {"ok": True, "stats": ctx.store.stats()}

    app.include_router(router)


# ---------------------------------------------------------------------------
#  Вспомогательное
# ---------------------------------------------------------------------------
def _parse_when(value: Any) -> float:
    """Дата из формы → epoch. Понимаем ISO («2026-10-01T18:30») и число."""
    if value in (None, "", 0, "0"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return 0.0
    if raw.replace(".", "", 1).isdigit():
        try:
            return float(raw)
        except ValueError:
            return 0.0
    if raw.endswith("Z"):
        raw = raw[:-1]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            import calendar
            tm = time.strptime(raw, fmt)
            return float(calendar.timegm(tm))
        except ValueError:
            continue
    return 0.0


def _body_photo(body: Dict[str, Any]):
    """Фото из тела запроса: data-URL (JSON) — так шлёт панель админки."""
    from web_upload import decode_json_photo
    if not (body or {}).get("photo"):
        return b"", ""
    return decode_json_photo({"data": body.get("photo"),
                              "filename": body.get("photo_name") or ""})


def _is_public(rec: dict) -> bool:
    return str((rec or {}).get("status")) == "published"


def _visible(rec: dict, request: Optional[Request] = None) -> bool:
    """Статья видна: опубликована — всем, черновик — только админу."""
    if _is_public(rec):
        return True
    if request is None:
        return False
    try:
        from web_account import current_user
        user = current_user(request)
        return bool(user and user.get("is_admin"))
    except Exception:                                    # noqa: BLE001
        return False


def _due_now(rec: dict, now: float) -> bool:
    return (str((rec or {}).get("status")) == "scheduled"
            and 0 < float((rec or {}).get("publish_at") or 0) <= now
            and float((rec or {}).get("retry_at") or 0) <= now)


async def tick(now: Optional[float] = None) -> Dict[str, Any]:
    """Один проход планировщика: публикуем всё, чему пришло время.

    Вынесено из петли отдельной функцией: так же, как у дайджеста, это можно
    проверить в тесте, не дожидаясь реального времени.
    """
    now = _now() if now is None else float(now)
    out: Dict[str, Any] = {"published": [], "failed": [], "skipped": 0}
    for rec in ctx.store.due(now):
        if not _due_now(rec, now) or ctx.busy:
            out["skipped"] += 1
            continue
        ctx.busy = True
        try:
            res = await publish_now(str(rec.get("id")), allow_ai=True)
        finally:
            ctx.busy = False
        if res.get("ok"):
            out["published"].append(str(rec.get("id")))
            continue
        fresh = ctx.store.get(str(rec.get("id"))) or dict(rec)
        fresh["last_error"] = str(res.get("hint") or res.get("error") or "")
        fresh["retry_at"] = _now() + RETRY_SEC
        ctx.store.add(fresh)
        out["failed"].append({"id": rec.get("id"), "why": fresh["last_error"]})
        log.info("Статьи: «%s» не опубликовалась (%s) — повтор через %d мин",
                 rec.get("id"), fresh["last_error"], int(RETRY_SEC // 60))
    return out


async def scheduler_loop(check_sec: float = 30.0) -> None:
    """Фоновая петля: публикует статьи, у которых наступило время.

    В Telegram отсюда ничего не уходит — каналы ждут кнопки админа. Если
    английская версия ещё не готова, пробуем перевести её ИИ; не получилось —
    статья остаётся запланированной, а следующая попытка будет не раньше
    ``RETRY_SEC``: держать ИИ в цикле каждые полминуты нельзя.
    """
    log.info("Статьи: планировщик запущен (проверка раз в %.0f с)", check_sec)
    while True:
        try:
            await tick()
        except Exception as e:                           # noqa: BLE001
            log.debug("статьи: планировщик споткнулся: %s", e)
        try:
            import asyncio
            await asyncio.sleep(check_sec)
        except Exception:                                # noqa: BLE001
            return
