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

Когда хостинг закрывает исходящие SMTP-порты (25/465/587) — обычная история
для VPS — остаётся отправка через HTTPS-API сервиса рассылок:

    LIQSCOPE_MAIL_API        resend | sendpulse | generic
    LIQSCOPE_MAIL_API_KEY    ключ сервиса (у SendPulse — ID)
    LIQSCOPE_MAIL_API_SECRET секрет (у SendPulse — Secret)
    LIQSCOPE_MAIL_API_URL    свой адрес для generic (по умолчанию — Resend)
    LIQSCOPE_SMTP_FROM       адрес отправителя, подтверждённый в сервисе
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import smtplib
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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


def sender_hint(raw: str) -> str:
    """Пусто, если отправитель — нормальный адрес. Иначе объясняем, что не так.

    Самая частая беда: значение записано в systemd без кавычек, и всё, что после
    пробела, отбрасывается — «LiqScope <box@site.ru>» превращается в «LiqScope».
    """
    _, addr = split_sender(raw)
    if "@" in addr:
        return ""
    return (f"отправитель «{raw}» — это имя без адреса: в systemd значение "
            "обрезалось по пробелу. Нужны кавычки: "
            'Environment="LIQSCOPE_SMTP_FROM=LiqScope <box@site.ru>", '
            "либо просто адрес без имени: LIQSCOPE_SMTP_FROM=box@site.ru")


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
        sender = self.sender
        problem = sender_hint(sender)
        if problem and "@" in (self.user or ""):
            log.warning("SMTP: %s — письмо пойдёт с логина %s", problem, self.user)
            sender = self.user
        msg = build_message(sender, to, subject, html, text)
        from_addr = split_sender(sender)[1]
        ok, err = self._attempt(msg, to, self.ipv4, from_addr)
        if not ok and not self.ipv4 and ipv4_address(self.host):
            # Частая беда дешёвых/российских хостингов: IPv6-адрес есть, а IPv6
            # не ходит, и попытка заканчивается «Network is unreachable».
            log.info("%s: повторяю отправку для %s только по IPv4", self.label, to)
            ok, err2 = self._attempt(msg, to, True, from_addr)
            if ok:
                self.ipv4 = True    # дальше сразу по IPv4, без лишней попытки
                return True, ""
            err = err2 or err
        if not ok:
            log.warning("%s: письмо для %s не ушло: %s", self.label, to, err)
            return False, str(err)[:200]
        return True, ""

    def _attempt(self, msg, to: str, ipv4: bool,
                 from_addr: str) -> Tuple[bool, Optional[Exception]]:
        try:
            smtp_deliver(self.host, self.port, self.user, self.password, self.tls,
                         self.timeout, msg, to, from_addr, ipv4_only=ipv4)
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
        name = f"{int(time.time() * 1000)}-{abs(hash(to)) % 100000}"
        path = os.path.join(self.folder, name + ".eml")
        # имена совпадают, если два письма ушли одному адресу в одну
        # миллисекунду: дописываем счётчик, чтобы стенд не терял письма
        seq = 1
        while os.path.exists(path):
            seq += 1
            path = os.path.join(self.folder, f"{name}-{seq}.eml")
        body = (f"To: {to}\nSubject: {subject}\n\n"
                + (text or html_to_text(html)) + "\n\n--- HTML ---\n" + html)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        except OSError as e:
            return False, str(e)[:200]
        return True, ""


def http_post_json(url: str, payload: Dict[str, Any],
                   headers: Dict[str, str], timeout: float = 15.0) -> Dict[str, Any]:
    """POST с JSON-телом; ошибку сервиса возвращаем словами, а не стеком."""
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    except Exception as e:                       # сеть, DNS, TLS
        raise RuntimeError(f"{type(e).__name__}: {e}") from None
    try:
        return json.loads(body) if body.strip() else {}
    except ValueError:
        return {"raw": body[:300]}


def http_post_form(url: str, fields: Dict[str, str],
                   timeout: float = 15.0) -> Dict[str, Any]:
    """POST формой (так SendPulse выдаёт OAuth-токен)."""
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}") from None
    try:
        return json.loads(body) if body.strip() else {}
    except ValueError:
        return {"raw": body[:300]}


API_URLS = {
    "resend": "https://api.resend.com/emails",
    "sendpulse": "https://api.sendpulse.com/smtp/emails",
}


