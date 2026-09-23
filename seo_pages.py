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
#: Язык сайта по умолчанию: его получают роботы, превью ссылок и гости, о языке
#: которых ничего не известно. Английский — потому что это общий язык интернета:
#: из него человек поймёт больше, чем из русского, а русский браузер всё равно
#: сильнее (см. ``lang_of``). Он же — версия для ``x-default`` и голый адрес.
DEFAULT_LANG = "en"
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

#: Google Analytics (gtag.js) — ID property из настроек счётчика. Переопределяется
#: переменной окружения ``LIQSCOPE_GA_ID``; пустое значение выключает счётчик
#: вовсе (например, на тестовом стенде, где визиты в статистику не нужны).
GA_ID = os.getenv("LIQSCOPE_GA_ID", "G-S88MTSY09G").strip()

# 💬 Чат на всех страницах: виджет как в терминале (общий, личные, сервисы, поддержка).
# Инжектируется в render(), если страница его ещё не содержит. Версия файла — v8 per-user.
TCHAT_CSS = "/static/terminal_chat.css?v=8"
TCHAT_JS = "/static/terminal_chat.js?v=8"
TCHAT_HTML = """<div id=\"terminal-chat\" class=\"tchat\">
  <button id=\"tchat-toggle\" class=\"tchat-toggle hidden\" type=\"button\" aria-hidden=\"true\" tabindex=\"-1\">💬 Чат <span id=\"tchat-unread\" class=\"tchat-unread hidden\" aria-hidden=\"true\">0</span></button>
  <div id=\"tchat-panel\" class=\"tchat-panel hidden\">
    <div class=\"tchat-head\"><span>💬 Чат LiqScope</span><button id=\"tchat-close\" class=\"tchat-close\" type=\"button\">✕</button></div>
    <div class=\"tchat-tabs\" id=\"tchat-tabs\">
      <button type=\"button\" class=\"tchat-tab active\" data-tab=\"public\">Общий</button>
      <button type=\"button\" class=\"tchat-tab\" data-tab=\"dm\">🔒 Личные <span id=\"tchat-tab-dm-badge\" class=\"tchat-tab-badge hidden\">0</span></button>
      <button type=\"button\" class=\"tchat-tab\" data-tab=\"services\">🔔 Сервисы <span id=\"tchat-tab-svc-badge\" class=\"tchat-tab-badge hidden\">0</span></button>
      <button type=\"button\" class=\"tchat-tab\" data-tab=\"support\">🆘 Поддержка <span id=\"tchat-tab-sup-badge\" class=\"tchat-tab-badge hidden\">0</span></button>
      <button type=\"button\" class=\"tchat-mute\" id=\"tchat-mute\" title=\"Вкл/выкл звук этой вкладки\">🔊</button>
    </div>
    <div id=\"tchat-list\" class=\"tchat-list\"></div>
    <div id=\"tchat-dm\" class=\"tchat-dm hidden\"><div id=\"tchat-dm-list\" class=\"tchat-dm-list\"></div></div>
    <div id=\"tchat-dm-chat\" class=\"tchat-dm-chat hidden\">
      <div id=\"tchat-dm-head\" class=\"tchat-dm-head\"></div>
      <div id=\"tchat-dm-msgs\" class=\"tchat-list\"></div>
    </div>
    <div id=\"tchat-services\" class=\"tchat-list hidden\"></div>
    <div id=\"tchat-support\" class=\"tchat-support hidden\">
      <div id=\"tchat-support-threads\" class=\"tchat-dm-list hidden\"></div>
      <div id=\"tchat-support-list\" class=\"tchat-list\"></div>
      <div class=\"tchat-support-name hidden\" id=\"tchat-support-name-wrap\"><input id=\"tchat-support-name\" maxlength=\"40\" placeholder=\"Ваш временный ник (обязательно для гостей)\"></div>
    </div>
    <div class=\"tchat-foot\"><input id=\"tchat-input\" maxlength=\"2000\" placeholder=\"Сообщение… (Enter)\"><button id=\"tchat-send\" type=\"button\">➤</button><div id=\"tchat-hint\" class=\"tchat-hint\"></div></div>
  </div>
</div>"""


