"""Письма LiqScope: подтверждение почты, вход по ссылке, сброс пароля.

Отправка — обычный SMTP из стандартной библиотеки (smtplib), поэтому
внешних зависимостей нет, а для тестов есть файловый транспорт.

Настройки берутся из окружения:

    LIQSCOPE_SMTP_HOST      хост SMTP (пусто — письма не отправляются)
    LIQSCOPE_SMTP_PORT      587 (STARTTLS) либо 465 (SSL)
    LIQSCOPE_SMTP_USER      логин
    LIQSCOPE_SMTP_PASSWORD  пароль / пароль приложения
    LIQSCOPE_SMTP_FROM      адрес отправителя ("LiqScope <no-reply@…>")
    LIQSCOPE_SMTP_TLS       starttls (по умолчанию) | ssl | none
    LIQSCOPE_SMTP_TIMEOUT   таймаут подключения, сек (по умолчанию 15)
    LIQSCOPE_SMTP_IPV4=1    слать только по IPv4 (по умолчанию — обычная
                            попытка, при сетевой ошибке повтор по IPv4)
    LIQSCOPE_MAIL_DIR       если задан — письма не уходят, а складываются
                            в папку (стенд/тесты)

Без LIQSCOPE_SMTP_HOST сервер всё равно работает: регистрация и вход
по почте живут, письмо просто не уходит. Ссылку администратор берёт из
журнала сервиса (строка «Ссылка для ручной выдачи»), а состояние отправок
видно в /api/admin/overview → mail.

Если у сервера сломан IPv6 (или хостинг его не даёт), первая попытка
отправки пройдёт по обычному пути, а при сетевой ошибке транспорт один раз
повторит её, используя только IPv4, и запомнит это для следующих писем.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import smtplib
import socket
import ssl
import threading
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("liqscope.mail")

BRAND = "LiqScope"
_TAG_RE = re.compile(r"<[^>]+>")


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _flag(key: str, default: bool = False) -> bool:
    v = _env(key).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def html_to_text(html: str) -> str:
    """Plain-text версия письма: ссылки видно, теги не мешают."""
    text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</\s*(p|div|tr|h[1-6])\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</\s*a>",
                  lambda m: f"{_TAG_RE.sub('', m.group(2))}: {m.group(1)}", text,
                  flags=re.I | re.S)
    text = _TAG_RE.sub("", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sender(raw: str) -> Tuple[str, str]:
    """«LiqScope <no-reply@liqscope.online>» → (имя, адрес)."""
    raw = (raw or "").strip() or "no-reply@liqscope.online"
    m = re.match(r"^\s*(.*?)\s*<\s*([^>]+)\s*>\s*$", raw)
    if m:
        return (m.group(1) or BRAND), m.group(2).strip()
    return BRAND, raw.strip()


def build_message(sender: str, to: str, subject: str, html: str,
                  text: str = "") -> MIMEMultipart:
    """Письмо text+HTML с правильными заголовками."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    name, addr = split_sender(sender)
    msg["From"] = formataddr((str(Header(name, "utf-8")), addr))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=addr.split("@")[-1] or "liqscope.online")
    msg.attach(MIMEText(text or html_to_text(html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def ipv4_address(host: str) -> str:
    """Первый IPv4-адрес хоста ("" — если A-записи нет)."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return ""
    for info in infos:
        return info[4][0]
    return ""


class _IPv4SMTP(smtplib.SMTP):
    """Как smtplib.SMTP, но подключается только по IPv4."""

    def _get_socket(self, host, port, timeout):
        return super()._get_socket(ipv4_address(host) or host, port, timeout)


class _IPv4SMTPSSL(smtplib.SMTP_SSL):
    """То же для SSL-порта: сертификат всё равно проверяется по имени хоста."""

    def _get_socket(self, host, port, timeout):
        return super()._get_socket(ipv4_address(host) or host, port, timeout)


@contextlib.contextmanager
def smtp_session(host: str, port: int, user: str, password: str, tls: str,
                 timeout: float, ipv4_only: bool = False):
    """Соединение с SMTP: TLS при необходимости и вход, если задан логин."""
    secure = (tls or "").lower() == "ssl"
    if secure:
        cls = _IPv4SMTPSSL if ipv4_only else smtplib.SMTP_SSL
        srv = cls(host, port, timeout=timeout,
                  context=ssl.create_default_context())
    else:
        cls = _IPv4SMTP if ipv4_only else smtplib.SMTP
        srv = cls(host, port, timeout=timeout)
    with srv:
        if not secure:
            srv.ehlo()
            if (tls or "").lower() not in ("none", "off", "plain"):
                try:
                    srv.starttls(context=ssl.create_default_context())
                    srv.ehlo()
                except smtplib.SMTPException as e:
                    log.warning("SMTP starttls не поднялся: %s", e)
        if user:
            srv.login(user, password)
        yield srv


def smtp_login(host: str, port: int, user: str, password: str, tls: str,
               timeout: float, ipv4_only: bool = False) -> None:
    """Только вход в SMTP, без письма (диагностика: tools/smtp_check.py)."""
    with smtp_session(host, port, user, password, tls, timeout, ipv4_only):
        pass


def smtp_deliver(host: str, port: int, user: str, password: str, tls: str,
                 timeout: float, msg, to: str, from_addr: str,
                 ipv4_only: bool = False) -> None:
    """Отправляет готовое письмо. Ошибки — исключениями."""
    with smtp_session(host, port, user, password, tls, timeout, ipv4_only) as srv:
        srv.sendmail(from_addr, [to], msg.as_string())


class SmtpTransport:
    """Реальная отправка по SMTP."""

    label = "SMTP"

    def __init__(self, host: str, port: int, user: str, password: str,
                 sender: str, tls: str = "starttls", timeout: float = 15.0,
                 ipv4: bool = False):
        self.host = host
        self.port = int(port or 25)
        self.user = user
        self.password = password
        self.sender = sender
        self.tls = (tls or "starttls").lower()
        self.timeout = float(timeout or 15)
        self.ipv4 = bool(ipv4)   # True — сразу шлём только по IPv4

    def send(self, to: str, subject: str, html: str, text: str = "") -> Tuple[bool, str]:
        msg = build_message(self.sender, to, subject, html, text)
        ok, err = self._attempt(msg, to, self.ipv4)
        if not ok and not self.ipv4 and ipv4_address(self.host):
            # Частая беда дешёвых/российских хостингов: IPv6-адрес есть, а IPv6
            # не ходит, и попытка заканчивается «Network is unreachable».
            log.info("%s: повторяю отправку для %s только по IPv4", self.label, to)
            ok, err2 = self._attempt(msg, to, True)
            if ok:
                self.ipv4 = True    # дальше сразу по IPv4, без лишней попытки
                return True, ""
            err = err2 or err
        if not ok:
            log.warning("%s: письмо для %s не ушло: %s", self.label, to, err)
            return False, str(err)[:200]
        return True, ""

    def _attempt(self, msg, to: str, ipv4: bool) -> Tuple[bool, Optional[Exception]]:
        try:
            smtp_deliver(self.host, self.port, self.user, self.password, self.tls,
                         self.timeout, msg, to, self._sender_parts()[1],
                         ipv4_only=ipv4)
            return True, None
        except Exception as e:   # сеть, авторизация, отказ сервера
            return False, e

    def _sender_parts(self) -> Tuple[str, str]:
        return split_sender(self.sender or self.user or "no-reply@liqscope.online")


class FileTransport:
    """Стенд/тесты: письмо кладём в файл, ничего наружу не отправляем."""

    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def send(self, to: str, subject: str, html: str, text: str = "") -> Tuple[bool, str]:
        name = f"{int(time.time() * 1000)}-{abs(hash(to)) % 100000}.eml"
        path = os.path.join(self.folder, name)
        body = (f"To: {to}\nSubject: {subject}\n\n"
                + (text or html_to_text(html)) + "\n\n--- HTML ---\n" + html)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        except OSError as e:
            return False, str(e)[:200]
        return True, ""


class Mailer:
    """Общая точка отправки: шаблоны, журнал отправок, сторож на ошибки."""

    def __init__(self, transport=None, sender: str = "", public_url: str = "",
                 enabled: Optional[bool] = None):
        self.transport = transport
        self.sender = sender
        self.public_url = (public_url or "").rstrip("/")
        self.enabled = bool(transport) if enabled is None else bool(enabled and transport)
        self._lock = threading.Lock()
        self.last: Dict[str, Any] = {}
        self.sent = 0
        self.failed = 0

    # ----- состояние ------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "smtp": bool(self.transport and isinstance(self.transport, SmtpTransport)),
            "from": self.sender,
            "sent": self.sent,
            "failed": self.failed,
            "last": dict(self.last) if self.last else None,
        }

    def link(self, path: str) -> str:
        base = self.public_url or "https://liqscope.online"
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    # ----- письма ---------------------------------------------------------
    def send(self, to: str, subject: str, html: str, text: str = "",
             kind: str = "") -> bool:
        to = (to or "").strip()
        if not to or "@" not in to:
            return False
        if not self.enabled or not self.transport:
            with self._lock:
                self.last = {"kind": kind, "to": to, "ok": False, "reason": "smtp_disabled",
                             "ts": time.time()}
            log.warning("SMTP не настроен (LIQSCOPE_SMTP_HOST) — письмо «%s» для %s "
                        "не отправлено", kind or subject, to)
            return False
        ok, err = self.transport.send(to, subject, html, text)
        with self._lock:
            if ok:
                self.sent += 1
            else:
                self.failed += 1
            self.last = {"kind": kind, "to": to, "ok": ok, "reason": err[:160],
                         "ts": time.time()}
        if ok:
            log.info("Письмо «%s» ушло на %s", kind or subject, to)
        return ok

    def send_verify(self, to: str, token: str, name: str = "") -> bool:
        url = self.link(f"/verify?token={token}")
        subject = f"{BRAND}: подтвердите почту"
        hello = f"Привет, {name}!" if name else "Привет!"
        html = self._wrap(subject, hello, [
            "Вы зарегистрировались на LiqScope — терминале ликвидаций крипто-фьючерсов.",
            "Нажмите кнопку ниже, чтобы подтвердить почту и открыть кабинет:",
            "Ссылка живёт 24 часа и одноразовая.",
        ], url, "Подтвердить почту",
            "Если вы не регистрировались, просто удалите письмо — адрес никто не увидит.")
        return self.send(to, subject, html, kind="verify")

    def send_login_link(self, to: str, token: str) -> bool:
        url = self.link(f"/email-login?token={token}")
        subject = f"{BRAND}: вход по ссылке"
        html = self._wrap(subject, "Вход без пароля", [
            "Вы просили войти в кабинет LiqScope без пароля. Жмите кнопку:",
            "Ссылка живёт 30 минут, одноразовая и работает в том же браузере,"
            " откуда вы её запросили.",
        ], url, "Войти в кабинет",
            "Если это были не вы — ничего делать не нужно, войти по ссылке без"
            " вашего клика нельзя.")
        return self.send(to, subject, html, kind="login")

    def send_reset(self, to: str, token: str) -> bool:
        url = self.link(f"/reset?token={token}")
        subject = f"{BRAND}: новый пароль"
        html = self._wrap(subject, "Сброс пароля", [
            "Вы запросили смену пароля в LiqScope. Жмите кнопку и задайте новый:",
            "Ссылка живёт час и одноразовая.",
        ], url, "Задать новый пароль",
            "Если запрос делали не вы — просто удалите письмо, пароль не изменится.")
        return self.send(to, subject, html, kind="reset")

    @staticmethod
    def _wrap(subject: str, hello: str, lines: List[str], url: str, button: str,
              footnote: str = "") -> str:
        body = "".join(f'<p style="margin:0 0 12px">{ln}</p>' for ln in lines)
        foot = (f'<p style="margin:18px 0 0;color:#8b98ad;font-size:12px">{footnote}</p>'
                if footnote else "")
        return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#0b0f16;color:#e8edf7;
 font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<div style="max-width:520px;margin:0 auto;background:#121826;border:1px solid #1b2536;
 border-radius:14px;padding:24px">
<div style="font-weight:800;letter-spacing:1px;color:#3d70ff;margin-bottom:14px">LIQSCOPE</div>
<h1 style="font-size:19px;margin:0 0 14px">{hello}</h1>
{body}
<p style="margin:18px 0"><a href="{url}" style="display:inline-block;background:#3d70ff;
 color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:700">{button}</a></p>
<p style="margin:0 0 6px;color:#8b98ad;font-size:12px">Если кнопка не работает, скопируйте ссылку:</p>
<p style="margin:0;word-break:break-all;font-size:12px;color:#9fb0c9">{url}</p>
{foot}
</div>
<p style="max-width:520px;margin:14px auto 0;color:#5f6b7f;font-size:11px;text-align:center">
 LiqScope — живой терминал ликвидаций крипто-фьючерсов</p>
</body></html>"""


def build_mailer(public_url: str = "") -> Mailer:
    """Собираем отправщик по окружению (пусто — письма не уходят)."""
    folder = _env("LIQSCOPE_MAIL_DIR")
    if folder:
        sender = _env("LIQSCOPE_SMTP_FROM", f"{BRAND} <no-reply@liqscope.online>")
        return Mailer(FileTransport(folder), sender=sender, public_url=public_url)
    host = _env("LIQSCOPE_SMTP_HOST")
    if not host:
        return Mailer(None, sender=_env("LIQSCOPE_SMTP_FROM",
                                        f"{BRAND} <no-reply@liqscope.online>"),
                      public_url=public_url, enabled=False)
    port = int(_env("LIQSCOPE_SMTP_PORT", "465" if _flag("LIQSCOPE_SMTP_SSL") else "587") or 587)
    tls = _env("LIQSCOPE_SMTP_TLS", "ssl" if _flag("LIQSCOPE_SMTP_SSL") else "starttls")
    user = _env("LIQSCOPE_SMTP_USER")
    # Яндекс, Mail.ru и Gmail отклоняют письмо, если отправитель не совпадает
    # с логином («Sender address not allowed»). Поэтому без LIQSCOPE_SMTP_FROM
    # шлём с того же адреса, под которым авторизуемся.
    sender = _env("LIQSCOPE_SMTP_FROM") or (f"{BRAND} <{user}>" if user
                                            else f"{BRAND} <no-reply@{host}>")
    transport = SmtpTransport(
        host=host, port=port, user=user,
        password=os.getenv("LIQSCOPE_SMTP_PASSWORD") or "",
        sender=sender,
        tls=tls, timeout=float(_env("LIQSCOPE_SMTP_TIMEOUT", "15") or 15),
        ipv4=_flag("LIQSCOPE_SMTP_IPV4"),
    )
    return Mailer(transport, sender=transport.sender, public_url=public_url, enabled=True)
