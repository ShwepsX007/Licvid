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

from refs import ex_link, gate_line, gate_url

log = logging.getLogger("liqscope.bot")

API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_PUBLIC_URL = "https://liqscope.online"
# Сторож опроса. Если успешного getUpdates не было дольше этого времени,
# задача опроса считается зависшей и пересоздаётся: иначе «бот пишет уведомления,
# но не видит кнопки» лечится только ручным перезапуском сервиса, а
# /api/health при этом показывает running: true и fails: 0.
POLL_STALE_SEC = float(os.getenv("LIQSCOPE_BOT_POLL_STALE_SEC", "180"))


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


def _strip_html(s: str) -> str:
    """Текст без разметки — для повтора, когда Telegram отверг parse_mode."""
    import html as _html
    import re
    return _html.unescape(re.sub(r"<[^>]+>", "", s or ""))


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
        # Второй канал — английская копия тех же сводок. Ссылки-приглашения
        # даёт админ (LIQSCOPE_CHANNEL2_URL / LIQSCOPE_CHANNEL2_ID), id
        # подхватывается и сам, когда бота добавляют в канал админом.
        self.channel2_url = (os.getenv("LIQSCOPE_CHANNEL2_URL")
                             or "https://t.me/+a13HtmoE9jNhY2M6").strip()
        self._channel2_id_cfg = (os.getenv("LIQSCOPE_CHANNEL2_ID") or "").strip()
        self._join_ok: Dict[int, float] = {}   # tg_id -> до какого времени пускать
        self.digest_fn = digest_fn or (lambda: {})
        self.ai = None                      # ai_text.AiWriter из server.py (может не быть)
        self._ai_recent: List[str] = []      # последние ИИ-шапки (рус)
        self._ai_recent_en: List[str] = []   # последние ИИ-шапки (англ)
        self._ai_state: Dict[str, Any] = {}  # что ответил ИИ (для админки)
        self._draft: Optional[Dict[str, Any]] = None   # непринятый пост (контроль)
        self._digest_task: Optional[asyncio.Task] = None
        self._ch_ok: Dict[int, float] = {}   # tg_id -> cache until
        self._ch_warn_at = 0.0               # троттлинг варнинга про канал
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
        self._wait_email: Dict[int, float] = {}     # tg_id -> ждём адрес почты до
        self._mail_at: Dict[str, float] = {}        # "tg:адрес" -> когда слали письмо
        self.mailer = None                          # mailer.Mailer из server.py
        self._menu_msg: Dict[int, int] = {}         # chat_id -> последнее меню
        self.alerts_market_fn: Optional[Callable[[], Any]] = None
        self._poll_fails = 0          # подряд неудачных getUpdates
        self._conflict_warned_at = 0.0  # когда последний раз слали 409-предупреждение
        self._conflict_mode = False     # конфликт: короткий опрос, быстрые повторы
        self._conflict_ok_streak = 0    # подряд успешных getUpdates в конфликт-режиме
        # Диагностика и сторож. Без этих полей «бот молчит, а health зелёный»
        # неотличим от «бот работает»: running — это флаг, выставленный один раз
        # при старте, он не говорит, жива ли задача опроса и доходят ли ответы.
        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_ok_at = 0.0        # последний успешный getUpdates
        self._last_update_at = 0.0    # последний полученный апдейт
        self._updates_seen = 0
        self._send_fails = 0          # Telegram отказался принять сообщение (429/403/parse)
        self._retry_after_until = 0.0  # пауза после 429 (flood control)
        self._flood_warned_at = 0.0
        self._watchdog_restarts = 0
        self._saved_offset = 0

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

    def channel_link_kb(self, lang: str = "ru") -> dict:
        """Кнопки под постом в канале: сайт, бот и партнёрская ссылка Gate."""
        gate = gate_url(lang)
        return {"inline_keyboard": [
            [{"text": "🌐 liqscope", "url": self.site_url()},
             {"text": "🤖 " + ("бот" if not str(lang).startswith("en") else "bot"),
              "url": self.bot_url()}],
            [{"text": ("💠 Торговать на Gate" if not str(lang).startswith("en")
                       else "💠 Trade on Gate"), "url": gate}],
        ]}

    # ----- красивые ссылки ------------------------------------------------
    def site_a(self, label: str, path: str = "") -> str:
        """Ссылка, спрятанная в слово: <a href="...">label</a>."""
        return f'<a href="{_esc(self.site_url(path))}">{_esc(label)}</a>'

    def site_footer(self) -> str:
        """Единый хвост уведомлений: сайт кликабельным словом."""
        return (
            "\n──────────────────────\n"
            f"🌐 {self.site_a('LiqScope')} — живой поток ликвидаций"
        )

    EXCHANGE_NAMES = {
        "binance": "Binance", "bybit": "Bybit", "okx": "OKX",
        "gate": "Gate.io", "bitget": "Bitget", "htx": "HTX",
        "bitmex": "BitMEX", "hyperliquid": "Hyperliquid", "dydx": "dYdX",
        "kraken": "Kraken", "bitfinex": "Bitfinex", "oxa": "0xArchive",
    }

    @classmethod
    def ex_name(cls, key: Any) -> str:
        k = str(key or "").lower()
        return cls.EXCHANGE_NAMES.get(k) or (str(key or "?").capitalize())

    @staticmethod
    def fmt_int(n: Any) -> str:
        try:
            return f"{int(round(float(n))):,}".replace(",", " ")
        except (TypeError, ValueError):
            return "0"

    @staticmethod
    def fmt_usd(n: Any) -> str:
        try:
            v = float(n)
        except (TypeError, ValueError):
            return "$0"
        sign = "−" if v < 0 else ""
        return f"{sign}${abs(v):,.0f}".replace(",", " ")

    @staticmethod
    def ago_label(sec: Any) -> str:
        try:
            s = max(0, int(float(sec)))
        except (TypeError, ValueError):
            return "—"
        if s < 60:
            return f"{s}с назад"
        if s < 3600:
            return f"{s // 60}м назад"
        return f"{s // 3600}ч назад"

    def _admin_tg_ids(self) -> List[int]:
        try:
            if self.store and hasattr(self.store, "admin_tg_ids"):
                return [int(x) for x in self.store.admin_tg_ids()]
        except Exception:
            pass
        return []

    def _reply_kb(self, user: Optional[dict] = None) -> dict:
        rows = [
            [{"text": "☰ Меню"}],                     # всегда под рукой
            [{"text": "👤 Кабинет"}, {"text": "⚡ Терминал"}],
            [{"text": "📊 Статистика"}, {"text": "🩺 Биржи"}],
            [{"text": "🛠 Сервисы"}, {"text": "📰 Лента"}],
            [{"text": "🔔 Алерты"}, {"text": "📣 Канал"}],
        ]
        if user and user.get("is_admin"):
            rows.append([{"text": "★ Админка"}])
        if user is not None:
            email = (user.get("email") or "").strip()
            if not email or not user.get("email_verified"):
                # Основной вход — почта: напоминаем, пока адрес не подтверждён
                rows.append([{"text": "✉️ Подтвердить почту"}])
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "is_persistent": True,
            "input_field_placeholder": "меню внизу экрана",
        }

    def _reply_cmd(self, text: str) -> str:
        key = " ".join((text or "").strip().lower().split())
        # Кнопка меню: одна на все случаи — если панель пропала после /start
        # или обновления Telegram, она возвращает и панель, и экран с командами.
        if key in ("☰ меню", "меню", "menu", "/menu", "команды"):
            return "help"
        key = key.replace("★ ", "").replace("👤 ", "").replace("⚡ ", "")
        key = key.replace("📊 ", "").replace("🩺 ", "").replace("🛠 ", "")
        key = key.replace("📰 ", "").replace("🔔 ", "").replace("📣 ", "")
        key = key.replace("✉️ ", "").replace("📧 ", "")
        return {
            "кабинет": "cabinet",
            "терминал": "terminal",
            "статистика": "stats",
            "биржи": "health",
            "сервисы": "services",
            "лента": "liq",
            "лента liq": "liq",
            "подтвердить почту": "mail",
            "почта": "mail",
            "email": "mail",
            "алерты": "al",
            "алерты по объёму": "al",
            "канал": "channel",
            "админка": "admin",
        }.get(key, "")

    async def start(self) -> None:
        if not self.token:
            log.warning("LIQSCOPE_BOT_TOKEN не задан — Telegram-бот выключен")
            return
        if self.running:
            return
        # getUpdates живёт до 50 с, остальные методы — короткие.
        # total на сессии не ставим: иначе длинный long-poll съедает лимит
        # и следующий edit/answer может оборваться.
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=70),
            connector=aiohttp.TCPConnector(limit=8),
        )
        # getMe на старте может отвалиться (сеть, 5xx у Telegram). Раньше в этом
        # случае бот молча оставался без опроса — кнопки не работали до
        # перезапуска сервиса. Повторяем несколько раз.
        me = None
        for attempt in range(5):
            me = await self._call("getMe")
            if me and me.get("ok"):
                break
            log.warning("getMe не удался (%d/5): %s", attempt + 1,
                        (me or {}).get("description") or "нет ответа")
            await asyncio.sleep(min(15.0, 3.0 * (attempt + 1)))
        if not me or not me.get("ok"):
            log.error("Не удалось получить бота getMe: %s — опрос не запущен", me)
            return
        self.username = me["result"]["username"]
        self.bot_id = int(me["result"]["id"])
        dw = await self._call("deleteWebhook", {"drop_pending_updates": False})
        if dw and not dw.get("ok"):
            log.error("Не удалось снять вебхук: %s — getUpdates может конфликтовать",
                      dw.get("description"))
        # offset переживает рестарт: иначе Telegram отдаёт старую очередь
        # апдейтов, и первые минуты после перезапуска бот отвечает «прошлым»
        # нажатиям, а свежие кнопки ждут своей очереди.
        try:
            # Ключ привязан к id бота: у разных ботов update_id нумеруются
            # независимо, и чужой offset заставил бы нового бота пропускать
            # апдейты (выглядит как «кнопки не работают после смены токена»).
            self._offset = int(
                self.store.get_setting(f"bot_offset:{self.bot_id}", "0") or 0)
        except (TypeError, ValueError, AttributeError):
            self._offset = 0
        self._saved_offset = self._offset
        self.running = True
        self._last_ok_at = time.time()
        self._task = asyncio.create_task(self._poll(), name="tg-bot")
        self._digest_task = asyncio.create_task(self._channel_loop(), name="tg-channel")
        self._watchdog_task = asyncio.create_task(self._watchdog(), name="tg-watchdog")
        log.info("Telegram-бот @%s запущен (offset=%s)", self.username, self._offset)

    async def stop(self) -> None:
        self.running = False
        for t in (self._task, self._digest_task, self._watchdog_task):
            if not t:
                continue
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._digest_task = None
        self._watchdog_task = None
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
            for attempt in (0, 1):
                async with self._session.post(url, json=payload or {},
                                              timeout=timeout) as r:
                    data = await r.json(content_type=None)
                # 429 (flood control) от Telegram: ждём столько, сколько сказано,
                # и пробуем ещё раз — иначе нажатие кнопки молча теряется,
                # а бот выглядит «сломанным», пока лимит не остынет.
                wait = self._flood_wait(data) if attempt == 0 else 0.0
                if method == "getUpdates" or not wait:
                    break
                log.warning("tg %s: флуд-лимит, ждём %.0f с и повторяем",
                            method, wait)
                await asyncio.sleep(wait)
            if method != "getUpdates" and data and not data.get("ok"):
                desc = str(data.get("description") or "")
                # «message is not modified» — повторный клик по той же кнопке,
                # для Telegram это не ошибка.
                if "not modified" not in desc.lower():
                    self._last_tg_err = desc
                    self._send_fails += 1
                    # chat_id в логе обязателен: иначе непонятно, кого именно
                    # Telegram не пускает (флуд-лимит 429, бот заблокирован 403,
                    # битый HTML) — а без этого «кнопки не работают» не отладить.
                    who = (payload or {}).get("chat_id")
                    log.warning("tg %s chat=%s: %s", method, who, desc)
                    if "retry after" in desc.lower() or data.get("error_code") == 429:
                        self._retry_after_until = time.time() + self._retry_after(data)
            return data
        except Exception as e:
            if method != "getUpdates":
                self._send_fails += 1
                self._last_tg_err = f"{method}: {e}"
            log.warning("tg %s: %s", method, e)
            return None

    @staticmethod
    def _flood_wait(res: Optional[dict]) -> float:
        """Сколько ждать перед повтором при 429. 0 — это не флуд-лимит."""
        if not res or res.get("ok"):
            return 0.0
        desc = str(res.get("description") or "").lower()
        code = res.get("error_code")
        if code != 429 and "retry after" not in desc and "too many requests" not in desc:
            return 0.0
        sec = TelegramBot._retry_after(res)
        # Дольше 20 с ждать смысла нет: пользователь всё равно уже не ждёт,
        # а опрос и очередь обновлений встанут.
        return min(sec, 20.0)

    @staticmethod
    def _retry_after(res: Optional[dict]) -> float:
        """Сколько секунд ждать после 429 (Telegram кладёт retry_after)."""
        try:
            params = (res or {}).get("parameters") or {}
            sec = float(params.get("retry_after") or 0)
            if sec > 0:
                return sec
        except (TypeError, ValueError, AttributeError):
            pass
        desc = str((res or {}).get("description") or "")
        tail = desc.split("retry after", 1)[-1]
        digits = "".join(ch for ch in tail if ch.isdigit())
        return max(1.0, float(digits)) if digits else 5.0

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
                # Разметку Telegram не принял: шлём тот же текст без тегов —
                # иначе в чате окажутся голые <pre>/<code>/<b>.
                body.pop("parse_mode", None)
                body["text"] = _strip_html(body.get("text") or "")
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
            # Раньше здесь стояло `or True`: правка, которую Telegram не принял
            # (флуд-лимит, сообщение удалено, битый HTML), считалась успешной —
            # и меню молча не перерисовывалось, а show_menu не слал новое.
            return bool(res2 and res2.get("ok"))
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
            # Ни правка, ни новое сообщение не прошли: без этой строки в логе
            # вообще ничего не видно, хотя для пользователя бот «просто молчит».
            log.error("меню не доставлено chat=%s: %s", chat_id,
                      self._last_tg_err or "Telegram не ответил")
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

    def channel_chat_id_en(self) -> str:
        return (self._channel2_id_cfg
                or (self.store.get_setting("channel_id_en", "") if self.store else "")
                or "").strip()

    def channel_url_en(self) -> str:
        return self.channel2_url

    def remember_channel_en(self, chat_id, title: str = "") -> str:
        cid = str(chat_id).strip()
        if not cid:
            return ""
        self.store.set_setting("channel_id_en", cid)
        if title:
            self.store.set_setting("channel_title_en", str(title)[:80])
        log.info("английский канал привязан chat_id=%s title=%s", cid, title)
        return cid

    def channel_pair(self) -> list:
        """Каналы для подписки: (код, название, url, id)."""
        return [
            ("ru", os.getenv("LIQSCOPE_CHANNEL_RU_NAME") or "LiqScopeRUS",
             self.channel_url, self.channel_chat_id()),
            ("en", os.getenv("LIQSCOPE_CHANNEL_EN_NAME") or "LiqScopeEng",
             self.channel_url_en(), self.channel_chat_id_en()),
        ]

    def _join_text(self, extra: str = "") -> str:
        more = f"\n\n{extra}" if extra else ""
        lines = [
            "<b>Выберите канал</b>",
            "Чтобы пользоваться ботом, подпишитесь на любой из двух каналов —",
            "содержание одно и то же, отличается язык:",
        ]
        for code, name, url, _cid in self.channel_pair():
            extra_txt = " 🇷🇺" if code == "ru" else " 🇬🇧"
            lines.append(f'• <a href="{_esc(url)}">{_esc(name)}</a>{extra_txt}')
        lines.append("")
        lines.append("Раз в 4 часа туда уходит разбор рынка: кто кого вынес,"
                     " на каких биржах, что с открытым интересом.")
        lines.append("Подписки на любой из них достаточно — бот откроется"
                     " полностью.")
        return "\n".join(lines) + more

    def _kb_join(self) -> dict:
        rows = []
        for code, name, url, _cid in self.channel_pair():
            if url:
                rows.append([{"text": f"📣 {name}", "url": url}])
        rows.append([{"text": "✅ Я подписался", "callback_data": "ch:check"}])
        return {"inline_keyboard": rows}

    async def _chat_member_status(self, cid: str, tg_id: int) -> Optional[bool]:
        """True — подписан, False — точно нет, None — проверить не удалось.

        Разница важна: «нет ответа» (бот не админ канала, сеть) не повод
        запирать человека, а вот честное «не участник» — повод.
        """
        if not cid or not tg_id:
            return None
        res = await self._call("getChatMember", {"chat_id": cid, "user_id": int(tg_id)})
        if not res:
            return None
        if not res.get("ok"):
            desc = str(res.get("description") or "").lower()
            # «chat not found» — это про канал, а не про человека: значит,
            # проверить подписку нельзя (бот не в канале), а не «не подписан».
            if ("user not found" in desc or "user_not_participant" in desc
                    or "not a member" in desc or "participant" in desc):
                return False
            if time.time() - self._ch_warn_at > 600:
                self._ch_warn_at = time.time()
                log.warning("getChatMember %s: %s — проверка канала недоступна,"
                            " пользователи пропускаются без неё",
                            cid, res.get("description") or "нет ответа")
            return None
        status = str(((res or {}).get("result") or {}).get("status") or "").lower()
        return status in ("creator", "administrator", "member", "restricted")

    async def _is_member(self, tg_id: int) -> bool:
        """Подписка на основной канал; None-ответ трактуем как «пропустить»."""
        cid = self.channel_chat_id()
        if not cid or not tg_id:
            return False
        until = self._ch_ok.get(int(tg_id), 0)
        if until > time.time():
            return True
        st = await self._chat_member_status(cid, int(tg_id))
        if st is None:
            return True
        if st:
            self._ch_ok[int(tg_id)] = time.time() + 180
        else:
            self._ch_ok.pop(int(tg_id), None)
        return st

    async def _is_member_any(self, tg_id: int) -> bool:
        """Подписки достаточно на ЛЮБОЙ из каналов: русский или английский."""
        uid = int(tg_id or 0)
        if not uid:
            return False
        if self._join_ok.get(uid, 0) > time.time():
            return True
        seen_any = False
        unknown = False
        for _code, _name, _url, cid in self.channel_pair():
            if not cid:
                continue
            seen_any = True
            st = await self._chat_member_status(cid, uid)
            if st is True:
                self._join_ok[uid] = time.time() + 180
                return True
            if st is None:
                unknown = True
        if not seen_any or unknown:
            # id каналов ещё не знаем или Telegram молчит — запирать не за что
            return True
        return False

    async def _ensure_channel(self, chat_id: int, user: dict,
                              message_id: Optional[int] = None,
                              extra: str = "") -> bool:
        """True — можно показывать меню. Без id каналов не блокируем."""
        if await self._is_member_any(int(user.get("tg_id") or 0)):
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
        code, cid = self.remember_channel_auto(chat.get("id"), chat.get("title") or "")
        title = _esc(chat.get("title") or cid)
        where = ("🇷🇺 русский" if code == "ru" else "🇬🇧 английский")
        await self.show_menu(
            chat_id,
            f"Канал привязан ({where}): <b>{title}</b>\n<code>{_esc(cid)}</code>\n\n"
            "Сводки уходят в оба канала сразу: русский текст — в русский,"
            " английский — в английский.\n"
            "У бота должно быть право «Публикация сообщений».",
            self._admin_kb(),
        )
        return True

    def remember_channel_auto(self, chat_id, title: str = "") -> tuple:
        """Какой это канал: русский или английский.

        Первый привязанный канал считается русским (он же основной), второй —
        английским. Так админу достаточно добавить бота в оба канала и
        переслать по посту из каждого: id подхватятся сами.
        """
        cid = str(chat_id).strip()
        ru = self.channel_chat_id()
        en = self.channel_chat_id_en()
        if ru and cid == ru:
            return ("ru", ru)
        if en and cid == en:
            return ("en", en)
        if self._channel_id_cfg and cid == self._channel_id_cfg:
            return ("ru", self.remember_channel(cid, title))
        if not ru:
            return ("ru", self.remember_channel(cid, title))
        return ("en", self.remember_channel_en(cid, title))

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
            self.remember_channel_auto(cid, chat.get("title") or "")
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

    # --- ИИ-шапка и контроль публикации -----------------------------------
    def ai_status(self) -> Dict[str, Any]:
        """Состояние ИИ для админки и /api/admin/overview."""
        ai = getattr(self, "ai", None)
        out: Dict[str, Any] = dict(ai.status()) if ai is not None else {"enabled": False}
        try:
            out["review"] = self._review_on()
        except Exception:
            out["review"] = False
        out["last_post"] = dict(self._ai_state or {})
        return out

    def _review_on(self) -> bool:
        """Контроль публикации: черновик у админа вместо поста в канал."""
        try:
            v = (self.store.get_setting("channel_digest_review") or "").strip().lower()
        except Exception:
            return False
        return v in ("1", "on", "true", "yes")

    def _set_review(self, on: bool, actor_id: Optional[int] = None) -> None:
        self.store.set_setting("channel_digest_review", "1" if on else "0",
                               actor_id=actor_id)

    async def _ai_headline(self, snap: dict, variant: int = 0,
                           lang: str = "ru") -> tuple:
        """(шапка или None, короткая пометка для админа).

        variant — номер поста: по нему ИИ выбирает новый акцент, чтобы
        подряд идущие сводки не начинались одинаково. lang="en" — отдельная
        английская шапка для второго канала (русские не повторяем).
        """
        ai = getattr(self, "ai", None)
        en = str(lang).startswith("en")
        if ai is None or not getattr(ai, "enabled", False):
            if not en:
                self._ai_state = {"provider": "", "ok": False, "reason": "ИИ не настроен"}
            return None, "ИИ не настроен — шапка из шаблонов"
        recent = self._ai_recent_en if en else self._ai_recent
        try:
            head = await ai.headline(snap, recent=list(recent),
                                     variant=int(variant or 0), lang=lang)
        except Exception as e:                      # сеть, лимиты, что угодно
            log.warning("ИИ-шапка: %s", e)
            head = None
        st = dict(ai.status().get("last") or {})
        if not en:
            self._ai_state = st
        if head:
            if en:
                self._ai_recent_en = (self._ai_recent_en + [head])[-8:]
            else:
                self._ai_recent = (self._ai_recent + [head])[-8:]
            note = f"ИИ: {st.get('provider') or '?'} · {st.get('ms') or 0} мс"
        else:
            note = (f"ИИ не ответил ({st.get('reason') or 'все сервисы'}) —"
                    " шапка из шаблонов")
        return head, note

    def _digest_fail(self, reason: str) -> bool:
        self._digest_err = reason
        log.warning("сводка: %s", reason)
        return False

    async def post_channel_digest(self, force: bool = False) -> bool:
        from channel_digest import (
            active_headlines, active_images, pick_image, render_post, render_top7,
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
        images = active_images(self.store)
        img = pick_image(n, images=images)
        # Русский пост — основной; английский уходит копией в свой канал.
        ai_head, ai_note = await self._ai_headline(snap, variant=n)
        caption = render_post(
            snap, n,
            headlines=active_headlines(self.store, hours),
            head_override=ai_head,
            site_url=self.site_url(),
            bot_url=self.bot_url(),
        )
        ai_head_en, _note_en = await self._ai_headline(snap, variant=n, lang="en")
        caption_en = render_post(
            snap, n,
            headlines=active_headlines(self.store, hours, lang="en"),
            head_override=ai_head_en or None,
            site_url=self.site_url(),
            bot_url=self.bot_url(),
            lang="en",
        )
        top = render_top7(snap.get("board"))
        top_en = render_top7(snap.get("board"), "en")
        posts = [
            {"lang": "ru", "cid": cid, "caption": caption, "top": top},
        ]
        cid_en = self.channel_chat_id_en()
        if cid_en:
            posts.append({"lang": "en", "cid": cid_en, "caption": caption_en,
                          "top": top_en})
        else:
            log.info("английский канал не привязан — пост только по-русски")
        if self._review_on():
            return await self._send_draft(posts, img, n, ai_note)
        return await self._publish_digest(posts, img, n)

    async def _publish_one(self, cid, caption: str, img, top: str = "",
                           lang: str = "ru") -> bool:
        """Пост в один канал: фото+подпись (или текст) и отдельно топ-7.

        Топ-7 по часам в подпись к фото не влезает (лимит 1024), поэтому он
        уходит следом отдельным сообщением — так весь стенд читается целиком.
        """
        markup = self.channel_link_kb(lang)
        ok = False
        if img and len(caption) <= 1024:
            ok = bool(await self.send_photo(cid, img, caption, markup))
        if not ok:
            ok = bool(await self.send(cid, caption, markup))
        if ok and top:
            sent = await self.send(cid, top)
            if not sent:
                log.warning("топ-7 не ушёл в канал %s", cid)
        return ok

    async def _publish_digest(self, posts, img, n: int) -> bool:
        """Отправка готового поста в каналы (русский и английский)."""
        delivered = 0
        for post in posts or []:
            cid = post.get("cid")
            if not cid:
                continue
            if await self._publish_one(cid, post.get("caption") or "", img,
                                       post.get("top") or "",
                                       post.get("lang") or "ru"):
                delivered += 1
        if delivered:
            self.store.set_setting("channel_digest_n", str(n + 1))
            self.store.set_setting("channel_digest_ts", str(int(time.time())))
            log.info("сводка n=%s ушла в каналы: %d", n, delivered)
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

    async def _send_draft(self, posts, img, n: int, note: str) -> bool:
        """Контроль публикации: показываем пост админу, в канал не отправляем."""
        admin = 0
        try:
            admins = self.store.admin_tg_ids() if self.store else []
            admin = int(admins[0]) if admins else 0
        except Exception:
            admin = 0
        if not admin:
            return self._digest_fail(
                "Контроль публикации включён, но у бота нет админа с Telegram. "
                "Выключите контроль в «Шаблоны канала» или привяжите Telegram админу")
        self._draft = {"posts": posts, "img": img, "n": int(n), "note": note}
        kb = {"inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": "d:pub"},
            {"text": "🔄 Перегенерировать", "callback_data": "d:regen"},
            {"text": "✖️ Отмена", "callback_data": "d:no"}]]}
        text = (f"<b>Черновик сводки</b>\n<i>{_esc(note)}</i>\n"
                "В каналы уйдёт только после «Опубликовать».")
        for post in posts or []:
            mark = "🇬🇧" if post.get("lang") == "en" else "🇷🇺"
            text += f"\n\n{mark} <b>{(post.get('cid') or '')}</b>\n" + (post.get("caption") or "")
        await self.send(admin, text, kb)
        # топ-7 по часам — тем же сообщением не влезает, шлём следом
        for post in posts or []:
            if post.get("top"):
                await self.send(admin, (post.get("top") or "")[:3900])
        log.info("сводка: черновик отправлен админу %s (n=%s)", admin, n)
        return True

    async def _on_draft_cb(self, chat_id, user: dict, data: str, message_id) -> None:
        """Кнопки под черновиком: опубликовать / перегенерировать / отменить."""
        d = dict(self._draft or {})
        if data == "d:regen":
            self._draft = None
            await self.reply(chat_id, "Готовлю новый вариант…", None,
                             message_id=message_id)
            await self.post_channel_digest(force=True)
            return
        if data == "d:no":
            self._draft = None
            await self.reply(chat_id, "Черновик отменён, в канал ничего не ушло.",
                             self._admin_kb(), message_id=message_id)
            return
        if data == "d:pub":
            if not d:
                await self.reply(chat_id, "Черновик уже неактуален — нажмите "
                                 "«Сводка в канал» ещё раз.",
                                 self._admin_kb(), message_id=message_id)
                return
            cid = self.channel_chat_id()
            ok = False
            if cid:
                posts = d.get("posts") or [{"lang": "ru", "cid": cid,
                                            "caption": d.get("caption") or "",
                                            "top": d.get("top") or ""}]
                ok = await self._publish_digest(posts, d.get("img"),
                                                int(d.get("n") or 0))
            self._draft = None
            await self.reply(chat_id, self._digest_result_text(ok), self._admin_kb(),
                             message_id=message_id)

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
        fails = 0
        while self.running:
            try:
                # 429 (flood control): молотить дальше нельзя — Telegram продлевает
                # запрет, и бот не видит кнопки. Ждём столько, сколько сказано.
                pause = self._retry_after_until - time.time()
                if pause > 0:
                    await asyncio.sleep(min(60.0, pause))
                res = await self._call("getUpdates", {
                    "offset": self._offset,
                    # в конфликт-режиме короткие опросы: быстрее перехватываем
                    # апдейты в окне, пока «чужой» процесс их не забрал
                    "timeout": 5 if self._conflict_mode else 50,
                    "allowed_updates": ["message", "callback_query", "my_chat_member"],
                })
                if not res or not res.get("ok"):
                    sleep_s = await self._poll_fail_step(res)
                    if not self.running:
                        break
                    fails += 1
                    await asyncio.sleep(sleep_s)
                    continue
                self._last_ok_at = time.time()
                if fails:
                    log.info("getUpdates снова в строю после %d сбоев — "
                             "кнопки и команды опять отвечают", fails)
                    fails = 0
                    self._poll_fails = 0
                if self._conflict_mode:
                    self._conflict_ok_streak += 1
                    if self._conflict_ok_streak >= 3:
                        self._conflict_mode = False
                        self._conflict_ok_streak = 0
                        log.info("конфликт-режим снят: getUpdates стабилен, "
                                 "возвращаем обычный опрос")
                for upd in res.get("result") or []:
                    self._offset = int(upd["update_id"]) + 1
                    self._last_update_at = time.time()
                    self._updates_seen += 1
                    try:
                        await self._on_update(upd)
                    except Exception as e:
                        # С трейсбеком: без него в журнале видно только текст
                        # ошибки, и «кнопка молчит» неоткуда отладить.
                        log.exception("update %s: %s", upd.get("update_id"), e)
                # offset на диск: чтобы после перезапуска не разбирать заново
                # старую очередь апдейтов (иначе первые минуты бот отвечает
                # вчерашним нажатиям, а свежие кнопки ждут очереди).
                if self._offset and self._offset != self._saved_offset:
                    try:
                        self.store.set_setting(f"bot_offset:{self.bot_id}",
                                               str(self._offset))
                        self._saved_offset = self._offset
                    except Exception as e:
                        log.debug("offset не сохранён: %s", e)
                if self._conflict_mode:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("poll: %s", e)
                await asyncio.sleep(3)

    def _watchdog_problem(self) -> str:
        """Пустая строка — опрос жив; иначе причина, по которой его надо пересоздать."""
        task = self._task
        if task is None or task.done():
            exc = None
            if task is not None:
                try:
                    exc = task.exception()
                except (asyncio.CancelledError, Exception):
                    exc = None
            return f"задача опроса остановилась ({exc!r})"
        if self._last_ok_at and (time.time() - self._last_ok_at) > POLL_STALE_SEC:
            return ("от Telegram не было успешных getUpdates "
                    f"{time.time() - self._last_ok_at:.0f} с")
        return ""

    async def _watchdog_restart(self, reason: str) -> None:
        """Пересоздаёт задачу опроса и говорит об этом админам.

        Без этого «running: true, fails: 0, а кнопки не отвечают» лечится
        только ручным `systemctl restart`.
        """
        log.error("сторож бота: %s — пересоздаю опрос", reason)
        self._watchdog_restarts += 1
        self._poll_fails = 0
        self._conflict_mode = False
        self._conflict_ok_streak = 0
        self._retry_after_until = 0.0
        self._last_ok_at = time.time()
        old = self._task
        if old is not None and not old.done():
            old.cancel()
        self._task = asyncio.create_task(self._poll(), name="tg-bot")
        text = ("♻️ <b>Опрос Telegram перезапущен</b>\n"
                f"{_esc(reason)}. "
                f"Перезапусков сторожем: {self._watchdog_restarts}.\n"
                "Кнопки и команды снова должны отвечать. Если это повторяется, "
                "смотрите журнал: <code>journalctl -u licvid -n 200</code>")
        for tg_id in self._admin_tg_ids():
            try:
                await self.send(tg_id, text, parse="HTML")
            except Exception as e:
                log.warning("сторож: не смог предупредить %s: %s", tg_id, e)

    async def _watchdog(self) -> None:
        """Сторож опроса: зависшую или упавшую задачу getUpdates пересоздаём.

        «Бот отвечает какое-то время после перезапуска, потом молчит, а
        /api/health показывает running: true и fails: 0» — ровно этот случай:
        флаг жив, а опрос стоит.
        """
        while self.running:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            if not self.running:
                break
            problem = self._watchdog_problem()
            if problem:
                await self._watchdog_restart(problem)

    def poll_status(self) -> Dict[str, Any]:
        """Срез состояния опроса getUpdates для /api/health.

        conflict=true — токен параллельно дёргает чужой процесс: команды
        и кнопки могут приходить ему, а не нам.
        task_alive/poll_ok_sec/last_update_sec отвечают на вопрос «бот правда
        живой?»: running — это флаг, выставленный при старте, он ничего не
        говорит ни о задаче опроса, ни о том, доходят ли ответы до Telegram.
        """
        now = time.time()
        return {
            "enabled": bool(self.token),
            "running": bool(self.running),
            "fails": self._poll_fails,
            "conflict": bool(self._conflict_mode),
            "offset": int(self._offset),
            "task_alive": bool(self._task and not self._task.done()),
            "poll_ok_sec": (round(now - self._last_ok_at, 1)
                            if self._last_ok_at else None),
            "last_update_sec": (round(now - self._last_update_at, 1)
                                if self._last_update_at else None),
            "updates_seen": int(self._updates_seen),
            "send_fails": int(self._send_fails),
            "last_err": (self._last_tg_err or "")[:160],
            "watchdog_restarts": int(self._watchdog_restarts),
            "waits": (len(self._wait_alert) + len(self._wait_tpl)
                      + len(self._wait_broadcast)),
        }

    def poll_line(self) -> str:
        """Одна строка о самом боте — видно прямо в чате, без curl."""
        st = self.poll_status()
        if not st["enabled"]:
            return "🤖 Бот: токен не задан"
        if not st["running"] or not st["task_alive"]:
            return ("🤖 Бот: ⚠️ опрос не идёт "
                    f"(running={st['running']}, задача={st['task_alive']}) — "
                    "кнопки не отвечают, нужен перезапуск сервиса")
        ok = st["poll_ok_sec"]
        bits = []
        if ok is not None and ok > POLL_STALE_SEC:
            bits.append(f"⚠️ опрос завис {ok:.0f} с")
        else:
            bits.append(f"опрос ок ({'—' if ok is None else f'{ok:.0f} с'})")
        fresh = st["last_update_sec"]
        bits.append("апдейт " + ("—" if fresh is None else f"{fresh:.0f} с назад"))
        if st["send_fails"]:
            bits.append(f"отказов Telegram {st['send_fails']}: "
                        f"{_esc(st['last_err'][:60])}")
        if st["conflict"]:
            bits.append("409: токен тянет кто-то ещё")
        if st["watchdog_restarts"]:
            bits.append(f"перезапусков сторожем {st['watchdog_restarts']}")
        return "🤖 Бот: " + " · ".join(bits)

    def poll_text(self) -> str:
        """Подробный разбор состояния бота (команда /bot)."""
        st = self.poll_status()
        def ago(v):
            return "—" if v is None else f"{v:.0f} с назад"
        lines = [
            "<b>🤖 Состояние бота</b>",
            f"опрос: {'идёт' if st['running'] and st['task_alive'] else 'НЕ идёт'}"
            f" · задача опроса {'жива' if st['task_alive'] else 'мертва'}"
            + (f" · завис {st['poll_ok_sec']:.0f} с" if st['poll_ok_sec'] and
               st['poll_ok_sec'] > POLL_STALE_SEC else ""),
            f"успешный getUpdates: {ago(st['poll_ok_sec'])}",
            f"последний апдейт: {ago(st['last_update_sec'])}"
            f" · обработано апдейтов: {st['updates_seen']}",
            f"сбоев getUpdates подряд: {st['fails']}"
            f" · 409-конфликт: {'да' if st['conflict'] else 'нет'}",
            f"отказов Telegram (отправка/правка): {st['send_fails']}"
            + (f"\nпоследняя: <code>{_esc(st['last_err'])}</code>"
               if st["last_err"] else ""),
            f"перезапусков сторожем: {st['watchdog_restarts']}"
            f" · чатов с меню: {len(self._menu_msg)}"
            f" · ожиданий ввода: {st['waits']}",
        ]
        return "\n".join(lines) + self.site_footer()

    async def _poll_fail_step(self, res: Optional[dict]) -> float:
        """Классифицирует ошибку getUpdates, возвращает секунды до повтора.

        Главное — 409 Conflict: этот же токен опрашивает другой процесс
        (старый бот, вторая копия сервиса) либо у бота включён вебхук.
        Уведомления при этом отправляются свободно, а кнопки и команды
        бот не видит. Один раз сообщаем админам, чтобы причину было видно.
        """
        desc = str((res or {}).get("description") or "")
        low = desc.lower()
        try:
            code = int((res or {}).get("error_code") or 0)
        except (TypeError, ValueError):
            code = 0
        self._poll_fails += 1
        if code == 401 or "unauthorized" in low:
            log.error("Telegram отверг токен бота (401): %s — "
                      "бот выключен, проверьте LIQSCOPE_BOT_TOKEN", desc or "нет описания")
            self.running = False
            return 0.0
        if code == 409 or "conflict" in low:
            if "webhook" in low:
                reason = "у бота включён вебхук"
            else:
                reason = ("этот токен параллельно опрашивает другой процесс — "
                          "старый бот или вторая копия сервиса")
            self._conflict_mode = True
            self._conflict_ok_streak = 0
            if time.time() - self._conflict_warned_at >= 1800:
                self._conflict_warned_at = time.time()
                log.error("getUpdates 409: %s. Бот отправляет уведомления, но НЕ видит "
                          "кнопки и команды. Уберите конфликтующий процесс (один токен — "
                          "ровно один опрашиватель) и перезапустите сервис.", reason)
                await self._notify_conflict(reason)
            elif self._poll_fails % 30 == 0:
                log.warning("конфликт getUpdates продолжается (%d сбоев): %s",
                            self._poll_fails, desc)
            return 0.5
        if self._poll_fails % 10 == 1:
            log.warning("getUpdates недоступен (%d сбоев подряд): %s",
                        self._poll_fails, desc or "нет ответа")
        return 2.0

    async def _notify_conflict(self, reason: str) -> None:
        """Предупреждаем админов: почему бот не отвечает на команды.

        Повторяем каждые ~30 минут, пока конфликт жив, — одна разовая
        нотификация теряется, а проблема чинится только на сервере.
        """
        text = (
            "🚨 <b>Конфликт бота</b>\n"
            "Я отправляю сигналы, но не вижу кнопки и команды: "
            f"{reason}.\n\n"
            "Признак призрачного процесса: сигналы приходят даже после того, "
            "как на сайте выключены ВСЕ уведомления — шлёт их старая копия бота, "
            "которая всё ещё висит на сервере.\n"
            "Один токен может опрашивать только один процесс.\n"
            "На сервере:\n"
            "• <code>ps aux | grep -E \"main\\.py|tg_bot|uvicorn|server:app\"</code> — "
            "ищем дубли (особенно запущенные давно);\n"
            "• <code>systemctl list-units | grep -i liq</code> — нет ли второго "
            "сервиса (старый liqscope из /root/LiqScope занимает тот же порт 8000);\n"
            "• убили лишнее — <code>systemctl restart licvid</code>, меню "
            "оживёт.\n"
            "Напоминаю каждые ~30 минут, пока конфликт не исчезнет."
        )
        for tg_id in self._admin_tg_ids():
            try:
                await self.send(tg_id, text, parse="HTML")
            except Exception as e:
                log.warning("conflict notify %s: %s", tg_id, e)

    async def _on_update(self, upd: dict) -> None:
        await self._route_message(upd)
        # «Плашка» от кнопки или команды: удаляем эхо пользователя, чтобы в
        # чате не копился мусор — живым остаётся только сообщение-меню.
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        message_id = msg.get("message_id")
        if (text and message_id and chat_id
                and str(chat.get("type") or "private") == "private"
                and not (msg.get("from") or {}).get("is_bot")):
            await self._call("deleteMessage",
                             {"chat_id": chat_id, "message_id": int(message_id)})

    async def _route_message(self, upd: dict) -> None:
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
        until = float(self._wait_email.get(tg_id) or 0)
        if until and until < time.time():
            self._wait_email.pop(tg_id, None)
            until = 0
        if until:
            if text.startswith("/"):
                self._wait_email.pop(tg_id, None)
                if text.startswith("/cancel"):
                    await self.show_menu(chat_id, "Отмена.", self._cabinet_kb(user))
                    return
            else:
                await self._handle_email_input(chat_id, user, text)
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
            if nav == "help":
                body, kb = self._screen(user, "help")
                await self.show_menu(chat_id, body, kb)
                return
            if nav == "admin":
                await self._cmd_admin(chat_id, user)
                return
            if nav == "channel":
                link = f'<a href="{_esc(self.channel_url)}">📣 канал LiqScope</a>'
                await self.show_menu(
                    chat_id,
                    "<b>📣 Канал</b>\n"
                    "Сводки ликвидаций, OI и CVD раз в 4 часа.\n"
                    f"{link} — подписывайтесь, чтобы не пропустить."
                    + self.site_footer(),
                    self._reply_kb(user))
                return
            if nav == "al":
                await self.show_menu(chat_id, self._alert_text(user), self._alert_kb(user))
                return
            try:
                body, kb = self._screen(user, nav)
            except Exception as e:
                # «Кнопка молчит» не должна быть немой: если экран упал,
                # пользователь видит причину, а не тишину.
                log.exception("экран %s: %s", nav, e)
                await self.show_menu(
                    chat_id,
                    "⚠️ Экран не открылся: <code>" + _esc(str(e)[:120])
                    + "</code>\nОшибка записана в журнал сервиса.",
                    self._reply_kb(user))
                return
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
        elif text.startswith("/mail"):
            body, kb = self._screen(user, "mail")
            await self.show_menu(chat_id, body, kb)
        elif text.startswith("/stats"):
            await self.show_menu(chat_id, self._stats_text(), self._reply_kb(user))
        elif text.startswith("/status") or text.startswith("/health"):
            await self.show_menu(chat_id, self._health_text(), self._reply_kb(user))
        elif text.startswith("/bot"):
            await self.show_menu(chat_id, self.poll_text(), self._reply_kb(user))
        elif text.startswith("/liq"):
            await self.show_menu(chat_id, self._liq_text(), self._reply_kb(user))
        elif text.startswith("/services"):
            await self.show_menu(chat_id, self._services_text(user), self._services_kb(user))
        elif text.startswith("/alerts"):
            await self.show_menu(chat_id, self._alert_text(user), self._alert_kb(user))
        elif text.startswith("/terminal"):
            await self.show_menu(chat_id, self._terminal_text(), self._terminal_kb())
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
            self._wait_email.pop(tg_id, None)
        # Сначала снимаем «часики»: если answer уйдёт после edit или с
        # пустым text, клиент залипает и следующие кнопки не нажимаются.
        await self.answer_cb(cb["id"])
        try:
            if data == "ch:check":
                if await self._is_member_any(tg_id):
                    text, markup = self._home_text(user), self._reply_kb(user)
                    ok = await self.reply(chat_id, text, markup, message_id=message_id)
                else:
                    extra = ("Telegram ещё не видит подписку. Откройте любой из"
                             " каналов (LiqScopeRUS или LiqScopeEng), затем нажмите"
                             " «Я подписался» ещё раз.")
                    ok = await self.show_menu(chat_id, self._join_text(extra),
                                              self._kb_join(), old_id=message_id)
                if not ok:
                    log.warning("меню не обновилось data=%s chat=%s msg=%s",
                                data, chat_id, message_id)
                return
            if data == "a:vmail":
                self._wait_email[int(tg_id or 0)] = time.time() + 900
                await self.show_menu(
                    chat_id,
                    "Пришлите адрес почты следующим сообщением.\n"
                    "На него уйдёт письмо со ссылкой — по ней почта привяжется"
                    " к этому аккаунту, и вы сможете входить на сайт.\n\n"
                    "/cancel — отмена.",
                    self._cabinet_kb(user))
                return
            if data == "a:vchk":
                fresh = self.store.get_user(int(user["id"])) or user
                email = (fresh.get("email") or "").strip()
                if email and fresh.get("email_verified"):
                    await self.show_menu(
                        chat_id,
                        f"Готово: <code>{_esc(email)}</code> подтверждена ✅\n"
                        "Теперь можно входить на сайт и этим адресом, и Telegram.",
                        self._cabinet_kb(fresh))
                elif email:
                    await self.show_menu(
                        chat_id,
                        f"Почта <code>{_esc(email)}</code> пока не подтверждена.\n"
                        "Откройте письмо от LiqScope и нажмите в нём кнопку —"
                        " ссылка живёт 24 часа.",
                        self._cabinet_kb(fresh))
                else:
                    await self.show_menu(chat_id, "Почта ещё не привязана.",
                                         self._cabinet_kb(fresh))
                return
            if data == "a:digest" and user.get("is_admin"):
                posted = await self.post_channel_digest(force=True)
                await self.reply(chat_id, self._digest_result_text(posted),
                                 self._admin_kb(), message_id=message_id)
                return
            if user.get("is_admin") and data.startswith("d:"):
                await self._on_draft_cb(chat_id, user, data, message_id)
                return
            if user.get("is_admin") and (
                    data == "a:tpl" or data.startswith("a:th") or data.startswith("a:tp")
                    or data in ("a:rv", "a:ai")):
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
            log.exception("cb %s: %s", data, e)
            # Раньше ошибка экрана оставалась только в журнале, а для
            # пользователя кнопка просто «ничего не делала».
            try:
                await self.send(
                    chat_id,
                    "⚠️ Экран не открылся: <code>" + _esc(str(e)[:120])
                    + "</code>\nОшибка записана в журнал сервиса.",
                    parse="HTML")
            except Exception:
                pass

    def _screen(self, user: dict, data: str) -> tuple:
        """Текст и клавиатура экрана."""
        if data in ("menu", "back", "nav:home", "home", "help", ""):
            if data == "help":
                return self._commands_text(user), self._commands_kb(user)
            return self._home_text(user), self._reply_kb(user)
        if data == "cabinet":
            return self._cabinet_text(user), self._reply_kb(user)
        if data == "mail":
            # Напоминание из кабинета: ждём адрес почты следующим сообщением
            self._wait_email[int(user.get("tg_id") or 0)] = time.time() + 900
            return self._mail_screen(user), self._cabinet_kb(user)
        if data == "stats":
            return self._stats_text(), self._reply_kb(user)
        if data == "health":
            return self._health_text(), self._reply_kb(user)
        if data == "a:health" and user.get("is_admin"):
            return self._health_text(), self._kb_back("nav:admin")
        if data == "liq":
            return self._liq_text(), self._reply_kb(user)
        if data == "terminal":
            # сразу на сайт: большая URL-кнопка, сообщение уходит без звука
            return self._terminal_text(), self._terminal_kb()
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
        if payload.startswith("link_"):
            # Кабинет попросил привязать Telegram к аккаунту с почтой:
            # отдельная регистрация не нужна, аккаунт тот же.
            nonce = payload[5:]
            r = self.store.confirm_tg_link(nonce, user)
            if r.get("ok"):
                linked = r.get("user") or {}
                extra = ""
                if linked.get("email"):
                    extra = f"\nПочта аккаунта: <code>{_esc(linked['email'])}</code>"
                if r.get("merged"):
                    extra += ("\nВесь профиль из Telegram перенесён в этот аккаунт"
                              " — история алертов и подписки на месте.")
                await self.show_menu(
                    chat_id,
                    f"✅ Telegram привязан к аккаунту LiqScope, {_esc(user['display_name'])}."
                    f"{extra}\n"
                    "Теперь сигналы алертов придут сюда, а вход в кабинет — "
                    "по почте или этим же Telegram.\n"
                    f"🌍 {self.site_a('открыть кабинет на сайте', '/cabinet')}",
                    self._reply_kb(user),
                )
                return
            err = r.get("error")
            if err == "taken":
                msg = ("Этот Telegram уже привязан к аккаунту с почтой — "
                       "сначала отвяжите его в кабинете того аккаунта.")
            elif err in ("expired", "used", "unknown"):
                msg = ("Ссылка привязки устарела. Нажмите «Привязать Telegram» "
                       "в кабинете ещё раз.")
            else:
                msg = "Не получилось привязать Telegram. Попробуйте ещё раз из кабинета."
            await self.show_menu(chat_id, msg, self._reply_kb(user))
            return
        if payload.startswith("login_"):
            nonce = payload[6:]
            if self.store.confirm_nonce(nonce, user["id"]):
                if not await self._ensure_channel(chat_id, user):
                    return
                await self.show_menu(
                    chat_id,
                    f"Вход подтверждён, {_esc(user['display_name'])}.\n"
                    "Вернитесь во вкладку браузера — кабинет откроется сам.\n"
                    f"🌍 {self.site_a('открыть кабинет на сайте', '/cabinet')}",
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
        extra = (f"\n🌍 {self.site_a('кабинет на сайте', '/cabinet')} · "
                 f"{self.site_a('терминал', '/terminal')}")
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
        lines.append("")
        ai = getattr(self, "ai", None)
        st = ai.status() if ai is not None else {"enabled": False}
        if st.get("enabled"):
            chain = " → ".join(str(x.get("name")) for x in (st.get("providers") or []))
            last = st.get("last") or {}
            tail = (f"последняя: {last.get('provider')} · {last.get('ms')} мс"
                    if last.get("ok") else f"последняя: ошибка — {last.get('reason') or '—'}")
            lines.append(f"🤖 ИИ-шапка: включена · {_esc(chain)}")
            lines.append(f"   {_esc(tail)}")
        else:
            lines.append("🤖 ИИ-шапка: выключена (нет ключей) — шапки из шаблонов")
        lines.append("🧪 Контроль постов: " +
                     ("включён — черновик приходит сюда" if self._review_on()
                      else "выключен — пост уходит сразу в канал"))
        return "\n".join(lines)

    def _tpl_kb(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "➕ Шапка", "callback_data": "a:th+"},
             {"text": "➕ Фото", "callback_data": "a:tp+"}],
            [{"text": "🗑 Удалить шапку", "callback_data": "a:th-"},
             {"text": "🗑 Удалить фото", "callback_data": "a:tp-"}],
            [{"text": f"🧪 Контроль: {'вкл' if self._review_on() else 'выкл'}",
              "callback_data": "a:rv"},
             {"text": "🤖 Проверить ИИ", "callback_data": "a:ai"}],
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
        if data == "a:rv":
            on = not self._review_on()
            self._set_review(on, actor_id=user.get("id"))
            await self.reply(chat_id, self._tpl_home_text(), self._tpl_kb(),
                             message_id=message_id)
            return
        if data == "a:ai":
            snap: dict = {}
            try:
                raw = self.digest_fn() if self.digest_fn else {}
                if asyncio.iscoroutine(raw):
                    raw = await raw
                snap = raw if isinstance(raw, dict) else {}
            except Exception as e:
                log.warning("проверка ИИ: снимок не собрался: %s", e)
            n = 0
            try:
                n = int(self.store.get_setting("channel_digest_n") or 0)
            except (TypeError, ValueError):
                n = 0
            head, note = await self._ai_headline(snap, variant=n)
            body = (f"🤖 <b>Проверка ИИ</b>\n<i>{_esc(note)}</i>\n\n"
                    + (f"<b>{_esc(head)}</b>" if head
                       else "Шапка: <i>не получилось, будет из шаблонов</i>"))
            await self.reply(chat_id, body, self._tpl_kb(), message_id=message_id)
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

    def _cabinet_kb(self, user: dict) -> dict:
        """Кнопки кабинета: напоминание про почту и возврат в меню."""
        rows = []
        email = (user.get("email") or "").strip()
        if not email or not user.get("email_verified"):
            rows.append([{"text": "✉️ Подтвердить почту",
                          "callback_data": "a:vmail"}])
        if email and not user.get("email_verified"):
            rows.append([{"text": "✅ Я подтвердил почту",
                          "callback_data": "a:vchk"}])
        rows.append([{"text": "← В меню", "callback_data": "nav:home"}])
        return {"inline_keyboard": rows}

    def _commands_kb(self, user: dict) -> dict:
        """Меню по кнопке «☰ Меню»: команды, которые не надо набирать руками."""
        admin = bool(user.get("is_admin"))
        rows = [
            [{"text": "🏠 /start", "callback_data": "nav:home"}],
            [{"text": "👤 Кабинет", "callback_data": "cabinet"},
             {"text": "⚡ Терминал", "callback_data": "terminal"}],
            [{"text": "📊 Статистика", "callback_data": "stats"},
             {"text": "🩺 Биржи", "callback_data": "health"}],
            [{"text": "🛠 Сервисы", "callback_data": "services"},
             {"text": "📰 Лента", "callback_data": "liq"}],
            [{"text": "🔔 Алерты", "callback_data": "al"},
             {"text": "📣 Канал", "callback_data": "channel"}],
            [{"text": "✉️ Подтвердить почту", "callback_data": "a:vmail"}],
        ]
        if admin:
            rows.append([{"text": "★ Админка", "callback_data": "nav:admin"}])
        return {"inline_keyboard": rows}

    def _commands_text(self, user: dict) -> str:
        return (
            "<b>☰ Меню LiqScope</b>\n"
            "Всё то же, что в панели внизу, — кнопками. Ничего набирать"
            " руками не нужно.\n\n"
            "/start — эта панель заново, /help — полный список команд."
            + self.site_footer()
        )


    def _mail_screen(self, user: dict) -> str:
        email = (user.get("email") or "").strip()
        head = "<b>✉️ Подтверждение почты</b>\n"
        if email and user.get("email_verified"):
            return (head + f"Почта <code>{_esc(email)}</code> уже подтверждена ✅\n"
                    "Она работает и на сайте, и здесь." + self.site_footer())
        lines = [
            head.rstrip(),
            "Почта — основной вход на сайт: по ней приходит ссылка для входа"
            " и сброс пароля.",
        ]
        if email:
            lines += [
                f"Сейчас привязана <code>{_esc(email)}</code>, но она"
                " <b>не подтверждена</b> — вход на сайт закрыт.",
                "Пришлите адрес ещё раз, если нужно письмо с новой ссылкой.",
            ]
        else:
            lines.append("Сейчас почта к аккаунту не привязана.")
        lines.append("Пришлите адрес следующим сообщением — отправлю письмо"
                     " со ссылкой. /cancel — отмена.")
        return "\n".join(lines) + self.site_footer()

    def _mail_left(self, tg_id: int, email: str, wait: float = 90.0) -> int:
        """Сколько секунд ждать до следующего письма (0 — можно слать)."""
        key = f"{int(tg_id)}:{email}"
        last = float(self._mail_at.get(key) or 0)
        left = int(wait - (time.time() - last)) if last else 0
        if left > 0:
            return left
        self._mail_at[key] = time.time()
        return 0

    def _mail_send(self, kind: str, to: str, token: str, name: str = "") -> bool:
        """Письмо из бота: mailer тот же, что у сайта (server.py подкладывает)."""
        m = self.mailer
        if not m or not getattr(m, "enabled", False):
            log.warning("Бот: почта не настроена — письмо «%s» для %s не отправлено",
                        kind, to)
            return False
        try:
            if kind == "verify":
                return bool(m.send_verify(to, token, name=name))
            if kind == "attach":
                return bool(m.send_tg_attach(to, token, tg_name=name))
        except Exception as e:
            log.warning("Бот: письмо «%s» для %s не ушло: %s", kind, to, e)
        return False

    async def _handle_email_input(self, chat_id: int, user: dict, text: str) -> None:
        """Человек прислал адрес почты из кабинета бота."""
        from accounts import normalize_email, valid_email

        self._wait_email.pop(int(user.get("tg_id") or 0), None)
        email = normalize_email(text)
        if not valid_email(email):
            self._wait_email[int(user.get("tg_id") or 0)] = time.time() + 900
            await self.show_menu(
                chat_id,
                "Это не похоже на адрес. Пришлите почту ещё раз,"
                " например <code>name@mail.ru</code>.\n/cancel — отмена.",
                self._cabinet_kb(user))
            return
        tg_id = int(user.get("tg_id") or 0)
        left = self._mail_left(tg_id, email)
        if left:
            await self.show_menu(
                chat_id,
                f"Письмо уже отправил — подождите {left} с и проверьте ящик"
                f" (и папку «Спам»).",
                self._cabinet_kb(user))
            return
        me = self.store.get_user(int(user["id"])) or user
        if (me.get("email") or "") == email and me.get("email_verified"):
            await self.show_menu(chat_id, f"Почта <code>{_esc(email)}</code>"
                                          " уже подтверждена ✅",
                                 self._cabinet_kb(me))
            return
        att = self.store.attach_email(int(me["id"]), email)
        if att.get("ok"):
            # адрес свободен или остался от незавершённой регистрации —
            # это тот же человек, просто просим подтвердить почту
            token = self.store.new_email_token(int(att["user"]["id"]), "verify", email=email)
            sent = self._mail_send("verify", email, token,
                                   name=att["user"].get("first_name") or "")
            if not sent:
                await self.show_menu(
                    chat_id,
                    "Почта не ушла: отправка писем на сервере не настроена."
                    " Адрес сохранил, но подтвердить его пока нельзя.",
                    self._cabinet_kb(att["user"]))
                return
            fresh = self.store.get_user(int(att["user"]["id"])) or att["user"]
            await self.show_menu(
                chat_id,
                f"Письмо ушло на <code>{_esc(email)}</code>.\n"
                "Откройте ссылку из письма — и почта станет входом на сайт.\n"
                "Пока адрес не подтверждён, вход на сайте закрыт.",
                self._cabinet_kb(fresh))
            return
        if att.get("error") != "taken":
            await self.show_menu(chat_id, "Не получилось привязать адрес — попробуйте позже.",
                                 self._cabinet_kb(me))
            return
        # Адрес уже подтверждён в кабинете на сайте. Чтобы чужой человек не забрал
        # его себе, привязку подтверждает владелец почты — письмом.
        other = self.store.get_user_by_email(email)
        if not other:
            await self.show_menu(chat_id, "Не получилось привязать адрес — попробуйте позже.",
                                 self._cabinet_kb(me))
            return
        token = self.store.new_tg_attach(int(other["id"]), {"tg_id": tg_id,
                                                           "username": me.get("username") or "",
                                                           "first_name": me.get("first_name") or "",
                                                           "last_name": me.get("last_name") or ""})
        sent = self._mail_send("attach", email, token,
                               name=me.get("username") or me.get("first_name") or "Telegram")
        if not sent:
            await self.show_menu(
                chat_id,
                "Почта не ушла: отправка писем на сервере не настроена.",
                self._cabinet_kb(me))
            return
        await self.show_menu(
            chat_id,
            f"На <code>{_esc(email)}</code> отправлено письмо с подтверждением.\n"
            "Нажмите в нём «Привязать Telegram» — и этот Telegram станет вашим"
            " входом в кабинет на сайте.\n"
            "Ссылка живёт 2 часа.",
            self._cabinet_kb(me))

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
            "Выберите раздел — кнопки внизу экрана.\n"
            + gate_line()
            + self.site_footer()
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
            "/bot — состояние опроса бота",
        ]
        email = (user.get("email") or "").strip()
        if not email or not user.get("email_verified"):
            lines.append("/mail — привязать и подтвердить почту"
                         " (основной вход на сайт)")
        if user.get("is_admin"):
            lines += [
                "",
                "<b>Админ</b>",
                "/admin /users /visits",
                "/broadcast текст — рассылка всем",
                "/digest — сводка ликвидаций в канал",
            ]
        return "\n".join(lines) + self.site_footer()

    def _cabinet_text(self, user: dict) -> str:
        un = f"@{_esc(user['username'])}" if user["username"] else "—"
        have = self.store.user_service_slugs(user["id"])
        svc = ", ".join(have) if have else "пока не выбраны"
        role = "👑 администратор" if user["is_admin"] else "👤 пользователь"
        lines = [
            "<b>👤 Кабинет</b>",
            f"{_esc(user['display_name'])} · {un}",
        ]
        # Основной вход — почта, Telegram может быть и не привязан
        email = (user.get("email") or "").strip()
        if email:
            mark = " ✅" if user.get("email_verified") else " (не подтверждена)"
            lines.append(f"Почта: <code>{_esc(email)}</code>{mark}")
        if not email:
            lines.append("Почта: <b>не привязана</b> — входить на сайт нечем,"
                         " кроме Telegram")
        elif not user.get("email_verified"):
            lines.append("⚠️ Почта не подтверждена — вход на сайт закрыт,"
                         " пока не перейдёте по ссылке из письма")
        if user.get("tg_id"):
            lines.append(f"Telegram ID: <code>{user['tg_id']}</code>")
        lines += [
            f"Роль: {role}",
            f"Сервисы: {_esc(svc)}",
            f"🌍 {self.site_a('кабинет на сайте', '/cabinet')}",
        ]
        return "\n".join(lines) + self.site_footer()

    def _terminal_text(self) -> str:
        return (
            "<b>⚡ Терминал</b>\n"
            "Живой поток ликвидаций с бирж: лента, свечи, кластеры.\n"
            f"{self.site_a('liqscope.online/terminal', '/terminal')}\n\n"
            "Кнопка ниже откроет его сразу, без звука."
            + self.site_footer()
        )

    def _terminal_kb(self) -> dict:
        # URL-кнопка ведёт на терминал напрямую; сообщение шлётся без звука
        return {"inline_keyboard": [
            [{"text": "⚡ Открыть терминал", "url": self.terminal_url()}],
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
            f"<b>📊 Рынок · 24ч</b>\n"
            f"💥 Ликвидации: <code>{usd(st.get('total_usd_24h'))}</code>\n"
            f"🔴 Лонги <code>{usd(st.get('longs_usd_24h'))}</code> · "
            f"🟢 шорты <code>{usd(st.get('shorts_usd_24h'))}</code>\n"
            f"⏱ За час: <code>{usd(st.get('total_usd_1h'))}</code>\n"
            f"🏆 Лидеры: {_esc(top_s)}"
            + self.site_footer()
        )

    def _health_text(self) -> str:
        h = self.health_fn() or {}
        srcs = h.get("sources") or {}
        skip = {"prices", "ticks", "oxa"}
        rows: List[str] = []
        live = 0
        total = 0
        events_total = 0
        for name, s in sorted(srcs.items()):
            if name in skip or not isinstance(s, dict):
                continue
            total += 1
            ev = int(s.get("events") or 0)
            events_total += ev
            if s.get("connected"):
                live += 1
                fresh = ""
                sec = s.get("seconds_since_event")
                if isinstance(sec, (int, float)):
                    fresh = f" · {self.ago_label(sec)}"
                rows.append(
                    f"🟢 <b>{ex_link(name, fallback=self.ex_name(name))}</b> · "
                    f"{self.fmt_int(ev)} событий{fresh}")
            else:
                err = _esc(str(s.get("last_error") or "нет связи")[:42])
                rows.append(f"🔴 <b>{ex_link(name, fallback=self.ex_name(name))}</b>"
                            f" · {err}")
        lines = ["<b>🩺 Биржи · эфир</b>", ""]
        lines.extend(rows or ["сервер ещё собирает источники"])
        lines.append("")
        lines.append(
            f"📡 В эфире <b>{live}/{total or '—'}</b> · "
            f"событий в памяти: {self.fmt_int(events_total)} · "
            f"зрителей WS: {self.ws_clients_fn()}")
        # Строка о самом боте: по ней видно «молчит, потому что не опрашивает»
        # прямо из чата, без curl к /api/health.
        lines.append("")
        lines.append(self.poll_line())
        return "\n".join(lines) + self.site_footer()

    def _liq_line(self, x: dict) -> str:
        """Одна строка ленты: время, монета, биржа, сумма, сторона."""
        try:
            tm = time.strftime("%H:%M:%S",
                               time.localtime(float(x.get("timestamp") or 0)))
        except (TypeError, ValueError, OSError):
            tm = "--:--:--"
        sym = str(x.get("symbol") or "?").replace("_", "/")
        mark = "🔴" if x.get("side") == "SELL" else "🟢"
        return (f"{tm} {mark} {_esc(sym)}  "
                f"{self.fmt_usd(x.get('usd'))}  {_esc(self.ex_name(x.get('exchange')))}")

    def _liq_text(self, limit: int = 3600) -> str:
        """Лента: максимум событий, которые влезают в сообщение Telegram."""
        rows = list(self.liqs_fn() or [])
        if not rows:
            return ("<b>📰 Лента</b>\n"
                    "Пока нет событий в памяти — биржевые потоки прогреваются."
                    + self.site_footer())
        rows.reverse()                                   # новые сверху
        total = longs = shorts = 0.0
        by_ex: Dict[str, int] = {}
        for x in rows:
            try:
                usd = float(x.get("usd") or 0)
            except (TypeError, ValueError):
                usd = 0.0
            total += usd
            if x.get("side") == "SELL":
                longs += usd
            else:
                shorts += usd
            ex = str(x.get("exchange") or "?")
            by_ex[ex] = by_ex.get(ex, 0) + 1
        ex_bits = " · ".join(
            f"{ex_link(k, fallback=self.ex_name(k))} {v}"
            for k, v in sorted(by_ex.items(), key=lambda kv: -kv[1])[:6])
        head = (
            "<b>📰 Лента ликвидаций</b>\n"
            f"💥 <code>{self.fmt_int(len(rows))}</code> событий в памяти · "
            f"касса {self.fmt_usd(total)}\n"
            f"🔴 лонги {self.fmt_usd(longs)} · 🟢 шорты {self.fmt_usd(shorts)}\n"
            f"🏛 {ex_bits}\n"
            "⏱ время UTC · новые сверху\n"
        )
        body: List[str] = []
        used = len(head)
        shown = 0
        for x in rows:
            line = self._liq_line(x)
            if used + len(line) + 1 > limit:
                break
            body.append(line)
            used += len(line) + 1
            shown += 1
        rest = len(rows) - shown
        tail = ""
        if rest > 0:
            tail = (f"\n… и ещё {self.fmt_int(rest)} — "
                    f"{self.site_a('смотреть в терминале', '/terminal')}")
        return head + "\n".join(body) + tail + self.site_footer()

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
        return "\n".join(lines) + self.site_footer()

    def _users_text(self) -> str:
        data = self.store.list_users(limit=10)
        c = self.store.user_counts()
        lines = [f"<b>Пользователи</b> · всего {c['total']} · за 24ч {c['active_24h']} · новых {c['new_24h']}"]
        for u in data["users"]:
            flag = " ★" if u["is_admin"] else ""
            ban = " ⛔" if u["is_banned"] else ""
            un = f" @{_esc(u['username'])}" if u["username"] else ""
            lines.append(f"· {_esc(u['display_name'])}{un} <code>{u['tg_id']}</code>{flag}{ban}")
        return "\n".join(lines) + self.site_footer()

    def _visits_text(self) -> str:
        v = self.store.visit_stats(7)
        lines = [
            "<b>Визиты</b>",
            f"Сегодня: {v['today_views']} просмотров, {v['today_uniques']} уникальных",
            f"Онлайн WS: {self.ws_clients_fn()}",
        ]
        for d in v["days"][-7:]:
            lines.append(f"· {d['day']}: {d['views']} / {d['uniques']} уник.")
        return "\n".join(lines) + self.site_footer()

    def _admin_text(self) -> str:
        c = self.store.user_counts()
        v = self.store.visit_stats(1)
        h = self.health_fn() or {}
        live = h.get("live_exchanges") or []
        return (
            f"<b>★ Админка LiqScope</b>\n"
            f"👥 Пользователи: {c['total']} (за сутки {c['active_24h']}, новых {c['new_24h']})\n"
            f"👁 Визиты сегодня: {v['today_views']} / {v['today_uniques']} уник.\n"
            f"📡 Онлайн WS: {self.ws_clients_fn()}\n"
            f"🩺 Биржи в эфире: {len(live)}\n"
            f"🛠 {self.site_a('панель на сайте', '/admin')}"
            + self.site_footer()
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
        return "\n".join(lines) + self.site_footer()

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
