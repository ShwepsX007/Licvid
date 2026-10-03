"""Удаление старых выпусков: архив сайта и уже вышедшие посты в Telegram.

Админ чистит архив — выпуск дня и сводки по часам уходят и со страниц
/digest, /hourly, и из каналов. Тут проверяем главное: запись исчезает из
файла архива, а в Telegram уходят ровно те id сообщений, которые запомнил
бот; без бота (или без записанных id) удаление не врёт — оно честно
отказывается и ничего не сносит на сайте.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import api_digest  # noqa: E402
import api_hourly  # noqa: E402
import hourly_posts  # noqa: E402
from daily_digest import DigestStore  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402
from hourly_posts import PostStore, post_id  # noqa: E402

DAY = "2026-02-01"
DAY2 = "2026-01-31"
NOW = 1_770_000_000.0


class FakeBot:
    """Бот, который «удаляет» сообщения в памяти и всё помнит."""

    def __init__(self, running: bool = True):
        self.running = running
        self.deleted: list = []
        self.channel_sent = {}

    async def delete_message(self, chat_id, message_id) -> bool:
        self.deleted.append((str(chat_id), int(message_id)))
        return self.running


def digest_rec(day: str = DAY, *, mid_ru: int = 0, mid_en: int = 0) -> dict:
    rec = {
        "id": day, "day": day, "created": NOW, "updated": NOW, "window_h": 24,
        "facts": {"liq_total_usd": 1_000_000.0, "liq_count": 12},
        "ai": {}, "published": {},
    }
    if mid_ru:
        rec["published"]["ru"] = {"ok": True, "at": NOW, "chat": "-100ru",
                                  "message_id": mid_ru, "extra": [mid_ru + 1]}
    if mid_en:
        rec["published"]["en"] = {"ok": True, "at": NOW, "chat": "-100en",
                                  "message_id": mid_en}
    return rec


def hourly_rec(ts: float = NOW, day: str = DAY, mid: int = 0) -> dict:
    rec = {
        "id": post_id(ts), "ts": ts, "day": day, "window_h": 4, "interval_h": 4,
        "total_usd": 5000.0, "liq_count": 3, "texts": {"ru": "пост", "en": "post"},
        "sent": {"ru": True, "en": True}, "n": 1,
    }
    if mid:
        rec["tg"] = {"ru": {"chat": "-100hourly", "message_id": mid}}
    return rec


class StoreTest(unittest.TestCase):
    """Хранилища умеют снимать запись — и это переживает перезагрузку."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.digests = os.path.join(self.tmp.name, "digests.json")
        self.posts = os.path.join(self.tmp.name, "posts.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_digest_mark_published_keeps_the_message_id(self):
        store = DigestStore(self.digests)
        store.save(digest_rec())
        store.mark_published(DAY, "ru", chat="-100ru", ok=True,
                             message_id=555, extra=[556])
        row = json.load(open(self.digests, encoding="utf-8"))
        rec = row["items"][0] if isinstance(row, dict) else row[0]
        self.assertEqual(rec["published"]["ru"]["message_id"], 555)
        self.assertEqual(rec["published"]["ru"]["extra"], [556])

    def test_digest_remove_deletes_the_day_and_survives_reload(self):
        store = DigestStore(self.digests)
        store.save(digest_rec())
        store.save(digest_rec(DAY2))
        gone = store.remove(DAY)
        self.assertEqual(gone["day"], DAY)
        self.assertEqual([r["day"] for r in store.list()], [DAY2])
        # новый объект читает файл: запись не вернулась
        self.assertEqual([r["day"] for r in DigestStore(self.digests).list()], [DAY2])

    def test_failed_republish_keeps_the_previous_message_id(self):
        """Повторная отправка не удалась — id прошлого поста не теряем."""
        store = DigestStore(self.digests)
        store.save(digest_rec())
        store.mark_published(DAY, "ru", chat="-100ru", ok=True, message_id=700)
        store.mark_published(DAY, "ru", ok=False)
        row = store.get(DAY)["published"]["ru"]
        self.assertFalse(row["ok"])
        self.assertEqual(row["message_id"], 700)
        self.assertEqual(row["chat"], "-100ru")

    def test_digest_remove_unknown_day_is_none(self):
        store = DigestStore(self.digests)
        store.save(digest_rec())
        self.assertIsNone(store.remove("2020-01-01"))
        self.assertEqual(len(store.list()), 1)

    def test_hourly_remove_and_remove_day(self):
        store = PostStore(self.posts)
        store.add(hourly_rec(NOW, DAY))
        store.add(hourly_rec(NOW - 4 * 3600, DAY))
        store.add(hourly_rec(NOW - 86400, DAY2))
        self.assertEqual(len(store.remove_day(DAY)), 2)
        self.assertEqual([r["day"] for r in store.list()], [DAY2])
        self.assertEqual(len(PostStore(self.posts).list()), 1)
        rid = post_id(NOW - 86400)
        self.assertIsNotNone(store.remove(rid))
        self.assertEqual(store.list(), [])
        self.assertIsNone(store.remove(rid))
        self.assertEqual(store.remove_day(DAY), [])


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.accounts = Store(os.path.join(root, "a.db"), secret="s")
        admin = self.accounts.create_email_user("admin@x.io", password_hash="x")["user"]
        with self.accounts._lock:                     # noqa: SLF001
            self.accounts._db.execute("UPDATE users SET is_admin=1 WHERE id=?",
                                      (int(admin["id"]),))
            self.accounts._db.commit()
        self.user = self.accounts.create_email_user("user@x.io",
                                                    password_hash="x")["user"]

        import web_account
        web_account.ctx.store = self.accounts
        web_account.ctx.secret = "s"

        self.bot = FakeBot()
        app = FastAPI()
        self.register(app)
        self.app = app
        self.guest = TestClient(app)
        self.admin_c = self.client_for(int(admin["id"]))
        self.user_c = self.client_for(int(self.user["id"]))

    def tearDown(self):
        self.tmp.cleanup()

    def client_for(self, uid: int) -> TestClient:
        c = TestClient(self.app)
        c.cookies.set(COOKIE_SID, self.accounts.create_session(int(uid)))
        return c

    def register(self, app) -> None:                 # pragma: no cover - переопределяют
        raise NotImplementedError


class DigestApiTest(ApiTestBase):
    def register(self, app) -> None:
        api_digest.register_digest_routes(app)

    def setUp(self):
        super().setUp()
        self.store = DigestStore(os.path.join(self.tmp.name, "digests.json"))
        api_digest.ctx.store = self.store
        api_digest.ctx.page_ok = True
        api_digest.ctx.public_url = "https://liqscope.online"
        api_digest.ctx.bot = self.bot
        api_digest.ctx.sent_ids_fn = None
        self.store.save(digest_rec(DAY, mid_ru=11, mid_en=22))
        self.store.save(digest_rec(DAY2))

    def tearDown(self):
        api_digest.ctx.store = DigestStore("")
        api_digest.ctx.bot = None
        api_digest.ctx.sent_ids_fn = None
        super().tearDown()

    def test_archive_lists_days_with_their_tg_state(self):
        res = self.admin_c.get("/api/admin/digest/archive")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual([x["day"] for x in data["items"]], [DAY, DAY2])
        first = data["items"][0]
        self.assertEqual(first["tg"], ["en", "ru"])
        self.assertTrue(first["tg_ready"])
        self.assertEqual(first["url"], "/digest?day=" + DAY)
        self.assertFalse(data["items"][1]["tg_ready"])

    def test_archive_is_admin_only(self):
        self.assertEqual(self.guest.get("/api/admin/digest/archive").status_code, 401)
        self.assertEqual(self.user_c.get("/api/admin/digest/archive").status_code, 403)

    def test_delete_removes_the_day_from_the_site(self):
        res = self.admin_c.post("/api/admin/digest/%s/delete" % DAY, json={})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.assertIsNone(self.store.get(DAY))
        self.assertEqual(self.bot.deleted, [])         # в Telegram не лезли

    def test_delete_unknown_day_is_404(self):
        res = self.admin_c.post("/api/admin/digest/2020-01-01/delete", json={})
        self.assertEqual(res.status_code, 404)

    def test_delete_needs_admin(self):
        self.assertEqual(self.guest.post("/api/admin/digest/%s/delete" % DAY,
                                         json={}).status_code, 401)
        self.assertEqual(self.user_c.post("/api/admin/digest/%s/delete" % DAY,
                                          json={}).status_code, 403)
        self.assertIsNotNone(self.store.get(DAY))

    def test_delete_with_tg_removes_both_channels(self):
        res = self.admin_c.post("/api/admin/digest/%s/delete" % DAY, json={"tg": True})
        data = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(sorted(self.bot.deleted), [("-100en", 22), ("-100ru", 11),
                                                   ("-100ru", 12)])
        self.assertEqual(data["tg"]["ru"]["deleted"], 2)   # пост дня и хвост
        self.assertEqual(data["tg"]["en"]["deleted"], 1)
        self.assertIsNone(self.store.get(DAY))

    def test_bot_off_keeps_the_day_on_the_site(self):
        self.bot.running = False
        res = self.admin_c.post("/api/admin/digest/%s/delete" % DAY, json={"tg": True})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "bot_off")
        self.assertIsNotNone(self.store.get(DAY))
        self.assertEqual(self.bot.deleted, [])

    def test_tg_flag_without_ids_just_removes_the_site_copy(self):
        res = self.admin_c.post("/api/admin/digest/%s/delete" % DAY2, json={"tg": True})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["tg"], {})
        self.assertIsNone(self.store.get(DAY2))

    def test_fresh_ids_from_the_bot_are_used_before_mark_published(self):
        rec = digest_rec(DAY2)                        # id в записи ещё нет
        api_digest.ctx.sent_ids_fn = None
        self.assertEqual(api_digest.sent_map(rec), {})
        self.bot.channel_sent = {"ru": {"chat": "-100ru", "message_id": 77,
                                        "extra": [78]}}
        api_digest.ctx.sent_ids_fn = lambda: dict(self.bot.channel_sent)
        fresh = api_digest.sent_map(rec)["ru"]
        self.assertEqual(fresh["message_id"], 77)
        self.assertEqual(fresh["extra"], [78])
        api_digest.ctx.sent_ids_fn = None


