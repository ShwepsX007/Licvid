(() => {
    "use strict";

    const $ = id => document.getElementById(id);
    const SVG_NS = "http://www.w3.org/2000/svg";
    const NETWORKS = [
        {id: "ETH", key: "screener.network_eth", short: "ETH", mark: "Ξ"},
        {id: "BNB", key: "screener.network_bnb", short: "BNB", mark: "B"},
        {id: "POLYGON", key: "screener.network_polygon", short: "POLYGON", mark: "P"},
        {id: "ARBITRUM", key: "screener.network_arbitrum", short: "ARB", mark: "A"},
        {id: "BASE", key: "screener.network_base", short: "BASE", mark: "B"},
        {id: "SOLANA", key: "screener.network_solana", short: "SOL", mark: "◎"},
        {id: "TRON", key: "screener.network_tron", short: "TRON", mark: "T"},
        {id: "HYPERLIQUID", key: "screener.network_hyperliquid", short: "HYPE", mark: "H"},
    ];
    const EXPLORERS = {
        ETH: "https://etherscan.io/tx/", BNB: "https://bscscan.com/tx/",
        POLYGON: "https://polygonscan.com/tx/", ARBITRUM: "https://arbiscan.io/tx/",
        BASE: "https://basescan.org/tx/", SOLANA: "https://solscan.io/tx/",
        TRON: "https://tronscan.org/#/transaction/", HYPERLIQUID: "https://hypurrscan.io/tx/",
    };
    const SERIES_COLORS = {
        inflow_usd: "#34d399", outflow_usd: "#fb7185",
        buy_usd: "#78a0ff", sell_usd: "#c084fc", transfer_usd: "#91a2bd",
    };
    const networkOpacity = index => Math.max(.48, .96 - index * .07);
    const state = {
        stats: null, liveRows: [], hlRows: [], selectedChain: "ALL", historyPage: 1,
        historyPages: 1, sortBy: "timestamp", sortDir: "desc",
        seenLive: new Set(), seenHL: new Set(), initializedLive: false,
        initializedHL: false, historyRequest: 0,
    };

    function t(key, vars) {
        try {
            if (window.LiqScopeI18n && typeof window.LiqScopeI18n.t === "function") {
                const value = window.LiqScopeI18n.t(key, vars || {});
                if (value && value !== key) return value;
            }
        } catch (_) {}
        return key;
    }
    function fmtNumber(value, maxDigits = 0) {
        const n = Number(value);
        return Number.isFinite(n) ? n.toLocaleString(undefined, {maximumFractionDigits: maxDigits}) : "0";
    }
    function fmtUSD(value, compact = false) {
        const n = Number(value);
        if (!Number.isFinite(n)) return "$0";
        if (compact && Math.abs(n) >= 1_000_000) return "$" + (n / 1_000_000).toLocaleString(undefined, {maximumFractionDigits: 1}) + "M";
        if (compact && Math.abs(n) >= 10_000) return "$" + (n / 1_000).toLocaleString(undefined, {maximumFractionDigits: 0}) + "K";
        return "$" + n.toLocaleString(undefined, {maximumFractionDigits: n >= 10_000 ? 0 : 2});
    }
    function fmtAmount(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 6}) : "—";
    }
    function networkName(id) {
        const net = NETWORKS.find(item => item.id === id);
        return net ? t(net.key) : (id || "—");
    }
    function timeLabel(ts, options) {
        const date = new Date(Number(ts) * 1000);
        if (Number.isNaN(date.getTime())) return "—";
        return date.toLocaleString(undefined, options || {month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit"});
    }
    function setText(id, value) {
        const node = $(id);
        if (node) node.textContent = value;
    }
    function explorerLink(row) {
        const hash = String(row.hash || "");
        const valid = ["ETH", "BNB", "POLYGON", "ARBITRUM", "BASE", "HYPERLIQUID"].includes(row.chain)
            ? /^0x[0-9a-f]{64}$/i.test(hash)
            : row.chain === "TRON" ? /^[0-9a-f]{64}$/i.test(hash)
            : row.chain === "SOLANA" ? /^[1-9A-HJ-NP-Za-km-z]{64,100}$/.test(hash) : false;
        return valid && EXPLORERS[row.chain] ? EXPLORERS[row.chain] + hash : "";
    }
    function exchangeFor(row) {
        const direction = String(row.direction || "").toLowerCase();
        if (direction === "inflow") return row.to_label || row.from_label || "";
        if (direction === "outflow") return row.from_label || row.to_label || "";
        return row.from_label || row.to_label || "";
    }
    function addressLabel(address, label) {
        if (label) return label;
        const value = String(address || "");
        return value ? value.slice(0, 8) + "…" + value.slice(-5) : "—";
    }
    function make(tag, className, text) {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined && text !== null) el.textContent = String(text);
        return el;
    }
    function localizedStatus(status) {
        if (status === "online") return t("screener.status_online");
        if (status === "error") return t("screener.status_error");
        return t("screener.status_waiting");
    }
    function getChainOptions() {
        const select = $("whale-chain");
        if (!select) return;
        select.replaceChildren();
        select.add(new Option(t("screener.network_all"), "ALL"));
        NETWORKS.forEach(net => select.add(new Option(networkName(net.id), net.id)));
        select.value = state.selectedChain;
    }

    function renderOverview() {
        const data = state.stats;
        if (!data) return;
        setText("overview-events", fmtNumber(data.total_events));
        setText("overview-volume", fmtUSD(data.total_volume_usd, true));
        setText("overview-online", fmtNumber(data.active_networks) + "/" + NETWORKS.length);
        setText("screener-updated", t("screener.updated_at", {time: new Date().toLocaleTimeString()}));

        const networks = data.networks || {};
        const grid = $("network-cards");
        grid.replaceChildren();
        NETWORKS.forEach(net => {
            const summary = networks[net.id] || {};
            const card = make("button", "network-card" + (state.selectedChain === net.id ? " is-selected" : ""));
            card.type = "button";
            card.dataset.network = net.id;
            card.setAttribute("aria-pressed", String(state.selectedChain === net.id));
            card.setAttribute("aria-label", networkName(net.id) + ", " + localizedStatus(summary.status));
            const top = make("div", "network-card-top");
            top.append(make("span", "network-card-name", networkName(net.id)));
            const status = make("span", "network-state status--" + (summary.status || "waiting"), localizedStatus(summary.status));
            top.append(status);
            card.append(top);
            card.append(make("div", "network-card-count", fmtNumber(summary.events || 0)));
            card.append(make("div", "network-card-caption", t("screener.events_24h")));
            card.append(make("div", "network-card-volume", fmtUSD(summary.volume_usd || 0, true)));
            card.addEventListener("click", () => selectChain(net.id));
            grid.append(card);
        });
        renderTabs();
        renderChart();
        renderExchanges();
        renderExchangeFilter();
        renderConnectionStatus();
    }

    function renderTabs() {
        const tabs = $("network-tabs");
        if (!tabs) return;
        tabs.replaceChildren();
        const values = [{id: "ALL", label: t("screener.network_all")}].concat(
            NETWORKS.map(net => ({id: net.id, label: networkName(net.id)})));
        values.forEach(item => {
            const button = make("button", "network-tab" + (state.selectedChain === item.id ? " is-active" : ""), item.label);
            button.type = "button";
            button.setAttribute("aria-pressed", String(state.selectedChain === item.id));
            button.addEventListener("click", () => selectChain(item.id));
            tabs.append(button);
        });
    }

    function selectChain(chain) {
        state.selectedChain = chain;
        const select = $("whale-chain");
        if (select) select.value = chain;
        state.historyPage = 1;
        if (state.stats) {
            const grid = $("network-cards");
            if (grid) grid.querySelectorAll(".network-card").forEach((card, index) => {
                const active = NETWORKS[index] && NETWORKS[index].id === chain;
                card.classList.toggle("is-selected", !!active);
                card.setAttribute("aria-pressed", String(!!active));
            });
            renderTabs();
            renderChart();
            renderExchanges();
            renderExchangeFilter();
        }
        fetchLive();
        fetchHistory();
    }

    function renderConnectionStatus() {
        const el = $("screener-live-status");
        if (!el || !state.stats) return;
        const data = state.stats;
        const online = Number(data.active_networks || 0);
        const states = Object.values(data.networks || {});
        const errors = states.filter(row => row.status === "error").length;
        let status = "waiting", label = t("screener.status_connecting");
        if (online > 0) {
            status = "online";
            label = t("screener.status_live");
        } else if (errors > 0) {
            status = "error";
            label = t("screener.status_attention");
        }
        el.className = "live-status status--" + status;
        setText("screener-live-label", label);
    }

    function svgNode(name, attrs, text) {
        const node = document.createElementNS(SVG_NS, name);
        Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function chartPoints(series) {
        const buckets = [];
        for (let start = 0; start < series.length; start += 2) {
            const rows = series.slice(start, start + 2);
            const networks = {};
            rows.forEach(point => Object.entries(point.networks || {}).forEach(([chain, cell]) => {
                const out = networks[chain] || (networks[chain] = {});
                Object.entries(cell || {}).forEach(([key, value]) => {
                    if (key.endsWith("_assets")) {
                        const target = out[key] || (out[key] = {});
                        Object.entries(value || {}).forEach(([symbol, amount]) => {
                            target[symbol] = Number(target[symbol] || 0) + Number(amount || 0);
                        });
                    } else out[key] = Number(out[key] || 0) + Number(value || 0);
                });
            }));
            buckets.push({timestamp: Number(rows[0].timestamp),
                end: Number(rows[rows.length - 1].timestamp) + 3600, networks});
        }
        return buckets;
    }
    function compactAssets(assets) {
        return Object.entries(assets || {}).filter(([, value]) => Number(value) > 0)
            .sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 3)
            .map(([symbol, value]) => fmtAmount(value) + " " + symbol).join(", ") || "—";
    }
    function compactAssetValue(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return "—";
        if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toLocaleString(undefined, {maximumFractionDigits: 1}) + "M";
        if (Math.abs(n) >= 1_000) return (n / 1_000).toLocaleString(undefined, {maximumFractionDigits: 1}) + "K";
        return fmtAmount(n);
    }
    function renderChart() {
        const data = state.stats;
        const chart = $("flow-chart");
        if (!chart || !data) return;
        chart.replaceChildren();
        const selected = state.selectedChain;
        const tradeOnly = selected === "HYPERLIQUID";
        const keys = selected === "ALL"
            ? ["inflow_usd", "outflow_usd", "transfer_usd", "buy_usd", "sell_usd"]
            : tradeOnly ? ["buy_usd", "sell_usd"]
                : ["inflow_usd", "outflow_usd", "transfer_usd"];
        const labels = {
            inflow_usd: t("screener.legend_inflow"), outflow_usd: t("screener.legend_outflow"),
            buy_usd: t("screener.legend_buy"), sell_usd: t("screener.legend_sell"),
            transfer_usd: t("screener.legend_transfer"),
        };
        const legend = $("chart-legend");
        legend.replaceChildren();
        keys.forEach(key => {
            const item = make("span", "legend-item");
            const swatch = make("i", "legend-swatch");
            swatch.style.background = SERIES_COLORS[key];
            item.append(swatch, document.createTextNode(labels[key]));
            legend.append(item);
        });
        if (selected === "ALL") {
            NETWORKS.forEach((net, index) => {
                const item = make("span", "legend-item network-legend-item");
                const swatch = make("i", "legend-swatch");
                swatch.style.background = SERIES_COLORS.inflow_usd;
                swatch.style.opacity = String(networkOpacity(index));
                item.append(swatch, document.createTextNode(networkName(net.id)));
                legend.append(item);
            });
        }
        setText("flow-title", t(tradeOnly ? "screener.trade_chart_title" : "screener.flow_title"));
        chart.setAttribute("aria-label", t(tradeOnly ? "screener.trade_chart_aria" : "screener.flow_chart_aria"));

        const points = chartPoints(data.series || []);
        const stackChains = selected === "ALL" ? NETWORKS.map(net => net.id) : [selected];
        const maxValue = Math.max(0, ...points.flatMap(point => keys.map(key =>
            stackChains.reduce((sum, chain) => sum + Number((point.networks[chain] || {})[key] || 0), 0))));
        const chartEmpty = $("chart-empty");
        chartEmpty.hidden = maxValue > 0;

        const W = 960, H = 288, left = 58, right = 10, top = 28, bottom = 32;
        const plotW = W - left - right, plotH = H - top - bottom;
        const scaleMax = maxValue > 0 ? maxValue * 1.2 : 1;
        for (let i = 0; i <= 4; i++) {
            const y = top + plotH * (i / 4);
            chart.append(svgNode("line", {x1: left, y1: y, x2: W - right, y2: y, class: "chart-grid-line"}));
            const val = scaleMax * (1 - i / 4);
            chart.append(svgNode("text", {x: left - 8, y: y + 3, "text-anchor": "end", class: "chart-axis-label"}, fmtUSD(val, true)));
        }
        const topExchanges = ((data.top_exchanges || {})[selected === "ALL" ? "ALL" : selected] || []).slice(0, 3);
        const topLabel = topExchanges.length ? topExchanges.map(row =>
            row.name + " " + fmtUSD(row.volume_usd, true)).join(" · ") : t("screener.no_top_exchanges");
        const groupW = plotW / Math.max(points.length, 1);
        const usable = groupW * (keys.length >= 5 ? .94 : .82);
        const barW = usable / keys.length;
        points.forEach((point, i) => {
            const xStart = left + i * groupW + (groupW - usable) / 2;
            keys.forEach((key, j) => {
                let yBottom = top + plotH;
                let total = 0;
                const assetTotals = {};
                stackChains.forEach((chain, chainIndex) => {
                    const cell = point.networks[chain] || {};
                    const value = Number(cell[key] || 0);
                    if (!(value > 0)) return;
                    total += value;
                    const assetKey = key.replace("_usd", "_assets");
                    Object.entries(cell[assetKey] || {}).forEach(([symbol, amount]) => {
                        assetTotals[symbol] = Number(assetTotals[symbol] || 0) + Number(amount || 0);
                    });
                    const height = Math.max(1, value / scaleMax * plotH);
                    yBottom -= height;
                    const x = xStart + j * barW + 1;
                    const rect = svgNode("rect", {
                        x, y: yBottom, width: Math.max(2, barW - 2), height,
                        fill: SERIES_COLORS[key], opacity: networkOpacity(chainIndex),
                        stroke: "#0b1020", "stroke-width": 1, class: "chart-bar", rx: 2,
                    });
                    const daily = selected === "ALL" ? (data.networks || {})[chain] || {}
                        : (data.networks || {})[selected] || {};
                    const tooltip = [
                        timeLabel(point.timestamp, {hour: "2-digit", minute: "2-digit"}) + "–" +
                            timeLabel(point.end, {hour: "2-digit", minute: "2-digit"}),
                        networkName(chain) + " · " + labels[key] + ": " + fmtUSD(value),
                        t("screener.tooltip_tokens") + ": " + compactAssets(cell[assetKey]),
                        t("screener.tooltip_24h") + ": " + fmtUSD(daily[key] || 0),
                        t("screener.tooltip_top3") + ": " + topLabel,
                    ].join("\n");
                    rect.append(svgNode("title", {}, tooltip));
                    chart.append(rect);
                });
                const stackHeight = total / scaleMax * plotH;
                const labelX = xStart + j * barW + barW / 2;
                if (total > 0 && stackHeight >= 22 && barW >= 7) {
                    const label = svgNode("text", {
                        x: labelX, y: Math.max(9, yBottom - 3),
                        "text-anchor": "middle", class: "chart-value-label",
                    }, fmtUSD(total, true));
                    label.append(svgNode("title", {}, compactAssets(assetTotals)));
                    chart.append(label);
                }
                if (selected !== "ALL" && total > 0 && stackHeight >= 36 && barW >= 20) {
                    const primary = Object.entries(assetTotals)
                        .sort((a, b) => Number(b[1]) - Number(a[1]))[0];
                    if (primary) {
                        const assetLabel = svgNode("text", {
                            x: labelX, y: Math.min(H - bottom - 3, yBottom + 13),
                            "text-anchor": "middle", class: "chart-asset-label",
                        }, compactAssetValue(primary[1]) + " " + primary[0]);
                        assetLabel.append(svgNode("title", {}, compactAssets(assetTotals)));
                        chart.append(assetLabel);
                    }
                }
            });
            if (i % 3 === 0 || i === points.length - 1) {
                chart.append(svgNode("text", {
                    x: left + i * groupW + groupW / 2, y: H - 8,
                    "text-anchor": "middle", class: "chart-axis-label",
                }, timeLabel(point.timestamp, {hour: "2-digit", minute: "2-digit"})));
            }
        });
    }

    function exchangeRows() {
        if (!state.stats) return [];
        const table = state.stats.top_exchanges || {};
        return table[state.selectedChain] || [];
    }
    function renderExchanges() {
        const list = $("exchange-list"), empty = $("exchange-empty");
        if (!list || !empty) return;
        const rows = exchangeRows();
        list.replaceChildren();
        empty.hidden = rows.length > 0;
        if (!rows.length) return;
        const max = Math.max(...rows.map(row => Number(row.volume_usd || 0)), 1);
        rows.forEach(row => {
            const item = make("div", "exchange-row");
            const head = make("div", "exchange-row-head");
            head.append(make("span", "exchange-name", row.name));
            head.append(make("span", "exchange-volume", fmtUSD(row.volume_usd, true)));
            const track = make("div", "exchange-track");
            const bar = make("div", "exchange-bar");
            bar.style.width = Math.max(2, Number(row.volume_usd || 0) / max * 100) + "%";
            track.append(bar);
            item.append(head, track, make("div", "exchange-caption", t("screener.exchange_events", {count: fmtNumber(row.events)})));
            list.append(item);
        });
    }

    function renderExchangeFilter() {
        const select = $("whale-exchange");
        if (!select) return;
        const selected = select.value || "ALL";
        const rows = exchangeRows();
        const names = rows.map(row => row.name);
        if (selected !== "ALL" && selected !== "unknown" && !names.includes(selected)) names.unshift(selected);
        select.replaceChildren();
        select.add(new Option(t("screener.exchange_all"), "ALL"));
        select.add(new Option(t("screener.exchange_unknown"), "unknown"));
        names.forEach(name => select.add(new Option(name, name)));
        select.value = names.includes(selected) || selected === "ALL" || selected === "unknown" ? selected : "ALL";
    }

    function getFilters() {
        return {
            chain: $("whale-chain").value || "ALL",
            min_usd: $("whale-min").value || "50000",
            direction: $("whale-direction").value || "ALL",
            exchange: $("whale-exchange").value || "ALL",
        };
    }
    function applyFilters(rows) {
        const filters = getFilters();
        return rows.filter(row => {
            if (Number(row.usd || 0) < Number(filters.min_usd)) return false;
            if (filters.chain !== "ALL" && row.chain !== filters.chain) return false;
            if (filters.direction !== "ALL" && row.direction !== filters.direction) return false;
            const venue = exchangeFor(row);
            if (filters.exchange === "unknown" && venue) return false;
            if (filters.exchange !== "ALL" && filters.exchange !== "unknown" && venue.toLowerCase() !== filters.exchange.toLowerCase()) return false;
            return true;
        });
    }
    function directionLabel(row, short = false) {
        const d = String(row.direction || "").toLowerCase();
        if (d === "inflow") return t(short ? "screener.direction_inflow_short" : "screener.direction_inflow");
        if (d === "outflow") return t(short ? "screener.direction_outflow_short" : "screener.direction_outflow");
        if (d === "trade") return t("screener.direction_trade") + (row.side ? " · " + row.side : "");
        return t("screener.direction_transfer");
    }
    function directionClass(row) {
        if (row.direction === "inflow") return "direction-badge--inflow";
        if (row.direction === "outflow") return "direction-badge--outflow";
        if (row.direction === "trade") return "direction-badge--trade";
        return "";
    }
    function makeEventRow(row, isNew) {
        const item = make("article", "event-row" + (isNew ? " is-new" : ""));
        const net = NETWORKS.find(entry => entry.id === row.chain);
        const network = make("div", "event-network");
        network.append(make("span", "chain-mark chain-mark--" + String(row.chain || "unknown").toLowerCase(),
                            net ? net.mark : "•"));
        const networkNameBlock = make("div", "event-network-copy");
        networkNameBlock.append(make("div", "event-chain-name", networkName(row.chain)));
        const sourceMarker = row.source === "historical" ? "🕒" : "⚡";
        const eventTime = make("div", "event-time", timeLabel(row.timestamp) + "  " + sourceMarker);
        eventTime.title = row.source === "historical" ? t("screener.source_history") : t("screener.source_realtime");
        networkNameBlock.append(eventTime);
        network.append(networkNameBlock);

        const direction = make("span", "direction-badge event-direction " + directionClass(row));
        const prefix = row.direction === "inflow" ? "↓ " : row.direction === "outflow" ? "↑ " : "";
        direction.textContent = prefix + directionLabel(row);

        const assetBlock = make("div", "event-asset-block");
        assetBlock.append(make("div", "event-asset", (row.symbol || "") + " · " + fmtAmount(row.amount)));
        assetBlock.append(make("div", "event-amount", t("screener.asset_quantity")));
        const value = make("div", "event-value", fmtUSD(row.usd));
        const route = make("div", "event-route");
        if (row.direction === "trade") {
            route.append(document.createTextNode(t("screener.buyer") + " "));
            route.append(make("strong", "", addressLabel(row.from, row.from_label)));
            route.append(document.createTextNode("  ↔  " + t("screener.seller") + " "));
            route.append(make("strong", "", addressLabel(row.to, row.to_label)));
        } else {
            route.append(make("strong", "", addressLabel(row.from, row.from_label)));
            route.append(document.createTextNode("  →  "));
            route.append(make("strong", "", addressLabel(row.to, row.to_label)));
        }
        const href = explorerLink(row);
        const link = make("a", "explorer-link", t("screener.open_explorer") + " ↗");
        if (href) {
            link.href = href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
        } else {
            link.removeAttribute("href");
            link.setAttribute("aria-disabled", "true");
        }
        item.append(network, direction, assetBlock, value, route, link);
        return item;
    }
    function renderLive(newKeys = new Set()) {
        const target = $("screener-events"), empty = $("feed-empty");
        if (!target || !empty) return;
        const filtered = applyFilters(state.liveRows);
        target.replaceChildren();
        filtered.slice(0, 50).forEach(row => target.append(makeEventRow(row, newKeys.has(rowKey(row)))));
        empty.hidden = filtered.length > 0;
        setText("feed-count", t("screener.events_count", {count: fmtNumber(filtered.length)}));
    }
    function renderHyperliquid(newKeys = new Set()) {
        const target = $("hl-screener-events"), empty = $("hl-feed-empty");
        if (!target || !empty) return;
        const min = Number($("hl-min").value || 50000);
        const side = String($("hl-direction").value || "ALL").toUpperCase();
        const rows = state.hlRows.filter(row => Number(row.usd || 0) >= min &&
            (side === "ALL" || String(row.side || "").toUpperCase() === side));
        target.replaceChildren();
        rows.slice(0, 50).forEach(row => target.append(makeEventRow(row, newKeys.has(rowKey(row)))));
        empty.hidden = rows.length > 0;
        setText("hl-feed-count", t("screener.events_count", {count: fmtNumber(rows.length)}));
    }
    function rowKey(row) { return [row.chain, row.hash, row.log_index].join(":"); }
    function addQueryFilters(params) {
        const filters = getFilters();
        Object.entries(filters).forEach(([key, value]) => params.set(key, value));
        return params;
    }
    async function fetchLive() {
        const requestRows = async chain => {
            const params = new URLSearchParams({chain, min_usd: "0", limit: "100"});
            const response = await fetch("/api/screener/whales?" + params,
                {credentials: "same-origin", cache: "no-store"});
            if (response.status === 401) throw new Error("HTTP 401");
            if (!response.ok) throw new Error("HTTP " + response.status);
            return response.json();
        };
        try {
            const [general, hyperliquid] = await Promise.all([
                requestRows("GENERAL"), requestRows("HYPERLIQUID"),
            ]);
            const rows = Array.isArray(general.events) ? general.events : [];
            const hlRows = Array.isArray(hyperliquid.events) ? hyperliquid.events : [];
            const newKeys = new Set(), newHLKeys = new Set();
            rows.forEach(row => {
                const key = rowKey(row);
                if (state.initializedLive && !state.seenLive.has(key)) newKeys.add(key);
                state.seenLive.add(key);
            });
            hlRows.forEach(row => {
                const key = rowKey(row);
                if (state.initializedHL && !state.seenHL.has(key)) newHLKeys.add(key);
                state.seenHL.add(key);
            });
            while (state.seenLive.size > 1200) state.seenLive.delete(state.seenLive.values().next().value);
            while (state.seenHL.size > 1200) state.seenHL.delete(state.seenHL.values().next().value);
            state.liveRows = rows;
            state.hlRows = hlRows;
            state.initializedLive = state.initializedHL = true;
            $("feed-error").hidden = true;
            renderLive(newKeys);
            renderHyperliquid(newHLKeys);
        } catch (error) {
            if (error && error.message === "HTTP 401") return goToLogin();
            $("feed-error").hidden = false;
            setText("feed-error", t("screener.load_error"));
        }
    }
    async function fetchStats() {
        try {
            const response = await fetch("/api/screener/stats", {credentials: "same-origin", cache: "no-store"});
            if (response.status === 401) return goToLogin();
            if (!response.ok) throw new Error("HTTP " + response.status);
            state.stats = await response.json();
            renderOverview();
            renderLive();
            renderHyperliquid();
        } catch (_) {
            setText("screener-live-label", t("screener.load_error"));
            const badge = $("screener-live-status");
            if (badge) badge.className = "live-status status--error";
        }
    }

    function makeTableCell(text, className) {
        return make("td", className || "", text);
    }
    function makeHistoryRow(row) {
        const tr = document.createElement("tr");
        const marker = row.source === "historical" ? "🕒" : "⚡";
        const time = makeTableCell(timeLabel(row.timestamp) + " " + marker, "table-time");
        time.title = row.source === "historical" ? t("screener.source_history") : t("screener.source_realtime");
        tr.append(time);
        tr.append(makeTableCell(networkName(row.chain), "table-network"));
        const directionCell = makeTableCell("");
        const dir = make("span", "direction-badge " + directionClass(row), directionLabel(row, true));
        directionCell.append(dir);
        tr.append(directionCell);
        tr.append(makeTableCell((row.symbol || "") + " · " + fmtAmount(row.amount)));
        tr.append(makeTableCell(fmtUSD(row.usd), "table-value"));
        tr.append(makeTableCell(exchangeFor(row) || "—"));
        const txCell = makeTableCell("");
        const href = explorerLink(row);
        if (href) {
            const link = make("a", "table-tx", String(row.hash).slice(0, 10) + "…");
            link.href = href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            txCell.append(link);
        } else txCell.textContent = "—";
        tr.append(txCell);
        return tr;
    }
    async function fetchHistory() {
        const sequence = ++state.historyRequest;
        const params = addQueryFilters(new URLSearchParams({
            page: String(state.historyPage), page_size: "50",
            sort_by: state.sortBy, sort_dir: state.sortDir,
        }));
        try {
            const response = await fetch("/api/screener/history?" + params, {credentials: "same-origin", cache: "no-store"});
            if (response.status === 401) return goToLogin();
            if (!response.ok) throw new Error("HTTP " + response.status);
            const data = await response.json();
            if (sequence !== state.historyRequest) return;
            const body = $("history-rows");
            body.replaceChildren();
            (data.events || []).forEach(row => body.append(makeHistoryRow(row)));
            const count = Number(data.total || 0);
            state.historyPages = Number(data.pages || 1);
            $("history-empty").hidden = count > 0;
            setText("history-count", t("screener.history_count", {count: fmtNumber(count)}));
            setText("history-page-label", t("screener.page_of", {page: data.page || 1, pages: data.pages || 1}));
            $("history-prev").disabled = state.historyPage <= 1;
            $("history-next").disabled = state.historyPage >= state.historyPages;
        } catch (_) {
            if (sequence !== state.historyRequest) return;
            const empty = $("history-empty");
            empty.hidden = false;
            empty.textContent = t("screener.load_error");
        }
    }
    function goToLogin() {
        window.location.assign("/login?next=/screener");
    }
    async function exportCsv() {
        const button = $("history-export");
        button.disabled = true;
        try {
            const params = addQueryFilters(new URLSearchParams());
            const response = await fetch("/api/screener/history.csv?" + params, {credentials: "same-origin", cache: "no-store"});
            if (response.status === 401) return goToLogin();
            if (!response.ok) throw new Error("CSV export failed");
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const anchor = make("a");
            anchor.href = url;
            anchor.download = "whale-history-7d.csv";
            document.body.append(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(url);
        } catch (_) {
            setText("feed-error", t("screener.export_error"));
            $("feed-error").hidden = false;
        } finally {
            button.disabled = false;
        }
    }
    function onFilterChanged() {
        state.historyPage = 1;
        renderLive();
        fetchHistory();
    }
    function bind() {
        getChainOptions();
        ["whale-min", "whale-direction", "whale-exchange"].forEach(id => $(id).addEventListener("change", onFilterChanged));
        ["hl-min", "hl-direction"].forEach(id => $(id).addEventListener("change", () => renderHyperliquid()));
        $("whale-chain").addEventListener("change", () => selectChain($("whale-chain").value));
        $("history-prev").addEventListener("click", () => {
            if (state.historyPage > 1) { state.historyPage -= 1; fetchHistory(); }
        });
        $("history-next").addEventListener("click", () => {
            if (state.historyPage < state.historyPages) { state.historyPage += 1; fetchHistory(); }
        });
        $("history-export").addEventListener("click", exportCsv);
        document.querySelectorAll("[data-sort]").forEach(button => button.addEventListener("click", () => {
            const column = button.getAttribute("data-sort");
            if (state.sortBy === column) state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
            else { state.sortBy = column; state.sortDir = column === "timestamp" ? "desc" : "desc"; }
            state.historyPage = 1;
            fetchHistory();
        }));
        if (window.LiqScopeI18n && typeof window.LiqScopeI18n.onChange === "function") {
            window.LiqScopeI18n.onChange(() => {
                getChainOptions();
                renderOverview();
                renderLive();
                renderHyperliquid();
                fetchHistory();
            });
        }
    }
    function init() {
        if (!(window.LiqScopeI18n && window.LiqScopeI18n.lang)) {
            // The account/nav bundle is independent; allow the page dictionary a turn to initialize.
            window.setTimeout(init, 30);
            return;
        }
        bind();
        fetchStats();
        fetchLive();
        fetchHistory();
        window.setInterval(() => { if (!document.hidden) fetchLive(); }, 9000);
        window.setInterval(() => { if (!document.hidden) fetchStats(); }, 30000);
        window.setInterval(() => { if (!document.hidden) fetchHistory(); }, 60000);
    }
    document.addEventListener("DOMContentLoaded", init, {once: true});
})();
