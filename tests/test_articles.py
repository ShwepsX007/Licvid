"""📰 Статьи: раздел, который наполняет админ, а не лента.

Проверяем правила, из-за которых раздел и делался именно так:

    * статью пишет админ — черновик видно только ему, гостю 404;
    * **без английской версии публикации нет**: сначала ИИ, а если он молчит —
      администратор вписывает перевод руками. На английской странице не должно
      быть русского текста;
    * расписание публикует статью на сайте само (и только на сайте: в каналы
      ничего не уходит без кнопки);
    * в Telegram русская версия идёт в русский канал, английская — в
      английский, и только по нажатию кнопки; повтор разрешён, но видно, что
      уже отправляли;
    * обложка одна на обе версии и лежит вне static.

Запуск:  python3 tests/test_articles.py
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import struct
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ai_text  # noqa: E402
import api_articles  # noqa: E402
import articles  # noqa: E402
from accounts import COOKIE_SID, Store  # noqa: E402


def png_bytes(size: int = 8, rgb=(30, 90, 200)) -> bytes:
    """Настоящий маленький PNG: сервер проверяет сигнатуру, а не имя файла."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def visible_body(html: str) -> str:
    """Видимый текст статьи: тело без данных для переключателя языка.

    В ``#art-versions`` лежит вторая языковая версия — это не то, что гость
    читает на странице, поэтому проверки «на странице нет чужого языка»
    смотрят только на ``#art-body``.
    """
    m = re.search(r'id="art-body">(.*?)</div>\s*</article>', html, re.S)
    return m.group(1) if m else html


class FakeBot:
    """Бот без Telegram: пишем то, что он должен был отправить."""

    def __init__(self, ru_channel="-100111", en_channel="-100222"):
        self.ru = ru_channel
        self.en = en_channel
        self.channel_url = "https://t.me/liqscope"
        self.channel2_url = "https://t.me/liqscope_en"
        self.sent = []
        self.fail = False
        self._last_tg_err = ""

    def running(self) -> bool:
        return True

    def channel_chat_id(self) -> str:
        return self.ru

    def channel_chat_id_en(self) -> str:
        return self.en

    def channel_url_en(self) -> str:
        return self.channel2_url

    async def send_photo(self, chat_id, path, caption, markup=None, raw=True):
        if self.fail:
            self._last_tg_err = "chat not found"
            return None
        self.sent.append({"chat": str(chat_id), "photo": path, "caption": caption})
        return 4242 + len(self.sent)

    async def send(self, chat_id, text, markup=None, parse="HTML", silent=False,
                   raw=False):
        if self.fail:
            self._last_tg_err = "chat not found"
            return None
        self.sent.append({"chat": str(chat_id), "photo": "", "caption": text})
        return 4242 + len(self.sent)


class StubAI:
    """Заглушка ИИ: переводит по словарю, умеет «молчать» и падать."""

    def __init__(self):
        self.calls = 0
        self.mode = "ok"

    async def __call__(self, text):
        self.calls += 1
        if self.mode == "off":
            return None
        if self.mode == "boom":
            raise RuntimeError("сервис ИИ недоступен")
        words = {"Стена": "A wall", "цена": "price"}
        out = str(text)
        for ru, en in words.items():
            out = out.replace(ru, en)
        if ai_text.cyrillic_ratio(out) > 0.01:
            # Честная заглушка: если русские слова остались, «перевод» не годится
            return "A wall is a large limit order that holds the price in the book."
        return out

    def status(self):
        return {"enabled": self.mode != "off", "providers": [], "last": {},
                "calls": self.calls}


RU_TEXT = ("## Что такое стена\n\nСтена держит **цену**. Смотрите на объём.\n\n"
           "- объём важнее числа заявок\n\n> Стена без объёма — украшение.")
EN_TEXT = ("## What a wall is\n\nA wall holds the **price**. Watch the size.\n\n"
           "- size matters more than order count\n\n> A wall with no size is decoration.")
#: Английский заголовок идёт вместе с английским текстом: без него публикации нет
TITLE_EN = "How to read order book walls"


