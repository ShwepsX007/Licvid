require("./_dom_env");
/** Offline browser feed integration, against a local demo server, no Alchemy key. */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");
const assert = require("assert");
const base = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
const row = {
  chain: "ETH", hash: "0x" + "a".repeat(64), log_index: "0x0",
  timestamp: Math.floor(Date.now() / 1000), symbol: "USDT", amount: 150000,
  usd: 150000, from: "0x" + "2".repeat(40), to: "0x" + "1".repeat(40),
  from_label: "", to_label: "Exchange <unsafe>", direction: "inflow",
};
const pending = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => {
    const msg = String(e.message || e);
    if (!msg.includes("fonts.googleapis.com")) errors.push(msg);
  });
  const dom = await JSDOM.fromURL(ru(base + "/terminal"), {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
      win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      const noop = () => {};
      win.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get(_t, p) {
          if (p === "measureText") return () => ({ width: 10 });
          if (p === "createLinearGradient" || p === "createRadialGradient") return () => ({ addColorStop: noop });
          if (p === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
          return noop;
        },
      });
      win.WebSocket = function () {
        const sock = { readyState: 1, send() {}, close() {} };
        pending.push(sock);
        setTimeout(() => {
          sock.onopen && sock.onopen();
          sock.onmessage && sock.onmessage({ data: JSON.stringify({
            type: "init", symbols: ["ETH_USDT"], custom_symbols: [],
            details: [], prices: { ETH_USDT: 2500 }, recent_liquidations: [],
            exchanges: [], stats: {},
          }) });
        }, 50);
        return sock;
      };
      win.WebSocket.OPEN = 1;
      win.fetch = async url => {
        const u = String(url);
        let data = {};
        if (u.includes("/api/screener/whales")) data = { enabled: true, events: [row] };
        else if (u.includes("/api/klines")) data = { symbol: "ETH_USDT", timeframe: 5, candles: [] };
        else if (u.includes("/api/liquidations")) data = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => data };
      };
    },
  });
  try {
    await sleep(1800);
    const win = dom.window, doc = win.document;
    const click = (id) => doc.getElementById(id).dispatchEvent(
      new win.MouseEvent("click", { bubbles: true }));
    click("feed-tab-whale");
    await sleep(120);
    assert.strictEqual(doc.getElementById("whale-panel").hidden, false);
    assert.strictEqual(doc.querySelectorAll(".whale-card").length, 1);
    assert.strictEqual(doc.querySelector(".whale-card a").href, "https://etherscan.io/tx/" + row.hash);
    assert.strictEqual(doc.querySelector(".whale-card .whale-inflow").textContent, "⬇ inflow");
    assert.strictEqual(doc.querySelector(".whale-card .whale-address").querySelector("unsafe"), null);
    // One live WS frame, no second browser socket and no duplicate after history fetch.
    pending[0].onmessage({ data: JSON.stringify({ type: "whale_tx", ...row }) });
    assert.strictEqual(doc.querySelectorAll(".whale-card").length, 1);
    const next = { ...row, hash: "0x" + "b".repeat(64), usd: 75000, amount: 75000,
                   chain: "BNB", direction: "outflow", to_label: "", from_label: "Exchange" };
    pending[0].onmessage({ data: JSON.stringify({ type: "whale_tx", ...next }) });
    assert.strictEqual(doc.querySelectorAll(".whale-card").length, 1); // default $100K
    doc.getElementById("whale-min").value = "50000";
    doc.getElementById("whale-min").dispatchEvent(new win.Event("change"));
    await sleep(120);
    assert.strictEqual(doc.querySelectorAll(".whale-card").length, 2);
    doc.getElementById("whale-chain").value = "BNB";
    doc.getElementById("whale-chain").dispatchEvent(new win.Event("change"));
    await sleep(120);
    assert.strictEqual(doc.querySelectorAll(".whale-card").length, 1);
    assert(doc.querySelector(".whale-card a").href.startsWith("https://bscscan.com/tx/"));
    doc.getElementById("whale-direction").value = "inflow";
    doc.getElementById("whale-direction").dispatchEvent(new win.Event("change"));
    assert.strictEqual(doc.querySelectorAll(".whale-card").length, 0);
    click("feed-tab-liq");
    assert.strictEqual(doc.getElementById("whale-panel").hidden, true);
    assert.strictEqual(pending.length, 1);
    assert.deepStrictEqual(errors, []);
    console.log("Whale UI: REST, WS, threshold, network, direction, explorer, XSS and tab OK");
  } finally {
    dom.window.close();
  }
}
main().catch(e => { console.error(e); process.exitCode = 1; });
