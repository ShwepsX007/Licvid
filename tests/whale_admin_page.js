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
  <select id="whale-admin-interval"><option value="30">30 sec</option><option value="60">60 sec</option><option value="120">2 min</option><option value="300">5 min</option></select>
  <input id="whale-admin-budget"><button id="whale-admin-save"></button>
  <select id="whale-admin-wallet-chain"><option value="ALL">All</option><option value="ETH">Ethereum</option><option value="BASE">Base</option></select>
  <input id="whale-admin-wallet-search"><span id="whale-admin-wallet-summary"></span>
  <button id="whale-admin-wallet-refresh"></button><span id="whale-admin-wallet-status"></span>
  <input id="whale-admin-wallet-name"><input id="whale-admin-wallet-address">
  <select id="whale-admin-wallet-add-chain"><option value="ETH">Ethereum</option><option value="BASE">Base</option></select>
  <button id="whale-admin-wallet-add"></button><button id="whale-admin-wallet-cancel" hidden></button>
  <table><tbody id="whale-admin-wallet-rows"></tbody></table>
  <p id="whale-admin-key-pool"></p>
  <div id="whale-admin-network-switches"></div><span id="whale-admin-network-status"></span>
  <span id="whale-admin-wallet-cleanup"></span>
  <input id="whale-admin-wallet-retention"><button id="whale-admin-wallet-retention-save"></button>
  <button id="whale-admin-wallet-prune"></button>
