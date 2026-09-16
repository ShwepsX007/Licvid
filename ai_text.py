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


def summarize(snap: dict) -> List[str]:
    """Строки-факты для промпта (без HTML и цифр-обманок)."""
    s = snap or {}
    try:
        h = int(s.get("window_h") or 4)
    except (TypeError, ValueError):
        h = 4
    longs = float(s.get("longs_usd") or 0)
    shorts = float(s.get("shorts_usd") or 0)
    out = [
        f"Окно сводки: последние {h} часа",
        f"Всего ликвидаций: {money(s.get('total_usd'))} "
        f"(лонги {money(longs)}, шорты {money(shorts)}), "
        f"событий: {int(s.get('count') or 0)}",
        f"Характер окна: {_side_word(longs, shorts)}",
    ]
    coins = (s.get("top_coins") or [])[:3]
    if coins:
        parts = []
        for c in coins:
            parts.append(f"{c.get('symbol') or '?'} {money(c.get('usd'))} "
                         f"(лонги {money(c.get('longs'))}, шорты {money(c.get('shorts'))})")
        out.append("Лидеры: " + "; ".join(parts))
    ex = list((s.get("exchanges") or {}).items())[:3]
    if ex:
        out.append("Биржи: " + ", ".join(f"{k} {money(v)}" for k, v in ex))
    b = s.get("biggest") or {}
    if b:
        out.append(f"Крупнейшее событие: {b.get('symbol') or '?'} "
                   f"{money(b.get('usd'))} на {b.get('exchange') or '?'}")
    oi = s.get("oi") or {}
    oi_bits = []
    for sym, p in list(oi.items())[:3]:
        ch = (p or {}).get("changes") or {}
        pct = _pct((ch.get("h1") or {}).get("pct")) if isinstance(ch.get("h1"), dict) else ""
        if pct:
            oi_bits.append(f"{sym} {pct} за час")
    if oi_bits:
        out.append("Открытый интерес: " + ", ".join(oi_bits))
    cvd = s.get("cvd") or {}
    cvd_bits = [f"{sym} {money(v)}" for sym, v in list(cvd.items())[:3]]
    if cvd_bits:
        out.append("Дельта объёма (CVD): " + ", ".join(cvd_bits))
    return out


def build_prompt(snap: dict, recent: Optional[List[str]] = None,
                 variant: int = 0) -> str:
    lines = [
        "Данные сводки — используй их в тексте (числа и монеты брать отсюда):",
        *[f"— {x}" for x in summarize(snap)],
    ]
    keep = [r for r in (recent or []) if r][-4:]
    if keep:
        lines.append("")
        lines.append("Прошлые шапки — не повторяй их формулировки и не начинай так же:")
        lines += [f"— {r}" for r in keep]
    lines.append("")
    lines.append(f"Акцент этого поста: {ANGLES[int(variant) % len(ANGLES)]}")
    lines.append("Напиши шапку: 2–3 предложения, с конкретной монетой и 1–2 числами.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Сервисы
# ---------------------------------------------------------------------------
@dataclass
class Provider:
    name: str
    kind: str                    # "gemini" | "openai"
    key: str
    url: str
    model: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    models_url: str = ""                 # где спросить список моделей
    explicit_model: bool = False         # модель задана админом вручную

    def public(self) -> Dict[str, str]:
        return {"name": self.name, "model": self.model}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _provider(name: str, default_model: str, key_env: str,
              model_env: str) -> Optional[Provider]:
    key = _env(key_env)
    if not key:
        return None
    explicit = bool(_env(model_env) or _env("LIQSCOPE_AI_MODEL"))
    model = _env(model_env) or _env("LIQSCOPE_AI_MODEL") or default_model
    if name == "gemini":
        url = _env("LIQSCOPE_AI_GEMINI_URL",
                   "https://generativelanguage.googleapis.com/v1beta/models")
        return Provider(name=name, kind="gemini", key=key, url=url, model=model,
                        models_url=url, explicit_model=explicit)
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
                    headers=headers, explicit_model=explicit)


