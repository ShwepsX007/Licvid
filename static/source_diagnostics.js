/* Unified source diagnostics for the admin page. Uses existing read-only health endpoints. */
(() => {
    "use strict";

    const $ = id => document.getElementById(id);
    const card = $("source-diagnostics-card");
    if (!card) return;

    const refreshButton = $("source-diagnostics-refresh");
    const updated = $("source-diagnostics-updated");
    const message = $("source-diagnostics-message");
    const summary = $("source-diagnostics-summary");
    const runtime = $("source-diagnostics-runtime");
    const marketRows = $("source-diagnostics-market-rows");
    const onchainRows = $("source-diagnostics-onchain-rows");
    const previousCounts = new Map();
    let loading = false;
    let timer = null;

    const MARKET_NAMES = {
        binance: "Binance · ликвидации",
        bybit: "Bybit · ликвидации",
        okx: "OKX · ликвидации",
        gate: "Gate.io · ликвидации",
        bitget: "Bitget · ликвидации",
        htx: "HTX · ликвидации",
        hyperliquid: "Hyperliquid · ликвидации",
        oxa: "0xArchive · транспорт Hyperliquid",
        prices: "Рыночные цены",
        ticks: "Сделки / поток CVD",
        kraken: "Kraken Futures · ликвидации",
        bitfinex: "Bitfinex · ликвидации",
    };
    const NETWORK_NAMES = {
        ETH: "Ethereum",
        BNB: "BNB Chain",
        POLYGON: "Polygon",
        ARBITRUM: "Arbitrum",
        BASE: "Base",
        SOLANA: "Solana",
        TRON: "TRON",
        HYPERLIQUID: "Hyperliquid",
    };
    const STATUS_LABELS = {
        online: ["Работает", "ok"],
        waiting: ["Ожидание данных", "warn"],
        paused: ["Приостановлена", "muted"],
        disabled: ["Отключена", "muted"],
        rate_limited: ["Лимит запросов", "warn"],
        auth_error: ["Ошибка ключа", "bad"],
        quota_exhausted: ["Квота исчерпана", "bad"],
        network_error: ["Ошибка сети", "bad"],
        error: ["Ошибка", "bad"],
        unavailable: ["Недоступен", "bad"],
    };

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function cell(row, value, className) {
        const td = element("td", className || "");
        if (value instanceof Node) td.append(value);
        else td.textContent = value === undefined || value === null || value === "" ? "—" : String(value);
        row.append(td);
        return td;
    }

    function pill(label, tone) {
        const node = element("span", "diag-pill", label);
        node.dataset.tone = tone || "muted";
        return node;
    }

    function ageLabel(seconds) {
        if (seconds === null || seconds === undefined || seconds === "") return "—";
        const n = Number(seconds);
        if (!Number.isFinite(n) || n < 0) return "—";
        if (n < 60) return Math.round(n) + " с";
        if (n < 3600) return Math.floor(n / 60) + " мин";
        if (n < 86400) return Math.floor(n / 3600) + " ч " + Math.floor((n % 3600) / 60) + " мин";
        return Math.floor(n / 86400) + " д " + Math.floor((n % 86400) / 3600) + " ч";
    }

    function ago(timestamp, nowSec) {
        const ts = Number(timestamp);
        if (!Number.isFinite(ts) || ts <= 0) return "—";
        return ageLabel(Math.max(0, nowSec - ts)) + " назад";
    }

    function number(value, digits) {
        const n = Number(value);
        if (!Number.isFinite(n)) return "—";
        return n.toLocaleString("ru-RU", {
            maximumFractionDigits: digits === undefined ? 0 : digits,
            minimumFractionDigits: 0,
        });
    }

    function duration(value) {
        const seconds = Number(value);
        if (!Number.isFinite(seconds) || seconds < 0) return "—";
        if (seconds < 60) return Math.round(seconds) + " с";
        if (seconds < 3600) return Math.floor(seconds / 60) + " мин";
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        if (hours < 48) return hours + " ч " + minutes + " мин";
        return Math.floor(hours / 24) + " д " + (hours % 24) + " ч";
    }

    async function request(path) {
        const response = await fetch(path, {
            credentials: "same-origin",
            cache: "no-store",
            headers: {"Accept": "application/json"},
        });
        let data = {};
        try { data = await response.json(); } catch (_) {}
        if (!response.ok) {
            const reason = data && (data.error || data.detail || data.message);
            throw new Error((reason ? String(reason) + " · " : "") + "HTTP " + response.status);
        }
        return data;
    }

    function marketState(name, source, silenceLimit) {
        if (source.enabled === false) return {label: "Отключён", tone: "muted"};
        if (source.supervisor_alive === false) return {label: "Фоновая задача остановлена", tone: "bad"};
        if (!source.connected) {
            return source.last_error
                ? {label: "Ошибка подключения", tone: "bad"}
                : {label: "Нет соединения", tone: "warn"};
        }
        if (source.stale === true) return {label: "Нет свежих событий", tone: "warn"};

        const age = Number(source.seconds_since_event);
        const dynamicLimit = Math.max(60, Number(silenceLimit) || 0);
        if ((name === "prices" || name === "ticks") && Number.isFinite(age) && age > dynamicLimit) {
            return {label: "Нет свежих данных", tone: "warn"};
        }
        if (!source.last_event_ts && Number(source.events || 0) === 0) {
            return {label: "Подключён · событий ещё нет", tone: "warn"};
        }
        if (source.last_error || source.error || source.specs_error) {
            return {label: "Работает с предупреждением", tone: "warn"};
        }
        return {label: "Работает", tone: "ok"};
    }

    function eventRate(name, count, nowMs) {
        const n = Number(count);
        if (!Number.isFinite(n)) return "—";
        const previous = previousCounts.get(name);
        previousCounts.set(name, {count: n, at: nowMs});
        if (!previous || nowMs <= previous.at || n < previous.count) return "сбор данных…";
        const rate = Math.max(0, n - previous.count) * 60000 / (nowMs - previous.at);
        return number(rate, rate < 10 ? 1 : 0);
    }

    function sourceDetail(source) {
        const text = source.last_error || source.error || source.specs_error ||
            source.warning || source.message || "";
        if (text) return String(text);
        if (source.supervisor_alive === false) return "Супервизор не запущен";
        if (source.stale) return "Соединение есть, но события давно не поступали";
        const details = [];
        if (source.subscribe && source.subscribe !== "success") details.push("Подписка: " + source.subscribe);
        if (source.specs_source) details.push("Спецификации: " + source.specs_source);
        if (source.specs_cached) details.push("Контрактов в кэше: " + number(source.specs_cached));
        if (source.respawns) details.push("Повторный запуск задачи: " + number(source.respawns));
        return details.join(" · ") || "Ошибок не зарегистрировано";
    }

    function renderMarket(sources, health) {
        marketRows.replaceChildren();
        const rows = Object.entries(sources || {}).sort((a, b) => {
            const order = ["binance", "bybit", "okx", "gate", "bitget", "htx", "hyperliquid", "oxa", "prices", "ticks"];
            const ai = order.indexOf(a[0]), bi = order.indexOf(b[0]);
            if (ai >= 0 || bi >= 0) return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
            return a[0].localeCompare(b[0]);
        });
        const nowMs = Date.now();
        const nowSec = Number(health && health.server_time) || nowMs / 1000;
        rows.forEach(([name, source]) => {
            if (!source || typeof source !== "object") return;
            const state = marketState(name, source, health && health.silence_limit_sec);
            const row = document.createElement("tr");
            const label = MARKET_NAMES[name] || name.replace(/[_-]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());
            cell(row, label);
            cell(row, pill(state.label, state.tone));
            const age = source.seconds_since_event !== undefined && source.seconds_since_event !== null
                ? ageLabel(source.seconds_since_event) + " назад"
                : ago(source.last_event_ts, nowSec);
            const ageCell = cell(row, age);
            if (Number(source.last_event_ts) > 0) ageCell.title = new Date(Number(source.last_event_ts) * 1000).toLocaleString();
            cell(row, eventRate(name, source.events, nowMs));
            cell(row, source.reconnects === undefined ? "—" : number(source.reconnects));
            const detail = cell(row, sourceDetail(source), "diag-detail");
            if (detail.textContent.length > 180) detail.title = detail.textContent;
        });
        if (!rows.length) {
            const row = document.createElement("tr");
            cell(row, "Нет данных об источниках", "diag-empty");
            row.firstChild.colSpan = 6;
            marketRows.append(row);
        }
        return rows.map(([name, source]) => ({name, source, state: marketState(name, source || {}, health && health.silence_limit_sec)}));
    }

    function networkState(network) {
        if (network.enabled === false || network.status === "disabled") return {label: "Отключена", tone: "muted"};
        if (network.status && STATUS_LABELS[network.status]) {
            const [label, tone] = STATUS_LABELS[network.status];
            if (network.warning_status && tone === "ok") return {label: "Работает · есть предупреждение", tone: "warn"};
            return {label, tone};
        }
        return {label: network.status || "Неизвестно", tone: "warn"};
    }

    function networkProvider(network) {
        return ({
            alchemy: "Alchemy RPC",
            alchemy_solana: "Alchemy Solana",
            trongrid: "TronGrid",
            native_api: "Native API",
        })[network.provider] || network.provider || "—";
    }

    function networkDetail(network) {
        const parts = [];
        if (network.warning_status) parts.push("Предупреждение: " + network.warning_status);
        if (network.http_status !== undefined && network.http_status !== null) parts.push("HTTP " + network.http_status);
        if (network.rpc_code !== undefined && network.rpc_code !== null) parts.push("RPC " + network.rpc_code);
        if (network.retry_in_sec) parts.push("Повтор через " + duration(network.retry_in_sec));
        if (network.cu_used) parts.push(number(network.cu_used) + " CU (оценка)");
        if (network.requests_last_cycle !== undefined && network.requests_last_cycle !== null) {
            parts.push(number(network.requests_last_cycle) + " запросов за цикл");
        }
        if (network.waiting_for_filters) parts.push("ожидает фильтры кошельков");
        if (network.error) parts.push(String(network.error));
        return parts.join(" · ") || "Ошибок не зарегистрировано";
    }

    function renderOnchain(networks, nowSec, unavailableMessage) {
        onchainRows.replaceChildren();
        if (!Array.isArray(networks) || !networks.length) {
            const row = document.createElement("tr");
            cell(row, unavailableMessage || "Нет данных по on-chain сетям", "diag-empty");
            row.firstChild.colSpan = 7;
            onchainRows.append(row);
            return [];
        }
        networks.forEach(network => {
            if (!network || typeof network !== "object") return;
            const row = document.createElement("tr");
            cell(row, NETWORK_NAMES[network.chain] || network.chain || "—");
            cell(row, networkProvider(network));
            const state = networkState(network);
            cell(row, pill(state.label, state.tone));
            cell(row, ago(network.last_success, nowSec));
            cell(row, ago(network.last_attempt, nowSec));
            cell(row, network.cu_used ? number(network.cu_used) : "—");
            const detail = cell(row, networkDetail(network), "diag-detail");
            if (detail.textContent.length > 180) detail.title = detail.textContent;
        });
        return networks.map(network => ({name: network.chain || "—", network, state: networkState(network)}));
    }

    function metricCard(label, value, note, tone) {
        const node = element("div", "diag-stat");
        node.append(element("span", "diag-stat-label", label));
        const valueNode = element("strong", "diag-stat-value", value);
        if (tone) valueNode.dataset.tone = tone;
        node.append(valueNode);
        if (note) node.append(element("small", "diag-muted", note));
        return node;
    }

    function renderRuntime(health, metrics) {
        runtime.replaceChildren();
        const h = health || {};
        const m = metrics || {};
        const queueDrops = Object.entries(m).filter(([key, value]) =>
            /drop/i.test(key) && value !== null && value !== undefined && Number.isFinite(Number(value))
        ).map(([key, value]) => key.replace(/_/g, " ") + ": " + number(value));
        if (h.log_dropped !== undefined && h.log_dropped !== null) {
            queueDrops.unshift("потеряно записей лога: " + number(h.log_dropped));
        }
        const wsClients = m.ws_clients_total !== undefined ? m.ws_clients_total : h.clients;
        const wsMax = m.ws_max_clients !== undefined ? m.ws_max_clients : h.ws_max_clients;
        runtime.append(
            metricCard("Аптайм сервера", duration(h.uptime_sec)),
            metricCard("Клиенты WebSocket", number(wsClients) + (wsMax ? " / " + number(wsMax) : "")),
            metricCard("HTTP p95", m.http_latency_p95_ms !== undefined
                ? number(m.http_latency_p95_ms, 1) + " мс" : "—", "Время обработки API"),
            metricCard("Проверка SQLite", m.sqlite_ms !== undefined
                ? number(m.sqlite_ms, 1) + " мс"
                : h.sqlite_ms !== undefined ? number(h.sqlite_ms, 1) + " мс" : "—"),
            metricCard("Задержка event loop", h.loop_lag_last_ms !== undefined
                ? number(h.loop_lag_last_ms, 1) + " мс" : "—",
                "максимум " + (h.loop_lag_max_ms !== undefined ? number(h.loop_lag_max_ms, 1) + " мс" : "—")),
            metricCard("Последняя сделка", ageLabel(h.seconds_since_last_tick), "по времени сервера"),
            metricCard("Последняя ликвидация", ageLabel(h.seconds_since_last_liquidation), "по времени сервера"),
            metricCard("Сбои/потери", queueDrops.length ? queueDrops.join(" · ") : "Нет отдельного счётчика")
        );
    }

    function renderSummary(market, networks, health, metrics, networkError) {
        summary.replaceChildren();
        const enabledMarket = market.filter(row => row.source && row.source.enabled !== false);
        const marketOnline = enabledMarket.filter(row => row.state.tone === "ok").length;
        const marketIssues = market.filter(row => row.state.tone === "bad" || row.state.tone === "warn").length;
        const enabledNetworks = networks.filter(row => row.network && row.network.enabled !== false);
        const networksOnline = enabledNetworks.filter(row => row.state.tone === "ok").length;
        const networkIssues = networks.filter(row => row.state.tone === "bad" || row.state.tone === "warn").length;
        summary.append(
            metricCard("Рыночные источники", marketOnline + " / " + enabledMarket.length,
                "свежие и подключённые", marketIssues ? "warn" : "ok"),
            metricCard("Замечания по рынку", number(marketIssues),
                "ошибки, отключения связи или устаревшие данные", marketIssues ? "warn" : "ok"),
            metricCard("On-chain сети", networksOnline + " / " + enabledNetworks.length,
                networkError ? "не удалось получить статус скринера" : "активные сети в норме", networkIssues ? "warn" : "ok"),
            metricCard("HTTP-запросы", metrics && metrics.http_requests_per_sec !== undefined
                ? number(metrics.http_requests_per_sec, 1) + " / сек" : "—",
                health && health.demo ? "ВНИМАНИЕ: включён DEMO" : "реальная телеметрия сервера",
                health && health.demo ? "warn" : "")
        );
    }

    function showMessage(text, tone) {
        message.textContent = text || "";
        message.dataset.tone = tone || "muted";
    }

    async function refresh() {
        if (loading) return;
        loading = true;
        if (refreshButton) refreshButton.disabled = true;
        showMessage("Обновляю состояние источников…", "muted");
        try {
            const results = await Promise.allSettled([
                request("/api/health"),
                request("/api/metrics"),
                request("/api/admin/alchemy/stats"),
            ]);
            const health = results[0].status === "fulfilled" ? results[0].value : {};
            const metrics = results[1].status === "fulfilled" ? results[1].value : {};
            const onchainData = results[2].status === "fulfilled" ? results[2].value : {};
            const marketError = results[0].status === "rejected" ? String(results[0].reason.message || results[0].reason) : "";
            const metricsError = results[1].status === "rejected" ? String(results[1].reason.message || results[1].reason) : "";
            const networkError = results[2].status === "rejected"
                ? String(results[2].reason.message || results[2].reason)
                : (onchainData.vault_error || "");

            const market = marketError
                ? (marketRows.replaceChildren(), [])
                : renderMarket(health.sources || {}, health);
            if (marketError) {
                const row = document.createElement("tr");
                cell(row, "Не удалось получить /api/health: " + marketError, "diag-empty");
                row.firstChild.colSpan = 6;
                marketRows.append(row);
            }
            const networks = renderOnchain(
                Array.isArray(onchainData.networks) ? onchainData.networks : [],
                Number(health.server_time) || Date.now() / 1000,
                networkError ? "Статус сетей недоступен: " + networkError : "API не вернул список сетей"
            );
            renderRuntime(health, metrics);
            renderSummary(market, networks, health, metrics, networkError);
            const parts = [];
            if (marketError) parts.push("ошибка health API");
            if (metricsError) parts.push("ошибка metrics API");
            if (networkError) parts.push("ошибка статуса скринера");
            const now = new Date();
            updated.textContent = "Обновлено " + now.toLocaleTimeString();
            showMessage(parts.length ? "Часть диагностики недоступна: " + parts.join("; ") : "Источники проверены", parts.length ? "warn" : "ok");
        } catch (error) {
            showMessage("Не удалось обновить диагностику: " + String(error && error.message || error), "bad");
        } finally {
            loading = false;
            if (refreshButton) refreshButton.disabled = false;
        }
    }

    async function enableForAdmin() {
        try {
            const data = await request("/api/auth/me");
            if (!data.user || !data.user.is_admin) {
                card.remove();
                return;
            }
            card.hidden = false;
            await refresh();
            timer = window.setInterval(() => {
                if (!document.hidden) refresh();
            }, 15000);
        } catch (_) {
            card.remove();
        }
    }

    if (refreshButton) refreshButton.addEventListener("click", refresh);
    enableForAdmin();
})();
