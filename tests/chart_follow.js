/** Автоследование графика за ценой: кнопка «🎯 Автоследование за ценой»
 *  живёт только в плашке слоёв («☰ Слои»).
 *
 *  Что проверяем:
 *    * кнопка есть в попапе слоёв и её нет в шапке графика; состояние
 *      сохраняется между заходами (localStorage liqscope.chartFollow);
 *    * кнопка горит ⇔ слежение включено (два состояния, паузы больше нет);
 *    * график НЕ дёргается на каждом тике: пока цена внутри коридора 15%,
 *      окно цены не трогаем вовсе (свой autoScale у библиотеки выключен —
 *      именно он заставлял шкалу пересчитываться на каждой свече);
 *    * цена, дойдя до границы коридора, сдвигает окно ровно на 15% от этой
 *      границы, размах (зум) при этом сохраняется; у границы есть гистерезис;
 *    * по горизонтали свеча не подходит к правому краю ближе 0.5% ширины, а
 *      после сдвига и после ручного ухода встаёт на 3% от края;
 *    * ручной сдвиг графика (мышью) возвращается к границам: пока жест
 *      удерживается — шаги молчат, после отпускания график возвращается;
 *    * при выключении библиотеке возвращаются её настройки (autoScale,
 *      отступы 6%/24%, rightOffset 6).
 *
 *  Часть 1 — реальная страница /terminal в jsdom с записывающей заглушкой
 *  шкал времени/цены. Часть 2 — математика без браузера.
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
  const consts = ["FOLLOW_MARGIN", "FOLLOW_EDGE_PCT", "FOLLOW_DRIFT_PCT",
                  "FOLLOW_PRICE_HYST"]
    .map((n) => src.match(new RegExp("const " + n + " = [^;]+;"))[0]).join("\n");
  const margins = src.match(/const PRICE_MARGINS_DEFAULT = \{[^}]+ \};/)[0];
  const keep = grab(src, "function", "followKeepBars");
  const range = grab(src, "function", "followRange");
  const shift = grab(src, "function", "followPriceShift");
  const fit = grab(src, "function", "followPriceFit");
  const opts = grab(src, "function", "followPriceOptions");
  // eslint-disable-next-line no-new-func
  const make = (body, ret) => new Function(consts + "\n" + body + "\nreturn " + ret + ";")();
  const followKeepBars = make(keep, "followKeepBars");
  const followRange = make(keep + "\n" + range, "followRange");
  const followPriceShift = make(shift, "followPriceShift");
  const followPriceFit = make(fit, "followPriceFit");
  const followPriceOptions = new Function(
    consts + "\n" + margins + "\n" + opts + "\nreturn followPriceOptions;")();
  return { followKeepBars, followRange, followPriceShift, followPriceFit,
           followPriceOptions, src };
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

/** Общие заглушки страницы: канва, сокет, fetch и сам график.
 *  Настоящую библиотеку подменяем (иначе jsdom грузит её и печатает
 *  падение целиком), а шкалы пишут всё, что в них уходит. */
