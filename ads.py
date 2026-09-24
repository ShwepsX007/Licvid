"""📣 Рекламные посты: панель в админке, отправка в бота и каналы, баннер на главной.

Реклама — не сводка: у неё свой текст, своё фото, свой срок и свой список
получателей. Админ пишет пост в панели /admin, выбирает **куда** его отправить
(бот, каналы RU/EN и любые другие, главная страница сайта) и **когда** — сразу
или в назначенное время. Через выбранный срок объявление снимается само: баннер
на главной пропадает, а посты в каналах удаляются.

Как это устроено:

    * ``GET  /api/ads``                 — что показывать баннером (публично);
    * ``GET  /api/ads/{id}/photo``      — фото баннера (пока объявление живо);
    * ``GET  /api/admin/ads``           — список, каналы и лимиты для админки;
    * ``POST /api/admin/ads``           — сохранить пост (черновик или расписание);
    * ``POST /api/admin/ads/{id}``      — правка текста, фото, получателей, срока;
    * ``POST /api/admin/ads/{id}/send`` — отправить сейчас;
    * ``POST /api/admin/ads/{id}/stop`` — снять объявление досрочно (и удалить посты);
    * ``POST /api/admin/ads/{id}/delete`` — убрать запись из панели.

Расписание ведёт ``AdService``: раз в ~20 секунд он публикует то, чему пришло
время, и снимает то, у чего вышел срок. Баннер при этом не ждёт планировщика —
``active_site_ads`` сам отсекает истёкшие объявления, поэтому на главной они не
задерживаются ни на секунду.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, JSONResponse

from hour_board import tz_offset

log = logging.getLogger("liqscope.ads")

# Текст поста: с фото Telegram принимает подпись до 1024 знаков, без фото —
# обычное сообщение до 4096. Сайт показывает тот же текст.
TEXT_MAX_PLAIN = 4000
TEXT_MAX_PHOTO = 1024
CAPTION_LIMIT = 1024
AD_KEEP_LIMIT = 40             # столько записей держим в панели

URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+")
CHAT_RE = re.compile(r"^(-?\d{5,20}|@[A-Za-z0-9_]{4,32})$")

#: Кликабельная ссылка объявления: только http/https, иначе «ссылка» вида
#: javascript:… превратила бы баннер в исполняемый код на чужой странице.
LINK_MAX = 600
#: HTML-баннер: произвольный код для слота баннера (партнёрский блок, iframe).
#: Админ доверенный, но размер ограничиваем — страница не должна весить мегабайты.
HTML_MAX = 20000

#: Настройки баннера по умолчанию — «как крутить и как плавно менять».
#: Хранятся в settings админки, поэтому меняются без перезапуска сервера.
BANNER_DEFAULTS = {
    "rotate_sec": 8,        # сколько секунд стоит один слайд
    "fade_ms": 500,         # плавность перехода между слайдами
    "layout": "carousel",   # carousel — листаем, grid — все сразу
    "max": 4,               # сколько объявлений показываем
    "fit": "contain",       # contain — фото видно целиком, cover — заполняет блок
    "caption": "below",     # below — подпись под фото, side — сбоку (как раньше)
}
BANNER_LIMITS = {          # min, max — за пределы не выпускаем никогда
    "rotate_sec": (2, 120),
    "fade_ms": (0, 2000),
    "max": (1, 8),
}
BANNER_CHOICES = {
    "layout": ("carousel", "grid"),
    "fit": ("contain", "cover"),
    "caption": ("below", "side"),
}
BANNER_KEYS = tuple(BANNER_DEFAULTS)
#: Ключ настройки в table settings — одна JSON-строка на весь блок.
BANNER_SETTING = "site_banner"


def clean_link(raw: Any) -> str:
    """Ссылка объявления: голая, без пробелов, только http/https.

    Баннер висит на публичной странице, поэтому «ссылка» тут — атрибут href,
    и пропустить в него ``javascript:`` значит отдать клик на произвольный код.
    Разрешаем ровно два вида: адрес со схемой http(s) и домен без схемы
    («gate.com/promo» — админ пишет его с телефона), всё остальное — пусто.
    """
    val = str(raw or "").strip()
    if not val:
        return ""
    if not re.match(r"^https?://", val, flags=re.I):
        if "://" in val or val.startswith("//") or ":" in val.split("/", 1)[0]:
            return ""                       # чужая схема: ftp, data, javascript…
        if not re.match(r"^[\w.-]+\.[a-z]{2,}", val, flags=re.I):
            return ""                       # не похоже на адрес — не угадываем
        val = "https://" + val
    if len(val) > LINK_MAX or re.search(r"[\s\"'<>\\]", val):
        return ""
    return val


def clean_html_banner(raw: Any) -> str:
    """HTML-баннер: произвольный код админа для слота баннера.

    Админ — доверенный, поэтому теги не вырезаем (партнёрские блоки часто
    требуют <script> или <iframe>), но размер ограничиваем и убираем
    нулевые байты. Пустая строка — нет HTML-баннера.
    """
    val = str(raw or "").strip()
    if not val:
        return ""
    # убираем нулевые байты и контролируем длину
    val = val.replace("\x00", "")[:HTML_MAX]
    return val.strip()


def normalize_banner(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Настройка баннера в виде, который можно отдавать странице.

    Значения проверяем здесь, а не в админке: рука может дать и ``rotate_sec``
    равный нулю — тогда баннер выжигал бы CPU перегонками, а страница с
    ``max: 999`` тянула бы весь архив объявлений.
    """
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    for key, default in BANNER_DEFAULTS.items():
        val = raw.get(key, default)
        if key in BANNER_CHOICES:
            choices = BANNER_CHOICES[key]
            out[key] = str(val) if str(val) in choices else default
            continue
        lo, hi = BANNER_LIMITS[key]
        try:
            num = int(round(float(val)))
        except (TypeError, ValueError):
            num = int(default)
        out[key] = max(lo, min(hi, num))
    return out