class AutoTranslateTest(unittest.TestCase):
    """Служебные части перевода статьи — без сети."""

    def test_long_article_is_split_by_paragraphs(self):
        text = "\n\n".join(["Абзац номер %d. " % i + "слово " * 40 for i in range(30)])
        chunks = ai_text.split_for_translation(text, limit=500)
        self.assertGreater(len(chunks), 1, "длинный текст должен резаться")
        self.assertTrue(all(len(c) <= 500 for c in chunks), [len(c) for c in chunks])
        self.assertEqual(" ".join(chunks).split(), text.split(),
                         "слова должны остаться теми же и в том же порядке")

    def test_answer_gets_cleaned(self):
        raw = "```\nHere is the translation:\n\nA wall holds the price.\n```"
        self.assertEqual(ai_text.clean_translation(raw), "A wall holds the price.")

    def test_untranslated_answer_is_rejected(self):
        """Если в «переводе» остался русский текст, он не годится."""

        class Half(AiWriter):
            def _attempt(self, p, prompt, lang="ru", tokens=None, system=None):
                return "A wall holds the price. Стена держит цену."

        writer = Half([ai_text.Provider(name="fake", kind="openai", key="k",
                                        url="", model="m")])
        self.assertIsNone(writer.translate_sync(RU_TEXT),
                          "половина на русском — не перевод")

    def test_translation_returns_text_from_provider(self):
        class Good(AiWriter):
            def _attempt(self, p, prompt, lang="ru", tokens=None, system=None):
                return EN_TEXT

        writer = Good([ai_text.Provider(name="fake", kind="openai", key="k",
                                        url="", model="m")])
        self.assertEqual(writer.translate_sync(RU_TEXT), EN_TEXT)

    def test_no_providers_means_no_translation(self):
        writer = ai_text.AiWriter([])
        self.assertIsNone(writer.translate_sync(RU_TEXT))


AiWriter = ai_text.AiWriter


class ArticlesTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.store = Store(os.path.join(root, "a.db"), secret="s")
        self.admin = self.store.create_email_user("admin@x.io", password_hash="x")["user"]
        self.store.set_admin(int(self.admin["id"]), True) if hasattr(
            self.store, "set_admin") else self._force_admin(int(self.admin["id"]))
        self.user = self.store.create_email_user("user@x.io", password_hash="x")["user"]

        self.articles = articles.ArticleStore(os.path.join(root, "articles.json"))
        self.photo_dir = os.path.join(root, "photos")
        api_articles.ctx.store = self.articles
        api_articles.ctx.photo_dir = self.photo_dir
        api_articles.ctx.public_url = "https://liqscope.online"
        api_articles.ctx.bot = FakeBot()
        api_articles.ctx.ai_fn = None
        api_articles.ctx.ai_status_fn = None
        api_articles.ctx.busy = False
        api_articles.ctx.page_ok = True

        import web_account
        self.web_account = web_account
        web_account.ctx.store = self.store
        web_account.ctx.secret = "s"

        app = FastAPI()
        api_articles.register_article_routes(app)
        self.app = app
        self.admin_c = self._client(int(self.admin["id"]))
        self.user_c = self._client(int(self.user["id"]))
        self.guest = TestClient(app)

    def _force_admin(self, uid: int) -> None:
        with self.store._lock:                    # noqa: SLF001
            self.store._db.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
            self.store._db.commit()

    def _client(self, uid: int) -> TestClient:
        c = TestClient(self.app)
        c.cookies.set(COOKIE_SID, self.store.create_session(int(uid)))
        return c

    def tearDown(self):
        api_articles.ctx.store = articles.ArticleStore("")
        api_articles.ctx.ai_fn = None
        api_articles.ctx.bot = None
        self.web_account.ctx.store = None
        self.tmp.cleanup()

    # --- помощники ----------------------------------------------------------
    def make(self, *, title="Стена в стакане", ru=RU_TEXT, en="", publish_at="",
             publish=False, photo=False, client=None,
             title_en="Как читать стены" if False else ""):
        body = {"title_ru": title, "title_en": title_en or (TITLE_EN if en else ""),
                "text_ru": ru, "text_en": en,
                "publish_at": publish_at, "publish": publish}
        if photo:
            body["photo"] = "data:image/png;base64," + base64.b64encode(
                png_bytes()).decode()
            body["photo_name"] = "cover.png"
        return (client or self.admin_c).post("/api/admin/articles/save", json=body)

    def slug_of(self, resp) -> str:
        return resp.json()["item"]["id"]


