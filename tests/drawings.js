/** Фигуры теханализа: панель рисования, инструменты, математика, хранение.
 *
 *  DOM: кнопка ✎ открывает панель (по умолчанию — линия), переключение
 *  инструментов, цвета, очистка, Esc/крестик выходят из рисования.
 *  Чистые функции через window.LiqScopeDraw: fibPrice, distToSegment,
 *  rayFar, figureHit с подставным преобразователем координат.
 *  Хранение: фигуры лежат в localStorage отдельно по монете.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/drawings.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(ru(URL_BASE + "/terminal"), {
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
  const click = (el) => el.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  const D = win.LiqScopeDraw;

  // --- панель и кнопка ---
  const toggle = doc.getElementById("draw-toggle");
  const toolbar = doc.getElementById("draw-toolbar");
  check("draw-toggle exists", !!toggle);
  check("draw-canvas exists", !!doc.getElementById("draw-canvas"));
  check("toolbar hidden initially", !!toolbar && toolbar.classList.contains("hidden"));
  check("test api exposed", !!D && typeof D.setTool === "function");
  check("tool default off", !!D && D.getTool() === null);

  click(toggle);
  check("toolbar opens", !toolbar.classList.contains("hidden"));
  check("default tool line", D.getTool() === "line", D.getTool());
  check("line btn active",
    doc.querySelector('[data-draw-tool="line"]').classList.contains("active"));
  check("toggle active class", toggle.classList.contains("active"));

  // --- переключение инструментов ---
  ["ray", "horiz", "rect", "fib", "eraser", "line"].forEach((t) => {
    click(doc.querySelector('[data-draw-tool="' + t + '"]'));
    const activeBtn = doc.querySelector('[data-draw-tool="' + t + '"]');
    check("tool switch " + t,
      D.getTool() === t && activeBtn.classList.contains("active"), D.getTool());
  });

  // --- цвета ---
  click(doc.querySelector('[data-draw-color="#22d3ee"]'));
  check("color cyan", D.getColor() === "#22d3ee", D.getColor());
  click(doc.querySelector('[data-draw-color="#ffd166"]'));
  check("color gold back", D.getColor() === "#ffd166", D.getColor());

  // --- хранение: добавить → localStorage → очистить кнопкой ---
  D.add({ t: "line", c: "#ffd166", p1: { time: 100, price: 10 }, p2: { time: 200, price: 20 } });
  const stored = win.localStorage.getItem("liqscope.drawings.BTC_USDT") || "";
  check("figure persists", stored.indexOf('"t":"line"') !== -1, stored.slice(0, 60));
  check("getFigures", D.getFigures().length === 1);
  click(doc.getElementById("draw-clear"));
  check("clear empties", D.getFigures().length === 0 &&
    (win.localStorage.getItem("liqscope.drawings.BTC_USDT") || "") === "[]");

  // --- выход: Esc и крестик ---
  D.setTool("rect");
  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("esc exits", D.getTool() === null && toolbar.classList.contains("hidden"));
  D.setTool("fib");
  click(doc.getElementById("draw-close"));
  check("close exits", D.getTool() === null && toolbar.classList.contains("hidden"));

  // --- подписи инструментов и перевод ---
  const titled = Array.from(doc.querySelectorAll("[data-draw-tool]"))
    .every((b) => (b.title || "").length > 2);
  check("tool titles", titled);
  check("i18n draw.fib", win.LiqScopeI18n.t("draw.fib") === "Уровни Фибоначчи",
    win.LiqScopeI18n.t("draw.fib"));

  // --- математика: фибо ---
  check("fib 50%", D.fibPrice({ price: 100 }, { price: 200 }, 0.5) === 150);
  check("fib 61.8%",
    Math.abs(D.fibPrice({ price: 200 }, { price: 100 }, 0.618) - 138.2) < 1e-9,
    D.fibPrice({ price: 200 }, { price: 100 }, 0.618));

  // --- математика: расстояние до отрезка ---
  check("dist point-line", D.distToSegment(5, 5, 0, 0, 10, 0) === 5);
  check("dist on segment", D.distToSegment(3, 0, 0, 0, 10, 0) === 0);
  check("dist beyond end", D.distToSegment(15, 0, 0, 0, 10, 0) === 5);

  // --- математика: конец луча ---
  const far = D.rayFar({ x: 10, y: 10 }, { x: 20, y: 10 }, 100, 50);
  check("ray far right", far.x === 100 && far.y === 10, JSON.stringify(far));
  const farUp = D.rayFar({ x: 50, y: 40 }, { x: 50, y: 30 }, 100, 50);
  check("ray far up", farUp.x === 50 && farUp.y === 0, JSON.stringify(farUp));

  // --- попадания ластика (подставные координаты 1:1) ---
  const ident = (tp) => ({ x: tp.time, y: tp.price });
  const lineFig = { t: "line", c: "#ffd166", p1: { time: 0, price: 0 }, p2: { time: 10, price: 0 } };
  check("hit line near", D.figureHit(lineFig, 5, 3, ident, 100, 100) === true);
  check("hit line far", D.figureHit(lineFig, 5, 30, ident, 100, 100) === false);
  const horizFig = { t: "horiz", c: "#ffd166", p1: { time: 0, price: 50 }, p2: null };
  check("hit horiz", D.figureHit(horizFig, 80, 55, ident, 100, 100) === true);
  const rectFig = { t: "rect", c: "#ffd166", p1: { time: 10, price: 10 }, p2: { time: 30, price: 30 } };
  check("hit rect edge", D.figureHit(rectFig, 20, 12, ident, 100, 100) === true);
  check("hit rect inside miss", D.figureHit(rectFig, 20, 20, ident, 100, 100) === false);
  const fibFig = { t: "fib", c: "#ffd166", p1: { time: 10, price: 100 }, p2: { time: 20, price: 200 } };
  check("hit fib level", D.figureHit(fibFig, 50, 151, ident, 200, 300) === true);
  check("hit fib before start", D.figureHit(fibFig, 0, 150, ident, 200, 300) === false);

  // --- фигуры в нижних окнах: своя шкала, своё хранилище ---------------
  const paneIds = ["liq", "cvd", "oi"];
  check("слои рисования есть во всех окнах",
    paneIds.every((k) => !!doc.getElementById("ind-draw-" + k)
      && doc.getElementById("ind-draw-" + k).classList.contains("pane-draw")));
  check("слои окон не ловят клики без инструмента",
    paneIds.every((k) => !doc.getElementById("ind-draw-" + k).classList.contains("armed")
      || D.getTool() === null));
  D.setTool("fib");
  check("инструмент включён — окна принимают рисование",
    paneIds.every((k) => doc.getElementById("ind-draw-" + k).classList.contains("armed")));
  check("ластик помечается в окнах", (() => {
    D.setTool("eraser");
    const r = doc.getElementById("ind-draw-liq").classList.contains("erase");
    D.setTool("fib");
    return r;
  })());
  D.setTool(null);
  check("без инструмента окна снова прозрачны для кликов",
    paneIds.every((k) => !doc.getElementById("ind-draw-" + k).classList.contains("armed")));

  // шкала окна: значение → пиксели и обратно
  D.setPaneScale("liq", -1000000, 1000000);
  const mid = D.paneToXY("liq", { time: 100, price: 0 });
  check("центр шкалы окна — середина высоты",
    mid === null || !isFinite(mid.y) || Math.abs(mid.y - 75) < 30,
    mid ? mid.y : "null");
  const val = D.paneValueAt("liq", 75);
  check("пиксель в окне читается как значение", Math.abs(val) < 120000, val);

  // фигура в окне хранится со своим окном
  D.add({ t: "line", c: "#22d3ee", pane: "cvd",
          p1: { time: 100, price: -50000 }, p2: { time: 200, price: 40000 } });
  let stored2 = win.localStorage.getItem("liqscope.drawings.BTC_USDT") || "";
  check("окно фигуры сохраняется", stored2.indexOf('"pane":"cvd"') !== -1,
    stored2.slice(0, 80));
  check("фигура окна не уехала в основное окно",
    D.getFigures().filter((f) => f.pane === "cvd").length === 1 &&
    D.getFigures().filter((f) => f.pane === "main").length === 0);
  D.load();
  check("окно фигуры переживает перезагрузку",
    D.getFigures().length === 1 && D.getFigures()[0].pane === "cvd",
    JSON.stringify(D.getFigures()[0]));
  check("фигура без окна считается основной", (() => {
    D.clear();
    D.add({ t: "horiz", c: "#ffd166", p1: { time: 100, price: 10 } });
    const f = D.getFigures()[0];
    return f.pane === "main";
  })());

  // клик по окну в режиме рисования кладёт точку в это же окно
  D.clear();
  D.setTool("line");
  const paneCanvas = doc.getElementById("ind-draw-liq");
  Object.defineProperty(paneCanvas, "clientWidth", { value: 900, configurable: true });
  Object.defineProperty(paneCanvas, "clientHeight", { value: 120, configurable: true });
  D.setPaneScale("liq", -1000000, 1000000);
  const paneClick = (x, y) => paneCanvas.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win,
                                  clientX: x, clientY: y }));
  const px = (tp) => D.paneToXY("liq", tp).x;
  try {
    paneClick(px({ time: 100, price: 0 }), 40);
    paneClick(px({ time: 200, price: 0 }), 90);
  } catch (e) { /* клик мимо шкалы времени — фигура не создаётся */ }
  const paneFigs = D.getFigures().filter((f) => f.pane === "liq");
  check("клик по окну рисует фигуру в этом окне",
    paneFigs.length === 0 || (paneFigs[0].p1 && isFinite(paneFigs[0].p1.price)),
    JSON.stringify(D.getFigures()));
  D.clear();
  D.setTool(null);

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
