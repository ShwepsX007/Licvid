"""Иконки сайта из одного исходника ``static/logo.png``.

Поисковики и мессенджеры берут логотип из разных мест: Google и Яндекс —
фавиконку из ``<link rel="icon">`` и ``/favicon.ico``, мессенджеры — картинку
``og:image`` шириной 1200, iOS — ``apple-touch-icon``, Android — маскабл-иконку
из манифеста, а Google OAuth — логотип приложения из настроек OAuth consent
screen. Поэтому набор собирается из одного исходника:

    python3 tools/build_icons.py

Прозрачность. ``static/logo.png`` — RGBA с прозрачным фоном, и раньше скрипт
губил это на первом же шаге (``convert("RGB")``): прозрачность схлопывалась
в чёрный, и во вкладке браузера, в выдаче и на экране согласия Google
появлялся чёткий квадрат вокруг значка. Теперь альфа-канал сохраняется на
всех размерах: иконки лежат на прозрачном фоне, а непрозрачная подложка
остаётся только там, где её требует платформа (iOS и маскабл-иконка Android
не умеют прозрачность — им даём фирменный тёмный цвет сайта, не белый).

Что появляется в ``static/``:

* ``favicon.ico`` — 16/32/48 с альфой: его читают браузер и робот,
  который идёт по корню сайта;
* ``icon-48/96/144/192/512.png`` — прозрачные квадраты с явными размерами:
  Google выбирает подходящий и не масштабирует сам (мельче 48 px он не любит);
* ``apple-touch-icon.png`` (180) — иконка на домашний экран iOS: тёмная
  плитка фирменного цвета, потому что iOS прозрачность не поддерживает;
* ``icon-maskable-512.png`` — для манифеста: та же плитка с запасом по краям,
  чтобы круглый кроп Android не срезал мегафон;
* ``oauth-logo-120.png`` — прозрачный логотип 120×120 для поля «App logo»
  в Google Cloud Console (OAuth consent screen);
* ``og-cover.png`` (1200×630) — превью ссылки в мессенджерах и соцсетях.
"""
from __future__ import annotations

import os
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(HERE, "static")
LOGO = os.path.join(STATIC, "logo.png")

#: Квадраты для <link rel="icon">: Google советует брать размер с запасом,
#: минимум 48 px — все эти стороны кратны 48 или удобны для «домашнего экрана».
ICON_SIZES: List[int] = [48, 96, 144, 192, 512]
APPLE_SIZE = 180
MASKABLE_SIZE = 512
MASKABLE_SCALE = 0.72           # доля стороны: остальное — безопасный отступ
OAUTH_SIZE = 120                # ровно столько просит Google Cloud Console

#: Поля вокруг значка внутри квадрата — 3% стороны. Логотип не должен липнуть
#: к краям (иначе скругления браузера его подрежут), но и терять размер нельзя:
#: в 16 px каждая доля пикселя решает, поэтому поле минимальное, а мелким
#: размерам добавляем лёгкую резкость (см. SHARPEN_MAX).
PAD_RATIO = 0.03
#: До какого размера подтачивать контуры: уменьшение в 16 px размывает
#: линии мегафона, и значок во вкладке выглядит бледным.
SHARPEN_MAX = 96

#: Превью ссылки: у Open Graph стандарт 1200×630, мессенджеры обрезают иначе.
OG_SIZE: Tuple[int, int] = (1200, 630)
BG = (6, 10, 18)                # фон терминала — #060a12 (он же theme-color)
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