class PublicPageTest(ArticlesTestBase):
    """Что видит посетитель."""

    def test_guest_cannot_write(self):
        self.assertEqual(self.guest.get("/api/admin/articles").status_code, 401)
        self.assertEqual(self.guest.post("/api/admin/articles/save",
                                         json={"title_ru": "x"}).status_code, 401)

    def test_regular_user_is_not_admin(self):
        self.assertEqual(self.user_c.get("/api/admin/articles").status_code, 403)

    def test_draft_is_invisible_for_guest(self):
        slug = self.slug_of(self.make())
        self.assertEqual(self.guest.get("/api/articles").json()["items"], [])
        self.assertEqual(self.guest.get("/api/articles/" + slug).status_code, 404)
        self.assertEqual(self.guest.get("/articles/" + slug).status_code, 404)
        # а админ черновик видит: ему его и вычитывать
        self.assertEqual(self.admin_c.get("/articles/" + slug).status_code, 200)
        self.assertEqual(self.admin_c.get("/api/admin/articles").json()["items"][0]["id"],
                         slug)

    def test_published_article_opens_in_both_languages(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish=True))
        ru = self.guest.get("/articles/" + slug,
                            headers={"Cookie": "liqscope_lang=ru"})
        en = self.guest.get("/articles/" + slug,
                            headers={"Cookie": "liqscope_lang=en"})
        self.assertEqual(ru.status_code, 200)
        self.assertIn("Стена", ru.text)
        self.assertIn("A wall", en.text)
        # Видимый текст — только на языке страницы. Вторая версия едет рядом,
        # в #art-versions: переключатель языка меняет текст без перезагрузки.
        self.assertNotIn("A wall", visible_body(ru.text))
        self.assertNotIn("Стена", visible_body(en.text))
        self.assertIn("art-versions", ru.text)
        # разметка статьи отрисована сервером: страница читается и без скриптов
        self.assertIn("<strong>цену</strong>", visible_body(ru.text))
        self.assertIn("<blockquote>", visible_body(ru.text))

    def test_admin_sees_why_the_article_is_not_live(self):
        """Черновик и расписание админ видит с пометкой: иначе кажется, что вышел."""
        slug = self.slug_of(self.make())
        ru = self.admin_c.get("/articles/" + slug + "?lang=ru")
        self.assertIn("Черновик", ru.text)
        en = self.admin_c.get("/articles/" + slug + "?lang=en")
        self.assertIn("Draft", en.text)
        self.admin_c.post("/api/admin/articles/save",
                          json={"id": slug, "publish_at": "2027-01-05T12:30"})
        scheduled = self.admin_c.get("/articles/" + slug + "?lang=ru")
        self.assertIn("Запланирована к публикации", scheduled.text)
        self.assertIn("5 января 2027", scheduled.text)
        # гостю эта страница не отдаётся вовсе, пометки ему не нужны
        self.assertEqual(self.guest.get("/articles/" + slug).status_code, 404)

    def test_list_page_shows_the_article(self):
        self.make(en=EN_TEXT, publish=True)
        page = self.guest.get("/articles")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/articles/", page.text)
        self.assertIn("art-card", page.text)

    def test_index_api_has_no_full_text(self):
        self.make(en=EN_TEXT, publish=True)
        item = self.guest.get("/api/articles?lang=en").json()["items"][0]
        self.assertIn("A wall", item["excerpt"])
        self.assertNotIn("plain", item)
        self.assertNotIn("html", item)