def analytics_block() -> str:
    """Счётчик Google Analytics — ровно тот код, что выдаёт Google для ``<head>``.

    Вставляется один здесь, а не в каждый шаблон: страницы сайта собираются
    ``render``, поэтому метрика оказывается сразу на всех публичных страницах
    (лендинг, терминал, дайджест, сводки по часам, вход и кабинет) и не может
    задвоиться, если кто-то поправит один файл и забудет про остальные.
    В админ-панели счётчика нет: это служебная страница, её просмотры — не
    аудитория, а шум в отчётах (и ``robots.txt`` её закрыт).
    """
    if not GA_ID:
        return ""
    return (
        "<!-- Google tag (gtag.js) -->\n"
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}">'
        "</script>\n"
        "<script>\n"
        "  window.dataLayer = window.dataLayer || [];\n"
        "  function gtag(){dataLayer.push(arguments);}\n"
        "  gtag('js', new Date());\n"
        f"  gtag('config', '{GA_ID}');\n"
        "</script>"
    )


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


def _language_block(path: str, lang: str = DEFAULT_LANG) -> str:
    """Ссылки hreflang + og:locale:alternate: поисковик и соцсети видят все языки."""
    rows = []
    for code in LANGS:
        rows.append(
            f'<link rel="alternate" hreflang="{HREFLANG[code]}"'
            f' href="{_attr(_lang_url(SITE_URL + path, code))}">'
        )
    rows.append(
        f'<link rel="alternate" hreflang="x-default" href="{_attr(SITE_URL + path)}">'
    )
    # og:locale:alternate для Facebook/Twitter — те же языки, что и hreflang
    for code in LANGS:
        if code == lang:
            continue
        rows.append(
            f'<meta property="og:locale:alternate" content="{OG_LOCALE.get(code, code)}">'
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
    status_code: int = 200,
    analytics: bool = True,
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
    block = _language_block(path, lang)
    # счётчик — тоже в head, но первым: async-загрузчик gtag.js не должен
    # ждать ни разметки языка, ни JSON-LD
    ga = analytics_block() if analytics else ""
    block += f'\n<script>window.LIQSCOPE_LANG = "{lang}";</script>'
    if auto:
        # Язык подобран за гостя (браузер или страна): клиент может уточнить его
        # сам — например, когда браузер прислал язык, которого на сайте нет
        block += '\n<script>window.LIQSCOPE_LANG_AUTO = 1;</script>' 
    # 💬 чат на всех страницах: CSS в head, если его ещё нет в шаблоне
    if "terminal_chat.css" not in html:
        block += f'\n<link rel="stylesheet" href="{TCHAT_CSS}">'
    if extra_head:
        block += "\n" + extra_head
    # вставляем в самый конец <head>: язык успевает выставиться к моменту
    # DOMContentLoaded, когда i18n.js применяет словари
    if ga:
        block = ga + "\n" + block
    html = html.replace("</head>", block + "\n</head>", 1)

    # 💬 чат-виджет: если страница ещё не содержит #terminal-chat — инжектим
    # тот же HTML, что в терминале, и скрипт. Скрипт после виджета, чтобы DOM уже был.
    if 'id="terminal-chat"' not in html:
        inject_body = TCHAT_HTML
        if "terminal_chat.js" not in html:
            inject_body += f'\n<script src="{TCHAT_JS}"></script>'
        if "</body>" in html:
            html = html.replace("</body>", inject_body + "\n</body>", 1)
        else:
            html += inject_body
    else:
        if "terminal_chat.js" not in html and "</body>" in html:
            html = html.replace("</body>", f'<script src="{TCHAT_JS}"></script>\n</body>', 1)

    resp = Response(content=html, media_type="text/html; charset=utf-8", status_code=status_code)
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


def sitemap_xml(digest_items=None, hourly_items=None) -> Response:
    """sitemap.xml: лендинг, сводки по часам, дайджест, терминал и вход — на всех языках.

    Плюс свежие выпуски дайджеста и сводок: каждый день/пост — отдельный URL
    вида ``/digest?day=YYYY-MM-DD`` и ``/hourly?day=YYYY-MM-DD``. Поисковик
    видит архив целиком, а не только шапку раздела. Для фото — image sitemap.
    """
    last = time.strftime("%Y-%m-%d", time.gmtime())
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml"'
        ' xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">',
    ]

    def add_url(loc_path: str, lastmod: str, freq: str, prio: str, images=None):
        # images: list of image urls (absolute)
        for code in LANGS:
            parts.append("  <url>")
            parts.append(f"    <loc>{html_mod.escape(_lang_url(SITE_URL + loc_path, code))}</loc>")
            parts.append(f"    <lastmod>{html_mod.escape(lastmod)}</lastmod>")
            parts.append(f"    <changefreq>{freq}</changefreq>")
            parts.append(f"    <priority>{prio}</priority>")
            # Картинки — если есть обложка выпуска, поисковик индексирует её как image
            if images:
                for img_url in images[:3]:  # не больше 3 на URL
                    parts.append(f"    <image:image><image:loc>{html_mod.escape(img_url)}</image:loc></image:image>")
            for alt in LANGS:
                parts.append(
                    f'    <xhtml:link rel="alternate" hreflang="{HREFLANG[alt]}"'
                    f' href="{html_mod.escape(_lang_url(SITE_URL + loc_path, alt))}"/>'
                )
            parts.append(
                f'    <xhtml:link rel="alternate" hreflang="x-default"'
                f' href="{html_mod.escape(SITE_URL + loc_path)}"/>'
            )
            parts.append("  </url>")

    # Базовые страницы — с общей обложкой
    og_cover = f"{SITE_URL}/static/og-cover.png"
    for path, freq, priority in _PAGES:
        add_url(path, last, freq, priority, images=[og_cover])

    # Дайджест: последние 100 выпусков
    try:
        items = list(digest_items or [])[:100]
        for rec in items:
            day = str((rec or {}).get("day") or (rec or {}).get("id") or "").strip()
            if not day:
                continue
            lm = day if re.match(r"^\d{4}-\d{2}-\d{2}$", day) else last
            # Пытаемся достать обложку выпуска для image sitemap
            imgs = []
            try:
                photo = (rec or {}).get("photo") or {}
                # Если фото уже имеет публичный url (через /api/digest/cover) — берём его
                if photo.get("path"):
                    # Абсолютный URL на обложку через API
                    imgs.append(f"{SITE_URL}/api/digest/cover?day={day}")
                else:
                    imgs.append(og_cover)
            except Exception:
                imgs = [og_cover]
            add_url(f"/digest?day={day}", lm, "daily", "0.7", images=imgs)
    except Exception:
        pass

    # Сводки по часам: последние 100 дней
    try:
        items = list(hourly_items or [])[:100]
        seen_days = set()
        for rec in items:
            day = str((rec or {}).get("day") or "").strip()
            if not day or day in seen_days:
                continue
            seen_days.add(day)
            lm = day if re.match(r"^\d{4}-\d{2}-\d{2}$", day) else last
            imgs = []
            try:
                # У часовых постов тоже есть фото — берём первое
                photo = (rec or {}).get("photo") or {}
                if photo.get("path") or (rec or {}).get("id"):
                    # Фото через /api/hourly/photo/{id} если id есть
                    pid = str((rec or {}).get("id") or "")
                    if pid:
                        imgs.append(f"{SITE_URL}/api/hourly/photo/{pid}")
                    else:
                        imgs.append(f"{SITE_URL}/api/digest/cover?day={day}")
                else:
                    imgs.append(og_cover)
            except Exception:
                imgs = [og_cover]
            add_url(f"/hourly?day={day}", lm, "daily", "0.6", images=imgs)
    except Exception:
        pass

    parts.append("</urlset>")
    parts.append("")
    return Response(content="\n".join(parts), media_type="application/xml; charset=utf-8")


