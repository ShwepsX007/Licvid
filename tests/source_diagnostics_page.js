/* Unified admin source diagnostics: aggregation, resilience and safe text rendering. */
const fs = require("fs");
const assert = require("assert");
const path = require("path");
const {JSDOM} = require("jsdom");

const script = fs.readFileSync(path.join(__dirname, "../static/source_diagnostics.js"), "utf8");
const now = Math.floor(Date.now() / 1000);
const makeResponse = (data, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
});

function setup(isAdmin) {
    const html = `<!doctype html><body>
        <div id="source-diagnostics-card" hidden>
            <button id="source-diagnostics-refresh"></button>
            <span id="source-diagnostics-updated"></span>
            <span id="source-diagnostics-message"></span>
            <div id="source-diagnostics-summary"></div>
            <table><tbody id="source-diagnostics-market-rows"></tbody></table>
            <table><tbody id="source-diagnostics-onchain-rows"></tbody></table>
            <div id="source-diagnostics-runtime"></div>
        </div>
    </body>`;
    const dom = new JSDOM(html, {
        url: "https://liqscope.online/admin",
        runScripts: "outside-only",
        pretendToBeVisual: true,
    });
    const win = dom.window;
    const calls = [];
    const health = {
        status: "ok",
        server_time: now,
        uptime_sec: 3600,
        clients: 2,
        ws_max_clients: 100,
        loop_lag_last_ms: 12,
        loop_lag_max_ms: 25,
        loop_stalls: 1,
        log_dropped: 0,
        seconds_since_last_tick: 3,
        seconds_since_last_liquidation: 20,
        silence_limit_sec: 60,
        demo: false,
        sources: {
            binance: {
                enabled: true, connected: true, events: 12,
                last_event_ts: now - 3, seconds_since_event: 3,
                reconnects: 1, attempts: 2, respawns: 0,
                last_error: "", supervisor_alive: true, stale: false,
            },
            bybit: {
                enabled: true, connected: false, events: 0,
                last_event_ts: 0, seconds_since_event: null,
                reconnects: 0, attempts: 3, respawns: 1,
                last_error: '<img id="diag-xss" src=x onerror="alert(1)">',
                supervisor_alive: true, stale: false,
            },
        },
    };
    const metrics = {
        http_latency_p95_ms: 42.5,
        sqlite_ms: 8.1,
        ws_clients_total: 2,
        ws_max_clients: 100,
        http_requests_per_sec: 3.2,
        ws_messages_per_sec: 12,
    };
    const onchain = {
        ok: true,
        available: true,
        networks: [
            {chain: "ETH", provider: "alchemy", status: "online", enabled: true,
                last_event: now - 20, last_success: now - 5, last_attempt: now - 6, cu_used: 100,
                http_status: 200, error: ""},
            {chain: "SOLANA", provider: "alchemy_solana", status: "quota_exhausted", enabled: true,
                last_event: now - 300, last_success: now - 300, last_attempt: now - 10, cu_used: 200,
                retry_in_sec: 3600, error: "provider quota exhausted"},
        ],
    };

    win.setInterval = () => 1;
    win.fetch = async (url, options = {}) => {
        const parsed = new URL(url, "https://liqscope.online");
        calls.push({path: parsed.pathname, options});
        if (parsed.pathname === "/api/auth/me") {
            return makeResponse({user: {is_admin: Boolean(isAdmin)}});
        }
        if (parsed.pathname === "/api/health") return makeResponse(health);
        if (parsed.pathname === "/api/metrics") return makeResponse(metrics);
        if (parsed.pathname === "/api/admin/alchemy/stats") return makeResponse(onchain);
        throw new Error("Unexpected request: " + parsed.pathname);
    };
    win.eval(script);
    return {dom, win, calls, health, metrics, onchain};
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
    // Admin receives a consolidated view assembled from the existing endpoints.
    const admin = setup(true);
    await sleep(50);
    assert(admin.win.document.getElementById("source-diagnostics-card"));
    assert.equal(admin.win.document.getElementById("source-diagnostics-card").hidden, false);
    const refreshMessage = admin.win.document.getElementById("source-diagnostics-message").textContent;
    assert(refreshMessage.includes("Источники проверены"), "unexpected refresh message: " + refreshMessage);
    assert.equal(admin.win.document.querySelectorAll("#source-diagnostics-market-rows tr").length, 2);
    assert.equal(admin.win.document.querySelectorAll("#source-diagnostics-onchain-rows tr").length, 2);
    assert(admin.win.document.getElementById("source-diagnostics-summary").textContent.includes("1 / 2"));
    assert(admin.win.document.getElementById("source-diagnostics-market-rows").textContent.includes("Ошибка подключения"));
    assert(admin.win.document.getElementById("source-diagnostics-onchain-rows").textContent.includes("Квота исчерпана"));
    assert(admin.win.document.getElementById("source-diagnostics-onchain-rows").textContent.includes("provider quota exhausted"));
    assert(admin.win.document.getElementById("source-diagnostics-runtime").textContent.includes("42,5 мс"));
    assert.equal(admin.win.document.querySelector("#diag-xss"), null, "provider/source errors are rendered as text, not HTML");
    assert(admin.calls.some(call => call.path === "/api/health"));
    assert(admin.calls.some(call => call.path === "/api/metrics"));
    assert(admin.calls.some(call => call.path === "/api/admin/alchemy/stats"));
    assert(admin.win.document.getElementById("source-diagnostics-message").textContent.includes("Источники проверены"));
    admin.dom.window.close();

    // Non-admins must not keep the diagnostics panel in the DOM or trigger source queries.
    const guest = setup(false);
    await sleep(30);
    assert.equal(guest.win.document.getElementById("source-diagnostics-card"), null);
    assert.deepEqual(guest.calls.map(call => call.path), ["/api/auth/me"]);
    guest.dom.window.close();

    console.log("Source diagnostics: market/on-chain aggregation, runtime metrics, safe errors and admin gating OK");
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
