"""Менеджер системных настроек (закрытие задачи «динамическая конфигурация»).

Вся конфигурация сервиса (SMTP, Telegram, админские доступы, лимиты) исторически
задавалась переменными окружения в systemd. Чтобы владелец мог менять её на лету
из админки без `systemctl edit` и перезапуска, значения читаются так:

    таблица ``system_settings`` в accounts.db  →  переменная окружения  →  дефолт

Модуль самодостаточен: держит собственное SQLite-подключение (та же база,
что у ``accounts.Store`` — WAL позволяет несколько читателей/писателей),
кэш значений в RAM и спецификацию управляемых ключей (какие секреты
маскировать в API, что применяется сразу, а что требует перезапуска).

Применение на лету:
* SMTP/почта: ``SettingsManager.apply_mailer`` пересобирает транспорт
  перед отправкой (вызывается из ``web_account._mailer`` на каждое письмо);
* админы: ``SettingsManager.apply_admin_access`` обновляет списки
  ``Store.admin_ids``/``Store.admin_emails`` без рестарта;
* токен бота и лимиты, читаемые один раз при старте, помечены
  ``restart=True`` — админка показывает предупреждение.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any, Dict, Optional

log = logging.getLogger("liqscope.settings")

HERE = os.path.dirname(os.path.abspath(__file__))

#: Заполнитель секрета в ответах админки и формах: «значение задано, но не
#: показывается». Пустой ввод при сохранении секрета = «оставить как было».
MASK = "***"

#: Разделы для карточек админки.
SECTION_SMTP = "smtp"
SECTION_TELEGRAM = "telegram"
SECTION_ACCESS = "access"
SECTION_LIMITS = "limits"

#: Управляемые ключи. ``secret`` — маскировать в ответах и не писать в аудит
#: значение; ``restart`` — изменение подхватывается только после рестарта
#: сервиса (значение читается один раз при старте процесса).
MANAGED_SETTINGS: Dict[str, Dict[str, Any]] = {
    # ✉️ Почта: транспорт и отправитель
    "LIQSCOPE_SMTP_HOST": {"section": SECTION_SMTP},
    "LIQSCOPE_SMTP_PORT": {"section": SECTION_SMTP},
    "LIQSCOPE_SMTP_USER": {"section": SECTION_SMTP},
    "LIQSCOPE_SMTP_PASSWORD": {"section": SECTION_SMTP, "secret": True},
    "LIQSCOPE_SMTP_FROM": {"section": SECTION_SMTP},
    "LIQSCOPE_SMTP_TLS": {"section": SECTION_SMTP},
    "LIQSCOPE_SMTP_TIMEOUT": {"section": SECTION_SMTP},
    "LIQSCOPE_SMTP_IPV4": {"section": SECTION_SMTP},
    "LIQSCOPE_SMTP_SSL": {"section": SECTION_SMTP},
    # …запасные транспорты письма (папка-стенд и HTTPS-API рассылок)
    "LIQSCOPE_MAIL_DIR": {"section": SECTION_SMTP},
    "LIQSCOPE_MAIL_API": {"section": SECTION_SMTP},
    "LIQSCOPE_MAIL_API_KEY": {"section": SECTION_SMTP, "secret": True},
    "LIQSCOPE_MAIL_API_SECRET": {"section": SECTION_SMTP, "secret": True},
    "LIQSCOPE_MAIL_API_URL": {"section": SECTION_SMTP},
    "LIQSCOPE_MAIL_API_FROM": {"section": SECTION_SMTP},
    # 🤖 Telegram-бот: токен читается один раз при старте — нужен рестарт
    "LIQSCOPE_BOT_TOKEN": {"section": SECTION_TELEGRAM, "secret": True,
                           "restart": True},
    # 🛡️ Доступы: списки админов применяются сразу (см. apply_admin_access)
    "LIQSCOPE_ADMIN_EMAILS": {"section": SECTION_ACCESS},
    "LIQSCOPE_ADMIN_IDS": {"section": SECTION_ACCESS},
    # 🧮 Лимиты и кэш (читаются при старте — нужен рестарт)
    "LIQSCOPE_LEVELS_BATCH": {"section": SECTION_LIMITS, "restart": True},
    "LIQSCOPE_LEVELS_EVENTS_TTL_SEC": {"section": SECTION_LIMITS, "restart": True},
    "LIQSCOPE_SNAP_CACHE_SEC": {"section": SECTION_LIMITS, "restart": True},
    # исключение: читается на каждое WS-подключение, поэтому без рестарта
    "LIQSCOPE_WS_INIT_LIQ": {"section": SECTION_LIMITS},
}

#: Ключи с секретами — маскируются в ответах и формах.
SECRET_KEYS = frozenset(k for k, m in MANAGED_SETTINGS.items() if m.get("secret"))


def debug_mode() -> bool:
    """``LIQSCOPE_DEBUG=1`` — подробный режим (например, полные токены в логе)."""
    return (os.getenv("LIQSCOPE_DEBUG", "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def mask_token(token: str, keep: int = 8) -> str:
    """Bearer-токен для журнала (аудит AUTH-01): префикс + маска.

    Полные токены пишутся в лог только при ``LIQSCOPE_DEBUG=1`` — см.
    ``web_account._send_mail_blocking``. Короткий токен скрываем целиком.
    """
    token = token or ""
    if not token:
        return ""
    if len(token) <= keep:
        return "...hidden"
    return token[:keep] + "...hidden"


def mask_secret(value: str) -> str:
    """Секрет для ответа админке: задан — маска, не задан — пусто."""
    return MASK if (value or "") else ""


def _flag_value(raw: str, default: bool = False) -> bool:
    v = (raw or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


class SettingsManager:
    """Хранилище настроек: БД → окружение → дефолт, плюс кэш в RAM."""

    TABLE = "system_settings"

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv(
            "LIQSCOPE_ACCOUNTS_DB", os.path.join(HERE, "data", "accounts.db"))
        self._lock = threading.Lock()
        self._db: Optional[sqlite3.Connection] = None
        # RAM-кэш значений из БД: экономит SELECT на каждое письмо/запрос.
        # Ключ — всегда управляемая настройка, чужие ключи не кэшируем.
        self._cache: Dict[str, str] = {}
        self._cache_loaded = False
        self._ensure_schema()

    # ----- подключение -----------------------------------------------------
    def _ensure_schema(self) -> None:
        with self._lock:
            if self._db is None:
                os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
                self._db = sqlite3.connect(self.db_path, check_same_thread=False)
                self._db.row_factory = sqlite3.Row
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute(
                f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
                " key TEXT PRIMARY KEY,"
                " value TEXT NOT NULL,"
                " updated_ts REAL NOT NULL DEFAULT 0)")

    def _load_cache_locked(self) -> None:
        assert self._db is not None
        rows = self._db.execute(f"SELECT key, value FROM {self.TABLE}").fetchall()
        self._cache = {r["key"]: r["value"] for r in rows
                       if r["key"] in MANAGED_SETTINGS}
        self._cache_loaded = True

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:  # noqa: BLE001 — закрытие не должно падать
                    pass
                self._db = None
                self._cache_loaded = False

    # ----- чтение/запись ----------------------------------------------------
    def db_value(self, key: str) -> Optional[str]:
        """Значение из БД (``None`` — переопределения нет)."""
        with self._lock:
            if not self._cache_loaded:
                self._load_cache_locked()
            return self._cache.get(key)

    def get(self, key: str, default: str = "") -> str:
        """Эффективное значение: БД → переменная окружения → дефолт."""
        val = self.db_value(key)
        if val is not None:
            return val
        env = os.getenv(key)
        if env is not None and env != "":
            return env
        return default

    def set(self, key: str, value: str) -> bool:
        """Сохранить переопределение в БД и обновить кэш в RAM."""
        if key not in MANAGED_SETTINGS:
            return False
        value = "" if value is None else str(value)
        with self._lock:
            assert self._db is not None
            self._db.execute(
                f"INSERT INTO {self.TABLE}(key,value,updated_ts) VALUES(?,?,strftime('%s','now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_ts=excluded.updated_ts",
                (key, value))
            self._db.commit()
            self._cache[key] = value
        return True

    def delete(self, key: str) -> None:
        """Убрать переопределение: снова действует окружение/дефолт."""
        with self._lock:
            assert self._db is not None
            self._db.execute(f"DELETE FROM {self.TABLE} WHERE key=?", (key,))
            self._db.commit()
            self._cache.pop(key, None)

    def overrides(self) -> Dict[str, str]:
        """Все сохранённые в БД переопределения (управляемые ключи)."""
        with self._lock:
            if not self._cache_loaded:
                self._load_cache_locked()
            return dict(self._cache)

    # ----- спецификация ------------------------------------------------------
    @staticmethod
    def is_secret(key: str) -> bool:
        return key in SECRET_KEYS

    @staticmethod
    def meta(key: str) -> Dict[str, Any]:
        return MANAGED_SETTINGS.get(key, {})

    @staticmethod
    def keys(section: str = "") -> list:
        if not section:
            return sorted(MANAGED_SETTINGS)
        return sorted(k for k, m in MANAGED_SETTINGS.items()
                      if m.get("section") == section)

    def admin_view(self) -> Dict[str, Dict[str, Any]]:
        """Срез всех настроек для ``GET /api/admin/settings``.

        Секреты возвращаются строкой ``***`` (если заданы) — полный пароль или
        токен в браузер не уходит никогда.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for key, m in MANAGED_SETTINGS.items():
            db_val = self.db_value(key)
            source = "db" if db_val is not None else (
                "env" if (os.getenv(key) or "") else "")
            raw = db_val if db_val is not None else (os.getenv(key) or "")
            secret = bool(m.get("secret"))
            out[key] = {
                "value": mask_secret(raw) if secret else raw,
                "source": source,
                "secret": secret,
                "restart": bool(m.get("restart")),
                "section": m.get("section", ""),
            }
        return out

    # ----- производные конфигурации ------------------------------------------
    def smtp_config(self) -> Dict[str, Any]:
        """Эффективная конфигурация отправки писем (та же логика, что в
        ``mailer.build_mailer``, но читается динамически из БД/окружения)."""
        from mailer import BRAND  # локально: не тащим тяжёлый импорт на старт
        mail_dir = self.get("LIQSCOPE_MAIL_DIR")
        api_kind = self.get("LIQSCOPE_MAIL_API")
        host = self.get("LIQSCOPE_SMTP_HOST")
        ssl_flag = _flag_value(self.get("LIQSCOPE_SMTP_SSL"))
        try:
            timeout = float(self.get("LIQSCOPE_SMTP_TIMEOUT", "15") or 15)
        except (TypeError, ValueError):
            timeout = 15.0
        cfg: Dict[str, Any] = {"timeout": timeout}
        if mail_dir:
            cfg.update({
                "kind": "file",
                "mail_dir": mail_dir,
                "sender": self.get("LIQSCOPE_SMTP_FROM",
                                   f"{BRAND} <no-reply@liqscope.online>"),
            })
            return cfg
        if api_kind:
            cfg.update({
                "kind": "api" if self.get("LIQSCOPE_MAIL_API_KEY") else "off",
                "api_kind": api_kind,
                "api_key": self.get("LIQSCOPE_MAIL_API_KEY"),
                "api_secret": self.get("LIQSCOPE_MAIL_API_SECRET"),
                "api_url": self.get("LIQSCOPE_MAIL_API_URL"),
                "sender": self.get("LIQSCOPE_SMTP_FROM")
                          or self.get("LIQSCOPE_MAIL_API_FROM")
                          or f"{BRAND} <no-reply@liqscope.online>",
            })
            return cfg
        if not host:
            cfg.update({"kind": "off",
                        "sender": self.get("LIQSCOPE_SMTP_FROM",
                                           f"{BRAND} <no-reply@liqscope.online>")})
            return cfg
        user = self.get("LIQSCOPE_SMTP_USER")
        try:
            port = int(self.get("LIQSCOPE_SMTP_PORT",
                                "465" if ssl_flag else "587") or 587)
        except (TypeError, ValueError):
            port = 587
        cfg.update({
            "kind": "smtp",
            "host": host,
            "port": port,
            "user": user,
            "password": self.get("LIQSCOPE_SMTP_PASSWORD"),
            "tls": self.get("LIQSCOPE_SMTP_TLS",
                            "ssl" if ssl_flag else "starttls"),
            # Яндекс/Mail.ru/Gmail отклоняют письмо, если отправитель не
            # совпадает с логином — без явного FROM шлём с адреса логина.
            "sender": self.get("LIQSCOPE_SMTP_FROM")
                      or (f"{BRAND} <{user}>" if user
                          else f"{BRAND} <no-reply@{host}>"),
            "ipv4": _flag_value(self.get("LIQSCOPE_SMTP_IPV4")),
        })
        return cfg

    # ----- применение на лету --------------------------------------------------
    def apply_mailer(self, mailer_obj, public_url: str = "") -> bool:
        """Подогнать ``mailer.Mailer`` под текущие настройки.

        Вызывается перед каждой отправкой (``web_account._mailer``) и после
        сохранения настроек из админки. Возвращает ``True``, если транспорт
        пересобран. Совпадение конфигурации — дешёвое сравнение кортежей.
        """
        if mailer_obj is None:
            return False
        from mailer import ApiTransport, FileTransport, SmtpTransport
        cfg = self.smtp_config()
        kind = cfg["kind"]
        tr = getattr(mailer_obj, "transport", None)
        sig = self._transport_sig(tr)
        if kind == "file":
            want = ("file", cfg["mail_dir"])
            if sig == want:
                return False
            mailer_obj.transport = FileTransport(cfg["mail_dir"])
        elif kind == "api":
            want = ("api", cfg["api_kind"], cfg["api_key"], cfg["api_secret"],
                    cfg["api_url"], cfg["sender"], round(cfg["timeout"], 3),
                    (public_url or "https://liqscope.online"))
            if sig == want:
                return False
            mailer_obj.transport = ApiTransport(
                kind=cfg["api_kind"], key=cfg["api_key"],
                secret=cfg["api_secret"], sender=cfg["sender"],
                url=cfg["api_url"], timeout=cfg["timeout"],
                site=public_url or "https://liqscope.online")
        elif kind == "smtp":
            want = ("smtp", cfg["host"], cfg["port"], cfg["user"],
                    cfg["password"], cfg["sender"], cfg["tls"],
                    round(cfg["timeout"], 3), cfg["ipv4"])
            if sig == want:
                return False
            mailer_obj.transport = SmtpTransport(
                host=cfg["host"], port=cfg["port"], user=cfg["user"],
                password=cfg["password"], sender=cfg["sender"],
                tls=cfg["tls"], timeout=cfg["timeout"], ipv4=cfg["ipv4"])
        else:  # off
            if sig == ("off",):
                if getattr(mailer_obj, "enabled", False):
                    mailer_obj.enabled = False
                return False
            mailer_obj.transport = None
        mailer_obj.sender = cfg.get("sender", getattr(mailer_obj, "sender", ""))
        mailer_obj.enabled = mailer_obj.transport is not None
        if kind == "off":
            mailer_obj.enabled = False
        log.info("почта: транспорт пересобран из настроек (%s)", kind)
        return True

    @staticmethod
    def _transport_sig(tr) -> tuple:
        from mailer import ApiTransport, FileTransport, SmtpTransport
        if tr is None:
            return ("off",)
        if isinstance(tr, FileTransport):
            return ("file", tr.folder)
        if isinstance(tr, ApiTransport):
            return ("api", tr.kind, tr.key, tr.secret, tr.url, tr.sender,
                    round(float(getattr(tr, "timeout", 15.0) or 15.0), 3),
                    getattr(tr, "site", ""))
        if isinstance(tr, SmtpTransport):
            return ("smtp", tr.host, tr.port, tr.user, tr.password, tr.sender,
                    tr.tls, round(float(getattr(tr, "timeout", 15.0) or 15.0), 3),
                    bool(tr.ipv4))
        return ("unknown", repr(type(tr)))

    def apply_admin_access(self, store) -> bool:
        """Обновить списки админов ``Store`` из настроек (без рестарта).

        Возвращает ``True``, если списки изменились. Главный администратор
        по-прежнему задаётся этими списками — так же, как раньше через
        переменные окружения.
        """
        if store is None:
            return False
        try:
            from accounts import normalize_email
        except Exception:  # noqa: BLE001 — тесты без полного окружения
            normalize_email = lambda s: (s or "").strip().lower()  # noqa: E731
        ids = set()
        for part in self.get("LIQSCOPE_ADMIN_IDS").replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        emails = {normalize_email(x) for x in
                  self.get("LIQSCOPE_ADMIN_EMAILS").replace(";", ",").split(",")
                  if normalize_email(x)}
        changed = (set(getattr(store, "admin_ids", set())) != ids or
                   set(getattr(store, "admin_emails", set())) != emails)
        if changed:
            store.admin_ids = ids
            store.admin_emails = emails
            log.info("доступы: списки админов обновлены из настроек "
                     "(id=%d, email=%d)", len(ids), len(emails))
        return changed


_default_manager: Optional[SettingsManager] = None
_default_lock = threading.Lock()


def default_manager() -> SettingsManager:
    """Общий менеджер процесса (лениво; путь базы — как у аккаунтов)."""
    global _default_manager
    with _default_lock:
        if _default_manager is None:
            _default_manager = SettingsManager()
        return _default_manager
