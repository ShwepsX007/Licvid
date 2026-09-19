"""Многоязычная отдача страниц и SEO-файлы (robots.txt, sitemap.xml, manifest).

Зачем это нужно
---------------
Страницы сайта — статические HTML, а язык подставляется в браузере
(``static/i18n.js``). Поисковику этого мало: он должен видеть язык страницы
сразу в ответе — в ``<html lang>``, в ``<title>``, в описании и в ссылках
``hreflang``. Поэтому здесь сервер:

  * выбирает язык запроса (``?lang=`` → cookie выбора → ru);
  * подставляет заголовок и описание страницы на этом языке из словарей
    ``static/i18n/<lang>.json`` (те же ключи, что и в разметке);
  * отдаёт ``robots.txt`` и ``sitemap.xml`` с альтернативами для всех языков.

Значения ключей берём ровно из тех словарей, что читает браузер, — иначе
заголовок в поиске и заголовок в окне разъедутся.
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
from typing import Dict, Optional

from fastapi import Request
from fastapi.responses import Response

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
I18N_DIR = os.path.join(STATIC_DIR, "i18n")

#: Языки сайта — ровно те, что есть в словарях (ничего не выдумываем).
LANGS = ("ru", "en", "zh", "hi", "es")
DEFAULT_LANG = "ru"
COOKIE = "liqscope_lang"

#: Как язык называется в hreflang и в og:locale.
HREFLANG = {
    "ru": "ru",
    "en": "en",
    "zh": "zh-Hans",
    "hi": "hi",
    "es": "es",
}
OG_LOCALE = {
    "ru": "ru_RU",
    "en": "en_US",
    "zh": "zh_CN",
    "hi": "hi_IN",
    "es": "es_ES",
}
#: Публичный адрес: и в hreflang, и в sitemap нужны абсолютные ссылки.
SITE_URL = (os.getenv("LIQSCOPE_PUBLIC_URL") or "https://liqscope.online").rstrip("/")

_cache: Dict[str, tuple] = {}


def _catalogue(lang: str) -> dict:
    """Словарь ключей языка. Читаем файл и держим в памяти до правки на диске."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    path = os.path.join(I18N_DIR, f"{lang}.json")
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return {}
    hit = _cache.get(lang)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        keys = data.get("keys") or {}
        if not isinstance(keys, dict):
            keys = {}
    except (OSError, ValueError):
        keys = {}
    _cache[lang] = (stamp, keys)
    return keys


#: Кто на каком языке говорит: страна (по заголовку CDN) → язык сайта. Нужно
#: только тем, кто ещё не выбрал язык сам и чей браузер молчит.
LANG_COUNTRIES = {
    "ru": ("RU", "BY", "KZ", "KG", "UZ", "TJ", "TM", "AM", "AZ", "MD"),
    "zh": ("CN", "TW", "HK", "MO"),
    "hi": ("IN",),
    "es": ("ES", "MX", "AR", "CO", "CL", "PE", "VE", "UY", "PY", "BO", "EC",
           "GT", "CR", "PA", "DO", "CU", "HN", "NI", "SV", "PR"),
}
#: Всем остальным (Европа, США, Азия…) показываем английский: это общий язык
#: интернета, и человеку он полезнее русского, которого он, скорее всего, не знает.
LANG_ABROAD = "en"

#: Роботов по языку браузера не подстраиваем: у страницы один адрес и один
#: заголовок x-default — в выдаче она должна остаться той же, что и была.
_BOT_MARKS = ("bot", "crawler", "spider", "slurp", "bingpreview", "yandex",
              "baidu", "duckduck", "applebot", "python-requests", "curl/",
              "wget", "httpclient", "facebookexternalhit", "whatsapp",
              "telegrambot", "slackbot", "twitterbot", "discordbot",
              "googlebot", "semrush", "ahrefs", "mj12", "petal", "gptbot",
              "ccbot", "claudebot", "perplexity")


