"""Проверка почты LiqScope: настройки, сеть и настоящая отправка письма.

Запуск на сервере (от root — чтобы прочитать окружение юнита):

    python3 tools/smtp_check.py                   # вход в SMTP по настройкам сервиса
    python3 tools/smtp_check.py terarasa@yandex.ru  # плюс тестовое письмо
    python3 tools/smtp_check.py --ports           # доступны ли SMTP-порты вообще

Настройки берутся по порядку: переменные окружения процесса → `systemctl show
<unit> -p Environment` (то, с чем реально работает сервис).

Пароль печатается только по длине: у Яндекса пароль приложения — 16 символов,
и если в файле оказался пароль от почты, это видно сразу.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import socket
import subprocess
import sys
import urllib.parse
from typing import Dict, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

KEYS = ("LIQSCOPE_SMTP_HOST", "LIQSCOPE_SMTP_PORT", "LIQSCOPE_SMTP_USER",
        "LIQSCOPE_SMTP_PASSWORD", "LIQSCOPE_SMTP_FROM", "LIQSCOPE_SMTP_TLS",
        "LIQSCOPE_SMTP_TIMEOUT", "LIQSCOPE_SMTP_IPV4", "LIQSCOPE_MAIL_DIR",
        "LIQSCOPE_MAIL_API", "LIQSCOPE_MAIL_API_KEY", "LIQSCOPE_MAIL_API_SECRET",
        "LIQSCOPE_MAIL_API_URL", "LIQSCOPE_MAIL_API_FROM", "LIQSCOPE_PUBLIC_URL")
PORTS = (465, 587, 25, 2525)
CONTROL = ("ya.ru", 443)          # обычный HTTPS: проверка, что интернет вообще есть


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


def api_lines(env: Dict[str, str]) -> str:
    """Строки про режим HTTPS-API (когда SMTP-порты у хостинга закрыты)."""
    kind = (env.get("LIQSCOPE_MAIL_API") or "").strip().lower()
    if not kind:
        return ""
    key = env.get("LIQSCOPE_MAIL_API_KEY") or ""
    secret = env.get("LIQSCOPE_MAIL_API_SECRET") or ""
    return ("\n".join([
        f"  отправка: HTTPS-API «{kind}» (порт 443, SMTP не нужен)",
        f"  ключ:    {len(key)} символов" if key else "  ключ:    НЕ ЗАДАН",
        f"  секрет:  {len(secret)} символов" if secret else "  секрет:  —",
        f"  адрес API: {env.get('LIQSCOPE_MAIL_API_URL') or '(по умолчанию сервиса)'}",
    ]))


def masked(env: Dict[str, str]) -> str:
    password = env.get("LIQSCOPE_SMTP_PASSWORD") or ""
    hint = f"{len(password)} символов" if password else "НЕ ЗАДАН"
    return "\n".join([*[
        f"  хост:    {env.get('LIQSCOPE_SMTP_HOST') or '—'}",
        f"  порт:    {env.get('LIQSCOPE_SMTP_PORT') or '—'}",
        f"  шифрование: {env.get('LIQSCOPE_SMTP_TLS') or '(по умолчанию starttls)'}",
        f"  логин:   {env.get('LIQSCOPE_SMTP_USER') or '—'}",
        f"  пароль:  {hint}",
        f"  отправитель: {env.get('LIQSCOPE_SMTP_FROM') or '(не задан — берём логин)'}",
        f"  только IPv4: {env.get('LIQSCOPE_SMTP_IPV4') or 'нет (авто: при сбое повторим по IPv4)'}",
        f"  LIQSCOPE_MAIL_DIR: {env.get('LIQSCOPE_MAIL_DIR') or '—'}",
    ], api_lines(env)])


def tcp_test(host: str, port: int, family: int = 0, timeout: float = 5) -> Tuple[bool, str]:
    """Пробуем открыть TCP-соединение; возвращаем (получилось, причина)."""
    try:
        infos = socket.getaddrinfo(host, port, family or socket.AF_UNSPEC,
                                   socket.SOCK_STREAM)
    except OSError as e:
        return False, f"DNS: {e}"
    if not infos:
        return False, "нет адреса"
    err = ""
    for fam, stype, proto, _canon, addr in infos:
        sock = socket.socket(fam, stype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(addr)
            sock.close()
            return True, ""
        except OSError as e:
            err = str(e)
        finally:
            sock.close()
    return False, err


def net_report(host: str, ports=PORTS) -> bool:
    """Какие адреса у хоста и какие SMTP-порты реально открыты."""
    print("  — сеть —")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        print(f"  DNS: {host} не разобрался ({e})")
        return False
    v4 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET})
    v6 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET6})
    print(f"  адреса: IPv4 {', '.join(v4) or '—'}; IPv6 {', '.join(v6) or '—'}")
    opened = []
    for port in ports:
        ok4, why4 = tcp_test(host, port, socket.AF_INET) if v4 else (False, "нет IPv4")
        ok6, why6 = tcp_test(host, port, socket.AF_INET6) if v6 else (False, "нет IPv6")
        if ok4 or ok6:
            opened.append(port)
        mark = "открыт" if (ok4 or ok6) else "ЗАКРЫТ"
        detail = []
        if v4:
            detail.append(f"IPv4: {'ок' if ok4 else why4}")
        if v6:
            detail.append(f"IPv6: {'ок' if ok6 else why6}")
        print(f"  порт {port}: {mark} ({'; '.join(detail) or 'адресов нет'})")
    ok_https, why = tcp_test(*CONTROL, family=socket.AF_INET)
    print(f"  контроль {CONTROL[0]}:{CONTROL[1]} (обычный HTTPS): "
          f"{'открыт' if ok_https else 'НЕДОСТУПЕН — ' + why}")
    if opened:
        print(f"  → открыты порты {', '.join(map(str, opened))} — на них и надо "
              "настраивать отправку")
    elif ok_https:
        print("  → Все SMTP-порты закрыты, а HTTPS работает: хостинг блокирует\n"
              "    исходящую почту. Варианты:\n"
              "      1) заявка в поддержку хостинга — открыть 465 и 587;\n"
              "      2) слать через HTTP-API сервиса рассылок (порт 443) —\n"
              "         транспорт сделаю, нужен только выбор сервиса и ключ.")
    else:
        print("  → Похоже, у сервера вообще нет выхода в интернет.")
    return bool(opened)


def sender_address(env: Dict[str, str]) -> str:
    raw = env.get("LIQSCOPE_SMTP_FROM") or ""
    if not raw:
        return env.get("LIQSCOPE_SMTP_USER") or ""
    m = re.match(r"^\s*(.*?)\s*<\s*([^>]+)\s*>\s*$", raw)
    return (m.group(2) if m else raw).strip()


def check_sender(env: Dict[str, str]) -> bool:
    """Яндекс (и Gmail) отклоняют письмо, если From не совпадает с логином."""
    from mailer import sender_hint

    raw = env.get("LIQSCOPE_SMTP_FROM") or env.get("LIQSCOPE_MAIL_API_FROM") or ""
    hint = sender_hint(raw)
    if hint:
        print(f"  ! {hint}")
        return False
    user = (env.get("LIQSCOPE_SMTP_USER") or "").strip().lower()
    sender = sender_address(env).lower()
    if user and sender and sender != user:
        print(f"  ! отправитель «{sender}» не совпадает с логином «{user}» —\n"
              "    Яндекс такое письмо отклонит: уберите LIQSCOPE_SMTP_FROM\n"
              "    или поставьте в него свой адрес логина")
        return False
    return True


def hint_for(err: str) -> str:
    e = err.lower()
    if "535" in e or "authentication failed" in e or "invalid user or password" in e:
        return ("    → Яндекс не принял логин/пароль. Нужен ИМЕННО пароль приложения\n"
                "      (id.yandex.ru/security/app-passwords, тип «Почта»), а не пароль\n"
                "      от почты. Проверьте длину: у Яндекса их 16 символов.")
    if "sender" in e or "553" in e or "501" in e or "not owned by user" in e:
        return ("    → Адрес отправителя: у Яндекса From обязан совпадать с логином.\n"
                "      Уберите LIQSCOPE_SMTP_FROM или заключите значение в кавычки:\n"
                "      Environment=\"LIQSCOPE_SMTP_FROM=LiqScope <you@yandex.ru>\"")
    if "network is unreachable" in e or "reset by peer" in e or "timed out" in e:
        return ("    → Сеть: запустите  python3 tools/smtp_check.py --ports  — он\n"
                "      покажет, какие порты открыты. Если все закрыты, поможет только\n"
                "      заявка в поддержку хостинга или отправка через HTTP-API (443).")
    if "certificate" in e or "ssl" in e:
        return "    → TLS: проверьте пару порт/режим (465 = ssl, 587 = starttls)."
    return "    → Полный ответ SMTP смотрите в journalctl -u licvid | grep SMTP"


def check_login(env: Dict[str, str]) -> bool:
    from mailer import ipv4_address, smtp_login

    host = env.get("LIQSCOPE_SMTP_HOST") or ""
    if not host:
        print("  ✗ LIQSCOPE_SMTP_HOST не задан — письма не настроены вовсе")
        return False
    user = env.get("LIQSCOPE_SMTP_USER") or ""
    password = env.get("LIQSCOPE_SMTP_PASSWORD") or ""
    tls = (env.get("LIQSCOPE_SMTP_TLS") or "starttls").lower()
    port = int(env.get("LIQSCOPE_SMTP_PORT") or (465 if tls == "ssl" else 587))
    timeout = float(env.get("LIQSCOPE_SMTP_TIMEOUT") or 15)
    has_v4 = bool(ipv4_address(host))
    attempts = [False] + ([True] if has_v4 else [])
    err: Exception = RuntimeError("нет попыток")
    for ipv4_only in attempts:
        label = "только IPv4" if ipv4_only else "обычная"
        print(f"  подключаюсь к {host}:{port} ({tls}, {label})…")
        try:
            smtp_login(host, port, user, password, tls, timeout, ipv4_only=ipv4_only)
        except Exception as e:                     # сеть, авторизация, отказ
            err = e
            print(f"  ✗ {label}: {e}")
            continue
        print("  ✓ SMTP-вход прошёл" + (" (по IPv4)" if ipv4_only else "")
              + " — сервер принял логин и пароль")
        return True
    print(hint_for(str(err)))
    return False


def api_mode(env: Dict[str, str], to: str = "") -> int:
    """Режим HTTPS-API: проверяем 443 и, если попросили, шлём тестовое письмо."""
    from mailer import API_URLS, build_mailer

    kind = (env.get("LIQSCOPE_MAIL_API") or "").strip().lower()
    url = env.get("LIQSCOPE_MAIL_API_URL") or API_URLS.get(kind) or API_URLS["resend"]
    host = urllib.parse.urlparse(url).hostname or ""
    if not (env.get("LIQSCOPE_MAIL_API_KEY") or "").strip():
        print("  ✗ LIQSCOPE_MAIL_API_KEY не задан — письма не уйдут")
        return 1
    from mailer import sender_hint
    hint = sender_hint(env.get("LIQSCOPE_SMTP_FROM")
                       or env.get("LIQSCOPE_MAIL_API_FROM") or "")
    if hint:
        print(f"  ! {hint}")
    print(f"  адрес:   {url}")
    ok, why = tcp_test(host, 443, socket.AF_INET)
    print(f"  порт 443 у {host}: " + ("открыт" if ok else f"НЕДОСТУПЕН — {why}"))
    if not ok:
        print("  → Серверу нужен обычный HTTPS на 443: если и он закрыт, "
              "вопрос к хостингу")
        return 1
    if not to:
        print("  (добавьте адрес аргументом, чтобы получить тестовое письмо)")
        return 0
    for k, v in env.items():
        os.environ[k] = v
    mailer = build_mailer(env.get("LIQSCOPE_PUBLIC_URL") or "https://liqscope.online")
    sent = mailer.send(to, "LiqScope: проверка почты",
                       "<p>Это тестовое письмо от LiqScope. Если вы его видите — "
                       "отправка через API настроена и работает.</p>")
    print("  тестовое письмо:", "отправлено" if sent else "НЕ отправлено")
    if sent:
        return 0
    print("  причина:", (mailer.status().get("last") or {}).get("reason") or "—")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка SMTP для LiqScope")
    ap.add_argument("to", nargs="?", help="кому отправить тестовое письмо")
    ap.add_argument("--unit", default="licvid", help="имя systemd-юнита (licvid)")
    ap.add_argument("--no-systemd", action="store_true", help="не читать окружение юнита")
    ap.add_argument("--ports", action="store_true",
                    help="только сеть: DNS и доступность SMTP-портов")
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
    host = env.get("LIQSCOPE_SMTP_HOST") or "smtp.yandex.ru"
    if (env.get("LIQSCOPE_MAIL_API") or "").strip() and not args.ports:
        return api_mode(env, args.to or "")
    if args.ports:
        net_report(host)
        return 0
    if env.get("LIQSCOPE_MAIL_DIR"):
        print("  ! задан LIQSCOPE_MAIL_DIR — сервер складывает письма в файлы, "
              "по сети ничего не уходит")
    env.pop("LIQSCOPE_MAIL_DIR", None)      # здесь нужен настоящий SMTP

    check_sender(env)
    ok = check_login(env)
    if not ok:
        net_report(host)
        return 1
    if args.to:
        from mailer import build_mailer
        for k, v in env.items():
            os.environ[k] = v
        mailer = build_mailer(env.get("LIQSCOPE_PUBLIC_URL") or "https://liqscope.online")
        sent = mailer.send(args.to, "LiqScope: проверка почты",
                           "<p>Это тестовое письмо от LiqScope. Если вы его видите — "
                           "отправка почты настроена и работает.</p>")
        print("  тестовое письмо:", "отправлено" if sent else "НЕ отправлено")
        if not sent:
            last = mailer.status().get("last") or {}
            print("  причина:", last.get("reason") or "—")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
