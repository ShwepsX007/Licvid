"""Пользователи LiqScope: Telegram-вход, сессии, визиты, сервисы, админка.

SQLite в data/accounts.db (путь задаётся LIQSCOPE_ACCOUNTS_DB).
Регистрация — только через Telegram (виджет или deep-link бота).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("liqscope.accounts")

COOKIE_SID = "liqscope_sid"
COOKIE_VID = "liqscope_vid"
SESSION_DAYS = 30
NONCE_TTL = 300  # 5 минут на подтверждение входа в боте

DEFAULT_SERVICES = (
    {
        "slug": "alerts",
        "title": "Алерты по объёму",
        "title_en": "Volume alerts",
        "description": "Порог объёма ликвидации и окно времени — сообщение в кабинет и в Telegram.",
        "icon": "🔔",
        "enabled": 1,
        "coming_soon": 1,
        "sort": 10,
    },
    {
        "slug": "correlations",
        "title": "Корреляции валют",
        "title_en": "Coin correlations",
        "description": "Связь ликвидаций и CVD между монетами за выбранный период.",
        "icon": "🔗",
        "enabled": 1,
        "coming_soon": 1,
        "sort": 20,
    },
    {
        "slug": "watchlist",
        "title": "Сторож монет",
        "title_en": "Watchlist",
        "description": "Личный список пар: всплески ликвидаций, смена CVD и OI.",
        "icon": "👁",
        "enabled": 1,
        "coming_soon": 1,
        "sort": 30,
    },
    {
        "slug": "digest",
        "title": "Дневной дайджест",
        "title_en": "Daily digest",
        "description": "Сводка рынка за сутки в кабинет и в Telegram.",
        "icon": "📰",
        "enabled": 1,
        "coming_soon": 1,
        "sort": 40,
    },
)


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
    return "id" + str(user.get("tg_id") or user.get("id") or "")


def public_user(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    return {
        "id": int(d["id"]),
        "tg_id": int(d["tg_id"]),
        "username": d.get("username") or "",
        "first_name": d.get("first_name") or "",
        "last_name": d.get("last_name") or "",
        "photo_url": d.get("photo_url") or "",
        "language": d.get("language") or "ru",
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
    def __init__(self, path: str, secret: str, admin_ids: Iterable[int] = ()):
        self.path = path
        self.secret = secret or secrets.token_hex(16)
        self.admin_ids = {int(x) for x in admin_ids if int(x)}
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
                    tg_id INTEGER UNIQUE NOT NULL,
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
                    ip_hash TEXT
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
            self._db.commit()

    # ----- users ----------------------------------------------------------
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
            is_admin = 1 if tg_id in self.admin_ids else (int(row["is_admin"]) if row else 0)
            if row:
                self._db.execute(
                    "UPDATE users SET username=?, first_name=?, last_name=?, photo_url=?,"
                    " language=?, is_admin=?, last_seen=?, login_count=login_count+1"
                    " WHERE tg_id=?",
                    (username or row["username"], first or row["first_name"],
                     last or row["last_name"], photo or row["photo_url"],
                     lang or row["language"], is_admin, now, tg_id),
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
                rows = self._db.execute(
                    "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ?"
                    " OR last_name LIKE ? OR CAST(tg_id AS TEXT) LIKE ?"
                    " ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                    (like, like, like, like, limit, offset),
                ).fetchall()
                matched = self._db.execute(
                    "SELECT COUNT(*) FROM users WHERE username LIKE ? OR first_name LIKE ?"
                    " OR last_name LIKE ? OR CAST(tg_id AS TEXT) LIKE ?",
                    (like, like, like, like),
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
    def record_visit(self, path: str, vid: str, user_id: Optional[int], ip_hash: str) -> None:
        path = (path or "/")[:120]
        with self._lock:
            self._db.execute(
                "INSERT INTO visits(ts,path,vid,user_id,ip_hash) VALUES(?,?,?,?,?)",
                (_now(), path, (vid or "")[:40], user_id, ip_hash),
            )
            # не копим бесконечно: раз в ~200 визитов чистим старше 90 дней
            if secrets.randbelow(200) == 0:
                self._db.execute("DELETE FROM visits WHERE ts<?", (_now() - 90 * 86400,))
            self._db.commit()

    def visit_stats(self, days: int = 14) -> Dict[str, Any]:
        days = max(1, min(int(days), 90))
        since = _now() - days * 86400
        day0 = _now() - (_now() % 86400)
        with self._lock:
            today_views = self._db.execute(
                "SELECT COUNT(*) FROM visits WHERE ts>=?", (day0,)
            ).fetchone()[0]
            today_uniques = self._db.execute(
                "SELECT COUNT(DISTINCT vid) FROM visits WHERE ts>=? AND vid!=''", (day0,)
            ).fetchone()[0]
            rows = self._db.execute(
                "SELECT strftime('%Y-%m-%d', ts, 'unixepoch') AS day,"
                " COUNT(*) AS views, COUNT(DISTINCT vid) AS uniques"
                " FROM visits WHERE ts>=? GROUP BY day ORDER BY day",
                (since,),
            ).fetchall()
            top_paths = self._db.execute(
                "SELECT path, COUNT(*) AS n FROM visits WHERE ts>=?"
                " GROUP BY path ORDER BY n DESC LIMIT 8",
                (since,),
            ).fetchall()
        by_day = [{"day": r["day"], "views": r["views"], "uniques": r["uniques"]} for r in rows]
        return {
            "today_views": today_views,
            "today_uniques": today_uniques,
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

    def tg_ids_for_broadcast(self) -> List[int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT tg_id FROM users WHERE is_banned=0"
            ).fetchall()
        return [int(r["tg_id"]) for r in rows]
