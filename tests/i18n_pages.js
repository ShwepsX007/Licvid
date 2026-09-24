require("./_dom_env");
/**
 * Языки страниц в браузере (jsdom): вход, лендинг и кабинет.
 *
 * Проверяем то, чего не видит сервер: после загрузки словарей весь видимый
 * текст переводится на выбранный язык, а переключатель языка перерисовывает
 * страницу без перезагрузки.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom ws
 *     node tests/i18n_pages.js [http://127.0.0.1:8000]
 */

const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;

function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const CYR = /[А-Яа-яЁё]/;

function stubCanvas(win) {
  const noop = () => {};
  const ctx = new Proxy({}, {
    get(_t, prop) {
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
}

async function openPage(path) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));
  const dom = await JSDOM.fromURL(URL_BASE + path, {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      stubCanvas(win);
      // браузер русский: страница обязана показать то, что выбрал сервер
      // (?lang=), а не язык системы
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true }); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      win.WebSocket = require("ws");
      win.open = () => ({ closed: false });
      win.fetch = async () => ({ ok: true, status: 200, json: async () => ({ ok: true, user: null, services: [] }) });
    },
  });
  await new Promise((r) => setTimeout(r, 900));
  return { win: dom.window, doc: dom.window.document };
}

/** Меняем язык переключателем: как это делает человек руками. */
async function switchTo(page, code) {
  const sel = page.doc.querySelector("[data-lang-select], #lang-select");
  if (!sel) return null;
  sel.value = code;
  sel.dispatchEvent(new page.win.Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 400));
  return sel.value;
}

async function main() {
  console.log("языки страниц: /login, / и /cabinet");

  // --- 1. вход: сервер отдаёт китайский, страница обязана быть китайской ---
  const zh = await openPage("/login?lang=zh");
  const zhBtn = zh.doc.querySelector("#email-submit");
  check("вход: <html lang> — китайский",
    zh.doc.documentElement.lang.indexOf("zh") === 0, zh.doc.documentElement.lang);
  check("вход: заголовок вкладки переведён",
    !CYR.test(zh.doc.title) && zh.doc.title.indexOf("LiqScope") !== -1, zh.doc.title);
  check("вход: кнопка «Войти» по-китайски",
    zhBtn && !CYR.test(zhBtn.textContent) && zhBtn.textContent.trim().length > 0,
    zhBtn && zhBtn.textContent);
  check("вход: вкладка «Регистрация» по-китайски",
    !CYR.test(zh.doc.querySelector('.auth-tab[data-tab="register"]').textContent));
  check("вход: описание страницы в <meta> без русского",
    !CYR.test(zh.doc.querySelector('meta[name="description"]').getAttribute("content")));
  check("вход: подсказка в поле пароля переведена",
    !CYR.test(zh.doc.querySelector("#in-password").getAttribute("placeholder")),
    zh.doc.querySelector("#in-password").getAttribute("placeholder"));

  // --- 2. переключатель языка работает без перезагрузки ---
  const switched = await switchTo(zh, "en");
  check("переключатель выставил английский", switched === "en", switched);
  check("вход: кнопка стала английской",
    !CYR.test(zh.doc.querySelector("#email-submit").textContent),
    zh.doc.querySelector("#email-submit").textContent);
  check("вход: <html lang> переключился",
    zh.doc.documentElement.lang.indexOf("en") === 0, zh.doc.documentElement.lang);
  check("вход: язык запомнен в localStorage",
    zh.win.localStorage.getItem("liqscope.lang") === "en",
    zh.win.localStorage.getItem("liqscope.lang"));
  check("вход: cookie для сервера выставлена",
    String(zh.doc.cookie).indexOf("liqscope_lang=en") !== -1, zh.doc.cookie);
  zh.win.close();

  // --- 3. лендинг на испанском ---
  const es = await openPage("/?lang=es");
  const h1 = es.doc.querySelector('[data-i18n="land.h1.1"]');
  check("лендинг: заголовок по-испански",
    h1 && !CYR.test(h1.textContent) && h1.textContent.trim().length > 0,
    h1 && h1.textContent);
  check("лендинг: подпись у switcher'а выбрана",
    es.doc.querySelector("#lang-select").value === "es",
    es.doc.querySelector("#lang-select").value);
  check("лендинг: строки статистики переведены",
    !CYR.test(es.doc.querySelector('[data-i18n="land.stat.24h"]').textContent));
  es.win.close();

  // --- 4. кабинет: статичная разметка переводится вместе со страницей ---
  const cab = await openPage("/cabinet?lang=en");
  const services = cab.doc.querySelector('[data-i18n="cab.services"]');
  check("кабинет: заголовок «Сервисы» по-английски",
    services && !CYR.test(services.textContent), services && services.textContent);
  check("кабинет: кнопка выхода переведена",
    !CYR.test(cab.doc.querySelector("#cab-logout").textContent),
    cab.doc.querySelector("#cab-logout").textContent);
  check("кабинет: переключатель языка есть",
    !!cab.doc.querySelector("[data-lang-select]"));
  check("кабинет: панель сервисов ждёт данные",
    !!cab.doc.querySelector("#svc-acc"));
  cab.win.close();

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  errors.slice(0, 10).forEach((e) => console.log("  ! " + e));
  process.exit(fail || errors.length ? 1 : 0);
}

main().catch((e) => { console.error("Тест упал:", e.message); process.exit(1); });
