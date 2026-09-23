"""📰 Статьи: раздел сайта, который наполняет админ.

Статья — это то, что админ написал сам: заголовок, текст и одна обложка на
обе языковые версии. Русская версия — основная, английская появляется либо
переводом ИИ, либо руками (если ИИ недоступен). Пока английской версии нет,
статья не публикуется: раздел сайта двуязычный, и англоязычный гость не
должен увидеть пустую страницу.

Хранение — JSON-файл (``data/articles.json``) рядом с архивами дайджеста и
сводок: записи читаются целиком, поэтому базы для них не нужно, а файл легко
унести вместе с фото. Фото лежат вне ``static`` (``data/articles``), отдаются
маршрутом ``/api/articles/photo/{id}`` — как обложки выпусков.

Модуль чистый: никакой сети и Telegram. Публикацию по расписанию и отправку в
каналы делает ``api_articles.py``.

Текст статьи пишется лёгкой разметкой (её же видит предпросмотр в админке):
``##`` заголовок, ``**жирный**``, ``*курсив*``, ``[ссылка](url)``, списки
``- `` и ``1. ``, цитаты ``> ``, разделитель ``---``. ``markdown_html()``
переводит её в безопасный HTML: сначала экранируется всё, потом возвращаются
разрешённые теги — чужой код со страницы статьи ничего не сломает.
"""
from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

DEFAULT_KEEP = 500              # сколько опубликованных статей держим в архиве
TITLE_MAX = 160
TEXT_MAX = 60000
EXCERPT_LEN = 200
STATUSES = ("draft", "scheduled", "published")
LANGS = ("ru", "en")

#: Кириллица → латиница: из русского заголовка получается читаемый адрес.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def now_ts() -> float:
    return time.time()


# ---------------------------------------------------------------------------
#  Адрес статьи
# ---------------------------------------------------------------------------
def slugify(text: str, fallback: str = "article") -> str:
    """Заголовок → адрес: «Как читать стены» → ``kak-chitat-steny``.

    Латиница остаётся как есть, кириллица транслитерируется, всё прочее —
    разделители. Пусто (одни знаки) — берём ``fallback``: адрес без букв
    нечитаем и мешает искать статью глазами.
    """
    raw = str(text or "").strip().lower()
    out: List[str] = []
    for ch in raw:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    slug = slug[:80].strip("-")
    return slug or fallback


def unique_slug(base: str, taken) -> str:
    """Свободный адрес: к занятому добавляем -2, -3 и так далее."""
    busy = {str(x) for x in (taken or [])}
    if base not in busy:
        return base
    n = 2
    while f"{base}-{n}" in busy:
        n += 1
    return f"{base}-{n}"


def article_path(slug: str) -> str:
    return f"/articles/{slug}"


def photo_url(slug: str) -> str:
    """Обложка статьи: файл лежит вне static, отдаём его маршрутом."""
    return f"/api/articles/photo/{slug}"


# ---------------------------------------------------------------------------
#  Лёгкая разметка текста
# ---------------------------------------------------------------------------
def _esc(text: Any) -> str:
    return html_mod.escape(str(text if text is not None else ""), quote=False)


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEAD_RE = re.compile(r"^(#{2,4})\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,})\s*$")


def inline_html(text: str) -> str:
    """Строчная разметка → HTML. Текст уже экранирован вызывающим."""
    out = _IMG_RE.sub(
        lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy">',
        text)
    out = _LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener nofollow">'
                  f"{m.group(1)}</a>",
        out)
    out = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    out = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    return out


