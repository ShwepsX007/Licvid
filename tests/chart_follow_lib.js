require("./_dom_env");
/** Автоследование на НАСТОЯЩЕЙ библиотеке графика (static/lightweight-charts.js).
 *
 *  tests/chart_follow.js гоняет страницу с записывающей заглушкой шкал: там
 *  библиотека отвечает ровно то, что её просят. Но именно библиотека решает
 *  то, из-за чего график мог дёргаться, поэтому здесь она настоящая (v5.2.1
 *  из static/) и проверяется контракт с ней:
 *    * принимает ли она окно с отступом справа (3%) или подрезает его по
 *      последней свече;
 *    * держит ли замороженный диапазон цены (autoScale:false) при новых
 *      свечах и тиках — или сама пересчитывает шкалу на每一次 обновлении;
 *    * уезжает ли окно само, когда приходит новая свеча;
 *    * возвращается ли автошкала, когда слежение выключают.
 *
 *  Рисуем в заглушку канвы (jsdom не умеет canvas), но шкалы от рисования не
 *  зависят. Важно: библиотека применяет новое окно времени не сразу, а на
 *  следующем кадре, — поэтому после записи окна тест ждёт кадр, а записи
 *  шкалы цены видны сразу.
 *
 *  Запуск:
 *      npm install --no-save jsdom
 *      NODE_PATH=/home/user/Licvid/node_modules node tests/chart_follow_lib.js
 */
const fs = require("fs");
const path = require("path");

const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

/* ================= функции слежения из static/app.js ================= */
function grab(src, name) {
  const marker = "function " + name;
  const i = src.indexOf(marker);
  if (i < 0) throw new Error("не нашёл " + marker);
  let j = i, depth = 0, started = false;
  for (; j < src.length; j++) {
    if (src[j] === "{") { depth++; started = true; }
    else if (src[j] === "}") { depth--; if (started && depth === 0) { j++; break; } }
  }
  return src.slice(i, j);
}

function loadFollowCode() {
  const src = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
  const consts = ["FOLLOW_MARGIN", "FOLLOW_EDGE_PCT", "FOLLOW_DRIFT_PCT", "FOLLOW_PRICE_HYST"]
    .map((n) => {
      const m = src.match(new RegExp("const " + n + " = [^;]+;"));
      if (!m) throw new Error("в static/app.js пропала константа " + n);
      return m[0];
    }).join("\n");
  const margins = src.match(/const PRICE_MARGINS_DEFAULT = \{[^}]+\};/);
  if (!margins) throw new Error("в static/app.js пропали PRICE_MARGINS_DEFAULT");
  const num = (n) => Number(consts.match(new RegExp(n + " = ([0-9.]+)"))[1]);
  return {
    FOLLOW_MARGIN: num("FOLLOW_MARGIN"),
    FOLLOW_EDGE_PCT: num("FOLLOW_EDGE_PCT"),
    FOLLOW_DRIFT_PCT: num("FOLLOW_DRIFT_PCT"),
    FOLLOW_PRICE_HYST: num("FOLLOW_PRICE_HYST"),
    followKeepBars: new Function(consts + "\n" + grab(src, "followKeepBars") +
                                 "\nreturn followKeepBars;")(),
    followRange: new Function(consts + "\n" + grab(src, "followKeepBars") + "\n" +
                              grab(src, "followRange") + "\nreturn followRange;")(),
    followPriceShift: new Function(consts + "\n" + grab(src, "followPriceShift") +
                                   "\nreturn followPriceShift;")(),
    followPriceFit: new Function(consts + "\n" + grab(src, "followPriceFit") +
                                 "\nreturn followPriceFit;")(),
    followPriceOptions: new Function(consts + "\n" + margins[0] + "\n" +
                                     grab(src, "followPriceOptions") +
                                     "\nreturn followPriceOptions;")(),
  };
}

