/** Живая проверка: клиент сам тянет кластеры истории с сервера.
 *
 *  Открывает /terminal на живом сервере (без заглушек сети), ждёт загрузку
 *  свечей и кластеров и печатает, что пришло: сколько свечей с кластерами,
 *  сколько уровней, попадают ли их времена в свечи графика.
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const SYMBOL = process.argv[3] || "BTC_USDT";
const MIN_USD = Number(process.argv[4] || 0);

async function main() {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        win.localStorage.setItem("liqscope.chartSymbol", SYMBOL);
        win.localStorage.setItem("liqscope.devLayers", "1");
        win.localStorage.setItem("liqscope.liqEnabled", "1");
        if (MIN_USD) win.localStorage.setItem("liqscope.minUsd", String(MIN_USD));
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      // jsdom не умеет fetch и WebSocket — берём их у node, чтобы проба шла
      // по настоящей сети (никаких заглушек ответов)
      win.fetch = (url, opts) => {
        const u = String(url);
        return fetch(u.indexOf("http") === 0 ? u : URL_BASE + u, opts);
      };
      const WS = require("ws").WebSocket;
      win.WebSocket = function (url) {
        return new WS(String(url).replace(/^\/\//, "ws://"));
      };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.HTMLCanvasElement.prototype.getContext = function () {
        const noop = () => {};
        return new Proxy({}, {
          get(_t, p) {
            if (p === "measureText") return (s) => ({ width: String(s).length * 5 });
            if (p === "canvas") return { width: 900, height: 500 };
            return typeof p === "string" ? noop : undefined;
          },
          set() { return true; },
        });
      };
    },
  });
  await new Promise((r) => setTimeout(r, 9000));
  const win = dom.window;
  const L = win.LiqScopeLiq;
  if (!L || !L.liqHistRows) {
    console.log("нет тестового API кластеров:", !!L, errors.slice(0, 3));
    process.exit(2);
  }
  const hist = L.liqHistRows() || {};
  const keys = Object.keys(hist);
  const candles = L.candles().map((c) => Number(c.time));
  const inCandles = keys.filter((k) => candles.indexOf(Number(k)) >= 0);
  let levels = 0, usd = 0, old = 0;
  const now = Math.floor(Date.now() / 1000);
  const oldest = candles.length ? candles[0] : now;
  keys.forEach((k) => {
    const rows = (hist[k] && hist[k].l) || [];
    levels += rows.length;
    rows.forEach((r) => { usd += (r[1] || 0) + (r[2] || 0); });
    if (Number(k) < now - 3600) old++;
  });
  console.log(JSON.stringify({
    symbol: SYMBOL, minUsd: MIN_USD, candles: candles.length, historyBuckets: keys.length,
    bucketsWithCandle: inCandles.length, levels: levels,
    usdTotal: Math.round(usd), bucketsOlderThanHour: old,
    oldestCandle: new Date(oldest * 1000).toISOString(),
    candlesRangeHours: Math.round((candles[candles.length - 1] - oldest) / 3600),
    errors: errors.slice(0, 3),
  }, null, 2));
  await win.close();
  process.exit(errors.length ? 1 : 0);      // сокеты jsdom держат цикл событий
}

main().catch((e) => { console.error("CRASH:", e); process.exit(2); });
