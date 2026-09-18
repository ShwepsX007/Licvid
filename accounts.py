"""Пользователи LiqScope: вход по почте и Telegram, сессии, визиты, сервисы.

SQLite в data/accounts.db (путь задаётся LIQSCOPE_ACCOUNTS_DB).

Основной вход — почта: регистрация с паролем и подтверждением письмом
(пока адрес не подтверждён, входа нет), плюс вход по ссылке из письма и
сброс пароля. Telegram — запасной вход и канал сигналов: привязать его к
аккаунту можно в кабинете, отдельная регистрация через бота не нужна.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("liqscope.accounts")

COOKIE_SID = "liqscope_sid"
COOKIE_VID = "liqscope_vid"
SESSION_DAYS = 30
NONCE_TTL = 300  # 5 минут на подтверждение входа в боте

# Почта и пароли
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 200
BAD_PASSWORDS = {"password", "passw0rd", "12345678", "123456789", "1234567890",
                 "qwertyui", "qwerty123", "11111111", "пароль123", "пароль1234"}
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

# Сколько живут ссылки в письмах
EMAIL_TOKEN_TTL = {
    "verify": 24 * 3600,   # подтверждение почты — сутки
    "login": 30 * 60,      # вход по ссылке — полчаса
    "reset": 3600,         # сброс пароля — час
    "link": 2 * 3600,      # привязка почты к аккаунту из бота — два часа
}
LINK_NONCE_TTL = 15 * 60   # привязка Telegram из кабинета
CAPTCHA_TTL = 10 * 60      # арифметическая капча на регистрацию


def normalize_email(raw: Any) -> str:
    """Адреса сравниваем без регистра и пробелов: Foo@Mail.ru == foo@mail.ru."""
    return str(raw or "").strip().lower()


def valid_email(raw: Any) -> bool:
    email = normalize_email(raw)
    if not email or len(email) > 254 or ".." in email:
        return False
    return bool(EMAIL_RE.match(email))


def password_problem(password: Any, email: str = "") -> str:
    """Пустая строка — пароль годный, иначе код ошибки для фронтенда."""
    p = str(password or "")
    if len(p) < PASSWORD_MIN:
        return "short"
    if len(p) > PASSWORD_MAX:
        return "long"
    if p.lower() in BAD_PASSWORDS:
        return "weak"
    if email and p.lower() == normalize_email(email):
        return "weak"
    return ""


def hash_password(password: str) -> str:
    """scrypt из стандартной библиотеки — внешних зависимостей не нужно."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(str(password).encode("utf-8"), salt=salt,
                        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Проверка пароля по сохранённому хешу (не падает на битом значении)."""
    if not password or not stored:
        return False
    try:
        kind, n, r, p, salt_hex, hash_hex = str(stored).split("$")
        if kind != "scrypt":
            return False
        dk = hashlib.scrypt(str(password).encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError, MemoryError):
        return False

DEFAULT_SERVICES = (
    {
        "slug": "alerts",
        "title": "Алерты по объёму",
        "title_en": "Volume alerts",
        "description": "Ликвидации, CVD и OI: порог, окно, монета — сигнал в кабинет и в Telegram.",
        "icon": "🔔",
        "enabled": 1,
        "coming_soon": 0,
        "sort": 10,
    },
    {
        "slug": "correlations",
        "title": "Корреляции валют",
        "title_en": "Coin correlations",
        "description": ("Какие монеты ходят вместе за час-неделю: ликвидации, "
                        "объём, CVD и OI. Где выносило лонги, а где шорты."),
        "icon": "🔗",
        "enabled": 1,
        "coming_soon": 0,
        "sort": 20,
    },
    {
        "slug": "watchlist",
        "title": "Сторож монет",
        "title_en": "Coin watcher",
        "description": ("Пампы и дампы всех монет Gate: порог в %, период свечей "
                        "и их число. Сигнал в Telegram со ссылкой на Gate."),
        "icon": "👁",
        "enabled": 1,
        "coming_soon": 0,
        "sort": 30,
    },
    {
        "slug": "digest",
        "title": "Дневной дайджест",
        "title_en": "Daily digest",
        "description": "Сводка рынка за сутки в кабинет и в Telegram.",
        "icon": "📰",
        "enabled": 1,
        "coming_soon": 0,
        "sort": 40,
    },
)


#: какие значения вообще пускаем в настройки сервиса (простые и короткие)
CONFIG_MAX_KEYS = 24
CONFIG_MAX_LIST = 40


#: насколько глубоко пускаем вложенность настроек сервиса: конфиг → метрика →
#: её поля. Глубже не нужно, а «мусор» из запроса отсекаем лимитами.
CONFIG_MAX_DEPTH = 3


def _clean_value(val: Any, depth: int = 0) -> Any:
    """Одно значение настроек: скаляр как есть, словарь/список — с лимитами."""
    if isinstance(val, bool) or isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        return val[:120]
    if isinstance(val, (list, tuple)):
        items = []
        for item in list(val)[:CONFIG_MAX_LIST]:
            if isinstance(item, (str, int, float, bool)):
                items.append(item[:120] if isinstance(item, str) else item)
        return items
    if isinstance(val, dict) and depth < CONFIG_MAX_DEPTH:
        out: Dict[str, Any] = {}
        for k, v in list(val.items())[:CONFIG_MAX_KEYS]:
            clean = _clean_value(v, depth + 1)
            if clean is not None or v is None:
                out[str(k)[:40]] = clean
        return out
    return None


def clean_service_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Чистка настроек сервиса: только простые значения и без мусора.

    Ключи приходят с сайта и из бота, так что лимиты нужны: строка — до 120
    символов, список — до 40 коротких значений, вложенность — до
    ``CONFIG_MAX_DEPTH`` уровней. Вложенность нужна настоящая: алерты по
    корреляции держат настройки каждой метрики (окно и два порога), и без
    второго уровня они молча терялись бы при сохранении.
    """
    if not isinstance(config, dict):
        return {}
    return _clean_value(dict(config), 0) or {}


def _now() -> float:
    return time.time()