def custom_provider() -> Optional[Provider]:
    """Любой OpenAI-совместимый сервис: LIQSCOPE_AI_URL + LIQSCOPE_AI_KEY."""
    url, key = _env("LIQSCOPE_AI_URL"), _env("LIQSCOPE_AI_KEY")
    if not (url and key):
        return None
    return Provider(name="custom", kind="openai", key=key, url=url,
                    model=_env("LIQSCOPE_AI_MODEL") or "gpt-4o-mini",
                    explicit_model=bool(_env("LIQSCOPE_AI_MODEL")))


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
                     "reason": "не пробовали", "dead": False, "ms": 0}
            for p in self.providers}
        self.last: Dict[str, Any] = {"provider": "", "ok": False, "reason": "",
                                     "ms": 0, "ts": 0.0}
        self.calls = 0
        self.fails = 0
        self.resolved: Dict[str, str] = {}    # сервис -> модель, которую подобрали
        self._models: Dict[str, List[str]] = {}     # кэш списка моделей сервиса
        self._rejected: Dict[str, set] = {}         # модели, которые не подошли
        self._extra: Dict[str, int] = {}            # добавка к лимиту ответа

    # --- состояние для админки ---
    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def status(self) -> Dict[str, Any]:
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

    def _attempt(self, p: Provider, prompt: str) -> Optional[str]:
        """Один запрос к сервису (с учётом добавки к лимиту ответа)."""
        tokens = self.max_tokens + self._extra.get(p.name, 0)
        url, body, headers = request_for(p, prompt, SYSTEM_PROMPT,
                                         self.temperature, tokens)
        data = post_json(url, body, headers, self.timeout)
        return parse_reply(p, data)

    def headline_sync(self, snap: dict, recent: Optional[List[str]] = None,
                      variant: int = 0) -> Optional[str]:
        """Первый удачный ответ или None (тогда шапка будет из шаблонов)."""
        if not self.providers:
            return None
        angle = int(variant)
        for p in self.providers:
            st = self.state[p.name]
            if st.get("dead"):
                continue
            started = time.time()
            tried: set = set()
            repeats = 0        # сколько раз переспрашивали из-за повтора
            try:
                while True:
                    try:
                        prompt = build_prompt(snap, recent, angle)
                        raw = self._attempt(p, prompt)
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
                        head = fit_head(head)
                        if not head:
                            raise RuntimeError("ответ не годится: пусто после чистки")
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
                        raise
                st.update({"ok": True, "reason": "",
                           "ms": int((time.time() - started) * 1000)})
                self.calls += 1
                self.last = {"provider": p.name, "ok": True, "reason": "",
                             "ms": st["ms"], "ts": time.time()}
                log.info("ИИ-шапка: %s (%s) за %s мс", p.name, p.model, st["ms"])
                return head
            except Exception as e:
                hint = ai_error_hint(str(e))
                reason = str(e)[:160] + (f" — {hint}" if hint else "")
                st.update({"ok": False, "reason": reason[:220],
                           "ms": int((time.time() - started) * 1000)})
                # мёртвым сервис считаем только при неверном ключе: модели
                # (404/403 в проекте) уже перебраны выше, лимиты и сеть — временное
                if re.search(r"HTTP 401|invalid api key|unauthorized", str(e), re.I) \
                        and not model_error(str(e)):
                    st["dead"] = True
                self.fails += 1
                self.last = {"provider": p.name, "ok": False, "reason": reason[:220],
                             "ms": st["ms"], "ts": time.time()}
                log.warning("ИИ-шапка: %s не ответил: %s%s", p.name, e,
                            f" ({hint})" if hint else "")
        return None

    async def headline(self, snap: dict, recent: Optional[List[str]] = None,
                       variant: int = 0) -> Optional[str]:
        """То же, но без блокировки event loop бота."""
        return await asyncio.to_thread(self.headline_sync, snap, recent, variant)


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
