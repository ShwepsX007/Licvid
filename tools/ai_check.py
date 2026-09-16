"""Проверка ИИ-шапки: какие сервисы настроены и кто из них реально отвечает.

Запуск на сервере (от root — чтобы прочитать окружение юнита):

    python3 tools/ai_check.py            # проверить все настроенные сервисы
    python3 tools/ai_check.py --dry      # только показать настройки и промпт
    python3 tools/ai_check.py --unit licvid

Ключи никогда не печатаются целиком — только длина. Пример данных для
проверки — синтетический снимок рынка, поэтому лимиты бесплатных тарифов
почти не расходуются (по одному запросу на сервис).

Ключи (любые из них, порядок задаётся LIQSCOPE_AI_ORDER):

    LIQSCOPE_AI_GEMINI_KEY, LIQSCOPE_AI_GROQ_KEY, LIQSCOPE_AI_OPENROUTER_KEY,
    LIQSCOPE_AI_DEEPSEEK_KEY, LIQSCOPE_AI_KEY + LIQSCOPE_AI_URL + LIQSCOPE_AI_MODEL
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from typing import Dict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ai_text  # noqa: E402
from ai_text import AiWriter, build_providers  # noqa: E402

KEYS = ("LIQSCOPE_AI_GEMINI_KEY", "LIQSCOPE_AI_GROQ_KEY", "LIQSCOPE_AI_OPENROUTER_KEY",
        "LIQSCOPE_AI_DEEPSEEK_KEY", "LIQSCOPE_AI_KEY", "LIQSCOPE_AI_URL",
        "LIQSCOPE_AI_MODEL", "LIQSCOPE_AI_ORDER", "LIQSCOPE_AI_DISABLED",
        "LIQSCOPE_AI_TIMEOUT", "LIQSCOPE_AI_MAX_TOKENS",
        "LIQSCOPE_AI_GEMINI_MODEL", "LIQSCOPE_AI_GROQ_MODEL",
        "LIQSCOPE_AI_OPENROUTER_MODEL", "LIQSCOPE_AI_DEEPSEEK_MODEL")

SAMPLE = {
    "window_h": 4,
    "total_usd": 12_400_000, "longs_usd": 9_000_000, "shorts_usd": 3_400_000,
    "count": 812,
    "top_coins": [{"symbol": "BTC", "usd": 5_000_000, "longs": 4_200_000, "shorts": 800_000},
                  {"symbol": "ETH", "usd": 3_100_000, "longs": 2_600_000, "shorts": 500_000}],
    "exchanges": {"binance": 7_000_000, "bybit": 3_000_000},
    "biggest": {"symbol": "SOL", "usd": 900_000, "exchange": "binance"},
    "oi": {"BTC": {"changes": {"h1": {"pct": 2.4}}}},
    "cvd": {"BTC": 3_100_000.0},
}


def systemd_env(unit: str) -> Dict[str, str]:
    """Окружение юнита в том виде, в каком его видит процесс сервиса."""
    try:
        out = subprocess.run(["systemctl", "show", unit, "-p", "Environment"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {}
    if not out.startswith("Environment="):
        return {}
    env: Dict[str, str] = {}
    for item in shlex.split(out[len("Environment="):]):
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    return env


def masked_env(env: Dict[str, str], providers) -> str:
    lines = []
    for p in providers:
        lines.append(f"  {p.name:11s} модель: {p.model}")
    for name in ("gemini", "groq", "openrouter", "deepseek"):
        key = env.get(f"LIQSCOPE_AI_{name.upper()}_KEY") or ""
        if key:
            lines.append(f"  ключ {name:11s} {len(key)} символов")
    if env.get("LIQSCOPE_AI_URL"):
        lines.append(f"  свой сервис: {env['LIQSCOPE_AI_URL']} "
                     f"(ключ {len(env.get('LIQSCOPE_AI_KEY') or '')} символов)")
    if env.get("LIQSCOPE_AI_DISABLED"):
        lines.append("  ! LIQSCOPE_AI_DISABLED задан — генерация выключена")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка ИИ-шапки для LiqScope")
    ap.add_argument("--unit", default="licvid", help="имя systemd-юнита (licvid)")
    ap.add_argument("--no-systemd", action="store_true", help="не читать окружение юнита")
    ap.add_argument("--dry", action="store_true",
                    help="только показать настройки и промпт, без запросов")
    ap.add_argument("--models", action="store_true",
                    help="спросить у сервисов список моделей и показать, какая подойдёт")
    args = ap.parse_args()

    env = {k: os.environ[k] for k in KEYS if os.environ.get(k)}
    source = "окружение процесса"
    if not args.no_systemd and not any(k.startswith("LIQSCOPE_AI") for k in env):
        unit_env = systemd_env(args.unit)
        if unit_env:
            env = unit_env
            source = f"systemd-юнит {args.unit}"
    for k, v in env.items():
        os.environ[k] = v

    providers = build_providers()
    print(f"настройки: {source}")
    if not providers:
        print("  ИИ-шапки выключены: ни один ключ не задан — посты уходят "
              "с шапками из шаблонов.")
        print("  Добавьте ключи (Gemini/Groq/OpenRouter — бесплатные тарифы) "
              "и перезапустите сервис.")
        return 1
    print(masked_env(env, providers))
    print("\nвыжимка для модели:")
    for line in ai_text.summarize(SAMPLE):
        print(f"  — {line}")
    if args.dry:
        print("\nпромпт целиком:\n")
        print(ai_text.build_prompt(SAMPLE))
        return 0
    if args.models:
        print("\nдоступные модели (и та, которую выберет код):")
        timeout = float(env.get("LIQSCOPE_AI_TIMEOUT", "15") or 15)
        for p_ in providers:
            ids = ai_text.list_models(p_, timeout)
            if not ids:
                print(f"  {p_.name}: список не получен (сеть или ключ) — "
                      f"остаётся {p_.model}")
                continue
            best = ai_text.pick_model(p_.name, ids) or "—"
            print(f"  {p_.name}: {len(ids)} шт., код выберет {best}")
            print(f"     первые: {', '.join(ids[:6])}")
        return 0

    ok_any = False
    print("\nпроверяю сервисы по очереди:")
    for p in providers:
        writer = AiWriter([p], timeout=float(env.get("LIQSCOPE_AI_TIMEOUT", "15") or 15))
        head = writer.headline_sync(SAMPLE)
        st = writer.status()["providers"][0]
        if head:
            ok_any = True
            print(f"  ✓ {p.name} ({p.model}) · {st['ms']} мс\n     «{head}»")
        else:
            hint = ai_text.ai_error_hint(st.get("reason") or "")
            print(f"  ✗ {p.name} ({p.model}): {st['reason']}")
            if hint:
                print(f"     → {hint}")
    print()
    if ok_any:
        print("Итог: есть рабочие сервисы — сводку подпишет ИИ. Остальные "
              "подхватятся автоматически, если этот упадёт.")
        return 0
    print("Итог: ни один сервис не ответил. Посты уйдут с шапками из шаблонов, "
          "ничего не сломается. Тексты ошибок выше — в журнал сервиса они "
          "попадают так же (journalctl -u licvid | grep -i \"ИИ-шапка\").")
    return 1


if __name__ == "__main__":
    sys.exit(main())
