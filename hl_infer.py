"""Вывод ликвидаций Hyperliquid из публичной ленты сделок (без ключей).

Зачем. У HL нет публичного канала ликвидаций: в `trades` сделка приходит набором
`coin/side/px/sz/hash/time/tid/users`, и признака `liquidation` в нём нет (он
есть только у `WsFill` в приватных `userFills`). Боевой замер 14.09.2026: 9911
сделок за 600 с при 24/24 подтверждённых подписках — ноль меток. Но сами
ликвидации при этом в ленте ЕСТЬ, просто замаскированы под обычные сделки: по
механике биржи (docs → Trading → Liquidations) позицию ниже maintenance margin
закрывает маркет-ордер, выставленный САМОЙ биржей ОТ ИМЕНИ ликвидируемого
аккаунта. Значит тейкер сделки = жертва, а `users` даёт её адрес.

Отсюда двухстадийная схема, которая и превращает «угадывание» в факт:

  1. кандидат — всплеск сделок одного адреса по одной монете в одном
     направлении; набирает очки за нотионал, серию, исполнение хуже марки и
     «некруглый» размер (остаток позиции, а не лот);
  2. подтверждение — бесплатный `POST /info {"type":"userFillsByTime",
     "user":<тейкер>,...}`: у филa ликвидации есть объект `liquidation`
     (`liquidatedUser/markPx/method`). Есть объект — событие в ленту, нет —
     кандидат отброшен как обычный маркет-ордер.

Что метод принципиально не видит: ADL и backstop-передачу позиции в liquidator
vault — они не создают сделок в стакане. Это потолок любого «вычислителя», у
индексаторов тот же.

Конфигурация (env): LIQSCOPE_HL_INFER, LIQSCOPE_HL_INFER_MIN_USD,
LIQSCOPE_HL_INFER_WINDOW_SEC, LIQSCOPE_HL_INFER_MIN_SCORE,
LIQSCOPE_HL_INFER_MAX_CONFIRM_PER_MIN, LIQSCOPE_HL_INFER_CONFIRM_TIMEOUT_SEC.
См. README → «Откуда всё-таки брать ликвидации HL: три пути».
"""

import asyncio
import logging
import os
import statistics
import time
from collections import deque
from typing import Dict, List, Optional

import aiohttp

log = logging.getLogger("liqscope.hl_infer")

# Включено по умолчанию: без этого Hyperliquid в ленте — пустая биржа, а путь
# не требует ни ключей, ни отдельного сокета (только редкие /info).
HL_INFER_ENABLED = os.getenv("LIQSCOPE_HL_INFER", "1").strip().lower() not in (
    "0", "off", "no", "false")
# Серия меньше этого — не кандидат. Порог по смыслу: маркет-ордер на $2k — это
# почти всегда частник, а не движок ликвидации.
HL_INFER_MIN_USD = float(os.getenv("LIQSCOPE_HL_INFER_MIN_USD", "25000"))
# Окно склейки всплеска: ликвидация исполняется пачкой филлов в одном-двух
# блоках, а частичная (20% от позиции) повторяется не чаще раза в 30 с.
HL_INFER_WINDOW_SEC = float(os.getenv("LIQSCOPE_HL_INFER_WINDOW_SEC", "2.0"))
# Минимум очков, чтобы кандидат пошёл на подтверждение.
HL_INFER_MIN_SCORE = float(os.getenv("LIQSCOPE_HL_INFER_MIN_SCORE", "3.0"))
# Сколько подтверждений в минуту. Это защита бюджета /info (лимит на IP), а не
# жадность: без клампа каскад из сотен ликвидаций положил бы REST-пул терминала.
HL_INFER_MAX_CONFIRM = int(os.getenv("LIQSCOPE_HL_INFER_MAX_CONFIRM_PER_MIN", "12"))
# Сколько ждать ответ /info. Висящий запрос не должен тормозить читателя сокета:
# подтверждения живут в своей задаче и своей очереди.
HL_INFER_CONFIRM_TIMEOUT = float(os.getenv("LIQSCOPE_HL_INFER_CONFIRM_TIMEOUT_SEC", "8"))
# Глубина истории цен монеты для «марки» (референса проскальзывания).
HL_INFER_REF_SAMPLES = 400
# Всплески старше этого возраста не подтверждаем (защита от среза истории на
# переподключении). Передаётся из market_feed (HL_FRESH_SEC).
HL_INFER_LOG_SEC = float(os.getenv("LIQSCOPE_HL_INFER_LOG_SEC", "60"))
# Очередь кандидатов: переполнение = отбросить oldest, но не тормозить сокет.
HL_INFER_QUEUE = int(os.getenv("LIQSCOPE_HL_INFER_QUEUE", "64"))