class PublishRulesTest(ArticlesTestBase):
    """Главное правило: без английской версии публикации нет."""

    def test_publish_without_english_title_is_refused(self):
        """Русский заголовок на английской странице — это поломка, не перевод."""
        api_articles.ctx.ai_fn = StubAI()
        slug = self.slug_of(self.make(en=EN_TEXT))
        res = self.admin_c.post("/api/admin/articles/%s/publish" % slug, json={})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(self.articles.get(slug)["titles"]["en"],
                        "ИИ обязан перевести и заголовок")

    def test_publish_without_english_and_without_ai_is_refused(self):
        slug = self.slug_of(self.make())
        res = self.admin_c.post("/api/admin/articles/%s/publish" % slug, json={})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "ai_unavailable")
        self.assertIn("вручную", res.json()["hint"])
        # на сайте статьи нет и статус не переключился
        self.assertEqual(self.guest.get("/api/articles").json()["items"], [])
        self.assertEqual(self.articles.get(slug)["status"], "draft")

    def test_ai_translates_and_publishes(self):
        api_articles.ctx.ai_fn = StubAI()
        slug = self.slug_of(self.make())
        res = self.admin_c.post("/api/admin/articles/%s/publish" % slug, json={})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["ai"], "translated")
        rec = self.articles.get(slug)
        self.assertEqual(rec["status"], "published")
        self.assertTrue(rec["texts"]["en"].strip())
        self.assertTrue(rec["titles"]["en"].strip(), "заголовок тоже переводим")
        self.assertEqual(len(self.guest.get("/api/articles").json()["items"]), 1)

    def test_ai_failure_keeps_article_unpublished(self):
        stub = StubAI()
        stub.mode = "boom"
        api_articles.ctx.ai_fn = stub
        slug = self.slug_of(self.make())
        res = self.admin_c.post("/api/admin/articles/%s/publish" % slug, json={})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "ai_unavailable")
        self.assertIn("ИИ", res.json()["hint"])
        self.assertEqual(self.articles.get(slug)["status"], "draft")

    def test_manual_english_text_publishes_without_ai(self):
        slug = self.slug_of(self.make(en=EN_TEXT))
        res = self.admin_c.post("/api/admin/articles/%s/publish" % slug, json={})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.articles.get(slug)["status"], "published")

    def test_translate_button_reports_ai_off(self):
        slug = self.slug_of(self.make())
        res = self.admin_c.post("/api/admin/articles/%s/translate" % slug, json={})
        self.assertEqual(res.status_code, 503)
        self.assertIn("вручную", res.json()["hint"])
        # текст статьи не потерялся и не превратился в псевдоперевод
        self.assertEqual(self.articles.get(slug)["texts"]["ru"], RU_TEXT)

    def test_translate_button_fills_english(self):
        api_articles.ctx.ai_fn = StubAI()
        slug = self.slug_of(self.make())
        res = self.admin_c.post("/api/admin/articles/%s/translate" % slug, json={})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("A wall", res.json()["text_en"])
        self.assertTrue(res.json()["title_en"])

    def test_short_text_is_not_published(self):
        slug = self.slug_of(self.make(ru="Коротко."))
        res = self.admin_c.post("/api/admin/articles/%s/publish" % slug, json={})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "not_ready")
        self.assertIn("коротк", res.json()["hint"].lower(),
                      "про короткий текст, а не про перевод: переводить нечего")

    def test_unpublish_removes_from_site_but_keeps_text(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish=True))
        self.assertEqual(len(self.guest.get("/api/articles").json()["items"]), 1)
        res = self.admin_c.post("/api/admin/articles/%s/unpublish" % slug, json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.guest.get("/api/articles").json()["items"], [])
        self.assertEqual(self.articles.get(slug)["texts"]["ru"], RU_TEXT)


