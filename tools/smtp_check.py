"""Проверка почты: те же настройки, что у сервиса, и настоящая отправка письма.

Запуск на сервере (root — чтобы прочитать окружение юнита):

    python3 tools/smtp_check.py                      # только проверка входа в SMTP
    python3 tools/smtp_check.py terarasa@yandex.ru   # плюс тестовое письмо

Настройки берутся в таком порядке:
  1) переменные окружения самого процесса (если заданы в оболочке);
  2) `systemctl show <unit> -p Environment` — то, с чем реально работает сервис
     (имя юнита: --unit, по умолчанию licvid).

Скрипт никогда не печатает пароль целиком — только длину, чтобы было видно, что
это пароль приложения (у Яндекса их 16 символов), а не пароль от почты.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import smtplib
import ssl
import subprocess
import sys
from typing import Dict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

KEYS = ("LIQSCOPE_SMTP_HOST", "LIQSCOPE_SMTP_PORT", "LIQSCOPE_SMTP_USER",
        "LIQSCOPE_SMTP_PASSWORD", "LIQSCOPE_SMTP_FROM", "LIQSCOPE_SMTP_TLS",
        "LIQSCOPE_SMTP_TIMEOUT", "LIQSCOPE_MAIL_DIR")


def systemd_env(unit: str) -> Dict[str, str]:
    """Окружение юнита в том виде, в каком его видит процесс."""
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


def masked(env: Dict[str, str]) -> str:
    password = env.get("LIQSCOPE_SMTP_PASSWORD") or ""
    hint = f"{len(password)} символов" if password else "НЕ ЗАДАН"
    sender = env.get("LIQSCOPE_SMTP_FROM") or "(не задан — отправитель = логин)"
    return "\n".join([
        f"  хост:    {env.get('LIQSCOPE_SMTP_HOST') or '—'}",
        f"  порт:    {env.get('LIQSCOPE_SMTP_PORT') or '—'}",
        f"  шифрование: {env.get('LIQSCOPE_SMTP_TLS') or '(по умолчанию starttls)'}",
        f"  логин:   {env.get('LIQSCOPE_SMTP_USER') or '—'}",
        f"  пароль:  {hint}",
        f"  отправитель: {sender}",
        f"  LIQSCOPE_MAIL_DIR: {env.get('LIQSCOPE_MAIL_DIR') or '—'}",
    ])


def sender_address(env: Dict[str, str]) -> str:
    """Адрес отправителя: то, что подставит mailer, если From не задан."""
    raw = env.get("LIQSCOPE_SMTP_FROM") or ""
    if not raw:
        user = env.get("LIQSCOPE_SMTP_USER") or ""
        return user
    m = re.match(r"^\s*(.*?)\s*<\s*([^>]+)\s*>\s*$", raw)
    return (m.group(2) if m else raw).strip()


def check_sender(env: Dict[str, str]) -> bool:
    """Яндекс (и Gmail) отклоняют письмо, если From не совпадает с логином."""
    user = (env.get("LIQSCOPE_SMTP_USER") or "").strip().lower()
    sender = sender_address(env).lower()
    if user and sender and sender != user:
        print(f"  ! отправитель «{sender}» не совпадает с логином «{user}» —"
              " Яндекс такое письмо отклонит")
        print("    уберите LIQSCOPE_SMTP_FROM или поставьте свой адрес логина")
        return False
    return True


def check_login(env: Dict[str, str]) -> bool:
    host = env.get("LIQSCOPE_SMTP_HOST") or ""
    user = env.get("LIQSCOPE_SMTP_USER") or ""
    password = env.get("LIQSCOPE_SMTP_PASSWORD") or ""
    port = int(env.get("LIQSCOPE_SMTP_PORT") or (465 if (env.get("LIQSCOPE_SMTP_TLS") == "ssl") else 587))
    tls = (env.get("LIQSCOPE_SMTP_TLS") or "starttls").lower()
    timeout = float(env.get("LIQSCOPE_SMTP_TIMEOUT") or 15)
    if not host:
        print("  ✗ LIQSCOPE_SMTP_HOST не задан — письма не настроены вовсе")
        return False
    print(f"  подключаюсь к {host}:{port} ({tls})…")
    try:
        if tls == "ssl":
            with smtplib.SMTP_SSL(host, port, timeout=timeout,
                                  context=ssl.create_default_context()) as srv:
                srv.login(user, password)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as srv:
                srv.ehlo()
                if tls not in ("none", "off", "plain"):
                    srv.starttls(context=ssl.create_default_context())
                    srv.ehlo()
                srv.login(user, password)
    except Exception as e:
        print("  ✗ SMTP-вход не прошёл:", e)
        print(hint_for(str(e)))
        return False
    print("  ✓ SMTP-вход прошёл — сервер принял логин и пароль приложения")
    return True


def hint_for(err: str) -> str:
    e = err.lower()
    if "535" in e or "authentication failed" in e or "invalid user or password" in e:
        return ("    → Яндекс не принял логин/пароль. Нужен ИМЕННО пароль приложения\n"
                "      (id.yandex.ru/security/app-passwords, тип «Почта»), а не пароль от почты.\n"
                "      Проверьте длину: у Яндекса пароль приложения — 16 символов.")
    if "sender" in e or "553" in e or "501" in e or "not owned by user" in e:
        return ("    → Проблема с адресом отправителя: у Яндекса From обязан совпадать\n"
                "      с логином. Уберите LIQSCOPE_SMTP_FROM или заключите значение\n"
                "      в кавычки: Environment=\"LIQSCOPE_SMTP_FROM=LiqScope <you@yandex.ru>\"")
    if "timed out" in e or "timeout" in e:
        return ("    → Похоже, провайдер закрывает 465. Попробуйте 587:\n"
                "      Environment=LIQSCOPE_SMTP_PORT=587\n"
                "      Environment=LIQSCOPE_SMTP_TLS=starttls")
    if "connection refused" in e or "network is unreachable" in e:
        return "    → SMTP-порт недоступен из этого дата-центра (частая практика хостингов)."
    if "reset by peer" in e or "unexpected eof" in e:
        return ("    → Соединение на 465 обрывают: либо провайдер режет SMTP-порты,\n"
                "      либо это SSL-перехват. Попробуйте 587 + starttls:\n"
                "      Environment=LIQSCOPE_SMTP_PORT=587\n"
                "      Environment=LIQSCOPE_SMTP_TLS=starttls")
    if "certificate" in e or "ssl" in e:
        return "    → Ошибка TLS: проверьте порт/режим (465 = ssl, 587 = starttls)."
    return "    → Полный ответ сервера смотрите в journalctl -u licvid | grep SMTP"


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка SMTP для LiqScope")
    ap.add_argument("to", nargs="?", help="кому отправить тестовое письмо")
    ap.add_argument("--unit", default="licvid", help="имя systemd-юнита (licvid)")
    ap.add_argument("--no-systemd", action="store_true", help="не читать окружение юнита")
    args = ap.parse_args()

    env = {k: os.environ[k] for k in KEYS if os.environ.get(k)}
    source = "окружение процесса"
    if not args.no_systemd and not env.get("LIQSCOPE_SMTP_HOST"):
        unit_env = systemd_env(args.unit)
        if unit_env:
            env = unit_env
            source = f"systemd-юнит {args.unit}"
    print(f"настройки: {source}")
    print(masked(env))
    if env.get("LIQSCOPE_MAIL_DIR"):
        print("  ! задан LIQSCOPE_MAIL_DIR — сервер складывает письма в файлы, а не шлёт")
    env.pop("LIQSCOPE_MAIL_DIR", None)   # здесь нужен настоящий SMTP

    check_sender(env)
    ok = check_login(env)
    if ok and args.to:
        from mailer import build_mailer
        for k, v in env.items():
            os.environ[k] = v
        mailer = build_mailer("https://liqscope.online")
        sent = mailer.send(
            args.to, "LiqScope: проверка почты",
            "<p>Это тестовое письмо от LiqScope. Если вы его видите — отправка "
            "почты настроена и работает.</p>")
        print("  тестовое письмо:", "отправлено" if sent else "НЕ отправлено")
        if not sent:
            last = mailer.status().get("last") or {}
            print("  причина:", last.get("reason") or "—")
            return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