class HourlyApiTest(ApiTestBase):
    def register(self, app) -> None:
        api_hourly.register_hourly_routes(app)

    def setUp(self):
        super().setUp()
        self.store = PostStore(os.path.join(self.tmp.name, "posts.json"))
        api_hourly.ctx.store = self.store
        api_hourly.ctx.page_ok = True
        api_hourly.ctx.public_url = "https://liqscope.online"
        api_hourly.ctx.bot = self.bot
        self.first = hourly_rec(NOW, DAY, mid=31)["id"]
        self.second = hourly_rec(NOW - 4 * 3600, DAY, mid=32)["id"]
        self.old = hourly_rec(NOW - 86400, DAY2, mid=33)["id"]
        for rec in (hourly_rec(NOW, DAY, mid=31),
                    hourly_rec(NOW - 4 * 3600, DAY, mid=32),
                    hourly_rec(NOW - 86400, DAY2, mid=33)):
            self.store.add(rec)

    def tearDown(self):
        api_hourly.ctx.store = PostStore("")
        api_hourly.ctx.bot = None
        super().tearDown()

    def test_archive_lists_posts_and_days(self):
        data = self.admin_c.get("/api/admin/hourly/archive").json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 3)
        self.assertEqual([x["day"] for x in data["days"]], [DAY, DAY2])
        self.assertEqual(data["items"][0]["tg"], ["ru"])
        self.assertTrue(data["items"][0]["tg_ready"])
        self.assertEqual(data["items"][0]["url"], "/hourly?post=" + self.first)

    def test_archive_is_admin_only(self):
        self.assertEqual(self.guest.get("/api/admin/hourly/archive").status_code, 401)
        self.assertEqual(self.user_c.get("/api/admin/hourly/archive").status_code, 403)

    def test_delete_one_post_removes_it_from_the_site_and_channel(self):
        res = self.admin_c.post("/api/admin/hourly/%s/delete" % self.first,
                                json={"tg": True})
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(self.store.get(self.first))
        self.assertEqual(self.bot.deleted, [("-100hourly", 31)])
        self.assertEqual(res.json()["count"], 2)

    def test_delete_without_tg_keeps_the_channel_post(self):
        res = self.admin_c.post("/api/admin/hourly/%s/delete" % self.first, json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.bot.deleted, [])
        self.assertIsNone(self.store.get(self.first))

    def test_delete_unknown_post_is_404(self):
        res = self.admin_c.post("/api/admin/hourly/nope/delete", json={})
        self.assertEqual(res.status_code, 404)

    def test_bot_off_keeps_the_post(self):
        self.bot.running = False
        res = self.admin_c.post("/api/admin/hourly/%s/delete" % self.first,
                                json={"tg": True})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "bot_off")
        self.assertIsNotNone(self.store.get(self.first))

    def test_delete_whole_day_removes_every_post_and_every_id(self):
        res = self.admin_c.post("/api/admin/hourly/day/%s/delete" % DAY,
                                json={"tg": True})
        data = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(data["deleted"], 2)
        self.assertEqual(self.store.by_day(DAY), [])
        self.assertEqual(self.store.by_day(DAY2)[0]["id"], self.old)
        # оба поста дня сняты из канала, чужой день не тронут
        self.assertEqual(sorted(self.bot.deleted), [("-100hourly", 31),
                                                    ("-100hourly", 32)])
        self.assertEqual(data["tg"]["ru"]["deleted"], 2)

    def test_delete_unknown_day_is_404(self):
        self.assertEqual(self.admin_c.post("/api/admin/hourly/day/2020-01-01/delete",
                                           json={}).status_code, 404)

    def test_page_stops_showing_the_deleted_post(self):
        self.assertIn(self.first, self.guest.get("/hourly").text)
        self.admin_c.post("/api/admin/hourly/%s/delete" % self.first, json={})
        self.assertNotIn(self.first, self.guest.get("/hourly").text)


