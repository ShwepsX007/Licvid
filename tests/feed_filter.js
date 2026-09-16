/** Клик по паре в ленте — фильтр ленты по этой монете, а не только график.
 *
 *  В терминале три ленты: ликвидации, CVD и OI. Клик по монете в любой из
 *  них выбирает монету целиком: лента ликвидаций оставляет только её события,
 *  график переключается на неё (то же, что выбрать монету в списке сверху).
 *  Рядом со счётчиком появляется плашка «BTC/USDT ✕» — клик по ней возвращает
 *  ленту ко всем монетам («ВСЕ»). Подсказка на кнопке монеты объясняет клик.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/feed_filter.js
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
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

const NOW = Math.floor(Date.now() / 1000);
const T2 = NOW - (NOW % 300), T1 = T2 - 3600, T3 = T2 - 7200;
const candles = [
  { time: T3, open: 50000, high: 50100, low: 49900, close: 50050, volume: 10,
    cvd: 9000, oi: 5.2e8, oiChg: 3e6 },
  { time: T2, open: 49000, high: 49100, low: 48900, close: 49050, volume: 10,
    cvd: -3000, oi: 5.3e8, oiChg: -1.5e6 },
  { time: T1, open: 49000, high: 49100, low: 48900, close: 49050, volume: 10,
    cvd: 4000, oi: 5.25e8, oiChg: 1.2e6 },
];
const liqs = [
  { id: "b1", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 90000, price: 50050, timestamp: NOW },
  { id: "e1", symbol: "ETH_USDT", exchange: "BYBIT", side: "BUY",
    usd: 150000, price: 2500, timestamp: NOW - 20 },
  { id: "e2", symbol: "ETH_USDT", exchange: "GATE", side: "SELL",
    usd: 120000, price: 2505, timestamp: NOW - 40 },
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
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          const init = {
            type: "init",
            symbols: ["BTC_USDT", "ETH_USDT"],
            custom_symbols: [],
            details: [{ symbol: "BTC_USDT", volAvg7d: 1e9 },
                      { symbol: "ETH_USDT", volAvg7d: 8e8 }],
            prices: { BTC_USDT: 50050, ETH_USDT: 2500 },
            recent_liquidations: liqs,
            exchanges: ["BINANCE", "BYBIT", "GATE"],
            stats: {},
          };
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify(init) });
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
  const chip = doc.getElementById("feed-filter");
  const title = doc.getElementById("current-symbol-title");
  const tab = (name) => doc.getElementById("feed-tab-" + name).dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  const coinCells = () => Array.prototype.slice.call(
    doc.querySelectorAll("#feed-body .coin-link, #feed-table tbody .coin-link"));
  const coinTexts = () => coinCells().map((b) => b.textContent.replace(/[📈\s✕]/g, ""));
  const clickCoin = (sym) => {
    const btn = coinCells().filter(
      (b) => decodeURIComponent(b.dataset.symbol).indexOf(sym) === 0)[0];
    if (!btn) return false;
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    return true;
  };

  // --- до клика: фильтра нет, в ленте обе монеты ---
  check("chip hidden by default", chip.classList.contains("hidden"), chip.className);
  const before = coinTexts();
  check("feed shows both coins", before.indexOf("BTC/USDT") !== -1 && before.indexOf("ETH/USDT") !== -1,
        before.join(","));
  check("coin tooltip mentions feed", /лента/i.test(coinCells()[0].title || ""), coinCells()[0].title);

  // --- клик по монете в ленте ликвидаций ---
  check("clicked ETH in liq feed", clickCoin("ETH"));
  await sleep(400);
  check("chip visible after click", !chip.classList.contains("hidden"), chip.className);
  check("chip names the coin", chip.textContent.indexOf("ETH/USDT") === 0, chip.textContent);
  const after = coinTexts();
  check("feed filtered to ETH", after.length > 0 && after.every((t) => t === "ETH/USDT"),
        after.join(","));
  check("chart switched to ETH", title.textContent.indexOf("ETH/USDT") === 0, title.textContent);
  check("chart symbol persisted", (() => {
    try { return win.localStorage.getItem("liqscope.chartSymbol") === "ETH_USDT"; }
    catch (e) { return false; }
  })());

  // --- «✕» на плашке = «ВСЕ» ---
  chip.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  await sleep(400);
  check("chip hidden after reset", chip.classList.contains("hidden"), chip.className);
  const back = coinTexts();
  check("feed back to all coins",
        back.indexOf("BTC/USDT") !== -1 && back.indexOf("ETH/USDT") !== -1, back.join(","));
  check("title back to all-coins mode", /все монеты|all coins/i.test(title.textContent),
        title.textContent);

  // --- то же из лент CVD и OI: там строки про монету графика (сейчас ETH) ---
  tab("cvd");
  await sleep(300);
  const cvdCoins = coinTexts();
  check("cvd feed is about the chart coin",
        cvdCoins.length > 0 && cvdCoins.every((t) => t === "ETH/USDT"), cvdCoins.join(","));
  check("clicked ETH in cvd feed", clickCoin("ETH"));
  await sleep(400);
  check("cvd click filters feed", !chip.classList.contains("hidden") &&
        chip.textContent.indexOf("ETH/USDT") === 0, chip.textContent);
  tab("liq");
  await sleep(300);
  const liqOnly = coinTexts();
  check("liq feed left with ETH only",
        liqOnly.length > 0 && liqOnly.every((t) => t === "ETH/USDT"), liqOnly.join(","));

  // --- сброс и то же из ленты OI ---
  chip.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  await sleep(300);
  tab("oi");
  await sleep(300);
  // в колонке «OI Δ» — изменение за свечу (oiChg), а не уровень интереса:
  // раньше сюда попадал уровень (миллиарды), и все строки были «▲»
  const oiRow = doc.querySelector("#feed-table tbody tr");
  const oiRowText = oiRow ? oiRow.textContent : "";
  check("oi feed shows the change, not the level",
        /1\D?200\D?000/.test(oiRowText) && oiRowText.indexOf("525") === -1,
        oiRowText);
  check("clicked ETH in oi feed", clickCoin("ETH"));
  await sleep(400);
  check("oi click filters feed", !chip.classList.contains("hidden") &&
        chip.textContent.indexOf("ETH/USDT") === 0, chip.textContent);
  tab("liq");
  await sleep(300);
  check("liq feed still ETH only after oi click",
        coinTexts().every((t) => t === "ETH/USDT"), coinTexts().join(","));
  check("chart title kept", title.textContent.indexOf("ETH/USDT") === 0, title.textContent);

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
