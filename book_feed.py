"""📖 Стакан: стены лимитных ордеров.

Публичный L2-стакан Binance / Bybit / OKX / Gate (USDT-перпы) опрашивается раз
в POLL_SEC только для монет, за которыми кто-то следит: открытый график или
подписка «Стакан: стены» в кабинете. Стаканы бирж агрегируются в ценовые
корзины; корзина (или слипшиеся соседние) с суммарным объёмом выше порога —
это «стена»: крупный resting-ордер, который висит в стакане.

Чего публичный L2 НЕ даёт (и никогда не давал без спота трейдера):
  • идентификаторов и числа ордеров в уровне — вместо «N ордеров» пишем
    «N уровней» и биржи, где уровень виден;
  • истории — биржи отдают только текущий снимок. Поэтому время жизни стены
    («появилась → исчезла») выводим сами: заводим событие при первом
    обнаружении, закрываем после GONE_AFTER пропущенных опросов подряд.
    История живёт ровно с момента запуска опросчика (шейрды на диске).

Стены живут в памяти (снимок для графика) и дублируются в ежедневные JSONL
шейрды data/book_walls/YYYY-MM-DD.jsonl — события open/upd/close. При старте
хвост шейрдов подгружается, лента истории не обнуляется на рестарте.
"""

import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime, timezone

import aiohttp

from market_feed import BINANCE_REST, BYBIT_REST, OKX_REST, GATE_REST, _get_json, base_of

log = logging.getLogger("book")

# --- Наблюдаемая зона и пороги ---------------------------------------------
POLL_SEC = float(os.getenv("LIQSCOPE_BOOK_POLL_SEC", "2.0"))
DEPTH_LIMIT = 150            # уровней на сторону с биржи (L2-«хвосты» не нужны)
SPAN_REL = 0.02              # смотрим ±2% от mid: дальше — мусор дальних уровней
BUCKET_REL = 0.00035         # цена корзины ≈ 0.035% от mid (BTC 63k → ~$22)
WALL_MIN_USD = float(os.getenv("LIQSCOPE_BOOK_MIN_USD", "150000"))
WALL_REL_MULT = 6.0          # порог не ниже 6×медианы корзин — «выдавленность»
GONE_AFTER = 5               # пропущенных опросов подряд — стена ушла
HISTORY_TTL_DAYS = 7         # сколько дней ленты хранить на диске
SNAPSHOT_TTL = 4             # сек. — чаще не отдаваем историю (легкий дедуп)
MAX_SYMBOLS = 16             # потолок одновременно опрашиваемых монет


# --- Утилиты ------------------------------------------------------------------

def utc_day(ts=None):
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


def depth_url(exchange, symbol):
    """URL стакана биржи для канон-символа BTC_USDT."""
    b = symbol.replace("_", "").upper()
    base = base_of(symbol)
    if exchange == "binance":
        return f"{BINANCE_REST}/fapi/v1/depth?symbol={b}&limit={min(DEPTH_LIMIT, 1000)}"
    if exchange == "bybit":
        return (f"{BYBIT_REST}/v5/market/orderbook?category=linear"
                f"&symbol={b}&limit={min(DEPTH_LIMIT, 500)}")
    if exchange == "okx":
        return f"{OKX_REST}/api/v5/market/books?instId={base}-USDT-SWAP&sz={min(DEPTH_LIMIT, 400)}"
    if exchange == "gate":
        return f"{GATE_REST}/order_book?contract={symbol.upper()}&last={min(DEPTH_LIMIT, 1000)}"
    raise ValueError(f"unknown exchange {exchange}")


