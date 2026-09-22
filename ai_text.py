"""Шапка поста от ИИ: Gemini → Groq → OpenRouter (и любой OpenAI-совместимый).

Сводку для канала бот собирает сам (channel_digest) — здесь только текст шапки.
Из снимка делается короткая выжимка (ликвидации, стороны, топ-монеты, биржи,
OI/CVD), она уходит модели, а ответ проверяется: никаких цифр, markdown и
обещаний. Если все сервисы молчат — шапка берётся из обычных шаблонов, пост
публикуется как раньше: сводка не должна теряться из-за ИИ.

Настройки из окружения (все необязательные):

    LIQSCOPE_AI_GEMINI_KEY       ключ Google AI Studio (есть бесплатный тариф)
    LIQSCOPE_AI_GROQ_KEY         ключ Groq (есть бесплатный тариф)
    LIQSCOPE_AI_OPENROUTER_KEY   ключ OpenRouter (модели с суффиксом :free)
    LIQSCOPE_AI_DEEPSEEK_KEY     ключ DeepSeek (API платный, но очень дешёвый)
    LIQSCOPE_AI_KEY, LIQSCOPE_AI_URL, LIQSCOPE_AI_MODEL
                                 свой OpenAI-совместимый сервис (или локальный)
    LIQSCOPE_AI_GEMINI_KEYS, LIQSCOPE_AI_GROQ_KEYS, LIQSCOPE_AI_OPENROUTER_KEYS,
    LIQSCOPE_AI_DEEPSEEK_KEYS, LIQSCOPE_AI_KEYS
                                 НЕСКОЛЬКО ключей на сервис (через запятую или
                                 точку с запятой). Когда у ключа кончился лимит
                                 (429, quota, rate limit), сервис отвечает
                                 отказом — работа переходит на следующий ключ,
                                 а потом на следующий сервис. Так лимит одного
                                 ключа не останавливает шапки и дайджесты
    LIQSCOPE_AI_ORDER            порядок сервисов, по умолчанию
                                 gemini,groq,openrouter,deepseek,custom
    LIQSCOPE_AI_<СЕРВИС>_MODEL   своя модель у сервиса (по умолчанию берём
                                 актуальную: если заданная устарела, сервис
                                 ответит 404 — тогда модель подбирается сама)
    LIQSCOPE_AI_TIMEOUT          таймаут запроса, сек (12)
    LIQSCOPE_AI_MAX_TOKENS       предел ответа модели (220 — шапка с цифрами
                                 длиннее, а reasoning-модели тратят часть
                                 лимита на размышления)
    LIQSCOPE_AI_DISABLED=1       выключить генерацию совсем

Без ключей модуль выключен: посты уходят с шапками из шаблонов, как раньше.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from channel_digest import HEAD_MAX_LEN, money

log = logging.getLogger("liqscope.ai")

# Cloudflare (edge Groq, OpenRouter) отвечает 403 «error code: 1010» на
# стандартный User-Agent python-urllib ещё до проверки ключа — поэтому
# представляемся своим именем и просим JSON.
USER_AGENT = "LiqScope/1.0 (+https://liqscope.online)"
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru,en;q=0.9",
}

MIN_LEN = 18
LONG = "слишком длинный"
DEFAULT_ORDER = ("gemini", "groq", "openrouter", "deepseek", "custom")

SYSTEM_PROMPT = (
    "Ты — редактор Telegram-канала о криптофьючерсах. Пишешь шапку к сводке "
    "ликвидаций: спокойный деловой язык, живо, но без сленга, жаргона, "
    "восклицаний и обращения к читателю. Пиши по-русски, 2–3 предложения "
    "(до 200 знаков).\n"
    "Обязательно опирайся на данные сводки: назови хотя бы одну конкретную "
    "монету и приведи 1–2 точных числа из них (сумму, перевес сторон или "
    "процент). Не пересказывай все цифры подряд — выбери самое важное.\n"
    "Не выдумывай факты и монеты, которых нет в данных, не давай советов и "
    "прогнозов, не пиши хештеги, markdown, кавычки, эмодзи и подписи. "
    "Не начинай с тех же слов, что прошлые шапки. Верни только текст шапки."
)

# Направления (акценты) — чередуются от поста к посту, чтобы формулировки
# не повторялись: цифры одни и те же, а угол зрения каждый раз новый.
SYSTEM_PROMPT_EN = (
    "You are the editor of a crypto-futures Telegram channel. You write the "
    "headline for a liquidation recap: calm business English, lively but "
    "without slang, hype or addressing the reader. Write in English, 2-3 "
    "sentences (up to 200 characters).\n"
    "Always lean on the data: name at least one concrete coin and quote 1-2 "
    "exact numbers from it (total, side skew or percentage). Do not list every "
    "figure — pick what matters.\n"
    "Do not invent facts or coins that are not in the data, give no advice or "
    "forecasts, no hashtags, markdown, quotes, emojis or signatures. Do not "
    "start with the same words as previous headlines. Return only the headline."
)

BODY_MIN_LEN = 320
BODY_MAX_LEN = 2600

# Рассказ для дневного дайджеста: живой язык, но без выдумок и воды.
BODY_SYSTEM_PROMPT = (
    "Ты — аналитик криптофьючерсного терминала LiqScope. Пишешь вечерний "
    "дайджест за сутки живым человеческим языком: так, будто весь день "
    "смотрел ленту ликвидаций и теперь рассказываешь, что произошло.\n"
    "Спокойный деловой тон, можно лёгкую иронию, но без сленга, кликбейта, "
    "восклицаний и обращения к читателю. Опирайся ТОЛЬКО на факты из запроса, "
    "ничего не выдумывай, не давай советов и прогнозов.\n"
    "Три–пять абзацев, каждый абзац 2–4 предложения, между абзацами пустая "
    "строка. Начни с самого крупного события дня, дальше биржи, монеты с "
    "самым тяжёлым открытым интересом относительно оборота, изменение цен и "
    "общее настроение рынка. Не пересказывай цифры списком — связывай их в "
    "живые фразы, объясняй, что за ними стоит.\n"
    "Верни только текст дайджеста: без заголовков, markdown, списков, "
    "хештегов, ссылок и подписи."
)
BODY_SYSTEM_PROMPT_EN = (
    "You are an analyst at the LiqScope crypto futures terminal, writing the "
    "evening daily digest in a live human voice: as if you watched the "
    "liquidation tape all day and now tell the story.\n"
    "Calm business English, mild irony is fine, but no slang, clickbait, "
    "exclamation marks or addressing the reader. Use ONLY the facts from the "
    "request, invent nothing, give no advice or forecasts.\n"
    "Three to five paragraphs, each 2-4 sentences, blank line between "
    "paragraphs. Start with the biggest event of the day, then exchanges, then "
    "the coins with the heaviest open interest relative to turnover, then "
    "price changes and the overall mood. Do not list numbers mechanically — "
    "weave them into live sentences and explain what they mean.\n"
    "Return only the digest text: no headings, markdown, lists, hashtags, "
    "links or signatures."
)


# ---------------------------------------------------------------------------
#  Промты из админки сайта
# ---------------------------------------------------------------------------
# Владелец канала может переписать инструкцию модели под себя: один промт для
# шапки поста, другой — для дневного дайджеста (и для каждого — русский и
# английский вариант, потому что посты уходят в два канала). В базе они лежат
# настройками ai_prompt_head_ru / ai_prompt_digest_en и т.д.; пока настройки
# нет, работает встроенный шаблон ниже — админ видит его в форме как образец.
_PROMPT_SOURCE = None       # callable(kind, lang) -> str, подключает server.py


def set_prompt_source(fn) -> None:
    """Подключить читалку админских промтов (настройки сайта)."""
    global _PROMPT_SOURCE
    _PROMPT_SOURCE = fn


def prompt_setting(kind: str, lang: str = "ru") -> str:
    """Имя настройки промта: ai_prompt_head_ru, ai_prompt_digest_en."""
    kind = "digest" if str(kind or "").strip().lower() == "digest" else "head"
    code = "en" if str(lang or "").startswith("en") else "ru"
    return f"ai_prompt_{kind}_{code}"


def default_prompt(kind: str, lang: str = "ru") -> str:
    """Встроенный промт: он же показывается в админке как шаблон."""
    en = str(lang or "").startswith("en")
    if str(kind or "").strip().lower() == "digest":
        return BODY_SYSTEM_PROMPT_EN if en else BODY_SYSTEM_PROMPT
    return SYSTEM_PROMPT_EN if en else SYSTEM_PROMPT


def custom_prompt(kind: str, lang: str = "ru") -> str:
    """Промт из админки; пусто — значит, админ его не переписывал."""
    if _PROMPT_SOURCE is None:
        return ""
    try:
        return str(_PROMPT_SOURCE(kind, lang) or "").strip()
    except Exception as e:                       # noqa: BLE001
        log.debug("промт %s/%s: %s", kind, lang, e)
        return ""


def active_prompt(kind: str, lang: str = "ru") -> str:
    """Промт, который реально уходит модели: админский или встроенный."""
    return custom_prompt(kind, lang) or default_prompt(kind, lang)


def clean_body(raw: str) -> str:
    """Чистим рассказ: без markdown, ссылок и лишних пустых строк."""
    t = (raw or "").strip()
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#~\[\]{}|]+", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip().strip('"‘’“”')


def fit_body(text: str, limit: int = BODY_MAX_LEN) -> str:
    """Обрезаем рассказ по границе предложения — длинные ответы моделей режем."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "! ", "? "):
        i = cut.rfind(sep)
        if i > limit * 0.6:
            return cut[:i + 1].strip()
    i = cut.rfind(" ")
    return (cut[:i] if i > 0 else cut).strip()