def hash_ip(secret: str, ip: str) -> str:
    ip = (ip or "").strip() or "0"
    return hmac.new(secret.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def display_name(user: Dict[str, Any]) -> str:
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    if first and last:
        return first + " " + last
    if first:
        return first
    if user.get("username"):
        return "@" + str(user["username"])
    email = str(user.get("email") or "")
    if email:
        return email.split("@")[0]
    return "id" + str(user.get("tg_id") or user.get("id") or "")


def public_user(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    """Публичный профиль: без хеша пароля, с признаками входа.

    tg_id у аккаунта по почте пустой — Telegram может быть не привязан вовсе,
    поэтому отдаём None, а не 0 (иначе фронтенд рисует «id 0»).
    """
    d = dict(row)
    tg_id = d.get("tg_id")
    return {
        "id": int(d["id"]),
        "tg_id": int(tg_id) if tg_id else None,
        "tg_linked": bool(tg_id),
        "email": d.get("email") or "",
        "email_verified": bool(d.get("email_verified")),
        "has_password": bool(d.get("password_hash")),
        "username": d.get("username") or "",
        "first_name": d.get("first_name") or "",
        "last_name": d.get("last_name") or "",
        "photo_url": d.get("photo_url") or "",
        "language": d.get("language") or "ru",
        "lang_manual": int(d.get("lang_manual") or 0),
        "is_admin": bool(d.get("is_admin")),
        "is_banned": bool(d.get("is_banned")),
        "created_at": float(d.get("created_at") or 0),
        "last_seen": float(d.get("last_seen") or 0),
        "login_count": int(d.get("login_count") or 0),
        "display_name": display_name(d),
    }


def verify_telegram_widget(data: Dict[str, Any], bot_token: str, max_age: int = 86400) -> bool:
    """Проверка подписи Telegram Login Widget.

    https://core.telegram.org/widgets/login#checking-authorization
    """
    if not bot_token or not data:
        return False
    try:
        check_hash = str(data.get("hash") or "")
        if not check_hash:
            return False
        auth_date = int(data.get("auth_date") or 0)
        if auth_date and abs(_now() - auth_date) > max_age:
            return False
        parts = []
        for k in sorted(data.keys()):
            if k == "hash":
                continue
            v = data[k]
            if v is None:
                continue
            parts.append(f"{k}={v}")
        data_check = "\n".join(parts)
        secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
        digest = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, check_hash)
    except Exception:
        return False


class Store:
    def __init__(self, path: str, secret: str, admin_ids: Iterable[int] = (),
                 admin_emails: Iterable[str] = ()):
        self.path = path
        self.secret = secret or secrets.token_hex(16)
        self.admin_ids = {int(x) for x in admin_ids if int(x)}
        # админы, зарегистрированные по почте (LIQSCOPE_ADMIN_EMAILS)
        self.admin_emails = {normalize_email(x) for x in admin_emails if normalize_email(x)}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    -- NULL = регистрация по почте, Telegram ещё не привязан
                    tg_id INTEGER UNIQUE,
                    email TEXT,
                    password_hash TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    email_verified_at REAL,
                    tg_linked_at REAL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    photo_url TEXT,
                    language TEXT DEFAULT 'ru',
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    is_banned INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    login_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS email_tokens (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    email TEXT,
                    kind TEXT NOT NULL,              -- verify | login | reset
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_email_tokens_user
                    ON email_tokens(user_id, kind);
                CREATE TABLE IF NOT EXISTS captchas (
                    token TEXT PRIMARY KEY,
                    answer INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tg_attaches (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,     -- аккаунт с подтверждённой почтой
                    tg_id INTEGER NOT NULL,
                    profile TEXT,                 -- JSON профиля Telegram
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tg_link_nonces (
                    nonce TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    ip_hash TEXT,
                    user_agent TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_nonces (
                    nonce TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    user_id INTEGER,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    path TEXT NOT NULL,
                    vid TEXT,
                    user_id INTEGER,
                    ip_hash TEXT,
                    ua TEXT,
                    bot INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS services (
                    slug TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    title_en TEXT,
                    description TEXT,
                    icon TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    coming_soon INTEGER NOT NULL DEFAULT 1,
                    sort INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS user_services (
                    user_id INTEGER NOT NULL,
                    slug TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    config TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    PRIMARY KEY (user_id, slug)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    actor_id INTEGER,
                    action TEXT NOT NULL,
                    detail TEXT
                );
                CREATE TABLE IF NOT EXISTS digest_heads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS digest_photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    name TEXT,
                    kind TEXT DEFAULT 'post',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    photo TEXT DEFAULT '',
                    photo_name TEXT DEFAULT '',
                    targets TEXT NOT NULL DEFAULT '{}',
                    send_at REAL NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    results TEXT NOT NULL DEFAULT '{}',
                    sent_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    author_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS feedback_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    user_read_at REAL NOT NULL DEFAULT 0,
                    admin_read_at REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_fb_thread_user
                    ON feedback_threads(user_id);
                CREATE TABLE IF NOT EXISTS feedback_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL,
                    author_id INTEGER,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fb_msg ON feedback_messages(thread_id, id);
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    user_id INTEGER NOT NULL,
                    metric TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    value REAL NOT NULL,
                    threshold REAL NOT NULL,
                    window_min INTEGER NOT NULL,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_alert_user_ts ON alert_events(user_id, ts);
                CREATE INDEX IF NOT EXISTS idx_alert_cool ON alert_events(user_id, metric, symbol, ts);
                """
            )
            self._db.commit()
            have = {r["slug"] for r in self._db.execute("SELECT slug FROM services")}
            for s in DEFAULT_SERVICES:
                if s["slug"] not in have:
                    self._db.execute(
                        "INSERT INTO services(slug,title,title_en,description,icon,"
                        "enabled,coming_soon,sort) VALUES(?,?,?,?,?,?,?,?)",
                        (s["slug"], s["title"], s["title_en"], s["description"],
                         s["icon"], s["enabled"], s["coming_soon"], s["sort"]),
                    )
            # работающие сервисы — снимаем «скоро» даже на старых базах
            for slug in ("alerts", "correlations", "watchlist"):
                live = next((s for s in DEFAULT_SERVICES if s["slug"] == slug), None)
                if live:
                    self._db.execute(
                        "UPDATE services SET coming_soon=0, description=?, title=? "
                        "WHERE slug=?",
                        (live["description"], live["title"], slug),
                    )
            self._db.commit()
        self._migrate_users()
        self._migrate_digest_photos()
        self._migrate_visits()
        self._seed_digest()
        self._digest_service_live()

    def _digest_service_live(self) -> None:
        """Дневной дайджест вышел из «скоро»: один раз снимаем флаг.

        Раньше сервис стоял заглушкой (coming_soon=1). Теперь он работает,
        но не всем приятно, чтобы их настройку меняли при обновлении, поэтому
        флаг снимаем ровно один раз — дальше в кабинете и в админке решает админ.
        """
        try:
            if (self.get_setting("digest_service_live", "") or "") == "1":
                return
        except Exception:
            pass
        try:
            with self._lock:
                self._db.execute(
                    "UPDATE services SET coming_soon=0 WHERE slug='digest' AND coming_soon=1")
                self._db.commit()
        except Exception as e:
            log.debug("дайджест: сервис не переключился: %s", e)
            return
        try:
            self.set_setting("digest_service_live", "1")
        except Exception:
            pass

    def _migrate_visits(self) -> None:
        """Колонки ua/bot: чтобы отсеивать роботов и не плодить «уникальных».

        Раньше визит писал только vid, а vid выдавался каждому запросу без
        cookie — из-за этого просмотры и «уникальные» почти совпадали (каждый
        прогон краулера выглядел новым посетителем). Теперь пишем
        user-agent и признак «служебный запрос», а посетителя без cookie
        привязываем к уже выданному vid той же связки ip+ua.
        """
        try:
            with self._lock:
                cols = {r["name"] for r in
                        self._db.execute("PRAGMA table_info(visits)")}
                if cols and "ua" not in cols:
                    self._db.execute("ALTER TABLE visits ADD COLUMN ua TEXT")
                if cols and "bot" not in cols:
                    self._db.execute(
                        "ALTER TABLE visits ADD COLUMN bot INTEGER NOT NULL DEFAULT 0")
                self._db.execute("UPDATE visits SET bot=0 WHERE bot IS NULL")
                self._db.commit()
        except Exception as e:                    # noqa: BLE001
            log.debug("визиты: миграция ua/bot: %s", e)

    def _migrate_digest_photos(self) -> None:
        """Колонка kind: фото для сводки постов или для дневного дайджеста.

        Раньше картинки были одни на всё: и в посты раз в N часов, и в вечерний
        выпуск. Теперь рубрика у фото своя, а старые строки считаем постовыми —
        как они и работали.
        """
        try:
            with self._lock:
                cols = {r["name"] for r in
                        self._db.execute("PRAGMA table_info(digest_photos)")}
                if cols and "kind" not in cols:
                    self._db.execute(
                        "ALTER TABLE digest_photos ADD COLUMN kind TEXT DEFAULT 'post'")
                    self._db.commit()
                self._db.execute(
                    "UPDATE digest_photos SET kind='post' WHERE kind IS NULL OR kind=''")
                self._db.commit()
        except Exception as e:                    # noqa: BLE001
            log.debug("фото канала: миграция kind: %s", e)

    def _migrate_users(self) -> None:
        """Догоняем старые базы: почта/пароль и tg_id без NOT NULL.

        Базы прежних версий знали только Telegram (tg_id INTEGER NOT NULL),
        поэтому аккаунт по почте туда просто не влезал — колонки добавляем
        ALTER-ом, а при NOT NULL таблицу пересобираем с сохранением строк.
        """
        with self._lock:
            cols = {r["name"]: r for r in self._db.execute("PRAGMA table_info(users)")}
            for name, ddl in (
                ("email", "TEXT"),
                ("password_hash", "TEXT"),
                ("email_verified", "INTEGER NOT NULL DEFAULT 0"),
                ("email_verified_at", "REAL"),
                ("tg_linked_at", "REAL"),
                # язык, выбранный кнопкой «🌐 RU/ENG» в боте: его нельзя
                # затирать языком клиента Telegram при каждом входе
                ("lang_manual", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in cols:
                    self._db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
            self._db.commit()
            cols = {r["name"]: r for r in self._db.execute("PRAGMA table_info(users)")}
            if cols.get("tg_id") and int(cols["tg_id"]["notnull"]):
                self._db.executescript(
                    """
                    ALTER TABLE users RENAME TO users_legacy;
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tg_id INTEGER UNIQUE,
                        email TEXT,
                        password_hash TEXT,
                        email_verified INTEGER NOT NULL DEFAULT 0,
                        email_verified_at REAL,
                        tg_linked_at REAL,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        photo_url TEXT,
                        language TEXT DEFAULT 'ru',
                        is_admin INTEGER NOT NULL DEFAULT 0,
                        is_banned INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        login_count INTEGER NOT NULL DEFAULT 0
                    );
                    INSERT INTO users(id, tg_id, email, password_hash, email_verified,
                        email_verified_at, tg_linked_at, username, first_name, last_name,
                        photo_url, language, is_admin, is_banned, created_at, last_seen,
                        login_count)
                    SELECT id, tg_id, NULL, NULL, 0, NULL, NULL, username, first_name,
                        last_name, photo_url, language, is_admin, is_banned, created_at,
                        last_seen, login_count FROM users_legacy;
                    DROP TABLE users_legacy;
                    """
                )
                self._db.commit()
                log.info("Схема users обновлена: Telegram-аккаунты сохранены, "
                         "регистрация по почте включена")
            self._db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email"
                " ON users(email) WHERE email IS NOT NULL AND email != ''"
            )
            self._db.commit()

    # ----- users (почта) --------------------------------------------------
    def _admin_flag(self, tg_id: Optional[int] = None, email: str = "") -> int:
        if tg_id and int(tg_id) in self.admin_ids:
            return 1
        if email and normalize_email(email) in self.admin_emails:
            return 1
        return 0

    def create_email_user(self, email: str, password_hash: str = "",
                          first_name: str = "", language: str = "ru") -> Dict[str, Any]:
        """Регистрация по почте. Адрес занят → {"ok": False, "error": "taken"}."""
        email = normalize_email(email)
        if not valid_email(email):
            return {"ok": False, "error": "bad_email"}
        now = _now()
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row and row["email_verified"]:
                return {"ok": False, "error": "taken"}
            resumed = bool(row)
            is_admin = self._admin_flag(email=email)
            if row:
                # регистрация оборвалась на подтверждении — даём начать заново
                self._db.execute(
                    "UPDATE users SET password_hash=?, first_name=?, language=?,"
                    " is_admin=?, last_seen=? WHERE id=?",
                    (password_hash or row["password_hash"], (first_name or "")[:64],
                     (language or "ru")[:8], is_admin or int(row["is_admin"]), now,
                     int(row["id"])),
                )
            else:
                self._db.execute(
                    "INSERT INTO users(tg_id,email,password_hash,email_verified,"
                    "first_name,language,is_admin,is_banned,created_at,last_seen,login_count)"
                    " VALUES(NULL,?,?,0,?,?,?,0,?,?,0)",
                    (email, password_hash, (first_name or "")[:64],
                     (language or "ru")[:8], is_admin, now, now),
                )
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return {"ok": True, "user": public_user(row), "resumed": resumed}

    def attach_email(self, user_id: int, email: str, password_hash: str = "") -> Dict[str, Any]:
        """Привязать почту к уже существующему аккаунту.

        Так закрывается дубль: человек пришёл из Telegram (аккаунт уже есть),
        а потом указал почту — это тот же аккаунт, а не второй. Если у адреса
        осталась незавершённая регистрация (почта введена, но не подтверждена),
        переносим её сюда же.

        Возвращает {"ok": True, "user": …} либо {"ok": False, "error": "taken"}.
        """
        email = normalize_email(email)
        if not valid_email(email):
            return {"ok": False, "error": "bad_email"}
        user_id = int(user_id)
        merged = False
        with self._lock:
            me = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not me:
                return {"ok": False, "error": "unknown"}
            other = self._db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if other and int(other["id"]) != user_id:
                if other["email_verified"]:
                    # адрес подтверждён у другого аккаунта: это чужой кабинет
                    return {"ok": False, "error": "taken"}
                self._merge_users_locked(int(other["id"]), user_id)
                merged = True
            keep_ok = bool(me["email_verified"]) and (me["email"] or "") == email
            self._db.execute(
                "UPDATE users SET email=?,"
                " password_hash=COALESCE(NULLIF(?, ''), password_hash),"
                " email_verified=?, email_verified_at=?, last_seen=? WHERE id=?",
                (email, password_hash or "", 1 if keep_ok else 0,
                 me["email_verified_at"] if keep_ok else None, _now(), user_id),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        self.audit(user_id, "email_attach", f"{email} merged={int(merged)}")
        return {"ok": True, "user": public_user(row), "merged": merged}

    # ----- капча (арифметика на регистрацию) --------------------------------
    def new_captcha(self, answer: int, ttl: float = CAPTCHA_TTL) -> str:
        """Задача-капча: храним только ответ, наружу отдаём случайный токен."""
        token = secrets.token_urlsafe(18)
        now = _now()
        with self._lock:
            # на забытых базах колонки может не быть — добавляем молча
            try:
                self._db.execute("ALTER TABLE captchas ADD COLUMN attempts"
                                 " INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            self._db.execute("DELETE FROM captchas WHERE expires_at<?", (now - 3600,))
            self._db.execute(
                "INSERT INTO captchas(token,answer,created_at,expires_at,used)"
                " VALUES(?,?,?,?,0)",
                (token, int(answer), now, now + float(ttl)),
            )
            self._db.commit()
        return token

    def check_captcha(self, token: str, answer: Any, attempts: int = 3) -> Tuple[bool, str]:
        """Проверка ответа: 3 попытки на задачу, потом токен сгорает."""
        token = str(token or "").strip()
        if not token:
            return False, "unknown"
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM captchas WHERE token=?", (token,)
            ).fetchone()
            if not row:
                return False, "unknown"
            if row["used"]:
                return False, "used"
            if row["expires_at"] < _now():
                return False, "expired"
            try:
                expect = int(row["answer"])
            except (TypeError, ValueError):
                return False, "unknown"
            try:
                got = int(str(answer).strip())
            except (TypeError, ValueError):
                got = None
            if got == expect:
                self._db.execute("UPDATE captchas SET used=1 WHERE token=?", (token,))
                self._db.commit()
                return True, ""
            try:
                seen = int(row["attempts"] or 0) + 1
            except (IndexError, TypeError, ValueError):
                seen = 1
            burn = 1 if seen >= max(1, int(attempts)) else 0
            self._db.execute(
                "UPDATE captchas SET attempts=?, used=? WHERE token=?", (seen, burn, token))
            self._db.commit()
            return False, ("used" if burn else "wrong")

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        email = normalize_email(email)
        if not email:
            return None
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return public_user(row) if row else None

    def set_user_password(self, user_id: int, password_hash: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (password_hash, int(user_id)),
            )
            self._db.commit()
        return cur.rowcount > 0

    def set_user_name(self, user_id: int, first_name: str) -> None:
        with self._lock:
            self._db.execute("UPDATE users SET first_name=? WHERE id=?",
                             ((first_name or "")[:64], int(user_id)))
            self._db.commit()

    def mark_email_verified(self, user_id: int, promote_admin: bool = True) -> Optional[Dict[str, Any]]:
        """Почта подтверждена (клик по ссылке из письма) — вход открывается."""
        now = _now()
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
            if not row:
                return None
            email = row["email"] or ""
            is_admin = int(row["is_admin"]) or (self._admin_flag(email=email) if promote_admin else 0)
            self._db.execute(
                "UPDATE users SET email_verified=1, email_verified_at=?, is_admin=?,"
                " last_seen=? WHERE id=?",
                (now, is_admin, now, int(user_id)),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
        return public_user(row)

    # ----- письма: одноразовые токены --------------------------------------
    def new_email_token(self, user_id: int, kind: str, email: str = "") -> str:
        ttl = EMAIL_TOKEN_TTL.get(kind, 3600)
        now = _now()
        token = secrets.token_urlsafe(32)
        with self._lock:
            # старые ссылки того же назначения больше не нужны
            self._db.execute(
                "UPDATE email_tokens SET consumed=1 WHERE user_id=? AND kind=? AND consumed=0",
                (int(user_id), kind),
            )
            self._db.execute("DELETE FROM email_tokens WHERE expires_at<?", (now - 7 * 86400,))
            self._db.execute(
                "INSERT INTO email_tokens(token,user_id,email,kind,created_at,expires_at,consumed)"
                " VALUES(?,?,?,?,?,?,0)",
                (token, int(user_id), normalize_email(email), kind, now, now + ttl),
            )
            self._db.commit()
        return token

    def email_token_user(self, token: str, kind: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
        """Кто владелец токена. Второе значение — "" или код ошибки."""
        token = str(token or "").strip()
        if not token:
            return None, "unknown"
        with self._lock:
            row = self._db.execute("SELECT * FROM email_tokens WHERE token=?", (token,)).fetchone()
            if not row or (kind and row["kind"] != kind):
                return None, "unknown"
            if row["consumed"]:
                return None, "used"
            if row["expires_at"] < _now():
                return None, "expired"
            user = self._db.execute("SELECT * FROM users WHERE id=?", (int(row["user_id"]),)).fetchone()
        if not user:
            return None, "unknown"
        return public_user(user), ""

    def consume_email_token(self, token: str, kind: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
        user, err = self.email_token_user(token, kind)
        if err:
            return None, err
        with self._lock:
            self._db.execute("UPDATE email_tokens SET consumed=1 WHERE token=?", (str(token),))
            self._db.commit()
        return user, ""

    def email_tokens_recent(self, user_id: Optional[int] = None, email: str = "",
                            kind: str = "", since: float = 0) -> int:
        """Сколько писем уже ушло — для ограничения частоты отправки."""
        where, args = ["created_at>=?"], [float(since or 0)]
        if user_id:
            where.append("user_id=?")
            args.append(int(user_id))
        if email:
            where.append("email=?")
            args.append(normalize_email(email))
        if kind:
            where.append("kind=?")
            args.append(kind)
        with self._lock:
            n = self._db.execute(
                "SELECT COUNT(*) FROM email_tokens WHERE " + " AND ".join(where), args
            ).fetchone()[0]
        return int(n)

    # ----- привязка Telegram к аккаунту ------------------------------------
    def new_link_nonce(self, user_id: int) -> str:
        """Одноразовая метка для deep-link бота: t.me/<bot>?start=link_<nonce>."""
        now = _now()
        nonce = secrets.token_urlsafe(16)
        with self._lock:
            self._db.execute(
                "UPDATE tg_link_nonces SET consumed=1 WHERE user_id=? AND consumed=0",
                (int(user_id),),
            )
            self._db.execute(
                "INSERT INTO tg_link_nonces(nonce,user_id,created_at,expires_at,consumed)"
                " VALUES(?,?,?,?,0)",
                (nonce, int(user_id), now, now + LINK_NONCE_TTL),
            )
            self._db.commit()
        return nonce

    def link_nonce_status(self, nonce: str) -> Dict[str, Any]:
        """Состояние привязки — кабинет опрашивает его после открытия бота."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tg_link_nonces WHERE nonce=?", (str(nonce or ""),)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "unknown"}
            user_id = int(row["user_id"])
            if not row["consumed"]:
                if row["expires_at"] < _now():
                    return {"ok": False, "error": "expired"}
                return {"ok": False, "pending": True}
            user = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {"ok": True, "user": public_user(user) if user else None}

    def _link_tg_locked(self, user_id: int, tg: Dict[str, Any]) -> Dict[str, Any]:
        """Привязать Telegram к аккаунту (замок уже держим).

        Если у этого Telegram уже есть отдельный «телеграмный» аккаунт (человек
        писал боту раньше) — переносим его данные сюда, чтобы не появилось двух
        половин одного человека. Аккаунт с почтой у другого человека не трогаем.
        """
        # Внимание: бот зовёт нас с публичным профилем, где "id" — это id записи
        # в базе, а Telegram-id лежит в "tg_id". У сырого апдейта Telegram —
        # наоборот. Поэтому сначала tg_id, и только потом id.
        tg_id = int(tg.get("tg_id") or tg.get("id") or 0)
        if not tg_id:
            return {"ok": False, "error": "no_tg"}
        user_id = int(user_id)
        other = self._db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        merged = False
        if other and int(other["id"]) != user_id:
            if (other["email"] or "").strip():
                return {"ok": False, "error": "taken"}
            self._merge_users_locked(int(other["id"]), user_id)
            merged = True
        self._db.execute(
            "UPDATE users SET tg_id=?, tg_linked_at=?, username=?, first_name=?,"
            " last_name=?, photo_url=?, language=?, is_admin=?, last_seen=?"
            " WHERE id=?",
            (tg_id, _now(),
             (tg.get("username") or "")[:64], (tg.get("first_name") or "")[:64],
             (tg.get("last_name") or "")[:64], (tg.get("photo_url") or "")[:500],
             (tg.get("language_code") or tg.get("language") or "ru")[:8],
             self._admin_flag(tg_id=tg_id), _now(), user_id),
        )
        self._db.commit()
        row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {"ok": True, "user": public_user(row), "merged": merged}

    def link_tg_to_user(self, user_id: int, tg: Dict[str, Any]) -> Dict[str, Any]:
        """Привязать Telegram к конкретному аккаунту (вход виджетом, вход ботом)."""
        want = self._admin_flag(tg_id=int(tg.get("tg_id") or tg.get("id") or 0))
        with self._lock:
            r = self._link_tg_locked(int(user_id), tg)
            uid = (r.get("user") or {}).get("id")
            if r.get("ok") and want and uid:
                self._db.execute("UPDATE users SET is_admin=1 WHERE id=?", (int(uid),))
                self._db.commit()
                r["user"] = public_user(
                    self._db.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone())
        if r.get("ok"):
            tg_id = int(tg.get("tg_id") or tg.get("id") or 0)
            self.audit((r.get("user") or {}).get("id"), "tg_link",
                       f"tg_id={tg_id} via=api merged={int(bool(r.get('merged')))}")
        return r

    def confirm_tg_link(self, nonce: str, tg: Dict[str, Any]) -> Dict[str, Any]:
        """Бот получил /start link_<nonce>: привязываем Telegram к аккаунту.

        Если у этого Telegram уже есть отдельный «телеграмный» аккаунт (человек
        писал боту до привязки) — переносим его данные в аккаунт с почтой,
        чтобы не было двух половин одного человека. Аккаунт с почтой у другого
        человека не трогаем: такая привязка запрещена.
        """
        nonce = str(nonce or "").strip()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tg_link_nonces WHERE nonce=?", (nonce,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "unknown"}
            if row["consumed"]:
                return {"ok": False, "error": "used"}
            if row["expires_at"] < _now():
                return {"ok": False, "error": "expired"}
            target_id = int(row["user_id"])
            r = self._link_tg_locked(target_id, tg)
            if r.get("ok"):
                self._db.execute(
                    "UPDATE tg_link_nonces SET consumed=1 WHERE nonce=?", (nonce,)
                )
                self._db.commit()
        if r.get("ok"):
            tg_id = int(tg.get("tg_id") or tg.get("id") or 0)
            self.audit(target_id, "tg_link", f"tg_id={tg_id} via=nonce"
                                            f" merged={int(bool(r.get('merged')))}")
        return r

    def _merge_users_locked(self, src_id: int, dst_id: int) -> None:
        """Слияние «телеграмного» аккаунта в аккаунт с почтой (под замком)."""
        src_id, dst_id = int(src_id), int(dst_id)
        for r in self._db.execute(
                "SELECT slug, enabled, config, created_at FROM user_services WHERE user_id=?",
                (src_id,)).fetchall():
            self._db.execute(
                "INSERT INTO user_services(user_id,slug,enabled,config,created_at)"
                " VALUES(?,?,?,?,?) ON CONFLICT(user_id,slug) DO NOTHING",
                (dst_id, r["slug"], r["enabled"], r["config"], r["created_at"]),
            )
        self._db.execute("DELETE FROM user_services WHERE user_id=?", (src_id,))
        for table in ("alert_events", "visits", "sessions", "email_tokens"):
            self._db.execute(f"UPDATE {table} SET user_id=? WHERE user_id=?", (dst_id, src_id))
        src = self._db.execute("SELECT * FROM users WHERE id=?", (src_id,)).fetchone()
        if src and int(src["is_admin"]):
            self._db.execute("UPDATE users SET is_admin=1 WHERE id=?", (dst_id,))
        if not src:
            return
        # «телеграмный» аккаунт был первой записью человека: имя/фото не теряем
        self._db.execute(
            "UPDATE users SET first_name=CASE WHEN first_name IS NULL OR first_name=''"
            " THEN ? ELSE first_name END,"
            " last_name=CASE WHEN last_name IS NULL OR last_name='' THEN ? ELSE last_name END,"
            " photo_url=CASE WHEN photo_url IS NULL OR photo_url='' THEN ? ELSE photo_url END,"
            " created_at=MIN(created_at, ?) WHERE id=?",
            (src["first_name"] or "", src["last_name"] or "", src["photo_url"] or "",
             float(src["created_at"] or _now()), dst_id),
        )
        self._db.execute("DELETE FROM users WHERE id=?", (src_id,))
        log.info("Telegram-аккаунт %s слит в аккаунт %s", src_id, dst_id)

    def unlink_telegram(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Отвязать Telegram: аккаунт остаётся, письма и вход по почте работают."""
        user_id = int(user_id)
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return None
            if not row["tg_id"]:
                return public_user(row)
            tg_id = int(row["tg_id"])
            self._db.execute(
                "UPDATE users SET tg_id=NULL, tg_linked_at=NULL, is_admin=? WHERE id=?",
                (self._admin_flag(email=row["email"] or ""), user_id),
            )
            # отвязываем только «свой» канал: сессии бота не нужны, он их не держит
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        self.audit(user_id, "tg_unlink", f"tg_id={tg_id}")
        return public_user(row)

    def set_user_language(self, user_id: int, lang: str) -> Dict[str, Any]:
        """Язык, выбранный в боте кнопкой «🌐 RU/ENG».

        Помечаем выбор флагом lang_manual: он главнее языка клиента Telegram,
        который приходит при каждом сообщении и иначе затирал бы выбор.
        """
        code = "en" if str(lang or "").strip().lower().startswith("en") else "ru"
        with self._lock:
            self._db.execute(
                "UPDATE users SET language=?, lang_manual=1 WHERE id=?",
                (code, int(user_id)))
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?",
                                   (int(user_id),)).fetchone()
        self.audit(int(user_id), "set_language", code)
        return public_user(row)

    # ----- users (Telegram) -----------------------------------------------
    def upsert_telegram_user(self, tg: Dict[str, Any]) -> Dict[str, Any]:
        tg_id = int(tg["id"] if "id" in tg else tg["tg_id"])
        username = (tg.get("username") or "")[:64]
        first = (tg.get("first_name") or "")[:64]
        last = (tg.get("last_name") or "")[:64]
        photo = (tg.get("photo_url") or "")[:500]
        lang = (tg.get("language_code") or tg.get("language") or "ru")[:8]
        now = _now()
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
            is_admin = 1 if (tg_id in self.admin_ids or (row and int(row["is_admin"]))) else 0
            if row:
                self._db.execute(
                    # Язык обновляем только у тех, кто не выбирал его сам:
                    # иначе клиент Telegram с русской локалью возвращал бы
                    # английский интерфейс к русскому при каждом сообщении.
                    "UPDATE users SET username=?, first_name=?, last_name=?, photo_url=?,"
                    " language=?, is_admin=?, last_seen=?, login_count=login_count+1"
                    " WHERE tg_id=?",
                    (username or row["username"], first or row["first_name"],
                     last or row["last_name"], photo or row["photo_url"],
                     (row["language"] if int(row["lang_manual"] or 0) else
                      (lang or row["language"])), is_admin, now, tg_id),
                )
            else:
                self._db.execute(
                    "INSERT INTO users(tg_id,username,first_name,last_name,photo_url,"
                    "language,is_admin,is_banned,created_at,last_seen,login_count)"
                    " VALUES(?,?,?,?,?,?,?,0,?,?,1)",
                    (tg_id, username, first, last, photo, lang, is_admin, now, now),
                )
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return public_user(row)

    # ----- привязка Telegram по ссылке из письма ----------------------------
    def new_tg_attach(self, user_id: int, tg: Dict[str, Any], ttl: float = 2 * 3600) -> str:
        """Письмо-подтверждение: «этот Telegram принадлежит владельцу почты»."""
        token = secrets.token_urlsafe(32)
        now = _now()
        tg_id = int(tg.get("tg_id") or tg.get("id") or 0)
        profile = {k: tg.get(k) for k in
                   ("username", "first_name", "last_name", "photo_url",
                    "language_code", "language")}
        with self._lock:
            self._db.execute(
                "UPDATE tg_attaches SET consumed=1 WHERE user_id=? AND consumed=0",
                (int(user_id),),
            )
            self._db.execute("DELETE FROM tg_attaches WHERE expires_at<?", (now - 7 * 86400,))
            self._db.execute(
                "INSERT INTO tg_attaches(token,user_id,tg_id,profile,created_at,expires_at,"
                "consumed) VALUES(?,?,?,?,?,?,0)",
                (token, int(user_id), tg_id, json.dumps(profile, ensure_ascii=False),
                 now, now + float(ttl)),
            )
            self._db.commit()
        return token

    def tg_attach_info(self, token: str) -> Tuple[Optional[Dict[str, Any]], str]:
        token = str(token or "").strip()
        if not token:
            return None, "unknown"
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tg_attaches WHERE token=?", (token,)).fetchone()
            if not row:
                return None, "unknown"
            if row["consumed"]:
                return None, "used"
            if row["expires_at"] < _now():
                return None, "expired"
            user = self._db.execute(
                "SELECT * FROM users WHERE id=?", (int(row["user_id"]),)).fetchone()
            if not user:
                return None, "unknown"
        return {"user": public_user(user), "tg_id": int(row["tg_id"]),
                "profile": row["profile"] or "{}"}, ""

    def confirm_tg_attach(self, token: str) -> Dict[str, Any]:
        """Ссылка из письма: привязываем Telegram к аккаунту с этой почтой."""
        info, err = self.tg_attach_info(token)
        if err:
            return {"ok": False, "error": err}
        profile = {}
        try:
            profile = json.loads(info.get("profile") or "{}")
        except Exception:
            profile = {}
        tg = dict(profile or {})
        tg["tg_id"] = int(info["tg_id"])
        with self._lock:
            r = self._link_tg_locked(int(info["user"]["id"]), tg)
            if r.get("ok"):
                self._db.execute(
                    "UPDATE tg_attaches SET consumed=1 WHERE token=?", (str(token),))
                self._db.commit()
        if r.get("ok"):
            self.audit((r.get("user") or {}).get("id"), "tg_link",
                       f"tg_id={tg['tg_id']} via=mail merged={int(bool(r.get('merged')))}")
        return r

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
        return public_user(row) if row else None

    def get_user_by_tg(self, tg_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE tg_id=?", (int(tg_id),)).fetchone()
        return public_user(row) if row else None

    def touch_user(self, user_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE users SET last_seen=? WHERE id=?", (_now(), int(user_id)))
            self._db.commit()

    def set_banned(self, user_id: int, banned: bool, actor_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._db.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if banned else 0, int(user_id)))
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
        if row:
            self.audit(actor_id, "ban" if banned else "unban", f"user_id={user_id} tg_id={row['tg_id']}")
            if banned:
                self.drop_user_sessions(int(user_id))
            return public_user(row)
        return None

    def set_admin(self, user_id: int, admin: bool, actor_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._db.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if admin else 0, int(user_id)))
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
        if row:
            self.audit(actor_id, "admin_on" if admin else "admin_off", f"user_id={user_id}")
            return public_user(row)
        return None

    def list_users(self, q: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        q = (q or "").strip()
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if q:
                like = f"%{q}%"
                where = ("username LIKE ? OR first_name LIKE ? OR last_name LIKE ?"
                         " OR email LIKE ? OR CAST(tg_id AS TEXT) LIKE ?")
                args = (like, like, like, like, like)
                rows = self._db.execute(
                    f"SELECT * FROM users WHERE {where} ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                    args + (limit, offset),
                ).fetchall()
                matched = self._db.execute(
                    f"SELECT COUNT(*) FROM users WHERE {where}", args
                ).fetchone()[0]
            else:
                rows = self._db.execute(
                    "SELECT * FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                matched = total
        return {"total": total, "matched": matched, "users": [public_user(r) for r in rows]}

    def user_counts(self) -> Dict[str, int]:
        now = _now()
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            admins = self._db.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]
            banned = self._db.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
            today = self._db.execute(
                "SELECT COUNT(*) FROM users WHERE last_seen>=?", (now - 86400,)
            ).fetchone()[0]
            new_today = self._db.execute(
                "SELECT COUNT(*) FROM users WHERE created_at>=?", (now - 86400,)
            ).fetchone()[0]
        return {"total": total, "admins": admins, "banned": banned,
                "active_24h": today, "new_24h": new_today}

    def admin_tg_ids(self) -> List[int]:
        """Telegram-id админов (не в бане) — для служебных уведомлений бота."""
        with self._lock:
            rows = self._db.execute(
                "SELECT tg_id FROM users WHERE is_admin=1 AND is_banned=0"
                " AND tg_id IS NOT NULL"
            ).fetchall()
        return [int(r["tg_id"]) for r in rows]

    # ----- sessions / nonces ----------------------------------------------
    def create_session(self, user_id: int, ip_hash: str = "", ua: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        with self._lock:
            self._db.execute(
                "INSERT INTO sessions(token,user_id,created_at,expires_at,ip_hash,user_agent)"
                " VALUES(?,?,?,?,?,?)",
                (token, int(user_id), now, now + SESSION_DAYS * 86400, ip_hash, (ua or "")[:180]),
            )
            self._db.commit()
        return token

    def user_by_session(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        now = _now()
        with self._lock:
            row = self._db.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id"
                " WHERE s.token=? AND s.expires_at>?",
                (token, now),
            ).fetchone()
        if not row:
            return None
        if row["is_banned"]:
            return None
        return public_user(row)

    def drop_session(self, token: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE token=?", (token,))
            self._db.commit()

    def drop_user_sessions(self, user_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE user_id=?", (int(user_id),))
            self._db.commit()

    def new_nonce(self) -> str:
        nonce = secrets.token_urlsafe(16)
        now = _now()
        with self._lock:
            self._db.execute(
                "INSERT INTO login_nonces(nonce,created_at,expires_at,consumed) VALUES(?,?,?,0)",
                (nonce, now, now + NONCE_TTL),
            )
            self._db.commit()
        return nonce

    def confirm_nonce(self, nonce: str, user_id: int) -> bool:
        now = _now()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM login_nonces WHERE nonce=?", (nonce,)
            ).fetchone()
            if not row or row["consumed"] or row["expires_at"] < now:
                return False
            self._db.execute(
                "UPDATE login_nonces SET user_id=?, consumed=1 WHERE nonce=?",
                (int(user_id), nonce),
            )
            self._db.commit()
        return True

    def nonce_status(self, nonce: str) -> Dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM login_nonces WHERE nonce=?", (nonce,)
            ).fetchone()
        if not row:
            return {"ok": False, "error": "unknown"}
        if row["expires_at"] < _now() and not row["consumed"]:
            return {"ok": False, "error": "expired"}
        if row["consumed"] and row["user_id"]:
            return {"ok": True, "user_id": int(row["user_id"])}
        return {"ok": False, "pending": True}

    # ----- visits ---------------------------------------------------------
    def record_visit(self, path: str, vid: str, user_id: Optional[int], ip_hash: str,
                     ua: str = "", bot: bool = False) -> None:
        """Записать просмотр страницы.

        ``bot`` — служебный запрос (краулер, превью мессенджера, скрипт): в
        счётчики просмотров и посетителей он не идёт, но хранится — админ
        видит, сколько такого шума отсеяно.
        """
        path = (path or "/")[:120]
        with self._lock:
            self._db.execute(
                "INSERT INTO visits(ts,path,vid,user_id,ip_hash,ua,bot)"
                " VALUES(?,?,?,?,?,?,?)",
                (_now(), path, ("" if bot else (vid or ""))[:40], user_id,
                 ip_hash, (ua or "")[:180], 1 if bot else 0),
            )
            # не копим бесконечно: раз в ~200 визитов чистим старше 90 дней
            if secrets.randbelow(200) == 0:
                self._db.execute("DELETE FROM visits WHERE ts<?", (_now() - 90 * 86400,))
            self._db.commit()

    def visit_vid(self, ip_hash: str, ua: str, window_sec: int = 86400) -> str:
        """vid, уже выданный этой связке ip+ua: браузер без cookie не «множится».

        Возвращаем последний vid за окно (по умолчанию сутки) — так гость,
        который не сохраняет cookie (или скрипт, прикинувшийся браузером),
        в уникальных считается один раз, а не каждой страницей.
        """
        if not ip_hash:
            return ""
        try:
            since = _now() - max(60, int(window_sec))
            with self._lock:
                row = self._db.execute(
                    "SELECT vid FROM visits WHERE ip_hash=? AND COALESCE(ua,'')=?"
                    " AND bot=0 AND COALESCE(vid,'')!='' AND ts>=?"
                    " ORDER BY ts DESC LIMIT 1",
                    (ip_hash, (ua or "")[:180], since),
                ).fetchone()
        except Exception as e:                    # noqa: BLE001
            log.debug("визиты: поиск vid: %s", e)
            return ""
        return str(row["vid"] or "") if row else ""

    def visit_stats(self, days: int = 14) -> Dict[str, Any]:
        days = max(1, min(int(days), 90))
        since = _now() - days * 86400
        day0 = _now() - (_now() % 86400)
        with self._lock:
            today_views = self._db.execute(
                "SELECT COUNT(*) FROM visits WHERE ts>=? AND bot=0", (day0,)
            ).fetchone()[0]
            today_uniques = self._db.execute(
                "SELECT COUNT(DISTINCT vid) FROM visits WHERE ts>=? AND bot=0"
                " AND COALESCE(vid,'')!=''", (day0,)
            ).fetchone()[0]
            today_bots = self._db.execute(
                "SELECT COUNT(*) FROM visits WHERE ts>=? AND bot=1", (day0,)
            ).fetchone()[0]
            rows = self._db.execute(
                "SELECT strftime('%Y-%m-%d', ts, 'unixepoch') AS day,"
                " SUM(CASE WHEN bot=0 THEN 1 ELSE 0 END) AS views,"
                " COUNT(DISTINCT CASE WHEN bot=0 THEN vid END) AS uniques,"
                " SUM(CASE WHEN bot=1 THEN 1 ELSE 0 END) AS bots"
                " FROM visits WHERE ts>=? GROUP BY day ORDER BY day",
                (since,),
            ).fetchall()
            top_paths = self._db.execute(
                "SELECT path, COUNT(*) AS n FROM visits WHERE ts>=? AND bot=0"
                " GROUP BY path ORDER BY n DESC LIMIT 8",
                (since,),
            ).fetchall()
        by_day = [{"day": r["day"], "views": r["views"], "uniques": r["uniques"],
                   "bots": r["bots"]} for r in rows]
        return {
            "today_views": today_views,
            "today_uniques": today_uniques,
            "today_bots": today_bots,
            "days": by_day,
            "paths": [{"path": r["path"], "n": r["n"]} for r in top_paths],
        }

    # ----- services / settings / audit ------------------------------------
    def list_services(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        with self._lock:
            q = "SELECT * FROM services"
            if not include_disabled:
                q += " WHERE enabled=1"
            q += " ORDER BY sort, slug"
            rows = self._db.execute(q).fetchall()
        return [dict(r) for r in rows]

    def user_service_slugs(self, user_id: int) -> List[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT slug FROM user_services WHERE user_id=? AND enabled=1",
                (int(user_id),),
            ).fetchall()
        return [r["slug"] for r in rows]

    def toggle_user_service(self, user_id: int, slug: str, enabled: bool) -> Dict[str, Any]:
        now = _now()
        with self._lock:
            svc = self._db.execute("SELECT * FROM services WHERE slug=?", (slug,)).fetchone()
            if not svc:
                return {"ok": False, "error": "unknown_service"}
            row = self._db.execute(
                "SELECT * FROM user_services WHERE user_id=? AND slug=?",
                (int(user_id), slug),
            ).fetchone()
            if row:
                self._db.execute(
                    "UPDATE user_services SET enabled=? WHERE user_id=? AND slug=?",
                    (1 if enabled else 0, int(user_id), slug),
                )
            else:
                self._db.execute(
                    "INSERT INTO user_services(user_id,slug,enabled,config,created_at)"
                    " VALUES(?,?,?,?,?)",
                    (int(user_id), slug, 1 if enabled else 0, "{}", now),
                )
            self._db.commit()
        return {"ok": True, "slug": slug, "enabled": bool(enabled),
                "coming_soon": bool(svc["coming_soon"])}

    def update_service(self, slug: str, **fields) -> Optional[Dict[str, Any]]:
        allowed = {"enabled", "coming_soon", "title", "description", "icon", "sort"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            args.append(v)
        if not sets:
            return None
        args.append(slug)
        with self._lock:
            self._db.execute(f"UPDATE services SET {', '.join(sets)} WHERE slug=?", args)
            self._db.commit()
            row = self._db.execute("SELECT * FROM services WHERE slug=?", (slug,)).fetchone()
        return dict(row) if row else None

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str, actor_id: Optional[int] = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._db.commit()
        self.audit(actor_id, "setting", f"{key}={value[:80]}")

    def all_settings(self) -> Dict[str, str]:
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def audit(self, actor_id: Optional[int], action: str, detail: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO audit(ts,actor_id,action,detail) VALUES(?,?,?,?)",
                (_now(), actor_id, action[:40], (detail or "")[:400]),
            )
            self._db.commit()

    def recent_audit(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def digest_photo_dir(self) -> str:
        d = os.path.join(os.path.dirname(os.path.abspath(self.path)) or ".", "channel")
        os.makedirs(d, exist_ok=True)
        return d

    def _seed_digest(self) -> None:
        from channel_digest import DEFAULT_HEAD_TEMPLATES, list_images
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM digest_heads").fetchone()[0]
            if n == 0:
                now = _now()
                self._db.executemany(
                    "INSERT INTO digest_heads(text, created_at) VALUES(?,?)",
                    [(t, now) for t in DEFAULT_HEAD_TEMPLATES],
                )
            n = self._db.execute("SELECT COUNT(*) FROM digest_photos").fetchone()[0]
            if n == 0:
                now = _now()
                for path in list_images():
                    self._db.execute(
                        "INSERT INTO digest_photos(path, name, kind, created_at)"
                        " VALUES(?,?,?,?)",
                        (path, os.path.basename(path), "post", now),
                    )
            self._db.commit()

    def list_digest_heads(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text, created_at FROM digest_heads ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_digest_head(self, text: str, actor_id: Optional[int] = None) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        if len(text) > 240:
            text = text[:240]
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM digest_heads").fetchone()[0]
            if n >= 80:
                return {"ok": False, "error": "limit"}
            cur = self._db.execute(
                "INSERT INTO digest_heads(text, created_at) VALUES(?,?)",
                (text, _now()),
            )
            self._db.commit()
            pid = int(cur.lastrowid)
        self.audit(actor_id, "digest_head_add", text[:80])
        return {"ok": True, "id": pid, "text": text}

    def delete_digest_head(self, head_id: int, actor_id: Optional[int] = None) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM digest_heads WHERE id=?", (int(head_id),))
            self._db.commit()
            ok = cur.rowcount > 0
        if ok:
            self.audit(actor_id, "digest_head_del", str(head_id))
        return ok

    def list_digest_photos(self, kind: str = "") -> List[Dict[str, Any]]:
        """Фото канала: ``kind`` — "post" (сводка), "digest" (дневной выпуск).

        Пустой ``kind`` — все фото: так их видит админка и старые вызовы.
        """
        kind = (kind or "").strip()
        with self._lock:
            if kind:
                rows = self._db.execute(
                    "SELECT id, path, name, kind, created_at FROM digest_photos"
                    " WHERE COALESCE(kind,'post')=? ORDER BY id", (kind,)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, path, name, kind, created_at FROM digest_photos"
                    " ORDER BY id"
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["exists"] = bool(d.get("path") and os.path.isfile(d["path"]))
            out.append(d)
        return out

    def set_digest_photo_kind(self, photo_id: int, kind: str,
                              actor_id: Optional[int] = None) -> bool:
        """Перенести фото в другую рубрику: сводка ⇄ дневной дайджест."""
        kind = "digest" if str(kind or "").strip().lower() == "digest" else "post"
        with self._lock:
            cur = self._db.execute("UPDATE digest_photos SET kind=? WHERE id=?",
                                   (kind, int(photo_id)))
            self._db.commit()
        if not cur.rowcount:
            return False
        self.audit(actor_id, "digest_photo_kind", f"{photo_id}→{kind}")
        return True

    def add_digest_photo(self, data: bytes, filename: str = "",
                         actor_id: Optional[int] = None,
                         kind: str = "post") -> Dict[str, Any]:
        data = data or b""
        if len(data) < 24:
            return {"ok": False, "error": "empty"}
        if len(data) > 12_000_000:
            return {"ok": False, "error": "too_big"}
        ext = ""
        if data[:2] == b"\xff\xd8":
            ext = ".jpg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ext = ".webp"
        if not ext:
            return {"ok": False, "error": "not_image"}
        kind = "digest" if str(kind or "").strip().lower() == "digest" else "post"
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM digest_photos").fetchone()[0]
            if n >= 40:
                return {"ok": False, "error": "limit"}
        folder = self.digest_photo_dir()
        name = f"{int(_now() * 1000)}_{secrets.token_hex(3)}{ext}"
        path = os.path.join(folder, name)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            return {"ok": False, "error": str(e)[:80]}
        orig = os.path.basename(filename or name)[:80]
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO digest_photos(path, name, kind, created_at)"
                " VALUES(?,?,?,?)",
                (path, orig, kind, _now()),
            )
            self._db.commit()
            pid = int(cur.lastrowid)
        self.audit(actor_id, "digest_photo_add", f"{kind}:{orig}")
        return {"ok": True, "id": pid, "path": path, "name": orig, "kind": kind}

    def get_digest_photo(self, photo_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT id, path, name, kind, created_at FROM digest_photos WHERE id=?",
                (int(photo_id),),
            ).fetchone()
        return dict(row) if row else None

    def delete_digest_photo(self, photo_id: int, actor_id: Optional[int] = None) -> bool:
        row = self.get_digest_photo(photo_id)
        if not row:
            return False
        with self._lock:
            self._db.execute("DELETE FROM digest_photos WHERE id=?", (int(photo_id),))
            self._db.commit()
        path = os.path.abspath(row.get("path") or "")
        root = os.path.abspath(self.digest_photo_dir())
        if path.startswith(root + os.sep):
            try:
                os.remove(path)
            except OSError:
                pass
        self.audit(actor_id, "digest_photo_del", str(photo_id))
        return True

    # --- 📣 Рекламные посты -------------------------------------------------
    #
    # Реклама живёт отдельно от постов канала: у неё свой текст, своё фото,
    # свой список получателей и свой срок. Записи никогда не участвуют в
    # карусели шапок и картинок — иначе реклама всплыла бы в обычной сводке.

    AD_ACTIVE = ("scheduled", "sending", "sent")
    AD_STATUSES = ("draft", "scheduled", "sending", "sent", "expired",
                   "cancelled", "failed")

    def ad_photo_dir(self) -> str:
        d = os.path.join(os.path.dirname(os.path.abspath(self.path)) or ".", "ads")
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _ad_row(row: Any) -> Dict[str, Any]:
        d = dict(row)
        for key in ("targets", "results"):
            try:
                val = json.loads(d.get(key) or "{}")
            except (TypeError, ValueError):
                val = {}
            d[key] = val if isinstance(val, dict) else {}
        d["has_photo"] = bool(d.get("photo") and os.path.isfile(d["photo"]))
        return d

    def list_ads(self, limit: int = 40, prune_days: int = 30) -> List[Dict[str, Any]]:
        """Рекламные посты: сначала те, что ещё в работе, потом архив."""
        self.prune_ads(prune_days)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM ads ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        out = [self._ad_row(r) for r in rows]
        order = {"scheduled": 0, "sending": 0, "sent": 1, "draft": 2}
        out.sort(key=lambda a: (order.get(a.get("status") or "", 3),
                                -(a.get("send_at") or a.get("created_at") or 0)))
        return out

    def prune_ads(self, days: int = 30) -> int:
        """Завершённую рекламу не держим вечно: месяц истории и хватит."""
        edge = _now() - max(1, int(days)) * 86400
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM ads WHERE status IN ('expired','cancelled','failed')"
                " AND created_at < ?",
                (edge,))
            self._db.commit()
        return int(cur.rowcount or 0)

    def get_ad(self, ad_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM ads WHERE id=?", (int(ad_id),)).fetchone()
        return self._ad_row(row) if row else None

    def add_ad(self, text: str, targets: Optional[Dict[str, Any]] = None,
               send_at: float = 0.0, expires_at: float = 0.0,
               photo: str = "", photo_name: str = "", status: str = "draft",
               author_id: Optional[int] = None) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text and not (photo or ""):
            return {"ok": False, "error": "empty"}
        if status not in self.AD_STATUSES:
            status = "draft"
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO ads(text, photo, photo_name, targets, send_at,"
                " expires_at, status, results, sent_at, created_at, author_id)"
                " VALUES(?,?,?,?,?,?,?,'{}',0,?,?)",
                (text[:4000], photo or "", os.path.basename(photo_name or "")[:80],
                 json.dumps(targets or {}, ensure_ascii=False), float(send_at or 0),
                 float(expires_at or 0), status, _now(), author_id),
            )
            self._db.commit()
            ad_id = int(cur.lastrowid)
        self.audit(author_id, "ad_add", f"#{ad_id} {status} {text[:60]}")
        return {"ok": True, "id": ad_id, "ad": self.get_ad(ad_id)}

    def update_ad(self, ad_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        """Точечная правка: текст, получатели, сроки, статус, отчёт о отправке."""
        allowed = ("text", "targets", "send_at", "expires_at", "status",
                   "results", "sent_at")
        sets, vals = [], []
        for key in allowed:
            if key not in fields:
                continue
            val = fields[key]
            if key in ("targets", "results"):
                val = json.dumps(val or {}, ensure_ascii=False)
            elif key in ("send_at", "expires_at", "sent_at"):
                val = float(val or 0)
            elif key == "status":
                val = str(val or "") if str(val or "") in self.AD_STATUSES else "draft"
            sets.append(f"{key}=?")
            vals.append(val)
        if not sets:
            return self.get_ad(ad_id)
        vals.append(int(ad_id))
        with self._lock:
            cur = self._db.execute(f"UPDATE ads SET {', '.join(sets)} WHERE id=?", vals)
            self._db.commit()
        return self.get_ad(ad_id) if cur.rowcount else None

    def delete_ad(self, ad_id: int, actor_id: Optional[int] = None) -> bool:
        row = self.get_ad(ad_id)
        if not row:
            return False
        with self._lock:
            self._db.execute("DELETE FROM ads WHERE id=?", (int(ad_id),))
            self._db.commit()
        path = os.path.abspath(row.get("photo") or "")
        root = os.path.abspath(self.ad_photo_dir())
        if path.startswith(root + os.sep):
            try:
                os.remove(path)
            except OSError:
                pass
        self.audit(actor_id, "ad_del", str(ad_id))
        return True

    def save_ad_photo(self, ad_id: int, data: bytes, filename: str = "",
                      actor_id: Optional[int] = None) -> Dict[str, Any]:
        """Фото рекламы: те же проверки, что у фото канала (jpg/png/webp ≤ 12 МБ)."""
        row = self.get_ad(ad_id)
        if not row:
            return {"ok": False, "error": "not_found"}
        data = data or b""
        if len(data) < 24:
            return {"ok": False, "error": "empty"}
        if len(data) > 12_000_000:
            return {"ok": False, "error": "too_big"}
        ext = ""
        if data[:2] == b"\xff\xd8":
            ext = ".jpg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ext = ".webp"
        if not ext:
            return {"ok": False, "error": "not_image"}
        folder = self.ad_photo_dir()
        name = f"ad{int(ad_id)}_{int(_now() * 1000)}{ext}"
        path = os.path.join(folder, name)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            return {"ok": False, "error": str(e)[:80]}
        old = row.get("photo") or ""
        with self._lock:
            self._db.execute(
                "UPDATE ads SET photo=?, photo_name=? WHERE id=?",
                (path, os.path.basename(filename or name)[:80], int(ad_id)),
            )
            self._db.commit()
        root = os.path.abspath(folder)
        if old and os.path.abspath(old).startswith(root + os.sep):
            try:
                os.remove(old)
            except OSError:
                pass
        self.audit(actor_id, "ad_photo", f"#{ad_id} {os.path.basename(path)}")
        return {"ok": True, "id": int(ad_id), "path": path,
                "name": os.path.basename(filename or name)}

    def clear_ad_photo(self, ad_id: int, actor_id: Optional[int] = None) -> bool:
        row = self.get_ad(ad_id)
        if not row or not row.get("photo"):
            return False
        path = os.path.abspath(row["photo"])
        with self._lock:
            self._db.execute("UPDATE ads SET photo='', photo_name='' WHERE id=?",
                             (int(ad_id),))
            self._db.commit()
        root = os.path.abspath(self.ad_photo_dir())
        if path.startswith(root + os.sep):
            try:
                os.remove(path)
            except OSError:
                pass
        self.audit(actor_id, "ad_photo_del", str(ad_id))
        return True

    def due_ads(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Пора публиковать: время пришло, а пост ещё не отправлен."""
        now = _now() if now is None else float(now)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM ads WHERE status='scheduled' AND send_at>0"
                " AND send_at<=? ORDER BY send_at LIMIT 5", (now,)
            ).fetchall()
        return [self._ad_row(r) for r in rows]

    def expired_ads(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Срок вышел: показ надо снять (баннер на сайте и посты в каналах)."""
        now = _now() if now is None else float(now)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM ads WHERE status='sent' AND expires_at>0"
                " AND expires_at<=? ORDER BY expires_at LIMIT 20", (now,)
            ).fetchall()
        return [self._ad_row(r) for r in rows]

    def active_site_ads(self, now: Optional[float] = None,
                        limit: int = 3) -> List[Dict[str, Any]]:
        """Что показывать баннером на главной: отправлено, для сайта, не истекло."""
        now = _now() if now is None else float(now)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM ads WHERE status='sent' AND send_at<=?"
                " AND (expires_at=0 OR expires_at>?) ORDER BY send_at DESC, id DESC"
                " LIMIT ?", (now, now, int(limit))
            ).fetchall()
        out = [self._ad_row(r) for r in rows]
        return [a for a in out if (a.get("targets") or {}).get("site")]

    # --- 💬 Обратная связь: «по всем вопросам» ------------------------------
    #
    # Один диалог на пользователя: он пишет из кабинета, админ отвечает из
    # админки. Непрочитанное считаем по времени последнего прочтения каждой
    # стороны, поэтому и у гостя, и у админа есть честный счётчик.

    FB_MAX_LEN = 2000

    @staticmethod
    def _fb_row(row: Any) -> Dict[str, Any]:
        d = dict(row)
        d["is_admin"] = bool(d.get("is_admin"))
        return d

    def feedback_thread(self, user_id: int, create: bool = True) -> Optional[Dict[str, Any]]:
        """Диалог пользователя; при ``create`` заводится при первом сообщении."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM feedback_threads WHERE user_id=?", (int(user_id),)
            ).fetchone()
            if row or not create:
                return dict(row) if row else None
            now = _now()
            cur = self._db.execute(
                "INSERT INTO feedback_threads(user_id, created_at, updated_at,"
                " user_read_at, admin_read_at, status) VALUES(?,?,?,?,?, 'open')",
                (int(user_id), now, now, now, 0.0),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM feedback_threads WHERE id=?",
                                   (int(cur.lastrowid),)).fetchone()
        return dict(row) if row else None

    def feedback_add(self, thread_id: int, text: str, author_id: Optional[int] = None,
                     is_admin: bool = False) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        if len(text) > self.FB_MAX_LEN:
            text = text[:self.FB_MAX_LEN]
        now = _now()
        with self._lock:
            row = self._db.execute("SELECT id FROM feedback_threads WHERE id=?",
                                   (int(thread_id),)).fetchone()
            if not row:
                return {"ok": False, "error": "not_found"}
            cur = self._db.execute(
                "INSERT INTO feedback_messages(thread_id, author_id, is_admin, text,"
                " created_at) VALUES(?,?,?,?,?)",
                (int(thread_id), author_id, 1 if is_admin else 0, text, now),
            )
            # Своё сообщение прочитано сразу: счётчик считает только чужие.
            field = "admin_read_at" if is_admin else "user_read_at"
            self._db.execute(
                f"UPDATE feedback_threads SET updated_at=?, {field}=?, status='open'"
                " WHERE id=?", (now, now, int(thread_id)),
            )
            self._db.commit()
            mid = int(cur.lastrowid)
        return {"ok": True, "id": mid, "created_at": now, "text": text,
                "is_admin": bool(is_admin), "author_id": author_id,
                "thread_id": int(thread_id)}

    def feedback_messages(self, thread_id: int, limit: int = 300) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM feedback_messages WHERE thread_id=? ORDER BY id LIMIT ?",
                (int(thread_id), int(limit)),
            ).fetchall()
        return [self._fb_row(r) for r in rows]

    def feedback_mark_read(self, thread_id: int, who: str = "user") -> bool:
        field = "admin_read_at" if str(who) == "admin" else "user_read_at"
        with self._lock:
            cur = self._db.execute(
                f"UPDATE feedback_threads SET {field}=? WHERE id=?", (_now(), int(thread_id)))
            self._db.commit()
        return bool(cur.rowcount)

    def feedback_unread(self, user_id: int) -> int:
        """Сколько ответов админа пользователь ещё не видел."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM feedback_messages m JOIN feedback_threads t"
                " ON t.id=m.thread_id WHERE t.user_id=? AND m.is_admin=1"
                " AND m.created_at>t.user_read_at", (int(user_id),)
            ).fetchone()
        return int(row[0] if row else 0)

    def feedback_for_user(self, user_id: int) -> Dict[str, Any]:
        thread = self.feedback_thread(user_id, create=False)
        if not thread:
            return {"thread": None, "messages": [], "unread": 0}
        return {"thread": thread,
                "messages": self.feedback_messages(thread["id"]),
                "unread": self.feedback_unread(user_id)}

    def feedback_threads(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Диалоги для админки: кто писал, последняя реплика и непрочитанное."""
        with self._lock:
            rows = self._db.execute(
                "SELECT t.*, u.first_name, u.last_name, u.username, u.email, u.tg_id,"
                " (SELECT COUNT(*) FROM feedback_messages m WHERE m.thread_id=t.id"
                "  AND m.is_admin=0 AND m.created_at>t.admin_read_at) AS unread,"
                " (SELECT COUNT(*) FROM feedback_messages m WHERE m.thread_id=t.id)"
                "  AS total,"
                " (SELECT m.text FROM feedback_messages m WHERE m.thread_id=t.id"
                "  ORDER BY m.id DESC LIMIT 1) AS last_text,"
                " (SELECT m.is_admin FROM feedback_messages m WHERE m.thread_id=t.id"
                "  ORDER BY m.id DESC LIMIT 1) AS last_admin"
                " FROM feedback_threads t LEFT JOIN users u ON u.id=t.user_id"
                " ORDER BY t.updated_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["unread"] = int(d.get("unread") or 0)
            d["total"] = int(d.get("total") or 0)
            d["last_admin"] = bool(d.get("last_admin"))
            d["name"] = display_name(d) or f"#{d.get('user_id')}"
            d["link"] = ("@" + str(d["username"])) if d.get("username") else ""
            out.append(d)
        return out

    def feedback_admin_unread(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM feedback_messages m JOIN feedback_threads t"
                " ON t.id=m.thread_id WHERE m.is_admin=0 AND m.created_at>t.admin_read_at"
            ).fetchone()
        return int(row[0] if row else 0)

    def tg_ids_for_broadcast(self) -> List[int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT tg_id FROM users WHERE is_banned=0 AND tg_id IS NOT NULL"
            ).fetchall()
        return [int(r["tg_id"]) for r in rows]

    def broadcast_targets(self) -> List[Dict[str, Any]]:
        """Получатели рассылки вместе с языком: рассылка тоже двуязычная.

        Язык нужен боту, чтобы перевести текст до отправки: без него адресат,
        ни разу не написавший боту после рестарта, получил бы русский текст
        независимо от своего выбора.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT tg_id, language FROM users "
                "WHERE is_banned=0 AND tg_id IS NOT NULL"
            ).fetchall()
        return [{"tg_id": int(r["tg_id"]), "language": r["language"] or "ru"}
                for r in rows]

    def _parse_svc_config(self, raw: str) -> Dict[str, Any]:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def get_user_service(self, user_id: int, slug: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM user_services WHERE user_id=? AND slug=?",
                (int(user_id), slug),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["config"] = self._parse_svc_config(d.get("config") or "")
        d["enabled"] = bool(d.get("enabled"))
        return d

    def set_user_service_config(self, user_id: int, slug: str, config: Dict[str, Any],
                                enabled: Optional[bool] = None) -> Dict[str, Any]:
        from alerts import normalize_config
        # У алертов свой строгий формат — его и нормализуем. Остальные сервисы
        # (корреляции, сторож монет) хранят собственные ключи: раньше их
        # настройки молча терялись, потому что проходили через ту же чистку.
        cfg = (normalize_config(config or {}) if slug == "alerts"
               else clean_service_config(config or {}))
        blob = json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
        now = _now()
        with self._lock:
            svc = self._db.execute("SELECT * FROM services WHERE slug=?", (slug,)).fetchone()
            if not svc:
                return {"ok": False, "error": "unknown_service"}
            row = self._db.execute(
                "SELECT * FROM user_services WHERE user_id=? AND slug=?",
                (int(user_id), slug),
            ).fetchone()
            if enabled is None:
                on = int(row["enabled"]) if row else 1
            else:
                on = 1 if enabled else 0
            if row:
                self._db.execute(
                    "UPDATE user_services SET config=?, enabled=? WHERE user_id=? AND slug=?",
                    (blob, on, int(user_id), slug),
                )
            else:
                self._db.execute(
                    "INSERT INTO user_services(user_id,slug,enabled,config,created_at)"
                    " VALUES(?,?,?,?,?)",
                    (int(user_id), slug, on, blob, now),
                )
            self._db.commit()
        return {"ok": True, "slug": slug, "enabled": bool(on), "config": cfg}

    def list_service_subscribers(self, slug: str) -> List[Dict[str, Any]]:
        """Кто включил сервис: для рассылки сигналов (алерты, сторож монет)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT u.id AS user_id, u.tg_id, u.email, u.language, "
                "us.config, us.enabled "
                "FROM user_services us JOIN users u ON u.id=us.user_id "
                "WHERE us.slug=? AND us.enabled=1 AND u.is_banned=0",
                (str(slug),),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["config"] = self._parse_svc_config(d.get("config") or "")
            d["enabled"] = bool(d.get("enabled"))
            out.append(d)
        return out

    def list_alert_subscribers(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT u.id AS user_id, u.tg_id, u.language, us.config, us.enabled "
                "FROM user_services us JOIN users u ON u.id=us.user_id "
                "WHERE us.slug='alerts' AND us.enabled=1 AND u.is_banned=0"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["config"] = self._parse_svc_config(d.get("config") or "")
            d["enabled"] = bool(d.get("enabled"))
            out.append(d)
        return out

    def add_alert_event(self, user_id: int, hit: Dict[str, Any]) -> int:
        now = _now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO alert_events(ts,user_id,metric,symbol,value,threshold,"
                "window_min,detail) VALUES(?,?,?,?,?,?,?,?)",
                (now, int(user_id),
                 str(hit.get("metric") or "liq")[:12],
                 str(hit.get("symbol") or "ALL")[:32],
                 float(hit.get("value") or 0),
                 float(hit.get("threshold") or 0),
                 int(hit.get("window_min") or 5),
                 json.dumps({k: hit.get(k) for k in
                             ("count", "longs", "shorts", "pct", "span_min",
                              "peers") if k in hit},
                            ensure_ascii=False)[:400]),
            )
            if secrets.randbelow(40) == 0:
                self._db.execute(
                    "DELETE FROM alert_events WHERE ts<?", (now - 14 * 86400,))
            self._db.commit()
            return int(cur.lastrowid)

    def list_alert_events(self, user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 80))
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM alert_events WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (int(user_id), limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("detail"), str) and d["detail"]:
                try:                                  # detail хранится строкой
                    d["detail"] = json.loads(d["detail"])
                except (ValueError, TypeError):
                    d["detail"] = {}
            out.append(d)
        return out

    def last_alert_ts(self, user_id: int, metric: str, symbol: str) -> Optional[float]:
        with self._lock:
            row = self._db.execute(
                "SELECT ts FROM alert_events WHERE user_id=? AND metric=? AND symbol=?"
                " ORDER BY ts DESC LIMIT 1",
                (int(user_id), str(metric)[:12], str(symbol)[:32]),
            ).fetchone()
        return float(row["ts"]) if row else None

    def last_alert_any(self, user_id: int, metric: str) -> Optional[float]:
        """Последний сигнал метрики по любой монете — якорь окна в режиме ALL.

        Подписка «все монеты» ловит лидера каждой метрики: сигналы хранятся
        под конкретными монетами, поэтому окно перезапускаем от самого
        свежего из них.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT ts FROM alert_events WHERE user_id=? AND metric=?"
                " ORDER BY ts DESC LIMIT 1",
                (int(user_id), str(metric)[:12]),
            ).fetchone()
        return float(row["ts"]) if row else None
