// Шаг B: как рендерится символ, который сервер разослал в WS-init (payload из шага A).
const { JSDOM, VirtualConsole } = require("jsdom");
const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
process.on("uncaughtException", (e) => console.log("uncaught:", String(e.message).slice(0, 120)));
process.on("unhandledRejection", () => {});

const PAYLOAD = "<IMG SRC=X ONERROR=ALERT(1)>_USDT";

(async () => {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on("jsdomError", (e) => errors.push(String(e.message).slice(0, 120)));
  vc.on("error", () => {});
  const noop = () => {};
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      win.requestIdleCallback = (cb) => win.setTimeout(() => cb({ didTimeout: false, timeRemaining: () => 0 }), 0);
      const ctxDummy = new Proxy({}, {
        get(_t, p) { if (p === "measureText") return (s) => ({ width: String(s).length * 5 });
          if (p === "canvas") return { width: 900, height: 500 };
          if (p === "createLinearGradient" || p === "createRadialGradient")
            return () => ({ addColorStop() {} });
          return typeof p === "string" ? noop : undefined; },
        set() { return true; },
      });
      win.HTMLCanvasElement.prototype.getContext = () => ctxDummy;
      const realFetch = win.fetch ? win.fetch.bind(win) : fetch;
      win.fetch = async (u, o) => {
        const url = String(u);
        if (url.indexOf("/api/") === 0) return { ok: true, status: 200, json: async () => ({}) };
        return realFetch(url, o);
      };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null, onclose: null, onerror: null,
                       send() {}, close() {}, addEventListener() {}, removeEventListener() {} };
        setTimeout(() => {
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify({
            type: "init",
            symbols: ["BTC_USDT", PAYLOAD],           // ровно то, что рассылает сервер (шаг A)
            custom_symbols: [PAYLOAD],
            details: [{ symbol: PAYLOAD, volAvg7d: 0 }],
            prices: {}, recent_liquidations: [], exchanges: ["BINANCE"], stats: {},
          }) });
        }, 30);
        return sock;
      };
      win.WebSocket.OPEN = 1; win.WebSocket.CLOSED = 3;
      win.scrollTo = noop;
    },
  });
  const win = dom.window;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const t0 = Date.now();
  let dd = null, img = null, rows = 0;
  while (Date.now() - t0 < 20000) {
    await wait(300);
    dd = win.document.querySelector("#symbol-dropdown");
    if (!dd) continue;
    rows = dd.querySelectorAll(".symbol-option").length;
    img = dd.querySelector("img");
    if (img) break;
  }
  console.log("строк в дропдауне:", rows);
  if (img) {
    console.log("ЗЛО: символ превратился в РЕАЛЬНЫЙ элемент DOM:", img.outerHTML);
    console.log("     onerror =", JSON.stringify(img.getAttribute("onerror")));
    console.log("     HTML строки:", img.closest(".symbol-option").innerHTML.slice(0, 200));
  } else {
    console.log("инъекции нет; HTML:", dd ? dd.innerHTML.slice(0, 200) : "нет дропдауна");
  }
  console.log("ошибки страницы (без gtag/canvas):",
    errors.filter((e) => !/gtag|googletagmanager|canvas/i.test(e)).slice(0, 3));
  process.exit(0);
})();