def taker_of(trade: dict):
    """Кто в сделке тейкер и какую позицию он закрывал.

    В `trades` side — сторона тейкера: 'A' = продавал → выносили ЛОНГ,
    'B' = покупал → выносили ШОРТ. users = [покупатель, продавец], поэтому
    тейкер — users[1] при 'A' и users[0] при 'B'.
    """
    side = str(trade.get("side") or "").upper()
    users = trade.get("users") or []
    if not isinstance(users, list) or len(users) < 2:
        return None, None
    if side == "A":
        return users[1], "LONG"
    if side == "B":
        return users[0], "SHORT"
    return None, None


def _is_odd_size(sz: float) -> bool:
    """Размер похож на остаток позиции, а не на круглый лот (4.885 vs 5.000)."""
    if sz <= 0:
        return False
    txt = repr(float(sz))
    frac = txt.split(".")[-1] if "." in txt else ""
    return len(frac.rstrip("0")) >= 3


def score_burst(rows: List[dict], ref_px: Optional[float], min_usd: float):
    """(score, причины, нотионал) для всплеска сделок одного адреса."""
    usd = sum(r["px"] * r["sz"] for r in rows)
    n = len(rows)
    span = rows[-1]["ts"] - rows[0]["ts"]
    score, why = 0.0, []
    if usd >= min_usd:
        score += 2.0
        why.append(f"{usd:,.0f}$")
    else:
        return 0.0, ["мелко"], usd
    if n >= 2:
        score += 1.5
        why.append(f"{n} филa за {span:.2f}с")
    if len({r["side"] for r in rows}) == 1:
        score += 0.5
        why.append("в одну сторону")
    qty = sum(r["sz"] for r in rows)
    vwap = usd / qty if qty else 0.0
    if ref_px and vwap:
        slip = (ref_px - vwap) / ref_px
        pos = rows[0]["position"]
        # лонг выбивают НИЖЕ марки, шорт — ВЫШЕ; обычный тейкер платит около
        # спреда, а ликвидация исполняется через худшую цену
        bad = slip > 0.0008 if pos == "LONG" else slip < -0.0008
        if bad:
            score += 2.0
            why.append(f"хуже марки на {abs(slip) * 100:.3f}%")
    if any(_is_odd_size(r["sz"]) for r in rows):
        score += 0.4
        why.append("размер = остаток позиции")
    return score, why, usd


def find_liquidation(payload, tid=None, since_ms=None, taker=None):
    """Фил с объектом `liquidation` из ответа /info userFillsByTime.

    Приоритет — точное совпадение по tid. Если он не совпал (одна ликвидация —
    это много филлов, а ответ конечен), берём новейший фил с меткой внутри окна
    запроса. Окно обязательно: иначе за ликвидацию можно принять событие
    часовой давности из того же аккаунта.
    """
    rows = payload if isinstance(payload, list) else (payload or {}).get("fills") or []
    if not isinstance(rows, list):
        return None
    cands = []
    for f in rows:
        if not isinstance(f, dict) or "liquidation" not in f:
            continue
        ts = float(f.get("time") or 0)
        if since_ms and ts and ts < since_ms:
            continue
        if tid is not None and f.get("tid") == tid:
            return _liq_info(f, taker, "tid")
        cands.append(f)
    if not cands:
        return None
    return _liq_info(max(cands, key=lambda x: float(x.get("time") or 0)),
                     taker, "окно")


