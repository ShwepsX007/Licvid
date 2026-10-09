"use strict";
// NODE_PATH=node_modules node tests/embed_idle.js [http://127.0.0.1:8015]
// Один терминал и три дополнительных графика: ускоряем интервалы и считаем
// запросы каждого окна отдельно (никакой реальной сети из JS).
require("./_dom_env");
const assert = require("node:assert/strict");
const { JSDOM, VirtualConsole } = require("jsdom");
const base = process.argv[2] || "http://127.0.0.1:8015";
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const forbidden = /^\/api\/(?:terminal\/chat|chat\/support|visit\/ping|layers\/trial|auth\/me)/;

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

async function open(path, host, popped = false) {
  const requests = [];
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", err => {
    const text = String(err.message || err);
    if (!/Not implemented|Could not load|canvas/i.test(text)) errors.push(text);
  });
  const dom = await JSDOM.fromURL(base + path, {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      if (host) Object.defineProperty(win, popped ? "opener" : "parent",
        { configurable: true, value: host });
      canvas(win);
      if (host && host.state.authGateReady) win.localStorage.setItem("liqscope.profileEnabled", "1");
      win.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      win.WebSocket = class { addEventListener() {} close() {} send() {} };
      const interval = win.setInterval.bind(win);
      win.setInterval = (fn, ms, ...args) => interval(fn, ms >= 3000 ? 120 : ms, ...args);
      win.fetch = async url => {
        const path = String(url);
        requests.push(path);
        return { ok: true, status: 200, json: async () => {
          if (path.startsWith("/api/auth/me")) return { ok: true, user: null };
          if (path.startsWith("/api/layers/trial")) return { ok: true, guest: true, enabled: true,
            allowed: true, left_sec: 1795, limit_sec: 1800 };
          if (path.startsWith("/api/klines")) return { ok: true, candles: [] };
          return { ok: true, items: [], rows: [], events: [], candles: [] };
        } };
      };
      win.navigator.sendBeacon = url => { requests.push(String(url)); return true; };
    },
  });
  return { win: dom.window, requests, errors };
}

(async () => {
  const main = await open("/terminal");
  // Родительское окно проверяет права, а не каждый дочерний график.
  await pause(350);
  assert.equal(main.win.state.authGateReady, true, "parent auth gate not ready");
  assert(main.requests.some(x => x.startsWith("/api/auth/me")), "parent must load auth");
  const extras = await Promise.all([
    open("/terminal?embed=1&slot=extra1&symbol=ETH_USDT&tf=5", main.win),
    open("/terminal?embed=1&slot=extra2&symbol=SOL_USDT&tf=5", main.win),
    open("/terminal?embed=1&slot=extra3&symbol=BNB_USDT&tf=5", main.win),
    open("/terminal?mode=panel&symbol=XRP_USDT&tf=5", main.win),
    open("/terminal?embed=1&pop=1&slot=extra4&symbol=ADA_USDT&tf=5", main.win, true),
  ]);
  await pause(1200); // несколько ускоренных циклов чата, пробника, присутствия
  assert(main.requests.filter(x => x.startsWith("/api/layers/trial")).length > 1,
    "parent trial watch was not exercised");
  for (const [i, item] of extras.entries()) {
    assert.deepEqual(item.errors, [], `embed ${i} script errors`);
    assert.deepEqual(item.requests.filter(x => forbidden.test(x)), [], `embed ${i} background traffic`);
    assert(item.requests.some(x => x.startsWith("/api/klines")), `embed ${i} lost candles`);
    assert(item.requests.some(x => x.startsWith("/api/liq_clusters?symbol=")),
      `embed ${i} lost enabled profile clusters`);
    assert.equal(item.win.state.authGateReady, true, `embed ${i} did not inherit auth`);
    assert.equal(item.win.state.layersAllowed, main.win.state.layersAllowed);
    assert.equal(item.win.state.trialTimer, 0, `embed ${i} trial watcher`);
    console.log(`embed ${i}: klines=${item.requests.filter(x => x.startsWith("/api/klines")).length}, forbidden=0`);
    item.win.close();
  }
  // Проверка гонки: iframe стартует раньше, чем родитель получил auth/trial.
  // До сообщения он не имеет прав, а после принимает только сообщение того же origin.
  const pendingHost = { location: { origin: new URL(base).origin },
    state: { authGateReady: false }, postMessage() {} };
  const early = await open("/terminal?embed=1&slot=early&symbol=ETH_USDT&tf=5", pendingHost);
  await pause(100);
  assert.equal(early.win.state.authGateReady, false);
  const message = { source: "liqscope-dock", type: "ws-data", payload: {
    type: "auth-gate", ready: true, userLoggedIn: true,
    layersAllowed: true, layersBlocked: false, layersTrial: null,
  }};
  early.win.dispatchEvent(new early.win.MessageEvent("message", {
    origin: "https://untrusted.example", data: message }));
  assert.equal(early.win.state.authGateReady, false, "foreign origin changed rights");
  early.win.dispatchEvent(new early.win.MessageEvent("message", {
    origin: new URL(base).origin, data: message }));
  await pause(120);
  assert.equal(early.win.state.authGateReady, true, "delayed gate was not received");
  assert.equal(early.win.state.userLoggedIn, true);
  assert.deepEqual(early.requests.filter(x => forbidden.test(x)), []);
  assert(early.requests.some(x => x.startsWith("/api/klines")));
  early.win.close();

  // Если ответ от родителя опоздал за безопасный таймаут, права закрыты,
  // но после сообщения тумблеры восстанавливают префы без второго boot.
  const late = await open("/terminal?embed=1&slot=late&symbol=ETH_USDT&tf=5", pendingHost);
  late.win.localStorage.setItem("liqscope.profileEnabled", "1");
  await pause(1680);
  assert.equal(late.win.state.layersBlocked, true);
  assert.equal(late.win.state.profileEnabled, false);
  late.win.dispatchEvent(new late.win.MessageEvent("message", {
    origin: new URL(base).origin, data: message }));
  await pause(130);
  assert.equal(late.win.state.layersBlocked, false);
  assert.equal(late.win.state.profileEnabled, true, "late gate must restore indicator");
  assert(late.requests.some(x => x.startsWith("/api/liq_clusters?symbol=ETH_USDT")));
  assert.deepEqual(late.requests.filter(x => forbidden.test(x)), []);
  late.win.dispatchEvent(new late.win.MessageEvent("message", {
    origin: new URL(base).origin,
    data: { source: "liqscope-dock", type: "ws-data", payload: {
      type: "auth-gate", ready: true, layersAllowed: false, layersBlocked: true,
    } },
  }));
  assert.equal(late.win.state.profileEnabled, false);
  assert.equal(late.win.localStorage.getItem("liqscope.profileEnabled"), "1",
    "child must not erase shared preferences on revoke");
  late.win.close();

  const published = [];
  const dock = main.win.LiqScopeDock;
  assert(dock, "parent dock did not start");
  const send = dock._bridgeSendAll.bind(dock);
  dock._bridgeSendAll = payload => { published.push(payload); send(payload); };
  main.win.LiqScopeLayersGate.expire();
  assert(published.some(p => p.type === "auth-gate" && p.layersBlocked),
    "parent did not broadcast a rights change to embeds");
  main.win.close();
  console.log("OK: parent + 5 embeds (dock, panel, pop-out) + early/late gates");
})().catch(err => { console.error(err); process.exit(1); });
