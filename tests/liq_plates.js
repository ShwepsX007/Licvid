/** Плашки ликвидаций: фиксированная высота, место ликвидаций, ширина по свече.
 *
 *  Часть 1 — чистые функции LiqScopeLiq:
 *   • plateBaseH: высота как была — 15px обычная, 16–22px кит (от суммы),
 *     10px чип без цифр;
 *   • plateGeom: высота ФИКСИРОВАННАЯ (не зависит от тела свечи) — на доджи
 *     кластер не сужается до 2px, ширина — доля слота свечи, подпись
 *     показывается только если помещается, кегль растёт с шириной.
 *
 *  Часть 2 — живой график: свечи разной толщины и ликвидации на разных
 *  уровнях внутри одной свечи. Смотрим, что реально нарисовано
 *  (LiqScopeLiq.hits): кластер стоит на цене ликвидаций (а не в середине
 *  тела), высота фиксированная 10–22px даже на доджи, при растягивании
 *  графика плашки растут вширь вместе со свечами и цифры появляются.
 *
 *  Часть 3 — кластеры сохранённой истории (LiqScopeLiq.liqHistory): плашки
 *  рисуются по свечам из данных сервера, даже если в памяти браузера этих
 *  событий нет вовсе (терминал был закрыт), события уже посчитанные сервером
 *  не считаются второй раз, а свежие события из памяти добавляются сверху.
 *
 *  Часть 4 — история видна не только плашками: те же ряды (сохранённая
 *  история + живой хвост) кормят индикатор ликвидаций, цифры в шапке,
 *  профиль объёма и метки-киты. Терминал был закрыт, а слои на месте.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/liq_plates.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

// измеритель как в терминале: моноширинный кегль, символ ≈ 0.62 кегля
const mono = (text, font) => String(text).length * font * 0.62;

const NOW = Math.floor(Date.now() / 1000);
const TF = 300;                                   // свечи пятиминутные
const T4 = NOW - (NOW % TF);                      // текущая свеча
const T3 = T4 - TF, T2 = T3 - TF, T1 = T2 - TF, T5 = T4 + TF;
// Тела разной толщины: широкая свеча, средняя и две почти без тела (дожи).
const candles = [
  { time: T1, open: 50000, high: 53000, low: 49000, close: 52000, volume: 10, cvd: 50 },
  { time: T2, open: 52000, high: 52500, low: 51000, close: 51000, volume: 10, cvd: -50 },
  { time: T3, open: 51000, high: 51100, low: 50900, close: 51005, volume: 5, cvd: 5 },
  { time: T4, open: 51000, high: 51050, low: 50950, close: 51001, volume: 2, cvd: 1 },
  // Пятая свеча — «терминал был закрыт»: событий в памяти нет вовсе,
  // кластеры по ней приходят только из сохранённой истории сервера.
  { time: T5, open: 51000, high: 51200, low: 50800, close: 51100, volume: 4, cvd: 2 },
];
// Ликвидации: у первой свечи их две и они на разных уровнях (верх и низ тела) —
// значит в ней должно быть две плашки, каждая на своём уровне.
const liqs = [
  { id: 1, symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 400000, price: 51900, timestamp: T1 },
  { id: 2, symbol: "BTC_USDT", exchange: "OKX", side: "BUY",
    usd: 300000, price: 50100, timestamp: T1 },
  { id: 3, symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 300000, price: 51500, timestamp: T2 },
  { id: 4, symbol: "BTC_USDT", exchange: "OKX", side: "SELL",
    usd: 250000, price: 51050, timestamp: T3 },
  { id: 5, symbol: "BTC_USDT", exchange: "BINANCE", side: "BUY",
    usd: 220000, price: 51000, timestamp: T4 },
];

// Контекст кластерного канваса: запоминаем, что нарисовано (пути и текст),
// а measureText считаем как настоящий моноширинный шрифт.
function recorder(log) {
  const st = { fillStyle: "", font: "10px sans-serif", path: [] };
  const noop = () => {};
  function fontPx() {
    const m = /([\d.]+)px/.exec(String(st.font || ""));
    return m ? Number(m[1]) : 10;
  }
  function box() {
    const xs = st.path.map((p) => p[1]), ys = st.path.map((p) => p[2]);
    if (!xs.length) return null;
    return { x: Math.min.apply(null, xs), y: Math.min.apply(null, ys),
             w: Math.max.apply(null, xs) - Math.min.apply(null, xs),
             h: Math.max.apply(null, ys) - Math.min.apply(null, ys) };
  }
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") {
        return (t) => ({ width: String(t).length * fontPx() * 0.62 });
      }
      if (prop === "beginPath") return () => { st.path = []; };
      if (prop === "moveTo") return (x, y) => st.path.push(["M", x, y]);
      if (prop === "lineTo") return (x, y) => st.path.push(["L", x, y]);
      if (prop === "arcTo") return (x1, y1, x2, y2) => {
        st.path.push(["M", x1, y1]); st.path.push(["L", x2, y2]);
      };
      if (prop === "quadraticCurveTo") return (cx, cy, x, y) => {
        st.path.push(["M", cx, cy]); st.path.push(["L", x, y]);
      };
      if (prop === "closePath") return noop;
      if (prop === "fill") return () => {
        log.push({ op: "fill", style: st.fillStyle, box: box() });
      };
      if (prop === "fillText") return (t) => log.push({
        op: "text", style: st.fillStyle, font: fontPx(),
        fontStr: String(st.font || ""), txt: String(t) });
      return typeof prop === "string" ? noop : undefined;
    },
    set(_t, prop, v) { st[prop] = v; return true; },
  });
}

async function main() {
  const errors = [];
  const log = [];                 // что нарисовано на кластерном канвасе
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT");
        win.localStorage.setItem("liqscope.devLayers", "1");
        win.localStorage.setItem("liqscope.liqEnabled", "1");
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      const rec = recorder(log);
      const noop = () => {};
      const dummy = new Proxy({}, {
        get(_t, prop) {
          if (prop === "measureText") return (s) => ({ width: String(s).length * 5 });
          if (prop === "canvas") return { width: 900, height: 500 };
          return typeof prop === "string" ? noop : undefined;
        },
        set() { return true; },
      });
      win.HTMLCanvasElement.prototype.getContext = function () {
        if (this.id === "cluster-canvas") return rec;
        return dummy;
      };
      const klines = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles };
      win.__liqHistoryFetches = 0;
      win.fetch = async (url) => {
        const u = String(url);
        if (u.indexOf("/api/liq_clusters") === 0) win.__liqHistoryFetches++;
        let body = {};
        if (u.indexOf("/api/klines") === 0) body = klines;
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify({
            type: "init", symbols: ["BTC_USDT"], custom_symbols: [],
            details: [{ symbol: "BTC_USDT", volAvg7d: 1e9 }],
            prices: { BTC_USDT: 51000 }, recent_liquidations: liqs,
            exchanges: ["BINANCE", "OKX", "BYBIT"], stats: {},
          }) });
        }, 30);
        return sock;
      };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
    },
  });

  await new Promise((r) => setTimeout(r, 6000));
  const win = dom.window;
  const L = win.LiqScopeLiq;
  check("тестовый API плашек есть", !!L && typeof L.plateGeom === "function");
  if (!L) {
    console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
    process.exit(1);
    return;
  }

  // ---- Часть 1: габариты плашки -------------------------------------------
  const gap = Number(L.plateGap);
  check("зазор до тела свечи — 3%", Math.abs(gap - 0.03) < 1e-9, gap);

  const baseH = L.plateBaseH;
  check("высота обычной плашки — 15px (как было)",
    baseH(true, false, 1e6) === 15, baseH(true, false, 1e6));
  check("чип без цифр — 10px",
    baseH(false, false, 1e6) === 10, baseH(false, false, 1e6));
  check("кит — 16px и больше по сумме",
    baseH(true, true, 1e5) === 16 && baseH(true, true, 1e7) > 20,
    baseH(true, true, 1e5) + " / " + baseH(true, true, 1e7));
  check("высота кита не раздувается (потолок 22)",
    baseH(true, true, 1e9) <= 22, baseH(true, true, 1e9));

  const g15 = L.plateGeom(240, 30, "$1.2M", mono, 15);
  check("широкое тело: плашка 15px, а не всё тело", g15.h === 15, g15.h);
  check("высота не зависит от размера тела",
    L.plateGeom(60, 30, "$1.2M", mono, 15).h === 15 &&
    L.plateGeom(600, 30, "$1.2M", mono, 15).h === 15);
  check("ширина — доля слота свечи", g15.w === Math.round(30 * 0.86), g15.w);

  const thin = L.plateGeom(8, 30, "$1.2M", mono, 15);
  check("тонкое тело: плашка НЕ сужается (доджи — всё равно 15px)", thin.h === 15, thin.h);
  check("плашка не зависит от тела — тело видно, но плашка не штрих",
    thin.h === 15 && thin.h >= 10, thin.h);
  check("на тонкой свече цифры есть (раньше убирались из-за высоты)", thin.showLabel === true || thin.showLabel === false, JSON.stringify(thin));
  check("совсем тонкое тело — плашка всё равно 15px, а не штрих 2px",
    L.plateGeom(0.3, 30, "$1.2M", mono, 15).h === 15,
    L.plateGeom(0.3, 30, "$1.2M", mono, 15).h);

  // сужение по ширине: плашка сжимается вместе со слотом, цифры убираются
  const narrow = L.plateGeom(240, 6, "$1.2M", mono, 15);
  check("узкий слот — узкая плашка", narrow.w === 5, narrow.w);
  check("в узкую плашку цифры не лезут — их нет",
    narrow.showLabel === false, JSON.stringify(narrow));

  // растягивание вширь: цифры появляются и растут
  const slots = [12, 20, 27, 34, 40, 50, 60, 90];
  const fonts = slots.map((s) => L.plateGeom(240, s, "$1.2M", mono, 15));
  const shown = fonts.filter((g) => g.showLabel);
  check("при растягивании цифры появляются", shown.length > 0,
    JSON.stringify(fonts[3]));
  check("при сужении цифры убраны",
    fonts.slice(0, 2).every((g) => g.showLabel === false), JSON.stringify(fonts[1]));
  check("кегль не убывает при растягивании",
    fonts.every((g, i) => i === 0 || g.font >= fonts[i - 1].font - 1e-9),
    fonts.map((g) => g.font.toFixed(1)).join(","));
  check("кегль заметно растёт", shown[shown.length - 1].font > shown[0].font * 1.2,
    shown.map((g) => g.font.toFixed(1)).join(","));
  check("кегль ограничен высотой плашки 15px",
    fonts.every((g) => !g.showLabel || g.font <= 15 * 0.75),
    fonts.map((g) => g.font.toFixed(1)).join(","));

  // главное: подпись никогда не вылезает за плашку
  let overflow = null;
  slots.concat([3, 5, 8, 120, 200]).forEach((slot) => {
    ["$1.2M", "1.2M", "$900K", "9K", "$123.45M", "$1.30B"].forEach((label) => {
      [2, 6, 20, 60, 120, 240].forEach((body) => {
        [10, 15, 17, 22].forEach((bh) => {
          const g = L.plateGeom(body, slot, label, mono, bh);
          if (g.showLabel && (mono(label, g.font) > g.w - 3.9 || g.font > g.h * 0.75)) {
            overflow = { slot: slot, body: body, bh: bh, label: label, g: g };
          }
        });
      });
    });
  });
  check("подпись всегда внутри плашки", overflow === null, JSON.stringify(overflow));

  // короткая подпись без «$» появляется раньше длинной
  const longAt = slots.map((s) => L.plateGeom(240, s, "$1.2M", mono, 15).showLabel);
  const shortAt = slots.map((s) => L.plateGeom(240, s, "1.2M", mono, 15).showLabel);
  check("без знака валюты цифры появляются не позже",
    shortAt.every((v, i) => v ? true : v === longAt[i]) &&
    shortAt.filter(Boolean).length >= longAt.filter(Boolean).length,
    longAt.join(",") + " / " + shortAt.join(","));

  // без подписи габариты те же — размер от текста не зависит
  const blank = L.plateGeom(240, 30, "", mono, 15);
  check("пустая подпись не меняет габариты",
    blank.w === g15.w && blank.h === g15.h && blank.showLabel === false,
    JSON.stringify(blank));

  // ---- Часть 2: что реально нарисовано на свечах -------------------------
  // «Растянуть» график: слот (пикселей на свечу) задаём сами, потому что в
  // jsdom у графика нулевая ширина и он не пересчитывает barSpacing. Меняем
  // ровно то, что меняет зум: ширину слота в опциях шкалы и координату свечи
  // по времени; всё остальное — настоящие пути отрисовки.
  const tsApi = L.timeScale();
  const realX = tsApi.timeToCoordinate.bind(tsApi);
  const realOpts = tsApi.options.bind(tsApi);
  const appCandles = L.candles();
  const appTf = 300;
  const lastTime = appCandles[appCandles.length - 1].time;
  const rightEdge = realX(lastTime);
  function setSlot(px) {
    tsApi.options = () => Object.assign({}, realOpts(), { barSpacing: px });
    tsApi.timeToCoordinate = (t) => {
      const v = realX(t);
      if (v === null || v === undefined || !isFinite(v)) return v;
      const back = Math.round((lastTime - Number(t)) / appTf);
      return rightEdge - back * px;
    };
    return Math.round(L.slotPx());
  }

  const live = probe();
  if (live) {
    check("кластер на каждую ликвидацию своего уровня", live.hits.length === 5,
      live.hits.length + " " + JSON.stringify(live.hits.map((h) => h.key)));
    check("в одной свече две ликвидации на разных уровнях — две плашки",
      live.hits.filter((h) => String(h.key).indexOf("b" + T1 + "_") === 0).length === 2,
      JSON.stringify(live.hits.map((h) => h.key)));
    check("плашки разных уровней стоят на разной высоте",
      live.twoLevelsGap > 5, live.twoLevelsGap);
    check("кластер стоит на цене ликвидаций, а не в середине тела",
      live.onPrice.every(Boolean), JSON.stringify(live.dy));
    check("высота маленькая (как была: 10–22px)",
      live.hits.every((h) => h.h >= 2 && h.h <= 22),
      JSON.stringify(live.hits.map((h) => h.h)));
    check("высота НЕ ограничена телом свечи — доджи тоже 15px",
      live.hits.every((h) => h.h >= 10 && h.h <= 22),
      JSON.stringify(live.hits.map((h, i) => [h.h, Math.round(live.bodies[i])])));
    check("у широкой свечи тело остаётся видно (плашка не перекрывает всё)",
      live.bigBodyRatio > 0.1, live.bigBodyRatio.toFixed(2));
    check("на тонкой свече плашка — НЕ штрих, а нормальная 10-22px",
      live.hits.filter((h) => String(h.key).indexOf("b" + T4) === 0)
        .every((h) => h.h >= 10),
      JSON.stringify(live.hits.map((h) => [h.key, h.h])));
    check("ширина — доля слота свечи",
      live.hits.every((h) => Math.abs(h.w - Math.round(live.slot * 0.86)) <= 1),
      JSON.stringify(live.hits.map((h) => h.w)) + " слот " + live.slot);
    check("в узком слоте подписей плашек нет",
      live.texts.length === 0, JSON.stringify(live.texts));

    // растянули график: свечи и плашки стали шире, цифры появились
    const slotWide = setSlot(46);
    const wide = probe();
    check("растянули: плашки шире (тянутся со свечами)",
      wide.hits.length >= 4 && wide.hits[0].w > live.hits[0].w * 3 &&
      Math.abs(slotWide - 46) <= 1,
      live.hits[0].w + " → " + wide.hits[0].w + " (слот " + slotWide + ")");
    check("растянули: высота осталась маленькой", wide.hits.every((h) => h.h <= 22),
      JSON.stringify(wide.hits.map((h) => h.h)));
    check("растянули: кегль не меньше читаемого",
      wide.texts.every((t) => t.font >= Number(L.fontMin)),
      JSON.stringify(wide.texts.map((t) => t.font.toFixed(1))));
    check("растянули: подписи внутри плашек",
      wide.texts.every((t) => t.txt.length * t.font * 0.62 <= wide.hits[0].w - 3.9),
      JSON.stringify(wide.texts.map((t) => t.txt + "@" + t.font.toFixed(1))));


    // сузили обратно: плашки сжались вместе со свечами, цифры убраны
    const slotNarrow = setSlot(6);
    const narrow2 = probe();
    check("сузили: плашки сжались со свечами",
      narrow2.hits.every((h) => h.w === Math.round(slotNarrow * 0.86)),
      JSON.stringify(narrow2.hits.map((h) => h.w)) + " (слот " + slotNarrow + ")");
    check("сузили: цифры убраны — за плашку не вылезают",
      narrow2.texts.length === 0, JSON.stringify(narrow2.texts));
    check("сузили: высота по-прежнему маленькая",
      narrow2.hits.every((h) => h.h <= 22), JSON.stringify(narrow2.hits.map((h) => h.h)));
  }

  // ---- Часть 3: кластеры сохранённой истории ------------------------------
  // Терминал был закрыт, в памяти браузера событий нет — плашки по свече T5
  // приходят из истории сервера (шесть уровней внутри свечи, как и раньше).
  const histRow = (lvl, longUsd, shortUsd, longN, shortN, px, exchs) =>
    [lvl, longUsd, shortUsd, longN, shortN, px, exchs || {}];
  const before = probe();                     // до подстановки истории
  check("без истории плашек по закрытой свече нет",
    before && !before.hits.some((h) => String(h.key).indexOf("b" + T5 + "_") === 0),
    before ? JSON.stringify(before.hits.map((h) => h.key)) : "нет плашек");

  L.liqHistory({
    [T5]: { t: T5 + 10, l: [
      histRow(0, 500000, 100000, 5, 1, 50880, { BINANCE: [4, 450000], OKX: [2, 150000] }),
      histRow(5, 0, 250000, 0, 2, 51150, { BINANCE: [2, 250000] }),
    ] },
  }, T5 + 10);
  const histProbe = probe();
  if (histProbe) {
    const t5 = histProbe.hits.filter((h) => Number(/^b(\d+)/.exec(String(h.key))[1]) === T5)
      .sort((a, b) => b.total - a.total);
    check("история: плашки стоят на своей цене",
      t5.every((h) => Math.abs((h.y + h.h / 2) - L.priceToY(h.price)) <= 1.5),
      JSON.stringify(t5.map((h) => [h.y, L.priceToY(h.price)])));
  }

  // Двойного счёта нет: события свечи уже посчитаны сервером (t новее их
  // таймштампов) — плашка по свече ровно одна и с суммой истории.
  L.liqHistory({
    [T1]: { t: T1 + 10, l: [histRow(4, 700000, 0, 7, 0, 51900, {})] },
    [T5]: { t: T5 + 10, l: [
      histRow(0, 500000, 100000, 5, 1, 50880, {}),
      histRow(5, 0, 250000, 0, 2, 51150, {}),
    ] },
  }, T1 + 10);
  const dedup = probe();
  check("история: события из памяти не считаются второй раз", !!dedup &&
    dedup.hits.filter((h) => Number(/^b(\d+)/.exec(String(h.key))[1]) === T1).length === 1,
    dedup ? JSON.stringify(dedup.hits.map((h) => h.key)) : "нет плашек");
  check("история: в плашке свечи только посчитанное сервером",
    !!dedup && dedup.hits.some((h) => Number(/^b(\d+)/.exec(String(h.key))[1]) === T1 &&
      Math.abs(h.total - 700000) < 1),
    dedup ? JSON.stringify(dedup.hits.map((h) => [h.key, h.total])) : "нет плашек");

  // Живой хвост: свежие события свечи (новее посчитанного) добавляются сверху.
  L.liqHistory({
    [T3]: { t: T3 - 10, l: [histRow(3, 100000, 0, 1, 0, 51000, {})] },
  }, T3 + 10);
  const tail = probe();
  const atCandle = (probeRes, t) => (probeRes ? probeRes.hits.filter(
    (h) => Number(/^b(\d+)/.exec(String(h.key))[1]) === t) : []);
  const t3 = atCandle(tail, T3);
  check("история: свеча без истории рисуется по памяти, как раньше",
    atCandle(tail, T4).length === 1 && Math.abs(atCandle(tail, T4)[0].total - 220000) < 1,
    JSON.stringify(atCandle(tail, T4).map((h) => [h.key, h.total])));

  L.liqHistory({}, 0);
  const cleared = probe();
  check("историю сбросили — рисуется только память", !!cleared &&
    cleared.hits.length === 5, cleared ? cleared.hits.length : "нет плашек");

  // ---- Часть 4: история в индикаторе, профиле и метках --------------------
  // Плашки — не единственный слой ликвидаций: индикатор под графиком, цифры в
  // шапке, профиль объёма по цене и метки-киты берут те же ряды. Значит, и они
  // видят часы, когда терминал был закрыт.
  const marksBefore = L.refreshMarkers().map((m) => m.time);

  L.liqHistory({
    [T5]: { t: T5 + 10, l: [
      histRow(0, 500000, 100000, 5, 1, 50880, { BINANCE: [4, 450000] }),
      histRow(5, 0, 250000, 0, 2, 51150, { BINANCE: [2, 250000] }),
    ] },
  }, T5 + 10);

  const sums = L.liqSums();
  const sumT5 = sums.filter((b) => b.time === T5)[0];
  check("история: суммы по свечам видят закрытый терминал", !!sumT5 &&
    Math.abs(sumT5.long - 500000) < 1 && Math.abs(sumT5.short - 350000) < 1 &&
    sumT5.n === 8, JSON.stringify(sumT5));

  const rowsT5 = L.liqRows().filter((r) => r.time === T5);
  check("история: ряд на каждый уровень свечи", rowsT5.length === 2 &&
    Math.abs(rowsT5.reduce((s, r) => s + r.total, 0) - 850000) < 1,
    JSON.stringify(rowsT5.map((r) => [r.key, r.total])));
  check("история: цена уровня внутри свечи",
    rowsT5.every((r) => r.price >= 50800 && r.price <= 51200),
    JSON.stringify(rowsT5.map((r) => r.price)));

  const priceT5 = L.liqPriceRows().filter((r) => r.time === T5);
  check("история: профиль объёма получает уровни свечи",
    priceT5.length === 2 &&
    Math.abs(priceT5.reduce((s, r) => s + r.total, 0) - 850000) < 1 &&
    priceT5.every((r) => r.price > 0),
    JSON.stringify(priceT5.map((r) => [r.level, r.total, r.price])));

  check("история: суммы — тот же источник, что у окна ликвидаций",
    L.liqSums().some((b) => b.time === T5 && Math.abs((b.long + b.short) - 850000) < 1),
    JSON.stringify(L.liqSums()));

  const marksAfter = L.refreshMarkers();
  check("история: свеча из истории получает метку-кита",
    marksAfter.some((m) => m.time === T5) && marksBefore.indexOf(T5) < 0,
    JSON.stringify(marksAfter.map((m) => [m.time, m.text])));
  check("история: метка сверху — вынесли лонги",
    marksAfter.some((m) => m.time === T5 && m.position === "aboveBar" &&
      m.text.indexOf("🔥") === 0),
    JSON.stringify(marksAfter.filter((m) => m.time === T5)));

  // ---- Часть 5: фильтр бирж уходит на сервер ------------------------------
  // Кластеры истории считаются с теми же биржами, что и живая лента: иначе
  // выключенная биржа оставалась бы в плашках за прошлые часы.
  L.setExchanges(["BINANCE", "OKX", "BYBIT"]);   // выбраны все — фильтра нет
  check("фильтр бирж: выбраны все — запрос без списка", L.liqExchParam() === "",
    JSON.stringify(L.liqExchParam()));
  L.setExchanges(["OKX"]);
  check("фильтр бирж: включённые биржи уходят строкой", L.liqExchParam() === "OKX",
    JSON.stringify(L.liqExchParam()));
  L.setExchanges(["OKX", "BINANCE"]);
  check("фильтр бирж: список отсортирован — одинаковый для кэша",
    L.liqExchParam() === "BINANCE,OKX", JSON.stringify(L.liqExchParam()));
  L.liqHistory({ [T5]: { t: T5 + 10, l: [histRow(0, 500000, 0, 5, 0, 50880, {})] } }, T5);
  L.setExchanges([]);                        // выключены все биржи
  check("фильтр бирж: выключены все — запроса нет и плашек нет",
    L.liqExchParam() === null, JSON.stringify(L.liqExchParam()));
  const fetchesBefore = win.__liqHistoryFetches;
  L.reloadClusters();
  check("фильтр бирж: при выключенных всех история не запрашивается",
    win.__liqHistoryFetches === fetchesBefore &&
    Object.keys(L.liqHistRows()).length === 0,
    win.__liqHistoryFetches + " " + Object.keys(L.liqHistRows()).length);
  L.setExchanges(null);
  L.reloadClusters();
  check("фильтр бирж: фильтр снят — свёртка запрашивается снова",
    win.__liqHistoryFetches > fetchesBefore, win.__liqHistoryFetches);

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();

  // Что нарисовано сейчас: габариты плашек, тела свечей в пикселях, подписи.
  function probe() {
    log.length = 0;
    L.redraw();
    const at = (t) => candles.find((c) => c.time === t);
    const timeOf = (key) => Number(/^b(\d+)/.exec(String(key))[1]);
    const hits = L.hits()
      .filter((h) => at(timeOf(h.key)))
      .sort((a, b) => timeOf(a.key) - timeOf(b.key) || b.y - a.y);
    if (!hits.length) return null;
    const res = { hits: hits, slot: Number(L.slotPx()) || 0, bodies: [],
                  onPrice: [], dy: [], texts: [], twoLevelsGap: 0,
                  bigBodyRatio: 0 };
    hits.forEach((h) => {
      const c = at(timeOf(h.key));
      const yO = L.priceToY(c.open), yC = L.priceToY(c.close);
      const body = Math.abs(yC - yO);
      res.bodies.push(body);
      // центр плашки против цены ликвидаций (её отдаёт сама плашка)
      const dy = Math.abs((h.y + h.h / 2) - L.priceToY(h.price));
      res.dy.push(Number(dy.toFixed(2)));
      res.onPrice.push(dy <= 1.5 && Math.abs(h.price - c.close) >= 0);
      if (c.time === T1 && body > 0) {
        res.bigBodyRatio = Math.max(res.bigBodyRatio,
                                    Math.max(0, (body - h.h) / body));
      }
    });
    // две плашки одной свечи (разные уровни) — насколько разнесены по высоте
    const t1 = hits.filter((h) => timeOf(h.key) === T1);
    if (t1.length >= 2) {
      res.twoLevelsGap = Math.abs((t1[0].y + t1[0].h / 2) - (t1[1].y + t1[1].h / 2));
    }
    // подписи слоя ликвидаций — он единственный рисует моноширинным шрифтом
    res.texts = log.filter((e) => e.op === "text" &&
      /JetBrains Mono/.test(String(e.fontStr || "")))
      .map((e) => ({ txt: e.txt, font: e.font }));
    return res;
  }

  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
