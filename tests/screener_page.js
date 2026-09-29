/* Dual-feed multichain UI: network cards, filters, stacked flow chart and safe text rendering. */
const fs = require("fs");
const assert = require("assert");
const {JSDOM} = require("jsdom");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "../static/screener.html"), "utf8");
const script = fs.readFileSync(path.join(__dirname, "../static/screener.js"), "utf8");
const dom = new JSDOM(html, {url: "https://liqscope.online/screener", runScripts: "outside-only"});
const win = dom.window;
const now = Math.floor(Date.now() / 1000);
const hash = n => "0x" + String(n).repeat(64).slice(0, 64);
const chains = ["ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA", "TRON", "HYPERLIQUID"];
const networks = {};
chains.forEach((chain, index) => {
    networks[chain] = {status: index === 0 || index === 7 ? "online" : "waiting",
        events: index === 0 || index === 7 ? 1 : 0,
        volume_usd: index === 0 ? 125000 : index === 7 ? 275000 : 0,
        inflow_usd: index === 0 ? 125000 : 0, outflow_usd: 0, transfer_usd: 0,
        buy_usd: index === 7 ? 275000 : 0, sell_usd: 0,
        assets: {}, inflow_assets: index === 0 ? {USDT: 125000} : {},
        outflow_assets: {}, transfer_assets: {}, buy_assets: index === 7 ? {BTC: 2.5} : {},
        sell_assets: {}};
});
const rows = [
    {chain: "ETH", hash: hash("a"), log_index: "0x1", timestamp: now - 60,
        symbol: "USDT", amount: 125000, usd: 125000,
        from: "0x" + "1".repeat(40), to: "0x" + "2".repeat(40),
        from_label: "<script>alert(1)</script>", to_label: "", direction: "outflow",
        source: "historical"},
    {chain: "HYPERLIQUID", hash: hash("b"), log_index: "BTC:123", timestamp: now - 30,
        symbol: "BTC", amount: 2.5, usd: 275000, from: "0x" + "2".repeat(40),
        to: "0x" + "3".repeat(40), from_label: "", to_label: "",
        direction: "trade", side: "BUY", source: "realtime"},
];
const series = Array.from({length: 24}, (_, i) => ({
    timestamp: Math.floor(now / 3600) * 3600 - (23 - i) * 3600,
    networks: Object.fromEntries(chains.map(chain => [chain, {
        inflow_usd: chain === "ETH" && i === 23 ? 125000 : 0,
        outflow_usd: 0, transfer_usd: 0,
        buy_usd: chain === "HYPERLIQUID" && i === 23 ? 275000 : 0,
        sell_usd: 0,
        inflow_assets: chain === "ETH" && i === 23 ? {USDT: 125000} : {},
        outflow_assets: {}, transfer_assets: {},
        buy_assets: chain === "HYPERLIQUID" && i === 23 ? {BTC: 2.5} : {}, sell_assets: {},
    }]))
}));
const stats = {total_events: 2, total_volume_usd: 400000, active_networks: 2,
    networks, series, top_exchanges: {ALL: [{name: "Binance 14", events: 1, volume_usd: 125000}],
        ETH: [{name: "Binance 14", events: 1, volume_usd: 125000}], HYPERLIQUID: []}};
