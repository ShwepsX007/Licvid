/* Standalone screener renders honest network/credential states without API secrets. */
const fs = require("fs");
const assert = require("assert");
const {JSDOM} = require("jsdom");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "../static/screener.html"), "utf8")
    .replace(/<script src="\/static\/screener\.js\?v=3" defer><\/script>/, "");
const script = fs.readFileSync(path.join(__dirname, "../static/screener.js"), "utf8");
const dom = new JSDOM(html, {url: "https://liqscope.online/screener", runScripts: "outside-only"});
const win = dom.window;
let sample = {enabled: false, events: [], native: {enabled: true, connected: false,
    state: "connecting", market_count: 0, subscriptions_acked: 0, trades_seen: 0, events: 0},
    poller: {phase: "no_key", keys_configured: 0, interval_sec: 3600,
    budget_cu: 10000000, reserved_cu: 0, supported: ["ETH", "BNB"],
    last_attempt: {}, last_success: {}, errors: {}}};
win.fetch = async () => ({ok: true, json: async () => sample});
win.eval(script);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
    await sleep(40);
    assert(win.document.getElementById("screener-state").textContent.includes("ключ Alchemy"));
    assert(win.document.getElementById("screener-chains").textContent.includes("HYPERLIQUID · native API"));
    sample.unavailable_reason = "Пакет cryptography не установлен";
    win.document.getElementById("whale-chain").dispatchEvent(new win.Event("change"));
    await sleep(40);
    assert(win.document.getElementById("screener-state").textContent.includes("cryptography"));
    sample = {enabled: true, native: {enabled: true, connected: true,
        state: "connected", market_count: 2, subscriptions_acked: 2,
        trades_seen: 10, events: 1},
        poller: {...sample.poller, phase: "error", keys_configured: 2,
        last_attempt: {ETH: 1780000000}, last_success: {ETH: 1780000000},
        cursors: {ETH: 321}, errors: {BNB: "HTTP 403"}}, events: [{chain: "ETH",
        hash: "0x"+"a".repeat(64), timestamp: 1780000000, symbol: "USDC", amount: 100000,
        usd: 100000, from: "<script>alert(1)</script>", to: "0x"+"1".repeat(40),
        direction: "inflow"}]};
    win.document.getElementById("whale-chain").dispatchEvent(new win.Event("change"));
    await sleep(40);
    assert(win.document.getElementById("screener-state").textContent.includes("ошибка"));
    assert(win.document.getElementById("screener-chains").textContent.includes("HTTP 403"));
    assert.equal(win.document.querySelectorAll("#screener-events .row").length, 1);
    assert.equal(win.document.querySelectorAll("#screener-events script").length, 0);
    assert(win.document.querySelector("#screener-events a").href.startsWith("https://etherscan.io/tx/"));
    sample.events.push({chain: "HYPERLIQUID", hash: "0x"+"b".repeat(64), timestamp: 1780000001,
        symbol: "BTC", amount: 1.5, usd: 100000, from: "0x"+"2".repeat(40),
        to: "0x"+"1".repeat(40), direction: "trade", side: "BUY"});
    win.document.getElementById("whale-chain").value = "ALL";
    win.document.getElementById("whale-chain").dispatchEvent(new win.Event("change"));
    await sleep(40);
    assert.equal(win.document.querySelectorAll("#screener-events .row").length, 2);
    const hlLink = Array.from(win.document.querySelectorAll("#screener-events a"))
        .find(a => a.textContent.includes("HYPERLIQUID"));
    assert(hlLink.href.startsWith("https://hypurrscan.io/tx/"));
    assert(win.document.getElementById("screener-events").textContent.includes("Сделка · BUY"));
    win.document.getElementById("whale-direction").value = "trade";
    win.document.getElementById("whale-direction").dispatchEvent(new win.Event("change"));
    assert.equal(win.document.querySelectorAll("#screener-events .row").length, 1);
    win.close();
    console.log("Standalone screener: native Hyperliquid / no-key / network-error / events / XSS OK");
})().catch(err => {win.close(); console.error(err); process.exitCode = 1;});
