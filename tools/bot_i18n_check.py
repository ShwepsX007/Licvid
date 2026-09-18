"""Какие русские подписи бота ещё не переводятся на английский.

Тест ``tests/test_bot_i18n.py`` требует, чтобы переводились все подписи,
которые видит человек. Этот инструмент показывает список оставшихся — с ним
таблица ``bot_i18n.RU_EN`` дополняется без догадок.

    python3 tools/bot_i18n_check.py            # только пропуски
    python3 tools/bot_i18n_check.py --all      # все подписи и их перевод
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bot_i18n  # noqa: E402

#: модули, чьи тексты уходят пользователю через бота
MODULES = ("tg_bot.py", "alerts.py", "correlations.py")
CYR = re.compile(r"[А-Яа-яЁё]")
LOG_FUNCS = ("debug", "info", "warning", "error", "exception", "critical")


def literals(path: str) -> list:
    """Русские строки модуля без докстрингов и сообщений в лог.

    Докстринги и логи человек не видит: их переводить не нужно, а шум в
    проверке только мешал бы.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            text = ast.get_docstring(node, clean=False)
            if text:
                docs.add(text)
    log_args = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in LOG_FUNCS:
                for arg in node.args:
                    for sub in ast.walk(arg):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                            log_args.add(sub.value)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if not CYR.search(value) or value in docs or value in log_args:
                continue
            out.append(value)
    return sorted(set(out))


def main(argv=None, quiet: bool = False) -> int:
    """Код возврата: 0 — все русские подписи переводятся, 1 — есть пропуски.

    ``argv`` передаётся тестом (иначе argparse спорит с аргументами unittest),
    ``quiet`` убирает вывод — тесту нужен только код возврата.
    """
    ap = argparse.ArgumentParser(description="Проверка переводов бота")
    ap.add_argument("--all", action="store_true", help="показать и переведённые")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="вывести только итог (для тестов)")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    quiet = quiet or args.quiet
    missing_total = 0
    for name in MODULES:
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        texts = literals(path)
        missing = bot_i18n.strings_missing_en(texts)
        missing_total += len(missing)
        if not quiet:
            print(f"\n=== {name}: {len(texts)} подписей, без перевода {len(missing)} ===")
        if args.all:
            for text in texts:
                mark = "!!" if bot_i18n.strings_missing_en([text]) else "ok"
                print(f"  {mark} {text[:100]!r} -> {bot_i18n.translate(text, 'en')[:110]!r}")
        else:
            for text in missing:
                print(f"  {text[:160]!r}")
    if not quiet:
        print(f"\nитого без перевода: {missing_total}")
    return 1 if missing_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
