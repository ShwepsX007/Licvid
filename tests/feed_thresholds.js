/** Пороги объёма в терминале: ликвидации, CVD и OI Δ.
 *
 *  В панели «Мин. объем» над лентой три порога — по одному на каждую серию:
 *  сумма ликвидации, |CVD| за свечу и |OI Δ| за свечу. Каждый порог фильтрует
 *  свою ленту и свои метки на графике (шарики ликвидаций, треугольники CVD,
 *  шарики OI). Пресеты и Enter в поле применяются к открытой вкладке ленты;
 *  значения запоминаются по отдельности.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/feed_thresholds.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

// Свечи графика: |CVD| 8K / 42K / 31K и |OI Δ| 0.9M / 7M / 5.2M — пороги ниже
// подобраны так, чтобы отсекать по одной свече.
const NOW = Math.floor(Date.now() / 1000);
const T3 = NOW - (NOW % 300) - 600, T2 = T3 + 300, T1 = T3 + 600;
const candles = [
  { time: T3, open: 50000, high: 50100, low: 49900, close: 50050, volume: 10,
    cvd: 8000, oi: 5.2e8, oiChg: 900000 },
  { time: T2, open: 50050, high: 50300, low: 50000, close: 50250, volume: 12,
    cvd: -42000, oi: 5.4e8, oiChg: -7000000 },
  { time: T1, open: 50250, high: 50400, low: 50100, close: 50350, volume: 11,
    cvd: 31000, oi: 5.3e8, oiChg: 5200000 },
];
const liqs = [
  { id: "b1", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 90000, price: 50350, timestamp: NOW },
  { id: "e1", symbol: "ETH_USDT", exchange: "BYBIT", side: "BUY",
    usd: 150000, price: 2500, timestamp: NOW - 20 },
];

function makeRecorder(log) {
  const noop = () => {};
  let radius = 0;
  const st = { path: [] };
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "clearRect") {
        return () => { radius = 0; st.path = []; log.push({ op: "frame" }); };
      }
      if (prop === "beginPath") return () => { st.path = []; };
      if (prop === "moveTo") return (x, y) => st.path.push([x, y]);
      if (prop === "lineTo") return (x, y) => st.path.push([x, y]);
      if (prop === "closePath") return noop;
      if (prop === "arc") {
        return (x, y, r) => { radius = r; log.push({ op: "arc", r: r, x: x, y: y }); };
      }
      if (prop === "fill") {
        return () => {
          if (radius) { log.push({ op: "ball", r: radius }); radius = 0; }
          else if (st.path.length === 3) log.push({ op: "tri" });
          st.path = [];
        };
      }
      if (prop === "setLineDash" || prop === "save" || prop === "restore") return noop;
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 160)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 160)));
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
            prices: { BTC_USDT: 50350, ETH_USDT: 2500 },
            recent_liquidations: liqs,
            exchanges: ["BINANCE", "BYBIT"],
            stats: {},
            flow: { type: "flow_all", window_min: 5, ts: NOW, cvd: [], oi: [],
                    liq: [], summary: { window_min: 60 } },
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
      // Слои (шарики ликвидаций, треугольники CVD, шарики OI) по умолчанию
      // выключены и доступны авторизованным — в тесте включаем dev-обход.
      try {
        win.localStorage.setItem("liqscope.devLayers", "1");
        win.localStorage.setItem("liqscope.liqEnabled", "1");
        win.localStorage.setItem("liqscope.cvdEnabled", "1");
        win.localStorage.setItem("liqscope.oiEnabled", "1");
      } catch (e) { /* ignore */ }
    },
  });

  await sleep(7000);
  const win = dom.window;
  const doc = win.document;
  const click = (el) => el && el.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  const byId = (id) => doc.getElementById(id);
  const tab = (name) => click(byId("feed-tab-" + name));
  const rows = () => Array.prototype.slice.call(
    doc.querySelectorAll("#feed-body tr, #feed-table tbody tr"));
  // Метки сравниваем по одному кадру: clearRect открывает кадр, дальше идут
  // шарики/треугольники. Берём последний кадр, в котором были шарики, — иначе
  // накопленный лог делал бы любую проверку «после порога меток больше».
  const lastFrameOps = (op, extra) => {
    const idx = [];
    for (let i = 0; i < log.length; i++) if (log[i].op === "frame") idx.push(i);
    idx.push(log.length);
    for (let f = idx.length - 2; f >= 0; f--) {
      const seg = log.slice(idx[f], idx[f + 1])
        .filter((e) => e.op === op && (!extra || extra(e)));
      if (seg.length) return seg;
    }
    return [];
  };
  const ballRadii = () => lastFrameOps("ball", (e) => e.r >= 6)
    .map((e) => Math.round(e.r * 100) / 100).sort((a, b) => a - b);
  const triCount = () => lastFrameOps("tri").length;

  // --- панель порогов: три поля ---
  check("панель ликвидаций на месте", !!byId("min-usd-panel"));
  check("поле CVD есть", !!byId("min-cvd-input"));
  check("поле OI есть", !!byId("min-oi-input"));

  // --- лента CVD: порог режет слабые свечи ---
  // Лента CVD/OI работает по свечам графика только когда выбрана монета:
  // в режиме «ВСЕ» это минутные потоки по всем монетам. Кликаем BTC в ленте.
  const btc = Array.prototype.slice.call(doc.querySelectorAll(".coin-link"))
    .filter((b) => decodeURIComponent(b.dataset.symbol || "").indexOf("BTC") === 0)[0];
  click(btc);
  await sleep(600);
  tab("cvd");
  await sleep(800);
  const cvdAll = rows().length;
  check("лента CVD показывает свечи", cvdAll >= 3, cvdAll);

  const trisBefore = triCount();
  check("треугольники CVD рисуются на графике", trisBefore >= 2, trisBefore);

  const setField = (id, value) => {
    const el = byId(id);
    el.value = String(value);
    el.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    return el;
  };
  setField("min-cvd-input", 20000);
  await sleep(800);
  const cvdFiltered = rows().length;
  check("порог CVD отсекает слабые свечи", cvdFiltered === 2, cvdFiltered);
  check("порог CVD виден в кнопке", /CVD/.test(byId("min-usd-btn").textContent),
        byId("min-usd-btn").textContent);
  check("порог CVD убирает слабые треугольники с графика",
        triCount() < trisBefore, trisBefore + " → " + triCount());
  check("порог CVD запомнен отдельно", (() => {
    try { return win.localStorage.getItem("liqscope.minCvd") === "20000"; }
    catch (e) { return false; }
  })(), (() => { try { return win.localStorage.getItem("liqscope.minCvd"); } catch (e) { return "?"; } })());
  check("порог ликвидаций не тронут", (() => {
    try { const v = win.localStorage.getItem("liqscope.minUsd"); return v === null || v === "0"; }
    catch (e) { return false; }
  })());

  // --- шарики OI на графике: порог режет мелочь ---
  rows();          // прогрев: метки уже нарисованы
  const ballsBefore = ballRadii();
  check("шарики OI рисуются", ballsBefore.length >= 2, ballsBefore.join(","));
  setField("min-oi-input", 6000000);
  await sleep(900);
  const ballsAfter = ballRadii();
  check("порог OI убирает слабые шарики с графика",
        ballsAfter.length < ballsBefore.length &&
        ballsAfter.every((r) => ballsBefore.indexOf(r) !== -1),
        ballsBefore.join(",") + " → " + ballsAfter.join(","));
  check("порог OI виден в кнопке", /OI/.test(byId("min-usd-btn").textContent),
        byId("min-usd-btn").textContent);

  // --- сброс порога возвращает метки ---
  setField("min-oi-input", 0);
  await sleep(900);
  const ballsReset = ballRadii();
  check("сброс порога OI возвращает шарики",
        ballsReset.length > ballsAfter.length && ballsReset.length === ballsBefore.length,
        ballsAfter.join(",") + " → " + ballsReset.join(","));

  // --- пресеты применяются к открытой вкладке ---
  tab("oi");
  await sleep(400);
  const preset = byId("min-usd-presets").querySelector('button[data-v="25000"]');
  click(preset);
  await sleep(700);
  check("пресет на вкладке OI меняет порог OI", (() => {
    try { return win.localStorage.getItem("liqscope.minOi") === "25000"; }
    catch (e) { return false; }
  })(), (() => { try { return win.localStorage.getItem("liqscope.minOi"); } catch (e) { return "?"; } })());
  check("пресет на вкладке OI не меняет порог CVD", (() => {
    try { return win.localStorage.getItem("liqscope.minCvd") === "20000"; }
    catch (e) { return false; }
  })());
  check("поле открытой вкладки подсвечено", byId("min-oi-input").classList.contains("filter-active"),
        byId("min-oi-input").className);

  check("без ошибок в консоли", errors.length === 0, errors.slice(0, 2).join(" | "));

  await dom.window.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  if (fail) process.exitCode = 1;
}

main().catch((e) => {
  console.error("тест упал:", e && e.stack || e);
  process.exitCode = 1;
});