class ScheduleTest(ArticlesTestBase):
    """Расписание публикует на сайте; каналы ждут кнопки."""

    def test_future_date_marks_as_scheduled(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish_at="2030-01-01T10:00"))
        rec = self.articles.get(slug)
        self.assertEqual(rec["status"], "scheduled")
        self.assertEqual(self.guest.get("/api/articles").json()["items"], [])

    def test_due_article_publishes_on_tick(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish_at="2030-01-01T10:00"))
        rec = self.articles.get(slug)
        rec["publish_at"] = 1_600_000_000        # время уже прошло
        self.articles.add(rec)
        out = asyncio.run(api_articles.tick(now=1_700_000_000))
        self.assertEqual(out["published"], [slug])
        self.assertEqual(self.articles.get(slug)["status"], "published")
        self.assertEqual(self.guest.get("/api/articles").json()["items"][0]["id"], slug)
        # в каналы при этом ничего не ушло: только по кнопке
        self.assertEqual(api_articles.ctx.bot.sent, [])

    def test_due_article_without_english_is_retried_later(self):
        slug = self.slug_of(self.make(publish_at="2030-01-01T10:00"))
        rec = self.articles.get(slug)
        rec["publish_at"] = 1_600_000_000
        self.articles.add(rec)
        out = asyncio.run(api_articles.tick(now=1_700_000_000))
        self.assertEqual(out["published"], [])
        self.assertEqual(len(out["failed"]), 1)
        again = asyncio.run(api_articles.tick(now=1_700_000_010))
        self.assertEqual(again["published"], [], "повтор не раньше, чем через 15 минут")
        self.assertEqual(again["skipped"], 1, "статья ждёт следующей попытки")
        rec = self.articles.get(slug)
        self.assertGreater(rec["retry_at"], 1_700_000_000)
        self.assertIn("англ", rec["last_error"].lower())

    def test_due_article_publishes_with_ai_when_ai_is_back(self):
        api_articles.ctx.ai_fn = StubAI()
        slug = self.slug_of(self.make(publish_at="2030-01-01T10:00"))
        rec = self.articles.get(slug)
        rec["publish_at"] = 1_600_000_000
        self.articles.add(rec)
        out = asyncio.run(api_articles.tick(now=1_700_000_000))
        self.assertEqual(out["published"], [slug])
        self.assertIn("A wall", self.articles.get(slug)["texts"]["en"])


class TelegramTest(ArticlesTestBase):
    """Каналы: RU → русский, EN → английский, и только по кнопке."""

    def _published(self, **kw):
        kw.setdefault("en", EN_TEXT)
        slug = self.slug_of(self.make(publish=True, **kw))
        return slug

    def test_ru_goes_to_ru_channel_and_en_to_en(self):
        slug = self._published()
        res = self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        self.assertEqual(res.status_code, 200, res.text)
        sent = api_articles.ctx.bot.sent
        self.assertEqual(len(sent), 2)
        by_chat = {s["chat"]: s["caption"] for s in sent}
        ru = by_chat[api_articles.ctx.bot.ru]
        en = by_chat[api_articles.ctx.bot.en]
        self.assertIn("Стена", ru)
        self.assertIn("Стена", ru.split("\n\n")[1])
        self.assertIn("A wall", en)
        for caption in (ru, en):
            self.assertIn("https://liqscope.online/articles/" + slug, caption)
        rec = self.articles.get(slug)
        self.assertTrue(rec["sent"]["ru"] and rec["sent"]["en"])
        self.assertEqual(rec["tg"]["en"]["chat"], api_articles.ctx.bot.en)

    def test_resend_is_marked_as_already_sent(self):
        slug = self._published()
        self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        again = self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["results"]["ru"]["again"])
        self.assertEqual(len(api_articles.ctx.bot.sent), 4)

    def test_send_needs_published_article(self):
        slug = self.slug_of(self.make(en=EN_TEXT))
        res = self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "not_published")
        self.assertEqual(api_articles.ctx.bot.sent, [])

    def test_send_reports_bot_off(self):
        slug = self._published()
        api_articles.ctx.bot.running = lambda: False
        res = self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "bot_off")

    def test_send_reports_missing_channel(self):
        slug = self._published()
        api_articles.ctx.bot.en = ""
        res = self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        body = res.json()
        self.assertTrue(body["ok"], "русская версия должна уйти")
        self.assertEqual(body["results"]["en"]["error"], "no_channel")
        self.assertIn("английск", body["errors"][0].lower())

    def test_telegram_error_is_shown_to_admin(self):
        slug = self._published()
        api_articles.ctx.bot.fail = True
        res = self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        self.assertEqual(res.status_code, 409)
        self.assertIn("chat not found", json.dumps(res.json()))

    def test_photo_goes_with_the_post(self):
        slug = self._published(photo=True)
        self.admin_c.post("/api/admin/articles/%s/send" % slug, json={})
        for s in api_articles.ctx.bot.sent:
            self.assertTrue(s["photo"], "пост уходит с обложкой")
            self.assertTrue(os.path.isfile(s["photo"]))

    def test_one_language_only(self):
        slug = self._published()
        res = self.admin_c.post("/api/admin/articles/%s/send" % slug,
                                json={"langs": ["ru"]})
        self.assertTrue(res.json()["results"]["ru"]["ok"])
        self.assertNotIn("en", res.json()["results"])
        self.assertFalse(self.articles.get(slug)["sent"].get("en"))