/* ================= окно jsdom с настоящей библиотекой ================= */
/** Канва-заглушка: jsdom не рисует, библиотеке нужны методы и measureText. */
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
    get(t, k) {
      if (k in t) return t[k];
      return typeof k === "string" && /^[a-z]/.test(k) ? noop : undefined;
    },
    set(t, k, v) { t[k] = v; return true; },
  });
}

async function main() {
  const F = loadFollowCode();
  const M = F.FOLLOW_MARGIN, KEEP = F.FOLLOW_EDGE_PCT, DRIFT = F.FOLLOW_DRIFT_PCT,
        HYST = F.FOLLOW_PRICE_HYST;

  const { JSDOM, VirtualConsole } = require("jsdom");
  const crashes = [];
  process.on("uncaughtException", (e) => {
    if (crashes.length < 3) crashes.push(String((e && e.message) || e).slice(0, 160));
  });
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { if (crashes.length < 3) crashes.push(String(e.message).slice(0, 160)); });

  const dom = new JSDOM(`<!doctype html><html><body><div id="c"></div></body></html>`,
    { runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
  const win = dom.window;
  win.HTMLCanvasElement.prototype.getContext = function () { return noopCtx(); };
  // библиотека слушает смену масштаба экрана — в jsdom такого API нет
  win.matchMedia = () => ({ matches: false, media: "", onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {},
    removeEventListener() {}, dispatchEvent() { return false; } });
  win.eval(fs.readFileSync(path.join(__dirname, "..", "static", "lightweight-charts.js"), "utf8"));
  const LC = win.LightweightCharts;
  if (!LC || !LC.createChart) throw new Error("не загрузилась lightweight-charts");

  const el = win.document.getElementById("c");
  Object.defineProperty(el, "clientWidth", { get: () => 900 });
  Object.defineProperty(el, "clientHeight", { get: () => 520 });
  const frame = () => new Promise((r) => setTimeout(r, 30));

  // ---- график как на странице: 300 свечей по 5 минут, цена ~50000
  const now = Math.floor(Date.now() / 1000), T = 300, N = 300;
  const candles = [];
  for (let i = 0; i < N; i++) {
    const base = 50000 + i * 4;
    candles.push({ time: now - (N - 1 - i) * T, open: base, high: base + 30,
                   low: base - 30, close: base });
  }
  const chart = LC.createChart(el, { width: 900, height: 520, autoSize: false,
                                     layout: { background: { color: "#000000" } } });
  const series = chart.addSeries(LC.CandlestickSeries, {});
  series.setData(candles);
  const ts = chart.timeScale(), ps = series.priceScale();
  await frame();

  console.log("— контракт библиотеки v" + (LC.version ? LC.version() : "?") + " —");
  check("график создался, данные приняты", !!ts.getVisibleLogicalRange(),
    JSON.stringify(ts.getVisibleLogicalRange()));

  // ---- что пишем и что читаем (считаем записи, как их видит страница)
  let timeWrites = 0, priceWrites = 0, escaped = null;
  const lastIdx = () => candles.length - 1;
  const lastPrice = () => candles[candles.length - 1].close;
  const lr = () => ts.getVisibleLogicalRange();
  const pr = () => ps.getVisibleRange();
  const edge = () => { const r = lr(); return r ? r.to - lastIdx() : NaN; };
  const spanOf = () => { const r = lr(); return r ? r.to - r.from : NaN; };
  const keepBars = () => F.followKeepBars(spanOf(), KEEP);

  /** Границы цены по видимым свечам — копия visiblePriceBand() из app.js. */
  function band() {
    const r = lr();
    let from = 0, to = candles.length - 1;
    if (r && isFinite(r.from) && isFinite(r.to)) {
      from = Math.max(0, Math.floor(r.from));
      to = Math.min(candles.length - 1, Math.ceil(r.to));
    }
    let low = Infinity, high = -Infinity;
    for (let i = from; i <= to; i++) {
      const c = candles[i];
      if (!c) continue;
      if (c.low < low) low = c.low;
      if (c.high > high) high = c.high;
    }
    return isFinite(low) ? { low: low, high: high } : null;
  }

  /** Шаг цены — копия followPriceNow() из app.js. */
  function priceStep() {
    const price = lastPrice();
    let cur = null;
    try { cur = pr(); } catch (e) { cur = null; }
    const okCur = !!cur && isFinite(cur.from) && isFinite(cur.to) &&
                  Number(cur.to) > Number(cur.from);
    let next = null;
    if (okCur) {
      const from = Number(cur.from), to = Number(cur.to), span = to - from;
      const far = price < from - span || price > to + span;
      next = far ? F.followPriceFit(band(), price, M)
                 : F.followPriceShift(cur, price, M, HYST);
    } else {
      next = F.followPriceFit(band(), price, M);
    }
    if (!next) return false;
    ps.setVisibleRange(next);
    priceWrites++;
    return true;
  }

  /** Возврат после жеста — копия followPriceSnap() из app.js:
   *  цена вне коридора встаёт РОВНО на 15% от той границы, за которую ушла
   *  (гистерезис выключен), внутри коридора не двигаем. */
  function priceSnap() {
    const price = lastPrice();
    let cur = null;
    try { cur = pr(); } catch (e) { cur = null; }
    const next = (cur && isFinite(cur.from) && isFinite(cur.to) && Number(cur.to) > Number(cur.from))
      ? F.followPriceShift(cur, price, M, 0)
      : F.followPriceFit(band(), price, M);
    if (!next) return false;
    ps.setVisibleRange(next);
    priceWrites++;
    return true;
  }

  /** Один шаг слежения — копия followChartNow() из app.js. */
  function step() {
    const r = lr();
    const next = F.followRange(r, lastIdx(), KEEP, DRIFT);
    let moved = false;
    if (next) { ts.setVisibleLogicalRange(next); timeWrites++; moved = true; }
    const movedPrice = priceStep();
    // цена не должна оказаться за видимыми границами (в том числе на каждом тике)
    const range = pr(), price = lastPrice();
    if (range && price !== undefined &&
        (price > range.to + 1e-9 || price < range.from - 1e-9) && !escaped) {
      escaped = { price: price, range: range };
    }
    return moved || movedPrice;
  }

  const corridor = () => {
    const r = pr();
    if (!r) return null;
    const span = r.to - r.from;
    return { from: r.from + span * M, to: r.to - span * M, span: span };
  };
  const mid = () => { const r = pr(); return (r.from + r.to) / 2; };

  // ================= включили слежение (applyFollowMode в app.js) =================
  ts.applyOptions({ rightOffset: 0 });
  ps.applyOptions(F.followPriceOptions(true));
  await frame();
  const spanFirst = spanOf();
  step();
  await frame();          // библиотека применяет окно на следующем кадре
  check("окно приняло отступ справа 3% (библиотека его не подрезает)",
    Math.abs(edge() - keepBars()) < 0.05,
    "edge=" + edge().toFixed(3) + " keep=" + keepBars() + " span=" + spanOf().toFixed(2));
  check("шкала цены заморожена (autoScale:false), диапазон задан",
    ps.options().autoScale === false && !!pr(),
    "autoScale=" + ps.options().autoScale + " range=" + JSON.stringify(pr()));
  const c1 = corridor(), p1 = lastPrice();
  check("цена держится в коридоре 15% (сверху не ближе 15%)",
    c1 && p1 <= c1.to + 1e-6 && p1 >= c1.from - 1e-6,
    "цена " + p1 + " коридор " + (c1 ? JSON.stringify(c1) : "нет"));

  // ================= библиотека держит диапазон при новых свечах =================
  const frozen = JSON.stringify(pr());
  const writesAtStart = { t: timeWrites, p: priceWrites };
  const flat = lastPrice();                 // новые свечи — вокруг текущей цены
  for (let i = 1; i <= 30; i++) {
    const c = { time: now + i * T, open: flat, high: flat + 30, low: flat - 30, close: flat };
    candles.push(c);
    series.update(c);
    await frame();
  }
  check("новые свечи не заставляют библиотеку пересчитывать шкалу цены",
    JSON.stringify(pr()) === frozen, JSON.stringify(pr()) + " было " + frozen);
  check("новые свечи не вызывают наших записей (окно внутри коридора молчит)",
    timeWrites === writesAtStart.t && priceWrites === writesAtStart.p,
    "время " + (timeWrites - writesAtStart.t) + ", цена " + (priceWrites - writesAtStart.p));

  // ================= 60 тиков внутри коридора: ни одной записи =================
  const quiet = { t: timeWrites, p: priceWrites, r: JSON.stringify(pr()),
                  l: JSON.stringify(lr()) };
  const base = mid();                    // качаем цену вокруг середины окна
  for (let i = 0; i < 60; i++) {
    const c = candles[candles.length - 1];
    c.close = base + Math.sin(i / 3) * 40;         // ±0.08% — далеко до границы
    c.high = Math.max(c.high, c.close);
    c.low = Math.min(c.low, c.close);
    series.update(c);
    step();
  }
  check("60 тиков внутри коридора — ноль записей в шкалы (график не дёргается)",
    timeWrites === quiet.t && priceWrites === quiet.p,
    "время " + (timeWrites - quiet.t) + ", цена " + (priceWrites - quiet.p));
  check("диапазон цены за эти тики не сдвинулся ни на пункт",
    JSON.stringify(pr()) === quiet.r, JSON.stringify(pr()) + " было " + quiet.r);

  // ================= цена дошла до верха: окно сдвигается на 15% =================
  const prBefore = JSON.parse(JSON.stringify(pr()));
  const spanBefore = prBefore.to - prBefore.from;
  let shifted = null, tick = 0;
  while (tick++ < 900 && !shifted) {
    const c = candles[candles.length - 1];
    c.close += 8;
    c.high = Math.max(c.high, c.close);
    series.update(c);
    const before = JSON.stringify(pr());
    step();
    if (JSON.stringify(pr()) !== before) shifted = { price: c.close, range: pr(), range0: JSON.parse(before) };
  }
  check("цена подошла к верху — окно подвинулось", !!shifted,
    "тиков " + tick + " цена " + lastPrice());
  if (shifted) {
    const span = shifted.range.to - shifted.range.from;
    check("после сдвига цена встала ровно в 15% от верха",
      Math.abs((shifted.range.to - shifted.price) / span - M) < 1e-9,
      (((shifted.range.to - shifted.price) / span) * 100).toFixed(3) + "%");
    check("размах (зум) при сдвиге сохранился",
      Math.abs(span - spanBefore) < 1e-9,
      "ширина " + span.toFixed(2) + " была " + spanBefore.toFixed(2));
    check("сдвиг не сработал на первом касании границы (есть гистерезис)",
      tick > 1, "тиков до сдвига: " + tick);
  }
  check("за видимые границы цена не выходила", !escaped,
    escaped ? JSON.stringify(escaped) : "");
  const writesUp = { t: timeWrites, p: priceWrites };
  for (let i = 0; i < 20; i++) {
    const c = candles[candles.length - 1];
    c.close += 1;                                   // мелкий шум у границы
    c.high = Math.max(c.high, c.close);
    series.update(c);
    step();
  }
  check("мелкий шум у самой границы окно больше не двигает",
    timeWrites === writesUp.t && priceWrites <= writesUp.p + 2,
    "время " + (timeWrites - writesUp.t) + ", цена " + (priceWrites - writesUp.p));

  // ================= цена пошла вниз: то же снизу =================
  const prDown = JSON.parse(JSON.stringify(pr()));
  const spanDown = prDown.to - prDown.from;
  let shiftedDown = null;
  tick = 0;
  while (tick++ < 1600 && !shiftedDown) {
    const c = candles[candles.length - 1];
    c.close -= 8;
    c.low = Math.min(c.low, c.close);
    series.update(c);
    const before = JSON.stringify(pr());
    step();
    if (JSON.stringify(pr()) !== before) shiftedDown = { price: c.close, range: pr() };
  }
  check("цена пошла вниз — окно подвинулось", !!shiftedDown, "тиков " + tick);
  if (shiftedDown) {
    const span = shiftedDown.range.to - shiftedDown.range.from;
    check("после сдвига вниз цена встала ровно в 15% от низа",
      Math.abs((shiftedDown.price - shiftedDown.range.from) / span - M) < 1e-9,
      (((shiftedDown.price - shiftedDown.range.from) / span) * 100).toFixed(3) + "%");
    check("размах при сдвиге вниз тоже сохранился",
      Math.abs(span - spanDown) < 1e-9,
      "ширина " + span.toFixed(2) + " была " + spanDown.toFixed(2));
  }
  check("вниз за видимые границы цена тоже не выходила", !escaped,
    escaped ? JSON.stringify(escaped) : "");

  // ================= ручной уход в историю: окно возвращается на 3% =================
  const spanNow = spanOf();
  ts.setVisibleLogicalRange({ from: 0, to: spanNow });     // как будто утащили мышью
  await frame();
  const tBefore = timeWrites;
  step();
  await frame();
  check("ручной уход в историю — окно вернулось к 3% справа",
    Math.abs(edge() - keepBars()) < 0.05 && timeWrites === tBefore + 1,
    "edge=" + edge().toFixed(3) + " keep=" + keepBars() + " записей " + (timeWrites - tBefore));

  // ================= жест утащил шкалу цены: возврат ставит цену в 15% =================
  const pNow = lastPrice();
  ps.setVisibleRange({ from: pNow - 140, to: pNow + 20 });      // как будто потянули мышью
  const snapWrites = priceWrites;
  priceSnap();
  const snapRange = pr();
  const snapSpan = snapRange.to - snapRange.from;
  check("после жеста цена вернулась ровно на 15% от верха",
    priceWrites === snapWrites + 1 && Math.abs((snapRange.to - pNow) / snapSpan - M) < 1e-9,
    JSON.stringify(snapRange) + " цена " + pNow);
  check("возврат после жеста размах шкалы цены не меняет",
    Math.abs(snapSpan - 160) < 1e-9, "ширина " + snapSpan.toFixed(3) + " было 160");
  // а внутри коридора возврат вертикаль не трогает
  ps.setVisibleRange({ from: pNow - 500, to: pNow + 500 });
  const quietWrites = priceWrites;
  const quietRange = JSON.stringify(pr());
  priceSnap();
  check("внутри коридора возврат после жеста шкалу цены не трогает",
    priceWrites === quietWrites && JSON.stringify(pr()) === quietRange,
    JSON.stringify(pr()) + " было " + quietRange);

  // ================= зум: слежение двигает окно, но ширину не меняет =================
  const zoomSpan = 45, zoomKeep = F.followKeepBars(zoomSpan, KEEP);
  ts.setVisibleLogicalRange({ from: lastIdx() + zoomKeep - zoomSpan, to: lastIdx() + zoomKeep });
  await frame();
  const zoomReal = spanOf();
  let zoomShift = null;
  tick = 0;
  while (tick++ < 900 && !zoomShift) {
    const c = candles[candles.length - 1];
    c.close += 6;
    c.high = Math.max(c.high, c.close);
    series.update(c);
    const before = JSON.stringify(pr());
    step();
    if (JSON.stringify(pr()) !== before) zoomShift = { range: pr() };
  }
  check("свой зум (45 свечей) сохраняется и при сдвиге окна",
    Math.abs(spanOf() - zoomReal) < 0.06,
    "ширина " + spanOf().toFixed(2) + " была " + zoomReal.toFixed(2));

  // ================= смена монеты (цена в 100 раз дальше) =================
  const newPrice = 3.5;
  candles.forEach((c) => {
    c.open = newPrice; c.high = newPrice * 1.001; c.low = newPrice * 0.999; c.close = newPrice;
  });
  series.setData(candles.map((c) => ({ ...c })));
  await frame();
  const looksLikeCoin = { price: lastPrice(), range: pr() };
  step();
  const corNew = corridor();
  check("другая монета — окно собрано вокруг новой цены, а не растянуто",
    corNew && corNew.from < newPrice && corNew.to > newPrice &&
    corNew.span < newPrice * 100,
    "цена " + looksLikeCoin.price + " диапазон " + JSON.stringify(pr()) +
    " коридор " + (corNew ? JSON.stringify(corNew) : "нет"));

  // ================= выключили слежение: автошкала возвращается библиотеке =================
  ps.applyOptions(F.followPriceOptions(false));
  await frame(); await frame();
  check("выключено: autoScale снова включён, отступы 6%/24%",
    ps.options().autoScale === true &&
    Math.abs(ps.options().scaleMargins.top - 0.06) < 1e-9 &&
    Math.abs(ps.options().scaleMargins.bottom - 0.24) < 1e-9,
    "autoScale=" + ps.options().autoScale + " margins=" + JSON.stringify(ps.options().scaleMargins));
  const writesOff = priceWrites;
  const rangeOff = JSON.stringify(pr());
  for (let i = 0; i < 30; i++) {
    const c = candles[candles.length - 1];
    c.close *= 1.02;
    c.high = Math.max(c.high, c.close);
    series.update(c);
    await frame();
  }
  check("выключено: библиотека сама пересчитывает шкалу, мы в неё не пишем",
    priceWrites === writesOff && JSON.stringify(pr()) !== rangeOff,
    "записей " + (priceWrites - writesOff) + " диапазон " + JSON.stringify(pr()));
  check("выключено: цена снова под автошкалой",
    pr() && lastPrice() <= pr().to + 1e-9 && lastPrice() >= pr().from - 1e-9,
    "цена " + lastPrice() + " диапазон " + JSON.stringify(pr()));

  // ================= выключено: новые свечи окно НЕ двигают =================
  // Страница ставит shiftVisibleRangeOnNewBar:false (app.js, applyFollowMode):
  // со включённым слежением окно ставим мы сами, а с выключенным график должен
  // стоять там, где его оставили, — иначе каждый новый бар тянул бы вид вправо
  // и промотать назад было бы нельзя.
  ts.applyOptions({ shiftVisibleRangeOnNewBar: false, rightOffset: 6 });
  ts.setVisibleLogicalRange({ from: lastIdx() - 40, to: lastIdx() + 6 });
  await frame();
  const lrStill = { ...lr() };
  for (let i = 0; i < 5; i++) {
    const c = { time: candles[candles.length - 1].time + T, open: 1, high: 1.01,
                low: 0.99, close: 1 };
    candles.push(c);
    series.update(c);
    await frame();
  }
  const lrAfter = lr();
  check("выключено: новые свечи окно не сдвигают — график статичен",
    lrAfter && Math.abs(lrAfter.from - lrStill.from) < 0.5 &&
    Math.abs(lrAfter.to - lrStill.to) < 0.5,
    JSON.stringify(lrAfter) + " было " + JSON.stringify(lrStill));
  // для контраста: с включённым сдвигом библиотека тянет вид за свечами —
  // именно это и приходилось видеть на выключенной кнопке до починки
  ts.applyOptions({ shiftVisibleRangeOnNewBar: true });
  const lrShift = { ...lr() };
  for (let i = 0; i < 3; i++) {
    const c = { time: candles[candles.length - 1].time + T, open: 1, high: 1.01,
                low: 0.99, close: 1 };
    candles.push(c);
    series.update(c);
    await frame();
  }
  check("а со включённым сдвигом библиотека тянула бы окно за свечами",
    lr().to - lrShift.to > 2, JSON.stringify(lr()) + " было " + JSON.stringify(lrShift));

  check("падений страницы нет", crashes.length === 0, crashes.join(" ~ "));

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => {
  console.log("  FAIL тест не прошёл: " + (e && e.message));
  console.log(`\nитог: ${ok} ок, ${fail + 1} ошибок`);
  process.exit(1);
});
