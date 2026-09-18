"""
SEO и Интернационализация для LiqScope.
Поддержка мультиязычных метатегов, hreflang, OpenGraph, JSON-LD (Schema.org),
sitemap.xml и robots.txt.
"""
from typing import Dict, Any, Optional
import html

SUPPORTED_LANGS = ["en", "ru", "zh", "hi", "es"]
DEFAULT_LANG = "en"

# BCP-47 / OpenGraph локали
LANG_LOCALES = {
    "en": {"html_lang": "en", "og_locale": "en_US", "hreflang": "en"},
    "ru": {"html_lang": "ru", "og_locale": "ru_RU", "hreflang": "ru"},
    "zh": {"html_lang": "zh-Hans", "og_locale": "zh_CN", "hreflang": "zh-Hans"},
    "hi": {"html_lang": "hi", "og_locale": "hi_IN", "hreflang": "hi"},
    "es": {"html_lang": "es", "og_locale": "es_ES", "hreflang": "es"},
}

# SEO метаданные для страниц
PAGE_SEO: Dict[str, Dict[str, Dict[str, str]]] = {
    "landing": {
        "en": {
            "title": "LiqScope — Real-Time Crypto Futures Liquidation Terminal",
            "desc": "Free live crypto futures liquidation terminal for Binance, Bybit, OKX, Gate.io, Bitget, and HTX. Interactive candlestick chart with clusters, CVD, open interest, and pair screener.",
            "keywords": "crypto liquidation tracker, binance liquidations live, bybit liquidation heatmap, crypto futures liquidation chart, bitcoin liquidation levels, orderflow CVD crypto",
        },
        "ru": {
            "title": "LiqScope — терминал ликвидаций крипто-фьючерсов в реальном времени",
            "desc": "Бесплатный live-терминал ликвидаций крипто-фьючерсов: Binance, Bybit, OKX, Gate.io, Bitget, HTX. Свечной график с кластерами ликвидаций, CVD, открытый интерес и фильтры пар.",
            "keywords": "ликвидации крипта онлайн, карта ликвидаций бинанс, ликвидации байбит, тепловая карта ликвидаций, трекинг ликвидаций фьючерсов, cvd открытый интерес",
        },
        "zh": {
            "title": "LiqScope — 实时加密货币期货爆仓终端与K线图",
            "desc": "免费实时加密货币期货爆仓监控终端：支持 Binance、Bybit、OKX、Gate.io、Bitget、HTX。带爆仓聚类K线图、CVD累积买卖差、持仓量OI与全币种搜索。",
            "keywords": "加密货币爆仓数据, 比特币爆仓监控, 合约爆仓热力图, 实时清算地图, 币安爆仓流, 虚拟货币强平数据",
        },
        "hi": {
            "title": "LiqScope — रीयल-टाइम क्रिप्टो फ्यूचर्स लिक्विडेशन टर्मिनल",
            "desc": "मुफ़्त लाइव क्रिप्टो फ्यूचर्स लिक्विडेशन टर्मिनल: Binance, Bybit, OKX, Gate.io, Bitget, HTX। क्लस्टर के साथ कैंडल चार्ट, CVD, ओपन इंटरेस्ट और कॉइन स्क्रीनर।",
            "keywords": "क्रिप्टो लिक्विडेशन ट्रैकर, बिटकॉइन लिक्विडेशन लाइव, क्रिप्टो फ्यूचर्स चार्ट, बायनेन्स लिक्विडेशन, ओपन इंटरेस्ट",
        },
        "es": {
            "title": "LiqScope — Terminal de liquidaciones de futuros cripto en tiempo real",
            "desc": "Terminal gratuito de liquidaciones de futuros en vivo: Binance, Bybit, OKX, Gate.io, Bitget, HTX. Gráfico interactivo con clústeres, CVD, interés abierto y filtrado de pares.",
            "keywords": "liquidaciones cripto en vivo, mapa de liquidaciones bitcoin, liquidaciones binance en directo, futuros criptomonedas, interes abierto CVD",
        },
    },
    "terminal": {
        "en": {
            "title": "LiqScope Terminal — Live Liquidation Stream, Clusters & Volume Profile",
            "desc": "Professional live crypto futures liquidation terminal with streaming data from 8 exchanges, candlestick cluster charts, CVD delta, and aggregated Open Interest.",
            "keywords": "live crypto terminal, crypto liquidation stream, futures cluster chart, CVD indicator, open interest real-time",
        },
        "ru": {
            "title": "LiqScope Terminal — Live-лента ликвидаций, кластеры и профиль объёмов",
            "desc": "Профессиональный живой терминал ликвидаций с 8 криптобирж, кластерный свечной график, дельта CVD и совокупный открытый интерес.",
            "keywords": "терминал ликвидаций, лента ликвидаций крипта, кластерный график биткоин, мониторинг фьючерсов",
        },
        "zh": {
            "title": "LiqScope 终端 — 实时爆仓流、K线聚类与成交量分布",
            "desc": "来自8家主流交易所的实时加密货币期货爆仓终端、K线聚类图、CVD指标与全网持仓量监测。",
            "keywords": "爆仓实时终端, 加密货币爆仓流, K线聚类分析, CVD指标, 持仓量监控",
        },
        "hi": {
            "title": "LiqScope टर्मिनल — लाइव लिक्विडेशन स्ट्रीम और कैंडल क्लस्टर चार्ट",
            "desc": "8 प्रमुख एक्सचेंजों से लाइव क्रिप्टो फ्यूचर्स लिक्विडेशन टर्मिनल, कैंडलस्टिक क्लस्टर चार्ट, CVD और ओपन इंटरेस्ट।",
            "keywords": "क्रिप्टो टर्मिनल, लिक्विडेशन स्ट्रीम, क्लस्टर चार्ट, वॉल्यूम प्रोफाइल",
        },
        "es": {
            "title": "LiqScope Terminal — Stream de liquidaciones en vivo y gráfico de clústeres",
            "desc": "Terminal profesional de liquidaciones de futuros cripto en directo desde 8 exchanges, gráficos de clústeres, CVD e interés abierto.",
            "keywords": "terminal liquidaciones cripto, stream de futuros en directo, gráfico cluster bitcoin, volumen delta",
        },
    },
    "digest": {
        "en": {
            "title": "LiqScope Daily Market Digest — Crypto Futures & Liquidation Reports",
            "desc": "Daily evening recap of crypto liquidations, exchange statistics, heavily leveraged coins, and market sentiment.",
            "keywords": "crypto daily market report, daily liquidation digest, bitcoin market recap, futures market report",
        },
        "ru": {
            "title": "LiqScope Дневной дайджест рынка — ликвидации и отчеты за сутки",
            "desc": "Ежевечерние итоги рынка крипто-фьючерсов: крупнейшие ликвидации, лидеры по открытому интересу, статистика бирж и рыночное настроение.",
            "keywords": "дайджест криптовалют, отчет по ликвидациям, статистика крипто-рынка, итоги дня крипта",
        },
        "zh": {
            "title": "LiqScope 每日加密市场简报 — 期货爆仓与市场动向分析",
            "desc": "每日加密期货爆仓回顾、交易所清算统计、高持仓量异动币种与全网多空情绪分析。",
            "keywords": "加密市场日报, 比特币爆仓复盘, 期货持仓分析, 市场情绪报告",
        },
        "hi": {
            "title": "LiqScope दैनिक बाज़ार डाइजेस्ट — क्रिप्टो फ्यूचर्स और लिक्विडेशन रिपोर्ट",
            "desc": "क्रिप्टो लिक्विडेशन, एक्सचेंज आंकड़े, सर्वाधिक लेवरेज वाले कॉइन और मार्केट सेंटिमेंट का दैनिक विश्लेषण।",
            "keywords": "दैनिक क्रिप्टो रिपोर्ट, लिक्विडेशन रिपोर्ट, बाज़ार समाचार, बिटकॉइन विश्लेषण",
        },
        "es": {
            "title": "LiqScope Resumen Diario del Mercado — Liquidaciones y análisis de futuros",
            "desc": "Resumen diario de liquidaciones cripto, estadísticas de exchanges, monedas con mayor interés abierto y sentimiento de mercado.",
            "keywords": "resumen diario cripto, informe de liquidaciones, análisis de futuros bitcoin, métricas del mercado",
        },
    },
}