function installStubs(win, rec, opts) {
  opts = opts || {};
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
    if (opts.candles === false) return sock;
    setTimeout(() => {
      if (sock.onopen) sock.onopen();
      if (sock.onmessage) sock.onmessage({ data: JSON.stringify({
        type: "init", symbols: ["BTC_USDT"], custom_symbols: [], details: [],
        prices: { BTC_USDT: LAST_PRICE }, recent_liquidations: [],
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
    if (opts.candles !== false && u.indexOf("/api/klines") === 0) {
      body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles: CANDLES };
    } else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
    return { ok: true, status: 200, json: async () => body };
  };

  let logical = null;
  const timeScale = rec.timeScale = {
    applyOptions(o) { rec.time.push(o); if (o.rightOffset !== undefined) this._ro = o.rightOffset; },
    options: () => ({ rightOffset: timeScale._ro === undefined ? 6 : timeScale._ro }),
    getVisibleLogicalRange: () => logical,
    setVisibleLogicalRange(r) {
      logical = { from: r.from, to: r.to };
      rec.ranges.push({ ...r });
      if (this._cb) this._cb(logical);          // библиотека зовёт подписчика
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
    getVisibleRange: () => priceScale._vr || null,
    setVisibleRange(r) {
      priceScale._vr = { from: r.from, to: r.to };
      rec.priceRanges.push({ from: r.from, to: r.to });
    },
  };
  rec.priceScale = priceScale;
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
  // страница подключает свой lightweight-charts.js — он бы затёр заглушку,
  // поэтому объявляем её неперезаписываемой
  try {
    Object.defineProperty(win, "LightweightCharts", {
      configurable: true,
      get() { return fakeCharts; },
      set() { /* страница грузит свою библиотеку — заглушку не отдаём */ },
    });
  } catch (e) { win.LightweightCharts = fakeCharts; }
  win.localStorage.setItem("liqscope.devLayers", "1");
}

const CANDLES = [];
const NOW = Math.floor(Date.now() / 1000);
const LAST_PRICE = 50050;
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

  const rec = { ranges: [], price: [], priceRanges: [], time: [] };
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) { installStubs(win, rec, { candles: true }); },
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
  F.setTicker(false);         // шаги по таймеру в тесте не нужны — дёргаем сами

  const btn = doc.getElementById("follow-toggle-pop");
  check("кнопка подписана", /Авто/.test(btn.textContent), btn.textContent);
  check("по умолчанию автоследование включено", F.enabled() === true);
  check("включённая кнопка подсвечена",
    btn.classList.contains("active") && btn.getAttribute("data-state") === "on" &&
    !btn.classList.contains("paused"),
    btn.className + " / " + btn.getAttribute("data-state"));

  /* --- вертикаль: свой коридор, без autoScale --------------------------- */
  check("зазор по вертикали 15%", F.margin === 0.15);
  check("слежение выключает autoScale: библиотека не пересчитывает шкалу",
    F.priceOptions(true).autoScale === false &&
    F.priceOptions(true).scaleMargins.top === 0.15 &&
    F.priceOptions(true).scaleMargins.bottom === 0.15,
    JSON.stringify(F.priceOptions(true)));
  check("в график ушло отключение autoScale, а не его включение",
    rec.price.some((o) => o.autoScale === false), JSON.stringify(rec.price));
  check("свеча держится в 3% от правого края",
    F.edgePct === 0.03 && F.driftPct === 0.005 && F.keepBars(130) === 4,
    String(F.edgePct) + "/" + String(F.driftPct) + "/" + String(F.keepBars(130)));
  check("отступ справа от библиотеки обнулён",
    rec.time.some((o) => o.rightOffset === 0), JSON.stringify(rec.time.slice(-3)));

  // цена внутри коридора — окно цены не трогаем ни на одном шаге
  const last = CANDLES.length - 1;
  const span = F.keepBars(60);
  rec.timeScale.setVisibleLogicalRange({ from: last - 60, to: last + span });
  const win0 = F.priceVisibleRange();
  check("окно цены после включения задано", !!win0, JSON.stringify(win0));
  const calls0 = rec.priceRanges.length;
  for (let i = 0; i < 5; i++) F.step();
  check("пять тиков подряд — окно цены ни разу не дёрнулось",
    rec.priceRanges.length === calls0, rec.priceRanges.length - calls0);
  const r0 = rec.ranges[rec.ranges.length - 1];
  check("и окно времени на месте: уже стоит на 3%",
    r0 && Math.abs(r0.to - (last + F.keepBars(r0.to - r0.from))) < 1e-6,
    JSON.stringify(r0));

  // цена подошла к верхней границе коридора — окно сдвигается на 15% от верха
  const cur = F.priceVisibleRange();
  const curSpan = cur.to - cur.from;
  const before1 = rec.priceRanges.length;
  rec.priceScale._vr = { from: cur.from, to: cur.to };
  win.__forcePrice = cur.to - curSpan * 0.05;      // цена у самого верха окна
  F.step();                                         // (шаг читает цену из свечей)
  CANDLES[CANDLES.length - 1].close = cur.to - curSpan * 0.05;
  F.priceStep();
  const up1 = rec.priceRanges[rec.priceRanges.length - 1] || cur;
  const upSpan1 = up1.to - up1.from;
  check("цена у границы — окно сдвинулось, цена встала в 15% от верха",
    rec.priceRanges.length > before1 &&
    Math.abs((up1.to - upSpan1 * 0.15) - (cur.to - curSpan * 0.05)) < 1e-6 &&
    Math.abs(upSpan1 - curSpan) < 1e-6,
    JSON.stringify(up1) + " было " + JSON.stringify(cur));
  const before2 = rec.priceRanges.length;
  F.priceStep();
  F.priceStep();
  check("после сдвига окно успокоилось — цена в коридоре, шаги молчат",
    rec.priceRanges.length === before2, rec.priceRanges.length - before2);

  // гистерезис: шум в 0.5% высоты у самой границы не двигает окно
  const cur2 = F.priceVisibleRange();
  const span2 = cur2.to - cur2.from;
  CANDLES[CANDLES.length - 1].close = cur2.to - span2 * 0.15 + span2 * 0.005;
  const before3 = rec.priceRanges.length;
  F.priceStep();
  check("шум у границы (0.5%) окно не двигает — график не ёрзает",
    rec.priceRanges.length === before3, rec.priceRanges.length - before3);

  // цена ушла на другой уровень (переключили монету) — окно подгоняется заново,
  // а не растягивается с прежнего места
  const save = CANDLES.map((c) => ({ ...c }));
  CANDLES.forEach((c) => {
    c.open *= 2; c.high *= 2; c.low *= 2; c.close *= 2;
  });
  const newPrice = CANDLES[CANDLES.length - 1].close;
  F.priceStep();
  const far = rec.priceRanges[rec.priceRanges.length - 1];
  check("другая монета — окно пересобрано вокруг нового уровня, а не растянуто",
    far && far.from < newPrice && far.to > newPrice &&
    (far.to - far.from) < newPrice && far.from > cur2.to,
    JSON.stringify(far) + " цена " + newPrice);
  save.forEach((c, i) => Object.assign(CANDLES[i], c));

  /* --- горизонталь: ручной уход возвращается к 3% ------------------------ */
  const container = doc.getElementById("tv-chart-container");
  rec.timeScale.setVisibleLogicalRange({ from: last - 400, to: last - 120 });
  container.dispatchEvent(new win.Event("pointerdown", { bubbles: true }));
  await sleep(60);
  check("во время жеста шаги слежения молчат", F.hold() === true &&
    F.step() === false && F.hold() === true, String(F.hold()));
  const heldRanges = rec.ranges.length;
  win.dispatchEvent(new win.Event("pointerup"));
  check("после отпускания шаг снова работает", F.hold() === false);
  F.step();
  const back = rec.ranges[rec.ranges.length - 1];
  const backSpan = back.to - back.from;
  check("ручной сдвиг возвращается к 3% справа",
    rec.ranges.length > heldRanges && back.from > last - 400 &&
    Math.abs(back.to - (last + F.keepBars(backSpan))) < 1e-6,
    JSON.stringify(back));

  // и без ручного шага: отпустили — вернулось само (таймер возврата)
  rec.timeScale.setVisibleLogicalRange({ from: last - 500, to: last - 40 });
  container.dispatchEvent(new win.Event("pointerdown", { bubbles: true }));
  win.dispatchEvent(new win.Event("pointerup"));
  await sleep(600);
  const auto = rec.ranges[rec.ranges.length - 1];
  check("после отпускания график возвращается сам, без тика по монете",
    Math.abs(auto.to - (last + F.keepBars(auto.to - auto.from))) < 1e-6,
    JSON.stringify(auto));

  // колесо (зум) — тоже жест: пока крутят, шаги молчат, потом возврат
  F.setHold(false);
  rec.timeScale.setVisibleLogicalRange({ from: last - 500, to: last - 60 });
  const wheelEvent = win.WheelEvent
    ? new win.WheelEvent("wheel", { bubbles: true, deltaY: 120 })
    : new win.Event("wheel", { bubbles: true });
  container.dispatchEvent(wheelEvent);
  check("колесо ставит шаги на паузу, пока крутят", F.hold() === true);
  const wheelRanges = rec.ranges.length;
  F.step();
  check("пока крутят колесо — окно не дёргается",
    rec.ranges.length === wheelRanges && F.hold() === true);
  await sleep(600);
  const afterWheel = rec.ranges[rec.ranges.length - 1];
  check("после колеса слежение вернулось и окно снова на 3%",
    F.hold() === false && rec.ranges.length > wheelRanges &&
    Math.abs(afterWheel.to - (last + F.keepBars(afterWheel.to - afterWheel.from))) < 1e-6,
    JSON.stringify(afterWheel));

  // цена вплотную к правому краю — окно сдвигается, свеча не уходит за край
  const near = { from: last - 200.4, to: last + 0.2 };
  rec.timeScale.setVisibleLogicalRange(near);
  F.step();
  const shifted = rec.ranges[rec.ranges.length - 1];
  check("свеча подошла к краю — окно сдвинулось, ширина сохранилась",
    shifted && Math.abs((shifted.to - shifted.from) - 200.6) < 1e-6 &&
    shifted.to > last, JSON.stringify(shifted));

  /* --- выключение -------------------------------------------------------- */
  click(btn);
  await sleep(50);
  check("клик выключил автоследование", F.enabled() === false);
  check("кнопка погасла", !btn.classList.contains("active") &&
    btn.getAttribute("data-state") === "off",
    btn.className + " / " + btn.getAttribute("data-state"));
  check("библиотеке вернули autoScale и прежние отступы",
    rec.price.some((o) => o.autoScale === true && o.scaleMargins &&
                          o.scaleMargins.bottom === 0.24 && o.scaleMargins.top === 0.06),
    JSON.stringify(rec.price.slice(-2)));
  check("и прежний отступ у шкалы времени",
    rec.time.some((o) => o.rightOffset === 6));
  const offRanges = rec.ranges.length;
  const offPrice = rec.priceRanges.length;
  F.step();
  check("выключено — шаги больше ничего не двигают",
    rec.ranges.length === offRanges && rec.priceRanges.length === offPrice,
    rec.ranges.length - offRanges + "/" + (rec.priceRanges.length - offPrice));
  check("выключенное состояние сохранено",
    win.localStorage.getItem("liqscope.chartFollow") === "0");
  await win.close();

  // второй заход — состояние помним
  const rec2 = { ranges: [], price: [], priceRanges: [], time: [] };
  const dom2 = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win2) {
      installStubs(win2, rec2, { candles: false });
      win2.localStorage.setItem("liqscope.chartFollow", "1");
    },
  });
  await sleep(1500);
  check("сохранённое состояние уважается",
    dom2.window.LiQScopeFollow && dom2.window.LiQScopeFollow.enabled() === true);
  check("и включённое слежение тоже без паузы",
    dom2.window.LiQScopeFollow.paused === undefined);
  await dom2.window.close();
  check("ошибок страницы нет", errors.length === 0, errors.join(" | "));
}

