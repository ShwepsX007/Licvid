"""Telegram-бот LiqScope: регистрация, кабинет, статистика, админка.

Ходит в Bot API через aiohttp (уже в зависимостях). Без токена — просто не стартует.
Команды пользователя зеркалят кабинет на сайте; админские — панель управления.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp

log = logging.getLogger("liqscope.bot")

API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_PUBLIC_URL = "https://liqscope.online"


def normalize_public_url(url: str = "") -> str:
    """Канонический сайт. Пустое значение, IP и :8000 → https://liqscope.online."""
    s = (url or "").strip().rstrip("/")
    if not s:
        return DEFAULT_PUBLIC_URL
    rest = s.split("://", 1)[-1].lower()
    host = rest.split("/")[0].split(":")[0]
    if host in ("liqscope.online", "www.liqscope.online", "78.17.66.215"):
        return DEFAULT_PUBLIC_URL
    return s


def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=False)


class TelegramBot:
    def __init__(
        self,
        token: str,
        store,
        public_url: str = "",
        health_fn: Optional[Callable[[], dict]] = None,
        stats_fn: Optional[Callable[[], dict]] = None,
        liqs_fn: Optional[Callable[[], list]] = None,
        ws_clients_fn: Optional[Callable[[], int]] = None,
        channel_url: str = "",
        channel_id: str = "",
        digest_fn: Optional[Callable[[], Any]] = None,
    ):
        self.token = (token or "").strip()
        self.store = store
        self.public_url = normalize_public_url(public_url)
        self.health_fn = health_fn or (lambda: {})
        self.stats_fn = stats_fn or (lambda: {})
        self.liqs_fn = liqs_fn or (lambda: [])
        self.ws_clients_fn = ws_clients_fn or (lambda: 0)
        self.channel_url = (channel_url or os.getenv("LIQSCOPE_CHANNEL_URL")
                            or "https://t.me/+4S1LsZtH1Pc5YWZi").strip()
        self._channel_id_cfg = (channel_id or os.getenv("LIQSCOPE_CHANNEL_ID")
                                or "").strip()
        self.digest_fn = digest_fn or (lambda: {})
        self._digest_task: Optional[asyncio.Task] = None
        self._ch_ok: Dict[int, float] = {}   # tg_id -> cache until
        self._digest_err = ""
        self._last_tg_err = ""
        self.username = ""
        self.bot_id = 0
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._wait_broadcast: Dict[int, bool] = {}  # tg_id -> waiting for text
        self._wait_tpl: Dict[int, str] = {}         # tg_id -> "head"|"photo"
        self._wait_alert: Dict[int, str] = {}       # tg_id -> "coin"|"win"|"thr:liq"|...
        self._menu_msg: Dict[int, int] = {}         # chat_id -> последнее меню
        self.alerts_market_fn: Optional[Callable[[], Any]] = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def deep_link(self, payload: str) -> str:
        if not self.username:
            return ""
        return f"https://t.me/{self.username}?start={payload}"

    def site_url(self, path: str = "") -> str:
        base = normalize_public_url(self.public_url)
        path = (path or "").strip()
        if not path:
            return base
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    def terminal_url(self) -> str:
        return self.site_url("/terminal")

    def cabinet_url(self) -> str:
        return self.site_url("/cabinet")

    def admin_url(self) -> str:
        return self.site_url("/admin")

    def site_link_kb(self, label: str = "посмотреть в терминале",
                     path: str = "/terminal") -> dict:
        return {"inline_keyboard": [[{"text": label, "url": self.site_url(path)}]]}

    def bot_url(self) -> str:
        name = (self.username or os.getenv("LIQSCOPE_BOT_USERNAME") or "LiqScopeBot")
        return f"https://t.me/{str(name).lstrip('@')}"

    def channel_link_kb(self) -> dict:
        return {"inline_keyboard": [[
            {"text": "liqscope", "url": self.site_url()},
            {"text": "бот", "url": self.bot_url()},
        ]]}

    def _reply_kb(self, user: Optional[dict] = None) -> dict:
        rows = [
            [{"text": "👤 Кабинет"}, {"text": "⚡ Терминал"}],
            [{"text": "📊 Статистика"}, {"text": "🩺 Биржи"}],
            [{"text": "🛠 Сервисы"}, {"text": "📰 Лента"}],
            [{"text": "🔔 Алерты"}, {"text": "📣 Канал"}],
        ]
        if user and user.get("is_admin"):
            rows.append([{"text": "★ Админка"}])
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "is_persistent": True,
            "input_field_placeholder": "меню внизу экрана",
        }

    def _reply_cmd(self, text: str) -> str:
        key = " ".join((text or "").strip().lower().split())
        key = key.replace("★ ", "").replace("👤 ", "").replace("⚡ ", "")
        key = key.replace("📊 ", "").replace("🩺 ", "").replace("🛠 ", "")
        key = key.replace("📰 ", "").replace("🔔 ", "").replace("📣 ", "")
        return {
            "кабинет": "cabinet",
            "терминал": "terminal",
            "статистика": "stats",
            "биржи": "health",
            "сервисы": "services",
            "лента": "liq",
            "лента liq": "liq",
            "алерты": "al",
            "алерты по объёму": "al",
            "канал": "channel",
            "админка": "admin",
        }.get(key, "")

    async def start(self) -> None:
        if not self.token:
            log.warning("LIQSCOPE_BOT_TOKEN не задан — Telegram-бот выключен")
            return
        # getUpdates живёт до 50 с, остальные методы — короткие.
        # total на сессии не ставим: иначе длинный long-poll съедает лимит
        # и следующий edit/answer может оборваться.
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=70),
            connector=aiohttp.TCPConnector(limit=8),
        )
        me = await self._call("getMe")
        if not me or not me.get("ok"):
            log.error("Не удалось получить бота getMe: %s", me)
            return
        self.username = me["result"]["username"]
        self.bot_id = int(me["result"]["id"])
        await self._call("deleteWebhook", {"drop_pending_updates": False})
        self.running = True
        self._task = asyncio.create_task(self._poll(), name="tg-bot")
        self._digest_task = asyncio.create_task(self._channel_loop(), name="tg-channel")
        log.info("Telegram-бот @%s запущен", self.username)

    async def stop(self) -> None:
        self.running = False
        for t in (self._task, self._digest_task):
            if not t:
                continue
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._digest_task = None
        if self._session:
            await self._session.close()
            self._session = None

    async def _call(self, method: str, payload: Optional[dict] = None) -> Optional[dict]:
        if not self._session:
            return None
        url = API.format(token=self.token, method=method)
        timeout = aiohttp.ClientTimeout(total=70, sock_read=70) if method == "getUpdates" \
            else aiohttp.ClientTimeout(total=15)
        try:
            async with self._session.post(url, json=payload or {}, timeout=timeout) as r:
                data = await r.json(content_type=None)
            if method != "getUpdates" and data and not data.get("ok"):
                desc = str(data.get("description") or "")
                # «message is not modified» — повторный клик по той же кнопке,
                # для Telegram это не ошибка.
                if "not modified" not in desc.lower():
                    log.warning("tg %s: %s", method, desc)
            return data
        except Exception as e:
            log.warning("tg %s: %s", method, e)
            return None

    @staticmethod
    def _inline_markup(markup: Optional[dict]) -> dict:
        """editMessageText принимает только inline. Пустой список снимает кнопки под текстом."""
        rows = (markup or {}).get("inline_keyboard")
        return {"inline_keyboard": rows if rows else []}

    async def send(self, chat_id: int, text: str, markup: Optional[dict] = None,
                   parse: str = "HTML", silent: bool = False) -> Optional[int]:
        body: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:3900],
            "parse_mode": parse,
            "disable_web_page_preview": True,
        }
        if silent:
            body["disable_notification"] = True
        if markup:
            body["reply_markup"] = markup
        res = await self._call("sendMessage", body)
        if not res or not res.get("ok"):
            desc = str((res or {}).get("description") or "нет ответа")
            self._last_tg_err = desc
            if parse and ("parse entit" in desc.lower() or "can't find end of the entity" in desc.lower()):
                body.pop("parse_mode", None)
                res = await self._call("sendMessage", body)
                if res and res.get("ok"):
                    mid = (res.get("result") or {}).get("message_id")
                    try:
                        return int(mid) if mid is not None else None
                    except (TypeError, ValueError):
                        return None
            return None
        mid = (res.get("result") or {}).get("message_id")
        try:
            return int(mid) if mid is not None else None
        except (TypeError, ValueError):
            return None

    async def edit(self, chat_id: int, message_id: int, text: str,
                   markup: Optional[dict] = None, parse: str = "HTML") -> bool:
        """Меняет то же сообщение (текст + кнопки), не плодит новые."""
        body: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:3900],
            "parse_mode": parse,
            "disable_web_page_preview": True,
        }
        if markup is not None:
            body["reply_markup"] = markup
        res = await self._call("editMessageText", body)
        if res and res.get("ok"):
            return True
        desc = str((res or {}).get("description") or "").lower()
        # тот же текст+клавиатура: клиент иногда не перерисовывает кнопки.
        # Невидимый символ заставляет Telegram принять правку и обновить UI.
        if "not modified" in desc:
            t = body.get("text") or ""
            body["text"] = t[:-1] if t.endswith("\u200b") else (t + "\u200b")
            res2 = await self._call("editMessageText", body)
            return bool(res2 and res2.get("ok")) or True
        if "parse entit" in desc or "can't find end of the entity" in desc:
            body.pop("parse_mode", None)
            res = await self._call("editMessageText", body)
            if res and res.get("ok"):
                return True
        return False

    async def drop_menu(self, chat_id: int, message_id: Optional[int]) -> None:
        if not chat_id or not message_id:
            return
        await self._call("deleteMessage", {
            "chat_id": chat_id, "message_id": int(message_id),
        })

    async def show_menu(self, chat_id: int, text: str, markup: Optional[dict] = None,
                        old_id: Optional[int] = None) -> bool:
        """Тот же экран: правим сообщение на месте, без пуша и удаления.

        ReplyKeyboard живёт у чата и не требует нового send. Инлайн под текстом
        обновляется через editMessageText. Новое сообщение — только если править
        нечего (первый /start или бот перезапустился); тогда без звука.
        """
        chat_id = int(chat_id)
        inline = self._inline_markup(markup)
        mid = old_id or self._menu_msg.get(chat_id)
        if mid:
            if await self.edit(chat_id, int(mid), text, inline):
                self._menu_msg[chat_id] = int(mid)
                return True
        new_id = await self.send(chat_id, text, markup, silent=True)
        if not new_id:
            return False
        self._menu_msg[chat_id] = int(new_id)
        return True

    async def reply(self, chat_id: int, text: str, markup: Optional[dict] = None,
                    message_id: Optional[int] = None) -> bool:
        return await self.show_menu(chat_id, text, markup, old_id=message_id)

    def channel_chat_id(self) -> str:
        return (self._channel_id_cfg
                or (self.store.get_setting("channel_id", "") if self.store else "")
                or "").strip()

    def _join_text(self, extra: str = "") -> str:
        url = _esc(self.channel_url)
        more = f"\n\n{extra}" if extra else ""
        return (
            "<b>Сначала канал</b>\n"
            "Чтобы пользоваться ботом LiqScope, подпишитесь на канал со сводками "
            "ликвидаций, OI и CVD.\n\n"
            "Раз в 4 часа туда уходит разбор рынка: кто кого вынес и на каких биржах.\n"
            f"Канал: {url}{more}"
        )

    def _kb_join(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "📣 Подписаться", "url": self.channel_url}],
            [{"text": "✅ Я подписался", "callback_data": "ch:check"}],
        ]}

    async def _is_member(self, tg_id: int) -> bool:
        cid = self.channel_chat_id()
        if not cid or not tg_id:
            return False
        until = self._ch_ok.get(int(tg_id), 0)
        if until > time.time():
            return True
        res = await self._call("getChatMember", {"chat_id": cid, "user_id": int(tg_id)})
        status = str(((res or {}).get("result") or {}).get("status") or "").lower()
        ok = status in ("creator", "administrator", "member", "restricted")
        if ok:
            self._ch_ok[int(tg_id)] = time.time() + 180
        else:
            self._ch_ok.pop(int(tg_id), None)
        return ok

    async def _ensure_channel(self, chat_id: int, user: dict,
                              message_id: Optional[int] = None,
                              extra: str = "") -> bool:
        """True — можно показывать меню. Без id канала не блокируем (нечего проверить)."""
        if not self.channel_chat_id():
            return True
        if await self._is_member(int(user.get("tg_id") or 0)):
            return True
        await self.show_menu(chat_id, self._join_text(extra), self._kb_join(),
                             old_id=message_id)
        return False

    def _digest_result_text(self, ok: bool) -> str:
        if ok:
            cid = self.channel_chat_id() or "—"
            return f"Сводка ушла в канал.\n<code>{_esc(cid)}</code>"
        err = _esc(self._digest_err or "неизвестная ошибка")
        return (
            "<b>Не удалось отправить сводку</b>\n"
            f"{err}\n\n"
            "Если бот уже админ — перешлите сюда любой пост из канала, "
            "затем снова /digest."
        )

    async def _bind_channel_from_message(self, msg: dict, chat_id: int) -> bool:
        """Админ переслал пост из канала → запоминаем id."""
        chat = self._extract_forward_chat(msg)
        if not chat:
            return False
        cid = self.remember_channel(chat.get("id"), chat.get("title") or "")
        title = _esc(chat.get("title") or cid)
        await self.show_menu(
            chat_id,
            f"Канал привязан: <b>{title}</b>\n<code>{_esc(cid)}</code>\n\n"
            "Теперь /digest отправит сводку туда. "
            "У бота должно быть право «Публикация сообщений».",
            self._admin_kb(),
        )
        return True

    def remember_channel(self, chat_id, title: str = "") -> str:
        cid = str(chat_id).strip()
        if not cid:
            return ""
        self.store.set_setting("channel_id", cid)
        if title:
            self.store.set_setting("channel_title", str(title)[:80])
        log.info("канал привязан chat_id=%s title=%s", cid, title)
        return cid

    def _extract_forward_chat(self, msg: dict) -> Optional[dict]:
        origin = msg.get("forward_origin") if isinstance(msg.get("forward_origin"), dict) else {}
        chat = origin.get("chat") if origin.get("type") in ("channel", "chat") else None
        if not isinstance(chat, dict):
            chat = msg.get("forward_from_chat")
        if not isinstance(chat, dict):
            return None
        if chat.get("type") not in ("channel", "supergroup"):
            return None
        if chat.get("id") in (None, "", 0):
            return None
        return chat

    async def _on_my_chat_member(self, ev: dict) -> None:
        chat = ev.get("chat") or {}
        if chat.get("type") not in ("channel", "supergroup"):
            return
        new = ev.get("new_chat_member") or {}
        st = str(new.get("status") or "")
        cid = chat.get("id")
        if cid and st in ("administrator", "creator"):
            self.remember_channel(cid, chat.get("title") or "")
        elif cid and st in ("left", "kicked"):
            log.warning("бота убрали из канала chat_id=%s", cid)

    async def send_photo(self, chat_id, path: str, caption: str = "",
                         markup: Optional[dict] = None) -> Optional[int]:
        if not self._session or not path or not os.path.isfile(path):
            return None
        url = API.format(token=self.token, method="sendPhoto")
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption[:1024])
            form.add_field("parse_mode", "HTML")
        if markup:
            form.add_field("reply_markup", json.dumps(markup, ensure_ascii=False))
        try:
            data = open(path, "rb").read()
        except OSError as e:
            log.warning("photo read %s: %s", path, e)
            return None
        name = os.path.basename(path)
        ctype = "image/png" if name.lower().endswith(".png") else "image/jpeg"
        form.add_field("photo", data, filename=name, content_type=ctype)
        try:
            async with self._session.post(
                    url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as r:
                res = await r.json(content_type=None)
        except Exception as e:
            log.warning("tg sendPhoto: %s", e)
            return None
        if not res or not res.get("ok"):
            desc = str((res or {}).get("description") or "нет ответа")
            self._last_tg_err = desc
            log.warning("tg sendPhoto: %s", desc)
            return None
        mid = (res.get("result") or {}).get("message_id")
        try:
            return int(mid) if mid is not None else None
        except (TypeError, ValueError):
            return None

    async def _channel_loop(self) -> None:
        from channel_digest import WINDOW_SEC
        # первый пост не сразу: пусть фиды прогреются; дальше — каждые 4 часа
        first = 90.0
        last = 0.0
        try:
            last = float(self.store.get_setting("channel_digest_ts") or 0)
        except (TypeError, ValueError):
            last = 0.0
        if last > 0:
            first = max(30.0, WINDOW_SEC - (time.time() - last))
        try:
            await asyncio.sleep(first)
        except asyncio.CancelledError:
            return
        while self.running:
            try:
                await self.post_channel_digest()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("channel digest: %s", e)
            try:
                await asyncio.sleep(WINDOW_SEC)
            except asyncio.CancelledError:
                break

    def _digest_fail(self, reason: str) -> bool:
        self._digest_err = reason
        log.warning("сводка: %s", reason)
        return False

    async def post_channel_digest(self, force: bool = False) -> bool:
        from channel_digest import (
            active_headlines, active_images, pick_image, render_post,
        )
        self._digest_err = ""
        self._last_tg_err = ""
        cid = self.channel_chat_id()
        if not cid:
            return self._digest_fail(
                "Бот ещё не знает id канала (инвайт-ссылки недостаточно). "
                "Перешлите сюда любой пост из канала — так он запомнит адрес. "
                "В правах админа включите «Публикация сообщений».")
        raw = self.digest_fn() if self.digest_fn else {}
        if asyncio.iscoroutine(raw):
            raw = await raw
        snap = raw if isinstance(raw, dict) else {}
        try:
            n = int(self.store.get_setting("channel_digest_n") or 0)
        except (TypeError, ValueError):
            n = 0
        try:
            hours = int(snap.get("window_h") or 4)
        except (TypeError, ValueError):
            hours = 4
        caption = render_post(
            snap, n,
            headlines=active_headlines(self.store, hours),
            site_url=self.site_url(),
            bot_url=self.bot_url(),
        )
        img = pick_image(n, images=active_images(self.store))
        markup = self.channel_link_kb()
        # одно сообщение: фото с подписью, иначе только текст. Никогда фото+текст.
        ok = False
        if img and len(caption) <= 1024:
            ok = bool(await self.send_photo(cid, img, caption, markup))
        if not ok:
            ok = bool(await self.send(cid, caption, markup))
        if ok:
            self.store.set_setting("channel_digest_n", str(n + 1))
            self.store.set_setting("channel_digest_ts", str(int(time.time())))
            log.info("сводка в канал n=%s chat=%s", n, cid)
            return True
        err = getattr(self, "_last_tg_err", "") or "Telegram отклонил пост"
        low = err.lower()
        extra = ""
        if "chat not found" in low:
            extra = " Перешлите боту любой пост из канала, чтобы привязать id."
        elif "not enough right" in low or "need administrator" in low or "have no rights" in low:
            extra = (" В канале у бота должно быть право «Публикация сообщений» "
                     "(и «Прикрепление файлов», если шлём картинку).")
        return self._digest_fail(err + extra)

    async def answer_cb(self, cb_id: str, text: str = "") -> None:
        # Пустой text Telegram иногда отвергает — тогда клиент «залипает»
        # и больше не шлёт нажатия.
        body: Dict[str, Any] = {"callback_query_id": cb_id}
        if text:
            body["text"] = text[:180]
        await self._call("answerCallbackQuery", body)

    async def broadcast(self, text: str, actor_id: Optional[int] = None) -> Dict[str, int]:
        ids = self.store.tg_ids_for_broadcast()
        ok = fail = 0
        for tg_id in ids:
            if await self.send(tg_id, text, parse="HTML"):
                ok += 1
            else:
                fail += 1
            await asyncio.sleep(0.04)  # ~25 msg/s
        self.store.audit(actor_id, "broadcast", f"ok={ok} fail={fail}")
        return {"ok": ok, "fail": fail, "total": len(ids)}

    async def _poll(self) -> None:
        while self.running:
            try:
                res = await self._call("getUpdates", {
                    "offset": self._offset,
                    "timeout": 50,
                    "allowed_updates": ["message", "callback_query", "my_chat_member"],
                })
                if not res or not res.get("ok"):
                    if res and res.get("description"):
                        log.warning("getUpdates: %s", res.get("description"))
                    await asyncio.sleep(2)
                    continue
                for upd in res.get("result") or []:
                    self._offset = int(upd["update_id"]) + 1
                    try:
                        await self._on_update(upd)
                    except Exception as e:
                        log.warning("update %s: %s", upd.get("update_id"), e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("poll: %s", e)
                await asyncio.sleep(3)

    async def _on_update(self, upd: dict) -> None:
        if "my_chat_member" in upd:
            await self._on_my_chat_member(upd["my_chat_member"])
            return
        if "callback_query" in upd:
            await self._on_callback(upd["callback_query"])
            return
        msg = upd.get("message") or {}
        if not msg:
            return
        from_u = msg.get("from") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or from_u.get("is_bot"):
            return
        user = self.store.upsert_telegram_user(from_u)
        if user["is_banned"]:
            await self.send(chat_id, "Доступ закрыт.")
            return
        tg_id = int(from_u.get("id") or 0)
        wait_al = self._wait_alert.get(tg_id)
        if wait_al:
            if text.startswith("/"):
                self._wait_alert.pop(tg_id, None)
                if text.startswith("/cancel"):
                    await self.show_menu(chat_id, "Отмена.", self._alert_kb(user))
                    return
            else:
                msg_ok = self._alert_apply_text(user, wait_al, text or msg)
                self._wait_alert.pop(tg_id, None)
                await self.show_menu(chat_id, msg_ok + "\n\n" + self._alert_text(user),
                                     self._alert_kb(user))
                return
        wait = self._wait_tpl.get(tg_id)
        if wait and user.get("is_admin"):
            if text.startswith("/"):
                self._wait_tpl.pop(tg_id, None)
                if text.startswith("/cancel"):
                    await self.show_menu(chat_id, "Отмена.", self._tpl_kb())
                    return
            elif wait == "head":
                if not text:
                    await self.show_menu(
                        chat_id, "Нужен текст шапки. /cancel — отмена.", self._tpl_kb())
                    return
                self._wait_tpl.pop(tg_id, None)
                r = self.store.add_digest_head(text, actor_id=user["id"])
                msg_ok = "Шапка добавлена." if r.get("ok") else f"Не вышло: {r.get('error')}"
                await self.show_menu(chat_id, msg_ok + "\n\n" + self._tpl_home_text(),
                                     self._tpl_kb())
                return
            elif wait == "photo":
                self._wait_tpl.pop(tg_id, None)
                r = await self._ingest_tpl_photo(msg, user)
                if not r.get("ok"):
                    self._wait_tpl[tg_id] = "photo"
                    await self.show_menu(
                        chat_id,
                        "Пришлите картинку jpg/png (как фото или файл). /cancel — отмена.",
                        self._tpl_kb())
                    return
                await self.show_menu(
                    chat_id, "Фото добавлено.\n\n" + self._tpl_home_text(), self._tpl_kb())
                return
        if user.get("is_admin") and not wait and await self._bind_channel_from_message(msg, chat_id):
            return
        if self._wait_broadcast.get(int(from_u["id"])) and user["is_admin"]:
            if text.startswith("/"):
                self._wait_broadcast.pop(int(from_u["id"]), None)
            else:
                self._wait_broadcast.pop(int(from_u["id"]), None)
                await self.send(chat_id, "Рассылаю…")
                st = await self.broadcast(text, actor_id=user["id"])
                await self.send(chat_id,
                                f"Готово: доставлено <b>{st['ok']}</b> из {st['total']}"
                                + (f", ошибок {st['fail']}" if st["fail"] else "") + ".")
                return
        if text.startswith("/start"):
            await self._cmd_start(chat_id, user, text)
            return
        if not await self._ensure_channel(chat_id, user):
            return
        nav = self._reply_cmd(text)
        if nav:
            if nav == "admin":
                await self._cmd_admin(chat_id, user)
                return
            if nav == "channel":
                await self.show_menu(
                    chat_id, f"Канал:\n{self.channel_url}", self._reply_kb(user))
                return
            if nav == "al":
                await self.show_menu(chat_id, self._alert_text(user), self._alert_kb(user))
                return
            body, kb = self._screen(user, nav)
            await self.show_menu(chat_id, body, kb)
            return
        if text.startswith("/digest") and user.get("is_admin"):
            ok = await self.post_channel_digest(force=True)
            await self.show_menu(chat_id, self._digest_result_text(ok),
                                 self._admin_kb())
        elif text.startswith("/help"):
            await self.show_menu(chat_id, self._help(user), self._reply_kb(user))
        elif text.startswith("/cabinet"):
            await self.show_menu(chat_id, self._cabinet_text(user), self._reply_kb(user))
        elif text.startswith("/stats"):
            await self.show_menu(chat_id, self._stats_text(), self._reply_kb(user))
        elif text.startswith("/status") or text.startswith("/health"):
            await self.show_menu(chat_id, self._health_text(), self._reply_kb(user))
        elif text.startswith("/liq"):
            await self.show_menu(chat_id, self._liq_text(), self._reply_kb(user))
        elif text.startswith("/services"):
            await self.show_menu(chat_id, self._services_text(user), self._services_kb(user))
        elif text.startswith("/alerts"):
            await self.show_menu(chat_id, self._alert_text(user), self._alert_kb(user))
        elif text.startswith("/terminal"):
            await self.show_menu(chat_id, self._terminal_text(), self._reply_kb(user))
        elif text.startswith("/admin"):
            await self._cmd_admin(chat_id, user)
        elif text.startswith("/users"):
            await self._cmd_users(chat_id, user)
        elif text.startswith("/visits"):
            await self._cmd_visits(chat_id, user)
        elif text.startswith("/broadcast"):
            await self._cmd_broadcast(chat_id, user, text)
        else:
            await self.show_menu(chat_id, "Не понял. Нажмите кнопку или /help.", self._reply_kb(user))

    async def _on_callback(self, cb: dict) -> None:
        from_u = cb.get("from") or {}
        data = (cb.get("data") or "").strip()
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id") or from_u.get("id")
        message_id = msg.get("message_id")
        user = self.store.upsert_telegram_user(from_u)
        if user["is_banned"]:
            await self.answer_cb(cb["id"], "Доступ закрыт")
            return
        tg_id = int(from_u.get("id") or 0)
        if data in ("menu", "back", "nav:home", "home", "nav:admin", "a:tpl", "al", "services"):
            self._wait_broadcast.pop(tg_id, None)
            self._wait_tpl.pop(tg_id, None)
            self._wait_alert.pop(tg_id, None)
        # Сначала снимаем «часики»: если answer уйдёт после edit или с
        # пустым text, клиент залипает и следующие кнопки не нажимаются.
        await self.answer_cb(cb["id"])
        try:
            if data == "ch:check":
                if await self._is_member(tg_id):
                    text, markup = self._home_text(user), self._reply_kb(user)
                    ok = await self.reply(chat_id, text, markup, message_id=message_id)
                else:
                    extra = ("Telegram ещё не видит подписку. Откройте канал, "
                             "затем нажмите «Я подписался» ещё раз.")
                    ok = await self.show_menu(chat_id, self._join_text(extra),
                                              self._kb_join(), old_id=message_id)
                if not ok:
                    log.warning("меню не обновилось data=%s chat=%s msg=%s",
                                data, chat_id, message_id)
                return
            if data == "a:digest" and user.get("is_admin"):
                posted = await self.post_channel_digest(force=True)
                await self.reply(chat_id, self._digest_result_text(posted),
                                 self._admin_kb(), message_id=message_id)
                return
            if user.get("is_admin") and (
                    data == "a:tpl" or data.startswith("a:th") or data.startswith("a:tp")):
                await self._on_tpl_cb(chat_id, user, data, message_id)
                return
            if data == "al" or data.startswith("al:"):
                await self._on_alert_cb(chat_id, user, data, message_id)
                return
            if data != "ch:check" and not await self._ensure_channel(
                    chat_id, user, message_id=message_id):
                return
            text, markup = self._screen(user, data)
            ok = await self.reply(chat_id, text, markup, message_id=message_id)
            if not ok:
                log.warning("меню не обновилось data=%s chat=%s msg=%s",
                            data, chat_id, message_id)
        except Exception as e:
            log.warning("cb %s: %s", data, e)

    def _screen(self, user: dict, data: str) -> tuple:
        """Текст и клавиатура экрана."""
        if data in ("menu", "back", "nav:home", "home", "help", ""):
            if data == "help":
                return self._help(user), self._reply_kb(user)
            return self._home_text(user), self._reply_kb(user)
        if data == "cabinet":
            return self._cabinet_text(user), self._reply_kb(user)
        if data == "stats":
            return self._stats_text(), self._reply_kb(user)
        if data == "health":
            return self._health_text(), self._reply_kb(user)
        if data == "a:health" and user.get("is_admin"):
            return self._health_text(), self._kb_back("nav:admin")
        if data == "liq":
            return self._liq_text(), self._reply_kb(user)
        if data == "terminal":
            return self._terminal_text(), self._reply_kb(user)
        if data == "services":
            return self._services_text(user), self._services_kb(user)
        if data.startswith("svc:"):
            slug = data.split(":", 1)[1]
            if slug == "alerts":
                row = next((s for s in self.store.list_services(False)
                            if s["slug"] == "alerts"), None)
                if row and not row.get("coming_soon"):
                    return self._alert_text(user), self._alert_kb(user)
            have = set(self.store.user_service_slugs(user["id"]))
            on = slug not in have
            self.store.toggle_user_service(user["id"], slug, on)
            return self._services_text(user), self._services_kb(user)
        if data in ("admin", "nav:admin") and user.get("is_admin"):
            self._wait_broadcast.pop(int(user.get("tg_id") or 0), None)
            self._wait_tpl.pop(int(user.get("tg_id") or 0), None)
            return self._admin_text(), self._admin_kb()
        if data == "users" and user.get("is_admin"):
            return self._users_text(), self._kb_back("nav:admin")
        if data == "visits" and user.get("is_admin"):
            return self._visits_text(), self._kb_back("nav:admin")
        if data == "broadcast" and user.get("is_admin"):
            self._wait_broadcast[int(user.get("tg_id") or 0)] = True
            return ("Пришлите текст рассылки следующим сообщением.\n"
                    "/cancel — отмена.", self._kb_back("nav:admin"))
        return self._home_text(user), self._reply_kb(user)

    async def _cmd_start(self, chat_id: int, user: dict, text: str) -> None:
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if payload.startswith("login_"):
            nonce = payload[6:]
            if self.store.confirm_nonce(nonce, user["id"]):
                if not await self._ensure_channel(chat_id, user):
                    return
                await self.show_menu(
                    chat_id,
                    f"Вход подтверждён, {_esc(user['display_name'])}.\n"
                    f"Вернитесь во вкладку браузера — кабинет откроется сам.\n\n"
                    f"Сайт: {self.cabinet_url()}",
                    self._reply_kb(user),
                )
                return
            await self.show_menu(
                chat_id,
                "Код входа недействителен или устарел. Нажмите «Войти» на сайте ещё раз.",
                self._reply_kb(user),
            )
            return
        if not await self._ensure_channel(chat_id, user):
            return
        welcome = self.store.get_setting(
            "bot_welcome",
            "Это бот <b>LiqScope</b> — живой терминал ликвидаций крипто-фьючерсов.\n"
            "Здесь тот же кабинет, что и на сайте: статистика рынка, биржи, сервисы.",
        )
        extra = f"\nКабинет: {self.cabinet_url()}\nТерминал: {self.terminal_url()}"
        await self.show_menu(
            chat_id,
            f"Привет, {_esc(user.get('display_name') or 'друг')}!\n\n{welcome}{extra}",
            self._reply_kb(user),
        )

    async def _cmd_admin(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._reply_kb(user))
            return
        await self.show_menu(chat_id, self._admin_text(), self._admin_kb())

    async def _cmd_users(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._reply_kb(user))
            return
        await self.show_menu(chat_id, self._users_text(), self._kb_back("nav:admin"))

    async def _cmd_visits(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._reply_kb(user))
            return
        await self.show_menu(chat_id, self._visits_text(), self._kb_back("nav:admin"))

    async def _cmd_broadcast(self, chat_id: int, user: dict, text: str) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._reply_kb(user))
            return
        rest = text.split(maxsplit=1)
        body = rest[1].strip() if len(rest) > 1 else ""
        if not body:
            self._wait_broadcast[int(user["tg_id"])] = True
            await self.show_menu(
                chat_id,
                "Пришлите текст рассылки следующим сообщением.\n/cancel — отмена.",
                self._kb_back("nav:admin"),
            )
            return
        await self.send(chat_id, "Рассылаю…")
        st = await self.broadcast(body, actor_id=user["id"])
        await self.show_menu(chat_id, f"Готово: {st['ok']}/{st['total']}.", self._admin_kb())

    def _tpl_home_text(self) -> str:
        heads = self.store.list_digest_heads() if self.store else []
        photos = self.store.list_digest_photos() if self.store else []
        lines = [
            "<b>Шаблоны сводки</b>",
            f"Шапки: {len(heads)} · фото: {len(photos)}",
            "Посты крутят их по очереди. В шапке можно {h} — это часы окна.",
            "",
        ]
        for i, h in enumerate(heads[:15], 1):
            t = str(h.get("text") or "")
            if len(t) > 70:
                t = t[:67] + "…"
            lines.append(f"{i}. {_esc(t)}")
        if len(heads) > 15:
            lines.append(f"… и ещё {len(heads) - 15}")
        if not heads:
            lines.append("Шапки пусты — в постах дефолтные из кода.")
        return "\n".join(lines)

    def _tpl_kb(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "➕ Шапка", "callback_data": "a:th+"},
             {"text": "➕ Фото", "callback_data": "a:tp+"}],
            [{"text": "🗑 Удалить шапку", "callback_data": "a:th-"},
             {"text": "🗑 Удалить фото", "callback_data": "a:tp-"}],
            [{"text": "← Назад", "callback_data": "nav:admin"}],
        ]}

    def _tpl_del_heads_kb(self) -> dict:
        rows: List[list] = []
        row: list = []
        for i, h in enumerate(self.store.list_digest_heads(), 1):
            row.append({"text": f"✗{i}", "callback_data": f"a:th:{h['id']}"})
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if not rows:
            rows.append([{"text": "список пуст", "callback_data": "a:tpl"}])
        rows.append([{"text": "← К шаблонам", "callback_data": "a:tpl"}])
        return {"inline_keyboard": rows}

    def _tpl_del_photos_kb(self) -> dict:
        rows: List[list] = []
        row: list = []
        for i, p in enumerate(self.store.list_digest_photos(), 1):
            row.append({"text": f"✗{i}", "callback_data": f"a:tp:{p['id']}"})
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if not rows:
            rows.append([{"text": "список пуст", "callback_data": "a:tpl"}])
        rows.append([{"text": "← К шаблонам", "callback_data": "a:tpl"}])
        return {"inline_keyboard": rows}

    async def _on_tpl_cb(self, chat_id, user: dict, data: str, message_id) -> None:
        tg_id = int(user.get("tg_id") or 0)
        if data == "a:tpl":
            await self.reply(chat_id, self._tpl_home_text(), self._tpl_kb(),
                             message_id=message_id)
            return
        if data == "a:th+":
            self._wait_tpl[tg_id] = "head"
            await self.reply(
                chat_id,
                "Пришлите текст шапки следующим сообщением.\n"
                "Можно {h} — подставится число часов.\n/cancel — отмена.",
                self._kb_back("a:tpl"), message_id=message_id)
            return
        if data == "a:tp+":
            self._wait_tpl[tg_id] = "photo"
            await self.reply(
                chat_id,
                "Пришлите картинку jpg/png (как фото или файл).\n/cancel — отмена.",
                self._kb_back("a:tpl"), message_id=message_id)
            return
        if data == "a:th-":
            await self.reply(chat_id, "Какую шапку убрать?",
                             self._tpl_del_heads_kb(), message_id=message_id)
            return
        if data == "a:tp-":
            await self.reply(chat_id, "Какое фото убрать?",
                             self._tpl_del_photos_kb(), message_id=message_id)
            return
        if data.startswith("a:th:"):
            try:
                hid = int(data.split(":")[2])
            except (IndexError, ValueError):
                hid = 0
            self.store.delete_digest_head(hid, actor_id=user["id"])
            await self.reply(chat_id, self._tpl_home_text(), self._tpl_kb(),
                             message_id=message_id)
            return
        if data.startswith("a:tp:"):
            try:
                pid = int(data.split(":")[2])
            except (IndexError, ValueError):
                pid = 0
            self.store.delete_digest_photo(pid, actor_id=user["id"])
            await self.reply(chat_id, self._tpl_home_text(), self._tpl_kb(),
                             message_id=message_id)

    async def _ingest_tpl_photo(self, msg: dict, user: dict) -> dict:
        file_id = None
        photos = msg.get("photo") or []
        if photos:
            file_id = (photos[-1] or {}).get("file_id")
        doc = msg.get("document") or {}
        mime = str(doc.get("mime_type") or "")
        name = str(doc.get("file_name") or "photo.jpg")
        if not file_id and mime.startswith("image/"):
            file_id = doc.get("file_id")
        if not file_id:
            return {"ok": False, "error": "no_photo"}
        res = await self._call("getFile", {"file_id": file_id})
        fpath = ((res or {}).get("result") or {}).get("file_path") or ""
        if not fpath or not self._session:
            return {"ok": False, "error": "no_file"}
        url = f"https://api.telegram.org/file/bot{self.token}/{fpath}"
        try:
            async with self._session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                blob = await r.read()
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}
        return self.store.add_digest_photo(blob, filename=name, actor_id=user["id"])

    def _kb_back(self, to: str = "nav:home") -> dict:
        aliases = {"menu": "nav:home", "home": "nav:home", "back": "nav:home",
                   "admin": "nav:admin"}
        to = aliases.get(to, to)
        return {"inline_keyboard": [[{"text": "← Назад", "callback_data": to}]]}

    def _menu(self, user: dict) -> dict:
        return self._reply_kb(user)

    def _admin_kb(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "👥 Пользователи", "callback_data": "users"},
             {"text": "📈 Визиты", "callback_data": "visits"}],
            [{"text": "📣 Рассылка", "callback_data": "broadcast"},
             {"text": "🩺 Здоровье", "callback_data": "a:health"}],
            [{"text": "📰 Сводка в канал", "callback_data": "a:digest"}],
            [{"text": "🎨 Шаблоны канала", "callback_data": "a:tpl"}],
            [{"text": "← Назад", "callback_data": "nav:home"}],
        ]}

    def _services_kb(self, user: dict) -> dict:
        have = set(self.store.user_service_slugs(user["id"]))
        rows = []
        for s in self.store.list_services(include_disabled=False):
            mark = "✓ " if s["slug"] in have else ""
            lock = " (скоро)" if s["coming_soon"] else ""
            rows.append([{"text": f"{mark}{s['icon']} {s['title']}{lock}",
                          "callback_data": "svc:" + s["slug"]}])
        rows.append([{"text": "← Назад", "callback_data": "nav:home"}])
        return {"inline_keyboard": rows}

    def _home_text(self, user: dict) -> str:
        return (
            f"<b>LiqScope</b>\n"
            f"Привет, {_esc(user['display_name'])}!\n"
            f"Выберите раздел — кнопки внизу экрана."
        )

    def _help(self, user: dict) -> str:
        lines = [
            "<b>Команды LiqScope</b>",
            "/start — регистрация / вход на сайт",
            "/cabinet — профиль",
            "/terminal — ссылка на терминал",
            "/stats — ликвидации 24ч",
            "/status — какие биржи в эфире",
            "/liq — последние события",
            "/services — сервисы кабинета",
            "/alerts — алерты по объёму",
        ]
        if user.get("is_admin"):
            lines += [
                "",
                "<b>Админ</b>",
                "/admin /users /visits",
                "/broadcast текст — рассылка всем",
                "/digest — сводка ликвидаций в канал",
            ]
        return "\n".join(lines)

    def _cabinet_text(self, user: dict) -> str:
        un = f"@{_esc(user['username'])}" if user["username"] else "—"
        link = f"\nСайт: {self.cabinet_url()}"
        have = self.store.user_service_slugs(user["id"])
        svc = ", ".join(have) if have else "пока не выбраны"
        role = "администратор" if user["is_admin"] else "пользователь"
        return (
            f"<b>Кабинет</b>\n"
            f"{_esc(user['display_name'])} · {un}\n"
            f"Telegram ID: <code>{user['tg_id']}</code>\n"
            f"Роль: {role}\n"
            f"Сервисы: { _esc(svc) }"
            f"{link}"
        )

    def _terminal_text(self) -> str:
        return f"Терминал ликвидаций:\n{self.terminal_url()}"

    def _terminal_kb(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "⚡ Открыть", "url": self.terminal_url()}],
            [{"text": "← Назад", "callback_data": "nav:home"}],
        ]}

    def _stats_text(self) -> str:
        st = self.stats_fn() or {}
        def usd(v):
            try:
                n = float(v or 0)
            except (TypeError, ValueError):
                n = 0.0
            if n >= 1e9:
                return f"${n/1e9:.2f}B"
            if n >= 1e6:
                return f"${n/1e6:.2f}M"
            if n >= 1e3:
                return f"${n/1e3:.1f}K"
            return f"${n:.0f}"
        top = st.get("top_coins") or []
        top_s = ", ".join(
            f"{(c.get('symbol') or '').replace('_', '/')} {usd(c.get('usd'))}"
            for c in top[:5]
        ) or "—"
        return (
            f"<b>Рынок · 24ч</b>\n"
            f"Ликвидации: {usd(st.get('total_usd_24h'))}\n"
            f"Лонги {usd(st.get('longs_usd_24h'))} × шорты {usd(st.get('shorts_usd_24h'))}\n"
            f"За час: {usd(st.get('total_usd_1h'))}\n"
            f"Лидеры: { _esc(top_s) }"
        )

    def _health_text(self) -> str:
        h = self.health_fn() or {}
        srcs = h.get("sources") or {}
        skip = {"prices", "ticks", "oxa"}
        lines = ["<b>Биржи</b>"]
        live = 0
        total = 0
        for name, s in sorted(srcs.items()):
            if name in skip or not isinstance(s, dict):
                continue
            total += 1
            ok = bool(s.get("connected"))
            if ok:
                live += 1
            mark = "●" if ok else "✕"
            err = "" if ok else f" · {_esc((s.get('last_error') or 'нет связи')[:40])}"
            lines.append(f"{mark} {_esc(name)}{err}")
        if not total:
            lines.append("сервер ещё собирает источники")
        lines.append(f"\nВ эфире {live}/{total or '—'} · WS-клиентов {self.ws_clients_fn()}")
        return "\n".join(lines)

    def _liq_text(self) -> str:
        rows = list(self.liqs_fn() or [])[-8:]
        rows = list(reversed(rows))
        if not rows:
            return "Пока нет событий в памяти."
        lines = ["<b>Последние ликвидации</b>"]
        for x in rows:
            side = "L" if x.get("side") == "SELL" else "S"
            sym = str(x.get("symbol") or "").replace("_", "/")
            try:
                usd = float(x.get("usd") or 0)
                usd_s = f"${usd:,.0f}"
            except (TypeError, ValueError):
                usd_s = "$?"
            lines.append(f"{side} {_esc(sym)} {_esc(x.get('exchange'))} {usd_s}")
        return "\n".join(lines)

    def _services_text(self, user: dict) -> str:
        have = set(self.store.user_service_slugs(user["id"]))
        lines = [
            "<b>Сервисы кабинета</b>",
            "Те же, что на сайте. Алерты по объёму уже работают — откройте 🔔.",
            "",
        ]
        for s in self.store.list_services(include_disabled=False):
            mark = "✓" if s["slug"] in have else "○"
            if s["slug"] == "alerts" and not s.get("coming_soon"):
                extra = " · настроить"
            else:
                extra = " · скоро" if s["coming_soon"] else ""
            lines.append(f"{mark} {s['icon']} <b>{_esc(s['title'])}</b>{extra}")
            if s.get("description"):
                lines.append(f"    {_esc(s['description'])}")
        lines.append("\nАлерты открывают настройки. Остальное — лист ожидания, пока «скоро».")
        return "\n".join(lines)

    def _users_text(self) -> str:
        data = self.store.list_users(limit=10)
        c = self.store.user_counts()
        lines = [f"<b>Пользователи</b> · всего {c['total']} · за 24ч {c['active_24h']} · новых {c['new_24h']}"]
        for u in data["users"]:
            flag = " ★" if u["is_admin"] else ""
            ban = " ⛔" if u["is_banned"] else ""
            un = f" @{_esc(u['username'])}" if u["username"] else ""
            lines.append(f"· {_esc(u['display_name'])}{un} <code>{u['tg_id']}</code>{flag}{ban}")
        return "\n".join(lines)

    def _visits_text(self) -> str:
        v = self.store.visit_stats(7)
        lines = [
            "<b>Визиты</b>",
            f"Сегодня: {v['today_views']} просмотров, {v['today_uniques']} уникальных",
            f"Онлайн WS: {self.ws_clients_fn()}",
        ]
        for d in v["days"][-7:]:
            lines.append(f"· {d['day']}: {d['views']} / {d['uniques']} уник.")
        return "\n".join(lines)

    def _admin_text(self) -> str:
        c = self.store.user_counts()
        v = self.store.visit_stats(1)
        h = self.health_fn() or {}
        live = h.get("live_exchanges") or []
        link = f"\nПанель: {self.admin_url()}"
        return (
            f"<b>Админка LiqScope</b>\n"
            f"Пользователи: {c['total']} (за сутки {c['active_24h']}, новых {c['new_24h']})\n"
            f"Визиты сегодня: {v['today_views']} / {v['today_uniques']} уник.\n"
            f"Онлайн WS: {self.ws_clients_fn()}\n"
            f"Биржи в эфире: {len(live)}"
            f"{link}"
        )

    def _alert_cfg(self, user: dict) -> dict:
        from alerts import normalize_config
        row = self.store.get_user_service(user["id"], "alerts") if self.store else None
        return normalize_config((row or {}).get("config") or {})

    def _alert_save(self, user: dict, cfg: dict) -> dict:
        return self.store.set_user_service_config(
            user["id"], "alerts", cfg, enabled=True)

    def _alert_text(self, user: dict) -> str:
        from alerts import format_config_text, live_snapshot, money, window_label
        cfg = self._alert_cfg(user)
        lines = ["<b>🔔 Алерты по объёму</b>", format_config_text(cfg)]
        fn = self.alerts_market_fn
        if fn:
            try:
                market = fn() or {}
                live = live_snapshot(cfg, market)
                bits = []
                for m, title in (("liq", "LIQ"), ("cvd", "CVD"), ("oi", "OI")):
                    row = live.get(m) or {}
                    bits.append(f"{title} {money(row.get('value'))}")
                lines.append("сейчас: " + " · ".join(bits))
                lines.append(f"окно {window_label(cfg['window_min'])}")
            except Exception:
                pass
        lines.append("\nКнопки ниже — метрика, монета, окно, порог. Сигнал приходит отдельным сообщением.")
        return "\n".join(lines)

    def _alert_kb(self, user: dict) -> dict:
        cfg = self._alert_cfg(user)
        w = cfg.get("watch") or []
        def mark(m, label):
            return ("✓ " if m in w else "") + label
        on = "🔔 Сигнал ВКЛ" if cfg.get("enabled") else "🔕 Сигнал выкл"
        return {"inline_keyboard": [
            [{"text": mark("liq", "💥 LIQ"), "callback_data": "al:m:liq"},
             {"text": mark("cvd", "🌊 CVD"), "callback_data": "al:m:cvd"},
             {"text": mark("oi", "📊 OI"), "callback_data": "al:m:oi"}],
            [{"text": "Монета", "callback_data": "al:c"},
             {"text": "Окно", "callback_data": "al:w"}],
            [{"text": "Порог", "callback_data": "al:t"},
             {"text": "Мин. удар", "callback_data": "al:n"}],
            [{"text": on, "callback_data": "al:on"}],
            [{"text": "← Назад", "callback_data": "services"}],
        ]}

    def _alert_coins_kb(self) -> dict:
        from alerts import COIN_PRESETS, coin_name
        rows = [[{"text": coin_name(c), "callback_data": f"al:c:{c}"}] for c in COIN_PRESETS]
        rows.append([{"text": "своя монета", "callback_data": "al:c:?"}])
        rows.append([{"text": "← К алертам", "callback_data": "al"}])
        return {"inline_keyboard": rows}

    def _alert_win_kb(self) -> dict:
        from alerts import WINDOW_PRESETS, window_label
        row = [{"text": window_label(w), "callback_data": f"al:w:{w}"} for w in WINDOW_PRESETS]
        return {"inline_keyboard": [
            row[:3], row[3:],
            [{"text": "свои минуты", "callback_data": "al:w:?"}],
            [{"text": "← К алертам", "callback_data": "al"}],
        ]}

    def _alert_thr_kb(self, metric: str, field: str) -> dict:
        from alerts import MIN_PRESETS, money, threshold_presets
        presets = threshold_presets(metric) if field == "thr" else MIN_PRESETS
        rows: List[list] = []
        row: list = []
        prefix = f"al:{'t' if field == 'thr' else 'n'}:{metric}:"
        for v in presets:
            row.append({"text": money(v) if v else "0", "callback_data": prefix + str(int(v))})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "своё число", "callback_data": prefix + "?"}])
        rows.append([{"text": "← К алертам", "callback_data": "al"}])
        return {"inline_keyboard": rows}

    def _alert_metric_pick_kb(self, field: str) -> dict:
        letter = "t" if field == "thr" else "n"
        return {"inline_keyboard": [
            [{"text": "LIQ", "callback_data": f"al:{letter}:liq"},
             {"text": "CVD", "callback_data": f"al:{letter}:cvd"},
             {"text": "OI", "callback_data": f"al:{letter}:oi"}],
            [{"text": "← К алертам", "callback_data": "al"}],
        ]}

    def _alert_apply_text(self, user: dict, wait: str, raw) -> str:
        from alerts import canon_symbol, money, window_label
        cfg = self._alert_cfg(user)
        text = raw if isinstance(raw, str) else ""
        if wait == "coin":
            cfg["symbol"] = canon_symbol(text)
            self._alert_save(user, cfg)
            return f"Монета: {cfg['symbol']}"
        if wait == "win":
            try:
                n = int(float(text.replace(",", ".")))
            except (TypeError, ValueError):
                return "Нужно число минут, например 5."
            cfg["window_min"] = max(1, min(n, 1440))
            self._alert_save(user, cfg)
            return f"Окно: {window_label(cfg['window_min'])}"
        if wait.startswith("thr:") or wait.startswith("min:"):
            kind, metric = wait.split(":", 1)
            try:
                n = float(text.replace(" ", "").replace(",", ".").replace("$", "")
                          .replace("k", "000").replace("K", "000")
                          .replace("m", "000000").replace("M", "000000"))
            except (TypeError, ValueError):
                return "Нужна сумма в долларах, например 250000 или 250k."
            key = "threshold" if kind == "thr" else "min_event"
            cfg[key][metric] = max(0.0, n)
            self._alert_save(user, cfg)
            return f"{'Порог' if kind == 'thr' else 'Мин. удар'} {metric}: {money(n)}"
        return "Не понял."

    async def _on_alert_cb(self, chat_id, user: dict, data: str, message_id) -> None:
        tg_id = int(user.get("tg_id") or 0)
        cfg = self._alert_cfg(user)
        if data == "al":
            await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                             message_id=message_id)
            return
        if data == "al:on":
            cfg["enabled"] = not cfg.get("enabled")
            self._alert_save(user, cfg)
            await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                             message_id=message_id)
            return
        if data.startswith("al:m:"):
            m = data.split(":")[2]
            w = list(cfg.get("watch") or [])
            if m in w:
                w.remove(m)
            else:
                w.append(m)
            cfg["watch"] = w
            self._alert_save(user, cfg)
            await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                             message_id=message_id)
            return
        if data == "al:c":
            await self.reply(chat_id, "Какую монету смотреть?", self._alert_coins_kb(),
                             message_id=message_id)
            return
        if data == "al:c:?":
            self._wait_alert[tg_id] = "coin"
            await self.reply(chat_id, "Пришлите тикер, например BTC или ETHUSDT.\n/cancel — отмена.",
                             self._kb_back("al"), message_id=message_id)
            return
        if data.startswith("al:c:"):
            from alerts import canon_symbol
            cfg["symbol"] = canon_symbol(data.split(":", 2)[2])
            self._alert_save(user, cfg)
            await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                             message_id=message_id)
            return
        if data == "al:w":
            await self.reply(chat_id, "Окно агрегации:", self._alert_win_kb(),
                             message_id=message_id)
            return
        if data == "al:w:?":
            self._wait_alert[tg_id] = "win"
            await self.reply(chat_id, "Сколько минут в окне? Число, например 7.\n/cancel — отмена.",
                             self._kb_back("al"), message_id=message_id)
            return
        if data.startswith("al:w:"):
            try:
                n = int(data.split(":")[2])
            except (IndexError, ValueError):
                n = 5
            cfg["window_min"] = max(1, min(n, 1440))
            self._alert_save(user, cfg)
            await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                             message_id=message_id)
            return
        if data in ("al:t", "al:n"):
            field = "thr" if data == "al:t" else "min"
            title = "Порог какой метрики?" if field == "thr" else "Мин. удар какой метрики?"
            await self.reply(chat_id, title, self._alert_metric_pick_kb(field),
                             message_id=message_id)
            return
        # al:t:liq / al:t:liq:500000 / al:t:liq:?
        parts = data.split(":")
        if len(parts) >= 3 and parts[1] in ("t", "n"):
            field = "thr" if parts[1] == "t" else "min"
            metric = parts[2]
            if len(parts) == 3:
                title = "Порог" if field == "thr" else "Мин. удар"
                await self.reply(chat_id, f"{title} {metric.upper()}:",
                                 self._alert_thr_kb(metric, field),
                                 message_id=message_id)
                return
            val = parts[3]
            if val == "?":
                self._wait_alert[tg_id] = f"{field}:{metric}"
                await self.reply(
                    chat_id,
                    "Пришлите сумму в $ — 250000 или 250k.\n/cancel — отмена.",
                    self._kb_back("al"), message_id=message_id)
                return
            try:
                n = float(val)
            except ValueError:
                n = 0.0
            key = "threshold" if field == "thr" else "min_event"
            cfg[key][metric] = max(0.0, n)
            self._alert_save(user, cfg)
            await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                             message_id=message_id)
            return
        await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                         message_id=message_id)