def markdown_html(text: Any) -> str:
    """Текст статьи → безопасный HTML для страницы.

    Экранируем всё, затем собираем блоки: заголовки, списки, цитаты,
    разделители и абзацы. Внутри абзаца одиночный перенос строки — это
    ``<br>``: админ пишет текст как в мессенджере, и строки не должны
    слипаться в одну.
    """
    src = str(text if text is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    # Блоки разбираем по «сырым» строкам: если экранировать сразу, «>» станет
    # «&gt;» и цитата перестанет отличаться от абзаца. Экранируем содержимое
    # блока — уже после того, как поняли, что это за блок.
    lines = src.split("\n")
    blocks: List[str] = []
    para: List[str] = []
    lst: List[str] = []          # накопленные <li>
    lst_tag = ""

    def flush_para() -> None:
        if para:
            blocks.append("<p>" + "<br>".join(inline_html(_esc(x)) for x in para) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal lst_tag
        if lst:
            blocks.append(f"<{lst_tag}>" + "".join(f"<li>{x}</li>" for x in lst)
                          + f"</{lst_tag}>")
            lst.clear()
            lst_tag = ""

    for line in lines:
        if _HR_RE.match(line):
            flush_para()
            flush_list()
            blocks.append("<hr>")
            continue
        m = _HEAD_RE.match(line)
        if m:
            flush_para()
            flush_list()
            level = min(4, len(m.group(1)) + 1)      # ## → h3, ### → h4
            blocks.append(f"<h{level}>{inline_html(_esc(m.group(2).strip()))}</h{level}>")
            continue
        m = _UL_RE.match(line)
        if m:
            flush_para()
            if lst_tag and lst_tag != "ul":
                flush_list()
            lst_tag = "ul"
            lst.append(inline_html(_esc(m.group(1).strip())))
            continue
        m = _OL_RE.match(line)
        if m:
            flush_para()
            if lst_tag and lst_tag != "ol":
                flush_list()
            lst_tag = "ol"
            lst.append(inline_html(_esc(m.group(1).strip())))
            continue
        m = _QUOTE_RE.match(line)
        if m:
            flush_para()
            flush_list()
            blocks.append("<blockquote>" + inline_html(_esc(m.group(1).strip()))
                          + "</blockquote>")
            continue
        if not line.strip():
            flush_para()
            flush_list()
            continue
        flush_list()
        para.append(line)
    flush_para()
    flush_list()
    return "\n".join(blocks)


def markdown_plain(text: Any) -> str:
    """Тот же текст без разметки — для превью ссылки и описания страницы."""
    src = str(text if text is not None else "")
    src = _IMG_RE.sub(lambda m: m.group(1), src)
    src = _LINK_RE.sub(lambda m: m.group(1), src)
    src = re.sub(r"^#{2,4}\s+", "", src, flags=re.M)
    src = re.sub(r"^\s*[-*>]\s+", "", src, flags=re.M)
    src = re.sub(r"^\s*\d+[.)]\s+", "", src, flags=re.M)
    src = re.sub(r"(?:\*\*|\*|`)", "", src)
    src = re.sub(r"\n{2,}", " ", src)
    src = re.sub(r"\s+", " ", src)
    return src.strip()


def excerpt(text: Any, limit: int = EXCERPT_LEN) -> str:
    """Первые знаки текста целыми словами — для карточки и поста в канал."""
    plain = markdown_plain(text)
    if len(plain) <= limit:
        return plain
    cut = plain[:limit]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:—-") + "…"


def text_problem(text: Any, title: Any = "") -> str:
    """Почему текст не годится: пусто или в нём только заголовок."""
    body = markdown_plain(text)
    if not str(text or "").strip():
        return "Нет текста статьи."
    if len(body) < 40:
        return "Текст слишком короткий — нужна хотя бы пара предложений."
    if len(str(text)) > TEXT_MAX:
        return f"Слишком длинный текст: до {TEXT_MAX} знаков."
    if not str(title or "").strip():
        return "Нет заголовка."
    return ""


# ---------------------------------------------------------------------------
#  Архив статей
# ---------------------------------------------------------------------------
class ArticleStore:
    """Статьи админа: JSON-файл, свежие первыми."""

    def __init__(self, path: str = "", keep: int = DEFAULT_KEEP):
        self.path = str(path or "")
        self.keep = max(1, int(keep or DEFAULT_KEEP))
        self.items: List[dict] = []
        self.error = ""
        self.load()

    # --- файл ---
    def load(self) -> List[dict]:
        self.items = []
        self.error = ""
        if not self.path or not os.path.exists(self.path):
            return self.items
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data = data.get("items") or []
            self.items = [x for x in data if isinstance(x, dict)]
            self._sort()
        except Exception as e:                       # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.items = []
        return self.items

    def _flush(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"items": self.items}, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:                       # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"

    def _sort(self) -> None:
        """Свежие сверху: сначала по дате публикации, потом по времени правки."""
        def key(rec: dict) -> float:
            return float(rec.get("published_at") or rec.get("updated")
                         or rec.get("created") or 0)
        self.items.sort(key=key, reverse=True)

    # --- чтение ---
    def get(self, aid: str) -> Optional[dict]:
        want = str(aid or "")
        for x in self.items:
            if str(x.get("id")) == want:
                return x
        return None

    def list(self) -> List[dict]:
        return list(self.items)

    def slugs(self) -> List[str]:
        return [str(x.get("id") or "") for x in self.items]

    def published(self, now: Optional[float] = None) -> List[dict]:
        """Что видит посетитель: опубликованные, свежие первыми."""
        now = now_ts() if now is None else float(now)
        out = [x for x in self.items
               if str(x.get("status")) == "published"
               and float(x.get("published_at") or 0) <= now]
        out.sort(key=lambda x: float(x.get("published_at") or 0), reverse=True)
        return out

    def scheduled(self) -> List[dict]:
        return [x for x in self.items if str(x.get("status")) == "scheduled"]

    def due(self, now: Optional[float] = None) -> List[dict]:
        """Запланированные, чьё время уже наступило."""
        now = now_ts() if now is None else float(now)
        out = [x for x in self.items
               if str(x.get("status")) == "scheduled"
               and 0 < float(x.get("publish_at") or 0) <= now]
        out.sort(key=lambda x: float(x.get("publish_at") or 0))
        return out

    # --- запись ---
    def add(self, rec: dict) -> dict:
        """Создать или обновить статью по её адресу (id)."""
        rec = dict(rec or {})
        rid = str(rec.get("id") or "")
        if not rid:
            rid = unique_slug(slugify(rec.get("title_ru") or rec.get("title_en")),
                              self.slugs())
        rec["id"] = rid
        rec.setdefault("created", now_ts())
        rec["updated"] = now_ts()
        if str(rec.get("status")) not in STATUSES:
            rec["status"] = "draft"
        self.items = [x for x in self.items if str(x.get("id")) != rid]
        self.items.insert(0, rec)
        self._sort()
        self._prune()
        self._flush()
        return rec

    def _prune(self) -> None:
        """Старые опубликованные обрезаем, черновики и планы не трогаем."""
        published = [x for x in self.items if str(x.get("status")) == "published"]
        if len(published) <= self.keep:
            return
        drop = {id(x) for x in published[self.keep:]}
        self.items = [x for x in self.items if id(x) not in drop]

    def delete(self, aid: str) -> bool:
        want = str(aid or "")
        before = len(self.items)
        self.items = [x for x in self.items if str(x.get("id")) != want]
        if len(self.items) == before:
            return False
        self._flush()
        return True

    def stats(self) -> Dict[str, Any]:
        by: Dict[str, int] = {}
        for x in self.items:
            st = str(x.get("status") or "draft")
            by[st] = by.get(st, 0) + 1
        return {
            "count": len(self.items),
            "published": by.get("published", 0),
            "draft": by.get("draft", 0),
            "scheduled": by.get("scheduled", 0),
            "with_en": sum(1 for x in self.items if (x.get("texts") or {}).get("en")),
            "keep": self.keep,
            "last_ts": float((self.items[0] or {}).get("updated") or 0) if self.items else 0.0,
        }


# ---------------------------------------------------------------------------
#  Публичный вид
# ---------------------------------------------------------------------------
def texts_of(rec: dict) -> Dict[str, str]:
    """Тексты статьи по языкам (пустые не отдаём)."""
    raw = (rec or {}).get("texts") or {}
    out: Dict[str, str] = {}
    for code in LANGS:
        val = str(raw.get(code) or "").strip()
        if val:
            out[code] = val
    return out


def titles_of(rec: dict) -> Dict[str, str]:
    raw = (rec or {}).get("titles") or {}
    out: Dict[str, str] = {}
    for code in LANGS:
        val = str(raw.get(code) or "").strip()
        if val:
            out[code] = val
    return out


def pick_text(rec: dict, lang: str = "en") -> str:
    """Текст на языке посетителя; нет перевода — показываем русский."""
    texts = texts_of(rec)
    code = "en" if str(lang or "").startswith("en") else str(lang or "ru")
    if code.startswith("en"):
        return texts.get("en") or texts.get("ru") or ""
    # zh/hi/es читают английскую версию: русскую им показывать нечего
    if code == "ru":
        return texts.get("ru") or texts.get("en") or ""
    return texts.get("en") or texts.get("ru") or ""


def pick_title(rec: dict, lang: str = "en") -> str:
    titles = titles_of(rec)
    text = pick_text(rec, lang)
    code = "ru" if str(lang or "").startswith("ru") else "en"
    fallback = excerpt(text, 80)
    if code == "ru":
        return titles.get("ru") or titles.get("en") or fallback
    return titles.get("en") or titles.get("ru") or fallback


def _photo_public(rec: dict) -> Optional[dict]:
    """Обложка: файл есть на диске — отдаём ссылку на маршрут.

    В адресе есть версия (``?v=имя файла``): админ заменил фото — и браузеры
    сразу показывают новое, а не часовую копию из кеша. Сам файл при этом
    кешируется надолго: адрес у каждой версии свой.
    """
    photo = (rec or {}).get("photo") or {}
    path = str(photo.get("path") or "")
    if not path or not os.path.isfile(path):
        return None
    name = str(photo.get("name") or os.path.basename(path))
    version = "".join(ch for ch in name if ch.isalnum())[-12:] or "1"
    return {"url": f"{photo_url(str(rec.get('id') or ''))}?v={version}",
            "base_url": photo_url(str(rec.get("id") or "")),
            "name": name,
            "source": str(photo.get("source") or "upload")}


def public_article(rec: dict, lang: str = "en", with_text: bool = True) -> dict:
    """Статья для страницы: заголовок, обложка и текст на языке посетителя."""
    rec = rec or {}
    rid = str(rec.get("id") or "")
    texts = texts_of(rec)
    title = pick_title(rec, lang)
    out: Dict[str, Any] = {
        "id": rid,
        "slug": rid,
        "url": article_path(rid),
        "titles": titles_of(rec),
        "langs": [k for k in LANGS if texts.get(k)],
        "published_at": float(rec.get("published_at") or 0),
        "updated": float(rec.get("updated") or 0),
        "created": float(rec.get("created") or 0),
        "status": str(rec.get("status") or "draft"),
        "sent": {k: float(v or 0) for k, v in (rec.get("sent") or {}).items()},
        "title": title,
        "title_lang": title_lang(rec, lang),
        "text_lang": shown_lang(rec, lang),
        "excerpt": excerpt(pick_text(rec, lang)),
        "photo": _photo_public(rec),
    }
    if with_text:
        text = pick_text(rec, lang)
        out["text"] = text
        out["html"] = markdown_html(text)
        out["plain"] = markdown_plain(text)
    return out


def shown_lang(rec: dict, lang: str = "en") -> str:
    """На каком языке текст, который увидит посетитель.

    Английской версии нет — показываем русскую. Знать это нужно странице:
    русский абзац на английской странице помечается ``lang="ru"``, иначе
    поисковик и скринридер считают его английским.
    """
    texts = texts_of(rec)
    code = "ru" if str(lang or "").startswith("ru") else "en"
    if code == "ru":
        return "ru" if texts.get("ru") else ("en" if texts.get("en") else "")
    return "en" if texts.get("en") else ("ru" if texts.get("ru") else "")


def title_lang(rec: dict, lang: str = "en") -> str:
    """На каком языке заголовок: он может отличаться от языка текста."""
    titles = titles_of(rec)
    code = "ru" if str(lang or "").startswith("ru") else "en"
    if code == "ru":
        return "ru" if titles.get("ru") else ("en" if titles.get("en") else shown_lang(rec, lang))
    return "en" if titles.get("en") else ("ru" if titles.get("ru") else shown_lang(rec, lang))


def index_row(rec: dict, lang: str = "en") -> dict:
    """Строка списка: без полного текста — карточкам он не нужен."""
    return public_article(rec, lang, with_text=False)


def is_published(rec: dict, now: Optional[float] = None) -> bool:
    now = now_ts() if now is None else float(now)
    return (str((rec or {}).get("status")) == "published"
            and float((rec or {}).get("published_at") or 0) <= now)
