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

function grabLine(src, marker) {
  const i = src.indexOf(marker);
  if (i < 0) throw new Error("не нашёл " + marker);
  return src.slice(i, src.indexOf("\n", i));
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
    setLineDash() {},
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

  // Высоты блоков проверяем на «стеке» известной высоты: в jsdom раскладки
  // нет, поэтому clientHeight задаём вручную.
  const STACK_H = 700;
  const stackEl = { clientHeight: STACK_H, classList: { toggle() {}, contains() { return false; } } };

  const canvases = {
    "ind-canvas-liq": fakeCanvas(360, 64),
    "ind-canvas-cvd": fakeCanvas(360, 64),
    "ind-canvas-oi": fakeCanvas(360, 64),
    // слои фигур теханализа поверх окон
    "ind-draw-liq": fakeCanvas(360, 64),
    "ind-draw-cvd": fakeCanvas(360, 64),
    "ind-draw-oi": fakeCanvas(360, 64),
  };
  const $stub = (id) => (canvases[id] ? canvases[id] : $(id));
  const $stack = (id) => (id === "chart-stack" ? stackEl : $stub(id));

  // главный график «живёт» с min-height 220px, пока пользователь не тянул
  // разделители (.chart-stack.sized)
  const wrapEl = { _mh: "220px" };
  const fakeDoc = {
    querySelector: (sel) => (sel.indexOf(".chart-wrapper") >= 0 ? wrapEl : null),
    querySelectorAll: () => ({ length: 3 }),   // три разделителя блоков видны
  };

  // Суммы по свечам для окна ликвидаций: в терминале их собирает
  // liqCandleSums (сохранённая история + живой хвост), здесь истории нет —
  // считаем по тем же событиям, что отдаёт visibleLiquidations.
  const histSums = new Map();          // добавка «сохранённой истории» для теста
  const liqCandleSums = () => {
    const out = new Map();
    histSums.forEach((v, k) => out.set(k, { long: v.long, short: v.short, n: v.n }));
    (liqs || []).forEach((x) => {
      const t = Math.floor(x.timestamp / tfSec) * tfSec;
      const b = out.get(t) || { long: 0, short: 0, n: 0 };
      if (x.side === "SELL") b.long += x.usd; else b.short += x.usd;
      b.n += 1;
      out.set(t, b);
    });
    return out;
  };

  const sandbox = {
    $: $stack,
    // главный график: цена → пиксели, а сам канвас нужен для проекции
    candleSeries: { priceToCoordinate: (p) => 200 - Number(p) / 100 },
    drawCanvas: { width: 360, height: 200, clientWidth: 360, clientHeight: 200 },
    document: fakeDoc,
    window: { devicePixelRatio: 1, getComputedStyle: (el) => ({ minHeight: (el && el._mh) || "" }) },
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
  const constNames = ["IND_PANES", "LAYOUT"];
  const names = ["indMoney", "indBarSpacing", "indGrid", "indVerticals",
    "indPrepareCanvas", "indCurve", "indNoData", "indSetVal", "drawPaneLiq",
    "drawPaneCvd", "drawPaneOi", "drawIndicatorPanes", "syncPaneVisibility",
    // фигуры теханализа в окнах: шкала окна, проекция с главного графика
    "paneHeight", "paneYOf", "paneValueAt", "paneOf", "paneOverlayCtx",
    "paneToXY", "projectToXY", "drawToXY", "drawLineSeg", "drawFigureShape",
    "drawPreview", "panePixelToTP", "paneTimeAt", "drawPaneFigures",
    "drawPanePreview", "drawPaneDrawings", "drawPixelToTP", "drawTestState",
    // регулировка высот блоков графика
    "indKey", "mobileLayout", "paneVisible", "visiblePaneKinds", "visibleSplitCount", "stackHeight",
    "chartMinHeight", "panesBudget", "maxCanvasFor", "normalizeHeights"];
  const scalars = ["IND_KINDS", "IND_DEFAULT_CANVAS_H", "IND_MIN_CANVAS_H",
    "IND_HEAD_H", "SPLIT_H", "STACK_SLACK_H"].map((n) =>
    grabLine(src, "const " + n + " = ")).join("\n") +
    "\n" + grabLine(src, "const paneScales =") +
    "\n" + grabLine(src, "const PANE_PAD =") +
    "\n" + grabLine(src, "const DRAW_PANES =") +
    ["let drawFiguresList =", "let drawDraftPane =", "let drawHoverPane =",
     "let drawTool =", "let drawColor =", "let drawDraft =", "let drawHover ="]
      .map((m) => grabLine(src, m)).join("\n");
  const code = scalars + "\n" + grabLine(src, "let layoutReady =") +
    "\nlayoutReady = true;\n" +
    constNames.map((n) => grab(src, "const", n)).join("\n") + "\n" +
    names.map((n) => grab(src, "function", n)).join("\n") + "\n" +
    "\nreturn { IND_PANES, LAYOUT, IND_KINDS, indMoney, drawPaneLiq, drawPaneCvd," +
    " drawPaneOi, drawIndicatorPanes, syncPaneVisibility, indKey, visiblePaneKinds," +
    " stackHeight, panesBudget, maxCanvasFor, normalizeHeights," +
    " paneYOf, paneValueAt, paneToXY, projectToXY, drawPaneFigures, drawPaneDrawings," +
    " drawFiguresList, paneScales, drawPanePreview, drawTestState };";
  const api = new Function("$", "window", "document", "localStorage", "I18n", "state",
    "chart", "visibleLiquidations", "fmtUsdShort", "candleSeries", "drawCanvas",
    "liqCandleSums", code)(
    sandbox.$, sandbox.window, sandbox.document, sandbox.localStorage, sandbox.I18n,
    sandbox.state, sandbox.chart, sandbox.visibleLiquidations, sandbox.fmtUsdShort,
    sandbox.candleSeries, sandbox.drawCanvas, liqCandleSums);

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

  // Терминал был закрыт: в памяти событий нет, но сохранённая история (её
  // отдаёт liqCandleSums) рисует столбик — как CVD и OI из свечей.
  const sumOf = (txt) => {                    // «Σ $1.08M» → 1080000
    const m = /\$([\d.]+)\s*([KMB])?/.exec(String(txt || ""));
    if (!m) return NaN;
    return parseFloat(m[1]) * ({ K: 1e3, M: 1e6, B: 1e9 }[m[2] || ""] || 1);
  };
  const histCandle = candles[39].time;
  const cyanBefore = rq.rects.filter((r) => r.fill.indexOf("0,214,255") >= 0).length;
  const sumBefore = sumOf($("ind-liq-val").textContent);
  histSums.set(histCandle, { long: 0, short: 777000, n: 3 });
  canvases["ind-canvas-liq"]._rec.rects.length = 0;
  api.drawPaneLiq();
  const rq2 = canvases["ind-canvas-liq"]._rec;
  const cyanAfter = rq2.rects.filter((r) => r.fill.indexOf("0,214,255") >= 0).length;
  check("LIQ: сохранённая история рисуется окном без событий в памяти",
        cyanAfter > cyanBefore, cyanBefore + " → " + cyanAfter);
  check("LIQ: история попала в цифру окна",
        isFinite(sumBefore) && sumOf($("ind-liq-val").textContent) > sumBefore + 700000,
        sumBefore + " → " + $("ind-liq-val").textContent);
  histSums.clear();

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

  // --- фигуры теханализа в окнах -------------------------------------------
  console.log("\nчасть 2в: фигуры теханализа в нижних окнах");
  const hPane = 64, pad = 6;
  api.paneScales.liq = { lo: -1000, hi: 1000 };      // шкала как её ставит окно
  check("шкала окна: центр диапазона — середина высоты",
        Math.abs(api.paneYOf("liq", 0, hPane) - (hPane / 2)) < 0.01,
        api.paneYOf("liq", 0, hPane));
  check("шкала окна: значение читается обратно",
        Math.abs(api.paneValueAt("liq", api.paneYOf("liq", 640, hPane), hPane) - 640) < 1,
        api.paneValueAt("liq", api.paneYOf("liq", 640, hPane), hPane));
  check("шкала окна: край диапазона — с отступом",
        Math.abs(api.paneYOf("liq", 1000, hPane) - (hPane - pad - (hPane - 2 * pad))) < 0.01,
        api.paneYOf("liq", 1000, hPane));

  // своя фигура окна рисуется в слое окна, а не в основном графике
  api.drawFiguresList.length = 0;
  api.drawFiguresList.push({ t: "line", c: "#22d3ee", pane: "cvd",
    p1: { time: candles[0].time, price: -20000 },
    p2: { time: candles[10].time, price: 30000 } });
  Object.keys(canvases).forEach((k) => { canvases[k]._rec.lines.length = 0; });
  api.drawPaneFigures("cvd");
  const rd = canvases["ind-draw-cvd"]._rec;
  check("фигура окна рисуется в слое этого окна", rd.lines.length >= 2, rd.lines.length);
  check("фигура окна не попадает в слой ликвидаций",
        canvases["ind-draw-liq"]._rec.lines.length === 0);

  // проекция фигуры главного графика: пунктир в окне
  api.drawFiguresList.length = 0;
  api.drawFiguresList.push({ t: "line", c: "#ffd166",
    p1: { time: candles[0].time, price: 10000 },
    p2: { time: candles[20].time, price: 30000 } });
  const rx = api.paneToXY("liq", { pane: "main" });
  const proj = rx({ time: candles[0].time, price: 10000 });
  check("проекция с графика даёт координаты в окне",
        proj && isFinite(proj.y) && proj.y > 0 && proj.y < hPane, proj ? proj.y : null);
  Object.keys(canvases).forEach((k) => { canvases[k]._rec.lines.length = 0; });
  api.drawPaneFigures("liq");
  check("фигура главного графика видна в окне проекцией",
        canvases["ind-draw-liq"]._rec.lines.length >= 2,
        canvases["ind-draw-liq"]._rec.lines.length);
  api.drawFiguresList.length = 0;

  // --- предпросмотр фигуры, начатой В ЭТОМ окне ----------------------------
  // Раньше «резинка» шла через проекцию главного графика: значение окна
  // трактовалось как цена, и линия при перетаскивании прилипала к верхней
  // кромке окна. Теперь драфт переводится по шкале самого окна.
  console.log("\nчасть 2г: перетаскивание фигуры внутри нижнего окна");
  const ctxLiq = canvases["ind-draw-liq"].getContext();
  api.drawTestState({ tool: "line", draftPane: "liq",
    draft: { time: candles[0].time, price: 0 },      // центр шкалы окна
    hover: { x: 120, y: 8 }, hoverPane: "liq" });
  const clearPanes = () => Object.keys(canvases).forEach(
    (k) => { canvases[k]._rec.lines.length = 0; canvases[k]._rec.arcs.length = 0; });
  clearPanes();
  api.drawPanePreview("liq", ctxLiq, 360, hPane);
  const rl = canvases["ind-draw-liq"]._rec;
  check("предпросмотр в окне нарисован", rl.lines.length >= 2, rl.lines.length);
  const ys = rl.lines.filter((l) => l[0] === "M" || l[0] === "L").map((l) => l[2]);
  const yDraft = api.paneYOf("liq", 0, hPane);
  check("линия идёт от точки драфта по шкале окна",
        ys.some((y) => Math.abs(y - yDraft) < 0.01), ys.join(",") + " vs " + yDraft);
  check("линия идёт за курсором, а не по верхней кромке",
        ys.some((y) => Math.abs(y - 8) < 0.01) &&
        ys.some((y) => Math.abs(y - yDraft) < 0.01) && Math.abs(yDraft - 8) > 5,
        ys.join(","));
  check("предпросмотр внутри окна (не вылезает за края)",
        ys.every((y) => y >= 0 && y <= hPane), ys.join(","));
  check("драфт своего окна не проецируется в другие окна",
        canvases["ind-draw-cvd"]._rec.lines.length === 0 &&
        canvases["ind-draw-oi"]._rec.lines.length === 0);

  // фигура начата в ДРУГОМ окне — здесь только точка, «резинки» нет
  api.drawTestState({ draftPane: "cvd", draft: { time: candles[2].time, price: -500 },
                      hover: { x: 120, y: 18 }, hoverPane: "cvd" });
  clearPanes();
  api.drawPanePreview("liq", ctxLiq, 360, hPane);
  check("чужой драфт из другого окна — только точка проекции",
        rl.lines.length === 0 && rl.arcs.length === 1, rl.arcs.length);
  check("точка проекции внутри окна",
        rl.arcs[0] && rl.arcs[0].y > 0 && rl.arcs[0].y < hPane, JSON.stringify(rl.arcs[0]));

  // фигура начата на главном графике — окно её не дублирует (рисует график)
  api.drawTestState({ draftPane: "main", draft: { time: candles[2].time, price: 51000 },
                      hover: { x: 120, y: 18 }, hoverPane: "main" });
  clearPanes();
  api.drawPanePreview("liq", ctxLiq, 360, hPane);
  check("драфт главного графика в окне не дублируется",
        rl.lines.length === 0 && rl.arcs.length === 0);
  api.drawTestState({ tool: null, draft: null, draftPane: null, hover: null,
                      hoverPane: null });

  // --- пустые данные --------------------------------------------------------
  sandbox.state.candles = candles.map((c) => ({ time: c.time, close: c.close }));
  canvases["ind-canvas-oi"].width = 0;
  api.drawPaneOi();
  // Подпись «нет данных» в окне OI берётся из I18n.t("ind.oi_nodata"): стенд
  // подменяет перевод возвратом ключа, поэтому здесь проверяется только
  // прочерк в шапке окна (сам текст подписи живёт в static/i18n.js).
  check("OI без данных: значение в шапке — прочерк", $("ind-oi-val").textContent === "—");

  // --- видимость ------------------------------------------------------------
  sandbox.state.paneCvd = false;
  api.syncPaneVisibility("cvd");
  check("выключенное окно получает hidden", $("ind-pane-cvd").classList.contains("hidden"));
  sandbox.state.paneCvd = true;
  api.syncPaneVisibility("cvd");
  check("включённое окно без hidden", !$("ind-pane-cvd").classList.contains("hidden"));

  // --- высоты блоков: бюджет стека и клампы (раскладка 700px) ---------------
  console.log("\nчасть 2b: бюджет высот блоков графика");
  const L = api.LAYOUT;
  const stackH = api.stackHeight();
  check("высота стека прочитана", stackH === STACK_H, stackH);
  // пока пользователь не тянул разделители, главному графику оставлен его
  // min-height 220px: 700 - 220 - 3 шапки(26) - 3 разделителя(8) - 12 = 366
  const budgetDefault = api.panesBudget();
  check("бюджет окон с запасом под график", budgetDefault === 366, budgetDefault);
  api.normalizeHeights();
  check("окна по 64px не ужимаются зря", L.indLiq === 64 && L.indCvd === 64 && L.indOi === 64,
        JSON.stringify([L.indLiq, L.indCvd, L.indOi]));
  // окно не может занять больше, чем осталось от соседей: 366 - 128 = 238
  check("потолок роста окна LIQ", api.maxCanvasFor("liq") === 238, api.maxCanvasFor("liq"));
  // потянули — главный график становится сжимаемым в ноль: 700 - 78 - 24 - 4 = 594
  L.sized = true;
  check("после растяжки график отдаёт свою высоту", api.panesBudget() === 586, api.panesBudget());
  check("потолок роста окна без запаса под график", api.maxCanvasFor("liq") === 458,
        api.maxCanvasFor("liq"));
  // окна в сумме больше стека — ужимаются пропорционально, соотношение цело
  L.indLiq = 500; L.indCvd = 500; L.indOi = 500;
  api.normalizeHeights();
  check("переполнение стека ужимает окна",
        Math.abs(L.indLiq + L.indCvd + L.indOi - 586) <= 2,
        L.indLiq + L.indCvd + L.indOi);
  check("соотношение высот сохранилось", L.indLiq === L.indCvd && L.indCvd === L.indOi &&
        L.indLiq >= 194 && L.indLiq <= 196, JSON.stringify([L.indLiq, L.indCvd, L.indOi]));
  // выключенное окно выпадает из бюджета — соседям больше места
  sandbox.state.paneOi = false;
  L.indLiq = 64; L.indCvd = 64;
  // окно выключено — его шапка (26px) больше не занимает стек
  check("выключенное окно не занимает бюджет", api.panesBudget() === 612,
        api.panesBudget());
  sandbox.state.paneOi = true;
  // окно можно сузить в ноль — это разрешённая высота, а не ошибка
  L.indLiq = 0; L.indCvd = 0; L.indOi = 0;
  api.normalizeHeights();
  check("нулевые высоты сохраняются (полное сужение разрешено)",
        L.indLiq === 0 && L.indCvd === 0 && L.indOi === 0);
  check("потолок при нулевых окнах — весь бюджет", api.maxCanvasFor("oi") === 586,
        api.maxCanvasFor("oi"));
  // на низком экране окна ужимаются, а не вылезают под график
  L.indLiq = 600; L.indCvd = 600; L.indOi = 600;
  api.normalizeHeights();
  check("переполнение на любом экране гасится до бюджета",
        Math.abs(L.indLiq + L.indCvd + L.indOi - 586) <= 2,
        L.indLiq + L.indCvd + L.indOi);
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
  check("CSS: окна индикаторов в одну колонку",
        /display:\s*flex/.test(cssRule) && /flex-direction:\s*column/.test(cssRule),
        cssRule.replace(/\s+/g, " ").slice(0, 120));
  check("CSS: окна не растягиваются в ряд", !/repeat\(auto-fit/.test(cssRule),
        cssRule.replace(/\s+/g, " ").slice(0, 120));

  // --- Блоки графика регулируются по высоте, как лента с «Лидерами» --------
  const cssVar = (n) => win.document.documentElement.style.getPropertyValue(n).trim();
  const savedLayout = () => {
    try { return JSON.parse(win.localStorage.getItem("liqscope.layout")) || {}; }
    catch (e) { return {}; }
  };
  const dragSplit = (id, fromY, toY) => {
    const el = $$("#" + id);
    el.dispatchEvent(new win.MouseEvent("pointerdown", { bubbles: true, clientY: fromY }));
    win.dispatchEvent(new win.MouseEvent("pointermove", { bubbles: true, clientY: toY }));
    win.dispatchEvent(new win.MouseEvent("pointerup", { bubbles: true, clientY: toY }));
  };
  const stack = $$("#chart-stack");
  check("главный график и окна — в одном стеке",
        !!stack && !!stack.querySelector("#chart-wrapper") && !!stack.querySelector("#indicator-panes"));
  ["liq", "cvd", "oi"].forEach((k) => {
    const sp = $$("#split-" + k + "-y");
    check("разделитель split-" + k + "-y есть и виден", !!sp && !sp.classList.contains("hidden"));
  });
  check("высота окна LIQ по умолчанию", cssVar("--ind-h-liq") === "64px", cssVar("--ind-h-liq"));
  check("высота окна CVD по умолчанию", cssVar("--ind-h-cvd") === "64px", cssVar("--ind-h-cvd"));
  // тянем разделитель над окном LIQ вверх на 40px — окно растёт, график отдаёт
  dragSplit("split-liq-y", 300, 260);
  check("перетаскивание вверх растянуло окно LIQ", cssVar("--ind-h-liq") === "104px",
        cssVar("--ind-h-liq"));
  check("растяжка включила режим «график можно сузить в ноль»",
        !!stack && stack.classList.contains("sized"));
  check("новая высота сохранена", savedLayout().indLiq === 104, JSON.stringify(savedLayout().indLiq));
  check("ширина ленты не поехала от вертикального драга", savedLayout().feedW === undefined ||
        savedLayout().feedW === 430, savedLayout().feedW);
  // разделитель над CVD забирает высоту у соседа сверху (LIQ), а не у графика
  dragSplit("split-cvd-y", 400, 340);
  check("окно CVD выросло", cssVar("--ind-h-cvd") === "124px", cssVar("--ind-h-cvd"));
  check("высоту отдало соседнее окно LIQ", cssVar("--ind-h-liq") === "44px", cssVar("--ind-h-liq"));
  // рывок вверх «до упора»: сосед сужается в ноль, окно забирает всё
  dragSplit("split-cvd-y", 400, -5000);
  check("соседнее окно сузилось до нуля", cssVar("--ind-h-liq") === "0px", cssVar("--ind-h-liq"));
  check("окно CVD забрало освободившуюся высоту", cssVar("--ind-h-cvd") === "168px",
        cssVar("--ind-h-cvd"));
  // и наоборот: рывок вниз сужает само окно до нуля (полное сужение разрешено)
  dragSplit("split-oi-y", 400, 5000);
  check("окно можно сузить полностью", cssVar("--ind-h-oi") === "0px", cssVar("--ind-h-oi"));
  check("нулевая высота сохранена", savedLayout().indOi === 0, JSON.stringify(savedLayout().indOi));
  // выключенное окно: разделитель прячется и ничего не двигает
  $$("#pane-liq-toggle").click();
  check("выключенное окно прячет свой разделитель",
        $$("#split-liq-y").classList.contains("hidden"));
  const beforeDisabled = ["liq", "cvd", "oi"].map((k) => cssVar("--ind-h-" + k)).join(" / ");
  dragSplit("split-liq-y", 200, 120);
  check("по выключенному окну разделитель не тянется",
        ["liq", "cvd", "oi"].map((k) => cssVar("--ind-h-" + k)).join(" / ") === beforeDisabled,
        ["liq", "cvd", "oi"].map((k) => cssVar("--ind-h-" + k)).join(" / "));
  $$("#pane-liq-toggle").click();
  check("включённое окно возвращает разделитель",
        !$$("#split-liq-y").classList.contains("hidden"));
  // верхний разделитель тянет высоту у главного графика, а не у соседних окон:
  // рывок вверх растит LIQ «до потолка стека», CVD и OI не меняются
  const cvdBeforeChartDrag = cssVar("--ind-h-cvd");
  const oiBeforeChartDrag = cssVar("--ind-h-oi");
  dragSplit("split-liq-y", 300, -5000);
  // в jsdom раскладки нет, поэтому потолок = 900px; в браузере его считает
  // maxCanvasFor по реальной высоте стека (там график сужается в ноль)
  check("окно LIQ растянулось за счёт главного графика", cssVar("--ind-h-liq") === "900px",
        cssVar("--ind-h-liq"));
  check("соседние окна при этом не тронуты",
        cssVar("--ind-h-cvd") === cvdBeforeChartDrag && cssVar("--ind-h-oi") === oiBeforeChartDrag,
        cssVar("--ind-h-cvd") + " / " + cssVar("--ind-h-oi"));
  // двойной клик по разделителю — исходные высоты
  $$("#split-cvd-y").dispatchEvent(new win.MouseEvent("dblclick", { bubbles: true }));
  const allDefault = ["liq", "cvd", "oi"].every((k) => cssVar("--ind-h-" + k) === "64px");
  check("двойной клик вернул исходные высоты", allDefault,
        ["liq", "cvd", "oi"].map((k) => cssVar("--ind-h-" + k)).join(" / "));
  check("сброс снял режим растяжки", !stack.classList.contains("sized"));
  check("сброс сохранён", savedLayout().indCvd === 64 && !savedLayout().sized,
        JSON.stringify(savedLayout()));
  // CSS: главный график — «остаток» стека и сжимается в ноль после растяжки
  const stackRule = (cssTxt.match(/\.chart-stack\s*\{[^}]*\}/) || [""])[0];
  check("CSS: стек графика — колонка",
        /display:\s*flex/.test(stackRule) && /flex-direction:\s*column/.test(stackRule),
        stackRule.replace(/\s+/g, " ").slice(0, 140));
  check("CSS: главный график занимает остаток", /flex:\s*1/.test(stackRule),
        stackRule.replace(/\s+/g, " ").slice(0, 140));
  const sizedRule = (cssTxt.match(/\.chart-stack\.sized\s*>\s*\.chart-wrapper\s*\{[^}]*\}/) || [""])[0];
  check("CSS: после растяжки график сужается в ноль", /min-height:\s*0/.test(sizedRule),
        sizedRule);
  const canvasWrapRule = (cssTxt.match(/\.ind-canvas-wrap\s*\{[^}]*\}/) || [""])[0];
  check("CSS: полотно окна тянется по переменной", /height:\s*var\(--ind-h/.test(canvasWrapRule),
        canvasWrapRule);
  // телефон: разделителей нет, окна вернулись к фиксированной высоте со скроллом
  const mobileCss = cssTxt.slice(cssTxt.indexOf("@media (max-width: 900px)"));
  check("CSS: на телефоне окна фиксированные и скроллятся",
        /#ind-pane-liq,\s*#ind-pane-cvd,\s*#ind-pane-oi\s*\{\s*--ind-h:\s*54px/.test(mobileCss) &&
        /\.indicator-panes\s*\{[^}]*overflow-y:\s*auto/.test(mobileCss));
  check("CSS: на телефоне главный график держит свои 240px",
        /\.chart-stack\.sized\s*>\s*\.chart-wrapper\s*\{\s*min-height:\s*240px/.test(mobileCss));
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

  // --- шлюз: у гостя без регистрации — пробные 30 минут ---------------
  // Само окно «зарегистрируйтесь бесплатно» и закрытие кнопки после
  // пробника проверяет tests/layers_gate.js; здесь важно, что во время
  // пробника кнопка и окна работают как у обычного пользователя.
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
  check("шлюз: гостю кнопка слоёв видна (пробные минуты)",
    !!callAnon && !callAnon.classList.contains("hidden"));
  const panesAnon = docAnon.getElementById("indicator-panes");
  check("шлюз: индикаторных окон у анонима нет",
    !!panesAnon && panesAnon.classList.contains("all-hidden"));
  const splitsAnon = docAnon.querySelectorAll("#chart-stack .stack-splitter");
  const splitsHidden = Array.prototype.every.call(splitsAnon,
    (el) => el.classList.contains("hidden"));
  check("шлюз: разделители блоков у анонима спрятаны",
    splitsAnon.length === 3 && splitsHidden, splitsAnon.length + " / " + splitsHidden);
  domAnon.window.close();
}

(async () => {
  try { part2(); } catch (e) { fail++; console.log("  FAIL часть 2: " + e.message); }
  try { await part1(); } catch (e) { fail++; console.log("  FAIL часть 1: " + e.message); }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