def manifest(public_url: Optional[str] = None, lang: str = DEFAULT_LANG) -> Response:
    """manifest.webmanifest — PWA-мелочи: имя, иконка, цвета.

    Язык берём из запроса (``?lang=``/cookie/Accept-Language): у PWA имя и
    описание должны совпадать с языком, на котором человек видит сайт,
    иначе установка приложения покажет русский текст англоязычному гостю.
    """
    lang = lang if lang in LANGS else DEFAULT_LANG
    # Имя и описание — из тех же словарей, что и <title> в поиске: заголовок
    # в выдаче и заголовок в окне не разъезжаются.
    name = _text(lang, "seo.land.title", "LiqScope — crypto futures liquidations")
    desc = _text(lang, "seo.land.desc", "Live crypto futures liquidation feed")
    # Короткое имя — всегда LiqScope, но для CJK можно оставить как есть
    short = "LiqScope"
    # start_url с языком, чтобы PWA открывалась на том же языке, на котором
    # её установили: без параметра — всегда английский (DEFAULT_LANG).
    start = _lang_url("/", lang) if lang != DEFAULT_LANG else "/"
    data = {
        "name": name,
        "short_name": short,
        "description": desc,
        "start_url": start,
        "scope": "/",
        "display": "standalone",
        "background_color": "#070a10",
        "theme_color": "#060a12",
        "lang": HREFLANG.get(lang, lang),
        "dir": "ltr",
        "categories": ["finance", "business", "utilities"],
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {
                "name": _text(lang, "seo.terminal.title", "Terminal"),
                "short_name": "Terminal",
                "description": _text(lang, "seo.terminal.desc", ""),
                "url": _lang_url("/terminal", lang),
                "icons": [{"src": "/static/icon-192.png", "sizes": "192x192"}],
            },
            {
                "name": _text(lang, "seo.digest.title", "Digest"),
                "short_name": "Digest",
                "url": _lang_url("/digest", lang),
                "icons": [{"src": "/static/icon-192.png", "sizes": "192x192"}],
            },
            {
                "name": _text(lang, "seo.hourly.title", "Hourly"),
                "short_name": "Hourly",
                "url": _lang_url("/hourly", lang),
                "icons": [{"src": "/static/icon-192.png", "sizes": "192x192"}],
            },
        ],
    }
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/manifest+json; charset=utf-8",
    )


