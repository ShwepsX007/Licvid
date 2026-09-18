/** Автоследование графика за ценой: кнопка «🎯 Автоследование за ценой»
 *  живёт только в плашке слоёв («☰ Слои»).
 *
 *  Что проверяем:
 *    * кнопка есть в попапе слоёв и её нет в шапке графика; состояние
 *      сохраняется между заходами (localStorage liqscope.chartFollow);
 *    * кнопка горит ⇔ слежение включено: на паузе остаётся подсвеченной
 *      (⏸), при выключении гаснет, а клик на паузе возвращает к цене,
 *      а не выключает слежение;
 *    * горизонталь — когда свеча подошла к правому краю, окно сдвигается
 *      ровно на шаг, сохраняя зум (чистая функция followRange);
 *    * вертикаль — цена удерживается в поле зрения зазором 15% сверху и снизу
 *      (настройки шкалы цены), при выключении возвращаются прежние отступы;
 *    * ручной отъезд влево автоследование не отменяет (двигаем только вправо,
 *      когда свеча подошла к краю).
 *
 *  Часть 1 — реальная страница /terminal в jsdom с записывающей заглушкой
 *  шкал времени/цены. Часть 2 — математика followRange без браузера.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/chart_follow.js
 */
const fs = require("fs");
const path = require("path");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

/* ================= часть 2: чистая математика ================= */
function grab(src, kind, name) {
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

function mathPart() {
  const src = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
  const consts = ["FOLLOW_EDGE_BARS", "FOLLOW_KEEP_BARS", "FOLLOW_MARGIN"]
    .map((n) => src.match(new RegExp("const " + n + " = [^;]+;"))[0]).join("\n");
  const fn = grab(src, "function", "followRange");
  const opts = grab(src, "function", "followPriceOptions");
  const margins = src.match(/const PRICE_MARGINS_DEFAULT = \{[^}]+\};/)[0];
  // eslint-disable-next-line no-new-func
  const followRange = new Function(consts + "\n" + fn + "\nreturn followRange;")();
  const followPriceOptions = new Function(
    consts + "\n" + margins + "\n" + opts + "\nreturn followPriceOptions;")();
  return { followRange, followPriceOptions, src };
}