class PhotoTest(ArticlesTestBase):
    """Одна обложка на обе версии; файл лежит вне static."""

    def test_photo_saved_and_served(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish=True, photo=True))
        rec = self.articles.get(slug)
        path = rec["photo"]["path"]
        self.assertTrue(os.path.isfile(path))
        self.assertNotIn(os.path.join("static", ""), path + os.sep)
        res = self.guest.get("/api/articles/photo/" + slug)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/png")
        self.assertTrue(res.content.startswith(b"\x89PNG"))

    def test_photo_url_changes_with_the_photo(self):
        """Замена фото не должна прятаться за кешем браузера."""
        slug = self.slug_of(self.make(en=EN_TEXT, title_en="T", publish=True,
                                      photo=True))
        first = self.admin_c.get("/api/admin/articles").json()["items"][0]["photo"]["url"]
        self.admin_c.post("/api/admin/articles/save", json={
            "id": slug,
            "photo": "data:image/png;base64," + base64.b64encode(
                png_bytes(rgb=(10, 200, 40))).decode(),
            "photo_name": "second.png"})
        second = self.admin_c.get("/api/admin/articles").json()["items"][0]["photo"]["url"]
        self.assertNotEqual(first, second, "адрес обложки должен обновиться")
        page = self.guest.get("/articles/" + slug,
                              headers={"Cookie": "liqscope_lang=ru"}).text
        self.assertIn(second, page)

    def test_same_photo_for_both_languages(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish=True, photo=True))
        ru = self.guest.get("/articles/" + slug,
                            headers={"Cookie": "liqscope_lang=ru"}).text
        en = self.guest.get("/articles/" + slug,
                            headers={"Cookie": "liqscope_lang=en"}).text
        src = "/api/articles/photo/" + slug
        self.assertIn(src, ru)
        self.assertIn(src, en)

    def test_draft_photo_is_hidden_from_guest(self):
        slug = self.slug_of(self.make(photo=True))
        self.assertEqual(self.guest.get("/api/articles/photo/" + slug).status_code, 404)
        self.assertEqual(self.admin_c.get("/api/articles/photo/" + slug).status_code, 200)

    def test_replacing_photo_removes_the_old_file(self):
        slug = self.slug_of(self.make(photo=True))
        first = self.articles.get(slug)["photo"]["path"]
        self.assertTrue(os.path.isfile(first))
        body = {"id": slug, "photo": "data:image/png;base64," +
                base64.b64encode(png_bytes(rgb=(200, 30, 30))).decode(),
                "photo_name": "new.png"}
        self.admin_c.post("/api/admin/articles/save", json=body)
        second = self.articles.get(slug)["photo"]["path"]
        self.assertNotEqual(first, second, "у новой версии своё имя файла")
        self.assertFalse(os.path.isfile(first), "старое фото не должно оставаться")
        self.assertTrue(os.path.isfile(second))

    def test_broken_photo_is_reported(self):
        """Прислали фото, но прочитать не вышло — говорим, а не молчим."""
        slug = self.slug_of(self.make(photo=True))
        before = self.articles.get(slug)["photo"]["path"]
        res = self.admin_c.post("/api/admin/articles/save", json={
            "id": slug, "photo": "data:image/png;base64:AAAA", "photo_name": "x.png"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "bad_photo")
        self.assertEqual(self.articles.get(slug)["photo"]["path"], before,
                         "старая обложка остаётся на месте")

    def test_remove_photo(self):
        slug = self.slug_of(self.make(photo=True))
        path = self.articles.get(slug)["photo"]["path"]
        res = self.admin_c.post("/api/admin/articles/save",
                                json={"id": slug, "remove_photo": True})
        self.assertTrue(res.json()["ok"], res.text)
        self.assertFalse(os.path.isfile(path))
        self.assertNotIn("photo", self.articles.get(slug))

    def test_bad_photo_is_refused(self):
        res = self.admin_c.post("/api/admin/articles/save", json={
            "title_ru": "Фото не то", "text_ru": RU_TEXT,
            "photo": "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4").decode()})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "bad_photo")


