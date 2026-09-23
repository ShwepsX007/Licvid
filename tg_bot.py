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

from bot_i18n import (DEFAULT_LANG, LANGS, SWITCH_TEXT, lang_label,
                      normalize_lang, other_lang, translate, translate_payload)
from refs import ex_link, gate_line, gate_url

log = logging.getLogger("liqscope.bot")

API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_PUBLIC_URL = "https://liqscope.online"
# Сторож опроса. Если успешного getUpdates не было дольше этого времени,
# задача опроса считается зависшей и пересоздаётся: иначе «бот пишет уведомления,
# но не видит кнопки» лечится только ручным перезапуском сервиса, а
# /api/health при этом показывает running: true и fails: 0.
POLL_STALE_SEC = float(os.getenv("LIQSCOPE_BOT_POLL_STALE_SEC", "180"))
# Как часто сверять роли каналов с их названиями в Telegram. Название —
# единственная подсказка, которую бот может получить сам: если русский пост
# уходит в «…Eng», getChat это покажет.
CHANNEL_CHECK_SEC = float(os.getenv("LIQSCOPE_CHANNEL_CHECK_SEC", "1800"))
# Предел подписи под фотографией в Telegram. Пост длиннее уходит текстом без
# картинки — это и есть причина «дневной дайджест вышел без фото».
CAPTION_LIMIT = 1024


