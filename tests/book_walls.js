/** 📖 Стакан: стены → кластеры у свечи + лента заявок в терминале.
 *
 *  Раньше стена рисовалась полосой через весь график (появилась → тянется до
 *  сих пор), и за пару часов холст зарастал «пеленой». Теперь принцип — как у
 *  плашек ликвидаций:
 *    • каждая стена привязана к СВОЕЙ свече (где её нашли) и живёт на ней,
 *      пока не уберётся или не исполнится; исчезла — плашка остаётся там же;
 *    • пересекающиеся коридоры одной свечи сливаются в зону и накапливаются
 *      (объём, уровни, число стен складываются);
 *    • ширина кластера — доля слота свечи: дальше тела не вылезает ни на
 *      одном ТФ;
 *    • ленты два конца: строка ленты «Заявки» подсвечивает кластер, наведение
 *      на кластер подсвечивает строку.
 *
 *  Запуск (демо-сервер на 127.0.0.1:8011 или обычный на :8000):
 *      NODE_PATH=/tmp/smoke/node_modules node tests/book_walls.js [url]
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const TF = 300;                               // минутки графика — ТФ 5 (дефолт)
// время подравниваем к сетке свечей: t0 кластеров обязан совпадать со свечами
const NOW = Math.floor(Date.now() / 1000 / TF) * TF;

const WALL = {                                  // живая bid, «накапливается» с WALL_M
  id: 101, side: "bid", lo: 49950, hi: 49972, px: 49961, usdt: 400000, peak: 650000,
  levels: 2, exchs: ["binance", "bybit"], opened: NOW - 36 * TF, closed: null,
  live: true, age_s: 36 * TF,
};
const WALL_M = {                                // та же свеча, коридор пересёкся
  id: 105, side: "bid", lo: 49960, hi: 49980, px: 49970, usdt: 300000, peak: 320000,
  levels: 1, exchs: ["binance"], opened: NOW - 36 * TF + 60, closed: null,
  live: true, age_s: 36 * TF - 60,
};
const WALL_ASK = {
  id: 102, side: "ask", lo: 50090, hi: 50112, px: 50101, usdt: 2600000, peak: 2600000,
  levels: 1, exchs: ["bybit"], opened: NOW - 12 * TF, closed: null,
  live: true, age_s: 12 * TF,
};
const WALL_OLD = {                              // ушла, другой час — свой кластер
  id: 103, side: "bid", lo: 49800, hi: 49822, px: 49811, usdt: 180000, peak: 220000,
  levels: 1, exchs: ["binance"], opened: NOW - 100 * TF, closed: NOW - 60 * TF,
  live: false, dur_s: 40 * TF,
};
const WALL_EATEN = {                            // «съедена»: остаток < 70% пика
  id: 106, side: "ask", lo: 50080, hi: 50100, px: 50090, usdt: 90000, peak: 200000,
  levels: 1, exchs: ["okx"], opened: NOW - 24 * TF, closed: NOW - 20 * TF,
  live: false, dur_s: 4 * TF,
};
const WALL_E2 = {                               // съеденная — в общую зону на -36TF
  id: 107, side: "bid", lo: 49965, hi: 49985, px: 49975, usdt: 90000, peak: 250000,
  levels: 1, exchs: ["bybit"], opened: NOW - 36 * TF + 120, closed: NOW - 30 * TF,
  live: false, dur_s: 6 * TF,
};
const WALL_G2 = {                               // ушедшая — тоже в ту зону
  id: 108, side: "bid", lo: 49940, hi: 49955, px: 49947, usdt: 160000, peak: 160000,
  levels: 1, exchs: ["okx"], opened: NOW - 36 * TF + 180, closed: NOW - 34 * TF,
  live: false, dur_s: 2 * TF,
};
const WALL_SMALL = {                            // ниже порога — не рисуется, не в ленте
  id: 104, side: "ask", lo: 50300, hi: 50320, px: 50310, usdt: 60000, peak: 60000,
  levels: 1, exchs: ["binance"], opened: NOW - 4 * TF, closed: null,
  live: true, age_s: 4 * TF,
};

function candles() {
  const out = [];
  for (let i = 0; i < 120; i++) {
    out.push({ time: NOW - 120 * TF + i * TF, open: 50000, high: 50150,
      low: 49850, close: 50000, volume: 120 });
  }
  return out;
}

function makeRecorder(log) {
  const st = { fillStyle: null, strokeStyle: null, font: null };
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "fillRect") return (x, y, w, h) => log.push({ op: "fill", style: st.fillStyle, x, y, w, h });
      if (prop === "strokeRect") return (x, y, w, h) => log.push({ op: "stroke", style: st.strokeStyle, x, y, w, h });
      if (prop === "fillText") return (txt) => log.push({ op: "text", style: st.fillStyle, font: st.font, txt: String(txt) });
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "fillStyle") return st.fillStyle;
      if (prop === "strokeStyle") return st.strokeStyle;
      if (prop === "font") return st.font;
      return typeof prop === "string" ? noop : undefined;
    },
    set(_t, prop, v) {
      if (prop === "fillStyle") st.fillStyle = v;
      else if (prop === "strokeStyle") st.strokeStyle = v;
      else if (prop === "font") st.font = v;
      return true;
    },
  });
}
function noopCtx() {
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));
  const log = [];
  const calls = { snap: 0, hist: 0 };

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      const rec = makeRecorder(log);
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () {
        return this.id === "cluster-canvas" ? rec : dummy;
      };
      win.WebSocket = function () { return { readyState: 3, send() {}, close() {} }; };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/klines") === 0) {
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles: candles() };
        } else if (u.indexOf("/api/book/snapshot") === 0) {
          calls.snap++;
          body = { ok: true, ts: NOW, symbol: "BTC_USDT", mid: 50000, spread_bps: 1.2,
                   min_usd: 150000, walls: [WALL, WALL_M, WALL_ASK, WALL_SMALL] };
        } else if (u.indexOf("/api/book/walls") === 0) {
          calls.hist++;
          body = { ok: true, ts: NOW, symbol: "BTC_USDT",
                   walls: [WALL, WALL_M, WALL_ASK, WALL_OLD, WALL_EATEN,
                           WALL_E2, WALL_G2] };
        } else if (u.indexOf("/api/stats") === 0) body = {};
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
      // слой доступен «прошлым» клиентам: dev-обход триала + включённый стакан
      try {
        win.localStorage.setItem("liqscope.devLayers", "1");
        win.localStorage.setItem("liqscope.bookEnabled", "1");
      } catch (e) { /* ignore */ }
    },
  });

  await new Promise((r) => setTimeout(r, 7500));
  const win = dom.window;
  const api = win.LiqScopeLiq;
  const book = api && api.bookState ? api.bookState() : null;
  const layers = api && api.layerState ? api.layerState() : {};

  check("кнопка 📖 есть и подсвечена", (() => {
    const el = win.document.getElementById("book-toggle");
    return el && el.classList.contains("active");
  })());
  check("слой включён (layerState.book)", layers.book === true, JSON.stringify(layers));
  check("снапшот получен (4 стены в т.ч. мелкая)", book && book.live === 4, JSON.stringify(book));
  check("история подтянута (hist=7)", book && book.hist === 7, JSON.stringify(book));
  check("poll-запросы шли на сервер", calls.snap >= 1 && calls.hist >= 1,
        JSON.stringify(calls));

  // ---------- 1. кластеры по свече вместо полос через весь график ----------
  const rows = api.bookRows();
  const uniq = new Set(rows.reduce((a, c) => a.concat(c.ids), []));
  check("кластеров ровно 4 (две свечи bid, две ask)", rows.length === 4,
        JSON.stringify(rows.map((r) => [r.time, r.side, r.usdt])));
  check("стены не задваиваются (live перетирает историю, дедуп по id)",
        uniq.size === 7 && !uniq.has(104), JSON.stringify([...uniq]));
  const mZone = rows.find((r) => r.time === NOW - 36 * TF && r.side === "bid");
  check("пересёкшиеся коридоры одной свечи слились (4 стены, 5 уровней)",
        mZone && mZone.count === 4 && mZone.levels === 5,
        JSON.stringify(mZone));
  check("объём зоны накопился (650K + 320K + 250K + 160K)",
        mZone && Math.abs(mZone.usdt - 1380000) < 1, JSON.stringify(mZone));
  check("коридор зоны расширился (49940–49985)",
        mZone && mZone.lo === 49940 && mZone.hi === 49985,
        JSON.stringify(mZone && [mZone.lo, mZone.hi]));
  check("живая стена живёт на СВОЕЙ свече, а не тянется до края",
        rows.every((r) => r.time % TF === 0) && mZone.live === true,
        JSON.stringify(rows.map((r) => r.time % TF)));
  const eaten = rows.find((r) => r.ids.indexOf(106) !== -1);
  check("«съеденная» стена помечена (остаток < 70% пика)",
        eaten && eaten.eaten === 1 && eaten.live === false, JSON.stringify(eaten));

  check("зона знает состав по статусам (USDT живых/съеденных/ушедших)",
        mZone && mZone.sums && Math.abs(mZone.sums.live - 970000) < 1 &&
        Math.abs(mZone.sums.eaten - 250000) < 1 && Math.abs(mZone.sums.gone - 160000) < 1,
        JSON.stringify(mZone && mZone.sums));
  const mHit = api.bookHits().find((h) => h.time === NOW - 36 * TF && h.sums);
  check("в плашке есть sums и для хита (модалка theirs)",
        mHit && mHit.sums && mHit.sums.live === 970000, JSON.stringify(mHit));

  // полосы сверяем по последнему кадру перерисовки (лог копится между кадрами);
  // в jsdom слот мелкий и ширины округлены до пикселя — точные доли задаёт
  // слой данных (sums выше), здесь проверяем сам факт трёх полос и порядок
  const frameMark = log.length;
  api.redraw();
  await new Promise((r) => setTimeout(r, 120));
  const frame = log.slice(frameMark);

  const hits = api.bookHits();
  const slot = Math.max(1, api.slotPx());
  check("плашки нарисованы (по числу кластеров)", hits.length === 4, hits.length);
  check("высота плашки минимум 12px — профиль влезает",
        hits.every((h) => h.h >= 12), JSON.stringify(hits.map((h) => h.h)));
  // внутри — трёхцветный профиль статусов (в mixed-зоне все три ряда)
  const profFills = frame.filter((e) => e.op === "fill" &&
        (e.style === "#4ade80" || e.style === "#fb7185" || e.style === "#94a3b8"));
  check("полосы профиля: зелёный/красный/серый рисуются",
        profFills.some((e) => e.style === "#4ade80") &&
        profFills.some((e) => e.style === "#fb7185") &&
        profFills.some((e) => e.style === "#94a3b8"),
        JSON.stringify(profFills.map((e) => e.style)));
  if (mHit) {
    const inZone = profFills.filter((e) =>
      e.x >= mHit.x - 1 && e.x + e.w <= mHit.x + mHit.w + 1 &&
      e.y >= mHit.y - 1 && e.y + e.h <= mHit.y + mHit.h + 1);
    check("полосы сидят строго внутри плашки (15% отступы не выплёскивают)",
          inZone.length === 3, JSON.stringify(inZone.map((e) => [e.style, Math.round(e.x), Math.round(e.w)])));
    const byStyle = {};
    inZone.forEach((e) => { byStyle[e.style] = Math.round(e.w); });
    // при 1px-строке «красный/серый» могут совпасть — сравнение нестрогое;
    // главный смысл: зелёная полоса длиннее и кратна ≈4 красным (70%/18%)
    check("длины полос по долям USDT (зелёная — самая длинная)",
          byStyle["#4ade80"] >= byStyle["#fb7185"] &&
          byStyle["#fb7185"] >= byStyle["#94a3b8"] &&
          byStyle["#4ade80"] >= 2 * byStyle["#94a3b8"],
          JSON.stringify(byStyle));
  }
  check("ширина — доля слота свечи: дальше тела не вылезает",
        hits.every((h) => h.w >= 3 && h.w <= Math.round(slot * 0.86) + 1 && h.w <= 96),
        JSON.stringify(hits.map((h) => [Math.round(h.x), h.w, slot])));
  const offBy = hits.map((h) => {
    const cx = api.timeToX(h.time);
    return (cx === null || cx === undefined || !isFinite(cx))
      ? 0 : Math.abs(h.x + h.w / 2 - cx);
  });
  check("центр плашки — на своей свече", offBy.every((d) => d <= 3), JSON.stringify(offBy));
  const fills = log.filter((e) => e.op === "fill");
  check("никаких длинных полос: все заливки шире максимум на слот",
        fills.filter((e) => e.style === "#67e8f9" || e.style === "#a78bfa")
             .every((e) => e.w <= 100),
        JSON.stringify(fills.map((e) => Math.round(e.w))));
  check("bid — циан, ask — фиолет (обе стороны)",
        fills.some((e) => e.style === "#67e8f9") && fills.some((e) => e.style === "#a78bfa"));
  check("обводка та же палитра", log.some((e) => e.op === "stroke" &&
        (e.style === "#22d3ee" || e.style === "#8b5cf6")));

  // ---------- 2. лента «📖 Заявки» ----------
  check("вкладка «Заявки» есть", Boolean(win.document.getElementById("feed-tab-book")));
  api.setFeed("book");
  await new Promise((r) => setTimeout(r, 400));
  check("лента переключилась", api.feedTab() === "book", api.feedTab());
  const tape = api.feedRowsDom();
  check("лента заявок: 7 строк (стена ниже порога отсеяна)", tape.length === 7,
        JSON.stringify(tape.map((r) => r.wallId)));
  check("строки знают свою стену (data-wall-id)",
        ["101", "102", "103", "105", "106"].every((id) =>
          tape.some((r) => r.wallId === id && r.key === "book_" + id)));
  // строки локали не фиксируем: в jsdom-гарнисаге язык плавает (en/ru — тот же
  // дрейф, что в cabinet_services), поэтому сверяем статусы по обоим языкам
  check("значки сторон и статус: жива / съедена",
        tape.every((r) => /BID|ASK/.test(r.txt)) &&
        /жива|live/.test((tape.find((r) => r.wallId === "101") || {}).txt || "") &&
        /съедена|eaten/.test((tape.find((r) => r.wallId === "106") || {}).txt || ""),
        JSON.stringify(tape.map((r) => r.txt.slice(0, 60))));
  check("«съеденная» строка показывает остаток (→ $90K)",
        (tape.find((r) => r.wallId === "106") || {}).txt.indexOf("→") !== -1);
  check("живые строки помечены полоской (feed-row-live)",
        ["101", "102", "105"].every((id) =>
          (tape.find((r) => r.wallId === id) || {}).cls.indexOf("feed-row-live") !== -1));

  // наведение строки ленты → кластер на графике (hoverHitKey), обратно — подсветка строки
  const tr102 = win.document.querySelector('#feed-tbody tr[data-wall-id="102"]');
  check("строка ленты в DOM", Boolean(tr102));
  if (tr102) {
    tr102.dispatchEvent(new win.MouseEvent("mouseenter"));
    await new Promise((r) => setTimeout(r, 120));
    const t2 = api.feedRowsDom();
    const hit102 = (t2.find((r) => r.wallId === "102") || {}).cls || "";
    const dim101 = (t2.find((r) => r.wallId === "101") || {}).cls || "";
    check("наведение в ленте: своя строка подсвечена, остальные притухли",
          hit102.indexOf("feed-hit") !== -1 && dim101.indexOf("feed-dim") !== -1,
          hit102 + " | " + dim101);
    const h102 = (api.bookHits().find((h) => h.ids.indexOf(102) !== -1)) || null;
    check("кластер ленты найден на графике (тот же id)", Boolean(h102),
          JSON.stringify(api.bookHits().map((h) => h.ids)));
    tr102.dispatchEvent(new win.MouseEvent("mouseleave"));
    await new Promise((r) => setTimeout(r, 60));
    const t3 = api.feedRowsDom();
    check("уход курсора снимает подсветку",
          t3.every((r) => r.cls.indexOf("feed-hit") === -1));
    // клик по строке: закрепляет кластер и открывает окно зоны с составом
    tr102.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 120));
    const modal = win.document.getElementById("detail-modal");
    const mtxt = modal ? modal.textContent.replace(/\s+/g, " ") : "";
    check("клик в ленте открывает окно кластера (зона + состав стены)",
          modal && !modal.classList.contains("hidden") &&
          /Кластер заявок|Order cluster/.test(mtxt) &&
          /Зона цены|Price zone/.test(mtxt) && /\$2,600,000/.test(mtxt),
          (mtxt || "").slice(0, 140));
    const t4 = api.feedRowsDom();
    check("закреплённая строка остаётся подсвеченной",
          ((t4.find((r) => r.wallId === "102") || {}).cls || "").indexOf("feed-hit") !== -1);
    api.setHover(null);
  }

  // ---------- 3. порог объёма заявок: фильтр графика и ленты ----------
  const thBtn = win.document.getElementById("min-usd-btn");
  api.setBookMin(500000);
  await new Promise((r) => setTimeout(r, 250));
  const rows5 = api.bookRows();
  check("порог $500K: на графике только зоны ≥ порога",
        rows5.length === 2 && rows5.every((r) => r.usdt >= 500000),
        JSON.stringify(rows5.map((r) => [r.side, r.usdt])));
  check("под зоной остаётся лишь стена 650K (партнёр 320K отсечён)",
        rows5.some((r) => r.side === "bid" && r.ids.length === 1 && r.ids[0] === 101),
        JSON.stringify(rows5.map((r) => r.ids)));
  const tape5 = api.feedRowsDom();
  check("порог режет и ленту заявок: остались 101 и 102",
        tape5.length === 2 && tape5.every((r) => r.wallId === "101" || r.wallId === "102"),
        JSON.stringify(tape5.map((r) => r.wallId)));
  check("активный порог виден на кнопке фильтра",
        thBtn && /500/.test(thBtn.textContent) && /Стены|Walls/.test(thBtn.textContent),
        thBtn ? thBtn.textContent : "нет кнопки");
  api.setBookMin(0);
  await new Promise((r) => setTimeout(r, 250));
  check("сброс порога вернул все кластеры и ленту",
        api.bookRows().length === 4 && api.feedRowsDom().length === 7);

  // ---------- 4. лента заявок не зависит от кнопки слоя ----------
  const btn = win.document.getElementById("book-toggle");
  if (btn) btn.click();                      // слой ОФ, вкладка «Заявки» открыта
  await new Promise((r) => setTimeout(r, 150));
  const after = api.bookState();
  check("слой выключен", after.on === false, JSON.stringify(after));
  check("плашки с графика сняты", api.bookHits().length === 0);
  check("лента не погасла вместе со слоем: данные и строки живы",
        after.live === 4 && after.hist === 7 && api.feedRowsDom().length === 7,
        JSON.stringify({ live: after.live, hist: after.hist }));
  const snapDuringTape = calls.snap;
  await new Promise((r) => setTimeout(r, 4500));
  check("полит идёт и при выключенном слое (ради ленты)",
        calls.snap > snapDuringTape, snapDuringTape + " -> " + calls.snap);
  // ни слоя, ни ленты — мониторим отпускание монеты серверу
  api.setFeed("liq");
  await new Promise((r) => setTimeout(r, 150));
  const snapIdle = calls.snap;
  await new Promise((r) => setTimeout(r, 4500));
  check("нет слоя и нет вкладки — сервер не дёргается",
        calls.snap === snapIdle, snapIdle + " -> " + calls.snap);
  check("никто не смотрит — данные сброшены",
        api.bookState().live === 0 && api.bookRows().length === 0);
  if (btn) btn.click();                      // слой ОПЯТЬ вкл — всё оживает
  await new Promise((r) => setTimeout(r, 400));
  check("слой вернули — кластеры и лента готовы рисоваться",
        api.bookRows().length === 4 && api.bookHits().length === 4,
        JSON.stringify({ rows: api.bookRows().length, hits: api.bookHits().length }));

  const real = errors.filter((e) => e.indexOf("Could not load script") === -1);
  check("нет js-ошибок", real.length === 0, real.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