def is_bot(request: Request) -> bool:
    """Робот ли это (поисковик, превью ссылки, скрипт) — по User-Agent."""
    try:
        ua = str(request.headers.get("user-agent") or "").lower()
    except Exception:                                     # noqa: BLE001
        return False
    if not ua:
        return True                                       # пустой UA — не человек
    return any(mark in ua for mark in _BOT_MARKS)


def lang_from_header(value: str) -> str:
    """Язык браузера из Accept-Language: первый поддерживаемый по весам.

    ``de-DE,de;q=0.9,en-US;q=0.8`` → ``en``. Региональные варианты сводим к
    базовому языку (zh-CN → zh, es-419 → es) — так же, как это делает клиент.
    """
    raw = str(value or "")
    if not raw:
        return ""
    items = []
    for order, part in enumerate(raw.split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, q = piece.partition(";")
        base = tag.strip().lower().split("-")[0]
        if base not in LANGS:
            continue
        try:
            weight = float(q.split("=")[1]) if "=" in q else 1.0
        except (TypeError, ValueError):
            weight = 1.0
        if weight <= 0:
            continue
        items.append((-weight, order, base))
    if not items:
        return ""
    return sorted(items)[0][2]


def lang_from_country(code: str) -> str:
    """Язык сайта по стране гостя: своя страна — свой язык, чужая — английский."""
    cc = str(code or "").strip().upper()
    if not cc:
        return ""
    for lang, countries in LANG_COUNTRIES.items():
        if cc in countries:
            return lang
    return LANG_ABROAD


def country_of(request: Request) -> str:
    """Страна гостя из заголовков CDN (Cloudflare и подобные) — без сети."""
    try:
        from geoip import header_country
        return header_country(getattr(request, "headers", {}) or {})
    except Exception:                                     # noqa: BLE001
        return ""


def lang_of(request: Request):
    """Язык страницы и признак «выбран автоматически».

    Порядок: ``?lang=`` → cookie выбора → язык браузера → страна → русский.
    Явный выбор (?lang=, cookie) сильнее всего; браузер сильнее страны
    (русскоязычный в Германии должен видеть русский); роботам страницу
    отдаём как есть — на языке по умолчанию, чтобы выдача не «прыгала».
    """
    try:
        query = str(request.query_params.get("lang") or "").strip().lower()
    except Exception:  # noqa: BLE001 - у тестового запроса может не быть query_params
        query = ""
    if query:
        base = query.split("-")[0]
        if base in LANGS:
            return base, False
    try:
        cookie = str(request.cookies.get(COOKIE) or "").strip().lower()
    except Exception:  # noqa: BLE001
        cookie = ""
    if cookie:
        base = cookie.split("-")[0]
        if base in LANGS:
            return base, False
    if is_bot(request):
        return DEFAULT_LANG, False
    try:
        header = str(request.headers.get("accept-language") or "")
    except Exception:  # noqa: BLE001
        header = ""
    picked = lang_from_header(header)
    if picked:
        return picked, True
    by_country = lang_from_country(country_of(request))
    if by_country:
        return by_country, True
    return DEFAULT_LANG, True


def detect_lang(request: Request) -> str:
    """Язык страницы (без признака выбора) — для тех, кому нужен только язык."""
    return lang_of(request)[0]


def _text(lang: str, key: str, fallback: str = "") -> str:
    return str(_catalogue(lang).get(key) or fallback)


def _attr(value: str) -> str:
    """Значение для атрибута: кавычки и угловые скобки не должны сломать разметку."""
    return html_mod.escape(str(value), quote=True)


#: <title data-i18n="ключ">текст</title>
_TITLE_RE = re.compile(r"(<title\b[^>]*\bdata-i18n=\"([^\"]+)\"[^>]*>)(.*?)(</title>)", re.S | re.I)
#: <meta ... data-i18n-meta="ключ" ...>
_META_RE = re.compile(r"<meta\b[^>]*\bdata-i18n-meta=\"([^\"]+)\"[^>]*>", re.I)
_CONTENT_RE = re.compile(r"\bcontent=\"([^\"]*)\"", re.I)
_OG_LOCALE_RE = re.compile(r'(<meta\b[^>]*\bproperty="og:locale"[^>]*\bcontent=")([^"]*)(")', re.I)
_HTML_RE = re.compile(r"<html\b[^>]*>", re.I)
#: og:image / twitter:image — их подменяем, если у страницы своя картинка
_OG_IMG_RE = re.compile(
    r'(<meta\b[^>]*\b(?:property="og:image"|name="twitter:image")[^>]*\bcontent=")([^"]*)(")',
    re.I)
_OG_DIM_RE = re.compile(
    r'\s*<meta\b[^>]*\bproperty="og:image:(?:width|height)"[^>]*>', re.I)
_CANON_RE = re.compile(r"(<link\b[^>]*\brel=\"canonical\"[^>]*\bhref=\")([^\"]*)(\")", re.I)


def _language_block(path: str) -> str:
    """Ссылки hreflang: поисковик видит все языковые версии одной страницы."""
    rows = []
    for code in LANGS:
        rows.append(
            f'<link rel="alternate" hreflang="{HREFLANG[code]}"'
            f' href="{_attr(_lang_url(SITE_URL + path, code))}">'
        )
    rows.append(
        f'<link rel="alternate" hreflang="x-default" href="{_attr(SITE_URL + path)}">'
    )
    return "\n".join(rows)


def _lang_url(url: str, lang: str) -> str:
    """Тот же адрес, но с ?lang=xx (для ru параметр не нужен — это язык по умолчанию)."""
    if lang == DEFAULT_LANG:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}lang={lang}"