#: FAQ для лендинга — на каждом языке, чтобы поисковик показывал ответы прямо в выдаче
FAQ = {
    "ru": [
        ("Что такое LiqScope?", "LiqScope — бесплатный терминал ликвидаций крипто-фьючерсов: живая лента с 8 бирж (Binance, Bybit, OKX, Gate, Bitget, HTX, Hyperliquid, dYdX), свечной график с кластерами, профиль объёмов, CVD и открытый интерес."),
        ("Нужно ли регистрироваться?", "Нет. Терминал открыт без регистрации и без API-ключей. Регистрация нужна только для алертов в Telegram, корреляций и других сервисов в кабинете."),
        ("Как работает определение ликвидаций на Hyperliquid?", "Hyperliquid не публикует ликвидации напрямую: событие вычисляется по всплеску маркет-ордеров в ленте сделок и подтверждается через /info по адресу кошелька."),
        ("Что такое CVD и OI?", "CVD — перевес агрессивных покупок над продажами за свечу, OI — изменение открытого интереса: рост — деньги заходят в позиции, падение — уходят."),
        ("Где смотреть итоги дня?", "В разделе Дайджест — каждый вечер около 22:00 МСК, и в Сводках по часам — каждые несколько часов, те же посты что в Telegram-канале."),
    ],
    "en": [
        ("What is LiqScope?", "LiqScope is a free crypto futures liquidation terminal: live feed from 8 exchanges (Binance, Bybit, OKX, Gate, Bitget, HTX, Hyperliquid, dYdX), candlestick chart with clusters, volume profile, CVD and open interest."),
        ("Do I need to sign up?", "No. The terminal is open without sign-up and without API keys. Sign-up is only for Telegram alerts, correlations and other services in your account."),
        ("How are Hyperliquid liquidations detected?", "Hyperliquid does not publish liquidations directly: the event is inferred from a market-order burst in the trade tape and confirmed via /info by wallet address."),
        ("What are CVD and OI?", "CVD is the taker buy/sell imbalance per candle, OI is the change in open interest: rise — money flows in, fall — flows out."),
        ("Where to see daily results?", "In Digest — every evening ~22:00 MSK, and in Hourly summaries — every few hours, the same posts as in our Telegram channel."),
    ],
    "zh": [
        ("LiqScope 是什么？", "LiqScope 是免费的加密期货清算终端：来自 8 家交易所的实时流、带聚类的K线图、成交量分布、CVD 和未平仓量。"),
        ("需要注册吗？", "不需要。终端无需注册、无需 API 密钥即可使用。注册仅用于 Telegram 提醒、相关性等账户服务。"),
        ("Hyperliquid 的清算如何检测？", "Hyperliquid 不直接发布清算：通过交易带中的市价单爆发推断，并通过 /info 按钱包地址确认。"),
        ("什么是 CVD 和 OI？", "CVD 是每根K线的吃单买卖不平衡，OI 是未平仓量变化：上升——资金流入，下降——流出。"),
        ("在哪里查看每日总结？", "在 Digest——每晚约22:00 MSK，以及 Hourly——每隔几小时，与 Telegram 频道相同的帖子。"),
    ],
    "hi": [
        ("LiqScope क्या है?", "LiqScope एक मुफ्त क्रिप्टो फ्यूचर्स लिक्विडेशन टर्मिनल है: 8 एक्सचेंजों से लाइव फीड, क्लस्टर वाला कैंडल चार्ट, वॉल्यूम प्रोफाइल, CVD और ओपन इंटरेस्ट।"),
        ("क्या साइन-अप जरूरी है?", "नहीं। टर्मिनल बिना साइन-अप और बिना API कुंजी के खुला है। साइन-अप केवल Telegram अलर्ट, सहसंबंध आदि के लिए है।"),
        ("Hyperliquid लिक्विडेशन कैसे पता चलते हैं?", "Hyperliquid सीधे लिक्विडेशन प्रकाशित नहीं करता: ट्रेड टेप में मार्केट-ऑर्डर बर्स्ट से अनुमान और /info द्वारा पुष्टि।"),
        ("CVD और OI क्या हैं?", "CVD प्रति कैंडल टेकर खरीद/बिक्री असंतुलन है, OI ओपन इंटरेस्ट में बदलाव है।"),
        ("दैनिक परिणाम कहाँ देखें?", "Digest में — हर शाम ~22:00 MSK, और Hourly में — हर कुछ घंटों में, Telegram चैनल जैसे ही पोस्ट।"),
    ],
    "es": [
        ("¿Qué es LiqScope?", "LiqScope es un terminal gratuito de liquidaciones de futuros cripto: feed en vivo de 8 exchanges, gráfico de velas con clústeres, perfil de volumen, CVD e interés abierto."),
        ("¿Necesito registrarme?", "No. El terminal está abierto sin registro y sin claves API. El registro es solo para alertas en Telegram, correlaciones y otros servicios."),
        ("¿Cómo se detectan liquidaciones en Hyperliquid?", "Hyperliquid no publica liquidaciones directamente: se infiere de una ráfaga de órdenes de mercado en la cinta y se confirma vía /info por dirección."),
        ("¿Qué son CVD y OI?", "CVD es el desequilibrio comprador/vendedor por vela, OI es el cambio en interés abierto."),
        ("¿Dónde ver resultados diarios?", "En Digest — cada noche ~22:00 MSK, y en Hourly — cada pocas horas, los mismos posts que en el canal de Telegram."),
    ],
}

