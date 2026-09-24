const { JSDOM } = require("jsdom");
const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
process.on("uncaughtException", () => {});
(async () => {
  const dom = await JSDOM.fromURL(URL_BASE + "/cabinet", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: new (require("jsdom").VirtualConsole)(),
    beforeParse(win) {
      Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true });
      win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
      win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      if (!win.fetch) win.fetch = fetch;
    },
  });
  const win = dom.window;
  await new Promise((r) => setTimeout(r, 4000));
  console.log("UA jsdom:", win.navigator.userAgent.slice(0, 60));
  console.log("navigator.language:", win.navigator.language);
  console.log("LIQSCOPE_LANG =", win.LIQSCOPE_LANG, "| LIQSCOPE_LANG_AUTO =", win.LIQSCOPE_LANG_AUTO);
  const html = await (await fetch(URL_BASE + "/cabinet", { headers: { "user-agent": win.navigator.userAgent } })).text();
  const m = html.match(/window\.LIQSCOPE_LANG(?:_AUTO)? = [^;]*/g) || [];
  console.log("в HTML для этого UA:", m);
  const body = win.document.body.innerText || "";
  console.log("есть русские подписи в тексте страницы:", /Загрузка|Кабинет|Войти|Монеты/.test(body));
  process.exit(0);
})();
