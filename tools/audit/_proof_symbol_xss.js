// Доказательство: символ из /api/symbols попадает в innerHTML без экранирования.
const { JSDOM, VirtualConsole } = require("jsdom");
process.on("uncaughtException", (e) => console.log("uncaught:", String(e.message).slice(0,120)));
process.on("unhandledRejection", () => {});
const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";

(async () => {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message).slice(0, 160)));
  vc.on("error", () => {});

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      win.requestIdleCallback = win.requestIdleCallback ||
        ((cb) => win.setTimeout(() => cb({ didTimeout: false, timeRemaining: () => 0 }), 0));
    },
  });
  const win = dom.window;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const t0 = Date.now();
  let dd = null, injected = null, rows = 0;
  while (Date.now() - t0 < 25000) {
    await wait(400);
    dd = win.document.querySelector("#symbol-dropdown");
    if (!dd) continue;
    rows = dd.querySelectorAll(".symbol-option").length;
    const img = dd.querySelector("img");
    if (img) { injected = img; break; }
    if (rows) break;
  }
  console.log("строк в дропдауне:", rows);
  if (injected) {
    console.log("ЗЛО: в дропдаун вставлен РЕАЛЬНЫЙ тег <img>:", injected.outerHTML.slice(0, 140));
    console.log("     атрибут onerror:", injected.getAttribute("onerror") || injected.getAttribute("ONERROR"));
    console.log("     HTML строки:", injected.closest(".symbol-option").innerHTML.slice(0, 220));
  } else {
    console.log("инъекции нет; HTML дропдауна:", dd ? dd.innerHTML.slice(0, 200) : "нет элемента");
  }
  // Попутно: есть ли payload-символ в /api/symbols и в списке приложения
  const sym = await (await fetch(URL_BASE + "/api/symbols")).json();
  console.log("custom_symbols на сервере:", JSON.stringify(sym.custom_symbols));
  console.log("ошибок страницы (jsdomError):", errors.filter((e) => !/gtag|googletagmanager/.test(e)).slice(0, 3));
  process.exit(0);
})();
