"""Сводки по часам: архив постов канала и их показ на сайте.

Каждый пост, который бот выложил в канал (RU и/или EN), попадает в архив
(`PostStore`): время, фото, подпись на двух языках и цифры окна. Из архива
собирается раздел сайта «Сводки по часам» — тот же пост, что ушёл в Telegram,
виден на странице `/hourly`, а календарь показывает, за какие дни посты есть
и сколько их было.

Модуль чистый (никакой сети): посты складывает бот (`tg_bot._publish_digest`),
страницу отдаёт `api_hourly.py`.

Подпись канала написана разметкой Telegram (``<b>``, ``<i>``, ``<a>``), поэтому
перед показом она проходит через `tg_html()`: всё лишнее экранируется, а
безопасные теги остаются — пост выглядит как в канале и не может ничего
сломать на странице.
"""
from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from daily_digest import day_key, day_label, is_en, weekday_label
from hour_board import tz_offset

DEFAULT_KEEP = 1200            # ~200 суток при посте раз в 4 часа
DAY_SEC = 86400

#: Что из разметки Telegram оставляем на странице. Всё остальное — экранируется.
_TAGS = ("b", "strong", "i", "em", "u", "s", "strike", "del", "code", "pre")
_TAG_RE = re.compile(r"&lt;(/?)(%s)&gt;" % "|".join(_TAGS), re.I)
#: Ссылка: адрес берём до первой экранированной кавычки, остальные атрибуты
#: (target, rel…) отбрасываем — свои подставим сами.
_LINK_RE = re.compile(
    r'&lt;a\s+href=&quot;(.*?)&quot;.*?&gt;|&lt;/a&gt;', re.I)
_BR_RE = re.compile(r"&lt;br\s*/?&gt;", re.I)


def tz_seconds() -> int:
    """Сдвиг часового пояса сводок (по умолчанию МСК, как в постах)."""
    try:
        return int(tz_offset())
    except Exception:                                     # noqa: BLE001
        return 3 * 3600


def post_id(ts: float, tz: Optional[int] = None) -> str:
    """Идентификатор поста: «2026-09-19-1530» — по нему видно и день, и время."""
    tz = tz_seconds() if tz is None else int(tz)
    return time.strftime("%Y-%m-%d-%H%M", time.gmtime(float(ts) + tz))


def tg_html(text: Any) -> str:
    """Подпись канала → безопасный HTML для страницы.

    Сначала экранируем всё, потом возвращаем на место только теги, которые
    Telegram и так показывает: жирный, курсив, подчёркивание, зачёркивание,
    моноширинный текст и ссылки. Ссылки получают ``target`` и ``rel``: пост
    уходит на внешний сайт, но не отдаёт ему наш referrer.
    """
    # Снимаем экранирование один раз (в канале разметка уже экранирована) и
    # экранируем заново: амперсанды и кавычки на странице выглядят как в
    # Telegram, а не как «&amp;amp;».
    raw = html_mod.escape(html_mod.unescape(str(text if text is not None else "")),
                          quote=True)
    raw = _BR_RE.sub("<br>", raw)
    raw = _TAG_RE.sub(lambda m: "<%s%s>" % ("/" if m.group(1) else "", m.group(2).lower()),
                      raw)
    # Ссылки собираем вручную и следим за парностью: «висячий» </a> остаётся
    # экранированным текстом и разметку страницы не ломает.
    out: List[str] = []
    pos = 0
    depth = 0
    for m in _LINK_RE.finditer(raw):
        out.append(raw[pos:m.start()])
        pos = m.end()
        if m.group(1) is not None:
            out.append('<a href="%s" target="_blank" rel="noopener nofollow">'
                       % m.group(1))
            depth += 1
        elif depth:
            out.append("</a>")
            depth -= 1
        else:
            out.append(m.group(0))
    out.append(raw[pos:])
    if depth:
        out.append("</a>" * depth)
    return "".join(out)


def plain_text(text: Any) -> str:
    """Подпись без разметки: описание для превью и поиска.

    Telegram-теги вырезаем, ссылки оставляем словом, лишние пустые строки
    сжимаем — в мете страницы это читается, а в ``<pre>`` не мешает.
    """
    raw = re.sub(r"<a\s+[^>]*>", "", str(text if text is not None else ""), flags=re.I)
    raw = re.sub(r"</a>", "", raw, flags=re.I)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html_mod.unescape(raw)
    lines = [ln.rstrip() for ln in raw.splitlines()]
    out: List[str] = []
    for ln in lines:
        if not ln.strip() and (not out or not out[-1].strip()):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def texts_of(rec: dict) -> Dict[str, str]:
    """Подписи поста по языкам: только непустые, ключи — ru/en."""
    out: Dict[str, str] = {}
    for lang, val in ((rec or {}).get("texts") or {}).items():
        key = "en" if is_en(lang) else ("ru" if str(lang).startswith("ru") else "")
        text = str(val or "").strip()
        if key and text:
            out[key] = text
    return out


def pick_text(rec: dict, lang: str = "ru") -> str:
    """Текст поста на языке сайта; нет своего — английский (канал-источник)."""
    texts = texts_of(rec)
    want = "en" if is_en(lang) else ("ru" if str(lang).startswith("ru") else "en")
    if want == "ru":
        return texts.get("ru") or texts.get("en") or ""
    # Остальные языки интерфейса читают английскую версию: из английского
    # канала и приходят эти посты.
    return texts.get("en") or texts.get("ru") or ""