def parse_depth(exchange, data, specs):
    """Ответ биржи → (bids[(px, usdt)], asks[(px, usdt)]) по убыванию/возрастанию цены."""
    if exchange == "binance":
        raw_b, raw_a = data["bids"], data["asks"]
        rows = [(float(p), float(q) * float(p)) for p, q in raw_b], \
               [(float(p), float(q) * float(p)) for p, q in raw_a]
    elif exchange == "bybit":
        res = data.get("result") or {}
        raw_b, raw_a = res.get("bids") or [], res.get("asks") or []
        rows = [(float(p), float(q) * float(p)) for p, q in raw_b], \
               [(float(p), float(q) * float(p)) for p, q in raw_a]
    elif exchange == "okx":
        item = (data.get("data") or [{}])[0]
        ct = float((specs.get("okx") or {}).get("ct_val") or 0.0)
        if ct <= 0:      # без спецификации контракта объёмы переврать нельзя
            return None
        rows = ([(float(p), float(s) * ct * float(p)) for p, s, *_ in item.get("bids") or []],
                [(float(p), float(s) * ct * float(p)) for p, s, *_ in item.get("asks") or []])
    elif exchange == "gate":
        mult = float((specs.get("gate") or {}).get("quanto") or 0.0)
        if mult <= 0:
            return None
        rows = ([(float(o["p"]), float(o["s"]) * mult * float(o["p"])) for o in data.get("bids") or []],
                [(float(o["p"]), float(o["s"]) * mult * float(o["p"])) for o in data.get("asks") or []])
    else:
        raise ValueError(exchange)
    bids = sorted([r for r in rows[0] if r[1] > 0], key=lambda x: -x[0])
    asks = sorted([r for r in rows[1] if r[1] > 0], key=lambda x: x[0])
    return bids[:DEPTH_LIMIT], asks[:DEPTH_LIMIT]


def aggregate_depths(depths, bucket_rel=BUCKET_REL, span_rel=SPAN_REL):
    """Слиток стаканов → корзины. depths: {exch: (bids, asks)}.

    Возвращает None, если ни один стакан не дал обеих сторон. Корзина —
    {lo, hi, usdt, exchs}; bids по убыванию цены, asks по возрастанию.
    """
    best_bid = None
    best_ask = None
    for bids, asks in depths.values():
        if bids and (best_bid is None or bids[0][0] > best_bid):
            best_bid = bids[0][0]
        if asks and (best_ask is None or asks[0][0] < best_ask):
            best_ask = asks[0][0]
    if not best_bid or not best_ask:
        return None
    mid = (best_bid + best_ask) / 2.0
    step = max(mid * bucket_rel, 1e-12)
    lo_lim, hi_lim = mid * (1 - span_rel), mid * (1 + span_rel)
    out = {"mid": mid, "best_bid": best_bid, "best_ask": best_ask,
           "spread_bps": round((best_ask - best_bid) / mid * 1e4, 2), "bids": [], "asks": []}

    def bucketize(side_rows, is_bid):
        acc = {}
        for exch, (bids, asks) in depths.items():
            rows = bids if is_bid else asks
            for px, usdt in rows:
                if is_bid and px > mid:
                    continue
                if (not is_bid) and px < mid:
                    continue
                if px < lo_lim or px > hi_lim or usdt <= 0:
                    continue
                k = math.floor(px / step)
                b = acc.setdefault(k, {"usdt": 0.0, "exchs": set()})
                b["usdt"] += usdt
                b["exchs"].add(exch)
        items = []
        for k in sorted(acc, reverse=is_bid):
            items.append({"lo": k * step, "hi": (k + 1) * step,
                          "usdt": round(acc[k]["usdt"], 2),
                          "exchs": sorted(acc[k]["exchs"])})
        return items

    out["bids"] = bucketize(depths, True)
    out["asks"] = bucketize(depths, False)
    return out


def _median(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0)


def detect_walls(agg, min_usd=WALL_MIN_USD, rel_mult=WALL_REL_MULT):
    """Корзины → список стен. Стена = слипшиеся соседние корзины с суммой ≥ порога.

    Порог: max(min_usd, rel_mult × медиана корзин стороны) — на тощих монетах
    не плодим «стены» из мусора, на жирных — ловим именно выбивающиеся уровни.
    """
    walls = []
    for side in ("bids", "asks"):
        buckets = agg.get(side) or []
        if not buckets:
            continue
        thr = max(min_usd, rel_mult * _median([b["usdt"] for b in buckets]))
        run = []
        for b in buckets + [None]:
            if b is not None and b["usdt"] >= thr:
                run.append(b)
                continue
            if run:
                exchs = sorted({e for bb in run for e in bb["exchs"]})
                peak = max(run, key=lambda bb: bb["usdt"])
                walls.append({
                    "side": "bid" if side == "bids" else "ask",
                    "lo": round(min(bb["lo"] for bb in run), 8),
                    "hi": round(max(bb["hi"] for bb in run), 8),
                    "px": round((peak["lo"] + peak["hi"]) / 2.0, 8),
                    "usdt": round(sum(bb["usdt"] for bb in run), 2),
                    "levels": len(run),
                    "exchs": exchs,
                })
            run = [b] if (b is not None and b["usdt"] >= thr) else []
    return walls


