/**
 * Задача 4: слой китов на графике и отбор ближних уровней.
 *
 * 1) Киты — стрелки на clusterCanvas (не метки серии): одна стрелка на
 *    свечу×направление с суммой группы; ховер открывает окно-пояснение
 *    с общим объёмом, направлением и списком событий.
 * 2) drawLiqLevels: топ-90 по массе + всегда ближайшие 3 сверху и 3 снизу
 *    от цены — ближний уровень не вытесняется дальними тяжёлыми.
 *
 * Нужен живой стенд: python -m uvicorn server:app --port 8000
 * Run: node tests/whale_canvas_layer.js
 */
"use strict";
require("./_dom_env");

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const ROOT = path.dirname(__dirname);

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

const TF = 300;
const NOW = Math.floor(Date.now() / 1000);
const CANDLES = [];
for (let i = 29; i >= 0; i--) {
  const t = NOW - (NOW % TF) - i * TF;
  // low/high разнесены широко: в заглушке цена→y сжата (0.05px/$), при
  // узких свечах стрелки «верх/низ» оказались бы в 17px друг от друга
  CANDLES.push({ time: t, open: 60000 + i, high: 61500 + i, low: 58500 + i,
                 close: 60050 + i, volume: 10 + i, cvd: 100 * i, oi: 5e8 + i });
}

// 90 тяжёлых (по $50M) далеко от цены + мелочь рядом: без правки топ-90
// съедал бы всё, и ближних уровней на графике не было бы
const LEVEL_ROWS = (() => {
  const rows = [];
  for (let i = 0; i < 90; i++) {
    rows.push({ price: 10000 + i * 500, usd: 50e6, side: i % 2 ? "long" : "short" });
  }
  [[60500, 4e5], [61200, 3e5], [62000, 2.5e5], [63000, 2e5]].forEach(([p, u]) =>
    rows.push({ price: p, usd: u, side: "short" }));
  [[59500, 3.5e5], [58800, 2.8e5], [58000, 2.6e5], [57000, 2.4e5]].forEach(([p, u]) =>
    rows.push({ price: p, usd: u, side: "long" }));
  return rows;
})();
const LEVELS_PAYLOAD = {
  ok: true, enabled: true, estimate: true, price: 60000,
  levels: LEVEL_ROWS,
  magnets_list: [{ price: 59500, kind: "down" }, { price: 60500, kind: "up" }],
  cumulative: [], coverage: {}, calibration: { applied: false },
};

function whaleRow(n, dir, usd, ts, side) {
  return {
    chain: "ETH",
    hash: "0x" + String(n).padStart(2, "0") +
      "b1d3c47e8f9012a4567c89d0ef1a2b3c4d5e6f708192a3b4c5d6e7f80912a3",
    log_index: n,
    symbol: "BTC",
    direction: dir,
    side: side || "",
    usd: usd,
    timestamp: ts,
    from: "0x1111111111111111111111111111111111111111",
    to: "0x2222222222222222222222222222222222222222",
  };
}