def _canonical(url: str, lang: str) -> str:
    return _lang_url(url, lang)


def render(
    filename: str,
    lang: str,
    path: str,
    *,
    indexable: bool = True,
    extra_head: str = "",
    og_image: str = "",
    auto: bool = False,
) -> Response:
    """Отдаёт страницу с уже подставленным языком в head.

    ``path`` — путь без языка (``/``, ``/digest``, ``/cabinet``…). Он нужен для
    canonical и hreflang: у каждой языковой версии свой адрес.

    ``auto`` — язык подобран автоматически (браузер или страна). В этом случае
    клиенту отдаём подсказку, что язык можно подобрать заново по браузеру, а в
    cookie выбор не пишем: гость ещё ничего не выбирал, и запоминать за него
    нечего.
    """
    lang = lang if lang in LANGS else DEFAULT_LANG
    page = os.path.join(STATIC_DIR, filename)
    with open(page, encoding="utf-8") as fh:
        html = fh.read()

    keys = _catalogue(lang)

    def title_sub(m: "re.Match[str]") -> str:
        key = m.group(2)
        value = keys.get(key)
        if not value:
            return m.group(0)
        return f"{m.group(1)}{html_mod.escape(str(value))}{m.group(4)}"

    html = _TITLE_RE.sub(title_sub, html, count=1)

    def meta_sub(m: "re.Match[str]") -> str:
        tag = m.group(0)
        value = keys.get(m.group(1))
        if not value:
            return tag
        if _CONTENT_RE.search(tag):
            return _CONTENT_RE.sub(lambda c: f'content="{_attr(value)}"', tag, count=1)
        return tag[:-1] + f' content="{_attr(value)}">'

    html = _META_RE.sub(meta_sub, html)

    if og_image:
        # у страницы есть своя картинка (обложка выпуска дайджеста): подставляем
        # её в og:image и twitter:image. Размеры снимаем — у фото дня он свой,
        # а не 1200×630 общей обложки: неверные цифры ломают превью.
        html = _OG_IMG_RE.sub(
            lambda m: m.group(1) + _attr(og_image) + m.group(3), html)
        html = _OG_DIM_RE.sub("", html)

    # <html lang="…"> и выбранный язык для клиента: разметка сразу совпадает
    html = _HTML_RE.sub(
        lambda m: f'<html lang="{HREFLANG[lang]}" data-lang="{lang}">', html, count=1
    )

    url = f"{SITE_URL}{path}"
    html = _CANON_RE.sub(
        lambda m: m.group(1) + _attr(_canonical(url, lang)) + m.group(3), html, count=1
    )

    html = _OG_LOCALE_RE.sub(
        lambda m: m.group(1) + OG_LOCALE[lang] + m.group(3), html, count=1
    )

    # hreflang и подсказка о языке — в конец <head>
    block = _language_block(path)
    block += f'\n<script>window.LIQSCOPE_LANG = "{lang}";</script>'
    if auto:
        # Язык подобран за гостя (браузер или страна): клиент может уточнить его
        # сам — например, когда браузер прислал язык, которого на сайте нет
        block += '\n<script>window.LIQSCOPE_LANG_AUTO = 1;</script>' 
    if extra_head:
        block += "\n" + extra_head
    # вставляем в самый конец <head>: язык успевает выставиться к моменту
    # DOMContentLoaded, когда i18n.js применяет словари
    html = html.replace("</head>", block + "\n</head>", 1)

    resp = Response(content=html, media_type="text/html; charset=utf-8")
    # язык зависит и от cookie, и от Accept-Language — говорим об этом кэшам
    resp.headers["Vary"] = "Cookie, Accept-Language"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Content-Language"] = HREFLANG[lang]
    if lang != DEFAULT_LANG and not auto:
        resp.set_cookie(
            COOKIE, lang, max_age=31536000, samesite="lax", path="/"
        )
    return resp