class PostStore:
    """Архив сводок: JSON-файл, свежие первыми.

    Пишем атомарно (tmp + rename) — перезапуск сервера в момент записи не
    должен оставить битый архив с постами.
    """

    def __init__(self, path: str = "", keep: int = DEFAULT_KEEP):
        self.path = str(path or "")
        self.keep = max(1, int(keep or DEFAULT_KEEP))
        self.items: List[dict] = []
        self.error = ""
        self.load()

    # --- чтение/запись -----------------------------------------------------
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
        except Exception as e:                      # битый файл не должен мешать
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
        except Exception as e:                      # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"

    # --- операции ----------------------------------------------------------
    def add(self, rec: dict) -> dict:
        """Положить пост в архив (по id заменяем, а не дублируем)."""
        rec = dict(rec or {})
        rid = str(rec.get("id") or "")
        if not rid:
            ts = float(rec.get("ts") or time.time())
            rid = post_id(ts)
        rec["id"] = rid
        rec.setdefault("ts", time.time())
        rec.setdefault("day", day_key(rec["ts"]))
        self.items = [x for x in self.items if str(x.get("id")) != rid]
        self.items.insert(0, rec)
        self._sort()
        self.items = self.items[:self.keep]
        self._flush()
        return rec

    def _sort(self) -> None:
        """Свежие посты первыми — даже если файл писали в другом порядке."""
        self.items.sort(key=lambda x: str(x.get("id") or ""), reverse=True)

    def get(self, rid: str) -> Optional[dict]:
        for x in self.items:
            if str(x.get("id")) == str(rid):
                return x
        return None

    def list(self) -> List[dict]:
        return list(self.items)

    def by_day(self, day: str) -> List[dict]:
        """Все посты одного дня (свежие первыми)."""
        want = str(day or "")
        return [x for x in self.items if str(x.get("day")) == want]

    def recent(self, limit: int = 6) -> List[dict]:
        return self.items[:max(1, int(limit or 6))]

    def days(self) -> List[dict]:
        """Индекс дней для календаря: дата, число постов и границы по времени."""
        out: Dict[str, dict] = {}
        for rec in self.items:
            day = str(rec.get("day") or "")
            if not day:
                continue
            row = out.get(day)
            if row is None:
                row = out[day] = {"day": day, "n": 0, "first": 0.0, "last": 0.0,
                                  "window_h": rec.get("window_h") or 4,
                                  "total_usd": 0.0, "liq_count": 0}
            ts = float(rec.get("ts") or 0)
            row["n"] += 1
            if not row["last"] or ts > row["last"]:
                row["last"] = ts
            if not row["first"] or ts < row["first"]:
                row["first"] = ts
            try:
                row["total_usd"] += float(rec.get("total_usd") or 0)
                row["liq_count"] += int(rec.get("liq_count") or 0)
            except (TypeError, ValueError):
                pass
        rows = sorted(out.values(), key=lambda x: x["day"], reverse=True)
        return rows

    def stats(self) -> Dict[str, Any]:
        """Сводка архива: сколько постов и за какой период."""
        days = self.days()
        return {
            "count": len(self.items),
            "days": len(days),
            "keep": self.keep,
            "first_day": days[-1]["day"] if days else "",
            "last_day": days[0]["day"] if days else "",
            "last_ts": float(self.items[0].get("ts") or 0) if self.items else 0.0,
        }


# ---------------------------------------------------------------------------
#  Публичный вид: то, что рисует сайт
# ---------------------------------------------------------------------------
def photo_url(rid: str) -> str:
    """Адрес картинки поста: она лежит вне static, отдаём её маршрутом."""
    return f"/api/hourly/photo/{rid}"


def public_post(rec: dict, lang: str = "ru", with_text: bool = True) -> dict:
    """Пост для сайта: время, фото и подпись — как в канале.

    ``text`` — подпись на языке сайта (для остальных языков интерфейса это
    английская версия: посты берутся из английского канала), ``texts`` —
    обе версии, чтобы страница могла переключить язык внутри поста.
    """
    rec = rec or {}
    rid = str(rec.get("id") or "")
    texts = texts_of(rec)
    photo = rec.get("photo") or {}
    path = str(photo.get("path") or "")
    out: Dict[str, Any] = {
        "id": rid,
        "ts": float(rec.get("ts") or 0),
        "day": rec.get("day") or "",
        "day_label": day_label(rec.get("day"), lang),
        "weekday": weekday_label(rec.get("day"), lang),
        "window_h": rec.get("window_h") or 4,
        "interval_h": rec.get("interval_h"),
        "total_usd": rec.get("total_usd"),
        "liq_count": rec.get("liq_count"),
        "langs": [k for k in ("ru", "en") if texts.get(k)],
        "sent": {k: bool(v) for k, v in (rec.get("sent") or {}).items()},
    }
    if path and os.path.isfile(path):
        out["photo"] = {"url": photo_url(rid), "name": photo.get("name") or "",
                        "source": photo.get("source") or "bundle"}
    if with_text:
        text = pick_text(rec, lang)
        out["text"] = text
        out["html"] = tg_html(text)
        out["texts"] = {k: tg_html(v) for k, v in texts.items()}
        out["plain"] = plain_text(text)[:400]
    return out


def day_index(rec_or_day: Any, lang: str = "ru") -> dict:
    """Строка календаря: «за день N сводок» — по ней видно, куда нажимать."""
    if isinstance(rec_or_day, dict):
        day = str(rec_or_day.get("day") or "")
        n = int(rec_or_day.get("n") or 0)
        total = rec_or_day.get("total_usd") or 0
    else:
        day, n, total = str(rec_or_day or ""), 0, 0
    return {"day": day, "label": day_label(day, lang), "weekday": weekday_label(day, lang),
            "n": n, "total_usd": total}
