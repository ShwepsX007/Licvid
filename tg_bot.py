"""Telegram-бот LiqScope: регистрация, кабинет, статистика, админка.

Ходит в Bot API через aiohttp (уже в зависимостях). Без токена — просто не стартует.
Команды пользователя зеркалят кабинет на сайте; админские — панель управления.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp

log = logging.getLogger("liqscope.bot")

API = "https://api.telegram.org/bot{token}/{method}"


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
    ):
        self.token = (token or "").strip()
        self.store = store
        self.public_url = (public_url or "").rstrip("/")
        self.health_fn = health_fn or (lambda: {})
        self.stats_fn = stats_fn or (lambda: {})
        self.liqs_fn = liqs_fn or (lambda: [])
        self.ws_clients_fn = ws_clients_fn or (lambda: 0)
        self.username = ""
        self.bot_id = 0
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._wait_broadcast: Dict[int, bool] = {}  # tg_id -> waiting for text

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def deep_link(self, payload: str) -> str:
        if not self.username:
            return ""
        return f"https://t.me/{self.username}?start={payload}"

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
        log.info("Telegram-бот @%s запущен", self.username)

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
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

    async def send(self, chat_id: int, text: str, markup: Optional[dict] = None,
                   parse: str = "HTML") -> bool:
        body: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:3900],
            "parse_mode": parse,
            "disable_web_page_preview": True,
        }
        if markup:
            body["reply_markup"] = markup
        res = await self._call("sendMessage", body)
        return bool(res and res.get("ok"))

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

    async def reply(self, chat_id: int, text: str, markup: Optional[dict] = None,
                    message_id: Optional[int] = None) -> bool:
        if message_id:
            if await self.edit(chat_id, message_id, text, markup):
                return True
        return await self.send(chat_id, text, markup)

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
                    "allowed_updates": ["message", "callback_query"],
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
        elif text.startswith("/help"):
            await self.send(chat_id, self._help(user), self._menu(user))
        elif text.startswith("/cabinet"):
            await self.send(chat_id, self._cabinet_text(user), self._kb_back("nav:home"))
        elif text.startswith("/stats"):
            await self.send(chat_id, self._stats_text(), self._kb_back("nav:home"))
        elif text.startswith("/status") or text.startswith("/health"):
            await self.send(chat_id, self._health_text(), self._kb_back("nav:home"))
        elif text.startswith("/liq"):
            await self.send(chat_id, self._liq_text(), self._kb_back("nav:home"))
        elif text.startswith("/services"):
            await self.send(chat_id, self._services_text(user), self._services_kb(user))
        elif text.startswith("/terminal"):
            await self.send(chat_id, self._terminal_text(), self._kb_back("nav:home"))
        elif text.startswith("/admin"):
            await self._cmd_admin(chat_id, user)
        elif text.startswith("/users"):
            await self._cmd_users(chat_id, user)
        elif text.startswith("/visits"):
            await self._cmd_visits(chat_id, user)
        elif text.startswith("/broadcast"):
            await self._cmd_broadcast(chat_id, user, text)
        else:
            await self.send(chat_id, "Не понял. Нажмите кнопку или /help.", self._menu(user))

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
        if data in ("menu", "back", "nav:home", "home"):
            self._wait_broadcast.pop(tg_id, None)
        # Сначала снимаем «часики»: если answer уйдёт после edit или с
        # пустым text, клиент залипает и следующие кнопки не нажимаются.
        await self.answer_cb(cb["id"])
        try:
            text, markup = self._screen(user, data)
            ok = await self.reply(chat_id, text, markup, message_id=message_id)
            if not ok:
                log.warning("меню не обновилось data=%s chat=%s msg=%s",
                            data, chat_id, message_id)
        except Exception as e:
            log.warning("cb %s: %s", data, e)

    def _screen(self, user: dict, data: str) -> tuple:
        """Текст и клавиатура экрана. Кнопки меняются на месте, не новым сообщением."""
        if data in ("menu", "back", "nav:home", "home", "help", ""):
            return (self._home_text(user) if data != "help" else self._help(user),
                    self._menu(user))
        if data == "cabinet":
            return self._cabinet_text(user), self._kb_back("nav:home")
        if data == "stats":
            return self._stats_text(), self._kb_back("nav:home")
        if data == "health":
            return self._health_text(), self._kb_back("nav:home")
        if data == "a:health" and user.get("is_admin"):
            return self._health_text(), self._kb_back("nav:admin")
        if data == "liq":
            return self._liq_text(), self._kb_back("nav:home")
        if data == "terminal":
            return self._terminal_text(), self._kb_back("nav:home")
        if data == "services":
            return self._services_text(user), self._services_kb(user)
        if data.startswith("svc:"):
            slug = data.split(":", 1)[1]
            have = set(self.store.user_service_slugs(user["id"]))
            on = slug not in have
            self.store.toggle_user_service(user["id"], slug, on)
            return self._services_text(user), self._services_kb(user)
        if data in ("admin", "nav:admin") and user.get("is_admin"):
            self._wait_broadcast.pop(int(user.get("tg_id") or 0), None)
            return self._admin_text(), self._admin_kb()
        if data == "users" and user.get("is_admin"):
            return self._users_text(), self._kb_back("nav:admin")
        if data == "visits" and user.get("is_admin"):
            return self._visits_text(), self._kb_back("nav:admin")
        if data == "broadcast" and user.get("is_admin"):
            self._wait_broadcast[int(user.get("tg_id") or 0)] = True
            return ("Пришлите текст рассылки следующим сообщением.\n"
                    "/cancel — отмена.", self._kb_back("nav:admin"))
        return self._home_text(user), self._menu(user)

    async def _cmd_start(self, chat_id: int, user: dict, text: str) -> None:
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if payload.startswith("login_"):
            nonce = payload[6:]
            if self.store.confirm_nonce(nonce, user["id"]):
                site = self.public_url or "сайт"
                await self.send(
                    chat_id,
                    f"Вход подтверждён, {_esc(user['display_name'])}.\n"
                    f"Вернитесь во вкладку браузера — кабинет откроется сам.\n\n"
                    f"Сайт: {site}/cabinet",
                    self._menu(user),
                )
                return
            await self.send(chat_id, "Код входа недействителен или устарел. Нажмите «Войти» на сайте ещё раз.",
                            self._menu(user))
            return
        welcome = self.store.get_setting(
            "bot_welcome",
            "Это бот <b>LiqScope</b> — живой терминал ликвидаций крипто-фьючерсов.\n"
            "Здесь тот же кабинет, что и на сайте: статистика рынка, биржи, сервисы.",
        )
        site = self.public_url
        extra = f"\nКабинет: {site}/cabinet\nТерминал: {site}/terminal" if site else ""
        await self.send(
            chat_id,
            f"Привет, {_esc(user['display_name'])}!\n\n{welcome}{extra}",
            self._menu(user),
        )

    async def _cmd_admin(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.send(chat_id, "Недостаточно прав.", self._menu(user))
            return
        await self.send(chat_id, self._admin_text(), self._admin_kb())

    async def _cmd_users(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.send(chat_id, "Недостаточно прав.", self._menu(user))
            return
        await self.send(chat_id, self._users_text(), self._kb_back("nav:admin"))

    async def _cmd_visits(self, chat_id: int, user: dict) -> None:
        if not user["is_admin"]:
            await self.send(chat_id, "Недостаточно прав.", self._menu(user))
            return
        await self.send(chat_id, self._visits_text(), self._kb_back("nav:admin"))

    async def _cmd_broadcast(self, chat_id: int, user: dict, text: str) -> None:
        if not user["is_admin"]:
            await self.send(chat_id, "Недостаточно прав.", self._menu(user))
            return
        rest = text.split(maxsplit=1)
        body = rest[1].strip() if len(rest) > 1 else ""
        if not body:
            self._wait_broadcast[int(user["tg_id"])] = True
            await self.send(chat_id,
                            "Пришлите текст рассылки следующим сообщением.\n/cancel — отмена.",
                            self._kb_back("nav:admin"))
            return
        await self.send(chat_id, "Рассылаю…")
        st = await self.broadcast(body, actor_id=user["id"])
        await self.send(chat_id, f"Готово: {st['ok']}/{st['total']}.", self._admin_kb())

    def _kb_back(self, to: str = "nav:home") -> dict:
        aliases = {"menu": "nav:home", "home": "nav:home", "back": "nav:home",
                   "admin": "nav:admin"}
        to = aliases.get(to, to)
        return {"inline_keyboard": [[{"text": "← Назад", "callback_data": to}]]}

    def _menu(self, user: dict) -> dict:
        rows = [
            [{"text": "👤 Кабинет", "callback_data": "cabinet"},
             {"text": "⚡ Терминал", "callback_data": "terminal"}],
            [{"text": "📊 Статистика", "callback_data": "stats"},
             {"text": "🩺 Биржи", "callback_data": "health"}],
            [{"text": "🛠 Сервисы", "callback_data": "services"},
             {"text": "📰 Лента liq", "callback_data": "liq"}],
        ]
        if user.get("is_admin"):
            rows.append([{"text": "★ Админка", "callback_data": "admin"}])
        return {"inline_keyboard": rows}

    def _admin_kb(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "👥 Пользователи", "callback_data": "users"},
             {"text": "📈 Визиты", "callback_data": "visits"}],
            [{"text": "📣 Рассылка", "callback_data": "broadcast"},
             {"text": "🩺 Здоровье", "callback_data": "a:health"}],
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
            f"Выберите раздел — кнопки ниже."
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
        ]
        if user.get("is_admin"):
            lines += [
                "",
                "<b>Админ</b>",
                "/admin /users /visits",
                "/broadcast текст — рассылка всем",
            ]
        return "\n".join(lines)

    def _cabinet_text(self, user: dict) -> str:
        un = f"@{_esc(user['username'])}" if user["username"] else "—"
        site = self.public_url
        link = f"\nСайт: {site}/cabinet" if site else ""
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
        site = self.public_url or ""
        if site:
            return f"Терминал ликвидаций:\n{site}/terminal"
        return "Терминал на сайте LiqScope (задайте LIQSCOPE_PUBLIC_URL, чтобы бот давал прямую ссылку)."

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
            "Те же, что на сайте. Сейчас закладываем каркас — алерты, корреляции и сторож появятся в кабинете и здесь.",
            "",
        ]
        for s in self.store.list_services(include_disabled=False):
            mark = "✓" if s["slug"] in have else "○"
            lock = " · скоро" if s["coming_soon"] else ""
            lines.append(f"{mark} {s['icon']} <b>{_esc(s['title'])}</b>{lock}")
            if s.get("description"):
                lines.append(f"    {_esc(s['description'])}")
        lines.append("\nНажмите сервис, чтобы подписаться (лист ожидания, пока он «скоро»).")
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
        site = self.public_url
        link = f"\nПанель: {site}/admin" if site else ""
        return (
            f"<b>Админка LiqScope</b>\n"
            f"Пользователи: {c['total']} (за сутки {c['active_24h']}, новых {c['new_24h']})\n"
            f"Визиты сегодня: {v['today_views']} / {v['today_uniques']} уник.\n"
            f"Онлайн WS: {self.ws_clients_fn()}\n"
            f"Биржи в эфире: {len(live)}"
            f"{link}"
        )