const fetches = [];
win.LiqScopeI18n = {lang: () => "en", t: (key, vars = {}) => {
    const labels = {
        "screener.network_all": "All networks", "screener.network_eth": "Ethereum",
        "screener.network_bnb": "BNB Chain", "screener.network_polygon": "Polygon",
        "screener.network_arbitrum": "Arbitrum", "screener.network_base": "Base",
        "screener.network_solana": "Solana", "screener.network_tron": "TRON",
        "screener.network_hyperliquid": "Hyperliquid Core",
        "screener.exchange_all": "All exchanges", "screener.exchange_unknown": "Unlabeled exchange",
        "screener.status_live": "Dashboard is updating", "screener.status_online": "Online",
        "screener.status_waiting": "Waiting", "screener.status_error": "Error",
        "screener.direction_outflow": "Exchange outflow", "screener.direction_trade": "Trade",
        "screener.direction_transfer": "Transfer", "screener.open_explorer": "View transaction",
        "screener.legend_inflow": "Inflow", "screener.legend_outflow": "Outflow",
        "screener.legend_buy": "Buy", "screener.legend_sell": "Sell",
        "screener.legend_transfer": "Transfer", "screener.tooltip_tokens": "Token volume",
        "screener.tooltip_24h": "24h total", "screener.tooltip_top3": "Top 3 exchanges",
        "screener.no_top_exchanges": "No labeled exchanges", "screener.flow_title": "Transfer flow",
        "screener.trade_chart_title": "Hyperliquid trade flow",
    };
    let value = labels[key] || key;
    Object.keys(vars).forEach(name => { value = value.replace("{" + name + "}", vars[name]); });
    return value;
}, onChange: () => {} };
win.fetch = async url => {
    const parsed = new URL(url, "https://liqscope.online");
    fetches.push(parsed.pathname + parsed.search);
    if (parsed.pathname.endsWith("/stats")) return {ok: true, json: async () => stats};
    if (parsed.pathname.endsWith("/whales")) {
        const chain = parsed.searchParams.get("chain") || "ALL";
        return {ok: true, json: async () => ({events: chain === "GENERAL"
            ? rows.filter(row => row.chain !== "HYPERLIQUID")
            : rows.filter(row => row.chain === chain)})};
    }
    if (parsed.pathname.endsWith("/history")) {
        const chain = parsed.searchParams.get("chain") || "ALL";
        const filtered = rows.filter(row => chain === "ALL" || row.chain === chain);
        return {ok: true, json: async () => ({events: filtered, total: filtered.length, page: 1, pages: 2})};
    }
    throw new Error("unexpected fetch: " + url);
};
win.eval(script);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
    await sleep(100);
    assert.equal(win.document.querySelectorAll("#network-cards .network-card").length, 8);
    assert.equal(win.document.querySelectorAll("#network-tabs .network-tab").length, 9);
    assert.equal(win.document.querySelectorAll("#flow-chart .chart-bar").length, 2);
    assert.equal(win.document.querySelectorAll("#chart-legend .network-legend-item").length, 8);
    assert.equal(win.document.querySelectorAll("#exchange-list .exchange-row").length, 1);
    assert.equal(win.document.querySelectorAll("#screener-events .event-row").length, 1);
    assert.equal(win.document.querySelectorAll("#hl-screener-events .event-row").length, 1);
    assert.equal(win.document.querySelectorAll("#history-rows tr").length, 2);
    assert(win.document.querySelector("#history-rows .table-time").textContent.includes("🕒"));
    assert.equal(win.document.querySelectorAll("#screener-events script").length, 0);
    assert(win.document.querySelector("#screener-events").textContent.includes("<script>alert(1)</script>"));
    const historicalRow = win.document.querySelector("#screener-events .event-row");
    assert(historicalRow.textContent.includes("🕒"), "historical events show the clock marker");
    assert(historicalRow.querySelector(".event-asset").textContent.includes("125,000"));
    const explorer = Array.from(win.document.querySelectorAll("#screener-events a"))
        .find(link => link.href.startsWith("https://etherscan.io/tx/"));
    assert(explorer, "Etherscan transaction link is rendered");
    const hlLink = Array.from(win.document.querySelectorAll("#hl-screener-events a"))
        .find(link => link.href.startsWith("https://hypurrscan.io/tx/"));
    assert(hlLink, "native Hyperliquid explorer link is rendered");
    assert(fetches.some(url => url.includes("chain=GENERAL")));
    assert(fetches.some(url => url.includes("chain=HYPERLIQUID")));
    const tooltip = Array.from(win.document.querySelectorAll("#flow-chart .chart-bar title"))
        .map(node => node.textContent).join("\n");
    assert(tooltip.includes("Token volume") && tooltip.includes("24h total") && tooltip.includes("Top 3 exchanges"));

    win.document.getElementById("hl-direction").value = "BUY";
    win.document.getElementById("hl-direction").dispatchEvent(new win.Event("change"));
    await sleep(20);
    assert.equal(win.document.querySelectorAll("#hl-screener-events .event-row").length, 1);
    assert(win.document.querySelector("#hl-screener-events").textContent.includes("BUY"));
    win.document.getElementById("hl-min").value = "500000";
    win.document.getElementById("hl-min").dispatchEvent(new win.Event("change"));
    await sleep(20);
    assert.equal(win.document.querySelectorAll("#hl-screener-events .event-row").length, 0);

    win.document.getElementById("whale-direction").value = "outflow";
    win.document.getElementById("whale-direction").dispatchEvent(new win.Event("change"));
    await sleep(20);
    assert.equal(win.document.querySelectorAll("#screener-events .event-row").length, 1);
    assert(fetches.some(url => url.includes("/api/screener/history?") && url.includes("direction=outflow")));

    win.document.getElementById("whale-direction").value = "ALL";
    win.document.getElementById("hl-min").value = "100000";
    win.document.getElementById("hl-min").dispatchEvent(new win.Event("change"));
    win.document.getElementById("whale-chain").value = "HYPERLIQUID";
    win.document.getElementById("whale-chain").dispatchEvent(new win.Event("change"));
    await sleep(50);
    assert.equal(win.document.querySelectorAll("#screener-events .event-row").length, 0);
    assert.equal(win.document.querySelectorAll("#hl-screener-events .event-row").length, 1);
    assert(fetches.some(url => url.includes("/api/screener/history?") && url.includes("chain=HYPERLIQUID")));
    assert(win.document.getElementById("history-next").disabled === false);
    win.close();
    console.log("Screener dashboard: dual feeds, eight networks, independent filters, stacked chart, history and safe text OK");
})().catch(err => {win.close(); console.error(err); process.exitCode = 1;});
