/** Раскладка легенды: ячейки кнопка+цифра, кнопки не рвутся, цифры не толкают.
 *
 *  Структура: каждая кнопка слоя и её живая цифра лежат в .layer-cell,
 *  цифра — сразу за кнопкой. Кнопки: nowrap, inline-flex, line-height 1.3
 *  (текст не вылезает), активный слой подсвечен цветом своих фигур.
 *  Цифры: inline-block, ширина строго по содержимому (без min-width —
 *  никаких пустот), стабильность даёт моноширинный шрифт + tabular-nums
 *  (все цифры равной ширины — кнопки не скачут). Мобильное правило
 *  2-в-ряд проверяем по тексту CSS (jsdom не применяет media-запросы).
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/legend_layout.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const http = require("http");

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
function httpGet(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => resolve(body));
    }).on("error", reject);
  });
}

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
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.WebSocket = function () { return { readyState: 3, send() {}, close() {} }; };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/klines") === 0) body = { symbol: "BTC_USDT", candles: [] };
        else if (u.indexOf("/api/stats") === 0) body = {};
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await sleep(6000);
  const win = dom.window;
  const doc = win.document;
  const cs = (el) => win.getComputedStyle(el);
  const pairs = [["liq-toggle", "liq-stat"], ["profile-toggle", "profile-stat"],
                 ["cvd-toggle", "cvd-stat"], ["oi-toggle", "oi-stat"]];

  // --- структура ячеек ---
  check("4 layer cells", doc.querySelectorAll(".chart-legend .layer-cell").length === 4);
  pairs.forEach(([b, s]) => {
    const btn = doc.getElementById(b), stat = doc.getElementById(s);
    check(b + " wrapped with stat",
      btn.parentElement.classList.contains("layer-cell") &&
      btn.parentElement === stat.parentElement &&
      btn.nextElementSibling === stat);
  });

  // --- кнопки не рвут текст ---
  pairs.forEach(([b]) => {
    const st = cs(doc.getElementById(b));
    check(b + " nowrap flex", st.whiteSpace === "nowrap" && st.display === "inline-flex",
      st.whiteSpace + "/" + st.display);
  });
  check("button line-height", cs(doc.getElementById("liq-toggle")).lineHeight === "1.3",
    cs(doc.getElementById("liq-toggle")).lineHeight);
  check("cell no shrink", cs(doc.querySelector(".layer-cell")).flexShrink === "0");

  // --- активные цвета слоёв (всё включено по умолчанию) ---
  const color = (id) => cs(doc.getElementById(id)).color;
  check("liq active gold", color("liq-toggle") === "rgb(255, 209, 102)", color("liq-toggle"));
  check("cvd active violet", color("cvd-toggle") === "rgb(167, 139, 250)", color("cvd-toggle"));
  check("oi active green", color("oi-toggle") === "rgb(74, 222, 128)", color("oi-toggle"));
  doc.getElementById("liq-toggle").dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  check("liq off loses gold", color("liq-toggle") !== "rgb(255, 209, 102)", color("liq-toggle"));

  // --- цифры компактные: без зарезервированной ширины ---
  const mw = (id) => cs(doc.getElementById(id)).minWidth;
  ["liq-stat", "profile-stat", "cvd-stat", "oi-stat"].forEach((id) => {
    check(id + " no reserved width",
      mw(id) === "" || mw(id) === "0px" || mw(id) === "auto", JSON.stringify(mw(id)));
  });
  check("stat inline-block", cs(doc.getElementById("liq-stat")).display === "inline-block");

  // --- мобильное правило 2-в-ряд — по тексту CSS ---
  const css = await httpGet(URL_BASE + "/static/style.css");
  const m900 = css.indexOf("@media (max-width: 900px)");
  const cellRule = css.indexOf(".layer-cell", m900);
  const cellBlock = cellRule !== -1 ? css.slice(cellRule, cellRule + 200) : "";
  check("mobile layer-cell rule", m900 !== -1 && cellRule !== -1 &&
    cellBlock.indexOf("flex: 1 1 42%") !== -1);
  check("mobile stat hugs button", cellBlock.indexOf("space-between") === -1 &&
    cellBlock.indexOf("flex-start") !== -1, cellBlock.slice(0, 80));

  // --- стабильность цифр — табличными цифрами, а не запасом ширины ---
  const cssBlock = (sel) => {
    const i = css.indexOf(sel + " {");
    return i === -1 ? "" : css.slice(i, i + 400);
  };
  ["live-stat", "cvd-stat"].forEach((cls) => {
    const b = cssBlock("." + cls);
    check(cls + " tabular-nums, no min-width",
      b.indexOf("tabular-nums") !== -1 && b.indexOf("min-width") === -1,
      b.slice(0, 60));
  });

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