def api_error_hint(err: str) -> str:
    """Человеческая расшифровка типовых ответов сервисов рассылок."""
    low = (err or "").lower()
    if "can only send testing emails" in low or "verify a domain" in low:
        return ("пока домен не подтверждён в Resend, письма уходят только на адрес "
                "владельца аккаунта. Для остальных адресов: resend.com/domains → "
                "Add Domain → прописать выданные DNS-записи → дождаться Verified, "
                "затем слать с адреса своего домена")
    if "401" in low or "invalid api key" in low or "unauthorized" in low:
        return "ключ не подошёл — проверьте LIQSCOPE_MAIL_API_KEY (ключ re_…) "
    if "422" in low and "from" in low:
        return ("сервис не понял адрес отправителя — в systemd значение с пробелом "
                "обязательно в кавычках")
    if "domain is not verified" in low:
        return "домен отправителя ещё не подтверждён в сервисе рассылок"
    return ""


class ApiTransport:
    """Отправка письма HTTP-API сервиса рассылок (порт 443, без SMTP).

    Нужна там, где хостинг блокирует исходящие SMTP-порты: HTTPS обычно открыт.
    Поддерживаются Resend и SendPulse (у обоих есть бесплатный тариф), а также
    «generic» — любой сервис, принимающий JSON с Bearer-ключом.
    """

    label = "API"

    def __init__(self, kind: str, key: str, sender: str, secret: str = "",
                 url: str = "", timeout: float = 15.0, site: str = ""):
        self.kind = (kind or "generic").strip().lower()
        self.key = key
        self.secret = secret
        self.sender = sender
        self.timeout = float(timeout or 15)
        self.site = site or "https://liqscope.online"
        if self.kind == "send_pulse":
            self.kind = "sendpulse"
        self.url = url or API_URLS.get(self.kind, API_URLS["resend"])
        self._token = ""
        self._token_until = 0.0

    def send(self, to: str, subject: str, html: str, text: str = "") -> Tuple[bool, str]:
        try:
            problem = sender_hint(self.sender)
            if problem:
                raise RuntimeError(problem)
            if self.kind == "sendpulse":
                self._send_sendpulse(to, subject, html, text)
            else:
                self._send_json_api(to, subject, html, text)
            return True, ""
        except Exception as e:
            message = str(e)[:200]
            hint = api_error_hint(message)
            log.warning("%s (%s): письмо для %s не ушло: %s%s",
                        self.label, self.kind, to, message,
                        f" — {hint}" if hint else "")
            return False, (f"{message} — {hint}" if hint else message)[:300]

    def _sender_parts(self) -> Tuple[str, str]:
        return split_sender(self.sender)

    def _send_json_api(self, to: str, subject: str, html: str, text: str) -> None:
        """Resend и «generic»: JSON с Bearer-ключом."""
        name, addr = self._sender_parts()
        payload = {
            "from": f"{name} <{addr}>" if name else addr,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text or html_to_text(html),
        }
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        if self.kind == "resend":
            headers["User-Agent"] = f"LiqScope ({self.site})"
        http_post_json(self.url, payload, headers, self.timeout)

    def _sendpulse_token(self) -> str:
        """SendPulse: OAuth-токен на час, держим в памяти."""
        if self._token and time.time() < self._token_until - 60:
            return self._token
        base = self.url.rsplit("/smtp/", 1)[0]
        data = http_post_form(f"{base}/oauth/access_token", {
            "grant_type": "client_credentials",
            "client_id": self.key,
            "client_secret": self.secret,
        }, self.timeout)
        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError(f"SendPulse не выдал токен: {str(data)[:200]}")
        self._token = token
        self._token_until = time.time() + float(data.get("expires_in") or 3600)
        return token

    def _send_sendpulse(self, to: str, subject: str, html: str, text: str) -> None:
        name, addr = self._sender_parts()
        payload = {"email": {
            "subject": subject,
            "html": html,
            "text": text or html_to_text(html),
            "from": {"name": name, "email": addr},
            "to": [{"email": to}],
        }}
        http_post_json(self.url, payload,
                       {"Authorization": f"Bearer {self._sendpulse_token()}"},
                       self.timeout)


#: Языки писем. Письмо уходит на том языке, который человек выбрал на сайте
#: (форма регистрации присылает его в теле запроса).
#: Язык письма, когда человек ещё ничего не выбирал: английский — язык сайта
#: по умолчанию (``seo_pages.DEFAULT_LANG``; здесь он продублирован, чтобы
#: рассылка не зависела от импорта страниц).
DEFAULT_MAIL_LANG = "en"

