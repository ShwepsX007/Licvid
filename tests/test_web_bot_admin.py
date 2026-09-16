"""Админка бота на сайте: /api/admin/bot* отвечает и правда управляет ботом.

Бота изображает заглушка: она запоминает вызовы (публикация сводки, дайджеста,
правки каналов) и отвечает так же, как настоящий TelegramBot на getChat и
getChatMember. Проверяем, что сайт — полноценная замена кнопок в Telegram:
каналы, контроль постов, публикация, время дайджеста, здоровье и ИИ.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api_digest  # noqa: E402
import web_account  # noqa: E402
import web_bot_admin  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402
from daily_digest import DigestStore  # noqa: E402

ADMIN_EMAIL = "boss@liqscope.online"
RU_CHAT = "-1001112223334"
EN_CHAT = "-1005556667778"


class FakeTelegram:
    """Мини-бот: хватает того, что зовёт админка бота."""

    def __init__(self, store: Store):
        self.store = store
        self.token = "test-token"
        self.username = "LiqScopeBot"
        self.admins_tg = {1001: "админ"}
        self._bindings = {"ru": "", "en": "", "titles": {}}
        self.calls: list = []
        self._digest_err = ""
        self.chats = {RU_CHAT: "LiqScopeRUS", EN_CHAT: "LiqScopeEng"}
        self.can_post = True
        self.chan_ok = True
        self._daily_state = {"ok": True, "day": "2026-09-17", "published": {}}

    # --- состояние ---------------------------------------------------------
    def poll_status(self) -> dict:
        return {"enabled": True, "running": True, "task_alive": True,
                "poll_ok_sec": 3.0, "last_update_sec": 1.0, "send_fails": 0,
                "conflict": False, "watchdog_restarts": 0, "fails": 0, "offset": 1,
                "updates_seen": 10, "last_err": "", "waits": 0}

    def poll_line(self) -> str:
        return "🤖 Бот: опрос ок (3 с) · апдейт 1 с назад"

    def ai_status(self) -> dict:
        return {"enabled": True, "providers": [{"name": "gemini"}, {"name": "groq"}],
                "last": {"ok": True, "provider": "gemini", "ms": 812},
                "review": self._review_on()}

    # --- каналы ------------------------------------------------------------
    def channel_chat_id(self) -> str:
        return self._bindings["ru"]

    def channel_chat_id_en(self) -> str:
        return self._bindings["en"]

    def channel_role(self, code: str) -> dict:
        cid = self.channel_chat_id_en() if code == "en" else self.channel_chat_id()
        title = (self._bindings["titles"] or {}).get(code) or (
            "LiqScopeEng" if code == "en" else "LiqScopeRUS")
        return {"code": code, "id": cid, "title": title,
                "url": "https://t.me/" + title}

    def channel_source(self, code: str = "ru") -> str:
        return "bot" if self._bindings.get(code) else "env"

    def channel_route_text(self) -> str:
        return ("🇷🇺 LiqScopeRUS — русский пост\n🇬🇧 LiqScopeEng — английский пост")

    def _channel_bindings(self) -> dict:
        return {"ru": self._bindings["ru"], "en": self._bindings["en"],
                "titles": dict(self._bindings["titles"])}

    def _save_channel_bindings(self, d: dict) -> None:
        self._bindings = {"ru": str(d.get("ru") or ""), "en": str(d.get("en") or ""),
                          "titles": dict(d.get("titles") or {})}

    def remember_channel_role(self, code: str, chat_id, title: str = "") -> str:
        cid = str(chat_id or "").strip()
        if not cid or code not in ("ru", "en"):
            return ""
        self._bindings[code] = cid
        if title:
            self._bindings["titles"][code] = title
        other = "en" if code == "ru" else "ru"
        if self._bindings.get(other) == cid:
            self._bindings[other] = ""
        return cid

    async def verify_channel_roles(self, force: bool = False) -> str:
        self.calls.append(("verify", bool(force)))
        return "Роли каналов сверены по названиям." if self.chan_ok else "каналы перепутаны"

    async def _call(self, method: str, payload: dict):
        self.calls.append((method, dict(payload or {})))
        if method == "getMe":
            return {"ok": True, "result": {"id": 4242, "username": self.username}}
        if method == "getChat":
            cid = str((payload or {}).get("chat_id"))
            if cid in self.chats:
                return {"ok": True, "result": {"id": cid, "title": self.chats[cid]}}
            return {"ok": False, "description": "chat not found"}
        if method == "getChatMember":
            return {"ok": True, "result": {"status": "administrator",
                                           "can_post_messages": self.can_post}}
        return {"ok": False, "description": "unknown method"}

    # --- контроль и публикация --------------------------------------------
    def _review_on(self) -> bool:
        return str(self.store.get_setting("channel_digest_review") or "") in ("1", "on")

    def _set_review(self, on: bool, actor_id=None) -> None:
        self.store.set_setting("channel_digest_review", "1" if on else "0",
                               actor_id=actor_id)

    async def post_channel_digest(self, force: bool = False) -> bool:
        self.calls.append(("post_channel", bool(force)))
        return bool(self.chan_ok)

    async def post_daily_digest(self, force: bool = True, langs=("ru", "en"),
                                reason: str = "bot") -> bool:
        self.calls.append(("post_daily", list(langs), reason))
        self._daily_state = {"ok": True, "day": "2026-09-17", "published": {}}
        return True

    def daily_status(self) -> dict:
        return {"review": self._review_on(), "wired": True, **self._daily_state}

    def digest_fn(self) -> dict:
        return {"window_h": 4, "board": {}, "total_usd": 1.0}

    async def _ai_headline(self, snap: dict, variant: int = 0, lang: str = "ru"):
        return "Ночь на рынке выдалась горячей — снесло $120M", "ИИ: gemini · 812 мс"


def _health() -> dict:
    return {"sources": {
        "binance": {"connected": True, "events": 120, "seconds_since_event": 4,
                    "attempts": 1, "respawns": 0},
        "bybit": {"connected": False, "events": 0, "last_error": "timeout",
                  "attempts": 3},
        "okx": {"connected": False, "events": 0, "supervisor_alive": False,
                "attempts": 9, "respawns": 2},
        "prices": {"connected": True, "events": 9999},
    }}


class BotAdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=[ADMIN_EMAIL])
        admin = self.store.create_email_user(ADMIN_EMAIL, password_hash="x")["user"]
        self.admin_id = int(admin["id"])
        self.assertTrue(admin["is_admin"], "админ должен получить флаг по адресу")
        self.token = self.store.create_session(self.admin_id)
        plain = self.store.create_email_user("vasya@example.com", password_hash="x")["user"]
        self.plain_id = int(plain["id"])
        self.plain_token = self.store.create_session(self.plain_id)

        self.bot = FakeTelegram(self.store)
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        web_bot_admin.ctx.bot = self.bot
        web_bot_admin.ctx.store = self.store
        web_bot_admin.ctx.health_fn = _health
        web_bot_admin.ctx.ws_clients_fn = lambda: 7
        api_digest.ctx.store = DigestStore(os.path.join(self.tmp.name, "dig.json"))
        api_digest.ctx.env_locked = {}

        self.app = FastAPI()
        self.app.state.digest_scheduler = None
        web_bot_admin.register_bot_admin_routes(self.app)
        self.client = TestClient(self.app)
        self.admin = TestClient(self.app)
        self.admin.cookies.set(COOKIE_SID, self.token)
        self.plain = TestClient(self.app)
        self.plain.cookies.set(COOKIE_SID, self.plain_token)

    def tearDown(self):
        web_bot_admin.ctx.bot = None
        web_bot_admin.ctx.store = None
        web_account.ctx.store = None
        api_digest.ctx.store = DigestStore("")
        self.tmp.cleanup()

    # --- доступ ------------------------------------------------------------
    def test_snapshot_requires_admin(self):
        self.assertEqual(self.client.get("/api/admin/bot").status_code, 401)
        self.assertEqual(self.plain.get("/api/admin/bot").status_code, 403)

    def test_snapshot_has_everything_for_panel(self):
        self.bot.remember_channel_role("ru", RU_CHAT, "LiqScopeRUS")
        self.bot.remember_channel_role("en", EN_CHAT, "LiqScopeEng")
        api_digest.ctx.store.save({"id": "2026-09-16", "day": "2026-09-16",
                                   "facts": {"liq_total_usd": 1234567.0,
                                             "liq_count": 640},
                                   "published": {"ru": {"ok": True}}})
        d = self.admin.get("/api/admin/bot").json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["bot"]["running"])
        self.assertEqual(d["bot"]["username"], "LiqScopeBot")
        self.assertIn("опрос ок", d["bot"]["line"])
        self.assertEqual(d["channels"]["ru"]["id"], RU_CHAT)
        self.assertEqual(d["channels"]["en"]["id"], EN_CHAT)
        self.assertFalse(d["review"])
        self.assertEqual(d["health"]["live"], 1)
        self.assertEqual(d["health"]["total"], 3)
        states = {r["name"]: r["state"] for r in d["health"]["rows"]}
        self.assertEqual(states["okx"], "dead")
        self.assertEqual(states["bybit"], "down")
        self.assertEqual(d["daily"]["last"]["day"], "2026-09-16")
        self.assertIn("LlqScopeEng".replace("Llq", "Liq"),
                      d["channels"]["en"]["title"])
        # права бота в канале спрашиваются сразу — админ видит, дойдёт ли пост
        self.assertTrue(d["probes"]["ru"]["can_post"])
        self.assertEqual(d["probes"]["ru"]["title"], "LiqScopeRUS")
        self.assertTrue(d["ai"]["enabled"])
        self.assertEqual(d["ai"]["providers"][0]["name"], "gemini")

    def test_no_bot_returns_503_not_page_error(self):
        web_bot_admin.ctx.bot = None
        d = self.admin.get("/api/admin/bot")
        self.assertEqual(d.status_code, 200)
        self.assertFalse(d.json()["bot"]["present"])
        r = self.admin.post("/api/admin/bot/publish", json={"kind": "channel"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"], "no_bot")

    # --- контроль постов ---------------------------------------------------
    def test_review_toggle_writes_setting(self):
        d = self.admin.post("/api/admin/bot/review", json={"on": True}).json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["review"])
        self.assertEqual(self.store.get_setting("channel_digest_review"), "1")
        self.assertTrue(self.bot._review_on())
        d = self.admin.post("/api/admin/bot/review", json={"on": False}).json()
        self.assertFalse(d["review"])
        self.assertEqual(self.store.get_setting("channel_digest_review"), "0")
        self.assertEqual(self.plain.post("/api/admin/bot/review",
                                         json={"on": True}).status_code, 403)

    # --- каналы ------------------------------------------------------------
    def test_channels_saved_with_telegram_check(self):
        d = self.admin.post("/api/admin/bot/channels",
                            json={"ru": RU_CHAT, "en": EN_CHAT}).json()
        self.assertTrue(d["ok"])
        self.assertEqual(self.bot.channel_chat_id(), RU_CHAT)
        self.assertEqual(self.bot.channel_chat_id_en(), EN_CHAT)
        self.assertEqual(d["probes"]["ru"]["title"], "LiqScopeRUS")
        # название подтянулось из Telegram и попало в роль
        self.assertEqual(self.bot.channel_role("ru")["title"], "LiqScopeRUS")

    def test_channels_reject_typo_and_invisible(self):
        bad = self.admin.post("/api/admin/bot/channels", json={"ru": "LiqScopeRUS"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["error"], "bad_id")
        hidden = self.admin.post("/api/admin/bot/channels", json={"ru": "-1009999999999"})
        self.assertEqual(hidden.status_code, 400)
        self.assertEqual(hidden.json()["error"], "not_visible")
        # ничего не поменялось
        self.assertEqual(self.bot.channel_chat_id(), "")

    def test_channels_can_be_unbound(self):
        self.bot.remember_channel_role("ru", RU_CHAT, "LiqScopeRUS")
        d = self.admin.post("/api/admin/bot/channels", json={"ru": ""}).json()
        self.assertTrue(d["ok"])
        self.assertEqual(self.bot.channel_chat_id(), "")

    def test_channels_swap(self):
        self.bot.remember_channel_role("ru", RU_CHAT, "LiqScopeRUS")
        self.bot.remember_channel_role("en", EN_CHAT, "LiqScopeEng")
        d = self.admin.post("/api/admin/bot/channels/swap").json()
        self.assertTrue(d["ok"])
        self.assertEqual(self.bot.channel_chat_id(), EN_CHAT)
        self.assertEqual(self.bot.channel_chat_id_en(), RU_CHAT)
        self.assertEqual(self.bot.channel_role("ru")["title"], "LiqScopeEng")
        # обменять нечего, если канал один
        self.bot._bindings["en"] = ""
        self.assertEqual(
            self.admin.post("/api/admin/bot/channels/swap").status_code, 400)

    def test_channels_verify_reports_roles_and_rights(self):
        self.bot.remember_channel_role("ru", RU_CHAT, "LiqScopeRUS")
        self.bot.remember_channel_role("en", EN_CHAT, "LiqScopeEng")
        self.bot.can_post = False
        d = self.admin.post("/api/admin/bot/channels/verify").json()
        self.assertTrue(d["ok"])
        self.assertIn("сверены", d["note"])
        self.assertFalse(d["probes"]["ru"]["can_post"])
        self.assertIn("Публикация сообщений", d["probes"]["ru"]["note"])
        self.assertIn(("verify", True), self.bot.calls)

    # --- публикация --------------------------------------------------------
    def test_publish_channel_now(self):
        d = self.admin.post("/api/admin/bot/publish", json={"kind": "channel"}).json()
        self.assertTrue(d["ok"])
        self.assertIn(("post_channel", True), self.bot.calls)
        self.assertIn("Сводка ушла", d["message"])

    def test_publish_channel_with_review_says_draft(self):
        self.bot._set_review(True)
        d = self.admin.post("/api/admin/bot/publish", json={"kind": "channel"}).json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["draft"])
        self.assertIn("Черновик", d["message"])

    def test_publish_daily_now_filters_langs(self):
        d = self.admin.post("/api/admin/bot/publish",
                            json={"kind": "daily", "langs": "en, xx"}).json()
        self.assertTrue(d["ok"])
        daily = [c for c in self.bot.calls if c[0] == "post_daily"][-1]
        self.assertEqual(daily[1], ["en"])
        self.assertIn("site:", daily[2])
        self.assertIn("2026-09-17", d["message"])

    def test_publish_daily_when_channels_missing(self):
        async def fail(force=True, langs=("ru", "en"), reason="bot"):
            self.bot._digest_err = "канал не привязан — перешлите боту пост из канала"
            self.bot._daily_state = {"ok": False, "day": "2026-09-17"}
            return False
        self.bot.post_daily_digest = fail
        d = self.admin.post("/api/admin/bot/publish", json={"kind": "daily"}).json()
        self.assertFalse(d["ok"])
        self.assertIn("канал не привязан", d["message"])

    # --- ИИ ----------------------------------------------------------------
    def test_ai_check_shows_headline(self):
        d = self.admin.post("/api/admin/bot/ai-check", json={"lang": "ru"}).json()
        self.assertTrue(d["ok"])
        self.assertIn("$120M", d["head"])
        self.assertIn("gemini", d["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