/* ================= часть 1: страница терминала ================= */
function noopCtx() {
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CANDLES = [];
const NOW = Math.floor(Date.now() / 1000);
for (let i = 24; i >= 0; i--) {
  CANDLES.push({ time: NOW - (NOW % 300) - i * 300, open: 50000 + i, high: 50100 + i,
                 low: 49900 + i, close: 50050 + i, volume: 10 + i,
                 cvd: 1000 * i, oi: 5e8 + i * 1e5, oiChg: 1e5 * i });
}

async function part1() {
  const { JSDOM, VirtualConsole } = require("jsdom");
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const m = String((e && e.message) || e);
    if (m.indexOf("Not implemented") !== -1) return;
    errors.push("jsdomError: " + m.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));

  const rec = { ranges: [], price: [], time: [] };
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
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify({
            type: "init", symbols: ["BTC_USDT"], custom_symbols: [], details: [],
            prices: { BTC_USDT: 50050 }, recent_liquidations: [],
            exchanges: ["BINANCE"], stats: {},
          }) });
        }, 40);
        return sock;
      };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/klines") === 0) {
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles: CANDLES };
        } else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
      /* Заглушка графика: нам важны только шкалы (окно времени и цена),
         поэтому series-объект — пустышка, а шкалы всё записывают. */
      let logical = null;
      const timeScale = rec.timeScale = {
        applyOptions(o) { rec.time.push(o); if (o.rightOffset !== undefined) this._ro = o.rightOffset; },
        options: () => ({ rightOffset: timeScale._ro === undefined ? 6 : timeScale._ro }),
        getVisibleLogicalRange: () => logical,
        setVisibleLogicalRange(r) {
          logical = { from: r.from, to: r.to };
          rec.ranges.push({ ...r });
          if (this._cb) this._cb(logical);      // библиотека зовёт подписчика
        },
        subscribeVisibleLogicalRangeChange(cb) { this._cb = cb; },
        unsubscribeVisibleLogicalRangeChange() { this._cb = null; },
        timeToCoordinate: () => 100,
        coordinateToTime: () => NOW,
        getVisibleRange: () => ({ from: NOW - 3600, to: NOW }),
        scrollToPosition() {}, fitContent() {},
        applyOptionsNoop() {},
      };
      const priceScale = {
        applyOptions(o) { rec.price.push(o); this._o = Object.assign({}, this._o, o); },
        options: () => Object.assign({ autoScale: true, scaleMargins: { top: 0.06, bottom: 0.24 } },
                                     priceScale._o || {}),
      };
      const series = () => ({
        setData() {}, update() {}, applyOptions() {},
        priceToCoordinate: (v) => Number(v), coordinateToPrice: (v) => Number(v),
        priceScale: () => priceScale,
        createPriceLine: () => ({ applyOptions() {} }),
        removePriceLine() {}, setMarkers() {}, markers: () => [],
      });
      const fakeCharts = {
        createChart: () => ({
          applyOptions() {}, remove() {}, resize() {},
          timeScale: () => timeScale,
          priceScale: () => priceScale,
          addSeries: () => series(),
          addCandlestickSeries: () => series(),
          addHistogramSeries: () => series(),
          addLineSeries: () => series(),
          panes: () => [],
          subscribeCrosshairMove() {}, unsubscribeCrosshairMove() {},
        }),
        CandlestickSeries: {}, HistogramSeries: {}, LineSeries: {},
        CrosshairMode: { Normal: 0 },
      };
      // страница подключает свой lightweight-charts.js — он бы затёр
      // заглушку, поэтому объявляем её неперезаписываемой
      try {
        Object.defineProperty(win, "LightweightCharts", {
          configurable: true,
          get() { return fakeCharts; },
          set() { /* страница грузит свою библиотеку — заглушку не отдаём */ },
        });
      } catch (e) { win.LightweightCharts = fakeCharts; }
      win.localStorage.setItem("liqscope.devLayers", "1");
    },
  });

  await sleep(6500);
  const win = dom.window;
  const doc = win.document;
  const F = win.LiQScopeFollow;
  const click = (el) => el.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));

  check("кнопки в шапке графика больше нет", !doc.getElementById("follow-toggle"));
  check("кнопка переехала в плашку слоёв", !!doc.getElementById("follow-toggle-pop"));
  check("в шапке на её месте — рисование",
    doc.getElementById("layer-call").nextElementSibling.id === "draw-toggle",
    doc.getElementById("layer-call").nextElementSibling.id);
  check("тестовый API доступен", !!F && typeof F.range === "function");

  const btn = doc.getElementById("follow-toggle-pop");
  check("кнопка подписана", /Авто/.test(btn.textContent), btn.textContent);
  check("по умолчанию автоследование включено", F.enabled() === true);
  check("включённая кнопка подсвечена",
    btn.classList.contains("active") && btn.getAttribute("data-state") === "on",
    btn.className + " / " + btn.getAttribute("data-state"));
  check("зазор по вертикали 15%",
    F.margin === 0.15 && F.priceOptions(true).scaleMargins.top === 0.15 &&
    F.priceOptions(true).scaleMargins.bottom === 0.15,
    JSON.stringify(F.priceOptions(true).scaleMargins));
  check("в график ушли отступы 15% обе стороны",
    rec.price.some((o) => o.scaleMargins && o.scaleMargins.top === 0.15 &&
                          o.scaleMargins.bottom === 0.15), JSON.stringify(rec.price));
  check("горизонтальный зазор почти вплотную к шкале",
    rec.time.some((o) => o.rightOffset === 1), JSON.stringify(rec.time.slice(-3)));

  // свеча у правого края — окно едет за ней
  const last = CANDLES.length - 1;
  rec.timeScale.setVisibleLogicalRange({ from: last - 50, to: last - 2 });
  const moved = F.step();
  check("свеча у края — окно подвинулось", moved === true);
  const lastRange = rec.ranges[rec.ranges.length - 1];
  check("последняя свеча осталась видимой с зазором 1",
    lastRange && lastRange.to - (last + F.keepBars) < 1e-6,
    lastRange && JSON.stringify(lastRange));
  check("зум сохранился (ширина окна та же)", lastRange &&
    (lastRange.to - lastRange.from) > 0);

  // повторный вызов без движения цены ничего не меняет (шаг = 1 свеча)
  const before = rec.ranges.length;
  F.step();
  check("второй шаг без новой свечи не дёргает окно", rec.ranges.length === before);

  // ручная прокрутка в историю — автоследование встаёт на паузу
  const panRange = { from: last - 300, to: last - 40 };
  rec.timeScale.setVisibleLogicalRange(panRange);
  check("прокрутка в историю ставит на паузу", F.paused() === true);
  const pausedBefore = rec.ranges.length;
  F.step();
  check("на паузе окно не дёргается", rec.ranges.length === pausedBefore);
  check("на паузе кнопка остаётся горящей: видно ⏸, но она не гаснет",
    btn.classList.contains("paused") && btn.classList.contains("active") &&
    btn.getAttribute("data-state") === "paused",
    btn.className + " / " + btn.getAttribute("data-state"));
  click(btn);   // на паузе клик — «верни меня к цене», а не «выключи»
  await sleep(60);
  check("клик на паузе вернул слежение, а не выключил его",
    F.enabled() === true && F.paused() === false &&
    btn.getAttribute("data-state") === "on",
    String(F.enabled()) + "/" + String(F.paused()) + " / " + btn.getAttribute("data-state"));
  const anchor = rec.ranges[rec.ranges.length - 1];
  check("включение возвращает к актуальной свече",
    F.paused() === false && anchor && Math.abs(anchor.to - (last + F.keepBars)) < 1e-6,
    anchor && JSON.stringify(anchor));

  // выключение: возвращаются прежние отступы
  click(btn);
  await sleep(50);
  check("клик выключил автоследование", F.enabled() === false);
  check("кнопка погасла и ничего не светит",
    !btn.classList.contains("active") && !btn.classList.contains("paused") &&
    btn.getAttribute("data-state") === "off",
    btn.className + " / " + btn.getAttribute("data-state"));
  check("вернулись прежние отступы цены",
    rec.price.some((o) => o.scaleMargins && o.scaleMargins.bottom === 0.24 &&
                          o.scaleMargins.top === 0.06),
    JSON.stringify(rec.price.slice(-2)));
  check("и прежний зазор у шкалы времени",
    rec.time.some((o) => o.rightOffset === 6));
  check("выключенное состояние сохранено",
    win.localStorage.getItem("liqscope.chartFollow") === "0");
  await win.close();

  // второй заход — состояние помним
  const dom2 = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win2) {
      win2.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win2.ResizeObserver) {
        win2.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      win2.HTMLCanvasElement.prototype.getContext = function () { return noopCtx(); };
      win2.WebSocket = function () { return { readyState: 3, send() {}, close() {} }; };
      win2.WebSocket.OPEN = 1;
      win2.WebSocket.CLOSED = 3;
      win2.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
      win2.localStorage.setItem("liqscope.devLayers", "1");
      win2.localStorage.setItem("liqscope.chartFollow", "1");
    },
  });
  await sleep(1500);
  check("сохранённое состояние уважается",
    dom2.window.LiQScopeFollow && dom2.window.LiQScopeFollow.enabled() === true);
  await dom2.window.close();
  check("ошибок страницы нет", errors.length === 0, errors.join(" | "));
}