MAIL_TEXT = {
    "ru": {
        "verify": {
            "subject": "подтвердите почту",
            "title": "Привет!",
            "title_name": "Привет, {name}!",
            "lines": [
                "Вы зарегистрировались на LiqScope — терминале ликвидаций крипто-фьючерсов.",
                "Нажмите кнопку ниже, чтобы подтвердить почту и открыть кабинет:",
                "Ссылка живёт 24 часа и одноразовая.",
            ],
            "button": "Подтвердить почту",
            "footnote": "Если вы не регистрировались, просто удалите письмо — "
                        "адрес никто не увидит.",
        },
        "login": {
            "subject": "вход по ссылке",
            "title": "Вход без пароля",
            "lines": [
                "Вы просили войти в кабинет LiqScope без пароля. Жмите кнопку:",
                "Ссылка живёт 30 минут, одноразовая и работает в том же браузере,"
                " откуда вы её запросили.",
            ],
            "button": "Войти в кабинет",
            "footnote": "Если это были не вы — ничего делать не нужно, войти по ссылке"
                        " без вашего клика нельзя.",
        },
        "attach": {
            "subject": "подтвердите привязку Telegram",
            "title": "Привет!",
            "title_name": "Привет, {name}!",
            "lines": [
                "Аккаунт {who} просит привязать Telegram к кабинету LiqScope с этой почтой.",
                "Если это вы — жмите кнопку, и Telegram станет вашим входом в кабинет:",
                "Ссылка живёт 2 часа и одноразовая.",
            ],
            "button": "Привязать Telegram",
            "footnote": "Если это не вы — просто удалите письмо: без вашего клика"
                        " Telegram к кабинету не привяжется.",
        },
        "reset": {
            "subject": "новый пароль",
            "title": "Сброс пароля",
            "lines": [
                "Вы запросили смену пароля в LiqScope. Жмите кнопку и задайте новый:",
                "Ссылка живёт час и одноразовая.",
            ],
            "button": "Задать новый пароль",
            "footnote": "Если запрос делали не вы — просто удалите письмо,"
                        " пароль не изменится.",
        },
    },
    "en": {
        "verify": {
            "subject": "confirm your email",
            "title": "Hi!",
            "title_name": "Hi, {name}!",
            "lines": [
                "You signed up for LiqScope — the crypto futures liquidation terminal.",
                "Press the button below to confirm your email and open the dashboard:",
                "The link lives for 24 hours and works once.",
            ],
            "button": "Confirm email",
            "footnote": "If it was not you, just delete the letter — nobody will see"
                        " the address.",
        },
        "login": {
            "subject": "sign-in link",
            "title": "Sign in without a password",
            "lines": [
                "You asked to enter the LiqScope dashboard without a password."
                " Press the button:",
                "The link lives for 30 minutes, works once, and only in the same"
                " browser you requested it from.",
            ],
            "button": "Open the dashboard",
            "footnote": "If it was not you, do nothing: nobody can sign in with the"
                        " link without your click.",
        },
        "attach": {
            "subject": "confirm the Telegram link",
            "title": "Hi!",
            "title_name": "Hi, {name}!",
            "lines": [
                "The {who} account asks to link Telegram to the LiqScope dashboard"
                " with this email.",
                "If that is you, press the button and Telegram becomes your way into"
                " the dashboard:",
                "The link lives for 2 hours and works once.",
            ],
            "button": "Link Telegram",
            "footnote": "If it was not you, just delete the letter: Telegram will not"
                        " be linked without your click.",
        },
        "reset": {
            "subject": "new password",
            "title": "Password reset",
            "lines": [
                "You asked to change your LiqScope password. Press the button and"
                " set a new one:",
                "The link lives for an hour and works once.",
            ],
            "button": "Set a new password",
            "footnote": "If you did not ask for it, just delete the letter: the"
                        " password will not change.",
        },
    },
    "zh": {
        "verify": {
            "subject": "请确认邮箱",
            "title": "你好！",
            "title_name": "你好，{name}！",
            "lines": [
                "你注册了 LiqScope——加密货币合约清算终端。",
                "点击下面的按钮确认邮箱并打开个人中心：",
                "链接 24 小时内有效，且只能使用一次。",
            ],
            "button": "确认邮箱",
            "footnote": "如果不是你注册的，直接删除邮件即可——地址不会有人看到。",
        },
        "login": {
            "subject": "登录链接",
            "title": "免密码登录",
            "lines": [
                "你申请免密码进入 LiqScope 个人中心。请点击按钮：",
                "链接 30 分钟内有效，只能使用一次，且仅在申请时的浏览器里生效。",
            ],
            "button": "进入个人中心",
            "footnote": "如果不是你本人操作，无需理会：没有你的点击，链接无法登录。",
        },
        "attach": {
            "subject": "请确认绑定 Telegram",
            "title": "你好！",
            "title_name": "你好，{name}！",
            "lines": [
                "{who} 账号申请把这个 Telegram 绑定到该邮箱的 LiqScope 个人中心。",
                "如果这是你本人——点击按钮，Telegram 就会成为你的登录方式：",
                "链接 2 小时内有效，且只能使用一次。",
            ],
            "button": "绑定 Telegram",
            "footnote": "如果不是你本人——直接删除邮件：没有你的点击，Telegram 不会绑定。",
        },
        "reset": {
            "subject": "新密码",
            "title": "重置密码",
            "lines": [
                "你申请修改 LiqScope 的密码。请点击按钮设置新密码：",
                "链接一小时内有效，且只能使用一次。",
            ],
            "button": "设置新密码",
            "footnote": "如果不是你申请的——直接删除邮件，密码不会改变。",
        },
    },
    "hi": {
        "verify": {
            "subject": "ईमेल की पुष्टि करें",
            "title": "नमस्ते!",
            "title_name": "नमस्ते, {name}!",
            "lines": [
                "आपने LiqScope पर रजिस्टर किया — यह क्रिप्टो फ्यूचर्स लिक्विडेशन टर्मिनल है।",
                "ईमेल की पुष्टि और कैबिनेट खोलने के लिए नीचे का बटन दबाएँ:",
                "लिंक 24 घंटे चलता है और एक ही बार काम करता है।",
            ],
            "button": "ईमेल की पुष्टि करें",
            "footnote": "अगर आपने रजिस्टर नहीं किया, तो ईमेल हटा दें — पता किसी को नहीं दिखेगा।",
        },
        "login": {
            "subject": "साइन-इन लिंक",
            "title": "बिना पासवर्ड साइन इन",
            "lines": [
                "आपने बिना पासवर्ड LiqScope कैबिनेट में आने का अनुरोध किया। बटन दबाएँ:",
                "लिंक 30 मिनट चलता है, एक ही बार काम करता है और उसी ब्राउज़र में जहाँ से माँगा गया।",
            ],
            "button": "कैबिनेट खोलें",
            "footnote": "अगर यह आपने नहीं किया, कुछ करने की ज़रूरत नहीं: आपके क्लिक के बिना"
                        " लिंक से साइन इन नहीं होगा।",
        },
        "attach": {
            "subject": "Telegram लिंक की पुष्टि करें",
            "title": "नमस्ते!",
            "title_name": "नमस्ते, {name}!",
            "lines": [
                "{who} खाता इस ईमेल वाले LiqScope कैबिनेट से Telegram जोड़ने का अनुरोध कर रहा है।",
                "अगर यह आप हैं — बटन दबाएँ, Telegram आपका लॉगिन बन जाएगा:",
                "लिंक 2 घंटे चलता है और एक ही बार काम करता है।",
            ],
            "button": "Telegram जोड़ें",
            "footnote": "अगर यह आप नहीं हैं — ईमेल हटा दें: आपके क्लिक के बिना Telegram नहीं जुड़ेगा।",
        },
        "reset": {
            "subject": "नया पासवर्ड",
            "title": "पासवर्ड रीसेट",
            "lines": [
                "आपने LiqScope का पासवर्ड बदलने का अनुरोध किया। बटन दबाएँ और नया पासवर्ड रखें:",
                "लिंक एक घंटे चलता है और एक ही बार काम करता है।",
            ],
            "button": "नया पासवर्ड रखें",
            "footnote": "अगर अनुरोध आपने नहीं किया — ईमेल हटा दें, पासवर्ड नहीं बदलेगा।",
        },
    },
    "es": {
        "verify": {
            "subject": "confirme su correo",
            "title": "¡Hola!",
            "title_name": "¡Hola, {name}!",
            "lines": [
                "Se registró en LiqScope, el terminal de liquidaciones de futuros cripto.",
                "Pulse el botón de abajo para confirmar su correo y abrir el panel:",
                "El enlace dura 24 horas y funciona una sola vez.",
            ],
            "button": "Confirmar correo",
            "footnote": "Si no se registró usted, borre la carta: nadie verá la dirección.",
        },
        "login": {
            "subject": "enlace de acceso",
            "title": "Entrar sin contraseña",
            "lines": [
                "Pidió entrar en el panel de LiqScope sin contraseña. Pulse el botón:",
                "El enlace dura 30 minutos, funciona una vez y solo en el mismo navegador"
                " desde el que lo pidió.",
            ],
            "button": "Abrir el panel",
            "footnote": "Si no fue usted, no haga nada: sin su clic nadie puede entrar"
                        " con el enlace.",
        },
        "attach": {
            "subject": "confirme el vínculo de Telegram",
            "title": "¡Hola!",
            "title_name": "¡Hola, {name}!",
            "lines": [
                "La cuenta {who} pide vincular Telegram al panel de LiqScope con este correo.",
                "Si es usted, pulse el botón y Telegram será su forma de entrar al panel:",
                "El enlace dura 2 horas y funciona una sola vez.",
            ],
            "button": "Vincular Telegram",
            "footnote": "Si no fue usted, borre la carta: sin su clic, Telegram no se vinculará.",
        },
        "reset": {
            "subject": "nueva contraseña",
            "title": "Restablecer contraseña",
            "lines": [
                "Pidió cambiar su contraseña de LiqScope. Pulse el botón y elija una nueva:",
                "El enlace dura una hora y funciona una sola vez.",
            ],
            "button": "Definir nueva contraseña",
            "footnote": "Si no lo pidió usted, borre la carta: la contraseña no cambiará.",
        },
    },
}