# FAQ данные для Schema.org и контента
FAQS = {
    "en": [
        {
            "q": "What is a crypto liquidation tracker?",
            "a": "A crypto liquidation tracker monitors forced position closures on crypto derivative exchanges (like Binance, Bybit, OKX) when a trader's margin balance falls below the maintenance margin requirement. Tracking liquidations reveals major support and resistance levels, market cascading runs, and stop-hunts."
        },
        {
            "q": "Which exchanges does LiqScope support?",
            "a": "LiqScope connects to real-time public feeds of 8 major cryptocurrency exchanges: Binance, Bybit, OKX, Gate.io, Bitget, HTX, dYdX, and Hyperliquid, aggregating futures trades and liquidations in a single live stream."
        },
        {
            "q": "How does LiqScope calculate CVD (Cumulative Volume Delta)?",
            "a": "Cumulative Volume Delta (CVD) measures market aggression by subtracting market sell volume from market buy volume for taker orders over specified rolling windows (5m, 1h, 24h), indicating whether aggressive buyers or aggressive sellers are driving the market."
        },
        {
            "q": "Is LiqScope completely free to use?",
            "a": "Yes, LiqScope is 100% free with no registration or API keys required. It operates directly using transparent public WebSocket data streams."
        }
    ],
    "ru": [
        {
            "q": "Что такое терминал ликвидаций крипто-фьючерсов?",
            "a": "Терминал ликвидаций отслеживает принудительные закрытия позиций трейдеров на фьючерсных биржах (Binance, Bybit, OKX и др.), когда баланса маржи не хватает для удержания сделки. Карта ликвидаций помогает трейдерам видеть скопления стоп-приказов и зоны повышенной ликвидности."
        },
        {
            "q": "Какие биржи подключены к LiqScope?",
            "a": "LiqScope собирает потоки данных в реальном времени с 8 ведущих криптобирж: Binance, Bybit, OKX, Gate.io, Bitget, HTX, dYdX и Hyperliquid без задержек и посредников."
        },
        {
            "q": "Что такое CVD и как он рассчитывается?",
            "a": "CVD (кумулятивная дельта объёма) рассчитывается как разница между объёмом покупок и продаж по рыночным ордерам (тейкерам) за выбранный таймфрейм. Положительный CVD указывает на давление активных покупателей, отрицательный — продавцов."
        },
        {
            "q": "Нужно ли платить за использование LiqScope?",
            "a": "Нет, LiqScope полностью бесплатен, не требует обязательной регистрации или ввода биржевых API-ключей. Все данные доступны публично."
        }
    ],
    "zh": [
        {
            "q": "什么是加密货币爆仓监控终端？",
            "a": "爆仓终端实时追踪交易者在加密货币衍生品交易所（如币安、Bybit、OKX）因保证金不足而被系统强制平仓的订单。监控大额爆仓有助于识别市场流动性洼地和关键支撑阻力位。"
        },
        {
            "q": "LiqScope 支持哪些主流交易所？",
            "a": "LiqScope 实时接入 8 家主流加密货币交易所的公开 WebSocket 数据流：Binance、Bybit、OKX、Gate.io、Bitget、HTX、dYdX 以及 Hyperliquid。"
        },
        {
            "q": "CVD（累积成交量差）代表什么？",
            "a": "CVD 衡量主动吃单（Taker）买入量与卖出量的净差值。正值代表主动买盘占优，负值代表主动抛压沉重。"
        },
        {
            "q": "使用 LiqScope 是否需要付费或绑定 API？",
            "a": "完全不需要，LiqScope 100% 免费开放，无需绑定私密 API 密钥或强制注册，打开网页即可查看全部实时数据。"
        }
    ],
    "hi": [
        {
            "q": "क्रिप्टो लिक्विडेशन ट्रैकर क्या है?",
            "a": "क्रिप्टो लिक्विडेशन ट्रैकर प्रमुख डेरिवेटिव एक्सचेंजों पर जबरन बंद किए गए ट्रेड्स की रीयल-टाइम निगरानी करता है, जिससे बाज़ार में बड़े सपोर्ट और रेजिस्टेंस स्तरों की पहचान होती है।"
        },
        {
            "q": "LiqScope किन एक्सचेंजों का समर्थन करता है?",
            "a": "LiqScope 8 प्रमुख क्रिप्टो एक्सचेंजों से डेटा एकत्र करता है: Binance, Bybit, OKX, Gate.io, Bitget, HTX, dYdX और Hyperliquid।"
        },
        {
            "q": "क्या LiqScope पूरी तरह से मुफ़्त है?",
            "a": "हाँ, LiqScope बिना किसी रजिस्ट्रेशन या API कुंजी के सभी ट्रेडर्स के लिए पूरी तरह से मुफ़्त है।"
        }
    ],
    "es": [
        {
            "q": "¿Qué es un rastreador de liquidaciones cripto?",
            "a": "Un rastreador de liquidaciones monitoriza el cierre forzoso de posiciones en exchanges de derivados (Binance, Bybit, OKX, etc.) cuando se agota el margen, señalando zonas críticas de liquidez y soporte."
        },
        {
            "q": "¿Qué exchanges están integrados en LiqScope?",
            "a": "LiqScope procesa flujos en tiempo real de 8 exchanges líderes: Binance, Bybit, OKX, Gate.io, Bitget, HTX, dYdX y Hyperliquid."
        },
        {
            "q": "¿Es LiqScope gratuito?",
            "a": "Sí, LiqScope es completamente gratuito y no requiere registro ni claves de API para acceder a los datos en vivo."
        }
    ]
}