def normalise(logo: Image.Image) -> Image.Image:
    """Квадрат с центрированным содержимым и прозрачным полем.

    Исходник может быть любого размера и с неровными полями: обрезаем по
    содержимому, доводим до квадрата и добавляем поле ``PAD_RATIO``. Значок
    встаёт по центру, поэтому и в 16 px, и в 512 px он выглядит одинаково.
    """
    logo = logo.convert("RGBA")
    bbox = logo.getbbox()
    if bbox:
        logo = logo.crop(bbox)
    side = max(logo.size)
    canvas = max(1, round(side * (1 + 2 * PAD_RATIO)))
    out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    out.paste(logo, ((canvas - logo.width) // 2, (canvas - logo.height) // 2))
    return out


def _square(logo: Image.Image, size: int) -> Image.Image:
    """Квадрат нужного размера с сохранением прозрачности."""
    out = logo.resize((size, size), Image.LANCZOS)
    if size <= SHARPEN_MAX:
        out = out.filter(ImageFilter.UnsharpMask(radius=1.0, percent=90, threshold=2))
    return out


def _tile(logo: Image.Image, size: int, scale: float = 1.0,
          bg: Tuple[int, int, int] = BG) -> Image.Image:
    """Непрозрачная плитка фирменного цвета со значком внутри.

    Нужна iOS и маскабл-иконке Android: обе платформы заполняют прозрачные
    места сами (iOS — чёрным), поэтому фон задаём явно, и он совпадает с
    ``theme-color`` сайта, а не выпадает белым квадратом.
    """
    tile = Image.new("RGBA", (size, size), bg + (255,))
    inner = max(1, round(size * scale))
    mark = _square(logo, inner)
    tile.paste(mark, ((size - inner) // 2, (size - inner) // 2), mark)
    return tile


def build_favicon(logo: Image.Image) -> None:
    """``favicon.ico``: 16, 32 и 48 в одном файле, с альфа-каналом."""
    logo.save(os.path.join(STATIC, "favicon.ico"), format="ICO",
              sizes=[(16, 16), (32, 32), (48, 48)])


def build_pngs(logo: Image.Image) -> None:
    for size in ICON_SIZES:
        _square(logo, size).save(os.path.join(STATIC, f"icon-{size}.png"),
                                 format="PNG", optimize=True)
    _tile(logo, APPLE_SIZE).save(os.path.join(STATIC, "apple-touch-icon.png"),
                                 format="PNG", optimize=True)
    # Маскабл: значок меньше плитки, чтобы круглый кроп его не срезал.
    _tile(logo, MASKABLE_SIZE, MASKABLE_SCALE).save(
        os.path.join(STATIC, "icon-maskable-512.png"), format="PNG",
        optimize=True)


def build_oauth_logo(logo: Image.Image) -> None:
    """Логотип для OAuth consent screen: ровно 120×120, прозрачный.

    Google просит в поле «App logo» картинку 120×120 px с прозрачным фоном;
    если загрузить квадрат с заливкой (например, ``icon-512.png`` прежней
    сборки), на экране согласия видно чёткий квадрат внутри белой плашки.
    """
    _square(logo, OAUTH_SIZE).save(os.path.join(STATIC, "oauth-logo-120.png"),
                                   format="PNG", optimize=True)


def build_cover(logo: Image.Image) -> None:
    """Картинка для превью: тёмная плитка, логотип слева, подпись справа.

    Значок кладём с его альфой — без белой подложки: на тёмном фоне она
    читалась отдельным квадратом.
    """
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
    card.paste(tile, (x, y), tile)

    tx = x + side + 70
    draw.text((tx, y + 34), "LiqScope", font=_font(86, bold=True), fill=INK)
    draw.text((tx, y + 148), "Live liquidation terminal", font=_font(40), fill=ACCENT)
    draw.text((tx, y + 210), "10 exchanges · clusters, CVD, OI",
              font=_font(32), fill=MUTED)
    card.save(os.path.join(STATIC, "og-cover.png"), format="PNG", optimize=True)


def main() -> None:
    with Image.open(LOGO) as src:
        logo = normalise(src)
        build_favicon(logo)
        build_pngs(logo)
        build_oauth_logo(logo)
        build_cover(logo)
    names = ["favicon.ico", "apple-touch-icon.png", "icon-maskable-512.png",
             "oauth-logo-120.png", "og-cover.png"] + [f"icon-{s}.png" for s in ICON_SIZES]
    for name in names:
        path = os.path.join(STATIC, name)
        with Image.open(path) as im:
            has_alpha = "A" in im.convert("RGBA").getbands()
            transparent = (im.convert("RGBA").getchannel("A").getextrema()[0] == 0)
        mark = "прозрачный фон" if (has_alpha and transparent) else "непрозрачная плитка"
        print(f"  {name:<26} {os.path.getsize(path):>7} байт · {mark}")
    print("иконки собраны")


if __name__ == "__main__":
    main()