def body_problem(text: str, lang: str = "ru") -> str:
    """Почему рассказ брать нельзя ("" — можно)."""
    if not text:
        return "пусто"
    if len(text) < BODY_MIN_LEN:
        return "слишком коротко"
    low = text.lower()
    for bad in ("не могу", "извин", "как ии", "языковая модель", "нейросет",
                "не является инвестиционн", "as an ai", "i cannot", "i'm sorry",
                "language model"):
        if bad in low:
            return f"отказ ({bad})"
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    share = cyr / max(1, len(text))
    if str(lang).startswith("en"):
        if share > 0.05:
            return "ответ не на английском"
    elif share < 0.30:
        return "ответ не на русском"
    return ""


ANGLES_EN = (
    "Start with the big picture of the window and the headline number.",
    "Focus on the skew: which side got wiped and by how much (numbers required).",
    "Focus on the leading coin: name it and its total.",
    "Focus on exchanges: where it hit hardest — with numbers.",
    "Focus on the single largest liquidation.",
    "Focus on open interest and volume delta (CVD) if the data has them.",
    "One dense sentence with the two most telling numbers — keep it short.",
    "Focus on scale: is this big for the window or did the market get off lightly.",
    "Start with the leading coin, then the window total.",
    "Focus on the long/short ratio in per cent.",
)

# Подписи фактов для промпта: русский — основной канал, английский — второй.
FACT_LABELS = {
    "ru": {"window": "Окно сводки: последние {h} часа", "total": "Всего ликвидаций",
           "longs": "лонги", "shorts": "шорты", "events": "событий",
           "mood": "Характер окна", "leaders": "Лидеры", "exchanges": "Биржи",
           "biggest": "Крупнейшее событие", "oi": "Открытый интерес",
           "cvd": "Дельта объёма (CVD)", "hour": "за час", "on": "на"},
    "en": {"window": "Recap window: last {h} hours", "total": "Total liquidations",
           "longs": "longs", "shorts": "shorts", "events": "events",
           "mood": "Tone of the window", "leaders": "Leaders", "exchanges": "Exchanges",
           "biggest": "Largest event", "oi": "Open interest",
           "cvd": "Volume delta (CVD)", "hour": "over the hour", "on": "on"},
}


def _flabel(lang: str, key: str, **kw) -> str:
    table = FACT_LABELS.get("en" if str(lang).startswith("en") else "ru") or {}
    text = table.get(key) or FACT_LABELS["ru"].get(key) or key
    return text.format(**kw) if kw else text