def build_head_seo(page_type: str, lang: str, base_url: str = "https://liqscope.online") -> str:
    """Генерирует полный набор метатегов: canonical, hreflang, OpenGraph, Twitter, JSON-LD."""
    base_url = (base_url or "https://liqscope.online").rstrip("/")
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG

    loc_info = LANG_LOCALES[lang]
    page_data = PAGE_SEO.get(page_type, PAGE_SEO["landing"])
    seo = page_data.get(lang, page_data["en"])

    # Определение путей для текущей страницы
    page_path = "" if page_type == "landing" else f"/{page_type}"

    # Текущий канонический URL
    current_url = f"{base_url}/{lang}{page_path}" if lang != "en" else (f"{base_url}{page_path}" if page_path else f"{base_url}/")

    # Формирование hreflang ссылок
    hreflang_links = []
    # x-default указывает на английскую версию
    hreflang_links.append(f'<link rel="alternate" hreflang="x-default" href="{base_url}{page_path or "/"}" />')
    for l in SUPPORTED_LANGS:
        tag = LANG_LOCALES[l]["hreflang"]
        if l == "en":
            l_url = f"{base_url}{page_path or '/'}"
        else:
            l_url = f"{base_url}/{l}{page_path}"
        hreflang_links.append(f'<link rel="alternate" hreflang="{tag}" href="{l_url}" />')

    hreflang_html = "\n    ".join(hreflang_links)

    # OpenGraph locale alternates
    og_alts = [f'<meta property="og:locale:alternate" content="{LANG_LOCALES[l]["og_locale"]}" />'
               for l in SUPPORTED_LANGS if l != lang]
    og_alts_html = "\n    ".join(og_alts)

    # JSON-LD Schema.org
    schema_entities = [
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": "LiqScope",
            "url": current_url,
            "applicationCategory": "FinanceApplication",
            "operatingSystem": "All",
            "description": seo["desc"],
            "image": f"{base_url}/static/logo.png",
            "offers": {
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "USD"
            }
        }
    ]

    faq_list = FAQS.get(lang, FAQS["en"])
    if faq_list:
        schema_entities.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": item["q"],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": item["a"]
                    }
                } for item in faq_list
            ]
        })

    import json
    json_ld_html = "\n    ".join(
        f'<script type="application/ld+json">{json.dumps(entity, ensure_ascii=False)}</script>'
        for entity in schema_entities
    )

    return f"""<!-- SEO & Internationalization -->
    <link rel="canonical" href="{current_url}" />
    {hreflang_html}
    <meta name="keywords" content="{html.escape(seo['keywords'])}">
    <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">

    <!-- Open Graph / Facebook -->
    <meta property="og:type" content="website">
    <meta property="og:url" content="{current_url}">
    <meta property="og:title" content="{html.escape(seo['title'])}">
    <meta property="og:description" content="{html.escape(seo['desc'])}">
    <meta property="og:image" content="{base_url}/static/logo.png">
    <meta property="og:locale" content="{loc_info['og_locale']}">
    {og_alts_html}

    <!-- Twitter Cards -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:url" content="{current_url}">
    <meta name="twitter:title" content="{html.escape(seo['title'])}">
    <meta name="twitter:description" content="{html.escape(seo['desc'])}">
    <meta name="twitter:image" content="{base_url}/static/logo.png">

    <!-- Schema.org Structured Data -->
    {json_ld_html}"""


