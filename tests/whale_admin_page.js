/* Whale admin dashboard: provider keys, mode, CU display and manual wallet CRUD. */
const fs = require("fs");
const assert = require("assert");
const {JSDOM} = require("jsdom");
const path = require("path");
const script = fs.readFileSync(path.join(__dirname, "../static/whale_admin.js"), "utf8");
const html = `<!doctype html><body>
<div id="whale-admin-card" hidden>
  <span id="whale-admin-status"></span><span id="whale-admin-key-status"></span>
  <input id="whale-admin-key"><button id="whale-admin-add-key"></button>
  <div id="whale-admin-summary"></div><div id="whale-admin-keys"></div>
  <div id="whale-admin-cu-label"></div><div id="whale-admin-cu-meta"></div>
  <div id="whale-admin-cu-progress"></div>
  <input id="trongrid-admin-key"><button id="trongrid-admin-add-key"></button>
  <span id="trongrid-admin-key-status"></span><div id="trongrid-admin-keys"></div>
  <div id="whale-admin-chains"></div>
  <select id="whale-admin-mode"><option value="realtime">Realtime</option><option value="economy">Economy</option></select>
  <select id="whale-admin-interval"><option value="5">5</option><option value="10">10</option><option value="15">15</option><option value="30">30</option><option value="60">60</option></select>
  <input id="whale-admin-budget"><button id="whale-admin-save"></button>
  <select id="whale-admin-wallet-chain"><option value="ALL">All</option><option value="ETH">Ethereum</option><option value="BASE">Base</option></select>
  <input id="whale-admin-wallet-search"><span id="whale-admin-wallet-summary"></span>
  <button id="whale-admin-wallet-refresh"></button><span id="whale-admin-wallet-status"></span>
  <input id="whale-admin-wallet-name"><input id="whale-admin-wallet-address">
  <select id="whale-admin-wallet-add-chain"><option value="ETH">Ethereum</option><option value="BASE">Base</option></select>
  <button id="whale-admin-wallet-add"></button><button id="whale-admin-wallet-cancel" hidden></button>
  <table><tbody id="whale-admin-wallet-rows"></tbody></table>
</div></body>`;
const dom = new JSDOM(html, {url: "https://liqscope.online/admin", runScripts: "outside-only", pretendToBeVisual: true});
const win = dom.window;
const calls = [];
let config = {mode: "realtime", history_interval_min: 15, interval_sec: 900,
    budget_cu: 10000000, phase: "ok"};
let walletRows = [
    {id: "aaaaaaaaaaaaaaaa", chain: "ETH", address: "0x" + "1".repeat(40), name: "Manual CEX", source: "manual"},
    {id: "bbbbbbbbbbbbbbbb", chain: "BASE", address: "0x" + "2".repeat(40), name: "Auto CEX", source: "defillama"},
];
let tronKeys = [];
let refreshFail = true;
const now = Math.floor(Date.now() / 1000);
const summary = () => ({total: walletRows.length,
    by_chain: walletRows.reduce((out, row) => (out[row.chain] = (out[row.chain] || 0) + 1, out), {}),
    updated_at: now});
