/** Индикаторные окна (LIQ/CVD/OI) под графиком терминала.
 *
 * Часть 1 (jsdom): структура, включение/выключение из «☰ Слои»,
 * крестик на окне, сохранение состояния.
 * Часть 2 (без браузера): математика отрисовки — выдёргиваем функции
 * из app.js и гоняем на записывающем 2d-контексте: столбики ликвидаций
 * (лонги вниз/шорты вверх), гистограмма CVD (покупки/продажи),
 * линия OI, сетка и цифры шкалы.
 *
 * Запуск части 1 (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom ws
 *     node tests/ind_panes.js [http://127.0.0.1:8000]
 * Часть 2 всегда выполняется и без сервера.
 */
const fs = require("fs");
const path = require("path");

const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

/* ================= часть 2: математика отрисовки ================= */

function grab(src, kind, name) {
  // kind: "function" | "const"
  const marker = kind === "function" ? "function " + name : "const " + name;
  const i = src.indexOf(marker);
  if (i < 0) throw new Error("не нашёл " + marker);
  let j = i, depth = 0, started = false;
  for (; j < src.length; j++) {
    if (src[j] === "{") { depth++; started = true; }
    else if (src[j] === "}") { depth--; if (started && depth === 0) { j++; break; } }
  }
  return src.slice(i, j);
}

function recordedCtx() {
  const rec = { rects: [], texts: [], lines: [], strokes: [], fills: [], arcs: [] };
  const ctx = {
    _fill: "", _stroke: "", font: "", textAlign: "", textBaseline: "",
    lineWidth: 0,
    set fillStyle(v) { this._fill = v; },
    get fillStyle() { return this._fill; },
    set strokeStyle(v) { this._stroke = v; },
    get strokeStyle() { return this._stroke; },
    clearRect() {},
    save() {}, restore() {},
    beginPath() {}, closePath() {},
    setTransform() {},
    fillRect(x, y, w, h) { rec.rects.push({ x, y, w, h, fill: this._fill }); },
    fillText(t, x, y) { rec.texts.push({ t, x, y }); },
    moveTo(x, y) { rec.lines.push(["M", x, y]); },
    lineTo(x, y) { rec.lines.push(["L", x, y]); },
    arc(x, y, r) { rec.arcs.push({ x, y, r }); },
    stroke() { rec.strokes.push(this._stroke); },
    fill() { rec.fills.push(this._fill); },
  };
  return { ctx, rec };
}

function fakeCanvas(w, h) {
  const { ctx, rec } = recordedCtx();
  const canvas = {
    clientWidth: w, clientHeight: h, width: 0, height: 0,
    parentElement: { clientWidth: w },
    getContext: () => ctx,
    _rec: rec,
  };
  return canvas;
}

