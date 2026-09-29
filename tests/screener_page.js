/* Standalone screener renders honest network/credential states without API secrets. */
const fs = require("fs");
const assert = require("assert");
const {JSDOM} = require("jsdom");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "../static/screener.html"), "utf8")
    .replace(/<script src="\/static\/screener\.js\?v=1" defer><\/script>/, "");
const script = fs.readFileSync(path.join(__dirname, "../static/screener.js"), "utf8");
const dom = new JSDOM(html, {url: "https://liqscope.online/screener", runScripts: "outside-only"});
const win = dom.window;
let sample = {enabled: false, events: [], poller: {phase: "no_key", keys_configured: 0,
    interval_sec: 3600, budget_cu: 10000000, reserved_cu: 0,
    supported: ["ETH", "BNB"], last_attempt: {}, last_success: {}, errors: {}}};
win.fetch = async () => ({ok: true, json: async () => sample});
win.eval(script);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
    await sleep(40);
    assert(win.document.getElementById("screener-state").textContent.includes("Нет ключа"));
    sample = {enabled: true, poller: {...sample.poller, phase: "error", keys_configured: 2,
        last_attempt: {ETH: 1780000000}, last_success: {ETH: 1780000000},
        cursors: {ETH: 321}, errors: {BNB: "HTTP 403"}}, events: [{chain: "ETH",
        hash: "0x"+"a".repeat(64), timestamp: 1780000000, symbol: "USDC", amount: 100000,
        usd: 100000, from: "<script>alert(1)</script>", to: "0x"+"1".repeat(40),
        direction: "inflow"}]};
    win.document.getElementById("whale-chain").dispatchEvent(new win.Event("change"));
    await sleep(40);
    assert(win.document.getElementById("screener-state").textContent.includes("Ошибка"));
    assert(win.document.getElementById("screener-chains").textContent.includes("HTTP 403"));
    assert.equal(win.document.querySelectorAll("#screener-events .row").length, 1);
    assert.equal(win.document.querySelectorAll("#screener-events script").length, 0);
    assert(win.document.querySelector("#screener-events a").href.startsWith("https://etherscan.io/tx/"));
    win.close();
    console.log("Standalone screener: no-key / network-error / events / XSS OK");
})().catch(err => {win.close(); console.error(err); process.exitCode = 1;});