class BotRemembersIdsTest(unittest.TestCase):
    """Бот запоминает id постов канала — без них Telegram не почистить."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PostStore(os.path.join(self.tmp.name, "posts.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_note_channel_post_keeps_the_last_message_per_language(self):
        import tg_bot
        bot = object.__new__(tg_bot.TelegramBot)
        bot.channel_sent = {}
        bot.note_channel_post("-100ru", 5, [6], "ru")
        bot.note_channel_post("-100en", 7, [], "en-BR")
        self.assertEqual(bot.channel_sent["ru"]["message_id"], 5)
        self.assertEqual(bot.channel_sent["ru"]["extra"], [6])
        self.assertEqual(bot.channel_sent["en"]["message_id"], 7)
        bot.note_channel_post("-100ru", 0, [], "ru")      # id не пришёл — не пишем
        self.assertEqual(bot.channel_sent["ru"]["message_id"], 5)

    def test_archive_posts_puts_the_ids_into_the_record(self):
        import tg_bot
        bot = object.__new__(tg_bot.TelegramBot)
        bot.hourly_store = self.store
        bot.store = FakeSettings()
        bot.channel_sent = {"ru": {"chat": "-100ru", "message_id": 9,
                                   "extra": [10]}}
        posts = [{"cid": "-100ru", "caption": "короткий", "full_caption": "полный",
                  "lang": "ru", "top": "топ"}]
        bot._archive_posts(posts, ["ru"], "", 3, meta={"window_h": 4,
                                                       "total_usd": 100.0,
                                                       "liq_count": 2})
        rec = self.store.list()[0]
        self.assertEqual(rec["tg"]["ru"]["message_id"], 9)
        self.assertEqual(rec["tg"]["ru"]["extra"], [10])
        self.assertEqual(rec["tg"]["ru"]["chat"], "-100ru")
        self.assertEqual(rec["texts"]["ru"], "полный")

    def test_archive_without_ids_has_no_tg_field(self):
        import tg_bot
        bot = object.__new__(tg_bot.TelegramBot)
        bot.hourly_store = self.store
        bot.store = FakeSettings()
        bot.channel_sent = {}
        bot._archive_posts([{"cid": "-100ru", "caption": "кап",
                             "full_caption": "полный", "lang": "ru"}],
                           ["ru"], "", 4)
        self.assertNotIn("tg", self.store.list()[0])

    def test_drop_channel_posts_reports_what_happened(self):
        from channel_digest import drop_channel_posts
        calls = []

        async def delete_fn(chat, mid):
            calls.append((chat, mid))
            return mid != 99                      # второй пост Telegram не отдал

        sent = {"ru": {"chat": "-100ru", "message_id": 98, "extra": [99]},
                "en": {"chat": "", "message_id": 5, "extra": []}}
        out = asyncio.run(drop_channel_posts(sent, delete_fn))
        self.assertEqual(calls, [("-100ru", 98), ("-100ru", 99)])
        self.assertEqual(out["ru"]["deleted"], 1)
        self.assertTrue(out["ru"]["err"])
        self.assertFalse(out["en"]["ok"])
        self.assertIn("бот", out["en"]["err"])

    def test_drop_without_delete_fn_explains_itself(self):
        from channel_digest import drop_channel_posts
        out = asyncio.run(drop_channel_posts(
            {"ru": {"chat": "-100ru", "message_id": 1, "extra": []}}, None))
        self.assertFalse(out["ru"]["ok"])
        self.assertEqual(out["ru"]["ids"], [1])

    def test_drop_marks_posts_without_ids_as_old(self):
        from channel_digest import drop_channel_posts

        async def delete_fn(chat, mid):
            return True

        out = asyncio.run(drop_channel_posts(
            {"ru": {"chat": "-100ru", "message_id": 0, "extra": []}}, delete_fn))
        self.assertIn("id", out["ru"]["err"])


class FakeSettings:
    """Заглушка базы аккаунтов: нужен только set_setting/list_digest_photos."""

    def set_setting(self, name, value) -> None:
        pass

    def list_digest_photos(self, kind: str = "") -> list:
        return []


class PublishStoresIdsTest(unittest.TestCase):
    """Публикация выпуска сразу записывает id сообщений в архив."""

    def test_mark_published_from_bot_memory(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            store = DigestStore(os.path.join(tmp.name, "d.json"))
            store.save(digest_rec())
            api_digest.ctx.store = store
            api_digest.ctx.sent_ids_fn = lambda: {"ru": {"chat": "-100ru",
                                                         "message_id": 41,
                                                         "extra": [42]}}
            store.mark_published(DAY, "ru", ok=True, chat="-100ru",
                                 message_id=41, extra=[42])
            self.assertEqual(api_digest.sent_map(store.get(DAY))["ru"]["extra"], [42])
            api_digest.ctx.sent_ids_fn = None
            api_digest.ctx.store = DigestStore("")
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
