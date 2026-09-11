/** Масштаб «крупности» от оборота монеты (volScale).
 *
 *  GRAM с оборотом 1/1000 от BTC: k=0.001. Проверяем, что пороги кита,
 *  подписей, тиров OI и минимума CVD ужаты в 1000 раз:
 *   - прямоугольник: $100 — кит (тепловая заливка + подпись $100 цветом
 *     #1a1200), $50 — подписанный по стороне (циан), $1 — чип без подписи;
 *   - CVD: треугольники рисуются от $50/$200 (без масштаба минимум $1000);
 *   - OI: $2K — тир1 (r=11, подпись $2K), $600 — мини (r=6, без подписи);
 *   - лента: $100 — whale-строка, остальные — обычные.
 *  Плюс проверяем новые цвета swapped-фигур: рост — зелёный, падение — красный.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/volscale.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const HEAT_FILL = "rgba(255,206,0,0.95)";     // ровно первый стоп шкалы
const HEAT_TEXT = "#1a1200";
const SIDE_TEXT = "#04070d";
const CYAN_FILL = "rgba(0,214,255,0.92)";
const OI_UP_T1 = "rgba(134,239,172,0.95)";    // тир1 роста — бледно-зелёный
const OI_DOWN_MINI = "rgba(252,200,200,0.95)"; // мини падения — бледно-красный
const OI_UP_TEXT = "#04160b";
const CVD_BUY_TEXT = "#0d0618";
const CVD_SELL_TEXT = "#1c0d00";

function makeRecorder(log) {
  const st = { fillStyle: null, shadowColor: null, font: null, path: [] };
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "fillText") {
        return (txt) => log.push({ op: "text", style: st.fillStyle, font: st.font,
                                   txt: String(txt) });
      }
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "beginPath") return () => { st.path = []; };
      if (prop === "moveTo") return (x, y) => st.path.push(["M", x, y]);
      if (prop === "lineTo") return (x, y) => st.path.push(["L", x, y]);
      if (prop === "closePath") return noop;
      if (prop === "arc") {
        return (x, y, r) => st.path.push(["A", x, y, r]);
      }
      if (prop === "fill") {
        return () => {
          log.push({ op: "fill", style: st.fillStyle });
          if (st.path.length === 3 && st.path[0][0] === "M" &&
              st.path[1][0] === "L" && st.path[2][0] === "L") {
            log.push({ op: "tri", h: Math.abs(st.path[1][2] - st.path[0][2]),
                       fill: st.fillStyle });
          } else if (st.path.length === 1 && st.path[0][0] === "A") {
            log.push({ op: "ball", r: st.path[0][3], fill: st.fillStyle });
          }
          st.path = [];
        };
      }
      if (prop === "stroke") return () => { st.path = []; };
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "fillStyle") return st.fillStyle;
      if (prop === "font") return st.font;
      if (prop === "shadowColor") return st.shadowColor;
      return typeof prop === "string" ? noop : undefined;
    },
    set(_t, prop, v) {
      if (prop === "fillStyle") st.fillStyle = v;
      else if (prop === "shadowColor") st.shadowColor = v;
      else if (prop === "font") st.font = v;
      else if (prop === "shadowBlur") log.push({ op: "glow", color: st.shadowColor, v: v });
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

const NOW = Math.floor(Date.now() / 1000);
const T2 = NOW - (NOW % 300), T1 = T2 - 3600;   // 300-aligned: иначе кластеры мимо свечей
const candles = [
  { time: T1, open: 50000, high: 50100, low: 49900, close: 50050,
    volume: 10, cvd: 50, oiChg: 2000 },
  { time: T2, open: 49000, high: 49100, low: 48900, close: 49050,
    volume: 10, cvd: -200, oiChg: -600 },
];
const liqs = [
  { id: 101, symbol: "GRAM_USDT", exchange: "BINANCE", side: "SELL",
    usd: 100, price: 50050, timestamp: T1 },
  { id: 102, symbol: "GRAM_USDT", exchange: "BINANCE", side: "BUY",
    usd: 50, price: 49050, timestamp: T2 },
  { id: 103, symbol: "GRAM_USDT", exchange: "BINANCE", side: "SELL",
    usd: 1, price: 50000, timestamp: T1 },
];

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));
  const log = [];

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
      try {
        win.localStorage.setItem("licvid.chartSymbol", "GRAM_USDT");
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
      const initMsg = {
        type: "init",
        symbols: ["GRAM_USDT"],
        custom_symbols: [],
        details: [
          { symbol: "BTC_USDT", volAvg7d: 1e9 },
          { symbol: "GRAM_USDT", volAvg7d: 1e6 },
        ],
        prices: {},
        recent_liquidations: liqs,
        exchanges: ["BINANCE"],
        stats: {},
      };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify(initMsg) });
        }, 50);
        return sock;
      };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/klines") === 0) {
          body = { symbol: "GRAM_USDT", timeframe: 5, source: "stub", candles };
        } else if (u.indexOf("/api/stats") === 0) body = {};
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await new Promise((r) => setTimeout(r, 7000));

  const doc = dom.window.document;
  const whales = doc.querySelectorAll("#feed-tbody tr.feed-row-whale");
  const plain = doc.querySelectorAll("#feed-tbody tr:not(.feed-row-whale)");
  const whaleTxt = whales.length ? whales[0].textContent : "";
  await dom.window.close();

  const texts = log.filter((e) => e.op === "text");
  const fills = log.filter((e) => e.op === "fill").map((e) => e.style);
  const hasText = (txt, style) => texts.some((e) => e.txt === txt && e.style === style);
  const anyText = (txt) => texts.some((e) => e.txt === txt);
  const balls = log.filter((e) => e.op === "ball");
  const radii = balls.map((t) => t.r);
  const hasR = (want) => radii.some((r) => Math.abs(r - want) < 0.05);
  const tris = log.filter((e) => e.op === "tri");
  const heights = tris.map((t) => t.h);
  const hasH = (want) => heights.some((h) => Math.abs(h - want) < 0.05);
  const ofont = (txt, style) => {
    const t = texts.find((x) => x.txt === txt && x.style === style);
    return t ? String(t.font).split(" ")[1] : null;
  };

  // прямоугольники: пороги ужаты в 1000 раз
  check("whale heat fill", fills.indexOf(HEAT_FILL) !== -1,
    JSON.stringify(fills.filter((f, i) => fills.indexOf(f) === i).slice(0, 12)));
  check("whale labeled $100 heat-text", hasText("$100", HEAT_TEXT));
  check("side rect labeled $50", hasText("$50", SIDE_TEXT),
    JSON.stringify(texts.filter((e) => e.txt === "$50").map((e) => e.style)));
  check("side cyan fill", fills.indexOf(CYAN_FILL) !== -1);
  check("dust $1 unlabeled", !anyText("$1"),
    JSON.stringify(texts.map((e) => e.txt).filter((v, i, a) => a.indexOf(v) === i)));

  // CVD: без масштаба минимум был бы $1000 — ничего бы не нарисовалось
  check("cvd buy h=31.5", hasH(31.5), JSON.stringify(heights));
  check("cvd sell h=35.1", hasH(35.1));
  check("cvd buy labeled $50", hasText("$50", CVD_BUY_TEXT));
  check("cvd sell labeled $200", hasText("$200", CVD_SELL_TEXT));

  // OI: тир1 от $1K вместо $1M
  check("oi tier1 r=11", hasR(11), JSON.stringify(radii));
  check("oi $2K green-dark 8px",
    hasText("$2K", OI_UP_TEXT) && ofont("$2K", OI_UP_TEXT) === "8px",
    ofont("$2K", OI_UP_TEXT));
  check("oi tier1 pale-green fill", balls.some((t) => t.fill === OI_UP_T1),
    JSON.stringify(balls.map((t) => t.fill)));
  check("oi mini r=6", hasR(6));
  check("oi mini unlabeled", !anyText("$600"));
  check("oi mini pale-red fill", balls.some((t) => t.fill === OI_DOWN_MINI));

  // лента: whale-класс тоже от масштаба
  check("feed: 1 whale + 2 plain", whales.length === 1 && plain.length === 2,
    "whale=" + whales.length + " plain=" + plain.length);
  check("feed whale is $100 row", whaleTxt.indexOf("$100") !== -1,
    whaleTxt.slice(0, 120));

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
