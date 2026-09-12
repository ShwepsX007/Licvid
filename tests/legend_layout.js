/** Раскладка легенды: ячейки кнопка/подпись+цифра, попап кнопок слоёв.
 *
 *  Структура: в плашке у всех слоёв подпись+цифра в .layer-cell,
 *  а все 4 кнопки выезжают сверху горизонтальной панелью по кнопке
 *  «☰ Слои» (закрытие: повторный клик, клик мимо, Esc).
 *  Кнопки: nowrap, inline-flex, line-height 1.3
 *  (текст не вылезает), активный слой подсвечен цветом своих фигур.
 *  Цифры: inline-block, ширина строго по содержимому (без min-width —
 *  никаких пустот), стабильность даёт моноширинный шрифт + tabular-nums
 *  (все цифры равной ширины — кнопки не скачут). На телефоне ячейки
 *  текут в строку как в браузере (без жёсткой сетки — проверяем по тексту
 *  CSS, jsdom не применяет media-запросы). Кнопки свернуть/развернуть
 *  прибиты к правому краю легенды.
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

  // --- структура ячеек: у всех подпись+цифра, кнопки — в попапе ---
  check("4 layer cells", doc.querySelectorAll(".chart-legend .layer-cell").length === 4);
  ["liq-stat", "profile-stat", "cvd-stat", "oi-stat"].forEach((s) => {
    const stat = doc.getElementById(s);
    const prev = stat.previousElementSibling;
    check(s + " label before stat",
      stat.parentElement.classList.contains("layer-cell") &&
      !!prev && prev.classList.contains("layer-label"));
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

  // --- попап кнопок слоёв ---
  const layerCall = doc.getElementById("layer-call");
  const layerPop = doc.getElementById("layer-pop");
  const click = (el) => el.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  check("call btn profile-sized",
    !!layerCall && layerCall.classList.contains("profile-toggle") &&
    layerCall.classList.contains("layer-call"));
  check("call label", win.LiqScopeI18n.t("chart.layers") === "☰ Слои",
    win.LiqScopeI18n.t("chart.layers"));
  check("pop hidden initially", !!layerPop && layerPop.classList.contains("hidden"));
  check("4 buttons in pop",
    ["liq-toggle", "profile-toggle", "cvd-toggle", "oi-toggle"].every((id) =>
      doc.getElementById(id).parentElement === layerPop));
  check("layers api", !!win.LiqScopeLayers && typeof win.LiqScopeLayers.isOpen === "function");
  click(layerCall);
  check("pop opens", win.LiqScopeLayers.isOpen() && !layerPop.classList.contains("hidden"));
  check("pop flex row", cs(layerPop).display === "flex" && cs(layerPop).position === "absolute",
    cs(layerPop).display + "/" + cs(layerPop).position);
  check("pop below header", cs(layerPop).top.indexOf("100%") !== -1, cs(layerPop).top);
  click(layerCall);
  check("pop closes on reclick", !win.LiqScopeLayers.isOpen());
  click(layerCall);
  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("pop closes on esc", !win.LiqScopeLayers.isOpen());
  click(layerCall);
  doc.body.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  check("pop closes outside", !win.LiqScopeLayers.isOpen());

  // --- мобильное: без жёсткой сетки, ячейки текут в строку — по тексту CSS ---
  const css = await httpGet(URL_BASE + "/static/style.css");
  const cssBlock = (sel) => {
    const i = css.indexOf(sel + " {");
    return i === -1 ? "" : css.slice(i, i + 400);
  };
  const m900 = css.indexOf("@media (max-width: 900px)");
  const m560 = css.indexOf("@media (max-width: 560px)");
  const mob900 = m900 !== -1 ? css.slice(m900, m560 !== -1 ? m560 : m900 + 3000) : "";
  check("mobile cells flow free", m900 !== -1 &&
    mob900.indexOf(".layer-cell {") === -1 && mob900.indexOf("42%") === -1,
    "grid-rule:" + (mob900.indexOf(".layer-cell {") !== -1));
  check("stat hugs button", cssBlock(".layer-cell").indexOf("space-between") === -1);

  // --- правый угол: Слои + рисование + свернуть/развернуть, прибиты и не бегают ---
  check("right cluster pinned",
    cssBlock("#layer-call").indexOf("margin-left: auto") !== -1);
  const legendKids = Array.from(doc.querySelector(".chart-legend").children);
  const kidIds = legendKids.map((k) => k.id || k.className);
  check("right cluster order",
    kidIds.slice(-4).join(",") === "layer-call,draw-toggle,chart-toggle,chart-expand",
    kidIds.slice(-4).join(","));
  check("toggles last in legend",
    legendKids.length >= 2 &&
    legendKids[legendKids.length - 2].id === "chart-toggle" &&
    legendKids[legendKids.length - 1].id === "chart-expand");

  // --- стабильность цифр — табличными цифрами, а не запасом ширины ---
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
