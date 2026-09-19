"""Иконки сайта из одного исходника ``static/logo.png``.

Поисковики и мессенджеры берут логотип из разных мест: Google и Яндекс —
фавиконку из ``<link rel="icon">`` и ``/favicon.ico``, мессенджеры — картинку
``og:image`` шириной 1200, iOS — ``apple-touch-icon``. Раньше везде стоял один
и тот же ``logo.png`` 256×256: фавиконки в выдаче не было (по правилам Google
сторона должна быть квадратной и с запасом по разрешению, а ``/favicon.ico``
вообще отдавал 404), а картинка для превью в соцсетях была мелкой.

Скрипт собирает полный набор из исходника — картинку руками не готовим, при
смене логотипа достаточно перезапустить:

    python3 tools/build_icons.py

Что появляется в ``static/``:

* ``favicon.ico`` — 16/32/48 в одном файле: его читают браузер и робот,
  который идёт по корню сайта;
* ``icon-48/96/144/192/512.png`` — квадраты с явными размерами: Google
  выбирает подходящий и не масштабирует сам (мельче 48 px он не любит);
* ``apple-touch-icon.png`` (180) — иконка на домашний экран iOS;
* ``icon-maskable-512.png`` — для манифеста: логотип с запасом по краям,
  чтобы круглый кроп Android не срезал мегафон;
* ``og-cover.png`` (1200×630) — превью ссылки в мессенджерах и соцсетях.
"""
from __future__ import annotations

import os
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(HERE, "static")
LOGO = os.path.join(STATIC, "logo.png")

#: Квадраты для <link rel="icon">: Google советует брать размер с запасом,
#: минимум 48 px — все эти стороны кратны 48 или удобны для «домашнего экрана».
ICON_SIZES: List[int] = [48, 96, 144, 192, 512]
APPLE_SIZE = 180
MASKABLE_SIZE = 512
MASKABLE_SCALE = 0.78           # доля стороны: остальное — безопасный отступ

#: Превью ссылки: у Open Graph стандарт 1200×630, мессенджеры обрезают иначе.
OG_SIZE: Tuple[int, int] = (1200, 630)
BG = (6, 10, 18)                # фон терминала — #060a12
INK = (232, 238, 247)
MUTED = (138, 152, 172)
ACCENT = (77, 163, 255)

FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font(size: int, bold: bool = False):
    for path in (FONT_PATHS if bold else reversed(FONT_PATHS)):
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _square(logo: Image.Image, size: int) -> Image.Image:
    """Квадрат нужного размера: логотип уже 1:1, остаётся аккуратно уменьшить."""
    return logo.resize((size, size), Image.LANCZOS)


def build_favicon(logo: Image.Image) -> None:
    """``favicon.ico``: 16, 32 и 48 в одном файле."""
    logo.save(os.path.join(STATIC, "favicon.ico"), format="ICO",
              sizes=[(16, 16), (32, 32), (48, 48)])


def build_pngs(logo: Image.Image) -> None:
    for size in ICON_SIZES:
        _square(logo, size).save(os.path.join(STATIC, f"icon-{size}.png"),
                                 format="PNG", optimize=True)
    _square(logo, APPLE_SIZE).save(os.path.join(STATIC, "apple-touch-icon.png"),
                                   format="PNG", optimize=True)
    # Маскабл: логотип меньше плитки, чтобы круглый кроп его не срезал.
    tile = Image.new("RGB", (MASKABLE_SIZE, MASKABLE_SIZE), (255, 255, 255))
    inner = int(MASKABLE_SIZE * MASKABLE_SCALE)
    tile.paste(_square(logo, inner), ((MASKABLE_SIZE - inner) // 2,) * 2)
    tile.save(os.path.join(STATIC, "icon-maskable-512.png"), format="PNG",
              optimize=True)


def build_cover(logo: Image.Image) -> None:
    """Картинка для превью: тёмная плитка, логотип слева, подпись справа."""
    w, h = OG_SIZE
    card = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(card)
    # Мягкий градиент сверху вниз: ровный фон на превью выглядит мёртвым.
    for y in range(h):
        k = 1 - y / h
        draw.line([(0, y), (w, y)],
                  fill=(int(BG[0] + 14 * k), int(BG[1] + 22 * k), int(BG[2] + 40 * k)))
    draw.rectangle([0, 0, w, 6], fill=ACCENT)

    side = 300
    tile = _square(logo, side)
    x, y = 96, (h - side) // 2
    draw.rounded_rectangle([x - 10, y - 10, x + side + 10, y + side + 10],
                           radius=34, fill=(255, 255, 255))
    card.paste(tile, (x, y))

    tx = x + side + 70
    draw.text((tx, y + 34), "LiqScope", font=_font(86, bold=True), fill=INK)
    draw.text((tx, y + 148), "Live liquidation terminal", font=_font(40), fill=ACCENT)
    draw.text((tx, y + 210), "10 exchanges · clusters, CVD, OI",
              font=_font(32), fill=MUTED)
    card.save(os.path.join(STATIC, "og-cover.png"), format="PNG", optimize=True)


def main() -> None:
    with Image.open(LOGO) as src:
        logo = src.convert("RGB")
        if logo.width != logo.height:
            side = min(logo.width, logo.height)
            left = (logo.width - side) // 2
            top = (logo.height - side) // 2
            logo = logo.crop((left, top, left + side, top + side))
        build_favicon(logo)
        build_pngs(logo)
        build_cover(logo)
    names = ["favicon.ico", "apple-touch-icon.png", "icon-maskable-512.png",
             "og-cover.png"] + [f"icon-{s}.png" for s in ICON_SIZES]
    for name in names:
        path = os.path.join(STATIC, name)
        print(f"  {name:<26} {os.path.getsize(path):>7} байт")
    print("иконки собраны")


if __name__ == "__main__":
    main()