def robots_txt() -> Response:
    """robots.txt: закрываем служебное, зовём в sitemap."""
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "# Логотип и фавиконка должны быть доступны роботам: иначе в выдаче",
            "# вместо значка сайта будет пустое место",
            "Allow: /static/",
            "Allow: /favicon.ico",
            "Disallow: /api/",
            "Disallow: /admin",
            "Disallow: /cabinet",
            "Disallow: /reset",
            "Disallow: /verify",
            "Disallow: /attach",
            "Disallow: /email-login",
            "# Живая лента и дайджест открыты всем: их и индексируем",
            f"Sitemap: {SITE_URL}/sitemap.xml",
            "",
        ]
    )
    return Response(content=body, media_type="text/plain; charset=utf-8")


#: Возможности терминала для разметки SoftwareApplication — на каждом языке.
FEATURES = {
    "ru": [
        "Живая лента ликвидаций с 10 бирж",
        "Свечной график с кластерами и профилем объёмов",
        "CVD и открытый интерес",
        "Алерты в Telegram",
    ],
    "en": [
        "Live liquidation feed from 10 exchanges",
        "Candlestick chart with clusters and volume profile",
        "CVD and open interest",
        "Telegram alerts",
    ],
    "zh": [
        "来自 10 家交易所的实时清算流",
        "带聚类与成交量分布的 K 线图",
        "CVD 与未平仓合约量",
        "Telegram 提醒",
    ],
    "hi": [
        "10 एक्सचेंजों से लाइव लिक्विडेशन फ़ीड",
        "क्लस्टर और वॉल्यूम प्रोफ़ाइल वाला कैंडल चार्ट",
        "CVD और ओपन इंटरेस्ट",
        "Telegram अलर्ट",
    ],
    "es": [
        "Flujo de liquidaciones en vivo de 10 exchanges",
        "Gráfico de velas con clústeres y perfil de volumen",
        "CVD e interés abierto",
        "Alertas en Telegram",
    ],
}


#: Что кладём в sitemap: адрес и как часто меняется.
_PAGES = (
    ("/", "hourly", "1.0"),
    ("/hourly", "hourly", "0.9"),
    ("/digest", "daily", "0.9"),
    ("/terminal", "hourly", "0.8"),
    # страница входа открыта и переведена — по ней ищут «LiqScope войти»
    ("/login", "monthly", "0.5"),
)