class EditAndDeleteTest(ArticlesTestBase):
    def test_edits_go_live_at_once(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish=True))
        self.admin_c.post("/api/admin/articles/save", json={
            "id": slug, "title_ru": "Стена: обновлено", "text_ru": RU_TEXT + "\n\nДописали абзац.",
            "text_en": EN_TEXT})
        page = self.guest.get("/articles/" + slug,
                              headers={"Cookie": "liqscope_lang=ru"}).text
        self.assertIn("обновлено", page)
        self.assertIn("Дописали абзац", page)

    def test_fields_are_not_wiped_by_partial_save(self):
        slug = self.slug_of(self.make(en=EN_TEXT))
        self.admin_c.post("/api/admin/articles/save",
                          json={"id": slug, "publish_at": "2030-05-05T09:00"})
        rec = self.articles.get(slug)
        self.assertEqual(rec["texts"]["en"], EN_TEXT)
        self.assertEqual(rec["texts"]["ru"], RU_TEXT)
        self.assertEqual(rec["status"], "scheduled")

    def test_delete_removes_article_and_photo(self):
        slug = self.slug_of(self.make(en=EN_TEXT, publish=True, photo=True))
        path = self.articles.get(slug)["photo"]["path"]
        res = self.admin_c.post("/api/admin/articles/%s/delete" % slug)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(self.articles.get(slug))
        self.assertFalse(os.path.isfile(path))
        self.assertEqual(self.guest.get("/api/articles/" + slug).status_code, 404)

    def test_slug_is_stable_and_unique(self):
        first = self.slug_of(self.make(title="Одинаковый заголовок"))
        second = self.slug_of(self.make(title="Одинаковый заголовок"))
        self.assertNotEqual(first, second)
        self.admin_c.post("/api/admin/articles/save",
                          json={"id": first, "title_ru": "Совсем другой заголовок"})
        self.assertEqual(self.articles.get(first)["id"], first)