function part2() {
  console.log("\nчасть 2: отрисовка окон (выдёргиваем функции из app.js)");
  const src = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");

  const els = {};
  function $(id) {
    if (!els[id]) els[id] = { textContent: "", className: "", title: "", classList: {
      _s: new Set(),
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    } };
    return els[id];
  }

  const tfSec = 300; // 5м
  const t0 = 1700000100; // кратно 300 — как реальные границы свечей
  const PX_PER_BAR = 9; // реальный масштаб: свечи в ~9px
  const candles = [];
  for (let i = 0; i < 40; i++) {
    candles.push({
      time: t0 + i * tfSec,
      cvd: (i % 3 === 0 ? -1 : 1) * (10000 + i * 500),
      oi: 1000000 + i * 2500,
      oiChg: 2500,
      close: 50000,
    });
  }
  const liqs = [];
  for (let i = 0; i < 12; i++) {
    liqs.push({
      timestamp: t0 + Math.floor(i / 2) * tfSec + 5,
      side: i % 2 ? "BUY" : "SELL",
      usd: 20000 + i * 1000,
      symbol: "BTC_USDT", exchange: "binance",
    });
  }

  const canvases = {
    "ind-canvas-liq": fakeCanvas(360, 64),
    "ind-canvas-cvd": fakeCanvas(360, 64),
    "ind-canvas-oi": fakeCanvas(360, 64),
  };
  const $stub = (id) => (canvases[id] ? canvases[id] : $(id));

  const sandbox = {
    $: $stub,
    window: { devicePixelRatio: 1 },
    localStorage: { getItem: () => null, setItem: () => {} },
    I18n: { t: (k) => k, onChange: () => {} },
    state: {
      candles: candles, timeframe: 5,
      paneLiq: true, paneCvd: true, paneOi: true,
    },
    chart: { timeScale: () => ({ timeToCoordinate: (t) => (t - t0) / tfSec * PX_PER_BAR }) },
    visibleLiquidations: () => liqs,
    fmtUsdShort: (v) => {
      v = Number(v) || 0;
      if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
      if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
      return String(Math.round(v));
    },
  };
  const names = ["IND_PANES", "indMoney", "indBarSpacing", "indGrid", "indVerticals",
    "indPrepareCanvas", "indCurve", "indNoData", "indSetVal", "drawPaneLiq",
    "drawPaneCvd", "drawPaneOi", "drawIndicatorPanes", "syncPaneVisibility"];
  const code = names.map((n) =>
    grab(src, n === "IND_PANES" ? "const" : "function", n)).join("\n") +
    "\nreturn { IND_PANES, indMoney, drawPaneLiq, drawPaneCvd, drawPaneOi," +
    " drawIndicatorPanes, syncPaneVisibility };";
  const api = new Function("$", "window", "localStorage", "I18n", "state",
    "chart", "visibleLiquidations", "fmtUsdShort", code)(
    sandbox.$, sandbox.window, sandbox.localStorage, sandbox.I18n, sandbox.state,
    sandbox.chart, sandbox.visibleLiquidations, sandbox.fmtUsdShort);

  // --- LIQ: двусторонние столбики -----------------------------------------
  api.drawPaneLiq();
  const rq = canvases["ind-canvas-liq"]._rec;
  const mid = 32.5; // базовая линия: Math.round(h/2) + 0.5 при высоте 64
  const shortsUp = rq.rects.filter((r) => r.y + r.h <= mid + 0.01 && r.fill.indexOf("0,214,255") >= 0);
  const longsDown = rq.rects.filter((r) => r.y >= mid - 0.01 && r.fill.indexOf("255,42,95") >= 0);
  check("LIQ: столбики шортов вверх (циан)", shortsUp.length > 0);
  check("LIQ: столбики лонгов вниз (красный)", longsDown.length > 0);
  check("LIQ: подписи краёв шкалы", rq.texts.some((t) => t.t.indexOf("▲") === 0) &&
        rq.texts.some((t) => t.t.indexOf("▼") === 0));
  const liqVal = $("ind-liq-val").textContent;
  check("LIQ: сумма в шапке не нулевая", /^Σ \$[1-9]/.test(liqVal), liqVal);
  check("LIQ: канва получила размер", canvases["ind-canvas-liq"].width === 360);

  // --- CVD: гистограмма дельты --------------------------------------------
  api.drawPaneCvd();
  const rc = canvases["ind-canvas-cvd"]._rec;
  const green = rc.rects.filter((r) => r.fill.indexOf("0,230,118") >= 0);
  const red = rc.rects.filter((r) => r.fill.indexOf("255,42,95") >= 0);
  check("CVD: зелёные столбики покупок", green.length > 0);
  check("CVD: красные столбики продаж", red.length > 0);
  check("CVD: нулевая линия", rc.lines.length > 0);
  const cvdVal = $("ind-cvd-val").textContent;
  check("CVD: значение со знаком в шапке", /^[+−]\$/.test(cvdVal), cvdVal);

  // --- полупрозрачные кривые «в моменте» (как линия OI) ---------------------
  check("LIQ: кривая накопленного перевеса поверх столбиков",
        rq.strokes.indexOf("rgba(255,209,102,0.85)") >= 0, JSON.stringify(rq.strokes));
  check("LIQ: заливка под кривой", rq.fills.indexOf("rgba(255,209,102,0.08)") >= 0,
        JSON.stringify(rq.fills));
  check("CVD: кривая накопления поверх столбиков",
        rc.strokes.indexOf("rgba(167,139,250,0.9)") >= 0, JSON.stringify(rc.strokes));
  check("CVD: заливка под кривой", rc.fills.indexOf("rgba(167,139,250,0.10)") >= 0,
        JSON.stringify(rc.fills));
  check("CVD: заливка кривой отрисована", rc.fills.length > 0);

  // --- OI: линия -----------------------------------------------------------
  api.drawPaneOi();
  const ro = canvases["ind-canvas-oi"]._rec;
  check("OI: линия золотом", ro.strokes.indexOf("#ffd54f") >= 0);
  check("OI: точки линии по свечам", ro.lines.filter((l) => l[0] === "L").length > 10);
  check("OI: точка последнего значения", ro.arcs.length === 1);
  const oiVal = $("ind-oi-val").textContent;
  check("OI: дельта видимого диапазона в шапке", /^[+−]\$/.test(oiVal), oiVal);

  // --- сетка и цифры --------------------------------------------------------
  api.drawPaneCvd();
  const rc2 = canvases["ind-canvas-cvd"]._rec;
  check("сетка: цифры шкалы у горизонталей", rc2.texts.length >= 3);

  // --- пустые данные --------------------------------------------------------
  sandbox.state.candles = candles.map((c) => ({ time: c.time, close: c.close }));
  canvases["ind-canvas-oi"].width = 0;
  api.drawPaneOi();
  const roEmpty = canvases["ind-canvas-oi"]._rec;
  check("OI без данных: подпись «нет данных»",
        roEmpty.texts.some((t) => /нет данных/.test(t.t)));
  check("OI без данных: значение в шапке — прочерк", $("ind-oi-val").textContent === "—");

  // --- видимость ------------------------------------------------------------
  sandbox.state.paneCvd = false;
  api.syncPaneVisibility("cvd");
  check("выключенное окно получает hidden", $("ind-pane-cvd").classList.contains("hidden"));
  sandbox.state.paneCvd = true;
  api.syncPaneVisibility("cvd");
  check("включённое окно без hidden", !$("ind-pane-cvd").classList.contains("hidden"));
}

