/**
 * Licvidation Terminal — реальный поток ликвидаций + кластерный график.
 *
 * Важное отличие от прошлой версии: библиотека Lightweight Charts v5
 * больше не имеет методов addCandlestickSeries()/setMarkers() — вызов
 * старого API выбрасывал исключение, из-за чего умирал ВЕСЬ скрипт
 * (не было ни графика, ни списка монет, ни данных). Здесь используется
 * v5 API с автоматическим откатом на v4, если библиотеку заменят.
 */

(function () {
    "use strict";

    // Мультиязычность (i18n.js): ru / en / zh / hi / es
    var I18n = window.LicvidationI18n || {
        t: function (k) { return k; }, lang: function () { return "ru"; },
        set: function () {}, plural: function (n, f) { return f[0]; },
        apply: function () {}, onChange: function () {}, init: function () {},
        number: function (n) { return String(Number(n) || 0); },
        time: function (ts) { return String(ts); }, dateTime: function (ts) { return String(ts); },
    };
    I18n.init();

    const state = {
        symbol: "ALL",          // фильтр ленты: конкретная монета или ALL
        chartSymbol: "",        // монета графика — живёт отдельно от фильтра
        timeframe: 5,
        minUsd: 0,
        exchanges: null,        // null = все биржи включены; иначе Set включённых
        availableExchanges: [],
        customSymbols: [],
        soundEnabled: false,
        profileEnabled: true,   // профиль ликвидаций по ценам (полосы на графике)
        liqEnabled: true,       // шарики ликвидаций на графике
        cvdEnabled: true,       // CVD-стрелки: перевес тейкер-покупок/продаж в свече
        cvdBars: 0,
        symbols: [],
        details: {},
        prices: {},
        liquidations: [],
        candles: [],
        candleSource: "",
        sessionOpen: null,
        demo: false,
        lastTickAt: 0,
        tickCount: 0,
        lagMs: null,
        searchTimer: null,
        searchRequestId: 0,
        lastHealth: null,
        lastStats: null,
        modalItem: null,
    };

    let connState = { status: "pulse yellow", key: "conn.connecting" };

    const MAX_HISTORY = 4000;

    let ws = null;
    let wsReconnectTimer = null;
    let chart = null;
    let candleSeries = null;
    let volumeSeries = null;
    let markersApi = null;
    let audioCtx = null;
    let redrawQueued = false;

    // Хит-тест шариков: координаты и ids событий из последнего drawClusters()
    let clusterBalls = [];        // [{x, y, r, key, ids:[...]}]
    let hoverBallKey = null;      // шарик под курсором (наведение)
    let pinBallKey = null;        // шарик, закреплённый кликом/тапом

    // --- DOM ---------------------------------------------------------------
    const $ = (id) => document.getElementById(id);
    const connStatusEl = $("conn-status");
    const demoBadge = $("demo-badge");
    const exchHealthEl = $("exch-health");
    const symbolTitleEl = $("current-symbol-title");
    const livePriceEl = $("current-price");
    const priceChangeEl = $("price-change-pct");
    const dataSourceEl = $("data-source");
    const feedTbody = $("feed-tbody");
    const feedEmptyEl = $("feed-empty");
    const feedCountEl = $("feed-count");
    const minUsdBtn = $("min-usd-btn");
    const minUsdInput = $("min-usd-input");
    const minUsdApply = $("min-usd-apply");
    const minUsdPresets = $("min-usd-presets");
    const minUsdPanel = $("min-usd-panel");
    const minUsdDd = $("min-usd-dd");
    const exchangeBtn = $("exchange-btn");
    const exchangePanel = $("exchange-panel");
    const exchangeDd = $("exchange-dd");
    const exchListEl = $("exch-list");
    const exchAllCheck = $("exch-all-check");
    const exchNoneCheck = $("exch-none-check");
    const symbolSearchBox = $("symbol-search-box");
    const symbolSearchResults = $("symbol-search-results");
    const symbolHint = $("symbol-hint");
    const soundToggleBtn = $("sound-toggle-btn");
    const soundIcon = $("sound-icon");
    const clearBtn = $("clear-clusters-btn");
    const topCoinsContainer = $("top-coins-list");
    const chartWrapper = $("chart-wrapper");
    const clusterCanvas = $("cluster-canvas");
    const chartSection = document.querySelector(".chart-section");
    const chartToggle = $("chart-toggle");
    const profileToggle = $("profile-toggle");
    const cvdStatEl = $("cvd-stat");
    const langSelect = $("lang-select");
    const symbolButtonsEl = $("symbol-buttons");   // старый контейнер (не используется)
    const symbolCurrentBtn = $("symbol-current");
    const symbolCurrentLabel = $("symbol-current-label");
    const symbolDropdown = $("symbol-dropdown");
    const symbolSearchEl = $("symbol-search");

    const stat24hEl = $("stat-24h-total");
    const stat1hEl = $("stat-1h-total");
    const stat5mEl = $("stat-5m-total");
    const longPctEl = $("long-pct");
    const shortPctEl = $("short-pct");
    const longRatioBar = $("long-ratio-bar");

    const detailModal = $("detail-modal");
    const closeModalBtn = $("close-modal");
    const modalTitle = $("modal-title");
    const modalBody = $("modal-body");

    // --- Утилиты -----------------------------------------------------------
    function fmtUsdShort(v) {
        v = Number(v) || 0;
        if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
        if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
        if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
        return Math.round(v).toString();
    }

    function fmtUsdFull(v) {
        return I18n.number(v);
    }

    function priceDigits(p) {
        p = Math.abs(Number(p) || 0);
        if (p >= 1000) return 2;
        if (p >= 10) return 3;
        if (p >= 1) return 4;
        if (p >= 0.01) return 5;
        return 8;
    }

    function fmtPrice(p) {
        const n = Number(p);
        if (!isFinite(n) || n === 0) return "—";
        return "$" + n.toLocaleString("en-US", {
            minimumFractionDigits: priceDigits(n),
            maximumFractionDigits: priceDigits(n),
        });
    }

    // График не привязан к фильтру ленты: включив «ВСЕ», пользователь видит
    // все ликвидации в эфире, а график остаётся на выбранной монете.
    const chartSymbol = () => state.chartSymbol || state.symbols[0] || "BTC_USDT";
    const pretty = (s) => String(s || "").replace("_", "/");

    // --- Фильтры (мин. объём + биржи) ----------------------------------------
    function exchangeName(e) {
        return String(e || "").charAt(0).toUpperCase() + String(e || "").slice(1);
    }

    function isExchangeOn(name) {
        if (!state.exchanges) return true;            // всё включено
        return state.exchanges.has(name);
    }

    function loadSavedFilters() {
        try {
            const v = localStorage.getItem("licvid.minUsd");
            if (v !== null) state.minUsd = Math.max(0, parseFloat(v) || 0);
        } catch (e) { /* ignore */ }
        try {
            const raw = localStorage.getItem("licvid.exchanges");
            if (raw !== null) {
                if (raw === "*") {
                    state.exchanges = null;               // все биржи включены
                } else {
                    const arr = JSON.parse(raw);
                    if (Array.isArray(arr)) state.exchanges = new Set(arr);
                }
            }
        } catch (e) { /* ignore */ }
    }

    function saveMinUsd() {
        try { localStorage.setItem("licvid.minUsd", String(state.minUsd || 0)); } catch (e) { /* ignore */ }
    }

    function saveExchanges() {
        try {
            localStorage.setItem("licvid.exchanges",
                state.exchanges ? JSON.stringify(Array.from(state.exchanges).sort()) : "*");
        } catch (e) { /* ignore */ }
    }

    function applyFiltersFull() {
        rebuildFeed();
        updateMarkers();
        queueRedraw();
    }

    // --- История ликвидаций: переживает F5 и рестарт сервера -----------------
    // Источник правды — сервер (REST /api/liquidations по паре и фильтрам
    // пользователя, сервер держит её в памяти И на диске). Подстраховка —
    // локальный IndexedDB-кэш на этом устройстве: если сервер перезапустили
    // с пустым файлом, у пользователя всё равно останутся его события.
    const historyLoaded = new Set();          // какие символы уже догружали
    const HISTORY_REST_LIMIT = 2000;          // максимум из REST-истории
    const HISTORY_CACHE_TTL = 24 * 3600;      // сек: сколько держим в браузере

    let liqDb = null;

    function openLiqDb() {
        if (liqDb) return Promise.resolve(liqDb);
        if (!window.indexedDB) return Promise.resolve(null);   // старый браузер
        return new Promise((resolve) => {
            try {
                const req = window.indexedDB.open("licvid-history", 1);
                req.onupgradeneeded = () => {
                    const db = req.result;
                    if (!db.objectStoreNames.contains("liqs")) {
                        const st = db.createObjectStore("liqs", { keyPath: "id" });
                        st.createIndex("symbol", "symbol", { unique: false });
                    }
                };
                req.onsuccess = () => { liqDb = req.result; resolve(liqDb); };
                req.onerror = () => resolve(null);
                req.onblocked = () => resolve(null);
            } catch (e) { resolve(null); }
        });
    }

    function cacheLiquidations(items) {
        if (!items || !items.length) return;
        openLiqDb().then((db) => {
            if (!db) return;
            try {
                const tx = db.transaction("liqs", "readwrite");
                const st = tx.objectStore("liqs");
                items.forEach((it) => { if (it && it.id != null) st.put(it); });
            } catch (e) { /* ignore */ }
        }).catch(() => { /* ignore */ });
    }

    function loadCachedLiquidations(symbol, limit) {
        return openLiqDb().then((db) => {
            if (!db) return [];
            return new Promise((resolve) => {
                try {
                    const since = Date.now() / 1000 - HISTORY_CACHE_TTL;
                    const all = [];
                    const tx = db.transaction("liqs", "readonly");
                    const idx = tx.objectStore("liqs").index("symbol");
                    const req = idx.openCursor(window.IDBKeyRange.only(symbol));
                    req.onsuccess = (e) => {
                        const cur = e.target.result;
                        if (cur) {
                            if ((cur.value.timestamp || 0) >= since) all.push(cur.value);
                            cur.continue();
                        } else {
                            all.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
                            resolve(all.slice(-limit));
                        }
                    };
                    req.onerror = () => resolve([]);
                } catch (e) { resolve([]); }
            });
        }).catch(() => []);
    }

    function mergeLiquidations(rows) {
        if (!rows || !rows.length) return;
        const seen = new Set();
        state.liquidations.forEach((x) => { if (x && x.id != null) seen.add(x.id); });
        let added = 0;
        rows.forEach((it) => {
            if (!it || it.id == null || seen.has(it.id)) return;
            seen.add(it.id);
            state.liquidations.push(it);
            added++;
        });
        if (!added) return;
        state.liquidations.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
        if (state.liquidations.length > MAX_HISTORY) {
            state.liquidations.splice(0, state.liquidations.length - MAX_HISTORY);
        }
    }

    async function loadHistoryFor(sym, force) {
        if (!sym || sym === "ALL") return;
        if (!force && historyLoaded.has(sym)) return;
        historyLoaded.add(sym);
        try {
            // порог и биржи НЕ пробрасываем в REST: пусть клиент фильтрует сам,
            // чтобы при смене фильтра на более мягкий история не «терялась»
            const q = "/api/liquidations?symbol=" + encodeURIComponent(sym) +
                    "&limit=" + HISTORY_REST_LIMIT;
            const [rest, cached] = await Promise.all([
                fetch(q).then((r) => r.json()).then((d) => d.liquidations || []).catch(() => []),
                loadCachedLiquidations(sym, MAX_HISTORY),
            ]);
            mergeLiquidations(cached);   // сначала локальное, оно может быть старше
            mergeLiquidations(rest);     // затем сервер — он источник правды
            if (rest.length) cacheLiquidations(rest);
            rebuildFeed();
            updateMarkers();
            queueRedraw();
        } catch (e) { /* сеть недоступна — живём на том, что уже пришло */ }
    }

    // --- Звук ---------------------------------------------------------------
    function playSound(usd, side) {
        if (!state.soundEnabled) return;
        try {
            if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            if (audioCtx.state === "suspended") audioCtx.resume();
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            if (usd >= 100000) {
                osc.type = "sine";
                osc.frequency.setValueAtTime(150, audioCtx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(45, audioCtx.currentTime + 0.5);
                gain.gain.setValueAtTime(0.22, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.5);
                osc.start(); osc.stop(audioCtx.currentTime + 0.5);
            } else {
                osc.type = "triangle";
                osc.frequency.setValueAtTime(side === "BUY" ? 800 : 420, audioCtx.currentTime);
                gain.gain.setValueAtTime(0.03, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.12);
                osc.start(); osc.stop(audioCtx.currentTime + 0.12);
            }
        } catch (e) { /* ignore */ }
    }

    // --- График (совместимость v4/v5) ---------------------------------------
    function createCandleSeries(c, options) {
        if (window.LightweightCharts && LightweightCharts.CandlestickSeries && c.addSeries) {
            return c.addSeries(LightweightCharts.CandlestickSeries, options);   // v5
        }
        return c.addCandlestickSeries(options);                                  // v4
    }

    function createVolumeSeries(c, options) {
        if (window.LightweightCharts && LightweightCharts.HistogramSeries && c.addSeries) {
            return c.addSeries(LightweightCharts.HistogramSeries, options);      // v5
        }
        return c.addHistogramSeries(options);                                    // v4
    }

    function applyMarkers(markers) {
        if (!candleSeries) return;
        try {
            if (window.LightweightCharts && typeof LightweightCharts.createSeriesMarkers === "function") {
                if (!markersApi) {
                    markersApi = LightweightCharts.createSeriesMarkers(candleSeries, markers);
                } else {
                    markersApi.setMarkers(markers);
                }
            } else if (typeof candleSeries.setMarkers === "function") {
                candleSeries.setMarkers(markers);
            }
        } catch (e) { /* ignore */ }
    }

    function initChart() {
        const container = $("tv-chart-container");
        container.innerHTML = "";

        if (typeof window.LightweightCharts === "undefined") {
            container.innerHTML = '<div class="chart-error">' + I18n.t("chart.error") + "</div>";
            return;
        }

        const width = container.clientWidth || chartWrapper.clientWidth || 900;
        const height = container.clientHeight || chartWrapper.clientHeight || 520;

        chart = LightweightCharts.createChart(container, {
            width, height,
            autoSize: false,
            layout: {
                background: { color: "#090c10" },
                textColor: "#8493a8",
                fontSize: 12,
                fontFamily: "Inter, system-ui, sans-serif",
                attributionLogo: false,
            },
            grid: {
                vertLines: { color: "#18202c" },
                horzLines: { color: "#18202c" },
            },
            crosshair: { mode: LightweightCharts.CrosshairMode ? LightweightCharts.CrosshairMode.Normal : 0 },
            rightPriceScale: { borderColor: "#212938", scaleMargins: { top: 0.06, bottom: 0.24 } },
            timeScale: { borderColor: "#212938", timeVisible: true, secondsVisible: false, rightOffset: 6 },
            localization: {
                priceFormatter: (p) => Number(p).toFixed(priceDigits(p)),
            },
        });

        candleSeries = createCandleSeries(chart, {
            upColor: "#00e676",
            downColor: "#ff2a5f",
            borderVisible: false,
            wickUpColor: "#00e676",
            wickDownColor: "#ff2a5f",
            priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        });

        volumeSeries = createVolumeSeries(chart, {
            priceFormat: { type: "volume" },
            priceScaleId: "volume",
            color: "#26a69a",
        });
        try {
            chart.priceScale("volume").applyOptions({
                scaleMargins: { top: 0.84, bottom: 0 },
                borderVisible: false,
            });
        } catch (e) { /* ignore */ }

        const handleResize = () => {
            if (!chart) return;
            const w = container.clientWidth || chartWrapper.clientWidth || 900;
            const h = container.clientHeight || chartWrapper.clientHeight || 520;
            chart.applyOptions({ width: w, height: h });
            if (clusterCanvas) {
                clusterCanvas.width = w;
                clusterCanvas.height = h;
            }
            queueRedraw();
        };

        if (window.ResizeObserver) {
            const ro = new ResizeObserver(handleResize);
            ro.observe(container);
            ro.observe(chartWrapper);
        }
        window.addEventListener("resize", handleResize);
        setTimeout(handleResize, 80);

        chart.timeScale().subscribeVisibleLogicalRangeChange(queueRedraw);
        markersApi = null;
    }

    function applyPricePrecision(price) {
        if (!candleSeries) return;
        const d = priceDigits(price);
        try {
            candleSeries.applyOptions({
                priceFormat: { type: "price", precision: d, minMove: Math.pow(10, -d) },
            });
        } catch (e) { /* ignore */ }
    }

    function setCandles(candles, source) {
        state.candles = Array.isArray(candles) ? candles.slice() : [];
        state.candleSource = source || "";
        if (!candleSeries || !state.candles.length) return;

        const bars = state.candles
            .map((c) => ({
                time: Number(c.time),
                open: Number(c.open), high: Number(c.high),
                low: Number(c.low), close: Number(c.close),
            }))
            .sort((a, b) => a.time - b.time);

        const vols = state.candles
            .map((c) => ({
                time: Number(c.time),
                value: Number(c.volume) || 0,
                color: Number(c.close) >= Number(c.open) ? "rgba(0,230,118,0.35)" : "rgba(255,42,95,0.35)",
            }))
            .sort((a, b) => a.time - b.time);

        applyPricePrecision(bars[bars.length - 1].close);
        candleSeries.setData(bars);
        if (volumeSeries) volumeSeries.setData(vols);

        state.sessionOpen = bars[0].open;
        updatePriceDisplay(bars[bars.length - 1].close);
        renderTickIndicator();
        updateMarkers();
        updateCvdStat();
        queueRedraw();
    }

    function updateCandle(c) {
        if (!candleSeries || !c) return;
        const bar = {
            time: Number(c.time), open: Number(c.open), high: Number(c.high),
            low: Number(c.low), close: Number(c.close),
        };
        try {
            candleSeries.update(bar);
            if (volumeSeries) {
                volumeSeries.update({
                    time: bar.time,
                    value: Number(c.volume) || 0,
                    color: bar.close >= bar.open ? "rgba(0,230,118,0.35)" : "rgba(255,42,95,0.35)",
                });
            }
        } catch (e) { return; }

        const last = state.candles[state.candles.length - 1];
        if (last && last.time === bar.time) {
            state.candles[state.candles.length - 1] = Object.assign({}, c);
        } else if (!last || bar.time > last.time) {
            state.candles.push(Object.assign({}, c));
            if (state.candles.length > 700) state.candles.shift();
        }
        updatePriceDisplay(bar.close);
        updateCvdStat();
        queueRedraw();
    }

    // --- Индикатор «живости» тиков ------------------------------------------
    function noteTick(exchangeTs) {
        state.tickCount += 1;
        state.lastTickAt = performance.now();
        if (exchangeTs) {
            const lagMs = Date.now() - exchangeTs * 1000;
            // сглаживаем, чтобы цифра не прыгала
            state.lagMs = state.lagMs === null ? lagMs : state.lagMs * 0.8 + lagMs * 0.2;
        }
        livePriceEl.classList.remove("tick-flash");
        void livePriceEl.offsetWidth;
        livePriceEl.classList.add("tick-flash");
    }

    function renderTickIndicator() {
        if (state.candleSource === "unavailable") {
            dataSourceEl.textContent = I18n.t("src.no_link");
            dataSourceEl.className = "data-source warn";
            return;
        }
        if (!state.lastTickAt) {
            dataSourceEl.textContent = state.candleSource === "demo"
                ? I18n.t("src.demo")
                : I18n.t("src.waiting");
            dataSourceEl.className = "data-source";
            return;
        }
        const since = (performance.now() - state.lastTickAt) / 1000;
        const lag = state.lagMs === null ? null : Math.max(0, Math.round(state.lagMs));
        if (since > 20) {
            dataSourceEl.textContent = I18n.t("src.no_ticks", { s: Math.round(since) });
            dataSourceEl.className = "data-source warn";
        } else {
            dataSourceEl.textContent = I18n.t("src.ticks", {
                n: state.tickCount,
                ms: lag !== null ? lag : "—",
            });
            dataSourceEl.className = "data-source live";
        }
    }

    function updatePriceDisplay(price) {
        if (!price) return;
        livePriceEl.textContent = fmtPrice(price);
        if (state.sessionOpen) {
            const pct = ((price - state.sessionOpen) / state.sessionOpen) * 100;
            priceChangeEl.textContent = (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
            priceChangeEl.className = "price-change " + (pct >= 0 ? "positive" : "negative");
        }
    }

    // --- Маркеры и кластеры --------------------------------------------------
    function visibleLiquidations() {
        const sym = chartSymbol();
        return state.liquidations.filter((x) =>
            x.symbol === sym &&
            isExchangeOn(x.exchange) &&
            x.usd >= state.minUsd);
    }

    // Палитра ликвидаций специально НЕ повторяет цвета свечей (зелёный/красный),
    // иначе шарик внутри свечи сливается с её телом.
    const LIQ_COLORS = {
        long:  { fill: "rgba(255,45,149,0.92)", ring: "#ffd7ec" },   // вынесли ЛОНГ — маджента
        short: { fill: "rgba(0,214,255,0.92)",  ring: "#ccf6ff" },   // вынесли ШОРТ — циан
        whale: { fill: "rgba(255,206,0,0.96)",  ring: "#fff6cc" },   // кит — золото
    };
    const WHALE_USD = 100000;

    function updateMarkers() {
        if (!candleSeries) return;
        if (!state.liqEnabled) { applyMarkers([]); return; }
        const tfSec = state.timeframe * 60;
        const byTime = new Map();

        // Метками помечаем только крупные события — всё остальное рисуем
        // шариками прямо на свече, чтобы не засорять поле графика.
        visibleLiquidations().slice(-400).forEach((item) => {
            if (item.usd < WHALE_USD) return;
            const t = Math.floor(item.timestamp / tfSec) * tfSec;
            const key = t + "_" + (item.side === "SELL" ? "L" : "S");
            const cur = byTime.get(key) || { time: t, side: item.side, usd: 0 };
            cur.usd += item.usd;
            byTime.set(key, cur);
        });

        const markers = Array.from(byTime.values())
            .sort((a, b) => a.usd - b.usd)
            .slice(-24)
            .map((m) => ({
                time: m.time,
                position: m.side === "SELL" ? "aboveBar" : "belowBar",
                color: LIQ_COLORS.whale.fill,
                shape: "circle",
                text: "🔥 $" + fmtUsdShort(m.usd),
            }))
            .sort((a, b) => a.time - b.time);

        applyMarkers(markers);
    }

    function queueRedraw() {
        if (redrawQueued) return;
        redrawQueued = true;
        requestAnimationFrame(() => {
            redrawQueued = false;
            drawClusters();
        });
    }

    function drawClusters() {
        if (!clusterCanvas || !chart || !candleSeries) return;
        const ctx = clusterCanvas.getContext("2d");
        ctx.clearRect(0, 0, clusterCanvas.width, clusterCanvas.height);

        drawLiquidationProfile(ctx);   // индикатор: полосы по ценовым уровням

        clusterBalls = [];             // актуальные геометрии шариков — только если слой включён
        if (state.liqEnabled) drawLiqBalls(ctx);
        if (state.cvdEnabled) drawCvdArrows(ctx);
    }

    function drawLiqBalls(ctx) {
        const tfSec = state.timeframe * 60;
        const items = visibleLiquidations();
        if (!items.length || !state.candles.length) return;

        // Свечи по времени: шарик рисуем только там, где свеча реально есть,
        // и прижимаем цену к её диапазону low..high. Биржи отдают цену
        // банкротства, она может уходить далеко от рынка — раньше из-за этого
        // шарики улетали в пустоту.
        const bars = new Map();
        state.candles.forEach((c) => bars.set(c.time, c));

        const clusters = new Map();
        items.forEach((item) => {
            const t = Math.floor(item.timestamp / tfSec) * tfSec;
            const bar = bars.get(t);
            if (!bar) return;
            const lo = Math.min(bar.low, bar.high);
            const hi = Math.max(bar.low, bar.high);
            let price = Number(item.price);
            if (!isFinite(price)) price = bar.close;
            price = Math.min(Math.max(price, lo), hi);
            // Внутри свечи раскладываем по 6 уровням, чтобы близкие
            // ликвидации слипались в один шарик, а не рисовались стопкой.
            const span = hi - lo;
            const level = span > 0 ? Math.round(((price - lo) / span) * 5) : 0;
            const key = t + "_" + level;
            const c = clusters.get(key) || {
                key: key, time: t, lo: lo, hi: hi, level: level,
                longUsd: 0, shortUsd: 0, total: 0, count: 0, ids: [],
            };
            if (item.side === "SELL") c.longUsd += item.usd; else c.shortUsd += item.usd;
            c.total += item.usd;
            c.count += 1;
            if (item.id != null) c.ids.push(item.id);
            clusters.set(key, c);
        });
        if (!clusters.size) return;

        const ts = chart.timeScale();
        const list = Array.from(clusters.values()).sort((a, b) => a.total - b.total);
        const activeKey = pinBallKey || hoverBallKey;   // закреплённый важнее

        clusterBalls = [];
        list.forEach((c) => {
            const price = c.hi > c.lo ? c.lo + ((c.hi - c.lo) * c.level) / 5 : c.lo;
            let x, y;
            try {
                x = ts.timeToCoordinate(c.time);
                y = candleSeries.priceToCoordinate(price);
            } catch (e) { return; }
            if (x === null || y === null || x === undefined || y === undefined) return;
            if (x < -30 || x > clusterCanvas.width + 30) return;
            if (y < -30 || y > clusterCanvas.height + 30) return;

            const whale = c.total >= WHALE_USD;
            const isLong = c.longUsd >= c.shortUsd;
            const theme = whale ? LIQ_COLORS.whale : (isLong ? LIQ_COLORS.long : LIQ_COLORS.short);
            const radius = Math.min(Math.max(Math.log10(Math.max(c.total, 10)) * 3.4, 5), 20);
            const isActive = activeKey === c.key;

            clusterBalls.push({ x: x, y: y, r: radius, key: c.key, ids: c.ids });

            ctx.save();
            // Тёмная подложка отделяет шарик от тела свечи любого цвета
            ctx.beginPath();
            ctx.arc(x, y, radius + 1.6, 0, 2 * Math.PI);
            ctx.fillStyle = "rgba(5,8,14,0.85)";
            ctx.fill();

            ctx.beginPath();
            ctx.arc(x, y, radius, 0, 2 * Math.PI);
            ctx.fillStyle = theme.fill;
            ctx.shadowColor = theme.fill;
            ctx.shadowBlur = whale ? 14 : 7;
            ctx.fill();
            ctx.shadowBlur = 0;
            ctx.lineWidth = whale ? 2 : 1.4;
            ctx.strokeStyle = theme.ring;
            ctx.stroke();

            // Активный шарик (наведение/нажатие) — белое кольцо поверх
            if (isActive) {
                ctx.beginPath();
                ctx.arc(x, y, radius + 4, 0, 2 * Math.PI);
                ctx.lineWidth = 2;
                ctx.strokeStyle = "rgba(255,255,255,0.95)";
                ctx.stroke();
            }

            if (radius >= 11) {
                ctx.fillStyle = whale ? "#1a1200" : "#04070d";
                ctx.font = "bold 9px 'JetBrains Mono', monospace";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillText("$" + fmtUsdShort(c.total), x, y);
            }
            ctx.restore();
        });
    }

    // --- CVD-стрелки по свечам -------------------------------------------------
    // delta = taker buy volume - taker sell volume (USDT) внутри одной свечи.
    // ▲ зелёная над свечой — цену толкали покупатели; ▼ красная под — продавцы.
    // Формирующаяся свеча обновляется на каждом тике, после закрытия стрелка
    // остаётся с финальным значением.
    function drawCvdArrows(ctx) {
        const candles = state.candles;
        if (!candles.length || !chart || !candleSeries) return;
        let maxAbs = 0;
        for (let i = 0; i < candles.length; i++) {
            const d = Math.abs(Number(candles[i].cvd));
            if (isFinite(d) && d > maxAbs) maxAbs = d;
        }
        state.cvdBars = 0;
        if (!(maxAbs > 0)) return;
        const minAbs = Math.max(1000, maxAbs * 0.04);   // микрошум не рисуем
        const ts = chart.timeScale();
        const W = clusterCanvas.width, H = clusterCanvas.height;
        const lastIdx = candles.length - 1;
        for (let i = 0; i < candles.length; i++) {
            const c = candles[i];
            const d = Number(c.cvd);
            if (!isFinite(d) || Math.abs(d) < minAbs) continue;
            const buy = d > 0;
            state.cvdBars += 1;
            let x, y;
            try {
                x = ts.timeToCoordinate(c.time);
                y = candleSeries.priceToCoordinate(buy ? c.high : c.low);
            } catch (e) { continue; }
            if (x === null || x === undefined || y === null || y === undefined) continue;
            if (x < -30 || x > W + 30 || y < -40 || y > H + 40) continue;
            const k = Math.sqrt(Math.abs(d) / maxAbs);
            const size = 3.5 + 7.5 * k;
            const gap = 4;
            ctx.save();
            ctx.globalAlpha = 0.45 + 0.55 * k;
            ctx.fillStyle = buy ? "rgba(0,230,118,0.95)" : "rgba(255,42,95,0.95)";
            if (i === lastIdx) {           // живая свеча — подсвечиваем сильнее
                ctx.shadowColor = ctx.fillStyle;
                ctx.shadowBlur = 10;
            }
            ctx.beginPath();
            if (buy) {                     // ▲ над максимумом
                ctx.moveTo(x, y - gap - size);
                ctx.lineTo(x - size * 0.62, y - gap);
                ctx.lineTo(x + size * 0.62, y - gap);
            } else {                       // ▼ под минимумом
                ctx.moveTo(x, y + gap + size);
                ctx.lineTo(x - size * 0.62, y + gap);
                ctx.lineTo(x + size * 0.62, y + gap);
            }
            ctx.closePath();
            ctx.fill();
            ctx.restore();
        }
    }

    function updateCvdStat() {
        if (!cvdStatEl) return;
        if (!state.cvdEnabled) { cvdStatEl.textContent = ""; return; }
        const last = state.candles[state.candles.length - 1];
        const d = last ? Number(last.cvd) : NaN;
        if (!isFinite(d)) {
            cvdStatEl.textContent = "CVD —";
            cvdStatEl.className = "cvd-stat";
            return;
        }
        const buy = d >= 0;
        cvdStatEl.textContent = (buy ? "▲ +$" : "▼ −$") + fmtUsdShort(Math.abs(d));
        cvdStatEl.className = "cvd-stat " + (buy ? "up" : "down");
    }

    // --- Наведение/нажатие на шарик: подсветка связанных записей в ленте -----
    function ballAt(px, py) {
        for (let i = clusterBalls.length - 1; i >= 0; i--) {
            const b = clusterBalls[i];
            const dx = px - b.x, dy = py - b.y;
            const rr = b.r + 5;
            if (dx * dx + dy * dy <= rr * rr) return b;
        }
        return null;
    }

    function applyFeedHighlight(ball, pin) {
        if (pin) {
            pinBallKey = ball ? ball.key : null;
            if (!ball) hoverBallKey = null;
        } else {
            hoverBallKey = ball ? ball.key : null;
        }
        const ids = ball ? new Set(ball.ids) : null;
        let firstRow = null;
        feedTbody.querySelectorAll("tr").forEach((tr) => {
            const hit = ids && ids.has(tr.dataset.liqId);
            tr.classList.toggle("feed-hit", !!hit);
            tr.classList.toggle("feed-dim", !!ids && !hit);
            if (hit && !firstRow) firstRow = tr;
        });
        if (firstRow) {
            try {
                firstRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
            } catch (e) { /* ignore */ }
        }
        queueRedraw();
    }

    function setupClusterInteraction() {
        if (!chart || !chart.subscribeCrosshairMove || !chart.subscribeClick) return;
        try {
            // наведение (и палец на телефоне при движении по графику)
            chart.subscribeCrosshairMove((param) => {
                if (!param || !param.point) {
                    if (!pinBallKey) applyFeedHighlight(null, false);
                    return;
                }
                if (pinBallKey) return;   // закреплено кликом/тапом — не дёргаем
                applyFeedHighlight(ballAt(param.point.x, param.point.y), false);
            });
            // клик/тап: закрепить шарик; повторный клик по тому же — снять
            chart.subscribeClick((param) => {
                const ball = (param && param.point)
                    ? ballAt(param.point.x, param.point.y) : null;
                if (ball && ball.key === pinBallKey) {
                    applyFeedHighlight(null, true);
                } else {
                    applyFeedHighlight(ball, true);
                }
            });
        } catch (e) { /* старая библиотека без подписок — просто без подсветки */ }
    }

    // --- Регулируемые панели: размеры сохраняются в браузере -----------------
    function clampPx(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    function initSplitters() {
        const root = document.documentElement;
        const layout = { feedW: 430, coinsH: 170 };
        try {
            const raw = localStorage.getItem("licvid.layout");
            if (raw) {
                const o = JSON.parse(raw) || {};
                layout.feedW = clampPx(Number(o.feedW) || 430, 280, 720);
                layout.coinsH = clampPx(Number(o.coinsH) || 170, 90, 460);
            }
        } catch (e) { /* ignore */ }
        const applyLayout = () => {
            root.style.setProperty("--feed-width", layout.feedW + "px");
            root.style.setProperty("--coins-height", layout.coinsH + "px");
        };
        const saveLayout = () => {
            try { localStorage.setItem("licvid.layout", JSON.stringify(layout)); } catch (e) { /* ignore */ }
        };
        const bind = (el, axis) => {
            if (!el) return;
            el.addEventListener("pointerdown", (e) => {
                e.preventDefault();
                el.classList.add("active");
                document.body.classList.add("split-dragging");
                if (axis === "y") document.body.classList.add("split-dragging-y");
                const start = axis === "x" ? e.clientX : e.clientY;
                const startVal = axis === "x" ? layout.feedW : layout.coinsH;
                const move = (ev) => {
                    ev.preventDefault();
                    const delta = (axis === "x" ? ev.clientX : ev.clientY) - start;
                    if (axis === "x") {
                        // лента прижата к правому краю: тянем разделитель вправо →
                        // её ширина УМЕНЬШАЕТСЯ (иначе блоки «уезжают» влево)
                        layout.feedW = clampPx(startVal - delta, 280, 720);
                    } else {
                        layout.coinsH = clampPx(startVal - delta, 90, 460);
                    }
                    applyLayout();
                };
                const up = () => {
                    el.classList.remove("active");
                    document.body.classList.remove("split-dragging", "split-dragging-y");
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", up);
                    window.removeEventListener("pointercancel", up);
                    saveLayout();
                };
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", up);
                window.addEventListener("pointercancel", up);
            });
        };
        bind($("split-feed-x"), "x");
        bind($("split-coins-y"), "y");
        applyLayout();
    }

    // --- Топ пар: выпадающий вертикальный список -----------------------------
    function setupTopCoinsToggle() {
        const box = $("top-coins-box");
        const tgl = $("top-coins-toggle");
        if (!box || !tgl) return;
        let open = true;
        try { open = localStorage.getItem("licvid.coinsOpen") !== "0"; } catch (e) { /* ignore */ }
        const render = () => {
            box.classList.toggle("closed", !open);
            tgl.setAttribute("aria-expanded", open ? "true" : "false");
        };
        tgl.addEventListener("click", () => {
            open = !open;
            try { localStorage.setItem("licvid.coinsOpen", open ? "1" : "0"); } catch (e) { /* ignore */ }
            render();
        });
        render();
    }

    // --- Профиль ликвидаций (индикатор на графике) ---------------------------
    // Горизонтальные полосы по ценовым уровням, как volume profile: ширина
    // полосы = объём ликвидаций на этом уровне за видимый период. Лонги —
    // маджента, шорты — циан. Рисуется на оверлее перед шариками.
    const PROFILE_BINS = 34;
    const PROFILE_MAX_WIDTH = 0.34;   // доля ширины графика

    function profilePriceRange() {
        let from = null, to = null;
        try {
            const vr = chart.timeScale().getVisibleRange();
            if (vr && vr.from && vr.to) {
                from = Number(vr.from);
                to = Number(vr.to);
            }
        } catch (e) { /* ignore */ }
        if (from === null || to === null) {
            if (!state.candles.length) return null;
            from = state.candles[0].time;
            to = state.candles[state.candles.length - 1].time;
        }
        return { from: Math.min(from, to), to: Math.max(from, to) };
    }

    function drawLiquidationProfile(ctx) {
        if (!state.profileEnabled || !state.candles.length || !chart || !candleSeries) return;

        const range = profilePriceRange();
        if (!range) return;
        const pad = state.timeframe * 60;

        // диапазон цены — только по видимым свечам
        let minP = Infinity, maxP = -Infinity;
        state.candles.forEach((c) => {
            if (c.time < range.from - pad || c.time > range.to + pad) return;
            minP = Math.min(minP, Number(c.low), Number(c.high));
            maxP = Math.max(maxP, Number(c.low), Number(c.high));
        });
        if (!isFinite(minP) || !isFinite(maxP) || minP >= maxP) return;

        const items = visibleLiquidations().filter((x) =>
            x.timestamp >= range.from - pad && x.timestamp <= range.to + pad);
        if (!items.length) return;

        const bins = [];
        for (let i = 0; i < PROFILE_BINS; i++) bins.push({ long: 0, short: 0, total: 0 });
        const step = (maxP - minP) / PROFILE_BINS;
        items.forEach((x) => {
            const idx = Math.min(Math.floor((Number(x.price) - minP) / step), PROFILE_BINS - 1);
            if (idx < 0) return;
            const b = bins[idx];
            if (x.side === "SELL") b.long += x.usd; else b.short += x.usd;
            b.total += x.usd;
        });

        let maxTotal = 0;
        bins.forEach((b) => { if (b.total > maxTotal) maxTotal = b.total; });
        if (maxTotal <= 0) return;

        const w = clusterCanvas.width;
        const h = clusterCanvas.height;
        const maxBar = Math.max(w * PROFILE_MAX_WIDTH, 60);
        const binH = h / PROFILE_BINS;
        const barH = Math.max(Math.min(binH * 0.58, 16), 2);
        const x0 = 2;

        ctx.save();
        // фоновая «линейка», чтобы профиль читался над свечами
        for (let i = 0; i < PROFILE_BINS; i++) {
            const b = bins[i];
            if (!b.total) continue;
            const price = maxP - (i + 0.5) * step;
            let y;
            try { y = candleSeries.priceToCoordinate(price); } catch (e) { continue; }
            if (y === null || y === undefined || y < -20 || y > h + 20) continue;

            const rowW = Math.max((b.total / maxTotal) * maxBar, 4);
            const longW = b.total > 0 ? rowW * (b.long / b.total) : 0;
            const shortW = rowW - longW;
            const top = y - barH / 2;

            ctx.globalAlpha = 0.42;
            if (longW > 0) {
                ctx.fillStyle = LIQ_COLORS.long.fill;
                ctx.fillRect(x0, top, longW, barH);
            }
            if (shortW > 0) {
                ctx.fillStyle = LIQ_COLORS.short.fill;
                ctx.fillRect(x0 + longW, top, shortW, barH);
            }
            ctx.globalAlpha = 0.75;
            ctx.strokeStyle = "rgba(255,255,255,0.16)";
            ctx.lineWidth = 1;
            ctx.strokeRect(x0 + 0.5, top + 0.5, rowW - 1, barH - 1);

            // подпись у широких полос
            if (rowW >= 56 && b.total >= maxTotal * 0.12) {
                ctx.globalAlpha = 0.9;
                ctx.fillStyle = "rgba(230,238,255,0.85)";
                ctx.font = "8px 'JetBrains Mono', monospace";
                ctx.textAlign = "left";
                ctx.textBaseline = "middle";
                ctx.fillText("$" + fmtUsdShort(b.total), x0 + rowW + 4, y);
            }
        }
        ctx.restore();
    }

    // --- Лента ---------------------------------------------------------------
    function passesFeedFilter(item) {
        if (state.symbol !== "ALL" && item.symbol !== state.symbol) return false;
        if (!isExchangeOn(item.exchange)) return false;
        return item.usd >= state.minUsd;
    }

    function feedRow(item) {
        const isLong = item.side === "SELL";
        const whale = item.usd >= 100000;
        const tr = document.createElement("tr");
        tr.className = whale ? "feed-row-whale" : "feed-row-new";
        if (item.id != null) tr.dataset.liqId = String(item.id);   // связь с шариком
        const d = new Date(item.timestamp * 1000);
        const valClass = whale ? "whale-val" : (isLong ? "long-val" : "short-val");
        const openTitle = I18n.t("feed.open_chart", { sym: pretty(item.symbol) });
        const openTitleHtml = openTitle.replace(/"/g, "&quot;");
        tr.innerHTML =
            '<td class="td-time">' + I18n.time(item.timestamp) + "</td>" +
            '<td class="td-coin"><button class="coin-link" type="button" data-symbol="' +
            encodeURIComponent(item.symbol) + '" title="' + openTitleHtml +
            '" aria-label="' + openTitleHtml + '">' +
            "<strong>" + pretty(item.symbol) + "</strong><span class=\"coin-link-icon\">📈</span></button></td>" +
            '<td><span class="exch-badge ' + item.exchange + '">' + item.exchange + "</span></td>" +
            '<td><span class="badge-side ' + (isLong ? "long" : "short") + '">' +
            (isLong ? "LONG LIQ" : "SHORT LIQ") + "</span></td>" +
            '<td class="td-usd-amount ' + valClass + '">$' + fmtUsdFull(item.usd) + "</td>" +
            '<td class="td-price">' + fmtPrice(item.price) + "</td>";
        tr.addEventListener("click", () => openModal(item));
        const coinBtn = tr.querySelector(".coin-link");
        if (coinBtn) {
            coinBtn.addEventListener("click", (e) => {
                e.stopPropagation();          // не открываем модалку — открываем график
                selectChartSymbol(decodeURIComponent(coinBtn.dataset.symbol));
            });
        }
        return tr;
    }

    function feedCountLabel(n) {
        return I18n.plural(n, [
            I18n.t("feed.count_one", { n: n }),
            I18n.t("feed.count_few", { n: n }),
            I18n.t("feed.count_many", { n: n }),
        ]);
    }

    function addFeedRow(item) {
        if (!passesFeedFilter(item)) return;
        feedEmptyEl.classList.add("hidden");
        feedTbody.insertBefore(feedRow(item), feedTbody.firstChild);
        while (feedTbody.children.length > 150) feedTbody.removeChild(feedTbody.lastChild);
        feedCountEl.textContent = feedCountLabel(feedTbody.children.length);
    }

    function rebuildFeed() {
        // строки пересоздаются — закреплённая подсветка шарика гаснет
        pinBallKey = null;
        hoverBallKey = null;
        const rows = state.liquidations.filter(passesFeedFilter).slice(-150).reverse();
        feedTbody.innerHTML = "";
        const frag = document.createDocumentFragment();
        rows.forEach((item) => {
            const tr = feedRow(item);
            tr.className = "";
            frag.appendChild(tr);
        });
        feedTbody.appendChild(frag);
        feedCountEl.textContent = feedCountLabel(rows.length);
        feedEmptyEl.classList.toggle("hidden", rows.length > 0);
    }

    function openModal(item) {
        state.modalItem = item;
        modalTitle.textContent = I18n.t("modal.liq_of", { sym: pretty(item.symbol) });
        const isLong = item.side === "SELL";
        modalBody.innerHTML =
            "<p><strong>" + I18n.t("modal.exch") + "</strong> <span style=\"text-transform:capitalize\">" +
            item.exchange + "</span></p>" +
            "<p><strong>" + I18n.t("modal.type") + "</strong> " + (isLong
                ? '<span class="badge-side long">' + I18n.t("modal.long_desc") + "</span>"
                : '<span class="badge-side short">' + I18n.t("modal.short_desc") + "</span>") + "</p>" +
            "<p><strong>" + I18n.t("modal.price") + "</strong> " + fmtPrice(item.price) + "</p>" +
            '<p><strong>' + I18n.t("modal.usd") + '</strong> <span style="font-size:1.1rem;font-weight:800;color:var(--color-gold)">$' +
            fmtUsdFull(item.usd) + "</span></p>" +
            "<p><strong>" + I18n.t("modal.qty") + "</strong> " +
            Number(item.qty).toLocaleString("en-US", { maximumFractionDigits: 6 }) + "</p>" +
            "<p><strong>" + I18n.t("modal.time") + "</strong> " + I18n.dateTime(item.timestamp) + "</p>";
        detailModal.classList.remove("hidden");
    }

    // --- Слои графика: ликвидации / профиль / CVD ----------------------------
    function setupLayerToggles() {
        const defs = [
            { el: profileToggle, skey: "profileEnabled", store: "licvid.profileEnabled",
              on: "chart.profile_on", off: "chart.profile_off" },
            { el: $("liq-toggle"), skey: "liqEnabled", store: "licvid.liqEnabled",
              on: "chart.liq_on", off: "chart.liq_off" },
            { el: $("cvd-toggle"), skey: "cvdEnabled", store: "licvid.cvdEnabled",
              on: "chart.cvd_on", off: "chart.cvd_off" },
        ];
        defs.forEach((d) => {
            if (!d.el) return;
            try {
                const v = localStorage.getItem(d.store);
                if (v === "0") state[d.skey] = false;
                else if (v === "1") state[d.skey] = true;
            } catch (e) { /* ignore */ }
            const paint = () => {
                d.el.classList.toggle("active", state[d.skey]);
                d.el.title = I18n.t(state[d.skey] ? d.on : d.off);
            };
            d.el.addEventListener("click", () => {
                state[d.skey] = !state[d.skey];
                try {
                    localStorage.setItem(d.store, state[d.skey] ? "1" : "0");
                } catch (e) { /* ignore */ }
                if (d.skey === "liqEnabled" && !state.liqEnabled) {
                    // с прячущихся шариков снимаем и подсветку ленты
                    pinBallKey = null;
                    hoverBallKey = null;
                    applyFeedHighlight(null, true);
                }
                paint();
                updateMarkers();
                updateCvdStat();
                queueRedraw();
            });
            paint();
        });
    }

    // --- Сворачивание графика (нужно на телефоне) ----------------------------
    function setupChartToggle() {
        if (!chartToggle || !chartSection) return;
        try {
            if (localStorage.getItem("licvid.chartCollapsed") === "1") {
                chartSection.classList.add("collapsed");
            }
        } catch (e) { /* ignore */ }

        chartToggle.addEventListener("click", () => {
            const collapsed = chartSection.classList.toggle("collapsed");
            try {
                localStorage.setItem("licvid.chartCollapsed", collapsed ? "1" : "0");
            } catch (e) { /* ignore */ }
            if (!collapsed) {
                // после разворачивания пересчитываем размеры графика
                setTimeout(() => {
                    window.dispatchEvent(new Event("resize"));
                    queueRedraw();
                }, 60);
            }
        });
    }

    // --- Список монет: выпадающий список (как на биржах) ---------------------
    let symbolDropdownOpen = false;

    function renderSymbolOpen() {
        if (!symbolCurrentBtn || !symbolDropdown) return;
        symbolCurrentBtn.classList.toggle("open", symbolDropdownOpen);
        symbolCurrentBtn.setAttribute("aria-expanded", symbolDropdownOpen ? "true" : "false");
        symbolDropdown.classList.toggle("hidden", !symbolDropdownOpen);
    }

    function openSymbolDropdown() {
        symbolDropdownOpen = true;
        renderSymbolOpen();
    }

    function closeSymbolDropdown() {
        symbolDropdownOpen = false;
        renderSymbolOpen();
    }

    function renderSymbolButtons() {
        if (!symbolDropdown) return;
        const q = (symbolSearchEl.value || "").trim().toUpperCase();

        // кнопка-триггер: только выбранная монета (или «ВСЕ»)
        if (state.symbol === "ALL") {
            symbolCurrentLabel.textContent = I18n.t("sym.all");
        } else {
            const d = state.details[state.symbol] || {};
            const isCustom = state.customSymbols.indexOf(state.symbol) !== -1 || d.custom;
            symbolCurrentLabel.innerHTML = escapeHtml(pretty(state.symbol)) +
                (d.liq24h ? ' <i class="sym-cur-liq">★ $' + fmtUsdShort(d.liq24h) + "</i>"
                          : (isCustom ? ' <i class="sym-cur-liq">★</i>' : ""));
        }

        // вертикальный список: «ВСЕ» + монеты (фильтр по тексту поиска)
        symbolDropdown.innerHTML = "";
        if (!q) {
            const allRow = symbolOptionEl("ALL", {
                label: I18n.t("sym.all"),
                title: I18n.t("sym.all_title"),
                active: state.symbol === "ALL",
            });
            allRow.addEventListener("click", () => {
                closeSymbolDropdown();
                selectSymbol("ALL");
            });
            symbolDropdown.appendChild(allRow);
        }
        state.symbols
            .filter((s) => !q || s.indexOf(q) !== -1)
            .forEach((s) => {
                const d = state.details[s] || {};
                const onChart = s === chartSymbol();
                const isCustom = state.customSymbols.indexOf(s) !== -1 || d.custom;
                const row = symbolOptionEl(s, {
                    label: s.split("_")[0],
                    title: pretty(s) + (d.price ? "  " + fmtPrice(d.price) : "") +
                        (isCustom ? "\n" + I18n.t("sym.custom") : "") +
                        "\n" + I18n.t("sym.click") +
                        "\n" + I18n.t("sym.shift"),
                    active: s === state.symbol,
                    onChart: onChart,
                    isCustom: isCustom,
                    liq: d.liq24h ? "$" + fmtUsdShort(d.liq24h) : "",
                });
                row.addEventListener("click", (ev) => {
                    closeSymbolDropdown();
                    if (ev.shiftKey) selectChartSymbol(s);
                    else selectSymbol(s);
                });
                symbolDropdown.appendChild(row);
            });

        if (!symbolDropdown.children.length) {
            const empty = document.createElement("span");
            empty.className = "sym-empty";
            empty.textContent = I18n.t("sym.empty");
            symbolDropdown.appendChild(empty);
        }
    }

    function symbolOptionEl(sym, o) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "symbol-option" + (o.active ? " active" : "");
        row.dataset.symbol = sym;
        row.setAttribute("role", "option");
        if (o.title) row.title = o.title;
        row.innerHTML =
            '<span class="sym-opt-left">' +
                (o.onChart ? '<i class="sym-chart">📈</i>' : "") +
                "<span>" + o.label + "</span>" +
                (o.isCustom ? '<i class="sym-star">★</i>' : "") +
            "</span>" +
            (o.liq ? '<i class="sym-liq">' + o.liq + "</i>" : "");
        return row;
    }

    function setupSymbolDropdown() {
        if (!symbolCurrentBtn || !symbolDropdown) return;
        symbolCurrentBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (symbolDropdownOpen) closeSymbolDropdown();
            else openSymbolDropdown();
        });
        symbolDropdown.addEventListener("click", (e) => e.stopPropagation());
        document.addEventListener("click", () => closeSymbolDropdown());
        symbolSearchEl.addEventListener("keydown", (e) => {
            if (e.key === "Escape") closeSymbolDropdown();
        });
    }

    function updateSymbolTitle() {
        symbolTitleEl.textContent = state.symbol === "ALL"
            ? pretty(chartSymbol()) + " · " + I18n.t("feed.all_feed")
            : pretty(state.symbol);
    }

    function selectSymbol(s) {
        const prevChart = chartSymbol();
        state.symbol = s;
        if (s !== "ALL") {
            state.chartSymbol = s;              // «ВСЕ» график не переключает
            try { localStorage.setItem("licvid.chartSymbol", s); } catch (e) { /* ignore */ }
        }
        const chartChanged = chartSymbol() !== prevChart;
        if (chartChanged) {
            state.tickCount = 0;
            state.lastTickAt = 0;
            state.lagMs = null;
        }
        updateSymbolTitle();
        renderSymbolButtons();
        sendConfig();
        if (chartChanged) loadCandles();        // иначе график не трогаем вовсе
        rebuildFeed();
        fetchStats();
        loadHistoryFor(s);                      // подтянуть историю новой пары
    }

    function selectChartSymbol(s) {
        if (!s || s === chartSymbol()) return;
        state.chartSymbol = s;
        try { localStorage.setItem("licvid.chartSymbol", s); } catch (e) { /* ignore */ }
        state.tickCount = 0;
        state.lastTickAt = 0;
        state.lagMs = null;
        updateSymbolTitle();
        renderSymbolButtons();
        sendConfig();
        loadCandles();
        loadHistoryFor(s);   // пузырьки новой пары сразу с полной историей
    }

    function renderExchangeOptions(exchanges) {
        state.availableExchanges = (exchanges || []).slice();
        // если сохранённый фильтр ссылается на несуществующие биржи — чистим
        if (state.exchanges) {
            const known = new Set(state.availableExchanges);
            state.exchanges = new Set(Array.from(state.exchanges).filter((e) => known.has(e)));
        }
        buildExchangePanel();
        refreshFilterButtons();
    }

    function buildExchangePanel() {
        if (!exchListEl) return;
        exchListEl.innerHTML = "";
        state.availableExchanges.forEach((e) => {
            const label = document.createElement("label");
            label.className = "check-row";
            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.value = e;
            cb.checked = isExchangeOn(e);
            cb.dataset.exch = e;
            const name = document.createElement("span");
            name.textContent = exchangeName(e);
            label.appendChild(cb);
            label.appendChild(name);
            exchListEl.appendChild(label);
        });
        Array.prototype.forEach.call(exchListEl.querySelectorAll("input[type=checkbox]"), (cb) => {
            cb.addEventListener("change", onExchangeCheckboxChange);
        });
    }

    function onExchangeCheckboxChange() {
        // включённые биржи = отмеченные; пустой набор = ничего не показывать
        state.exchanges = new Set();
        Array.prototype.forEach.call(exchListEl.querySelectorAll("input[type=checkbox]"), (cb) => {
            if (cb.checked) state.exchanges.add(cb.value);
        });
        saveExchanges();
        refreshFilterButtons();
        applyFiltersFull();
    }

    function refreshFilterButtons() {
        if (!minUsdBtn) return;
        minUsdBtn.textContent = state.minUsd > 0
            ? I18n.t("filter.min_ge", { v: I18n.number(state.minUsd) })
            : I18n.t("filter.all_usd");
        if (minUsdInput) minUsdInput.value = state.minUsd > 0 ? String(state.minUsd) : "";
        if (exchangeBtn) {
            if (!state.exchanges || state.exchanges.size === state.availableExchanges.length) {
                exchangeBtn.textContent = I18n.t("filter.all_exchanges");
            } else if (state.exchanges.size === 0) {
                exchangeBtn.textContent = I18n.t("filter.disabled");
            } else {
                exchangeBtn.textContent = state.availableExchanges
                    .filter((e) => state.exchanges.has(e))
                    .map(exchangeName).join(", ") + " ▾";
            }
        }
    }

    function setMinUsd(v, closePanel) {
        state.minUsd = Math.max(0, parseFloat(v) || 0);
        saveMinUsd();
        refreshFilterButtons();
        applyFiltersFull();
        if (closePanel && minUsdPanel) minUsdPanel.classList.add("hidden");
    }

    // --- Выпадающие панели фильтров ------------------------------------------
    function setupFilterControls() {
        loadSavedFilters();
        if (minUsdBtn && minUsdPanel) {
            minUsdBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                minUsdPanel.classList.toggle("hidden");
                exchangePanel.classList.add("hidden");
            });
        }
        if (minUsdApply) {
            minUsdApply.addEventListener("click", () => setMinUsd(minUsdInput.value, true));
        }
        if (minUsdInput) {
            minUsdInput.addEventListener("keydown", (e) => {
                if (e.key === "Enter") setMinUsd(minUsdInput.value, true);
            });
        }
        if (minUsdPresets) {
            Array.prototype.forEach.call(minUsdPresets.querySelectorAll("button"), (b) => {
                b.addEventListener("click", () => setMinUsd(b.dataset.v, true));
            });
        }
        if (exchangeBtn && exchangePanel) {
            exchangeBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                exchangePanel.classList.toggle("hidden");
                minUsdPanel.classList.add("hidden");
            });
        }
        if (exchAllCheck) {
            exchAllCheck.addEventListener("click", () => {
                state.exchanges = null;               // все включены
                saveExchanges();
                buildExchangePanel();
                refreshFilterButtons();
                applyFiltersFull();
            });
        }
        if (exchNoneCheck) {
            exchNoneCheck.addEventListener("click", () => {
                state.exchanges = new Set();          // выключить все
                saveExchanges();
                buildExchangePanel();
                refreshFilterButtons();
                applyFiltersFull();
            });
        }
        // клик мимо панелей — закрыть
        document.addEventListener("click", (e) => {
            if (minUsdDd && !minUsdDd.contains(e.target) && minUsdPanel) {
                minUsdPanel.classList.add("hidden");
            }
            if (exchangeDd && !exchangeDd.contains(e.target) && exchangePanel) {
                exchangePanel.classList.add("hidden");
            }
        });
        refreshFilterButtons();
    }

    // --- Поиск и добавление монет --------------------------------------------
    function setupSymbolSearch() {
        if (!symbolSearchEl) return;
        symbolSearchEl.addEventListener("input", () => {
            renderSymbolButtons();
            scheduleSymbolSearch(symbolSearchEl.value);
        });
        symbolSearchEl.addEventListener("keydown", (e) => {
            if (e.key === "Enter") triggerFirstSearchResult();
            if (e.key === "Escape") hideSymbolResults();
        });
        // закрыть выпадашку по клику мимо
        document.addEventListener("click", (e) => {
            if (symbolSearchBox && !symbolSearchBox.contains(e.target)) hideSymbolResults();
        });
    }

    function scheduleSymbolSearch(q) {
        clearTimeout(state.searchTimer);
        if (!q || !String(q).trim()) {
            hideSymbolResults();
            return;
        }
        state.searchTimer = setTimeout(() => runSymbolSearch(String(q).trim()), 250);
    }

    async function runSymbolSearch(q) {
        const reqId = ++state.searchRequestId;
        try {
            const r = await fetch("/api/symbols/search?q=" + encodeURIComponent(q) + "&limit=8");
            const data = await r.json();
            if (reqId !== state.searchRequestId) return;   // ответ устарел
            renderSymbolSearchResults(data.results || [], q);
        } catch (e) { /* сеть недоступна — молча */ }
    }

    function renderSymbolSearchResults(results, q) {
        if (!symbolSearchResults) return;
        if (!results.length) {
            symbolSearchResults.classList.remove("hidden");
            symbolSearchResults.innerHTML = '<div class="search-result-empty">' +
                I18n.t("search.nothing", { q: escapeHtml(q) }) + "</div>";
            return;
        }
        const html = results.map((r) => {
            const sym = r.symbol;
            const custom = state.customSymbols.indexOf(sym) !== -1 || state.symbols.indexOf(sym) !== -1;
            return '<div class="search-result" data-symbol="' + encodeURIComponent(sym) + '">' +
                '<div class="search-result-main">' +
                '<span class="search-result-sym">' + pretty(sym) + "</span>" +
                (r.exchanges && r.exchanges.length
                    ? '<span class="search-result-exch">' + r.exchanges.map(exchangeName).join(" · ") + "</span>"
                    : "") +
                "</div>" +
                '<div class="search-result-data">' +
                (r.price ? "<span>" + fmtPrice(r.price) + "</span>" : "") +
                (r.volume24h ? "<span>" + I18n.t("search.vol24", { v: "$" + fmtUsdShort(r.volume24h) }) + "</span>" : "") +
                "</div>" +
                '<button class="btn btn-sm search-result-add" type="button">' +
                (custom ? I18n.t("search.open") : I18n.t("search.add")) + "</button></div>";
        }).join("");
        symbolSearchResults.classList.remove("hidden");
        symbolSearchResults.innerHTML = html;
        symbolSearchResults.querySelectorAll(".search-result").forEach((row) => {
            row.querySelector(".search-result-add").addEventListener("click", (e) => {
                e.stopPropagation();
                addAndOpenSymbol(row.dataset.symbol);
            });
        });
    }

    function hideSymbolResults() {
        if (symbolSearchResults) {
            symbolSearchResults.classList.add("hidden");
            symbolSearchResults.innerHTML = "";
        }
    }

    function triggerFirstSearchResult() {
        if (!symbolSearchResults || symbolSearchResults.classList.contains("hidden")) return;
        const first = symbolSearchResults.querySelector(".search-result");
        if (first) addAndOpenSymbol(first.dataset.symbol);
    }

    async function addAndOpenSymbol(rawSymbol) {
        try {
            const r = await fetch("/api/symbols/add?symbol=" + encodeURIComponent(rawSymbol),
                { method: "POST" });
            const data = await r.json();
            if (!data.added) {
                symbolHint.textContent = I18n.t("search.added_fail", { sym: pretty(rawSymbol) });
                symbolHint.className = "symbol-hint warn";
                return;
            }
            if (state.symbols.indexOf(data.symbol) === -1) state.symbols.push(data.symbol);
            if (state.customSymbols.indexOf(data.symbol) === -1) state.customSymbols.push(data.symbol);
            state.details[data.symbol] = Object.assign({}, state.details[data.symbol], data.details || {}, { custom: true });
            if (data.details && data.details.price) state.prices[data.symbol] = data.details.price;
            symbolHint.textContent = I18n.t("search.added_ok", { sym: pretty(data.symbol) });
            symbolHint.className = "symbol-hint ok";
            hideSymbolResults();
            symbolSearchEl.value = "";
            renderSymbolButtons();
            selectChartSymbol(data.symbol);   // график — на новую пару, лента не трогаем
        } catch (e) {
            symbolHint.textContent = I18n.t("search.network_error");
            symbolHint.className = "symbol-hint warn";
        }
    }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    function renderHealth(health) {
        if (!health || !health.sources) return;
        state.lastHealth = health;
        state.demo = !!health.demo;
        demoBadge.classList.toggle("hidden", !state.demo);
        const parts = [];
        Object.keys(health.sources).forEach((name) => {
            const s = health.sources[name];
            if (!s.enabled) return;
            const cls = s.connected ? "ok" : "bad";
            let label = name;
            if (name.indexOf("prices") === 0) label = I18n.t("health.prices");
            else if (name.indexOf("ticks") === 0) label = I18n.t("health.ticks");
            const title = s.connected
                ? I18n.t("health.connected", { n: s.events })
                : I18n.t("health.noconn", { err: s.last_error || "—" });
            parts.push('<span class="exch-chip ' + cls + '" title="' +
                title.replace(/"/g, "&quot;") + '">' + label + "</span>");
        });
        exchHealthEl.innerHTML = parts.join("");
    }

    // --- Статистика ----------------------------------------------------------
    function renderStats(data) {
        if (!data) return;
        state.lastStats = data;
        stat24hEl.textContent = "$" + fmtUsdShort(data.total_usd_24h);
        stat1hEl.textContent = "$" + fmtUsdShort(data.total_usd_1h);
        stat5mEl.textContent = "$" + fmtUsdShort(data.total_usd_5m);

        const total = data.total_usd_24h || 0;
        const longPct = total > 0 ? Math.round((data.longs_usd_24h / total) * 100) : 50;
        longPctEl.textContent = longPct + "%";
        shortPctEl.textContent = (100 - longPct) + "%";
        longRatioBar.style.width = longPct + "%";

        if (data.top_coins) {
            topCoinsContainer.innerHTML = data.top_coins.slice(0, 8).map((c) =>
                '<div class="top-coin-card" data-symbol="' + c.symbol + '">' +
                '<div class="top-coin-name">' + pretty(c.symbol) + "</div>" +
                '<div class="top-coin-val">$' + fmtUsdShort(c.usd) + "</div></div>").join("");
            Array.prototype.forEach.call(topCoinsContainer.children, (el) => {
                el.addEventListener("click", () => selectSymbol(el.dataset.symbol));
            });
        }

        // подписи «сколько ликвидаций по монете» в кнопках
        if (data.top_coins) {
            data.top_coins.forEach((c) => {
                state.details[c.symbol] = Object.assign({}, state.details[c.symbol], { liq24h: c.usd });
            });
        }
    }

    async function fetchStats() {
        try {
            const q = state.symbol === "ALL" ? "" : "?symbol=" + encodeURIComponent(state.symbol);
            const r = await fetch("/api/stats" + q);
            renderStats(await r.json());
        } catch (e) { /* ignore */ }
    }

    // --- Свечи ---------------------------------------------------------------
    async function loadCandles() {
        try {
            const r = await fetch("/api/klines?symbol=" + encodeURIComponent(chartSymbol()) +
                "&timeframe=" + state.timeframe);
            const data = await r.json();
            if (data && data.candles) setCandles(data.candles, data.source);
        } catch (e) {
            console.error("klines:", e);
        }
    }

    // --- WebSocket -----------------------------------------------------------
    function sendConfig() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({
            action: "sub",
            symbol: state.symbol,          // фильтр ленты
            chart: chartSymbol(),          // символ графика (может отличаться)
            tf: state.timeframe,
            min_usd: 0,
            exchange: "ALL",               // фильтр бирж применяется на клиенте
        }));
    }

    function setConn(status, key, vars) {
        connState = { status: status, key: key, vars: vars };
        connStatusEl.innerHTML = '<span class="dot ' + status + '"></span> ' +
            I18n.t(key, vars);
    }

    function connectWs() {
        clearTimeout(wsReconnectTimer);
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        setConn("pulse yellow", "conn.connecting");
        try {
            ws = new WebSocket(proto + "//" + location.host + "/ws");
        } catch (e) {
            wsReconnectTimer = setTimeout(connectWs, 3000);
            return;
        }

        ws.onopen = () => {
            setConn("pulse green", "conn.live");
            sendConfig();
        };
        ws.onmessage = (ev) => {
            let msg;
            try { msg = JSON.parse(ev.data); } catch (e) { return; }
            handleMessage(msg);
        };
        ws.onclose = () => {
            setConn("red", "conn.reconnecting");
            wsReconnectTimer = setTimeout(connectWs, 2500);
        };
        ws.onerror = () => { try { ws.close(); } catch (e) {} };
    }

    function handleMessage(msg) {
        switch (msg.type) {
            case "init": {
                state.symbols = msg.symbols || [];
                state.customSymbols = msg.custom_symbols || [];
                (msg.details || []).forEach((d) => { state.details[d.symbol] = d; });
                state.prices = msg.prices || {};
                // при переподключении НЕ стираем то, что уже накопилось,
                // а сливаем новое (дедупликация по id) — иначе после F5
                // история пузырьков опять обрывалась бы на 200 событиях
                mergeLiquidations(msg.recent_liquidations || []);
                cacheLiquidations(msg.recent_liquidations || []);
                renderExchangeOptions(msg.exchanges);
                renderHealth(msg.health);
                renderSymbolButtons();
                if (state.symbol !== "ALL" && state.symbols.indexOf(state.symbol) === -1) {
                    state.symbol = "ALL";
                }
                if (!state.chartSymbol) {
                    let saved = "";
                    try { saved = localStorage.getItem("licvid.chartSymbol") || ""; } catch (e) { /* ignore */ }
                    // сохранённая пара может быть пользовательской — держим её,
                    // даже если она не попала в текущий топ (сервер найдёт свечи)
                    state.chartSymbol = (saved && /^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$/.test(saved))
                        ? saved
                        : (state.symbol !== "ALL" ? state.symbol : (state.symbols[0] || ""));
                }
                updateSymbolTitle();
                renderStats(msg.stats);
                rebuildFeed();
                sendConfig();
                loadCandles();
                // догружаем историю по фильтрам пользователя: сервер отдаёт
                // до 2000 событий выбранной пары, локальный кэш — что успели
                // накопить раньше (в т.ч. если сервер только что перезапустился)
                loadHistoryFor(chartSymbol(), true);
                if (state.symbol !== "ALL" && state.symbol !== chartSymbol()) {
                    loadHistoryFor(state.symbol);
                }
                break;
            }
            case "liqs": {
                const rows = msg.data || [];
                rows.forEach((item) => {
                    state.liquidations.push(item);
                    addFeedRow(item);
                    if (passesFeedFilter(item)) playSound(item.usd, item.side);
                });
                if (state.liquidations.length > MAX_HISTORY) {
                    state.liquidations.splice(0, state.liquidations.length - MAX_HISTORY);
                }
                cacheLiquidations(rows);   // копия на этом устройстве (IndexedDB)
                updateMarkers();
                queueRedraw();
                break;
            }
            case "prices": {
                Object.assign(state.prices, msg.data || {});
                const p = state.prices[chartSymbol()];
                if (p) updatePriceDisplay(p);
                break;
            }
            case "tick":
            case "candle": {
                if (msg.symbol === chartSymbol() && msg.tf === state.timeframe) {
                    updateCandle(msg.candle);
                    if (msg.type === "tick") noteTick(msg.ts);
                }
                break;
            }
            case "candles": {
                if (msg.symbol === chartSymbol() && msg.tf === state.timeframe) {
                    setCandles(msg.candles, msg.source);
                }
                break;
            }
            case "stats": {
                renderStats(msg.data);
                renderHealth(msg.health);
                break;
            }
            default:
                break;
        }
    }

    // --- Язык интерфейса -----------------------------------------------------
    function applyLanguage() {
        if (langSelect) langSelect.value = I18n.lang();
        document.title = I18n.t("terminal.title");
        // динамический текст (не покрыт data-i18n)
        setConn(connState.status, connState.key, connState.vars);
        refreshFilterButtons();
        renderHealth(state.lastHealth);
        renderStats(state.lastStats);
        renderSymbolButtons();
        updateSymbolTitle();
        rebuildFeed();
        renderTickIndicator();
        if (state.modalItem && !detailModal.classList.contains("hidden")) {
            openModal(state.modalItem);
        }
    }

    function setupLanguage() {
        if (langSelect) {
            langSelect.value = I18n.lang();
            langSelect.addEventListener("change", (e) => I18n.set(e.target.value));
        }
        I18n.onChange(applyLanguage);
        applyLanguage();
    }

    // --- Обработчики UI ------------------------------------------------------
    document.querySelectorAll(".btn-tf").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".btn-tf").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.timeframe = parseInt(btn.dataset.tf, 10);
            sendConfig();
            loadCandles();
        });
    });

    setupFilterControls();
    setupSymbolSearch();
    setupLanguage();

    soundToggleBtn.addEventListener("click", () => {
        state.soundEnabled = !state.soundEnabled;
        soundIcon.textContent = state.soundEnabled ? "🔊" : "🔇";
        soundToggleBtn.classList.toggle("active", state.soundEnabled);
        if (state.soundEnabled) playSound(1000, "BUY");
    });

    clearBtn.addEventListener("click", () => {
        state.liquidations = [];
        historyLoaded.clear();   // при след. выборе пары история подтянется заново
        feedTbody.innerHTML = "";
        feedCountEl.textContent = feedCountLabel(0);
        feedEmptyEl.classList.remove("hidden");
        applyMarkers([]);
        queueRedraw();
    });

    function closeModal() {
        state.modalItem = null;
        detailModal.classList.add("hidden");
    }
    closeModalBtn.addEventListener("click", closeModal);
    detailModal.addEventListener("click", (e) => {
        if (e.target === detailModal) closeModal();
    });

    // --- Старт ---------------------------------------------------------------
    function boot() {
        initChart();
        setupClusterInteraction();   // наведение/нажатие на шарик → подсветка в ленте
        initSplitters();             // регулируемая ширина ленты и высота «Лидеров»
        setupTopCoinsToggle();       // выпадающий список топ-пар
        setupSymbolDropdown();       // выпадающий список монет
        loadCandles();
        connectWs();
        setInterval(fetchStats, 15000);
        setInterval(renderTickIndicator, 1000);
        setupLayerToggles();
        setupChartToggle();
        // страховка: если WS молчит дольше 30с — перезапрашиваем свечи
        setInterval(() => {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            loadCandles();
        }, 60000);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
