require("./_dom_env");
/** Рефреш графика (↻): свечи после обновления не должны сплющиваться.
 *
 *  Что было. Кнопка ↻ убирала ряды (``setData([])``) и заново просила свечи.
 *  В слежении шкала цены наша — ``autoScale`` выключен, окно двигаем мы
 *  (tests/chart_follow_lib.js). После ``setData([])`` библиотека окно НЕ
 *  сбрасывает: оно остаётся от старых свечей. Новые данные приходят с другими
 *  ценами — попадают за границы окна, и график выглядит сплющенной линией,
 *  а метки разлетаются (они привязаны к тем же координатам).
 *
 *  Что проверяем:
 *    1. связку в static/app.js: refreshChart чистит ряды и метки до запроса,
 *       показывает спиннер, ждёт новые данные, а setCandles по флагу
 *       refreshPending сбрасывает масштаб (refitChartScale);
 *    2. сам баг на настоящей библиотеке: замороженное окно + новый уровень
 *       цены → свечи занимают больше высоты шкалы, чем есть (визуально линия);
 *    3. что фикс лечит: после refitChartScale виден весь набор свечей, окно
 *       цены накрывает новые данные, а при включённом слежении график
 *       возвращается к последней свече;
 *    4. что при выключенном слежении библиотека подгоняет шкалу сама, а
 *       сброс времени всё равно происходит.
 *
 *  Запуск:
 *      npm install --no-save jsdom
 *      NODE_PATH=/home/user/Licvid/node_modules node tests/chart_refresh_lib.js
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

/** Тело функции из исходника: от сигнатуры до закрывающей скобки. */
function grab(src, signature) {
  const i = src.indexOf(signature);
  if (i < 0) throw new Error("не нашёл " + signature);
  let j = i, depth = 0, started = false;
  for (; j < src.length; j++) {
    if (src[j] === "{") { depth++; started = true; }
    else if (src[j] === "}") { depth--; if (started && depth === 0) { j++; break; } }
  }
  return src.slice(i, j);
}

function num(src, name) {
  const m = src.match(new RegExp("const " + name + "\\s*=\\s*([0-9.]+)"));
  if (!m) throw new Error("нет константы " + name);
  return Number(m[1]);
}

/* ================= канва-заглушка (jsdom не рисует) ================= */
function noopCtx() {
  const noop = () => {};
  const target = {
    canvas: { width: 900, height: 520 },
    measureText: (t) => ({ width: String(t || "").length * 6 }),
    getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    createLinearGradient: () => ({ addColorStop: noop }),
    createRadialGradient: () => ({ addColorStop: noop }),
    createPattern: () => null,
    isPointInPath: () => false,
    isPointInStroke: () => false,
    getLineDash: () => [],
    getTransform: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }),
  };
  return new Proxy(target, {
    get(t, k) { if (k in t) return t[k]; return typeof k === "string" && /^[a-z]/.test(k) ? noop : undefined; },
    set(t, k, v) { t[k] = v; return true; },
  });
}