# --- Конфиг подписки в кабинете ------------------------------------------------

def normalize_book_cfg(raw):
    """Конфиг услуги «book»: {enabled, symbols, min_usd, side, notify}."""
    raw = raw if isinstance(raw, dict) else {}
    syms = raw.get("symbols")
    if not isinstance(syms, list):
        syms = []
    out = {
        "enabled": bool(raw.get("enabled", True)),
        "symbols": [str(s).strip().upper().replace("-", "_") for s in syms if str(s).strip()][:8],
        "min_usd": min(max(float(raw.get("min_usd") or WALL_MIN_USD), 50_000), 5_000_000),
        "side": raw.get("side") if raw.get("side") in ("bid", "ask", "both") else "both",
        "notify": bool(raw.get("notify", True)),
    }
    if not out["symbols"]:
        out["symbols"] = ["BTC_USDT", "ETH_USDT"]
    return out


def format_wall_html(wall, lang="ru"):
    """Текст TG-сообщения про новую стену (числа и символы — безопасны)."""
    side = "Bid" if wall.get("side") == "bid" else "Ask"
    money = _fmt_usd(wall.get("usdt") or wall.get("peak") or 0)
    if lang == "en":
        head = f"📖 Wall on {wall['sym']} — {side} {money}"
        body = (f"Price {wall['lo']:g}–{wall['hi']:g}\n"
                f"Seen on: {', '.join(wall.get('exchs') or []) or '—'}")
    else:
        head = f"📖 Стена на {wall['sym']} — {side} {money}"
        body = (f"Цены {wall['lo']:g}–{wall['hi']:g}\n"
                f"Видна на: {', '.join(wall.get('exchs') or []) or '—'}")
    return f"{head}\n{body}"


def _fmt_usd(v):
    v = abs(float(v or 0))
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


# --- Корм для демо: стены там, где биржи не поднимешь -------------------------

