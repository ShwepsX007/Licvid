/* Dual-feed multichain UI: network cards, filters, stacked flow chart and safe text rendering. */
const fs = require("fs");
const assert = require("assert");
const {JSDOM} = require("jsdom");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "../static/screener.html"), "utf8");
const script = fs.readFileSync(path.join(__dirname, "../static/screener.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../static/screener.css"), "utf8");
const dom = new JSDOM(html, {url: "https://liqscope.online/screener", runScripts: "outside-only"});
const win = dom.window;
const now = Math.floor(Date.now() / 1000);
const hash = n => "0x" + String(n).repeat(64).slice(0, 64);
const chains = ["ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA", "TRON", "HYPERLIQUID"];
const networks = {};
chains.forEach((chain, index) => {
    networks[chain] = {status: index === 0 || index === 7 ? "online" : "waiting",
        warning_status: index === 0 ? "rate_limited" : "",
        retry_in_sec: index === 0 ? 60 : 0,
        http_status: index === 0 ? 429 : null,
        rpc_code: null, key_active: true,
        events: index === 0 || index === 7 ? 1 : 0,
        volume_usd: index === 0 ? 125000 : index === 7 ? 275000 : 0,
        inflow_usd: index === 0 ? 125000 : 0, outflow_usd: 0, transfer_usd: 0,
        buy_usd: index === 7 ? 275000 : 0, sell_usd: 0,
        assets: {}, inflow_assets: index === 0 ? {USDT: 125000} : {},
        outflow_assets: {}, transfer_assets: {}, buy_assets: index === 7 ? {BTC: 2.5} : {},
        sell_assets: {}, error: chain === "ETH" ? "HTTP 429: provider throttled"
            : chain === "SOLANA" ? "No indexed Solana CEX wallets" : ""};
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
// Ответ /exchanges_24h: список бирж строится по данным — в нём только те
// площадки, у которых за сутки был реальный поток (binance и gate). Bybit, OKX
// и Kraken без событий отсутствуют целиком, а не нулевыми клетками.
const venueRows = venue => Array.from({length: 144}, (_, i) => ({
    timestamp: Math.floor(now / 600) * 600 - (143 - i) * 600,
    hour: String(Math.floor(i / 6)).padStart(2, "0") + ":" + String((i % 6) * 10).padStart(2, "0"),
    inflow: venue === "binance" && (i === 141 || i === 143)
        ? (i === 143 ? 150000 : 100000) : venue === "gate" && i === 140 ? 40000 : 0,
    outflow: venue === "binance" && i === 142 ? 50000 : venue === "gate" && i === 141 ? 90000 : 0,
    net_flow: venue === "binance"
        ? (i === 141 ? -100000 : i === 142 ? 50000 : i === 143 ? -150000 : 0)
        : venue === "gate" ? (i === 140 ? -40000 : i === 141 ? 90000 : 0) : 0,
}));
let venueFlows = {
    minutes: 10, points: 144,
    venues: [
        {id: "binance", name: "Binance", inflow: 250000, outflow: 50000,
            balance: 200000, events: 3},
        {id: "gate", name: "Gate.io", inflow: 40000, outflow: 90000,
            balance: -50000, events: 2},
    ],
    series: {binance: venueRows("binance"), gate: venueRows("gate")},
};
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
        "screener.status_rate_limited": "🟡 Rate limit (429) — waiting {seconds}s",
        "screener.status_auth_error": "Authentication error",
        "screener.status_quota_exhausted": "Quota exhausted",
        "screener.status_network_error": "Network error", "screener.status_paused": "Paused",
        "screener.direction_outflow": "Exchange outflow", "screener.direction_trade": "Trade",
        "screener.direction_transfer": "Transfer", "screener.open_explorer": "View transaction",
        "screener.legend_inflow": "Inflow", "screener.legend_outflow": "Outflow",
        "screener.legend_buy": "Buy", "screener.legend_sell": "Sell",
        "screener.legend_transfer": "Transfer", "screener.tooltip_tokens": "Token volume",
        "screener.tooltip_24h": "24h total", "screener.tooltip_top3": "Top 3 exchanges",
        "screener.no_top_exchanges": "No labeled exchanges", "screener.flow_title": "Transfer flow",
        "screener.trade_chart_title": "Hyperliquid trade flow",
        "screener.period_24h": "24h", "screener.net_flow": "Net Flow",
        "screener.venue_flow_empty": "No exchange flows in this period.",
        "screener.venue_in": "In", "screener.venue_out": "Out",
    };
    let value = labels[key] || key;
    Object.keys(vars).forEach(name => { value = value.replace("{" + name + "}", vars[name]); });
    return value;
}, onChange: () => {} };
win.fetch = async url => {
    const parsed = new URL(url, "https://liqscope.online");
    fetches.push(parsed.pathname + parsed.search);
    if (parsed.pathname.endsWith("/exchanges_24h")) {
        assert.equal(parsed.searchParams.get("minutes"), "10", "10-минутные бакеты по умолчанию");
        return {ok: true, json: async () => venueFlows};
    }
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
    const ethNetworkCard = win.document.querySelector('#network-cards [data-network="ETH"]');
    assert(ethNetworkCard.querySelector(".network-state").textContent.includes(
        "Online · 🟡 Rate limit (429) — waiting 60s"));
    assert(ethNetworkCard.querySelector(".network-state.status--rate_limited"),
        "an online network with a rate-limit warning uses the yellow status style");
    assert(ethNetworkCard.title.includes("HTTP 429: provider throttled"));
    assert(win.document.querySelector('[data-network="SOLANA"]').title.includes(
        "No indexed Solana CEX wallets"));
    assert.equal(win.document.querySelectorAll("#flow-chart .chart-bar").length, 2);
    assert(parseFloat(win.document.querySelector("#flow-chart .chart-bar").getAttribute("width")) >= 30,
        "six-hour category bars have readable widths");
    assert(css.includes("grid-template-columns: minmax(0,1fr) minmax(0,1fr)"));
    assert(css.includes("gap: 16px"));
    assert(css.includes("height: 540px; max-height: 540px"));
    assert(css.includes("overflow-y: auto"));
    assert(css.includes(".event-list { display: flex; flex-direction: column"));
    assert(css.includes("height: 78px; flex: 0 0 78px"));
    assert(css.includes("grid-template-areas: \"network direction details value link\""));
    assert(css.includes("font-feature-settings: \"tnum\""));
    assert(css.includes(".network-state.status--rate_limited"));
    assert(css.includes("@media (max-width: 900px)"));
    assert(css.includes("font: 13px var(--font, system-ui)"));
    ["screener-events", "hl-screener-events"].forEach(id => {
        const feed = win.document.getElementById(id);
        assert.equal(feed.getAttribute("role"), "region");
        assert.equal(feed.getAttribute("tabindex"), "0");
    });
    assert.equal(win.document.querySelectorAll("#chart-legend .network-legend-item").length, 8);
    const venueCards = win.document.querySelectorAll("#venue-charts .venue-card");
    assert.equal(venueCards.length, 2, "клетки только у бирж, у которых есть поток");
    assert(!win.document.querySelector('#venue-charts [data-venue="bybit"]'),
        "биржи без данных не получают пустую клетку-заглушку");
    assert.equal(win.document.querySelector('#venue-charts [data-venue="binance"] .venue-name').textContent,
        "Binance 24h");
    const binanceCard = win.document.querySelector('#venue-charts [data-venue="binance"]');
    // Кривая накопленного потока: зашло 100K — вверх, вышло 50K — вниз, зашло 150K — снова вверх.
    assert.equal(binanceCard.querySelectorAll(".venue-seg.is-up").length, 2,
        "два участка роста: деньги заходили на биржу");
    assert.equal(binanceCard.querySelectorAll(".venue-seg.is-down").length, 1,
        "один участок падения: деньги уходили с биржи");
    const rising = binanceCard.querySelector(".venue-seg.is-up").getAttribute("d");
    const falling = binanceCard.querySelector(".venue-seg.is-down").getAttribute("d");
    // В SVG ось Y смотрит вниз: рост значения — это уменьшение y.
    assert(parseFloat(rising.match(/M[\d.]+ ([\d.]+)/)[1]) >
        parseFloat(rising.match(/L[\d.]+ ([\d.]+)/)[1]),
        "зелёный участок идёт вверх (заход на биржу)");
    assert(parseFloat(falling.match(/M[\d.]+ ([\d.]+)/)[1]) <
        parseFloat(falling.match(/L[\d.]+ ([\d.]+)/)[1]),
        "красный участок идёт вниз (выход с биржи)");
    assert(binanceCard.querySelector(".venue-area"), "под кривой заливка, как у биржевого графика");
    assert(binanceCard.querySelector(".venue-zero"), "нулевая линия подписана и видна");
    const axis = Array.from(binanceCard.querySelectorAll(".venue-axis-label"))
        .map(node => node.textContent).join(" ");
    assert(axis.includes("+$") && axis.includes("−$") && axis.includes("$0"),
        "по оси подписи сумм со знаком — плюс, ноль и минус: " + axis);
    assert(binanceCard.querySelectorAll(".venue-axis-label").length >= 5,
        "подписи сумм и времени на месте");
    assert(binanceCard.querySelector(".venue-net").textContent.includes("+$200K"),
        "итог за сутки: на биржу зашло на $200K больше, чем ушло");
    assert(binanceCard.querySelector(".venue-net").classList.contains("is-inflow"));
    assert(binanceCard.querySelector(".venue-totals").textContent.includes("In $250K") &&
        binanceCard.querySelector(".venue-totals").textContent.includes("Out $50K"),
        "под графиком видно, сколько зашло и сколько вышло");
    const segTitles = Array.from(binanceCard.querySelectorAll(".venue-seg title"))
        .map(node => node.textContent).join(" | ");
    assert(segTitles.includes("In $100K") && segTitles.includes("Out $50K"),
        "подсказка участка объясняет направление: " + segTitles);
    const gateCard = win.document.querySelector('#venue-charts [data-venue="gate"]');
    assert(gateCard, "новая биржа из реестра появляется без правки кода");
    assert.equal(gateCard.querySelector(".venue-name").textContent, "Gate.io 24h");
    assert(gateCard.querySelector(".venue-net").textContent.includes("−$50K"),
        "итог Gate.io: с биржи ушло на $50K больше, чем зашло");
    assert(gateCard.querySelector(".venue-net").classList.contains("is-outflow"));
    // Ни одной биржи с потоком — одно общее сообщение вместо набора заглушек.
    venueFlows = {minutes: 10, points: 144, venues: [], series: {}};
    // jsdom держит документ «скрытым»: показываем его, чтобы проверить догон
    // графиков после возвращения на вкладку (в реальном браузере hidden=false).
    Object.defineProperty(win.document, "hidden", {value: false, configurable: true});
    const beforeRefetch = fetches.filter(url => url.includes("exchanges_24h")).length;
    win.document.dispatchEvent(new win.Event("visibilitychange"));
    await sleep(50);
    assert.equal(fetches.filter(url => url.includes("exchanges_24h")).length, beforeRefetch + 1,
        "возвращённая вкладка догоняет графики бирж, не дожидаясь интервала");
    assert.equal(win.document.querySelectorAll("#venue-charts .venue-card").length, 0,
        "без данных карточек нет");
    assert.equal(win.document.querySelectorAll("#venue-charts .venue-empty").length, 1,
        "показываем одно честное сообщение об отсутствии потоков");
    venueFlows = {
        minutes: 10, points: 144,
        venues: [
            {id: "binance", name: "Binance", inflow: 250000, outflow: 50000,
                balance: 200000, events: 3},
            {id: "gate", name: "Gate.io", inflow: 40000, outflow: 90000,
                balance: -50000, events: 2},
        ],
        series: {binance: venueRows("binance"), gate: venueRows("gate")},
    };
    assert(css.includes(".venue-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr))"),
        "на десктопе три колонки");
    assert(css.includes(".venue-chart { display: block; width: 100%; height: 200px"),
        "график биржи высотой ~200px");
    assert(css.includes(".venue-seg.is-up { stroke: #00e676; }") &&
        css.includes(".venue-seg.is-down { stroke: #ff2a5f; }"),
        "цвета участков — как у свечей терминала");
    assert(fetches.some(url => url.includes("/api/screener/stats/exchanges_24h") && url.includes("minutes=10")),
        "фронт запрашивает потоки по биржам за 24 часа");
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
    assert(historicalRow.querySelector(".event-details .event-route").title.includes("→"));
    assert(historicalRow.querySelector(".event-direction").title);
    assert(historicalRow.querySelector(".event-value").title);
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