def banner_settings(store=None) -> Dict[str, Any]:
    """Как крутить баннер: из настроек админки, поверх дефолтов из кода."""
    store = store if store is not None else ctx.store
    raw: Dict[str, Any] = {}
    if store is not None:
        try:
            raw = json.loads(store.get_setting(BANNER_SETTING, "") or "{}")
        except Exception:                               # noqa: BLE001
            raw = {}
    return normalize_banner(raw)


def save_banner_settings(store, body: Dict[str, Any],
                         actor_id: Optional[int] = None) -> Dict[str, Any]:
    """Сохранить настройку баннера; на выходе — то, что реально применилось."""
    store = store if store is not None else ctx.store
    if store is None:
        return dict(BANNER_DEFAULTS)
    current: Dict[str, Any] = {}
    try:
        current = json.loads(store.get_setting(BANNER_SETTING, "") or "{}")
    except Exception:                                   # noqa: BLE001
        current = {}
    if not isinstance(current, dict):
        current = {}
    for key in BANNER_KEYS:
        if key in (body or {}):
            current[key] = (body or {}).get(key)
    applied = normalize_banner(current)
    store.set_setting(BANNER_SETTING, json.dumps(applied, ensure_ascii=False),
                      actor_id=actor_id)
    return applied


def _bot_ready(bot) -> bool:
    """Бот включён? У TelegramBot это свойство, у заглушек бывает метод."""
    if bot is None:
        return False
    val = getattr(bot, "enabled", False)
    try:
        return bool(val() if callable(val) else val)
    except Exception:                                   # noqa: BLE001
        return False


class Ctx:
    """Связка с сервером: база, бот и адрес сайта — заполняет server.py."""

    store = None
    bot = None
    public_url = ""
    site_url = ""


ctx = Ctx()


# ---------------------------------------------------------------------------
#  Текст: ссылки, разметка Telegram и подпись на сайте
# ---------------------------------------------------------------------------
def _split_links(text: str):
    """Режем текст на куски: обычный / ссылка / обычный …"""
    pos = 0
    for m in URL_RE.finditer(text or ""):
        if m.start() > pos:
            yield False, text[pos:m.start()]
        url = m.group(0).rstrip(".,;:!?»)")
        yield True, url
        pos = m.start() + len(url)
    if pos < len(text or ""):
        yield False, (text or "")[pos:]


def linkify_html(text: str) -> str:
    """Экранировать и превратить голые ссылки в кликабельные.

    Своя разметка не поддерживается намеренно: рекламу пишут с телефона, и
    случайный ``<`` в тексте не должен ломать сообщение (Telegram отвергает
    такое целиком), а ссылку всё равно хочется видеть ссылкой.
    """
    import html as _html

    out = []
    for is_url, chunk in _split_links(text or ""):
        if is_url:
            out.append(f'<a href="{_html.escape(chunk, quote=True)}">'
                       f"{_html.escape(chunk)}</a>")
        else:
            out.append(_html.escape(chunk))
    return "".join(out).strip()


def site_html(text: str) -> str:
    """То же для баннера: ссылки открываются в новой вкладке."""
    return (linkify_html(text)
            .replace("<a href=", '<a target="_blank" rel="noopener" href='))


def caption_len(text: str) -> int:
    """Длина подписи в знаках Telegram: эмодзи и редкие символы — по два."""
    from tg_bot import caption_len as _len
    return _len(text or "")


def text_problem(text: str, has_photo: bool, has_html: bool = False) -> str:
    """Почему текст не примут: пусто или длиннее лимита для этого случая.

    Если есть HTML-баннер, текст может быть пустым — баннер покажет HTML.
    """
    text = (text or "").strip()
    if not text:
        return "" if has_html else "empty"
    if caption_len(text) > (TEXT_MAX_PHOTO if has_photo else TEXT_MAX_PLAIN):
        return "too_long_photo" if has_photo else "too_long"
    return ""