</div></body>`;
const dom = new JSDOM(html, {url: "https://liqscope.online/admin", runScripts: "outside-only", pretendToBeVisual: true});
const win = dom.window;
const calls = [];
let config = {mode: "realtime", poll_interval_sec: 120, interval_sec: 120,
    budget_cu: 10000000, phase: "ok"};
let walletRows = [
    {id: "aaaaaaaaaaaaaaaa", chain: "ETH", address: "0x" + "1".repeat(40), name: "Manual CEX", source: "manual"},
    {id: "bbbbbbbbbbbbbbbb", chain: "BASE", address: "0x" + "2".repeat(40), name: "Auto CEX", source: "defillama"},
];
let tronKeys = [];
let refreshFail = true;
let walletRetentionDays = 7;
let walletPruneRemoved = 5;
// состояние тумблеров «как на проде»: дорогие сети по умолчанию выключены,
// SOLANA здесь включена вручную — на ней проверяем карточку здоровья
let networkEnabled = {ETH: true, BNB: false, POLYGON: false, ARBITRUM: false, BASE: true,
    SOLANA: true, TRON: true, HYPERLIQUID: true};
const now = Math.floor(Date.now() / 1000);
const summary = () => ({total: walletRows.length,
    by_chain: walletRows.reduce((out, row) => (out[row.chain] = (out[row.chain] || 0) + 1, out), {}),
    updated_at: now});
const response = value => ({ok: true, json: async () => value});
// тумблеры берутся из того же состояния, что и POST: reload обязан показать новый флаг
const switchRows = () => ({networks: Object.keys(networkEnabled).map(id => ({
    id, title: id === "BASE" ? "Base" : id === "ETH" ? "Ethereum"
        : id === "ARBITRUM" ? "Arbitrum One" : id,
    provider: id === "TRON" ? "trongrid" : "alchemy", paid: id !== "TRON",
    enabled: Boolean(networkEnabled[id])})),
    enabled: Object.keys(networkEnabled).filter(id => networkEnabled[id]),
    wallet_retention_days: walletRetentionDays});
win.LiqScopeI18n = {t: (key, vars = {}) => {
    let value = ({
        "screener.network_eth": "Ethereum", "screener.network_base": "Base",
        "screener.network_arbitrum": "Arbitrum",
        "screener.network_solana": "Solana", "screener.network_tron": "TRON",
        "screener.network_hyperliquid": "Hyperliquid Core",
        "adm.cex_wallet_manual": "Manual", "adm.cex_wallet_defillama": "DeFiLlama",
        "adm.cex_wallet_edit": "Edit", "adm.cex_wallet_save_changes": "Save changes",
        "adm.cex_wallet_add": "Add manual address", "adm.alchemy_delete": "Delete",
        "adm.cex_wallet_confirm_delete": "Delete {name}: {address}?",
        "adm.cex_wallet_updated": "Wallet updated", "adm.cex_wallet_added": "Wallet added",
        "adm.cex_wallet_refreshed": "DeFiLlama API addresses {count}",
        "adm.cex_wallet_refreshed_etherscan": "Etherscan labels {count}",
        "adm.trongrid_saved": "TronGrid key saved",
        "adm.trongrid_no_key": "No TronGrid key", "adm.alchemy_settings_saved": "Settings saved",
        "adm.alchemy_monthly_estimate": "Monthly CU estimate: {amount}",
        "adm.alchemy_online": "Online",
        "adm.alchemy_online_waiting_cex_filters": "Online (waiting for CEX filters)",
        "adm.alchemy_rate_limited": "🟡 Rate limit (429) — waiting {seconds}s",
        "adm.cex_wallet_refresh_error_detail": "Refresh failed: {reason}",
        "adm.alchemy_no_keys": "No keys", "adm.alchemy_network_cu": "{amount} CU",
        "adm.alchemy_pool_active": "active {key}", "adm.alchemy_pool_reserve": "reserve {count}: {keys}",
        "adm.alchemy_pool_alone": "key alone", "adm.alchemy_pool_exhausted": "exhausted {count}",
        "adm.alchemy_pool_idle": "silent {seconds}s", "adm.alchemy_pool_switched": "switch: {reason} {time}",
        "adm.networks_switch_empty": "no networks", "adm.networks_switch_free": "free source",
        "adm.networks_switch_paid": "paid CU", "adm.networks_switch_on": "ON", "adm.networks_switch_off": "OFF",
        "adm.networks_switch_saving": "saving…", "adm.networks_switch_saved": "{network} {state}",
        "adm.networks_switch_error": "switch failed", "adm.networks_switch_off_state": "switched off",
        "adm.networks_switch_title": "network switch", "adm.alchemy_disabled": "disabled",
        "adm.networks_switch_off_hint": "network switched off",
        "adm.cex_wallet_cleanup_on": "retention {days}d, stale {stale}, every {interval}h, total {count}",
        "adm.cex_wallet_cleanup_off": "auto cleanup off, total {count}",
        "adm.cex_wallet_prune_last": "last prune removed {removed} at {time}",
        "adm.cex_wallet_prune_last_none": "last prune at {time}: nothing to remove",
        "adm.cex_wallet_retention_saved": "retention saved: {days}", "adm.alchemy_saving": "saving",
        "adm.cex_wallet_prune_running": "cleaning", "adm.cex_wallet_pruned": "removed {count} of {total}",
        "adm.cex_wallet_prune_none": "nothing to remove", "adm.cex_wallet_prune_error": "prune failed",
        "adm.cex_wallet_retention_range": "retention must be 0..365",
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
        config = {...config, mode: body.mode, poll_interval_sec: body.poll_interval_sec,
            interval_sec: body.poll_interval_sec, budget_cu: body.monthly_cu};
        return response({ok: true, config});
    }
    if (parsed.pathname === "/api/admin/alchemy/stats") return response({
        network_switch: switchRows(),
        cu: {used: 2500, limit: 10000000, month: "2026-09", estimated_monthly: 7500,
            key_share: 10000000, keys: []},
        key_pool: {keys_configured: 3, active_key_id: "key-1", active_hint: "alchemy••••one",
            wait_sec: 0, reserve: ["alchemy••••two", "alchemy••••three"], exhausted: [],
            idle_sec: 420, idle_failover_sec: 300, switch: {at: now, reason: "тишина 900 с"}},

        networks: ["ETH", "BASE", "SOLANA", "ARBITRUM", "TRON", "HYPERLIQUID"].map(chain =>
            ({chain, status: chain === "ETH" ? "online" : chain === "BASE" ? "rate_limited"
                : chain === "SOLANA" ? "online" : "paused",
              warning_status: chain === "ETH" ? "rate_limited" : "",
              retry_in_sec: chain === "ETH" || chain === "BASE" ? 60 : 0,
              http_status: chain === "ETH" ? 429 : chain === "BASE" ? 200 : null,
              rpc_code: chain === "BASE" ? 429 : null,
              key_active: true,
              enabled: Boolean(networkEnabled[chain]),
              cu_used: chain === "ETH" ? 4200 : 0,
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
        return response({wallets: rows, summary: summary(),
            cleanup: {retention_days: walletRetentionDays, stale: 3, interval_sec: 86400,
                      last_prune: {at: now - 60, removed: walletPruneRemoved, reason: "older than 7d"}}});
    }
    if (parsed.pathname === "/api/admin/screener/cex-wallets" && method === "POST") {
        const row = {id: "dddddddddddddddd", ...body, source: "manual"};
        walletRows.push(row);
        return response({ok: true, wallet: row, summary: summary()});
    }
    if (parsed.pathname === "/api/admin/screener/cex-wallets/refresh" && method === "POST") {
        if (refreshFail) return {ok: false, json: async () => ({
            error: "refresh_failed",
            reason: "HTTP error; URL=https://api.llama.fi/protocols; HTTP 404; body=Not Found",
        })};
        return response({ok: true, after: walletRows.length,
            defillama_count: 3, etherscan_count: 2,
            warnings: ["secondary labels were skipped"]});
    }
    if (parsed.pathname.startsWith("/api/admin/screener/cex-wallets/") && method === "PUT") {
        const id = parsed.pathname.split("/").pop();
        walletRows = walletRows.map(row => row.id === id ? {id: "eeeeeeeeeeeeeeee", ...body, source: "manual"} : row);
        return response({ok: true, wallet: walletRows.find(row => row.name === body.name), summary: summary()});
    }
    if (parsed.pathname === "/api/admin/screener/networks" && method === "POST") {
        networkEnabled[String(body.chain)] = Boolean(body.enabled);
        return response({ok: true, applied: [{chain: String(body.chain),
            enabled: Boolean(body.enabled), changed: true}], network_switch: switchRows()});
    }
    if (parsed.pathname === "/api/admin/screener/cex-wallets/retention" && method === "POST") {
        walletRetentionDays = Number(body.days);
        return response({ok: true, wallet_retention_days: walletRetentionDays,
            cleanup: {retention_days: walletRetentionDays, interval_sec: 86400}});
    }
    if (parsed.pathname === "/api/admin/screener/cex-wallets/prune" && method === "POST") {
        walletPruneRemoved = walletRows.filter(row => row.source !== "manual").length;
        walletRows = walletRows.filter(row => row.source === "manual");
        return response({ok: true, dry_run: false, removed: walletPruneRemoved,
            before: walletPruneRemoved + 1, after: 1, by_chain: {ETH: walletPruneRemoved},
            retention_days: walletRetentionDays, applied: true, activity_source: "history",
            summary: summary()});
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
    assert.equal(win.document.getElementById("whale-admin-interval").value, "120");
    assert(/7[,\.]?500/.test(win.document.getElementById("whale-admin-cu-meta").textContent));
    const networkText = win.document.getElementById("whale-admin-chains").textContent;
    assert(networkText.includes("Solana") && networkText.includes("TRON"));
    assert(networkText.includes("Online (waiting for CEX filters)"));
    assert(networkText.includes("🟡 Rate limit (429) — waiting 60s"));
    assert(win.document.querySelector(".whale-admin-network.status--rate_limited"),
        "rate-limited admin network cards use the yellow warning style");
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
    assert(walletStatus.textContent.includes("https://api.llama.fi/protocols"));
    assert(walletStatus.textContent.includes("HTTP 404"));
    assert(walletStatus.textContent.includes("Not Found"));
    assert(calls.some(call => call.path.endsWith("/refresh") && call.method === "POST"));
    refreshFail = false;
    win.document.getElementById("whale-admin-wallet-refresh").click();
    await sleep(40);
    assert(walletStatus.textContent.includes("DeFiLlama API addresses 3"));
    assert(walletStatus.textContent.includes("Etherscan labels 2"));
    assert(walletStatus.title.includes("secondary labels were skipped"));
    win.document.getElementById("trongrid-admin-key").value = "tron-secret-value";
    win.document.getElementById("trongrid-admin-add-key").click();
    await sleep(50);
    assert(calls.some(call => call.path === "/api/admin/trongrid/key" && call.method === "POST"));
    assert.equal(win.document.getElementById("trongrid-admin-key").value, "");
    assert(!win.document.getElementById("whale-admin-card").textContent.includes("tron-secret-value"));

    // --- пул ключей: один активный, остальные в резерве ---------------------
    const poolLine = win.document.getElementById("whale-admin-key-pool").textContent;
    assert(poolLine.includes("active alchemy••••one"), poolLine);
    assert(poolLine.includes("reserve 2"), poolLine);
    assert(poolLine.includes("silent 420s"), poolLine);
    assert(poolLine.includes("switch: тишина 900 с"), poolLine);

    // --- тумблеры сетей ------------------------------------------------------
    // выключенная сеть в карточках здоровья — статус disabled, а не «ожидание»
    const chainsDom = () => Array.from(win.document.querySelectorAll("#whale-admin-chains .whale-admin-network"));
    const disabledCard = chainsDom().find(card => card.textContent.includes("Arbitrum"));
    assert(disabledCard.classList.contains("status--disabled"), disabledCard.className);
    assert(disabledCard.textContent.includes("switched off"), disabledCard.textContent);
    assert(disabledCard.textContent.includes("network switched off"), disabledCard.textContent);
    const switchRowsDom = win.document.querySelectorAll("#whale-admin-network-switches .whale-admin-switch-row");
    assert.equal(switchRowsDom.length, 8, "все восемь сетей в переключателе");
    const offRow = Array.from(switchRowsDom).find(row => row.querySelector("input").dataset.chain === "ARBITRUM");
    const ethRow = Array.from(switchRowsDom).find(row => row.querySelector("input").dataset.chain === "ETH");
    const tronRow = Array.from(switchRowsDom).find(row => row.querySelector("input").dataset.chain === "TRON");
    assert(offRow.classList.contains("is-off"), "выключенная сеть помечена is-off");
    assert.equal(offRow.querySelector("input").checked, false);
    assert(!ethRow.classList.contains("is-off"), "включённая сеть без is-off");
    assert.equal(ethRow.querySelector("input").checked, true);
    assert(offRow.textContent.includes("paid CU"), offRow.textContent);
    assert(offRow.textContent.includes("Arbitrum One"), offRow.textContent);
    assert(tronRow.textContent.includes("free source"), tronRow.textContent);
    assert.equal(win.document.getElementById("whale-admin-wallet-retention").value, "7");
    const baseToggle = offRow.querySelector("input");
    baseToggle.checked = true;
    baseToggle.dispatchEvent(new win.Event("change"));
    await sleep(60);
    const switchCall = calls.filter(call => call.path === "/api/admin/screener/networks").pop();
    assert.equal(switchCall.method, "POST");
    assert.equal(switchCall.body.chain, "ARBITRUM");
    assert.equal(switchCall.body.enabled, true);
    assert.equal(networkEnabled.ARBITRUM, true, "переключатель дошёл до сервера");
    assert.equal(win.document.getElementById("whale-admin-network-status").textContent,
        "ARBITRUM ON");
    const afterToggle = Array.from(win.document.querySelectorAll("#whale-admin-network-switches .whale-admin-switch-row"))
        .find(row => row.querySelector("input").dataset.chain === "ARBITRUM");
    assert(!afterToggle.classList.contains("is-off"), "после включения строка без is-off");
    assert.equal(afterToggle.querySelector("input").checked, true, "состояние чекбокса обновилось");
    assert(!chainsDom().find(card => card.textContent.includes("Arbitrum"))
        .classList.contains("status--disabled"), "включённая сеть снова живая карточка");


    // --- автоочистка базы кошельков -----------------------------------------
    const cleanupLine = win.document.getElementById("whale-admin-wallet-cleanup").textContent;
    assert(cleanupLine.includes("retention 7d"), cleanupLine);
    assert(cleanupLine.includes("stale 3"), cleanupLine);
    assert(cleanupLine.includes("every 24h"), cleanupLine);
    assert(cleanupLine.includes("last prune removed 5"), cleanupLine);
    assert.equal(win.document.getElementById("whale-admin-wallet-retention").value, "7");
    win.document.getElementById("whale-admin-wallet-retention").value = "0";
    win.document.getElementById("whale-admin-wallet-retention-save").click();
    await sleep(60);
    assert.equal(walletRetentionDays, 0, "окно очистки сохранено");
    assert(win.document.getElementById("whale-admin-wallet-cleanup").textContent
        .includes("auto cleanup off"));
    win.document.getElementById("whale-admin-wallet-retention").value = "400";
    win.document.getElementById("whale-admin-wallet-retention-save").click();
    await sleep(40);
    assert(win.document.getElementById("whale-admin-wallet-status").textContent
        .includes("retention must be 0..365"));
    assert.equal(walletRetentionDays, 0, "некорректное окно не ушло на сервер");
    win.document.getElementById("whale-admin-wallet-retention").value = "7";
    win.document.getElementById("whale-admin-wallet-retention-save").click();
    await sleep(60);
    // в базе остаются и ручная, и автострока: чистка обязана съесть только автостроку
    walletRows.push({id: "ffffffffffffffff", chain: "ETH", address: "0x" + "9".repeat(40),
        name: "Stale CEX", source: "defillama"});
    walletRows.push({id: "gggggggggggggggg", chain: "ETH", address: "0x" + "8".repeat(40),
        name: "Kept by hand", source: "manual"});
    win.document.getElementById("whale-admin-wallet-prune").click();
    await sleep(60);
    assert(calls.some(call => call.path === "/api/admin/screener/cex-wallets/prune"
        && call.method === "POST"));
    assert.deepEqual(walletRows.map(row => row.name), ["Kept by hand"],
        "автострока удалена, ручная осталась");
    assert(win.document.getElementById("whale-admin-wallet-status").textContent
        .includes("removed 2 of 1"), "пробега по автострокам: убраны обе, ручная осталась");

    win.document.getElementById("whale-admin-mode").value = "economy";
    win.document.getElementById("whale-admin-interval").value = "30";
    win.document.getElementById("whale-admin-save").click();
    await sleep(60);
    assert(calls.some(call => call.path === "/api/admin/screener/config" &&
        call.method === "POST" && call.body.mode === "economy" && call.body.poll_interval_sec === 30));
    win.close();
    console.log("Whale admin: CU/mode, Solana/TRON health, TronGrid key and CEX wallet CRUD OK");
})().catch(err => {win.close(); console.error(err); process.exitCode = 1;});