def inject_seo_into_html(html_text: str, page_type: str, lang: str, base_url: str = "") -> str:
    """Подставляет правильный lang="", заголовок, описание и SEO теги в HTML."""
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG

    loc_tag = LANG_LOCALES[lang]["html_lang"]
    page_data = PAGE_SEO.get(page_type, PAGE_SEO["landing"])
    seo = page_data.get(lang, page_data["en"])

    # Замена <html lang="...">
    import re
    html_text = re.sub(r'<html[^>]*lang=["\'][^"\']*["\']', f'<html lang="{loc_tag}"', html_text, count=1)
    if '<html lang=' not in html_text:
        html_text = html_text.replace("<html", f'<html lang="{loc_tag}"', 1)

    # Замена <title>
    html_text = re.sub(r'<title[^>]*>.*?</title>', f'<title>{html.escape(seo["title"])}</title>', html_text, count=1, flags=re.DOTALL)

    # Замена <meta name="description" ...>
    desc_tag = f'<meta name="description" data-i18n-meta="{ "land.meta" if page_type == "landing" else "terminal.meta" }" content="{html.escape(seo["desc"])}">'
    html_text = re.sub(r'<meta\s+name=["\']description["\'][^>]*>', desc_tag, html_text, count=1)

    # Вставка в <head>
    head_seo = build_head_seo(page_type, lang, base_url)
    html_text = html_text.replace("</head>", f"    {head_seo}\n</head>", 1)

    return html_text


