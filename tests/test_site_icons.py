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
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import seo_pages  # noqa: E402

STATIC = os.path.join(HERE, "static")

#: Фон сайта (theme-color) — им залиты плитки, которым прозрачность нельзя.
BRAND_BG = (6, 10, 18)
#: Иконки, которые обязаны лежать на прозрачном фоне, иначе значок виден
#: во вкладке, в выдаче и на экране согласия Google чётким квадратом.
TRANSPARENT_ICONS = ("icon-48.png", "icon-96.png", "icon-144.png",
                     "icon-192.png", "icon-512.png", "oauth-logo-120.png")
#: Плитки для платформ, которые прозрачность не поддерживают (iOS, Android).
OPAQUE_TILES = ("apple-touch-icon.png", "icon-maskable-512.png")
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
        return png_size_bytes(fh.read(24), path)


def png_size_bytes(head: bytes, label: str = "png") -> tuple:
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{label}: это не PNG")
    return struct.unpack(">II", head[16:24])


def png_color_type(path: str) -> int:
    """Тип цвета PNG: 2 — RGB (без прозрачности), 6 — RGBA (с альфой)."""
    with open(path, "rb") as fh:
        return png_color_type_bytes(fh.read(26), path)


def png_color_type_bytes(head: bytes, label: str = "png") -> int:
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{label}: это не PNG")
    depth, color_type = head[24], head[25]
    if depth != 8:
        raise AssertionError(f"{label}: битность {depth}, ждали 8")
    return color_type


def ico_entry(blob: bytes, size: int):
    """Смещение и битность картинки нужного размера внутри .ico."""
    count = struct.unpack("<H", blob[4:6])[0]
    for i in range(count):
        off = 6 + i * 16
        side = blob[off] or 256
        if side != size:
            continue
        bpp = struct.unpack("<H", blob[off + 6:off + 8])[0]
        (img_off,) = struct.unpack("<I", blob[off + 12:off + 16])
        return img_off, bpp
    raise AssertionError(f"в ico нет размера {size}")


def ico_pixel(path: str, size: int, x: int, y: int) -> tuple:
    """Пиксель RGBA нужного размера внутри .ico.

    Pillow кладёт в ico PNG-записи (так делают и современные сборщики),
    поэтому читаем их своим декодером; на случай BMP-записи оставлен
    запасной путь: BGRA, строки снизу вверх.
    """
    with open(path, "rb") as fh:
        blob = fh.read()
    img_off, bpp = ico_entry(blob, size)
    if blob[img_off:img_off + 8] == b"\x89PNG\r\n\x1a\n":
        return png_pixels_bytes(blob[img_off:], {(x, y)}, f"{path}:{size}")[(x, y)]
    if bpp != 32:
        return (0, 0, 0, 255)           # 24 бита — альфы в файле нет
    (header_size,) = struct.unpack("<I", blob[img_off:img_off + 4])
    stride = size * 4
    row = size - 1 - y                  # строки снизу вверх
    i = img_off + header_size + row * stride + x * 4
    blue, green, red, alpha = blob[i:i + 4]
    return (red, green, blue, alpha)


def png_pixels(path: str, wanted: set) -> dict:
    """Значения RGBA в точках ``wanted={(x, y)}`` — без сторонних библиотек.

    Свой мини-декодер нужен, чтобы проверить именно прозрачность фона:
    тип цвета в заголовке говорит лишь о наличии альфа-канала, а квадрат
    может быть записан и как RGBA с непрозрачной заливкой вокруг значка.
    """
    with open(path, "rb") as fh:
        return png_pixels_bytes(fh.read(), wanted, path)


def png_pixels_bytes(blob: bytes, wanted: set, label: str = "png") -> dict:
    """Значения RGBA в указанных точках картинки."""
    rows = png_rgba(blob, label)
    out = {}
    for x, y in wanted:
        i = x * 4
        out[(x, y)] = tuple(rows[y][i:i + 4])
    return out


