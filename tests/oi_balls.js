/** OI-шарики и CVD-треугольники: формы, тиры, цвета.
 *
 *  OI-шарики: мини (<$1M, r=6, без подписи), тиры 1/2/3 (r=11/13.42/15.95,
 *  кегль 8/9/10, свечение 8/12/16), цвет — зелёная/красная шкала по тиру.
 *  CVD-треугольники: фиолет/оранж, размер от p90, подпись когда влезает.
 *  Рекордер пишет вызовы cluster-canvas: шарики опознаём по дугам arc()
 *  с OI-заливками, треугольники — по путям из 3 точек с CVD-заливками,
 *  подписи — по уникальным цветам текста.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/oi_balls.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const OI_TEXT = ["#04160b", "#1c060d"];
const UP_SHADES = [[190, 242, 200], [134, 239, 172], [34, 197, 94], [0, 230, 118]];
const DOWN_SHADES = [[252, 200, 200], [248, 113, 113], [239, 68, 68], [255, 42, 95]];
function oiFill(rgb) {
  return "rgba(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ",0.95)";
}
const OI_FILLS = UP_SHADES.map(oiFill).concat(DOWN_SHADES.map(oiFill));
const CVD_FILLS = ["rgba(139,92,246,0.96)", "rgba(255,145,0,0.96)"];
const CVD_TEXT = ["#0d0618", "#1c0d00"];

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
function oiCandles(pair) {
  return pair.map((d, i) => ({
    time: NOW - 3600 + i * 3600, open: 50000, high: 50100, low: 49900,
    close: 50050, volume: 10, cvd: 0, oiChg: d,
  }));
}
function cvdCandles() {
  return [
    { time: NOW - 3600, open: 50000, high: 50100, low: 49900, close: 50050,
      volume: 10, cvd: 5000, oiChg: 0 },
    { time: NOW, open: 49000, high: 49100, low: 48900, close: 49050,
      volume: 10, cvd: -12000, oiChg: 0 },
  ];
}

async function runCase(candles) {
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
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles };
        } else if (u.indexOf("/api/stats") === 0) body = {};
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await new Promise((r) => setTimeout(r, 7000));
  await dom.window.close();

  const balls = log.filter((e) => e.op === "ball" && OI_FILLS.indexOf(e.fill) !== -1);
  const tris = log.filter((e) => e.op === "tri" && CVD_FILLS.indexOf(e.fill) !== -1);
  const otexts = log.filter((e) => e.op === "text" && OI_TEXT.indexOf(e.style) !== -1);
  const ctexts = log.filter((e) => e.op === "text" && CVD_TEXT.indexOf(e.style) !== -1);
  const oglows = log.filter((e) => e.op === "glow" && OI_FILLS.indexOf(e.color) !== -1)
                     .map((e) => e.v);
  return { balls, tris, otexts, ctexts, oglows };
}

async function main() {
  const r1 = await runCase(oiCandles([500e3, -2e6]));    // мини + тир1
  const r2 = await runCase(oiCandles([10e6, -50e6]));     // тир2 + тир3
  const r3 = await runCase(cvdCandles());                 // CVD-треугольники
  const balls = r1.balls.concat(r2.balls);
  const otexts = r1.otexts.concat(r2.otexts);
  const oglows = r1.oglows.concat(r2.oglows);
  const radii = balls.map((t) => t.r);
  const hasR = (want) => radii.some((r) => Math.abs(r - want) < 0.05);
  const ovals = otexts.map((t) => t.txt);
  const ofont = (txt) => {
    const t = otexts.find((x) => x.txt === txt);
    return t ? String(t.font).split(" ")[1] : null;
  };
  const ocolor = (txt) => {
    const t = otexts.find((x) => x.txt === txt);
    return t ? t.style : null;
  };

  check("all 4 balls drawn", balls.length >= 4, balls.length);
  check("mini r=6", hasR(6), JSON.stringify(radii));
  check("tier1 r=11", hasR(11));
  check("tier2 r=13.42", hasR(13.42));
  check("tier3 r=15.95", hasR(15.95));
  check("growth: max>14", Math.max.apply(null, radii.concat([0])) > 14);
  check("mini unlabeled", ovals.indexOf("$500K") === -1, JSON.stringify(ovals));
  check("tier1 labeled $2M", ovals.indexOf("$2M") !== -1);
  check("tier2 labeled $10M", ovals.indexOf("$10M") !== -1);
  check("tier3 labeled $50M", ovals.indexOf("$50M") !== -1);
  check("tier1 font 8px", ofont("$2M") === "8px", ofont("$2M"));
  check("tier2 font 9px", ofont("$10M") === "9px", ofont("$10M"));
  check("tier3 font 10px", ofont("$50M") === "10px", ofont("$50M"));
  check("down text red-dark", ocolor("$2M") === "#1c060d" && ocolor("$50M") === "#1c060d");
  check("up text green-dark", ocolor("$10M") === "#04160b");
  check("glow 8 present", oglows.indexOf(8) !== -1, JSON.stringify(oglows));
  check("glow 12 present", oglows.indexOf(12) !== -1);
  check("glow 16 present", oglows.indexOf(16) !== -1);
  check("tier2 green", balls.some((t) => t.fill === oiFill(UP_SHADES[2])),
    JSON.stringify(balls.map((t) => t.fill)));
  check("tier3 red-hot", balls.some((t) => t.fill === oiFill(DOWN_SHADES[3])));
  check("mini pale green", balls.some((t) => t.fill === oiFill(UP_SHADES[0])));
  check("tier1 pale red", balls.some((t) => t.fill === oiFill(DOWN_SHADES[1])));

  // CVD-треугольники: фиолет/оранж, размер от p90, подписи
  const theights = r3.tris.map((t) => t.h);
  const hasH = (want) => theights.some((h) => Math.abs(h - want) < 0.05);
  const cvals = r3.ctexts.map((t) => t.txt);
  const ccolor = (txt) => {
    const t = r3.ctexts.find((x) => x.txt === txt);
    return t ? t.style : null;
  };
  check("cvd 2 tris drawn", r3.tris.length >= 2, r3.tris.length);
  check("cvd buy h=31.5", hasH(31.5), JSON.stringify(theights));
  check("cvd sell h=35.1", hasH(35.1));
  check("cvd $5K labeled", cvals.indexOf("$5K") !== -1, JSON.stringify(cvals));
  check("cvd $12K labeled", cvals.indexOf("$12K") !== -1);
  check("cvd buy violet-dark", ccolor("$5K") === "#0d0618");
  check("cvd sell orange-dark", ccolor("$12K") === "#1c0d00");

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