/* ================= часть 1: jsdom-структура и переключатели ================= */

async function part1() {
  console.log("\nчасть 1: страница терминала в jsdom");
  let JSDOM, VirtualConsole;
  try {
    ({ JSDOM, VirtualConsole } = require("jsdom"));
  } catch (e) {
    console.log("  --   jsdom не установлен (npm install --no-save jsdom ws) — пропуск");
    return;
  }
  const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));

  const noop = () => {};
  function stubCanvas(win) {
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
  const mkBeforeParse = (devBypass) => (win) => {
    stubCanvas(win);
    win.matchMedia = () => ({ matches: false, media: "", onchange: null,
      addListener() {}, removeListener() {},
      addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
    if (!win.ResizeObserver) {
      win.ResizeObserver = function () {
        return { observe() {}, unobserve() {}, disconnect() {} };
      };
    }
    win.WebSocket = require("ws");
    // в jsdom нет fetch — пустая заглушка, чтобы скрипты не падали
    win.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
    // dev-обход шлюза авторизации: в jsdom нет сессии пользователя,
    // а тест проверяет сами окна/переключатели
    if (devBypass) {
      try {
        win.localStorage.setItem("liqscope.devLayers", "1");
        // по умолчанию окна выключены — восстанавливаем «прошлые» выборы
        ["liqscope.paneLiq", "liqscope.paneCvd", "liqscope.paneOi"].forEach((k) =>
          win.localStorage.setItem(k, "1"));
      } catch (e) { /* ignore */ }
    }
  };
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse: mkBeforeParse(true),
  });
  const win = dom.window;
  const doc = win.document;

  await new Promise((r) => setTimeout(r, 4000));

  const $$ = (s) => doc.querySelector(s);
  check("контейнер окон есть", !!$$("#indicator-panes"));
  const panes = ["ind-pane-liq", "ind-pane-cvd", "ind-pane-oi"];
  panes.forEach((id) => {
    const el = $$("#" + id);
    check("окно " + id + " на месте и включено", !!el && !el.classList.contains("hidden"));
  });
  ["pane-liq-toggle", "pane-cvd-toggle", "pane-oi-toggle"].forEach((id) => {
    check("кнопка «Слои» " + id, !!$$("#" + id));
  });
  check("крестики закрытия", !!$$("#ind-close-liq") && !!$$("#ind-close-cvd") && !!$$("#ind-close-oi"));
  // Раскладка — в столбик (как в мобильной версии): в ряд окна ужимались
  // и обрезали цифры шкалы.
  const cssTxt = await new Promise((res, rej) => {
    require("http").get(URL_BASE + "/static/style.css", (r) => {
      let b = ""; r.on("data", (c) => (b += c)); r.on("end", () => res(b));
    }).on("error", rej);
  });
  const cssRule = (cssTxt.match(/\.indicator-panes\s*\{[^}]*\}/) || [""])[0];
  check("CSS: окна индикаторов в одну колонку", /grid-template-columns:\s*1fr/.test(cssRule),
        cssRule.replace(/\s+/g, " ").slice(0, 120));
  check("CSS: окна не растягиваются в ряд", !/repeat\(auto-fit/.test(cssRule),
        cssRule.replace(/\s+/g, " ").slice(0, 120));
  check("заголовки локализованы (LIQ)", ($$("#ind-pane-liq .ind-title") || {}).textContent === "💥 LIQ");

  // клик по кнопке слоя прячет окно и гасит кнопку
  const tog = $$("#pane-cvd-toggle");
  check("кнопка окна активна по умолчанию", tog.classList.contains("active"));
  tog.click();
  await new Promise((r) => setTimeout(r, 60));
  check("клик по кнопке скрыл окно", $$("#ind-pane-cvd").classList.contains("hidden"));
  check("клик по кнопке погасил её", !tog.classList.contains("active"));
  check("контейнер жив, пока есть другие окна", !$$("#indicator-panes").classList.contains("all-hidden"));

  // крестик на окне = та же кнопка слоя
  $$("#ind-close-liq").click();
  await new Promise((r) => setTimeout(r, 60));
  check("крестик скрыл окно LIQ", $$("#ind-pane-liq").classList.contains("hidden"));
  check("крестик погасил кнопку слоя", !$$("#pane-liq-toggle").classList.contains("active"));

  // выключаем последнее — контейнер целиком скрыт
  $$("#pane-oi-toggle").click();
  await new Promise((r) => setTimeout(r, 60));
  check("все окна выключены — контейнер скрыт", $$("#indicator-panes").classList.contains("all-hidden"));

  // состояние сохранено
  check("состояние окон в localStorage",
    win.localStorage.getItem("liqscope.paneCvd") === "0" &&
    win.localStorage.getItem("liqscope.paneLiq") === "0" &&
    win.localStorage.getItem("liqscope.paneOi") === "0");

  // включаем обратно — окно возвращается
  tog.click();
  await new Promise((r) => setTimeout(r, 60));
  check("повторный клик вернул окно", !$$("#ind-pane-cvd").classList.contains("hidden"));

  check("ошибок страницы нет", errors.length === 0, errors.join(" | "));
  // первое окно не закрываем раньше времени: его WS продолжит сыпать
  // сообщения в мёртвый document и уронит процесс

  // --- шлюз: анониму слои недоступны вообще ---
  const vcAnon = new VirtualConsole();
  const domAnon = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vcAnon,
    beforeParse: mkBeforeParse(false),
  });
  await new Promise((r) => setTimeout(r, 4000));
  const docAnon = domAnon.window.document;
  const callAnon = docAnon.getElementById("layer-call");
  check("шлюз: «Слои» скрыты у анонима", !!callAnon && callAnon.classList.contains("hidden"));
  const panesAnon = docAnon.getElementById("indicator-panes");
  check("шлюз: индикаторных окон у анонима нет",
    !!panesAnon && panesAnon.classList.contains("all-hidden"));
  domAnon.window.close();
}

(async () => {
  try { part2(); } catch (e) { fail++; console.log("  FAIL часть 2: " + e.message); }
  try { await part1(); } catch (e) { fail++; console.log("  FAIL часть 1: " + e.message); }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