def _liq_info(fill: dict, taker, matched: str) -> dict:
    liq = fill.get("liquidation") or {}
    return {"liquidatedUser": str(liq.get("liquidatedUser") or taker or ""),
            "markPx": liq.get("markPx"), "method": liq.get("method"),
            "tid": fill.get("tid"), "dir": fill.get("dir"),
            "startPosition": fill.get("startPosition"), "matched": matched}


class HlLiquidationInferer:
    """Лента сделок → кандидаты → подтверждение по адресу → событие терминала.

    Разделен на две половины намеренно: `observe()` вызывается из читателя
    сокета и обязан быть копеечным (словари, deque, никаких await), а вся
    сетевая часть живёт в `worker()`. Иначе висящий /info посадил бы WS — ровно
    та ошибка, из-за которой HL когда-то «вешал» весь фид ликвидаций.
    """

    def __init__(self, coin_map: Dict[str, str], session, on_event,
                 *, hl_rest: str, max_age: float = 120.0,
                 min_usd: float = HL_INFER_MIN_USD,
                 window: float = HL_INFER_WINDOW_SEC,
                 min_score: float = HL_INFER_MIN_SCORE,
                 max_confirm_per_min: int = HL_INFER_MAX_CONFIRM,
                 confirm_timeout: float = HL_INFER_CONFIRM_TIMEOUT,
                 queue_size: int = HL_INFER_QUEUE):
        self.coin_map = coin_map
        self.session = session
        self.on_event = on_event            # async (event) -> None
        self.hl_rest = hl_rest
        self.max_age = max_age
        self.min_usd = min_usd
        self.window = window
        self.min_score = min_score
        self.max_confirm_per_min = max(1, max_confirm_per_min)
        self.confirm_timeout = confirm_timeout
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.bursts: Dict[tuple, List[dict]] = {}
        self.prices: Dict[str, deque] = {}  # монета -> история цен (для марки)
        self.marked: set = set()            # tid, уже помеченные биржей
        self.seen: set = set()              # (монета, адрес, слот времени)
        self.stats = {"trades": 0, "candidates": 0, "confirmed": 0,
                      "rejected": 0, "budget_skipped": 0, "queue_skipped": 0,
                      "info_calls": 0, "info_errors": 0, "usd": 0.0,
                      "latency_ms": 0.0, "latency_n": 0}
        self._spent_at = 0.0
        self._spent = 0
        self._log_at = 0.0
        self._stop = asyncio.Event()

    # -- горячий путь: вызывается из цикла чтения сокета ---------------------
    def observe(self, payload: dict) -> None:
        """Кадры `trades` → всплески. Ничего не ждём и не кидаем наружу."""
        if not isinstance(payload, dict) or payload.get("channel") != "trades":
            return
        data = payload.get("data")
        if not isinstance(data, list):
            return
        now = time.time()
        for t in data:
            if not isinstance(t, dict):
                continue
            self.stats["trades"] += 1
            tid = t.get("tid")
            if "liquidation" in t:
                # биржа сама что-то пометила — подтверждение не нужно, и
                # двойного события не будет
                if tid is not None:
                    self.marked.add(tid)
                continue
            coin = str(t.get("coin") or "")
            try:
                px = float(t.get("px") or 0)
                sz = float(t.get("sz") or 0)
                ts = float(t.get("time") or 0) / 1000.0
            except (TypeError, ValueError):
                continue
            if px <= 0 or sz <= 0:
                continue
            hist = self.prices.setdefault(coin, deque(maxlen=HL_INFER_REF_SAMPLES))
            hist.append((ts, px))
            taker, position = taker_of(t)
            if not taker or not coin:
                continue
            self._fold(coin, taker, {"coin": coin, "px": px, "sz": sz,
                                     "ts": ts or now, "seen": now,
                                     "side": str(t.get("side") or "").upper(),
                                     "position": position, "tid": tid})

    def _fold(self, coin: str, taker: str, row: dict) -> None:
        key = (coin, taker)
        rows = self.bursts.get(key)
        if rows is None:
            self.bursts[key] = [row]
            return
        # длинный разрыв = новый всплеск: старый закрываем и оцениваем
        if row["seen"] - rows[-1]["seen"] > self.window:
            closed = [r for r in rows if row["seen"] - r["seen"] <= self.window * 4]
            self.bursts[key] = [row]
            if closed:
                self._propose(coin, taker, closed)
        else:
            rows.append(row)

    def _propose(self, coin: str, taker: str, rows: List[dict]) -> None:
        now = time.time()
        if self.max_age and rows[-1]["ts"] and now - rows[-1]["ts"] > self.max_age:
            return                       # срез истории на переподключении
        if any(r["tid"] in self.marked for r in rows if r["tid"] is not None):
            return
        ref = self._ref_price(coin, rows[0]["ts"])
        score, why, usd = score_burst(rows, ref, self.min_usd)
        if score < self.min_score:
            return
        slot = int(rows[0]["ts"] // 5)
        tag = (coin, taker, slot)
        if tag in self.seen:
            return
        self.seen.add(tag)
        if len(self.seen) > 8192:
            self.seen.clear()
            self.seen.add(tag)
        self.stats["candidates"] += 1
        burst = {"coin": coin, "taker": taker, "rows": rows, "usd": usd,
                 "why": why, "score": score, "found_at": now}
        try:
            self.queue.put_nowait(burst)
        except asyncio.QueueFull:
            # сокет тормозить нельзя: жертвуем самым старым кандидатом
            self.stats["queue_skipped"] += 1
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(burst)
            except Exception:
                pass

    def _ref_price(self, coin: str, before_ts: float) -> Optional[float]:
        """Медиана цен монеты ДО всплеска — наше приближение марки."""
        hist = self.prices.get(coin)
        if not hist:
            return None
        lo = before_ts - 30.0
        vals = [px for ts, px in hist if lo <= ts <= before_ts]
        return statistics.median(vals) if len(vals) >= 5 else None

    def coin_name(self, coin: str) -> str:
        """Имя монеты для людей: 'kPEPE' -> 'kPEPE_USDT' (через карту слушателя)."""
        cmap = self.coin_map() if callable(self.coin_map) else self.coin_map
        return cmap.get(coin) or cmap.get(str(coin).upper()) or f"{str(coin).upper()}_USDT"

    def sweep(self) -> None:
        """Досмотреть всплески, которые замолчали (иначе хвост окна потерян)."""
        now = time.time()
        for key, rows in list(self.bursts.items()):
            if rows and now - rows[-1]["seen"] > self.window + 1.0:
                self.bursts[key] = []
                self._propose(key[0], key[1], rows)

    # -- холодный путь: подтверждения ---------------------------------------
    def _budget_ok(self) -> bool:
        minute = int(time.time() // 60)
        if minute != int(self._spent_at // 60):
            self._spent_at = time.time()
            self._spent = 0
        if self._spent >= self.max_confirm_per_min:
            self.stats["budget_skipped"] += 1
            return False
        self._spent += 1
        return True

    async def worker(self) -> None:
        """Единственный потребитель очереди. Живёт своей задачей."""
        while not self._stop.is_set():
            try:
                burst = await self.queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._confirm(burst)
            except asyncio.CancelledError:
                raise
            except Exception as e:            # одна проверка не роняет воркер
                log.debug("[hyperliquid/infer] подтверждение упало: %s", e)
            self.maybe_log()

    async def _confirm(self, burst: dict) -> None:
        rows = burst["rows"]
        if not self._budget_ok():
            return
        t0 = rows[0]["ts"] - 10.0
        t1 = time.time() + 2.0
        body = {"type": "userFillsByTime", "user": burst["taker"],
                "startTime": int(t0 * 1000), "endTime": int(t1 * 1000)}
        self.stats["info_calls"] += 1
        try:
            async with self.session.post(
                    f"{self.hl_rest}/info", json=body,
                    timeout=aiohttp.ClientTimeout(total=self.confirm_timeout)) as r:
                if r.status != 200:
                    self.stats["info_errors"] += 1
                    log.debug("[hyperliquid/infer] /info HTTP %s", r.status)
                    return
                data = await r.json(content_type=None)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.stats["info_errors"] += 1
            log.debug("[hyperliquid/infer] /info недоступен: %s", e)
            return
        hit = find_liquidation(data, tid=rows[-1].get("tid"), taker=burst["taker"],
                              since_ms=int(t0 * 1000))
        if not hit:
            self.stats["rejected"] += 1
            return
        qty = sum(r["sz"] for r in rows)
        # coin_map — словарь или getter: слушатель пересобирает карту при
        # обновлении universe, и держать ссылку на старый словарь значило бы
        # терять монеты, появившиеся после старта
        cmap = self.coin_map() if callable(self.coin_map) else self.coin_map
        symbol = (cmap.get(burst["coin"]) or cmap.get(str(burst["coin"]).upper())
                  or f"{str(burst['coin']).upper()}_USDT")
        ev = {"symbol": symbol, "side": rows[0]["position"],
              "price": burst["usd"] / qty if qty else rows[-1]["px"],
              "qty": qty, "ts": rows[-1]["ts"], "usd": burst["usd"],
              "kind": "tape", "liquidation": hit}
        self.stats["confirmed"] += 1
        self.stats["usd"] += burst["usd"]
        lat = (time.time() - burst["found_at"]) * 1000.0
        n = self.stats["latency_n"]
        self.stats["latency_ms"] = (self.stats["latency_ms"] * n + lat) / (n + 1)
        self.stats["latency_n"] = n + 1
        log.info("[hyperliquid/infer] %s %s %.0f$ — ликвидация подтверждена "
                 "по адресу (method=%s, markPx=%s, matched=%s); признаки: %s",
                 symbol, rows[0]["position"], burst["usd"], hit.get("method"),
                 hit.get("markPx"), hit.get("matched"), ", ".join(burst["why"]))
        await self.on_event(ev)

    def maybe_log(self) -> None:
        now = time.monotonic()
        if now - self._log_at < HL_INFER_LOG_SEC:
            return
        self._log_at = now
        s = self.stats
        log.info("[hyperliquid/infer] сделок %d, кандидатов %d, подтверждено %d "
                 "(%.1f$), отклонено %d, вне бюджета %d, /info: %d вызовов, "
                 "%d ошибок, латентность %.0f мс",
                 s["trades"], s["candidates"], s["confirmed"], s["usd"],
                 s["rejected"], s["budget_skipped"], s["info_calls"],
                 s["info_errors"], s["latency_ms"])

    async def aclose(self) -> None:
        self._stop.set()
        try:
            self.queue.put_nowait(None)       # разбудить висящего worker'а
        except Exception:
            pass

    def describe(self) -> dict:
        s = self.stats
        return {
            "hl_infer_trades": s["trades"],
            "hl_infer_candidates": s["candidates"],
            "hl_infer_confirmed": s["confirmed"],
            "hl_infer_rejected": s["rejected"],
            "hl_infer_usd": round(s["usd"]),
            "hl_infer_info_calls": s["info_calls"],
            "hl_infer_info_errors": s["info_errors"],
            "hl_infer_budget_skipped": s["budget_skipped"],
            "hl_infer_queue_skipped": s["queue_skipped"],
            "hl_infer_latency_ms": round(s["latency_ms"]),
            "hl_infer_budget_per_min": self.max_confirm_per_min,
        }

