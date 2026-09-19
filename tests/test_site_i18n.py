"""Словари сайта и многоязычная отдача страниц.

Что проверяем:

* ключи (``data-i18n``) есть во всех пяти языках и совпадают по составу —
  иначе на каком-то языке подпись останется ключом вида ``auth.forgot``;
* в переводах не осталось русского текста (то же правило, что у бота в
  ``tests/test_bot_i18n.py``): исключение — самоназвания языков в свитчере;
* ``static/i18n.pages.js`` собран из JSON и не устарел;
* каждый ключ, который написан в разметке (``data-i18n*``), существует в
  словаре или во встроенных сообщениях ``static/i18n.js``;
* сервер отдаёт robots.txt, sitemap.xml с hreflang и подставляет язык в
  ``<html lang>``/``<title>`` по ``?lang=``.

Запуск:  python3 -m pytest tests/test_site_i18n.py -q
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import seo_pages  # noqa: E402
from tools.build_i18n_pages import build as build_pages  # noqa: E402

LANGS = ("ru", "en", "zh", "hi", "es")
I18N_DIR = os.path.join(HERE, "static", "i18n")
PAGES_JS = os.path.join(HERE, "static", "i18n.pages.js")
STATIC = os.path.join(HERE, "static")

CYR = re.compile(r"[А-Яа-яЁё]")

#: Самоназвания в переключателе языка переводить нельзя — они и так на своём.
ENDONYMS = {"🇷🇺 Русский"}


def load(lang: str) -> dict:
    with open(os.path.join(I18N_DIR, f"{lang}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def builtin_keys() -> set:
    """Ключи, которые живут в static/i18n.js (терминал и лендинг)."""
    with open(os.path.join(STATIC, "i18n.js"), encoding="utf-8") as fh:
        src = fh.read()
    start = src.find("var RU = {")
    end = src.find("\n    };", start)
    block = src[start:end] if start >= 0 and end > start else ""
    block = re.sub(r"//.*", "", block)
    return set(re.findall(r'"([A-Za-z][A-Za-z0-9_.]*)"\s*:', block))


class DictionariesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dicts = {lang: load(lang) for lang in LANGS}

    def test_all_languages_present(self) -> None:
        for lang in LANGS:
            data = self.dicts[lang]
            self.assertIn("keys", data, lang)
            self.assertIn("phrases", data, lang)
            self.assertTrue(data["keys"], f"{lang}: пустой словарь ключей")
            if lang != "ru":
                # ru — язык-источник: переводить русский на русский нечего
                self.assertTrue(data["phrases"], f"{lang}: пустой словарь фраз")

    def test_keys_match_source(self) -> None:
        source = set(self.dicts["ru"]["keys"])
        for lang in LANGS:
            keys = set(self.dicts[lang]["keys"])
            self.assertEqual(
                source, keys,
                f"{lang}: ключи разошлись — нет {sorted(source - keys)[:5]}, "
                f"лишние {sorted(keys - source)[:5]}",
            )

    def test_no_empty_values(self) -> None:
        for lang in LANGS:
            for bucket in ("keys", "phrases"):
                for key, value in self.dicts[lang][bucket].items():
                    self.assertTrue(
                        str(value).strip(),
                        f"{lang}.{bucket}: пустой перевод для «{key[:60]}»",
                    )

    def test_translations_are_not_russian(self) -> None:
        """Латиница/中文/देवनागरी/Español — но не «Сохранить»."""
        for lang in LANGS:
            if lang == "ru":
                continue
            for bucket in ("keys", "phrases"):
                bad = [
                    key for key, value in self.dicts[lang][bucket].items()
                    if CYR.search(str(value)) and str(key) not in ENDONYMS
                    and str(value) not in ENDONYMS
                ]
                self.assertFalse(
                    bad,
                    f"{lang}.{bucket}: остался русский текст — {bad[:6]}",
                )

    def test_phrase_keys_are_matchable(self) -> None:
        """Ключ фразы — русский кусок разметки: движок пропускает текст без кириллицы."""
        for lang in ("en", "zh", "hi", "es"):
            dead = [key for key in self.dicts[lang]["phrases"] if not CYR.search(key)]
            self.assertFalse(dead, f"{lang}: ключи фраз без кириллицы — {dead[:6]}")


class PagesBundleTest(unittest.TestCase):
    def test_bundle_matches_json(self) -> None:
        """i18n.pages.js собирается из JSON — собранное должно совпадать."""
        with open(PAGES_JS, encoding="utf-8") as fh:
            on_disk = fh.read()
        self.assertEqual(
            on_disk, build_pages(),
            "static/i18n.pages.js устарел: запустите python3 tools/build_i18n_pages.py",
        )

    def test_bundle_carries_all_languages(self) -> None:
        with open(PAGES_JS, encoding="utf-8") as fh:
            text = fh.read()
        # rindex: то же имя встречается выше в комментарии-шапке файла
        cut = text.rindex("window.LIQSCOPE_I18N_PAGES")
        payload = text[text.index("=", cut) + 1:].strip().rstrip(";")
        data = json.loads(payload)
        self.assertEqual(sorted(data), sorted(LANGS))
        for lang in LANGS:
            self.assertIn("keys", data[lang])
            self.assertIn("phrases", data[lang])


class MarkupKeysTest(unittest.TestCase):
    """Ключи в разметке должны существовать — иначе на странице видно «auth.foo»."""

    FILES = ("landing.html", "index.html", "digest.html", "login.html",
             "reset.html", "cabinet.html", "admin.html")

    def test_markup_keys_exist(self) -> None:
        known = set(load("ru")["keys"]) | builtin_keys()
        self.assertGreater(len(builtin_keys()), 50, "не разобрали ключи static/i18n.js")
        used = set()
        for name in self.FILES:
            with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
                html = fh.read()
            used.update(re.findall(r'data-i18n(?:-placeholder|-title|-meta)?="([^"]+)"', html))
        self.assertTrue(used, "в разметке нет ни одного data-i18n")
        missing = sorted(used - known)
        self.assertFalse(missing, f"ключей нет в словарях: {missing}")


CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/127.0 Safari/537.36")


class Req:
    """Запрос для проверки выбора языка: ссылка, cookie, браузер, страна, UA."""

    def __init__(self, query="", cookie="", accept="", country="", ua=CHROME_UA):
        self.query_params = query and {"lang": query} or {}
        self.cookies = {"liqscope_lang": cookie} if cookie else {}
        self.headers = {"accept-language": accept, "user-agent": ua}
        if country:
            self.headers["cf-ipcountry"] = country


class SeoPagesTest(unittest.TestCase):
    def test_language_detection(self) -> None:
        self.assertEqual(seo_pages.detect_lang(Req(query="en")), "en")
        self.assertEqual(seo_pages.detect_lang(Req(query="zh-CN")), "zh")
        self.assertEqual(seo_pages.detect_lang(Req(cookie="es")), "es")
        self.assertEqual(seo_pages.detect_lang(Req(query="klingon")), "ru")
        # явный выбор сильнее всего: он и в ссылке, и в cookie, и в браузере
        self.assertEqual(seo_pages.detect_lang(Req(query="hi", cookie="es",
                                                   accept="de-DE,de;q=0.9")), "hi")

    def test_language_follows_the_browser(self) -> None:
        """Гость из Европы или Америки видит свой язык, а не русский."""
        self.assertEqual(seo_pages.detect_lang(Req(accept="en-US,en;q=0.9")), "en")
        self.assertEqual(seo_pages.detect_lang(Req(accept="de-DE,de;q=0.9,en-US;q=0.8")), "en")
        self.assertEqual(seo_pages.detect_lang(Req(accept="zh-CN,zh;q=0.9")), "zh")
        self.assertEqual(seo_pages.detect_lang(Req(accept="es-419,es;q=0.9")), "es")
        self.assertEqual(seo_pages.detect_lang(Req(accept="hi-IN,hi;q=0.9")), "hi")
        # русскоязычный браузер — русский, где бы гость ни был
        self.assertEqual(seo_pages.detect_lang(Req(accept="ru-RU,ru;q=0.9",
                                                   country="DE")), "ru")

    def test_language_falls_back_to_the_country(self) -> None:
        """Браузер молчит или просит язык, которого на сайте нет — решает страна."""
        self.assertEqual(seo_pages.detect_lang(Req(country="US")), "en")
        self.assertEqual(seo_pages.detect_lang(Req(country="DE")), "en")
        self.assertEqual(seo_pages.detect_lang(Req(accept="de", country="DE")), "en")
        self.assertEqual(seo_pages.detect_lang(Req(country="ES")), "es")
        self.assertEqual(seo_pages.detect_lang(Req(country="MX")), "es")
        self.assertEqual(seo_pages.detect_lang(Req(country="CN")), "zh")
        self.assertEqual(seo_pages.detect_lang(Req(country="IN")), "hi")
        self.assertEqual(seo_pages.detect_lang(Req(country="RU")), "ru")
        self.assertEqual(seo_pages.detect_lang(Req(country="KZ")), "ru")
        # страны нет вовсе — остаёмся на языке по умолчанию
        self.assertEqual(seo_pages.detect_lang(Req(accept="de")), "ru")

    def test_robots_get_the_default_language(self) -> None:
        """Поисковик и превью ссылки видят страницу как есть: адрес один."""
        bot = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
        self.assertEqual(seo_pages.detect_lang(Req(accept="en-US,en;q=0.9", ua=bot)), "ru")
        self.assertEqual(seo_pages.detect_lang(Req(country="US", ua=bot)), "ru")
        self.assertEqual(seo_pages.detect_lang(
            Req(country="US", ua="TelegramBot (like TwitterBot)")), "ru")
        self.assertEqual(seo_pages.detect_lang(Req(country="US", ua="")), "ru")
        # ... но явная ссылка с языком работает и для них
        self.assertEqual(seo_pages.detect_lang(Req(query="en", ua=bot)), "en")

    def test_auto_pick_is_marked_and_not_saved(self) -> None:
        """Автоматический выбор — подсказка: cookie за гостя не пишем."""
        lang, auto = seo_pages.lang_of(Req(accept="en-US,en;q=0.9"))
        self.assertEqual((lang, auto), ("en", True))
        lang, auto = seo_pages.lang_of(Req(query="en"))
        self.assertEqual((lang, auto), ("en", False))
        lang, auto = seo_pages.lang_of(Req(cookie="zh"))
        self.assertEqual((lang, auto), ("zh", False))

        auto_resp = seo_pages.render("landing.html", "en", "/", auto=True)
        auto_html = auto_resp.body.decode("utf-8")
        self.assertIn("window.LIQSCOPE_LANG_AUTO = 1", auto_html)
        # за гостя не решаем: cookie выбора не ставим
        self.assertNotIn("liqscope_lang", str(auto_resp.headers.get("set-cookie") or ""))
        chosen_resp = seo_pages.render("landing.html", "en", "/")
        chosen = chosen_resp.body.decode("utf-8")
        self.assertNotIn("LIQSCOPE_LANG_AUTO", chosen)
        self.assertIn("liqscope_lang=en", str(chosen_resp.headers.get("set-cookie") or ""))
        # кэшам сказано, что язык зависит и от cookie, и от заголовка браузера
        self.assertIn("Accept-Language", chosen_resp.headers.get("vary", ""))

    def test_header_and_country_helpers(self) -> None:
        self.assertEqual(seo_pages.lang_from_header("en-GB,en;q=0.9,de;q=0.5"), "en")
        self.assertEqual(seo_pages.lang_from_header("de-DE,de;q=0.9"), "")
        self.assertEqual(seo_pages.lang_from_header(""), "")
        self.assertEqual(seo_pages.lang_from_header("es;q=0.2,ru;q=0.9"), "ru")
        self.assertEqual(seo_pages.lang_from_header("zh-Hans-CN,zh;q=0.9"), "zh")
        self.assertEqual(seo_pages.lang_from_header("klingon"), "")
        self.assertEqual(seo_pages.lang_from_country("de"), "en")
        self.assertEqual(seo_pages.lang_from_country("BR"), "en")
        self.assertEqual(seo_pages.lang_from_country("ES"), "es")
        self.assertEqual(seo_pages.lang_from_country(""), "")

    def test_pages_render_in_requested_language(self) -> None:
        pages = {
            "landing.html": ("seo.land.title", "en"),
            "index.html": ("seo.terminal.title", "zh"),
            "digest.html": ("seo.digest.title", "hi"),
            "login.html": ("seo.login.title", "es"),
            "cabinet.html": ("seo.cabinet.title", "en"),
            "reset.html": ("seo.reset.title", "zh"),
            "admin.html": ("seo.admin.title", "hi"),
            "hourly.html": ("seo.hourly.title", "es"),
        }
        dictionaries = {lang: load(lang) for lang in LANGS}
        for name, (key, lang) in pages.items():
            with self.subTest(page=name, lang=lang):
                resp = seo_pages.render(name, lang, "/x")
                html = resp.body.decode("utf-8")
                self.assertIn(f'<html lang="{seo_pages.HREFLANG[lang]}"', html)
                self.assertIn(dictionaries[lang]["keys"][key], html)
                self.assertIn(f'window.LIQSCOPE_LANG = "{lang}"', html)
                for code in LANGS:
                    self.assertIn(f'hreflang="{seo_pages.HREFLANG[code]}"', html)
                self.assertIn('rel="canonical"', html)
                self.assertNotIn("<title>", html)  # заголовок подставлен, а не ключ

    def test_robots_and_sitemap(self) -> None:
        robots = seo_pages.robots_txt().body.decode("utf-8")
        self.assertIn("Sitemap: ", robots)
        self.assertIn("Disallow: /api/", robots)
        sitemap = seo_pages.sitemap_xml().body.decode("utf-8")
        self.assertIn("<urlset", sitemap)
        for code in LANGS:
            self.assertIn(f'hreflang="{seo_pages.HREFLANG[code]}"', sitemap)
        self.assertIn("x-default", sitemap)
        self.assertIn("https://liqscope.online/digest", sitemap)
        # раздел «Сводки по часам» — тоже контент, который стоит индексировать
        self.assertIn("https://liqscope.online/hourly", sitemap)
        # вход открыт для поиска: страница переведена и может отвечать на
        # запрос «LiqScope войти», а robots.txt её не закрывает
        self.assertIn("https://liqscope.online/login?lang=zh", sitemap)
        self.assertNotIn("Disallow: /login", robots)

    def test_sitemap_covers_every_page_in_every_language(self) -> None:
        sitemap = seo_pages.sitemap_xml().body.decode("utf-8")
        for path, _freq, _prio in seo_pages._PAGES:
            for code in LANGS:
                url = seo_pages._lang_url(seo_pages.SITE_URL + path, code)
                with self.subTest(path=path, lang=code):
                    self.assertIn(f"<loc>{url}</loc>", sitemap)

    def test_structured_data_is_translated(self) -> None:
        seen = {}
        for lang in LANGS:
            block = seo_pages.jsonld("landing", lang)
            data = json.loads(block.split(">", 1)[1].rsplit("</script>", 1)[0])
            self.assertEqual(data[0]["@type"], "WebSite")
            self.assertIn(seo_pages.HREFLANG[lang], data[0]["inLanguage"])
            seen[lang] = json.dumps(data, ensure_ascii=False)
        self.assertEqual(len(set(seen.values())), len(LANGS), "разметка не зависит от языка")

    def test_head_keeps_language_block_inside_head(self) -> None:
        html = seo_pages.render("landing.html", "hi", "/").body.decode("utf-8")
        head = html.split("</head>")[0]
        for code in LANGS:
            self.assertIn(f'hreflang="{seo_pages.HREFLANG[code]}"', head)
        self.assertIn('hreflang="x-default"', head)
        self.assertIn('window.LIQSCOPE_LANG = "hi"', head)
        self.assertNotIn("hreflang", html.split("</head>", 1)[1])


class ApiRoutesTest(unittest.TestCase):
    """Роуты SEO отдаёт само приложение (не только модуль)."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from fastapi.testclient import TestClient
            import server
        except Exception as exc:  # noqa: BLE001 - окружение без зависимостей
            raise unittest.SkipTest(f"приложение не поднимается: {exc}")
        cls.client = TestClient(server.app)

    def test_robots(self) -> None:
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Sitemap:", r.text)

    def test_sitemap(self) -> None:
        r = self.client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("hreflang", r.text)

    def test_manifest(self) -> None:
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["short_name"], "LiqScope")

    def test_landing_language(self) -> None:
        ru = self.client.get("/")
        en = self.client.get("/?lang=en")
        self.assertIn('<html lang="ru"', ru.text)
        self.assertIn('<html lang="en"', en.text)
        self.assertIn("https://liqscope.online/?lang=en", en.text)
        self.assertEqual(en.headers.get("content-language"), "en")
        self.assertIn("ru", ru.headers.get("content-language", "ru"))

    def test_visitor_language_on_the_live_routes(self) -> None:
        """Гость из Европы или Америки получает английский — на всех страницах."""
        # клиент один на весь класс и держит cookie прошлых тестов — начинаем с чистого
        self.client.cookies.clear()
        ua = {"user-agent": CHROME_UA}
        en_headers = dict(ua, **{"accept-language": "en-US,en;q=0.9"})
        for path in ("/", "/terminal", "/digest", "/hourly"):
            with self.subTest(path=path):
                r = self.client.get(path, headers=en_headers)
                self.assertEqual(r.status_code, 200)
                self.assertIn('<html lang="en"', r.text)
                # язык подобран за гостя — cookie выбора не ставим
                self.assertNotIn("liqscope_lang=", str(r.headers.get("set-cookie") or ""))
        # русскоязычный браузер остаётся русским — и тоже без cookie выбора
        r = self.client.get("/", headers=dict(ua, **{"accept-language": "ru-RU,ru;q=0.9"}))
        self.assertIn('<html lang="ru"', r.text)
        self.assertNotIn("liqscope_lang", str(r.headers.get("set-cookie") or ""))
        # браузер молчит — решает страна
        self.assertIn('<html lang="en"', self.client.get(
            "/", headers=dict(ua, **{"cf-ipcountry": "US"})).text)
        self.assertIn('<html lang="ru"', self.client.get(
            "/", headers=dict(ua, **{"cf-ipcountry": "RU"})).text)
        # поисковику — язык по умолчанию, в выдаче ничего не «прыгает»
        bot = self.client.get("/", headers={
            "user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "accept-language": "en-US,en;q=0.9",
            "cf-ipcountry": "US",
        })
        self.assertIn('<html lang="ru"', bot.text)
        self.assertNotIn("LIQSCOPE_LANG_AUTO", bot.text)   # робот — язык по умолчанию
        # прошлый выбор гостя сильнее всего
        self.assertIn('<html lang="es"', self.client.get(
            "/", headers=dict(en_headers, cookie="liqscope_lang=es")).text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