/* ================= прогон ================= */
(async () => {
  console.log("автоследование графика: границы, возврат после ручного сдвига, без дрожания");
  const { followKeepBars, followRange, followPriceShift, followPriceFit,
          followPriceOptions } = mathPart();

  // сколько свечей в доле ширины
  check("3% от 300 свечей — 9 свечей", followKeepBars(300, 0.03) === 9,
    String(followKeepBars(300, 0.03)));
  check("минимум одна свеча", followKeepBars(10, 0.03) === 1 &&
    followKeepBars(0, 0.03) === 1, String(followKeepBars(10, 0.03)));

  // горизонталь: свеча в 3% — не трогаем
  check("свеча на 3% от края — окно не двигаем",
    followRange({ from: 0, to: 103 }, 100, 0.03, 0.005) === null);
  // свеча подошла к краю ближе 0.5% — сдвигаем на 3%, ширина та же
  const drift = followRange({ from: 0, to: 100.2 }, 100, 0.03, 0.005);
  check("свеча подошла к краю — окно сдвигается на 3%",
    drift && Math.abs(drift.to - 103) < 1e-6 && Math.abs((drift.to - drift.from) - 100.2) < 1e-6,
    JSON.stringify(drift));
  check("свеча у самой границы — тоже сдвигаем",
    (() => { const r = followRange({ from: 0, to: 100 }, 100, 0.03, 0.005);
             return r && Math.abs(r.to - 103) < 1e-6; })());
  // ручной уход в историю и прыжок вправо — возвращаемся к границам
  const farPan = followRange({ from: 0, to: 400 }, 100, 0.03, 0.005);
  const farKeep = farPan ? followKeepBars(farPan.to - farPan.from, 0.03) : 0;
  check("ручной уход в историю — окно возвращается на 3%",
    farPan && Math.abs(farPan.to - (100 + farKeep)) < 1e-6 &&
    Math.abs((farPan.to - farPan.from) - 400) < 1e-6, JSON.stringify(farPan));
  check("уход вправо за свечу — тоже возврат",
    (() => { const r = followRange({ from: 200, to: 300 }, 100, 0.03, 0.005);
             return r && Math.abs(r.to - 103) < 1e-6; })());
  check("мусор на входе не роняет",
    followRange(null, 5, 0.03, 0.005) === null &&
    followRange({ from: 5, to: 5 }, 5, 0.03, 0.005) === null &&
    followRange({ from: 0, to: 10 }, -1, 0.03, 0.005) === null);

  // вертикаль: коридор 15% с гистерезисом, размах сохраняется
  const M = 0.15, H = 0.01;
  check("цена в середине коридора — окно не трогаем",
    followPriceShift({ from: 100, to: 200 }, 150, M, H) === null);
  check("цена в 10% от верха — всё ещё не трогаем",
    followPriceShift({ from: 100, to: 200 }, 180, M, H) === null);
  check("шум у самой границы (в пределах гистерезиса) — не трогаем",
    followPriceShift({ from: 100, to: 200 }, 185.5, M, H) === null);
  const up = followPriceShift({ from: 100, to: 200 }, 190, M, H);
  check("цена вышла за 15% — окно сдвигается, цена встаёт на 15% от верха",
    up && Math.abs((up.to - (up.to - up.from) * 0.15) - 190) < 1e-9 &&
    Math.abs((up.to - up.from) - 100) < 1e-9, JSON.stringify(up));
  const down = followPriceShift({ from: 100, to: 200 }, 109, M, H);
  check("цена вышла за низ — окно сдвигается, цена на 15% от низа",
    down && Math.abs((down.from + (down.to - down.from) * 0.15) - 109) < 1e-9 &&
    Math.abs((down.to - down.from) - 100) < 1e-9, JSON.stringify(down));
  check("размах при сдвиге не меняется",
    up && Math.abs((up.to - up.from) - 100) < 1e-9 &&
    down && Math.abs((down.to - down.from) - 100) < 1e-9);
  check("мусор на входе не роняет",
    followPriceShift(null, 150, M, H) === null &&
    followPriceShift({ from: 5, to: 5 }, 5, M, H) === null &&
    followPriceShift({ from: 1, to: 2 }, NaN, M, H) === null);

  // первичная подгонка: видимые свечи и цена с зазорами
  const fit = followPriceFit({ low: 100, high: 200 }, 150, M);
  const fitSpan = fit.to - fit.from;
  check("подгонка: свечи в кадре с зазорами 15% с обеих сторон",
    Math.abs((fit.from + fitSpan * M) - 100) < 1e-9 &&
    Math.abs((fit.to - fitSpan * M) - 200) < 1e-9, JSON.stringify(fit));
  const fitPrice = followPriceFit({ low: 100, high: 200 }, 260, M);
  check("подгонка: вышедшая цена тоже в кадре",
    fitPrice && fitPrice.from < 260 && fitPrice.to > 260, JSON.stringify(fitPrice));
  check("подгонка: без свечей считаем по цене",
    !!followPriceFit(null, 100, M));
  check("подгонка: без данных — ничего",
    followPriceFit(null, null, M) === null);

  // настройки шкалы цены
  const on = followPriceOptions(true), off = followPriceOptions(false);
  check("включено: autoScale выключен, зазор 15% (следим сами)",
    on.autoScale === false && on.scaleMargins.top === 0.15 && on.scaleMargins.bottom === 0.15,
    JSON.stringify(on));
  check("выключено: библиотеке возвращается автошкала с 6%/24%",
    off.autoScale === true && off.scaleMargins.top === 0.06 && off.scaleMargins.bottom === 0.24,
    JSON.stringify(off.scaleMargins));

  try { await part1(); } catch (e) { fail++; console.log("  FAIL часть 1: " + e.message); }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