const response = value => ({ok: true, json: async () => value});
win.LiqScopeI18n = {t: (key, vars = {}) => {
    let value = ({
        "screener.network_eth": "Ethereum", "screener.network_base": "Base",
        "screener.network_solana": "Solana", "screener.network_tron": "TRON",
        "screener.network_hyperliquid": "Hyperliquid Core",
        "adm.cex_wallet_manual": "Manual", "adm.cex_wallet_defillama": "DeFiLlama",
        "adm.cex_wallet_edit": "Edit", "adm.cex_wallet_save_changes": "Save changes",
        "adm.cex_wallet_add": "Add manual address", "adm.alchemy_delete": "Delete",
        "adm.cex_wallet_confirm_delete": "Delete {name}: {address}?",
        "adm.cex_wallet_updated": "Wallet updated", "adm.cex_wallet_added": "Wallet added",
        "adm.cex_wallet_refreshed": "Refreshed {count}", "adm.trongrid_saved": "TronGrid key saved",
        "adm.trongrid_no_key": "No TronGrid key", "adm.alchemy_settings_saved": "Settings saved",
        "adm.alchemy_monthly_estimate": "Monthly CU estimate: {amount}",
        "adm.alchemy_online": "Online",
        "adm.alchemy_online_waiting_cex_filters": "Online (waiting for CEX filters)",
        "adm.alchemy_rate_limited": "Rate limit (429) — pause {seconds}s, key active",
        "adm.cex_wallet_refresh_error_detail": "Refresh failed: {reason}",
    })[key] || key;
    Object.keys(vars).forEach(name => { value = value.replaceAll("{" + name + "}", String(vars[name])); });
    return value;
}};
win.confirm = () => true;
win.fetch = async (url, options = {}) => {
    const parsed = new URL(url, "https://liqscope.online");
    const method = String(options.method || "GET").toUpperCase();
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({path: parsed.pathname, method, body});
    if (parsed.pathname === "/api/auth/me") return response({user: {is_admin: true}});
    if (parsed.pathname === "/api/admin/screener/config" && method === "GET") return response({config});
    if (parsed.pathname === "/api/admin/screener/config" && method === "POST") {
        config = {...config, mode: body.mode, history_interval_min: body.interval_min,
            interval_sec: body.interval_min * 60, budget_cu: body.monthly_cu};
        return response({ok: true, config});
    }
    if (parsed.pathname === "/api/admin/alchemy/stats") return response({
        cu: {used: 2500, limit: 10000000, month: "2026-09", estimated_monthly: 7500,
            key_share: 10000000, keys: []},
        networks: ["ETH", "BASE", "SOLANA", "TRON", "HYPERLIQUID"].map(chain =>
            ({chain, status: chain === "ETH" ? "online" : chain === "BASE" ? "rate_limited"
                : chain === "SOLANA" ? "online" : "paused",
              warning_status: chain === "ETH" ? "rate_limited" : "",
              retry_in_sec: chain === "ETH" || chain === "BASE" ? 15 : 0,
              http_status: chain === "ETH" ? 429 : chain === "BASE" ? 200 : null,
              rpc_code: chain === "BASE" ? 429 : null,
              key_active: true,
              waiting_for_filters: chain === "SOLANA", last_success: 0,
              error: chain === "ETH" ? "HTTP 429: rate limit exceeded"
                : chain === "BASE" ? "JSON-RPC code=429: Too many requests" : ""}))});
    if (parsed.pathname === "/api/admin/alchemy/keys") return response({available: true, keys: []});
    if (parsed.pathname === "/api/admin/trongrid/key" && method === "GET") {
        return response({available: true, keys: tronKeys});
    }
    if (parsed.pathname === "/api/admin/trongrid/key" && method === "POST") {
        const row = {id: "cccccccccccccccc", hint: "tron••••test", source: "admin"};
        tronKeys = [row];
        assert(!JSON.stringify(row).includes(body.key), "TronGrid secret is not returned");
        return response({ok: true, key: row});
    }
    if (parsed.pathname === "/api/admin/screener/cex-wallets" && method === "GET") {
        const chain = parsed.searchParams.get("chain") || "ALL";
        const exchange = (parsed.searchParams.get("exchange") || "").toLowerCase();
        const rows = walletRows.filter(row => (chain === "ALL" || row.chain === chain) &&
            (!exchange || row.name.toLowerCase().includes(exchange)));
        return response({wallets: rows, summary: summary()});
    }
    if (parsed.pathname === "/api/admin/screener/cex-wallets" && method === "POST") {
        const row = {id: "dddddddddddddddd", ...body, source: "manual"};
        walletRows.push(row);
        return response({ok: true, wallet: row, summary: summary()});
    }
    if (parsed.pathname === "/api/admin/screener/cex-wallets/refresh" && method === "POST") {
        if (refreshFail) return {ok: false, json: async () => ({
            error: "refresh_failed",
            reason: "HTTP error; URL=https://api.llama.fi/cexs; HTTP 404; body=Not Found",
        })};
        return response({ok: true, after: walletRows.length});
    }
    if (parsed.pathname.startsWith("/api/admin/screener/cex-wallets/") && method === "PUT") {
        const id = parsed.pathname.split("/").pop();
        walletRows = walletRows.map(row => row.id === id ? {id: "eeeeeeeeeeeeeeee", ...body, source: "manual"} : row);
        return response({ok: true, wallet: walletRows.find(row => row.name === body.name), summary: summary()});
    }
    if (parsed.pathname.startsWith("/api/admin/screener/cex-wallets/") && method === "DELETE") {
        const id = parsed.pathname.split("/").pop();
        walletRows = walletRows.filter(row => row.id !== id);
        return response({ok: true, summary: summary()});
    }
    throw new Error("unexpected request: " + method + " " + parsed.href);
};
win.eval(script);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
    await sleep(100);
    assert.equal(win.document.getElementById("whale-admin-card").hidden, false);
    assert.equal(win.document.getElementById("whale-admin-mode").value, "realtime");
    assert.equal(win.document.getElementById("whale-admin-interval").value, "15");
    assert(/7[,\.]?500/.test(win.document.getElementById("whale-admin-cu-meta").textContent));
    const networkText = win.document.getElementById("whale-admin-chains").textContent;
    assert(networkText.includes("Solana") && networkText.includes("TRON"));
    assert(networkText.includes("Online (waiting for CEX filters)"));
    assert(networkText.includes("Rate limit (429) — pause 15s, key active"));
    assert(networkText.includes("HTTP 429: rate limit exceeded"));
    assert(networkText.includes("JSON-RPC code=429: Too many requests"));
    assert(!networkText.includes("All keys exhausted"));
    assert.equal(win.document.querySelectorAll("#whale-admin-wallet-rows tr").length, 2);

    const firstRow = win.document.querySelector("#whale-admin-wallet-rows tr");
    firstRow.querySelector("button").click();
    assert.equal(win.document.getElementById("whale-admin-wallet-name").value, "Manual CEX");
    assert.equal(win.document.getElementById("whale-admin-wallet-cancel").hidden, false);
    win.document.getElementById("whale-admin-wallet-name").value = "Renamed CEX";
    win.document.getElementById("whale-admin-wallet-address").value = "0x" + "3".repeat(40);
    win.document.getElementById("whale-admin-wallet-add-chain").value = "BASE";
    win.document.getElementById("whale-admin-wallet-add").click();
    await sleep(50);
    assert(calls.some(call => call.method === "PUT" && call.path.endsWith("/aaaaaaaaaaaaaaaa")));
    assert(walletRows.some(row => row.name === "Renamed CEX" && row.chain === "BASE"));

    const manualRow = Array.from(win.document.querySelectorAll("#whale-admin-wallet-rows tr"))
        .find(row => row.textContent.includes("Renamed CEX"));
    manualRow.querySelectorAll("button")[1].click();
    await sleep(40);
    assert(!walletRows.some(row => row.name === "Renamed CEX"));
    assert(calls.some(call => call.method === "DELETE" && call.path.endsWith("/eeeeeeeeeeeeeeee")));

    const walletStatus = win.document.getElementById("whale-admin-wallet-status");
    win.document.getElementById("whale-admin-wallet-refresh").click();
    await sleep(40);
    assert(walletStatus.textContent.includes("https://api.llama.fi/cexs"));
    assert(walletStatus.textContent.includes("HTTP 404"));
    assert(walletStatus.textContent.includes("Not Found"));
    assert(calls.some(call => call.path.endsWith("/refresh") && call.method === "POST"));
    refreshFail = false;
    win.document.getElementById("whale-admin-wallet-refresh").click();
    await sleep(40);
    assert(walletStatus.textContent.includes("Refreshed"));
    win.document.getElementById("trongrid-admin-key").value = "tron-secret-value";
    win.document.getElementById("trongrid-admin-add-key").click();
    await sleep(50);
    assert(calls.some(call => call.path === "/api/admin/trongrid/key" && call.method === "POST"));
    assert.equal(win.document.getElementById("trongrid-admin-key").value, "");
    assert(!win.document.getElementById("whale-admin-card").textContent.includes("tron-secret-value"));

    win.document.getElementById("whale-admin-mode").value = "economy";
    win.document.getElementById("whale-admin-interval").value = "30";
    win.document.getElementById("whale-admin-save").click();
    await sleep(60);
    assert(calls.some(call => call.path === "/api/admin/screener/config" &&
        call.method === "POST" && call.body.mode === "economy" && call.body.interval_min === 30));
    win.close();
    console.log("Whale admin: CU/mode, Solana/TRON health, TronGrid key and CEX wallet CRUD OK");
})().catch(err => {win.close(); console.error(err); process.exitCode = 1;});
