"""SEO и языковая адаптация страниц сайта — одни и те же правила для всех.

Разделов на сайте уже семь (лендинг, терминал, дайджест, сводки по часам,
статьи, вход, кабинет), и каждый обязан отвечать на одни и те же вопросы
поисковика и гостя:

* ``<html lang>`` и ``Content-Language`` — язык страницы;
* ``<title>`` и описание — на языке гостя (на английской странице кириллицы
  быть не должно);
* ``rel="canonical"`` и ``og:url`` — **один** адрес страницы, а не раздела
  (у статьи и выпуска он свой);
* hreflang на все пять языков плюс ``x-default``;
* ``og:*`` и ``twitter:*`` говорят одно и то же (иначе превью ссылки в
  соцсети расходится с выдачей);
* ``robots`` — служебные страницы закрыты, публичные открыты;
* JSON-LD разбирается как JSON и знает, о чём страница;
* раздел «Статьи» доступен со всех страниц, где есть остальные разделы.

Запуск: ``python3 tests/test_pages_seo.py``
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import articles  # noqa: E402
import seo_pages  # noqa: E402
import api_articles  # noqa: E402

LANGS = ("ru", "en", "zh", "hi", "es")
DEFAULT = "en"                       # язык сайта по умолчанию (без ?lang=)
CYR = re.compile(r"[А-Яа-яЁё]")


def page_url(path: str, lang: str) -> str:
    """Адрес языковой версии: у языка по умолчанию — без параметра."""
    base = seo_pages.SITE_URL + path
    return base if lang == DEFAULT else f"{base}?lang={lang}"

#: страница → закрыта ли от поисковика
PAGES = {
    "/": False,
    "/terminal": False,
    "/digest": False,
    "/hourly": False,
    "/articles": False,
    "/login": False,
    "/reset": True,
    "/cabinet": True,
    "/admin": True,
    "/no-such-page-here": True,
}


def attr(tag: str, name: str) -> str:
    m = re.search(name + r'="([^"]*)"', tag)
    return html_mod.unescape(m.group(1)) if m else ""


def head_of(html: str) -> str:
    return html.split("</head>", 1)[0]


def meta(html: str, key: str) -> str:
    """Содержимое <meta> по имени или property."""
    for tag in re.findall(r"<meta\b[^>]*>", head_of(html), re.I):
        if attr(tag, "name") == key or attr(tag, "property") == key:
            return html_mod.unescape(attr(tag, "content"))
    return ""


def metas(html: str, key: str) -> list:
    out = []
    for tag in re.findall(r"<meta\b[^>]*>", head_of(html), re.I):
        if attr(tag, "name") == key or attr(tag, "property") == key:
            out.append(html_mod.unescape(attr(tag, "content")))
    return out


def link(html: str, rel: str) -> list:
    out = []
    for tag in re.findall(r"<link\b[^>]*>", head_of(html), re.I):
        if attr(tag, "rel").lower() == rel:
            out.append(tag)
    return out


def canonical(html: str) -> str:
    tags = link(html, "canonical")
    return attr(tags[0], "href") if tags else ""


def hreflangs(html: str) -> dict:
    out = {}
    for tag in link(html, "alternate"):
        code = attr(tag, "hreflang")
        if code:
            out[code] = attr(tag, "href")
    return out


def title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return html_mod.unescape(m.group(1)).strip() if m else ""


def jsonld_blocks(html: str) -> list:
    """Объекты Schema.org со страницы: в блоке их может быть и список."""
    out = []
    for raw in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.S | re.I):
        data = json.loads(raw)
        items = data if isinstance(data, list) else [data]
        out.extend(x for x in items if isinstance(x, dict))
    return out


class PagesSeoTest(unittest.TestCase):
    """Все страницы отвечают одним и тем же правилам SEO."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from fastapi.testclient import TestClient
            import server
        except Exception as exc:  # noqa: BLE001 — окружение без зависимостей
            raise unittest.SkipTest(f"приложение не поднимается: {exc}")
        cls.client = TestClient(server.app)
        cls.site = seo_pages.SITE_URL

    def get(self, path: str, lang: str):
        sep = "&" if "?" in path else "?"
        return self.client.get(f"{path}{sep}lang={lang}")

    # --- head --------------------------------------------------------------
    def test_head_is_complete_in_every_language(self) -> None:
        texts = {path: {"title": {}, "desc": {}} for path in PAGES}
        for path, noindex in PAGES.items():
            for lang in LANGS:
                with self.subTest(path=path, lang=lang):
                    r = self.get(path, lang)
                    self.assertEqual(r.status_code, 404 if noindex and path.startswith("/no-such") else 200)
                    self.assertNotIn("{{", r.text, "в HTML остались метки шаблона")
                    html = r.text
                    self.assertTrue(html.startswith("<!DOCTYPE html") or "<html" in html)
                    self.assertIn(f'<html lang="{seo_pages.HREFLANG[lang]}"', html)
                    self.assertEqual(r.headers.get("content-language"),
                                     seo_pages.HREFLANG[lang])
                    title = title_of(html)
                    desc = meta(html, "description")
                    self.assertTrue(title, "нет <title>")
                    self.assertTrue(desc, "нет описания страницы")
                    self.assertEqual(bool(CYR.search(title)), lang == "ru",
                                     f"заголовок не на языке страницы: {title!r}")
                    self.assertEqual(bool(CYR.search(desc)), lang == "ru",
                                     f"описание не на языке страницы: {desc!r}")
                    texts[path]["title"][lang] = title
                    texts[path]["desc"][lang] = desc
        # Адаптация, а не перевод «в двух языках»: у каждой страницы должны
        # быть свои строки для всех пяти языков, без английского на месте
        # китайского и русского
        for path, got in texts.items():
            with self.subTest(path=path):
                for what, values in got.items():
                    self.assertEqual(len(set(values.values())), len(LANGS),
                                     f"{path}: {what} повторяется между языками: {values}")

    def test_canonical_hreflang_and_og_url_agree(self) -> None:
        for path in PAGES:
            # страница «не найдено» отдаётся на любой чужой адрес и указывает
            # canonical на себя, а не на мусорный URL со случайным путём
            want_path = "/404" if path.startswith("/no-such") else path
            want = self.site + want_path
            for lang in LANGS:
                with self.subTest(path=path, lang=lang):
                    html = self.get(path, lang).text
                    canon = canonical(html)
                    self.assertEqual(canon, page_url(want_path, lang),
                                     "canonical — адрес именно этой страницы")
                    self.assertEqual(meta(html, "og:url"), canon,
                                     "og:url должен совпадать с canonical")
                    links = hreflangs(html)
                    codes = set(seo_pages.HREFLANG.values())
                    self.assertEqual(sorted(links), sorted(codes | {"x-default"}),
                                     f"hreflang неполный: {sorted(links)}")
                    self.assertEqual(links["x-default"], want)
                    for code in LANGS:      # в hreflang — код языка, в адресе — ?lang=
                        self.assertEqual(links[seo_pages.HREFLANG[code]],
                                         page_url(want_path, code))
                    self.assertEqual(meta(html, "og:locale"), seo_pages.OG_LOCALE[lang])
                    self.assertEqual(len(metas(html, "og:locale:alternate")), len(LANGS) - 1)

    def test_social_preview_repeats_the_page(self) -> None:
        """Превью ссылки в соцсети — тот же заголовок и текст, что и на странице."""
        for path in PAGES:
            for lang in LANGS:
                with self.subTest(path=path, lang=lang):
                    html = self.get(path, lang).text
                    title = title_of(html)
                    desc = meta(html, "description")
                    self.assertEqual(meta(html, "og:title"), title)
                    self.assertEqual(meta(html, "og:description"), desc)
                    self.assertEqual(meta(html, "twitter:title"), title)
                    self.assertEqual(meta(html, "twitter:description"), desc)
                    self.assertEqual(meta(html, "twitter:card"), "summary_large_image")
                    self.assertEqual(meta(html, "twitter:image"), meta(html, "og:image"))
                    self.assertTrue(meta(html, "og:image").startswith("https://"))
                    self.assertTrue(meta(html, "og:image:alt"), "у картинки нет подписи")
                    self.assertTrue(meta(html, "og:type"))

    def test_robots_policy(self) -> None:
        for path, noindex in PAGES.items():
            with self.subTest(path=path):
                robots = meta(self.get(path, "en").text, "robots").lower()
                self.assertTrue(robots, "нет meta robots")
                if noindex:
                    self.assertIn("noindex", robots)
                else:
                    self.assertIn("index", robots)
                    self.assertNotIn("noindex", robots)

    def test_jsonld_is_valid_and_about_the_page(self) -> None:
        for path in ("/", "/terminal", "/digest", "/hourly", "/articles", "/no-such-page-here"):
            for lang in LANGS:
                with self.subTest(path=path, lang=lang):
                    blocks = jsonld_blocks(self.get(path, lang).text)
                    self.assertTrue(blocks, "страница без разметки Schema.org")
                    for item in blocks:
                        self.assertEqual(item.get("@context"), "https://schema.org",
                                         "у блока JSON-LD нет @context")
                    text = json.dumps(blocks, ensure_ascii=False)
                    self.assertIn(seo_pages.HREFLANG[lang], text,
                                  "в разметке не указан язык страницы")

    def test_robots_txt_and_sitemap_cover_the_sections(self) -> None:
        robots = self.client.get("/robots.txt").text
        self.assertIn("Sitemap:", robots)
        self.assertNotIn("Disallow: /articles", robots)
        sitemap = self.client.get("/sitemap.xml").text
        for path in ("/", "/terminal", "/digest", "/hourly", "/articles"):
            self.assertIn(f"<loc>{self.site}{path}</loc>", sitemap, path)

    # --- навигация ---------------------------------------------------------
    def test_articles_section_is_linked_from_every_page(self) -> None:
        """Раздел «Статьи» — рядом с остальными разделами, из любой страницы.

        На лендинге, в дайджесте, сводках и на 404 ссылка стоит прямо в HTML
        (её видит и гость без скриптов, и поисковик). На страницах со шапкой
        кабинета кнопки рисует ``static/account.js`` — там их и проверяем.
        """
        for path in ("/", "/digest", "/hourly", "/articles", "/no-such-page-here"):
            with self.subTest(path=path):
                html = self.get(path, "ru").text
                for section in ("/articles", "/digest", "/hourly", "/terminal"):
                    if section == path:
                        continue          # страница сама себе ссылкой не нужна
                    self.assertIn(f'href="{section}"', html,
                                  f"нет ссылки на раздел {section}")
        # шапка кабинета/терминала/админки: кнопки живёт в скрипте, и разделы
        # там перечислены один раз — список общий для гостя и вошедшего
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        js = open(os.path.join(root, "static", "account.js"), encoding="utf-8").read()
        for section in ("/terminal", "/digest", "/hourly", "/articles"):
            self.assertEqual(js.count('href: "%s"' % section), 1,
                             f"раздел {section} должен быть в общей шапке ровно раз")
        self.assertIn("sectionLinks", js, "разделы рисует общая функция шапки")
        self.assertIn("ownSection", js, "на саму себя страница ссылкой не нужна")
        self.assertIn("art.title", js, "подпись кнопки берётся из словаря")

    def test_section_pages_carry_the_same_menu(self) -> None:
        """Меню разделов одинаково на всех страницах: без ссылки на саму себя."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pages = {
            "digest.html": "/digest", "hourly.html": "/hourly",
            "articles.html": "/articles", "article.html": "/articles",
            "404.html": "/404", "landing.html": "/",
        }
        for name, own in pages.items():
            with self.subTest(page=name):
                html = open(os.path.join(root, "static", name), encoding="utf-8").read()
                for section in ("/articles", "/hourly", "/digest", "/terminal"):
                    if section in own:
                        continue
                    self.assertIn(f'href="{section}"', html,
                                  f"{name}: нет ссылки на {section}")


    def test_every_header_loads_the_shared_menu(self) -> None:
        """Шапку рисует один скрипт: страница без него — тупик.

        Так раздел «Статьи» остался без кнопки кабинета: в разметке только
        логотип и выбор языка, а ``static/account.js``, который дорисовывает
        меню, к странице не подключили. Теперь это ловит тест, а не гость.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        static = os.path.join(root, "static")
        for name in sorted(os.listdir(static)):
            if not name.endswith(".html"):
                continue
            html = open(os.path.join(static, name), encoding="utf-8").read()
            with self.subTest(page=name):
                self.assertIn('id="nav-account"', html, f"{name}: нет шапки с меню")
                self.assertIn("/static/account.js", html,
                              f"{name}: шапку рисует скрипт, а он не подключён")

        # Шапку рисует ровно один скрипт. Дайджест и сводки держали свою
        # копию — она затирала общую и всегда показывала «Войти», даже когда
        # человек уже вошёл (и проверяла у ответа /api/auth/me поле `ok`,
        # которого там нет).
        for name in sorted(os.listdir(static)):
            if not name.endswith(".js") or name == "account.js":
                continue
            body = open(os.path.join(static, name), encoding="utf-8").read()
            with self.subTest(script=name):
                self.assertNotIn("nav-account", body,
                                 f"{name}: шапку рисует только static/account.js")

    def test_mobile_menu_complements_the_pages_own_links(self) -> None:
        """Ссылки, скрытые на телефоне, шапка заменяет своими кнопками.

        Главная держит разделы в разметке как ``nav-desk`` (CSS прячет их на
        телефоне), а ``static/account.js`` дорисовывает кнопки ``nav-mob``
        вместо них. Договор держится на двух правилах CSS: одно прячет ссылку
        на телефоне, второе — кнопку на широком экране. Без второго на
        компьютере каждый раздел показывался бы дважды.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        static = os.path.join(root, "static")
        landing = open(os.path.join(static, "landing.html"), encoding="utf-8").read()
        account_css = open(os.path.join(static, "account.css"), encoding="utf-8").read()
        account_js = open(os.path.join(static, "account.js"), encoding="utf-8").read()
        self.assertIn(".nav-desk", landing, "разделы главной прячутся на телефоне")
        self.assertIn("a.nav-mob", landing,
                      "главная не прячет кнопки-замены на широком экране")
        self.assertIn("a.nav-mob", account_css,
                      "тем же правилом пользуются остальные страницы")
        self.assertIn("nav-desk", account_js,
                      "шапка должна знать про ссылки, скрытые на телефоне")
        self.assertIn("nav-mob", account_js, "и рисовать себе пару")


class ArticlePagesSeoTest(unittest.TestCase):
    """Страница статьи: свой адрес, свои заголовки и пометка языка текста."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from fastapi.testclient import TestClient
            import server
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"приложение не поднимается: {exc}")
        cls.client = TestClient(server.app)
        cls.site = seo_pages.SITE_URL

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_store = api_articles.ctx.store
        self.old_dir = api_articles.ctx.photo_dir
        self.store = articles.ArticleStore(os.path.join(self.tmp.name, "a.json"))
        api_articles.ctx.store = self.store
        api_articles.ctx.photo_dir = os.path.join(self.tmp.name, "photos")
        api_articles.ctx.public_url = self.site
        self.store.add({
            "id": "proverka-seo", "status": "published", "published_at": 1_700_000_000.0,
            "titles": {"ru": "Проверка SEO", "en": "SEO check"},
            "texts": {"ru": "Первый абзац.\n\nВторой абзац.",
                      "en": "First paragraph.\n\nSecond paragraph."},
        })
        self.slug = str(api_articles.ctx.store.list()[0].get("id"))

    def tearDown(self) -> None:
        api_articles.ctx.store = self.old_store
        api_articles.ctx.photo_dir = self.old_dir
        self.tmp.cleanup()

    def test_article_page_has_its_own_address_and_headlines(self) -> None:
        for lang, want_title in (("ru", "Проверка SEO"), ("en", "SEO check")):
            with self.subTest(lang=lang):
                r = self.client.get(f"/articles/{self.slug}?lang={lang}")
                self.assertEqual(r.status_code, 200)
                html = r.text
                want = f"{self.site}/articles/{self.slug}"
                self.assertEqual(canonical(html), page_url(f"/articles/{self.slug}", lang))
                self.assertEqual(meta(html, "og:url"), canonical(html))
                self.assertEqual(title_of(html), want_title)
                self.assertEqual(meta(html, "og:title"), want_title)
                self.assertEqual(meta(html, "twitter:title"), want_title)
                desc = meta(html, "description")
                self.assertTrue(desc)
                self.assertEqual(meta(html, "og:description"), desc)
                self.assertEqual(meta(html, "twitter:description"), desc)
                self.assertEqual(meta(html, "og:type"), "article")
                self.assertTrue(meta(html, "article:published_time"))
                self.assertEqual(hreflangs(html)["x-default"], want)
                blocks = jsonld_blocks(html)
                self.assertTrue(blocks, "нет разметки статьи")
                self.assertIn("BlogPosting", json.dumps(blocks, ensure_ascii=False))
                self.assertIn(want, json.dumps(blocks, ensure_ascii=False))

    def test_article_page_has_a_language_switch(self) -> None:
        """Переключатель RU/EN на странице статьи: обе версии и текущая.

        Гость видит, что у статьи есть английская версия, и может перейти на
        неё одним кликом — без этого английский текст существовал, но найти
        его было нечем (как на дайджесте и в сводках по часам).
        """
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                html = self.client.get(f"/articles/{self.slug}?lang={lang}").text
                box = re.search(r'id="art-langs">(.*?)</div>', html, re.S)
                self.assertTrue(box, "переключателя языка нет на странице")
                chips = re.findall(r"<a\b[^>]*class=\"art-lang[^\"]*\"[^>]*>", box.group(1))
                self.assertEqual(len(chips), 2, "нужны обе версии: RU и EN")
                for code in ("ru", "en"):
                    chip = [c for c in chips if f'data-lang="{code}"' in c]
                    self.assertEqual(len(chip), 1, f"нет ссылки на {code}")
                    self.assertIn(f'href="?lang={code}"', chip[0])   # без JS тоже работает
                    self.assertIn(f'hreflang="{code}"', chip[0])
                current = [c for c in chips if 'class="art-lang on"' in c]
                self.assertEqual(len(current), 1, "текущий язык помечен один раз")
                self.assertIn(f'data-lang="{lang}"', current[0])
                # подпись «Язык статьи:» — на языке самой страницы
                self.assertIn(seo_pages.text(lang, "art.langs"), html)

    def test_article_page_carries_both_texts_for_the_switcher(self) -> None:
        """Обе версии текста едут вместе со страницей: переключение — без беготни.

        Ссылка ``?lang=xx`` остаётся запасным путём для гостя без JS, а скрипт
        берёт заголовок и текст из ``#art-versions`` и меняет их на месте.
        """
        html = self.client.get(f"/articles/{self.slug}?lang=ru").text
        raw = re.search(r'id="art-versions">(.*?)</script>', html, re.S)
        self.assertTrue(raw, "нет данных для переключения без перезагрузки")
        # внутри <script> не должно быть «<»: иначе текст статьи закроет тег
        self.assertNotIn("<", raw.group(1))
        data = json.loads(raw.group(1))
        self.assertEqual(set(data), {"ru", "en"})
        self.assertEqual(data["ru"]["title"], "Проверка SEO")
        self.assertEqual(data["en"]["title"], "SEO check")
        self.assertIn("Первый абзац.", data["ru"]["html"])
        self.assertIn("First paragraph.", data["en"]["html"])

    def test_list_card_shows_which_versions_exist(self) -> None:
        """В списке видно, что у статьи есть английская версия.

        Иначе гость читает русскую карточку и не знает, что материал есть и
        на английском — ровно то, из-за чего английскую статью «не находили».
        """
        self.store.add({"id": "odna-versiya-karta", "status": "published",
                        "published_at": 1_700_000_300.0,
                        "titles": {"ru": "Только по-русски"},
                        "texts": {"ru": "Первый абзац."}})
        cards = self.client.get("/articles?lang=ru").text
        two = re.search(r'href="/articles/' + re.escape(self.slug) + r'"[^>]*>.*?</a>',
                        cards, re.S)
        self.assertTrue(two, "карточка статьи не найдена в списке")
        self.assertIn('<span class="ac-langs">RU · EN</span>', two.group(0))
        one = re.search(r'href="/articles/odna-versiya-karta"[^>]*>.*?</a>', cards, re.S)
        self.assertTrue(one, "карточка одноязычной статьи не найдена")
        self.assertNotIn("ac-langs", one.group(0))

    def test_one_version_means_no_switch(self) -> None:
        """Версия одна — переключать нечего: пустой блок и никаких ссылок."""
        self.store.add({"id": "tolko-russkiy-link", "status": "published",
                        "published_at": 1_700_000_200.0,
                        "titles": {"ru": "Только по-русски"}})
        html = self.client.get("/articles/tolko-russkiy-link?lang=ru").text
        box = re.search(r'id="art-langs">(.*?)</div>', html, re.S)
        self.assertTrue(box)
        self.assertNotIn("art-lang\"", box.group(1))
        self.assertNotIn("data-lang", box.group(1))

    def test_russian_text_on_the_english_page_is_marked(self) -> None:
        """Английской версии нет — русский текст помечаем ``lang="ru"``."""
        self.store.add({"id": "tolko-russkiy", "status": "published",
                        "published_at": 1_700_000_100.0,
                        "titles": {"ru": "Только по-русски"},
                        "texts": {"ru": "Первый абзац.\n\nВторой абзац."}})
        r = self.client.get("/articles/tolko-russkiy?lang=en")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn("Только по-русски", html)
        self.assertIn('<h1 class="art-title" id="art-title" lang="ru">', html)
        self.assertIn('<div class="art-body" id="art-body" lang="ru">', html)
        # на русской странице той же статьи пометка не нужна: язык совпадает
        ru = self.client.get("/articles/tolko-russkiy?lang=ru").text
        self.assertNotIn('id="art-body" lang=', ru)
        # в списке карточка тоже говорит, на каком языке текст
        cards = self.client.get("/articles?lang=en").text
        self.assertIn('lang="ru"', cards)

    def test_list_chrome_is_localized_on_the_server(self) -> None:
        """Список статей: подписи карточек приходят сразу на языке гостя.

        Заголовок страницы и описание сервер берёт из словаря, а «Читать →»
        на карточке раньше был только русским или английским — китайский,
        хинди и испанский гости видели английскую подпись.
        """
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self.client.get(f"/articles?lang={lang}").text
                self.assertIn(seo_pages.text(lang, "art.read"), html)
                if lang != "ru":
                    self.assertNotIn('class="ac-more">Читать', html)

    def test_unknown_article_is_a_clean_404(self) -> None:
        r = self.client.get("/articles/nyet-takoy-stati")
        self.assertEqual(r.status_code, 404)
        self.assertIn("noindex", meta(r.text, "robots"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