HOWTO = {
    "ru": {
        "name": "Как работает LiqScope",
        "steps": [
            ("Слушаем биржи", "Публичные WebSocket-каналы ликвидаций и сделок — напрямую, без посредников."),
            ("Нормализуем данные", "Пары, объёмы и стороны приводятся к единому виду, дубли отсекаются."),
            ("Рисуем на графике", "Ликвидации ложатся на свечи в свою цену, профиль показывает их объёмы по уровням."),
            ("Вы смотрите", "Лента, статистика 5м/1ч/24ч, топ монет — всё обновляется в реальном времени."),
        ],
    },
    "en": {
        "name": "How LiqScope works",
        "steps": [
            ("Listen to exchanges", "Public WebSocket liquidation and trade channels — directly, no middlemen."),
            ("Normalize data", "Pairs, volumes and sides are unified, duplicates are dropped."),
            ("Draw on chart", "Liquidations are placed at their price on candles, profile shows volume by levels."),
            ("You watch", "Feed, 5m/1h/24h stats, top coins — everything updates in real time."),
        ],
    },
    "zh": {
        "name": "LiqScope 如何工作",
        "steps": [
            ("监听交易所", "公共 WebSocket 清算和交易通道——直接，无中介。"),
            ("标准化数据", "交易对、成交量和方向统一，去重。"),
            ("在图表上绘制", "清算按价格落在K线上，分布显示按价格的成交量。"),
            ("你来观看", "流、5m/1h/24h 统计、热门币——全部实时更新。"),
        ],
    },
    "hi": {
        "name": "LiqScope कैसे काम करता है",
        "steps": [
            ("एक्सचेंज सुनें", "पब्लिक WebSocket लिक्विडेशन और ट्रेड चैनल — सीधे।"),
            ("डेटा सामान्य करें", "पेयर, वॉल्यूम और साइड एकरूप, डुप्लिकेट हटते हैं।"),
            ("चार्ट पर बनाएं", "लिक्विडेशन अपनी कीमत पर कैंडल पर लगते हैं।"),
            ("आप देखें", "फीड, आँकड़े, टॉप कॉइन — सब रीयल-टाइम।"),
        ],
    },
    "es": {
        "name": "Cómo funciona LiqScope",
        "steps": [
            ("Escuchamos exchanges", "Canales WebSocket públicos de liquidaciones y trades — directo."),
            ("Normalizamos datos", "Pares, volúmenes y lados unificados, duplicados eliminados."),
            ("Dibujamos en el gráfico", "Las liquidaciones caen en su precio sobre las velas."),
            ("Tú miras", "Feed, stats 5m/1h/24h, top monedas — todo en tiempo real."),
        ],
    },
}

