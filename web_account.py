"""HTTP: вход по почте и Telegram, кабинет, админка, учёт визитов.

Основной путь — почта: регистрация с паролем, письмо со ссылкой-подтверждением
(без подтверждения входа нет), вход по паролю или по ссылке из письма, сброс
пароля. Telegram — запасной вход и канал сигналов: привязывается к аккаунту
из кабинета, отдельная регистрация через бота не нужна.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from accounts import (COOKIE_SID, COOKIE_VID, hash_ip, hash_password,
                      normalize_email, password_problem, valid_email,
                      verify_password, verify_telegram_widget)
from web_upload import PHOTO_ERR, decode_json_photo, parse_multipart_file

log = logging.getLogger("liqscope.account")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Ctx:
    """Связка сервера и аккаунтов — заполняется в server.py."""
    store = None
    bot = None
    mailer = None
    public_url = ""
    secret = ""
    cookie_secure = False
    dev_login = False
    require_email_verification = True
    health_fn = staticmethod(lambda: {})
    stats_fn = staticmethod(lambda: {})
    liqs_fn = staticmethod(lambda: [])
    ws_clients_fn = staticmethod(lambda: 0)
    alerts_market_fn = staticmethod(lambda: {})
    symbols_fn = staticmethod(lambda: [])


ctx = Ctx()


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _set_sid(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_SID, token,
        httponly=True, samesite="lax",
        max_age=30 * 86400, path="/",
        secure=ctx.cookie_secure,
    )


def _set_vid(response: Response, vid: str) -> None:
    response.set_cookie(
        COOKIE_VID, vid,
        httponly=True, samesite="lax",
        max_age=400 * 86400, path="/",
        secure=ctx.cookie_secure,
    )


def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_SID)
    if not token or not ctx.store:
        return None
    user = ctx.store.user_by_session(token)
    if user:
        ctx.store.touch_user(user["id"])
    return user


def _need_auth() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "auth"}, status_code=401)


def _need_admin() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "admin"}, status_code=403)


def _need_verified() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "email_unverified"}, status_code=403)


class RateLimiter:
    """Простое окно для писем и входов: не больше N попыток за период.

    Считаем по ключу (IP + действие), в памяти процесса: этого хватает,
    чтобы почта не превратилась в спам-рассылку при переборе.
    """

    def __init__(self, limit: int, window: float):
        self.limit = int(limit)
        self.window = float(window)
        self._hits: Dict[str, list] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        with _RATE_LOCK:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 5000:   # редкая чистка, чтобы словарь не рос
                for k in list(self._hits):
                    if not [t for t in self._hits[k] if now - t < self.window]:
                        self._hits.pop(k, None)
        return True

    def reset(self, key: str) -> None:
        with _RATE_LOCK:
            self._hits.pop(key, None)


_RATE_LOCK = threading.Lock()
_MAIL_RATE = RateLimiter(3, 15 * 60)     # 3 письма на IP за 15 минут
_MAIL_RATE_EMAIL = RateLimiter(5, 3600)  # 5 писем на адрес за час
_LOGIN_RATE = RateLimiter(10, 15 * 60)   # 10 попыток входа на IP за 15 минут


def _rate_key(request: Request, action: str, extra: str = "") -> str:
    return f"{action}:{_client_ip(request)}:{extra}"


def _lang_code(request: Request, body: Optional[dict] = None) -> str:
    if body:
        lang = str((body or {}).get("language") or "").strip().lower()
        if lang:
            return lang[:5]
    head = (request.headers.get("accept-language") or "").lower()
    if head.startswith("en"):
        return "en"
    return "ru"


def _mailer():
    return ctx.mailer


def _email_enabled() -> bool:
    return bool(ctx.mailer and getattr(ctx.mailer, "enabled", False))


def _mail_path(kind: str, token: str) -> str:
    if kind == "verify":
        return f"/verify?token={token}"
    if kind == "login":
        return f"/email-login?token={token}"
    if kind == "reset":
        return f"/reset?token={token}"
    return ""


def _send_mail_blocking(kind: str, to: str, token: str, name: str = "") -> bool:
    m = _mailer()
    if not m or not getattr(m, "enabled", False):
        # Пока SMTP не настроен, регистрация не должна упираться в стену:
        # ссылку пишем в журнал сервиса, админ отдаст её человеку руками.
        log.warning("SMTP не настроен — письмо «%s» для %s не отправлено. "
                    "Ссылка для ручной выдачи: %s",
                    kind, to, (m.link(_mail_path(kind, token)) if m else _mail_path(kind, token)))
        return False
    if kind == "verify":
        return m.send_verify(to, token, name=name)
    if kind == "login":
        return m.send_login_link(to, token)
    if kind == "reset":
        return m.send_reset(to, token)
    return False


async def _send_mail(kind: str, to: str, token: str, name: str = "") -> bool:
    """SMTP — блокирующий вызов: уводим его в поток, не морозя event loop."""
    return await run_in_threadpool(_send_mail_blocking, kind, to, token, name)


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _email_error(lang: str, code: str) -> str:
    ru = {
        "bad_email": "Похоже, в адресе опечатка.",
        "email_unverified": "Сначала подтвердите почту — ссылка есть в письме от LiqScope.",
        "no_user": "Аккаунта с такой почтой нет.",
        "no_password": "У этого аккаунта нет пароля — войдите по ссылке из письма или через Telegram.",
        "short": "Пароль короче 8 символов.",
        "long": "Пароль слишком длинный.",
        "weak": "Такой пароль слишком простой.",
        "rate": "Слишком часто. Попробуйте через пару минут.",
        "expired": "Ссылка устарела — запросите новую.",
        "used": "Ссылка уже использована. Запросите новую.",
        "unknown": "Ссылка не найдена — запросите новую.",
        "taken": "Эта почта уже зарегистрирована — войдите или восстановите пароль.",
        "mail_failed": "Письмо не ушло. Проверьте адрес или попробуйте позже.",
    }
    en = {
        "bad_email": "That address does not look right.",
        "email_unverified": "Confirm your email first — the link is in our letter.",
        "no_user": "No account with this email.",
        "no_password": "This account has no password — use the email link or Telegram.",
        "short": "Password is shorter than 8 characters.",
        "long": "Password is too long.",
        "weak": "That password is too simple.",
        "rate": "Too many attempts. Try again in a few minutes.",
        "expired": "The link expired — request a new one.",
        "used": "That link was already used. Request a new one.",
        "unknown": "Link not found — request a new one.",
        "taken": "This email is already registered — sign in or reset the password.",
        "mail_failed": "The letter did not go out. Check the address or try later.",
    }
    table = en if str(lang).startswith("en") else ru
    return table.get(code, code)


def _user_session(request: Request, response: Response, user: dict) -> str:
    iph = hash_ip(ctx.secret, _client_ip(request))
    token = ctx.store.create_session(user["id"], iph, request.headers.get("user-agent") or "")
    _set_sid(response, token)
    return token


def _verify_allowed(user: dict) -> bool:
    """Пускать ли в кабинет: подтверждение почты обязательно (жёсткий режим)."""
    if not ctx.require_email_verification:
        return True
    if not user.get("email"):
        return True          # Telegram-аккаунт без почты — отдельный случай
    return bool(user.get("email_verified"))


def _next_after_verify() -> str:
    return "/cabinet"


def _safe_next(value: str) -> str:
    """Только свой путь: «//evil.com» и «https://…» не принимаем."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return ""
    return value