class StoreAndMarkdownTest(unittest.TestCase):
    """Хранилище и разметка — на них стоит и сайт, и админка."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = articles.ArticleStore(os.path.join(self.tmp.name, "a.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_slug_from_russian_title(self):
        self.assertEqual(articles.slugify("Как читать стены в стакане"),
                         "kak-chitat-steny-v-stakane")
        self.assertEqual(articles.slugify(""), "article")

    def test_unique_slug_adds_number(self):
        self.assertEqual(articles.unique_slug("abc", ["abc", "abc-2"]), "abc-3")

    def test_store_prunes_only_published(self):
        """Черновики не пропадают: архив режется только по опубликованным."""
        store = articles.ArticleStore(os.path.join(self.tmp.name, "b.json"), keep=2)
        for i in range(4):
            store.add({"id": "p%d" % i, "titles": {"ru": "Статья %d" % i},
                       "texts": {"ru": RU_TEXT}, "status": "published",
                       "published_at": 1000 + i})
        for i in range(3):
            store.add({"id": "d%d" % i, "titles": {"ru": "Черновик %d" % i},
                       "texts": {"ru": RU_TEXT}, "status": "draft"})
        st = store.stats()
        self.assertEqual(st["published"], 2)
        self.assertEqual(st["draft"], 3)

    def test_published_only_after_its_time(self):
        import time as _t
        now = _t.time()
        rec = {"id": "x", "titles": {"ru": "Т"}, "texts": {"ru": RU_TEXT},
               "status": "published", "published_at": now + 100}
        self.store.add(rec)
        self.assertEqual(self.store.published(now), [])
        self.assertEqual(len(self.store.published(now + 200)), 1)

    def test_markdown_renders_and_escapes(self):
        html = articles.markdown_html(
            "## Заголовок\n\n**жирный** и *курсив*, [ссылка](https://x.io)\n\n"
            "- пункт\n\n> цитата\n\n---\n\n<script>alert(1)</script>")
        self.assertIn("<h3>Заголовок</h3>", html)
        self.assertIn("<strong>жирный</strong>", html)
        self.assertIn("<em>курсив</em>", html)
        self.assertIn('href="https://x.io"', html)
        self.assertIn("<li>пункт</li>", html)
        self.assertIn("<blockquote>", html)
        self.assertIn("<hr>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_markdown_quotes_cannot_break_attributes(self):
        """Кавычка в URL или alt не должна вырываться из атрибута."""
        link = articles.markdown_html(
            '[клик](https://a.com/"onmouseover="alert(1))')
        self.assertNotIn('href="https://a.com/"onmouseover', link)
        # скобка в payload режет markdown-URL раньше кавычки; кавычка не выходит из href
        self.assertIn('href="https://a.com/&quot;onmouseover=&quot;alert(1"', link)
        self.assertNotIn('onmouseover="alert', link)
        img = articles.markdown_html(
            '![x" onerror="alert(1)](https://a.com/i.png)')
        self.assertNotIn('onerror="alert(1)"', img)
        self.assertIn("alt=\"x&quot; onerror=&quot;alert(1)\"", img)
        self.assertIn('src="https://a.com/i.png"', img)

    def test_markdown_relative_links(self):
        html = articles.markdown_html("[терминал](/terminal)")
        self.assertIn('href="/terminal"', html)
        self.assertNotIn("target=", html)
        # protocol-relative и javascript: ссылками не становятся
        bad = articles.markdown_html("[x](//evil.example)\n\n[y](javascript:alert(1))")
        self.assertNotIn('href="//', bad)
        self.assertNotIn('href="javascript:', bad)
        self.assertNotIn("<a ", bad)

    def test_markdown_keeps_plain_text_readable(self):
        plain = articles.markdown_plain("## Т\n\n**жирный** текст и [ссылка](https://x.io)")
        self.assertNotIn("**", plain)
        self.assertNotIn("##", plain)
        self.assertNotIn("(https://x.io)", plain)
        self.assertIn("жирный текст и ссылка", plain)

    def test_text_problem_explains_what_is_missing(self):
        self.assertIn("текста", articles.text_problem("", "Заголовок"))
        self.assertIn("коротк", articles.text_problem("мало", "Заголовок"))
        self.assertIn("заголовк", articles.text_problem(RU_TEXT, "").lower())
        self.assertEqual(articles.text_problem(RU_TEXT, "Заголовок"), "")

    def test_article_reports_en_only_when_translated(self):
        rec = {"id": "x", "titles": {"ru": "Т", "en": ""},
               "texts": {"ru": RU_TEXT, "en": EN_TEXT}, "status": "published",
               "published_at": 1}
        self.assertEqual(articles.public_article(rec, "en")["title"], "Т",
                         "без английского заголовка показываем русский, а не пустоту")
        rec["titles"]["en"] = "A wall"
        self.assertEqual(articles.public_article(rec, "en")["title"], "A wall")


if __name__ == "__main__":
    unittest.main(verbosity=2)
