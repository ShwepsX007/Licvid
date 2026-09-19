"""Иконки и SEO-файлы: логотип должен быть виден поисковикам и мессенджерам.

Проверяем то, из-за чего значок сайта пропадает в выдаче:

* ``/favicon.ico`` отдаётся (раньше был 404 — робот, который идёт по корню,
  не находил картинку);
* у иконок в ``<link rel="icon">`` есть размеры, файлы существуют и они
  квадратные: Google требует 1:1 и советует брать не меньше 48 px;
* ``og:image`` — широкая картинка 1200×630 со своими размерами, а не
  квадратный логотип (мессенджеры такие превью обрезают);
* в манифесте размеры иконок совпадают с файлами;
* в robots.txt нет запрета на логотип и фавиконку: их читает Googlebot-Image.

Запуск:  python3 -m pytest tests/test_site_icons.py -q
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import seo_pages  # noqa: E402

STATIC = os.path.join(HERE, "static")
PAGES = ("admin.html", "cabinet.html", "digest.html", "index.html",
         "landing.html", "login.html", "reset.html")
#: Файлы, которые обязана видеть выдача: имя — ожидаемая сторона в пикселях.
SQUARE_ICONS = {
    "icon-48.png": 48,
    "icon-96.png": 96,
    "icon-144.png": 144,
    "icon-192.png": 192,
    "icon-512.png": 512,
    "apple-touch-icon.png": 180,
    "icon-maskable-512.png": 512,
}


def png_size(path: str) -> tuple:
    """Размеры PNG из заголовка IHDR — без картинок и сторонних библиотек."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path}: это не PNG")
    return struct.unpack(">II", head[16:24])


class IconsTest(unittest.TestCase):
    def test_icons_exist_and_are_square(self):
        for name, side in SQUARE_ICONS.items():
            path = os.path.join(STATIC, name)
            self.assertTrue(os.path.isfile(path), f"нет иконки {name}")
            w, h = png_size(path)
            self.assertEqual((w, h), (side, side), f"{name}: {w}×{h}, ждали {side}")

    def test_favicon_ico_has_three_sizes(self):
        """В .ico лежат 16, 32 и 48 — его читают браузер и робот с корня."""
        path = os.path.join(STATIC, "favicon.ico")
        self.assertTrue(os.path.isfile(path), "нет static/favicon.ico")
        with open(path, "rb") as fh:
            blob = fh.read()
        self.assertEqual(blob[:4], b"\x00\x00\x01\x00", "это не ICO")
        count = struct.unpack("<H", blob[4:6])[0]
        self.assertGreaterEqual(count, 3, f"в ico только {count} размер(а)")
        sizes = []
        for i in range(count):
            off = 6 + i * 16
            w = blob[off] or 256
            sizes.append(w)
        for side in (16, 32, 48):
            self.assertIn(side, sizes, f"в ico нет {side}px: {sizes}")

    def test_og_cover_is_wide(self):
        """Превью ссылки: 1200×630 — стандарт Open Graph."""
        path = os.path.join(STATIC, "og-cover.png")
        self.assertTrue(os.path.isfile(path), "нет static/og-cover.png")
        self.assertEqual(png_size(path), (1200, 630))
        self.assertLess(os.path.getsize(path), 400_000, "обложка слишком тяжёлая")


class PagesHeadTest(unittest.TestCase):
    def _head(self, name: str) -> str:
        with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_every_page_declares_real_icons(self):
        for name in PAGES:
            html = self._head(name)
            self.assertIn('rel="icon"', html, f"{name}: нет <link rel=icon>")
            self.assertIn("/static/favicon.ico", html, f"{name}: нет favicon.ico")
            self.assertIn('sizes="48x48"', html, f"{name}: нет иконки 48×48")
            self.assertIn('sizes="180x180"', html, f"{name}: нет apple-touch-icon")
            self.assertNotIn('rel="icon" type="image/png" href="/static/logo.png"',
                             html, f"{name}: осталась старая иконка-логотип")

    def test_og_image_points_to_cover_with_dimensions(self):
        for name in PAGES:
            html = self._head(name)
            if "og:image" not in html:
                continue                      # админка не для соцсетей
            self.assertIn("/static/og-cover.png", html, f"{name}: og:image не обложка")
            self.assertIn('property="og:image:width" content="1200"', html, name)
            self.assertIn('property="og:image:height" content="630"', html, name)

    def test_icon_links_have_files(self):
        """Каждая ссылка из разметки ведёт на существующий файл."""
        for name in PAGES:
            for href in re.findall(r'rel="(?:icon|apple-touch-icon)"[^>]*href="([^"]+)"',
                                   self._head(name)) or []:
                self.assertTrue(href.startswith("/static/"), href)
                path = os.path.join(STATIC, href[len("/static/"):])
                self.assertTrue(os.path.isfile(path), f"{name}: нет файла {href}")


class SeoFilesTest(unittest.TestCase):
    def test_manifest_icon_sizes_match_files(self):
        data = json.loads(seo_pages.manifest().body.decode("utf-8"))
        self.assertTrue(data["icons"])
        for icon in data["icons"]:
            self.assertTrue(icon["src"].startswith("/static/"), icon)
            path = os.path.join(STATIC, icon["src"][len("/static/"):])
            self.assertTrue(os.path.isfile(path), f"манифест: нет файла {icon['src']}")
            w, h = png_size(path)
            self.assertEqual(icon["sizes"], f"{w}x{h}",
                             f"{icon['src']}: в манифесте {icon['sizes']}, файл {w}×{h}")

    def test_manifest_has_maskable_icon(self):
        data = json.loads(seo_pages.manifest().body.decode("utf-8"))
        purposes = " ".join(str(i.get("purpose") or "") for i in data["icons"])
        self.assertIn("maskable", purposes, data["icons"])

    def test_robots_allows_logo_and_favicon(self):
        body = seo_pages.robots_txt().body.decode("utf-8")
        self.assertIn("Allow: /static/", body)
        self.assertIn("Allow: /favicon.ico", body)
        for line in body.splitlines():
            if line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                self.assertFalse(path.startswith("/static"), body)
                self.assertNotEqual(path, "/favicon.ico", body)

    def test_jsonld_organization_has_logo(self):
        for kind in ("landing", "digest", "terminal"):
            raw = seo_pages.jsonld(kind, "ru")
            payload = raw.split(">", 1)[1].rsplit("<", 1)[0]
            data = json.loads(payload)
            orgs = [d for d in data if d.get("@type") == "Organization"]
            self.assertEqual(len(orgs), 1, kind)
            logo = orgs[0]["logo"]
            self.assertTrue(logo["url"].endswith("/static/icon-512.png"), logo)
            self.assertGreaterEqual(logo["width"], 112, logo)     # минимум Google
            self.assertEqual(logo["width"], logo["height"], logo)


if __name__ == "__main__":
    unittest.main()
