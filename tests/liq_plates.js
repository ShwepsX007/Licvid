/** Плашки ликвидаций: размер тела свечи, зазор 3%, цифры по месту.
 *
 *  Часть 1 — чистая функция LiqScopeLiq.plateGeom: высота плашки почти
 *  равна телу свечи (зазор LIQ_PLATE_GAP с каждой стороны), ширина — доля
 *  слота свечи, подпись показывается только если помещается, кегль растёт
 *  вместе с телом вширь и ограничен высотой плашки.
 *
 *  Часть 2 — живой график: три свечи с разными телами, ликвидации на каждой.
 *  Смотрим, что реально нарисовано (LiqScopeLiq.hits): одна плашка на свечу,
 *  её высота = тело минус зазор, она внутри тела и не шире слота; при
 *  растягивании графика (barSpacing) вместе со свечами растёт и плашка, а
 *  цифры появляются/убираются по месту, но никогда не вылезают за плашку.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/liq_plates.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const NOW = Math.floor(Date.now() / 1000);
const TF = 300;                                   // свечи пятиминутные
const T3 = NOW - (NOW % TF);                      // текущая свеча
const T2 = T3 - TF, T1 = T2 - TF;
// Тела свечей разной высоты: широкая / средняя / узкая — по ним видно,
// что плашка тянется вместе со свечой, а не живёт своей жизнью.
const candles = [
  { time: T1, open: 50000, high: 53000, low: 49000, close: 52000, volume: 10, cvd: 50 },
  { time: T2, open: 52000, high: 52500, low: 51000, close: 51000, volume: 10, cvd: -50 },
  { time: T3, open: 51000, high: 51100, low: 50900, close: 51100, volume: 10, cvd: 10 },
];
const liqs = [
  { id: 1, symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 400000, price: 51000, timestamp: T1 },
  { id: 2, symbol: "BTC_USDT", exchange: "BINANCE", side: "BUY",
    usd: 300000, price: 51500, timestamp: T2 },
  { id: 3, symbol: "BTC_USDT", exchange: "OKX", side: "SELL",
    usd: 250000, price: 51050, timestamp: T3 },
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

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

// измеритель как в терминале: моноширинный кегль, символ ≈ 0.62 кегля
const mono = (text, font) => String(text).length * font * 0.62;

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
      try { win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT"); } catch (e) { /* ignore */ }
      try {
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
  let live = null;                // результаты живой части (часть 2)
  check("тестовый API плашек есть", !!L && typeof L.plateGeom === "function");
  if (!L) {
    console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
    process.exit(1);
    return;
  }

  const gap = Number(L.plateGap);
  check("зазор до тела свечи — 3%", Math.abs(gap - 0.03) < 1e-9, gap);

  // высота: почти тело свечи, зазор с каждой стороны
  const big = L.plateGeom(120, 30, "$1.2M", mono);
  const expectH = Math.round(120 * (1 - 2 * gap));
  check("высота = тело минус зазоры", big.h === expectH, big.h + " vs " + expectH);
  check("тело видно с обеих сторон", big.h < 120 && big.h / 120 > 0.9, big.h);
  // ширина: доля слота свечи — тянется вместе со свечой
  check("ширина тянется со слотом свечи", big.w === Math.round(30 * 0.86), big.w);

  // сужение: плашка сжимается, цифры убираются
  const narrow = L.plateGeom(120, 6, "$1.2M", mono);
  check("узкая свеча — узкая плашка", narrow.w === 5, narrow.w);
  check("в узкую плашку цифры не лезут — их нет",
    narrow.showLabel === false, JSON.stringify(narrow));
  check("плашка не вылезает за слот", narrow.w <= 6, narrow.w);

  // тело-дождик: плашка не раздувается, цифр нет
  const doji = L.plateGeom(2, 30, "$1.2M", mono);
  check("тонкое тело — плашка тонкая", doji.h === 2, doji.h);
  check("в тонкую плашку подпись не влезает", doji.showLabel === false, JSON.stringify(doji));

  // растягивание вширь: кегль растёт вместе со свечой
  const slots = [12, 20, 27, 34, 40, 50, 60, 90];
  const fonts = slots.map((slot) => L.plateGeom(240, slot, "$1.2M", mono));
  const shown = fonts.filter((g) => g.showLabel);
  check("при растягивании цифры появляются", shown.length > 0, JSON.stringify(fonts[2]));
  check("при сужении цифры убраны",
    fonts.slice(0, 2).every((g) => g.showLabel === false), JSON.stringify(fonts[1]));
  check("кегль не убывает при растягивании",
    fonts.every((g, i) => i === 0 || g.font >= fonts[i - 1].font - 1e-9),
    fonts.map((g) => g.font.toFixed(1)).join(","));
  check("кегль заметно растёт", shown[shown.length - 1].font > shown[0].font * 1.5,
    shown.map((g) => g.font.toFixed(1)).join(","));
  check("кегль упирается в потолок 14",
    Math.abs(shown[shown.length - 1].font - 14) < 1e-9, shown[shown.length - 1].font);

  // главное: подпись никогда не вылезает за плашку
  let overflow = null;
  slots.concat([3, 5, 8, 120, 200]).forEach((slot) => {
    ["$1.2M", "1.2M", "$900K", "9K", "$123.45M", "$1.30B"].forEach((label) => {
      [2, 6, 20, 60, 120, 240].forEach((body) => {
        const g = L.plateGeom(body, slot, label, mono);
        if (g.showLabel && mono(label, g.font) > g.w - 3.9) {
          overflow = { slot: slot, body: body, label: label, g: g };
        }
      });
    });
  });
  check("подпись всегда внутри плашки", overflow === null, JSON.stringify(overflow));

  // короткая подпись без «$» появляется раньше длинной
  const longAt = slots.map((slot) => L.plateGeom(240, slot, "$1.2M", mono).showLabel);
  const shortAt = slots.map((slot) => L.plateGeom(240, slot, "1.2M", mono).showLabel);
  check("без знака валюты цифры появляются не позже",
    shortAt.every((v, i) => v ? true : v === longAt[i]) &&
    shortAt.filter(Boolean).length >= longAt.filter(Boolean).length,
    longAt.join(",") + " / " + shortAt.join(","));

  // без подписи плашка такая же по габаритам — размер от текста не зависит
  const blank = L.plateGeom(120, 30, "", mono);
  check("пустая подпись не меняет габариты",
    blank.w === big.w && blank.h === big.h && blank.showLabel === false,
    JSON.stringify(blank));

  // кегль не вылезает за плашку
  check("кегль ограничен высотой плашки",
    fonts.every((g) => !g.showLabel || g.font <= g.h * 0.75 + 0.01),
    fonts.map((g) => g.font.toFixed(1)).join(","));

  // «Растянуть» график: слот (пикселей на свечу) задаём сами, потому что в
  // jsdom у графика нулевая ширина и он не пересчитывает barSpacing. Меняем
  // ровно то, что меняет зум: координату свечи по времени; всё остальное —
  // настоящие пути отрисовки: плашки, тело свечи, подписи.
  const tsApi = L.timeScale();
  const realX = tsApi.timeToCoordinate.bind(tsApi);
  const realOpts = tsApi.options.bind(tsApi);
  const appCandles = L.candles();
  const appTf = 300;
  const lastTime = appCandles[appCandles.length - 1].time;
  const rightEdge = realX(lastTime);
  function setSlot(px) {
    // как отдаёт живой график после зума: ширина слота в опциях шкалы…
    tsApi.options = () => Object.assign({}, realOpts(), { barSpacing: px });
    // …и та же ширина в координатах свечей
    tsApi.timeToCoordinate = (t) => {
      const v = realX(t);
      if (v === null || v === undefined || !isFinite(v)) return v;
      const back = Math.round((lastTime - Number(t)) / appTf);
      return rightEdge - back * px;
    };
    return Math.round(L.slotPx());
  }

  // ---- Часть 2: что реально нарисовано на свечах -------------------------
  live = probe();
  if (live) {
    check("на каждую свечу с ликвидациями — своя плашка", live.hits.length === 3,
      JSON.stringify(live.hits));
    check("высоты плашек повторяют высоты тел свечей",
      live.ratios.every((r) => Math.abs(r - (1 - 2 * gap)) < 0.06),
      JSON.stringify(live.bodies) + " / " + JSON.stringify(live.heights));
    check("высокая свеча — высокая плашка, низкая — низкая",
      live.heights[0] > live.heights[1] && live.heights[1] > live.heights[2]
      && live.bodies[0] > live.bodies[1] && live.bodies[1] > live.bodies[2],
      JSON.stringify(live.bodies) + " / " + JSON.stringify(live.heights));
    check("плашка не закрывает тело целиком",
      live.hits.every((h, i) => h.h < live.bodies[i] && h.h / live.bodies[i] > 0.85),
      JSON.stringify(live.hits.map((h, i) => (h.h / live.bodies[i]).toFixed(2))));
    check("плашка внутри тела свечи (сверху и снизу)",
      live.inside.every(Boolean), JSON.stringify(live.inside));
    check("ширина — доля слота свечи",
      live.wFrac.every((f) => Math.abs(f - 0.86) < 0.05),
      JSON.stringify(live.wFrac.map((f) => f.toFixed(3))));
    check("плашка уже свечи и не наезжает на соседнюю",
      live.hits.every((h) => h.w <= live.slot + 1) && live.slot > 0,
      live.slot);
    check("в узком слоте подписей плашек нет",
      live.texts.length === 0, JSON.stringify(live.texts));

    // Растянули график: свечи встают реже (слот шире) — вместе с ними
    // растут плашки, и появляются цифры.
    const slotWide = setSlot(46);
    const wide = probe();
    check("растянули: свечи и плашки стали шире",
      wide.hits.length === 3 && wide.hits[0].w > live.hits[0].w * 3 &&
      Math.abs(slotWide - 46) <= 1,
      live.hits[0].w + " → " + wide.hits[0].w + " (слот " + slotWide + ")");
    check("растянули: высоты плашек по-прежнему по телам свечей",
      wide.ratios.every((r) => Math.abs(r - (1 - 2 * gap)) < 0.06) &&
      wide.heights[0] > wide.heights[1] && wide.heights[1] > wide.heights[2],
      JSON.stringify(wide.ratios.map((r) => r.toFixed(3))));
    check("растянули: цифры появились", wide.texts.length >= 2,
      JSON.stringify(wide.texts));
    check("растянули: кегль не меньше читаемого",
      wide.texts.every((t) => t.font >= Number(L.fontMin)),
      JSON.stringify(wide.texts.map((t) => t.font.toFixed(1))));
    check("растянули: подписи внутри плашек",
      wide.texts.every((t) => t.txt.length * t.font * 0.62 <= wide.hits[0].w - 3.9),
      JSON.stringify(wide.texts.map((t) => t.txt + "@" + t.font.toFixed(1))));
    check("растянули: тонкая свеча так и осталась без цифр",
      wide.texts.length === 2, JSON.stringify(wide.texts.map((t) => t.txt)));

    const slotWider = setSlot(70);
    const wider = probe();
    check("растянули сильнее: кегль ещё крупнее",
      wider.texts.length >= 2 && wider.texts[0].font > wide.texts[0].font &&
      Math.abs(slotWider - 70) <= 1,
      JSON.stringify(wider.texts.map((t) => t.font.toFixed(1))) + " (слот " + slotWider + ")");

    // сузили обратно — плашки сжались вместе со свечами, цифры убраны
    const slotNarrow = setSlot(6);
    const narrow2 = probe();
    check("сузили: плашки сжались со свечами",
      narrow2.hits.length === 3 &&
      narrow2.hits.every((h) => h.w <= live.hits[0].w + 1 &&
                               h.w === Math.round(slotNarrow * 0.86)),
      JSON.stringify(narrow2.hits.map((h) => h.w)) + " (слот " + slotNarrow + ")");
    check("сузили: цифры убраны — за плашку не вылезают",
      narrow2.texts.length === 0, JSON.stringify(narrow2.texts));
  }
  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();

  // Что нарисовано сейчас: габариты плашек, тела свечей в пикселях и подписи.
  function probe() {
    log.length = 0;
    L.redraw();
    const at = (t) => candles.find((c) => c.time === t);
    const hits = L.hits()
      .filter((h) => at(Number(String(h.key).slice(1))))
      .sort((a, b) => Number(String(a.key).slice(1)) - Number(String(b.key).slice(1)));
    if (!hits.length) return null;
    const res = { hits: hits, slot: Number(L.slotPx()) || 0, bodies: [], heights: [],
                  ratios: [], inside: [], wFrac: [], texts: [] };
    hits.forEach((h) => {
      const c = at(Number(String(h.key).slice(1)));
      const yO = L.priceToY(c.open), yC = L.priceToY(c.close);
      const body = Math.abs(yC - yO);
      const top = Math.min(yO, yC), bot = Math.max(yO, yC);
      res.bodies.push(Math.round(body));
      res.heights.push(h.h);
      res.ratios.push(h.h / body);
      res.inside.push(h.y >= top - 1 && h.y + h.h <= bot + 1);
      res.wFrac.push(h.w / (res.slot || 1));
    });
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
