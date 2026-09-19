/**
 * Плашка о cookie для новых гостей: показывается один раз и на всех страницах.
 *
 * Запуск (сервер уже на 127.0.0.1:8011):
 *     NODE_PATH=./node_modules node tests/cookie_consent.js [http://127.0.0.1:8011]
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8011";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

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

/** Страница с заглушками сети и (опционально) уже принятой плашкой. */
async function openPage(path, { accepted = false } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    if (msg.indexOf("Could not load link") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(ru(URL_BASE + path), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      stubCanvas(win);
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true }); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
        dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      try { win.localStorage.clear(); } catch (e) {}
      if (accepted) { try { win.localStorage.setItem("liqscope.cookie.ok", "1"); } catch (e) {} }
      win.WebSocket = require("ws");
      win.open = () => ({ closed: false });
      win.fetch = async () => ({ ok: true, status: 200, json: async () => ({ ok: true, user: null }) });
    },
  });
  await new Promise((r) => setTimeout(r, 1400));   // плашка появляется с задержкой
  return { win: dom.window, doc: dom.window.document };
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  console.log("плашка о cookie: новые гости на страницах сайта");

  // --- 1. гость первый раз: плашка есть ---------------------------------
  const first = await openPage("/login");
  const bar = first.doc.querySelector("#cookie-bar");
  check("плашка появилась у нового гостя", !!bar);
  check("плашка подписана и говорит про cookie",
    !!bar && /cookie/i.test(bar.textContent) && /использует/i.test(bar.textContent),
    bar ? bar.textContent.slice(0, 80) : "нет");
  check("объяснено, зачем cookie",
    !!bar && (bar.textContent.indexOf("в аккаунте") !== -1 ||
              bar.textContent.indexOf("посещаемость") !== -1),
    bar ? bar.textContent.slice(0, 160) : "нет");
  check("есть кнопка согласия", !!first.doc.querySelector("#cookie-bar .cookie-ok"),
    first.doc.querySelector("#cookie-bar .cookie-ok") &&
    first.doc.querySelector("#cookie-bar .cookie-ok").textContent);
  check("кнопка подписана по-русски",
    first.doc.querySelector("#cookie-bar .cookie-ok").textContent === "Понятно");
  check("список cookie по умолчанию свёрнут",
    first.doc.querySelector("#cookie-bar .cookie-list").hidden === true);
  check("плашка висит внизу и видна (класс on)",
    bar.classList.contains("on"),
    first.win.getComputedStyle(bar).transform);
  check("плашка полупрозрачная поверх страницы",
    first.win.getComputedStyle(bar).position === "fixed",
    first.win.getComputedStyle(bar).position);

  // --- 2. «Что именно хранится» раскрывает список ------------------------
  const moreBtn = first.doc.querySelector("#cookie-bar .cookie-more-btn");
  moreBtn.dispatchEvent(new first.win.MouseEvent("click", { bubbles: true, cancelable: true, view: first.win }));
  await wait(50);
  const list = first.doc.querySelector("#cookie-bar .cookie-list");
  check("по кнопке «Что именно хранится» список раскрывается", list.hidden === false);
  check("в списке названы реальные cookie сессии и гостя",
    /liqscope_sid/.test(list.textContent) && /liqscope_vid/.test(list.textContent),
    list.textContent.replace(/\s+/g, " ").slice(0, 120));

  // --- 3. «Понятно» прячет и запоминает ---------------------------------
  first.doc.querySelector("#cookie-bar .cookie-ok")
    .dispatchEvent(new first.win.MouseEvent("click", { bubbles: true, cancelable: true, view: first.win }));
  await wait(400);
  check("после «Понятно» плашка уходит со страницы",
    !first.doc.querySelector("#cookie-bar"));
  check("выбор записан в localStorage",
    first.win.localStorage.getItem("liqscope.cookie.ok") === "1",
    first.win.localStorage.getItem("liqscope.cookie.ok"));

  // --- 4. вернувшийся гость плашку не видит ------------------------------
  const back = await openPage("/login", { accepted: true });
  check("вернувшемуся гостю плашка не показывается",
    !back.doc.querySelector("#cookie-bar"));

  // --- 5. плашка есть на всех страницах сайта ----------------------------
  for (const path of ["/terminal", "/cabinet", "/admin", "/"]) {
    const page = await openPage(path);
    const el = page.doc.querySelector("#cookie-bar");
    check("плашка на странице " + path, !!el,
      el ? "есть" : page.doc.body.textContent.slice(0, 60));
    if (el) {
      check("и только одна на " + path,
        page.doc.querySelectorAll(".cookie-bar").length === 1);
    }
  }

  check("ошибок страниц нет", errors.length === 0, errors.slice(0, 3).join(" | "));
}

(async () => {
  try { await main(); } catch (e) { fail++; console.log("  FAIL исключение: " + e.message); }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