def png_rgba(blob: bytes, label: str = "png") -> list:
    """Все строки PNG как RGBA — общий декодер для проверок фона и границ."""
    width, height = png_size_bytes(blob, label)
    color_type = png_color_type_bytes(blob, label)
    if color_type == 6:
        channels = 4
    elif color_type == 2:
        channels = 3
    else:
        raise AssertionError(f"{label}: тип цвета {color_type} не поддерживается")
    idat = bytearray()
    pos = 8
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        tag = blob[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat += blob[pos + 8:pos + 8 + length]
        elif tag == b"IEND":
            break
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    bpp = channels
    stride = width * channels
    prev = bytearray(stride)
    out = []
    for y in range(height):
        off = y * (stride + 1)
        flt = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        if flt == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif flt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif flt == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif flt == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up = prev[i]
                upleft = prev[i - bpp] if i >= bpp else 0
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                line[i] = (line[i] + pred) & 0xFF
        elif flt != 0:
            raise AssertionError(f"{label}: фильтр {flt} не поддерживается")
        if channels == 4:
            out.append(bytes(line))
        else:
            rgba = bytearray(stride // 3 * 4)
            for x in range(width):
                rgba[x * 4:x * 4 + 3] = line[x * 3:x * 3 + 3]
                rgba[x * 4 + 3] = 255
            out.append(bytes(rgba))
        prev = line
    return out


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


class IconBackgroundTest(unittest.TestCase):
    """Значок не должен приезжать квадратом — фон обязан быть прозрачным.

    Регрессия из жизни: сборка делала ``convert("RGB")``, прозрачность
    схлопывалась в чёрный, и во вкладке браузера, в выдаче и на экране
    согласия Google вокруг логотипа стоял чёткий чёрный (у маскабла — белый)
    квадрат.
    """

    def test_icons_are_transparent_around_the_mark(self):
        for name in TRANSPARENT_ICONS:
            with self.subTest(icon=name):
                path = os.path.join(STATIC, name)
                self.assertTrue(os.path.isfile(path), f"нет иконки {name}")
                width, height = png_size(path)
                corners = {(0, 0), (width - 1, 0), (0, height - 1),
                           (width - 1, height - 1)}
                pixels = png_pixels(path, corners)
                for point, rgba in pixels.items():
                    self.assertEqual(
                        rgba[3], 0,
                        f"{name}: угол {point} не прозрачный ({rgba}) — "
                        "вокруг значка виден квадрат")

    def test_favicon_has_transparent_corners(self):
        """В .ico у каждого размера — прозрачные углы, а не чёрный квадрат."""
        path = os.path.join(STATIC, "favicon.ico")
        with open(path, "rb") as fh:
            count = struct.unpack("<H", fh.read(6)[4:6])[0]
        self.assertGreaterEqual(count, 3, f"в ico всего {count} размер(а)")
        for side in (16, 32, 48):
            with self.subTest(size=side):
                self.assertEqual(ico_pixel(path, side, 0, 0)[3], 0,
                                 f"левый верхний угол {side}px не прозрачный")
                self.assertEqual(ico_pixel(path, side, side - 1, side - 1)[3], 0,
                                 f"правый нижний угол {side}px не прозрачный")

    def test_platform_tiles_are_opaque_brand_color(self):
        """iOS и Android прозрачность не умеют: у них — плитка цвета сайта.

        Раньше это был белый квадрат (и чёрный у маскабла), чужой оформлению.
        """
        for name in OPAQUE_TILES:
            with self.subTest(icon=name):
                path = os.path.join(STATIC, name)
                width, height = png_size(path)
                pixels = png_pixels(path, {(0, 0), (width - 1, height - 1)})
                for point, rgba in pixels.items():
                    self.assertEqual(
                        rgba, BRAND_BG + (255,),
                        f"{name}: {point} = {rgba}, ждали плитку {BRAND_BG}")

    def test_maskable_icon_fits_android_safe_zone(self):
        """Значок маскабл-иконки целиком внутри круга 80% — кроп не срежет.

        Android вырезает из маскабл-иконки круг диаметром 80% стороны и всё,
        что вылезло за него (а равно и чужой фон), обрезается или видно
        полосами. Считаем именно пиксели значка: сравнивать по рамке нельзя,
        углы рамки у диагонального мегафона пустые.
        """
        path = os.path.join(STATIC, "icon-maskable-512.png")
        with open(path, "rb") as fh:
            rows = png_rgba(fh.read(), path)
        side = len(rows)
        center = (side - 1) / 2
        safe_r = 0.40 * side
        outside = 0
        for y in range(side):
            row = rows[y]
            for x in range(side):
                if ((x - center) ** 2 + (y - center) ** 2) ** 0.5 <= safe_r:
                    continue
                i = x * 4
                r, g, b, a = row[i:i + 4]
                if a and abs(r - BRAND_BG[0]) + abs(g - BRAND_BG[1]) + abs(b - BRAND_BG[2]) > 24:
                    outside += 1
        self.assertEqual(
            outside, 0,
            f"{outside} пикселей значка за безопасным кругом Android")

    def test_oauth_logo_is_120_and_transparent(self):
        """Логотип для Google Cloud Console: ровно 120×120 и без фона."""
        path = os.path.join(STATIC, "oauth-logo-120.png")
        self.assertTrue(os.path.isfile(path), "нет static/oauth-logo-120.png")
        self.assertEqual(png_size(path), (120, 120))
        self.assertEqual(png_color_type(path), 6, "у логотипа нет прозрачности")
        self.assertLess(os.path.getsize(path), 1_000_000, "Google просит до 1 МБ")

    def test_cover_has_no_white_square_behind_the_mark(self):
        """На обложке превью логотип лежит на тёмном фоне, без белой плашки."""
        path = os.path.join(STATIC, "og-cover.png")
        width, height = png_size(path)
        # Плашку раньше рисовали в левой части карточки (x≈86…406)
        pixels = png_pixels(path, {(90, 40), (110, height // 2), (400, height - 40)})
        for point, rgba in pixels.items():
            self.assertNotEqual(rgba[:3], (255, 255, 255),
                                f"og-cover: {point} белый — вернулась плашка")


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