#: JSON-LD: сайт, приложение и организация — одна структура на все страницы.
def jsonld(kind: str = "landing", lang: str = DEFAULT_LANG,
           image: str = "", article: Optional[dict] = None) -> str:
    """Разметка Schema.org. ``image`` — картинка страницы (обложка выпуска).

    ``article`` — для конкретного дня: {"day": "YYYY-MM-DD", "title": "...", "desc": "...", "date": "..."}
    — тогда отдаём Article/BlogPosting вместо общей CollectionPage.
    """
    lang = lang if lang in LANGS else DEFAULT_LANG
    common = {
        "@context": "https://schema.org",
        "name": "LiqScope",
        "url": SITE_URL,
        "logo": f"{SITE_URL}/static/logo.png",
        "description": _text(lang, "seo.land.desc"),
    }
    def breadcrumb(items):
        # items: list of (name, url)
        return {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i + 1,
                    "name": n,
                    "item": u,
                }
                for i, (n, u) in enumerate(items)
            ],
        }

    if kind == "landing":
        data = [
            {
                "@type": "WebSite",
                **common,
                "inLanguage": list(HREFLANG.values()),
                "potentialAction": {
                    "@type": "SearchAction",
                    "target": f"{SITE_URL}/terminal?search={{search_term_string}}",
                    "query-input": "required name=search_term_string",
                },
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
            breadcrumb([
                ("LiqScope", SITE_URL + "/"),
            ]),
            {
                "@type": "FAQPage",
                "inLanguage": HREFLANG.get(lang, lang),
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a},
                    }
                    for q, a in FAQ.get(lang, FAQ.get(DEFAULT_LANG, []))
                ],
            },
            {
                "@type": "HowTo",
                "name": HOWTO.get(lang, HOWTO.get(DEFAULT_LANG, {})).get("name", "How it works"),
                "inLanguage": HREFLANG.get(lang, lang),
                "step": [
                    {
                        "@type": "HowToStep",
                        "name": s[0],
                        "text": s[1],
                        "position": i + 1,
                    }
                    for i, s in enumerate(HOWTO.get(lang, HOWTO.get(DEFAULT_LANG, {})).get("steps", []))
                ],
            },
        ]
    elif kind == "hourly":
        # Если открыт конкретный день/пост — отдаём Article, иначе CollectionPage
        if article and article.get("day"):
            art_title = article.get("title") or _text(lang, "seo.hourly.title")
            art_desc = article.get("desc") or _text(lang, "seo.hourly.desc")
            art_date = article.get("date") or article.get("day")
            data = [
                {
                    "@type": "BlogPosting",
                    "headline": art_title,
                    "description": art_desc,
                    "url": f"{SITE_URL}/hourly?day={article.get('day')}" if article.get("day") else f"{SITE_URL}/hourly",
                    "inLanguage": HREFLANG.get(lang, lang),
                    "datePublished": art_date,
                    "dateModified": art_date,
                    "author": {"@type": "Organization", "name": "LiqScope", "url": SITE_URL},
                    "publisher": {
                        "@type": "Organization",
                        "name": "LiqScope",
                        "logo": {"@type": "ImageObject", "url": f"{SITE_URL}/static/icon-512.png", "width": 512, "height": 512},
                    },
                    "isPartOf": {"@type": "Blog", "name": "LiqScope Hourly", "url": f"{SITE_URL}/hourly"},
                },
                breadcrumb([
                    ("LiqScope", SITE_URL + "/"),
                    (_text(lang, "seo.hourly.title", "Hourly"), SITE_URL + "/hourly"),
                    (article.get("day") or "Day", f"{SITE_URL}/hourly?day={article.get('day')}"),
                ]),
            ]
        else:
            data = [
                {
                    "@type": "CollectionPage",
                    **common,
                    "url": f"{SITE_URL}/hourly",
                    "inLanguage": list(HREFLANG.values()),
                    "description": _text(lang, "seo.hourly.desc"),
                    "isPartOf": {"@type": "Blog", "name": "LiqScope", "url": f"{SITE_URL}/hourly"},
                },
                breadcrumb([
                    ("LiqScope", SITE_URL + "/"),
                    (_text(lang, "seo.hourly.title", "Hourly"), SITE_URL + "/hourly"),
                ]),
            ]
    elif kind == "digest":
        if article and article.get("day"):
            art_title = article.get("title") or f"{_text(lang, 'seo.digest.title')} — {article.get('day')}"
            art_desc = article.get("desc") or _text(lang, "seo.digest.desc")
            art_date = article.get("date") or article.get("day")
            data = [
                {
                    "@type": "BlogPosting",
                    "headline": art_title,
                    "description": art_desc,
                    "url": f"{SITE_URL}/digest?day={article.get('day')}",
                    "inLanguage": HREFLANG.get(lang, lang),
                    "datePublished": art_date,
                    "dateModified": art_date,
                    "author": {"@type": "Organization", "name": "LiqScope", "url": SITE_URL},
                    "publisher": {
                        "@type": "Organization",
                        "name": "LiqScope",
                        "logo": {"@type": "ImageObject", "url": f"{SITE_URL}/static/icon-512.png", "width": 512, "height": 512},
                    },
                    "isPartOf": {"@type": "Blog", "name": "LiqScope Digest", "url": f"{SITE_URL}/digest"},
                },
                breadcrumb([
                    ("LiqScope", SITE_URL + "/"),
                    (_text(lang, "seo.digest.title", "Digest"), SITE_URL + "/digest"),
                    (article.get("day") or "Day", f"{SITE_URL}/digest?day={article.get('day')}"),
                ]),
            ]
        else:
            data = [
                {
                    "@type": "Blog",
                    **common,
                    "url": f"{SITE_URL}/digest",
                    "inLanguage": list(HREFLANG.values()),
                    "description": _text(lang, "seo.digest.desc"),
                },
                breadcrumb([
                    ("LiqScope", SITE_URL + "/"),
                    (_text(lang, "seo.digest.title", "Digest"), SITE_URL + "/digest"),
                ]),
            ]
    elif kind == "404":
        data = [
            {"@type": "WebPage", **common, "url": f"{SITE_URL}/404", "name": _text(lang, "seo.404.title")},
            breadcrumb([
                ("LiqScope", SITE_URL + "/"),
                (_text(lang, "404.title", "404"), SITE_URL + "/404"),
            ]),
        ]
    else:
        data = [
            {"@type": "WebPage", **common, "url": f"{SITE_URL}/terminal"},
            breadcrumb([
                ("LiqScope", SITE_URL + "/"),
                (_text(lang, "seo.terminal.title", "Terminal"), SITE_URL + "/terminal"),
            ]),
        ]
    if image:
        # картинка страницы: поисковик и мессенджер берут её для превью, а для
        # выпуска дайджеста это то самое фото дня, что ушло в канал
        for item in data:
            if isinstance(item, dict) and item.get("@type") in ("BlogPosting", "CollectionPage", "Blog", "WebPage", "SoftwareApplication", "WebSite"):
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
