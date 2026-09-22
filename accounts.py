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

#: Пробный доступ к слоям графика для гостей без регистрации: 30 минут.
#: Считается на сервере по гостю (vid, а без cookie — по связке ip+ua),
#: поэтому перезагрузка страницы и чистка localStorage испытание не сбрасывают.
LAYERS_TRIAL_SEC = 1800

# Сколько живут ссылки в письмах
EMAIL_TOKEN_TTL = {
    "verify": 24 * 3600,   # подтверждение почты — сутки
    "login": 30 * 60,      # вход по ссылке — полчаса
    "reset": 3600,         # сброс пароля — час
    "link": 2 * 3600,      # привязка почты к аккаунту из бота — два часа
}
LINK_NONCE_TTL = 15 * 60   # привязка Telegram из кабинета
CAPTCHA_TTL = 10 * 60      # арифметическая капча на регистрацию

# Фото канала (шапки постов и обложки дайджеста). Раньше был общий потолок 40
# на всё, и с пачкой в один запрос он упирался уже на седьмом фото. Теперь
# лимит на рубрику и общий — загружать можно сразу много файлов.
MAX_DIGEST_PHOTOS_KIND = 120
MAX_DIGEST_PHOTOS = 200


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
        "description": "Ликвидации, CVD и OI: порог, окно, монета — сигналы в Telegram.",
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
                        "и их число. Сигнал в Telegram."),
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
        # Пустая строка = человек язык не выбирал. Не подставляем сюда «ru»:
        # какой язык по умолчанию, решает бот (bot_i18n.DEFAULT_LANG) — сейчас
        # это английский, а русский остаётся тем, кто выбрал его сам или пришёл
        # с русской локалью Telegram.
        "language": d.get("language") or "",
        "lang_manual": int(d.get("lang_manual") or 0),
        "is_admin": bool(d.get("is_admin")),
        "is_banned": bool(d.get("is_banned")),
        # владелец — определяется в Store.is_owner, здесь ставим False по умолчанию
        "is_owner": bool(d.get("is_owner")),
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
                CREATE TABLE IF NOT EXISTS geo_cache (
                    ip_hash TEXT PRIMARY KEY,
                    country TEXT NOT NULL DEFAULT '',
                    country_name TEXT,
                    ts REAL NOT NULL,
                    src TEXT
                );
                CREATE TABLE IF NOT EXISTS presence (
                    vid TEXT PRIMARY KEY,
                    first_ts REAL NOT NULL,
                    ts REAL NOT NULL,
                    user_id INTEGER,
                    country TEXT,
                    country_name TEXT,
                    source TEXT,
                    source_kind TEXT,
                    path TEXT,
                    ua TEXT,
                    bot INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_presence_ts ON presence(ts);
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
                    used_at REAL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    photo TEXT DEFAULT '',
                    photo_name TEXT DEFAULT '',
                    link TEXT DEFAULT '',
                    html TEXT DEFAULT '',
                    html_code TEXT DEFAULT '',
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
                CREATE TABLE IF NOT EXISTS channel_sent_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    day TEXT NOT NULL,
                    ts REAL NOT NULL,
                    extra TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_channel_sent_kind_day_ts
                    ON channel_sent_log(kind, day, ts);
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
                CREATE TABLE IF NOT EXISTS layer_trials (
                    who TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 0,
                    user_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_layer_trials_started
                    ON layer_trials(started_at);
                CREATE INDEX IF NOT EXISTS idx_alert_user_ts ON alert_events(user_id, ts);
                CREATE INDEX IF NOT EXISTS idx_alert_cool ON alert_events(user_id, metric, symbol, ts);
                CREATE TABLE IF NOT EXISTS terminal_chat (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_terminal_chat_ts ON terminal_chat(created_at);
                CREATE INDEX IF NOT EXISTS idx_terminal_chat_id ON terminal_chat(id);
                -- 🔒 Приватные диалоги: комната на пару (user_a < user_b), приглашение,
                -- история 3 дня. Авторы — user_id: смена ника на диалог не влияет.
                CREATE TABLE IF NOT EXISTS private_chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_a INTEGER NOT NULL,
                    user_b INTEGER NOT NULL,
                    invited_by INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',  -- pending|active|declined
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_private_chats_pair
                    ON private_chats(user_a, user_b);
                CREATE INDEX IF NOT EXISTS idx_private_chats_a ON private_chats(user_a);
                CREATE INDEX IF NOT EXISTS idx_private_chats_b ON private_chats(user_b);
                CREATE TABLE IF NOT EXISTS private_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_private_messages_room
                    ON private_messages(room_id, id);
                CREATE TABLE IF NOT EXISTS private_reads (
                    room_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_read_id INTEGER NOT NULL DEFAULT 0,
                    reminded_id INTEGER NOT NULL DEFAULT 0,
                    reminded_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (room_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS content_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_content_comments_kind_item ON content_comments(kind, item_id, id);
                CREATE INDEX IF NOT EXISTS idx_content_comments_ts ON content_comments(created_at);
                CREATE INDEX IF NOT EXISTS idx_content_comments_user ON content_comments(user_id);
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
        self._migrate_ads()
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
                # география и источник перехода: колонки появились позже,
                # у старых записей они пустые — это честно, страна неизвестна
                for col in ("country", "country_name", "country_src",
                            "source", "source_kind"):
                    if cols and col not in cols:
                        self._db.execute(f"ALTER TABLE visits ADD COLUMN {col} TEXT")
                self._db.commit()
        except Exception as e:                    # noqa: BLE001
            log.debug("визиты: миграция ua/bot: %s", e)

    def _migrate_digest_photos(self) -> None:
        """Колонки фото канала: рубрика (kind) и когда фото вышло (used_at).

        ``kind``: фото для сводки постов или для дневного дайджеста. Раньше
        картинки были одни на всё: и в посты раз в N часов, и в вечерний
        выпуск. Теперь рубрика у фото своя, а старые строки считаем постовыми —
        как они и работали.

        ``used_at``: обложки листаются «по кругу без повторов» — перед постом
        берём фото, которое дольше всех не выходило. По нулю у старых строк
        видно, что они ещё не участвовали: круг начнётся с них.
        """
        try:
            with self._lock:
                cols = {r["name"] for r in
                        self._db.execute("PRAGMA table_info(digest_photos)")}
                if cols and "kind" not in cols:
                    self._db.execute(
                        "ALTER TABLE digest_photos ADD COLUMN kind TEXT DEFAULT 'post'")
                    self._db.commit()
                if cols and "used_at" not in cols:
                    self._db.execute(
                        "ALTER TABLE digest_photos ADD COLUMN used_at REAL DEFAULT 0")
                    self._db.commit()
                self._db.execute(
                    "UPDATE digest_photos SET kind='post' WHERE kind IS NULL OR kind=''")
                self._db.commit()
        except Exception as e:                    # noqa: BLE001
            log.debug("фото канала: миграция kind: %s", e)

    def _migrate_ads(self) -> None:
        """Колонки link и html: кликабельный баннер и HTML-баннер.

        Раньше ссылкой становился только голый URL внутри текста объявления, и
        чтобы вся реклама на сайте была кликабельной, админ должен был писать
        адрес в текст. Теперь у объявления есть своё поле ссылки: оно ведёт
        на страницу акции, а текст остаётся только подписью.

        HTML-баннер — выбор админа: вместо картинки+ссылки в слот баннера
        вставляется произвольный HTML-код (партнёрский блок, iframe, верстка).
        Поле ``html`` хранит этот код, а ``html_code`` — алиас для совместимости.
        """
        try:
            with self._lock:
                cols = {r["name"] for r in
                        self._db.execute("PRAGMA table_info(ads)")}
                if cols:
                    if "link" not in cols:
                        self._db.execute("ALTER TABLE ads ADD COLUMN link TEXT DEFAULT ''")
                    if "html" not in cols:
                        self._db.execute("ALTER TABLE ads ADD COLUMN html TEXT DEFAULT ''")
                    if "html_code" not in cols:
                        self._db.execute("ALTER TABLE ads ADD COLUMN html_code TEXT DEFAULT ''")
                self._db.commit()
        except Exception as e:                                  # noqa: BLE001
            log.debug("реклама: колонки link/html не добавлены: %s", e)

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

    def is_owner(self, user: Optional[Dict[str, Any]] | sqlite3.Row) -> bool:
        """Главный админ (владелец) — тот, кто задан в окружении.

        ``LIQSCOPE_ADMIN_IDS`` / ``LIQSCOPE_ADMIN_EMAILS`` — это вы. Такого
        пользователя нельзя забанить или снять с админки обычным админом.
        """
        if not user:
            return False
        try:
            d = dict(user) if not isinstance(user, dict) else user
        except Exception:
            return False
        tg_id = d.get("tg_id")
        try:
            if tg_id and int(tg_id) in self.admin_ids:
                return True
        except Exception:
            pass
        email = normalize_email(d.get("email") or "")
        if email and email in self.admin_emails:
            return True
        return False

    def _pub(self, row: Optional[sqlite3.Row | Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """public_user + is_owner (владелец по env)."""
        if not row:
            return None
        u = public_user(row)
        try:
            u["is_owner"] = self.is_owner(row)
        except Exception:
            u["is_owner"] = False
        # владелец всегда админ
        if u.get("is_owner"):
            u["is_admin"] = True
        return u

    def create_email_user(self, email: str, password_hash: str = "",
                          first_name: str = "", language: str = "") -> Dict[str, Any]:
        # language пустой = язык не выбирали: решает тот, кто отправляет
        # (бот — язык по умолчанию, страницы сайта — язык запроса)
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
                     (language or "")[:8], is_admin or int(row["is_admin"]), now,
                     int(row["id"])),
                )
            else:
                self._db.execute(
                    "INSERT INTO users(tg_id,email,password_hash,email_verified,"
                    "first_name,language,is_admin,is_banned,created_at,last_seen,login_count)"
                    " VALUES(NULL,?,?,0,?,?,?,0,?,?,0)",
                    (email, password_hash, (first_name or "")[:64],
                     (language or "")[:8], is_admin, now, now),
                )
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return {"ok": True, "user": self._pub(row), "resumed": resumed}

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
        return {"ok": True, "user": self._pub(row), "merged": merged}

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
        return self._pub(row) if row else None

    def set_user_password(self, user_id: int, password_hash: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (password_hash, int(user_id)),
            )
            self._db.commit()
        return cur.rowcount > 0

    def set_user_name(self, user_id: int, first_name: str) -> None:
        # full nick: overwrite first_name with the whole string and clear last_name,
        # so "Александр Швакин" -> "SHWePS" doesn't become "SHWePS Швакин".
        name = (first_name or "").strip()[:64]
        name = " ".join(name.split())
        with self._lock:
            self._db.execute("UPDATE users SET first_name=?, last_name='' WHERE id=?",
                             (name, int(user_id)))
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
        return self._pub(row)

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
        return self._pub(user), ""

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
        return {"ok": True, "user": self._pub(user) if user else None}

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
             (tg.get("language_code") or tg.get("language") or "")[:8],
             self._admin_flag(tg_id=tg_id), _now(), user_id),
        )
        self._db.commit()
        row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {"ok": True, "user": self._pub(row), "merged": merged}

    def link_tg_to_user(self, user_id: int, tg: Dict[str, Any]) -> Dict[str, Any]:
        """Привязать Telegram к конкретному аккаунту (вход виджетом, вход ботом)."""
        want = self._admin_flag(tg_id=int(tg.get("tg_id") or tg.get("id") or 0))
        with self._lock:
            r = self._link_tg_locked(int(user_id), tg)
            uid = (r.get("user") or {}).get("id")
            if r.get("ok") and want and uid:
                self._db.execute("UPDATE users SET is_admin=1 WHERE id=?", (int(uid),))
                self._db.commit()
                r["user"] = self._pub(
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
                return self._pub(row)
            tg_id = int(row["tg_id"])
            self._db.execute(
                "UPDATE users SET tg_id=NULL, tg_linked_at=NULL, is_admin=? WHERE id=?",
                (self._admin_flag(email=row["email"] or ""), user_id),
            )
            # отвязываем только «свой» канал: сессии бота не нужны, он их не держит
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        self.audit(user_id, "tg_unlink", f"tg_id={tg_id}")
        return self._pub(row)

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
        return self._pub(row)

    # ----- users (Telegram) -----------------------------------------------
    def upsert_telegram_user(self, tg: Dict[str, Any]) -> Dict[str, Any]:
        tg_id = int(tg["id"] if "id" in tg else tg["tg_id"])
        username = (tg.get("username") or "")[:64]
        first = (tg.get("first_name") or "")[:64]
        last = (tg.get("last_name") or "")[:64]
        photo = (tg.get("photo_url") or "")[:500]
        lang = (tg.get("language_code") or tg.get("language") or "")[:8]
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
        return self._pub(row)

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
        return {"user": self._pub(user), "tg_id": int(row["tg_id"]),
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
        return self._pub(row) if row else None

    def get_user_by_tg(self, tg_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE tg_id=?", (int(tg_id),)).fetchone()
        return self._pub(row) if row else None

    def touch_user(self, user_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE users SET last_seen=? WHERE id=?", (_now(), int(user_id)))
            self._db.commit()

    def set_banned(self, user_id: int, banned: bool, actor_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        user_id = int(user_id)
        with self._lock:
            target = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                return None
            # защита владельца
            if banned and self.is_owner(target):
                actor = None
                if actor_id:
                    actor = self._db.execute("SELECT * FROM users WHERE id=?", (int(actor_id),)).fetchone()
                if not actor or not self.is_owner(actor):
                    # нельзя банить владельца обычным админом
                    return {"ok": False, "error": "protected", "user": self._pub(target)}
            # обычный админ не может банить другого админа
            if banned and int(target["is_admin"]):
                actor = None
                if actor_id:
                    actor = self._db.execute("SELECT * FROM users WHERE id=?", (int(actor_id),)).fetchone()
                if actor and not self.is_owner(actor) and int(actor["id"]) != user_id:
                    # админ пытается забанить админа — только владелец может
                    return {"ok": False, "error": "admin_protected", "user": self._pub(target)}
            if user_id == int(actor_id or 0) and banned:
                return {"ok": False, "error": "self", "user": self._pub(target)}
            self._db.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if banned else 0, user_id))
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row:
            self.audit(actor_id, "ban" if banned else "unban", f"user_id={user_id} tg_id={row['tg_id']}")
            if banned:
                self.drop_user_sessions(user_id)
            return self._pub(row)
        return None

    def set_admin(self, user_id: int, admin: bool, actor_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        user_id = int(user_id)
        with self._lock:
            target = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                return None
            actor = None
            if actor_id:
                actor = self._db.execute("SELECT * FROM users WHERE id=?", (int(actor_id),)).fetchone()
            # только владелец может назначать/снимать админов
            if actor and not self.is_owner(actor):
                return {"ok": False, "error": "owner_only", "user": self._pub(target)}
            if not actor and self.admin_ids:
                # если actor не найден, но есть список владельцев — требуем владельца
                # (для dev-режима без actor разрешаем)
                pass
            # нельзя снять админку с владельца
            if not admin and self.is_owner(target):
                return {"ok": False, "error": "protected", "user": self._pub(target)}
            if user_id == int(actor_id or 0) and not admin:
                # нельзя снять с себя (чтобы не запереться), кроме владельца снимающего другого
                # владелец может снять с себя? лучше запретить
                return {"ok": False, "error": "self", "user": self._pub(target)}
            self._db.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if admin else 0, user_id))
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row:
            self.audit(actor_id, "admin_on" if admin else "admin_off", f"user_id={user_id}")
            return self._pub(row)
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
        return {"total": total, "matched": matched, "users": [self._pub(r) for r in rows]}

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
        return self._pub(row)

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
                     ua: str = "", bot: bool = False, country: str = "",
                     country_name: str = "", country_src: str = "",
                     source: str = "", source_kind: str = "") -> int:
        """Записать просмотр страницы.

        ``bot`` — служебный запрос (краулер, превью мессенджера, скрипт): в
        счётчики просмотров и посетителей он не идёт, но хранится — админ
        видит, сколько такого шума отсеяно.

        ``country``/``source`` — откуда гость и как нашёл сайт: у первого
        запроса страну иногда узнать не успеваем (IP спрашивают у внешнего
        сервиса), поэтому возвращаем id строки — по нему страна допишется,
        когда ответ придёт (``set_visit_country``).
        """
        path = (path or "/")[:120]
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO visits(ts,path,vid,user_id,ip_hash,ua,bot,country,"
                "country_name,country_src,source,source_kind)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), path, ("" if bot else (vid or ""))[:40], user_id,
                 ip_hash, (ua or "")[:180], 1 if bot else 0,
                 (country or "")[:2].upper(), (country_name or "")[:60],
                 (country_src or "")[:16], (source or "")[:60],
                 (source_kind or "")[:16]),
            )
            visit_id = int(cur.lastrowid or 0)
            # не копим бесконечно: раз в ~200 визитов чистим старше 90 дней
            if secrets.randbelow(200) == 0:
                self._db.execute("DELETE FROM visits WHERE ts<?", (_now() - 90 * 86400,))
                self._db.execute("DELETE FROM presence WHERE ts<?", (_now() - 30 * 86400,))
            self._db.commit()
        return visit_id

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

    # ----- пробный доступ к слоям (гость без регистрации) -------------------
    def layer_trial(self, who: str, user_id: Optional[int] = None,
                    limit_sec: int = LAYERS_TRIAL_SEC,
                    now: Optional[float] = None) -> Dict[str, Any]:
        """Сколько пробного времени осталось гостю: 30 минут и всё.

        Считает сервер, а не браузер: строка на гостя (``who`` — vid, а если
        cookie нет, связка ip+ua) заводится при первом обращении и живёт
        дальше, поэтому перезагрузка страницы, чистка localStorage или другой
        браузер испытание не начинают заново.

        Зарегистрированному пробник не нужен: у него слои без ограничений.
        """
        limit = max(0, int(limit_sec))
        if user_id:
            return {"tracked": False, "guest": False, "allowed": True, "left": None,
                    "limit": limit, "started": 0.0, "hits": 0, "expired": False,
                    "ended": 0.0}
        who = str(who or "")[:80]
        if not who:
            # гость без cookie: считать не по чему, не мешаем смотреть
            return {"tracked": False, "guest": True, "allowed": True, "left": None,
                    "limit": limit, "started": 0.0, "hits": 0, "expired": False,
                    "ended": 0.0}
        now = float(now if now is not None else _now())
        with self._lock:
            row = self._db.execute(
                "SELECT started_at, hits FROM layer_trials WHERE who=?", (who,)
            ).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO layer_trials(who,started_at,last_seen,hits) "
                    "VALUES(?,?,?,1)", (who, now, now))
                started, hits = now, 1
            else:
                started, hits = float(row["started_at"]), int(row["hits"]) + 1
                self._db.execute(
                    "UPDATE layer_trials SET last_seen=?, hits=? WHERE who=?",
                    (now, hits, who))
                # редкая чистка: таблица маленькая, но пусть не растёт вечно
                if secrets.randbelow(200) == 0:
                    self._db.execute("DELETE FROM layer_trials WHERE last_seen<?",
                                     (now - 120 * 86400,))
            self._db.commit()
        left = max(0.0, started + limit - now) if limit else 0.0
        return {"tracked": True, "guest": True, "allowed": (left > 0) or not limit,
                "left": round(left, 1), "limit": limit, "started": started,
                "hits": hits, "expired": bool(limit and left <= 0),
                "ended": round(started + limit, 1) if limit else 0.0}

    def layer_trial_stats(self, now: Optional[float] = None,
                          limit_sec: int = LAYERS_TRIAL_SEC) -> Dict[str, Any]:
        """Сводка испытаний: сколько гостей признали, сколько уже упёрлось.

        Нужна админке: видно, сколько людей знакомятся со слоями и сколько
        дошло до стены регистрации.
        """
        now = float(now if now is not None else _now())
        edge = now - max(0, int(limit_sec))
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n, SUM(started_at>=?) AS active FROM layer_trials",
                (edge,)).fetchone()
        total = int((row or {})["n"] or 0) if row is not None else 0
        active = int((row or {})["active"] or 0) if row is not None else 0
        return {"total": total, "active": active, "expired": max(0, total - active),
                "limit_sec": int(limit_sec)}

    def layer_trial_reset(self, who: str = "") -> int:
        """Сбросить пробный доступ к слоям: гостю (``who``) или всем (пусто).

        Удаляем строку испытания — следующий запрос гостя заводит её заново и
        получает полный лимит с текущей секунды. Админу это нужно, чтобы дать
        человеку ещё времени, не меняя лимит для всех: сброс не выдаёт
        бессрочный доступ, таймер просто начинается сначала.

        Возвращаем, сколько строк удалили.
        """
        who = str(who or "").strip()[:80]
        with self._lock:
            if who:
                cur = self._db.execute("DELETE FROM layer_trials WHERE who=?", (who,))
            else:
                cur = self._db.execute("DELETE FROM layer_trials")
            self._db.commit()
            return int(cur.rowcount or 0)

    def layer_trials_of(self, whos: List[str]) -> Dict[str, Dict[str, Any]]:
        """Что известно о пробниках этих гостей: когда пришли и сколько заходов.

        Админке это нужно, чтобы рядом с живым гостем показать остаток пробного
        доступа и кнопку «дать ещё»: ``who`` — тот же ключ, что и в
        ``layer_trial`` (``v:<vid>`` у гостя с cookie, ``i:<хеш>`` без неё).
        """
        keys = [str(w or "")[:80] for w in (whos or []) if w]
        if not keys:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        marks = ",".join("?" for _ in keys)
        with self._lock:
            try:
                rows = self._db.execute(
                    f"SELECT who, started_at, last_seen, hits FROM layer_trials"
                    f" WHERE who IN ({marks})", tuple(keys)).fetchall()
            except Exception as e:                        # noqa: BLE001
                log.debug("слои: пробники гостей не прочитались: %s", e)
                return {}
        for r in rows:
            out[str(r["who"])] = {"started_at": float(r["started_at"] or 0.0),
                                  "last_seen": float(r["last_seen"] or 0.0),
                                  "hits": int(r["hits"] or 0)}
        return out

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
                " AND path NOT IN ('/manifest.webmanifest','/sw.js','/favicon.ico',"
                " '/robots.txt','/sitemap.xml')"
                " AND path NOT LIKE '%.webmanifest' AND path NOT LIKE '/static/%'"
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

    # ----- стирание статистики --------------------------------------------
    def clear_visits(self, include_cache: bool = False) -> Dict[str, int]:
        """Стереть статистику посещений: визиты, присутствие, (по желанию) кэш.

        Админу это нужно, когда цифры надо начать с чистого листа — например,
        после проверок, накрутки или переезда сайта. Что именно исчезает:

        * ``visits`` — переходы, уникальные, график за две недели, страны,
          источники и время на сайте;
        * ``presence`` — «кто сейчас на сайте» и долгие визиты;
        * ``geo_cache`` — только по флагу: это не статистика, а кэш «адрес →
          страна». Его потеря безобидна, но после стирания страна каждого
          адреса спрашивается у внешнего сервиса заново.

        Аккаунты, сервисы, подписки и письма остаются на месте: стираются
        только измерения посещаемости. Возвращаем, сколько строк удалили, —
        админка показывает это в подтверждении.
        """
        counts = {"visits": 0, "presence": 0, "geo_cache": 0}
        with self._lock:
            pairs = [("visits", "DELETE FROM visits"),
                     ("presence", "DELETE FROM presence")]
            if include_cache:
                pairs.append(("geo_cache", "DELETE FROM geo_cache"))
            for name, sql in pairs:
                try:
                    counts[name] = int(self._db.execute(sql).rowcount or 0)
                except Exception:                             # noqa: BLE001
                    # базы прошлых версий: таблицы присутствия или кэша
                    # могло ещё не быть — стирать нечего, и это не ошибка
                    counts[name] = 0
            self._db.commit()
            # VACUUM после DELETE не запускаем: файл тот же, а блокировка на
            # время уборки задержала бы запись новых визитов
        return counts

    def visits_volume(self) -> Dict[str, int]:
        """Сколько сейчас лежит в базе: показываем в подтверждении стирания."""
        with self._lock:
            out = {}
            for name, sql in (("visits", "SELECT COUNT(*) FROM visits"),
                              ("presence", "SELECT COUNT(*) FROM presence"),
                              ("geo_cache", "SELECT COUNT(*) FROM geo_cache")):
                try:
                    out[name] = int(self._db.execute(sql).fetchone()[0] or 0)
                except Exception:                             # noqa: BLE001
                    out[name] = 0
        return out

    # ----- география посещений -------------------------------------------
    def geo_cached(self, ip_hash: str, ttl_sec: float = 0.0) -> Optional[Dict[str, Any]]:
        """Что уже знаем об этом адресе: страна и когда спрашивали.

        Кэш нужен, чтобы не спрашивать внешний сервис на каждый визит: адрес
        спрашивают один раз, дальше страна берётся из базы. ``cc`` может быть
        пустым — это тоже ответ («страна неизвестна»), и его держим недолго.
        """
        if not ip_hash:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT country, country_name, ts, src FROM geo_cache WHERE ip_hash=?",
                (ip_hash,),
            ).fetchone()
        if not row:
            return None
        if _now() - float(row["ts"] or 0) > float(ttl_sec or 0.0):
            return None
        return {"cc": (row["country"] or "").upper(),
                "name": row["country_name"] or "",
                "src": row["src"] or "cache",
                "ts": float(row["ts"] or 0),
                "fresh": bool(ttl_sec)}

    def geo_remember(self, ip_hash: str, cc: str, name: str = "",
                     src: str = "provider") -> None:
        """Запомнить страну адреса (в том числе «неизвестно»)."""
        if not ip_hash:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO geo_cache(ip_hash,country,country_name,ts,src)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(ip_hash) DO UPDATE SET country=excluded.country,"
                " country_name=excluded.country_name, ts=excluded.ts, src=excluded.src",
                (ip_hash, (cc or "")[:2].upper(), (name or "")[:60], _now(),
                 (src or "")[:16]),
            )
            if secrets.randbelow(200) == 0:
                self._db.execute("DELETE FROM geo_cache WHERE ts<?",
                                 (_now() - 180 * 86400,))
            self._db.commit()

    def set_visit_country(self, visit_id: int, cc: str, name: str = "",
                          src: str = "") -> None:
        """Дописать страну в уже записанный визит (ответ пришёл позже визита)."""
        if not visit_id or not cc:
            return
        with self._lock:
            self._db.execute(
                "UPDATE visits SET country=?, country_name=?, country_src=?"
                " WHERE id=?",
                ((cc or "")[:2].upper(), (name or "")[:60], (src or "")[:16],
                 int(visit_id)),
            )
            self._db.commit()

    def touch_presence(self, vid: str, user_id: Optional[int] = None,
                       country: str = "", country_name: str = "",
                       source: str = "", source_kind: str = "",
                       path: str = "", ua: str = "", bot: bool = False,
                       keep: float = 30 * 86400) -> Dict[str, Any]:
        """Отметить, что гость сейчас на сайте: одно «сердцебиение» на гостя.

        Строка одна на гостя: ``first_ts`` — когда он пришёл, ``ts`` — когда
        последний раз подавал признаки жизни. Отсюда и «сколько уже на сайте»,
        и «кто онлайн» (последние :data:`ONLINE_SEC` секунд). Страну и источник
        заполняем один раз — первый заход важнее повторных.

        ``keep`` — сколько дней держим строки: чистим их в том же запросе
        (редко, чтобы не делать лишнюю работу на каждом пинге).
        """
        vid = (vid or "")[:40]
        if not vid:
            return {}
        now = _now()
        with self._lock:
            self._db.execute(
                "INSERT INTO presence(vid,first_ts,ts,user_id,country,country_name,"
                "source,source_kind,path,ua,bot) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(vid) DO UPDATE SET ts=excluded.ts,"
                " user_id=COALESCE(excluded.user_id, presence.user_id),"
                " country=CASE WHEN COALESCE(presence.country,'')=''"
                "   THEN excluded.country ELSE presence.country END,"
                " country_name=CASE WHEN COALESCE(presence.country_name,'')=''"
                "   THEN excluded.country_name ELSE presence.country_name END,"
                " source=CASE WHEN COALESCE(presence.source,'')=''"
                "   THEN excluded.source ELSE presence.source END,"
                " source_kind=CASE WHEN COALESCE(presence.source_kind,'')=''"
                "   THEN excluded.source_kind ELSE presence.source_kind END,"
                " path=excluded.path, ua=excluded.ua, bot=excluded.bot",
                (vid, now, now, user_id, (country or "")[:2].upper(),
                 (country_name or "")[:60], (source or "")[:60],
                 (source_kind or "")[:16], (path or "")[:120], (ua or "")[:180],
                 1 if bot else 0),
            )
            if secrets.randbelow(200) == 0:
                self._db.execute("DELETE FROM presence WHERE ts<?",
                                 (now - float(keep or 30 * 86400),))
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM presence WHERE vid=?", (vid,)).fetchone()
        out = dict(row) if row else {}
        if out:
            out["sec"] = max(0.0, now - float(out.get("first_ts") or now))
        return out

    def set_presence_country(self, vid: str, cc: str, name: str = "") -> None:
        """Дописать страну в строку присутствия, если её там ещё нет."""
        if not vid or not cc:
            return
        with self._lock:
            self._db.execute(
                "UPDATE presence SET country=?, country_name=?"
                " WHERE vid=? AND COALESCE(country,'')=''",
                ((cc or "")[:2].upper(), (name or "")[:60], (vid or "")[:40]),
            )
            self._db.commit()

    def geo_online(self, window_sec: float = 300.0,
                   limit: int = 200) -> List[Dict[str, Any]]:
        """Кто сейчас на сайте: свежие «сердцебиения» из presence.

        ``window_sec`` берём как есть (пол — одна секунда): окно выбирает тот,
        кто спрашивает — у админки оно своё (``web_geo.ONLINE_SEC``).
        """
        since = _now() - max(1.0, float(window_sec))
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM presence WHERE ts>=? AND bot=0"
                " ORDER BY ts DESC LIMIT ?", (since, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def geo_stats(self, hours: float = 24.0, online_sec: float = 300.0,
                  dots: int = 600) -> Dict[str, Any]:
        """Картина посещаемости окна: гости, страны, источники, время на сайте.

        Возвращаем «сырьё» без геометрии (широту и долготу стран добавляет
        слой выше, у него есть справочник центроидов):

        * ``guests`` — по одной строке на гостя: страна, откуда пришёл, сколько
          визитов и сколько уже провёл на сайте (от первого визита до
          последнего «сердцебиения»);
        * ``points`` — те же гости, но только для карты (ограничение ``dots``);
        * ``online`` — кто подал признак жизни за ``online_sec`` секунд;
        * ``countries``/``sources`` — сводка «откуда» и «из какого источника».

        Источник гостя — самый свежий *внешний* переход: внутренние переходы
        по сайту (клики по меню) в таблице источников смысла не имеют.
        """
        now = _now()
        hours = max(1.0, min(float(hours or 24.0), 24 * 90.0))
        since = now - hours * 3600.0
        online_window = max(30.0, float(online_sec))
        with self._lock:
            rows = self._db.execute(
                "SELECT vid, MAX(user_id) AS user_id, MIN(ts) AS first_ts,"
                " MAX(ts) AS last_ts, COUNT(*) AS views"
                " FROM visits WHERE ts>=? AND bot=0 AND COALESCE(vid,'')!=''"
                " GROUP BY vid ORDER BY last_ts DESC LIMIT ?",
                (since, max(1, int(dots) * 4)),
            ).fetchall()
            # страна гостя: код и имя берём из ОДНОЙ строки визита. Если взять
            # MAX(country) и MAX(country_name) по отдельности, код и имя
            # разъедутся (у одного визита страна FI, у другого DE) — и на
            # карте «Финляндия» подпишется как «Spain». Берём первый известный
            # визит: важно, откуда гость пришёл, а не куда его потом занесло.
            cc_rows = self._db.execute(
                "SELECT vid, ts, COALESCE(country,'') AS country,"
                " COALESCE(country_name,'') AS country_name FROM visits"
                " WHERE ts>=? AND bot=0 AND COALESCE(vid,'')!=''"
                " AND COALESCE(country,'')!='' ORDER BY ts ASC LIMIT 20000",
                (since,)).fetchall()
            src_rows = self._db.execute(
                "SELECT vid, ts, COALESCE(source,'') AS source,"
                " COALESCE(source_kind,'') AS source_kind FROM visits"
                " WHERE ts>=? AND bot=0 AND COALESCE(vid,'')!=''"
                " AND COALESCE(source_kind,'') NOT IN ('', 'internal')"
                " ORDER BY ts DESC LIMIT 20000", (since,)).fetchall()
            pres = self._db.execute(
                "SELECT vid, first_ts, ts, country, country_name, source,"
                " source_kind, path, user_id FROM presence WHERE ts>=?",
                (since,)).fetchall()
            anon = self._db.execute(
                "SELECT COUNT(*) AS n FROM visits WHERE ts>=? AND bot=0"
                " AND COALESCE(vid,'')=''", (since,)).fetchone()
            bots = self._db.execute(
                "SELECT COUNT(*) AS n FROM visits WHERE ts>=? AND bot=1",
                (since,)).fetchone()
            paths = self._db.execute(
                "SELECT path, COUNT(*) AS n FROM visits WHERE ts>=? AND bot=0"
                " AND path NOT IN ('/manifest.webmanifest','/sw.js','/favicon.ico',"
                " '/robots.txt','/sitemap.xml')"
                " AND path NOT LIKE '%.webmanifest' AND path NOT LIKE '/static/%'"
                " GROUP BY path ORDER BY n DESC LIMIT 8", (since,)).fetchall()
        # самый свежий внешний переход на гостя (строки уже отсортированы)
        src_map: Dict[str, Dict[str, str]] = {}
        for r in src_rows:
            src_map.setdefault(str(r["vid"]), {"source": r["source"],
                                               "kind": r["source_kind"]})
        # первая известная страна гостя — код и имя из той же строки
        cc_map: Dict[str, Dict[str, str]] = {}
        for r in cc_rows:
            cc_map.setdefault(str(r["vid"]), {
                "country": (r["country"] or "").upper(),
                "country_name": r["country_name"] or ""})
        pmap = {str(r["vid"]): dict(r) for r in pres}

        def _src(vid: str, p: Dict[str, Any]) -> Dict[str, str]:
            """Источник гостя: сначала строка присутствия, потом история визитов."""
            if p.get("source"):
                return {"source": p.get("source") or "",
                        "kind": p.get("source_kind") or "direct"}
            return src_map.get(vid) or {"source": "", "kind": "direct"}

        guests: List[Dict[str, Any]] = []
        for r in rows:
            vid = str(r["vid"] or "")
            v: Dict[str, Any] = dict(r)
            p = pmap.get(vid) or {}
            online = bool(p) and now - float(p.get("ts") or 0) <= online_window
            last = float(v.get("last_ts") or 0)
            if online:
                last = max(last, float(p.get("ts") or 0))
            cc = cc_map.get(vid) or {}
            v["country"] = (p.get("country") or cc.get("country") or "").upper()
            v["country_name"] = p.get("country_name") or cc.get("country_name") or ""
            src = _src(vid, p)
            v["source"], v["source_kind"] = src["source"], src["kind"]
            v["online"] = online
            v["first_ts"] = float(v.get("first_ts") or 0)
            v["last_ts"] = last
            v["sec"] = max(0.0, last - v["first_ts"])
            v["path"] = (p.get("path") or "")
            guests.append(v)
        # кто-то только открыл страницу и уже прислал «сердцебиение», а визит
        # запишется в фоне — его в списке гостей ещё нет, но онлайн он есть
        known = {str(g["vid"]) for g in guests}
        for vid, p in pmap.items():
            if vid in known or now - float(p.get("ts") or 0) > online_window:
                continue
            guests.append({
                "vid": vid, "user_id": p.get("user_id"),
                "first_ts": float(p.get("first_ts") or 0),
                "last_ts": float(p.get("ts") or 0),
                "views": 0, "country": p.get("country") or "",
                "country_name": p.get("country_name") or "",
                "source": (p.get("source") or ""),
                "source_kind": (p.get("source_kind") or "direct"),
                "path": p.get("path") or "", "online": True,
                "sec": max(0.0, now - float(p.get("first_ts") or now)),
            })
        guests.sort(key=lambda g: g["last_ts"], reverse=True)
        countries: Dict[str, Dict[str, Any]] = {}
        sources: Dict[Any, Dict[str, Any]] = {}
        for g in guests:
            c = countries.setdefault(g["country"] or "", {
                "country": g["country"] or "", "name": g["country_name"] or "",
                "visitors": 0, "views": 0, "online": 0, "sec": 0.0, "last": 0.0})
            c["visitors"] += 1
            c["views"] += int(g["views"] or 0)
            c["sec"] += float(g["sec"] or 0)
            c["online"] += 1 if g["online"] else 0
            c["last"] = max(c["last"], g["last_ts"])
            key = (g["source"], g["source_kind"])
            b = sources.setdefault(key, {"source": g["source"],
                                         "kind": g["source_kind"],
                                         "visitors": 0, "views": 0, "online": 0,
                                         "last": 0.0})
            b["visitors"] += 1
            b["views"] += int(g["views"] or 0)
            b["online"] += 1 if g["online"] else 0
            b["last"] = max(b["last"], g["last_ts"])
        for c in countries.values():
            c["avg_sec"] = (c["sec"] / c["visitors"]) if c["visitors"] else 0.0
        total_sec = sum(float(g["sec"] or 0) for g in guests)
        return {
            "now": now,
            "hours": hours,
            "online_sec": online_window,
            "guests": guests,
            "points": guests[:max(1, int(dots))],
            "online": [g for g in guests if g["online"]],
            "countries": sorted(countries.values(),
                                key=lambda b: (-b["visitors"], -b["last"])),
            "sources": sorted(sources.values(),
                              key=lambda b: (-b["visitors"], -b["last"])),
            "long": sorted([g for g in guests if float(g["sec"] or 0) >= 60],
                           key=lambda g: g["sec"], reverse=True)[:10],
            "paths": [{"path": r["path"], "n": r["n"]} for r in paths],
            "totals": {
                "online": len([g for g in guests if g["online"]]),
                "visitors": len(guests),
                "views": sum(int(g["views"] or 0) for g in guests),
                "anon_views": int((anon or {"n": 0})["n"] or 0),
                "bots": int((bots or {"n": 0})["n"] or 0),
                "countries": len([c for c in countries if c]),
                "avg_sec": (total_sec / len(guests)) if guests else 0.0,
                "long_60": len([g for g in guests if float(g["sec"] or 0) >= 60]),
            },
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
        ``used_at`` — когда фото последний раз было обложкой поста.
        """
        kind = (kind or "").strip()
        with self._lock:
            if kind:
                rows = self._db.execute(
                    "SELECT id, path, name, kind, used_at, created_at"
                    " FROM digest_photos"
                    " WHERE COALESCE(kind,'post')=? ORDER BY id", (kind,)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, path, name, kind, used_at, created_at"
                    " FROM digest_photos"
                    " ORDER BY id"
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["exists"] = bool(d.get("path") and os.path.isfile(d["path"]))
            out.append(d)
        return out

    def digest_photo_counts(self) -> Dict[str, int]:
        """Сколько фото в каждой рубрике и всего — для счётчика в админке."""
        with self._lock:
            rows = self._db.execute(
                "SELECT COALESCE(kind,'post') AS k, COUNT(*) AS n"
                " FROM digest_photos GROUP BY k"
            ).fetchall()
        out = {"post": 0, "digest": 0}
        for r in rows:
            out[str(r["k"] or "post")] = int(r["n"])
        out["total"] = sum(out.values())
        return out

    def digest_photo_limits(self) -> Dict[str, int]:
        """Лимиты загрузки: сколько всего и сколько на каждую рубрику."""
        return {"total": MAX_DIGEST_PHOTOS, "kind": MAX_DIGEST_PHOTOS_KIND}

    def get_photo_order(self, kind: str = "post") -> str:
        """Порядок выдачи фото: "random" (по умолчанию) или "queue" (по очереди).

        "random" — круг без повторов, но внутри круга случайный выбор: набор
        проходит по разу, порядок каждый круг новый (как было раньше).
        "queue" — строго по очереди (id/used_at): 1,2,3…N, снова 1,2,3… —
        удобно, когда загрузили пачку и хочется, чтобы каждая показалась.
        """
        kind = (kind or "").strip().lower()
        if kind not in ("post", "digest"):
            kind = "post"
        key = f"photo_order_{kind}"
        raw = (self.get_setting(key, "") or "").strip().lower()
        if raw in ("queue", "ordered", "seq", "sequential", "order"):
            return "queue"
        return "random"

    def set_photo_order(self, kind: str, order: str, actor_id: Optional[int] = None) -> str:
        kind = (kind or "").strip().lower()
        if kind not in ("post", "digest"):
            kind = "post"
        order = (order or "").strip().lower()
        if order in ("queue", "ordered", "seq", "sequential", "order"):
            order = "queue"
        else:
            order = "random"
        self.set_setting(f"photo_order_{kind}", order, actor_id=actor_id)
        return order

    def _photo_round_start(self, key: str) -> float:
        """Когда начался текущий круг обложек (0 — круга ещё не было)."""
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key=?",
                                   (key,)).fetchone()
        try:
            return float((row["value"] if row else "") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _photo_round_new(self, key: str, when: float) -> None:
        """Отметить начало нового круга. Пишем напрямую: это служебная метка,
        ей не место в журнале действий (set_setting пишет туда запись)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, repr(float(when))),
            )
            self._db.commit()

    def pick_digest_photo(self, kind: str = "", actor_id: Optional[int] = None) -> str:
        """Обложка поста по кругу без повторов, с поддержкой порядка выдачи.

        У каждого фото помним, когда оно последний раз было обложкой
        (``used_at``), а у рубрики — когда начался текущий круг. Из фото,
        которые в этом круге ещё не выходили, берём одно:
        * ``random`` — случайное (как раньше): набор проходит по разу, порядок
          каждый круг новый;
        * ``queue`` — строго по очереди: по ``used_at``/``id``, 1,2,3…N, снова 1,2,3…
        Пустая строка — фото нет (или файлы пропали с диска).
        """
        kind = str(kind or "").strip().lower()
        if kind not in ("post", "digest"):
            kind = ""
        # порядок: post и digest настраиваются отдельно, по умолчанию random
        order_kind = "digest" if kind == "digest" else "post"
        try:
            order = self.get_photo_order(order_kind)
        except Exception:
            order = "random"
        key = f"digest_photo_round_{kind or 'all'}"
        with self._lock:
            if kind:
                rows = self._db.execute(
                    "SELECT id, path, used_at FROM digest_photos"
                    " WHERE COALESCE(kind,'post')=? ORDER BY id", (kind,)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, path, used_at FROM digest_photos ORDER BY id"
                ).fetchall()
        live = [r for r in rows if r["path"] and os.path.isfile(r["path"])]
        if not live:
            return ""
        start = self._photo_round_start(key)
        pool = [r for r in live if float(r["used_at"] or 0) < start]
        if not pool:
            # Круг закончился: все фото вышли по разу. Начинаем новый круг.
            start = _now()
            self._photo_round_new(key, start)
            pool = live
        if order == "queue":
            # очередь: сначала те, что давно не показывались, затем по id
            pool = sorted(pool, key=lambda r: (float(r["used_at"] or 0), int(r["id"])))
            pick = pool[0]
        else:
            pick = secrets.choice(pool)
        with self._lock:
            self._db.execute("UPDATE digest_photos SET used_at=? WHERE id=?",
                             (start, int(pick["id"])))
            self._db.commit()
        log.debug("обложка: фото %s (%s), в круге ещё %d", pick["id"], order, len(pool) - 1)
        return str(pick["path"])

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
        """Одно фото: те же проверки, что и у пачки (см. ``add_digest_photos``)."""
        r = self._save_digest_photo(data, filename, kind)
        if r.get("ok"):
            self.audit(actor_id, "digest_photo_add", f"{r['kind']}:{r.get('name')}")
        return r

    def add_digest_photos(self, items: Iterable[Tuple[bytes, str]] = (),
                          actor_id: Optional[int] = None,
                          kind: str = "post") -> Dict[str, Any]:
        """Пачка фото из одной загрузки.

        Админ выбирает сразу много файлов — сохраняем каждый (одна запись
        аудита на пачку, чтобы журнал не пух), а ошибки по конкретным файлам
        возвращаем списком: одно битое фото не отменяет остальные.
        """
        kind = "digest" if str(kind or "").strip().lower() == "digest" else "post"
        added: List[int] = []
        names: List[str] = []
        errors: List[Dict[str, Any]] = []
        for data, name in (items or []):
            r = self._save_digest_photo(data, name, kind)
            if r.get("ok"):
                added.append(int(r["id"]))
                names.append(str(r.get("name") or ""))
            else:
                err = {"name": os.path.basename(str(name or ""))[:80],
                       "error": str(r.get("error") or "error")}
                if r.get("scope"):
                    err["scope"] = r["scope"]
                errors.append(err)
        if added:
            detail = (f"{kind}:{names[0]}"[:80] if len(added) == 1
                      else f"{kind}: {len(added)} шт.")
            self.audit(actor_id, "digest_photo_add", detail)
        counts = self.digest_photo_counts()
        return {"ok": bool(added), "added": len(added), "ids": added,
                "errors": errors, "kind": kind, "counts": counts,
                "limits": self.digest_photo_limits()}

    def _save_digest_photo(self, data: bytes, filename: str = "",
                           kind: str = "post") -> Dict[str, Any]:
        """Проверки и запись одного фото. Без аудита — его ведёт вызывающий."""
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
            rows = self._db.execute(
                "SELECT COALESCE(kind,'post') AS k, COUNT(*) AS n"
                " FROM digest_photos GROUP BY k"
            ).fetchall()
            per_kind = {str(r["k"] or "post"): int(r["n"]) for r in rows}
            if per_kind.get(kind, 0) >= MAX_DIGEST_PHOTOS_KIND:
                return {"ok": False, "error": "limit", "scope": "kind",
                        "limit": MAX_DIGEST_PHOTOS_KIND}
            if sum(per_kind.values()) >= MAX_DIGEST_PHOTOS:
                return {"ok": False, "error": "limit", "scope": "total",
                        "limit": MAX_DIGEST_PHOTOS}
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

    # --- 📢 Лог отправки постов и дайджестов (защита от дублей) --------------
    # Бот пишет сюда каждый успешный пост в канал: вид (post/digest), день
    # YYYY-MM-DD и время. После перезапуска он проверяет лог и не шлёт
    # повторно то, что уже ушло за эти сутки — даже если target_day в памяти
    # сбросился. Для постов раз в N часов защита — по времени (последний пост
    # должен быть старше окна), для дайджеста — по дню (один раз в сутки).

    def log_channel_sent(self, kind: str, day: str = "", ts: float = 0.0,
                         extra: str = "") -> None:
        kind = str(kind or "").strip().lower()
        if kind not in ("post", "digest"):
            return
        day = str(day or "").strip()[:12]
        if not day:
            try:
                import datetime
                day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            except Exception:
                day = ""
        ts = float(ts or 0.0) or __import__("time").time()
        extra = str(extra or "")[:500]
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO channel_sent_log(kind, day, ts, extra) VALUES(?,?,?,?)",
                    (kind, day, ts, extra))
                self._db.commit()
                # Чистим старье: держим неделю, остальное не нужно для защиты
                self._db.execute("DELETE FROM channel_sent_log WHERE ts<?",
                                 (ts - 7 * 86400,))
                self._db.commit()
        except Exception as e:
            import logging
            logging.getLogger("liqscope.accounts").debug("channel log: %s", e)

    def was_channel_sent(self, kind: str, day: str = "") -> bool:
        kind = str(kind or "").strip().lower()
        day = str(day or "").strip()[:12]
        if not day or kind not in ("post", "digest"):
            return False
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT 1 FROM channel_sent_log WHERE kind=? AND day=? LIMIT 1",
                    (kind, day)).fetchone()
                return bool(row)
        except Exception:
            return False

    def last_channel_sent_ts(self, kind: str) -> float:
        kind = str(kind or "").strip().lower()
        if kind not in ("post", "digest"):
            return 0.0
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT ts FROM channel_sent_log WHERE kind=? ORDER BY ts DESC LIMIT 1",
                    (kind,)).fetchone()
                return float(row["ts"] or 0.0) if row else 0.0
        except Exception:
            return 0.0

    def list_channel_sent(self, days: int = 7) -> List[Dict[str, Any]]:
        days = max(1, min(int(days or 7), 30))
        try:
            import time
            since = time.time() - days * 86400
        except Exception:
            since = 0
        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT kind, day, ts, extra FROM channel_sent_log WHERE ts>=? ORDER BY ts DESC LIMIT 200",
                    (since,)).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

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
        # HTML-баннер: колонка html или html_code — единый ключ html_banner
        raw_html = (d.get("html_code") or d.get("html") or "").strip()
        d["html_banner"] = raw_html
        # оставляем оба поля для совместимости, но нормируем
        d["html"] = raw_html
        d["html_code"] = raw_html
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
               author_id: Optional[int] = None, link: str = "",
               photo_pending: bool = False, html: str = "") -> Dict[str, Any]:
        """Новое объявление. ``photo_pending`` — фото приложат сразу после
        создания (ему нужен id записи), поэтому «пустой» пост с фотографией
        всё равно принимается: на сайте баннер из одной картинки законен.

        ``html`` — HTML-баннер: если задан, текст/фото не обязательны — баннер
        покажет произвольный HTML в слоте.
        """
        text = (text or "").strip()
        html_raw = (html or "").strip()
        if not text and not (photo or "") and not photo_pending and not html_raw:
            return {"ok": False, "error": "empty"}
        if status not in self.AD_STATUSES:
            status = "draft"
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO ads(text, photo, photo_name, link, html, html_code, targets,"
                " send_at, expires_at, status, results, sent_at, created_at,"
                " author_id) VALUES(?,?,?,?,?,?,?,?,?,?,'{}',0,?,?)",
                (text[:4000], photo or "", os.path.basename(photo_name or "")[:80],
                 (link or "").strip()[:600], html_raw[:20000], html_raw[:20000],
                 json.dumps(targets or {}, ensure_ascii=False), float(send_at or 0),
                 float(expires_at or 0), status, _now(), author_id),
            )
            self._db.commit()
            ad_id = int(cur.lastrowid)
        self.audit(author_id, "ad_add", f"#{ad_id} {status} {text[:60]} html={int(bool(html_raw))}")
        return {"ok": True, "id": ad_id, "ad": self.get_ad(ad_id)}

    def update_ad(self, ad_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        """Точечная правка: текст, получатели, сроки, статус, отчёт о отправке."""
        allowed = ("text", "targets", "send_at", "expires_at", "status",
                   "results", "sent_at", "link", "html", "html_code")
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
            elif key == "link":
                val = str(val or "").strip()[:600]
            elif key in ("html", "html_code"):
                val = str(val or "").strip()[:20000]
            sets.append(f"{key}=?")
            vals.append(val)
        # html — пишем в обе колонки для совместимости
        if any(k in fields for k in ("html", "html_code")):
            html_val = str(fields.get("html") or fields.get("html_code") or "").strip()[:20000]
            if "html" not in fields:
                sets.append("html=?")
                vals.append(html_val)
            if "html_code" not in fields:
                sets.append("html_code=?")
                vals.append(html_val)
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
                        limit: int = 8, place: str = "") -> List[Dict[str, Any]]:
        """Что показывать баннером на сайте: отправлено, не истекло, для этой страницы.

        ``place`` — ``landing`` (главная), ``terminal`` (под графиком), ``digest``
        (дайджест), ``hourly`` (сводка по часам); пусто — любая страница сайта.
        Разметка «куда идти» живёт в JSON внутри ``targets``, поэтому отбор по
        странице делаем в Python: в SQLite JSON фильтровался бы подстрокой и
        путал ``"site"`` с ``"site_x"``.
        """
        now = _now() if now is None else float(now)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM ads WHERE status='sent' AND send_at<=?"
                " AND (expires_at=0 OR expires_at>?) ORDER BY send_at DESC, id DESC"
                " LIMIT ?", (now, now, int(limit) * 2)
            ).fetchall()
        out = [self._ad_row(r) for r in rows]
        # landing → site (историческое имя), terminal, digest, hourly — отдельные страницы
        # Для digest/hourly баннер должен показываться всем как на главной:
        # если объявление отмечено для главной (site), оно также подходит для
        # дайджеста и сводки, иначе старые записи с site=true исчезли бы с этих
        # страниц после расширения таргетинга.
        place_norm = str(place or "").strip().lower()
        key = {
            "landing": "site",
            "terminal": "terminal",
            "digest": "digest",
            "hourly": "hourly",
        }.get(place_norm)
        keep = []
        for a in out:
            tg = a.get("targets") or {}
            if place_norm in ("digest", "hourly"):
                # digest/hourly: показываем если явно отмечено для этой страницы
                # ИЛИ если отмечено для главной (site) — так баннер возвращается
                # на эти страницы и виден всем, как на лендинге.
                if not (tg.get(key) or tg.get("site")):
                    continue
            elif key:
                if not tg.get(key):
                    continue
            elif not (tg.get("site") or tg.get("terminal") or tg.get("digest") or tg.get("hourly")):
                continue
            keep.append(a)
            if len(keep) >= max(1, int(limit)):
                break
        return keep

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
        # Пустой язык не заменяем на русский: язык по умолчанию решает бот
        # (bot_i18n.DEFAULT_LANG), и это английский — здесь мы про него не знаем
        return [{"tg_id": int(r["tg_id"]), "language": r["language"] or ""}
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
            # держим ленту компактной: максимум 30 сигналов на пользователя
            # (по 10 на метрику liq/cvd/oi) — остальное подтирается
            try:
                self._db.execute(
                    "DELETE FROM alert_events WHERE user_id=? AND id NOT IN "
                    "(SELECT id FROM alert_events WHERE user_id=? ORDER BY id DESC LIMIT 30)",
                    (int(user_id), int(user_id)),
                )
            except Exception:
                pass
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

    # ----- 💬 Мини-чат терминала (3 дня истории) -----------------------------
    CHAT_MAX_LEN = 500
    CHAT_KEEP_SEC = 3 * 86400
    CHAT_LIMIT = 200

    def add_chat_message(self, user_id: int, display_name: str, text: str,
                         is_admin: bool = False) -> Dict[str, Any]:
        """Сообщение в чат терминала: только зарегистрированные пишут."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        if len(text) > self.CHAT_MAX_LEN:
            text = text[:self.CHAT_MAX_LEN]
        now = _now()
        with self._lock:
            edge = now - self.CHAT_KEEP_SEC
            if secrets.randbelow(20) == 0:
                self._db.execute("DELETE FROM terminal_chat WHERE created_at<?",
                                 (edge,))
            cur = self._db.execute(
                "INSERT INTO terminal_chat(user_id, display_name, text, created_at, is_admin)"
                " VALUES(?,?,?,?,?)",
                (int(user_id), (display_name or "")[:80], text, now,
                 1 if is_admin else 0),
            )
            self._db.commit()
            mid = int(cur.lastrowid)
        return {"ok": True, "id": mid, "created_at": now}

    def list_chat_messages(self, limit: int = 100, after_id: int = 0,
                           keep_sec: Optional[int] = None) -> List[Dict[str, Any]]:
        """Последние сообщения чата за 3 дня. ``after_id`` — только новее."""
        now = _now()
        keep = int(keep_sec) if keep_sec else self.CHAT_KEEP_SEC
        edge = now - keep
        limit = max(1, min(int(limit or 100), self.CHAT_LIMIT))
        after_id = max(0, int(after_id or 0))
        with self._lock:
            if after_id:
                rows = self._db.execute(
                    "SELECT id, user_id, display_name, text, created_at, is_admin"
                    " FROM terminal_chat WHERE created_at>=? AND id>? ORDER BY id",
                    (edge, after_id),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, user_id, display_name, text, created_at, is_admin"
                    " FROM terminal_chat WHERE created_at>=? ORDER BY id DESC LIMIT ?",
                    (edge, limit),
                ).fetchall()
                rows = list(reversed(rows))
        return [dict(r) for r in rows]

    def delete_chat_message(self, msg_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM terminal_chat WHERE id=?",
                                   (int(msg_id),))
            self._db.commit()
            return bool(cur.rowcount)

    def prune_chat(self, keep_sec: Optional[int] = None) -> int:
        edge = _now() - (int(keep_sec) if keep_sec else self.CHAT_KEEP_SEC)
        with self._lock:
            cur = self._db.execute("DELETE FROM terminal_chat WHERE created_at<?",
                                   (edge,))
            self._db.commit()
            return int(cur.rowcount or 0)

    # ----- 🔒 Приватные диалоги (3 дня истории, приглашение, юзер_id = якорь) --
    PRIVATE_KEEP_SEC = 3 * 86400
    PRIVATE_LIMIT = 200

    def _private_pair(self, user_id: int, peer_id: int) -> tuple:
        a, b = sorted((int(user_id), int(peer_id)))
        return a, b

    def private_room_for_pair(self, user_id: int,
                              peer_id: int) -> Optional[Dict[str, Any]]:
        a, b = self._private_pair(user_id, peer_id)
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM private_chats WHERE user_a=? AND user_b=?",
                (a, b),
            ).fetchone()
        return dict(row) if row else None

    def private_room_get(self, room_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM private_chats WHERE id=?",
                                   (int(room_id),)).fetchone()
        return dict(row) if row else None

    def private_room_open(self, user_id: int,
                          peer_id: int) -> Dict[str, Any]:
        """Найти комнату пары или создать с приглашением peer_id.

        Отклонённая комната при повторном приглашении снова становится
        «pending». Один диалог на пару — смена ника ничего не ломает,
        идентификаторы участников — user_id.
        """
        a, b = self._private_pair(user_id, peer_id)
        if a == b:
            return {"ok": False, "error": "self"}
        now = _now()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM private_chats WHERE user_a=? AND user_b=?",
                (a, b),
            ).fetchone()
            if row:
                r = dict(row)
                if r.get("status") == "declined":
                    self._db.execute(
                        "UPDATE private_chats SET status='pending',"
                        " invited_by=?, updated_at=? WHERE id=?",
                        (int(user_id), now, r["id"]),
                    )
                    self._db.commit()
                    r["status"] = "pending"
                    r["invited_by"] = int(user_id)
                    r["updated_at"] = now
                return {"ok": True, "room": r, "created": False}
            cur = self._db.execute(
                "INSERT INTO private_chats(user_a,user_b,invited_by,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (a, b, int(user_id), "pending", now, now),
            )
            self._db.commit()
            rid = int(cur.lastrowid)
        room = {"id": rid, "user_a": a, "user_b": b,
                "invited_by": int(user_id), "status": "pending",
                "created_at": now, "updated_at": now}
        return {"ok": True, "room": room, "created": True}

    def private_room_member(self, room: Dict[str, Any],
                            user_id: int) -> bool:
        uid = int(user_id)
        return uid in (int(room["user_a"]), int(room["user_b"]))

    def private_room_other(self, room: Dict[str, Any],
                           user_id: int) -> int:
        uid = int(user_id)
        return int(room["user_b"]) if uid == int(room["user_a"]) \
            else int(room["user_a"])

    def private_room_set_status(self, room_id: int, status: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE private_chats SET status=?, updated_at=? WHERE id=?",
                (status, _now(), int(room_id)),
            )
            self._db.commit()

    def private_add_message(self, room_id: int, user_id: int,
                            text: str) -> Dict[str, Any]:
        """Сообщение в приватный диалог. Копия на пару секунд в историю 3 дня."""
        text = (text or "").strip()[: self.CHAT_MAX_LEN]
        if not text:
            return {"ok": False, "error": "empty"}
        now = _now()
        with self._lock:
            edge = now - self.PRIVATE_KEEP_SEC
            if secrets.randbelow(20) == 0:
                self._db.execute(
                    "DELETE FROM private_messages WHERE created_at<?", (edge,))
            cur = self._db.execute(
                "INSERT INTO private_messages(room_id,user_id,text,created_at)"
                " VALUES(?,?,?,?)",
                (int(room_id), int(user_id), text, now),
            )
            self._db.execute(
                "UPDATE private_chats SET updated_at=? WHERE id=?",
                (now, int(room_id)),
            )
            self._db.commit()
            mid = int(cur.lastrowid)
        return {"ok": True, "id": mid, "text": text, "created_at": now}

    def private_room_messages(self, room_id: int, after_id: int = 0,
                              limit: int = 200) -> List[Dict[str, Any]]:
        """История комнаты за 3 дня. Имена — актуальные из профиля (ник менялся — имена свежие)."""
        now = _now()
        edge = now - self.PRIVATE_KEEP_SEC
        limit = max(1, min(int(limit or 200), self.PRIVATE_LIMIT))
        after_id = max(0, int(after_id or 0))
        with self._lock:
            if after_id:
                rows = self._db.execute(
                    "SELECT m.id, m.room_id, m.user_id, m.text, m.created_at"
                    " FROM private_messages m WHERE m.room_id=? AND m.created_at>=?"
                    " AND m.id>? ORDER BY m.id",
                    (int(room_id), edge, after_id),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT m.id, m.room_id, m.user_id, m.text, m.created_at"
                    " FROM private_messages m WHERE m.room_id=? AND m.created_at>=?"
                    " ORDER BY m.id DESC LIMIT ?",
                    (int(room_id), edge, limit),
                ).fetchall()
                rows = list(reversed(rows))
            # имена и флаги админа — по текущему состоянию профиля
            names: Dict[int, tuple] = {}
            out = []
            uids = {int(r["user_id"]) for r in rows}
            for uid in uids:
                urow = self._db.execute(
                    "SELECT id, first_name, last_name, username, email, tg_id,"
                    " is_admin FROM users WHERE id=?", (uid,)).fetchone()
                if urow:
                    u = dict(urow)
                    names[uid] = (display_name(u), bool(u.get("is_admin")))
                else:
                    names[uid] = (f"id{uid}", False)
            for r in rows:
                d = dict(r)
                nm, adm = names.get(int(d["user_id"]), (f"id{d['user_id']}", False))
                d["name"] = nm
                d["is_admin"] = 1 if adm else 0
                out.append(d)
        return out

    def private_mark_read(self, room_id: int, user_id: int,
                          last_id: int) -> None:
        """Отметка «дочитано до id» + снятие напоминаний до этого id."""
        with self._lock:
            self._db.execute(
                "INSERT INTO private_reads(room_id,user_id,last_read_id,"
                " reminded_id, reminded_at) VALUES(?,?,?,0,0)"
                " ON CONFLICT(room_id,user_id) DO UPDATE SET"
                " last_read_id=MAX(last_read_id, excluded.last_read_id),"
                " reminded_id=MAX(reminded_id, excluded.reminded_id)",
                (int(room_id), int(user_id), int(last_id)),
            )
            self._db.commit()

    def private_last_read(self, room_id: int, user_id: int) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT last_read_id FROM private_reads WHERE room_id=? AND user_id=?",
                (int(room_id), int(user_id))).fetchone()
        return int(row["last_read_id"]) if row else 0

    def private_unread_for(self, user_id: int) -> Dict[int, int]:
        """room_id -> сколько непрочитанных сообщений от собеседника."""
        uid = int(user_id)
        edge = _now() - self.PRIVATE_KEEP_SEC
        with self._lock:
            rows = self._db.execute(
                "SELECT c.id AS room,"
                " (SELECT COUNT(*) FROM private_messages m"
                "   WHERE m.room_id=c.id AND m.created_at>=? AND m.user_id!=?"
                "     AND m.id > COALESCE((SELECT r.last_read_id FROM private_reads r"
                "                 WHERE r.room_id=c.id AND r.user_id=?),0)) AS n"
                " FROM private_chats c WHERE (c.user_a=? OR c.user_b=?)"
                " AND c.updated_at>=?",
                (edge, uid, uid, uid, uid, edge),
            ).fetchall()
        return {int(r["room"]): int(r["n"]) for r in rows if int(r["n"]) > 0}

    def private_rooms_for(self, user_id: int) -> List[Dict[str, Any]]:
        """Комнаты участника: карточка + последнее сообщение + непрочитанное."""
        uid = int(user_id)
        edge = _now() - self.PRIVATE_KEEP_SEC
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM private_chats WHERE (user_a=? OR user_b=?)"
                " AND updated_at>=? ORDER BY updated_at DESC LIMIT 100",
                (uid, uid, edge),
            ).fetchall()
        rooms: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            rid = int(d["id"])
            other = self.private_room_other(d, uid)
            u = self.get_user(other)
            if u is None:
                name, banned = f"id{other}", False
            else:
                name = u.get("display_name") or f"id{other}"
                banned = bool(u.get("is_banned"))
            with self._lock:
                last = self._db.execute(
                    "SELECT id, user_id, text, created_at FROM private_messages"
                    " WHERE room_id=? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
                unread_row = self._db.execute(
                    "SELECT COUNT(*) AS n FROM private_messages m"
                    " WHERE m.room_id=? AND m.created_at>=? AND m.user_id!=?"
                    " AND m.id > COALESCE((SELECT r2.last_read_id FROM private_reads r2"
                    "             WHERE r2.room_id=? AND r2.user_id=?),0)",
                    (rid, edge, uid, rid, uid)).fetchone()
            last_json = None
            if last:
                lm = dict(last)
                lu = self.get_user(int(lm["user_id"]))
                lm["name"] = (lu or {}).get("display_name") or f"id{lm['user_id']}"
                lm["text"] = (lm.get("text") or "")[:80]
                last_json = lm
            rooms.append({
                "id": rid,
                "status": d.get("status") or "pending",
                "invited_by": int(d.get("invited_by") or 0),
                "peer": {"id": other, "name": name, "banned": banned},
                "last": last_json,
                "unread": int(unread_row["n"]) if unread_row else 0,
                "updated_at": float(d.get("updated_at") or 0),
                "created_at": float(d.get("created_at") or 0),
            })
        rooms.sort(key=lambda x: x["updated_at"], reverse=True)
        return rooms

    def private_notify_counts(self, user_id: int) -> Dict[str, int]:
        """Счётчики для бейджа: непрочитанные сообщения и входящие приглашения."""
        uid = int(user_id)
        edge = _now() - self.PRIVATE_KEEP_SEC
        unread = sum(self.private_unread_for(uid).values())
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM private_chats"
                " WHERE status='pending' AND updated_at>=?"
                " AND ((user_a!=? AND user_b=?) OR (user_b!=? AND user_a=?))"
                " AND invited_by!=?",
                (edge, uid, uid, uid, uid, uid),
            ).fetchone()
        return {"unread": int(unread), "invites": int(row["n"]) if row else 0}

    def private_reminder_state(self, room_id: int, user_id: int) -> tuple:
        """(reminded_id, reminded_at) — до какого сообщения уже напоминали."""
        with self._lock:
            row = self._db.execute(
                "SELECT reminded_id, reminded_at FROM private_reads"
                " WHERE room_id=? AND user_id=?",
                (int(room_id), int(user_id))).fetchone()
        if not row:
            return 0, 0.0
        return int(row["reminded_id"] or 0), float(row["reminded_at"] or 0)

    def private_reminder_mark(self, room_id: int, user_id: int,
                              msg_id: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO private_reads(room_id,user_id,last_read_id,"
                " reminded_id, reminded_at) VALUES(?,?,0,?,?)"
                " ON CONFLICT(room_id,user_id) DO UPDATE SET"
                " reminded_id=MAX(reminded_id, excluded.reminded_id),"
                " reminded_at=excluded.reminded_at",
                (int(room_id), int(user_id), int(msg_id), _now()),
            )
            self._db.commit()

    def private_rooms_due(self, before_ts: float) -> List[Dict[str, Any]]:
        """Комнаты с безответными сообщениями старше before_ts (для TG-напоминаний).

        Для каждой комнаты отдаём самое свежее непрочитанное сообщение от
        собеседника и обоих участников: «прочитал или ответил» = сигнал не слать.
        """
        edge = _now() - self.PRIVATE_KEEP_SEC
        with self._lock:
            rooms = self._db.execute(
                "SELECT * FROM private_chats WHERE updated_at>=?"
                " AND status IN ('pending','active')", (edge,)).fetchall()
            due: List[Dict[str, Any]] = []
            for r in rooms:
                d = dict(r)
                rid = int(d["id"])
                for member in (int(d["user_a"]), int(d["user_b"])):
                    last_read = self._db.execute(
                        "SELECT COALESCE(MAX(last_read_id),0) AS x FROM private_reads"
                        " WHERE room_id=? AND user_id=?", (rid, member)).fetchone()
                    last_answer = self._db.execute(
                        "SELECT COALESCE(MAX(id),0) AS x FROM private_messages"
                        " WHERE room_id=? AND user_id=?", (rid, member)).fetchone()
                    floor = max(int(last_read["x"] or 0),
                                int(last_answer["x"] or 0))
                    pending = self._db.execute(
                        "SELECT m.id, m.user_id, m.text, m.created_at"
                        " FROM private_messages m"
                        " WHERE m.room_id=? AND m.created_at>=? AND m.user_id!=?"
                        "   AND m.id>? AND m.created_at<?"
                        " ORDER BY m.id DESC LIMIT 1",
                        (rid, edge, member, floor, float(before_ts)),
                    ).fetchone()
                    if not pending:
                        continue
                    reminded = self._db.execute(
                        "SELECT COALESCE(MAX(reminded_id),0) AS x FROM private_reads"
                        " WHERE room_id=? AND user_id=?", (rid, member)).fetchone()
                    if int(pending["id"]) <= int(reminded["x"] or 0):
                        continue
                    due.append({
                        "room_id": rid,
                        "member": member,
                        "message_id": int(pending["id"]),
                        "sender_id": int(pending["user_id"]),
                        "text": pending["text"],
                        "created_at": float(pending["created_at"]),
                    })
        return due

    def prune_private(self, keep_sec: Optional[int] = None) -> int:
        keep = int(keep_sec) if keep_sec else self.PRIVATE_KEEP_SEC
        edge = _now() - keep
        with self._lock:
            msg = self._db.execute(
                "DELETE FROM private_messages WHERE created_at<?", (edge,))
            reads = self._db.execute(
                "DELETE FROM private_reads WHERE room_id IN"
                " (SELECT id FROM private_chats WHERE updated_at<?)", (edge,))
            rooms = self._db.execute(
                "DELETE FROM private_chats WHERE updated_at<?", (edge,))
            self._db.commit()
        return int((msg.rowcount or 0) + (reads.rowcount or 0)
                   + (rooms.rowcount or 0))

    def chat_participants(self, keep_sec: Optional[int] = None,
                          limit: int = 50) -> List[Dict[str, Any]]:
        """Кто писал в общий чат за окно истории (для списка и приглашений)."""
        keep = int(keep_sec) if keep_sec else self.CHAT_KEEP_SEC
        edge = _now() - keep
        lim = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id, COUNT(*) AS cnt, MAX(id) AS last_mid,"
                " MAX(created_at) AS last_at"
                " FROM terminal_chat WHERE created_at>=?"
                " GROUP BY user_id ORDER BY last_mid DESC LIMIT ?",
                (edge, lim),
            ).fetchall()
        out = []
        for r in rows:
            uid = int(r["user_id"])
            u = self.get_user(uid)
            out.append({
                "id": uid,
                "name": (u or {}).get("display_name") or f"id{uid}",
                "admin": bool((u or {}).get("is_admin")),
                "messages": int(r["cnt"]),
                "last_at": float(r["last_at"] or 0),
            })
        return out

    def find_chat_users(self, q: str, limit: int = 8,
                        exclude_id: int = 0) -> List[Dict[str, Any]]:
        """Поиск кандидатов в личный диалог по началу ника/@username/имени.

        Только для авторизованных; письма наружу не отдаём — в чате человек
        представлен id и ником, и они же остаются якорем приватного диалога.
        """
        q = (q or "").strip().lstrip("@")[:64]
        if len(q) < 2:
            return []
        lim = max(1, min(int(limit), 10))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, first_name, last_name, username, is_admin FROM users"
                " WHERE is_banned=0 AND id!=?"
                "   AND (username LIKE ? OR first_name LIKE ? OR last_name LIKE ?"
                "        OR email LIKE ?)"
                " ORDER BY last_seen DESC LIMIT ?",
                (int(exclude_id or 0), q + "%", q + "%", q + "%", q + "%", lim),
            ).fetchall()
        out = []
        for r in rows:
            u = dict(r)
            out.append({"id": int(u["id"]),
                        "name": display_name(u),
                        "username": u.get("username") or "",
                        "admin": bool(u.get("is_admin"))})
        return out


    # ----- 💬 Комментарии к дайджесту и сводке по часам ----------------------
    CONTENT_COMMENT_MAX_LEN = 1000
    CONTENT_COMMENT_LIMIT = 200
    CONTENT_COMMENT_KINDS = ("digest", "hourly")

    def add_content_comment(self, user_id: int, display_name: str, kind: str,
                            item_id: str, text: str,
                            is_admin: bool = False) -> Dict[str, Any]:
        """Комментарий к дайджесту/часовке: только зарегистрированные пишут.

        ``kind`` — ``digest`` (дневной выпуск) или ``hourly`` (сводка по часам),
        ``item_id`` — идентификатор: дата YYYY-MM-DD для дайджеста или дата/post-id
        для часовки. Текст до 1000 знаков, хранится бессрочно (модерация — через удаление).
        """
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        if len(text) > self.CONTENT_COMMENT_MAX_LEN:
            text = text[:self.CONTENT_COMMENT_MAX_LEN]
        kind = str(kind or "").strip().lower()
        if kind not in self.CONTENT_COMMENT_KINDS:
            return {"ok": False, "error": "bad_kind"}
        item_id = str(item_id or "").strip()[:120]
        if not item_id:
            return {"ok": False, "error": "bad_item"}
        now = _now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO content_comments(kind, item_id, user_id, display_name, text, created_at, is_admin)"
                " VALUES(?,?,?,?,?,?,?)",
                (kind, item_id, int(user_id), (display_name or "")[:80], text, now,
                 1 if is_admin else 0),
            )
            self._db.commit()
            mid = int(cur.lastrowid)
        return {"ok": True, "id": mid, "created_at": now}

    def list_content_comments(self, kind: str, item_id: str,
                              limit: int = 100, after_id: int = 0) -> List[Dict[str, Any]]:
        """Комментарии к конкретному выпуску/посту. ``after_id`` — только новее."""
        kind = str(kind or "").strip().lower()
        if kind not in self.CONTENT_COMMENT_KINDS:
            return []
        item_id = str(item_id or "").strip()[:120]
        if not item_id:
            return []
        limit = max(1, min(int(limit or 100), self.CONTENT_COMMENT_LIMIT))
        after_id = max(0, int(after_id or 0))
        with self._lock:
            if after_id:
                rows = self._db.execute(
                    "SELECT id, kind, item_id, user_id, display_name, text, created_at, is_admin"
                    " FROM content_comments WHERE kind=? AND item_id=? AND id>? ORDER BY id",
                    (kind, item_id, after_id),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, kind, item_id, user_id, display_name, text, created_at, is_admin"
                    " FROM content_comments WHERE kind=? AND item_id=? ORDER BY id DESC LIMIT ?",
                    (kind, item_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
        return [dict(r) for r in rows]

    def list_content_comments_by_kind(self, kind: str, limit: int = 50) -> List[Dict[str, Any]]:
        kind = str(kind or "").strip().lower()
        if kind not in self.CONTENT_COMMENT_KINDS:
            return []
        limit = max(1, min(int(limit or 100), 200))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, kind, item_id, user_id, display_name, text, created_at, is_admin"
                " FROM content_comments WHERE kind=? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_content_comment(self, comment_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM content_comments WHERE id=?",
                                   (int(comment_id),))
            self._db.commit()
            return bool(cur.rowcount)

    def content_comments_count(self, kind: str = "", item_id: str = "") -> int:
        with self._lock:
            if kind and item_id:
                row = self._db.execute(
                    "SELECT COUNT(*) FROM content_comments WHERE kind=? AND item_id=?",
                    (str(kind).strip().lower(), str(item_id).strip()[:120]),
                ).fetchone()
            elif kind:
                row = self._db.execute(
                    "SELECT COUNT(*) FROM content_comments WHERE kind=?",
                    (str(kind).strip().lower(),),
                ).fetchone()
            else:
                row = self._db.execute("SELECT COUNT(*) FROM content_comments").fetchone()
        return int(row[0] if row else 0)

