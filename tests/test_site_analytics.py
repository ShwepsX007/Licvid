"""Google Analytics (gtag.js) на страницах сайта.

Счётчик вставляется в одном месте — в `seo_pages.render`, которым собираются все
страницы, — поэтому проверяем ровно то, из-за чего метрика молчит или врёт:

* snippet есть на каждой публичной странице: лендинг, терминал, дайджест,
  сводки по часам, вход, кабинет, сброс пароля, 404 (если тега нет на одной
  из них, воронка «лендинг → регистрация» в отчёте обрывается);
* стоит ровно один раз и внутри ``<head>``: дубль удваивает визиты, а тег,
  уехавший в ``<body>``, ловится не всеми браузерами одинаково;
* ID в ``gtag('config', …)`` совпадает с адресом загрузчика — с чужим ID
  Google пишет «данных нет», и ждёшь отчёт впустую;
* в админке счётчика нет: это служебная страница, её просмотры — шум в
  аудитории, а ``robots.txt`` её закрыт;
* ``LIQSCOPE_GA_ID=""`` выключает вставку — на тестовом стенде визиты
  в статистику не должны попадать вовсе.

Запуск:  python3 -m pytest tests/test_site_analytics.py -q
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import seo_pages  # noqa: E402

#: Публичные страницы: файл шаблона — путь, по которому он живёт.
PAGES = (
    ("landing.html", "/"),
    ("index.html", "/terminal"),
    ("digest.html", "/digest"),
    ("hourly.html", "/hourly"),
    ("cabinet.html", "/cabinet"),
    ("login.html", "/login"),
    ("reset.html", "/reset"),
    ("404.html", "/404"),
)
#: Адреса, которые отдаёт приложение: проверяем, что счётчик доезжает до
#: браузера, а не только до функции сборки страницы.
ROUTES = ("/", "/terminal", "/digest", "/hourly", "/login", "/cabinet")


def _head(html: str) -> str:
    return html.split("</head>", 1)[0]


class AnalyticsBlockTest(unittest.TestCase):
    """Сам блок: код Google, две части, один ID."""

    def test_block_is_the_google_snippet(self) -> None:
        block = seo_pages.analytics_block()
        self.assertIn("<!-- Google tag (gtag.js) -->", block)
        self.assertIn('<script async src="https://www.googletagmanager.com/gtag/js',
                      block)
        self.assertIn("window.dataLayer = window.dataLayer || [];", block)
        self.assertIn("function gtag(){dataLayer.push(arguments);}", block)
        self.assertIn("gtag('js', new Date());", block)
        self.assertIn(f"gtag('config', '{seo_pages.GA_ID}');", block)

    def test_id_matches_the_loader(self) -> None:
        """ID загрузчика и config — один: с чужим Google не пишет данных."""
        block = seo_pages.analytics_block()
        self.assertEqual(block.count(seo_pages.GA_ID), 2, block)
        self.assertIn(f"id={seo_pages.GA_ID}", block)

    def test_empty_id_disables_the_counter(self) -> None:
        """Пустой ID (переменная окружения) — в странице ни строчки тега."""
        saved = seo_pages.GA_ID
        try:
            seo_pages.GA_ID = ""
            self.assertEqual(seo_pages.analytics_block(), "")
            html = seo_pages.render("landing.html", "ru", "/").body.decode()
            self.assertNotIn("googletagmanager", html)
        finally:
            seo_pages.GA_ID = saved


class AnalyticsOnPagesTest(unittest.TestCase):
    """Счётчик на всех публичных страницах и только на них."""

    def test_every_public_page_has_the_counter(self) -> None:
        for filename, path in PAGES:
            for lang in seo_pages.LANGS:
                html = seo_pages.render(filename, lang, path).body.decode()
                head = _head(html)
                self.assertIn("googletagmanager.com/gtag/js", head, (filename, lang))
                self.assertIn(f"gtag('config', '{seo_pages.GA_ID}')", head,
                              (filename, lang))
                # ровно один раз, и ничего счётчикового — после </head>
                self.assertEqual(html.count("googletagmanager.com/gtag/js"), 1,
                                 (filename, lang))
                self.assertNotIn("googletagmanager",
                                 html.split("</head>", 1)[1], (filename, lang))

    def test_admin_panel_has_no_counter(self) -> None:
        """Админка — не аудитория: счётчик там превращает просмотры в визиты."""
        for lang in ("ru", "en"):
            html = seo_pages.render("admin.html", lang, "/admin",
                                    analytics=False).body.decode()
            self.assertNotIn("googletagmanager", html)
            self.assertNotIn("dataLayer", html)
            # язык при этом подставляется как обычно
            self.assertIn("window.LIQSCOPE_LANG", _head(html), lang)

    def test_counter_does_not_break_the_language_block(self) -> None:
        """Тег в head не вытесняет языковую разметку: она остаётся в head."""
        for lang in ("ru", "en"):
            html = seo_pages.render("landing.html", lang, "/").body.decode()
            head = _head(html)
            self.assertIn(f'<html lang="{seo_pages.HREFLANG[lang]}"', html)
            self.assertIn("hreflang", head)
            self.assertIn("window.LIQSCOPE_LANG", head)
            self.assertLess(html.index("googletagmanager"), html.index("</head>"))


class AnalyticsRoutesTest(unittest.TestCase):
    """Ответ приложения: счётчик доезжает до браузера на живых роутах."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from fastapi.testclient import TestClient
            import server
        except Exception as exc:  # noqa: BLE001 - окружение без зависимостей
            raise unittest.SkipTest(f"приложение не поднимается: {exc}")
        cls.client = TestClient(server.app)

    def test_pages_serve_the_counter(self) -> None:
        for route in ROUTES:
            r = self.client.get(route)
            self.assertEqual(r.status_code, 200, route)
            self.assertIn("googletagmanager.com/gtag/js", _head(r.text), route)
            self.assertIn(f"gtag('config', '{seo_pages.GA_ID}')", r.text, route)

    def test_admin_route_has_no_counter(self) -> None:
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("googletagmanager", r.text)


if __name__ == "__main__":
    unittest.main()
