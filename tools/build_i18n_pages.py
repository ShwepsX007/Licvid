#!/usr/bin/env python3
"""Сборка браузерного словаря страниц из JSON.

Источник — ``static/i18n/<язык>.json`` (по-русски править их удобнее: видно
разницу между языками). Браузеру же нужен один файл, который подключается
обычным <script> и успевает примениться до отрисовки: собираем его сюда.

    python3 tools/build_i18n_pages.py            # пересобрать static/i18n.pages.js
    python3 tools/build_i18n_pages.py --check    # только проверить (код 1, если устарел)

Тест ``tests/test_site_i18n.py`` сверяет собранный файл с JSON и падает, если
их забыли синхронизировать.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "static" / "i18n"
OUT = HERE / "static" / "i18n.pages.js"

LANGS = ("ru", "en", "zh", "hi", "es")

HEADER = """/**
 * LiqScope — словари страниц: кабинет, вход/регистрация, сброс пароля,
 * дайджест, админка и всё, что рисуют сервисы.
 *
 * Файл СГЕНЕРИРОВАН из static/i18n/<язык>.json — правьте JSON, а потом
 * пересоберите:  python3 tools/build_i18n_pages.py
 *
 * Разметка: window.LIQSCOPE_I18N_PAGES = {язык: {keys, phrases}}.
 *   keys    — ключи для атрибутов data-i18n (их же читает сервер: SSR);
 *   phrases — русский фрагмент → перевод, для живого текста (см. i18n.js).
 */
window.LIQSCOPE_I18N_PAGES =
"""


def build() -> str:
    data = {}
    for lang in LANGS:
        path = SRC / f"{lang}.json"
        if not path.exists():
            raise SystemExit(f"нет словаря: {path}")
        data[lang] = json.loads(path.read_text(encoding="utf-8"))
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    return HEADER + body + ";\n"


def build_per_lang() -> dict:
    """Словарь lang -> текст файла только с этим языком."""
    out = {}
    for lang in LANGS:
        path = SRC / f"{lang}.json"
        if not path.exists():
            raise SystemExit(f"нет словаря: {path}")
        data = {lang: json.loads(path.read_text(encoding="utf-8"))}
        body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
        out[lang] = HEADER + body + ";\n"
    return out


def main() -> int:
    check = "--check" in sys.argv[1:]
    text = build()
    per_lang = build_per_lang()
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if check:
        ok = True
        if old != text:
            print("static/i18n.pages.js: УСТАРЕЛ — запустите python3 tools/build_i18n_pages.py")
            ok = False
        else:
            print(f"static/i18n.pages.js: синхронизирован ({len(text)} байт)")
        # Проверяем и per-lang файлы
        for lang, content in per_lang.items():
            p = HERE / "static" / f"i18n.pages.{lang}.js"
            old_pl = p.read_text(encoding="utf-8") if p.exists() else ""
            if old_pl != content:
                print(f"static/i18n.pages.{lang}.js: УСТАРЕЛ")
                ok = False
            else:
                print(f"static/i18n.pages.{lang}.js: синхронизирован ({len(content)} байт)")
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8")
    state = "без изменений" if old == text else "обновлён"
    print(f"static/i18n.pages.js: {state} ({len(text)} байт, {len(LANGS)} языка)")
    for lang, content in per_lang.items():
        p = HERE / "static" / f"i18n.pages.{lang}.js"
        old_pl = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(content, encoding="utf-8")
        st = "без изменений" if old_pl == content else "обновлён"
        print(f"static/i18n.pages.{lang}.js: {st} ({len(content)} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
