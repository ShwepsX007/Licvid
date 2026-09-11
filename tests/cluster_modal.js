/** Окно кластера ликвидаций: наведение показывает + подсвечивает ленту, клик закрепляет.
 *
 *  Наведение на прямоугольник открывает попап с разбором кластера (свеча,
 *  уровень, доминанта, сумма + кит, число событий, сплит L/S, движение цены,
 *  пояснение) И подсвечивает связанные строки в ленте. Клик закрепляет оба,
 *  повторный клик / клик мимо / крестик — снимает. Координаты прямоугольников
 *  берём из записей канваса (rrPath → roundRect), события мыши — синтетические
 *  через настоящий LWC (клик = mousedown+mouseup с паузой от даблклика).
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/cluster_modal.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const UNDERLAYS = ["rgba(5,8,14,0.88)", "rgba(5,8,14,0.85)"];

function makeRecorder(log) {
  const st = { fillStyle: null, strokeStyle: null, shadowColor: null, font: null, path: [] };
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
      if (prop === "roundRect") {
        return (x, y, w, h) => st.path.push(["R", x, y, w, h]);
      }
      if (prop === "arc") {
        return (x, y, r) => st.path.push(["A", x, y, r]);
      }
      if (prop === "fill") {
        return () => {
          if (st.path.length === 1 && st.path[0][0] === "R") {
            const q = st.path[0];
            log.push({ op: "rect", x: q[1] + q[3] / 2, y: q[2] + q[4] / 2,
                       fill: st.fillStyle });
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
      if (prop === "strokeStyle") return st.strokeStyle;
      if (prop === "font") return st.font;
      if (prop === "shadowColor") return st.shadowColor;
      return typeof prop === "string" ? noop : undefined;
    },
    set(_t, prop, v) {
      if (prop === "fillStyle") st.fillStyle = v;
      else if (prop === "strokeStyle") st.strokeStyle = v;
      else if (prop === "shadowColor") st.shadowColor = v;
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

const NOW = Math.floor(Date.now() / 1000);
const T2 = NOW - (NOW % 300), T1 = T2 - 3600;
const candles = [
  { time: T1, open: 50000, high: 50100, low: 49900, close: 50050,
    volume: 10, cvd: 0, oiChg: 0 },
  { time: T2, open: 49000, high: 49100, low: 48900, close: 49050,
    volume: 10, cvd: 0, oiChg: 0 },
];
const liqs = [
  { id: "w1", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 60000, price: 50050, timestamp: T1 },
  { id: "w2", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 60000, price: 50060, timestamp: T1 },
  { id: "s1", symbol: "BTC_USDT", exchange: "BINANCE", side: "BUY",
    usd: 5000, price: 49050, timestamp: T2 },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
        win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT");
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
        symbols: ["BTC_USDT"],
        custom_symbols: [],
        details: [{ symbol: "BTC_USDT", volAvg7d: 1e9 }],
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
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles };
        } else if (u.indexOf("/api/stats") === 0) body = {};
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await sleep(7000);
  const win = dom.window;
  const doc = win.document;
  const modal = doc.getElementById("detail-modal");
  const title = doc.getElementById("modal-title");
  const body = doc.getElementById("modal-body");

  // центры прямоугольников — из записей канваса (без тёмных подложек)
  const dedup = (pts) => {
    const seen = new Set(), out = [];
    pts.forEach((p) => {
      const k = Math.round(p.x / 15) + ":" + Math.round(p.y / 15);
      if (!seen.has(k)) { seen.add(k); out.push(p); }
    });
    return out;
  };
  const mains = dedup(log.filter((e) => e.op === "rect" &&
    UNDERLAYS.indexOf(e.fill) === -1));
  // кит — тепловая заливка (красный канал 255), обычный — маджента/циан
  const whalePts = mains.filter((p) => (p.fill || "").indexOf("rgba(255,") === 0);

  const root = doc.getElementById("tv-chart-container");
  const allNodes = [root].concat(Array.prototype.slice.call(root.querySelectorAll("*")));
  const panes = allNodes.filter((n) => n.tagName === "CANVAS");
  const pane = panes[1] || root;
  function fire(type, x, y) {
    pane.dispatchEvent(new win.MouseEvent(type, { bubbles: true, cancelable: true,
      clientX: x, clientY: y, view: win }));
  }
  const move = (x, y) => { fire("mouseenter", x, y); fire("mousemove", x, y); };
  const click = async (x, y) => {
    move(x, y);
    fire("mousedown", x, y);
    fire("mouseup", x, y);
    await sleep(600);
  };
  const shown = () => !modal.classList.contains("hidden");
  const txt = () => body.textContent || "";
  const norm = (s) => String(s).replace(/\s/g, "");
  const hits = () => doc.querySelectorAll("#feed-tbody tr.feed-hit").length;
  const dims = () => doc.querySelectorAll("#feed-tbody tr.feed-dim").length;

  check("rect mains found", mains.length >= 2, JSON.stringify(mains));
  check("whale rect found", whalePts.length >= 1,
    JSON.stringify(mains.map((p) => p.fill)));
  if (!whalePts.length) {
    console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
    await win.close();
    process.exit(1);
  }
  const W = whalePts[0];
  const empty = [[8, 8], [892, 8], [8, 492], [892, 492], [450, 5]].find((pt) =>
    mains.every((p) => Math.abs(p.x - pt[0]) > 60 && Math.abs(p.y - pt[1]) > 60)) || [8, 8];

  // --- наведение: окно + подсветка ленты ---
  move(W.x, W.y);
  check("hover shows modal", shown());
  check("hover peek", modal.classList.contains("peek") &&
    !modal.classList.contains("peek-pinned"));
  check("title", title.textContent === "Ликвидации BTC/USDT", title.textContent);
  const t = txt();
  check("candle row", t.indexOf("Свеча:") !== -1 && t.indexOf("–") !== -1);
  const lvl = t.match(/Уровень:\s*≈\s*\$([\d,\.]+)/);
  check("level ≈ 50060", !!lvl && Math.abs(Number(lvl[1].replace(/,/g, "")) - 50060) < 1,
    lvl && lvl[1]);
  check("dominant long", t.indexOf("LONG (принудительная продажа)") !== -1);
  check("total + whale", norm(t).indexOf("$120000") !== -1 && t.indexOf("кит") !== -1 &&
    t.indexOf("🐋") !== -1, t.slice(0, 160));
  check("events", t.indexOf("2 · 2 LONG / 0 SHORT") !== -1, t.slice(0, 200));
  check("split", t.indexOf("L $120.0K") !== -1 && t.indexOf("S $0") !== -1,
    t.slice(0, 220));
  check("about", t.indexOf("слипшиеся") !== -1);
  check("feed highlight 2+1", hits() === 2 && dims() === 1,
    "hit=" + hits() + " dim=" + dims());
  move(empty[0], empty[1]);
  check("leave hides + clears", !shown() && hits() === 0 && dims() === 0);

  // --- клик закрепляет окно и подсветку ---
  await click(W.x, W.y);
  check("click pins", shown() && modal.classList.contains("peek-pinned"));
  move(empty[0], empty[1]);
  check("pinned survives", shown() && hits() === 2, "hit=" + hits());
  await click(W.x, W.y);
  check("second click clears", !shown() && hits() === 0);

  // --- клик мимо снимает ---
  await click(W.x, W.y);
  check("re-pinned", shown() && hits() === 2);
  await click(empty[0], empty[1]);
  check("click away clears", !shown() && hits() === 0);

  // --- крестик ---
  await click(W.x, W.y);
  check("pinned again", shown());
  doc.getElementById("close-modal").dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  check("X clears all", !shown() && hits() === 0);
  move(W.x, W.y);
  check("hover works after X", shown() && hits() === 2);
  move(empty[0], empty[1]);

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
