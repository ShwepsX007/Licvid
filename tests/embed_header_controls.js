"use strict";
// NODE_PATH=node_modules node tests/embed_header_controls.js [http://127.0.0.1:8000]
// Независимые графики: управление живёт в заголовке каждого графика.
//  1) встроенный график (iframe-эмуляция): селектор таймфрейма и выпадашка
//     монеты в шапке, смена шлёт родителю liqscope-iframe/update_state;
//  2) родитель (док): сообщение мгновенно обновляет слот, вкладку и расклад.
require("./_dom_env");
const assert = require("node:assert/strict");
const { JSDOM, VirtualConsole, requestInterceptor } = require("jsdom");

// Вложенные iframe (embed=1) в тесте не исполняем: у их окон нет стабов,
// а доку достаточно пустого документа слота.
const frameStub = requestInterceptor(async (request, { element }) => {
  if (element && element.localName === "iframe" && /[?&](embed=1|mode=panel)/.test(request.url)) {
    return new Response("<!doctype html><html><body></body></html>", {
      headers: { "Content-Type": "text/html" },
    });
  }
  return undefined;   // остальное — как обычно
});
const base = process.argv[2] || "http://127.0.0.1:8000";
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function canvas(win) {
  const noop = () => {};
  const ctx = new Proxy({}, { get(_t, key) {
    if (key === "canvas") return { width: 900, height: 500 };
    if (key === "measureText") return () => ({ width: 10 });
    if (key === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
    if (key === "createLinearGradient" || key === "createRadialGradient")
      return () => ({ addColorStop: noop });
    return noop;
  }});
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
}

const SYMBOLS_PAYLOAD = {
  symbols: ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
  custom_symbols: [],
  details: [
    { symbol: "BTC_USDT", price: 65000, liq24h: 1e6, custom: false, exchanges: ["BINANCE"] },
    { symbol: "ETH_USDT", price: 3500, liq24h: 5e5, custom: false, exchanges: ["BINANCE"] },
    { symbol: "SOL_USDT", price: 150, liq24h: 2e5, custom: false, exchanges: ["BYBIT"] },
  ],
  prices: { BTC_USDT: 65000, ETH_USDT: 3500, SOL_USDT: 150 },
  exchanges: ["BINANCE", "BYBIT"],
  timeframes: [1, 3, 5, 15, 60, 240, 1440],
};

async function open(path, parentStub) {
  const requests = [];
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", err => {
    const text = String(err.message || err);
    if (!/Not implemented|Could not load|canvas|fetch is not defined/i.test(text)) errors.push(text);
  });
  const dom = await JSDOM.fromURL(base + path, {
    runScripts: "dangerously", resources: { interceptors: [frameStub] }, pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      if (parentStub) Object.defineProperty(win, "parent", { configurable: true, value: parentStub });
      canvas(win);
      win.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {}, removeEventListener() {} });
      win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      win.WebSocket = class { addEventListener() {} close() {} send() {} };
      const interval = win.setInterval.bind(win);
      win.setInterval = (fn, ms, ...args) => interval(fn, ms >= 3000 ? 120 : ms, ...args);
      win.fetch = async url => {
        const p = String(url);
        requests.push(p);
        return { ok: true, status: 200, json: async () => {
          if (p.includes("/api/auth/me")) return { ok: true, user: null };
          if (p.includes("/api/layers/trial")) return { ok: true, guest: true, enabled: true,
            allowed: true, left_sec: 1795, limit_sec: 1800 };
          if (p.includes("/api/symbols/search")) return { ok: true, results: [] };
          if (p.includes("/api/symbols")) return SYMBOLS_PAYLOAD;
          if (p.includes("/api/klines")) return { ok: true, candles: [] };
          return { ok: true, items: [], rows: [], events: [], candles: [], results: [] };
        } };
      };
      win.navigator.sendBeacon = url => { requests.push(String(url)); return true; };
    },
  });
  return { win: dom.window, requests, errors };
}

async function waitFor(fn, what, timeoutMs = 8000) {
  const t0 = Date.now();
  for (;;) {
    try { const v = fn(); if (v) return v; } catch (e) { /* ещё не готово */ }
    if (Date.now() - t0 > timeoutMs) throw new Error("timeout waiting: " + what);
    await pause(50);
  }
}