async function main() {
  const src = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
  const html = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");
  const css = fs.readFileSync(path.join(ROOT, "static", "style.css"), "utf8");

  console.log("— связка в static/app.js —");
  const refreshBody = grab(src, "async function refreshChart");
  check("рефреш чистит свечи и объём до запроса",
    refreshBody.includes("candleSeries.setData([])") &&
    refreshBody.includes("volumeSeries.setData([])"));
  check("маркеры снимаются до запроса", refreshBody.includes("applyMarkers([])"));
  check("показан спиннер и он же убирается",
    refreshBody.includes("showChartLoading(true)") &&
    refreshBody.includes("showChartLoading(false)"));
  check("рефреш ждёт новые свечи", refreshBody.includes("await loadCandles()"));
  check("флаг рефреша поднят до загрузки и снят после",
    refreshBody.includes("state.refreshPending = true") &&
    refreshBody.includes("state.refreshPending = false"));
  const setBody = grab(src, "function setCandles");
  check("масштаб сбрасывается ровно на данных после рефреша",
    setBody.includes("state.refreshPending") && setBody.includes("refitChartScale()"));
  const liveBody = grab(src, "function updateCandle");
  check("тик во время рефреша не рисуется поверх пустого графика",
    /if \(state\.refreshPending\) return;/.test(liveBody));
  const refitBody = grab(src, "function refitChartScale");
  check("refitChartScale ставит диапазон времени по длине ряда",
    refitBody.includes("setVisibleLogicalRange({ from: -0.5, to: bars - 0.5 })"));
  check("если ряд пуст, остаётся подстраховка fitContent",
    refitBody.includes("fitContent()"));
  check("слежение: окно цены считается по показываемым свечам",
    refitBody.includes("priceBandBetween(0, bars - 1)") &&
    refitBody.includes("scale.setVisibleRange(next)"));
  check("без слежения: цена остаётся на попечении библиотеки",
    refitBody.includes("followPriceOptions(false)"));
  check("диапазон цен — общая чистая функция",
    src.includes("function priceBandBetween(from, to)") &&
    grab(src, "function visiblePriceBand").includes("priceBandBetween(from, to)"));
  check("в разметке есть плашка со спиннером",
    html.includes('id="chart-loading"') && html.includes("chart-spinner"));
  check("в CSS есть спиннер и анимация", css.includes(".chart-spinner") &&
    css.includes("@keyframes chart-spin"));

  /* ================= настоящая библиотека ================= */
  const { JSDOM, VirtualConsole } = require("jsdom");
  const vc = new VirtualConsole();
  const dom = new JSDOM('<!doctype html><html><body><div id="c"></div></body></html>',
    { runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
  const win = dom.window;
  win.HTMLCanvasElement.prototype.getContext = function () { return noopCtx(); };
  win.matchMedia = () => ({ matches: false, media: "", onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {},
    removeEventListener() {}, dispatchEvent() { return false; } });
  win.eval(fs.readFileSync(path.join(ROOT, "static", "lightweight-charts.js"), "utf8"));
  const LC = win.LightweightCharts;
  const el = win.document.getElementById("c");
  Object.defineProperty(el, "clientWidth", { get: () => 900 });
  Object.defineProperty(el, "clientHeight", { get: () => 520 });
  const frame = () => new Promise((r) => setTimeout(r, 40));


  const now = Math.floor(Date.now() / 1000), T = 300, N = 300;
  const mk = (base0, step) => {
    const out = [];
    for (let i = 0; i < N; i++) {
      const b = base0 + i * step;
      out.push({ time: now - (N - 1 - i) * T, open: b, high: b + 30, low: b - 30, close: b });
    }
    return out;
  };
  const oldCandles = mk(50000, 4);
  const newCandles = mk(51500, 4);          // цена ушла на другой уровень
  const newLow = newCandles[0].low, newHigh = newCandles[N - 1].high;

  const chart = LC.createChart(el, { width: 900, height: 520, autoSize: false });
  const series = chart.addSeries(LC.CandlestickSeries, {});
  const ts = chart.timeScale(), ps = series.priceScale();

  /** refitChartScale из app.js: те же зависимости, что в замыкании страницы. */
  function makeRefit(hooks) {
    return new Function("chart", "state", "followPriceNow", "anchorToLast",
      grab(src, "function refitChartScale") + "\nreturn refitChartScale;")(
        chart, hooks.state, hooks.followPriceNow, hooks.anchorToLast);
  }
  const priceWindow = () => { const r = ps.getVisibleRange(); return { from: Number(r.from), to: Number(r.to) }; };
  const logical = () => ts.getVisibleLogicalRange();
  const candlesShare = () => {
    const w = priceWindow();
    return (newHigh - newLow) / (w.to - w.from);
  };

  console.log("— баг на настоящей библиотеке v" + (LC.version ? LC.version() : "?") + " —");
  series.setData(oldCandles);
  await frame();
  // как на странице со слежением: autoScale выключен, окно цены заморожено
  ps.applyOptions({ autoScale: false });
  ps.setVisibleRange({ from: 50000, to: 51000 });
  ts.setVisibleLogicalRange({ from: 240, to: 310 });
  await frame();

  // рефреш: очистка → новые данные (масштаб НЕ сбрасываем — так было)
  series.setData([]);
  await frame();
  series.setData(newCandles);
  await frame();
  const buggyShare = candlesShare();
  const buggyWindow = priceWindow();
  check("баг воспроизведён: новые свечи не влезают в старое окно цены",
    buggyShare > 1.05 && buggyWindow.from === 50000,
    "доля " + buggyShare.toFixed(2) + ", окно " + buggyWindow.from + "…" + buggyWindow.to);

  console.log("— фикс: refitChartScale по новым данным —");
  const priceBandBetween = new Function("state", "isFinite",
    grab(src, "function priceBandBetween") + "\nreturn priceBandBetween;")(
      { candles: newCandles }, isFinite);
  const followPriceFit = new Function("isFinite",
    grab(src, "function followPriceFit") + "\nreturn followPriceFit;")(isFinite);
  const lastClose = newCandles[N - 1].close;
  const state = { chartFollow: true, candles: newCandles };
  const refit = new Function("chart", "state", "rightPriceScale", "followPriceOptions",
    "priceBandBetween", "lastChartPrice", "followPriceFit", "FOLLOW_MARGIN",
    grab(src, "function refitChartScale") + "\nreturn refitChartScale;")(
      chart, state, () => ps, (on) => (on
        ? { autoScale: false, scaleMargins: { top: 0.15, bottom: 0.15 } }
        : { autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } }),
      priceBandBetween, () => lastClose, followPriceFit, 0.15);
  check("refitChartScale отработал", refit() === true);
  const fixedWindow = priceWindow(), fixedLr0 = logical();
  check("шкала слежения не переключена обратно на авто",
    ps.options().autoScale === false);
  check("окно цены накрыло новые свечи сразу, без ожидания кадров",
    fixedWindow.from <= newLow && fixedWindow.to >= newHigh,
    JSON.stringify(fixedWindow));
  check("свечи занимают разумную долю шкалы (не линия)",
    candlesShare() > 0.3 && candlesShare() < 1,
    "доля " + candlesShare().toFixed(2));
  await frame();     // кадр библиотеки: применяет окно времени и не трогает нашу цену
  const fixedLr = logical(), afterFrame = priceWindow();
  check("окно времени показывает весь ряд", fixedLr &&
    Math.abs(fixedLr.from - (-0.5)) < 0.01 &&
    Math.abs(fixedLr.to - (N - 0.5)) < 0.01, JSON.stringify(fixedLr));
  check("кадр библиотеки окно цены не сбивает",
    Math.abs(afterFrame.from - fixedWindow.from) < 0.01 &&
    Math.abs(afterFrame.to - fixedWindow.to) < 0.01, JSON.stringify(afterFrame));
  check("ошибки округления не портят диапазон", fixedLr0 !== null);

  console.log("— слежение выключено: шкалу ведёт библиотека —");
  series.setData([]);
  await frame();
  series.setData(oldCandles);                 // рефреш принёс другой набор свечей
  await frame();
  ps.applyOptions({ autoScale: false });      // окно заморожено от прежних данных
  ps.setVisibleRange({ from: 50000, to: 51000 });
  ts.setVisibleLogicalRange({ from: 240, to: 310 });
  await frame();
  const stateOff = { chartFollow: false, candles: oldCandles };
  const refitOff = new Function("chart", "state", "rightPriceScale", "followPriceOptions",
    "priceBandBetween", "lastChartPrice", "followPriceFit", "FOLLOW_MARGIN",
    grab(src, "function refitChartScale") + "\nreturn refitChartScale;")(
      chart, stateOff, () => ps, (on) => (on
        ? { autoScale: false, scaleMargins: { top: 0.15, bottom: 0.15 } }
        : { autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } }),
      priceBandBetween, () => oldCandles[N - 1].close, followPriceFit, 0.15);
  check("refitChartScale работает и без слежения", refitOff() === true);
  check("библиотеке возвращено её обычное поведение шкалы",
    ps.options().autoScale === true);
  await frame();     // библиотека применяет окно времени и автошкалу
  const offLr = logical(), offWindow = priceWindow();
  check("время сброшено на весь ряд",
    offLr && Math.abs(offLr.from - (-0.5)) < 0.01 && Math.abs(offLr.to - (N - 0.5)) < 0.01,
    JSON.stringify(offLr));
  check("библиотека сама подгоняет шкалу под показанные свечи",
    offWindow.from <= oldCandles[0].low && offWindow.to >= oldCandles[N - 1].high,
    JSON.stringify(offWindow));
  check("свечи и тут занимают разумную долю шкалы",
    (oldCandles[N - 1].high - oldCandles[0].low) / (offWindow.to - offWindow.from) > 0.3,
    "доля " + ((oldCandles[N - 1].high - oldCandles[0].low) / (offWindow.to - offWindow.from)).toFixed(2));

  console.log(fail ? "\n" + ok + " ok, " + fail + " FAIL" : "\nвсе " + ok + " проверок прошли");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
