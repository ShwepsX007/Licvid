/** 📖 Стакан: стены лимиток — слой, запросы и полосы на графике.
 *
 *  Проверка на живом сервере (демо): терминал загружается с включённым слоем,
 *  тянет /api/book/snapshot и /api/book/walls, рисует на cluster-canvas
 *  полосы: bid цианом, ask фиолетом; живая стена тянется до правого края,
 *  закрытая — обрезана по времени исчезновения; мелкие стены ниже порога
 *  не рисуем; внутри полосы — объём и число уровней.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
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

const NOW = Math.floor(Date.now() / 1000);
const TF = 300;                       // минутки графика — ТФ 5 (дефолт state.timeframe)
const WALL = {
  id: 101, side: "bid", lo: 49950, hi: 49972, px: 49961, usdt: 400000, peak: 650000,
  levels: 2, exchs: ["binance", "bybit"], opened: NOW - 36 * TF, closed: null,
  live: true, age_s: 36 * TF,
};
const WALL_ASK = Object.assign({}, WALL, {
  id: 102, side: "ask", lo: 50090, hi: 50112, usdt: 2600000, peak: 2600000,
  opened: NOW - 12 * TF, age_s: 12 * TF, levels: 1,
});
const WALL_OLD = Object.assign({}, WALL, {
  id: 103, side: "bid", lo: 49800, hi: 49822, usdt: 180000, peak: 220000,
  levels: 1, opened: NOW - 100 * TF, closed: NOW - 60 * TF, live: false, dur_s: 40 * TF,
});
const WALL_SMALL = Object.assign({}, WALL, {
  id: 104, side: "ask", lo: 50300, hi: 50320, usdt: 60000, peak: 60000,
  levels: 1, opened: NOW - 40, age_s: 40, live: true,
});

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
                   min_usd: 150000, walls: [WALL, WALL_ASK, WALL_SMALL] };
        } else if (u.indexOf("/api/book/walls") === 0) {
          calls.hist++;
          body = { ok: true, ts: NOW, symbol: "BTC_USDT", walls: [WALL, WALL_ASK, WALL_OLD] };
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
  check("снапшот получен (3 стены в состоянии)",
        book && book.live === 3, JSON.stringify(book));
  check("история подтянута (hist=3)", book && book.hist === 3, JSON.stringify(book));
  check("poll-запросы шли на сервер", calls.snap >= 1 && calls.hist >= 1,
        JSON.stringify(calls));

  const fills = log.filter((e) => e.op === "fill");
  const bidRects = fills.filter((e) => e.style === "#67e8f9");
  const askRects = fills.filter((e) => e.style === "#a78bfa");
  check("bid-стена: циановая полоса", bidRects.length >= 1, bidRects.length);
  check("ask-стена: фиолетовая полоса", askRects.length >= 1, askRects.length);
  check("обводка та же палитра", log.some((e) => e.op === "stroke" &&
        (e.style === "#22d3ee" || e.style === "#8b5cf6")));
  // живые стены тянутся до правого края (900px), закрытая — нет
  check("живая стена до правого края", bidRects.some((e) => e.x + e.w >= 897),
        JSON.stringify(bidRects.slice(0, 4)));
  const closedRect = fills.find((e) => e.style === "#67e8f9" && e.x + e.w < 890 && e.w < 890);
  check("закрытая стена обрезана по времени", Boolean(closedRect),
        JSON.stringify(fills.map((e) => [Math.round(e.x), Math.round(e.w)])));
  const texts = log.filter((e) => e.op === "text").map((e) => e.txt);
  check("цифра объёма внутри полосы ($650K · 2 — пик)",
        texts.some((t) => t.indexOf("$650K") === 0 && t.indexOf("· 2") > 0),
        JSON.stringify(texts.slice(0, 8)));
  check("стена ниже порога (60K) не подписана",
        !texts.some((t) => t.indexOf("$60K") === 0), JSON.stringify(texts));

  // переключатель выключает слой и запросы замирают
  const btn = win.document.getElementById("book-toggle");
  if (btn) btn.click();
  await new Promise((r) => setTimeout(r, 100));
  const after = api.bookState();
  check("клик выключил слой и сбросил данные",
        after.on === false && after.live === 0 && after.hist === 0, JSON.stringify(after));
  const snapBefore = calls.snap;
  await new Promise((r) => setTimeout(r, 4500));
  check("выключенный слой не дёргает сервер", calls.snap === snapBefore,
        snapBefore + " -> " + calls.snap);

  const real = errors.filter((e) => e.indexOf("Could not load script") === -1);
  check("нет js-ошибок", real.length === 0, real.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
