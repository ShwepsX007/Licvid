"""Версии статики в HTML: хеш содержимого вместо ручного ``?v=``.

Зачем тест. ``/static/...?v=`` отдаётся браузеру как ``immutable`` на год
(nginx и ``CachedStaticFiles``). Версию правили руками, и забытый бамп
оставлял вернувшегося гостя со старым JS: новая разметка уже содержала
кнопку «Войти через Google», а закэшированный ``account.js`` её не оживлял,
да ещё и словарь без ключей показывал сырое ``auth.google_btn``.

Теперь ``seo_pages.render`` проставляет версию сам — от содержимого файла.
Проверяем: хеш совпадает с содержимым, меняется вместе с ним, чужие
параметры запроса не теряются, несуществующие файлы и страницы (``.html``)
не трогаются, а все страницы сайта получают версии.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import seo_pages  # noqa: E402

#: Ссылки на статику внутри src="…" / href="…".
REF_RE = re.compile(r'(?:src|href)="(/static/[^"]+)"')


def content_hash(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.blake2b(fh.read(), digest_size=4).hexdigest()


class AssetVersionTest(unittest.TestCase):
    def test_version_is_content_hash(self):
        ver = seo_pages.asset_version("account.js")
        self.assertRegex(ver, r"^[0-9a-f]{8}$")
        self.assertEqual(
            ver,
            content_hash(os.path.join(seo_pages.STATIC_DIR, "account.js")),
        )

    def test_version_changes_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            static = os.path.join(tmp, "static")
            os.makedirs(static)
            path = os.path.join(static, "probe.js")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("var a = 1;\n")
            with mock.patch.object(seo_pages, "STATIC_DIR", static):
                first = seo_pages.asset_version("probe.js")
                # другая длина файла
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("var a = 2; // правка\n")
                second = seo_pages.asset_version("probe.js")
                # та же длина, другой текст и новая метка времени: кэш по
                # (mtime, размеру) обязан перечитать файл
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("var a = 3;\n")
                stamp = time.time() + 5
                os.utime(path, (stamp, stamp))
                third = seo_pages.asset_version("probe.js")
            self.assertNotEqual(first, second, "правка файла должна менять версию")
            self.assertNotEqual(second, third, "новая mtime должна менять версию")

    def test_missing_file_has_no_version(self):
        self.assertEqual(seo_pages.asset_version("nope-not-here.js"), "")

    def test_urls_are_versioned_by_content(self):
        html = (
            '<link rel="stylesheet" href="/static/account.css?v=rebrand1">'
            '<script src="/static/account.js?v=nav5"></script>'
        )
        out = seo_pages.version_static_urls(html)
        self.assertNotIn("rebrand1", out)
        self.assertNotIn("nav5", out)
        self.assertIn(
            "/static/account.css?v=" + seo_pages.asset_version("account.css"), out)
        self.assertIn(
            "/static/account.js?v=" + seo_pages.asset_version("account.js"), out)

    def test_extra_query_params_survive(self):
        out = seo_pages.version_static_urls(
            '<script src="/static/account.js?v=nav5&lang=ru"></script>')
        self.assertIn("lang=ru", out)
        self.assertNotIn("v=nav5", out)

    def test_unknown_files_and_pages_are_untouched(self):
        html = (
            '<script src="/static/nope-not-here.js?v=old"></script>'
            '<a href="/static/screener.html?v=old">терминал</a>'
        )
        self.assertEqual(seo_pages.version_static_urls(html), html)

    def test_rendered_pages_carry_current_versions(self):
        pages = (
            ("login.html", "/login"),
            ("cabinet.html", "/cabinet"),
            ("index.html", "/terminal"),
            ("landing.html", "/"),
            ("admin.html", "/admin"),
        )
        for page, path in pages:
            with self.subTest(page=page):
                resp = seo_pages.render(page, "ru", path)
                html = resp.body.decode("utf-8")
                refs = REF_RE.findall(html)
                self.assertTrue(refs, f"{page}: нет ссылок на статику")
                for ref in refs:
                    url, _, query = ref.partition("?")
                    name = url[len("/static/"):]
                    ver = dict(
                        part.split("=", 1) for part in query.split("&") if "=" in part
                    ).get("v", "")
                    self.assertEqual(
                        ver, seo_pages.asset_version(name),
                        f"{page}: {url} без актуальной версии",
                    )
                # i18n.pages.js разбит по языкам и тоже версионируется
                self.assertIn("/static/i18n.pages.ru.js?v=", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