def generate_sitemap_xml(base_url: str = "https://liqscope.online") -> str:
    """Генерирует sitemap.xml со всеми поддерживаемыми страницами и языковыми версиями."""
    base_url = (base_url or "https://liqscope.online").rstrip("/")
    pages = ["", "terminal", "digest", "login"]

    urls_xml = []
    for page in pages:
        p_path = f"/{page}" if page else ""
        for lang in SUPPORTED_LANGS:
            if lang == "en":
                loc_url = f"{base_url}{p_path or '/'}"
            else:
                loc_url = f"{base_url}/{lang}{p_path}"

            priority = "1.0" if not page else ("0.9" if page == "terminal" else "0.8")
            changefreq = "always" if page == "terminal" else ("hourly" if not page else "daily")

            alt_links = []
            # x-default
            alt_links.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{base_url}{p_path or "/"}" />')
            for alt_lang in SUPPORTED_LANGS:
                alt_tag = LANG_LOCALES[alt_lang]["hreflang"]
                if alt_lang == "en":
                    alt_url = f"{base_url}{p_path or '/'}"
                else:
                    alt_url = f"{base_url}/{alt_lang}{p_path}"
                alt_links.append(f'    <xhtml:link rel="alternate" hreflang="{alt_tag}" href="{alt_url}" />')

            alts_str = "\n".join(alt_links)
            urls_xml.append(f"""  <url>
    <loc>{loc_url}</loc>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
{alts_str}
  </url>""")

    joined_urls = "\n".join(urls_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
{joined_urls}
</urlset>"""


def generate_robots_txt(base_url: str = "https://liqscope.online") -> str:
    """Генерирует оптимизированный robots.txt для международных поисковых ботов."""
    base_url = (base_url or "https://liqscope.online").rstrip("/")
    return f"""# LiqScope robots.txt
User-agent: *
Allow: /
Allow: /en/
Allow: /ru/
Allow: /zh/
Allow: /hi/
Allow: /es/
Allow: /terminal
Allow: /digest
Allow: /api/stats
Allow: /api/health
Allow: /api/digest
Allow: /static/

# Disallow internal control and account endpoints
Disallow: /admin
Disallow: /cabinet
Disallow: /reset
Disallow: /api/admin/
Disallow: /api/account/
Disallow: /api/bot_admin/

Sitemap: {base_url}/sitemap.xml
"""