# ---------------------------------------------------------------------------
#  Получатели
# ---------------------------------------------------------------------------
def clean_targets(raw: Any) -> Dict[str, Any]:
    """Что выбрал админ: бот, каналы и/или страницы сайта (главная, терминал,
    дайджест, сводка по часам, статьи)."""
    raw = raw if isinstance(raw, dict) else {}
    channels: List[str] = []
    for item in (raw.get("channels") or []):
        cid = str(item or "").strip()
        if cid and CHAT_RE.match(cid) and cid not in channels:
            channels.append(cid)
    return {"bot": bool(raw.get("bot")),
            "site": bool(raw.get("site")),
            "terminal": bool(raw.get("terminal")),
            "digest": bool(raw.get("digest")),
            "hourly": bool(raw.get("hourly")),
            "articles": bool(raw.get("articles")),
            "channels": channels[:20]}


def targets_text(targets: Dict[str, Any], channels: Optional[List[dict]] = None) -> str:
    """Человеческая строка «куда уйдёт» для панели."""
    names = {str(c.get("id")): (c.get("title") or c.get("id"))
             for c in (channels or [])}
    parts = []
    if (targets or {}).get("bot"):
        parts.append("бот")
    for cid in (targets or {}).get("channels") or []:
        parts.append(names.get(str(cid)) or f"канал {cid}")
    if (targets or {}).get("site"):
        parts.append("главная сайта")
    if (targets or {}).get("terminal"):
        parts.append("терминал")
    if (targets or {}).get("digest"):
        parts.append("дайджест")
    if (targets or {}).get("hourly"):
        parts.append("сводка по часам")
    if (targets or {}).get("articles"):
        parts.append("статьи")
    return ", ".join(parts) or "никуда"


def known_channels(bot=None) -> List[Dict[str, Any]]:
    """Каналы, которые знает бот: RU и EN из привязок, плюс пересланные."""
    bot = ctx.bot if bot is None else bot
    if bot is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        binds = bot._channel_bindings() or {}
        titles = binds.get("titles") or {}
        for code, label in (("ru", "🇷🇺 Русский канал"), ("en", "🇬🇧 Английский канал")):
            cid = str(binds.get(code) or "").strip()
            if not cid:
                continue
            out.append({"id": cid, "code": code, "label": label,
                        "title": str(titles.get(code) or "").strip() or label})
    except Exception as e:                              # noqa: BLE001
        log.debug("каналы для рекламы: %s", e)
    return out


def ad_public(ad: Dict[str, Any], with_text: bool = True) -> Dict[str, Any]:
    """Объявление для страницы: без получателей и служебного отчёта.

    ``link`` — куда ведёт клик по баннеру. Отдельно от текста он нужен, чтобы
    картинка была кликабельной целиком, а подпись оставалась подписью.
    ``banner_html`` / ``html_banner`` — произвольный HTML-код баннера, если
    админ выбрал HTML-тип вместо картинки.
    """
    raw_html = clean_html_banner(ad.get("html_banner") or ad.get("html_code") or ad.get("html") or "")
    # если html_banner уже отделён от текстового html, берём его
    if not raw_html:
        # пробуем колонки из _ad_row нормализации
        raw_html = clean_html_banner(ad.get("html_banner") or "")
    out = {
        "id": ad.get("id"),
        "sent_at": ad.get("sent_at") or 0,
        "expires_at": ad.get("expires_at") or 0,
        "has_photo": bool(ad.get("has_photo")),
        "photo": f"/api/ads/{ad.get('id')}/photo" if ad.get("has_photo") else "",
        "link": clean_link(ad.get("link")),
        "has_html": bool(raw_html),
        "banner_html": raw_html,
        "html_banner": raw_html,
    }
    if with_text:
        out["text"] = ad.get("text") or ""
        out["html"] = site_html(ad.get("text") or "")
    return out


def ad_admin(ad: Dict[str, Any], channels: Optional[List[dict]] = None) -> Dict[str, Any]:
    """Объявление для панели: со статусом, получателями и отчётом отправки."""
    out = dict(ad)
    out["link"] = clean_link(ad.get("link"))
    raw_html = clean_html_banner(ad.get("html_banner") or ad.get("html_code") or ad.get("html") or "")
    out["html_banner"] = raw_html
    out["banner_html"] = raw_html
    out["html_code"] = raw_html
    # для совместимости: поле html в админке — это HTML-баннер, а не linkify
    # но если нужен текст — он в out["text"]
    out["has_html"] = bool(raw_html)
    out["human_targets"] = targets_text(ad.get("targets") or {}, channels)
    out.pop("photo", None)          # путь на диске админке не нужен
    out.pop("photo_name", None)
    return out