#: Подписи в письме, не зависящие от повода.
MAIL_UI = {
    "ru": {"default_who": "ваш Telegram",
           "link_hint": "Если кнопка не работает, скопируйте ссылку:",
           "footer": "LiqScope — живой терминал ликвидаций крипто-фьючерсов"},
    "en": {"default_who": "your Telegram",
           "link_hint": "If the button does not work, copy the link:",
           "footer": "LiqScope — a live crypto futures liquidation terminal"},
    "zh": {"default_who": "你的 Telegram",
           "link_hint": "如果按钮不可用，请复制链接：",
           "footer": "LiqScope——实时加密货币合约清算终端"},
    "hi": {"default_who": "आपका Telegram",
           "link_hint": "अगर बटन काम न करे, तो लिंक कॉपी करें:",
           "footer": "LiqScope — लाइव क्रिप्टो फ्यूचर्स लिक्विडेशन टर्मिनल"},
    "es": {"default_who": "su Telegram",
           "link_hint": "Si el botón no funciona, copie el enlace:",
           "footer": "LiqScope — terminal en vivo de liquidaciones de futuros cripto"},
}


def _hello(text: dict, name: str) -> str:
    """Приветствие: с именем, если оно есть."""
    name = str(name or "").strip()
    if name:
        return str(text.get("title_name") or text.get("title") or "").replace("{name}", name)
    return str(text.get("title") or "")


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

    # ----- тексты писем: на языке, который человек выбрал на сайте --------
    @staticmethod
    def lang(value: str) -> str:
        """Язык письма: выбранный человеком, иначе язык сайта по умолчанию."""
        code = str(value or "").strip().lower().split("-")[0].split("_")[0]
        return code if code in MAIL_TEXT else DEFAULT_MAIL_LANG

    def send_verify(self, to: str, token: str, name: str = "",
                    lang: str = DEFAULT_MAIL_LANG) -> bool:
        url = self.link(f"/verify?token={token}")
        t = MAIL_TEXT[self.lang(lang)]["verify"]
        html = self._wrap(f"{BRAND}: {t['subject']}", _hello(t, name), t["lines"],
                          url, t["button"], t["footnote"], lang=lang)
        return self.send(to, f"{BRAND}: {t['subject']}", html, kind="verify")

    def send_login_link(self, to: str, token: str,
                        lang: str = DEFAULT_MAIL_LANG) -> bool:
        url = self.link(f"/email-login?token={token}")
        t = MAIL_TEXT[self.lang(lang)]["login"]
        html = self._wrap(f"{BRAND}: {t['subject']}", t["title"], t["lines"],
                          url, t["button"], t["footnote"], lang=lang)
        return self.send(to, f"{BRAND}: {t['subject']}", html, kind="login")

    def send_tg_attach(self, to: str, token: str, tg_name: str = "",
                       name: str = "", lang: str = DEFAULT_MAIL_LANG) -> bool:
        """Подтверждение привязки Telegram к кабинету с этой почтой."""
        url = self.link(f"/attach?token={token}")
        code = self.lang(lang)
        t = MAIL_TEXT[code]["attach"]
        who = tg_name or MAIL_UI[code]["default_who"]
        lines = [ln.replace("{who}", who) for ln in t["lines"]]
        html = self._wrap(f"{BRAND}: {t['subject']}", _hello(t, name), lines,
                          url, t["button"], t["footnote"], lang=code)
        return self.send(to, f"{BRAND}: {t['subject']}", html, kind="attach")

    def send_reset(self, to: str, token: str,
                   lang: str = DEFAULT_MAIL_LANG) -> bool:
        url = self.link(f"/reset?token={token}")
        t = MAIL_TEXT[self.lang(lang)]["reset"]
        html = self._wrap(f"{BRAND}: {t['subject']}", t["title"], t["lines"],
                          url, t["button"], t["footnote"], lang=lang)
        return self.send(to, f"{BRAND}: {t['subject']}", html, kind="reset")

    @staticmethod
    def _wrap(subject: str, hello: str, lines: List[str], url: str, button: str,
              footnote: str = "", lang: str = DEFAULT_MAIL_LANG) -> str:
        ui = MAIL_UI[Mailer.lang(lang)]
        body = "".join(f'<p style="margin:0 0 12px">{ln}</p>' for ln in lines)
        foot = (f'<p style="margin:18px 0 0;color:#8b98ad;font-size:12px">{footnote}</p>'
                if footnote else "")
        return f"""<!DOCTYPE html>
<html lang="{Mailer.lang(lang)}"><body style="margin:0;padding:24px;background:#0b0f16;color:#e8edf7;
 font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<div style="max-width:520px;margin:0 auto;background:#121826;border:1px solid #1b2536;
 border-radius:14px;padding:24px">
<div style="font-weight:800;letter-spacing:1px;color:#3d70ff;margin-bottom:14px">LIQSCOPE</div>
<h1 style="font-size:19px;margin:0 0 14px">{hello}</h1>
{body}
<p style="margin:18px 0"><a href="{url}" style="display:inline-block;background:#3d70ff;
 color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:700">{button}</a></p>
<p style="margin:0 0 6px;color:#8b98ad;font-size:12px">{ui["link_hint"]}</p>
<p style="margin:0;word-break:break-all;font-size:12px;color:#9fb0c9">{url}</p>
{foot}
</div>
<p style="max-width:520px;margin:14px auto 0;color:#5f6b7f;font-size:11px;text-align:center">
 {ui["footer"]}</p>
</body></html>"""