def caption_len(text: str) -> int:
    """Длина подписи так, как её считает Telegram: UTF-16, эмодзи = 2 знака.

    Лимит подписи под фотографией — 1024 «знака»; в UTF-16 эмодзи занимают по
    две единицы, поэтому пост, который по ``len()`` проходит, Telegram может
    отклонить. Считает одна общая функция (channel_digest.caption_len) —
    иначе подбор блоков и отправка снова разъедутся, и русский пост опять
    уйдёт без фото.
    """
    from channel_digest import caption_len as shared
    return shared(text)


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
        # Полный (не обрезанный) текст последней ИИ-шапки по языкам: в TG
        # уходит короткая версия под лимит подписи, на сайт — весь текст.
        self._ai_head_full: Dict[str, str] = {}
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
        # chat_id -> язык пользователя. Держим рядом с обработчиками, а не
        # ходим в базу на каждое сообщение: язык нужен и при отправке алертов,
        # которым некогда ждать SQLite.
        self._langs: Dict[int, str] = {}
        # chat_id -> id последнего отправленного сообщения. Нужен, чтобы не
        # править меню, которое уже уехало вверх: id сообщений в чате растут,
        # и по этой паре видно, ушло ли после меню что-то ещё (алерты, отчёты).
        self._last_msg: Dict[int, int] = {}
        # Что ушло в каналы последней публикацией: ``{язык: {chat, message_id,
        # extra}}``. По этим id админка удаляет уже вышедшие выпуски из
        # Telegram — своих id у архива сайта нет.
        self.channel_sent: Dict[str, Dict[str, Any]] = {}
        self.alerts_market_fn: Optional[Callable[[], Any]] = None
        # Корреляции валют: (окно, метрика) → готовая картина по истории
        self.correlations_fn: Optional[Callable[[str, str], Any]] = None
        # Сторож монет: (настройки) → пампы, дампы и последние сигналы
        self.pump_snapshot_fn: Optional[Callable[[dict], Any]] = None
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

    def digest_kb(self, lang: str = "ru") -> dict:
        """Кнопки под дневным выпуском: сверху — ссылка на полный разбор на сайте.

        В подписи под фотографией ссылка тоже есть, но кнопка заметнее: она
        ведёт на страницу /digest, где лежит весь рассказ и все блоки дня.
        """
        kb = dict(self.channel_link_kb(lang))
        rows = [list(r) for r in (kb.get("inline_keyboard") or [])]
        url = self.site_url("/digest")
        label = ("📖 Full day breakdown" if str(lang).startswith("en")
                 else "📖 Полный разбор дня")
        if url:
            rows.insert(0, [{"text": label, "url": url}])
        kb["inline_keyboard"] = rows
        return kb

    def hourly_kb(self, lang: str = "ru") -> dict:
        """Кнопки под сводкой по часам: сверху — ссылка на раздел на сайте.

        Подпись под фотографией Telegram режет по лимиту 1024 знаков, и пост
        может уйти в канал обрезанным. Кнопка ведёт на страницу /hourly, где
        сводка лежит целиком, — так же, как в дневном дайджесте кнопка ведёт
        на /digest.
        """
        kb = dict(self.channel_link_kb(lang))
        rows = [list(r) for r in (kb.get("inline_keyboard") or [])]
        url = self.site_url("/hourly")
        label = ("📖 Full recap on the site" if str(lang).startswith("en")
                 else "📖 Сводка целиком на сайте")
        if url:
            rows.insert(0, [{"text": label, "url": url}])
        kb["inline_keyboard"] = rows
        return kb

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
        "hyperliquid": "Hyperliquid", "dydx": "dYdX",
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
        """Твёрдая клавиатура чата: одна кнопка «☰ Меню».

        Раньше внизу висело полотно из восьми кнопок-разделов — низ экрана
        занимал пол-чата, а часть кнопок дублировала друг друга. Теперь там
        одна кнопка: она открывает меню, а все разделы приходят
        инлайн-кнопками под сообщением (см. ``_menu_kb``).
        """
        return {
            "keyboard": [[{"text": "☰ Меню"}]],
            "resize_keyboard": True,
            "is_persistent": True,
            "input_field_placeholder": "меню — кнопкой ☰ внизу, разделы — кнопками в сообщении",
        }

    def _menu_rows(self, user: Optional[dict] = None) -> List[List[dict]]:
        """Ряды инлайн-меню: разделы, язык, канал, админ, напоминание о почте."""
        user = user or {}
        rows = [
            [{"text": "👤 Кабинет", "callback_data": "cabinet"},
             {"text": "⚡ Терминал", "callback_data": "terminal"}],
            [{"text": "📊 Статистика", "callback_data": "stats"},
             {"text": "🩺 Биржи", "callback_data": "health"}],
            [{"text": "🛠 Сервисы", "callback_data": "services"},
             {"text": "📰 Лента", "callback_data": "liq"}],
            # Язык — рядом с каналом: переключатель один на весь бот
            [{"text": SWITCH_TEXT, "callback_data": "lang"},
             {"text": "📣 Канал", "callback_data": "channel"}],
        ]
        if user:
            email = (user.get("email") or "").strip()
            if not email or not user.get("email_verified"):
                # Основной вход — почта: напоминаем, пока адрес не подтверждён
                rows.append([{"text": "✉️ Подтвердить почту",
                              "callback_data": "a:vmail"}])
            if user.get("is_admin"):
                rows.append([{"text": "★ Админка", "callback_data": "nav:admin"}])
        # Партнёрская ссылка кнопкой: url-кнопку Telegram подсвечивает
        # как ссылку, а не как действие бота
        rows.append([{"text": "💠 Торговать на Gate — скидка на комиссию",
                      "url": gate_url()}])
        return rows

    def _menu_kb(self, user: Optional[dict] = None) -> dict:
        """Инлайн-меню под сообщением — то, что раньше висело кнопками внизу."""
        return {"inline_keyboard": self._menu_rows(user)}

    async def push_reply_kb(self, chat_id: int, user: Optional[dict] = None) -> None:
        """Поставить внизу чата одну твёрдую кнопку «☰ Меню».

        Твёрдая клавиатура приезжает вместе с сообщением и остаётся в чате до
        замены, поэтому служебное сообщение сразу удаляем: в чате остаётся
        только экран с инлайн-кнопками, а кнопка внизу — на месте.
        """
        mid = await self.send(chat_id, "⌨️", self._reply_kb(user), silent=True)
        if not mid:
            return
        await self.drop_menu(chat_id, mid)
        try:
            cid = int(chat_id)
            # Удалённый id не должен считаться «последним сообщением»: иначе
            # меню решит, что уехало вверх, и пришлёт лишний экран.
            if self._last_msg.get(cid) == int(mid):
                self._last_msg[cid] = int(self._menu_msg.get(cid) or 0)
        except (TypeError, ValueError):
            pass

    def _reply_cmd(self, text: str) -> str:
        key = " ".join((text or "").strip().lower().split())
        # Кнопка меню: одна на все случаи — если панель пропала после /start
        # или обновления Telegram, она возвращает и панель, и экран с командами.
        if key in ("☰ меню", "меню", "menu", "/menu", "команды"):
            return "help"
        key = key.replace("★ ", "").replace("👤 ", "").replace("⚡ ", "")
        key = key.replace("📊 ", "").replace("🩺 ", "").replace("🛠 ", "")
        key = key.replace("📰 ", "").replace("🔔 ", "").replace("📣 ", "")
        key = key.replace("🌐 ", "")
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
            # Английские подписи панели — тот же язык, другая раскладка:
            # после переключения на английский кнопки иначе не работали бы.
            "account": "cabinet",
            "terminal": "terminal",
            "stats": "stats",
            "exchanges": "health",
            "services": "services",
            "feed": "liq",
            "feed liq": "liq",
            "alerts": "al",
            "volume alerts": "al",
            "channel": "channel",
            "admin": "admin",
            "verify email": "mail",
            "menu": "help",
            "ru / eng": "lang",
            "ru/eng": "lang",
            "lang": "lang",
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

    def lang_of(self, chat_id: Any) -> str:
        """Язык получателя: то, что человек выбрал кнопкой «🌐 RU/ENG».

        Пишем язык в кэш при каждом сообщении и в аккаунт при переключении,
        поэтому здесь не бывает походов в базу: функция зовётся на каждой
        отправке, в том числе из рассылки алертов.
        """
        try:
            return self._langs.get(int(chat_id), DEFAULT_LANG)
        except (TypeError, ValueError):
            return DEFAULT_LANG

    def remember_lang(self, chat_id: Any, lang: str) -> str:
        """Запомнить язык получателя (и записать в аккаунт, если он есть)."""
        code = normalize_lang(lang)
        try:
            self._langs[int(chat_id)] = code
        except (TypeError, ValueError):
            pass
        return code

    def warm_langs(self, rows) -> None:
        """Запомнить языки получателей перед массовой отправкой.

        Сигналы алертов и рассылка уходят людям, которые могли ни разу не
        написать боту после рестарта: без прогрева кэша они получили бы
        русский текст, даже выбрав английский.
        """
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            tg_id = row.get("tg_id") or row.get("chat_id")
            if tg_id:
                self.remember_lang(tg_id, row.get("language") or row.get("lang") or "")

    def _channel_chats(self) -> set:
        """Чаты каналов: их сообщения переводить нельзя.

        Пост в русский канал уже русский нарочно (английская версия идёт во
        второй канал), поэтому «перевести всё исходящее» здесь не годится.
        """
        out = set()
        for value in (self.channel_chat_id(), self.channel_chat_id_en()):
            text = str(value or "").strip()
            if text:
                out.add(text)
                try:
                    out.add(str(int(text)))
                except ValueError:
                    pass
        return out

    async def _call(self, method: str, payload: Optional[dict] = None) -> Optional[dict]:
        if not self._session:
            return None
        if payload:
            # Метка «как есть»: черновики постов админу показывают будущую
            # публикацию в её собственном языке — переводить её нельзя, иначе
            # админ не увидит, что уйдёт в русский канал.
            raw = bool(payload.pop("_raw", False))
            chat_id = payload.get("chat_id")
            if not raw and chat_id is not None \
                    and str(chat_id) not in self._channel_chats():
                payload = translate_payload(method, payload, self.lang_of(chat_id))
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
                   parse: str = "HTML", silent: bool = False,
                   raw: bool = False) -> Optional[int]:
        """``raw=True`` — отправить текст как есть, без перевода на язык чата."""
        body: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:3900],
            "parse_mode": parse,
            "disable_web_page_preview": True,
        }
        if raw:
            body["_raw"] = True
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
                    return self._note_sent(chat_id, mid)
            return None
        mid = (res.get("result") or {}).get("message_id")
        return self._note_sent(chat_id, mid)

    def _note_sent(self, chat_id, message_id) -> Optional[int]:
        """Запомнить последнее сообщение чата: id растут по времени.

        Нужно для меню: пока id меню-сообщения не меньше последнего
        отправленного, оно лежит внизу и правка не сдвинет ленту. Канал можно
        указать и именем (@channel) — тогда ключом остаётся имя, а не число.
        """
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            return None
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            cid = str(chat_id)
        self._last_msg[cid] = max(mid, self._last_msg.get(cid, 0))
        return mid

    def _menu_is_last(self, chat_id: int, mid: Optional[int]) -> bool:
        """Меню — последнее сообщение чата? Тогда правим на месте.

        Если после меню бот прислал что-то ещё (сигналы алертов, отчёт о
        сводке, черновик), старое сообщение уже выше по ленте: правка
        показывает именно его, и пользователю приходится мотать чат вверх.
        """
        if not mid:
            return False
        try:
            last = int(self._last_msg.get(int(chat_id)) or 0)
            return not last or int(mid) >= last
        except (TypeError, ValueError):
            return False

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

    def _stale_menus(self) -> int:
        """Сколько чатов живут с меню, уехавшим вверх по ленте (диагностика)."""
        return sum(1 for cid, mid in self._menu_msg.items()
                   if not self._menu_is_last(cid, mid))

    async def show_menu(self, chat_id: int, text: str, markup: Optional[dict] = None,
                        old_id: Optional[int] = None) -> bool:
        """Экран меню — всегда внизу чата, под сигналами и отчётами.

        Пока меню — последнее сообщение, правим его на месте: без новых
        сообщений, пуша и мусора. Но если после меню бот прислал что-то ещё
        (сигналы алертов, отчёт, черновик), править старое нельзя: Telegram
        показывает именно его, и лента уезжает наверх — приходилось мотать
        чат. Тогда шлём свежий экран вниз, а старое меню убираем, чтобы в
        чате оставался один актуальный экран.

        ReplyKeyboard живёт у чата и приезжает вместе с новым send. Инлайн
        под текстом обновляется через editMessageText.
        """
        chat_id = int(chat_id)
        inline = self._inline_markup(markup)
        mid = old_id or self._menu_msg.get(chat_id)
        stale = bool(mid) and not self._menu_is_last(chat_id, mid)
        if mid and not stale:
            if await self.edit(chat_id, int(mid), text, inline):
                self._menu_msg[chat_id] = int(mid)
                self._note_sent(chat_id, mid)
                return True
        new_id = await self.send(chat_id, text, markup, silent=True)
        if not new_id:
            # Ни правка, ни новое сообщение не прошли: без этой строки в логе
            # вообще ничего не видно, хотя для пользователя бот «просто молчит».
            log.error("меню не доставлено chat=%s: %s", chat_id,
                      self._last_tg_err or "Telegram не ответил")
            return False
        new_id = int(new_id)
        self._menu_msg[chat_id] = new_id
        if stale:
            # Старые экраны больше не нужны: пользователь просил меню
            # последним сообщением, а не стопку меню между алертами.
            for old in {int(mid or 0), int(old_id or 0)}:
                if old and old != new_id:
                    await self.drop_menu(chat_id, old)
        return True

    async def reply(self, chat_id: int, text: str, markup: Optional[dict] = None,
                    message_id: Optional[int] = None) -> bool:
        return await self.show_menu(chat_id, text, markup, old_id=message_id)

    def channel_chat_id(self) -> str:
        """id русского канала: выбор админа в боте важнее env.

        Переменные LIQSCOPE_CHANNEL_ID/CHANNEL2_ID — это стартовое значение
        для первого запуска; если админ потом перепривязал канал кнопками,
        главным становится его выбор (иначе пост уходит не туда, и по логам
        это не видно).
        """
        d = self._channel_bindings()          # выбор админа важнее env
        return (str(d.get("ru") or "").strip() or self._channel_id_cfg.strip())

    def channel_chat_id_en(self) -> str:
        d = self._channel_bindings()
        return (str(d.get("en") or "").strip() or self._channel2_id_cfg.strip())

    def channel_source(self, code: str = "ru") -> str:
        """Откуда взялся id: «кнопки» (админ) или env (переменная окружения)."""
        d = self._channel_bindings()
        if code == "en":
            saved = str(d.get("en") or "").strip()
            return "bot" if saved else ("env" if self._channel2_id_cfg else "")
        saved = str(d.get("ru") or "").strip()
        return "bot" if saved else ("env" if self._channel_id_cfg else "")

    def channel_url_en(self) -> str:
        return self.channel2_url

    def remember_channel_en(self, chat_id, title: str = "") -> str:
        cid = str(chat_id).strip()
        if not cid:
            return ""
        self.remember_channel_role("en", cid, title)
        log.info("английский канал привязан chat_id=%s title=%s", cid, title)
        return cid

    def channel_role(self, code: str) -> dict:
        """Что сейчас привязано к роли: {id, title, url}."""
        if code == "en":
            cid = self.channel_chat_id_en()
            title = (self.store.get_setting("channel_title_en", "") if self.store else "") or ""
            return {"code": "en", "id": cid, "title": title or self.channel2_name(),
                    "url": self.channel_url_en()}
        cid = self.channel_chat_id()
        # id из env имеет приоритет — показываем именно его
        if self._channel_id_cfg and cid == self._channel_id_cfg:
            title = (self.store.get_setting("channel_title", "") if self.store else "") or ""
        else:
            title = (self.store.get_setting("channel_title", "") if self.store else "") or ""
        return {"code": "ru", "id": cid, "title": title or "LiqScopeRUS",
                "url": self.channel_url}

    def channel2_name(self) -> str:
        return os.getenv("LIQSCOPE_CHANNEL_EN_NAME") or "LiqScopeEng"

    def remember_channel_role(self, code: str, chat_id, title: str = "") -> str:
        """Жёстко закрепляет канал за языком.

        Заодно снимает ту же роль с другого канала: один канал не может быть
        и русским, и английским, иначе посты уходят не туда.
        """
        cid = str(chat_id).strip()
        if not cid or code not in ("ru", "en"):
            return ""
        d = self._channel_bindings()
        d[code] = cid
        if title:
            d.setdefault("titles", {})[code] = str(title)[:80]
        # если этот же id стоял на другой роли — убираем оттуда
        for other in ("ru", "en"):
            if other != code and d.get(other) == cid:
                d[other] = ""
        self._save_channel_bindings(d)
        log.info("канал %s привязан: %s (%s)", code, cid, title)
        return cid

    def _channel_bindings(self) -> dict:
        if self.store:
            try:
                raw = self.store.get_setting("channel_bindings", "")
                if raw:
                    d = json.loads(raw)
                    if isinstance(d, dict):
                        return {"ru": str(d.get("ru") or ""), "en": str(d.get("en") or ""),
                                "titles": d.get("titles") or {}}
            except Exception as e:
                log.debug("channel_bindings: %s", e)
        return {"ru": self.store.get_setting("channel_id", "") if self.store else "",
                "en": self.store.get_setting("channel_id_en", "") if self.store else "",
                "titles": {}}

    def _save_channel_bindings(self, d: dict) -> None:
        if not self.store:
            return
        d = {"ru": str(d.get("ru") or ""), "en": str(d.get("en") or ""),
             "titles": d.get("titles") or {}}
        self.store.set_setting("channel_bindings", json.dumps(d, ensure_ascii=False))
        # держим и старые ключи: их читают channel_chat_id() и проверка подписки
        self.store.set_setting("channel_id", d["ru"])
        self.store.set_setting("channel_id_en", d["en"])
        t = d.get("titles") or {}
        if t.get("ru"):
            self.store.set_setting("channel_title", str(t["ru"])[:80])
        if t.get("en"):
            self.store.set_setting("channel_title_en", str(t["en"])[:80])

    LANG_MARKS = {
        "en": ("eng", "english", "_en", " en", "intl", "global"),
        "ru": ("rus", "russian", "_ru", " ru", "русс", "рус"),
    }

    @classmethod
    def _title_lang(cls, title: str) -> str:
        """Язык канала по названию: «LiqScopeEng» → en, «LiqScopeRUS» → ru."""
        low = (title or "").lower()
        for code in ("en", "ru"):
            if any(m in low for m in cls.LANG_MARKS[code]):
                return code
        return ""

    async def channel_titles_live(self) -> dict:
        """Свежие названия каналов из Telegram: {код: название}."""
        out = {}
        for code in ("ru", "en"):
            cid = self.channel_chat_id() if code == "ru" else self.channel_chat_id_en()
            if not cid:
                continue
            res = await self._call("getChat", {"chat_id": cid})
            chat = res.get("result") if isinstance(res, dict) else None
            if not isinstance(chat, dict):
                continue
            title = str(chat.get("title") or chat.get("username") or "").strip()
            if title:
                out[code] = title
        return out

    async def verify_channel_roles(self, force: bool = False) -> str:
        """Сверить роли каналов с их названиями и починить перепутанные.

        Бот админ в обоих каналах, значит getChat отдаёт актуальное название:
        «LiqScopeEng» — английский, «LiqScopeRUS» — русский. Если название
        говорит, что русский пост уходит в английский канал (а английский —
        в русский), молча меняем роли местами и говорим об этом админу: иначе
        «сводка не туда» лечится только перепривязкой каналов вручную.
        """
        now = time.time()
        last = 0.0
        if self.store:
            try:
                last = float(self.store.get_setting("channel_roles_check", "0") or 0)
            except (TypeError, ValueError):
                last = 0.0
        if not force and now - last < CHANNEL_CHECK_SEC:
            return ""
        if self.store:
            self.store.set_setting("channel_roles_check", str(int(now)))
        titles = {}
        try:
            titles = await self.channel_titles_live()
        except Exception as e:
            log.debug("названия каналов: %s", e)
        if not titles:
            return ""
        # Роли берём так же, как их видит публикация: выбор админа, иначе env.
        # Тогда перепутанные id, пришедшие только из переменных окружения, тоже
        # лечатся — а именно так «русский пост уходил в английский канал».
        ru = self.channel_chat_id().strip()
        en = self.channel_chat_id_en().strip()
        langs = {c: self._title_lang(t) for c, t in titles.items()}
        note = ""
        if ru and en and langs.get("ru") == "en" and langs.get("en") == "ru":
            self.remember_channel_role("ru", en, titles.get("en", ""))
            self.remember_channel_role("en", ru, titles.get("ru", ""))
            log.warning("каналы были перепутаны — поменяли местами")
            note = "каналы были перепутаны: поменяли местами"
        elif ru and not en and langs.get("ru") == "en":
            log.warning("русская роль стоит на английском канале %s", ru)
            note = "русская роль стоит на английском канале"
        # названия в настройках держим актуальными: их видит админ на экране
        if self.store:
            for code, title in titles.items():
                key = "channel_title_en" if code == "en" else "channel_title"
                if not self.store.get_setting(key, ""):
                    self.store.set_setting(key, str(title)[:80])
        return note

    def channel_route_text(self) -> str:
        """Куда уходит каждая сводка: две строки с id — ответ на «пост не туда»."""
        lines = []
        for code, flag in (("ru", "🇷🇺"), ("en", "🇬🇧")):
            role = self.channel_role(code)
            cid = role.get("id") or "—"
            title = role.get("title") or "—"
            lines.append(f"{flag} <code>{_esc(str(cid))}</code> · {_esc(str(title))}")
        return "\n".join(lines)

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
        lines.append(f"Раз в {self.channel_interval_h()} ч туда уходит разбор рынка: кто кого вынес,"
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
            routes = getattr(self, "_digest_routes", "") or self.channel_route_text()
            return ("📣 <b>Сводка ушла в канал</b>\n" + routes
                    + "\n\nЕсли адрес не тот — меню «📣 Каналы» → «🔄 Поменять"
                      " местами», либо перешлите боту пост из нужного канала"
                      " и выберите язык."
                    + self.site_footer())
        err = _esc(self._digest_err or "неизвестная ошибка")
        return (
            "⚠️ <b>Не удалось отправить сводку</b>\n"
            f"<code>{err}</code>\n\n"
            "Если бот уже админ — перешлите сюда любой пост из канала, "
            "затем снова /digest."
            + self.site_footer()
        )

    async def _bind_channel_from_message(self, msg: dict, chat_id: int) -> bool:
        """Админ переслал пост из канала → спрашиваем, какой это язык.

        Раньше язык угадывался (по названию и порядку пересылки) — и канал
        легко вставал не на ту роль: русский пост уходил в английский канал.
        Теперь решает админ: две кнопки, без догадок.
        """
        chat = self._extract_forward_chat(msg)
        if not chat:
            return False
        cid = str(chat.get("id") or "").strip()
        if not cid:
            return False
        title = chat.get("title") or cid
        self._pending_channel = {"id": cid, "title": str(title)}
        if self.store:
            # Кнопка роли может прийти уже после перезапуска бота: держим
            # ожидание в настройках, а не только в памяти процесса.
            try:
                self.store.set_setting("channel_pending",
                                       json.dumps(self._pending_channel,
                                                  ensure_ascii=False))
            except Exception as e:
                log.debug("channel_pending: %s", e)
        rows = [[{"text": "🇷🇺 Русский", "callback_data": "ch:role:ru"},
                 {"text": "🇬🇧 English", "callback_data": "ch:role:en"}]]
        await self.show_menu(
            chat_id,
            f"Канал <b>{_esc(str(title))}</b>\n<code>{_esc(cid)}</code>\n\n"
            "Куда слать сводки — на русском или на английском?\n"
            "Посты пойдут в оба канала: русский в русский, английский"
            " в английский.\n"
            "У бота должно быть право «Публикация сообщений».",
            {"inline_keyboard": rows},
        )
        return True

    async def _set_channel_role(self, code: str, chat_id: int) -> None:
        pend = getattr(self, "_pending_channel", None) or {}
        if not pend and self.store:
            try:
                pend = json.loads(self.store.get_setting("channel_pending", "") or "{}")
            except Exception as e:
                log.debug("channel_pending: %s", e)
                pend = {}
        pend = pend if isinstance(pend, dict) else {}
        cid = str(pend.get("id") or "")
        if not cid:
            await self.show_menu(chat_id, "Не помню, из какого канала был пост."
                                            " Перешлите его ещё раз.",
                                 self._admin_kb())
            return
        self.remember_channel_role(code, cid, pend.get("title") or "")
        self._pending_channel = None
        if self.store:
            self.store.set_setting("channel_pending", "")
        await self.show_menu(chat_id, self._channels_text(), self._channels_kb())

    def _channel_text(self) -> str:
        """Экран «Каналы»: те же посты, что уходят в канал, — в двух языках."""
        links = "\n".join(
            f'• <a href="{_esc(url)}">{_esc(name)}</a>'
            + (" 🇷🇺" if code == "ru" else " 🇬🇧")
            for code, name, url, _cid in self.channel_pair())
        return ("<b>📣 Каналы LiqScope</b>\n"
                f"Сводки ликвидаций, OI и CVD раз в {self.channel_interval_h()} ч — тот же разбор,"
                " что в боте.\n"
                "Содержание одинаковое, отличается язык: русский и английский.\n"
                f"{links}\n\n"
                "Подписки на любой из них достаточно."
                + self.site_footer())

    def _channels_text(self) -> str:
        ru, en = self.channel_role("ru"), self.channel_role("en")
        def line(role: dict, flag: str) -> str:
            cid = role.get("id") or "—"
            src = self.channel_source(str(role.get("code") or "ru"))
            note = {"bot": " · выбрано в боте", "env": " · из переменной окружения"}.get(src, "")
            return (f"{flag} <b>{_esc(role.get('title') or '—')}</b>{note}"
                    f"\n   <code>{_esc(cid)}</code>")
        warn = ""
        if not ru.get("id") or not en.get("id"):
            warn = ("\n\n⚠️ Пока привязан один канал — сводка уходит только в него.\n"
                    "Добавьте бота админом во второй канал и перешлите сюда его пост.")
        elif ru.get("id") == en.get("id"):
            warn = "\n\n⚠️ Оба языка указывают на один канал — перешлите пост из второго."
        return ("<b>📣 Каналы бота</b>\n"
                "Русская сводка — в русский канал, английская — в английский.\n\n"
                f"{line(ru, '🇷🇺')}\n\n{line(en, '🇬🇧')}{warn}\n\n"
                "Чтобы сменить канал: перешлите боту любой пост из него и выберите язык."
                + self.site_footer())

    def _channels_kb(self) -> dict:
        ru, en = self.channel_role("ru"), self.channel_role("en")
        rows = []
        if ru.get("url"):
            rows.append([{"text": f"🇷🇺 {ru.get('title') or 'русский'}", "url": ru["url"]}])
        if en.get("url"):
            rows.append([{"text": f"🇬🇧 {en.get('title') or 'английский'}", "url": en["url"]}])
        rows.append([{"text": "🔄 Поменять местами", "callback_data": "ch:swap"}])
        rows.append([{"text": "← Назад", "callback_data": "nav:home"}])
        return {"inline_keyboard": rows}

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
        # Название надёжнее порядка: «LiqScopeEng» — английский, «LiqScopeRUS» —
        # русский, даже если админ переслал их в обратном порядке.
        low = (title or "").lower()
        if not en and ("eng" in low or low.endswith("_en") or low.endswith(" en")):
            return ("en", self.remember_channel_en(cid, title))
        if not ru and ("rus" in low or low.endswith("_ru") or low.endswith(" ru")):
            return ("ru", self.remember_channel(cid, title))
        if not ru:
            return ("ru", self.remember_channel(cid, title))
        return ("en", self.remember_channel_en(cid, title))

    def remember_channel(self, chat_id, title: str = "") -> str:
        cid = str(chat_id).strip()
        if not cid:
            return ""
        self.remember_channel_role("ru", cid, title)
        log.info("русский канал привязан chat_id=%s title=%s", cid, title)
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
                         markup: Optional[dict] = None,
                         raw: bool = False) -> Optional[int]:
        """Фото с подписью. ``raw=True`` — подпись не переводить.

        Подпись уходит multipart-запросом мимо ``_call``, поэтому язык здесь
        применяем сами: иначе подпись под фото осталась бы русской у тех, кто
        выбрал английский, а preview чужого поста — наоборот, переведённой.
        """
        if not self._session or not path or not os.path.isfile(path):
            return None
        if not raw and str(chat_id) not in self._channel_chats():
            lang = self.lang_of(chat_id)
            caption = translate(caption, lang)
            markup = translate_markup(markup, lang)
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
        return self._note_sent(chat_id, mid)

    async def _sleep_between_posts(self, chunk: float = 60.0) -> None:
        """Пауза до следующего поста.

        Спим кусками и каждый раз заново спрашиваем частоту: смена расписания
        в админке (раз в 1…24 часов) применяется без перезапуска сервиса.
        """
        while self.running:
            period = self.channel_window_sec()
            try:
                last = float(self.store.get_setting("channel_digest_ts") or 0)
            except (TypeError, ValueError):
                last = 0.0
            if not last:
                return
            left = last + period - time.time()
            if left <= 0:
                return
            try:
                await asyncio.sleep(min(chunk, max(1.0, left)))
            except asyncio.CancelledError:
                raise

    async def _channel_loop(self) -> None:
        # первый пост не сразу: пусть фиды прогреются; дальше — по расписанию
        first = 90.0
        last = 0.0
        try:
            last = float(self.store.get_setting("channel_digest_ts") or 0)
        except (TypeError, ValueError):
            last = 0.0
        try:
            # Учитываем и лог отправок — защита от дубля после рестарта
            log_ts = float(self.store.last_channel_sent_ts("post") or 0)
            if log_ts > last:
                last = log_ts
        except Exception:
            pass
        if last > 0:
            first = max(30.0, self.channel_window_sec() - (time.time() - last))
        try:
            await asyncio.sleep(first)
        except asyncio.CancelledError:
            return
        while self.running:
            try:
                # Проверка вкл/выкл постов — если выключено, просто ждём интервал
                try:
                    raw = str(self.store.get_setting("channel_posts_enabled", "1") or "1").strip().lower()
                    if raw in ("0", "false", "off", "no"):
                        log.debug("сводка: отправка выключена, пропускаем цикл")
                        await asyncio.sleep(self.channel_window_sec())
                        continue
                except Exception:
                    pass
                await self.post_channel_digest()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("channel digest: %s", e)
            try:
                await self._sleep_between_posts()
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

    def channel_interval_h(self) -> int:
        """Частота постов в канал: раз в N часов (1…24).

        Окно поста — эти же N часов, сводка внутри поста почасовая: по одной
        строке на каждый из N последних завершённых часов.
        """
        from channel_digest import interval_hours
        try:
            return interval_hours(self.store)
        except Exception as e:                    # noqa: BLE001
            log.debug("частота постов: %s", e)
            from channel_digest import DEFAULT_INTERVAL_H
            return DEFAULT_INTERVAL_H

    def channel_window_sec(self) -> int:
        """Окно поста в секундах: оно равно промежутку между постами."""
        return max(1, self.channel_interval_h()) * 3600

    def set_channel_interval(self, hours, actor_id: Optional[int] = None) -> int:
        """Сохранить частоту постов: применяется без перезапуска сервиса."""
        from channel_digest import INTERVAL_SETTING, clamp_interval
        n = clamp_interval(hours)
        if self.store:
            try:
                self.store.set_setting(INTERVAL_SETTING, str(n), actor_id=actor_id)
            except Exception as e:                # noqa: BLE001
                log.warning("частота постов не сохранилась: %s", e)
        return n

    def interval_text(self) -> str:
        """«раз в 4 ч · окно 4 ч · разбор по часам» — для админки и /bot."""
        h = self.channel_interval_h()
        return f"раз в {h} ч · окно {h} ч · разбор по часам"

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
        key = "en" if en else "ru"
        self._ai_head_full[key] = ""   # шапка прошлого поста не должна протечь
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
            # Полный текст шапки (до обрезки под подпись Telegram) — для
            # сайта: там пост идёт целиком, обрывать рассказ на полуслове нельзя.
            full = str(getattr(ai, "last_full", "") or "").strip()
            self._ai_head_full[key] = full or head
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
            HEAD_TG_LIMIT, active_headlines, pick_active_image, render_post,
        )
        self._digest_err = ""
        self._last_tg_err = ""
        # Проверка вкл/выкл постов из админки
        try:
            raw = str(self.store.get_setting("channel_posts_enabled", "1") or "1").strip().lower()
            if raw in ("0", "false", "off", "no"):
                if not force:
                    log.info("сводка: отправка выключена в админке (channel_posts_enabled=0)")
                    return False
        except Exception:
            pass
        # Защита от дубля после рестарта: если пост уже ушёл недавно (<80% интервала), не шлём повторно
        if not force:
            try:
                import time as _time
                last_ts = float(self.store.get_setting("channel_digest_ts") or 0)
                # Дополнительно проверяем лог отправок
                try:
                    last_log = self.store.last_channel_sent_ts("post")
                    if last_log:
                        last_ts = max(last_ts, last_log)
                except Exception:
                    pass
                interval = self.channel_window_sec()
                if last_ts and (_time.time() - last_ts) < interval * 0.8:
                    log.info("сводка: уже отправлялась %.0f мин назад — пропускаем дубль",
                             (_time.time() - last_ts) / 60)
                    return False
            except Exception as e:
                log.debug("сводка: проверка дубля: %s", e)
        try:
            # Названия каналов важнее памяти: если роли перепутаны, пост уйдёт
            # не туда — проверяем перед каждой отправкой (не чаще 30 минут).
            note = await self.verify_channel_roles()
            if note:
                self._digest_err = ""
                log.info("сводка: %s", note)
        except Exception as e:
            log.debug("проверка каналов: %s", e)
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
        # Одно фото на пост. Набор из админки листается по кругу без повторов:
        # обложкой идёт то фото, которое дольше всех не выходило, — при пачке
        # загруженных картинок пост не повторяет вчерашнюю обложку. Альбомом
        # набор не уходит: альбом валил все загруженные фото в один пост, и это
        # читалось как сбой публикации.
        img = pick_active_image(self.store, "post", variant=n)
        # Русский пост — основной; английский уходит копией в свой канал.
        # Вид для канала: шапка и цифры окна (касса, лидер, CVD) без
        # почасовых строк — почасовка целиком живёт на сайте /hourly,
        # кнопка «📖 Сводка целиком на сайте» под постом ведёт туда.
        # Шапка в канале — полный текст ИИ (как на сайте), но с пределом
        # HEAD_TG_LIMIT по границе предложения: после ухода почасовых строк
        # в подписи много места, а обрыв на полуслове читался как сбой.
        ai_head, ai_note = await self._ai_headline(snap, variant=n)
        ai_head_full = self._ai_head_full.get("ru") or ai_head
        caption = render_post(
            snap, n,
            headlines=active_headlines(self.store, hours),
            head_override=ai_head_full or None,
            site_url=self.site_url(),
            bot_url=self.bot_url(),
            with_hours=False,
            head_limit=HEAD_TG_LIMIT,
        )
        ai_head_en, _note_en = await self._ai_headline(snap, variant=n, lang="en")
        ai_head_full_en = self._ai_head_full.get("en") or ai_head_en
        caption_en = render_post(
            snap, n,
            headlines=active_headlines(self.store, hours, lang="en"),
            head_override=ai_head_full_en or None,
            site_url=self.site_url(),
            bot_url=self.bot_url(),
            lang="en",
            with_hours=False,
            head_limit=HEAD_TG_LIMIT,
        )
        # Полный текст для сайта /hourly — без лимита 1024 и без обрезки
        # ИИ-шапки: на сайте рассказ идёт целиком (ai_head_full выше).
        caption_full = render_post(
            snap, n,
            headlines=active_headlines(self.store, hours),
            head_override=ai_head_full or None,
            site_url=self.site_url(),
            bot_url=self.bot_url(),
            limit=10 ** 9,
            head_full=True,
        )
        caption_full_en = render_post(
            snap, n,
            headlines=active_headlines(self.store, hours, lang="en"),
            head_override=ai_head_full_en or None,
            site_url=self.site_url(),
            bot_url=self.bot_url(),
            lang="en",
            limit=10 ** 9,
            head_full=True,
        )
        # Пост в канал — всегда один: почасовых строк в подписи больше нет
        # (они живут на сайте /hourly), поэтому и аварийного второго
        # сообщения с топ-7 не нужно — часы из канала убраны намеренно.
        top = ""
        top_en = ""
        # Цифры окна: вместе с подписью и фото их складывает архив раздела
        # «Сводки по часам» — на сайте видно полный текст, в TG — обрезанный
        meta = {"window_h": hours,
                "total_usd": snap.get("total_usd"),
                "liq_count": snap.get("count"),
                "longs_usd": snap.get("longs_usd"),
                "shorts_usd": snap.get("shorts_usd")}
        posts = [
            {"lang": "ru", "cid": cid, "caption": caption, "top": top,
             "full_caption": caption_full, "kb": self.hourly_kb("ru")},
        ]
        cid_en = self.channel_chat_id_en()
        if cid_en:
            posts.append({"lang": "en", "cid": cid_en, "caption": caption_en,
                          "top": top_en, "full_caption": caption_full_en,
                          "kb": self.hourly_kb("en")})
        else:
            log.info("английский канал не привязан — пост только по-русски")
        if self._review_on():
            return await self._send_draft(posts, img, n, ai_note, meta=meta)
        return await self._publish_digest(posts, img, n, meta=meta)

    async def _publish_one(self, cid, caption: str, img, top: str = "",
                           lang: str = "ru", images: Optional[List[str]] = None,
                           kb: Optional[dict] = None) -> bool:
        """Пост в один канал: одно фото и подпись под ним.

        В канал уходит ровно одна картинка. Раньше весь набор из админки
        уходил альбомом (sendMediaGroup), и все загруженные фото валились в
        один пост: порядок листался, но исправлять это альбомом не нужно —
        альбома в постах больше нет. Если фото нет или Telegram его не принял,
        уходит текст: сводка важнее обложки. Подпись длиннее 1024 символов
        (лимит подписи) тоже уходит текстом.

        В подписи уже есть и шапка, и часы с топ-7. ``top`` непустой только
        в аварийном случае (подпись исчерпана до первого часа) — тогда текст
        уходит вторым сообщением, чтобы данные не потерялись.
        """
        markup = kb if kb else self.channel_link_kb(lang)
        photos = [x for x in (images if images is not None else
                              ([img] if img else [])) if x and os.path.isfile(x)]
        ok = False
        mid = 0
        if photos:
            # фото важнее хвоста цифр: если подпись не влезает, подрезаем её
            # (по целым строкам, подпись бренда оставляем) и всё равно шлём
            # картинкой. Раньше пост с длинной подписью уходил текстом — так
            # русский канал и остался без фото, пока английский его получал.
            from channel_digest import caption_fit
            fit = caption_fit(caption)
            if fit != caption:
                log.warning("подпись %s знаков > лимита %s — хвост обрезан, "
                            "фото уходит", caption_len(caption), CAPTION_LIMIT)
            mid = int(await self.send_photo(cid, photos[0], fit, markup) or 0)
            ok = bool(mid)
        if not ok:
            mid = int(await self.send(cid, caption, markup) or 0)
            ok = bool(mid)
        extra: List[int] = []
        if ok and top:
            sent = await self.send(cid, top)
            if sent:
                extra.append(int(sent))
            else:
                log.warning("топ-7 не ушёл в канал %s", cid)
        if ok:
            self.note_channel_post(cid, mid, extra, lang)
        return ok

    def note_channel_post(self, chat_id: Any, message_id: Any, extra=None,
                          lang: str = "ru") -> None:
        """Запомнить, каким сообщением пост лёг в канал.

        Нужно не для отправки, а для уборки: админка удаляет старые выпуски —
        и на сайте, и в Telegram, а id сообщения знает только отправитель.
        """
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            return
        if mid <= 0:
            return
        key = "en" if str(lang or "").startswith("en") else "ru"
        self.channel_sent[key] = {
            "chat": str(chat_id), "message_id": mid,
            "extra": [int(x) for x in (extra or []) if str(x).isdigit()],
            "at": time.time(),
        }

    @staticmethod
    def _caption_note(posts) -> str:
        """Что будет с фото: влезает ли пост в подпись под фотографией.

        Telegram разрешает подпись не длиннее 1024 знаков, а дневной выпуск
        обычно длиннее — тогда он уходит текстом, без картинки. Админ видит
        это ещё в черновике и решает, укорачивать ли рассказ промтом.
        """
        sizes = [(p.get("lang") or "ru", caption_len(p.get("caption") or ""))
                 for p in (posts or [])]
        if not sizes:
            return ""
        shown = " · ".join(
            f"{'🇬🇧' if lang == 'en' else '🇷🇺'} {n}" for lang, n in sizes)
        long = max(n for _, n in sizes) > CAPTION_LIMIT
        tail = ("длиннее лимита подписи (1024) — хвост поста обрежется, но фото "
                "уйдёт" if long else "влезает в подпись под фото")
        return f"<i>Подпись: {shown} знаков — {tail}.</i>"

    @staticmethod
    def _photo_list(img) -> List[str]:
        """Фото поста одним списком: одиночный путь снаружи, список изнутри.

        Список остаётся ради обратной совместимости вызовов: в канал уходит
        только первый элемент, альбомов в постах больше нет.
        """
        if isinstance(img, (list, tuple)):
            return [x for x in img if x]
        return [img] if img else []

    async def _publish_digest(self, posts, img, n: int, meta=None) -> bool:
        """Отправка готового поста в каналы (русский и английский)."""
        delivered = 0
        images = self._photo_list(img)
        sent_langs: List[str] = []
        for post in posts or []:
            cid = post.get("cid")
            if not cid:
                continue
            if await self._publish_one(cid, post.get("caption") or "",
                                       images[0] if images else None,
                                       post.get("top") or "",
                                       post.get("lang") or "ru",
                                       images=images,
                                       kb=post.get("kb") or None):
                delivered += 1
                sent_langs.append("en" if str(post.get("lang") or "").startswith("en")
                                  else "ru")
        if delivered:
            # Тот же пост — в архив сайта: раздел «Сводки по часам» показывает
            # подпись и фото ровно такими, какими они ушли в канал
            self._archive_posts(posts, sent_langs, images[0] if images else "", n,
                                meta=meta)
            self._digest_routes = self.channel_route_text()
            now_ts = int(__import__("time").time())
            self.store.set_setting("channel_digest_n", str(n + 1))
            self.store.set_setting("channel_digest_ts", str(now_ts))
            # Лог отправки для защиты от дубля после рестарта
            try:
                import datetime as _dt
                day = _dt.datetime.utcnow().strftime("%Y-%m-%d")
                self.store.log_channel_sent("post", day, now_ts, f"n={n}")
            except Exception as e:
                log.debug("лог поста: %s", e)
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

    def _archive_posts(self, posts, langs, img, n: int, meta=None) -> None:
        """Положить опубликованный пост в архив сайта («Сводки по часам»).

        В архив идут только те языки, которые реально ушли в канал: если
        английский канал не привязан, поста в нём и не было. На сайт кладём
        полный текст (full_caption), в TG уже ушёл обрезанный caption.
        Ошибка архива публикацию не ломает — пост уже в канале, а сайт просто
        не пополнится.
        """
        store = getattr(self, "hourly_store", None)
        if store is None:
            return
        langs = [x for x in (langs or []) if x]
        if not langs:
            return
        meta = dict(meta or {})
        try:
            from channel_digest import cover_info
            from hourly_posts import post_id
            now = time.time()
            texts: Dict[str, str] = {}
            sent: Dict[str, bool] = {}
            for post in posts or []:
                lang = "en" if str(post.get("lang") or "").startswith("en") else "ru"
                if lang not in langs or texts.get(lang):
                    continue
                # Полный для сайта, обрезанный для отметки отправки
                full = str(post.get("full_caption") or post.get("caption") or "").strip()
                cap = str(post.get("caption") or "").strip()
                if full:
                    texts[lang] = full
                    if cap:
                        sent[lang] = True
            if not texts:
                return
            rec: Dict[str, Any] = {
                "id": post_id(now),
                "ts": now,
                "window_h": meta.get("window_h"),
                "interval_h": meta.get("window_h"),
                "total_usd": meta.get("total_usd"),
                "liq_count": meta.get("liq_count"),
                "longs_usd": meta.get("longs_usd"),
                "shorts_usd": meta.get("shorts_usd"),
                "texts": texts,
                "sent": sent or {x: True for x in texts},
                "n": int(n or 0),
            }
            # Где этот пост лежит в Telegram: по id админка удалит его из
            # канала вместе с записью архива (своих id у сайта нет).
            tg_ids: Dict[str, Any] = {}
            for lang in texts:
                info = (getattr(self, "channel_sent", None) or {}).get(lang) or {}
                if int(info.get("message_id") or 0) > 0:
                    tg_ids[lang] = {"chat": str(info.get("chat") or ""),
                                    "message_id": int(info["message_id"]),
                                    "extra": list(info.get("extra") or [])}
            if tg_ids:
                rec["tg"] = tg_ids
            if img and os.path.isfile(str(img)):
                rec["photo"] = cover_info(str(img), self.store)
            store.add(rec)
            log.info("сводка n=%s добавлена в архив сайта: %s (%s)",
                     n, rec["id"], ", ".join(sorted(texts)))
        except Exception as e:                      # noqa: BLE001
            log.warning("сводки по часам: пост не попал в архив: %s", e)

    async def _send_draft(self, posts, img, n: int, note: str, meta=None) -> bool:
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
        self._draft = {"posts": posts, "img": img, "n": int(n), "note": note,
                       "meta": dict(meta or {}),
                       "images": list(img) if isinstance(img, (list, tuple)) else [img]}
        kb = {"inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": "d:pub"},
            {"text": "🔄 Перегенерировать", "callback_data": "d:regen"},
            {"text": "✖️ Отмена", "callback_data": "d:no"}]]}
        text = (f"<b>Черновик сводки</b>\n<i>{_esc(note)}</i>\n"
                "В каналы уйдёт только после «Опубликовать».")
        for post in posts or []:
            mark = "🇬🇧" if post.get("lang") == "en" else "🇷🇺"
            text += f"\n\n{mark} <b>{(post.get('cid') or '')}</b>\n" + (post.get("caption") or "")
        # raw=True: черновик — это будущий пост, а не сообщение админу.
        # Перевод подписи скрыл бы от админа, что уйдёт в русский канал.
        await self.send(admin, text, kb, raw=True)
        # Черновик показывается так же, как уйдёт в канал: одна картинка.
        imgs = [x for x in (self._draft.get("images") or []) if x and os.path.isfile(x)]
        if imgs:
            await self.send_photo(admin, imgs[0], "Обложка поста — как уйдёт в канал",
                                  raw=True)
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
                ok = await self._publish_digest(posts, d.get("images") or d.get("img"),
                                                int(d.get("n") or 0),
                                                meta=d.get("meta"))
            self._draft = None
            await self.reply(chat_id, self._digest_result_text(ok), self._admin_kb(),
                             message_id=message_id)

    # --- дневной дайджест (вечерний выпуск за сутки) ----------------------
    def daily_status(self) -> Dict[str, Any]:
        """Что известно про дневной дайджест — для админки и логов."""
        out: Dict[str, Any] = dict(getattr(self, "_daily_state", None) or {})
        out["review"] = self._review_on()
        out["wired"] = bool(getattr(self, "daily_run_fn", None))
        return out

    async def publish_daily_digest(self, rec: dict, langs=("ru", "en"),
                                   force: bool = False) -> Dict[str, Any]:
        """Разложить готовый дайджест по каналам: {язык: [ок, ошибка]}.

        Запись собирает сервер (api_digest): здесь только отправка и контроль
        публикации — если он включён, посты уходят админу черновиком.
        """
        from channel_digest import pick_digest_image
        from daily_digest import render_channel

        self._digest_err = ""
        self._last_tg_err = ""
        # Проверка вкл/выкл дайджеста из админки (отдельно от постов)
        try:
            raw = str(self.store.get_setting("digest_enabled", "1") or "1").strip().lower()
            if raw in ("0", "false", "off", "no") and not force:
                log.info("дайджест: отправка выключена в админке (digest_enabled=0)")
                return {"ru": [False, "отправка дайджеста выключена в админке"]}
        except Exception:
            pass
        # Защита от дубля после рестарта: один дайджест в сутки
        day_check = str((rec or {}).get("day") or "").strip()
        if day_check and not force:
            try:
                if self.store.was_channel_sent("digest", day_check):
                    log.info("дайджест: %s уже отправлялся — пропускаем дубль", day_check)
                    return {"ru": [False, f"дайджест за {day_check} уже отправлен"]}
            except Exception as e:
                log.debug("дайджест дубль чек: %s", e)
        try:
            await self.verify_channel_roles()
        except Exception as e:
            log.debug("проверка каналов: %s", e)
        day = str((rec or {}).get("day") or "")
        posts: List[dict] = []
        result: Dict[str, Any] = {}
        seen: set = set()
        for lang in langs or ("ru", "en"):
            lang = "en" if str(lang).startswith("en") else "ru"
            if lang in seen:
                continue
            seen.add(lang)
            cid = self.channel_chat_id_en() if lang == "en" else self.channel_chat_id()
            if not cid:
                result[lang] = [False, ("английский канал не привязан"
                                        if lang == "en" else
                                        "канал не привязан — перешлите боту пост из канала")]
                continue
            # В канал — небольшой пост: шапка, лид рассказа, цифры дня и
            # ссылка на полный разбор на сайте. Так он влезает в подпись под
            # фотографией (1024 знака) и фото уходит всегда; большой текст
            # целиком живёт на странице /digest.
            caption = render_channel(rec, lang, self.site_url())
            posts.append({"lang": lang, "cid": cid, "caption": caption, "top": "",
                          "kb": self.digest_kb(lang)})
        if not posts:
            err = next((v[1] for v in result.values()), "каналы не привязаны")
            self._digest_err = err
            self._daily_state = {"ok": False, "day": day, "error": err,
                                 "at": time.time()}
            return result
        # Фото рубрики «дневной дайджест» (если админ их загрузил); иначе —
        # общий набор сводки. Одно фото на пост, порядок — по кругу без
        # повторов, альбома нет — как и в постах сводки.
        #
        # Обложка обычно уже выбрана при сборке выпуска (api_digest.assign_cover)
        # и записана в запись — тогда её видит и страница /digest: в канале и на
        # сайте одна и та же картинка. Своей обложки нет (старый выпуск или
        # файл пропал) — выбираем здесь, как раньше: пост без фото не выходит.
        img = str(((rec or {}).get("photo") or {}).get("path") or "")
        if not img or not os.path.isfile(img):
            try:
                variant = int(day.replace("-", "")[-2:] or 0)
            except (TypeError, ValueError):
                variant = 0
            # Своя рубрика есть — берём из неё; нет — постовые, но по тому же
            # кругу без повторов: вечерний выпуск не повторяет вчерашнюю обложку.
            # Совсем пусто (нет фото ни в базе, ни в комплекте) — пост уйдёт текстом.
            img = pick_digest_image(self.store, variant=variant)
        if self._review_on():
            ok = await self._send_daily_draft(posts, img, day)
            for post in posts:
                result[post["lang"]] = [False, "" if ok else
                                        (self._digest_err or "черновик не ушёл")]
            self._daily_state = {"ok": False, "day": day, "draft": bool(ok),
                                 "at": time.time()}
            return result
        sent = await self._publish_daily_posts(posts, img, day)
        result.update(sent)
        return result

    async def _publish_daily_posts(self, posts, img, day: str = "") -> Dict[str, Any]:
        """Отправка постов дайджеста по каналам (один пост = одно сообщение)."""
        out: Dict[str, Any] = {}
        images = self._photo_list(img)
        for post in posts or []:
            lang = post.get("lang") or "ru"
            cid = post.get("cid")
            if not cid:
                out[lang] = [False, "канал не привязан"]
                continue
            ok = await self._publish_one(cid, post.get("caption") or "",
                                         images[0] if images else None,
                                         post.get("top") or "", lang,
                                         images=images,
                                         kb=post.get("kb") or None)
            err = "" if ok else (getattr(self, "_last_tg_err", "")
                                 or "Telegram отклонил пост")
            if not ok:
                low = err.lower()
                if "chat not found" in low:
                    err += " Перешлите боту любой пост из канала, чтобы привязать id."
                elif ("not enough right" in low or "need administrator" in low
                      or "have no rights" in low):
                    err += (" В канале у бота должно быть право «Публикация "
                            "сообщений» (и «Прикрепление файлов», если шлём картинку).")
            out[lang] = [bool(ok), err]
        delivered = [l for l, v in out.items() if v[0]]
        if delivered:
            self._digest_routes = self.channel_route_text()
            now_ts = int(__import__("time").time())
            try:
                self.store.set_setting("daily_digest_ts", str(now_ts))
            except Exception as e:
                log.debug("дайджест: метка времени не сохранилась: %s", e)
            # Лог отправки для защиты от дубля после рестарта (на сутки)
            try:
                import datetime as _dt
                day_str = str(day or "").strip() or _dt.datetime.utcnow().strftime("%Y-%m-%d")
                self.store.log_channel_sent("digest", day_str, now_ts, ",".join(delivered))
                # Также обновляем daily_state с днём
                self._daily_state = {"ok": True, "day": day_str, "langs": delivered, "at": float(now_ts)}
            except Exception as e:
                log.debug("лог дайджеста: %s", e)
                self._daily_state = {"ok": True, "langs": delivered, "at": float(now_ts)}
            log.info("дневной дайджест ушёл в каналы: %s", ", ".join(delivered))
        else:
            self._daily_state = {"ok": False,
                                 "error": next((v[1] for v in out.values()), ""),
                                 "at": time.time()}
        return out

    async def _send_daily_draft(self, posts, img, day: str) -> bool:
        """Контроль публикации: дневной дайджест показываем админу, не в канал."""
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
        self._daily_draft = {"posts": posts, "img": img, "day": day}
        kb = {"inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": "dd:pub"},
            {"text": "🔄 Перегенерировать", "callback_data": "dd:regen"},
            {"text": "✖️ Отмена", "callback_data": "dd:no"}]]}
        text = (f"<b>Черновик дневного дайджеста</b> · {_esc(day)}\n"
                "Это суточный выпуск (не сводка за окно поста). В каналы он уйдёт "
                "только после «Опубликовать».\n"
                + self._caption_note(posts))
        await self.send(admin, text, kb)
        imgs = [x for x in self._photo_list(img) if x and os.path.isfile(x)]
        if imgs:
            await self.send_photo(admin, imgs[0], "Обложка поста — как уйдёт в канал",
                                  raw=True)
        for post in posts or []:
            mark = "🇬🇧" if post.get("lang") == "en" else "🇷🇺"
            # raw=True: это будущая публикация в её собственном языке
            await self.send(admin, f"{mark} {post.get('caption') or ''}", raw=True)
        # В канал уходит короткая версия: рассказ целиком и все блоки —
        # на сайте, поэтому админу говорим, где смотреть полный текст.
        site = str(self.site_url() or "").rstrip("/")
        if site.startswith("http"):
            await self.send(admin, f"Полный разбор дня — на сайте: {site}/digest")
        log.info("дневной дайджест: черновик отправлен админу %s (%s)", admin, day)
        return True

    async def _on_daily_cb(self, chat_id, user: dict, data: str, message_id) -> None:
        """Кнопки под черновиком дайджеста: выложить / перегенерировать / отмена."""
        d = dict(getattr(self, "_daily_draft", None) or {})
        if data == "dd:regen":
            self._daily_draft = None
            await self.reply(chat_id, "Собираю новый дайджест за сутки…", None,
                             message_id=message_id)
            await self.post_daily_digest(force=True)
            return
        if data == "dd:no":
            self._daily_draft = None
            await self.reply(chat_id, "Черновик отменён, в каналы ничего не ушло.",
                             self._admin_kb(), message_id=message_id)
            return
        if data == "dd:pub":
            if not d:
                await self.reply(chat_id, "Черновик уже неактуален — нажмите "
                                 "«🗞 Дайджест за сутки» ещё раз.",
                                 self._admin_kb(), message_id=message_id)
                return
            res = await self._publish_daily_posts(d.get("posts") or [],
                                                  d.get("img"))
            self._daily_draft = None
            ok = any(v[0] for v in res.values())
            await self.reply(chat_id, self._daily_result_text(ok, res),
                             self._admin_kb(), message_id=message_id)

    async def post_daily_digest(self, force: bool = True, langs=("ru", "en"),
                                reason: str = "bot") -> bool:
        """Кнопка «выложить в любое время»: собрать суточный дайджест и отправить."""
        fn = getattr(self, "daily_run_fn", None)
        if fn is None:
            return self._digest_fail("дневной дайджест не подключён на сервере")
        try:
            rec = fn(force=bool(force), langs=list(langs), reason=reason)
            if asyncio.iscoroutine(rec):
                rec = await rec
        except Exception as e:
            return self._digest_fail(f"дайджест не собрался: {e}")
        if not isinstance(rec, dict):
            return self._digest_fail("дайджест не собрался: пустой ответ")
        pub = rec.get("published") or {}
        ok = any(bool((v or {}).get("ok")) for v in pub.values())
        if not ok and self._review_on():
            # черновик уже у админа — это не ошибка, а режим контроля
            return bool(getattr(self, "_daily_state", {}).get("draft"))
        self._daily_state = {"ok": ok, "day": rec.get("day"), "at": time.time(),
                             "published": pub}
        return ok

    def _daily_result_text(self, ok: bool, result=None) -> str:
        if ok:
            routes = getattr(self, "_digest_routes", "") or self.channel_route_text()
            return ("🗞 <b>Дневной дайджест ушёл в каналы</b>\n" + routes
                    + "\n\nПрошлые выпуски — на сайте в разделе «Дайджест»."
                    + self.site_footer())
        if self._review_on():
            return ("Черновик дневного дайджеста — выше в чате. Проверьте текст "
                    "и нажмите «✅ Опубликовать».")
        err = _esc(self._digest_err or "неизвестная ошибка")
        detail = ""
        if isinstance(result, dict):
            for lang, val in result.items():
                if isinstance(val, (list, tuple)) and val and not val[0]:
                    mark = "🇬🇧" if lang == "en" else "🇷🇺"
                    detail += f"\n{mark} {_esc(val[1] if len(val) > 1 else '')}"
        return ("⚠️ <b>Не удалось отправить дневной дайджест</b>\n"
                f"<code>{err}</code>{detail}\n\n"
                "Если бот уже админ — перешлите сюда любой пост из канала "
                "и попробуйте снова."
                + self.site_footer())

    async def answer_cb(self, cb_id: str, text: str = "") -> None:
        # Пустой text Telegram иногда отвергает — тогда клиент «залипает»
        # и больше не шлёт нажатия.
        body: Dict[str, Any] = {"callback_query_id": cb_id}
        if text:
            body["text"] = text[:180]
        await self._call("answerCallbackQuery", body)

    async def broadcast(self, text: str, actor_id: Optional[int] = None) -> Dict[str, int]:
        # Язык каждого получателя известен заранее: текст переведётся при
        # отправке (перевод идёт в _call), поэтому переводим не здесь.
        targets = (self.store.broadcast_targets() if hasattr(self.store, "broadcast_targets")
                   else [{"tg_id": i} for i in self.store.tg_ids_for_broadcast()])
        self.warm_langs(targets)
        ids = [int(t["tg_id"]) for t in targets]
        ok = fail = 0
        for tg_id in ids:
            if await self.send(tg_id, text, parse="HTML"):
                ok += 1
            else:
                fail += 1
            await asyncio.sleep(0.04)  # ~25 msg/s
        self.store.audit(actor_id, "broadcast", f"ok={ok} fail={fail}")
        return {"ok": ok, "fail": fail, "total": len(ids)}

    async def broadcast_post(self, text: str, photo: str = "",
                             markup: Optional[dict] = None,
                             raw: bool = True,
                             actor_id: Optional[int] = None) -> Dict[str, int]:
        """Рассылка рекламного поста: с фото — картинкой с подписью, без — текстом.

        ``raw=True`` по умолчанию: реклама уходит ровно так, как её написал
        админ. Текст, который бот переводит сам, здесь не нужен — иначе
        «рекламное» объявление менялось бы от языка получателя.
        """
        targets = (self.store.broadcast_targets() if hasattr(self.store, "broadcast_targets")
                   else [{"tg_id": i} for i in self.store.tg_ids_for_broadcast()])
        ids = [int(t["tg_id"]) for t in targets]
        has_photo = bool(photo and os.path.isfile(photo))
        ok = fail = 0
        for tg_id in ids:
            mid = (await self.send_photo(tg_id, photo, text, markup, raw=raw)
                   if has_photo else await self.send(tg_id, text, markup, raw=raw))
            if mid:
                ok += 1
            else:
                fail += 1
            await asyncio.sleep(0.04)  # ~25 msg/s
        self.store.audit(actor_id, "broadcast_post", f"ok={ok} fail={fail} photo={has_photo}")
        return {"ok": ok, "fail": fail, "total": len(ids)}

    async def delete_message(self, chat_id: Any, message_id: Any) -> bool:
        """Удалить своё сообщение: так снимается реклама по истечении срока.

        В каналах работает для любого возраста поста (бот — админ), в личке
        Telegram разрешает удалять свои сообщения только первые 48 часов.
        """
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            return False
        if not mid:
            return False
        cid = chat_id
        if str(chat_id).lstrip("-").isdigit():
            cid = int(str(chat_id))
        res = await self._call("deleteMessage", {"chat_id": cid, "message_id": mid})
        return bool(res and res.get("ok"))

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
            f" · меню не внизу: {self._stale_menus()}"
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
        self.remember_lang(chat_id, user.get("language"))
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
                if str(wait_al).startswith("corr:"):
                    msg_ok = self._corr_apply_text(user, wait_al, text or msg)
                    self._wait_alert.pop(tg_id, None)
                    await self.show_menu(chat_id, msg_ok + "\n\n" + self._corr_text(user),
                                         self._corr_kb(user))
                    return
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
                await self.show_menu(chat_id, self._channel_text(), self._menu_kb(user))
                return
            if nav == "al":
                await self.show_menu(chat_id, self._alert_text(user), self._alert_kb(user))
                return
            if nav == "lang":
                await self.show_menu(chat_id, self._lang_text(user), self._lang_kb(user))
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
                    self._menu_kb(user))
                return
            await self.show_menu(chat_id, body, kb)
            return
        if text.startswith("/digest") and user.get("is_admin"):
            ok = await self.post_channel_digest(force=True)
            await self.show_menu(chat_id, self._digest_result_text(ok),
                                 self._admin_kb())
        elif text.startswith("/help"):
            await self.show_menu(chat_id, self._help(user), self._menu_kb(user))
        elif text.startswith("/cabinet"):
            await self.show_menu(chat_id, self._cabinet_text(user), self._menu_kb(user))
        elif text.startswith("/mail"):
            body, kb = self._screen(user, "mail")
            await self.show_menu(chat_id, body, kb)
        elif text.startswith("/lang") or text.startswith("/language"):
            await self.show_menu(chat_id, self._lang_text(user), self._lang_kb(user))
        elif text.startswith("/stats"):
            await self.show_menu(chat_id, self._stats_text(), self._menu_kb(user))
        elif text.startswith("/status") or text.startswith("/health"):
            await self.show_menu(chat_id, self._health_text(), self._menu_kb(user))
        elif text.startswith("/bot"):
            await self.show_menu(chat_id, self.poll_text(), self._menu_kb(user))
        elif text.startswith("/liq"):
            await self.show_menu(chat_id, self._liq_text(), self._menu_kb(user))
        elif text.startswith("/services"):
            await self.show_menu(chat_id, self._services_text(user), self._services_kb(user))
        elif text.startswith("/pumps") or text.startswith("/watch"):
            await self.show_menu(chat_id, self._pump_text(user), self._pump_kb(user))
        elif text.startswith("/correlations") or text.startswith("/corr"):
            await self.show_menu(chat_id, self._corr_text(user), self._corr_kb(user))
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
            await self.show_menu(chat_id, "Не понял. Нажмите кнопку или /help.", self._menu_kb(user))

    async def _on_callback(self, cb: dict) -> None:
        from_u = cb.get("from") or {}
        data = (cb.get("data") or "").strip()
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id") or from_u.get("id")
        message_id = msg.get("message_id")
        user = self.store.upsert_telegram_user(from_u)
        self.remember_lang(chat_id, user.get("language"))
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
            if data.startswith("ch:role:"):
                if not user.get("is_admin"):
                    await self.show_menu(chat_id, "Привязывать каналы может только"
                                                    " администратор.",
                                         self._menu_kb(user))
                    return
                await self._set_channel_role(data.split(":")[-1], chat_id)
                return
            if data == "lang":
                await self.show_menu(chat_id, self._lang_text(user), self._lang_kb(user),
                                     old_id=message_id)
                return
            if data.startswith("setlang:"):
                code = data.split(":", 1)[1]
                await self.answer_cb(cb["id"], lang_label(code))
                await self.set_lang(chat_id, user, code)
                return
            if data == "ch:swap":
                if not user.get("is_admin"):
                    return
                ru = self.channel_chat_id()
                en = self.channel_chat_id_en()
                if ru and en:
                    d = self._channel_bindings()
                    d["ru"], d["en"] = en, ru
                    t = d.get("titles") or {}
                    t["ru"], t["en"] = t.get("en", ""), t.get("ru", "")
                    d["titles"] = t
                    self._save_channel_bindings(d)
                await self.show_menu(chat_id, self._channels_text(), self._channels_kb())
                return
            if data == "a:channels":
                # Экран открывают, чтобы понять «куда уходит пост»: перед
                # показом сверяем роли с названиями каналов (getChat) — если
                # они перепутаны, админ увидит уже исправленные строки.
                try:
                    await self.verify_channel_roles(force=True)
                except Exception as e:
                    log.debug("проверка каналов: %s", e)
                await self.show_menu(chat_id, self._channels_text(), self._channels_kb())
                return
            if data == "ch:check":
                if await self._is_member_any(tg_id):
                    text, markup = self._home_text(user), self._menu_kb(user)
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
            if data == "a:ddigest" and user.get("is_admin"):
                posted = await self.post_daily_digest(force=True)
                await self.reply(chat_id, self._daily_result_text(posted),
                                 self._admin_kb(), message_id=message_id)
                return
            if user.get("is_admin") and data.startswith("dd:"):
                await self._on_daily_cb(chat_id, user, data, message_id)
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
            if data == "cor" or data.startswith("cor:"):
                await self._on_corr_cb(chat_id, user, data, message_id)
                return
            if data == "pw" or data.startswith("pw:"):
                await self._on_pump_cb(chat_id, user, data, message_id)
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
            return self._home_text(user), self._menu_kb(user)
        if data == "lang":
            return self._lang_text(user), self._lang_kb(user)
        if data == "cabinet":
            return self._cabinet_text(user), self._menu_kb(user)
        if data == "mail":
            # Напоминание из кабинета: ждём адрес почты следующим сообщением
            self._wait_email[int(user.get("tg_id") or 0)] = time.time() + 900
            return self._mail_screen(user), self._cabinet_kb(user)
        if data == "stats":
            return self._stats_text(), self._menu_kb(user)
        if data == "health":
            return self._health_text(), self._menu_kb(user)
        if data == "a:health" and user.get("is_admin"):
            return self._health_text(), self._kb_back("nav:admin")
        if data == "liq":
            return self._liq_text(), self._menu_kb(user)
        if data == "terminal":
            # сразу на сайт: большая URL-кнопка, сообщение уходит без звука
            return self._terminal_text(), self._terminal_kb()
        if data == "services":
            return self._services_text(user), self._services_kb(user)
        if data == "channel":
            # Кнопка «📣 Канал» в инлайн-меню: раньше её не знал _screen и
            # нажатие молча выбрасывало на главный экран
            return self._channel_text(), self._menu_kb(user)
        if data.startswith("svc:"):
            slug = data.split(":", 1)[1]
            if slug == "alerts":
                row = next((s for s in self.store.list_services(False)
                            if s["slug"] == "alerts"), None)
                if row and not row.get("coming_soon"):
                    return self._alert_text(user), self._alert_kb(user)
            if slug == "correlations":
                row = next((s for s in self.store.list_services(False)
                            if s["slug"] == "correlations"), None)
                if row and not row.get("coming_soon"):
                    return self._corr_text(user), self._corr_kb(user)
            if slug == "watchlist":
                row = next((s for s in self.store.list_services(False)
                            if s["slug"] == "watchlist"), None)
                if row and not row.get("coming_soon"):
                    return self._pump_text(user), self._pump_kb(user)
            have = set(self.store.user_service_slugs(user["id"]))
            on = slug not in have
            self.store.toggle_user_service(user["id"], slug, on)
            return self._services_text(user), self._services_kb(user)
        if data == "a:interval" and user.get("is_admin"):
            return self._post_int_text(), self._post_int_kb()
        if data.startswith("pi:") and user.get("is_admin"):
            try:
                self.set_channel_interval(data.split(":", 1)[1], actor_id=user.get("id"))
            except Exception as e:                # noqa: BLE001
                return (f"⚠️ Не сохранил частоту: {_esc(str(e)[:120])}",
                        self._post_int_kb())
            return self._post_int_text(), self._post_int_kb()
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
        return self._home_text(user), self._menu_kb(user)

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
                    self._menu_kb(user),
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
            await self.show_menu(chat_id, msg, self._menu_kb(user))
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
                    self._menu_kb(user),
                )
                return
            await self.show_menu(
                chat_id,
                "Код входа недействителен или устарел. Нажмите «Войти» на сайте ещё раз.",
                self._menu_kb(user),
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
        # Твёрдая клавиатура — одна кнопка ☰ Меню: ставим её здесь (в чате
        # она приезжает служебным сообщением, которое сразу удаляется), а
        # разделы уходят инлайн-кнопками под приветствием.
        await self.push_reply_kb(chat_id, user)
        await self.show_menu(
            chat_id,
            f"Привет, {_esc(user.get('display_name') or 'друг')}!\n\n{welcome}{extra}",
            self._menu_kb(user),
        )

    async def _cmd_admin(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._menu_kb(user))
            return
        await self.show_menu(chat_id, self._admin_text(), self._admin_kb())

    async def _cmd_users(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._menu_kb(user))
            return
        await self.show_menu(chat_id, self._users_text(), self._kb_back("nav:admin"))

    async def _cmd_visits(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._menu_kb(user))
            return
        await self.show_menu(chat_id, self._visits_text(), self._kb_back("nav:admin"))

    async def _cmd_broadcast(self, chat_id: int, user: dict, text: str) -> None:
        if not user["is_admin"]:
            await self.show_menu(chat_id, "Недостаточно прав.", self._menu_kb(user))
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
            [{"text": "🕒 Частота сводки", "callback_data": "a:interval"}],
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
        """Меню по кнопке «☰ Меню»: разделы кнопками под сообщением.

        Твёрдая клавиатура внизу теперь одна (☰ Меню), поэтому всё
        остальное приходит здесь — инлайном.
        """
        rows = [[{"text": "🏠 /start", "callback_data": "nav:home"}]]
        rows += self._menu_rows(user)
        return {"inline_keyboard": rows}

    def _commands_text(self, user: dict) -> str:
        return (
            "<b>☰ Меню LiqScope</b>\n"
            "Разделы — кнопками ниже: набирать команды руками не нужно.\n"
            "Внизу экрана остаётся одна твёрдая кнопка ☰ Меню — она всегда"
            " возвращает этот экран.\n\n"
            "/start — экран заново, /help — полный список команд."
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
        return self._menu_kb(user)

    def _post_int_text(self) -> str:
        """Экран «Частота постов»: окно поста и почасовой разбор."""
        from channel_digest import MAX_INTERVAL_H, MIN_INTERVAL_H
        return (
            "<b>🕒 Частота сводки в канал</b>\n"
            f"Сейчас: {self.interval_text()}\n\n"
            "Сводка почасовая: в посте — по строке на каждый из N последних "
            "завершённых часов, независимо от частоты постов.\n\n"
            f"Выберите частоту ({MIN_INTERVAL_H}…{MAX_INTERVAL_H} ч) — применяется "
            "сразу, перезапуск не нужен.\n"
            + self.site_footer()
        )

    def _post_int_kb(self) -> dict:
        from channel_digest import MAX_INTERVAL_H
        cur = self.channel_interval_h()
        rows: List[list] = []
        row: list = []
        for n in range(1, MAX_INTERVAL_H + 1):
            row.append({"text": (f"✓ {n} ч" if n == cur else f"{n} ч"),
                        "callback_data": f"pi:{n}"})
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "← Назад", "callback_data": "nav:admin"}])
        return {"inline_keyboard": rows}

    def _admin_kb(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "👥 Пользователи", "callback_data": "users"},
             {"text": "📈 Визиты", "callback_data": "visits"}],
            [{"text": "📣 Рассылка", "callback_data": "broadcast"},
             {"text": "🩺 Здоровье", "callback_data": "a:health"}],
            [{"text": "📰 Сводка в канал", "callback_data": "a:digest"},
             {"text": "🕒 Частота", "callback_data": "a:interval"}],
            [{"text": "🗞 Дайджест за сутки", "callback_data": "a:ddigest"}],
            [{"text": "📣 Каналы", "callback_data": "a:channels"},
             {"text": "🎨 Шаблоны", "callback_data": "a:tpl"}],
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
            "Выберите раздел — кнопки под сообщением, внизу экрана только"
            " ☰ Меню.\n"
            + gate_line()
            + self.site_footer()
        )

    def _help(self, user: dict) -> str:
        lines = [
            "<b>Команды LiqScope</b>",
            "/start — регистрация / вход на сайт",
            "☰ Меню — кнопки разделов вместо ручного ввода",
            "/cabinet — профиль",
            "/terminal — ссылка на терминал",
            "/stats — ликвидации 24ч",
            "/status — какие биржи в эфире",
            "/liq — последние события",
            "/services — сервисы кабинета",
            "/alerts — алерты по объёму",
            "/correlations — корреляции валют",
            "/pumps — сторож монет: пампы и дампы",
            "/bot — состояние опроса бота",
            "/lang — язык бота: RU / ENG",
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
            f"{_esc((c.get('symbol') or '').replace('_', '/'))}"
            f" <code>{usd(c.get('usd'))}</code>"
            for c in top[:5]
        ) or "—"
        return (
            f"<b>📊 Рынок · 24ч</b>\n"
            f"💥 Ликвидации: <code>{usd(st.get('total_usd_24h'))}</code>\n"
            f"🔴 Лонги <code>{usd(st.get('longs_usd_24h'))}</code> · "
            f"🟢 шорты <code>{usd(st.get('shorts_usd_24h'))}</code>\n"
            f"⏱ За час: <code>{usd(st.get('total_usd_1h'))}</code>\n"
            f"🏆 Лидеры: {top_s}"
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
            dead = s.get("supervisor_alive") is False
            if dead:
                # Слушателя нет вовсе: попыток больше не будет, пока сторож
                # не поднимет поток заново. Раньше это выглядело как «нет
                # связи» без единой попытки — источник молчал до рестарта.
                respawns = int(s.get("respawns") or 0)
                tries = int(s.get("attempts") or 0)
                rows.append(
                    f"🟠 <b>{ex_link(name, fallback=self.ex_name(name))}</b> · "
                    f"слушатель не запущен — поднимает сторож"
                    + (f" · попыток: <code>{self.fmt_int(tries)}</code>" if tries else "")
                    + (f" (подъёмов: <code>{respawns}</code>)" if respawns else ""))
            elif s.get("connected"):
                live += 1
                fresh = ""
                sec = s.get("seconds_since_event")
                if isinstance(sec, (int, float)):
                    fresh = f" · {self.ago_label(sec)}"
                again = int(s.get("attempts") or 0)
                if again > 1:
                    fresh += f" · попыток: <code>{self.fmt_int(again)}</code>"
                rows.append(
                    f"🟢 <b>{ex_link(name, fallback=self.ex_name(name))}</b> · "
                    f"<code>{self.fmt_int(ev)} событий</code>{fresh}")
            else:
                err = _esc(str(s.get("last_error") or "нет связи")[:42])
                attempts = int(s.get("attempts") or 0)
                tail = f" · попыток: <code>{attempts}</code>" if attempts else ""
                rows.append(f"🔴 <b>{ex_link(name, fallback=self.ex_name(name))}</b>"
                            f" · {err}{tail}")
        lines = ["<b>🩺 Биржи · эфир</b>", ""]
        lines.extend(rows or ["сервер ещё собирает источники"])
        lines.append("")
        lines.append(
            f"📡 В эфире <b>{live}/{total or '—'}</b> · "
            f"<code>{self.fmt_int(events_total)} событий</code> в памяти · "
            f"зрителей WS: <code>{self.ws_clients_fn()}</code>")
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
        # строка ленты — обычный текст (не <pre>), значит биржу можно сделать
        # ссылкой: Gate ведёт на партнёрскую, остальные — просто название
        ex_key = x.get("exchange")
        return (f"{tm} {mark} {_esc(sym)}  "
                f"<code>{self.fmt_usd(x.get('usd'))}</code>  "
                f"{ex_link(ex_key, fallback=self.ex_name(ex_key))}")

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
            f"💥 <code>{self.fmt_int(len(rows))} событий</code> в памяти · "
            f"касса <code>{self.fmt_usd(total)}</code>\n"
            f"🔴 лонги <code>{self.fmt_usd(longs)}</code>"
            f" · 🟢 шорты <code>{self.fmt_usd(shorts)}</code>\n"
            f"🏛 <code>{ex_bits}</code>\n"
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
            "Те же, что на сайте: 🔔 алерты, 🔗 корреляции валют и 👁 сторож "
            "монет уже работают.",
            "",
        ]
        for s in self.store.list_services(include_disabled=False):
            mark = "✓" if s["slug"] in have else "○"
            if s["slug"] in ("alerts", "correlations", "watchlist") and not s.get("coming_soon"):
                extra = " · открыть"
            else:
                extra = " · скоро" if s["coming_soon"] else ""
            lines.append(f"{mark} {s['icon']} <b>{_esc(s['title'])}</b>{extra}")
            if s.get("description"):
                lines.append(f"    {_esc(s['description'])}")
        lines.append("\nРаботающие сервисы открывают свои настройки, "
                     "остальные — лист ожидания, пока «скоро».")
        return "\n".join(lines) + self.site_footer()

    def _users_text(self) -> str:
        data = self.store.list_users(limit=10)
        c = self.store.user_counts()
        lines = [
            f"<b>Пользователи</b> · всего <code>{c['total']}</code>"
            f" · за 24ч <code>{c['active_24h']}</code>"
            f" · новых <code>{c['new_24h']}</code>"
        ]
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
            f"Сегодня: <code>{v['today_views']} переходов</code>,"
            f" <code>{v['today_uniques']} посетителей</code>",
            f"Служебных запросов отсеяно: <code>{v.get('today_bots', 0)}</code>",
            f"Онлайн WS: <code>{self.ws_clients_fn()}</code>",
        ]
        for d in v["days"][-7:]:
            lines.append(f"· {d['day']}: <code>{d['views']} переходов /"
                         f" {d['uniques']} посетителей</code>")
        lines.append("<i>Считаем только браузерные переходы: краулеры и превью"
                     " в статистику не идут, один гость без cookie не"
                     " размножается на каждой странице.</i>")
        return "\n".join(lines) + self.site_footer()

    def _admin_text(self) -> str:
        c = self.store.user_counts()
        v = self.store.visit_stats(1)
        h = self.health_fn() or {}
        live = h.get("live_exchanges") or []
        ru, en = self.channel_role("ru"), self.channel_role("en")
        def ch(role: dict) -> str:
            return f"{_esc(role.get('title') or '—')} <code>{_esc(role.get('id') or '—')}</code>"
        return (
            f"<b>★ Админка LiqScope</b>\n"
            f"👥 Пользователи: <code>{c['total']}</code>"
            f" (за сутки <code>{c['active_24h']}</code>,"
            f" новых <code>{c['new_24h']}</code>)\n"
            f"👁 Визиты сегодня: <code>{v['today_views']} переходов /"
            f" {v['today_uniques']} посетителей</code>\n"
            f"📡 Онлайн WS: <code>{self.ws_clients_fn()}</code>\n"
            f"🩺 Биржи в эфире: <code>{len(live)}</code>\n"
            f"🇷🇺 Канал: {ch(ru)}\n"
            f"🇬🇧 Канал: {ch(en)}\n"
            f"🕒 Сводка: {self.interval_text()}\n"
            f"🛠 {self.site_a('панель на сайте', '/admin')}"
            + self.site_footer()
        )

    # --- язык интерфейса -------------------------------------------------
    def lang_of_user(self, user: Optional[dict]) -> str:
        """Язык пользователя: выбор кнопкой важнее языка клиента Telegram."""
        if isinstance(user, dict):
            return normalize_lang(user.get("language"))
        return self.lang_of(user)

    def _lang_text(self, user: Optional[dict] = None) -> str:
        """Экран «🌐 Язык бота».

        Текст всегда русский: при выбранном английском его переведёт тот же
        слой, что и остальные сообщения (см. bot_i18n), поэтому второй копии
        экрана на английском не нужно.
        """
        cur = self.lang_of_user(user) if isinstance(user, dict) else self.lang_of(user)
        return ("<b>🌐 Язык бота</b>\n"
                f"Сейчас выбран {lang_label(cur)}.\n"
                "Выберите язык — меню, сигналы и сводки будут на нём."
                + self.site_footer())

    def _lang_kb(self, user: Optional[dict] = None) -> dict:
        """Две кнопки языка: текущая помечена галочкой."""
        cur = self.lang_of_user(user) if isinstance(user, dict) else \
            self.lang_of(user)
        rows = []
        for code in ("ru", "en"):
            info = LANGS[code]
            mark = "✓ " if code == cur else ""
            rows.append([{"text": f"{mark}{info['flag']} {info['label']}",
                          "callback_data": f"setlang:{code}"}])
        rows.append([{"text": "← В меню", "callback_data": "nav:home"}])
        return {"inline_keyboard": rows}

    async def set_lang(self, chat_id: int, user: dict, code: str) -> None:
        """Переключить язык пользователя и подтвердить это на новом языке."""
        lang = normalize_lang(code)
        if self.store and user.get("id"):
            try:
                fresh = self.store.set_user_language(user["id"], lang)
                if isinstance(fresh, dict):
                    user.update(fresh)
            except Exception as e:                       # noqa: BLE001
                log.warning("язык не сохранился (%s): %s", lang, e)
        self.remember_lang(chat_id, lang)
        await self.show_menu(chat_id, self._lang_text(user), self._menu_kb(user))

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
        lines = [format_config_text(cfg)]
        fn = self.alerts_market_fn
        if fn:
            try:
                market = fn() or {}
                live = live_snapshot(cfg, market)
                bits = []
                for m, icon, title in (("liq", "💥", "LIQ"), ("cvd", "🌊", "CVD"),
                                       ("oi", "📊", "OI")):
                    row = live.get(m) or {}
                    bits.append(f"{icon} {title} <code>{money(row.get('value'))}</code>")
                lines.append("сейчас: " + " · ".join(bits))
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

    def _alert_coins_kb(self, user: dict, metric: str) -> dict:
        """Монеты для одной метрики: у каждой она своя."""
        from alerts import COIN_PRESETS, METRIC_ICON, METRIC_TITLE, coin_name, symbol_of
        cfg = self._alert_cfg(user)
        cur = symbol_of(cfg, metric)
        rows = []
        for c in COIN_PRESETS:
            mark = "✓ " if cur == c else ""
            rows.append([{"text": mark + coin_name(c),
                          "callback_data": f"al:c:{metric}:{c}"}])
        rows.append([{"text": "своя монета", "callback_data": f"al:c:{metric}:?"}])
        rows.append([{"text": f"← {METRIC_ICON[metric]} {METRIC_TITLE[metric]}",
                      "callback_data": "al:c"}])
        rows.append([{"text": "← К алертам", "callback_data": "al"}])
        return {"inline_keyboard": rows}

    def _alert_win_kb(self, user: dict) -> dict:
        """Окна по метрикам: у ликвидаций, CVD и OI они свои."""
        from alerts import METRIC_ICON, METRIC_TITLE, window_label, window_of
        cfg = self._alert_cfg(user)
        rows = []
        for m in ("liq", "cvd", "oi"):
            label = f"{METRIC_ICON[m]} {m.upper()} · {window_label(window_of(cfg, m))}"
            rows.append([{"text": label, "callback_data": f"al:w:{m}"}])
        rows.append([{"text": "← К алертам", "callback_data": "al"}])
        return {"inline_keyboard": rows}

    def _alert_win_metric_kb(self, metric: str, cfg: dict) -> dict:
        """Пресеты минут для одной метрики: своё окно — свои кнопки."""
        from alerts import METRIC_ICON, METRIC_TITLE, WINDOW_PRESETS, window_label
        cur = window_label(cfg.get("windows", {}).get(metric) or cfg.get("window_min"))
        row = [{"text": ("· " if window_label(w) == cur else "") + window_label(w),
                "callback_data": f"al:w:{metric}:{w}"} for w in WINDOW_PRESETS]
        return {"inline_keyboard": [
            row[:3], row[3:],
            [{"text": f"свои минуты · {METRIC_ICON[metric]} {METRIC_TITLE[metric]}",
              "callback_data": f"al:w:{metric}:?"}],
            [{"text": "← Другая метрика", "callback_data": "al:w"}],
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
        letter = {"thr": "t", "min": "n", "coin": "c"}[field]
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
        if wait == "coin" or wait.startswith("coin:"):
            from alerts import METRICS
            coin = canon_symbol(text)
            if ":" in wait:
                metric = wait.split(":", 1)[1]
                coins = dict(cfg.get("coins") or {})
                coins[metric] = coin
                cfg["coins"] = coins
                self._alert_save(user, cfg)
                from alerts import METRIC_TITLE
                return f"Монета {METRIC_TITLE.get(metric, metric)}: {coin}"
            # старый ввод: одна монета на все метрики
            cfg["symbol"] = coin
            cfg["coins"] = {m: coin for m in METRICS}
            self._alert_save(user, cfg)
            return f"Монета: {cfg['symbol']}"
        if wait == "win" or wait.startswith("win:"):
            from alerts import window_minutes
            metric = wait.split(":", 1)[1] if ":" in wait else \
                (cfg.get("watch") or ["liq"])[0]
            try:
                n = int(float(text.replace(",", ".")))
            except (TypeError, ValueError):
                return "Нужно число минут, например 5."
            wins = dict(cfg.get("windows") or {})
            wins[metric] = window_minutes(n)
            cfg["windows"] = wins
            if metric in (cfg.get("watch") or []):
                cfg["window_min"] = wins[metric]      # старое поле — под метрику
            self._alert_save(user, cfg)
            titles = {"liq": "Ликвидации", "cvd": "CVD", "oi": "OI"}
            return f"Окно {titles.get(metric, metric)}: {window_label(wins[metric])}"
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

    # ----- сервис «Сторож монет»: пампы и дампы ---------------------------
    @staticmethod
    def _user_lang(user: dict) -> str:
        """Язык пользователя для ссылок и текстов сигналов.

        Сначала выбор человека, потом локаль клиента Telegram, и только потом
        язык по умолчанию (``normalize_lang``) — он же решает, что делать с
        языком, которого бот не знает.
        """
        lang = ((user or {}).get("language_code") or
                (user or {}).get("language") or "")
        return normalize_lang(lang)

    def _pump_cfg(self, user: dict) -> dict:
        from pump_scan import normalize
        row = self.store.get_user_service(user["id"], "watchlist") if self.store else None
        cfg = normalize((row or {}).get("config") or {})
        if row is None:
            cfg["enabled"] = True
        return cfg

    def _pump_save(self, user: dict, cfg: dict) -> None:
        self.store.set_user_service_config(
            user["id"], "watchlist", cfg, enabled=bool(cfg.get("enabled")))

    def _pump_data(self, cfg: dict) -> dict:
        fn = self.pump_snapshot_fn
        if not fn:
            return {}
        try:
            return fn(cfg) or {}
        except Exception as e:  # noqa: BLE001
            log.debug("сторож монет: %s", e)
            return {}

    def _pump_text(self, user: dict) -> str:
        from pump_scan import format_settings_text, money, price_str
        cfg = self._pump_cfg(user)
        data = self._pump_data(cfg)
        status = data.get("status") or {}
        lines = ["<b>👁 Сторож монет: пампы и дампы</b>",
                 format_settings_text(cfg)]
        if not status.get("coins"):
            lines.append("")
            lines.append("Цены Gate ещё не подтянулись — сторож начнёт считать "
                         "минутную историю с первого удачного опроса.")
        else:
            lines.append(f"под наблюдением монет: <b>{status['coins']}</b>"
                         + (f" · данные {int(status['age_sec'])} с назад"
                            if status.get("age_sec") is not None else ""))
        movers = data.get("movers") or []
        if movers:
            lines.append("")
            lines.append("<b>Самые резкие движения сейчас</b>")
            for m in movers[:5]:
                arrow = "🚀" if m["change_pct"] > 0 else "🩸"
                lines.append(f"{arrow} <b>{m['symbol'].replace('_USDT', '')}</b> "
                             f"<b>{m['change_pct']:+.2f}%</b>"
                             f" · <code>{price_str(m.get('price'))}</code>"
                             f" · оборот <code>{money(m.get('volume24h'))}</code>")
        hits = data.get("hits") or []
        if hits:
            lines.append("")
            lines.append("<b>Уже за порогом</b>")
            for h in hits[:3]:
                kind = "памп" if h["kind"] == "pump" else "дамп"
                lines.append(f"· <b>{h['symbol'].replace('_USDT', '')}</b> — {kind} "
                             f"<b>{h['change_pct']:+.2f}%</b>"
                             f" за {int(h['span_min'])} мин")
        lines.append("")
        lines.append("Кнопки ниже — режим, порог, период и число свечей. "
                     "Сигналы приходят отдельными сообщениями со ссылкой на Gate.")
        return "\n".join(lines) + self.site_footer()

    def _pump_kb(self, user: dict) -> dict:
        from pump_scan import CANDLE_PRESETS, THRESHOLDS, period_label
        cfg = self._pump_cfg(user)

        def mark(on: bool, label: str) -> str:
            return ("✓ " if on else "") + label

        on = "🔔 Следить ВКЛ" if cfg.get("enabled") else "🔕 Следить выкл"
        kb = [
            [{"text": mark(cfg["mode"] in ("pump", "both"), "🚀 Пампы"),
              "callback_data": "pw:mode:pump"},
             {"text": mark(cfg["mode"] in ("dump", "both"), "🩸 Дампы"),
              "callback_data": "pw:mode:dump"},
             {"text": mark(cfg["mode"] == "both", "⚖️ Оба"),
              "callback_data": "pw:mode:both"}],
        ]
        thr = [{"text": mark(abs(cfg["threshold"] - t) < 1e-9, f"{t:g}%"),
                "callback_data": f"pw:thr:{t:g}"} for t in THRESHOLDS[:4]]
        kb.append(thr)
        kb.append([{"text": mark(abs(cfg["threshold"] - t) < 1e-9, f"{t:g}%"),
                    "callback_data": f"pw:thr:{t:g}"} for t in THRESHOLDS[4:]])
        kb.append([{"text": mark(cfg["period"] == k, k),
                    "callback_data": f"pw:per:{period_label(k)}"}
                   for k in ("1m", "5m", "15m")])
        kb.append([{"text": mark(cfg["period"] == k, k),
                    "callback_data": f"pw:per:{period_label(k)}"}
                   for k in ("30m", "1h", "4h")])
        kb.append([{"text": mark(cfg["candles"] == n, f"{n} свеч."),
                    "callback_data": f"pw:cn:{n}"} for n in CANDLE_PRESETS[:5]])
        kb.append([{"text": on, "callback_data": "pw:on"},
                   {"text": "🔄 Проверить", "callback_data": "pw:now"}])
        kb.append([{"text": "💠 Gate", "url": gate_url(self._user_lang(user))},
                   {"text": "← Назад", "callback_data": "services"}])
        return {"inline_keyboard": kb}

    async def _on_pump_cb(self, chat_id, user: dict, data: str, message_id) -> None:
        from pump_scan import normalize
        cfg = self._pump_cfg(user)
        parts = data.split(":")
        changed = True
        if data in ("pw", "pw:now"):
            changed = False
        elif data == "pw:on":
            cfg["enabled"] = not cfg.get("enabled")
        elif len(parts) >= 3 and parts[1] == "mode":
            mode = parts[2]
            if mode not in ("pump", "dump", "both"):
                mode = "both"
            cfg["mode"] = mode
            cfg["enabled"] = True
        elif len(parts) >= 3 and parts[1] == "thr":
            try:
                cfg["threshold"] = float(parts[2])
            except ValueError:
                pass
        elif len(parts) >= 3 and parts[1] == "per":
            cfg["period"] = parts[2]
        elif len(parts) >= 3 and parts[1] == "cn":
            try:
                cfg["candles"] = int(parts[2])
            except ValueError:
                pass
        else:
            changed = False
        if changed:
            cfg = normalize(cfg)
            self._pump_save(user, cfg)
        await self.reply(chat_id, self._pump_text(user), self._pump_kb(user),
                         message_id=message_id)

    # ----- сервис «Корреляции валют» --------------------------------------
    def _corr_cfg(self, user: dict) -> dict:
        from correlations import (DEFAULT_METRIC, DEFAULT_WINDOW, normalize_alerts,
                                  window_key)
        row = self.store.get_user_service(user["id"], "correlations") if self.store else None
        cfg = (row or {}).get("config") or {}
        return {"window": window_key(cfg.get("window") or DEFAULT_WINDOW),
                "metric": str(cfg.get("metric") or DEFAULT_METRIC),
                # алерты по корреляции: у каждой метрики своё окно и два порога
                "alerts": normalize_alerts(cfg)}

    def _corr_save(self, user: dict, cfg: dict) -> None:
        self.store.set_user_service_config(
            user["id"], "correlations", cfg, enabled=True)

    def _corr_data(self, cfg: dict) -> dict:
        fn = self.correlations_fn
        if not fn:
            return {}
        try:
            return fn(cfg.get("window", "24h"), cfg.get("metric", "liq")) or {}
        except Exception as e:  # noqa: BLE001
            log.debug("корреляции: %s", e)
            return {}

    def _corr_text(self, user: dict) -> str:
        from correlations import format_text, metric_title, window_label
        cfg = self._corr_cfg(user)
        data = self._corr_data(cfg)
        from correlations import alerts_line
        if not data:
            # история ещё копится, но алерты уже можно настраивать: сигналы
            # пойдут, как только появятся часы для расчёта связи
            return ("<b>🔗 Корреляции валют</b>\n"
                    "История ещё собирается: сервис считает связи монет по часовым "
                    "свёрткам ликвидаций, объёма, CVD и OI. Загляните позже — или "
                    "посмотрите тепловую карту на сайте.\n\n"
                    + alerts_line(cfg.get("alerts"))
                    + self.site_footer())
        body = format_text(data, "ru", site=self.site_url("/cabinet"))
        lines = body.split("\n")
        lines[0] = (f"<b>🔗 Корреляции валют</b> · {window_label(cfg['window'])} · "
                    f"{metric_title(cfg['metric'])}")
        lines.append("")
        lines.append(alerts_line(cfg.get("alerts")))
        return "\n".join(lines) + self.site_footer()

    def _corr_kb(self, user: dict) -> dict:
        from correlations import WINDOWS, window_label
        cfg = self._corr_cfg(user)
        rows = []
        for key, _minutes in WINDOWS:
            mark = "✓ " if key == cfg["window"] else ""
            rows.append({"text": mark + window_label(key),
                         "callback_data": "cor:w:" + key})
        kb = [rows[i:i + 3] for i in range(0, len(rows), 3)]
        kb.append([
            {"text": ("✓ " if cfg["metric"] == "liq" else "") + "💥 LIQ",
             "callback_data": "cor:m:liq"},
            {"text": ("✓ " if cfg["metric"] == "vol" else "") + "📦 Объём",
             "callback_data": "cor:m:vol"},
        ])
        kb.append([
            {"text": ("✓ " if cfg["metric"] == "cvd" else "") + "🌊 CVD",
             "callback_data": "cor:m:cvd"},
            {"text": ("✓ " if cfg["metric"] == "oi" else "") + "📊 OI",
             "callback_data": "cor:m:oi"},
        ])
        kb.append([{"text": "🔔 Алерты", "callback_data": "cor:a"}])
        kb.append([{"text": "🔄 Пересчитать", "callback_data": "cor:now"},
                   {"text": "🌐 Тепловая карта", "url":
                    self.site_url("/cabinet#correlations")}])
        kb.append([{"text": "← Назад", "callback_data": "services"}])
        return {"inline_keyboard": kb}

    def _corr_num(self, v) -> str:
        return f"{float(v):+.2f}".replace("-", "−")

    def _corr_alerts_text(self, user: dict) -> str:
        from correlations import format_alerts_config
        cfg = self._corr_cfg(user)
        return (format_alerts_config(cfg.get("alerts")) +
                "\n\nУ каждой метрики своё окно и свои пороги. «В противофазе» — "
                "связь со знаком минус, «в одну сторону» — со знаком плюс: "
                "порог 0.5 значит «коэффициент 0.5 и выше». Поставьте порог — "
                "сигнал включится сам." + self.site_footer())

    def _corr_alerts_kb(self, user: dict) -> dict:
        from correlations import METRICS, metric_icon, metric_title, window_label
        cfg = self._corr_cfg(user)
        alerts = cfg.get("alerts") or {}
        rows = []
        for key, _title, _hint in METRICS:
            row = alerts.get(key) or {}
            mark = "✓ " if row.get("enabled") else ""
            label = (f"{mark}{metric_icon(key)} {metric_title(key)} · "
                     f"{window_label(row.get('window') or '24h')}")
            rows.append([{"text": label, "callback_data": f"cor:a:{key}"}])
        rows.append([{"text": "← К корреляциям", "callback_data": "cor"}])
        return {"inline_keyboard": rows}

    def _corr_metric_text(self, user: dict, metric: str) -> str:
        from correlations import metric_icon, metric_title, window_label
        cfg = self._corr_cfg(user)
        row = (cfg.get("alerts") or {}).get(metric) or {}
        on = "включён" if row.get("enabled") else "выключен"
        return "\n".join([
            f"{metric_icon(metric)} <b>Алерты · {metric_title(metric)}</b>",
            f"сигнал: <b>{on}</b>",
            f"окно: <b>{window_label(row.get('window') or '24h')}</b>",
            f"в противофазе: порог <code>{self._corr_num(row.get('opp') or 0)}</code>",
            f"в одну сторону: порог <code>{self._corr_num(row.get('same') or 0)}</code>",
            "\nСигнал — пара монет с такой связью. Пороги нажимаются кнопками "
            "или вводятся руками.",
        ]) + self.site_footer()

    def _corr_metric_kb(self, user: dict, metric: str) -> dict:
        from correlations import ALERT_THRESHOLDS, WINDOWS, window_label
        cfg = self._corr_cfg(user)
        row = (cfg.get("alerts") or {}).get(metric) or {}
        on = "🔔 Сигнал ВКЛ" if row.get("enabled") else "🔕 Сигнал выкл"
        kb = [[{"text": on, "callback_data": f"cor:a:{metric}:on"}]]
        wins = [{"text": ("· " if key == (row.get("window") or "24h") else "") +
                         window_label(key),
                 "callback_data": f"cor:a:{metric}:w:{key}"}
                for key, _m in WINDOWS]
        kb += [wins[i:i + 3] for i in range(0, len(wins), 3)]
        opp = [{"text": ("· " if abs(float(v) - float(row.get("opp") or 0)) < 1e-9
                         else "") + f"−{v:.1f}",
                "callback_data": f"cor:a:{metric}:opp:{v}"} for v in ALERT_THRESHOLDS]
        same = [{"text": ("· " if abs(float(v) - float(row.get("same") or 0)) < 1e-9
                          else "") + f"+{v:.1f}",
                 "callback_data": f"cor:a:{metric}:same:{v}"} for v in ALERT_THRESHOLDS]
        kb.append([{"text": "противофаза", "callback_data": f"cor:a:{metric}"}])
        kb += [opp[i:i + 3] for i in range(0, len(opp), 3)]
        kb.append([{"text": "в одну сторону", "callback_data": f"cor:a:{metric}"}])
        kb += [same[i:i + 3] for i in range(0, len(same), 3)]
        kb.append([{"text": "свой порог: противофаза",
                    "callback_data": f"cor:a:{metric}:?:opp"},
                   {"text": "свой порог: в одну сторону",
                    "callback_data": f"cor:a:{metric}:?:same"}])
        kb.append([{"text": "← Алерты", "callback_data": "cor:a"}])
        kb.append([{"text": "← К корреляциям", "callback_data": "cor"}])
        return {"inline_keyboard": kb}

    def _corr_alert_row(self, cfg: dict, metric: str) -> dict:
        from correlations import normalize_alerts
        alerts = cfg.get("alerts") or normalize_alerts({})
        return dict(alerts.get(metric) or {})

    def _corr_alert_store(self, user: dict, cfg: dict, metric: str, row: dict) -> None:
        alerts = dict(cfg.get("alerts") or {})
        alerts[metric] = row
        cfg["alerts"] = alerts
        self._corr_save(user, cfg)

    def _corr_apply_text(self, user: dict, wait: str, raw) -> str:
        from correlations import METRIC_KEYS, metric_title
        text = raw if isinstance(raw, str) else ""
        parts = str(wait).split(":")
        metric = parts[1] if len(parts) > 1 else "liq"
        field = parts[2] if len(parts) > 2 else "opp"
        if metric not in METRIC_KEYS:
            return "Неизвестная метрика."
        cfg = self._corr_cfg(user)
        row = self._corr_alert_row(cfg, metric)
        try:
            n = float(text.replace(" ", "").replace(",", ".").replace("−", "-"))
        except (TypeError, ValueError):
            return "Нужно число от 0 до 1, например 0.5."
        if field == "opp":
            row["opp"] = round(max(-1.0, min(0.0, -abs(n))), 3)
        else:
            row["same"] = round(max(0.0, min(1.0, abs(n))), 3)
        row["enabled"] = True        # поставил порог — ждём сигнал
        self._corr_alert_store(user, cfg, metric, row)
        title = metric_title(metric)
        sign = "−" if field == "opp" else "+"
        word = "противофаза" if field == "opp" else "в одну сторону"
        return (f"{title}: {word} {sign}{abs(n):.2f} · сигнал включён")

    async def _on_corr_cb(self, chat_id, user: dict, data: str, message_id) -> None:
        cfg = self._corr_cfg(user)
        parts = data.split(":")
        if data == "cor" or data == "cor:now":
            await self.reply(chat_id, self._corr_text(user), self._corr_kb(user),
                             message_id=message_id)
            return
        if data == "cor:a" or data.startswith("cor:a:"):
            from correlations import METRIC_KEYS, window_key
            parts = data.split(":")
            if len(parts) == 2:
                await self.reply(chat_id, self._corr_alerts_text(user),
                                 self._corr_alerts_kb(user), message_id=message_id)
                return
            metric = parts[2]
            if metric not in METRIC_KEYS:
                await self.reply(chat_id, self._corr_alerts_text(user),
                                 self._corr_alerts_kb(user), message_id=message_id)
                return
            row = self._corr_alert_row(cfg, metric)
            if len(parts) == 3:
                await self.reply(chat_id, self._corr_metric_text(user, metric),
                                 self._corr_metric_kb(user, metric),
                                 message_id=message_id)
                return
            action = parts[3]
            if action == "on":
                row["enabled"] = not row.get("enabled")
                self._corr_alert_store(user, cfg, metric, row)
                await self.reply(chat_id, self._corr_metric_text(user, metric),
                                 self._corr_metric_kb(user, metric),
                                 message_id=message_id)
                return
            if action == "?":
                tg_id = int(user.get("tg_id") or 0)
                self._wait_alert[tg_id] = f"corr:{metric}:{parts[4] if len(parts) > 4 else 'opp'}"
                await self.reply(
                    chat_id,
                    "Пришлите порог от 0 до 1, например 0.5.\n/cancel — отмена.",
                    self._kb_back("cor"), message_id=message_id)
                return
            value = parts[4] if len(parts) > 4 else ""
            if action == "w":
                row["window"] = window_key(value)
            elif action in ("opp", "same"):
                if value == "?":
                    tg_id = int(user.get("tg_id") or 0)
                    self._wait_alert[tg_id] = f"corr:{metric}:{action}"
                    await self.reply(
                        chat_id,
                        "Пришлите порог от 0 до 1, например 0.5.\n/cancel — отмена.",
                        self._kb_back("cor"), message_id=message_id)
                    return
                try:
                    num = float(str(value).replace("−", "-"))
                except ValueError:
                    num = 0.5
                if action == "opp":
                    row["opp"] = round(max(-1.0, min(0.0, -abs(num))), 3)
                else:
                    row["same"] = round(max(0.0, min(1.0, abs(num))), 3)
                row["enabled"] = True    # поставил порог — ждём сигнал
            self._corr_alert_store(user, cfg, metric, row)
            await self.reply(chat_id, self._corr_metric_text(user, metric),
                             self._corr_metric_kb(user, metric),
                             message_id=message_id)
            return
        if len(parts) >= 3 and parts[1] == "w":
            cfg["window"] = parts[2]
        elif len(parts) >= 3 and parts[1] == "m":
            cfg["metric"] = parts[2]
        else:
            await self.reply(chat_id, self._corr_text(user), self._corr_kb(user),
                             message_id=message_id)
            return
        self._corr_save(user, cfg)
        await self.reply(chat_id, self._corr_text(user), self._corr_kb(user),
                         message_id=message_id)

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
            await self.reply(chat_id, "Монета какой метрики? У каждой она своя.",
                             self._alert_metric_pick_kb("coin"),
                             message_id=message_id)
            return
        if data.startswith("al:c:"):
            from alerts import METRIC_ICON, METRIC_TITLE, METRICS, canon_symbol
            parts = data.split(":")
            # al:c:<монета> — старый колбэк: одна монета на все метрики
            if len(parts) == 3 and parts[2] not in METRICS:
                if parts[2] == "?":
                    self._wait_alert[tg_id] = "coin"
                    await self.reply(
                        chat_id,
                        "Пришлите тикер, например BTC или ETHUSDT.\n/cancel — отмена.",
                        self._kb_back("al"), message_id=message_id)
                    return
                coin = canon_symbol(parts[2])
                cfg["coins"] = {m: coin for m in METRICS}
                self._alert_save(user, cfg)
                await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                                 message_id=message_id)
                return
            metric = parts[2]
            if metric not in METRICS:
                await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                                 message_id=message_id)
                return
            if len(parts) == 3:
                title = f"{METRIC_ICON[metric]} {METRIC_TITLE[metric]}"
                await self.reply(chat_id, f"Монета метрики {title}:",
                                 self._alert_coins_kb(user, metric),
                                 message_id=message_id)
                return
            val = parts[3]
            if val == "?":
                self._wait_alert[tg_id] = f"coin:{metric}"
                await self.reply(
                    chat_id,
                    "Пришлите тикер, например BTC или ETHUSDT.\n/cancel — отмена.",
                    self._kb_back("al"), message_id=message_id)
                return
            coins = dict(cfg.get("coins") or {})
            coins[metric] = canon_symbol(val)
            cfg["coins"] = coins
            self._alert_save(user, cfg)
            await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                             message_id=message_id)
            return
        if data == "al:w":
            await self.reply(chat_id, "Окно какой метрики? У каждой своё.",
                             self._alert_win_kb(user), message_id=message_id)
            return
        if data.startswith("al:w:"):
            from alerts import METRIC_ICON, METRIC_TITLE, WINDOW_PRESETS, window_minutes
            parts = data.split(":")
            # al:w:<метрика> — пресеты этой метрики; al:w:<метрика>:<минут|?>
            if len(parts) >= 3 and parts[2] in ("liq", "cvd", "oi"):
                metric = parts[2]
                if len(parts) == 3:
                    await self.reply(chat_id,
                                     f"{METRIC_ICON[metric]} Окно {METRIC_TITLE[metric]}:",
                                     self._alert_win_metric_kb(metric, cfg),
                                     message_id=message_id)
                    return
                val = parts[3]
                if val == "?":
                    self._wait_alert[tg_id] = f"win:{metric}"
                    await self.reply(
                        chat_id,
                        "Сколько минут в окне? Число, например 7.\n/cancel — отмена.",
                        self._kb_back("al"), message_id=message_id)
                    return
                try:
                    n = int(val)
                except ValueError:
                    n = WINDOW_PRESETS[0]
                wins = dict(cfg.get("windows") or {})
                wins[metric] = window_minutes(n)
                cfg["windows"] = wins
                if metric in (cfg.get("watch") or []):
                    cfg["window_min"] = wins[metric]
                self._alert_save(user, cfg)
                await self.reply(chat_id, self._alert_text(user), self._alert_kb(user),
                                 message_id=message_id)
                return
            # старый колбэк al:w:<минут> из уже отправленных меню
            try:
                n = window_minutes(int(parts[2]))
            except (IndexError, ValueError):
                n = window_minutes(0)
            wins = dict(cfg.get("windows") or {})
            for m in (cfg.get("watch") or ["liq"]):
                wins[m] = n
            cfg["windows"] = wins
            cfg["window_min"] = n
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
