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
    LIQSCOPE_AI_MAX_TOKENS       предел ответа модели (120)
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
    "ликвидаций спокойным деловым языком: живым, но без сленга, жаргона, "
    "восклицаний и обращения к читателю. Одно-два коротких предложения. "
    "Не используй цифры и проценты — они идут в таблице ниже поста. "
    "Не выдумывай факты, не давай советов, прогнозов и обещаний, не пиши "
    "хештеги, markdown, кавычки и подписи. Верни только текст шапки."
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


def build_prompt(snap: dict, recent: Optional[List[str]] = None) -> str:
    lines = [
        "Данные сводки (для смысла, не пересказывай их по пунктам):",
        *[f"— {x}" for x in summarize(snap)],
    ]
    keep = [r for r in (recent or []) if r][-4:]
    if keep:
        lines.append("")
        lines.append("Прошлые шапки — не повторяй их формулировки:")
        lines += [f"— {r}" for r in keep]
    lines.append("")
    lines.append("Напиши шапку (одна-две фразы, без цифр и процентов).")
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
                                ("groq", "llama-3.3-70b-versatile"),
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


MODEL_WORDS_BAD = ("embedding", "embed", "image", "tts", "audio", "live",
                   "transcribe", "translate", "veo", "imagen", "whisper",
                   "guard", "moderation", "rerank", "speech")


def pick_model(kind: str, ids: List[str]) -> str:
    """Самая подходящая модель для короткого текста из списка сервиса."""
    good = [i for i in ids if i and not any(w in i.lower() for w in MODEL_WORDS_BAD)]
    if not good:
        return ""
    if kind == "gemini":
        lite = [i for i in good if "flash-lite" in i]
        flash = [i for i in good if "flash" in i]
        pool = lite or flash or good

        def ver(x: str) -> float:
            m = re.search(r"gemini-(\d+)(?:\.(\d+))?", x)
            return float(f"{m.group(1)}.{m.group(2) or 0}") if m else 0.0

        return sorted(pool, key=ver, reverse=True)[0]
    if kind == "openrouter":
        free = [i for i in good if i.endswith(":free")]
        return sorted(free, key=len, reverse=True)[0] if free else ""
    if kind == "groq":
        for want in ("70b", "120b", "llama-3.3", "gpt-oss"):
            hit = [i for i in good if want in i.lower()]
            if hit:
                return sorted(hit, key=len)[0]
        return sorted(good, key=len)[0]
    return sorted(good, key=len)[0]


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
    """Похоже, что модель устарела/переименована (а не сеть и не ключ)."""
    low = (err or "").lower()
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
    if "401" in low or "403" in low or "invalid api key" in low:
        return "ключ не подошёл или у него нет доступа к этой модели"
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
    if any(ch.isdigit() for ch in text):
        return "есть цифры — они идут в таблице ниже"
    if len(text) < MIN_LEN:
        return "слишком коротко"
    low = text.lower()
    for bad in ("не могу", "извин", "как ии", "языковая модель", "нейросет",
                "инвестиционн", "не является"):
        if bad in low:
            return f"не шапка, а отказ ({bad})"
    if "http" in low or "t.me" in low:
        return "ссылка в шапке"
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
                 max_tokens: int = 120, temperature: float = 0.85):
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
    def _repair_model(self, p: Provider) -> bool:
        """Модель устарела — спрашиваем у сервиса актуальную и пробуем снова."""
        # выбор модели зависит от сервиса (groq/openrouter/gemini), а kind у
        # OpenAI-совместимых один — поэтому смотрим на имя сервиса
        fresh = pick_model(p.name, list_models(p, self.timeout))
        if not fresh or fresh == p.model:
            return False
        log.warning("ИИ (%s): модель %s недоступна — перехожу на %s",
                    p.name, p.model, fresh)
        p.model = fresh
        self.state[p.name]["model"] = fresh
        self.resolved[p.name] = fresh
        return True

    def _attempt(self, p: Provider, prompt: str) -> Optional[str]:
        """Один запрос к сервису с самолечением модели. None — не вышло."""
        url, body, headers = request_for(p, prompt, SYSTEM_PROMPT,
                                         self.temperature, self.max_tokens)
        data = post_json(url, body, headers, self.timeout)
        return parse_reply(p, data)

    def headline_sync(self, snap: dict, recent: Optional[List[str]] = None) -> Optional[str]:
        """Первый удачный ответ или None (тогда шапка будет из шаблонов)."""
        if not self.providers:
            return None
        prompt = build_prompt(snap, recent)
        for p in self.providers:
            st = self.state[p.name]
            if st.get("dead"):
                continue
            started = time.time()
            try:
                try:
                    raw = self._attempt(p, prompt)
                except Exception as e:
                    # Google шлёт в 404 готовую замену — пробуем её, а если её
                    # нет, спрашиваем список моделей у сервиса.
                    if not model_error(str(e)):
                        raise
                    hint = suggested_model(str(e), p.model)
                    if hint:
                        log.warning("ИИ (%s): модель %s устарела — беру %s из ответа",
                                    p.name, p.model, hint)
                        p.model = hint
                        st["model"] = hint
                        self.resolved[p.name] = hint
                        raw = self._attempt(p, prompt)
                    elif self._repair_model(p):
                        raw = self._attempt(p, prompt)
                    else:
                        raise
                head = clean_head(raw)
                problem = head_problem(head)
                if problem and problem != LONG:
                    raise RuntimeError(f"ответ не годится: {problem}")
                head = fit_head(head)
                if not head:
                    raise RuntimeError("ответ не годится: пусто после чистки")
                st.update({"ok": True, "reason": "", "ms": int((time.time() - started) * 1000)})
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
                # ключ и модель (после подбора) больше не дёргаем; лимиты и сеть — повторяем
                if re.search(r"HTTP (401|403)|invalid api key", str(e), re.I) \
                        and not model_error(str(e)):
                    st["dead"] = True
                self.fails += 1
                self.last = {"provider": p.name, "ok": False, "reason": reason[:220],
                             "ms": st["ms"], "ts": time.time()}
                log.warning("ИИ-шапка: %s не ответил: %s%s", p.name, e,
                            f" ({hint})" if hint else "")
        return None

    async def headline(self, snap: dict,
                       recent: Optional[List[str]] = None) -> Optional[str]:
        """То же, но без блокировки event loop бота."""
        return await asyncio.to_thread(self.headline_sync, snap, recent)


def build_ai() -> Optional[AiWriter]:
    """Собираем писателя по окружению (None — если ключей нет)."""
    providers = build_providers()
    if not providers:
        log.info("ИИ-шапка: ключей нет — шапки будут из шаблонов")
        return None
    writer = AiWriter(
        providers,
        timeout=float(_env("LIQSCOPE_AI_TIMEOUT", "12") or 12),
        max_tokens=int(_env("LIQSCOPE_AI_MAX_TOKENS", "120") or 120),
        temperature=float(_env("LIQSCOPE_AI_TEMPERATURE", "0.85") or 0.85),
    )
    log.info("ИИ-шапка: %s", " → ".join(f"{p.name} ({p.model})" for p in providers))
    return writer