def _mood_word(longs: float, shorts: float, lang: str = "ru") -> str:
    if str(lang).startswith("en"):
        if longs + shorts <= 0:
            return "almost silent"
        if longs > shorts * 1.25:
            return "longs were cut"
        if shorts > longs * 1.25:
            return "shorts were wrecked"
        return "both sides got hit"
    return _side_word(longs, shorts)


ANGLES = (
    "Начни с общей картины окна и главной суммы.",
    "Акцент на перекосе: кого снимали и с каким отрывом (числа обязательны).",
    "Акцент на монете-лидере: назови её и её сумму.",
    "Акцент на биржах: где сработало сильнее всего — с числами.",
    "Акцент на самой крупной единичной ликвидации.",
    "Акцент на открытом интересе и дельте объёма (CVD), если данные есть.",
    "Одна плотная фраза с двумя самыми говорящими числами — кратко.",
    "Акцент на масштабе: крупно это для окна или рынок отделался лёгким.",
    "Начни с монеты-лидера, затем общая сумма окна.",
    "Акцент на соотношении лонгов и шортов в процентах.",
)


# ---------------------------------------------------------------------------
#  Выжимка из снимка: только то, что нужно модели
# ---------------------------------------------------------------------------
def _pct(v: Any) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return ""


def _side_word(longs: float, shorts: float) -> str:
    if longs > shorts * 1.25:
        return "снимали в основном лонги"
    if shorts > longs * 1.25:
        return "снимали в основном шорты"
    return "били с обеих сторон"


def summarize(snap: dict, lang: str = "ru") -> List[str]:
    """Строки-факты для промпта (без HTML и цифр-обманок)."""
    s = snap or {}
    L = (lambda key, **kw: _flabel(lang, key, **kw))
    try:
        h = int(s.get("window_h") or 4)
    except (TypeError, ValueError):
        h = 4
    longs = float(s.get("longs_usd") or 0)
    shorts = float(s.get("shorts_usd") or 0)
    out = [
        L("window", h=h),
        f"{L('total')}: {money(s.get('total_usd'))} "
        f"({L('longs')} {money(longs)}, {L('shorts')} {money(shorts)}), "
        f"{L('events')}: {int(s.get('count') or 0)}",
        f"{L('mood')}: {_mood_word(longs, shorts, lang)}",
    ]
    coins = (s.get("top_coins") or [])[:3]
    if coins:
        parts = []
        for c in coins:
            parts.append(f"{c.get('symbol') or '?'} {money(c.get('usd'))} "
                         f"({L('longs')} {money(c.get('longs'))}, "
                         f"{L('shorts')} {money(c.get('shorts'))})")
        out.append(L("leaders") + ": " + "; ".join(parts))
    ex = list((s.get("exchanges") or {}).items())[:3]
    if ex:
        out.append(L("exchanges") + ": " + ", ".join(f"{k} {money(v)}" for k, v in ex))
    b = s.get("biggest") or {}
    if b:
        out.append(f"{L('biggest')}: {b.get('symbol') or '?'} "
                   f"{money(b.get('usd'))} {L('on')} {b.get('exchange') or '?'}")
    oi = s.get("oi") or {}
    oi_bits = []
    for sym, p in list(oi.items())[:3]:
        ch = (p or {}).get("changes") or {}
        pct = _pct((ch.get("h1") or {}).get("pct")) if isinstance(ch.get("h1"), dict) else ""
        if pct:
            oi_bits.append(f"{sym} {pct} {L('hour')}")
    if oi_bits:
        out.append(L("oi") + ": " + ", ".join(oi_bits))
    cvd = s.get("cvd") or {}
    cvd_bits = [f"{sym} {money(v)}" for sym, v in list(cvd.items())[:3]]
    if cvd_bits:
        out.append(L("cvd") + ": " + ", ".join(cvd_bits))
    return out


