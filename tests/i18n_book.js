require("./_dom_env");
// Локали стакана/ленты/индикаторов: все 5 языков, без утечек русского и сырых ключей.
// Запуск: NODE_PATH=/tmp/smoke/node_modules node tests/i18n_book.js http://127.0.0.1:8011

const { JSDOM, VirtualConsole } = require("jsdom");
const BASE = process.argv[2];
const errs = [];
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

(async () => {
  const html = await (await fetch(BASE + "/terminal")).text();
  const results = {};
  for (const lang of ["ru","en","zh","hi","es"]) {
    const vc = new VirtualConsole(); vc.on("jsdomError", (e) => errs.push(lang + ": " + e.message));
    const dom = await JSDOM.fromURL(BASE + "/terminal?lang=" + lang, { runScripts: "dangerously", resources: "usable",
      pretendToBeVisual: true, virtualConsole: vc,
      beforeParse(win) {
        win.matchMedia = () => ({ matches: false, media: "", onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
        if (!win.ResizeObserver) win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
        const dummy = noopCtx();
        win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
        win.WebSocket = function(){ return { readyState: 3, send(){}, close(){} }; }; win.WebSocket.OPEN=1; win.WebSocket.CLOSED=3;
        win.fetch = async (u) => ({ ok: true, status: 200, json: async () => (String(u).includes("/api/klines") ? { candles: [] } : String(u).includes("/api/book") ? { ok: true, walls: [], min_usd: 150000 } : String(u).includes("/api/liquidations") ? { liquidations: [], total: 0 } : {}) });
        try { win.localStorage.setItem("liqscope.lang", lang); win.localStorage.setItem("liqscope.devLayers", "1"); } catch(e){}
      }});
    await new Promise(r => setTimeout(r, 5000));
    const d = dom.window.document, $ = (s) => d.querySelector(s);
    const I = dom.window.LiqScopeI18n;
    results[lang] = {
      html_lang: d.documentElement.lang, title: d.title.slice(0, 60),
      desc: ($('meta[name="description"]').content || "").slice(0, 50),
      og_locale: $('meta[property="og:locale"]').content,
      book_btn: ($("#book-toggle") || {}).textContent, tab: ($("#feed-tab-book") || {}).textContent,
      th_book: I.t("filter.th_book"), ph_book: ($("#min-book-input") || {}).placeholder,
      modal_title: I.t("modal.book_title"), comp: I.t("modal.book_comp"),
      live: I.t("feed.book_live"), eaten: I.t("feed.book_eaten"), gone: I.t("feed.book_gone"),
      empty_book: I.t("feed.empty_book").slice(0, 40), ind_oi: I.t("ind.oi_hint", {last: "1M"}),
      card10: I.t("land.card10.title"), nodata: I.t("ind.nodata"),
    };
    dom.window.close();
  }
  for (const [l, r] of Object.entries(results)) console.log(l, JSON.stringify(r));
  const bad = [];
  for (const [l, r] of Object.entries(results)) for (const [k, v] of Object.entries(r)) {
    if (!v || v === "undefined") bad.push(l + "." + k + "=EMPTY");
    if (l !== "ru" && typeof v === "string" && /[а-яё]/i.test(v)) bad.push(l + "." + k + " RU-LEAK: " + v);
    if (typeof v === "string" && /^[a-z]+\.[a-z_.]+$/.test(v)) bad.push(l + "." + k + " RAW-KEY: " + v);
  }
  console.log(bad.length ? "PROBLEMS:\n  " + bad.join("\n  ") : "ALL LOCALES OK");
  const real = errs.filter(e => !/gtag|ResizeObserver|Not implemented/.test(e)); if (real.length) console.log("JS errors:", real.slice(0,3));
})();
