"""Менеджер системных настроек («Системные настройки» в админке).

Вся конфигурация сервиса исторически задавалась переменными окружения в
systemd. Чтобы владелец мог менять её на лету из админки без `systemctl edit`
и перезапуска, значения читаются так:

    таблица ``system_settings`` в accounts.db  →  переменная окружения  →  дефолт

Модуль самодостаточен: держит собственное SQLite-подключение (та же база,
что у ``accounts.Store`` — WAL позволяет несколько читателей/писателей),
кэш значений в RAM и реестр управляемых ключей (человекочитаемое имя,
подсказка, секретность, нужен ли рестарт).

Динамическое применение без перезапуска:
* почта: ``apply_mailer`` пересобирает транспорт перед каждой отправкой
  (хук ``Mailer.config_sync`` и ``web_account._mailer``);
* ИИ: ``apply_ai`` обновляет писателя перед каждой генерацией
  (хук ``AiWriter.config_sync``; ключи/модели читаются через
  ``ai_text._env`` → источник из БД);
* доступы: ``apply_admin_access`` обновляет списки ``Store`` сразу;
* ключи, читаемые один раз при старте процесса, помечены ``restart=True`` —
  админка честно показывает предупреждение о перезапуске.
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

#: Вкладки админки (группы настроек).
SECTION_MAIL = "mail"          # ✉️ Почта и рассылки (SMTP, HTTPS-API)
SECTION_AI = "ai"              # 🤖 ИИ и нейросети (ключи, модели, порядок)
SECTION_SECURITY = "security"  # 🛡️ Безопасность и доступы (админы)
SECTION_TELEGRAM = "telegram"  # 💬 Telegram (бот и каналы)
SECTION_TUNING = "tuning"      # ⚙️ Тюнинг и производительность

#: Разделы в порядке показа в админке.
SECTION_ORDER = (SECTION_MAIL, SECTION_AI, SECTION_SECURITY,
                 SECTION_TELEGRAM, SECTION_TUNING)

#: Суффиксы секретных ключей: маскируются даже без явного флага.
_SECRET_SUFFIXES = ("_KEY", "_KEYS", "_SECRET", "_TOKEN", "_PASSWORD")


def _spec(section: str, label: str, hint: str = "", secret: bool = False,
          restart: bool = False, placeholder: str = "") -> Dict[str, Any]:
    return {"section": section, "label": label, "hint": hint,
            "secret": secret, "restart": restart, "placeholder": placeholder}


#: Управляемые ключи. ``secret`` — маскировать в ответах и не писать значение
#: в аудит; ``restart`` — читается один раз при старте процесса, изменение
#: подхватится только после перезапуска сервиса.
MANAGED_SETTINGS: Dict[str, Dict[str, Any]] = {
    # ── ✉️ Почта и рассылки: SMTP -------------------------------------------
    "LIQSCOPE_SMTP_HOST": _spec(
        SECTION_MAIL, "SMTP-сервер",
        "Адрес сервера исходящей почты. Пример: smtp.yandex.ru. "
        "Пусто — письма не отправляются.",
        placeholder="smtp.yandex.ru"),
    "LIQSCOPE_SMTP_PORT": _spec(
        SECTION_MAIL, "SMTP-порт",
        "Обычно 465 для SSL или 587 для STARTTLS.", placeholder="465"),
    "LIQSCOPE_SMTP_TLS": _spec(
        SECTION_MAIL, "Шифрование",
        "ssl — отдельный защищённый порт (465); starttls — апгрейд "
        "обычного соединения (587); none — без шифрования (не рекомендуется)."),
    "LIQSCOPE_SMTP_USER": _spec(
        SECTION_MAIL, "SMTP-логин",
        "Имя пользователя для входа на SMTP-сервер, обычно адрес почты.",
        placeholder="no-reply@liqscope.online"),
    "LIQSCOPE_SMTP_PASSWORD": _spec(
        SECTION_MAIL, "SMTP-пароль",
        "Пароль приложения (не пароль от почты!). Яндекс и Gmail выдают "
        "его отдельно в настройках аккаунта.", secret=True),
    "LIQSCOPE_SMTP_FROM": _spec(
        SECTION_MAIL, "Отправитель",
        "Формат: «Имя <адрес>». Если пусто — письмо уйдёт с адреса логина: "
        "Яндекс, Mail.ru и Gmail требуют совпадения.",
        placeholder="LiqScope <no-reply@liqscope.online>"),
    "LIQSCOPE_SMTP_TIMEOUT": _spec(
        SECTION_MAIL, "Таймаут SMTP, сек", "Сколько ждать ответа сервера. По умолчанию 15."),
    "LIQSCOPE_SMTP_IPV4": _spec(
        SECTION_MAIL, "Только IPv4",
        "1 — не пробовать IPv6. Включайте, если хостинг не выпускает "
        "письма по IPv6 («Network is unreachable»)."),
    "LIQSCOPE_SMTP_SSL": _spec(
        SECTION_MAIL, "Флаг «порт 465 = SSL»",
        "1 — порт и шифрование по умолчанию берутся для SSL-подключения. "
        "Обычно не нужен, если заданы порт и шифрование явно."),
    # ── ✉️ Почта и рассылки: сервисы рассылок (HTTPS-API) -------------------
    "LIQSCOPE_MAIL_API": _spec(
        SECTION_MAIL, "Сервис рассылок (вместо SMTP)",
        "resend, sendpulse или generic — письма через HTTPS (порт 443), "
        "когда хостинг блокирует SMTP-порты. Пусто — используется SMTP."),
    "LIQSCOPE_MAIL_API_KEY": _spec(
        SECTION_MAIL, "Ключ сервиса рассылок",
        "Для Resend это ключ вида re_…. У SendPulse — client_id.",
        secret=True, placeholder="re_…"),
    "LIQSCOPE_MAIL_API_SECRET": _spec(
        SECTION_MAIL, "Секрет сервиса рассылок",
        "Нужен только для SendPulse (client_secret).", secret=True),
    "LIQSCOPE_MAIL_API_URL": _spec(
        SECTION_MAIL, "URL API рассылок",
        "Пусто — стандартный адрес выбранного сервиса. Для generic — "
        "обязательно: сюда шлётся JSON с Bearer-ключом."),
    "LIQSCOPE_MAIL_API_FROM": _spec(
        SECTION_MAIL, "Отправитель сервиса рассылок",
        "Запасной адрес отправителя для рассылок, если общий не задан."),
    "LIQSCOPE_MAIL_DIR": _spec(
        SECTION_MAIL, "Папка-стенд для писем",
        "Письма складываются в файлы вместо отправки — только для стенда "
        "и отладки. Главнее всех остальных способов отправки!"),
    # ── 🤖 ИИ и нейросети -----------------------------------------------------
    "LIQSCOPE_AI_GEMINI_KEY": _spec(
        SECTION_AI, "Ключ Gemini (Google AI Studio)",
        "Бесплатный тариф на aistudio.google.com. Основной генератор "
        "шапок постов и дайджестов.", secret=True),
    "LIQSCOPE_AI_GEMINI_KEYS": _spec(
        SECTION_AI, "Ключи Gemini (несколько)",
        "Список через запятую: когда у ключа кончился лимит, запрос "
        "повторяется со следующим.", secret=True),
    "LIQSCOPE_AI_GEMINI_MODEL": _spec(
        SECTION_AI, "Модель Gemini",
        "Пусто — берём актуальную сами. Пример: gemini-3.5-flash-lite."),
    "LIQSCOPE_AI_GEMINI_URL": _spec(
        SECTION_AI, "URL API Gemini",
        "Пусто — стандартный адрес. Меняйте только для своего прокси."),
    "LIQSCOPE_AI_GROQ_KEY": _spec(
        SECTION_AI, "Ключ Groq",
        "Бесплатный тариф на console.groq.com — запасной генератор.",
        secret=True),
    "LIQSCOPE_AI_GROQ_KEYS": _spec(
        SECTION_AI, "Ключи Groq (несколько)",
        "Список через запятую — перебираются при исчерпании лимита.",
        secret=True),
    "LIQSCOPE_AI_GROQ_MODEL": _spec(
        SECTION_AI, "Модель Groq",
        "Модель должна быть включена в настройках проекта Groq. "
        "Пример: qwen/qwen3.8-27b."),
    "LIQSCOPE_AI_GROQ_URL": _spec(
        SECTION_AI, "URL API Groq", "Пусто — стандартный адрес."),
    "LIQSCOPE_AI_OPENROUTER_KEY": _spec(
        SECTION_AI, "Ключ OpenRouter",
        "Третий запасной сервис; есть модели с суффиксом :free.",
        secret=True),
    "LIQSCOPE_AI_OPENROUTER_KEYS": _spec(
        SECTION_AI, "Ключи OpenRouter (несколько)",
        "Список через запятую — перебираются при исчерпании лимита.",
        secret=True),
    "LIQSCOPE_AI_OPENROUTER_MODEL": _spec(
        SECTION_AI, "Модель OpenRouter",
        "Пример: openrouter/free или google/gemini-2.5-flash."),
    "LIQSCOPE_AI_OPENROUTER_URL": _spec(
        SECTION_AI, "URL API OpenRouter", "Пусто — стандартный адрес."),
    "LIQSCOPE_AI_DEEPSEEK_KEY": _spec(
        SECTION_AI, "Ключ DeepSeek",
        "Платный, но очень дешёвый запасной сервис.", secret=True),
    "LIQSCOPE_AI_DEEPSEEK_KEYS": _spec(
        SECTION_AI, "Ключи DeepSeek (несколько)",
        "Список через запятую — перебираются при исчерпании лимита.",
        secret=True),
    "LIQSCOPE_AI_DEEPSEEK_MODEL": _spec(
        SECTION_AI, "Модель DeepSeek", "Пусто — deepseek-chat."),
    "LIQSCOPE_AI_DEEPSEEK_URL": _spec(
        SECTION_AI, "URL API DeepSeek", "Пусто — стандартный адрес."),
    "LIQSCOPE_AI_KEY": _spec(
        SECTION_AI, "Ключ своего OpenAI-совместимого API",
        "Любой сервис с OpenAI-совместимым интерфейсом: задайте ему URL "
        "и модель ниже.", secret=True),
    "LIQSCOPE_AI_KEYS": _spec(
        SECTION_AI, "Ключи своего API (несколько)",
        "Список через запятую — перебираются при исчерпании лимита.",
        secret=True),
    "LIQSCOPE_AI_URL": _spec(
        SECTION_AI, "URL своего OpenAI-совместимого API",
        "Пример: https://api.my-llm.example/v1/chat/completions"),
    "LIQSCOPE_AI_MODEL": _spec(
        SECTION_AI, "Модель своего API",
        "Имя модели у своего сервиса. Также служит общей моделью по "
        "умолчанию, если у сервиса своя не задана. Пример: gpt-4o-mini."),
    "LIQSCOPE_AI_ORDER": _spec(
        SECTION_AI, "Порядок сервисов",
        "Кого спрашивать первым, через запятую: "
        "gemini,groq,openrouter,deepseek,custom.",
        placeholder="gemini,groq,openrouter,deepseek"),
    "LIQSCOPE_AI_TIMEOUT": _spec(
        SECTION_AI, "Таймаут запроса к ИИ, сек",
        "Сколько ждать ответа модели. По умолчанию 12."),
    "LIQSCOPE_AI_MAX_TOKENS": _spec(
        SECTION_AI, "Предел ответа модели, токенов",
        "Длина генерации: 220 хватает для шапки поста с цифрами."),
    "LIQSCOPE_AI_TEMPERATURE": _spec(
        SECTION_AI, "Температура генерации",
        "0.0 — строго, 1.0 — творчески. По умолчанию 0.9."),
    "LIQSCOPE_AI_DISABLED": _spec(
        SECTION_AI, "Выключить ИИ совсем",
        "1 — шапки постов и дайджесты всегда из шаблонов, запросов к "
        "сервисам нет."),
    # ── 🛡️ Безопасность и доступы ---------------------------------------------
    "LIQSCOPE_ADMIN_EMAILS": _spec(
        SECTION_SECURITY, "Email админов",
        "Через запятую. Главный администратор задаётся этим списком: его "
        "нельзя забанить или снять. Применяется сразу.",
        placeholder="owner@liqscope.online"),
    "LIQSCOPE_ADMIN_IDS": _spec(
        SECTION_SECURITY, "Telegram ID админов",
        "Числовые id через запятую (узнать: @userinfobot). Применяется сразу.",
        placeholder="123456789"),
    # ── 💬 Telegram -----------------------------------------------------------
    "LIQSCOPE_BOT_TOKEN": _spec(
        SECTION_TELEGRAM, "Токен бота",
        "Выдаёт @BotFather. Опрос бота запускается при старте процесса, "
        "поэтому новый токен подхватится после перезапуска.",
        secret=True, restart=True),
    "LIQSCOPE_CHANNEL_ID": _spec(
        SECTION_TELEGRAM, "ID основного канала",
        "Числовой id канала для сводок (обычно -100…). Меняется также из "
        "панели бота; после смены через настройки нужен перезапуск.",
        restart=True),
    "LIQSCOPE_CHANNEL_URL": _spec(
        SECTION_TELEGRAM, "Ссылка основного канала",
        "Приглашение вида https://t.me/+…. Показывается в боте.",
        restart=True),
    "LIQSCOPE_CHANNEL2_ID": _spec(
        SECTION_TELEGRAM, "ID второго (английского) канала",
        "Куда дублируются английские сводки.", restart=True),
    "LIQSCOPE_CHANNEL2_URL": _spec(
        SECTION_TELEGRAM, "Ссылка второго канала",
        "Приглашение вида https://t.me/+….", restart=True),
    # ── ⚙️ Тюнинг: уровни ликвидаций ------------------------------------------
    "LIQSCOPE_LEVELS_BATCH": _spec(
        SECTION_TUNING, "Размер пачки расчёта уровней",
        "Сколько монет считать за один проход фона. Рекомендуется 2–4: "
        "меньше — меньше задержек сервера, больше — быстрее обновляются "
        "уровни.", restart=True),
    "LIQSCOPE_LEVELS_AGG_ROWS": _spec(
        SECTION_TUNING, "Схлопывание строк лестницы",
        "1 (по умолчанию) — соседние строки уровней сливаются перед "
        "отрисовкой: расчёт дешевле кратно.", restart=True),
    "LIQSCOPE_LEVELS_AGG_BUCKET": _spec(
        SECTION_TUNING, "Шаг склейки уровней, %",
        "Насколько близкие ценовые строки считать одной. По умолчанию 0.5.",
        restart=True),
    "LIQSCOPE_LEVELS_TTL": _spec(
        SECTION_TUNING, "TTL расчёта уровней, сек",
        "Как часто пересчитывать лестницу. По умолчанию 20.", restart=True),
    "LIQSCOPE_LEVELS_PRICE_TTL": _spec(
        SECTION_TUNING, "TTL кэша цен уровней, сек",
        "По умолчанию 300.", restart=True),
    "LIQSCOPE_LEVELS_EVENTS_TTL_SEC": _spec(
        SECTION_TUNING, "TTL кэша событий уровней, сек",
        "Сколько держать прочитанные дневные шарды ликвидаций. Больше — "
        "меньше чтений диска, но старый уровень может оставаться невычтенным "
        "до конца окна. По умолчанию 600, минимум 5.", restart=True),
    "LIQSCOPE_LEVELS_EVENTS_CACHE_MAX": _spec(
        SECTION_TUNING, "Монет в кэше событий уровней",
        "Потолок памяти: прежние 64 записи давали кучу больше гигабайта. "
        "По умолчанию 24.", restart=True),
    "LIQSCOPE_LEVELS_EVENTS_CACHE_EVENTS": _spec(
        SECTION_TUNING, "Событий в кэше уровней (всего)",
        "Общий бюджет событий по всем монетам кэша. По умолчанию 100000.",
        restart=True),
    "LIQSCOPE_LEVELS_CALIB_BUDGET": _spec(
        SECTION_TUNING, "Бюджет калибровки уровней",
        "Сколько секунд CPU за проход отдавать калибровке. По умолчанию 2.",
        restart=True),
    "LIQSCOPE_LEVELS_CALIB_BUDGET_SEC": _spec(
        SECTION_TUNING, "Окно бюджета калибровки, сек",
        "По умолчанию 30.", restart=True),
    # ── ⚙️ Тюнинг: кэш и фоновые задачи ----------------------------------------
    "LIQSCOPE_SNAP_CACHE_SEC": _spec(
        SECTION_TUNING, "TTL снимка рынка, сек",
        "Кэш агрегатов для шапки и топа монет. По умолчанию 12.",
        restart=True),
    "LIQSCOPE_ALERTS_SNAP_TTL_SEC": _spec(
        SECTION_TUNING, "TTL снимка алертов, сек",
        "По умолчанию 1.", restart=True),
    "LIQSCOPE_FLOW_SNAP_TTL_SEC": _spec(
        SECTION_TUNING, "TTL снимка потоков (CVD/OI), сек",
        "По умолчанию 2.", restart=True),
    "LIQSCOPE_WS_INIT_LIQ": _spec(
        SECTION_TUNING, "Ликвидаций в первом кадре /ws",
        "Сколько недавних событий отдавать при подключении терминала "
        "(20–200). Применяется без перезапуска."),
    "LIQSCOPE_HISTORY_TTL_HOURS": _spec(
        SECTION_TUNING, "Глубина истории ликвидаций, часов",
        "По умолчанию месяц. Старшее — вытесняется.", restart=True),
    "LIQSCOPE_HISTORY_MAX": _spec(
        SECTION_TUNING, "Событий истории в памяти",
        "Потолок ленты в оперативной памяти. По умолчанию 60000.",
        restart=True),
    "LIQSCOPE_POST_INTERVAL_H": _spec(
        SECTION_TUNING, "Интервал постов в канал, часов",
        "Частота сводок (1–24). Обычно меняется из панели бота — там "
        "применяется сразу; здесь это стартовое значение.", restart=True),
    "LIQSCOPE_DIGEST_HOUR": _spec(
        SECTION_TUNING, "Час вечернего дайджеста (МСК)",
        "По умолчанию 22.", restart=True),
    "LIQSCOPE_DIGEST_MIN": _spec(
        SECTION_TUNING, "Минута вечернего дайджеста", "По умолчанию 0.",
        restart=True),
    "LIQSCOPE_DIGEST_JITTER_MIN": _spec(
        SECTION_TUNING, "Разброс публикации дайджеста, мин",
        "Случайная задержка после назначенного времени. По умолчанию 10.",
        restart=True),
    "LIQSCOPE_DIGEST_SCHED": _spec(
        SECTION_TUNING, "Автопубликация дайджеста",
        "0 — выпуск не публикуется сам, только вручную.", restart=True),
    "LIQSCOPE_OXA_POLL_SEC": _spec(
        SECTION_TUNING, "Интервал опроса 0xArchive, сек",
        "Ликвидации Hyperliquid через индексатор. По умолчанию 120.",
        restart=True),
    "LIQSCOPE_WHALE_POLL_INTERVAL_SEC": _spec(
        SECTION_TUNING, "Интервал опроса китов, сек",
        "Фоновый проход скринера китов (60/120/300). Применяется без "
        "перезапуска: читается на каждый цикл."),
    # ── ⚙️ Тюнинг: сборка мусора, журнал, сжатие --------------------------------
    "LIQSCOPE_GC_LOG_MS": _spec(
        SECTION_TUNING, "Порог лога сборки мусора, мс",
        "С какой длительности писать о сборке в журнал. По умолчанию 200.",
        restart=True),
    "LIQSCOPE_GC_FREEZE": _spec(
        SECTION_TUNING, "Заморозка кучи старта",
        "1 (по умолчанию) — объекты старта не участвуют в сборках: "
        "паузы короче.", restart=True),
    "LIQSCOPE_LOOP_LAG_MS": _spec(
        SECTION_TUNING, "Порог сторожа пауз, мс",
        "Пауза event loop длиннее порога пишется в журнал. По умолчанию 500.",
        restart=True),
    "LIQSCOPE_LOG_ASYNC": _spec(
        SECTION_TUNING, "Асинхронный журнал",
        "1 (по умолчанию) — запись логов через очередь из отдельного "
        "потока: воркер не блокируется на journald.", restart=True),
    "LIQSCOPE_LOG_QUEUE": _spec(
        SECTION_TUNING, "Глубина очереди журнала",
        "Переполнение роняет запись, а не воркер. По умолчанию 20000.",
        restart=True),
    "LIQSCOPE_GZIP_THREAD_MIN_SIZE": _spec(
        SECTION_TUNING, "Порог сжатия в потоке, байт",
        "Ответы больше этого размера сжимаются в отдельном потоке (64 КБ "
        "по умолчанию): zlib отпускает GIL и не держит воркер.",
        restart=True),
}

#: Ключи с секретами — маскируются в ответах и формах.
SECRET_KEYS = frozenset(
    k for k, m in MANAGED_SETTINGS.items()
    if m.get("secret") or k.endswith(_SECRET_SUFFIXES))

#: Ключи ИИ-блока: по ним считается сигнатура для динамической пересборки.
AI_KEYS = tuple(sorted(k for k in MANAGED_SETTINGS
                       if MANAGED_SETTINGS[k]["section"] == SECTION_AI))


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
    """Секрет для ответа админке: начало и хвост узнаваемы, середина скрыта.

    Длинное значение: первые 3 символа + ``***`` + последние 4
    (например, ``AIz***a1b2``). Короткое — просто ``***``. Не задано — пусто.
    """
    value = value or ""
    if not value:
        return ""
    if len(value) <= 10:
        return MASK
    return value[:3] + MASK + value[-4:]


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
        # Значения читаются точечным SELECT по первичному ключу (микросекунды,
        # WAL-чтение не блокирует писателей). Общий кэш в памяти не держим:
        # с одной базой могут работать несколько менеджеров (сервер,
        # whale_poller, тесты), и чтение из БД гарантирует, что все они видят
        # сохранённое админкой сразу.
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

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:  # noqa: BLE001 — закрытие не должно падать
                    pass
                self._db = None

    # ----- чтение/запись ----------------------------------------------------
    def db_value(self, key: str) -> Optional[str]:
        """Значение из БД (``None`` — переопределения нет).

        Именно эту функцию ``server.py`` передаёт в ``ai_text`` источником
        настроек: БД главнее окружения. Читается всегда из базы — это дёшево
        (ключ — первичный) и гарантирует согласованность между несколькими
        менеджерами одной базы.
        """
        with self._lock:
            assert self._db is not None
            row = self._db.execute(
                f"SELECT value FROM {self.TABLE} WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

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
        return True

    def delete(self, key: str) -> None:
        """Убрать переопределение: снова действует окружение/дефолт."""
        with self._lock:
            assert self._db is not None
            self._db.execute(f"DELETE FROM {self.TABLE} WHERE key=?", (key,))
            self._db.commit()

    def overrides(self) -> Dict[str, str]:
        """Все сохранённые в БД переопределения (управляемые ключи)."""
        with self._lock:
            assert self._db is not None
            rows = self._db.execute(
                f"SELECT key, value FROM {self.TABLE}").fetchall()
        return {r["key"]: r["value"] for r in rows
                if r["key"] in MANAGED_SETTINGS}

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

        Секреты возвращаются маскированными (``AIz***a1b2``) — полное
        значение в браузер не уходит никогда. Для каждого ключа отдаются
        человекочитаемое имя и подсказка — фронтенд рисует формы по ним.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for key, m in MANAGED_SETTINGS.items():
            db_val = self.db_value(key)
            source = "db" if db_val is not None else (
                "env" if (os.getenv(key) or "") else "")
            raw = db_val if db_val is not None else (os.getenv(key) or "")
            secret = bool(key in SECRET_KEYS)
            out[key] = {
                "value": mask_secret(raw) if secret else raw,
                "source": source,
                "secret": secret,
                "restart": bool(m.get("restart")),
                "section": m.get("section", ""),
                "label": m.get("label", key),
                "hint": m.get("hint", ""),
                "placeholder": m.get("placeholder", ""),
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

    def ai_signature(self) -> tuple:
        """Отпечаток всех эффективных ИИ-настроек: по нему понятно, нужно ли
        пересобирать писателя. Дешёвый: значения уже в RAM-кэше."""
        return tuple(self.get(k) for k in AI_KEYS)

    # ----- применение на лету --------------------------------------------------
    def apply_ai(self, writer):
        """Подогнать ``ai_text.AiWriter`` под текущие настройки.

        Возвращает писателя (тот же объект с обновлёнными провайдерами,
        нового или ``None``, если ключей больше нет). Совпадение
        конфигурации — сравнение сигнатуры, поэтому на горячем пути
        (каждая генерация) это просто сравнение кортежа строк.
        """
        import ai_text
        sig = self.ai_signature()
        if writer is not None and getattr(writer, "_liq_settings_sig", None) == sig:
            return writer
        if writer is None:
            new = ai_text.build_ai()
            if new is not None:
                new._liq_settings_sig = sig
                log.info("ИИ: писатель собран из настроек (%d провайдеров)",
                         len(new.providers))
            return new
        # обновление на месте: ссылки на объект остаются живыми
        writer.providers = ai_text.build_providers()
        try:
            writer.timeout = float(ai_text._env("LIQSCOPE_AI_TIMEOUT", "12") or 12)
            writer.max_tokens = int(ai_text._env("LIQSCOPE_AI_MAX_TOKENS", "220") or 220)
            writer.temperature = float(ai_text._env("LIQSCOPE_AI_TEMPERATURE", "0.9") or 0.9)
        except (TypeError, ValueError):
            pass
        writer.state = {
            p.name: {"name": p.name, "model": p.model, "ok": False,
                     "reason": "не пробовали", "dead": False, "ms": 0,
                     "keys": len(p.keys) or 1, "key_index": p.key_index + 1,
                     "keys_used": 0}
            for p in writer.providers}
        writer.resolved = {}
        writer._models = {}
        writer._rejected = {}
        writer._liq_settings_sig = sig
        log.info("ИИ: настройки применены на лету (%d провайдеров)",
                 len(writer.providers))
        return writer

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