# ---------------------------------------------------------------------------
#  Сервис: публикация и автоудаление
# ---------------------------------------------------------------------------
class AdService:
    """Публикует рекламу по расписанию и снимает её, когда вышел срок."""

    def __init__(self, store=None, bot=None, site_url: str = "") -> None:
        self.store = store if store is not None else ctx.store
        self.bot = bot if bot is not None else ctx.bot
        self.site_url = site_url or ctx.site_url or ctx.public_url
        self.busy = False
        self.last: Dict[str, Any] = {}
        self.last_tick = 0.0
        self.sent_total = 0
        self.expired_total = 0
        self.error = ""
        self.log: List[str] = []

    # --- журнал для админки ------------------------------------------------
    def note(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime())
        self.log.append(f"{stamp} {text}")
        self.log = self.log[-40:]

    def status(self) -> Dict[str, Any]:
        return {"busy": self.busy, "last_tick": self.last_tick,
                "sent_total": self.sent_total, "expired_total": self.expired_total,
                "error": self.error, "log": list(self.log[-12:]),
                "bot": bool(_bot_ready(self.bot))}

    # --- отправка ----------------------------------------------------------
    async def publish(self, ad: Dict[str, Any], actor: str = "") -> Dict[str, Any]:
        """Разослать объявление по выбранным источникам.

        Возвращает запись с отчётом: по каждому каналу — ушло ли сообщение и
        его id (позже по нему объявление удаляется), по боту — сколько дошло,
        по сайту — отметку, что баннер показывается.
        """
        store, bot = self.store, self.bot
        if store is None:
            return {"ok": False, "error": "no_store"}
        ad_id = int(ad.get("id") or 0)
        targets = clean_targets(ad.get("targets"))
        text = (ad.get("text") or "").strip()
        photo = ad.get("photo") or ""
        if photo and not os.path.isfile(photo):
            photo = ""
        html = linkify_html(text)
        results: Dict[str, Any] = {}
        if bot is None:
            self.error = "бот не подключён"
        # --- каналы ---
        for cid in targets["channels"]:
            res = {"ok": False, "mid": 0, "err": ""}
            if bot is None:
                res["err"] = "бот не подключён"
            else:
                mid = None
                if photo:
                    mid = await bot.send_photo(cid, photo, html, raw=True)
                if mid is None and not photo:
                    mid = await bot.send(cid, html, raw=True)
                res["ok"] = bool(mid)
                res["mid"] = int(mid or 0)
                if not mid:
                    res["err"] = str(getattr(bot, "_last_tg_err", "") or "не ушло")
            results[f"ch:{cid}"] = res
        # --- бот: личка всем, кто запускал ---
        if targets["bot"]:
            if bot is None:
                results["bot"] = {"ok": False, "total": 0, "ok_count": 0,
                                  "fail": 0, "err": "бот не подключён"}
            else:
                try:
                    stats = await bot.broadcast_post(html, photo, raw=True)
                    results["bot"] = {"ok": bool(stats.get("ok")),
                                      "total": stats.get("total", 0),
                                      "ok_count": stats.get("ok", 0),
                                      "fail": stats.get("fail", 0), "err": ""}
                except Exception as e:                  # noqa: BLE001
                    results["bot"] = {"ok": False, "total": 0, "ok_count": 0,
                                      "fail": 0, "err": f"{type(e).__name__}: {e}"[:120]}
        # --- сайт: баннеры на всех страницах (главная, терминал, дайджест, сводка,
        # статьи) ---
        if targets["site"]:
            results["site"] = {"ok": True, "err": ""}
        if targets["terminal"]:
            results["terminal"] = {"ok": True, "err": ""}
        if targets["digest"]:
            results["digest"] = {"ok": True, "err": ""}
        if targets["hourly"]:
            results["hourly"] = {"ok": True, "err": ""}
        if targets["articles"]:
            results["articles"] = {"ok": True, "err": ""}
        ok_any = any(bool((v or {}).get("ok")) for v in results.values())
        status = "sent" if ok_any else "failed"
        store.update_ad(ad_id, status=status, sent_at=time.time(), results=results,
                        send_at=ad.get("send_at") or time.time())
        # Если срок уже истёк (объявление ушло с опозданием) — сразу снимаем.
        rec = store.get_ad(ad_id) or ad
        if rec.get("expires_at") and float(rec["expires_at"]) <= time.time():
            await self.expire(rec, force=True)
            rec = store.get_ad(ad_id) or rec
        self.sent_total += 1 if ok_any else 0
        self.note(f"#{ad_id} → {targets_text(targets, known_channels(bot))}: "
                  + ("отправлено" if ok_any else "не ушло"))
        return rec

    async def expire(self, ad: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        """Снять объявление: убрать баннер и удалить посты из каналов."""
        store, bot = self.store, self.bot
        if store is None:
            return {"ok": False, "error": "no_store"}
        ad_id = int(ad.get("id") or 0)
        results = dict(ad.get("results") or {})
        removed = 0
        for key, val in list(results.items()):
            if not key.startswith("ch:") or not isinstance(val, dict):
                continue
            mid = int(val.get("mid") or 0)
            if not mid or not bot:
                continue
            try:
                cid = key[3:]
                if await bot.delete_message(int(cid) if cid.lstrip("-").isdigit() else cid, mid):
                    removed += 1
                    val["deleted"] = True
            except Exception as e:                      # noqa: BLE001
                log.debug("удаление поста %s: %s", key, e)
        store.update_ad(ad_id, status="expired", results=results)
        self.expired_total += 1
        self.note(f"#{ad_id} снят: баннеры убраны, постов удалено {removed}")
        return store.get_ad(ad_id) or ad

    # --- один тик планировщика --------------------------------------------
    async def tick(self) -> Dict[str, Any]:
        store = self.store
        self.last_tick = time.time()
        if store is None:
            return {"ok": False, "error": "no_store"}
        published: List[int] = []
        expired: List[int] = []
        for ad in store.due_ads():
            try:
                rec = await self.publish(ad, actor="scheduler")
                published.append(int(ad.get("id") or 0))
                log.info("реклама #%s: %s", ad.get("id"), (rec or {}).get("status"))
            except Exception as e:                      # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                log.warning("реклама #%s не ушла: %s", ad.get("id"), e)
                store.update_ad(int(ad.get("id") or 0), status="failed",
                                results={"scheduler": {"ok": False, "err": str(e)[:120]}})
        for ad in store.expired_ads():
            try:
                await self.expire(ad)
                expired.append(int(ad.get("id") or 0))
            except Exception as e:                      # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
        return {"ok": True, "published": published, "expired": expired}

    # --- отчёт о настройке ------------------------------------------------
    def publish_line(self, ad: Dict[str, Any]) -> str:
        """Строка «что вышло» для панели: кому ушло, кому нет."""
        results = ad.get("results") or {}
        if not results:
            return "ещё не отправлялось"
        bits = []
        for key, val in results.items():
            val = val if isinstance(val, dict) else {}
            if key == "site":
                bits.append("главная: баннер" + ("" if val.get("ok") else " не встал"))
            elif key == "terminal":
                bits.append("терминал: баннер" + ("" if val.get("ok") else " не встал"))
            elif key == "digest":
                bits.append("дайджест: баннер" + ("" if val.get("ok") else " не встал"))
            elif key == "hourly":
                bits.append("сводка: баннер" + ("" if val.get("ok") else " не встал"))
            elif key == "articles":
                bits.append("статьи: баннер" + ("" if val.get("ok") else " не встал"))
            elif key == "bot":
                if val.get("total"):
                    bits.append(f"бот: {val.get('ok_count', 0)}/{val.get('total')}")
                else:
                    bits.append("бот: " + (val.get("err") or "нет получателей"))
            elif key.startswith("ch:"):
                cid = key[3:]
                bits.append(f"{cid}: " + ("ушло" if val.get("ok") else
                                          (val.get("err") or "не ушло")))
        return "; ".join(bits)


async def scheduler_loop(svc: AdService, tick: float = 20.0) -> None:
    """Фоновый цикл: публикация по времени и автоудаление по сроку."""
    await asyncio.sleep(8.0)                    # не мешаем старту сервера
    while True:
        try:
            await svc.tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:                  # noqa: BLE001
            log.warning("планировщик рекламы: %s", e)
            svc.error = f"{type(e).__name__}: {e}"
        try:
            await asyncio.sleep(max(5.0, float(tick)))
        except asyncio.CancelledError:
            raise


# ---------------------------------------------------------------------------
#  HTTP
# ---------------------------------------------------------------------------
def _admin(request: Request):
    from web_account import current_user
    user = current_user(request)
    if not user:
        return None, JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not user.get("is_admin"):
        return None, JSONResponse({"ok": False, "error": "admin"}, status_code=403)
    return user, None


def parse_when(value: Any, now: float) -> float:
    """Время отправки: unix-секунды, ISO-строка или «через N минут»."""
    if value in (None, "", 0, "0"):
        return 0.0
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts / 1000.0 if ts > 10_000_000_000 else ts
    raw = str(value).strip()
    if raw.isdigit():
        return float(raw)
    try:                                        # ISO из <input type=datetime-local>
        from datetime import datetime
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        return ts
    except ValueError:
        return 0.0


def register_ad_routes(app, svc: AdService) -> None:
    router = APIRouter()

    # --- страница ----------------------------------------------------------
    @router.get("/api/ads")
    async def api_ads(request: Request):
        """Активные объявления для баннера (публично) и как их крутить.

        ``?place=landing`` — главная, ``?place=terminal`` — терминал под
        графиком, ``?place=digest`` — дайджест, ``?place=hourly`` — сводка по
        часам, ``?place=articles`` — раздел статей. Вместе со списком отдаём ``banner`` — настройку смены:
        интервал, плавность, режим и как вписать фотографию. Страница не
        хранит эти числа у себя, поэтому админ меняет их и видит результат
        на следующем показе, без перезапуска сервера.
        """
        store = svc.store
        place = str(request.query_params.get("place") or "landing").strip().lower()
        if place not in ("landing", "terminal", "digest", "hourly", "articles"):
            place = "landing"
        cfg = banner_settings(store)
        if store is None:
            return {"ok": True, "items": [], "place": place,
                    "banner": cfg, "now": time.time()}
        items = [ad_public(a) for a in
                 store.active_site_ads(limit=cfg["max"], place=place)]
        return {"ok": True, "items": items, "place": place, "banner": cfg,
                "now": time.time(), "tz_hours": round(tz_offset() / 3600.0, 2)}

    @router.get("/api/ads/{ad_id}/photo")
    async def api_ad_photo(request: Request, ad_id: int):
        """Фото объявления. Публично — только пока объявление показывается."""
        store = svc.store
        ad = store.get_ad(ad_id) if store else None
        if not ad:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        user, _err = _admin(request)
        # «живо» = показывается на любой из страниц: фото просят и баннер
        # главной, и баннер терминала
        active = int(ad_id) in {int(a.get("id") or 0)
                                for a in (store.active_site_ads(limit=10) if store else [])}
        if not active and not (user or {}).get("is_admin"):
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        path = ad.get("photo") or ""
        if not path or not os.path.isfile(path):
            return JSONResponse({"ok": False, "error": "no_photo"}, status_code=404)
        return FileResponse(path)

    # --- админка -----------------------------------------------------------
    @router.get("/api/admin/ads")
    async def api_admin_ads(request: Request):
        user, err = _admin(request)
        if err:
            return err
        store = svc.store
        if store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        channels = known_channels(svc.bot)
        items = [ad_admin(a, channels) for a in store.list_ads(AD_KEEP_LIMIT)]
        for it in items:
            it["line"] = svc.publish_line(it)
        return {"ok": True, "items": items, "channels": channels,
                "banner": banner_settings(store),
                "banner_keys": list(BANNER_KEYS),
                "banner_choices": {k: list(v) for k, v in BANNER_CHOICES.items()},
                "banner_limits": {k: list(v) for k, v in BANNER_LIMITS.items()},
                "bot": bool(svc.bot is not None),
                "bot_ready": _bot_ready(svc.bot),
                "limits": {"plain": TEXT_MAX_PLAIN, "photo": TEXT_MAX_PHOTO,
                           "caption": CAPTION_LIMIT},
                "site": (svc.site_url or ctx.public_url) + "/",
                "now": time.time(),
                "tz_hours": round(tz_offset() / 3600.0, 2),
                "service": svc.status()}

    @router.post("/api/admin/ads/banner")
    async def api_admin_banner(request: Request,
                               body: Optional[dict] = Body(default=None)):
        """Как показывать баннер: интервал смены, плавность, режим, подписи."""
        user, err = _admin(request)
        if err:
            return err
        applied = save_banner_settings(svc.store, body or {},
                                       actor_id=(user or {}).get("id"))
        svc.note("баннер: " + ", ".join(f"{k}={v}" for k, v in applied.items()))
        return {"ok": True, "banner": applied}

    def _photo_blob(request: Request, body: Dict[str, Any]) -> Any:
        """Фото из тела запроса: data-URL (JSON) — так шлёт панель."""
        from web_upload import PHOTO_ERR, decode_json_photo
        if not body.get("photo"):
            return None, None
        blob, filename = decode_json_photo({"data": body.get("photo"),
                                            "filename": body.get("photo_name") or ""})
        if not blob:
            return None, JSONResponse(
                {"ok": False, "error": "bad_data", "hint": PHOTO_ERR["bad_data"]},
                status_code=400)
        return (blob, filename or "ad.jpg"), None

    @router.post("/api/admin/ads")
    async def api_admin_ad_create(request: Request,
                                  body: Optional[dict] = Body(default=None)):
        """Сохранить объявление: черновик, «сейчас» или отложенная отправка."""
        user, err = _admin(request)
        if err:
            return err
        store = svc.store
        if store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = body or {}
        blob_pair, photo_err = _photo_blob(request, body)
        if photo_err:
            return photo_err
        blob = (blob_pair or (None, None))[0] or b""
        targets = clean_targets(body.get("targets"))
        text = str(body.get("text") or "").strip()
        link = clean_link(body.get("link"))
        html_raw = clean_html_banner(body.get("html") or body.get("html_banner")
                                     or body.get("banner_html") or body.get("html_code") or "")
        now = time.time()
        draft = bool(body.get("draft"))
        when = parse_when(body.get("send_at"), now)
        if not draft and not (targets["bot"] or targets["site"]
                              or targets["terminal"] or targets["digest"]
                              or targets["hourly"] or targets["articles"]
                              or targets["channels"]):
            return JSONResponse({"ok": False, "error": "no_targets",
                                 "hint": "Выберите хотя бы один источник: бот, канал или главную."},
                                status_code=400)
        # На сайте баннер из одной картинки или HTML-кода законен: подпись —
        # украшение, а не обязательное поле (в каналы же без текста идти нечему,
        # там проверка остаётся). Фотографию сохраняем после создания записи —
        # ей нужен id, поэтому «будет фото» передаём в add_ad отдельным признаком.
        site_only = bool(targets["site"] or targets["terminal"]
                           or targets["digest"] or targets["hourly"])
        has_html = bool(html_raw)
        if not draft:
            if not when or when <= now + 1:
                when = now                    # «отправить сейчас»
            if (not site_only or (not blob and not has_html)) and text_problem(text, bool(blob), has_html):
                code = text_problem(text, bool(blob), has_html)
                if code == "empty" and not blob and not has_html:
                    return JSONResponse({"ok": False, "error": "empty",
                                         "hint": "Пустой пост: нужен текст, фотография или HTML-баннер."},
                                        status_code=400)
                if code:
                    limit = TEXT_MAX_PHOTO if blob else TEXT_MAX_PLAIN
                    return JSONResponse(
                        {"ok": False, "error": code,
                         "hint": f"Текст длиннее {limit} знаков — Telegram не примет. "
                                 "Сократите или снимите фотографию."},
                        status_code=400)
            # HTML-баннер только для сайта: если выбран HTML, а получатели — только бот/каналы, предупреждаем
            if has_html and not site_only and (targets["bot"] or targets["channels"]):
                # разрешаем, но текст для Telegram всё равно нужен, если его нет — уже проверили выше
                pass
        else:
            when = when or 0.0
        ttl_min = int(body.get("ttl_min") or 0)
        expires = parse_when(body.get("expires_at"), now)
        if not expires and ttl_min > 0:
            expires = (when or now) + ttl_min * 60.0
        # «сейчас» тоже идёт через расписание: publish ждёт прямо в этом запросе,
        # а метка статуса одна — «к отправке».
        status = "draft" if draft else "scheduled"
        created = store.add_ad(text, targets=targets, send_at=when,
                               expires_at=expires, status=status,
                               author_id=user.get("id"), link=link,
                               photo_pending=bool(blob) or has_html,
                               html=html_raw)
        if not created.get("ok"):
            return JSONResponse({**created, "hint": "Нужен текст или фотография."},
                                status_code=400)
        ad_id = int(created["id"])
        if blob:
            up = store.save_ad_photo(ad_id, blob, filename=(blob_pair[1] if blob_pair else ""),
                                     actor_id=user.get("id"))
            if not up.get("ok"):
                from web_upload import PHOTO_ERR
                return JSONResponse({"ok": False, "error": up.get("error"),
                                     "id": ad_id,
                                     "hint": PHOTO_ERR.get(str(up.get("error")), "фото не загрузилось")},
                                    status_code=400)
        ad = store.get_ad(ad_id) or {}
        # «Сейчас» отправляем не дожидаясь планировщика: админ ждёт результат в панели.
        if not draft and float(ad.get("send_at") or 0) <= time.time() + 1:
            ad = await svc.publish(ad, actor=f"admin:{user.get('id')}") or ad
        channels = known_channels(svc.bot)
        out = ad_admin(ad, channels)
        out["line"] = svc.publish_line(ad)
        return {"ok": True, "item": out, "channels": channels}

    @router.post("/api/admin/ads/{ad_id}")
    async def api_admin_ad_update(request: Request, ad_id: int,
                                  body: Optional[dict] = Body(default=None)):
        """Правка: текст, получатели, время отправки, срок жизни, новое фото."""
        user, err = _admin(request)
        if err:
            return err
        store = svc.store
        ad = store.get_ad(ad_id) if store else None
        if not ad:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        if ad.get("status") == "sent" and (body or {}).get("send_at") not in (None, ""):
            return JSONResponse({"ok": False, "error": "already_sent",
                                 "hint": "Пост уже отправлен — отредактировать можно "
                                         "только срок показа."}, status_code=409)
        body = body or {}
        blob_pair, photo_err = _photo_blob(request, body)
        if photo_err:
            return photo_err
        fields: Dict[str, Any] = {}
        now = time.time()
        if "text" in body:
            fields["text"] = str(body.get("text") or "").strip()[:TEXT_MAX_PLAIN]
        if "targets" in body:
            fields["targets"] = clean_targets(body.get("targets"))
        if "link" in body:
            fields["link"] = clean_link(body.get("link"))
        if any(k in body for k in ("html", "html_banner", "banner_html", "html_code")):
            raw = body.get("html")
            if raw is None:
                raw = body.get("html_banner") or body.get("banner_html") or body.get("html_code")
            html_clean = clean_html_banner(raw)
            fields["html"] = html_clean
            fields["html_code"] = html_clean
        if "send_at" in body:
            when = parse_when(body.get("send_at"), now)
            fields["send_at"] = when
            if ad.get("status") in ("draft", "scheduled"):
                fields["status"] = "scheduled"
        if "ttl_min" in body:
            ttl_min = int(body.get("ttl_min") or 0)
            base = float(fields.get("send_at") or ad.get("send_at") or now)
            fields["expires_at"] = (base + ttl_min * 60.0) if ttl_min > 0 else 0.0
        if "expires_at" in body and "ttl_min" not in body:
            fields["expires_at"] = parse_when(body.get("expires_at"), now)
        # очистка HTML-баннера по флагу
        if body.get("clear_html"):
            fields["html"] = ""
            fields["html_code"] = ""
        store.update_ad(ad_id, **fields)
        if blob_pair:
            up = store.save_ad_photo(ad_id, blob_pair[0], filename=blob_pair[1],
                                     actor_id=user.get("id"))
            if not up.get("ok"):
                from web_upload import PHOTO_ERR
                return JSONResponse({"ok": False, "error": up.get("error"),
                                     "hint": PHOTO_ERR.get(str(up.get("error")), "фото не загрузилось")},
                                    status_code=400)
        elif body.get("clear_photo"):
            store.clear_ad_photo(ad_id, actor_id=user.get("id"))
        ad = store.get_ad(ad_id) or ad
        channels = known_channels(svc.bot)
        out = ad_admin(ad, channels)
        out["line"] = svc.publish_line(ad)
        return {"ok": True, "item": out}

    @router.post("/api/admin/ads/{ad_id}/send")
    async def api_admin_ad_send(request: Request, ad_id: int):
        """Отправить немедленно, не дожидаясь выбранного времени."""
        user, err = _admin(request)
        if err:
            return err
        store = svc.store
        ad = store.get_ad(ad_id) if store else None
        if not ad:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        if ad.get("status") == "sent":
            return JSONResponse({"ok": False, "error": "already_sent",
                                 "hint": "Этот пост уже отправлен."}, status_code=409)
        targets = clean_targets(ad.get("targets"))
        if not (targets["bot"] or targets["site"] or targets["terminal"]
                or targets["digest"] or targets["hourly"]
                or targets["channels"]):
            return JSONResponse({"ok": False, "error": "no_targets",
                                 "hint": "Выберите хотя бы один источник."}, status_code=400)
        if ad.get("photo") and text_problem(ad.get("text"), True) == "too_long_photo":
            return JSONResponse({"ok": False, "error": "too_long_photo",
                                 "hint": "С фото текст длиннее 1024 знаков — Telegram его не примет."},
                                status_code=400)
        store.update_ad(ad_id, send_at=time.time(), status="scheduled")
        ad = store.get_ad(ad_id) or ad
        ad = await svc.publish(ad, actor=f"admin:{user.get('id')}") or ad
        channels = known_channels(svc.bot)
        out = ad_admin(ad, channels)
        out["line"] = svc.publish_line(ad)
        return {"ok": True, "item": out, "published": ad.get("status") == "sent"}

    @router.post("/api/admin/ads/{ad_id}/stop")
    async def api_admin_ad_stop(request: Request, ad_id: int):
        """Снять объявление досрочно: баннер убрать, посты в каналах удалить."""
        user, err = _admin(request)
        if err:
            return err
        store = svc.store
        ad = store.get_ad(ad_id) if store else None
        if not ad:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        if ad.get("status") == "sent":
            ad = await svc.expire(ad)
        else:
            store.update_ad(ad_id, status="cancelled")
            ad = store.get_ad(ad_id) or ad
        out = ad_admin(ad, known_channels(svc.bot))
        out["line"] = svc.publish_line(ad)
        return {"ok": True, "item": out}

    @router.post("/api/admin/ads/{ad_id}/delete")
    async def api_admin_ad_delete(request: Request, ad_id: int):
        """Убрать запись совсем: сначала снимаем показ, потом стираем."""
        user, err = _admin(request)
        if err:
            return err
        store = svc.store
        ad = store.get_ad(ad_id) if store else None
        if not ad:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        if ad.get("status") == "sent":
            try:
                await svc.expire(ad)
            except Exception as e:                      # noqa: BLE001
                log.debug("снятие перед удалением: %s", e)
        store.delete_ad(ad_id, actor_id=user.get("id"))
        return {"ok": True, "id": int(ad_id)}

    app.include_router(router)