def sitemap_xml() -> Response:
    """sitemap.xml: лендинг, сводки по часам, дайджест, терминал и вход — на всех языках."""
    last = time.strftime("%Y-%m-%d", time.gmtime())
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for path, freq, priority in _PAGES:
        for code in LANGS:
            parts.append("  <url>")
            parts.append(f"    <loc>{html_mod.escape(_lang_url(SITE_URL + path, code))}</loc>")
            parts.append(f"    <lastmod>{last}</lastmod>")
            parts.append(f"    <changefreq>{freq}</changefreq>")
            parts.append(f"    <priority>{priority}</priority>")
            for alt in LANGS:
                parts.append(
                    f'    <xhtml:link rel="alternate" hreflang="{HREFLANG[alt]}"'
                    f' href="{html_mod.escape(_lang_url(SITE_URL + path, alt))}"/>'
                )
            parts.append(
                f'    <xhtml:link rel="alternate" hreflang="x-default"'
                f' href="{html_mod.escape(SITE_URL + path)}"/>'
            )
            parts.append("  </url>")
    parts.append("</urlset>")
    parts.append("")
    return Response(content="\n".join(parts), media_type="application/xml; charset=utf-8")


def manifest(public_url: Optional[str] = None) -> Response:
    """manifest.webmanifest — PWA-мелочи: имя, иконка, цвета."""
    data = {
        "name": "LiqScope — ликвидации крипто-фьючерсов",
        "short_name": "LiqScope",
        "description": _text(DEFAULT_LANG, "seo.land.desc"),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#070a10",
        "theme_color": "#060a12",
        "lang": "ru",
        # Иконки — реальные файлы из tools/build_icons.py: раньше здесь были
        # размеры 512/192 при картинке 256×256, и установка приложения ломалась.
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/manifest+json; charset=utf-8",
    )


#: JSON-LD: сайт, приложение и организация — одна структура на все страницы.
def jsonld(kind: str = "landing", lang: str = DEFAULT_LANG,
           image: str = "") -> str:
    """Разметка Schema.org. ``image`` — картинка страницы (обложка выпуска)."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    common = {
        "@context": "https://schema.org",
        "name": "LiqScope",
        "url": SITE_URL,
        "logo": f"{SITE_URL}/static/logo.png",
        "description": _text(lang, "seo.land.desc"),
    }
    if kind == "landing":
        data = [
            {
                "@type": "WebSite",
                **common,
                "inLanguage": list(HREFLANG.values()),
            },
            {
                "@type": "SoftwareApplication",
                "name": "LiqScope Terminal",
                "applicationCategory": "FinanceApplication",
                "operatingSystem": "Web",
                "url": f"{SITE_URL}/terminal",
                "inLanguage": list(HREFLANG.values()),
                "description": _text(lang, "seo.terminal.desc"),
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                "featureList": FEATURES.get(lang, FEATURES[DEFAULT_LANG]),
            },
        ]
    elif kind == "hourly":
        data = [
            {
                "@type": "CollectionPage",
                **common,
                "url": f"{SITE_URL}/hourly",
                "inLanguage": list(HREFLANG.values()),
                "description": _text(lang, "seo.hourly.desc"),
                "isPartOf": {"@type": "Blog", "name": "LiqScope",
                             "url": f"{SITE_URL}/hourly"},
            }
        ]
    elif kind == "digest":
        data = [
            {
                "@type": "Blog",
                **common,
                "url": f"{SITE_URL}/digest",
                "inLanguage": list(HREFLANG.values()),
                "description": _text(lang, "seo.digest.desc"),
            }
        ]
    else:
        data = [{"@type": "WebPage", **common, "url": f"{SITE_URL}/terminal"}]
    if image:
        # картинка страницы: поисковик и мессенджер берут её для превью, а для
        # выпуска дайджеста это то самое фото дня, что ушло в канал
        for item in data:
            item.setdefault("image", image)
    # Organization с логотипом-картинкой: поисковик берёт отсюда логотип для
    # брендовой панели, поэтому у картинки указаны реальные размеры.
    data.append({
        "@type": "Organization",
        "name": "LiqScope",
        "url": SITE_URL,
        "logo": {
            "@type": "ImageObject",
            "url": f"{SITE_URL}/static/icon-512.png",
            "width": 512,
            "height": 512,
        },
    })
    return (
        '<script type="application/ld+json">'
        + json.dumps(data, ensure_ascii=False)
        + "</script>"
    )
