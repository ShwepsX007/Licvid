"""HTTP-часть дневного дайджеста: сборка выпуска и API для сайта.

Собирает факты за сутки из уже работающих источников сервера (ликвидации,
цены и оборот по часовым свечам, открытый интерес), просит ИИ написать
живой рассказ на двух языках, складывает выпуск в архив и отдаёт сайту:

    GET  /digest                 — страница архива (static/digest.html)
    GET  /api/digest             — список выпусков (без тяжёлых полей)
    GET  /api/digest/today       — последний выпуск целиком
    GET  /api/digest/{day}       — выпуск за конкретную дату
    POST /api/digest/run         — собрать (и при желании сразу выложить) — админ

Публикацию в каналы делает бот (tg_bot.publish_digest), сюда он не влезает.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, JSONResponse

import seo_pages
from daily_digest import (DAY_SEC, DEFAULT_KEEP, NARRATIVE_MIN, DigestStore, brief,
                          collect_day, day_key, day_label, fallback_narrative,
                          render_article, render_post)
from hour_board import tz_offset

log = logging.getLogger("liqscope.digest")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")


class Ctx:
    """Всё, что нужно сборщику; вешает server.py (как account_ctx в web_account)."""

    def __init__(self) -> None:
        self.store: DigestStore = DigestStore("")
        #: Store аккаунтов: в нём лежат фото рубрик (обложка выпуска)
        self.photo_store = None
        self.liqs_fn = None          # () -> список событий ликвидаций
        self.symbols_fn = None       # () -> [монета] (по обороту, свежие первыми)
        self.candles_fn = None       # async (монета, таймфрейм) -> {"candles": [...]}
        self.oi_fn = None            # async (монета) -> payload OI
        self.ai_fn = None            # async (facts, lang) -> текст рассказа
        self.publish_fn = None       # async (rec, langs, force) -> {lang: [ok, err]}
        self.page_ok = True          # отдавать ли страницу /digest
        self.public_url = ""
        self.window_sec = DAY_SEC
        self.max_symbols = 12        # сколько монет тянуть за сутки
        self.oi_symbols = 6          # по скольким считать OI против оборота
        self.price_top = 5
        self.oi_top = 5
        self.hours_pull = 25         # часовых свечей на монету (24ч + запас)
        self.last: Dict[str, Any] = {}
        self.busy = False
        self.retry_at = 0.0          # когда снова пробовать выпуск после сбоя
        self.retry_note = ""         # почему выпуск не ушёл в прошлый раз
        # Настройки вечернего выпуска: их читает сервер (store + env),
        # планировщик только получает готовый словарь — модуль без базы.
        self.settings_fn = None
        self.env_locked: Dict[str, str] = {}   # ключи, замороженные окружением
        # set_setting_fn — запись настроек в базу (её даёт server.py)
        self.set_setting_fn = None


ctx = Ctx()


# ---------------------------------------------------------------------------
#  Данные за сутки: часовые свечи монет
# ---------------------------------------------------------------------------
def _rows(entry: Any) -> List[dict]:
    if isinstance(entry, dict):
        rows = entry.get("candles") or []
    else:
        rows = entry or []
    return [c for c in rows if isinstance(c, dict)]


def hour_cell(rows: List[dict], now: float, hours: int) -> Dict[str, dict]:
    """Разложить свечи по календарным часам (UTC) внутри окна."""
    out: Dict[str, dict] = {}
    start = now - hours * 3600
    for c in rows:
        try:
            ts = float(c.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if ts < start or ts > now + 3600:
            continue
        h = str(int(ts // 3600 * 3600))
        try:
            vol = float(c.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        cvd = c.get("cvd")
        cell = out.setdefault(h, {"vol": 0.0, "cvd": 0.0, "has_cvd": False})
        cell["vol"] += vol
        if cvd is not None:
            try:
                cell["cvd"] += float(cvd)
                cell["has_cvd"] = True
            except (TypeError, ValueError):
                pass
    return out


def price_row(rows: List[dict], now: float, hours: int) -> Optional[dict]:
    """Цена, изменение за сутки и оборот — по часовым свечам монеты."""
    window = []
    start = now - hours * 3600
    for c in rows:
        try:
            ts = float(c.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if start <= ts <= now + 3600:
            window.append(c)
    if not window:
        return None
    window.sort(key=lambda c: float(c.get("time") or 0))
    first, last = window[0], window[-1]
    def _f(c: dict, key: str) -> Optional[float]:
        try:
            return float(c.get(key))
        except (TypeError, ValueError):
            return None
    base = _f(first, "open") or _f(first, "close")
    last_close = _f(last, "close") or base
    pct = None
    if base and last_close:
        pct = (last_close / base - 1.0) * 100.0
    vol = 0.0
    for c in window:
        try:
            vol += float(c.get("volume") or 0)
        except (TypeError, ValueError):
            pass
    return {"price": last_close, "pct": pct, "vol_usd": vol}


async def market_day(symbols: List[str], hours: int = 24,
                     now: Optional[float] = None) -> Dict[str, dict]:
    """{потоки по часам, цены} по списку монет — свечи тянем параллельно."""
    now = float(now if now is not None else time.time())
    flows: Dict[str, dict] = {}
    prices: Dict[str, dict] = {}
    if not symbols or ctx.candles_fn is None:
        return {"flows": flows, "prices": prices}
    sem = asyncio.Semaphore(5)

    async def one(sym: str):
        async with sem:
            try:
                entry = await asyncio.wait_for(ctx.candles_fn(sym, 60), timeout=25)
            except Exception as e:
                log.debug("дайджест %s: свечи не пришли: %s", sym, e)
                return sym, None
            return sym, entry

    for sym, entry in await asyncio.gather(*(one(s) for s in symbols)):
        rows = _rows(entry)
        if not rows:
            continue
        cells = hour_cell(rows, now, hours)
        if cells:
            flows[sym] = cells
        row = price_row(rows, now, hours)
        if row:
            prices[sym] = row
    return {"flows": flows, "prices": prices}


async def collect_oi(symbols: List[str]) -> Dict[str, dict]:
    """Открытый интерес по монетам (payload трекера OI)."""
    out: Dict[str, dict] = {}
    if ctx.oi_fn is None:
        return out
    for sym in symbols:
        try:
            payload = await asyncio.wait_for(ctx.oi_fn(sym), timeout=20)
        except Exception as e:
            log.debug("дайджест OI %s: %s", sym, e)
            continue
        if isinstance(payload, dict) and payload.get("total_usd"):
            out[sym] = payload
    return out


async def build_facts(now: Optional[float] = None,
                      window: Optional[int] = None) -> dict:
    """Полный снимок суток из живых источников."""
    now = float(now if now is not None else time.time())
    window = int(window or ctx.window_sec)
    events = list(ctx.liqs_fn() or []) if ctx.liqs_fn else []
    symbols: List[str] = []
    if ctx.symbols_fn:
        try:
            symbols = [s for s in (ctx.symbols_fn() or []) if s]
        except Exception as e:
            log.warning("дайджест: список монет не получен: %s", e)
    # монеты, по которым были ликвидации, важнее прочих — их добавляем в конец,
    # чтобы они тоже попали в цену и оборот, но не вытеснили топ оборота
    for x in reversed(events):
        sym = str(x.get("symbol") or "")
        if sym and sym not in symbols:
            symbols.append(sym)
        if len(symbols) >= ctx.max_symbols:
            break
    symbols = symbols[:ctx.max_symbols]
    market = await market_day(symbols, hours=ctx.hours_pull, now=now)
    # OI считаем по самым оборотистым монетам: они и в цене, и в потоке
    oi_symbols = [s for s in symbols[:ctx.oi_symbols]]
    oi = await collect_oi(oi_symbols)
    facts = collect_day(events, now=now, window_sec=window, oi=oi,
                        prices=market["prices"], flows=market["flows"],
                        oi_top=ctx.oi_top, price_top=ctx.price_top)
    facts["symbols"] = symbols
    return facts


async def ai_narratives(facts: dict) -> Dict[str, str]:
    """Рассказ на RU и EN; без ИИ — шаблонный текст из данных."""
    out: Dict[str, str] = {}
    for lang in ("ru", "en"):
        text = ""
        if ctx.ai_fn is not None:
            try:
                text = str(await ctx.ai_fn(facts, lang) or "").strip()
            except Exception as e:
                log.warning("дайджест: ИИ (%s) не справился: %s", lang, e)
                text = ""
        if len(text) < NARRATIVE_MIN:
            text = fallback_narrative(facts, lang)
        out[lang] = text
    return out


async def build_digest(now: Optional[float] = None, window: Optional[int] = None,
                       save: bool = True, ai: bool = True,
                       force_day: str = "") -> dict:
    """Собрать выпуск: факты + рассказ + запись в архив."""
    now = float(now if now is not None else time.time())
    facts = await build_facts(now=now, window=window)
    day = force_day or day_key(now)
    rec = {
        "id": day,
        "day": day,
        "created": now,
        "updated": now,
        "window_h": facts.get("window_h") or 24,
        "facts": facts,
        "ai": {},
        "published": {},
    }
    if ai:
        rec["ai"] = await ai_narratives(facts)
    assign_cover(rec, day)
    if save and isinstance(ctx.store, DigestStore):
        prev = ctx.store.get(day)
        if prev:
            # пересборка того же дня: сохраняем историю публикаций и первый сбор
            rec["published"] = prev.get("published") or {}
            rec["created"] = prev.get("created") or now
            rec["versions"] = int(prev.get("versions") or 1) + 1
            if not ai and prev.get("ai"):
                rec["ai"] = prev.get("ai") or {}
            # обложка дня уже выбрана — пересборка её не меняет, иначе картинка
            # на сайте и в канале разъезжались бы от каждого перезапуска сборки
            if not rec.get("photo") and (prev.get("photo") or {}):
                rec["photo"] = prev["photo"]
        ctx.store.save(rec)
        rec = ctx.store.get(day) or rec
    ctx.last = {"day": day, "at": now}
    return rec


def cover_variant(day: str) -> int:
    """Номер варианта обложки из даты: у комплекта картинок свой порядок."""
    try:
        return int(str(day or "").replace("-", "")[-2:] or 0)
    except (TypeError, ValueError):
        return 0


def assign_cover(rec: dict, day: str = "") -> dict:
    """Выбрать обложку выпуска и запомнить её в записи.

    Одно фото на выпуск: то же самое уходит и в Telegram, и на страницу
    ``/digest`` — сайт не остаётся без картинки, а канал не повторяет вчерашнюю.
    Фото выбирается «по кругу без повторов» (``channel_digest``), поэтому
    функция вызывается один раз на день: если обложка уже выбрана и файл на
    месте, она не меняется.
    """
    rec = rec if isinstance(rec, dict) else {}
    day = day or str(rec.get("day") or "")
    have = rec.get("photo") or {}
    path = str(have.get("path") or "")
    if path and os.path.isfile(path):
        return have
    if ctx.photo_store is None:
        return {}
    try:
        from channel_digest import ensure_digest_cover
        cover = ensure_digest_cover(ctx.photo_store, variant=cover_variant(day))
    except Exception as e:                    # noqa: BLE001
        log.debug("дайджест: обложка не выбралась: %s", e)
        return {}
    if cover:
        rec["photo"] = cover
    return cover


def public_photo(rec: dict) -> Optional[dict]:
    """Обложка для сайта: относительный адрес, имя файла и откуда он взялся.

    Админские фото лежат вне static (в data/channel), поэтому отдаём их через
    ``/api/digest/cover?day=…``: так страница показывает ровно ту картинку,
    которая ушла в канал, и превью ссылки строится на неё же.
    """
    photo = (rec or {}).get("photo") or {}
    path = str(photo.get("path") or "")
    day = str((rec or {}).get("day") or (rec or {}).get("id") or "")
    if not path or not os.path.isfile(path) or not day:
        return None
    out = {"url": f"/api/digest/cover?day={day}", "name": photo.get("name") or "",
           "source": photo.get("source") or "admin", "day": day}
    if photo.get("id"):
        out["id"] = int(photo["id"])
    if photo.get("kind"):
        out["kind"] = str(photo["kind"])
    return out


def public_record(rec: dict, lang: str = "ru", with_article: bool = False) -> dict:
    """Запись архива без внутренностей: то, что рисует сайт."""
    facts = rec.get("facts") or {}
    out = {
        "id": rec.get("id"),
        "day": rec.get("day"),
        "day_label": day_label(rec.get("day"), lang),
        "created": rec.get("created"),
        "window_h": rec.get("window_h"),
        "brief": brief(rec, lang),
        "mood": (facts.get("mood") or {}).get("label", {}).get(lang)
                or (facts.get("mood") or {}).get("label", {}).get("ru") or "",
        "total_usd": facts.get("liq_total_usd"),
        "liq_count": facts.get("liq_count"),
        "published": bool((rec.get("published") or {}).get("ru")
                          or (rec.get("published") or {}).get("en")),
        "summary": (rec.get("ai") or {}).get(lang)
                   or (rec.get("ai") or {}).get("ru") or "",
        "post": render_post(rec, lang, ctx.public_url),
        "photo": public_photo(rec),
    }
    if with_article:
        out["article"] = render_article(rec, lang)
        out["facts"] = facts
    return out


# ---------------------------------------------------------------------------
#  Планировщик: каждый вечер ~22:00 МСК
# ---------------------------------------------------------------------------
class DigestScheduler:
    """Ждёт целевое время в часовом поясе сводок и запускает сбор.

    Время по умолчанию 22:00 ±10 минут, чтобы выпуски не выглядели
    роботом, который пишет ровно по секундам. Первый запуск в сутках —
    пропущенный (сервер лежал) считаем отдельно, не задним числом.
    """

    def __init__(self, hour: int = 22, minute: int = 0, jitter_min: int = 10,
                 tz: Optional[int] = None, enabled: bool = True,
                 late_sec: float = 3 * 3600):
        self.hour = int(hour) % 24
        self.minute = int(minute) % 60
        self.jitter_min = max(0, int(jitter_min))
        self.tz = tz
        self.enabled = bool(enabled)
        self.late_sec = float(late_sec)
        self.target_day = ""                 # за какой день уже собрали
        self.target_ts = 0.0
        self._target_day = ""
        self.last_error = ""

    def _tz(self) -> int:
        return tz_offset() if self.tz is None else int(self.tz)

    def apply(self, settings: Optional[dict]) -> None:
        """Применить настройки выпуска (их может менять админка сайта).

        Если время сдвинули, сбрасываем запомненную цель — иначе новая
        настройка заработала бы только со следующих суток.
        """
        s = settings or {}
        changed = False
        for key in ("hour", "minute", "jitter_min"):
            if key not in s or s[key] is None:
                continue
            try:
                val = int(s[key])
            except (TypeError, ValueError):
                continue
            if key == "hour":
                val = val % 24
            elif key == "minute":
                val = val % 60
            else:
                val = max(0, val)
            if val != getattr(self, key):
                setattr(self, key, val)
                changed = True
        if "enabled" in s and s["enabled"] is not None:
            val = bool(s["enabled"])
            if val != self.enabled:
                self.enabled = val
                changed = True
        if changed:
            self._target_day = ""
            self.target_ts = 0.0
            log.info("Дайджест: настройки обновлены — %02d:%02d, ±%d мин, %s",
                     self.hour, self.minute, self.jitter_min,
                     "включён" if self.enabled else "выключен")

    def _sync(self) -> None:
        """Раз в проверку подтягиваем значения из store (их меняет сайт)."""
        fn = ctx.settings_fn
        if fn is None:
            return
        try:
            self.apply(fn() or {})
        except Exception as e:                      # настройки не должны ломать цикл
            log.debug("Дайджест: настройки не прочитались: %s", e)

    def target_for(self, day: str, rnd: float = 0.0) -> float:
        """Момент публикации (epoch) для даты day в её часовом поясе."""
        import calendar
        tm = time.strptime(f"{day} {self.hour:02d}:{self.minute:02d}", "%Y-%m-%d %H:%M")
        # tz_offset() — сдвиг пояса в секундах, поэтому вычитаем как есть
        base = calendar.timegm(tm) - self._tz()
        if self.jitter_min:
            span = self.jitter_min * 60
            base += (float(rnd) - 0.5) * 2 * span
        return base

    def due(self, now: Optional[float] = None) -> Optional[str]:
        """Дата, которую пора выпускать (или None)."""
        self._sync()
        if not self.enabled:
            return None
        now = float(now if now is not None else time.time())
        day = day_key(now, self._tz())
        if day == self.target_day:
            return None
        left = self._seconds_until(day, now)
        if left > 0:
            return None
        if -left > self.late_sec:
            # сервер подняли сильно позже вечера: день не догоняем,
            # чтобы ночной выпуск не выглядел как «дайджест за сутки» утром
            log.info("Дайджест: %s пропущен (опоздали на %.0f мин)", day, -left / 60)
            self.mark(day)
            return None
        return day

    def _seconds_until(self, day: str, now: float) -> float:
        """Сколько осталось до вечернего времени; считаем один раз в сутки."""
        if not self.target_ts or self._target_day != day:
            import random
            self._target_day = day
            self.target_ts = self.target_for(day, random.random())
        return self.target_ts - now

    def mark(self, day: str) -> None:
        self.target_day = day
        self._target_day = day

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "hour": self.hour,
            "minute": self.minute,
            "jitter_min": self.jitter_min,
            "tz": self._tz(),
            "tz_hours": round(self._tz() / 3600.0, 2),
            "target_day": self.target_day,
            "next_target": self.target_ts,
            "last": ctx.last,
            "error": self.last_error,
            "env_locked": dict(ctx.env_locked or {}),
            "archive": len(ctx.store.list()) if isinstance(ctx.store, DigestStore) else 0,
            "retry_at": float(ctx.retry_at or 0),
            "retry_note": ctx.retry_note,
        }


RETRY_SEC = 900.0        # повтор после сбоя: не чаще, чем раз в 15 минут


def day_index(rec: dict, lang: str = "ru") -> dict:
    """Строка календаря выпусков: дата, подпись, статус.

    Лёгкая запись без поста и статьи: архив отдаёт её на каждый выпуск (их
    сотни), и рендерить текст ради кнопки в календаре не нужно.
    """
    facts = rec.get("facts") or {}
    return {
        "day": rec.get("day"),
        "label": day_label(rec.get("day"), lang),
        "published": bool((rec.get("published") or {}).get("ru")
                          or (rec.get("published") or {}).get("en")),
        "total_usd": facts.get("liq_total_usd"),
        "liq_count": facts.get("liq_count"),
    }


def pub_state(value) -> Tuple[bool, str]:
    """Привести запись о публикации к паре (ушло, ошибка).

    В архиве лежат две формы: результат отправки ``[ok, err]`` и отметка
    ``DigestStore.mark_published`` — ``{"ok": …, "at": …, "chat": …}``.
    """
    if isinstance(value, dict):
        return (bool(value.get("ok")),
                str(value.get("error") or value.get("err") or "").strip())
    if isinstance(value, (list, tuple)):
        ok = bool(value[0]) if value else False
        err = str(value[1] or "") if len(value) > 1 else ""
        return ok, err.strip()
    return bool(value), ""


def publish_done(rec: Optional[dict]) -> bool:
    """Ушёл ли выпуск — или его надо повторить.

    Повторяем в двух случаях: выпуск пропущен (пустой день: история может
    восстановиться) и ни один канал не принял пост (не привязан канал, Telegram
    отклонил). Черновик админу (режим контроля публикации) считается
    отправленным: он ждёт кнопку «Опубликовать», и повторять его не нужно.
    """
    if not rec or rec.get("skipped"):
        return False
    pub = rec.get("published") or {}
    if not pub:
        return False
    sent = retry = draft = False
    for v in pub.values():
        ok, err = pub_state(v)
        if ok:
            sent = True
        elif isinstance(v, dict):
            # отметка архива: в канал не ушло (черновик в архиве не отмечают)
            retry = True
        elif err:
            # результат отправки с ошибкой: канал не привязан, Telegram отказал
            retry = True
        else:
            # результат отправки без ошибки = черновик админу, ждёт «Опубликовать»
            draft = True
    if sent:
        return True
    return not retry and draft


async def scheduler_loop(sched: DigestScheduler, check_sec: float = 60.0) -> None:
    """Фоновый цикл: раз в минуту смотрим, не пора ли выпускать дайджест."""
    log.info("Дайджест: планировщик запущен (%02d:%02d МСК, ±%d мин, %s)",
             sched.hour, sched.minute, sched.jitter_min,
             "включён" if sched.enabled else "выключен")
    while True:
        try:
            now = time.time()
            day = sched.due(now)
            if day and not ctx.busy and now >= float(ctx.retry_at or 0):
                ctx.busy = True
                try:
                    rec = await publish_digest(now=now, langs=("ru", "en"), force=True,
                                               reason="schedule")
                    if publish_done(rec):
                        # выпуск ушёл (или ждёт кнопки «Опубликовать» у админа)
                        sched.mark(day)
                        ctx.retry_at = 0.0
                        ctx.retry_note = ""
                    else:
                        # пустой день или каналы не приняли пост: попробуем ещё
                        # раз, но не чаще, чем раз в RETRY_SEC — сборка выпуска
                        # тянет ИИ и свечи, долбить ими каждую минуту нельзя
                        ctx.retry_at = now + RETRY_SEC
                        ctx.retry_note = ((rec or {}).get("skipped")
                                          or _publish_error(rec)
                                          or "выпуск не ушёл")
                        log.info("Дайджест %s: %s — повтор через %d мин",
                                 day, ctx.retry_note, int(RETRY_SEC // 60))
                finally:
                    ctx.busy = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            sched.last_error = f"{type(e).__name__}: {e}"
            log.warning("Дайджест: планировщик споткнулся: %s", e)
        await asyncio.sleep(check_sec)


def _publish_error(rec: Optional[dict]) -> str:
    """Первая ошибка публикации — для лога и повторов."""
    for v in ((rec or {}).get("published") or {}).values():
        err = pub_state(v)[1]
        if err:
            return err
    return ""


def empty_day_reason(facts: Optional[dict]) -> str:
    """Почему выпуск нельзя публиковать: за сутки не собрано ни одной ликвидации.

    Так бывает, когда сервер перезапустили, а дисковая история за сутки не
    восстановилась: в памяти ноль событий, и пост выходит с «$0 · 0 ликвидаций»
    и без единой крупной ликвидации. Такой выпуск хуже, чем пропуск: вместо
    публикации сервис говорит админу, что проверить.
    """
    facts = facts or {}
    return ("за сутки не собрано ни одной ликвидации — похоже, история не "
            "восстановилась после перезапуска (в /api/health смотрите "
            "liquidations_in_memory); выпуск не отправлен")


async def publish_digest(now: Optional[float] = None, langs=("ru", "en"),
                         force: bool = False, reason: str = "manual",
                         window: Optional[int] = None,
                         publish: bool = True) -> Optional[dict]:
    """Собрать выпуск и опубликовать его в каналах (если есть чем)."""
    rec = await build_digest(now=now, window=window, save=True, ai=True)
    facts = rec.get("facts") or {}
    log.info("Дайджест за %s собран (%s): %s ликвидаций на %s",
             rec.get("day"), reason, facts.get("liq_count"),
             facts.get("liq_total_usd"))
    if float(facts.get("liq_total_usd") or 0) <= 0 and int(facts.get("liq_count") or 0) <= 0:
        text = empty_day_reason(facts)
        rec["skipped"] = "no_liquidations"
        rec["published"] = {str(lang): [False, text] for lang in (langs or ("ru",))}
        log.warning("Дайджест %s: %s", rec.get("day"), text)
        return rec
    if publish and ctx.publish_fn is not None:
        try:
            result = await ctx.publish_fn(rec, list(langs), bool(force))
            for lang, res in (result or {}).items():
                ok = bool(res[0]) if isinstance(res, (list, tuple)) else bool(res)
                ctx.store.mark_published(rec.get("id"), lang, ok=ok)
            log.info("Дайджест %s опубликован: %s", rec.get("day"), result)
        except Exception as e:
            log.warning("Дайджест: публикация не удалась: %s", e)
    return rec


# ---------------------------------------------------------------------------
#  Маршруты
# ---------------------------------------------------------------------------
def register_digest_routes(app) -> None:
    router = APIRouter()

    @router.get("/digest")
    async def page_digest(request: Request, day: str = ""):
        """Страница архива. Если открыт конкретный выпуск — превью ссылки
        строится на его обложку: в мессенджере и в выдаче видно фото дня,
        а не общий логотип."""
        if not ctx.page_ok:
            return JSONResponse({"ok": False, "error": "off"}, status_code=404)
        lang = seo_pages.detect_lang(request)
        items = ctx.store.list()
        rec = ctx.store.get(day) if day else (items[0] if items else None)
        photo = public_photo(rec) if rec else None
        og_image = ""
        if photo:
            og_image = (ctx.public_url or seo_pages.SITE_URL).rstrip("/") + photo["url"]
        return seo_pages.render(
            "digest.html", lang, "/digest",
            extra_head=seo_pages.jsonld("digest", lang, image=og_image),
            og_image=og_image,
        )

    @router.get("/api/digest")
    async def api_list(lang: str = "ru", limit: int = 12):
        """Архив выпусков: свежие — полностью, все даты — лёгким индексом.

        ``items`` уходят в ленту свежих выпусков (их немного), ``days`` — в
        календарь: по нему видно, за какие даты выпуск есть, и можно открыть
        любой старый, не заваливая страницу списком.
        """
        all_recs = ctx.store.list()
        items = [public_record(r, lang) for r in all_recs[:max(1, limit)]]
        return {
            "ok": True,
            "items": items,
            "days": [day_index(r, lang) for r in all_recs],
            "count": len(all_recs),
            "keep": DEFAULT_KEEP,
            "now": day_key(time.time()),
            "tz_hours": round(tz_offset() / 3600.0, 2),
            "schedule": {"hour": 22, "minute": 0, "jitter_min": 10,
                         "tz_hours": round(tz_offset() / 3600.0, 2)},
        }

    @router.get("/api/digest/today")
    async def api_today(lang: str = "ru"):
        items = ctx.store.list()
        if not items:
            return {"ok": True, "item": None}
        return {"ok": True, "item": public_record(items[0], lang, with_article=True)}

    @router.get("/api/digest/cover")
    async def api_cover(day: str = ""):
        """Картинка-обложка выпуска: её показывает страница /digest.

        Свои фото админка хранит вне static (в data/channel), поэтому отдаём
        их здесь. Кэш на сутки: обложка выбранного дня не меняется.
        """
        items = ctx.store.list()
        rec = ctx.store.get(day) if day else (items[0] if items else None)
        path = str(((rec or {}).get("photo") or {}).get("path") or "")
        if not path or not os.path.isfile(path):
            return JSONResponse({"ok": False, "error": "no_cover"}, status_code=404)
        ext = os.path.splitext(path)[1].lower()
        media = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "image/jpeg")
        return FileResponse(path, media_type=media,
                            headers={"Cache-Control": "public, max-age=86400"})

    @router.get("/api/digest/status")
    async def api_status():
        sched = getattr(app.state, "digest_scheduler", None)
        return {"ok": True, "busy": ctx.busy,
                "schedule": sched.status() if sched else None,
                "store_error": ctx.store.error}

    @router.get("/api/digest/{day}")
    async def api_day(day: str, lang: str = "ru"):
        rec = ctx.store.get(day)
        if rec is None:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return {"ok": True, "item": public_record(rec, lang, with_article=True)}

    @router.post("/api/digest/settings")
    async def api_settings(request: Request,
                           body: Optional[dict] = Body(default=None)):
        """Время вечернего выпуска и авто-публикация — правки из админки сайта.

        Значения из окружения имеют приоритет: если сервер задал час явно,
        поменять его с сайта нельзя, и об этом честно сообщаем.
        """
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        locked = dict(ctx.env_locked or {})
        sched = getattr(app.state, "digest_scheduler", None)
        new_values: dict = {}
        for key, lo, hi in (("hour", 0, 23), ("minute", 0, 59),
                            ("jitter_min", 0, 120)):
            if key in locked or key not in body or body.get(key) in (None, ""):
                continue
            try:
                val = int(body[key])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "bad_value", "key": key},
                                    status_code=400)
            new_values[key] = max(lo, min(hi, val))
        if "enabled" in body and "enabled" not in locked:
            new_values["enabled"] = bool(body["enabled"])
        if new_values and ctx.set_setting_fn is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        for key, val in new_values.items():
            name = f"digest_{key}"
            try:
                ctx.set_setting_fn(name,
                                   "1" if val is True else ("0" if val is False else val),
                                   user.get("id"))
            except TypeError:                       # store без actor_id
                ctx.set_setting_fn(name, "1" if val is True else
                                   ("0" if val is False else val))
        if sched is not None and new_values:
            sched.apply(new_values)
        if locked:
            names = ", ".join(sorted(set(locked.values())))
            note = ("Сохранено. С сайта не меняется: " + names +
                    " — это значение задано на сервере переменной окружения.")
        else:
            note = "Время выпуска сохранено."
        return {"ok": True, "saved": new_values, "note": note,
                "locked": locked,
                "schedule": sched.status() if sched is not None else None}

    def _admin(request: Request):
        from web_account import current_user
        user = current_user(request)
        if not user:
            return None, JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        if not user.get("is_admin"):
            return None, JSONResponse({"ok": False, "error": "admin"}, status_code=403)
        return user, None

    @router.post("/api/digest/run")
    async def api_run(request: Request, body: Optional[dict] = Body(default=None)):
        user, err = _admin(request)
        if err:
            return err
        body = body or {}
        langs = body.get("langs") or ["ru", "en"]
        if isinstance(langs, str):
            langs = [x for x in langs.replace(",", " ").split() if x]
        langs = [x for x in langs if x in ("ru", "en")] or ["ru", "en"]
        if ctx.busy:
            return JSONResponse({"ok": False, "error": "busy"}, status_code=409)
        ctx.busy = True
        try:
            rec = await publish_digest(langs=langs,
                                       force=bool(body.get("force", True)),
                                       reason=f"admin:{user.get('id')}",
                                       publish=bool(body.get("publish", True)),
                                       window=body.get("window") or None)
        except Exception as e:
            log.warning("Дайджест вручную: %s", e)
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"},
                                status_code=500)
        finally:
            ctx.busy = False
        return {"ok": True, "item": public_record(rec, "ru", with_article=True),
                "published": rec.get("published") or {},
                "en_preview": render_post(rec, "en", ctx.public_url)}

    app.include_router(router)