def demo_depths(mid, t, rng=None):
    """Синтетический стакан вокруг mid для LIQSCOPE_DEMO: 2-3 стены,
    которые «дышат» по синусоиде и периодически исчезают — вся механика
    жизненного цикла (появление/рост/уход) видна на графике."""
    import random
    rnd = rng or random.Random(int(t // 7))
    step = mid * BUCKET_REL
    base_usdt = max(step * 50.0, 50.0)      # «шум» уровня: заметно меньше порога
    depths = {}
    for exch, k in (("binance", 1.0), ("bybit", 0.6), ("gate", 0.35)):
        bids, asks = [], []
        for i in range(1, 91):
            wob = 1.0 + 0.4 * math.sin(i * 0.9 + t * 0.003 + (0 if exch == "binance" else 1.3))
            bids.append((mid - i * step, base_usdt * max(wob, 0.2) * k))
            asks.append((mid + i * step, base_usdt * max(2 - wob, 0.2) * k))
        # стены: фиктивные сгустки, живут «волнами» — видны не всегда
        for w_i in range(3):
            phase = t * 0.00035 + w_i * 2.1 + (1 if exch == "binance" else 0)
            vis = math.sin(phase) > -0.15            # стена видна часть цикла
            if not vis:
                continue
            at = 8 + w_i * 22 + int(6 * math.sin(t * 0.001 + w_i))
            spike = 200000 + 150000 * abs(math.sin(t * 0.0005 + w_i * 1.7))
            rows = bids if w_i % 2 == 0 else asks
            px = mid + (at * step if w_i % 2 else -at * step)
            rows.insert(max(0, min(len(rows) - 1, at)), (px, spike * (0.4 + 0.6 * rnd.random()) * k))
        depths[exch] = (bids, asks)
    return depths


# --- Фид -----------------------------------------------------------------------

class BookFeed:
    """Опрос стаканов + детектор стен + лента событий. Один экземпляр на сервер."""

    EXCHANGES = ("binance", "bybit", "okx", "gate")

    def __init__(self, data_dir=None, *, min_usd=WALL_MIN_USD, poll_sec=POLL_SEC,
                 gone_after=GONE_AFTER, ttl_days=HISTORY_TTL_DAYS, sub_symbols_fn=None,
                 demo=False):
        self.data_dir = data_dir
        self.min_usd = float(min_usd)
        self.poll_sec = max(0.5, float(poll_sec))
        self.gone_after = int(gone_after)
        self.ttl_days = int(ttl_days)
        self.sub_symbols_fn = sub_symbols_fn or (lambda: [])
        self.demo = bool(demo)
        self.depths = {}        # sym -> {exch: (bids, asks)} — последний снимок
        self.agg = {}           # sym -> агрегат (для mid/spread в снапшоте)
        self.walls = {}         # sym -> {id: wall}
        self.status = {}        # exch -> {"ok","error","last_ts","calls"}
        self.mid = {}           # sym -> mid (для демо-прогулки цены)
        self._next_id = 1
        self._session = None
        self._specs = {}        # base -> {"okx": {...}, "gate": {...}}
        self._hist_cache = {}   # (sym, hours bucket) -> (ts, payload)
        self._demo_walk = {}    # sym -> {"px":..., "v":...}
        self._viewers = {}      # sym -> ts последнего запроса с графика (TTL 90 c)
        self._day = utc_day()
        if self.data_dir:
            os.makedirs(self.data_dir, exist_ok=True)
            self._load_tail()

    # — Symbols

    def note_view(self, sym):
        """График попросил снапшот/историю — держим монету в опросе."""
        sym = str(sym or "").strip().upper()
        if sym:
            self._viewers[sym] = time.time()

    def wanted_symbols(self):
        now = time.time()
        try:
            live = sorted(s for s, ts in self._viewers.items() if now - ts < 90)
        except Exception:
            live = []
        try:
            subs = sorted({str(s).strip().upper() for s in self.sub_symbols_fn() if s})
        except Exception:
            subs = []
        out = list(dict.fromkeys(live + subs))
        return out[:MAX_SYMBOLS]

    # — Poll loop

    async def run(self):
        # задачи сервера снимаются через cancel — цикл просто вечно опрашивает
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            self._session = session
            while True:
                t0 = time.monotonic()
                try:
                    await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("book poll failed")
                if self.demo:
                    try:
                        self.demo_tick()
                    except Exception:
                        log.exception("book demo tick failed")
                delay = max(0.2, self.poll_sec - (time.monotonic() - t0))
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise

    async def poll_once(self):
        syms = self.wanted_symbols()
        if not syms or self._session is None:
            return
        await asyncio.gather(*(self._poll_symbol(s) for s in syms))

    async def _fetch_spec(self, base):
        """ctVal для OKX и quanto для Gate (вUSDT-объёмы без них неверны)."""
        now = time.time()
        ent = self._specs.get(base)
        if ent and now - ent.get("ts", 0) < 86400:
            return ent
        ent = {"ts": now}
        try:
            data = await _get_json(self._session, f"{OKX_REST}/api/v5/public/instruments?instType=SWAP&instId={base}-USDT-SWAP")
            item = (data.get("data") or [{}])[0]
            if item.get("ctVal"):
                ent["okx"] = {"ct_val": float(item["ctVal"])}
        except Exception as exc:
            self._mark("okx", str(exc)[:160])
        try:
            data = await _get_json(self._session, f"{GATE_REST}/contracts/{base}_USDT")
            if data.get("quanto_multiplier"):
                ent["gate"] = {"quanto": float(data["quanto_multiplier"])}
        except Exception as exc:
            self._mark("gate", str(exc)[:160])
        self._specs[base] = ent
        return ent

    def _mark(self, exch, error=None):
        st = self.status.setdefault(exch, {"ok": False, "error": None, "last_ts": 0, "calls": 0})
        st["calls"] += 1
        if error is None:
            st["ok"] = True
            st["error"] = None
            st["last_ts"] = time.time()
        else:
            st["ok"] = False
            st["error"] = error

    async def _poll_symbol(self, sym):
        base = base_of(sym)
        specs = await self._fetch_spec(base) if base else {}
        results = await asyncio.gather(
            *(self._fetch_depth(exch, sym, specs) for exch in self.EXCHANGES),
            return_exceptions=True)
        depths = {}
        for exch, res in zip(self.EXCHANGES, results):
            if isinstance(res, tuple):
                depths[exch] = res
        if depths:
            self.depths[sym] = depths
            agg = aggregate_depths(depths)
            if agg:
                self.agg[sym] = agg
                fresh = detect_walls(agg, self.min_usd)
                self._merge(sym, fresh, time.time())
                return
        # биржи недоступны — хотя бы не давим историю: считаем пропуски
        self._merge(sym, [], time.time(), no_data=True)

    async def _fetch_depth(self, exch, sym, specs):
        url = depth_url(exch, sym)
        data = await _get_json(self._session, url, timeout=5.0)
        parsed = parse_depth(exch, data, specs)
        if not parsed or (not parsed[0] and not parsed[1]):
            raise ValueError(f"empty depth {exch}")
        self._mark(exch)
        return parsed

    # — Жизненный цикл стен

    def _merge(self, sym, fresh, now, no_data=False):
        store = self.walls.setdefault(sym, {})
        matched = set()
        for w in fresh:
            hit = None
            for wid, cur in store.items():
                if wid in matched or cur["side"] != w["side"] or cur["closed"]:
                    continue
                # пересечение ценовых полос — та же стена
                if w["lo"] <= cur["hi"] and w["hi"] >= cur["lo"]:
                    hit = (wid, cur)
                    break
            if hit:
                wid, cur = hit
                matched.add(wid)
                prev_usdt = cur["usdt"]
                # границы полосы — текущее наблюдение, а не исторический охват:
                # лимитка стоит на месте, «разъезжаться» ей некуда
                cur.update(lo=w["lo"], hi=w["hi"], usdt=w["usdt"], px=w["px"],
                           levels=max(cur["levels"], w["levels"]),
                           exchs=sorted(set(cur["exchs"]) | set(w["exchs"])),
                           last_seen=round(now, 3), miss=0)
                new_peak = w["usdt"] > cur["peak"]
                cur["peak"] = round(max(cur["peak"], w["usdt"]), 2)
                # В шейрд пишем редко: новый пик или смена размера >15% —
                # иначе файл рос бы на каждый тик опроса.
                if new_peak or abs(w["usdt"] - prev_usdt) > 0.15 * max(prev_usdt, 1.0):
                    self._append({"e": "upd", "id": wid, "sym": sym,
                                  "ts": cur["last_seen"], "usdt": cur["usdt"]})
            else:
                wid = self._next_id
                self._next_id += 1
                matched.add(wid)
                wall = dict(w, id=wid, sym=sym, opened=round(now, 3), last_seen=round(now, 3),
                            closed=None, peak=round(w["usdt"], 2), miss=0, live=True)
                store[wid] = wall
                self._append({"e": "open", "id": wid, "sym": sym, "ts": wall["opened"],
                              "side": wall["side"], "lo": wall["lo"], "hi": wall["hi"],
                              "px": wall["px"], "usdt": wall["usdt"], "levels": wall["levels"],
                              "exchs": wall["exchs"]})
        if no_data:
            return          # нет связи — не убиваем стены из-за таймаута
        for wid, cur in store.items():
            if wid in matched or cur["closed"]:
                continue
            cur["miss"] += 1
            if cur["miss"] >= self.gone_after:
                cur["closed"] = cur["last_seen"]
                cur["live"] = False
                self._append({"e": "close", "id": wid, "sym": sym, "ts": round(now, 3),
                              "gone_at": cur["closed"], "peak": cur["peak"]})
        # чистка: давно закрытые — вон (лента остаётся на диске)
        cut = now - self.ttl_days * 86400
        for wid in [w for w, cur in store.items() if (cur["closed"] or cur["opened"]) < cut]:
            store.pop(wid, None)

    # — Демо-режим: синтетика без сети ------------------------------------------

    def demo_tick(self, now=None):
        now = now or time.time()
        for sym in self.wanted_symbols():
            walk = self._demo_walk.setdefault(sym, {"px": 63000.0, "v": 0.0})
            walk["v"] = walk["v"] * 0.97 + (0.5 - (now % 1)) * 4.0
            walk["px"] = max(1000.0, walk["px"] + walk["v"])
            mid = walk["px"]
            depths = demo_depths(mid, now)
            agg = aggregate_depths(depths)
            if not agg:
                continue
            self.mid[sym] = mid
            self._merge(sym, detect_walls(agg, self.min_usd), now)
            for exch in self.EXCHANGES:
                self._mark(exch)

    # — Персистентность: ежедневные JSONL-шейрды ---------------------------------

    def _shard_path(self, day):
        return os.path.join(self.data_dir, f"{day}.jsonl")

    def _append(self, ev):
        if not self.data_dir:
            return
        try:
            day = utc_day(ev.get("ts"))
            if day != self._day:
                self._day = day
            with open(self._shard_path(day), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            log.warning("book shard write failed", exc_info=True)

    def _load_tail(self):
        """Поднимаем хвост ленты (несколько последних дней) — стены и история
        не обнуляются на рестарте процесса."""
        if not self.data_dir or not os.path.isdir(self.data_dir):
            return
        now = time.time()
        cut = now - self.ttl_days * 86400
        ids = {}
        try:
            files = sorted(f for f in os.listdir(self.data_dir) if f.endswith(".jsonl"))
        except OSError:
            return
        for fname in files[-self.ttl_days:]:
            try:
                with open(os.path.join(self.data_dir, fname), encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        self._replay(ev, ids, now, cut)
            except OSError:
                continue
        self._next_id = (max(ids) + 1) if ids else 1

    def _replay(self, ev, ids, now, cut):
        e = ev.get("e")
        if e == "open":
            wid = int(ev.get("id") or 0)
            if not wid:
                return
            store = self.walls.setdefault(ev.get("sym") or "", {})
            store[wid] = {"id": wid, "sym": ev.get("sym"), "side": ev.get("side", "bid"),
                          "lo": ev["lo"], "hi": ev["hi"], "px": ev["px"],
                          "usdt": ev.get("usdt") or 0.0, "peak": ev.get("usdt") or 0.0,
                          "levels": ev.get("levels") or 1, "exchs": ev.get("exchs") or [],
                          "opened": ev.get("ts") or now, "last_seen": ev.get("ts") or now,
                          "closed": None, "miss": 0, "live": True}
            ids[wid] = True
        elif e == "upd":
            cur = self.walls.get(ev.get("sym") or "", {}).get(ev.get("id"))
            if cur:
                cur["usdt"] = ev.get("usdt") or cur["usdt"]
                cur["peak"] = max(cur["peak"], cur["usdt"])
                cur["last_seen"] = ev.get("ts") or cur["last_seen"]
        elif e == "close":
            cur = self.walls.get(ev.get("sym") or "", {}).get(ev.get("id"))
            if cur is None:      # старые строки без sym — ищем по всем монетам
                for store in self.walls.values():
                    cur = store.get(ev.get("id"))
                    if cur:
                        break
            if cur and cur["live"]:
                cur["closed"] = ev.get("gone_at") or cur["last_seen"]
                cur["last_seen"] = cur["closed"]
                cur["live"] = False
        # стены, открытые до окна отсечки и до сих пор «живые» после простоя —
        # закрываем мягко: время без данных не считаем временем жизни стены
        for store in self.walls.values():
            for wid, cur in list(store.items()):
                if cur["live"] and cur["opened"] < cut and now - cur["last_seen"] > 600:
                    cur["closed"] = cur["last_seen"]
                    cur["live"] = False

    # — Отдача ---------------------------------------------------------------

    def _wall_out(self, w, now):
        return {"id": w["id"], "side": w["side"], "lo": w["lo"], "hi": w["hi"], "px": w["px"],
                "usdt": round(w["usdt"], 2), "peak": round(w["peak"], 2), "levels": w["levels"],
                "exchs": w["exchs"], "opened": w["opened"], "closed": w["closed"],
                "live": bool(w["live"]), "age_s": int(now - w["opened"])}

    def snapshot(self, sym, min_usd=0.0, now=None):
        """Живые стены монеты + параметры стакана — для графика (политка 4 сек)."""
        now = now or time.time()
        sym = (sym or "").upper()
        agg = self.agg.get(sym) or {}
        store = self.walls.get(sym, {})
        min_usd = max(float(min_usd or 0), 0)
        walls = [self._wall_out(w, now) for w in store.values()
                 if w["live"] and max(w["usdt"], w["peak"]) >= (min_usd or self.min_usd)]
        walls.sort(key=lambda w: -w["usdt"])
        return {"ok": True, "ts": round(now, 1), "symbol": sym,
                "mid": agg.get("mid"), "best_bid": agg.get("best_bid"),
                "best_ask": agg.get("best_ask"), "spread_bps": agg.get("spread_bps"),
                "min_usd": max(self.min_usd, min_usd), "walls": walls,
                "poll_sec": self.poll_sec, "status": self.status_summary()}

    def status_summary(self):
        mode = ("demo" if self.demo else "live") if self._session else "idle"
        out = {"mode": mode, "symbols": self.wanted_symbols()}
        for exch, st in self.status.items():
            if exch in ("mode",):
                continue
            out[exch] = {"ok": st.get("ok"), "last_ts": st.get("last_ts"),
                         "error": (st.get("error") or "")[:120] or None}
        return out

    def history(self, sym, hours=6.0, min_usd=0.0):
        """Стены монеты за окно (активные + закрывшиеся). Память уже содержит
        сыгранный с диска хвост — на диск не лезем (PoC: всё в памяти)."""
        now = time.time()
        sym = (sym or "").upper()
        since = now - max(0.25, float(hours)) * 3600
        min_usd = max(float(min_usd or 0), self.min_usd)
        key = (sym, round(float(hours) * 4), round(min_usd / 1000))
        cached = self._hist_cache.get(key)
        if cached and now - cached[0] < SNAPSHOT_TTL:
            return cached[1]
        bands = []
        for w in self.walls.get(sym, {}).values():
            end = w["closed"] or now
            if end < since or (w["peak"] or w["usdt"]) < min_usd:
                continue
            out = self._wall_out(w, now)
            out["dur_s"] = int(end - w["opened"])
            bands.append(out)
        bands.sort(key=lambda w: -w["opened"])
        payload = {"ok": True, "ts": round(now, 1), "symbol": sym, "since": since,
                   "hours": float(hours), "min_usd": min_usd, "count": len(bands),
                   "walls": bands[:400]}
        self._hist_cache[key] = (now, payload)
        return payload

    # — Уведомления кабинета ----------------------------------------------------

    def new_open_walls(self, symbols, min_usd, side, since_ts):
        """Стены, открытые позже since_ts (для TG-оповещений без спама)."""
        out = []
        want = {str(s).upper() for s in (symbols or [])}
        for sym, store in self.walls.items():
            if want and sym not in want:
                continue
            for w in store.values():
                if w["opened"] < since_ts:
                    continue
                if side in ("bid", "ask") and w["side"] != side:
                    continue
                if (w["peak"] or w["usdt"]) < float(min_usd):
                    continue
                d = self._wall_out(w, time.time())
                d["sym"] = sym
                out.append(d)
        out.sort(key=lambda w: w["opened"])
        return out[:20]


def register_book_routes(app, get_feed):
    """Маршруты стакана. get_feed() -> BookFeed | None (сервер может жить без фида)."""
    from fastapi import APIRouter, Query
    from fastapi.responses import JSONResponse

    router = APIRouter()

    @router.get("/api/book/snapshot")
    async def book_snapshot(symbol: str = Query("BTC_USDT"), min_usd: float = Query(0)):
        feed = get_feed()
        if feed is None:
            return JSONResponse({"ok": False, "error": "book feed is off"}, status_code=503)
        feed.note_view(symbol)       # опрос графика держит монету в опросе бирж
        return feed.snapshot(symbol, min_usd)

    @router.get("/api/book/walls")
    async def book_walls(symbol: str = Query("BTC_USDT"),
                         hours: float = Query(6.0), min_usd: float = Query(0)):
        feed = get_feed()
        if feed is None:
            return JSONResponse({"ok": False, "error": "book feed is off"}, status_code=503)
        feed.note_view(symbol)
        return feed.history(symbol, hours, min_usd)

    @router.get("/api/book/status")
    async def book_status():
        feed = get_feed()
        if feed is None:
            return JSONResponse({"ok": False, "error": "book feed is off"}, status_code=503)
        return {"ok": True, "wall_ids": sum(len(s) for s in feed.walls.values()),
                **feed.status_summary()}

    app.include_router(router)
