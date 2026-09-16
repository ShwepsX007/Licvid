/** Ярко-белая подсветка кластера при наведении строки ленты.
 *
 *  В терминале наведение на событие в ленте (и нажатие на графике) включает
 *  кластер: он должен читаться сразу — белый контур, ореол и светлая вуаль
 *  поверх цвета плашки. В jsdom мышью не походишь, поэтому состояние
 *  наведения ставим через LiqScopeLiq.setHover(key) — тот же путь, которым
 *  пользуется highlightFromFeed.
 *
 *  Проверяем: без наведения белого нет; у подсвеченного кластера есть ореол
 *  (тень) и резкий белый контур по габаритам именно этой плашки; при
 *  переносе наведения подсветка переезжает на другой кластер.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      NODE_PATH=/tmp/smoke/node_modules node tests/liq_hover.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const NOW = Math.floor(Date.now() / 1000);
const TF = 300;
const T3 = NOW - (NOW % TF), T2 = T3 - TF, T1 = T2 - TF;
const candles = [
  { time: T1, open: 50000, high: 53000, low: 49000, close: 52000, volume: 10, cvd: 50 },
  { time: T2, open: 52000, high: 52500, low: 51000, close: 51000, volume: 10, cvd: -50 },
  { time: T3, open: 51000, high: 51100, low: 50900, close: 51005, volume: 5, cvd: 5 },
];
const liqs = [
  { id: 1, symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 400000, price: 51900, timestamp: T1 },
  { id: 2, symbol: "BTC_USDT", exchange: "OKX", side: "SELL",
    usd: 300000, price: 51500, timestamp: T2 },
  { id: 3, symbol: "BTC_USDT", exchange: "OKX", side: "SELL",
    usd: 250000, price: 51050, timestamp: T3 },
];

// Запоминаем заполнения и обводки: у обводки важны цвет, толщина и тень.
function recorder(log) {
  const st = { fillStyle: "", strokeStyle: "", lineWidth: 1, shadowBlur: 0,
               shadowColor: "", font: "10px sans-serif", path: [] };
  const noop = () => {};
  function fontPx() {
    const m = /([\d.]+)px/.exec(String(st.font || ""));
    return m ? Number(m[1]) : 10;
  }
  function box() {
    const xs = st.path.map((p) => p[1]), ys = st.path.map((p) => p[2]);
    if (!xs.length) return null;
    return { x: Math.min.apply(null, xs), y: Math.min.apply(null, ys),
             w: Math.max.apply(null, xs) - Math.min.apply(null, xs),
             h: Math.max.apply(null, ys) - Math.min.apply(null, ys) };
  }
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") {
        return (t) => ({ width: String(t).length * fontPx() * 0.62 });
      }
      if (prop === "beginPath") return () => { st.path = []; };
      if (prop === "moveTo") return (x, y) => st.path.push(["M", x, y]);
      if (prop === "lineTo") return (x, y) => st.path.push(["L", x, y]);
      if (prop === "arcTo") return (x1, y1, x2, y2) => {
        st.path.push(["M", x1, y1]); st.path.push(["L", x2, y2]);
      };
      if (prop === "roundRect") return (x, y, w, h) => {
        // скруглённый прямоугольник: для габаритов достаточно углов
        st.path.push(["M", x, y]); st.path.push(["L", x + w, y]);
        st.path.push(["L", x + w, y + h]); st.path.push(["L", x, y + h]);
      };
      if (prop === "closePath") return noop;
      if (prop === "fill") return () => log.push({
        op: "fill", style: String(st.fillStyle), box: box() });
      if (prop === "stroke") return () => log.push({
        op: "stroke", style: String(st.strokeStyle),
        lw: Number(st.lineWidth) || 0, shadow: Number(st.shadowBlur) || 0,
        shadowColor: String(st.shadowColor), box: box() });
      if (prop === "fillText") return (t, x, y) => log.push({
        op: "text", style: String(st.fillStyle), font: fontPx(),
        txt: String(t), x: x, y: y });
      return typeof prop === "string" ? noop : undefined;
    },
    set(_t, prop, v) { st[prop] = v; return true; },
  });
}

const near = (a, b, tol) => Math.abs(Number(a) - Number(b)) <= (tol === undefined ? 1.5 : tol);

async function main() {
  const errors = [];
  const log = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT");
        win.localStorage.setItem("liqscope.devLayers", "1");
        win.localStorage.setItem("liqscope.liqEnabled", "1");
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      const rec = recorder(log);
      const noop = () => {};
      const dummy = new Proxy({}, {
        get(_t, prop) {
          if (prop === "measureText") return (s) => ({ width: String(s).length * 5 });
          if (prop === "canvas") return { width: 900, height: 500 };
          return typeof prop === "string" ? noop : undefined;
        },
        set() { return true; },
      });
      win.HTMLCanvasElement.prototype.getContext = function () {
        if (this.id === "cluster-canvas") return rec;
        return dummy;
      };
      const klines = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles };
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/klines") === 0) body = klines;
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify({
            type: "init", symbols: ["BTC_USDT"], custom_symbols: [],
            details: [{ symbol: "BTC_USDT", volAvg7d: 1e9 }],
            prices: { BTC_USDT: 51000 }, recent_liquidations: liqs,
            exchanges: ["BINANCE", "OKX"], stats: {},
          }) });
        }, 30);
        return sock;
      };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
    },
  });

  await new Promise((r) => setTimeout(r, 6000));
  const win = dom.window;
  const L = win.LiqScopeLiq;
  check("тестовый API плашек есть", !!L && typeof L.setHover === "function");
  if (!L) {
    console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
    process.exit(1);
    return;
  }

  const hits = L.hits().sort((a, b) => b.total - a.total);
  check("плашки нарисованы (есть что подсвечивать)", hits.length === 3,
    JSON.stringify(hits.map((h) => [h.key, h.w, h.h])));
  if (hits.length < 2) {
    console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
    process.exit(1);
    return;
  }

  const white = (s) => /255,\s*255,\s*255|#ffffff/i.test(String(s));
  function shot() {
    log.length = 0;
    L.redraw();
    return {
      veils: log.filter((e) => e.op === "fill" && white(e.style)),
      halos: log.filter((e) => e.op === "stroke" && e.shadow >= 10 && white(e.shadowColor)),
      crisp: log.filter((e) => e.op === "stroke" && white(e.style) &&
                                 !e.shadow && e.lw >= 2),
    };
  }

  // --- без наведения: ничего белого --------------------------------------
  const idle = shot();
  check("без наведения белой подсветки нет",
    idle.veils.length === 0 && idle.halos.length === 0 && idle.crisp.length === 0,
    JSON.stringify([idle.veils.length, idle.halos.length, idle.crisp.length]));

  // --- наведение на самый крупный кластер -------------------------------
  const target = hits[0];
  L.setHover(target.key);
  const lit = shot();
  check("есть ореол: белая тень вокруг плашки", lit.halos.length === 1,
    JSON.stringify(lit.halos.map((e) => [e.style, e.lw, e.shadow])));
  check("ореол заметный (тень ≥ 12px)", lit.halos.length === 1 && lit.halos[0].shadow >= 12,
    lit.halos.length ? lit.halos[0].shadow : "нет");
  check("есть резкий белый контур", lit.crisp.length === 1,
    JSON.stringify(lit.crisp.map((e) => [e.style, e.lw])));
  check("контур ярко-белый (не полупрозрачный)",
    lit.crisp.length === 1 && /^#ffffff$/i.test(lit.crisp[0].style),
    lit.crisp.length ? lit.crisp[0].style : "нет");
  check("контур толще прежнего 1.6px", lit.crisp.length === 1 && lit.crisp[0].lw >= 2,
    lit.crisp.length ? lit.crisp[0].lw : "нет");
  check("поверх цвета — светлая вуаль", lit.veils.length === 1,
    JSON.stringify(lit.veils.map((e) => e.style)));
  check("вуаль полупрозрачная (цвет кластера виден)",
    lit.veils.length === 1 && /0\.\d+\)/.test(lit.veils[0].style),
    lit.veils.length ? lit.veils[0].style : "нет");

  // габариты подсветки — вокруг именно этой плашки, а не вокруг всего графика
  const v = lit.veils[0] && lit.veils[0].box;
  check("вуаль ровно по габаритам наведённой плашки",
    !!v && near(v.x, target.x) && near(v.y, target.y) &&
    near(v.w, target.w) && near(v.h, target.h),
    JSON.stringify([v, target]));
  const c = lit.crisp[0] && lit.crisp[0].box;
  check("контур чуть шире плашки (2px с каждой стороны)",
    !!c && near(c.x, target.x - 2, 0.6) && near(c.y, target.y - 2, 0.6) &&
    near(c.w, target.w + 4, 1.2) && near(c.h, target.h + 4, 1.2),
    JSON.stringify([c, target]));

  // --- перенесли наведение: светится другой кластер ----------------------
  const second = hits.find((h) => h.key !== target.key && h.w > 6);
  if (second) {
    L.setHover(second.key);
    const moved = shot();
    const v2 = moved.veils[0] && moved.veils[0].box;
    check("подсветка переехала на другой кластер",
      moved.veils.length === 1 && !!v2 && near(v2.x, second.x) &&
      near(v2.y, second.y) && near(v2.w, second.w) && near(v2.h, second.h),
      JSON.stringify([v2, second]));
    check("старая подсветка погасла",
      moved.crisp.length === 1 && !near(moved.crisp[0].box.x, target.x, 0.5),
      JSON.stringify([moved.crisp[0] && moved.crisp[0].box, target]));
  }

  // --- сняли наведение: снова чисто --------------------------------------
  L.setHover(null);
  const off = shot();
  check("сняли наведение — подсветка убрана",
    off.veils.length === 0 && off.halos.length === 0 && off.crisp.length === 0,
    JSON.stringify([off.veils.length, off.halos.length, off.crisp.length]));

  // --- растянутый график: цифры плашек остаются поверх подсветки ---------
  const ts = L.timeScale();
  const realX = ts.timeToCoordinate.bind(ts);
  const realOpts = ts.options.bind(ts);
  const appCandles = L.candles();
  const tf = 300;
  const lastTime = appCandles[appCandles.length - 1].time;
  const rightEdge = realX(lastTime);
  const slot = 46;
  ts.options = () => Object.assign({}, realOpts(), { barSpacing: slot });
  ts.timeToCoordinate = (t) => {
    const v = realX(t);
    if (v === null || v === undefined || !isFinite(v)) return v;
    return rightEdge - Math.round((lastTime - Number(t)) / tf) * slot;
  };
  const wideKey = hits[0].key;                 // тот же кластер, но шире
  L.setHover(wideKey);
  const litWide = shot();
  const texts = log.filter((e) => e.op === "text");
  const veilIdx = log.indexOf(litWide.veils[0]);
  const wideTarget = L.hits().filter((h) => h.key === wideKey)[0] || hits[0];
  check("растянули: у подсвеченного кластера появились цифры",
    texts.length > 0, JSON.stringify(texts.map((e) => e.txt)));
  check("цифры плашек рисуются поверх подсветки",
    texts.length > 0 && veilIdx >= 0 &&
    log.slice(veilIdx + 1).some((e) => e.op === "text"), "порядок отрисовки");
  const vw = litWide.veils[0] && litWide.veils[0].box;
  check("растянули: подсветка по габаритам широкой плашки",
    !!vw && near(vw.x, wideTarget.x) && near(vw.y, wideTarget.y) &&
    near(vw.w, wideTarget.w) && near(vw.h, wideTarget.h),
    JSON.stringify([vw, wideTarget]));
  check("растянули: подсветка не шире плашки по свече",
    !!vw && vw.w <= slot * 0.86 + 1.5, JSON.stringify(vw && vw.w));

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
