"""Партнёрские ссылки: одно место на весь проект.

Биржа Gate упоминается в постах канала, в сообщениях бота и на сайте.
Везде, где её имя кликабельно, оно должно вести по реферальной ссылке —
поэтому ссылка живёт здесь, а не расползается копиями по файлам.
"""
from __future__ import annotations

import html as _html

# Реферальная ссылка Gate (владелец проекта)
GATE_REF = "https://www.gate.com/ru/signup/VLFCAVWMBW?ref_type=103"
# Английская версия страницы — для англоязычного канала
GATE_REF_EN = "https://www.gate.com/signup/VLFCAVWMBW?ref_type=103"

# Биржи, у которых есть партнёрская ссылка: имя в данных -> (заголовок, URL)
PARTNERS = {
    "gate": ("Gate", GATE_REF),
}


def gate_url(lang: str = "ru") -> str:
    return GATE_REF_EN if str(lang).startswith("en") else GATE_REF


def ref_pair(exchange: str, lang: str = "ru"):
    """(заголовок, url) для биржи или None, если ссылки нет."""
    key = str(exchange or "").strip().lower()
    label, url = PARTNERS.get(key, (None, None))
    if not label:
        return None
    return label, (GATE_REF_EN if str(lang).startswith("en") else url)


def ex_link(exchange: str, fallback: str = "", lang: str = "ru") -> str:
    """HTML: имя биржи ссылкой, если для неё есть партнёрка.

    Telegram не делает ссылки внутри <pre>/<code>, поэтому вызывать это
    стоит только в обычном тексте сообщения.
    """
    pair = ref_pair(exchange, lang)
    if not pair:
        return _html.escape(fallback or str(exchange or ""), quote=False)
    label, url = pair
    name = fallback or label
    return f'<a href="{_html.escape(url, quote=True)}">{_html.escape(name, quote=False)}</a>'


def gate_line(lang: str = "ru", prefix: str = "💠") -> str:
    """Готовая строка-приглашение для постов и сообщений бота."""
    url = _html.escape(gate_url(lang), quote=True)
    if str(lang).startswith("en"):
        text = "Trade on Gate — up to 20% off fees"
    else:
        text = "Торговать на Gate — скидка на комиссию"
    return f'{prefix} <a href="{url}">{text}</a>'
