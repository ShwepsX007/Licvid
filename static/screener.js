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
    // Биржи для графиков потоков приходят с сервера вместе с данными: карточка
    // существует только там, где за сутки был реальный inflow/outflow. Порядок
    // карточек закрепляем на клиенте (venueOrder), чтобы клетки не прыгали при
    // каждом обновлении, когда объёмы меняются местами.
    const networkOpacity = index => Math.max(.48, .96 - index * .07);
    // Что оставил включённым админ (тумблеры сетей в `/admin`). null = сервер
    // ничего не сказал → показываем все сети, как раньше; пустой список — это
    // тоже ответ («все выключены»), и он легален.
    let enabledNetworks = null;
    const visibleNetworks = () => (enabledNetworks
        ? NETWORKS.filter(net => enabledNetworks.includes(net.id))
        : NETWORKS.slice());
    function applyEnabledNetworks(list) {
        if (!Array.isArray(list)) return;
        enabledNetworks = list.map(item => String(item || "").toUpperCase()).filter(Boolean);
        if (state.selectedChain !== "ALL" && !enabledNetworks.includes(state.selectedChain)) {
            // ссылку на выключенную сеть могли принести из рассылки или закладки
            state.selectedChain = "ALL";
        }
        getChainOptions();
        renderTabs();
        if (state.stats) {
            // карточки, счётчик и график перестраиваются сразу: сеть, которую
            // админ заглушил, не должна остаться столбиком в стеке до
            // следующего опроса
            renderOverview();
            renderChart();
        }
    }
    const state = {
        stats: null, liveRows: [], hlRows: [], selectedChain: "ALL", historyPage: 1,
        historyPages: 1, sortBy: "timestamp", sortDir: "desc",
        seenLive: new Set(), seenHL: new Set(), initializedLive: false,
        initializedHL: false, historyRequest: 0,
        venueFlows: null,       // {venues: [{id, name, …}], series: {id: [{…}]}}
        venueOrder: [],         // закреплённый порядок бирж между обновлениями
        venueRequest: 0,
        rankings: null,         // ответ /api/screener/whales/rankings
        rankMetrics: {wallets: "usd", tokens: "usd", networks: "usd"},
        rankRequest: 0,
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
    function localizedStatus(status, retryInSec = 0) {
        if (status === "online") return t("screener.status_online");
        if (status === "rate_limited") {
            return t("screener.status_rate_limited", {seconds: fmtNumber(retryInSec)});
        }
        if (status === "auth_error") return t("screener.status_auth_error");
        if (status === "quota_exhausted") return t("screener.status_quota_exhausted");
        if (status === "error") return t("screener.status_error");
        if (status === "network_error") return t("screener.status_network_error");
        if (status === "paused") return t("screener.status_paused");
        return t("screener.status_waiting");
    }
    function getChainOptions() {
        const select = $("whale-chain");
        if (!select) return;
        select.replaceChildren();
        select.add(new Option(t("screener.network_all"), "ALL"));
        visibleNetworks().forEach(net => select.add(new Option(networkName(net.id), net.id)));
        select.value = state.selectedChain;
    }

    function renderOverview() {
        const data = state.stats;
        if (!data) return;
        setText("overview-events", fmtNumber(data.total_events));
        setText("overview-volume", fmtUSD(data.total_volume_usd, true));
        const shown = visibleNetworks();
        setText("overview-online", fmtNumber(data.active_networks) + "/" + shown.length);
        setText("screener-updated", t("screener.updated_at", {time: new Date().toLocaleTimeString()}));

        const networks = data.networks || {};
        const grid = $("network-cards");
        grid.replaceChildren();
        shown.forEach(net => {
            const summary = networks[net.id] || {};
            if (summary.enabled === false) return;   // сеть выключена в админке
            const card = make("button", "network-card" + (state.selectedChain === net.id ? " is-selected" : ""));
            card.type = "button";
            card.dataset.network = net.id;
            card.setAttribute("aria-pressed", String(state.selectedChain === net.id));
            const warningStatus = String(summary.warning_status || "");
            const statusLabel = localizedStatus(summary.status, summary.retry_in_sec || 0);
            const warningLabel = warningStatus
                ? localizedStatus(warningStatus, summary.retry_in_sec || 0) : "";
            const diagnosticParts = [];
            if (warningLabel) diagnosticParts.push(warningLabel);
            else if (summary.status && summary.status !== "online") diagnosticParts.push(statusLabel);
            if (summary.http_status && summary.http_status !== 200) {
                diagnosticParts.push("HTTP " + summary.http_status);
            }
            if (summary.rpc_code !== undefined && summary.rpc_code !== null) {
                diagnosticParts.push("JSON-RPC code=" + summary.rpc_code);
            }
            const errorDetail = String(summary.error || "").trim();
            if (errorDetail) diagnosticParts.push(errorDetail);
            const diagnostic = diagnosticParts.join(" · ");
            const fullStatus = statusLabel +
                (warningLabel && summary.status === "online" ? " · " + warningLabel : "");
            const visualStatus = warningStatus === "rate_limited" && summary.status === "online"
                ? "rate_limited" : (summary.status || "waiting");
            card.title = diagnostic;
            card.setAttribute("aria-label", networkName(net.id) + ", " +
                fullStatus + (diagnostic ? ". " + diagnostic : ""));
            const top = make("div", "network-card-top");
            top.append(make("span", "network-card-name", networkName(net.id)));
            const status = make("span", "network-state status--" + visualStatus, fullStatus);
            if (diagnostic) status.title = diagnostic;
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
            visibleNetworks().map(net => ({id: net.id, label: networkName(net.id)})));
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
                const visible = visibleNetworks();
                const active = visible[index] && visible[index].id === chain;
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
        const bucketHours = 6;
        for (let start = 0; start < series.length; start += bucketHours) {
            const rows = series.slice(start, start + bucketHours);
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
            visibleNetworks().forEach((net, index) => {
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
        const stackChains = selected === "ALL"
            ? visibleNetworks().map(net => net.id)
            : [selected];
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
        const labelEvery = points.length <= 6 ? 1 : 3;
        const groupW = plotW / Math.max(points.length, 1);
        // Match the familiar Chart.js bar/category proportions while the SVG
        // keeps each six-hour bucket readable at desktop and mobile widths.
        const categoryPercentage = 0.85;
        const barPercentage = 0.85;
        const usable = groupW * categoryPercentage;
        const barW = usable / keys.length;
        const rectW = Math.max(3, barW * barPercentage);
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
                    const x = xStart + j * barW + (barW - rectW) / 2;
                    const rect = svgNode("rect", {
                        x, y: yBottom, width: rectW, height,
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
                if (total > 0 && stackHeight >= 22 && barW >= 32) {
                    const label = svgNode("text", {
                        x: labelX, y: Math.max(9, yBottom - 3),
                        "text-anchor": "middle", class: "chart-value-label",
                    }, fmtUSD(total, true));
                    label.append(svgNode("title", {}, compactAssets(assetTotals)));
                    chart.append(label);
                }
                if (selected !== "ALL" && total > 0 && stackHeight >= 36 && barW >= 32) {
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
            if (i % labelEvery === 0 || i === points.length - 1) {
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

    /** Сумма потоков биржи за сутки: сколько пришло, ушло и что в остатке. */
    function venueTotals(points) {
        let inflow = 0, outflow = 0;
        (points || []).forEach(point => {
            inflow += Number(point.inflow || 0);
            outflow += Number(point.outflow || 0);
        });
        // balance — то, что рисует кривая: сколько за сутки зашло минус сколько
        // вышло. Положительный — биржа накопила (на неё пришло больше).
        return {inflow: inflow, outflow: outflow, balance: inflow - outflow};
    }

    /** Один график биржи: столбцы inflow вниз / outflow вверх и линия Net Flow.
     *
     *  Рисуем вручную SVG-путями (как и остальные графики Скринера): у клетки
     *  144 точки, поэтому на столбцы уходит ровно две фигуры и одна линия, а
     *  не сотни узлов. Сетку не рисуем — только нулевую линию: она общая для
     *  столбцов и Net Flow. Столбцы масштабированы по максимуму валового
     *  потока, линия — по своему максимуму (иначе Net Flow визуально слилась
     *  бы с нулём).
     */
    /** Компактная сумма со знаком: «+$150K», «−$50K», «$0». */
    function fmtSignedUSD(value) {
        const n = Number(value) || 0;
        if (Math.abs(n) < 0.5) return "$0";
        return (n > 0 ? "+" : "−") + fmtUSD(Math.abs(n), true);
    }

    /** Накопленный поток биржи за сутки: сколько зашло минус сколько вышло.
     *
     *  Точка кривой — «сколько денег на бирже относительно начала окна»:
     *  приток поднимает её, отток опускает. Ровно так читается пример «зашло
     *  100K — выросло, вышло 50K — опустилось»: видна динамика движения
     *  средств за 24 часа, а не отдельные столбцы.
     */
    function venueSeries(points) {
        const rows = points || [];
        if (!rows.length) return [];
        const step = rows.length > 1
            ? Math.max(1, Number(rows[1].timestamp) - Number(rows[0].timestamp)) : 600;
        const series = [{timestamp: Number(rows[0].timestamp) - step, value: 0}];
        let acc = 0;
        rows.forEach(point => {
            acc += (Number(point.inflow) || 0) - (Number(point.outflow) || 0);
            series.push({timestamp: Number(point.timestamp), value: acc});
        });
        return series;
    }

    /** Кривая накопленного потока по участкам одного направления. */
    function venueSegments(series, xAt, yAt) {
        const segments = [];
        for (let i = 1; i < series.length; i++) {
            const delta = series[i].value - series[i - 1].value;
            const dir = delta > 0 ? 1 : delta < 0 ? -1 : 0;
            const previous = [xAt(i - 1), yAt(series[i - 1].value)];
            const point = [xAt(i), yAt(series[i].value)];
            const last = segments[segments.length - 1];
            if (last && last.dir === dir) {
                last.pts.push(point);
                last.to = i;
            } else {
                segments.push({dir: dir, from: i - 1, to: i, pts: [previous, point]});
            }
        }
        return segments;
    }

    /** Один график биржи — как биржевой график в терминале: сплошная кривая
     *  накопленного потока, зелёные участки — деньги заходят на биржу,
     *  красные — выходят (в терминале те же цвета у свечей вверх/вниз), по
     *  бокам суммы, по низу — время. Столбцов нет: важно движение за сутки.
     *
     *  Рисуем вручную SVG-путями, как и остальные графики Скринера: 144 точки
     *  превращаются в несколько путей (участки одного направления), а не в
     *  сотни узлов. fillId — свой градиент заливки на каждую клетку (общий id
     *  заставил бы все графики взять первую заливку). false — данных нет.
     */
    function drawVenueChart(svg, points, fillId) {
        svg.replaceChildren();
        const series = venueSeries(points);
        if (series.length < 2) return false;
        const W = 320, H = 200, left = 48, right = 10, top = 10, bottom = 22;
        const plotW = W - left - right, plotH = H - top - bottom;
        let low = 0, high = 0;
        series.forEach(point => {
            low = Math.min(low, point.value);
            high = Math.max(high, point.value);
        });
        if (!(high > 0) && !(low < 0)) return false;      // за сутки потоков не было
        const span = Math.max(high - low, 1);
        const pad = span * 0.12;
        const yMax = high + pad, yMin = low - pad;
        const scaleY = plotH / (yMax - yMin);
        const xAt = index => left + plotW * (index / (series.length - 1));
        const yAt = value => top + (yMax - value) * scaleY;

        if (fillId) {
            const defs = svgNode("defs", {});
            const gradient = svgNode("linearGradient", {id: fillId,
                x1: "0", y1: "0", x2: "0", y2: "1"});
            gradient.append(svgNode("stop", {offset: "0", "stop-color": "#78a0ff",
                                             "stop-opacity": "0.26"}));
            gradient.append(svgNode("stop", {offset: "1", "stop-color": "#78a0ff",
                                             "stop-opacity": "0"}));
            defs.append(gradient);
            svg.append(defs);
        }

        // Три линии по суммам: верх, ноль (пунктиром) и низ.
        [yMax, 0, yMin].forEach(value => {
            const y = yAt(value);
            const zero = Math.abs(value) < 1e-9;
            svg.append(svgNode("line", {x1: left, y1: y.toFixed(2), x2: W - right,
                                        y2: y.toFixed(2), class: zero ? "venue-zero" : "venue-grid-line"}));
            svg.append(svgNode("text", {x: left - 6, y: (y + 3.5).toFixed(2),
                                        "text-anchor": "end", class: "venue-axis-label"},
                               fmtSignedUSD(value)));
        });

        // Площадь под кривой — как у биржевого графика: заливка от линии вниз.
        const area = series.map((point, index) =>
            (index ? "L" : "M") + xAt(index).toFixed(2) + " " + yAt(point.value).toFixed(2))
            .join(" ");
        svg.append(svgNode("path", {
            d: area + " L" + (W - right).toFixed(2) + " " + (H - bottom).toFixed(2) +
               " L" + left.toFixed(2) + " " + (H - bottom).toFixed(2) + " Z",
            class: "venue-area", fill: fillId ? "url(#" + fillId + ")" : "rgba(120,160,255,0.12)"}));

        const classByDir = {"1": "is-up", "-1": "is-down", "0": "is-flat"};
        venueSegments(series, xAt, yAt).forEach(segment => {
            const path = svgNode("path", {
                d: segment.pts.map((pt, index) =>
                    (index ? "L" : "M") + pt[0].toFixed(2) + " " + pt[1].toFixed(2)).join(" "),
                class: "venue-seg " + classByDir[String(segment.dir)]});
            const delta = series[segment.to].value - series[segment.from].value;
            const from = timeLabel(series[segment.from].timestamp, {hour: "2-digit", minute: "2-digit"});
            const to = timeLabel(series[segment.to].timestamp, {hour: "2-digit", minute: "2-digit"});
            path.append(svgNode("title", {}, from + "–" + to + " · " +
                (delta >= 0 ? t("screener.venue_in") : t("screener.venue_out")) + " " +
                fmtUSD(Math.abs(delta), true)));
            svg.append(path);
        });

        // Последняя точка — как отметка текущей цены у биржевого графика.
        const last = series[series.length - 1];
        svg.append(svgNode("circle", {cx: xAt(series.length - 1).toFixed(2),
                                      cy: yAt(last.value).toFixed(2), r: 2.6,
                                      class: "venue-dot " + (last.value >= 0 ? "is-up" : "is-down")}));

        // Время по низу: четыре подписи — читается и на телефоне.
        const ticks = [0, Math.round((series.length - 1) / 3),
                       Math.round(2 * (series.length - 1) / 3), series.length - 1];
        ticks.forEach((index, position) => {
            const point = series[index];
            if (!point) return;
            svg.append(svgNode("text", {
                x: xAt(index).toFixed(2), y: H - 6,
                "text-anchor": position === 0 ? "start" : position === ticks.length - 1 ? "end" : "middle",
                class: "venue-axis-label",
            }, timeLabel(point.timestamp, {hour: "2-digit", minute: "2-digit"})));
        });
        const totals = venueTotals(points);
        svg.append(svgNode("title", {}, t("screener.venue_in") + " " +
            fmtUSD(totals.inflow, true) + " · " + t("screener.venue_out") + " " +
            fmtUSD(totals.outflow, true) + " · " + fmtSignedUSD(totals.balance)));
        return true;
    }

    /** Карточки потоков по биржам. Биржу без событий просто не рисуем, а если
     *  за сутки их нет ни у одной площадки — показываем одно общее сообщение. */
    function renderVenueFlows() {
        const grid = $("venue-charts");
        if (!grid) return;
        grid.replaceChildren();
        const data = state.venueFlows;
        const all = (data && Array.isArray(data.venues)) ? data.venues : [];
        const series = (data && data.series) || {};
        const known = state.venueOrder.length
            ? all.filter(venue => state.venueOrder.includes(venue.id))
                .sort((a, b) => state.venueOrder.indexOf(a.id) - state.venueOrder.indexOf(b.id))
            : [];
        const fresh = all.filter(venue => !state.venueOrder.includes(venue.id));
        const venues = known.concat(fresh);
        state.venueOrder = venues.map(venue => venue.id);
        if (!venues.length) {
            grid.append(make("p", "venue-empty", t("screener.venue_flow_empty")));
            return;
        }
        venues.forEach(venue => {
            const points = Array.isArray(series[venue.id]) ? series[venue.id] : null;
            const totals = venueTotals(points);
            const card = make("div", "venue-card");
            card.dataset.venue = venue.id;
            const head = make("div", "venue-card-head");
            head.append(make("span", "venue-name",
                venue.name + " " + t("screener.period_24h")));
            const hasFlow = totals.inflow > 0 || totals.outflow > 0;
            const net = make("span", "venue-net",
                hasFlow ? fmtSignedUSD(totals.balance) : "—");
            if (hasFlow) net.classList.add(totals.balance >= 0 ? "is-inflow" : "is-outflow");
            head.append(net);
            card.append(head);
            const svg = svgNode("svg", {viewBox: "0 0 320 200", class: "venue-chart",
                role: "img",
                "aria-label": venue.name + " " + t("screener.net_flow")});
            if (points && drawVenueChart(svg, points, "venue-fill-" + venue.id)) {
                card.append(svg);
                card.append(make("div", "venue-totals",
                    t("screener.venue_in") + " " + fmtUSD(totals.inflow, true) +
                    " · " + t("screener.venue_out") + " " + fmtUSD(totals.outflow, true) +
                    " · " + t("screener.exchange_events", {count: fmtNumber(venue.events || 0)})));
            } else {
                card.append(make("p", "venue-empty", t("screener.venue_flow_empty")));
            }
            grid.append(card);
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
        const chainName = networkName(row.chain);
        const chainNameNode = make("div", "event-chain-name", chainName);
        chainNameNode.title = chainName;
        networkNameBlock.append(chainNameNode);
        const sourceMarker = row.source === "historical" ? "🕒" : "⚡";
        const eventTime = make("div", "event-time", timeLabel(row.timestamp) + "  " + sourceMarker);
        eventTime.title = eventTime.textContent + " · " +
            (row.source === "historical" ? t("screener.source_history") : t("screener.source_realtime"));
        networkNameBlock.append(eventTime);
        network.append(networkNameBlock);

        const direction = make("span", "direction-badge event-direction " + directionClass(row));
        const prefix = row.direction === "inflow" ? "↓ " : row.direction === "outflow" ? "↑ " : "";
        direction.textContent = prefix + directionLabel(row);
        direction.title = direction.textContent;

        const assetBlock = make("div", "event-asset-block");
        const assetLabel = (row.symbol || "") + " · " + fmtAmount(row.amount);
        const asset = make("div", "event-asset", assetLabel);
        asset.title = assetLabel;
        assetBlock.append(asset);
        const quantity = make("div", "event-amount", t("screener.asset_quantity"));
        quantity.title = quantity.textContent;
        assetBlock.append(quantity);
        const valueText = fmtUSD(row.usd);
        const value = make("div", "event-value", valueText);
        value.title = valueText;
        const route = make("div", "event-route");
        const from = addressLabel(row.from, row.from_label);
        const to = addressLabel(row.to, row.to_label);
        route.title = row.direction === "trade"
            ? t("screener.buyer") + " " + from + " ↔ " + t("screener.seller") + " " + to
            : from + " → " + to;
        if (row.direction === "trade") {
            route.append(document.createTextNode(t("screener.buyer") + " "));
            const fromNode = make("strong", "", from);
            fromNode.title = from;
            route.append(fromNode);
            route.append(document.createTextNode("  ↔  " + t("screener.seller") + " "));
            const toNode = make("strong", "", to);
            toNode.title = to;
            route.append(toNode);
        } else {
            const fromNode = make("strong", "", from);
            fromNode.title = from;
            route.append(fromNode);
            route.append(document.createTextNode("  →  "));
            const toNode = make("strong", "", to);
            toNode.title = to;
            route.append(toNode);
        }
        const details = make("div", "event-details");
        details.append(assetBlock, route);
        const href = explorerLink(row);
        const link = make("a", "explorer-link", t("screener.open_explorer") + " ↗");
        if (href) {
            link.href = href;
            link.title = href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
        } else {
            link.removeAttribute("href");
            link.title = t("screener.open_explorer");
            link.setAttribute("aria-disabled", "true");
        }
        item.append(network, direction, details, value, link);
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
    /** Ответ /exchanges_24h → {venues, series}. Старая форма («биржа → точки»
     *  без обёртки) тоже разбирается: кэш браузера может отдать JS новее
     *  сервера и наоборот, а пустой блок графиков — худший из вариантов. */
    function normalizeVenueFlows(data) {
        if (!data || typeof data !== "object") return null;
        if (Array.isArray(data.venues)) {
            const series = data.series || {};
            const venues = data.venues
                .filter(venue => venue && venue.id && Array.isArray(series[venue.id]))
                .map(venue => ({
                    id: String(venue.id),
                    name: venue.name || String(venue.id).replace(/[_-]/g, " "),
                    inflow: Number(venue.inflow || 0),
                    outflow: Number(venue.outflow || 0),
                    balance: Number(venue.balance || 0),
                    events: Number(venue.events || 0),
                }));
            return {venues: venues, series: series};
        }
        const venues = [];
        const series = {};
        Object.keys(data).forEach(id => {
            const points = data[id];
            if (!Array.isArray(points)) return;
            let inflow = 0, outflow = 0;
            points.forEach(point => {
                inflow += Number(point.inflow || 0);
                outflow += Number(point.outflow || 0);
            });
            if (!(inflow > 0 || outflow > 0)) return;      // пустых клеток нет
            series[id] = points;
            venues.push({id: id, name: id.replace(/[_-]/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
                inflow: inflow, outflow: outflow, balance: inflow - outflow, events: points.length});
        });
        venues.sort((a, b) => (b.inflow + b.outflow) - (a.inflow + a.outflow));
        return {venues: venues, series: series};
    }

    async function fetchVenueFlows() {
        const sequence = ++state.venueRequest;
        try {
            const response = await fetch("/api/screener/stats/exchanges_24h?minutes=10",
                {credentials: "same-origin", cache: "no-store"});
            if (response.status === 401) return goToLogin();
            if (!response.ok) throw new Error("HTTP " + response.status);
            const data = await response.json();
            if (sequence !== state.venueRequest) return;
            state.venueFlows = normalizeVenueFlows(data);
        } catch (_) {
            if (sequence !== state.venueRequest) return;
            state.venueFlows = null;
        }
        renderVenueFlows();
    }

    async function fetchStats() {
        try {
            const response = await fetch("/api/screener/stats", {credentials: "same-origin", cache: "no-store"});
            if (response.status === 401) return goToLogin();
            if (!response.ok) throw new Error("HTTP " + response.status);
            state.stats = await response.json();
            applyEnabledNetworks(state.stats.enabled_networks);
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
    // --- Рейтинги окна: топы по деньгам и по скорости ----------------------
    const RANK_METRICS = {
        events: ["usd"],
        inflow: ["usd"],
        outflow: ["usd"],
        wallets: ["usd", "usd_per_min"],
        tokens: ["usd", "usd_per_min", "usd_vs_vol24h"],
        networks: ["usd", "usd_per_min"],
    };

    function rankMetricValue(row, metric) {
        if (metric === "usd") return Number(row.usd) || 0;
        if (metric === "usd_per_min") return Number(row.usd_per_min) || 0;
        if (metric === "usd_vs_vol24h") return Number(row.usd_vs_vol24h) || 0;
        return 0;
    }

    function fmtRate(value) {
        const n = Number(value);
        if (!Number.isFinite(n) || n <= 0) return "—";
        if (n >= 1_000_000) return "$" + (n / 1_000_000).toFixed(1) + "M/m";
        if (n >= 1_000) return "$" + Math.round(n / 1_000) + "K/m";
        return "$" + n.toFixed(0) + "/m";
    }

    function renderRankSortButtons() {
        document.querySelectorAll(".rank-sort").forEach(box => {
            const group = box.getAttribute("data-metric-group");
            box.replaceChildren();
            (RANK_METRICS[group] || ["usd"]).forEach(metric => {
                const btn = make("button", "rank-sort-btn", t("screener.rankings_sort_" +
                    (metric === "usd" ? "usd" : metric === "usd_per_min" ? "speed" : "vol")));
                btn.type = "button";
                if (state.rankMetrics[group] === metric) btn.classList.add("active");
                btn.addEventListener("click", () => {
                    state.rankMetrics[group] = metric;
                    renderRankings();
                });
                box.appendChild(btn);
            });
        });
    }

    function rankRow(item, group) {
        const li = make("li", "rank-row");
        const metric = state.rankMetrics[group] || "usd";
        const main = make("div", "rank-main");
        const label = make("span", "rank-label");
        if (group === "events" || group === "inflow" || group === "outflow") {
            label.textContent = timeLabel(item.timestamp, {month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit"});
        } else if (group === "wallets") {
            label.textContent = networkName(item.chain) + " · " + addressLabel(item.address);
        } else if (group === "tokens") {
            label.textContent = String(item.key || "—");
        } else {
            label.textContent = networkName(item.key);
        }
        main.appendChild(label);
        const value = make("span", "rank-value", group === "events" || group === "inflow" || group === "outflow"
            ? (item.symbol ? item.symbol + " " : "") + fmtUSD(item.usd, true)
            : fmtUSD(item.usd, true));
        main.appendChild(value);
        li.appendChild(main);
        const meta = make("div", "rank-meta");
        if (group === "events" || group === "inflow" || group === "outflow") {
            meta.textContent = networkName(item.chain) +
                (exchangeFor(item) ? " · " + exchangeFor(item) : "") +
                (item.direction ? " · " + t("screener.direction_" + item.direction) : "");
        } else {
            const bits = [];
            if (metric === "usd_per_min") bits.push(fmtRate(item.usd_per_min));
            if (metric === "usd_vs_vol24h") {
                bits.push(Number(item.usd_vs_vol24h) > 0
                    ? "×" + Number(item.usd_vs_vol24h).toLocaleString(undefined, {maximumFractionDigits: 2}) + " " + t("screener.rankings_sort_vol").replace(/^\$\s*\/\s*/, "")
                    : "—");
            }
            const net = Number(item.net_usd) || 0;
            bits.push((net >= 0 ? "net ↑ " : "net ↓ ") + fmtUSD(Math.abs(net), true));
            bits.push(fmtNumber(item.n) + "×");
            meta.textContent = bits.join(" · ");
        }
        li.appendChild(meta);
        return li;
    }

    function renderRankings() {
        renderRankSortButtons();
        const data = state.rankings;
        const empty = $("rankings-empty");
        const groups = {
            events: data ? data.events : [],
            inflow: data ? data.inflow : [],
            outflow: data ? data.outflow : [],
            wallets: data ? data.wallets : [],
            tokens: data ? data.tokens : [],
            networks: data ? data.networks : [],
        };
        let total = 0;
        Object.keys(groups).forEach(group => {
            const list = $("rank-" + group);
            if (!list) return;
            const metric = state.rankMetrics[group] || "usd";
            const sorted = groups[group].slice()
                .sort((a, b) => rankMetricValue(b, metric) - rankMetricValue(a, metric))
                .slice(0, 10);
            total += sorted.length;
            const frag = document.createDocumentFragment();
            sorted.forEach(item => frag.appendChild(rankRow(item, group)));
            list.replaceChildren(frag);
        });
        if (empty) empty.hidden = total > 0;
    }

    async function fetchRankings() {
        const seq = ++state.rankRequest;
        const hours = $("rank-window") ? $("rank-window").value : "24";
        const minUsd = $("rank-min") ? $("rank-min").value : "100000";
        try {
            const res = await fetch("/api/screener/whales/rankings?window_hours=" +
                encodeURIComponent(hours) + "&min_usd=" + encodeURIComponent(minUsd),
                {credentials: "same-origin"});
            if (res.status === 401) {
                state.rankings = null;
                if (seq === state.rankRequest) {
                    const empty = $("rankings-empty");
                    if (empty) {
                        empty.hidden = false;
                        empty.textContent = t("screener.members_only");
                    }
                    renderRankings();
                }
                return;
            }
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            if (seq !== state.rankRequest) return;
            state.rankings = data && data.available ? data : null;
            const empty = $("rankings-empty");
            if (empty) empty.textContent = t("screener.rankings_empty");
            renderRankings();
        } catch (_) {
            // сеть/сервер недоступны: список оставляем как был — «нет событий»
            // означало бы, что мы получили пустой ответ, а это не так
            if (seq === state.rankRequest && state.rankings) renderRankings();
        }
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
        ["rank-min", "rank-window"].forEach(id => {
            const el = $(id);
            if (el) el.addEventListener("change", fetchRankings);
        });
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
                renderVenueFlows();
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
        fetchVenueFlows();
        fetchRankings();
        window.setInterval(() => { if (!document.hidden) fetchLive(); }, 9000);
        window.setInterval(() => { if (!document.hidden) fetchStats(); }, 30000);
        // Пока вкладка спрятана, интервалы пропускаются — при возвращении
        // догоняем графики бирж сразу, не дожидаясь следующего цикла.
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) return;
            // вернулись во вкладку — список сетей и потоки бирж должны быть
            // свежими, а не «до следующего получаса»
            fetchStats();
            fetchVenueFlows();
        });
        window.setInterval(() => { if (!document.hidden) fetchHistory(); }, 60000);
        // Потоки по биржам меняются медленно, но карточки строятся по данным:
        // раз в минуту — компромисс между живостью и одним лёгким запросом.
        window.setInterval(() => { if (!document.hidden) fetchVenueFlows(); }, 60000);
        // рейтинги пересобираются на сервере из окна событий — минуты достаточно
        window.setInterval(() => { if (!document.hidden) fetchRankings(); }, 60000);
    }
    document.addEventListener("DOMContentLoaded", init, {once: true});
})();
