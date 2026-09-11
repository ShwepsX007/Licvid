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
        oiEnabled: true,        // OI-шарики: рост/падение открытого интереса за свечу
        oiBars: 0,
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
        lastOI: null,
        statWin: { liq: "24h", cvd: "24h", oi: "24h" },
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

    // Хит-тест прямоугольников: координаты и ids событий из последнего drawClusters()
    let clusterHits = [];         // [{kind:"liq", x, y, w, h, key, ids:[...]}]
    let hoverHitKey = null;       // прямоугольник под курсором (наведение)
    let pinHitKey = null;         // прямоугольник, закреплённый кликом/тапом
    // Хит-тест фигур CVD/OI: круги из последнего drawClusters()
    let cvdHits = [];             // [{kind:"cvd", key, x, y, r, time, d, buy, live, p90, c}]
    let oiHits = [];              // [{kind:"oi", key, x, y, r, time, d, up, tier, live, c}]
    let shapeHover = null;        // {kind, key} — фигура под курсором (liq/cvd/oi)
    let shapePin = null;          // {kind, key} — фигура, закреплённая кликом/тапом

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
    const liqStatEl = $("liq-stat");
    const profileStatEl = $("profile-stat");
    const oiStatEl = $("oi-stat");
    const langSelect = $("lang-select");
    const symbolButtonsEl = $("symbol-buttons");   // старый контейнер (не используется)
    const symbolCurrentBtn = $("symbol-current");
    const symbolCurrentLabel = $("symbol-current-label");
    const symbolDropdown = $("symbol-dropdown");
    const symbolSearchEl = $("symbol-search");

    const statBoxLiq = $("stat-box-liq"), statLiqHead = $("stat-liq-head"),
          statLiqWin = $("stat-liq-win"), statLiqValue = $("stat-liq-value"),
          statLiqSub = $("stat-liq-sub"), statMenuLiq = $("stat-menu-liq");
    const statBoxCvd = $("stat-box-cvd"), statCvdHead = $("stat-cvd-head"),
          statCvdWin = $("stat-cvd-win"), statCvdValue = $("stat-cvd-value"),
          statCvdSub = $("stat-cvd-sub"), statMenuCvd = $("stat-menu-cvd");
    const statBoxOi = $("stat-box-oi"), statOiHead = $("stat-oi-head"),
          statOiWin = $("stat-oi-win"), statOiValue = $("stat-oi-value"),
          statOiSub = $("stat-oi-sub"), statMenuOi = $("stat-menu-oi");
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
        updateLiveStats();
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
            updateLiveStats();
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
        updateLiveStats();
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
        updateLiveStats();
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

    // Палитра ликвидаций: мелкие события кодируют СТОРОНУ (маджента — вынесли
    // лонг, циан — вынесли шорт), а киты — ОБЪЁМ по тепловой шкале
    // от жёлтого к ярко-красному (см. HEAT_STOPS ниже). Порог кита
    // масштабируется от оборота монеты (для BTC — $100K, см. volScale).
    const LIQ_COLORS = {
        long:  { fill: "rgba(255,45,149,0.92)", ring: "#ffd7ec", text: "#04070d" },
        short: { fill: "rgba(0,214,255,0.92)",  ring: "#ccf6ff", text: "#04070d" },
    };
    const WHALE_USD = 100000;
    const LIQ_LABEL_MIN_USD = 2000;   // меньше — рисуем чип без подписи

    // --- Масштаб «крупности» от оборота монеты -------------------------------
    // Пороги китов/подписей/тиров заданы для BTC; для остальных монет умножаем
    // на отношение средненедельного оборота к BTC. Что для BTC пыль ($100K),
    // для GRAM — кит. Нет данных об оборотах — масштаб 1, как раньше.
    function volOf(d) {
        return Number(d.volAvg7d) || Number(d.volume24h) || 0;
    }
    function volAnchor() {
        const b = volOf(state.details["BTC_USDT"] || {});
        if (b > 0) return b;
        let mx = 0;
        for (const k in state.details) mx = Math.max(mx, volOf(state.details[k] || {}));
        return mx;
    }
    function volScale(symbol) {
        const a = volAnchor(), v = volOf(state.details[symbol] || {});
        if (!(v > 0) || !(a > 0)) return 1;
        return Math.min(2, Math.max(0.001, v / a));
    }
    function chartVolScale() { return volScale(chartSymbol()); }

    // Тепловая шкала китов: [порог $, R, G, B]. Между соседними стопами цвет
    // интерполируется, выше последнего — ярко-красный с растущим свечением.
    const HEAT_STOPS = [
        [100000, 255, 206, 0],    // $100K — жёлтый
        [300000, 255, 168, 0],    // $300K — жёлто-оранжевый
        [500000, 255, 128, 0],    // $500K — оранжевый
        [1000000, 255, 72, 0],    // $1M — красно-оранжевый
        [2000000, 255, 30, 8],    // $2M — красный
        [5000000, 255, 16, 48],   // $5M+ — ярко-красный
    ];

    function heatRGB(usd, k) {
        k = k || 1;
        const first = HEAT_STOPS[0], last = HEAT_STOPS[HEAT_STOPS.length - 1];
        if (usd <= first[0] * k) return [first[1], first[2], first[3]];
        for (let i = 1; i < HEAT_STOPS.length; i++) {
            if (usd <= HEAT_STOPS[i][0] * k) {
                const a = HEAT_STOPS[i - 1], b = HEAT_STOPS[i];
                const t = (usd - a[0] * k) / ((b[0] - a[0]) * k);
                return [
                    Math.round(a[1] + (b[1] - a[1]) * t),
                    Math.round(a[2] + (b[2] - a[2]) * t),
                    Math.round(a[3] + (b[3] - a[3]) * t),
                ];
            }
        }
        return [last[1], last[2], last[3]];
    }

    function heatTheme(usd, k) {
        const c = heatRGB(usd, k);
        const lum = (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255;
        const mx = (v) => Math.round(v + (255 - v) * 0.55);
        return {
            fill: "rgba(" + c[0] + "," + c[1] + "," + c[2] + ",0.95)",
            ring: "rgb(" + mx(c[0]) + "," + mx(c[1]) + "," + mx(c[2]) + ")",
            text: lum > 0.5 ? "#1a1200" : "#ffffff",
        };
    }

    function heatCss(usd, k) {
        const c = heatRGB(usd, k);
        return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")";
    }

    // Компактная подпись: $150K вместо $150.0K, $2.5M вместо $2.50M.
    function fmtCompact(v) {
        return "$" + fmtUsdShort(v).replace(/(\.\d)0+([KM])/, "$1$2").replace(/\.0+([KM])/, "$1");
    }

    function updateMarkers() {
        if (!candleSeries) return;
        if (!state.liqEnabled) { applyMarkers([]); return; }
        const tfSec = state.timeframe * 60;
        const byTime = new Map();

        // Метками помечаем только крупные события — всё остальное рисуем
        // прямоугольниками прямо на свече, чтобы не засорять поле графика.
        const kvol = chartVolScale();
        visibleLiquidations().slice(-400).forEach((item) => {
            if (item.usd < WHALE_USD * kvol) return;
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
                color: heatCss(m.usd, kvol),
                shape: "circle",
                text: "🔥 " + fmtCompact(m.usd),
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

        clusterHits = [];              // актуальные геометрии — только если слой включён
        cvdHits = [];
        oiHits = [];
        if (state.liqEnabled) drawLiqRects(ctx);
        if (state.cvdEnabled) drawCvdTriangles(ctx);
        if (state.oiEnabled) drawOiBalls(ctx);
    }

    // --- Прямоугольники ликвидаций ---------------------------------------------
    // Каждый кластер — скруглённый прямоугольник с суммой. Мелкие (ниже кита)
    // красятся по стороне (маджента/циан), киты — по тепловой шкале объёма.
    // Пороги масштабируются от оборота монеты (для BTC кит — $100K).
    // Совсем мелкие (ниже $2K × масштаб) рисуются чипом без текста; подписанные
    // прямоугольники не налезают друг на друга (жадная раскладка).
    function drawLiqRects(ctx) {
        const tfSec = state.timeframe * 60;
        const kvol = chartVolScale();
        const items = visibleLiquidations();
        if (!items.length || !state.candles.length) return;

        // Свечи по времени: прямоугольник рисуем только там, где свеча реально
        // есть, и прижимаем цену к её диапазону low..high. Биржи отдают цену
        // банкротства, она может уходить далеко от рынка — раньше из-за этого
        // метки улетали в пустоту.
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
            // ликвидации слипались в один прямоугольник.
            const span = hi - lo;
            const level = span > 0 ? Math.round(((price - lo) / span) * 5) : 0;
            const key = t + "_" + level;
            const c = clusters.get(key) || {
                key: key, time: t, lo: lo, hi: hi, level: level,
                longUsd: 0, shortUsd: 0, total: 0, count: 0, longN: 0, shortN: 0, ids: [],
            };
            if (item.side === "SELL") { c.longUsd += item.usd; c.longN += 1; }
            else { c.shortUsd += item.usd; c.shortN += 1; }
            c.total += item.usd;
            c.count += 1;
            if (item.id != null) c.ids.push(item.id);
            clusters.set(key, c);
        });
        if (!clusters.size) return;

        const ts = chart.timeScale();
        const list = Array.from(clusters.values()).sort((a, b) => a.total - b.total);
        const activeKey = pinHitKey || hoverHitKey;   // закреплённый важнее

        clusterHits = [];
        const labeled = [];   // подписанные прямоугольники — защита от налезания
        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        list.forEach((c) => {
            const price = c.hi > c.lo ? c.lo + ((c.hi - c.lo) * c.level) / 5 : c.lo;
            let x, y;
            try {
                x = ts.timeToCoordinate(c.time);
                y = candleSeries.priceToCoordinate(price);
            } catch (e) { return; }
            if (x === null || y === null || x === undefined || y === undefined) return;
            if (x < -40 || x > clusterCanvas.width + 40) return;
            if (y < -30 || y > clusterCanvas.height + 30) return;

            const whale = c.total >= WHALE_USD * kvol;
            const isLong = c.longUsd >= c.shortUsd;
            const theme = whale ? heatTheme(c.total, kvol) : (isLong ? LIQ_COLORS.long : LIQ_COLORS.short);
            const wantLabel = whale || c.total >= LIQ_LABEL_MIN_USD * kvol;

            let bw, bh, label;
            if (wantLabel) {
                ctx.font = "bold 9px 'JetBrains Mono', monospace";
                label = fmtCompact(c.total);
                bw = Math.max(30, Math.ceil(ctx.measureText(label).width) + 12);
                bh = whale ? 16 + Math.min(6, Math.max(-3, (Math.log10(c.total) - 5) * 2.5)) : 15;
            } else {
                bw = 10; bh = 10; label = "";
            }
            const bx = Math.round(x - bw / 2), by = Math.round(y - bh / 2);

            // Подписанный прямоугольник, налезающий на другой подписанный, —
            // рисуем чипом без текста (кроме китов: киты всегда с текстом).
            let showLabel = wantLabel;
            if (wantLabel && !whale) {
                for (let j = 0; j < labeled.length; j++) {
                    const p = labeled[j];
                    if (bx < p.x + p.w + 2 && bx + bw + 2 > p.x &&
                        by < p.y + p.h + 2 && by + bh + 2 > p.y) {
                        showLabel = false;
                        bw = 10; bh = 10;
                        break;
                    }
                }
            }
            const fx = showLabel ? bx : Math.round(x - bw / 2);
            const fy = showLabel ? by : Math.round(y - bh / 2);
            if (showLabel) labeled.push({ x: fx, y: fy, w: bw, h: bh });

            const isActive = activeKey === c.key;
            clusterHits.push({ kind: "liq", x: fx, y: fy, w: bw, h: bh, key: c.key, ids: c.ids,
                time: c.time, price: price, total: c.total, count: c.count,
                longUsd: c.longUsd, shortUsd: c.shortUsd,
                longN: c.longN, shortN: c.shortN, whale: whale });

            const glow = 6 + Math.min(12, (Math.log10(Math.max(c.total, 10)) - 3) * 3);
            const rad = showLabel ? 4 : 3;

            // Тёмная подложка отделяет прямоугольник от тела свечи любого цвета
            rrPath(ctx, fx - 1.5, fy - 1.5, bw + 3, bh + 3, rad + 1);
            ctx.fillStyle = "rgba(5,8,14,0.85)";
            ctx.fill();

            ctx.shadowColor = theme.fill;
            ctx.shadowBlur = glow;
            rrPath(ctx, fx, fy, bw, bh, rad);
            ctx.fillStyle = theme.fill;
            ctx.fill();
            ctx.shadowBlur = 0;
            ctx.lineWidth = whale ? 1.8 : 1.2;
            ctx.strokeStyle = theme.ring;
            ctx.stroke();

            // Активный прямоугольник (наведение/нажатие) — белое кольцо поверх
            if (isActive) {
                rrPath(ctx, fx - 3.5, fy - 3.5, bw + 7, bh + 7, rad + 2);
                ctx.lineWidth = 1.8;
                ctx.strokeStyle = "rgba(255,255,255,0.95)";
                ctx.stroke();
            }

            if (showLabel) {
                ctx.font = "bold 9px 'JetBrains Mono', monospace";
                ctx.fillStyle = theme.text;
                ctx.fillText(label, x, y + 0.5);
            }
        });
        ctx.restore();
    }

    // --- CVD-треугольники в центре свечи -------------------------------------------
    // delta = taker buy volume - taker sell volume (USDT) внутри одной свечи.
    // Треугольник стоит СТРОГО в центре тела свечи: фиолетовый ▲ —
    // преобладали покупатели, оранжевый ▼ — продавцы; размер — величина
    // перевеса относительно p90 видимых свечей, внутри — сумма, когда влезает.
    // Направление читается самой формой, отдельный глиф не нужен.
    // Палитра специально НЕ повторяет свечи (зелёный/красный): треугольник
    // лежит прямо на теле и должен отличаться от него при любом цвете.
    const CVD_COLORS = {
        buy:  { fill: "rgba(139,92,246,0.96)",  ring: "#e6d9ff", text: "#0d0618" },
        sell: { fill: "rgba(255,145,0,0.96)",   ring: "#ffe3b8", text: "#1c0d00" },
    };
    const CVD_MAX_TRI = 40;

    function rrPath(ctx, x, y, w, h, r) {
        r = Math.min(r, w / 2, h / 2);
        ctx.beginPath();
        if (typeof ctx.roundRect === "function") {
            ctx.roundRect(x, y, w, h, r);
        } else {
            ctx.moveTo(x + r, y);
            ctx.arcTo(x + w, y, x + w, y + h, r);
            ctx.arcTo(x + w, y + h, x, y + h, r);
            ctx.arcTo(x, y + h, x, y, r);
            ctx.arcTo(x, y, x + w, y, r);
            ctx.closePath();
        }
    }

    function drawCvdTriangles(ctx) {
        const candles = state.candles;
        if (!candles.length || !chart || !candleSeries) return;
        let maxAbs = 0;
        for (let i = 0; i < candles.length; i++) {
            const d = Math.abs(Number(candles[i].cvd));
            if (isFinite(d) && d > maxAbs) maxAbs = d;
        }
        state.cvdBars = 0;
        if (!(maxAbs > 0)) return;
        // Нормировка по 90-му перцентилю, а не по максимуму: один гигантский
        // бар иначе сплющивает все остальные треугольники до точек без подписей.
        const absVals = [];
        for (let i = 0; i < candles.length; i++) {
            const d = Math.abs(Number(candles[i].cvd));
            if (isFinite(d) && d > 0) absVals.push(d);
        }
        absVals.sort((a, b) => a - b);
        let p90 = absVals.length ? absVals[Math.floor(0.9 * (absVals.length - 1))] : 0;
        if (!(p90 > 0)) p90 = maxAbs;
        const minAbs = Math.max(1000 * chartVolScale(), p90 * 0.05);   // микрошум не рисуем

        // Кандидаты: только значимые свечи. Раскладываем от сильных к слабым,
        // чтобы при тесноте выживали самые важные сигналы.
        const lastIdx = candles.length - 1;
        const cand = [];
        for (let i = 0; i < candles.length; i++) {
            const d = Number(candles[i].cvd);
            if (isFinite(d) && Math.abs(d) >= minAbs) {
                cand.push({ c: candles[i], d: d, live: i === lastIdx });
            }
        }
        cand.sort((a, b) => Math.abs(b.d) - Math.abs(a.d));

        const ts = chart.timeScale();
        const W = clusterCanvas.width, H = clusterCanvas.height;
        const placed = [];   // центры и радиусы — защита от налезания
        let drawn = 0;

        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";

        for (let k = 0; k < cand.length && drawn < CVD_MAX_TRI; k++) {
            const c = cand[k].c, d = cand[k].d;
            const buy = d > 0;
            // центр ТЕЛА свечи — середина между open и close
            const mid = (Number(c.open) + Number(c.close)) / 2;
            let x, y;
            try {
                x = ts.timeToCoordinate(c.time);
                y = candleSeries.priceToCoordinate(mid);
            } catch (e) { continue; }
            if (x === null || x === undefined || y === null || y === undefined) continue;
            const kk = Math.min(Math.sqrt(Math.abs(d) / p90), 1.2);
            const halfW = 9 + 12 * kk;
            const h = halfW * 1.5;
            const rr = Math.max(halfW, h / 2);
            if (x < -rr - 10 || x > W + rr + 10 || y < -rr - 10 || y > H + rr + 10) continue;

            // налезает на уже нарисованный треугольник? пропускаем более слабый
            let clash = false;
            for (let j = 0; j < placed.length; j++) {
                const q = placed[j];
                const dx = x - q.x, dy = y - q.y;
                const rad = rr + q.r + 2;
                if (dx * dx + dy * dy <= rad * rad) { clash = true; break; }
            }
            if (clash) continue;
            placed.push({ x: x, y: y, r: rr });
            const triKey = "cvd_" + c.time;
            cvdHits.push({ kind: "cvd", key: triKey, x: x, y: y, r: rr,
                           time: c.time, d: d, buy: buy, live: cand[k].live, p90: p90, c: c });

            // Треугольник центрируем по y: вершина выше/ниже центра на h/2.
            const apexY = buy ? y - h / 2 : y + h / 2;
            const cy = buy ? apexY + h * 0.68 : apexY - h * 0.68;
            const theme = buy ? CVD_COLORS.buy : CVD_COLORS.sell;

            // Тёмная подложка — треугольник читается на свече любого цвета
            triPath(ctx, x, buy ? apexY - 1.6 : apexY + 1.6, halfW + 1.6, h + 1.6, buy);
            ctx.fillStyle = "rgba(5,8,14,0.88)";
            ctx.fill();

            ctx.shadowColor = theme.fill;
            ctx.shadowBlur = cand[k].live ? 16 : 9;
            triPath(ctx, x, apexY, halfW, h, buy);
            ctx.fillStyle = theme.fill;
            ctx.fill();
            ctx.shadowBlur = 0;
            ctx.lineWidth = 1.4;
            ctx.strokeStyle = theme.ring;
            ctx.stroke();

            // Активный треугольник (наведение/закреп) — белая обводка поверх
            if (shapeIsActive("cvd", triKey)) {
                triPath(ctx, x, apexY, halfW, h, buy);
                ctx.lineWidth = 1.8;
                ctx.strokeStyle = "rgba(255,255,255,0.9)";
                ctx.stroke();
            }

            // Сумма — если влезает; шрифт подбираем под размер треугольника.
            const val = fmtCompact(Math.abs(d));
            let vFont = 0;
            const vSizes = [8, 7, 6.5];
            for (let f = 0; f < vSizes.length; f++) {
                ctx.font = "bold " + vSizes[f] + "px 'JetBrains Mono', monospace";
                if (ctx.measureText(val).width <= halfW * 1.3) { vFont = vSizes[f]; break; }
            }
            if (vFont > 0) {
                ctx.fillStyle = theme.text;
                ctx.fillText(val, x, cy + 0.5);
            }
            drawn += 1;
        }
        ctx.restore();
        state.cvdBars = drawn;
    }

    function updateCvdStat() {
        paintCvdBox();   // бокс CVD в шапке — из тех же свечей
        if (!cvdStatEl) return;
        // слой на графике и цифры живут отдельно: выключенные треугольники
        // не должны гасить ни бокс в шапке, ни живой индикатор у кнопки
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

    // Ликвидации текущей свечи: сумма и число событий. Те же фильтры,
    // что у прямоугольников на графике (монета, биржи, мин. сумма).
    function updateLiqStat() {
        if (!liqStatEl) return;
        const tfSec = state.timeframe * 60;
        const bucket = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
        let sum = 0, n = 0;
        const items = visibleLiquidations();
        for (let i = 0; i < items.length; i++) {
            const t = Math.floor((Number(items[i].timestamp) || 0) / tfSec) * tfSec;
            if (t !== bucket) continue;
            sum += Number(items[i].usd) || 0;
            n += 1;
        }
        if (!n) {
            liqStatEl.textContent = "—";
            liqStatEl.className = "live-stat";
            return;
        }
        liqStatEl.textContent = "$" + fmtUsdShort(sum) + "·" + n;
        liqStatEl.className = "live-stat on-liq";
    }

    // Объём профиля: сумма ликвидаций видимого диапазона — то, что
    // раскладывают полосы индикатора.
    function updateProfileStat() {
        if (!profileStatEl) return;
        const range = profilePriceRange();
        let sum = 0;
        if (range) {
            const pad = state.timeframe * 60;
            const items = visibleLiquidations();
            for (let i = 0; i < items.length; i++) {
                const ts = Number(items[i].timestamp) || 0;
                if (ts < range.from - pad || ts > range.to + pad) continue;
                sum += Number(items[i].usd) || 0;
            }
        }
        if (!(sum > 0)) {
            profileStatEl.textContent = "—";
            profileStatEl.className = "live-stat";
            return;
        }
        profileStatEl.textContent = "$" + fmtUsdShort(sum);
        profileStatEl.className = "live-stat on-profile";
    }

    // Изменение OI текущей свечи — пара к CVD-индикатору.
    function updateOiStat() {
        if (!oiStatEl) return;
        const last = state.candles[state.candles.length - 1];
        const d = last ? Number(last.oiChg) : NaN;
        if (!isFinite(d)) {
            oiStatEl.textContent = "OI —";
            oiStatEl.className = "live-stat";
            return;
        }
        const up = d >= 0;
        oiStatEl.textContent = (up ? "● +$" : "● −$") + fmtUsdShort(Math.abs(d));
        oiStatEl.className = "live-stat " + (up ? "up" : "down");
    }

    // Живые цифры у кнопок слоёв (+ бокс CVD в шапке внутри updateCvdStat).
    // Слои на графике и цифры живут отдельно: выключенный слой прячет
    // только фигуры, цифры продолжают обновляться.
    function updateLiveStats() {
        updateCvdStat();
        updateLiqStat();
        updateProfileStat();
        updateOiStat();
    }

    // --- Боксы статистики: ликвидации / CVD / OI --------------------------------
    // Три окошка в шапке, у каждого — меню выбора периода. Окна выбираются
    // из STAT_WIN_DEFS, выбор помнится в localStorage отдельно для бокса.
    const STAT_WIN_DEFS = [
        { key: "1m", sec: 60 }, { key: "5m", sec: 300 },
        { key: "15m", sec: 900 }, { key: "30m", sec: 1800 },
        { key: "1h", sec: 3600 }, { key: "4h", sec: 14400 },
        { key: "24h", sec: 86400 },
    ];
    const OI_WIN_KEY = { "1m": "m1", "5m": "m5", "15m": "m15", "30m": "m30",
                         "1h": "h1", "4h": "h4", "24h": "h24" };
    function statWinSec(key) {
        for (let i = 0; i < STAT_WIN_DEFS.length; i++)
            if (STAT_WIN_DEFS[i].key === key) return STAT_WIN_DEFS[i].sec;
        return 86400;
    }
    (function loadStatWin() {
        const kinds = ["liq", "cvd", "oi"];
        for (let i = 0; i < kinds.length; i++) {
            try {
                const v = localStorage.getItem("licvid.statwin." + kinds[i]);
                if (v && OI_WIN_KEY[v]) state.statWin[kinds[i]] = v;
            } catch (e) { /* ignore */ }
        }
    })();
    function setStatWin(kind, key) {
        if (!OI_WIN_KEY[key]) return;
        state.statWin[kind] = key;
        try { localStorage.setItem("licvid.statwin." + kind, key); } catch (e) {}
        paintStatBox(kind);
    }

    // Значения боксов за окно key: ликвидации — из /api/stats,
    // CVD — сумма поля cvd загруженных свечей, OI — из /api/oi.
    function statLiqVal(key) {
        const d = state.lastStats;
        if (!d) return null;
        const t = Number(d["total_usd_" + key]);
        if (!isFinite(t)) return null;
        return { total: t,
                 longs: Number(d["longs_usd_" + key]) || 0,
                 shorts: Number(d["shorts_usd_" + key]) || 0 };
    }
    function statCvdVal(key) {
        if (!state.candles.length) return null;
        const since = Date.now() / 1000 - statWinSec(key);
        let buy = 0, sell = 0, ok = false;
        for (let i = 0; i < state.candles.length; i++) {
            const c = state.candles[i];
            if ((Number(c.time) || 0) < since) continue;
            const d = Number(c.cvd);
            if (!isFinite(d)) continue;
            ok = true;
            if (d >= 0) buy += d; else sell -= d;
        }
        return ok ? { net: buy - sell, buy: buy, sell: sell } : null;
    }
    function statOiVal(key) {
        const ch = (state.lastOI && state.lastOI.changes) || {};
        const c = ch[OI_WIN_KEY[key]];
        return c ? Number(c.usd) : NaN;
    }

    function statHeadEls(kind) {
        return kind === "liq"
            ? { box: statBoxLiq, win: statLiqWin, value: statLiqValue, sub: statLiqSub, menu: statMenuLiq }
            : kind === "cvd"
            ? { box: statBoxCvd, win: statCvdWin, value: statCvdValue, sub: statCvdSub, menu: statMenuCvd }
            : { box: statBoxOi, win: statOiWin, value: statOiValue, sub: statOiSub, menu: statMenuOi };
    }

    // Текст строки меню/шапки: { txt, cls, title }
    function statCell(kind, key) {
        if (kind === "liq") {
            const v = statLiqVal(key);
            if (!v) return { txt: "—", cls: "", title: "" };
            return { txt: "$" + fmtUsdShort(v.total), cls: "",
                     title: "L $" + fmtUsdFull(v.longs) + " / S $" + fmtUsdFull(v.shorts) };
        }
        if (kind === "cvd") {
            const v = statCvdVal(key);
            if (!v) return { txt: "—", cls: "", title: "" };
            const up = v.net >= 0;
            return { txt: (up ? "+$" : "−$") + fmtUsdShort(Math.abs(v.net)),
                     cls: up ? "cvd-pos" : "cvd-neg",
                     title: I18n.t("stats.cvd_title") + ": " + (up ? "+" : "−") +
                            "$" + fmtUsdFull(Math.abs(v.net)) };
        }
        const v = statOiVal(key);
        if (!isFinite(v)) return { txt: "—", cls: "", title: "" };
        const up = v >= 0;
        return { txt: (up ? "+$" : "−$") + fmtUsdShort(Math.abs(v)),
                 cls: up ? "oi-pos" : "oi-neg",
                 title: I18n.t("stats.oi_title") + ": " + (up ? "+" : "−") +
                        "$" + fmtUsdFull(Math.abs(v)) };
    }

    function paintStatBox(kind) {
        const E = statHeadEls(kind);
        const key = state.statWin[kind];
        if (E.win) E.win.textContent = I18n.t("stats.w" + key);
        const cell = statCell(kind, key);
        if (E.value) {
            E.value.textContent = cell.txt;
            E.value.className = "stat-box-value" +
                (kind === "liq" ? " text-gold" : kind === "cvd" ? " cvd-val" : " oi-val") +
                (cell.cls ? " " + cell.cls : "");
            if (cell.title) E.value.title = cell.title;
            else E.value.removeAttribute("title");
        }
        if (E.sub) {
            if (kind === "liq") {
                const v = statLiqVal(key);
                E.sub.innerHTML = v
                    ? '<span class="sub-long">L $' + fmtUsdShort(v.longs) + "</span> · " +
                      '<span class="sub-short">S $' + fmtUsdShort(v.shorts) + "</span>"
                    : "—";
            } else if (kind === "cvd") {
                const v = statCvdVal(key);
                E.sub.textContent = v
                    ? "+$" + fmtUsdShort(v.buy) + " / −$" + fmtUsdShort(v.sell)
                    : "—";
            } else {
                const t = state.lastOI ? Number(state.lastOI.total_usd) : NaN;
                E.sub.innerHTML = isFinite(t)
                    ? 'Σ <span class="sub-total">$' + fmtUsdShort(t) + "</span>"
                    : "—";
            }
        }
        if (kind !== "liq") {
            const base = chartSymbol().split("_")[0];
            const coins = E.box ? E.box.querySelectorAll(kind === "cvd" ? ".cvd-coin" : ".oi-coin")
                                : document.querySelectorAll(kind === "cvd" ? ".cvd-coin" : ".oi-coin");
            for (let i = 0; i < coins.length; i++) coins[i].textContent = base;
        }
        if (openStatMenu === kind) renderStatMenu(kind);
    }

    function renderStatMenu(kind) {
        const E = statHeadEls(kind);
        if (!E.menu) return;
        const cur = state.statWin[kind];
        let html = "";
        for (let i = 0; i < STAT_WIN_DEFS.length; i++) {
            const k = STAT_WIN_DEFS[i].key;
            const cell = statCell(kind, k);
            html += '<button type="button" role="option" class="stat-menu-row' +
                (k === cur ? " sel" : "") + '" data-k="' + k + '"' +
                (cell.title ? ' title="' + cell.title.replace(/"/g, "&quot;") + '"' : "") + ">" +
                '<span class="w">' + I18n.t("stats.w" + k) + "</span>" +
                '<span class="v' + (cell.cls ? " " + cell.cls : "") + '">' + cell.txt + "</span></button>";
        }
        E.menu.innerHTML = html;
        Array.prototype.forEach.call(E.menu.children, (row) => {
            row.addEventListener("click", (ev) => {
                ev.stopPropagation();
                setStatWin(kind, row.dataset.k);
                closeStatMenus();
            });
        });
    }

    let openStatMenu = null;
    function closeStatMenus() {
        openStatMenu = null;
        const pairs = [[statBoxLiq, statMenuLiq], [statBoxCvd, statMenuCvd], [statBoxOi, statMenuOi]];
        for (let i = 0; i < pairs.length; i++) {
            if (pairs[i][0]) pairs[i][0].classList.remove("open");
            if (pairs[i][1]) pairs[i][1].classList.add("hidden");
        }
    }
    function toggleStatMenu(kind) {
        if (openStatMenu === kind) { closeStatMenus(); return; }
        closeStatMenus();
        const E = statHeadEls(kind);
        const head = kind === "liq" ? statLiqHead : kind === "cvd" ? statCvdHead : statOiHead;
        if (!E.menu || !head) return;
        openStatMenu = kind;
        renderStatMenu(kind);
        E.menu.classList.remove("hidden");
        // fixed-позиция под боксом: не обрезается скроллом шапки на мобильных
        const r = head.getBoundingClientRect();
        let left = r.left + (window.scrollX || 0);
        left = Math.max(8, Math.min(left, (window.innerWidth || 1024) - E.menu.offsetWidth - 8));
        let top = r.bottom + (window.scrollY || 0) + 6;
        if (top + E.menu.offsetHeight > (window.scrollY || 0) + (window.innerHeight || 768) - 8)
            top = Math.max(8, r.top + (window.scrollY || 0) - E.menu.offsetHeight - 6);
        E.menu.style.left = left + "px";
        E.menu.style.top = top + "px";
        if (E.box) E.box.classList.add("open");
    }
    function setupStatBoxes() {
        const pairs = [[statLiqHead, "liq"], [statCvdHead, "cvd"], [statOiHead, "oi"]];
        for (let i = 0; i < pairs.length; i++) {
            if (pairs[i][0]) pairs[i][0].addEventListener("click", (ev) => {
                ev.stopPropagation();
                toggleStatMenu(pairs[i][1]);
            });
        }
        document.addEventListener("click", (ev) => {
            if (!ev.target.closest || !ev.target.closest(".stat-box")) closeStatMenus();
        });
        document.addEventListener("keydown", (ev) => {
            if (ev.key === "Escape") closeStatMenus();
        });
        document.addEventListener("scroll", () => closeStatMenus(), true);
        window.addEventListener("resize", () => closeStatMenus());
    }

    // --- Табло CVD в шапке: перевес тейкеров по монете ГРАФИКА ---------------
    // Суммируем поле cvd загруженных свечей за выбранное окно. Свечи живые
    // (текущая обновляется каждым тиком), поэтому табло всегда свежее.
    function paintCvdBox() { paintStatBox("cvd"); }

    // --- Бокс OI в шапке: изменение за выбранное окно + суммарный интерес ----
    function paintOiBox() { paintStatBox("oi"); }

    let oiRequestId = 0;
    async function fetchOI() {
        const my = ++oiRequestId;
        try {
            const r = await fetch("/api/oi?symbol=" + encodeURIComponent(chartSymbol()));
            const data = await r.json();
            if (my !== oiRequestId) return;   // пришёл новый запрос — этот стар
            state.lastOI = data;
            paintOiBox();
        } catch (e) { /* ignore */ }
    }

    // --- OI-шарики над/под свечой ------------------------------------------------
    // oiChg = изменение открытого интереса за свечу (USD, биржи с историей).
    // Рост — шарик над хаем, падение — под лоем; внутри — сумма.
    // Цвет — интуитивный светофор по направлению и величине: рост зеленеет
    // от бледного к едкому с тиром, падение краснеет от бледного к ядрёному.
    // Шарики висят ВНЕ тела свечи, поэтому со свечами не сливаются.
    const OI_BALL = {
        up:   { shades: [[190, 242, 200], [134, 239, 172], [34, 197, 94], [0, 230, 118]],
                ring: "#d8ffe6", text: "#04160b" },
        down: { shades: [[252, 200, 200], [248, 113, 113], [239, 68, 68], [255, 42, 95]],
                ring: "#ffe3e6", text: "#1c060d" },
    };
    const OI_MAX_BALLS = 60;
    // Тиры OI по |Δ|: мелочь (<$1M × масштаб) — мини-значок без подписи,
    // крупняк — больше, ярче и с сильным свечением.
    const OI_TIER_MIN = 1000000;
    const OI_TIER_STYLE = [
        null,   // 0 — мини
        { mult: 1.0,  font: 0, glow: 8  },   // $1M+
        { mult: 1.22, font: 1, glow: 12 },   // $5M+
        { mult: 1.45, font: 2, glow: 16 },   // $20M+
    ];
    function oiTier(abs, k) {
        k = k || 1;
        if (abs >= 20000000 * k) return 3;
        if (abs >= 5000000 * k) return 2;
        if (abs >= OI_TIER_MIN * k) return 1;
        return 0;
    }
    function oiTheme(up, tier) {
        const b = up ? OI_BALL.up : OI_BALL.down;
        const c = b.shades[Math.min(3, Math.max(0, tier))];
        const st = OI_TIER_STYLE[tier] || OI_TIER_STYLE[1];
        return {
            fill: "rgba(" + c[0] + "," + c[1] + "," + c[2] + ",0.95)",
            ring: b.ring, text: b.text, glow: st.glow,
        };
    }

    function triPath(ctx, x, apexY, halfW, h, up) {
        ctx.beginPath();
        if (up) {
            ctx.moveTo(x, apexY);
            ctx.lineTo(x + halfW, apexY + h);
            ctx.lineTo(x - halfW, apexY + h);
        } else {
            ctx.moveTo(x, apexY);
            ctx.lineTo(x + halfW, apexY - h);
            ctx.lineTo(x - halfW, apexY - h);
        }
        ctx.closePath();
    }

    function drawOiBalls(ctx) {
        const candles = state.candles;
        if (!candles.length || !chart || !candleSeries) return;
        // Значимость — по p90, как у треугольников CVD: один выброс не должен
        // гасить остальные шарики.
        const absVals = [];
        for (let i = 0; i < candles.length; i++) {
            const d = Math.abs(Number(candles[i].oiChg));
            if (isFinite(d) && d > 0) absVals.push(d);
        }
        state.oiBars = 0;
        if (!absVals.length) return;
        absVals.sort((a, b) => a - b);
        const p90 = absVals[Math.floor(0.9 * (absVals.length - 1))] || 0;
        if (!(p90 > 0)) return;
        const kvol = chartVolScale();
        const minAbs = Math.max(1000 * kvol, p90 * 0.05);

        const cand = [];
        for (let i = 0; i < candles.length; i++) {
            const d = Number(candles[i].oiChg);
            if (isFinite(d) && Math.abs(d) >= minAbs) {
                cand.push({ c: candles[i], d: d, live: i === candles.length - 1 });
            }
        }
        cand.sort((a, b) => Math.abs(b.d) - Math.abs(a.d));

        const ts = chart.timeScale();
        const W = clusterCanvas.width, H = clusterCanvas.height;
        const placed = [];
        let drawn = 0;
        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        for (let k = 0; k < cand.length && drawn < OI_MAX_BALLS; k++) {
            const c = cand[k].c, d = cand[k].d;
            const up = d > 0;
            const ref = up ? Number(c.high) : Number(c.low);
            if (!isFinite(ref)) continue;
            let x, yRef;
            try {
                x = ts.timeToCoordinate(c.time);
                yRef = candleSeries.priceToCoordinate(ref);
            } catch (e) { continue; }
            if (x === null || x === undefined || yRef === null || yRef === undefined) continue;

            // Размер — по тиру величины: мелочь (<$1M × масштаб) всегда мини
            // без подписи, крупняку радиус и кегль наращиваем.
            const abs = Math.abs(d);
            const tier = oiTier(abs, kvol);
            const val = fmtCompact(abs);
            let r, fs = 0;
            if (tier > 0) {
                ctx.font = "bold 8px 'JetBrains Mono', monospace";
                const tw8 = ctx.measureText(val).width;
                if (tw8 <= 1.9 * 26 - 5) {
                    r = Math.max(11, (tw8 + 5) / 1.9);
                    const sizes = [8, 7, 6.5];
                    for (let f = 0; f < sizes.length; f++) {
                        ctx.font = "bold " + sizes[f] + "px 'JetBrains Mono', monospace";
                        if (ctx.measureText(val).width <= 1.9 * r - 5) { fs = sizes[f]; break; }
                    }
                }
            }
            if (!fs) { r = 6; }
            else if (tier >= 2) {
                const st = OI_TIER_STYLE[tier];
                fs = Math.min(10, fs + st.font);
                ctx.font = "bold " + fs + "px 'JetBrains Mono', monospace";
                r = Math.min(30, Math.max(r * st.mult, (ctx.measureText(val).width + 5) / 1.9));
            }
            const gap = 10 + tier * 2;   // чем крупнее, тем дальше от свечи
            const cy = up ? yRef - gap - r : yRef + gap + r;
            if (x < -r || x > W + r || cy < -r || cy > H + r) continue;

            // Налезает на уже нарисованный? пропускаем более слабый.
            let clash = false;
            for (let j = 0; j < placed.length; j++) {
                const q = placed[j];
                const dx = x - q.x, dy = cy - q.y;
                const rad = r + q.r + 2;
                if (dx * dx + dy * dy <= rad * rad) { clash = true; break; }
            }
            if (clash) continue;
            placed.push({ x: x, y: cy, r: r });
            const ballKey = "oi_" + c.time;
            oiHits.push({ kind: "oi", key: ballKey, x: x, y: cy, r: r,
                          time: c.time, d: d, up: up, tier: tier, live: cand[k].live, c: c });

            const theme = oiTheme(up, tier);
            // Тёмная подложка — читается на свече любого цвета
            ctx.beginPath();
            ctx.arc(x, cy, r + 1.6, 0, 2 * Math.PI);
            ctx.fillStyle = "rgba(5,8,14,0.88)";
            ctx.fill();

            ctx.shadowColor = theme.fill;
            ctx.shadowBlur = theme.glow;
            ctx.beginPath();
            ctx.arc(x, cy, r, 0, 2 * Math.PI);
            ctx.fillStyle = theme.fill;
            ctx.fill();
            ctx.shadowBlur = 0;
            ctx.lineWidth = 1.3;
            ctx.strokeStyle = theme.ring;
            ctx.stroke();

            // Активный шарик (наведение/закреп) — белое кольцо поверх
            if (shapeIsActive("oi", ballKey)) {
                ctx.beginPath();
                ctx.arc(x, cy, r + 3.5, 0, 2 * Math.PI);
                ctx.lineWidth = 1.8;
                ctx.strokeStyle = "rgba(255,255,255,0.9)";
                ctx.stroke();
            }

            if (fs > 0) {
                ctx.fillStyle = theme.text;
                ctx.fillText(val, x, cy + 0.5);
            }
            drawn++;
        }
        ctx.restore();
        state.oiBars = drawn;
    }

    // --- Наведение/нажатие на фигуры: прямоугольники, CVD, OI -------------------
    // Порядок проверки — обратный отрисовке: шарики поверх треугольников
    // поверх прямоугольников. У фигур хит-тест кругом с допуском 4px.
    function hitAt(px, py) {
        for (let i = oiHits.length - 1; i >= 0; i--) {
            const b = oiHits[i];
            const dx = px - b.x, dy = py - b.y, rr = b.r + 4;
            if (dx * dx + dy * dy <= rr * rr) return b;
        }
        for (let i = cvdHits.length - 1; i >= 0; i--) {
            const b = cvdHits[i];
            const dx = px - b.x, dy = py - b.y, rr = b.r + 4;
            if (dx * dx + dy * dy <= rr * rr) return b;
        }
        for (let i = clusterHits.length - 1; i >= 0; i--) {
            const b = clusterHits[i];
            if (px >= b.x - 4 && px <= b.x + b.w + 4 &&
                py >= b.y - 4 && py <= b.y + b.h + 4) return b;
        }
        return null;
    }

    function shapeIsActive(kind, key) {
        if (shapePin) return shapePin.kind === kind && shapePin.key === key;
        return !!shapeHover && shapeHover.kind === kind && shapeHover.key === key;
    }

    function applyFeedHighlight(ball, pin) {
        if (pin) {
            pinHitKey = ball ? ball.key : null;
            if (!ball) hoverHitKey = null;
        } else {
            hoverHitKey = ball ? ball.key : null;
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

    // Окно фигуры: наведение показывает, уход курсора прячет (если не закреплено
    // кликом). Закреп одновременно один: прямоугольник или фигура CVD/OI.
    function hoverShape(hit) {
        const key = hit ? hit.kind + ":" + hit.key : null;
        const cur = shapeHover ? shapeHover.kind + ":" + shapeHover.key : null;
        if (key === cur) return;
        shapeHover = hit ? { kind: hit.kind, key: hit.key } : null;
        if (hit) openShapeModal(hit.kind, hit);
        else hideShapeModal();
        queueRedraw();
    }

    function pinShape(hit) {
        shapePin = { kind: hit.kind, key: hit.key };
        shapeHover = null;
        queueRedraw();
    }

    function unpinShape() {
        if (!shapePin && !shapeHover) return;
        shapePin = null;
        shapeHover = null;
        queueRedraw();
    }

    function hideShapeModal() {
        if (shapePin) return;   // закреплено кликом — не пропадает
        if (!state.modalItem) {
            detailModal.classList.add("hidden");
            detailModal.classList.remove("peek", "peek-pinned");
        }
    }

    function setupClusterInteraction() {
        if (!chart || !chart.subscribeCrosshairMove || !chart.subscribeClick) return;
        try {
            // наведение (и палец на телефоне при движении по графику)
            chart.subscribeCrosshairMove((param) => {
                if (!param || !param.point) {
                    if (!pinHitKey && !shapePin) {
                        applyFeedHighlight(null, false);
                        hoverShape(null);
                    }
                    return;
                }
                if (pinHitKey || shapePin) return;   // закреплено — не дёргаем
                const hit = hitAt(param.point.x, param.point.y);
                if (!hit) {
                    hoverShape(null);
                    applyFeedHighlight(null, false);
                } else if (hit.kind === "liq") {
                    // прямоугольник: окно кластера ПЛЮС подсветка строк в ленте
                    hoverShape(hit);
                    applyFeedHighlight(hit, false);
                } else {
                    applyFeedHighlight(null, false);
                    hoverShape(hit);
                }
            });
            // клик/тап: закрепить фигуру; повторный клик по ней или клик
            // мимо — снять закреп
            chart.subscribeClick((param) => {
                const hit = (param && param.point)
                    ? hitAt(param.point.x, param.point.y) : null;
                if (!hit) {
                    applyFeedHighlight(null, true);
                    unpinShape();
                    hideShapeModal();
                } else if (hit.kind === "liq") {
                    if (hit.key === pinHitKey) {
                        applyFeedHighlight(null, true);
                        unpinShape();
                        hideShapeModal();
                    } else {
                        applyFeedHighlight(hit, true);
                        pinShape(hit);
                        openShapeModal(hit.kind, hit);
                    }
                } else if (shapePin && hit.key === shapePin.key) {
                    unpinShape();
                    hideShapeModal();
                } else {
                    applyFeedHighlight(null, true);
                    pinShape(hit);
                    openShapeModal(hit.kind, hit);
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
                ctx.fillText(fmtCompact(b.total), x0 + rowW + 4, y);
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
        const whale = item.usd >= 100000 * volScale(item.symbol);
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
        // строки пересоздаются — закреплённая подсветка шарика гаснет,
        // окно кластера (ссылалось на старые строки) — тоже
        pinHitKey = null;
        hoverHitKey = null;
        if (shapePin && shapePin.kind === "liq") {
            unpinShape();
            hideShapeModal();
        }
        const rows = state.liquidations.filter(passesFeedFilter).slice(-150).reverse();
        feedTbody.innerHTML = "";
        const frag = document.createDocumentFragment();
        rows.forEach((item) => {
            const tr = feedRow(item);
            // гасим анимацию «новая строка», но подсветку кита оставляем
            tr.className = tr.classList.contains("feed-row-whale") ? "feed-row-whale" : "";
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
        unpinShape();
        detailModal.classList.remove("hidden");
        detailModal.classList.remove("peek", "peek-pinned");
    }

    // --- Окно фигуры: кластер ликвидаций, CVD, OI ---------------------------------
    // Тот же попап, что у строк ленты, но фон некликабельный и прозрачный:
    // наведение не должно перекрывать график, иначе фигуру не «отпустить».
    // Закреплённое кликом окно затемняется, но клики всё равно проходят
    // сквозь фон — снять закреп можно кликом мимо фигуры или по крестику.
    function openShapeModal(kind, hit) {
        state.modalItem = null;
        const sym = pretty(chartSymbol());
        const tfSec = state.timeframe * 60;
        const range = I18n.time(hit.time) + "–" + I18n.time(Number(hit.time) + tfSec);
        // Свеча фигуры — для движения цены и бейджа «формируется».
        // У кластера свечи в хите нет — ищем по времени.
        let bar = hit.c || null, live = !!hit.live;
        if (!bar && state.candles.length) {
            for (let i = 0; i < state.candles.length; i++) {
                if (Number(state.candles[i].time) === Number(hit.time)) {
                    bar = state.candles[i];
                    break;
                }
            }
            if (bar) {
                live = Number(hit.time) ===
                    Number(state.candles[state.candles.length - 1].time);
            }
        }
        const open = bar ? Number(bar.open) : NaN;
        const close = bar ? Number(bar.close) : NaN;
        let moveHtml = "—";
        if (isFinite(open) && isFinite(close) && open > 0) {
            const pct = ((close - open) / open) * 100;
            const cls = pct > 0 ? "text-success" : pct < 0 ? "text-danger" : "";
            moveHtml = fmtPrice(open) + " → " + fmtPrice(close) +
                ' <span class="' + cls + '">(' + (pct > 0 ? "+" : "") +
                pct.toFixed(2) + "%)</span>";
        }
        const liveHtml = live ? " · <em>" + I18n.t("modal.live") + "</em>" : "";
        const usdRow = (inner) => "<p><strong>" + I18n.t("modal.usd") + "</strong> " +
            '<span style="font-size:1.1rem;font-weight:800;color:var(--color-gold)">' +
            inner + "</span></p>";
        let dirHtml, valRowHtml, extraRows, about;
        if (kind === "liq") {
            modalTitle.textContent = I18n.t("modal.clust_of", { sym: sym });
            const isLong = hit.longUsd >= hit.shortUsd;
            dirHtml = isLong
                ? '<span class="badge-side long">' + I18n.t("modal.long_desc") + "</span>"
                : '<span class="badge-side short">' + I18n.t("modal.short_desc") + "</span>";
            valRowHtml = usdRow("$" + fmtUsdFull(hit.total) +
                (hit.whale ? " · 🐋 " + I18n.t("modal.whale") : ""));
            extraRows =
                "<p><strong>" + I18n.t("modal.level") + "</strong> ≈ " +
                fmtPrice(hit.price) + "</p>" +
                "<p><strong>" + I18n.t("modal.events") + "</strong> " + hit.count +
                " · " + hit.longN + " LONG / " + hit.shortN + " SHORT</p>" +
                "<p><strong>" + I18n.t("modal.split") + "</strong> " +
                '<span class="badge-side long">L $' + fmtUsdShort(hit.longUsd) + "</span> " +
                '<span class="badge-side short">S $' + fmtUsdShort(hit.shortUsd) + "</span></p>";
            about = I18n.t("modal.clust_about");
        } else if (kind === "cvd") {
            const abs = Math.abs(hit.d);
            const sign = hit.d > 0 ? "+" : hit.d < 0 ? "−" : "";
            const valColor = hit.buy ? "#a78bfa" : "#ffab4a";
            modalTitle.textContent = I18n.t("modal.cvd_of", { sym: sym });
            dirHtml = hit.buy
                ? '<span class="badge-cvd-buy">' + I18n.t("modal.cvd_buy") + "</span>"
                : '<span class="badge-cvd-sell">' + I18n.t("modal.cvd_sell") + "</span>";
            valRowHtml = "<p><strong>" + I18n.t("modal.usd") + '</strong> <span style="font-size:1.1rem;font-weight:800;color:' +
                valColor + '">' + sign + "$" + fmtUsdFull(abs) + "</span></p>";
            // Сила относительно p90 видимых свечей: ≥1 — топ-10% по величине.
            let strengthHtml = "—";
            if (hit.p90 > 0) {
                const r = abs / hit.p90;
                strengthHtml = I18n.t("modal.p90mult", { r: r.toFixed(1) }) +
                    (r >= 1 ? " · " + I18n.t("modal.top10") : "");
            }
            extraRows = "<p><strong>" + I18n.t("modal.strength") + "</strong> " +
                strengthHtml + "</p>";
            about = I18n.t("modal.cvd_about");
        } else {
            modalTitle.textContent = I18n.t("modal.oi_of", { sym: sym });
            dirHtml = hit.up
                ? '<span class="badge-oi-up">' + I18n.t("modal.oi_up") + "</span>"
                : '<span class="badge-oi-down">' + I18n.t("modal.oi_down") + "</span>";
            valRowHtml = "<p><strong>" + I18n.t("modal.usd") + '</strong> <span style="font-size:1.1rem;font-weight:800;color:' +
                (hit.up ? "#4ade80" : "#fb7185") + '">' +
                (hit.d > 0 ? "+" : hit.d < 0 ? "−" : "") +
                "$" + fmtUsdFull(Math.abs(hit.d)) + "</span></p>";
            // Тир с порогом в масштабе оборота монеты (для GRAM тиры в 1000 раз ниже).
            const k = chartVolScale();
            const tierHtml = hit.tier > 0
                ? "T" + hit.tier + " · ≥ " + fmtCompact([0, 1000000, 5000000, 20000000][hit.tier] * k)
                : I18n.t("modal.tier_mini") + " · < " + fmtCompact(1000000 * k);
            extraRows = "<p><strong>" + I18n.t("modal.tier") + "</strong> " +
                tierHtml + "</p>";
            about = I18n.t("modal.oi_about");
        }
        modalBody.innerHTML =
            "<p><strong>" + I18n.t("modal.candle") + "</strong> " + range + liveHtml + "</p>" +
            "<p><strong>" + I18n.t("modal.direction") + "</strong> " + dirHtml + "</p>" +
            valRowHtml +
            extraRows +
            "<p><strong>" + I18n.t("modal.price_move") + "</strong> " + moveHtml + "</p>" +
            '<p class="modal-about">' + about + "</p>";
        detailModal.classList.remove("hidden");
        const pinned = !!shapePin;
        detailModal.classList.toggle("peek", !pinned);
        detailModal.classList.toggle("peek-pinned", pinned);
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
            { el: $("oi-toggle"), skey: "oiEnabled", store: "licvid.oiEnabled",
              on: "chart.oi_on", off: "chart.oi_off" },
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
                    // с прячущихся прямоугольников снимаем подсветку ленты и окно
                    pinHitKey = null;
                    hoverHitKey = null;
                    applyFeedHighlight(null, true);
                    unpinShape();
                    hideShapeModal();
                }
                // слой фигуры погас — её окно тоже прячем (окно строки
                // ленты не трогаем — у него свой крестик)
                if (shapePin && ((shapePin.kind === "cvd" && d.skey === "cvdEnabled") ||
                                 (shapePin.kind === "oi" && d.skey === "oiEnabled")) &&
                        !state[d.skey]) {
                    unpinShape();
                    hideShapeModal();
                }
                paint();
                updateMarkers();
                updateLiveStats();
                queueRedraw();
            });
            I18n.onChange(paint);
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

    // --- Разворот графика на весь экран (как на биржах) -----------------------
    function setupChartExpand() {
        const btn = $("chart-expand");
        if (!btn || !chartSection) return;
        const paint = () => {
            const fs = chartSection.classList.contains("fullscreen");
            btn.classList.toggle("active", fs);
            btn.title = I18n.t(fs ? "chart.collapse_title" : "chart.expand_title");
        };
        const setFs = (on) => {
            chartSection.classList.toggle("fullscreen", on);
            document.body.classList.toggle("chart-fullscreen", on);
            paint();
            // после смены геометрии — пересчитать размеры графика
            setTimeout(() => {
                window.dispatchEvent(new Event("resize"));
                queueRedraw();
            }, 60);
        };
        btn.addEventListener("click", () => {
            setFs(!chartSection.classList.contains("fullscreen"));
        });
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && chartSection.classList.contains("fullscreen")) setFs(false);
        });
        I18n.onChange(paint);
        paint();
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
            unpinShape();
            closeModal();
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
        unpinShape();
        closeModal();
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
        paintStatBox("liq");

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
        fetchOI();   // табло OI — за монетой графика
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
                updateLiveStats();
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
        paintStatBox("liq");   // renderStats(null) молчит — ярлык окна всё равно локализуем
        paintCvdBox();
        paintOiBox();
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
        // закрываем окно закреплённого кластера — снимаем и подсветку ленты
        // (у кластера пин живёт в двух местах); окно строки ленты чужой
        // закреп прямоугольника не трогает
        const clusterPinned = !state.modalItem && shapePin && shapePin.kind === "liq";
        state.modalItem = null;
        unpinShape();
        if (clusterPinned) {
            pinHitKey = null;
            hoverHitKey = null;
            applyFeedHighlight(null, true);
        }
        detailModal.classList.add("hidden");
        detailModal.classList.remove("peek", "peek-pinned");
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
        setupStatBoxes();            // боксы статистики: меню выбора периода
        loadCandles();
        connectWs();
        setInterval(fetchStats, 15000);
        fetchStats();   // бокс ликвидаций — сразу, не ждём WS/первый тик
        setInterval(fetchOI, 15000);
        fetchOI();
        setInterval(paintCvdBox, 15000);   // окно CVD медленно ползёт
        setInterval(renderTickIndicator, 1000);
        setupLayerToggles();
        setupChartToggle();
        setupChartExpand();
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
