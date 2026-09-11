/** Живые цифры у кнопок слоёв + развязка от тумблеров.
 *
 *  У каждой кнопки слоя свой индикатор: ликвидации текущей свечи ($+число),
 *  объём профиля (видимый диапазон), CVD и OI текущей свечи. Строгое соседство
 *  кнопка→цифра. Выключение слоя прячет только фигуры: цифры у кнопок и боксы
 *  в шапке продолжают обновляться (регрессия: раньше тумблер CVD гасил шапку).
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/live_stats.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
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
    volume: 10, cvd: 8000, oiChg: 2e6 },
  { time: T2, open: 49000, high: 49100, low: 48900, close: 49050,
    volume: 10, cvd: -3000, oiChg: -1500 },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

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
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          // метки «сейчас» — строго текущий бакет свечи
          const now = Math.floor(win.Date.now() / 1000);
          const liqs = [
            { id: "a1", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
              usd: 60000, price: 50050, timestamp: now },
            { id: "a2", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
              usd: 60000, price: 50060, timestamp: now },
            { id: "a3", symbol: "BTC_USDT", exchange: "BINANCE", side: "BUY",
              usd: 5000, price: 49050, timestamp: now },
          ];
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
  const val = (id) => doc.getElementById(id).textContent;
  const cls = (id) => doc.getElementById(id).className;
  const tap = (id) => doc.getElementById(id).dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));

  // --- значения живых цифр ---
  check("cvd live", val("cvd-stat") === "▼ −$3.0K", val("cvd-stat"));
  check("oi live", val("oi-stat") === "● −$1.5K", val("oi-stat"));
  check("oi class down", cls("oi-stat") === "live-stat down", cls("oi-stat"));
  check("liq live", val("liq-stat") === "$125.0K·3", val("liq-stat"));
  check("liq class", cls("liq-stat") === "live-stat on-liq", cls("liq-stat"));
  check("profile live", val("profile-stat") === "$125.0K", val("profile-stat"));
  check("header cvd box", val("stat-cvd-value") === "+$5.0K", val("stat-cvd-value"));

  // --- строгое соседство: все цифры — сразу за своими подписями ---
  const afterLabel = (stat) => {
    const prev = doc.getElementById(stat).previousElementSibling;
    return !!prev && prev.classList.contains("layer-label");
  };
  check("liq after label", afterLabel("liq-stat"));
  check("profile after label", afterLabel("profile-stat"));
  check("cvd after label", afterLabel("cvd-stat"));
  check("oi after label", afterLabel("oi-stat"));
  check("liq title i18n", doc.getElementById("liq-stat").title === "Ликвидации текущей свечи",
    doc.getElementById("liq-stat").title);

  // --- тумблеры не гасят цифры и шапку ---
  tap("cvd-toggle");
  check("cvd off: live stays", val("cvd-stat") === "▼ −$3.0K", val("cvd-stat"));
  check("cvd off: header stays", val("stat-cvd-value") === "+$5.0K", val("stat-cvd-value"));
  tap("cvd-toggle");
  check("cvd on again", val("cvd-stat") === "▼ −$3.0K" &&
    val("stat-cvd-value") === "+$5.0K");
  tap("liq-toggle");
  check("liq off: live stays", val("liq-stat") === "$125.0K·3", val("liq-stat"));
  check("liq off: profile stays", val("profile-stat") === "$125.0K", val("profile-stat"));
  tap("liq-toggle");
  tap("oi-toggle");
  check("oi off: live stays", val("oi-stat") === "● −$1.5K", val("oi-stat"));
  tap("oi-toggle");
  tap("profile-toggle");
  check("profile off: live stays", val("profile-stat") === "$125.0K", val("profile-stat"));
  tap("profile-toggle");

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