def build_prompt(snap: dict, recent: Optional[List[str]] = None,
                 variant: int = 0, lang: str = "ru") -> str:
    en = str(lang).startswith("en")
    angles = ANGLES_EN if en else ANGLES
    lines = [
        ("Recap data - use it in the text (take numbers and coins from here):"
         if en else
         "Данные сводки — используй их в тексте (числа и монеты брать отсюда):"),
        *[f"— {x}" for x in summarize(snap, lang)],
    ]
    keep = [r for r in (recent or []) if r][-4:]
    if keep:
        lines.append("")
        lines.append("Previous headlines - do not repeat their wording or openings:"
                     if en else
                     "Прошлые шапки — не повторяй их формулировки и не начинай так же:")
        lines += [f"— {r}" for r in keep]
    lines.append("")
    lines.append(("Angle for this post: " if en else "Акцент этого поста: ")
                 + str(angles[int(variant) % len(angles)]))
    lines.append("Write the headline: 2-3 sentences, one concrete coin and 1-2 numbers."
                 if en else
                 "Напиши шапку: 2–3 предложения, с конкретной монетой и 1–2 числами.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Сервисы
# ---------------------------------------------------------------------------
@dataclass
class Provider:
    name: str
    kind: str                    # "gemini" | "openai"
    key: str                     # текущий ключ (может меняться при лимите)
    url: str
    model: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    models_url: str = ""                 # где спросить список моделей
    explicit_model: bool = False         # модель задана админом вручную
    keys: List[str] = field(default_factory=list)   # все ключи сервиса по порядку
    key_index: int = 0                   # на каком ключе работаем сейчас

    def public(self) -> Dict[str, str]:
        return {"name": self.name, "model": self.model,
                "keys": str(len(self.keys) or 1),
                "key_index": str(self.key_index + 1)}

    def next_key(self) -> Optional[str]:
        """Следующий ключ сервиса: лимит на текущем — берём неиспробованный.

        Возвращает ключ или None, если ключи кончились (тогда вызывающий идёт
        к другому сервису). Счётчик ключа крутится по кольцу: если ключи были
        исчерпаны, но лимиты обновились, сервис снова оживает.
        """
        if len(self.keys) < 2:
            return None
        self.key_index = (self.key_index + 1) % len(self.keys)
        self.key = self.keys[self.key_index]
        return self.key


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _split_keys(raw: str) -> List[str]:
    """«ключ1, ключ2 ; ключ3» → список ключей без пустых."""
    parts = re.split(r"[,;\s]+", raw or "")
    return [p for p in (x.strip() for x in parts) if p]


def keys_for(name: str, key_env: str) -> List[str]:
    """Ключи сервиса по порядку: LIQSCOPE_AI_<СЕРВИС>_KEYS, затем по одному.

    Несколько ключей нужны, когда у одного кончился лимит: запрос повторяется
    со следующим ключом того же сервиса, а потом уже идёт следующий сервис.
    Дубли (один и тот же ключ в списке и в старом имени) не повторяем.
    """
    out = _split_keys(_env(f"LIQSCOPE_AI_{name.upper()}_KEYS"))
    single = _env(key_env)
    if single:
        out.append(single)
    seen: set = set()
    uniq: List[str] = []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


#: Лимит одного ключа исчерпан: сервис отвечает 429/quota, но ключ рабочий.
#: Такой провайдер не «мёртв» — просто на этом ключе работа кончилась.
def quota_error(err: str) -> bool:
    text = str(err or "")
    return bool(re.search(
        r"HTTP (429|402)\b|rate[_ ]?limit|quota|resource[_ ]?exhausted|"
        r"insufficient[_ ]?(quota|balance|credits)|too many requests|"
        r"limit exceeded|exceeded your current quota",
        text, re.I))


def auth_error(err: str) -> bool:
    """Ключ не принят вовсе — его дальше пробовать бессмысленно."""
    text = str(err or "")
    if quota_error(text) or model_error(text):
        return False
    return bool(re.search(r"HTTP (401|403)\b|invalid[_ ]?api[_ ]?key|"
                          r"unauthorized|api[_ ]?key[_ ]?not[_ ]?valid", text, re.I))


def _provider(name: str, default_model: str, key_env: str,
              model_env: str) -> Optional[Provider]:
    keys = keys_for(name, key_env)
    if not keys:
        return None
    key = keys[0]
    explicit = bool(_env(model_env) or _env("LIQSCOPE_AI_MODEL"))
    model = _env(model_env) or _env("LIQSCOPE_AI_MODEL") or default_model
    if name == "gemini":
        url = _env("LIQSCOPE_AI_GEMINI_URL",
                   "https://generativelanguage.googleapis.com/v1beta/models")
        return Provider(name=name, kind="gemini", key=key, url=url, model=model,
                        models_url=url, explicit_model=explicit, keys=keys)
    urls = {
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "deepseek": "https://api.deepseek.com/chat/completions",
    }
    headers = {}
    if name == "openrouter":
        headers = {"HTTP-Referer": "https://liqscope.online", "X-Title": "LiqScope"}
    url = _env(f"LIQSCOPE_AI_{name.upper()}_URL", urls.get(name, ""))
    return Provider(name=name, kind="openai", key=key, url=url, model=model,
                    models_url=url.rsplit("/chat/completions", 1)[0] + "/models",
                    headers=headers, explicit_model=explicit, keys=keys)


def custom_provider() -> Optional[Provider]:
    """Любой OpenAI-совместимый сервис: LIQSCOPE_AI_URL + LIQSCOPE_AI_KEY.

    Ключей тоже может быть несколько: LIQSCOPE_AI_KEYS (или LIQSCOPE_AI_KEY).
    """
    url = _env("LIQSCOPE_AI_URL")
    keys = _split_keys(_env("LIQSCOPE_AI_KEYS")) or _split_keys(_env("LIQSCOPE_AI_KEY"))
    if not (url and keys):
        return None
    return Provider(name="custom", kind="openai", key=keys[0], url=url,
                    model=_env("LIQSCOPE_AI_MODEL") or "gpt-4o-mini",
                    explicit_model=bool(_env("LIQSCOPE_AI_MODEL")), keys=keys)


def build_providers() -> List[Provider]:
    """Сервисы в нужном порядке: только те, для которых есть ключ."""
    if _env("LIQSCOPE_AI_DISABLED") in ("1", "true", "on", "yes"):
        return []
    found: Dict[str, Provider] = {}
    for name, default_model in (("gemini", "gemini-3.5-flash-lite"),
                                ("groq", "qwen/qwen3.8-27b"),
                                ("openrouter", "openrouter/free"),
                                ("deepseek", "deepseek-chat")):
        p = _provider(name, default_model, f"LIQSCOPE_AI_{name.upper()}_KEY",
                      f"LIQSCOPE_AI_{name.upper()}_MODEL")
        if p is not None:
            found[name] = p
    c = custom_provider()
    if c is not None:
        found["custom"] = c
    order = [x.strip().lower() for x in
             _env("LIQSCOPE_AI_ORDER", ",".join(DEFAULT_ORDER)).split(",") if x.strip()]
    chain = [found[n] for n in order if n in found]
    chain += [p for n, p in found.items() if p not in chain]   # прочие в конце
    return chain


# ---------------------------------------------------------------------------
#  HTTP (одна функция — её же используют тесты и tools/ai_check.py)
# ---------------------------------------------------------------------------
def post_json(url: str, payload: dict, headers: Dict[str, str],
              timeout: float = 12.0) -> dict:
    """POST с JSON-телом. Ошибки сервиса — исключением с понятным текстом."""
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={**BASE_HEADERS, "Content-Type": "application/json",
                                **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    except Exception as e:                       # сеть, DNS, TLS, таймаут
        raise RuntimeError(f"{type(e).__name__}: {e}") from None
    try:
        return json.loads(body) if body.strip() else {}
    except ValueError:
        raise RuntimeError(f"не JSON в ответе: {body[:120]!r}") from None


def get_json(url: str, headers: Dict[str, str], timeout: float = 12.0) -> dict:
    """GET JSON (список моделей у сервиса)."""
    req = urllib.request.Request(url, headers={**BASE_HEADERS, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}") from None
    try:
        return json.loads(body) if body.strip() else {}
    except ValueError:
        raise RuntimeError(f"не JSON в ответе: {body[:120]!r}") from None


# Модели не для генерации текста: распознавание речи, синтез, классификаторы
# («prompt-guard», «safeguard»), картинки. Их в перебор не берём.
MODEL_WORDS_BAD = ("embedding", "embed", "image", "tts", "audio", "live",
                   "transcribe", "translate", "veo", "imagen", "whisper",
                   "guard", "moderation", "rerank", "speech", "orpheus",
                   "parakeet", "playai")


# У Groq модели включаются в настройках проекта, поэтому порядок предпочтения
# задан под тот набор, что там обычно доступен: сначала многоязычные модели,
# которые хорошо пишут по-русски, в конце — маленькие и специализированные.
GROQ_PREFER = ("qwen3.8-27b", "qwen3.8", "qwen3", "qwen",
               "gpt-oss-120b", "gpt-oss-20b",
               "llama-3.3-70b", "llama-3.1-8b", "llama-4",
               "compound-mini", "compound", "mistral", "gemma", "allam")


def rank_models(kind: str, ids: List[str]) -> List[str]:
    """Модели по убыванию предпочтения: для короткого русского текста.

    Возвращаем список, а не одну: у Groq модели бывают «заблокированы в
    проекте», поэтому код пробует следующую, а не сдаётся.
    """
    good = [i for i in ids if i and not any(w in i.lower() for w in MODEL_WORDS_BAD)]
    if not good:
        return []
    if kind == "gemini":
        def ver(x: str) -> float:
            m = re.search(r"gemini-(\d+)(?:\.(\d+))?", x)
            return float(f"{m.group(1)}.{m.group(2) or 0}") if m else 0.0

        return sorted(good, key=lambda x: (0 if "flash-lite" in x else 1, -ver(x)))
    if kind == "openrouter":
        # только бесплатные: цепочка собрана из сервисов с бесплатным тарифом,
        # платные модели у OpenRouter упрутся в отсутствие баланса
        return sorted([i for i in good if i.endswith(":free")], key=len, reverse=True)
    if kind == "groq":
        out: List[str] = []
        for want in GROQ_PREFER:
            for i in sorted(good):
                if want in i.lower() and i not in out:
                    out.append(i)
        return out + [i for i in sorted(good) if i not in out]
    return sorted(good)


def pick_model(kind: str, ids: List[str]) -> str:
    """Самая подходящая модель (первая из ранжированного списка)."""
    ranked = rank_models(kind, ids)
    return ranked[0] if ranked else ""


def list_models(provider: Provider, timeout: float = 12.0) -> List[str]:
    """Спрашиваем у сервиса список моделей (для авто-подбора)."""
    if not provider.models_url:
        return []
    try:
        if provider.kind == "gemini":
            data = get_json(f"{provider.models_url}?key={provider.key}",
                            provider.headers, timeout)
            out = []
            for m in data.get("models") or []:
                name = str(m.get("name") or "").split("/")[-1]
                methods = m.get("supportedGenerationMethods") or []
                if name and (not methods or "generateContent" in methods):
                    out.append(name)
            return out
        data = get_json(provider.models_url,
                        {"Authorization": f"Bearer {provider.key}", **provider.headers},
                        timeout)
        return [str(m.get("id") or "") for m in (data.get("data") or [])]
    except Exception as e:
        log.info("ИИ (%s): список моделей не получен: %s", provider.name, e)
        return []


def suggested_model(err: str, current: str = "") -> str:
    """Google в 404 сам пишет замену: «use models/gemini-3.5-flash-lite».

    В тексте ошибки сначала идёт *старое* имя («…models/gemini-2.5-flash-lite
    is no longer available… use models/gemini-3.5-flash-lite»), поэтому берём
    имя после слов use/update/replace, а не первое совпадение.
    """
    text = err or ""
    m = re.search(r"(?:use|update to|replace with|switch to|move to)\s+"
                  r"(?:the\s+)?model[s]?/([a-z0-9.\-]+)", text, re.I)
    if m and m.group(1) != current:
        return m.group(1)
    for name in re.findall(r"models/([a-z0-9.\-]+)", text, re.I):
        if name != current:
            return name
    return ""


def model_error(err: str) -> bool:
    """Модель недоступна: устарела, переименована или выключена в проекте.

    Это не про ключ: у Groq модель можно отключить в настройках проекта
    («blocked at the project level»), и тогда её надо просто заменить.
    """
    low = (err or "").lower()
    if "blocked at the project" in low or "is blocked" in low:
        return True
    return ("model" in low and any(x in low for x in (
        "no longer available", "not found", "decommissioned", "does not exist",
        "unknown model", "unsupported model", "invalid model")))


def ai_error_hint(err: str) -> str:
    """Человеческая расшифровка ответа сервиса."""
    low = (err or "").lower()
    if "1010" in low:
        return ("Cloudflare сервиса не пускает запрос (error code: 1010) — "
                "обычно из-за User-Agent; обновите код до версии с "
                "LiqScope/1.0 в заголовках")
    if "429" in low or "rate limit" in low or "quota" in low:
        return "лимит бесплатного тарифа — подождите или проверьте консоль сервиса"
    if "blocked at the project" in low or "is blocked" in low:
        return ("модель выключена в настройках проекта сервиса — включите её "
                "(у Groq: console.groq.com/settings/project) или задайте другую "
                "через LIQSCOPE_AI_<СЕРВИС>_MODEL")
    if "401" in low or "invalid api key" in low:
        return "ключ не подошёл — проверьте LIQSCOPE_AI_<СЕРВИС>_KEY"
    if "403" in low:
        return "сервис не пускает запрос (ключ или регион)"
    if model_error(err):
        return "сервис переименовал модель — обновите LIQSCOPE_AI_<СЕРВИС>_MODEL"
    return ""


def parse_reply(provider: Provider, data: dict) -> str:
    """Достаём текст ответа у Google и у OpenAI-совместимых сервисов."""
    if not isinstance(data, dict):
        raise RuntimeError("пустой ответ")
    if provider.kind == "gemini":
        cands = data.get("candidates") or []
        if not cands:
            fb = (data.get("promptFeedback") or {}).get("blockReason")
            raise RuntimeError(f"нет ответа модели{' (' + str(fb) + ')' if fb else ''}")
        parts = ((cands[0].get("content") or {}).get("parts")) or []
        text = " ".join(str(p.get("text") or "") for p in parts)
    else:
        choices = data.get("choices") or []
        if not choices:
            err = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else ""
            raise RuntimeError(f"нет ответа модели{f' ({err})' if err else ''}")
        msg = choices[0].get("message") or {}
        text = str(msg.get("content") or "")
    if not text.strip():
        raise RuntimeError("модель прислала пустой текст")
    return text


def request_for(provider: Provider, prompt: str, system: str,
                temperature: float, max_tokens: int) -> Tuple[str, dict, Dict[str, str]]:
    """(url, тело, заголовки) — своё у gemini, общий формат у остальных."""
    if provider.kind == "gemini":
        url = f"{provider.url.rstrip('/')}/{provider.model}:generateContent?key={provider.key}"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_tokens},
        }
        return url, body, dict(provider.headers)
    body = {
        "model": provider.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    return provider.url, body, {"Authorization": f"Bearer {provider.key}",
                                **provider.headers}


# ---------------------------------------------------------------------------
#  Проверка и чистка ответа
# ---------------------------------------------------------------------------
COMMON_NAMES = {
    "BTC": ("биткоин", "bitcoin", "btc"),
    "ETH": ("эфириум", "эфир", "ethereum", "ether", "eth"),
    "SOL": ("солана", "solana", "sol"),
    "XRP": ("xrp", "рипл"),
    "BNB": ("bnb", "бинанс коин"),
    "DOGE": ("догикоин", "dogecoin", "doge"),
    "TON": ("toncoin", "тон", "ton"),
    "ADA": ("cardano", "кардано", "ada"),
    "AVAX": ("avalanche", "avax"),
    "LINK": ("chainlink", "link"),
    "LTC": ("litecoin", "лайткоин", "ltc"),
    "SUI": ("sui", "суи"),
}


def coin_words(snap: dict) -> set:
    """Как могут называться монеты из сводки (тикер или слово)."""
    syms = set()
    for row in (snap.get("top_coins") or []):
        if row.get("symbol"):
            syms.add(str(row["symbol"]).upper())
    big = (snap.get("biggest") or {}).get("symbol")
    if big:
        syms.add(str(big).upper())
    for src in (snap.get("oi") or {}, snap.get("cvd") or {}):
        syms.update(str(k).upper() for k in src)
    out = set()
    for sym in syms:
        base = sym.split("_")[0].split("/")[0]
        out.add(base.lower())
        out.update(COMMON_NAMES.get(base, ()))
    return out


def missing_specifics(head: str, snap: dict) -> str:
    """Требуем конкретику: монету или число — иначе шапка «водяная»."""
    low = (head or "").lower()
    has_coin = any(w and w in low for w in coin_words(snap))
    has_fig = bool(re.search(r"\d", head or ""))
    if has_fig:
        return ""
    if has_coin:
        return "нет ни одного числа — добавьте 1–2 цифры из данных"
    return "нет ни монеты, ни числа — шапка без конкретики"


def _words(text: str) -> set:
    return set(re.findall(r"[а-яёa-z]{5,}", (text or "").lower()))


def too_similar(head: str, recent: Optional[List[str]], limit: float = 0.5) -> bool:
    """Похоже на прошлые шапки? (доля общих значимых слов)"""
    w = _words(head)
    if not w:
        return False
    for old in (recent or [])[-4:]:
        ow = _words(old)
        if not ow:
            continue
        common = w & ow
        # одного общего слова мало («сняли», «лонги») — нужны два и высокая доля
        if len(common) >= 2 and len(common) / max(1, min(len(w), len(ow))) >= limit:
            return True
    return False


def clean_head(raw: str) -> str:
    """Убираем markdown, кавычки и лишние пробелы."""
    t = (raw or "").strip()
    t = re.sub(r"<[^>]+>", " ", t)                     # html, если модель расстаралась
    t = re.sub(r"[*_`#~\[\]{}|]+", "", t)
    t = re.sub(r"^[\s>•\-–—]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip('"\'«»„“”')


def head_problem(text: str) -> str:
    """Почему шапку брать нельзя ("" — можно)."""
    if not text:
        return "пусто"
    if len(text) < MIN_LEN:
        return "слишком коротко"
    low = text.lower()
    for bad in ("не могу", "извин", "как ии", "языковая модель", "нейросет",
                "инвестиционн", "не является"):
        if bad in low:
            return f"не шапка, а отказ ({bad})"
    if "http" in low or "t.me" in low:
        return "ссылка в шапке"
    if "#" in text:
        return "хештег в шапке"
    if len(text) > HEAD_MAX_LEN * 2:
        return LONG                                   # длинно: режем через fit_head
    return ""


def fit_head(text: str) -> str:
    """Обрезаем до того же предела, что и шаблоны (HEAD_MAX_LEN)."""
    if len(text) <= HEAD_MAX_LEN:
        return text
    cut = text[:HEAD_MAX_LEN].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut or text[:HEAD_MAX_LEN]


# ---------------------------------------------------------------------------
#  Основной класс
# ---------------------------------------------------------------------------
class AiWriter:
    """Пишет шапку поста, перебирая сервисы по порядку."""

    def __init__(self, providers: List[Provider], timeout: float = 12.0,
                 max_tokens: int = 220, temperature: float = 0.9):
        self.providers = list(providers)
        self.timeout = float(timeout or 12)
        self.max_tokens = int(max_tokens or 120)
        self.temperature = float(temperature)
        self.state: Dict[str, Dict[str, Any]] = {
            p.name: {"name": p.name, "model": p.model, "ok": False,
                     "reason": "не пробовали", "dead": False, "ms": 0,
                     "keys": len(p.keys) or 1, "key_index": p.key_index + 1,
                     "keys_used": 0}
            for p in self.providers}
        self.last: Dict[str, Any] = {"provider": "", "ok": False, "reason": "",
                                     "ms": 0, "ts": 0.0}
        self.calls = 0
        self.fails = 0
        self.resolved: Dict[str, str] = {}    # сервис -> модель, которую подобрали
        self._models: Dict[str, List[str]] = {}     # кэш списка моделей сервиса
        self._rejected: Dict[str, set] = {}         # модели, которые не подошли
        self._extra: Dict[str, int] = {}            # добавка к лимиту ответа
        # Полный текст последней удачной шапки — до обрезки fit_head.
        # В канал уходит короткая версия (HEAD_MAX_LEN), а на сайт — весь
        # рассказ целиком: сайт не ограничен подписью Telegram.
        self.last_full: str = ""

    # --- состояние для админки ---
    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def status(self) -> Dict[str, Any]:
        # В состояние добавляем счётчики ключей: сколько их у сервиса и на
        # каком работаем — это видно в админке.
        for p in self.providers:
            st = self.state.setdefault(p.name, {})
            st["keys"] = len(p.keys) or 1
            st["key_index"] = p.key_index + 1
        return {
            "enabled": self.enabled,
            "providers": [dict(v) for v in self.state.values()],
            "last": dict(self.last),
            "calls": self.calls,
            "fails": self.fails,
        }

    # --- сама генерация ---
    def _model_list(self, p: Provider) -> List[str]:
        """Список моделей сервиса (кэшируем: спрашиваем один раз за запуск)."""
        if p.name not in self._models:
            ids = list_models(p, self.timeout) if p.models_url else []
            # выбор модели зависит от сервиса, а kind у OpenAI-совместимых один,
            # поэтому ранжируем по имени сервиса
            self._models[p.name] = rank_models(p.name, ids)
        return self._models[p.name]

    def _switch_model(self, p: Provider, tried: set) -> Optional[str]:
        """Следующая модель сервиса. None — больше нечего пробовать."""
        bad = self._rejected.setdefault(p.name, set())
        for mid in self._model_list(p):
            if mid and mid not in bad and mid not in tried and mid != p.model:
                bad.add(p.model) if p.model else None
                log.warning("ИИ (%s): модель %s не подошла — пробую %s",
                            p.name, p.model, mid)
                p.model = mid
                self.state[p.name]["model"] = mid
                self.resolved[p.name] = mid
                return mid
        return None

    def _repair_model(self, p: Provider) -> bool:
        """Модель устарела — спрашиваем у сервиса актуальную и пробуем снова."""
        return self._switch_model(p, set()) is not None

    def _switch_key(self, p: Provider, tried: set) -> Optional[str]:
        """Следующий ключ сервиса, когда на текущем кончился лимит.

        Перебираем все ключи по кольцу (начиная со следующего), но каждый — не
        больше одного раза за запрос: ключ, который только что ответил «лимит»,
        второй раз спрашивать бессмысленно. Возвращаем рабочий ключ или None.
        """
        st = self.state.setdefault(p.name, {})
        tried.add(p.key)                 # текущий ключ уже ответил «лимит»
        remaining = [k for k in p.keys if k not in tried]
        if not remaining:
            return None
        chosen = remaining[0]
        p.key = chosen
        p.key_index = p.keys.index(chosen)
        tried.add(chosen)
        st["key_index"] = p.key_index + 1
        st["keys_used"] = min(len(p.keys), int(st.get("keys_used", 0)) + 1)
        log.warning("ИИ (%s): лимит ключа #%d — перехожу на ключ #%d из %d",
                    p.name, len(tried), p.key_index + 1, len(p.keys))
        return chosen

    def _attempt(self, p: Provider, prompt: str, lang: str = "ru",
                 tokens: Optional[int] = None,
                 system: Optional[str] = None) -> Optional[str]:
        """Один запрос к сервису (с учётом добавки к лимиту ответа).

        ``system`` — инструкция модели: у шапки и у дневного дайджеста они
        разные, и обе можно переписать в админке (см. ``active_prompt``).
        """
        if tokens is None:
            tokens = self.max_tokens + self._extra.get(p.name, 0)
        if system is None:
            system = active_prompt("head", lang)
        url, body, headers = request_for(p, prompt, system,
                                         self.temperature, tokens)
        data = post_json(url, body, headers, self.timeout)
        return parse_reply(p, data)

    def headline_sync(self, snap: dict, recent: Optional[List[str]] = None,
                      variant: int = 0, lang: str = "ru") -> Optional[str]:
        """Первый удачный ответ или None (тогда шапка будет из шаблонов)."""
        self.last_full = ""     # полный текст прошлого поста не должен протечь
        if not self.providers:
            return None
        angle = int(variant)
        for p in self.providers:
            st = self.state[p.name]
            if st.get("dead"):
                continue
            started = time.time()
            tried: set = set()
            tried_keys: set = set()
            repeats = 0        # сколько раз переспрашивали из-за повтора
            try:
                while True:
                    try:
                        prompt = build_prompt(snap, recent, angle, lang)
                        raw = self._attempt(p, prompt, lang)
                        head = clean_head(raw)
                        problem = head_problem(head)
                        if problem and problem != LONG:
                            raise RuntimeError(f"ответ не годится: {problem}")
                        generic = missing_specifics(head, snap)
                        if generic:
                            raise RuntimeError(f"ответ не годится: {generic}")
                        if too_similar(head, recent) and repeats < 3:
                            # тот же смысл, но другой заход: меняем акцент,
                            # а не сервис — цифры те же, подача новая
                            repeats += 1
                            angle += 1
                            log.info("ИИ (%s): шапка похожа на прошлую — меняю акцент",
                                     p.name)
                            continue
                        head_full_text = head    # до обрезки: весь текст для сайта
                        head = fit_head(head)
                        if not head:
                            raise RuntimeError("ответ не годится: пусто после чистки")
                        full = head_full_text        # полный ответ — для сайта
                        break
                    except Exception as e:
                        # Google шлёт в 404 готовую замену — пробуем её, а если её
                        # нет, спрашиваем список моделей у сервиса и берём другую:
                        # у Groq модель может быть выключена в настройках проекта.
                        if model_error(str(e)):
                            hint = suggested_model(str(e), p.model)
                            if hint and hint not in tried:
                                log.warning("ИИ (%s): модель %s недоступна — беру %s "
                                            "из ответа", p.name, p.model, hint)
                                tried.add(p.model)
                                p.model = hint
                                st["model"] = hint
                                self.resolved[p.name] = hint
                                continue
                            nxt = self._switch_model(p, tried)
                            if nxt:
                                tried.add(nxt)
                                continue
                            st["dead"] = True    # ни одной рабочей модели у сервиса
                            raise
                        if "пустой текст" in str(e):
                            # reasoning-модели тратят лимит на размышления: повторяем
                            # тот же запрос с запасом по длине ответа
                            base = self.max_tokens + self._extra.get(p.name, 0)
                            if base < 400:
                                self._extra[p.name] = max(base * 4, 400) - self.max_tokens
                                log.info("ИИ (%s): пустой ответ — повтор с лимитом %s",
                                         p.name, self.max_tokens + self._extra[p.name])
                                continue
                        if quota_error(str(e)) and self._switch_key(p, tried_keys):
                            # Лимит одного ключа — не повод терять сервис:
                            # тот же запрос уходит со следующего ключа.
                            continue
                        raise
                st.update({"ok": True, "reason": "",
                           "ms": int((time.time() - started) * 1000)})
                self.calls += 1
                self.last = {"provider": p.name, "ok": True, "reason": "",
                             "ms": st["ms"], "ts": time.time()}
                # полный текст (без обрезки) — его берёт сайт; в TG уйдёт head
                self.last_full = full
                log.info("ИИ-шапка: %s (%s) за %s мс", p.name, p.model, st["ms"])
                return head
            except Exception as e:
                hint = ai_error_hint(str(e))
                reason = str(e)[:160] + (f" — {hint}" if hint else "")
                st.update({"ok": False, "reason": reason[:220],
                           "ms": int((time.time() - started) * 1000)})
                # мёртвым сервис считаем только при неверном ключе: модели
                # (404/403 в проекте) уже перебраны выше, лимиты и сеть — временное
                if auth_error(str(e)):
                    st["dead"] = True
                self.fails += 1
                self.last = {"provider": p.name, "ok": False, "reason": reason[:220],
                             "ms": st["ms"], "ts": time.time()}
                log.warning("ИИ-шапка: %s не ответил: %s%s", p.name, e,
                            f" ({hint})" if hint else "")
        return None

    async def headline(self, snap: dict, recent: Optional[List[str]] = None,
                       variant: int = 0, lang: str = "ru") -> Optional[str]:
        """То же, но без блокировки event loop бота."""
        return await asyncio.to_thread(self.headline_sync, snap, recent, variant, lang)


    # --- рассказ для дневного дайджеста ---------------------------------
    def narrative_sync(self, facts: dict, lang: str = "ru",
                       variant: int = 0) -> Optional[str]:
        """Подробный текст дайджеста (RU/EN) или None — тогда будет шаблон."""
        if not self.providers:
            return None
        from daily_digest import day_prompt
        prompt = day_prompt(facts, lang, variant)
        system = active_prompt("digest", lang)
        for p in self.providers:
            st = self.state[p.name]
            if st.get("dead"):
                continue
            started = time.time()
            tried: set = set()
            tried_keys: set = set()
            tokens = max(700, self.max_tokens * 3)
            try:
                while True:
                    try:
                        raw = self._attempt(p, prompt, lang, tokens=tokens,
                                            system=system)
                        text = clean_body(raw)
                        problem = body_problem(text, lang)
                        if problem:
                            raise RuntimeError(f"ответ не годится: {problem}")
                        text = fit_body(text)
                        break
                    except Exception as e:
                        if model_error(str(e)):
                            hint = suggested_model(str(e), p.model)
                            if hint and hint not in tried:
                                tried.add(p.model)
                                p.model = hint
                                st["model"] = hint
                                self.resolved[p.name] = hint
                                continue
                            nxt = self._switch_model(p, tried)
                            if nxt:
                                tried.add(nxt)
                                continue
                            st["dead"] = True
                            raise
                        if "пустой текст" in str(e) and tokens < 3200:
                            tokens = min(3200, tokens * 2)
                            log.info("ИИ (%s): пустой рассказ — повтор с лимитом %s",
                                     p.name, tokens)
                            continue
                        if quota_error(str(e)) and self._switch_key(p, tried_keys):
                            # Ключ упёрся в лимит — рассказ досочинит следующий
                            # ключ того же сервиса (или следующий сервис в цепи).
                            continue
                        raise
                st.update({"ok": True, "reason": "",
                           "ms": int((time.time() - started) * 1000)})
                self.calls += 1
                self.last = {"provider": p.name, "ok": True, "reason": "",
                             "ms": st["ms"], "ts": time.time()}
                log.info("ИИ-дайджест (%s): %s (%s), %d знаков за %s мс",
                         lang, p.name, p.model, len(text), st["ms"])
                return text
            except Exception as e:
                hint = ai_error_hint(str(e))
                reason = str(e)[:160] + (f" — {hint}" if hint else "")
                st.update({"ok": False, "reason": reason[:220],
                           "ms": int((time.time() - started) * 1000)})
                if auth_error(str(e)):
                    st["dead"] = True
                self.fails += 1
                self.last = {"provider": p.name, "ok": False, "reason": reason[:220],
                             "ms": st["ms"], "ts": time.time()}
                log.warning("ИИ-дайджест (%s): %s не ответил: %s%s", lang, p.name, e,
                            f" ({hint})" if hint else "")
        return None

    async def narrative(self, facts: dict, lang: str = "ru",
                        variant: int = 0) -> Optional[str]:
        """То же, но без блокировки event loop."""
        return await asyncio.to_thread(self.narrative_sync, facts, lang, variant)


def build_ai() -> Optional[AiWriter]:
    """Собираем писателя по окружению (None — если ключей нет)."""
    providers = build_providers()
    if not providers:
        log.info("ИИ-шапка: ключей нет — шапки будут из шаблонов")
        return None
    writer = AiWriter(
        providers,
        timeout=float(_env("LIQSCOPE_AI_TIMEOUT", "12") or 12),
        max_tokens=int(_env("LIQSCOPE_AI_MAX_TOKENS", "220") or 220),
        temperature=float(_env("LIQSCOPE_AI_TEMPERATURE", "0.9") or 0.9),
    )
    log.info("ИИ-шапка: %s", " → ".join(f"{p.name} ({p.model})" for p in providers))
    return writer