def build_mailer(public_url: str = "") -> Mailer:
    """Собираем отправщик по окружению (пусто — письма не уходят)."""
    folder = _env("LIQSCOPE_MAIL_DIR")
    if folder:
        sender = _env("LIQSCOPE_SMTP_FROM", f"{BRAND} <no-reply@liqscope.online>")
        return Mailer(FileTransport(folder), sender=sender, public_url=public_url)
    hint = sender_hint(_env("LIQSCOPE_SMTP_FROM") or _env("LIQSCOPE_MAIL_API_FROM"))
    if hint:
        log.warning("%s", hint)
    api_kind = _env("LIQSCOPE_MAIL_API")
    if api_kind:
        key = (os.getenv("LIQSCOPE_MAIL_API_KEY") or "").strip()
        default_from = f"{BRAND} <no-reply@liqscope.online>"
        sender = (_env("LIQSCOPE_SMTP_FROM") or _env("LIQSCOPE_MAIL_API_FROM")
                  or default_from)
        if not key:
            log.warning("LIQSCOPE_MAIL_API=%s задан, а LIQSCOPE_MAIL_API_KEY пуст — "
                        "письма не уходят", api_kind)
            return Mailer(None, sender=sender, public_url=public_url, enabled=False)
        transport = ApiTransport(
            kind=api_kind, key=key,
            secret=os.getenv("LIQSCOPE_MAIL_API_SECRET") or "",
            sender=sender, url=_env("LIQSCOPE_MAIL_API_URL"),
            timeout=float(_env("LIQSCOPE_SMTP_TIMEOUT", "15") or 15),
            site=public_url,
        )
        return Mailer(transport, sender=sender, public_url=public_url, enabled=True)
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
