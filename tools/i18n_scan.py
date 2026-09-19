#!/usr/bin/env python3
"""Сбор русских текстов интерфейса — заготовка таблицы переводов.

Скрипт проходит по файлам сайта (кабинет, вход, админка, дайджест, терминал),
вытаскивает всё, что видит человек — текст между тегами, подписи полей,
подсказки, — и печатает список фраз, которые обязаны быть в
``static/i18n.pages.js`` (или в словарях ``static/i18n.js``).

Ключи вида ``data-i18n="…"`` сюда не попадают: они переводятся словарём.
Печатается ровно то, что собирается кодом (шаблоны строк), — по нему и
строится таблица фраз.

Запуск:
    python3 tools/i18n_scan.py            # все файлы, сводка
    python3 tools/i18n_scan.py --missing en   # чего не хватает английскому
    python3 tools/i18n_scan.py --app          # только кабинет/сервисы (account.js)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

CYR = re.compile(r"[А-Яа-яЁё]")
TAG = re.compile(r"<[^>]*>")
HTML_ATTR = re.compile(r'(?:placeholder|title|aria-label|alt)="([^"]*)"')
STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'', re.S)

#: файлы, где живёт интерфейс (в порядке важности)
SOURCES = (
    "static/account.js",
    "static/digest.js",
    "static/consent.js",
    "static/ads.js",
    "static/login.html",
    "static/cabinet.html",
    "static/reset.html",
    "static/admin.html",
    "static/digest.html",
    "static/index.html",
    "static/landing.html",
    "static/app.js",
)

#: фрагменты, которые переводить не нужно: разметка, эмодзи, числа
NOT_TEXT = re.compile(r"^[\s\W\d_]*$")


def string_literals(path: Path) -> list[str]:
    """Все строковые литералы файла: и в двойных, и в одинарных кавычках."""
    src = path.read_text(encoding="utf-8")
    out: list[str] = []
    for i, line in enumerate(src.split("\n"), start=1):
        st = line.strip()
        if st.startswith("//") or st.startswith("*") or st.startswith("/*"):
            continue                      # комментарии — не текст интерфейса
        code = re.sub(r"(?<!:)//.*$", "", line)
        for m in STRING.finditer(code):
            lit = m.group(1) if m.group(1) is not None else m.group(2)
            if lit:
                out.append(lit)
    return out


def text_units(raw: str) -> list[str]:
    """Куски текста из строки кода: без разметки, но с подписями полей."""
    units: list[str] = []
    for m in HTML_ATTR.finditer(raw):
        if CYR.search(m.group(1)):
            units.append(m.group(1).strip())
    text = TAG.sub("\n", raw.replace("\\n", "\n"))
    text = text.replace("\\'", "'").replace('\\"', '"')
    for chunk in re.split(r"[\n]|(?<=[.!?])\s+", text):
        chunk = chunk.strip()
        if not chunk or NOT_TEXT.match(chunk) or not CYR.search(chunk):
            continue
        units.append(chunk)
    return units


def collect(paths: list[str], skip_dict: bool = True) -> dict[str, list[str]]:
    """Фразы по файлам: {файл: [уникальные фразы по убыванию длины]}."""
    out: dict[str, list[str]] = {}
    for rel in paths:
        path = HERE / rel
        if not path.exists():
            continue
        seen: set[str] = set()
        for lit in string_literals(path):
            if skip_dict and _looks_like_dictionary(path, lit):
                continue
            for unit in text_units(lit):
                if len(unit) > 1:
                    seen.add(unit)
        out[rel] = sorted(seen, key=lambda s: (-len(s), s))
    return out


def _looks_like_dictionary(path: Path, lit: str) -> bool:
    """Строки самих словарей перевода не переводим: они уже переведены."""
    return path.name in ("i18n.js", "i18n.pages.js")


def load_pages() -> dict:
    """Словари страниц из static/i18n.pages.js (файл обязан быть строгим JSON)."""
    path = HERE / "static/i18n.pages.js"
    if not path.exists():
        return {}
    src = path.read_text(encoding="utf-8")
    body = src[src.index("=") + 1: src.rindex(";")]
    return json.loads(body)


def leftover(text: str, lang: str, pages: dict) -> str:
    """Что останется от русского текста после подстановки фраз — упрощённо.

    Точную проверку делает tests/i18n_coverage.js (там работает настоящий
    сопоставитель из i18n.js); здесь — быстрая прикидка по ключам словаря.
    """
    phrases = (pages.get(lang) or {}).get("phrases") or {}
    out = text
    for ru in sorted(phrases, key=len, reverse=True):
        out = out.replace(ru, phrases[ru])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Сбор фраз интерфейса для перевода")
    ap.add_argument("--missing", metavar="LANG", help="показать, чего нет в словаре")
    ap.add_argument("--app", action="store_true", help="только account.js (кабинет/сервисы)")
    ap.add_argument("--json", action="store_true", help="печатать JSON со фразами")
    args = ap.parse_args()

    paths = ["static/account.js"] if args.app else list(SOURCES)
    found = collect(paths)

    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0

    pages = load_pages()
    total = 0
    for rel, units in found.items():
        total += len(units)
        print(f"\n=== {rel}: {len(units)} фраз")
        if not args.missing:
            continue
        for unit in units:
            if CYR.search(leftover(unit, args.missing, pages)):
                print("   ?", unit)
    print(f"\nвсего фраз: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