(async () => {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String(e.message || e);
    if (msg.includes("fonts.googleapis.com")) return;   // сеть песочницы закрыта
    errors.push(msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push(a.map(String).join(" ").slice(0, 200)));

  const t5 = CANDLES[5].time, t10 = CANDLES[10].time;
  let crossCb = null, clickCb = null;
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
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
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify({
            type: "init", symbols: ["BTC_USDT"], custom_symbols: [], details: [],
            prices: { BTC_USDT: 60050 }, recent_liquidations: [],
            exchanges: ["BINANCE"], stats: {},
          }) });
        }, 40);
        return sock;
      };
      win.WebSocket.OPEN = 1; win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.includes("/api/klines")) {
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles: CANDLES };
        } else if (u.includes("/api/liq_levels")) {
          body = LEVELS_PAYLOAD;
        } else if (u.includes("/api/liquidations")) {
          body = { liquidations: [], total: 0 };
        }
        return { ok: true, status: 200, json: async () => body };
      };
      const timeScale = {
        applyOptions() {}, options: () => ({ rightOffset: 6 }),
        getVisibleLogicalRange: () => ({ from: 0, to: 29 }),
        setVisibleLogicalRange() {},
        subscribeVisibleLogicalRangeChange() {},
        timeToCoordinate: (t) => {
          const i = CANDLES.findIndex((c) => Number(c.time) === Number(t));
          return i >= 0 ? 30 + i * 8 : null;
        },
        coordinateToTime: () => NOW,
        getVisibleRange: () => ({ from: NOW - 9000, to: NOW }),
        scrollToPosition() {}, fitContent() {},
      };
      const priceScale = {
        applyOptions() {}, options: () => ({ autoScale: true,
          scaleMargins: { top: 0.06, bottom: 0.24 } }),
        getVisibleRange: () => ({ from: 59500, to: 60500 }),
      };
      const series = () => ({
        setData() {}, update() {}, applyOptions() {},
        priceToCoordinate: (v) => 125 + (Number(v) - 60000) * 0.05,
        coordinateToPrice: (v) => 60000 + (Number(v) - 125) / 0.05,
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
          subscribeCrosshairMove(cb) { crossCb = cb; },
          unsubscribeCrosshairMove() {},
          subscribeClick(cb) { clickCb = cb; },
          unsubscribeClick() {},
        }),
        CandlestickSeries: {}, HistogramSeries: {}, LineSeries: {},
        CrosshairMode: { Normal: 0 },
      };
      try {
        Object.defineProperty(win, "LightweightCharts", {
          configurable: true,
          get() { return fakeCharts; },
          set() { /* своя библиотека страницы заглушку не затрёт */ },
        });
      } catch (e) { win.LightweightCharts = fakeCharts; }
      try {
        win.localStorage.setItem("liqscope.lang", "ru");
        win.localStorage.setItem("liqscope.devLayers", "1");
        win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT");
        win.localStorage.removeItem("liqscope.levelsEnabled");
        win.localStorage.setItem("liqscope.whaleLayerEnabled", "0");
      } catch (e) { /* ignore */ }
    },
  });
  await sleep(5000);
  const win = dom.window, doc = win.document;
  check("страница без js-ошибок", errors.length === 0, errors.slice(0, 3).join(" // "));

  /* ---------- часть 1: ближние уровни всегда в отборе ------------------- */
  const LD = win.LiqScopeLevelsDraw;
  check("тестовый API уровней доступен", !!LD && typeof LD.rows === "function");
  const btn = doc.getElementById("levels-toggle");
  check("кнопка слоя уровней есть", !!btn);
  if (btn) {
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await sleep(1500);
    const rows = (LD ? LD.rows() : []).map((r) => Number(r.price));
    check("расчёт отрисован (данные пришли)", rows.length > 0,
          "строк: " + rows.length);
    check("топ-90 тяжёлых на месте", rows.length >= 90, "строк: " + rows.length);
    check("ближний сверху (60500) в наборе", rows.includes(60500),
          rows.filter((p) => p > 60000).join(","));
    check("ближний снизу (59500) в наборе", rows.includes(59500),
          rows.filter((p) => p < 60000).join(","));
    check("вторые ближние тоже (61200/58800)",
          rows.includes(61200) && rows.includes(58800));
    check("третьи ближние тоже (62000/58000)",
          rows.includes(62000) && rows.includes(58000));
    check("четвёртые дальние не добавлены (63000/57000)",
          !rows.includes(63000) && !rows.includes(57000));
    check("итого 96 строк (90 тяжёлых + 6 ближних)", rows.length === 96,
          "строк: " + rows.length);
  }

  /* ---------- часть 2: киты — стрелки на canvas -------------------------- */
  const WL = win.LiqScopeWhaleLayer;
  check("тестовый API китов доступен",
        !!WL && typeof WL.set === "function" && typeof WL.hits === "function");
  if (WL) {
    WL.set(true);
    WL.feed([
      whaleRow(1, "inflow", 250000, t5 + 60),
      whaleRow(2, "outflow", 1200000, t5 + 120),
      whaleRow(3, "transfer", 400000, t5 + 180),
      whaleRow(4, "inflow", 150000, t10 + 30),
    ]);
    await sleep(400);
    const hits = WL.hits();
    check("стрелки нарисованы", hits.length === 3, "хитов: " + hits.length);
    const up5 = hits.find((h) => h.up && h.time === t5);
    const down5 = hits.find((h) => !h.up && h.time === t5);
    const up10 = hits.find((h) => h.up && h.time === t10);
    check("приток t5: стрелка вверх, $250k",
          !!up5 && up5.total === 250000 && up5.n === 1,
          JSON.stringify(up5));
    check("вывод+перевод t5: одна стрелка вниз, сумма $1.6M из двух событий",
          !!down5 && down5.total === 1600000 && down5.n === 2,
          JSON.stringify(down5));
    check("приток t10: своя стрелка, $150k",
          !!up10 && up10.total === 150000, JSON.stringify(up10));
    check("ключи вида whale_<время>_<направление>",
          hits.every((h) => /^whale_\d+_[ud]$/.test(h.key)),
          hits.map((h) => h.key).join(","));

    /* ---------- часть 3: ховер открывает окно-пояснение ------------------ */
    const modal = doc.getElementById("detail-modal");
    check("окно фигуры есть в разметке", !!modal);
    if (modal && down5 && crossCb) {
      crossCb({ point: { x: down5.x, y: down5.y } });
      await sleep(150);
      check("ховер по стрелке открывает окно",
            !modal.classList.contains("hidden"), modal.className);
      const txt = modal.textContent.replace(/\s+/g, " ").trim();
      const html = modal.innerHTML;
      check("заголовок — киты on-chain", /Киты on-chain/.test(txt), txt.slice(0, 80));
      check("направление группы — вывод с биржи", /Вывод с биржи/.test(txt));
      check("общий объём группы показан", /\$[\d\s\u00a0]{6,}/.test(txt), txt.slice(0, 200));
      check("число событий группы показано", /событ/.test(txt) && /2/.test(txt));
      check("события перечислены со скан-ссылкой",
            /etherscan\.io\/tx\/0x/.test(html), html.slice(0, 200));
      check("пояснение, что такое кит, есть", /кит/i.test(txt) && /приток/i.test(txt));
      crossCb({});
      await sleep(150);
      check("уход ховера закрывает окно (не закреплено)",
            modal.classList.contains("hidden"), modal.className);
    }
    WL.set(false);
  }

  /* ---------- часть 4: источник — стрелки, а не метки серии -------------- */
  const src = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
  check("метки серии больше не используются",
        !src.includes("whaleChartMarkers().forEach"));
  check("кругляшки-метки ушли из китового слоя",
        !/"circle"/.test(src.slice(src.indexOf("WHALE_STABLE_TOKENS"),
                                    src.indexOf("function paintWhales"))));
  check("сумма окрашивается по объёму (бледная → янтарная)",
        src.includes("whaleNumberStyle") &&
        src.includes("190 + (255 - 190) * t"));
  check("слой вызывается из drawClusters",
        src.includes("if (state.whaleLayerEnabled) drawWhaleMarks(ctx);"));

  console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.error("HARNESS CRASH:", e);
  process.exit(1);
});
