/**
 * Язык сайта по умолчанию: подстраивается ли он под гостя.
 *
 *  Сервер выбирает язык так: ссылка ?lang= → cookie выбора → язык браузера →
 *  страна по заголовку CDN → русский. Браузеру он верит только у людей:
 *  поисковикам и превью ссылок отдаёт язык по умолчанию, иначе выдача
 *  «прыгала» бы вместе с языком робота. Когда выбор сделан автоматически,
 *  сервер помечает страницу LIQSCOPE_LANG_AUTO — клиент в этом случае может
 *  уточнить язык по браузеру, а на явный выбор не покушается.
 *
 *  Проверяем именно клиентскую половину: как i18n.js выбирает язык из того,
 *  что положили сервер, ссылка, localStorage и navigator.
 *
 *      NODE_PATH=./node_modules node tests/lang_auto.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "i18n.js"), "utf-8");

let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

/** Страница с заданными сервером, ссылкой, localStorage и языком браузера. */
function page(opts) {
  const o = opts || {};
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "https://liqscope.online" + (o.search || "/"),
    runScripts: "outside-only",
  });
  const win = dom.window;
  win.LIQSCOPE_I18N_PAGES = null;
  if (o.serverLang) win.LIQSCOPE_LANG = o.serverLang;
  if (o.auto) win.LIQSCOPE_LANG_AUTO = 1;
  try {
    Object.defineProperty(win.navigator, "language",
      { value: o.nav || "ru-RU", configurable: true });
    Object.defineProperty(win.navigator, "languages",
      { value: o.navs || [o.nav || "ru-RU"], configurable: true });
  } catch (e) { /* ignore */ }
  if (o.saved) {
    try { win.localStorage.setItem("liqscope.lang", o.saved); } catch (e) { /* ignore */ }
  }
  win.eval(SRC);
  win.LiqScopeI18n.init();
  return win;
}

function langOf(opts) {
  return page(opts).LiqScopeI18n.lang();
}

console.log("язык сайта по умолчанию: браузер, страна и выбор гостя");

// --- гость из Америки: браузер английский ------------------------------------
check("английский браузер — английский сайт",
  langOf({ serverLang: "en", auto: true, nav: "en-US" }) === "en");

// --- сервер подобрал язык по стране, браузер молчит --------------------------
check("браузер просит неподдерживаемый язык — остаётся выбор сервера по стране",
  langOf({ serverLang: "en", auto: true, nav: "de-DE" }) === "en",
  langOf({ serverLang: "en", auto: true, nav: "de-DE" }));
check("гость из Испании (браузер es) — испанский",
  langOf({ serverLang: "es", auto: true, nav: "es-ES" }) === "es");

// --- русскоязычный гость в Германии -----------------------------------------
check("русский браузер сильнее страны",
  langOf({ serverLang: "ru", auto: true, nav: "ru-RU" }) === "ru");

// --- явный выбор: ссылка и прошлый выбор гостя -------------------------------
check("ссылка ?lang=zh сильнее языка браузера",
  langOf({ search: "/terminal?lang=zh", serverLang: "zh", auto: true, nav: "en-US" }) === "zh");
check("прошлый выбор гостя (localStorage) не перебивается браузером",
  langOf({ serverLang: "en", auto: true, nav: "en-US", saved: "hi" }) === "hi");

// --- роботы и превью ссылок: язык по умолчанию -------------------------------
check("роботу страница отдаётся на языке по умолчанию",
  langOf({ serverLang: "ru", auto: false, nav: "en-US" }) === "ru");
check("для робота язык браузера не учитывается, даже если он английский",
  langOf({ serverLang: "ru", auto: false, nav: "en-US", navs: ["en-US", "en"] }) === "ru");

// --- что-то не то в подсказке сервера ----------------------------------------
check("неизвестный язык сервера игнорируется",
  langOf({ serverLang: "klingon", auto: true, nav: "fr-FR" }) === "ru");
check("нет подсказок вовсе — русский по умолчанию",
  langOf({ nav: "de-DE" }) === "ru");

// --- смена языка гостем ------------------------------------------------------
const win = page({ serverLang: "en", auto: true, nav: "en-US" });
win.LiqScopeI18n.set("es");
check("гость переключил язык — он и применился", win.LiqScopeI18n.lang() === "es");
check("выбор гостя запомнился в localStorage",
  win.localStorage.getItem("liqscope.lang") === "es",
  win.localStorage.getItem("liqscope.lang"));
check("заголовок страницы тоже на выбранном языке",
  /Resúmenes|LiqScope/.test(win.document.title) ||
  win.document.documentElement.lang === "es-ES",
  win.document.documentElement.lang);

console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
process.exit(fail ? 1 : 0);