# Публичное имя бота, если getMe ещё не ответил или токен не подхватился.
DEFAULT_BOT_USERNAME = (os.getenv("LIQSCOPE_BOT_USERNAME") or "LiqScopeBot").lstrip("@")


def _bot_username() -> str:
    bot = ctx.bot
    name = ((bot.username if bot else "") or "").lstrip("@")
    return name or DEFAULT_BOT_USERNAME


def _bot_link(start: str = "") -> str:
    user = _bot_username()
    if start:
        return f"https://t.me/{user}?start={quote(start)}"
    return f"https://t.me/{user}"


def _public_url(request: Request) -> str:
    if ctx.public_url:
        return ctx.public_url.rstrip("/")
    return str(request.base_url).rstrip("/")


async def read_uploaded_photo(request: Request) -> Tuple[bytes, str]:
    ctype = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ctype:
        raw = await request.body()
        return parse_multipart_file(raw, request.headers.get("content-type") or "")
    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            body = {}
        return decode_json_photo(body)
    if ctype.startswith("image/"):
        return await request.body(), ""
    return b"", ""


def register_account_routes(app) -> None:
    router = APIRouter()

    @router.get("/login")
    async def page_login():
        return FileResponse(os.path.join(STATIC_DIR, "login.html"))

    @router.get("/cabinet")
    async def page_cabinet():
        return FileResponse(os.path.join(STATIC_DIR, "cabinet.html"))

    @router.get("/admin")
    async def page_admin():
        return FileResponse(os.path.join(STATIC_DIR, "admin.html"))

    @router.get("/api/auth/me")
    async def api_me(request: Request):
        user = current_user(request)
        bot_user = _bot_username()
        notice = ctx.store.get_setting("site_notice", "") if ctx.store else ""
        return {
            "user": user,
            "bot_username": bot_user,
            "bot_ready": bool(bot_user),
            "bot_link": _bot_link(),
            "public_url": _public_url(request),
            "site_notice": notice,
            # кабинет спрашивает это, чтобы показать «подтвердите почту»
            "verified": bool(user) and _verify_allowed(user),
            "email_required": bool(ctx.require_email_verification),
            "mail_enabled": _email_enabled(),
        }

    # ----- почта: регистрация и вход ---------------------------------------
    @router.post("/api/auth/email/register")
    async def api_email_register(request: Request, response: Response):
        """Регистрация по почте. Возвращает только «письмо отправлено»."""
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = await _json_body(request)
        email = normalize_email(body.get("email"))
        lang = _lang_code(request, body)
        if not valid_email(email):
            return JSONResponse({"ok": False, "error": "bad_email",
                                 "hint": _email_error(lang, "bad_email")}, status_code=400)
        problem = password_problem(body.get("password"), email)
        if problem:
            return JSONResponse({"ok": False, "error": problem,
                                 "hint": _email_error(lang, problem)}, status_code=400)
        if not _MAIL_RATE.allow(_rate_key(request, "mail")):
            return JSONResponse({"ok": False, "error": "rate",
                                 "hint": _email_error(lang, "rate")}, status_code=429)
        r = ctx.store.create_email_user(
            email, password_hash=hash_password(str(body.get("password"))),
            first_name=str(body.get("name") or "")[:64], language=lang)
        if not r.get("ok"):
            # адрес уже подтверждён: честно говорим, что аккаунт есть, и
            # отправляем на вход — письмо чужому человеку не уходит
            return JSONResponse({
                "ok": True, "sent": False, "exists": True, "email": email,
                "verify_required": True,
                "hint": _email_error(lang, "taken"),
            }, status_code=200)
        user = r["user"]
        token = ctx.store.new_email_token(user["id"], "verify", email=email)
        sent = await _send_mail("verify", email, token, name=user.get("first_name") or "")
        if not sent and _email_enabled():
            return JSONResponse({"ok": False, "error": "mail_failed",
                                 "hint": _email_error(lang, "mail_failed")}, status_code=502)
        log.info("Регистрация по почте: %s (письмо %s)", email, "ушло" if sent else "не ушло")
        return {"ok": True, "sent": bool(sent), "email": email,
                "verify_required": True, "mail_enabled": _email_enabled()}

    @router.post("/api/auth/email/resend")
    async def api_email_resend(request: Request):
        body = await _json_body(request)
        email = normalize_email(body.get("email"))
        lang = _lang_code(request, body)
        if not valid_email(email):
            return JSONResponse({"ok": False, "error": "bad_email",
                                 "hint": _email_error(lang, "bad_email")}, status_code=400)
        if not _MAIL_RATE.allow(_rate_key(request, "mail")) or \
                not _MAIL_RATE_EMAIL.allow(_rate_key(request, "mail-addr", email)):
            return JSONResponse({"ok": False, "error": "rate",
                                 "hint": _email_error(lang, "rate")}, status_code=429)
        user = ctx.store.get_user_by_email(email) if ctx.store else None
        # ответ всегда одинаковый: не выдаём, зарегистрирован адрес или нет
        if user and not user.get("email_verified"):
            token = ctx.store.new_email_token(user["id"], "verify", email=email)
            sent = await _send_mail("verify", email, token, name=user.get("first_name") or "")
            return {"ok": True, "sent": bool(sent), "email": email}
        return {"ok": True, "sent": True, "email": email}

    @router.get("/verify")
    async def page_verify(request: Request, token: str = ""):
        """Ссылка из письма: подтверждаем почту и сразу открываем кабинет."""
        if not ctx.store or not token:
            return RedirectResponse("/login?verify=bad", status_code=303)
        user, err = ctx.store.consume_email_token(token, "verify")
        if err:
            return RedirectResponse("/login?verify=" + err, status_code=303)
        user = ctx.store.mark_email_verified(user["id"]) or user
        if user["is_banned"]:
            return RedirectResponse("/login?banned=1", status_code=303)
        ctx.store.audit(user["id"], "email_verified", user.get("email") or "")
        resp = RedirectResponse(_next_after_verify(), status_code=303)
        _set_sid(resp, ctx.store.create_session(
            user["id"], hash_ip(ctx.secret, _client_ip(request)),
            request.headers.get("user-agent") or ""))
        return resp

    @router.post("/api/auth/email/login")
    async def api_email_login(request: Request, response: Response):
        body = await _json_body(request)
        email = normalize_email(body.get("email"))
        lang = _lang_code(request, body)
        if not _LOGIN_RATE.allow(_rate_key(request, "login")):
            return JSONResponse({"ok": False, "error": "rate",
                                 "hint": _email_error(lang, "rate")}, status_code=429)
        user = ctx.store.get_user_by_email(email) if ctx.store else None
        row = None
        if ctx.store and user:
            row = ctx.store._db.execute(   # пароль наружу не отдаём публичным профилем
                "SELECT password_hash FROM users WHERE id=?", (int(user["id"]),)
            ).fetchone()
        stored = (row["password_hash"] if row else "") or ""
        password = str(body.get("password") or "")
        if not user or not stored or not verify_password(password, stored):
            return JSONResponse({"ok": False, "error": "bad_credentials",
                                 "hint": _email_error(lang, "no_user")}, status_code=401)
        if user["is_banned"]:
            return JSONResponse({"ok": False, "error": "banned",
                                 "hint": _email_error(lang, "banned")}, status_code=403)
        if not _verify_allowed(user):
            # жёсткий режим: без клика по ссылке из письма входа нет
            token = ctx.store.new_email_token(user["id"], "verify", email=email)
            sent = await _send_mail("verify", email, token, name=user.get("first_name") or "")
            return JSONResponse({"ok": False, "error": "email_unverified",
                                 "hint": _email_error(lang, "email_unverified"),
                                 "sent": bool(sent)}, status_code=403)
        _LOGIN_RATE.reset(_rate_key(request, "login"))
        _user_session(request, response, user)
        return {"ok": True, "user": user}

    @router.post("/api/auth/email/link")
    async def api_email_link(request: Request):
        """Вход по ссылке: присылаем ссылку на почту, пароль не нужен."""
        body = await _json_body(request)
        email = normalize_email(body.get("email"))
        lang = _lang_code(request, body)
        if not valid_email(email):
            return JSONResponse({"ok": False, "error": "bad_email",
                                 "hint": _email_error(lang, "bad_email")}, status_code=400)
        if not _MAIL_RATE.allow(_rate_key(request, "mail")) or \
                not _MAIL_RATE_EMAIL.allow(_rate_key(request, "mail-addr", email)):
            return JSONResponse({"ok": False, "error": "rate",
                                 "hint": _email_error(lang, "rate")}, status_code=429)
        user = ctx.store.get_user_by_email(email) if ctx.store else None
        if user and not user["is_banned"]:
            token = ctx.store.new_email_token(user["id"], "login", email=email)
            await _send_mail("login", email, token)
        return {"ok": True, "sent": True, "email": email}

    @router.get("/email-login")
    async def page_email_login(request: Request, token: str = ""):
        if not ctx.store or not token:
            return RedirectResponse("/login?link=bad", status_code=303)
        user, err = ctx.store.consume_email_token(token, "login")
        if err:
            return RedirectResponse("/login?link=" + err, status_code=303)
        if user["is_banned"]:
            return RedirectResponse("/login?banned=1", status_code=303)
        if not _verify_allowed(user):
            return RedirectResponse("/login?verify=first", status_code=303)
        resp = RedirectResponse("/cabinet", status_code=303)
        _set_sid(resp, ctx.store.create_session(
            user["id"], hash_ip(ctx.secret, _client_ip(request)),
            request.headers.get("user-agent") or ""))
        return resp

    @router.post("/api/auth/email/reset")
    async def api_email_reset(request: Request):
        """Забыли пароль: письмо со ссылкой на смену."""
        body = await _json_body(request)
        email = normalize_email(body.get("email"))
        lang = _lang_code(request, body)
        if not valid_email(email):
            return JSONResponse({"ok": False, "error": "bad_email",
                                 "hint": _email_error(lang, "bad_email")}, status_code=400)
        if not _MAIL_RATE.allow(_rate_key(request, "mail")) or \
                not _MAIL_RATE_EMAIL.allow(_rate_key(request, "mail-addr", email)):
            return JSONResponse({"ok": False, "error": "rate",
                                 "hint": _email_error(lang, "rate")}, status_code=429)
        user = ctx.store.get_user_by_email(email) if ctx.store else None
        if user and not user["is_banned"]:
            token = ctx.store.new_email_token(user["id"], "reset", email=email)
            await _send_mail("reset", email, token)
        return {"ok": True, "sent": True, "email": email}

    @router.get("/reset")
    async def page_reset(request: Request, token: str = ""):
        return FileResponse(os.path.join(STATIC_DIR, "reset.html"))

    @router.get("/api/auth/email/token")
    async def api_email_token_info(request: Request, token: str = ""):
        """Годится ли ссылка из письма (страница сброса спрашивает это)."""
        if not ctx.store:
            return {"ok": False, "error": "no_store"}
        user, err = ctx.store.email_token_user(token, "")
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "email": user.get("email") or ""}

    @router.post("/api/auth/email/set-password")
    async def api_email_set_password(request: Request, response: Response):
        body = await _json_body(request)
        lang = _lang_code(request, body)
        problem = password_problem(body.get("password"), body.get("email") or "")
        if problem:
            return JSONResponse({"ok": False, "error": problem,
                                 "hint": _email_error(lang, problem)}, status_code=400)
        token = str(body.get("token") or "")
        user, err = ctx.store.consume_email_token(token, "reset") if ctx.store else (None, "unknown")
        if err:
            return JSONResponse({"ok": False, "error": err,
                                 "hint": _email_error(lang, err)}, status_code=400)
        ctx.store.set_user_password(user["id"], hash_password(str(body.get("password"))))
        # пароль сменили — старые сессии уходят
        ctx.store.drop_user_sessions(user["id"])
        refreshed = ctx.store.get_user(user["id"]) or user
        if refreshed.get("email") and not refreshed.get("email_verified"):
            refreshed = ctx.store.mark_email_verified(refreshed["id"]) or refreshed
        _user_session(request, response, refreshed)
        ctx.store.audit(user["id"], "password_reset", user.get("email") or "")
        return {"ok": True, "user": refreshed}

    # ----- Telegram: привязка к аккаунту (не регистрация) -------------------
    @router.post("/api/auth/telegram/link")
    async def api_tg_link(request: Request):
        """Кабинет просит ссылку на бота: привяжем Telegram к этому аккаунту."""
        user = current_user(request)
        if not user:
            return _need_auth()
        if not ctx.bot or not getattr(ctx.bot, "token", ""):
            return JSONResponse({"ok": False, "error": "no_bot",
                                 "hint": "Задайте LIQSCOPE_BOT_TOKEN и перезапустите сервер."},
                                status_code=503)
        nonce = ctx.store.new_link_nonce(user["id"])
        return {"ok": True, "nonce": nonce, "bot_username": _bot_username(),
                "bot_link": _bot_link("link_" + nonce),
                "expires_in": 900}

    @router.get("/api/auth/telegram/link/status")
    async def api_tg_link_status(request: Request, nonce: str = ""):
        user = current_user(request)
        if not user:
            return _need_auth()
        st = ctx.store.link_nonce_status(nonce)
        if st.get("ok"):
            # сессия браузера остаётся, но данные в ней обновляем
            st["user"] = ctx.store.get_user(user["id"]) or user
        return st

    @router.post("/api/auth/telegram/unlink")
    async def api_tg_unlink(request: Request):
        user = current_user(request)
        if not user:
            return _need_auth()
        updated = ctx.store.unlink_telegram(user["id"])
        if not updated:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True, "user": updated}

    @router.post("/api/auth/logout")
    async def api_logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE_SID)
        if token and ctx.store:
            ctx.store.drop_session(token)
        response.delete_cookie(COOKIE_SID, path="/")
        return {"ok": True}

    @router.post("/api/auth/telegram/start")
    async def api_tg_start(request: Request):
        if not ctx.store:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        bot_user = _bot_username()
        if not ctx.bot or not getattr(ctx.bot, "token", ""):
            return JSONResponse({
                "ok": False,
                "error": "no_bot",
                "hint": "Задайте LIQSCOPE_BOT_TOKEN и перезапустите сервер.",
            }, status_code=503)
        nonce = ctx.store.new_nonce()
        payload = "login_" + nonce
        return {
            "ok": True,
            "nonce": nonce,
            "bot_username": bot_user,
            "bot_link": _bot_link(payload),
            "expires_in": 300,
        }

    @router.get("/api/auth/telegram/wait")
    async def api_tg_wait(request: Request, response: Response, nonce: str = ""):
        if not ctx.store or not nonce:
            return {"ok": False, "pending": True}
        st = ctx.store.nonce_status(nonce)
        if not st.get("ok"):
            return st
        user = ctx.store.get_user(st["user_id"])
        if not user:
            return {"ok": False, "error": "user"}
        if user["is_banned"]:
            return JSONResponse({"ok": False, "error": "banned"}, status_code=403)
        iph = hash_ip(ctx.secret, _client_ip(request))
        token = ctx.store.create_session(user["id"], iph, request.headers.get("user-agent") or "")
        _set_sid(response, token)
        return {"ok": True, "user": user}

    @router.post("/api/auth/telegram/widget")
    async def api_tg_widget(request: Request, response: Response):
        if not ctx.store or not ctx.bot or not ctx.bot.token:
            return JSONResponse({"ok": False, "error": "no_bot"}, status_code=503)
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not verify_telegram_widget(data, ctx.bot.token):
            return JSONResponse({"ok": False, "error": "bad_hash"}, status_code=401)
        user = ctx.store.upsert_telegram_user(data)
        if user["is_banned"]:
            return JSONResponse({"ok": False, "error": "banned"}, status_code=403)
        iph = hash_ip(ctx.secret, _client_ip(request))
        token = ctx.store.create_session(user["id"], iph, request.headers.get("user-agent") or "")
        _set_sid(response, token)
        return {"ok": True, "user": user}

    @router.post("/api/auth/dev")
    async def api_dev_login(request: Request, response: Response):
        """Только при LIQSCOPE_DEV_LOGIN=1 — вход без Telegram (разработка/тесты)."""
        if not ctx.dev_login or not ctx.store:
            return JSONResponse({"ok": False, "error": "disabled"}, status_code=404)
        try:
            data = await request.json()
        except Exception:
            data = {}
        tg_id = int(data.get("tg_id") or 0)
        if not tg_id:
            return JSONResponse({"ok": False, "error": "tg_id"}, status_code=400)
        user = ctx.store.upsert_telegram_user({
            "id": tg_id,
            "username": data.get("username") or "dev",
            "first_name": data.get("first_name") or "Dev",
            "last_name": data.get("last_name") or "",
            "language_code": data.get("language") or "ru",
        })
        if data.get("is_admin"):
            user = ctx.store.set_admin(user["id"], True) or user
        token = ctx.store.create_session(user["id"], "dev", "dev")
        _set_sid(response, token)
        return {"ok": True, "user": user}

    @router.get("/api/account/services")
    async def api_services(request: Request):
        user = current_user(request)
        if not user:
            return _need_auth()
        if not _verify_allowed(user):
            return _need_verified()
        have = set(ctx.store.user_service_slugs(user["id"]))
        out = []
        for s in ctx.store.list_services(include_disabled=False):
            item = dict(s)
            item["subscribed"] = s["slug"] in have
            out.append(item)
        return {"ok": True, "services": out}

    @router.post("/api/account/services/{slug}")
    async def api_service_toggle(request: Request, slug: str):
        user = current_user(request)
        if not user:
            return _need_auth()
        if not _verify_allowed(user):
            return _need_verified()
        try:
            body = await request.json()
        except Exception:
            body = {}
        enabled = bool(body.get("enabled", True))
        return ctx.store.toggle_user_service(user["id"], slug, enabled)

    @router.get("/api/account/alerts")
    async def api_alerts_get(request: Request):
        from alerts import live_snapshot, normalize_config, presets
        user = current_user(request)
        if not user:
            return _need_auth()
        if not _verify_allowed(user):
            return _need_verified()
        row = ctx.store.get_user_service(user["id"], "alerts")
        cfg = normalize_config((row or {}).get("config") or {})
        market = {}
        try:
            market = ctx.alerts_market_fn() or {}
        except Exception as e:
            log.debug("alerts market: %s", e)
        return {
            "ok": True,
            "config": cfg,
            "subscribed": bool(row and row.get("enabled")),
            "presets": presets(),
            "live": live_snapshot(cfg, market),
            "history": ctx.store.list_alert_events(user["id"], 48),
            "symbols": list(ctx.symbols_fn() or [])[:60],
        }

    @router.post("/api/account/alerts")
    async def api_alerts_save(request: Request):
        from alerts import live_snapshot, normalize_config
        user = current_user(request)
        if not user:
            return _need_auth()
        if not _verify_allowed(user):
            return _need_verified()
        try:
            body = await request.json()
        except Exception:
            body = {}
        r = ctx.store.set_user_service_config(
            user["id"], "alerts", body or {}, enabled=True)
        if not r.get("ok"):
            return JSONResponse(r, status_code=400)
        market = {}
        try:
            market = ctx.alerts_market_fn() or {}
        except Exception:
            market = {}
        cfg = r.get("config") or normalize_config(body)
        return {
            "ok": True,
            "config": cfg,
            "subscribed": True,
            "live": live_snapshot(cfg, market),
        }

    # ----- admin ----------------------------------------------------------
    def _admin(request: Request):
        user = current_user(request)
        if not user:
            return None, _need_auth()
        if not user["is_admin"]:
            return None, _need_admin()
        return user, None

    @router.get("/api/admin/overview")
    async def admin_overview(request: Request):
        user, err = _admin(request)
        if err:
            return err
        c = ctx.store.user_counts()
        v = ctx.store.visit_stats(14)
        h = ctx.health_fn() or {}
        bot_user = _bot_username()
        return {
            "ok": True,
            "me": user,
            "users": c,
            "visits": v,
            "ws_clients": ctx.ws_clients_fn(),
            "bot": {
                "username": bot_user,
                "ready": bool(bot_user),
                "running": bool(ctx.bot and ctx.bot.running),
            },
            "health": {
                "live_exchanges": h.get("live_exchanges") or [],
                "demo": h.get("demo"),
                "ready": h.get("ready"),
            },
            "settings": {
                "bot_welcome": ctx.store.get_setting("bot_welcome", ""),
                "site_notice": ctx.store.get_setting("site_notice", ""),
            },
            "services": ctx.store.list_services(True),
            "audit": ctx.store.recent_audit(20),
            "mail": (ctx.mailer.status() if ctx.mailer else {"enabled": False}),
        }

    @router.get("/api/admin/users")
    async def admin_users(request: Request, q: str = "", limit: int = 50, offset: int = 0):
        user, err = _admin(request)
        if err:
            return err
        return {"ok": True, **ctx.store.list_users(q=q, limit=limit, offset=offset)}

    @router.post("/api/admin/users/{user_id}/ban")
    async def admin_ban(request: Request, user_id: int):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        banned = bool(body.get("banned", True))
        u = ctx.store.set_banned(user_id, banned, actor_id=actor["id"])
        if not u:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True, "user": u}

    @router.post("/api/admin/broadcast")
    async def admin_broadcast(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        if not ctx.bot or not ctx.bot.running:
            return JSONResponse({"ok": False, "error": "no_bot"}, status_code=503)
        st = await ctx.bot.broadcast(text, actor_id=actor["id"])
        return {"ok": True, **st}

    @router.post("/api/admin/settings")
    async def admin_settings(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        allowed = {"bot_welcome", "site_notice"}
        saved = {}
        for k, v in body.items():
            if k in allowed and isinstance(v, str):
                ctx.store.set_setting(k, v[:2000], actor_id=actor["id"])
                saved[k] = v[:2000]
        return {"ok": True, "saved": saved}

    @router.post("/api/admin/services/{slug}")
    async def admin_service(request: Request, slug: str):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        fields = {}
        if "enabled" in body:
            fields["enabled"] = 1 if body["enabled"] else 0
        if "coming_soon" in body:
            fields["coming_soon"] = 1 if body["coming_soon"] else 0
        row = ctx.store.update_service(slug, **fields)
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        ctx.store.audit(actor["id"], "service", f"{slug} {fields}")
        return {"ok": True, "service": row}

    @router.get("/api/admin/stats")
    async def admin_market(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        return {"ok": True, "stats": ctx.stats_fn(), "health": ctx.health_fn()}

    @router.get("/api/admin/digest")
    async def admin_digest_list(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        heads = ctx.store.list_digest_heads()
        photos = []
        for p in ctx.store.list_digest_photos():
            photos.append({
                "id": p["id"],
                "name": p.get("name") or "",
                "exists": bool(p.get("exists")),
                "url": f"/api/admin/digest/photos/{p['id']}/file",
            })
        return {
            "ok": True,
            "heads": heads,
            "photos": photos,
            "using_default_heads": not bool(heads),
            "using_default_photos": not bool(photos),
        }

    @router.post("/api/admin/digest/heads")
    async def admin_digest_add_head(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        r = ctx.store.add_digest_head(str(body.get("text") or ""), actor_id=actor["id"])
        if not r.get("ok"):
            return JSONResponse(r, status_code=400)
        return r

    @router.post("/api/admin/digest/heads/{head_id}/delete")
    async def admin_digest_del_head(request: Request, head_id: int):
        actor, err = _admin(request)
        if err:
            return err
        if not ctx.store.delete_digest_head(head_id, actor_id=actor["id"]):
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True}

    @router.post("/api/admin/digest/photos")
    async def admin_digest_add_photo(request: Request):
        actor, err = _admin(request)
        if err:
            return err
        blob, filename = await read_uploaded_photo(request)
        if not blob:
            return JSONResponse(
                {"ok": False, "error": "bad_data", "hint": PHOTO_ERR["bad_data"]},
                status_code=400)
        r = ctx.store.add_digest_photo(blob, filename=filename, actor_id=actor["id"])
        if not r.get("ok"):
            code = str(r.get("error") or "error")
            r = dict(r)
            r["hint"] = PHOTO_ERR.get(code, code)
            return JSONResponse(r, status_code=400)
        return {"ok": True, "id": r["id"], "name": r.get("name")}

    @router.get("/api/admin/digest/photos/{photo_id}/file")
    async def admin_digest_photo_file(request: Request, photo_id: int):
        actor, err = _admin(request)
        if err:
            return err
        row = ctx.store.get_digest_photo(photo_id)
        path = (row or {}).get("path") or ""
        if not row or not os.path.isfile(path):
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return FileResponse(path)

    @router.post("/api/admin/digest/photos/{photo_id}/delete")
    async def admin_digest_del_photo(request: Request, photo_id: int):
        actor, err = _admin(request)
        if err:
            return err
        if not ctx.store.delete_digest_photo(photo_id, actor_id=actor["id"]):
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True}

    app.include_router(router)

    @app.middleware("http")
    async def visit_and_vid(request: Request, call_next):
        path = request.url.path or "/"
        vid = request.cookies.get(COOKIE_VID) or ""
        new_vid = ""
        if not vid:
            new_vid = secrets.token_urlsafe(12)
            vid = new_vid
        response = await call_next(request)
        if new_vid:
            _set_vid(response, new_vid)
        # страницы, не статика/апи/ws
        if (
            ctx.store
            and request.method in ("GET", "HEAD")
            and response.status_code < 400
            and not path.startswith("/static")
            and not path.startswith("/api")
            and path != "/ws"
        ):
            try:
                user = current_user(request)
                iph = hash_ip(ctx.secret, _client_ip(request))
                ctx.store.record_visit(path, vid, user["id"] if user else None, iph)
            except Exception as e:
                log.debug("visit: %s", e)
        return response