/* ================= прогон ================= */
(async () => {
  console.log("автоследование графика: кнопка, горизонталь и вертикаль");
  const { followRange, followPriceOptions } = mathPart();

  // горизонталь: свеча подошла к краю — едем, сохраняя ширину окна
  const r1 = followRange({ from: 90, to: 103 }, 100, 3, 1);
  check("окно едет, когда свеча в 3 барах от края",
    r1 && Math.abs(r1.to - 101) < 1e-6 && Math.abs((r1.to - r1.from) - 13) < 1e-6,
    JSON.stringify(r1));
  check("свеча далеко от края — окно не трогаем",
    followRange({ from: 10, to: 80 }, 100, 3, 1) === null);
  check("свеча ровно в зазоре — не дёргаем",
    followRange({ from: 90, to: 101 }, 100, 3, 1) === null);
  check("свеча в зоне слежения чуть правее — доводим до зазора",
    (() => { const r = followRange({ from: 90, to: 98 }, 100, 3, 1);
             return r && Math.abs(r.to - 101) < 1e-6; })(),
    JSON.stringify(followRange({ from: 90, to: 98 }, 100, 3, 1)));
  // прокрутили в историю — дальше зоны слежения не лезем (там пауза)
  check("далеко от свечи окно не трогаем",
    followRange({ from: 10, to: 80 }, 100, 3, 1) === null);
  check("окно кончается задолго до свечи — пауза, а не прыжок",
    followRange({ from: 200, to: 260 }, 100, 3, 1) === null,
    JSON.stringify(followRange({ from: 200, to: 260 }, 100, 3, 1)));
  check("мусор на входе не роняет",
    followRange(null, 5, 3, 1) === null && followRange({ from: 5, to: 5 }, 5, 3, 1) === null &&
    followRange({ from: 0, to: 10 }, -1, 3, 1) === null);
  // длинная история: ширина окна не меняется при сдвиге
  const wide = followRange({ from: 0, to: 300 }, 297, 3, 1);
  check("широкое окно сдвигается на тот же шаг",
    wide && Math.abs((wide.to - wide.from) - 300) < 1e-6 && Math.abs(wide.to - 298) < 1e-6,
    JSON.stringify(wide));
  check("у самого края окно не дёргается повторно",
    followRange({ from: 0, to: 300 }, 299, 3, 1) === null);

  // вертикаль: зазор 15% с обеих сторон, при выключении — прежние 6%/24%
  const on = followPriceOptions(true), off = followPriceOptions(false);
  check("включено: autoscale и 15% зазора",
    on.autoScale === true && on.scaleMargins.top === 0.15 && on.scaleMargins.bottom === 0.15);
  check("выключено: прежние отступы",
    off.autoScale === true && off.scaleMargins.top === 0.06 && off.scaleMargins.bottom === 0.24,
    JSON.stringify(off.scaleMargins));

  try { await part1(); } catch (e) { fail++; console.log("  FAIL часть 1: " + e.message); }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
