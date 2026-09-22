"""📣 Рекламные посты: панель, рассылка, баннер на главной и автоудаление.

Реклама живёт своей жизнью: текст и фото пишет админ, он же выбирает
источники (бот, каналы, главная сайта), время отправки и срок. Проверяем
ровно это:

    * текст превращается в безопасную разметку со ссылками;
    * получатели фильтруются (никаких опечаток в id), время понимает и
      число, и ISO из <input type=datetime-local>;
    * отправка уважает выбранные источники и запоминает id постов, чтобы
      потом их удалить;
    * планировщик публикует по времени и снимает по сроку: баннер на главной
      перестаёт отдаваться, посты в каналах удаляются;
    * API админки закрыто от гостей и обычных пользователей, а публичный
      /api/ads отдаёт только живые объявления.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ads as ads_mod  # noqa: E402
import web_account  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402
from ads import (AdService, banner_settings, clean_link, clean_targets,
                    linkify_html, normalize_banner, save_banner_settings,
                    site_html, text_problem)  # noqa: E402

ADMIN_EMAIL = "boss@liqscope.online"
RU_CHAT = "-1001112223334"
EN_CHAT = "-1005556667778"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32          # хватает проверки формата


def png_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


class FakeBot:
    """Мини-бот: помнит, что и куда отправлял, и умеет удалять сообщения."""

    def __init__(self, ru: str = RU_CHAT, en: str = EN_CHAT, users=3):
        self.ru, self.en = ru, en
        self.users = users
        self.sent: list = []          # (chat_id, text, photo)
        self.deleted: list = []       # (chat_id, mid)
        self.photo_on = True
        self._last_tg_err = ""
        self._mid = 500
        self._enabled = True

    @property
    def enabled(self) -> bool:
        # как у настоящего TelegramBot: это свойство, а не метод
        return self._enabled

    def _channel_bindings(self) -> dict:
        return {"ru": self.ru, "en": self.en,
                "titles": {"ru": "LiqScope RU", "en": "LiqScope EN"}}

    def _next_mid(self) -> int:
        self._mid += 1
        return self._mid

    async def send(self, chat_id, text, markup=None, parse="HTML", raw=False):
        if not self._enabled:
            self._last_tg_err = "бот выключен"
            return None
        self.sent.append((chat_id, text, ""))
        return self._next_mid()

    async def send_photo(self, chat_id, path, caption="", markup=None, raw=False):
        if not self._enabled or not self.photo_on:
            self._last_tg_err = "photo failed"
            return None
        self.sent.append((chat_id, caption, path))
        return self._next_mid()

    async def broadcast_post(self, text, photo="", markup=None, raw=True, actor_id=None):
        for i in range(self.users):
            chat_id = 9000 + i
            if photo:
                await self.send_photo(chat_id, photo, text, raw=True)
            else:
                await self.send(chat_id, text, raw=True)
        return {"ok": self.users, "fail": 0, "total": self.users}

    async def delete_message(self, chat_id, message_id) -> bool:
        self.deleted.append((chat_id, int(message_id)))
        return True


class TextTest(unittest.TestCase):
    """Плюс-текст рекламы: экранирование, ссылки и лимиты Telegram."""

    def test_links_become_clickable_and_tags_escape(self):
        out = linkify_html("Скидка 10% <b>всем</b> — https://liqscope.online/promo?x=1&y=2")
        self.assertIn("&lt;b&gt;всем&lt;/b&gt;", out)
        self.assertIn('<a href="https://liqscope.online/promo?x=1&amp;y=2">', out)
        self.assertNotIn("<b>", out)

    def test_trailing_punctuation_stays_out_of_the_link(self):
        out = linkify_html("Смотри https://liqscope.online/promo.")
        self.assertIn('<a href="https://liqscope.online/promo">', out)
        self.assertTrue(out.endswith("."))

    def test_site_version_opens_links_in_new_tab(self):
        self.assertIn('target="_blank"', site_html("Подробнее: https://a.b/c"))

    def test_text_without_links_is_untouched(self):
        self.assertEqual(linkify_html("Просто текст"), "Просто текст")

    def test_limits_depend_on_photo(self):
        long_text = "я" * 1100
        self.assertEqual(text_problem(long_text, False), "")
        self.assertEqual(text_problem(long_text, True), "too_long_photo")
        self.assertEqual(text_problem("", True), "empty")
        self.assertEqual(text_problem("я" * 5000, False), "too_long")

    def test_emoji_count_as_two_symbols(self):
        # Telegram считает подпись в UTF-16: 600 эмодзи — это уже 1200 знаков
        self.assertEqual(text_problem("🔥" * 600, True), "too_long_photo")


class TargetsTest(unittest.TestCase):
    def test_only_valid_chats_get_through(self):
        got = clean_targets({"bot": 1, "site": "yes",
                             "channels": [RU_CHAT, "@mychannel", "опечатка", "", RU_CHAT]})
        self.assertTrue(got["bot"])
        self.assertTrue(got["site"])
        self.assertEqual(got["channels"], [RU_CHAT, "@mychannel"])

    def test_junk_targets_are_dropped(self):
        got = clean_targets("не словарь")
        self.assertEqual(got, {"bot": False, "site": False, "terminal": False,
                               "digest": False, "hourly": False,
                               "channels": []})

    def test_terminal_is_a_place_of_its_own(self):
        """Терминал — отдельная страница: хочется одну акцию везде или только там."""
        got = clean_targets({"site": True, "terminal": 1, "bot": ""})
        self.assertTrue(got["site"])
        self.assertTrue(got["terminal"])
        self.assertFalse(got["bot"])
        self.assertFalse(got["digest"])
        self.assertFalse(got["hourly"])
        line = ads_mod.targets_text(got)
        self.assertIn("главная сайта", line)

    def test_digest_and_hourly_are_places_of_their_own(self):
        """Дайджест и сводка — отдельные страницы: акцию можно отправить только туда."""
        got = clean_targets({"digest": 1, "hourly": True})
        self.assertTrue(got["digest"])
        self.assertTrue(got["hourly"])
        self.assertFalse(got["site"])
        self.assertFalse(got["terminal"])
        line = ads_mod.targets_text(got)
        self.assertIn("дайджест", line)
        self.assertIn("сводка по часам", line)

    def test_send_at_accepts_seconds_iso_and_empty(self):
        now = 1_788_000_000.0
        self.assertEqual(ads_mod.parse_when(int(now), now), now)
        self.assertEqual(ads_mod.parse_when("", now), 0.0)
        self.assertEqual(ads_mod.parse_when(0, now), 0.0)
        iso = ads_mod.parse_when("2026-09-18T22:00", now)
        self.assertGreater(iso, now - 40000000)
        self.assertEqual(ads_mod.parse_when("не дата", now), 0.0)
        self.assertEqual(ads_mod.parse_when(1_788_000_000_000, now), 1_788_000_000.0)


class LinkTest(unittest.TestCase):
    """Клик по баннеру: своя ссылка у объявления — и только нормальная.

    Ссылка попадает в ``href`` на публичной странице, поэтому сюда не должны
    проходить ``javascript:``, чужие схемы и обрывки разметки: всё это стало бы
    кликом в чужую сторону.
    """

    def test_domain_without_scheme_becomes_https(self) -> None:
        self.assertEqual(clean_link("gate.com/promo"), "https://gate.com/promo")
        self.assertEqual(clean_link("  https://a.example/x  "), "https://a.example/x")

    def test_junk_schemes_are_rejected(self) -> None:
        for bad in ("", "javascript:alert(1)", "data:text/html,hi", "ftp://a.example/x",
                    "//gate.com/x", "не ссылка", "a.b"):
            self.assertEqual(clean_link(bad), "", bad)

    def test_markup_cannot_escape_the_attribute(self) -> None:
        self.assertEqual(clean_link('https://a.example/x" onmouseover="x'), "")
        self.assertEqual(clean_link("https://a.example/x\'"), "")
        self.assertEqual(clean_link("https://a.example/" + "y" * 700), "")

    def test_public_card_carries_the_link(self) -> None:
        out = ads_mod.ad_public({"id": 3, "text": "", "link": "gate.com/promo",
                                 "has_photo": True})
        self.assertEqual(out["link"], "https://gate.com/promo")
        self.assertEqual(out["photo"], "/api/ads/3/photo")
        self.assertEqual(out["text"], "")      # подписи нет — баннер из одного фото


class BannerSettingsTest(unittest.TestCase):
    """Как крутить баннер: числа в разумных пределах, режимы — из списка."""

    def test_defaults_when_nothing_saved(self) -> None:
        self.assertEqual(banner_settings(None), {
            "rotate_sec": 8, "fade_ms": 500, "layout": "carousel",
            "max": 4, "fit": "contain", "caption": "below"})

    def test_numbers_are_clamped_and_words_are_checked(self) -> None:
        got = normalize_banner({"rotate_sec": 0, "fade_ms": 99999, "max": 900,
                                "layout": "карусель", "fit": "squish",
                                "caption": "wherever"})
        self.assertEqual(got["rotate_sec"], 2)          # 0 выжигал бы CPU
        self.assertEqual(got["fade_ms"], 2000)
        self.assertEqual(got["max"], 8)                 # весь архив не тянем
        self.assertEqual(got["layout"], "carousel")
        self.assertEqual(got["fit"], "contain")
        self.assertEqual(got["caption"], "below")

    def test_junk_and_strings_still_give_numbers(self) -> None:
        got = normalize_banner({"rotate_sec": "12", "fade_ms": None, "max": "abc"})
        self.assertEqual(got["rotate_sec"], 12)
        self.assertEqual(got["fade_ms"], 500)
        self.assertEqual(got["max"], 4)

    def test_grid_mode_is_a_choice_not_a_default(self) -> None:
        self.assertEqual(normalize_banner({"layout": "grid"})["layout"], "grid")

    def test_saved_values_survive_the_store(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(os.path.join(tmp.name, "b.db"), secret="s")
        applied = save_banner_settings(store, {"rotate_sec": 3, "layout": "grid",
                                               "max": 24})
        self.assertEqual(applied["rotate_sec"], 3)
        self.assertEqual(applied["layout"], "grid")
        self.assertEqual(applied["max"], 8)             # причесано при сохранении
        self.assertEqual(banner_settings(store), applied)


class AdStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, **kw):
        data = {"text": "Пост", "targets": {"site": True}, "send_at": time.time() + 60,
                "status": "scheduled"}
        data.update(kw)
        return self.store.add_ad(**data)["id"]

    def test_add_and_get_roundtrips_targets(self):
        ad = self.store.get_ad(self.add(targets={"bot": True, "site": True,
                                                 "channels": [RU_CHAT]}))
        self.assertEqual(ad["targets"]["channels"], [RU_CHAT])
        self.assertEqual(ad["status"], "scheduled")
        self.assertEqual(ad["results"], {})

    def test_empty_ad_is_rejected(self):
        self.assertEqual(self.store.add_ad("")["error"], "empty")

    def test_photo_without_text_is_allowed(self):
        """Баннер из одной картинки — законный пост: текст украшение, не обязательное поле.

        Фотографию сервер пишет уже после создания записи (ей нужен id), поэтому
        склад знает о ней по признанию ``photo_pending``: иначе «фото без текста»
        нельзя было отправить вообще.
        """
        self.assertEqual(self.store.add_ad("", photo_pending=True,
                                           targets={"site": True})["ok"], True)
        self.assertEqual(self.store.add_ad("", photo="какой-то файл")["ok"], True)
        self.assertEqual(self.store.add_ad("")["error"], "empty")

    def test_link_survives_the_roundtrip_and_edits(self):
        ad_id = self.add(link="https://gate.com/promo")
        self.assertEqual(self.store.get_ad(ad_id)["link"], "https://gate.com/promo")
        self.store.update_ad(ad_id, link="https://example.com/x")
        self.assertEqual(self.store.get_ad(ad_id)["link"], "https://example.com/x")
        self.store.update_ad(ad_id, link="   ")
        self.assertEqual(self.store.get_ad(ad_id)["link"], "")

    def test_banner_places_are_picked_per_page(self):
        """Одно объявление может жить на главной, в терминале, дайджесте, сводке или на всех."""
        now = time.time()
        self.store.add_ad("обе", status="sent", send_at=now - 10,
                          targets={"site": True, "terminal": True})
        self.store.add_ad("главная", status="sent", send_at=now - 20,
                          targets={"site": True})
        self.store.add_ad("терминал", status="sent", send_at=now - 30,
                          targets={"terminal": True})
        self.store.add_ad("дайджест", status="sent", send_at=now - 40,
                          targets={"digest": True})
        self.store.add_ad("сводка", status="sent", send_at=now - 50,
                          targets={"hourly": True})
        self.assertEqual([a["text"] for a in self.store.active_site_ads(now, place="landing")],
                         ["обе", "главная"])
        self.assertEqual([a["text"] for a in self.store.active_site_ads(now, place="terminal")],
                         ["обе", "терминал"])
        # digest/hourly показывают и баннеры главной (site): так старые записи
        # с site=true остаются видимыми на этих страницах (см. active_site_ads)
        self.assertEqual([a["text"] for a in self.store.active_site_ads(now, place="digest")],
                         ["обе", "главная", "дайджест"])
        self.assertEqual([a["text"] for a in self.store.active_site_ads(now, place="hourly")],
                         ["обе", "главная", "сводка"])
        self.assertEqual(len(self.store.active_site_ads(now, place="")), 5)

    def test_banner_limit_is_honest_about_pages(self):
        """Лимит «сколько показывать» applies after the page filter, not before."""
        now = time.time()
        for i in range(6):
            self.store.add_ad(f"терминал {i}", status="sent", send_at=now - i,
                              targets={"terminal": True})
            self.store.add_ad(f"чужое {i}", status="sent", send_at=now - i - 0.5,
                              targets={"site": True})
        got = [a["text"] for a in self.store.active_site_ads(now, limit=3,
                                                             place="terminal")]
        self.assertEqual(got, ["терминал 0", "терминал 1", "терминал 2"])

    def test_due_and_expired_are_selected_by_time(self):
        now = time.time()
        self.store.add_ad("пора", send_at=now - 5, status="scheduled")
        self.store.add_ad("рано", send_at=now + 300, status="scheduled")
        self.assertEqual([a["text"] for a in self.store.due_ads(now)], ["пора"])
        sent = self.store.add_ad("живёт", send_at=now - 60, status="sent",
                                 expires_at=now - 1)
        self.store.add_ad("живёт долго", send_at=now - 60, status="sent",
                          expires_at=now + 3600)
        self.assertEqual([a["text"] for a in self.store.expired_ads(now)], ["живёт"])
        self.assertEqual(self.store.get_ad(sent["id"])["status"], "sent")

    def test_site_banner_only_for_sent_site_ads_in_their_window(self):
        now = time.time()
        for slug, kw in (
            ("активный", {"status": "sent", "send_at": now - 60}),
            ("без сайта", {"status": "sent", "send_at": now - 60, "targets": {"bot": True}}),
            ("истёк", {"status": "sent", "send_at": now - 60,
                       "expires_at": now - 1}),
            ("черновик", {"status": "draft", "send_at": now + 100}),
        ):
            kw.setdefault("expires_at", 0)
            kw.setdefault("targets", {"site": True})
            self.store.add_ad(slug, **kw)
        self.assertEqual([a["text"] for a in self.store.active_site_ads(now)], ["активный"])

    def test_expired_site_ad_disappears_without_scheduler(self):
        now = time.time()
        self.add(status="sent", send_at=now - 10, expires_at=now + 5)
        self.assertEqual(len(self.store.active_site_ads(now)), 1)
        self.assertEqual(self.store.active_site_ads(now + 6), [])

    def test_update_keeps_only_known_statuses(self):
        ad_id = self.add(status="draft")
        self.store.update_ad(ad_id, status="чепуха")
        self.assertEqual(self.store.get_ad(ad_id)["status"], "draft")
        self.store.update_ad(ad_id, status="sent", results={"site": {"ok": True}})
        ad = self.store.get_ad(ad_id)
        self.assertEqual(ad["status"], "sent")
        self.assertTrue(ad["results"]["site"]["ok"])

    def test_photo_is_stored_and_removed_with_the_ad(self):
        ad_id = self.add()
        up = self.store.save_ad_photo(ad_id, PNG, filename="ad.png")
        self.assertTrue(up["ok"])
        path = up["path"]
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(self.store.get_ad(ad_id)["has_photo"])
        self.assertEqual(self.store.save_ad_photo(ad_id, b"not-an-image-file-at-all!!")["error"], "not_image")
        self.assertEqual(self.store.save_ad_photo(ad_id, PNG[:10])["error"], "empty")
        self.store.clear_ad_photo(ad_id)
        self.assertFalse(os.path.isfile(path))
        self.assertFalse(self.store.get_ad(ad_id)["has_photo"])

    def test_delete_removes_ad_and_its_photo(self):
        ad_id = self.add()
        path = self.store.save_ad_photo(ad_id, PNG)["path"]
        self.assertTrue(self.store.delete_ad(ad_id))
        self.assertIsNone(self.store.get_ad(ad_id))
        self.assertFalse(os.path.isfile(path))
        self.assertFalse(self.store.delete_ad(ad_id))

    def test_prune_keeps_recent_history(self):
        ad_id = self.add(status="expired")
        self.assertEqual(self.store.prune_ads(30), 0)          # свежая — остаётся
        old = self.add(status="cancelled")
        self.store._db.execute("UPDATE ads SET created_at=? WHERE id=?",
                               (time.time() - 40 * 86400, old))
        self.store._db.commit()
        self.assertEqual(self.store.prune_ads(30), 1)
        self.assertIsNotNone(self.store.get_ad(ad_id))
        self.assertIsNone(self.store.get_ad(old))


class AdServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        self.bot = FakeBot()
        self.svc = AdService(store=self.store, bot=self.bot,
                             site_url="https://liqscope.online")

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kw):
        data = {"text": "Скидка на комиссию\nhttps://liqscope.online/promo",
                "targets": {"bot": True, "site": True, "channels": [RU_CHAT]},
                "send_at": time.time() - 1, "status": "scheduled"}
        data.update(kw)
        return self.store.get_ad(self.store.add_ad(**data)["id"])

    def test_publish_goes_to_channels_users_and_site(self):
        ad = self.make()
        got = asyncio.run(self.svc.publish(ad))
        self.assertEqual(got["status"], "sent")
        res = got["results"]
        self.assertTrue(res[f"ch:{RU_CHAT}"]["ok"])
        self.assertGreater(res[f"ch:{RU_CHAT}"]["mid"], 0)
        self.assertEqual(res["bot"]["ok_count"], 3)
        self.assertTrue(res["site"]["ok"])
        # в канал ушло сообщение с HTML-ссылкой, а не «сырым» текстом
        chat, text, _photo = self.bot.sent[0]
        self.assertEqual(chat, RU_CHAT)
        self.assertIn('<a href="https://liqscope.online/promo">', text)

    def test_photo_is_sent_when_the_ad_has_one(self):
        ad = self.make()
        self.store.save_ad_photo(ad["id"], PNG, filename="p.png")
        ad = self.store.get_ad(ad["id"])
        asyncio.run(self.svc.publish(ad))
        self.assertTrue(self.bot.sent[0][2], "в канал должно уйти фото")

    def test_only_chosen_sources_receive_the_post(self):
        ad = self.make(targets={"site": True})
        asyncio.run(self.svc.publish(ad))
        self.assertEqual(self.bot.sent, [])
        self.assertEqual(len(self.store.active_site_ads()), 1)

    def test_without_bot_nothing_is_lost_silently(self):
        svc = AdService(store=self.store, bot=None, site_url="")
        ad = self.make(targets={"channels": [RU_CHAT]})
        got = asyncio.run(svc.publish(ad))
        self.assertEqual(got["status"], "failed")
        self.assertIn("бот не подключён", got["results"][f"ch:{RU_CHAT}"]["err"])

    def test_publish_line_explains_where_it_went(self):
        ad = self.make()
        got = asyncio.run(self.svc.publish(ad))
        line = self.svc.publish_line(got)
        self.assertIn("бот: 3/3", line)
        self.assertIn("главная: баннер", line)
        self.assertIn(RU_CHAT, line)
        self.assertIn("ушло", line)

    def test_expire_deletes_channel_posts_and_takes_banner_down(self):
        ad = self.make(expires_at=time.time() + 3600)
        got = asyncio.run(self.svc.publish(ad))
        mid = got["results"][f"ch:{RU_CHAT}"]["mid"]
        out = asyncio.run(self.svc.expire(got))
        self.assertEqual(out["status"], "expired")
        self.assertIn((int(RU_CHAT), mid), self.bot.deleted)
        self.assertEqual(self.store.active_site_ads(), [])
        self.assertEqual(self.store.get_ad(ad["id"])["results"][f"ch:{RU_CHAT}"]["deleted"],
                         True)

    def test_tick_publishes_due_and_clears_expired(self):
        due = self.make(text="пора")
        self.store.add_ad("рано", targets={"site": True},
                          send_at=time.time() + 600, status="scheduled")
        old = self.store.add_ad("старое", targets={"site": True},
                                send_at=time.time() - 100, status="sent",
                                expires_at=time.time() - 1)["id"]
        out = asyncio.run(self.svc.tick())
        self.assertEqual(out["published"], [due["id"]])
        self.assertEqual(out["expired"], [old])
        statuses = {a["id"]: a["status"] for a in self.store.list_ads()}
        self.assertEqual(statuses[due["id"]], "sent")
        self.assertEqual(statuses[old], "expired")
        self.assertEqual(self.svc.sent_total, 1)
        self.assertEqual(self.svc.expired_total, 1)

    def test_late_publish_does_not_outlive_its_deadline(self):
        ad = self.make(expires_at=time.time() - 5)
        got = asyncio.run(self.svc.publish(ad))
        self.assertEqual(got["status"], "expired")
        self.assertEqual(self.store.active_site_ads(), [])

    def test_status_lists_where_the_bot_is(self):
        st = self.svc.status()
        self.assertTrue(st["bot"])
        self.assertEqual(st["sent_total"], 0)
        self.assertIn("busy", st)


class AdRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=[ADMIN_EMAIL])
        admin = self.store.create_email_user(ADMIN_EMAIL, password_hash="x")["user"]
        self.admin_id = int(admin["id"])
        plain = self.store.create_email_user("vasya@example.com", password_hash="x")["user"]
        self.bot = FakeBot()
        self.svc = AdService(store=self.store, bot=self.bot,
                             site_url="https://liqscope.online")
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        ads_mod.ctx.store = self.store
        ads_mod.ctx.bot = self.bot
        ads_mod.ctx.public_url = "https://liqscope.online"
        self.app = FastAPI()
        ads_mod.register_ad_routes(self.app, self.svc)
        self.client = TestClient(self.app)
        self.admin = TestClient(self.app)
        self.admin.cookies.set(COOKIE_SID, self.store.create_session(self.admin_id))
        self.plain = TestClient(self.app)
        self.plain.cookies.set(COOKIE_SID, self.store.create_session(int(plain["id"])))

    def tearDown(self):
        web_account.ctx.store = None
        ads_mod.ctx.store = None
        ads_mod.ctx.bot = None
        self.tmp.cleanup()

    def post_ad(self, **body):
        data = {"text": "Реклама", "targets": {"site": True, "channels": [RU_CHAT]},
                "send_at": int(time.time()) - 1}
        data.update(body)
        return self.admin.post("/api/admin/ads", json=data)

    # --- доступ ------------------------------------------------------------
    def test_admin_api_is_closed_for_guests_and_users(self):
        self.assertEqual(self.client.get("/api/admin/ads").status_code, 401)
        self.assertEqual(self.plain.get("/api/admin/ads").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/ads", json={"text": "x"}).status_code, 401)

    def test_public_banner_api_is_open(self):
        r = self.client.get("/api/ads")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"], [])

    # --- создание ----------------------------------------------------------
    def test_panel_lists_channels_and_limits(self):
        d = self.admin.get("/api/admin/ads").json()
        self.assertTrue(d["ok"])
        self.assertEqual([c["id"] for c in d["channels"]], [RU_CHAT, EN_CHAT])
        self.assertTrue(d["bot_ready"])
        self.assertEqual(d["limits"]["photo"], 1024)
        self.assertTrue(d["bot_ready"])
        self.assertTrue(d["site"].endswith("/"))

    def test_send_now_publishes_and_shows_up_in_the_banner(self):
        r = self.post_ad(text="Скидка на комиссию")
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["item"]["status"], "sent")
        live = self.client.get("/api/ads").json()["items"]
        self.assertEqual([a["text"] for a in live], ["Скидка на комиссию"])
        self.assertEqual(self.bot.sent[0][1], "Скидка на комиссию")

    def test_scheduled_ad_waits_for_its_time(self):
        r = self.post_ad(send_at=int(time.time()) + 3600, ttl_min=60)
        item = r.json()["item"]
        self.assertEqual(item["status"], "scheduled")
        self.assertEqual(self.bot.sent, [])
        self.assertEqual(self.client.get("/api/ads").json()["items"], [])
        self.assertAlmostEqual(item["expires_at"] - item["send_at"], 3600, delta=2)

    def test_ad_without_targets_is_rejected(self):
        r = self.post_ad(targets={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "no_targets")

    def test_long_text_with_photo_is_rejected_before_sending(self):
        r = self.post_ad(text="я" * 1100, photo=png_data_url(),
                         targets={"channels": [RU_CHAT]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "too_long_photo")
        self.assertIn("Сократите", r.json()["hint"])
        self.assertEqual(self.store.list_ads(), [])      # публиковать нечего

    def test_draft_is_kept_without_sending(self):
        r = self.post_ad(draft=True, targets={}, text="Заготовка")
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["item"]["status"], "draft")
        self.assertEqual(self.bot.sent, [])

    def test_photo_goes_to_the_channel_and_the_banner(self):
        r = self.post_ad(photo=png_data_url(), targets={"channels": [RU_CHAT], "site": True})
        ad_id = r.json()["item"]["id"]
        self.assertTrue(r.json()["item"]["has_photo"])
        self.assertEqual(open(self.bot.sent[0][2], "rb").read()[:8], PNG[:8])
        self.assertEqual(self.client.get("/api/ads").json()["items"][0]["photo"],
                         f"/api/ads/{ad_id}/photo")
        self.assertEqual(self.client.get(f"/api/ads/{ad_id}/photo").status_code, 200)

    def test_photo_of_a_finished_ad_is_not_public(self):
        ad_id = self.post_ad(photo=png_data_url()).json()["item"]["id"]
        self.admin.post(f"/api/admin/ads/{ad_id}/stop", json={})
        self.assertEqual(self.client.get(f"/api/ads/{ad_id}/photo").status_code, 404)

    # --- правка и снятие ---------------------------------------------------
    def test_edit_changes_text_and_schedule(self):
        ad_id = self.post_ad(text="Старый текст").json()["item"]["id"]
        d = self.admin.post(f"/api/admin/ads/{ad_id}",
                            json={"text": "Новый текст", "ttl_min": 30}).json()
        self.assertEqual(d["item"]["text"], "Новый текст")
        self.assertAlmostEqual(d["item"]["expires_at"] - d["item"]["send_at"], 1800, delta=5)

    def test_send_now_button_ignores_the_chosen_time(self):
        ad_id = self.post_ad(send_at=int(time.time()) + 7200).json()["item"]["id"]
        r = self.admin.post(f"/api/admin/ads/{ad_id}/send", json={})
        self.assertTrue(r.json()["ok"])
        self.assertTrue(r.json()["published"])
        self.assertEqual(self.bot.sent[-1][1], "Реклама")
        self.assertEqual(self.admin.post(f"/api/admin/ads/{ad_id}/send", json={}).status_code,
                         409)

    def test_stop_takes_the_banner_down_and_deletes_the_post(self):
        ad_id = self.post_ad(targets={"channels": [RU_CHAT], "site": True}).json()["item"]["id"]
        r = self.admin.post(f"/api/admin/ads/{ad_id}/stop", json={}).json()
        self.assertEqual(r["item"]["status"], "expired")
        self.assertEqual(self.client.get("/api/ads").json()["items"], [])
        self.assertTrue(self.bot.deleted)

    def test_delete_removes_the_record(self):
        ad_id = self.post_ad(draft=True, targets={}).json()["item"]["id"]
        self.assertTrue(self.admin.post(f"/api/admin/ads/{ad_id}/delete", json={}).json()["ok"])
        self.assertIsNone(self.store.get_ad(ad_id))
        self.assertEqual(self.admin.post(f"/api/admin/ads/{ad_id}/delete", json={}).status_code,
                         404)

    def test_scheduled_ad_can_be_stopped_before_it_goes_out(self):
        ad_id = self.post_ad(send_at=int(time.time()) + 3600).json()["item"]["id"]
        r = self.admin.post(f"/api/admin/ads/{ad_id}/stop", json={}).json()
        self.assertEqual(r["item"]["status"], "cancelled")
        self.assertEqual(asyncio.run(self.svc.tick())["published"], [])

    def test_unknown_ad_gives_404(self):
        self.assertEqual(self.admin.post("/api/admin/ads/999/send", json={}).status_code, 404)
        self.assertEqual(self.admin.post("/api/admin/ads/999/delete", json={}).status_code, 404)


class BannerRoutesTest(AdRoutesTest):
    """Баннер как он теперь: фото без текста, две страницы и настройка смены.

    Наследуем инфраструктуру ``AdRoutesTest`` (склад, бот-заглушка, админская
    сессия) и проверяем ровно то, что просил админ: картинку можно выложить
    без подписи, акция может жить на главной, в терминале или на обеих
    страницах, а режим смены и плавности правится в админке без рестарта.
    """

    # --- фото без текста ----------------------------------------------------
    def test_photo_only_ad_goes_to_the_site(self):
        """Фотография без текста на сайт уходит: раньше это было невозможно."""
        r = self.post_ad(text="", photo=png_data_url(),
                         targets={"site": True, "terminal": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"], r.text)
        item = r.json()["item"]
        self.assertTrue(item["has_photo"])
        self.assertEqual(item["text"], "")
        live = self.client.get("/api/ads?place=landing").json()["items"]
        self.assertEqual(len(live), 1, live)
        self.assertTrue(live[0]["photo"].endswith("/photo"))
        self.assertEqual(live[0]["text"], "")

    def test_photo_only_ad_still_needs_a_site_target(self):
        """В канал без текста идти нечего: там проверка на месте."""
        r = self.post_ad(text="", photo=png_data_url(),
                         targets={"channels": [RU_CHAT]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "empty")

    def test_nothing_at_all_is_rejected(self):
        r = self.post_ad(text="", targets={"site": True})
        self.assertEqual(r.status_code, 400)

    # --- ссылка и страницы --------------------------------------------------
    def test_link_reaches_the_page_and_can_be_edited(self):
        r = self.post_ad(text="Скидка", link="gate.com/promo")
        ad_id = r.json()["item"]["id"]
        live = self.client.get("/api/ads").json()["items"][0]
        self.assertEqual(live["link"], "https://gate.com/promo")
        upd = self.admin.post("/api/admin/ads/%d" % ad_id,
                              json={"link": "javascript:alert(1)"})
        self.assertEqual(upd.json()["item"]["link"], "")
        self.assertEqual(self.client.get("/api/ads").json()["items"][0]["link"], "")

    def test_terminal_place_has_its_own_feed(self):
        self.post_ad(text="только главная", targets={"site": True},
                     send_at=int(time.time()) - 1)
        self.post_ad(text="обе страницы", targets={"site": True, "terminal": True},
                     send_at=int(time.time()) - 2)
        landing = self.client.get("/api/ads?place=landing").json()["items"]
        terminal = self.client.get("/api/ads?place=terminal").json()["items"]
        self.assertEqual([a["text"] for a in landing], ["обе страницы", "только главная"])
        self.assertEqual([a["text"] for a in terminal], ["обе страницы"])
        # неизвестная страница = главная, а не «всё сразу»
        junk = self.client.get("/api/ads?place=луну").json()
        self.assertEqual(junk["place"], "landing")
        self.assertEqual(len(junk["items"]), 2)

    def test_banner_config_travels_with_the_items(self):
        d = self.client.get("/api/ads").json()
        self.assertEqual(d["banner"]["layout"], "carousel")
        self.assertEqual(d["banner"]["rotate_sec"], 8)
        self.assertEqual(d["banner"]["fade_ms"], 500)
        self.assertEqual(d["banner"]["fit"], "contain")
        self.assertEqual(d["banner"]["caption"], "below")

    # --- настройка баннера -------------------------------------------------
    def test_admin_sees_and_saves_banner_settings(self):
        d = self.admin.get("/api/admin/ads").json()
        self.assertEqual(d["banner"]["rotate_sec"], 8)
        r = self.admin.post("/api/admin/ads/banner",
                            json={"rotate_sec": 3, "fade_ms": 1200,
                                  "layout": "grid", "max": 6, "fit": "cover",
                                  "caption": "side"})
        self.assertEqual(r.json()["banner"], {"rotate_sec": 3, "fade_ms": 1200,
                                             "layout": "grid", "max": 6,
                                             "fit": "cover", "caption": "side"})
        page = self.client.get("/api/ads").json()["banner"]
        self.assertEqual(page["layout"], "grid")
        self.assertEqual(page["rotate_sec"], 3)
        # значение вне границ прижимается, а не улетает в browser-meltdown
        bad = self.admin.post("/api/admin/ads/banner",
                             json={"rotate_sec": 0, "fade_ms": 999999,
                                   "layout": "карусель", "max": 900})
        self.assertEqual(bad.json()["banner"]["rotate_sec"], 2)
        self.assertEqual(bad.json()["banner"]["fade_ms"], 2000)
        self.assertEqual(bad.json()["banner"]["layout"], "carousel")
        self.assertEqual(bad.json()["banner"]["max"], 8)

    def test_max_limits_the_number_of_slides(self):
        self.admin.post("/api/admin/ads/banner", json={"max": 1})
        for i in range(3):
            self.post_ad(text="акция %d" % i, send_at=int(time.time()) - 10 + i)
        items = self.client.get("/api/ads").json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "акция 2")   # свежая сверху

    def test_banner_settings_are_admin_only(self):
        self.assertEqual(self.client.post("/api/admin/ads/banner",
                                          json={"max": 1}).status_code, 401)
        self.assertEqual(self.plain.post("/api/admin/ads/banner",
                                         json={"max": 1}).status_code, 403)

    def test_settings_are_one_json_record(self):
        """Настройка лежит одной записью settings: не надо мигрировать таблицу."""
        self.admin.post("/api/admin/ads/banner", json={"rotate_sec": 5})
        raw = self.store.get_setting("site_banner", "")
        self.assertIn("rotate_sec", raw)
        self.assertEqual(banner_settings(self.store)["rotate_sec"], 5)

    def test_publish_line_names_both_pages(self):
        r = self.post_ad(text="везде", targets={"site": True, "terminal": True,
                                                "digest": True, "hourly": True},
                         send_at=int(time.time()) - 1)
        line = r.json()["item"]["line"]
        self.assertIn("главная: баннер", line)
        self.assertIn("терминал: баннер", line)
        self.assertIn("дайджест: баннер", line)
        self.assertIn("сводка: баннер", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