(async () => {
  // --- Часть 1+2: встроенный график (как внутри дока) ----------------------
  const sent = [];
  const fakeParent = { postMessage: (data, origin) => sent.push({ data, origin }) };
  const child = await open("/terminal?embed=1&slot=c_test1&symbol=BTC_USDT&tf=5", fakeParent);
  const cw = child.win;
  await waitFor(() => cw.state && cw.state.authGateReady !== undefined && cw.document.body.classList.contains("chart-embed"), "embed init");
  assert(cw.document.body.classList.contains("embed-mode"), "body.embed-mode не поставлен");

  // панель управления в шапке: селектор таймфрейма
  const tfSel = cw.document.getElementById("chart-header-tf");
  assert(tfSel, "в заголовке графика нет селектора таймфрейма");
  assert.deepEqual(Array.from(tfSel.options).map(o => o.value),
    ["1", "3", "5", "15", "60", "240", "1440"], "набор ТФ селектора");
  assert.equal(tfSel.value, "5", "ТФ взят из URL слота");

  // смена ТФ через заголовок: только этот график + сигнал родителю
  assert(!cw.document.getElementById("tf-buttons"), "глобальная панель #tf-buttons удалена");
  tfSel.value = "60";
  tfSel.dispatchEvent(new cw.Event("change", { bubbles: true }));
  assert.equal(cw.state.timeframe, 60, "ТФ графика сменился");
  assert.equal(tfSel.value, "60", "селектор ТФ в заголовке в синхроне");
  await waitFor(() => sent.some(m => m.data && m.data.source === "liqscope-iframe" && m.data.action === "update_state" && m.data.tf === 60), "update_state");
  const upd = sent.find(m => m.data.source === "liqscope-iframe" && m.data.action === "update_state" && m.data.tf === 60);
  assert.equal(upd.data.slotId, "c_test1", "slotId в update_state");
  assert.equal(upd.data.symbol, "BTC_USDT", "symbol в update_state");
  assert.equal(upd.origin, base, "postMessage только в свой origin");
  assert(sent.some(m => m.data.source === "liqscope-dock" && m.data.type === "embed-sub" && m.data.tf === 60),
    "старый формат embed-sub тоже послан (совместимость)");
  console.log("ok   заголовок iframe: селектор ТФ меняет свой график и шлёт update_state");

  // синхронизация фильтра объёма в iframe по postMessage от дока
  cw.dispatchEvent(new cw.MessageEvent("message", {
    origin: base,
    data: { source: "liqscope-dock", action: "update_volume_filter", minVolume: 25000 },
  }));
  assert.equal(cw.state.minUsd, 25000, "iframe принял minVolume=25000 от дока");

  // кнопка «Обновить» (↻) в шапке графика перезагружает свечи только этого графика
  const refreshBtn = cw.document.getElementById("chart-refresh");
  assert(refreshBtn && refreshBtn.textContent.includes("↻"), "кнопка ↻ в шапке графика");
  const klineReqsBefore = child.requests.filter(r => r.includes("/api/klines")).length;
  refreshBtn.click();
  await waitFor(() => child.requests.filter(r => r.includes("/api/klines")).length > klineReqsBefore, "refresh klines");
  console.log("ok   шапка iframe: ↻ перезагружает свечи и приёмник update_volume_filter работает");

  // меню монеты в заголовке: список добирается по REST и открывается кликом
  await waitFor(() => child.requests.some(r => r.includes("/api/symbols")), "rest symbols");
  await waitFor(() => cw.document.querySelectorAll("#symbol-dropdown .symbol-option").length >= 3, "symbol list");
  const wrap = cw.document.getElementById("symbol-dropdown-wrap");
  const panel = cw.document.getElementById("symbol-panel");
  assert(panel.classList.contains("hidden"), "панель монеты закрыта до клика");
  cw.document.getElementById("symbol-current").click();
  assert(!panel.classList.contains("hidden"), "клик по монете открыл панель выбора");
  console.log("ok   заголовок iframe: клик по монете открыл меню с поиском");

  // выбор другой монеты — локальный, с мгновенным update_state родителю
  sent.length = 0;
  const ethRow = Array.from(cw.document.querySelectorAll("#symbol-dropdown .symbol-option"))
    .find(el => el.dataset.symbol === "ETH_USDT");
  assert(ethRow, "в списке есть ETH");
  ethRow.click();
  assert.equal(cw.state.symbol, "ETH_USDT", "монета графика сменилась");
  await waitFor(() => sent.some(m => m.data.source === "liqscope-iframe" && m.data.action === "update_state" && m.data.symbol === "ETH_USDT"), "symbol update_state");
  console.log("ok   заголовок iframe: смена монеты шлёт update_state");
  cw.close();

  // --- Часть 3: родитель (док) мгновенно обновляет вкладки ------------------
  const main = await open("/terminal", null);
  const w = main.win;
  await waitFor(() => w.LiqScopeDock && w.state && w.state.authGateReady, "dock boot");
  const dock = w.LiqScopeDock;

  // Проверка ЧАСТЬ 1: навигация в верхней шапке сразу после блока с кнопкой «Чат», панель контролов — в доке
  const topNav = w.document.querySelector("header.top-nav");
  const brand = topNav.querySelector(".brand");
  const hdrControls = topNav.querySelector(".header-controls");
  assert(brand && hdrControls && brand.nextElementSibling === hdrControls,
    "меню навигации в header.top-nav сразу после блока с кнопкой Чат");
  assert(w.document.querySelector(".chart-dock-tools .controls-bar"),
    "панель управления (Очистить, Сигнал, Мин. объем, Биржа) перенесена в панель дока");

  w.document.getElementById("add-chart-btn").click();
  await waitFor(() => dock.order.length === 2, "slot added");
  const slotId = dock.order.find(id => id !== "native");
  assert(slotId && /^c_/.test(slotId), "док создал встроенный слот");
  const frame = dock.els.get(slotId).querySelector("iframe");
  assert(frame && frame.src.includes("embed=1") && frame.src.includes("slot=" + slotId), "iframe слота");
  await pause(400);        // load заглушки слота уже отработал — дальше только наши сообщения

  // Проверка ЧАСТЬ 2: изменение фильтра объёма в панели дока рассылает postMessage во все iframe
  const postedToIframe = [];
  if (frame.contentWindow) {
    frame.contentWindow.postMessage = (msg, origin) => postedToIframe.push({ msg, origin });
  }
  const preset100k = w.document.querySelector('#min-usd-presets button[data-v="100000"]');
  assert(preset100k, "пресет $100K в фильтре объёма");
  preset100k.click();
  assert.equal(w.state.minUsd, 100000, "основной график применил порог $100K");
  assert(postedToIframe.some(p => p.msg && p.msg.source === "liqscope-dock" && p.msg.action === "update_volume_filter" && p.msg.minVolume === 100000),
    "док разослал update_volume_filter всем открытым iframe");

  // Проверка ЧАСТЬ 4: Fullscreen в режиме «Сетка» не ломает раскладку 50/50 при выходе
  dock.setView("grid");
  dock.toggleSlotFullscreen("native");
  const nativeSlot = dock.els.get("native");
  assert(nativeSlot.classList.contains("is-fullscreen"), "слот получил .is-fullscreen");
  dock.exitSlotFullscreen();
  assert(!nativeSlot.classList.contains("is-fullscreen") && !nativeSlot.classList.contains("is-slot-fs"),
    "классы fullscreen сняты после выхода");
  assert(nativeSlot.parentElement && nativeSlot.parentElement.classList.contains("chart-split-cell"),
    "слот остался в своей ячейке .chart-split-cell (соседи не зажимаются)");
  assert.equal(nativeSlot.style.width, "", "inline width очищен после выхода из Fullscreen");
  assert.equal(nativeSlot.style.height, "", "inline height очищен после выхода из Fullscreen");

  dock.setView("tabs");   // режим вкладок — подпись на кнопке-вкладке
  // эмуляция сообщения из iframe (символ+ТФ сменили в его заголовке).
  // В браузере это postMessage; jsdom не ставит origin — задаём явно.
  w.dispatchEvent(new w.MessageEvent("message", {
    origin: base,
    data: { source: "liqscope-iframe", action: "update_state", slotId, symbol: "ETH_USDT", tf: 60 },
  }));
  await waitFor(() => dock.meta[slotId].symbol === "ETH_USDT" && dock.meta[slotId].tf === 60, "parent meta");
  const tabLabel = w.document.querySelector('.chart-dock-tab[data-id="' + slotId + '"] .chart-dock-tab-label');
  assert(tabLabel, "кнопка-вкладка есть");
  assert.equal(tabLabel.textContent, "ETH/USDT · 1ч", "подпись вкладки обновилась мгновенно");
  const slotTitle = dock.els.get(slotId).querySelector(".chart-slot-title");
  assert.equal(slotTitle.textContent, "ETH/USDT · 1ч", "заголовок окна слота обновился");
  const tfCtl = dock.els.get(slotId).querySelector(".chart-slot-tf");
  assert.equal(tfCtl.value, "60", "ТФ-селектор слота в синхроне");
  await pause(400);   // _save() с дебаунсом ~180мс
  const saved = JSON.parse(w.localStorage.getItem("liqscope_chart_dock_v1") || "{}");
  const rec = (saved.charts || []).find(c => c.id === slotId);
  assert(rec && rec.symbol === "ETH_USDT" && rec.tf === 60, "расклад сохранён с новыми параметрами");
  console.log("ok   док: update_state мгновенно обновил слот, вкладку и сохранил расклад");
  w.close();

  console.log("ИТОГ: 6 ок, 0 провал(ов)");
})().catch(e => { console.error("Тест не смог запуститься:", e); process.exit(1); });
