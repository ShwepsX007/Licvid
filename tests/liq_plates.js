/** Плашки ликвидаций: прежняя высота, место ликвидаций, ширина по свече.
 *
 *  Часть 1 — чистые функции LiqScopeLiq:
 *   • plateBaseH: высота как была — 15px обычная, 16–22px кит (от суммы),
 *     10px чип без цифр;
 *   • plateGeom: высота ограничена телом свечи с зазором 3% с каждой стороны
 *     (тело никогда не закрывается целиком), ширина — доля слота свечи,
 *     подпись показывается только если помещается, кегль растёт с шириной.
 *
 *  Часть 2 — живой график: свечи разной толщины и ликвидации на разных
 *  уровнях внутри одной свечи. Смотрим, что реально нарисовано
 *  (LiqScopeLiq.hits): кластер стоит на цене ликвидаций (а не в середине
 *  тела), высота маленькая и тело остаётся видно, на тонкой свече плашка
 *  сжимается вместе с телом и цифры убираются; при растягивании графика
 *  плашки растут вширь вместе со свечами и цифры появляются.
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
const T3 = T4 - TF, T2 = T3 - TF, T1 = T2 - TF;
// Тела разной толщины: широкая свеча, средняя и две почти без тела (дожи).
const candles = [
  { time: T1, open: 50000, high: 53000, low: 49000, close: 52000, volume: 10, cvd: 50 },
  { time: T2, open: 52000, high: 52500, low: 51000, close: 51000, volume: 10, cvd: -50 },
  { time: T3, open: 51000, high: 51100, low: 50900, close: 51005, volume: 5, cvd: 5 },
  { time: T4, open: 51000, high: 51050, low: 50950, close: 51001, volume: 2, cvd: 1 },
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
      win.fetch = async (url) => {
        const u = String(url);
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
            exchanges: ["BINANCE", "OKX"], stats: {},
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
  check("тонкое тело: плашка тоньше тела (зазор 3%)", thin.h === 7, thin.h);
  check("плашка не выше тела — тело видно снизу и сверху",
    thin.h < 8 && thin.h / 8 < 0.95, thin.h);
  check("на тонкой свече цифры убраны", thin.showLabel === false, JSON.stringify(thin));
  check("совсем тонкое тело — плашка-штрих",
    L.plateGeom(0.3, 30, "$1.2M", mono, 15).h === 2,
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
    check("высота ограничена телом свечи",
      live.hits.every((h, i) => h.h <= Math.max(2, Math.floor(live.bodies[i] * 0.94))),
      JSON.stringify(live.hits.map((h, i) => [h.h, Math.round(live.bodies[i])])));
    check("у широкой свечи тело остаётся видно",
      live.bigBodyRatio > 0.6, live.bigBodyRatio.toFixed(2));
    check("на тонкой свече плашка — штрих, тело видно",
      live.hits.filter((h) => String(h.key).indexOf("b" + T4) === 0)
        .every((h) => h.h <= 7),
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
    check("растянули: у свечей с телом цифры появились", wide.texts.length === 3,
      JSON.stringify(wide.texts));
    check("растянули: у тонких свечей цифр нет (плашка-штрих)",
      wide.hits.filter((h) => String(h.key).indexOf("b" + T3) === 0 ||
                              String(h.key).indexOf("b" + T4) === 0)
        .every((h) => h.h < Number(L.fontMin) / 0.74),
      JSON.stringify(wide.hits.map((h) => [h.key, h.h])));
    check("растянули: кегль не меньше читаемого",
      wide.texts.every((t) => t.font >= Number(L.fontMin)),
      JSON.stringify(wide.texts.map((t) => t.font.toFixed(1))));
    check("растянули: подписи внутри плашек",
      wide.texts.every((t) => t.txt.length * t.font * 0.62 <= wide.hits[0].w - 3.9),
      JSON.stringify(wide.texts.map((t) => t.txt + "@" + t.font.toFixed(1))));

    const slotWider = setSlot(70);
    const wider = probe();
    check("растянули сильнее: кегль ещё крупнее",
      wider.texts.length === 3 && wider.texts[0].font > wide.texts[0].font &&
      Math.abs(slotWider - 70) <= 1,
      JSON.stringify(wider.texts.map((t) => t.font.toFixed(1))) + " (слот " + slotWider + ")");

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
