/**
 * LiqScope Terminal — реальный поток ликвидаций + кластерный график.
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
    var I18n = window.LiqScopeI18n || {
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
        minUsd: 0,              // порог ликвидаций, $
        minCvd: 0,              // порог |CVD| за свечу, $ (треугольники и лента)
        minOi: 0,               // порог |OI Δ| за свечу, $ (шарики и лента)
        exchanges: null,        // null = все биржи включены; иначе Set включённых
        availableExchanges: [],
        customSymbols: [],
        soundEnabled: false,
        profileEnabled: false,  // профиль ликвидаций по ценам (полосы на графике)
        liqEnabled: false,      // шарики ликвидаций на графике
        cvdEnabled: false,      // CVD-стрелки: перевес тейкер-покупок/продаж в свече
        cvdBars: 0,
        oiEnabled: false,       // OI-шарики: рост/падение открытого интереса за свечу
        oiBars: 0,
        // индикаторные окна под графиком (те же данные, что в кабинете)
        paneLiq: false,
        paneCvd: false,
        paneOi: false,
        userLoggedIn: false,    // слои и окна доступны только зарегистрированным
        layersAllowed: false,   // userLoggedIn || dev-обход для тестов
        symbols: [],
        details: {},
        prices: {},
        liquidations: [],
        // Кластеры из сохранённой истории: {время свечи: {l: [уровни], t: ts}}.
        // Их считает сервер по дневным шардам, поэтому плашки видны за всю
        // историю (до месяца), а не только за события этой вкладки.
        liqHist: {},
        liqCut: 0,
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
        feedTab: "liq",
        // Минутные потоки всех монет с сервера (лента CVD/OI в режиме «ВСЕ»)
        flowAll: null,         // лента: liq | cvd | oi
        chartFollow: true,      // автоследование: окно само едет за ценой
        followMoved: 0,         // сколько раз окно подвинулось (для тестов/диагностики)
    };

    let connState = { status: "pulse yellow", key: "conn.connecting" };

    const MAX_HISTORY = 4000;

    // --- Автоследование графика -------------------------------------------
    // Пока автоследование включено, шкалой цены управляем мы, а не библиотека:
    // её собственный autoScale пересчитывает диапазон на каждой свече и на
    // каждом тике, из-за чего график дёргался вверх-вниз. Поэтому:
    //   * размах (зум) шкалы цены фиксируется — его задаёт пользователь или
    //     первичная подгонка, а слежение размах не меняет;
    //   * цена держится в «коридоре»: не ближе FOLLOW_MARGIN от верха и низа.
    //     Пока цена внутри коридора, окно не двигается вообще (никакого
    //     дрожания на каждом тике); как только цена вышла за границу —
    //     окно сдвигается ровно так, чтобы цена встала на эту границу.
    //   * у границы есть гистерезис (FOLLOW_PRICE_HYST): шум в пару пунктов
    //     не заставляет график ёрзать.
    // По горизонтали последняя свеча не подходит к правому краю ближе
    // FOLLOW_DRIFT_PCT, а после сдвига (и после ручного ухода) встаёт на
    // FOLLOW_EDGE_PCT от края — то есть «вплотную, но не за край».
    const FOLLOW_MARGIN = 0.15;        // коридор цены: доля высоты окна
    const FOLLOW_EDGE_PCT = 0.03;      // на сколько отступить от правого края
    const FOLLOW_DRIFT_PCT = 0.005;    // ближе этого к краю свечу не подпускаем
    const FOLLOW_PRICE_HYST = 0.01;    // гистерезис у границы коридора
    const FOLLOW_RELEASE_MS = 350;     // пауза после жеста — и возврат к границам
    const PRICE_MARGINS_DEFAULT = { top: 0.06, bottom: 0.24 };

    /** Сколько свечей соответствует доле ширины окна (минимум одна). */
    function followKeepBars(span, pct) {
        const n = Math.round(Number(span) * Number(pct));
        return Number.isFinite(n) && n > 1 ? n : 1;
    }

    /** Куда сдвинуть окно времени: null — двигать не надо.
     *
     * Чистая функция — её гоняет tests/chart_follow.js без браузера.
     * lr — видимый логический диапазон (from/to), last — индекс последней
     * свечи, keepPct — отступ от правого края (доля ширины), driftPct —
     * ближе какого расстояния свечу к краю не подпускаем.
     *
     * Двигаем в двух случаях: свеча подошла к краю ближе driftPct (тогда
     * ставим её на keepPct) и когда окно уехало не туда — ручной сдвиг или
     * прыжок вправо (тогда возвращаем её на keepPct, как и просили: график
     * при включённом слежении всегда возвращается к своим границам).
     */
    function followRange(lr, last, keepPct, driftPct) {
        if (!lr || !isFinite(lr.from) || !isFinite(lr.to) || last < 0) return null;
        const span = Number(lr.to) - Number(lr.from);
        if (!(span > 0)) return null;
        const keep = followKeepBars(span, keepPct);
        const drift = Math.max(0, span * Number(driftPct || 0));
        const edge = Number(lr.to) - Number(last);
        // 0.05 свечи — допуск на округления библиотеки: иначе окно «подъезжало»
        // бы бесконечно на доли бара
        if (edge <= keep + 0.05 && edge >= drift) return null;
        const to = Number(last) + keep;
        return { from: to - span, to: to };
    }

    /** Настройки цены для режима слежения и обратно.
     *
     * Слежение ведём сами: autoScale выключаем (иначе библиотека пересчитает
     * диапазон на каждом тике и график будет дёргаться). При выключении
     * возвращаем библиотеке её обычное поведение.
     */
    function followPriceOptions(on) {
        return on
            ? { autoScale: false, scaleMargins: { top: FOLLOW_MARGIN, bottom: FOLLOW_MARGIN } }
            : { autoScale: true, scaleMargins: Object.assign({}, PRICE_MARGINS_DEFAULT) };
    }

    /** Сдвиг окна цены, когда цена вышла за коридор: null — не трогаем.
     *
     * Чистая функция — гоняется tests/chart_follow.js. Размах окна сохраняем
     * (зум не меняем), цену ставим ровно на границу коридора — margin от
     * верха при движении вверх и margin от низа при движении вниз. Выход за
     * границу должен быть больше гистерезиса, иначе ничего не двигаем.
     */
    function followPriceShift(cur, price, margin, hyst) {
        if (!cur) return null;
        const from = Number(cur.from), to = Number(cur.to), p = Number(price);
        if (!isFinite(from) || !isFinite(to) || !isFinite(p) || !(to > from)) return null;
        const span = to - from;
        let m = Number(margin);
        if (!isFinite(m) || m < 0) m = 0;
        m = Math.min(m, 0.4);
        const pad = span * m;
        let hy = Number(hyst);
        if (!isFinite(hy) || hy < 0) hy = 0;
        const eps = span * hy;
        if (p > to - pad + eps) {                 // ушла вверх — ставим на 15% от верха
            const nTo = p + pad;
            return { from: nTo - span, to: nTo };
        }
        if (p < from + pad - eps) {               // ушла вниз — ставим на 15% от низа
            const nFrom = p - pad;
            return { from: nFrom, to: nFrom + span };
        }
        return null;                              // цена в коридоре — не дёргаем
    }

    /** Первичная подгонка окна цены: видимые свечи и цена с зазорами.
     *
     * Нужна, когда своего окна ещё нет и когда цена ушла на другой уровень
     * (переключили монету): сдвигать прежнее окно там бессмысленно.
     */
    function followPriceFit(band, price, margin) {
        const p = Number(price);
        const hasPrice = price !== null && price !== undefined && isFinite(p);
        if (!band && !hasPrice) return null;
        let low = band ? Number(band.low) : p;
        let high = band ? Number(band.high) : p;
        if (hasPrice) { low = Math.min(low, p); high = Math.max(high, p); }
        if (!isFinite(low) || !isFinite(high)) return null;
        if (high <= low) {
            const pad0 = Math.max(Math.abs(high) * 1e-4, 1e-9);
            low -= pad0; high += pad0;
        }
        let m = Number(margin);
        if (!isFinite(m) || m < 0) m = 0;
        m = Math.min(m, 0.4);
        const need = (high - low) / (1 - 2 * m);
        const mid = (low + high) / 2;
        return { from: mid - need / 2, to: mid + need / 2 };
    }

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
    const feedFilterEl = $("feed-filter");          // плашка «фильтр: монета»
    const minUsdBtn = $("min-usd-btn");
    const minUsdInput = $("min-usd-input");
    const minCvdInput = $("min-cvd-input");
    const minOiInput = $("min-oi-input");
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
    const clearMenuBtn = $("clear-menu-btn");
    const clearMenu = $("clear-menu");
    const topCoinsContainer = $("top-coins-list");
    const chartWrapper = $("chart-wrapper");
    const clusterCanvas = $("cluster-canvas");
    const drawCanvas = $("draw-canvas");
    const drawToggle = $("draw-toggle");
    const drawToolbar = $("draw-toolbar");
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

    // --- Время на графике: местное, как в ленте -----------------------------
    // Библиотека графика по умолчанию подписывает ось временем Гринвича, из-за
    // чего низ графика жил в UTC, а лента — в поясе пользователя. Подставляем
    // свои форматеры: и деления оси, и подпись перекрестия идут в местном
    // времени и на языке интерфейса.
    const TICK_YEAR = 0, TICK_MONTH = 1, TICK_DAY = 2, TICK_TIME = 3, TICK_TIME_SEC = 4;

    function chartTimeText(t, tickType) {
        const d = new Date((Number(t) || 0) * 1000);
        if (isNaN(d.getTime())) return "";
        const tag = (I18n && I18n.localeTag) ? I18n.localeTag() : undefined;
        const daily = Number(state.timeframe) >= 1440;
        try {
            switch (tickType) {
                case TICK_YEAR: return String(d.getFullYear());
                case TICK_MONTH: return d.toLocaleDateString(tag, { month: "short" });
                case TICK_DAY:
                    return d.toLocaleDateString(tag, { day: "2-digit", month: "short" });
                case TICK_TIME_SEC:
                    return d.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
                case TICK_TIME:
                    return daily ? d.toLocaleDateString(tag, { day: "2-digit", month: "2-digit" })
                                 : d.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit" });
                default:
                    // Неизвестный тип деления: на дневках — дата, иначе время
                    return daily
                        ? d.toLocaleDateString(tag, { day: "2-digit", month: "2-digit" })
                        : d.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit" });
            }
        } catch (e) { return ""; }
    }

    /** Подпись перекрестия (и метка на оси): дата + местное время. */
    function chartCrosshairText(t) {
        const d = new Date((Number(t) || 0) * 1000);
        if (isNaN(d.getTime())) return "";
        const tag = (I18n && I18n.localeTag) ? I18n.localeTag() : undefined;
        const daily = Number(state.timeframe) >= 1440;
        try {
            const date = d.toLocaleDateString(tag, { day: "2-digit", month: "short", year: "numeric" });
            if (daily) return date;
            return date + " " + d.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit" });
        } catch (e) { return ""; }
    }

    /** Форматеры оси: применяются к графику и переприменяются при смене языка. */
    function chartTimeOptions() {
        return {
            timeScale: {
                tickMarkFormatter: (t, tickType) => chartTimeText(t, tickType),
            },
            localization: {
                locale: (I18n && I18n.localeTag) ? I18n.localeTag() : undefined,
                timeFormatter: (t) => chartCrosshairText(t),
            },
        };
    }

    function applyChartTimeOptions() {
        if (!chart || !chart.applyOptions) return;
        try { chart.applyOptions(chartTimeOptions()); } catch (e) { /* ignore */ }
    }

    // График не привязан к фильтру ленты: включив «ВСЕ», пользователь видит
    // все ликвидации в эфире, а график остаётся на выбранной монете.
    const chartSymbol = () => state.chartSymbol || state.symbols[0] || "BTC_USDT";
    const pretty = (s) => String(s || "").replace("_", "/");

    // --- Фильтры (мин. объём + биржи) ----------------------------------------
    // Как биржа подписывается в интерфейсе (код биржи -> читаемое имя)
    const EXCH_NAMES = {
        dydx: "dYdX", okx: "OKX", htx: "HTX",
    };

    function exchangeName(e) {
        const k = String(e || "").toLowerCase();
        return EXCH_NAMES[k] || (k.charAt(0).toUpperCase() + k.slice(1));
    }

    function isExchangeOn(name) {
        if (!state.exchanges) return true;            // всё включено
        return state.exchanges.has(name);
    }

    // Пороги объёма по видам ленты: ликвидации, CVD за свечу и OI Δ за свечу.
    // У каждого свой ключ в localStorage: фильтр ликвидаций не должен менять
    // порог CVD, и наоборот.
    const THRESHOLDS = {
        liq: { key: "minUsd", store: "liqscope.minUsd" },
        cvd: { key: "minCvd", store: "liqscope.minCvd" },
        oi: { key: "minOi", store: "liqscope.minOi" },
    };

    function thresholdOf(kind) {
        const cfg = THRESHOLDS[kind] || THRESHOLDS.liq;
        return Math.max(0, Number(state[cfg.key]) || 0);
    }

    function loadSavedFilters() {
        Object.keys(THRESHOLDS).forEach((kind) => {
            const cfg = THRESHOLDS[kind];
            try {
                const v = localStorage.getItem(cfg.store);
                if (v !== null) state[cfg.key] = Math.max(0, parseFloat(v) || 0);
            } catch (e) { /* ignore */ }
        });
        try {
            const raw = localStorage.getItem("liqscope.exchanges");
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

    function saveThreshold(kind) {
        const cfg = THRESHOLDS[kind] || THRESHOLDS.liq;
        try { localStorage.setItem(cfg.store, String(state[cfg.key] || 0)); } catch (e) { /* ignore */ }
    }

    function saveMinUsd() {
        saveThreshold("liq");
    }

    function saveExchanges() {
        try {
            localStorage.setItem("liqscope.exchanges",
                state.exchanges ? JSON.stringify(Array.from(state.exchanges).sort()) : "*");
        } catch (e) { /* ignore */ }
    }

    function applyFiltersFull() {
        rebuildFeed();
        updateMarkers();
        updateLiveStats();
        queueRedraw();
        // Фильтры ленты (порог и биржи) фильтруют и исторические кластеры:
        // сервер считает свёртку с теми же условиями, поэтому перезапрашиваем
        // её — ключ запроса включает порог и список включённых бирж.
        loadLiqClusters();
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
                const req = window.indexedDB.open("liqscope-history", 1);
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

    // --- Кластеры из сохранённой истории ------------------------------------
    // Плашки рисуются по свечам, а свечи живут максимум 300 штук: для часа это
    // две недели, для минуты — пять часов. Раньше кластеры собирались только
    // из памяти браузера (2000 последних событий с сервера + сутки в
    // IndexedDB), поэтому всё, что случилось при закрытом терминале, на графике
    // не появлялось. Теперь сервер отдаёт готовые кластеры по свечам из своей
    // истории — как OI и CVD, которые тоже приходят вместе со свечами.
    const LIQ_HIST_TTL = 60000;               // мс: как часто перезапрашивать
    let liqHistKey = "";                      // что уже загружено (монета|тф|порог)
    let liqHistAt = 0;
    let liqHistReqId = 0;

    // Включённые биржи строкой для /api/liq_clusters. Пусто — фильтра нет,
    // сервер считает по всем: тогда и запрос, и ответ кэшируются как раньше.
    // null — выключены все биржи: сохранённых плашек тоже быть не должно.
    function liqExchParam() {
        const all = state.availableExchanges || [];
        if (!state.exchanges) return "";
        if (!state.exchanges.size) return null;      // выключены все
        if (!all.length) return Array.from(state.exchanges).sort().join(",");
        if (state.exchanges.size >= all.length) return "";   // выбраны все
        return all.filter((e) => state.exchanges.has(e)).sort().join(",");
    }

    async function loadLiqClusters(force) {
        const sym = chartSymbol();
        const tf = Number(state.timeframe);
        if (!sym) return;
        const exs = liqExchParam();
        const key = sym + "|" + tf + "|" + (Number(state.minUsd) || 0) + "|" +
                    (exs === null ? "none" : exs);
        if (!force && key === liqHistKey && Date.now() - liqHistAt < LIQ_HIST_TTL) return;
        if (exs === null) {
            // Все биржи выключены — истории ликвидаций нет, как и живых событий:
            // запрос не отправляем, старые плашки убираем.
            liqHistKey = key;
            liqHistAt = Date.now();
            state.liqHist = {};
            state.liqCut = 0;
            queueRedraw();
            return;
        }
        const req = ++liqHistReqId;
        try {
            const r = await fetch("/api/liq_clusters?symbol=" + encodeURIComponent(sym) +
                "&timeframe=" + tf +
                "&min_usd=" + (Number(state.minUsd) || 0) +
                "&exchanges=" + encodeURIComponent(exs));
            const data = await r.json();
            if (req !== liqHistReqId) return;          // пришёл ответ по старой паре
            if (chartSymbol() !== sym || Number(state.timeframe) !== tf) return;
            state.liqHist = (data && data.candles) || {};
            state.liqCut = Number(data && data.cut) || 0;
            liqHistKey = key;
            liqHistAt = Date.now();
            queueRedraw();
        } catch (e) { /* нет сети — рисуем по памяти, как раньше */ }
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
        lastMarkers = (markers || []).slice();     // для тестов: что ушло на график
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
            timeScale: {
                borderColor: "#212938", timeVisible: true, secondsVisible: false, rightOffset: 6,
                // Деления оси — в местном времени пользователя (как в ленте),
                // а не в UTC, который библиотека ставит по умолчанию
                tickMarkFormatter: (t, tickType) => chartTimeText(t, tickType),
            },
            localization: {
                priceFormatter: (p) => Number(p).toFixed(priceDigits(p)),
                locale: (I18n && I18n.localeTag) ? I18n.localeTag() : undefined,
                timeFormatter: (t) => chartCrosshairText(t),
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
            if (drawCanvas) {
                drawCanvas.width = w;
                drawCanvas.height = h;
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

        // Окно поехало (мы или вручную) — перерисовать плашки кластеров.
        // Возврат к границам делает шаг автоследования: см. followChartNow.
        chart.timeScale().subscribeVisibleLogicalRangeChange(() => queueRedraw());
        markersApi = null;
        watchFollowGestures(container);
        applyFollowMode();
    }

    /** Включить/выключить режим слежения и сразу поставить график по местам. */
    function applyFollowMode() {
        if (!chart) return;
        try {
            const scale = rightPriceScale();
            if (scale && scale.applyOptions) {
                scale.applyOptions(followPriceOptions(state.chartFollow));
            }
        } catch (e) { /* ignore */ }
        try {
            const ts = chart.timeScale();
            if (ts && ts.applyOptions) {
                // Пока следим сами — правый отступ 0: положение последней свечи
                // задаём мы (3% от края), иначе библиотека тянула бы её к самому
                // краю своим автоскроллом. И окно на новой свече библиотека не
                // сдвигает: со включённым слежением его ставим мы сами, а с
                // выключенным график обязан стоять там, где его оставили, — иначе
                // каждый новый бар тянул бы вид вправо и промотать назад было бы
                // нельзя.
                ts.applyOptions({ rightOffset: state.chartFollow ? 0 : 6,
                                  shiftVisibleRangeOnNewBar: false });
            }
        } catch (e) { /* ignore */ }
        followChartNow();
    }

    // Пока пользователь тянет график рукой, шаги слежения молчат; после
    // отпускания возвращаем график к его границам (3% справа, 15% сверху/снизу).
    let followHold = false;
    let followReleaseTimer = null;
    let followTimer = null;

    /** Шаг по таймеру: страховка на случай, когда тиков по монете нет. */
    function followTick() {
        if (state.chartFollow && !followHold) followChartNow();
    }

    function setFollowTicker(on) {
        if (followTimer) { clearInterval(followTimer); followTimer = null; }
        if (on !== false) followTimer = setInterval(followTick, 500);
    }

    function watchFollowGestures(el) {
        if (!el || !el.addEventListener) return;
        const hold = () => {
            followHold = true;
            if (followReleaseTimer) { clearTimeout(followReleaseTimer); followReleaseTimer = null; }
        };
        const release = () => {
            if (!followHold) return;
            followHold = false;
            if (followReleaseTimer) clearTimeout(followReleaseTimer);
            // Возврат к границам — только пока автоследование включено. С
            // выключенной кнопкой график статичен: его двигают руками, и он
            // остаётся там, где его оставили.
            if (!state.chartFollow) return;
            followReleaseTimer = setTimeout(() => {
                followReleaseTimer = null;
                // жест двигает и время, и шкалу цены — возвращаем обе: свечу к
                // 3% справа, цену к 15% от края, если её из коридора вывели
                anchorFollowAll();
            }, FOLLOW_RELEASE_MS);
        };
        el.addEventListener("pointerdown", hold, true);
        window.addEventListener("pointerup", release);
        window.addEventListener("pointercancel", release);
        // Колесо (зум/прокрутка) — тоже жест: пока крутят, шаги молчат (иначе
        // слежение тянуло бы окно на каждый шаг колеса), а после — возврат
        // к границам. Слушаем без passive-предупреждений: ничего не отменяем.
        el.addEventListener("wheel", () => {
            hold();
            if (followReleaseTimer) clearTimeout(followReleaseTimer);
            followReleaseTimer = setTimeout(() => {
                followReleaseTimer = null;
                followHold = false;
                // с выключенным слежением колесо ничего не откидывает назад
                if (state.chartFollow) anchorFollowAll();
            }, FOLLOW_RELEASE_MS);
        }, { passive: true, capture: true });
        // Страховка: если слежение включено, а тиков по монете нет, шаг всё
        // равно вернёт график к границам после случайного сдвига
        setFollowTicker(true);
    }

    /** Шкала цены справа: через серию (v4/v5), с запасным путём через график. */
    function rightPriceScale() {
        try {
            if (candleSeries && candleSeries.priceScale) {
                const s = candleSeries.priceScale();
                if (s && s.applyOptions) return s;
            }
        } catch (e) { /* ignore */ }
        try {
            const s = chart && chart.priceScale ? chart.priceScale("right") : null;
            if (s && s.applyOptions) return s;
        } catch (e) { /* ignore */ }
        return null;
    }

    /** Границы цены по видимым свечам (для вертикального слежения). */
    function visiblePriceBand() {
        const candles = state.candles || [];
        if (!candles.length) return null;
        let from = 0, to = candles.length - 1;
        try {
            const lr = chart.timeScale().getVisibleLogicalRange();
            if (lr && isFinite(lr.from) && isFinite(lr.to)) {
                from = Math.max(0, Math.floor(Number(lr.from)));
                to = Math.min(candles.length - 1, Math.ceil(Number(lr.to)));
            }
        } catch (e) { /* окно неизвестно — берём все свечи */ }
        let low = Infinity, high = -Infinity;
        for (let i = from; i <= to; i++) {
            const c = candles[i];
            if (!c) continue;
            const lo = Number(c.low), hi = Number(c.high);
            if (isFinite(lo) && lo < low) low = lo;
            if (isFinite(hi) && hi > high) high = hi;
        }
        if (!isFinite(low) || !isFinite(high)) return null;
        return { low: low, high: high };
    }

    /** Последняя цена на графике (закрытие живой свечи). */
    function lastChartPrice() {
        const candles = state.candles || [];
        const c = candles[candles.length - 1];
        const p = c ? Number(c.close) : NaN;
        return isFinite(p) && p > 0 ? p : null;
    }

    /** Вертикальная часть автоследования: цена не выходит за коридор.
     *
     * Шкалой цены в режиме слежения управляем мы (autoScale выключен в
     * applyFollowMode), поэтому шаг такой:
     *   1. читаем текущее окно цены;
     *   2. если окна нет или цена ушла на другой уровень (переключили монету) —
     *      подгоняем окно заново по видимым свечам;
     *   3. иначе — двигаем окно только если цена вышла за границу коридора,
     *      и ровно настолько, чтобы встать на эту границу. Внутри коридора
     *      не делаем ничего: именно это убирает дрожание на каждом тике.
     */
    function followPriceNow() {
        if (!chart || !state.chartFollow) return false;
        const scale = rightPriceScale();
        if (!scale || !scale.setVisibleRange) return false;
        const price = lastChartPrice();
        if (price === null) return false;
        let cur = null;
        try {
            cur = scale.getVisibleRange ? scale.getVisibleRange() : null;
        } catch (e) { cur = null; }
        const okCur = !!cur && isFinite(cur.from) && isFinite(cur.to) &&
                      Number(cur.to) > Number(cur.from);
        let next = null;
        if (okCur) {
            const from = Number(cur.from), to = Number(cur.to), span = to - from;
            // цена вообще не из этого окна (другая монета) — подгоняем заново,
            // иначе «сдвиг» растянул бы окно на тысячи процентов
            const far = price < from - span || price > to + span;
            next = far ? followPriceFit(visiblePriceBand(), price, FOLLOW_MARGIN)
                       : followPriceShift(cur, price, FOLLOW_MARGIN, FOLLOW_PRICE_HYST);
        } else {
            next = followPriceFit(visiblePriceBand(), price, FOLLOW_MARGIN);
        }
        if (!next) return false;
        try {
            scale.setVisibleRange(next);
        } catch (e) { return false; }
        return true;
    }

    /** Один шаг автоследования: свеча — к правому краю, цена — в коридоре.
     *
     * Пока пользователь тянет график рукой (followHold), шагов не делаем:
     * иначе жест и автоследование тянули бы окно в разные стороны. Как только
     * жест отпущен, шаг возвращает график к его границам — 3% справа и
     * коридор 15% сверху и снизу.
     */
    function followChartNow() {
        if (!chart || !state.chartFollow || followHold) return false;
        if (!state.candles.length) return false;
        let lr = null;
        try {
            lr = chart.timeScale().getVisibleLogicalRange();
        } catch (e) { return false; }
        const next = followRange(lr, state.candles.length - 1,
                                 FOLLOW_EDGE_PCT, FOLLOW_DRIFT_PCT);
        const movedTime = next ? applyFollowRange(next) : false;
        const movedPrice = followPriceNow();
        return movedTime || movedPrice;
    }

    function applyFollowRange(next) {
        try {
            chart.timeScale().setVisibleLogicalRange(next);
        } catch (e) { return false; }
        state.followMoved += 1;
        return true;
    }

    /** Возврат после жеста: свеча — на 3% от правого края, цена — ровно на
     *  15% от края, если жест вывел её из коридора.
     *
     *  Внутри коридора вертикаль не трогаем: цена и так видна с запасом не
     *  меньше 15%, а лишний прыжок после каждого жеста читался бы как
     *  дрожание. Гистерезис здесь выключен — это как раз случай «вернуть
     *  график к минимальным отступам», а не «терпеть шум».
     */
    function anchorFollowAll() {
        // Выключено — не двигаем ничего: график остаётся там, где его оставили.
        if (!state.chartFollow) return false;
        const movedTime = anchorToLast();
        const movedPrice = followPriceSnap();
        return movedTime || movedPrice;
    }

    /** Цена за коридором — ставим её ровно на 15% от той границы, за которую
     *  она ушла. Окна цены нет — подгоняем по видимым свечам. */
    function followPriceSnap() {
        if (!chart || !state.chartFollow) return false;
        const scale = rightPriceScale();
        if (!scale || !scale.setVisibleRange) return false;
        const price = lastChartPrice();
        if (price === null) return false;
        let cur = null;
        try { cur = scale.getVisibleRange ? scale.getVisibleRange() : null; } catch (e) { cur = null; }
        const next = (cur && isFinite(cur.from) && isFinite(cur.to) && Number(cur.to) > Number(cur.from))
            ? followPriceShift(cur, price, FOLLOW_MARGIN, 0)
            : followPriceFit(visiblePriceBand(), price, FOLLOW_MARGIN);
        if (!next) return false;
        try {
            scale.setVisibleRange(next);
        } catch (e) { return false; }
        return true;
    }

    /** Прыжок к последней свече: включаем автоследование — сразу к цене. */
    function anchorToLast() {
        if (!chart || !state.chartFollow || !state.candles.length) return false;
        let lr = null;
        try { lr = chart.timeScale().getVisibleLogicalRange(); } catch (e) { return false; }
        const span = lr && lr.to > lr.from ? (lr.to - lr.from) : 80;
        const to = state.candles.length - 1 + followKeepBars(span, FOLLOW_EDGE_PCT);
        return applyFollowRange({ from: to - span, to: to });
    }

    /** Кнопка/попап: включить автоследование и запомнить выбор. */
    function setChartFollow(on) {
        state.chartFollow = !!on;
        try { localStorage.setItem("liqscope.chartFollow", on ? "1" : "0"); }
        catch (e) { /* ignore */ }
        paintFollowButtons();
        applyFollowMode();
        if (on) anchorToLast();   // включили — сразу к актуальной свече
    }

    /** Подсветка кнопки: горит ⇔ автоследование включено.
     *
     * Состояний ровно два: включено — кнопка горит синим, выключено — гаснет.
     * Паузы больше нет: с включённым слежением график всегда возвращается к
     * своим границам, даже если его подвинули руками.
     */
    function paintFollowButtons() {
        const on = !!state.chartFollow;
        [$("follow-toggle"), $("follow-toggle-pop")].forEach((btn) => {
            if (!btn) return;
            btn.classList.toggle("active", on);
            btn.classList.remove("paused");
            btn.setAttribute("aria-pressed", on ? "true" : "false");
            btn.setAttribute("data-state", on ? "on" : "off");
            btn.title = I18n.t(on ? "chart.follow_on" : "chart.follow_off");
        });
    }

    function setupFollowToggle() {
        const btns = [$("follow-toggle"), $("follow-toggle-pop")];
        if (!btns.some(Boolean)) return;
        try {
            const v = localStorage.getItem("liqscope.chartFollow");
            if (v === "0") state.chartFollow = false;
            else if (v === "1") state.chartFollow = true;
        } catch (e) { /* ignore */ }
        btns.forEach((btn) => {
            if (!btn) return;
            btn.addEventListener("click", () => setChartFollow(!state.chartFollow));
        });
        I18n.onChange(() => { paintFollowButtons(); applyChartTimeOptions(); });
        paintFollowButtons();
        applyFollowMode();
        window.LiQScopeFollow = window.LiQScopeFollow || {};
        window.LiQScopeFollow = {
            // для tests/chart_follow.js: состояние, чистая математика и ручной шаг
            enabled: () => !!state.chartFollow,
            setEnabled: setChartFollow,
            range: followRange,
            priceOptions: followPriceOptions,
            moved: () => state.followMoved,
            hold: () => followHold,
            setHold: (v) => { followHold = !!v; },
            tick: () => followTick(),
            setTicker: (v) => setFollowTicker(v),
            anchor: () => anchorToLast(),
            anchorAll: () => anchorFollowAll(),
            priceSnap: () => followPriceSnap(),
            step: () => followChartNow(),
            edgePct: FOLLOW_EDGE_PCT,
            driftPct: FOLLOW_DRIFT_PCT,
            keepBars: (span) => followKeepBars(span, FOLLOW_EDGE_PCT),
            margin: FOLLOW_MARGIN,
            hyst: FOLLOW_PRICE_HYST,
            // настройки шкалы цены, что реально ушли в график
            priceScaleOptions: () => {
                try {
                    const sc = rightPriceScale();
                    return sc && sc.options ? sc.options() : null;
                } catch (e) { return null; }
            },
            // вертикальное окно цены: чистая математика + что реально в графике
            priceShift: followPriceShift,
            priceFit: followPriceFit,
            priceStep: () => followPriceNow(),
            priceVisibleRange: () => {
                try {
                    const sc = rightPriceScale();
                    return sc && sc.getVisibleRange ? sc.getVisibleRange() : null;
                } catch (e) { return null; }
            },
            band: () => visiblePriceBand(),
            timeScaleOptions: () => {
                try {
                    const ts = chart && chart.timeScale();
                    return ts && ts.options ? ts.options() : null;
                } catch (e) { return null; }
            },
        };
        // Для tests/chart_time.js: подписи времени можно проверить без графика
        window.LiQScopeChartTime = {
            tick: (t, type) => chartTimeText(t, type),
            crosshair: (t) => chartCrosshairText(t),
            apply: () => applyChartTimeOptions(),
            options: () => {
                try {
                    return chart && chart.options ? chart.options() : null;
                } catch (e) { return null; }
            },
        };
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
        try {
            if (chart && chart.timeScale) {
                chart.timeScale().applyOptions({
                    timeVisible: Number(state.timeframe) < 1440,
                    secondsVisible: false,
                });
            }
        } catch (e) { /* ignore */ }
        candleSeries.setData(bars);
        if (volumeSeries) volumeSeries.setData(vols);

        state.sessionOpen = bars[0].open;
        updatePriceDisplay(bars[bars.length - 1].close);
        renderTickIndicator();
        // Кластеры за всю сохранённую историю — под текущую пару и таймфрейм
        loadLiqClusters();
        updateMarkers();
        updateLiveStats();
        queueRedraw();
        queueShapeFeed();
        followChartNow();
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
        queueShapeFeed();
        followChartNow();
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
    // Шесть уровней цены внутри свечи — столько же у сервера в
    // /api/liq_clusters: расклад плашек с историей должен совпадать.
    const LIQ_LEVELS = 6;
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

    let lastMarkers = [];       // последние применённые метки-киты

    function updateMarkers() {
        if (!candleSeries) return;
        if (!state.liqEnabled) { applyMarkers([]); return; }
        const byTime = new Map();

        // Метками помечаем только крупные события — всё остальное рисуем
        // прямоугольниками прямо на свече, чтобы не засорять поле графика.
        const kvol = chartVolScale();
        // Киты — из тех же рядов: метки остаются на графике и за те часы, когда
        // терминал был закрыт (как треугольники CVD и круги OI).
        liqClusterRows().forEach((r) => {
            [[r.longUsd, "SELL"], [r.shortUsd, "BUY"]].forEach(([usd, side]) => {
                if (!(usd >= WHALE_USD * kvol)) return;
                const key = r.time + "_" + (side === "SELL" ? "L" : "S");
                const cur = byTime.get(key) || { time: r.time, side: side, usd: 0 };
                cur.usd += usd;
                byTime.set(key, cur);
            });
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
        // Порядок снизу вверх: шары OI → треугольники CVD → кластеры ликвидаций.
        // Крупные OI-шары больше не перекрывают прямоугольники и треугольники.
        if (state.oiEnabled) drawOiBalls(ctx);
        if (state.cvdEnabled) drawCvdTriangles(ctx);
        if (state.liqEnabled) drawLiqRects(ctx);
        drawFigures();   // фигуры теханализа — свой canvas поверх
        drawIndicatorPanes();   // окна LIQ/CVD/OI под графиком
    }

    // --- Плашки ликвидаций ------------------------------------------------------
    // Плашка — компактная метка кластера ликвидаций: она стоит **там, где были
    // ликвидации** (по цене, внутри свечи), а не растянута по её телу.
    // Габариты:
    //   • высота — как была: 15px у обычной плашки, 16–22px у кита (от суммы),
    //     10px у чипа без цифр. Сверху ограничена телом свечи с зазором 3%
    //     с каждой стороны — тело никогда не закрывается целиком;
    //   • ширина — слот свечи: тянете график вширь — свечи и плашки растут
    //     вместе, сужаете — сжимаются вместе с ними.
    // Цифры внутри показываем, только если помещаются: кегль растёт вместе с
    // шириной, при сужении подпись убирается совсем (остаётся цвет стороны).
    const LIQ_PLATE_GAP = 0.03;      // зазор до тела свечи с каждой стороны
    const LIQ_PLATE_H = 15;          // высота обычной плашки (как было)
    const LIQ_PLATE_CHIP_H = 10;     // чип без цифр
    const LIQ_PLATE_WHALE_H = 16;    // базовая высота плашки кита (+ до 6px)
    const LIQ_PLATE_W_FRAC = 0.86;   // доля слота свечи по ширине
    const LIQ_PLATE_MIN_W = 3;       // совсем узкий слот: плашка-штрих
    const LIQ_PLATE_MIN_H = 2;
    const LIQ_PLATE_FONT_MIN = 7.5;  // ниже этого цифры не читаются — убираем
    const LIQ_PLATE_FONT_MAX = 14;   // крупнее подпись не становится
    const LIQ_PLATE_TEXT_PAD = 4;    // рамка плашки по бокам подписи
    const LIQ_PLATE_FONT_W = 0.9;    // кегль как доля ширины плашки
    const LIQ_PLATE_FONT_H = 0.74;   // кегль как доля высоты плашки

    // Высота плашки: как была, с поправкой на сумму для китов.
    function liqPlateBaseH(wantLabel, whale, total) {
        if (!wantLabel) return LIQ_PLATE_CHIP_H;
        if (!whale) return LIQ_PLATE_H;
        const extra = Math.min(6, Math.max(-3,
            (Math.log10(Math.max(Number(total) || 10, 10)) - 5) * 2.5));
        return LIQ_PLATE_WHALE_H + extra;
    }

    // Габариты плашки по телу свечи и слоту; подпись — если помещается.
    // Чистая функция (проверяется в tests/liq_plates.js): measure(text, font)
    // возвращает ширину текста в пикселях на заданном кегле.
    function liqPlateGeom(bodyPx, slotPx, label, measure, baseH) {
        const slot = Math.max(1, Number(slotPx) || 1);
        const body = Math.max(0, Number(bodyPx) || 0);
        const base = Math.max(LIQ_PLATE_MIN_H, Number(baseH) || LIQ_PLATE_H);
        const w = Math.max(LIQ_PLATE_MIN_W, Math.round(slot * LIQ_PLATE_W_FRAC));
        // Потолок по телу: 3% зазора с каждой стороны (floor — чтобы округление
        // не съедало зазор). На тонкой свече плашка становится тоньше её тела.
        const cap = Math.floor(body * (1 - 2 * LIQ_PLATE_GAP));
        const h = Math.max(LIQ_PLATE_MIN_H, cap > 0 ? Math.min(base, cap)
                                                   : LIQ_PLATE_MIN_H);
        const out = { w: w, h: h, font: 0, showLabel: false, text: "" };
        const text = String(label || "");
        if (!text || typeof measure !== "function") return out;
        const room = w - LIQ_PLATE_TEXT_PAD;
        const per = measure(text, 1);      // ширина подписи на кегле 1
        if (!(per > 0) || !(room > 0)) return out;
        // Кегль хотим как можно крупнее — растёт вместе с шириной плашки,
        // но подпись обязана в неё влезть.
        let font = Math.min(w * LIQ_PLATE_FONT_W, h * LIQ_PLATE_FONT_H,
                            LIQ_PLATE_FONT_MAX, room / per);
        // Ширина текста в канвасе растёт от кегля почти линейно, но не строго:
        // подгоняем по реальной мере, чтобы подпись гарантированно влезла.
        for (let i = 0; i < 3; i++) {
            const wide = measure(text, font);
            if (!(wide > room)) break;
            font = font * (room / wide) * 0.97;
        }
        if (font < LIQ_PLATE_FONT_MIN) return out;   // узко/мелко — цифры убираем
        if (measure(text, font) > room) return out;  // не влезла даже сжатой
        out.font = font;
        out.showLabel = true;
        out.text = text;
        return out;
    }

    // Пикселей на свечу: ширина слота (barSpacing), медиана по свечам или
    // ширина канваса, делённая на число видимых свечей.
    function plateSlotPx() {
        try {
            const opt = chart && chart.timeScale && chart.timeScale().options
                ? chart.timeScale().options() : null;
            const bs = opt ? Number(opt.barSpacing) : NaN;
            if (isFinite(bs) && bs > 0) return bs;
        } catch (e) { /* ниже — считаем сами */ }
        const gaps = [];
        const cds = state.candles || [];
        for (let i = 1; i < cds.length && gaps.length < 40; i++) {
            try {
                const a = chart.timeScale().timeToCoordinate(cds[i - 1].time);
                const b = chart.timeScale().timeToCoordinate(cds[i].time);
                if (isFinite(a) && isFinite(b) && b > a) gaps.push(b - a);
            } catch (e) { /* пропускаем */ }
        }
        if (gaps.length) {
            gaps.sort((x, y) => x - y);
            return gaps[Math.floor(gaps.length / 2)];
        }
        const W = clusterCanvas ? Number(clusterCanvas.width) || 0 : 0;
        let visible = 0;
        try {
            const r = chart.timeScale().getVisibleLogicalRange();
            if (r && isFinite(r.to - r.from) && r.to > r.from) {
                visible = Math.ceil(r.to - r.from);
            }
        } catch (e) { /* нет шкалы — считаем по свечам */ }
        if (!visible) visible = Math.min(cds.length, 80);
        if (W > 0 && visible > 0) return W / visible;
        return 8;
    }

    // Кластеры ликвидаций: сохранённая история с сервера + живой хвост из памяти.
    //
    // История (/api/liq_clusters) посчитана по дневным шардам за весь месяц и
    // приходит по свечам графика — как CVD и OI, которые тоже приезжают вместе
    // со свечами. Этих событий в памяти браузера может не быть вовсе: терминал
    // был закрыт, — а кластеры за прошлые часы и дни на месте. Из памяти
    // добавляется только то, что сервер ещё не посчитал (событие позже
    // последнего посчитанного ``t`` этой свечи): иначе одна и та же ликвидация
    // попала бы в плашку дважды.
    //
    // Ряд — свеча × уровень цены внутри свечи:
    // {key, time, level, bar, lo, hi, longUsd, shortUsd, longN, shortN, total,
    //  count, pxSum, ids, exchs}.
    function liqClusterRows() {
        const tfSec = state.timeframe * 60;
        const hist = state.liqHist || {};
        const rows = new Map();
        const rowOf = (t, bar, lo, hi, level) => {
            const key = "b" + t + "_" + level;
            let r = rows.get(key);
            if (!r) {
                r = {
                    key: key, time: t, bar: bar, lo: lo, hi: hi, level: level,
                    longUsd: 0, shortUsd: 0, total: 0, count: 0, longN: 0,
                    shortN: 0, ids: [], exchs: {}, pxSum: 0,
                };
                rows.set(key, r);
            }
            return r;
        };
        // Строка истории сервера: [уровень, лонги, шорты, nлонги, nшорты,
        // средняя цена, биржи]. Уже посчитана — раскладываем как есть.
        const addHistory = (r, row) => {
            const longUsd = Number(row[1]) || 0;
            const shortUsd = Number(row[2]) || 0;
            const usd = longUsd + shortUsd;
            if (usd <= 0) return;
            r.longUsd += longUsd;
            r.longN += Number(row[3]) || 0;
            r.shortUsd += shortUsd;
            r.shortN += Number(row[4]) || 0;
            r.total += usd;
            r.count += (Number(row[3]) || 0) + (Number(row[4]) || 0);
            r.pxSum += (Number(row[5]) || 0) * usd;
            const ex = row[6] || {};
            Object.keys(ex).forEach((name) => {
                const slot = r.exchs[name] || { n: 0, usd: 0 };
                slot.n += Number(ex[name][0]) || 0;
                slot.usd += Number(ex[name][1]) || 0;
                r.exchs[name] = slot;
            });
        };

        // Живой хвост из памяти — сразу по свечам: события, которые сервер уже
        // посчитал, пропускаем (иначе двойной счёт).
        const live = new Map();
        visibleLiquidations().forEach((item) => {
            const t = Math.floor(item.timestamp / tfSec) * tfSec;
            const served = hist[t];
            if (served && served.t != null &&
                    Number(item.timestamp) <= Number(served.t)) return;
            const arr = live.get(t);
            if (arr) arr.push(item); else live.set(t, [item]);
        });

        // Идём по свечам графика: вне них плашек всё равно нет, зато работа не
        // зависит от того, сколько истории накопил сервер (а он держит месяц).
        state.candles.forEach((bar) => {
            const t = Number(bar.time);
            const lo = Math.min(bar.low, bar.high);
            const hi = Math.max(bar.low, bar.high);
            const served = hist[t];
            if (served && served.l && served.l.length) {
                served.l.forEach((row) => addHistory(
                    rowOf(t, bar, lo, hi, Number(row[0]) || 0), row));
            }
            const items = live.get(t);
            if (!items) return;
            items.forEach((item) => {
                let price = Number(item.price);
                if (!isFinite(price)) price = Number(bar.close);
                price = Math.min(Math.max(price, lo), hi);
                const span = hi - lo;
                const level = span > 0
                    ? Math.round(((price - lo) / span) * (LIQ_LEVELS - 1)) : 0;
                const r = rowOf(t, bar, lo, hi, level);
                if (item.side === "SELL") { r.longUsd += item.usd; r.longN += 1; }
                else { r.shortUsd += item.usd; r.shortN += 1; }
                r.total += item.usd;
                r.count += 1;
                r.pxSum += price * item.usd;
                const exKey = String(item.exchange || "?").toUpperCase();
                const slot = r.exchs[exKey] || { n: 0, usd: 0 };
                slot.n += 1;
                slot.usd += item.usd;
                r.exchs[exKey] = slot;
                if (item.id != null) r.ids.push(item.id);
            });
        });
        return Array.from(rows.values());
    }

    // Суммы по свечам (лонги/шорты/события) — из тех же рядов, что и плашки:
    // индикатор ликвидаций и цифры в шапке видят всю историю, а не только то,
    // что успело накопиться в открытом терминале.
    function liqCandleSums() {
        const out = new Map();
        liqClusterRows().forEach((r) => {
            let b = out.get(r.time);
            if (!b) { b = { long: 0, short: 0, n: 0 }; out.set(r.time, b); }
            b.long += r.longUsd;
            b.short += r.shortUsd;
            b.n += r.count;
        });
        return out;
    }

    // Ликвидации по цене: цена ряда — средняя по уровню свечи. Профиль объёма
    // (и его цифра в шапке) тоже строится по всей истории.
    function liqPriceRows() {
        return liqClusterRows().map((r) => ({
            time: r.time, bar: r.bar, level: r.level, count: r.count,
            price: r.total > 0 ? r.pxSum / r.total : Number(r.bar.close),
            long: r.longUsd, short: r.shortUsd, total: r.total,
            exchs: r.exchs, ids: r.ids,
        }));
    }

    function drawLiqRects(ctx) {
        const kvol = chartVolScale();
        const list0 = liqClusterRows();
        if (!list0.length || !state.candles.length) return;
        const clusters = new Map();
        list0.forEach((r) => { clusters.set(r.key, r); });
        if (!clusters.size) return;

        const priceOf = (c) => (c.total > 0 ? c.pxSum / c.total
                                            : Number(c.bar.close));
        const ts = chart.timeScale();
        const slotPx = plateSlotPx();
        const measure = (text, font) => {
            ctx.font = "bold " + font.toFixed(1) + "px 'JetBrains Mono', monospace";
            return ctx.measureText ? ctx.measureText(text).width : 0;
        };
        // От слабых к сильным: крупные плашки рисуются последними, поверх
        const list = Array.from(clusters.values()).sort((a, b) => a.total - b.total);
        const activeKey = pinHitKey || hoverHitKey;   // закреплённый важнее

        clusterHits = [];
        const drawn = [];    // наложение: сначала чип без цифр, потом пропуск
        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        list.forEach((c) => {
            const bar = c.bar;
            let x, yO, yC, y;
            try {
                x = ts.timeToCoordinate(c.time);
                yO = candleSeries.priceToCoordinate(Number(bar.open));
                yC = candleSeries.priceToCoordinate(Number(bar.close));
                y = candleSeries.priceToCoordinate(priceOf(c));
            } catch (e) { return; }
            if (x === null || x === undefined || !isFinite(x)) return;
            if (!isFinite(yO) || !isFinite(yC)) return;
            if (x < -40 || x > clusterCanvas.width + 40) return;
            const bodyPx = Math.abs(yC - yO);

            const whale = c.total >= WHALE_USD * kvol;
            const isLong = c.longUsd >= c.shortUsd;
            const theme = whale ? heatTheme(c.total, kvol)
                                : (isLong ? LIQ_COLORS.long : LIQ_COLORS.short);
            const wantLabel = whale || c.total >= LIQ_LABEL_MIN_USD * kvol;
            const full = fmtCompact(c.total);
            const baseH = liqPlateBaseH(wantLabel, whale, c.total);
            let geom = liqPlateGeom(bodyPx, slotPx, wantLabel ? full : "",
                                    measure, baseH);
            if (wantLabel && !geom.showLabel && full.charAt(0) === "$") {
                // Не влезла подпись со знаком валюты — пробуем без него:
                // в узком слоте выигранный символ и есть шанс показать цифры.
                const alt = liqPlateGeom(bodyPx, slotPx, full.slice(1),
                                         measure, baseH);
                if (alt.showLabel) geom = alt;
            }
            // Центр плашки — ровно на цене ликвидаций (в моменте), а не в
            // середине тела: по положению видно уровень, где снесло позиции.
            const cy = isFinite(y) ? y : (yO + yC) / 2;
            const place = (g) => {
                const bx = Math.round(x - g.w / 2), by = Math.round(cy - g.h / 2);
                for (let j = 0; j < drawn.length; j++) {
                    const d = drawn[j];
                    if (bx < d.x + d.w && bx + g.w > d.x &&
                            by < d.y + d.h && by + g.h > d.y) return null;
                }
                return { fx: bx, fy: by, bw: g.w, bh: g.h };
            };
            let box = place(geom);
            if (!box && geom.showLabel) {
                // Налезает на соседнюю плашку — как и раньше, показываем чипом
                // без цифр: уровень виден, цифры не наслаиваются.
                const chip = liqPlateGeom(bodyPx, slotPx, "", measure,
                                          LIQ_PLATE_CHIP_H);
                box = place(chip);
                if (box) geom = chip;
            }
            if (!box) return;
            const fx = box.fx, fy = box.fy, bw = box.bw, bh = box.bh;
            drawn.push({ x: fx, y: fy, w: bw, h: bh });

            const isActive = activeKey === c.key;
            clusterHits.push({ kind: "liq", x: fx, y: fy, w: bw, h: bh, key: c.key,
                ids: c.ids, time: c.time, price: priceOf(c), total: c.total,
                count: c.count, longUsd: c.longUsd, shortUsd: c.shortUsd,
                longN: c.longN, shortN: c.shortN, whale: whale, exchs: c.exchs });

            const glow = 6 + Math.min(12, (Math.log10(Math.max(c.total, 10)) - 3) * 3);
            const rad = Math.max(1, Math.min(3, bh / 2));

            // Тёмная подложка отделяет плашку от тела свечи любого цвета
            rrPath(ctx, fx - 1, fy - 1, bw + 2, bh + 2, rad + 0.5);
            ctx.fillStyle = "rgba(5,8,14,0.85)";
            ctx.fill();

            ctx.shadowColor = theme.fill;
            ctx.shadowBlur = glow;
            rrPath(ctx, fx, fy, bw, bh, rad);
            ctx.fillStyle = theme.fill;
            ctx.fill();
            ctx.shadowBlur = 0;
            ctx.lineWidth = whale ? 1.6 : 1.1;
            ctx.strokeStyle = theme.ring;
            ctx.stroke();

            // Активная плашка (наведение в ленте или нажатие на графике) —
            // ярко-белая подсветка: мягкий ореол, резкий контур и светлая
            // вуаль поверх цвета, чтобы нужный кластер было видно сразу.
            if (isActive) {
                ctx.save();
                rrPath(ctx, fx - 3, fy - 3, bw + 6, bh + 6, rad + 2);
                ctx.lineWidth = 3;
                ctx.strokeStyle = "rgba(255,255,255,0.6)";
                ctx.shadowColor = "#ffffff";
                ctx.shadowBlur = 16;
                ctx.stroke();
                ctx.shadowBlur = 0;
                rrPath(ctx, fx - 2.2, fy - 2.2, bw + 4.4, bh + 4.4, rad + 1.6);
                ctx.lineWidth = 2.2;
                ctx.strokeStyle = "#ffffff";
                ctx.stroke();
                rrPath(ctx, fx, fy, bw, bh, rad);
                ctx.fillStyle = "rgba(255,255,255,0.24)";
                ctx.fill();
                ctx.restore();
            }

            if (geom.showLabel) {
                ctx.font = "bold " + geom.font.toFixed(1) +
                    "px 'JetBrains Mono', monospace";
                ctx.fillStyle = theme.text;
                ctx.fillText(geom.text || full, x, cy + 0.5);
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
        // Порог CVD из панели фильтра: нулевой — рисуем всё, что прошло
        // автоотбор; заданный — режет слабые свечи, как порог ликвидаций.
        const thr = Math.max(minAbs, state.minCvd > 0 ? state.minCvd : 0);
        for (let i = 0; i < candles.length; i++) {
            const d = Number(candles[i].cvd);
            if (isFinite(d) && Math.abs(d) >= thr) {
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
        const cur = liqCandleSums().get(bucket);
        if (cur) {
            sum = cur.long + cur.short;
            n = cur.n;
        } else {
            // свечи этого бакета ещё нет — считаем по памяти, как раньше
            const items = visibleLiquidations();
            for (let i = 0; i < items.length; i++) {
                const t = Math.floor((Number(items[i].timestamp) || 0) / tfSec) * tfSec;
                if (t !== bucket) continue;
                sum += Number(items[i].usd) || 0;
                n += 1;
            }
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
            // Профиль — тоже по всей истории: ряд на уровень свечи.
            liqPriceRows().forEach((r) => {
                if (r.time < range.from - pad || r.time > range.to + pad) return;
                sum += r.total;
            });
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
                const v = localStorage.getItem("liqscope.statwin." + kinds[i]);
                if (v && OI_WIN_KEY[v]) state.statWin[kinds[i]] = v;
            } catch (e) { /* ignore */ }
        }
    })();
    function setStatWin(kind, key) {
        if (!OI_WIN_KEY[key]) return;
        state.statWin[kind] = key;
        try { localStorage.setItem("liqscope.statwin." + kind, key); } catch (e) {}
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
    // крупняк — чуть больше и с более сильным свечением. Размер нарочно
    // сдержанный (потолок вдвое ниже прежнего): главную работу делает цвет.
    const OI_TIER_MIN = 1000000;
    // Масштаб шариков: на прежних размерах цифры внутри не читались
    // (мини r≈6, подписи 8px). Всё, что рисуем, умножаем на этот множитель —
    // пропорции и формула вписывания текста остаются прежними.
    const OI_BALL_SCALE = 1.5;
    const OI_TIER_STYLE = [
        null,   // 0 — мини
        { mult: 1.0,  font: 0, glow: 6  },   // $1M+
        { mult: 1.10, font: 1, glow: 9 },    // $5M+
        { mult: 1.20, font: 2, glow: 12 },   // $20M+
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
            ring: b.ring, text: b.text,
            glow: Math.round(st.glow * OI_BALL_SCALE),
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
        const thr = Math.max(minAbs, state.minOi > 0 ? state.minOi : 0);
        for (let i = 0; i < candles.length; i++) {
            const d = Number(candles[i].oiChg);
            if (isFinite(d) && Math.abs(d) >= thr) {
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
            // Подбор размера считаем в «базовых» единицах (как раньше),
            // а в конце умножаем геометрию на OI_BALL_SCALE — иначе подпись
            // внутри шарика уходила в 6.5-8px и не читалась.
            const s = OI_BALL_SCALE;
            let rB = 0, fsB = 0;
            if (tier > 0) {
                ctx.font = "bold 8px 'JetBrains Mono', monospace";
                const tw8 = ctx.measureText(val).width;
                if (tw8 <= 1.9 * 18 - 5) {
                    rB = Math.min(15, Math.max(9, (tw8 + 5) / 1.9));
                    const sizes = [8, 7, 6.5];
                    for (let f = 0; f < sizes.length; f++) {
                        ctx.font = "bold " + sizes[f] + "px 'JetBrains Mono', monospace";
                        if (ctx.measureText(val).width <= 1.9 * rB - 5) { fsB = sizes[f]; break; }
                    }
                }
            }
            let r, fs = 0;
            if (!fsB) {
                r = 6 * s;   // мелочь без подписи
            } else {
                if (tier >= 2) {
                    const st = OI_TIER_STYLE[tier];
                    fsB = Math.min(10, fsB + st.font);
                    ctx.font = "bold " + fsB + "px 'JetBrains Mono', monospace";
                    rB = Math.min(15, Math.max(rB * st.mult,
                                               (ctx.measureText(val).width + 5) / 1.9));
                }
                fs = Math.round(fsB * s * 10) / 10;
                r = rB * s;
                ctx.font = "bold " + fs + "px 'JetBrains Mono', monospace";
            }
            const gap = (10 + tier * 2) * s;   // чем крупнее, тем дальше от свечи
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
    // Порядок проверки — обратный отрисовке: кластеры поверх треугольников
    // поверх шаров OI. У фигур хит-тест кругом с допуском 4px.
    function hitAt(px, py) {
        for (let i = clusterHits.length - 1; i >= 0; i--) {
            const b = clusterHits[i];
            if (px >= b.x - 4 && px <= b.x + b.w + 4 &&
                py >= b.y - 4 && py <= b.y + b.h + 4) return b;
        }
        for (let i = cvdHits.length - 1; i >= 0; i--) {
            const b = cvdHits[i];
            const dx = px - b.x, dy = py - b.y, rr = b.r + 4;
            if (dx * dx + dy * dy <= rr * rr) return b;
        }
        for (let i = oiHits.length - 1; i >= 0; i--) {
            const b = oiHits[i];
            const dx = px - b.x, dy = py - b.y, rr = b.r + 4;
            if (dx * dx + dy * dy <= rr * rr) return b;
        }
        return null;
    }

    function shapeIsActive(kind, key) {
        if (shapePin) return shapePin.kind === kind && shapePin.key === key;
        return !!shapeHover && shapeHover.kind === kind && shapeHover.key === key;
    }

    function applyFeedHighlight(ball, pin) {
        if (pin) {
            pinHitKey = (ball && ball.kind === "liq") ? ball.key : null;
            if (!ball) hoverHitKey = null;
        } else {
            hoverHitKey = (ball && ball.kind === "liq") ? ball.key : null;
        }
        const kind = ball && ball.kind;
        const matchTab = (kind === "liq" && state.feedTab === "liq")
            || (kind === "cvd" && state.feedTab === "cvd")
            || (kind === "oi" && state.feedTab === "oi");
        const ids = (kind === "liq" && ball.ids) ? new Set(ball.ids.map(String)) : null;
        const feedKey = (kind === "cvd" || kind === "oi") ? (kind + "_" + ball.time) : null;
        let firstRow = null;
        feedTbody.querySelectorAll("tr").forEach((tr) => {
            let hit = false;
            if (ball && matchTab) {
                if (ids) hit = ids.has(String(tr.dataset.liqId));
                else if (feedKey) hit = tr.dataset.feedKey === feedKey;
            }
            tr.classList.toggle("feed-hit", !!hit);
            tr.classList.toggle("feed-dim", !!(ball && matchTab) && !hit);
            if (hit && !firstRow) firstRow = tr;
        });
        if (firstRow) {
            try {
                firstRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
            } catch (e) { /* ignore */ }
        }
        queueRedraw();
    }

    // Лента → график: наведение/клик по строке подсвечивает кластер/треугольник/шар.
    function hitFromFeedItem(item) {
        if (!item) return null;
        if (item._kind === "cvd") {
            for (let i = 0; i < cvdHits.length; i++) {
                if (Number(cvdHits[i].time) === Number(item.time)) return cvdHits[i];
            }
            return {
                kind: "cvd", key: "cvd_" + item.time, x: 0, y: 0, r: 0,
                time: item.time, d: item.delta, buy: item.delta > 0,
                live: !!item.live, p90: 0, c: item.c,
            };
        }
        if (item._kind === "oi") {
            for (let i = 0; i < oiHits.length; i++) {
                if (Number(oiHits[i].time) === Number(item.time)) return oiHits[i];
            }
            return {
                kind: "oi", key: "oi_" + item.time, x: 0, y: 0, r: 0,
                time: item.time, d: item.delta, up: item.delta > 0,
                tier: 0, live: !!item.live, c: item.c,
            };
        }
        if (item.id != null) {
            const sid = String(item.id);
            for (let i = 0; i < clusterHits.length; i++) {
                const ids = clusterHits[i].ids || [];
                for (let j = 0; j < ids.length; j++) {
                    if (String(ids[j]) === sid) return clusterHits[i];
                }
            }
        }
        return null;
    }

    function highlightFromFeed(item, pin) {
        if (pinHitKey || shapePin) {
            if (!pin) return;   // закреп остаётся, пока не кликнут иначе
        }
        const hit = item ? hitFromFeedItem(item) : null;
        if (!item) {
            applyFeedHighlight(null, false);
            if (!shapePin) { shapeHover = null; queueRedraw(); }
            return;
        }
        if (hit && hit.kind === "liq") {
            applyFeedHighlight(hit, pin);
            if (pin) pinShape(hit);
            else if (!shapePin) {
                shapeHover = { kind: "liq", key: hit.key };
                queueRedraw();
            }
            return;
        }
        if (hit && (hit.kind === "cvd" || hit.kind === "oi")) {
            if (pin) {
                pinShape(hit);
                applyFeedHighlight(hit, true);
            } else if (!shapePin) {
                shapeHover = { kind: hit.kind, key: hit.key };
                applyFeedHighlight(hit, false);
            }
            return;
        }
        // события нет на текущем графике (другая монета) — подсветим только строку
        feedTbody.querySelectorAll("tr").forEach((tr) => {
            const self = (item.id != null && String(tr.dataset.liqId) === String(item.id))
                || (item._kind && tr.dataset.feedKey === item._kind + "_" + item.time);
            tr.classList.toggle("feed-hit", !!self);
            tr.classList.toggle("feed-dim", !self);
        });
    }

    function bindFeedHover(tr, item) {
        tr.addEventListener("mouseenter", () => highlightFromFeed(item, false));
        tr.addEventListener("mouseleave", () => highlightFromFeed(null, false));
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

    // --- Фигуры теханализа: рисование поверх графика ---------------------------
    // Линия, луч, горизонталь, прямоугольник, Фибоначчи + ластик. Фигуры
    // хранятся во времени/цене (не в пикселях) — переживают зум, скролл
    // и смену таймфрейма. Хранение — localStorage отдельно по каждой монете.
    const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
    const DRAW_HIT_PX = 8;
    let drawTool = null;          // null | line | ray | horiz | rect | fib | eraser
    let drawColor = "#ffd166";
    let drawDraft = null;         // первая точка двухточечной фигуры {time, price}
    let drawHover = null;         // {x, y} в пикселях для предпросмотра
    let drawFiguresList = [];     // [{t, c, pane, p1:{time,price}, p2?}]
    let drawDraftPane = null;     // в каком окне начата фигура
    let drawHoverPane = null;     // {x, y} чьего окна сейчас под курсором
    // Шкалы нижних окон (lo…hi по значениям) — заполняются, когда окно рисуется
    const paneScales = {};
    // Отступы шкалы внутри окна: те же, что в рисовании самих окон
    const PANE_PAD = { liq: 6, cvd: 6, oi: 8 };

    function drawKey() { return "liqscope.drawings." + chartSymbol(); }

    function loadDrawings() {
        drawFiguresList = [];
        drawDraft = null;
        drawHover = null;
        try {
            const raw = localStorage.getItem(drawKey());
            if (!raw) return;
            const arr = JSON.parse(raw);
            if (!Array.isArray(arr)) return;
            arr.forEach((f) => {
                if (!f || typeof f !== "object") return;
                if (["line", "ray", "horiz", "rect", "fib"].indexOf(f.t) === -1) return;
                if (!f.p1 || !isFinite(Number(f.p1.time)) || !isFinite(Number(f.p1.price))) return;
                if (f.t !== "horiz" &&
                        (!f.p2 || !isFinite(Number(f.p2.time)) || !isFinite(Number(f.p2.price)))) return;
                const pane = DRAW_PANES.indexOf(f.pane) === -1 ? "main" : f.pane;
                drawFiguresList.push({
                    t: f.t,
                    c: typeof f.c === "string" ? f.c : drawColor,
                    pane: pane,
                    p1: { time: Number(f.p1.time), price: Number(f.p1.price) },
                    p2: f.p2 ? { time: Number(f.p2.time), price: Number(f.p2.price) } : null,
                });
            });
        } catch (e) { /* ignore */ }
    }

    function saveDrawings() {
        try { localStorage.setItem(drawKey(), JSON.stringify(drawFiguresList)); } catch (e) { /* ignore */ }
    }

    function drawAddFigure(fig) {
        if (fig && !fig.pane) fig.pane = "main";
        drawFiguresList.push(fig);
        saveDrawings();
        queueRedraw();
    }

    function drawClearAll() {
        drawFiguresList = [];
        drawDraft = null;
        saveDrawings();
        queueRedraw();
    }

    // Время+цена → пиксели (null, если точка вне видимости).
    // Окна нижних графиков: x берём из общей шкалы времени, y — из шкалы окна.
    const DRAW_PANES = ["main", "liq", "cvd", "oi"];

    function paneHeight(kind) {
        const P = IND_PANES[kind];
        if (!P) return 0;
        const cv = $(P.canvas) || $(P.draw);
        if (!cv) return 0;
        return cv.clientHeight || cv.height || 0;
    }

    function paneYOf(kind, value, h) {
        const sc = paneScales[kind];
        let lo = sc ? Number(sc.lo) : 0, hi = sc ? Number(sc.hi) : 1;
        if (!isFinite(lo) || !isFinite(hi) || hi - lo < 1e-9) { lo = 0; hi = 1; }
        const pad = PANE_PAD[kind] === undefined ? 8 : PANE_PAD[kind];
        const H = h || paneHeight(kind) || 1;
        return H - pad - (H - 2 * pad) * (Number(value) - lo) / (hi - lo);
    }

    function paneValueAt(kind, y, h) {
        const sc = paneScales[kind];
        let lo = sc ? Number(sc.lo) : 0, hi = sc ? Number(sc.hi) : 1;
        if (!isFinite(lo) || !isFinite(hi) || hi - lo < 1e-9) { lo = 0; hi = 1; }
        const pad = PANE_PAD[kind] === undefined ? 8 : PANE_PAD[kind];
        const H = h || paneHeight(kind) || 1;
        const frac = (H - pad - Number(y)) / (H - 2 * pad);
        return lo + (hi - lo) * frac;
    }

    // точка фигуры главного окна → пиксели нижнего: x по времени, y по доле
    // видимого диапазона цен (чтобы уровень читался в масштабе окна)
    function projectToXY(kind, tp) {
        if (!chart || !candleSeries || !tp) return null;
        const H = paneHeight(kind);
        if (!H) return null;
        try {
            const x = chart.timeScale().timeToCoordinate(Number(tp.time));
            const ym = candleSeries.priceToCoordinate(Number(tp.price));
            const Hm = (drawCanvas && (drawCanvas.clientHeight || drawCanvas.height)) || 0;
            if (!isFinite(x) || !isFinite(ym) || !Hm) return null;
            const frac = Math.min(1, Math.max(0, 1 - ym / Hm));
            const pad = PANE_PAD[kind] === undefined ? 8 : PANE_PAD[kind];
            return { x: x, y: H - pad - (H - 2 * pad) * frac };
        } catch (e) { return null; }
    }

    // конвертер для конкретного окна: своя фигура — по своим значениям,
    // фигура с главного графика — проекцией
    function paneToXY(kind, fig) {
        return function (tp) {
            if (!tp) return null;
            if (fig && fig.pane && fig.pane !== "main") {
                try {
                    const x = chart.timeScale().timeToCoordinate(Number(tp.time));
                    if (!isFinite(x)) return null;
                    return { x: x, y: paneYOf(kind, Number(tp.price), paneHeight(kind)) };
                } catch (e) { return null; }
            }
            return projectToXY(kind, tp);
        };
    }

    function drawToXY(tp) {
        if (!chart || !candleSeries || !tp) return null;
        try {
            const x = chart.timeScale().timeToCoordinate(Number(tp.time));
            const y = candleSeries.priceToCoordinate(Number(tp.price));
            if (!isFinite(x) || !isFinite(y)) return null;
            return { x, y };
        } catch (e) { return null; }
    }

    function drawPixelToTP(x, y) {
        if (!chart || !candleSeries) return null;
        try {
            if (!chart.timeScale().coordinateToTime || !candleSeries.coordinateToPrice) return null;
            const t = chart.timeScale().coordinateToTime(x);
            const p = candleSeries.coordinateToPrice(y);
            const time = (t !== null && t !== undefined) ? Number(t) : NaN;
            const price = (p !== null && p !== undefined) ? Number(p) : NaN;
            if (!isFinite(time) || !isFinite(price)) return null;
            return { time, price };
        } catch (e) { return null; }
    }

    // клик библиотеки → время+цена (null, если мимо шкал)
    function drawClickToTP(param) {
        if (!param || !param.point || !chart || !candleSeries) return null;
        try {
            let time = (param.time !== undefined && param.time !== null) ? Number(param.time) : NaN;
            if (!isFinite(time)) {
                const tp = drawPixelToTP(param.point.x, param.point.y);
                return tp;
            }
            const price = candleSeries.coordinateToPrice
                ? Number(candleSeries.coordinateToPrice(param.point.y)) : NaN;
            if (!isFinite(price)) return null;
            return { time, price };
        } catch (e) { return null; }
    }

    // расстояние от точки до отрезка (чистая функция — покрыта тестами)
    function distToSegment(px, py, ax, ay, bx, by) {
        const dx = bx - ax, dy = by - ay;
        const len2 = dx * dx + dy * dy;
        let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
        t = Math.max(0, Math.min(1, t));
        const cx = ax + t * dx, cy = ay + t * dy;
        return Math.sqrt((px - cx) * (px - cx) + (py - cy) * (py - cy));
    }

    // цена уровня фибо (чистая функция — покрыта тестами)
    function fibPrice(p1, p2, ratio) {
        return Number(p1.price) + (Number(p2.price) - Number(p1.price)) * ratio;
    }

    // дальний конец луча: от a через b до края canvas (чистая — в тестах)
    function rayFar(a, b, W, H) {
        const dx = b.x - a.x, dy = b.y - a.y;
        if (!dx && !dy) return { x: b.x, y: b.y };
        let t = Infinity;
        if (dx > 0) t = Math.min(t, (W - a.x) / dx);
        else if (dx < 0) t = Math.min(t, (0 - a.x) / dx);
        if (dy > 0) t = Math.min(t, (H - a.y) / dy);
        else if (dy < 0) t = Math.min(t, (0 - a.y) / dy);
        if (!isFinite(t) || t < 0) t = 0;
        return { x: a.x + dx * t, y: a.y + dy * t };
    }

    // попадание клика (x, y — пиксели) в фигуру; toXY — преобразователь
    function drawFigureHit(fig, x, y, toXY, W, H) {
        const cvt = toXY || drawToXY;
        if (fig.t === "horiz") {
            const a = cvt(fig.p1);
            return !!a && Math.abs(y - a.y) <= DRAW_HIT_PX;
        }
        if (fig.t === "rect") {
            const a = cvt(fig.p1), b = cvt(fig.p2);
            if (!a || !b) return false;
            const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x);
            const y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
            return distToSegment(x, y, x0, y0, x1, y0) <= DRAW_HIT_PX ||
                   distToSegment(x, y, x1, y0, x1, y1) <= DRAW_HIT_PX ||
                   distToSegment(x, y, x1, y1, x0, y1) <= DRAW_HIT_PX ||
                   distToSegment(x, y, x0, y1, x0, y0) <= DRAW_HIT_PX;
        }
        if (fig.t === "fib") {
            const a = cvt(fig.p1);
            if (!a || x < a.x - DRAW_HIT_PX) return false;
            for (let i = 0; i < FIB_RATIOS.length; i++) {
                const pt = cvt({ time: fig.p1.time, price: fibPrice(fig.p1, fig.p2, FIB_RATIOS[i]) });
                if (pt && Math.abs(y - pt.y) <= DRAW_HIT_PX) return true;
            }
            return false;
        }
        // line / ray
        const a = cvt(fig.p1), b = cvt(fig.p2);
        if (!a || !b) return false;
        if (fig.t === "ray") {
            const f = rayFar(a, b, W || 0, H || 0);
            return distToSegment(x, y, a.x, a.y, f.x, f.y) <= DRAW_HIT_PX;
        }
        return distToSegment(x, y, a.x, a.y, b.x, b.y) <= DRAW_HIT_PX;
    }

    function drawLineSeg(ctx, x1, y1, x2, y2) {
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
    }

    function drawFigureShape(ctx, fig, W, H, preview, toXY) {
        const cvt = toXY || drawToXY;
        ctx.strokeStyle = fig.c;
        ctx.fillStyle = fig.c;
        ctx.setLineDash(preview ? [5, 4] : []);
        if (fig.t === "horiz") {
            const a = cvt(fig.p1);
            if (a) drawLineSeg(ctx, 0, a.y, W, a.y);
        } else if (fig.t === "line") {
            const a = cvt(fig.p1), b = cvt(fig.p2);
            if (a && b) drawLineSeg(ctx, a.x, a.y, b.x, b.y);
        } else if (fig.t === "ray") {
            const a = cvt(fig.p1), b = cvt(fig.p2);
            if (a && b) {
                const f = rayFar(a, b, W, H);
                drawLineSeg(ctx, a.x, a.y, f.x, f.y);
            }
        } else if (fig.t === "rect") {
            const a = cvt(fig.p1), b = cvt(fig.p2);
            if (a && b) {
                const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
                const w = Math.abs(b.x - a.x), h = Math.abs(b.y - a.y);
                ctx.globalAlpha = 0.12;
                ctx.fillRect(x, y, w, h);
                ctx.globalAlpha = 1;
                ctx.strokeRect(x + 0.5, y + 0.5, w, h);
            }
        } else if (fig.t === "fib") {
            const a = cvt(fig.p1);
            if (a) {
                ctx.lineWidth = 1;
                ctx.font = "11px Inter, system-ui, sans-serif";
                ctx.textBaseline = "bottom";
                FIB_RATIOS.forEach((r) => {
                    const lvl = fibPrice(fig.p1, fig.p2, r);
                    const pt = cvt({ time: fig.p1.time, price: lvl });
                    if (!pt) return;
                    drawLineSeg(ctx, a.x, pt.y, W, pt.y);
                    const label = (r * 100).toFixed(1) + "%  " + lvl.toFixed(priceDigits(lvl));
                    const tw = ctx.measureText ? ctx.measureText(label).width : 60;
                    ctx.fillText(label, Math.max(2, W - tw - 6), pt.y - 2);
                });
                ctx.lineWidth = 2;
            }
        }
        ctx.setLineDash([]);
    }

    function paneOf(fig) {
        return fig && DRAW_PANES.indexOf(fig.pane) !== -1 ? fig.pane : "main";
    }

    function drawFigures() {
        if (!drawCanvas || !chart || !candleSeries) return;
        const ctx = drawCanvas.getContext("2d");
        const W = drawCanvas.width, H = drawCanvas.height;
        ctx.clearRect(0, 0, W, H);
        ctx.lineWidth = 2;
        drawFiguresList.forEach((fig) => {
            if (paneOf(fig) === "main") drawFigureShape(ctx, fig, W, H, false);
        });
        drawPreview(ctx, W, H);
    }

    // --- фигуры в нижних окнах (LIQ/CVD/OI) --------------------------------
    // В окне видны: свои фигуры (по его шкале значений) и проекции фигур
    // основного графика — приглушённо, чтобы уровни читались по всей стопке.
    function paneOverlayCtx(kind) {
        const P = IND_PANES[kind];
        if (!P) return null;
        const canvas = $(P.draw);
        if (!canvas || !state[P.skey]) return null;
        const parent = canvas.parentElement || {};
        const w = canvas.clientWidth || parent.clientWidth || 0;
        const h = canvas.clientHeight || parent.clientHeight || 0;
        if (w < 10 || h < 8) return { canvas: canvas, ctx: canvas.getContext("2d"), w: 0, h: 0 };
        const dpr = window.devicePixelRatio || 1;
        const W = Math.round(w * dpr), H = Math.round(h * dpr);
        if (canvas.width !== W || canvas.height !== H) {
            canvas.width = W; canvas.height = H;
        }
        const ctx = canvas.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        return { canvas: canvas, ctx: ctx, w: w, h: h };
    }

    function drawPaneFigures(kind) {
        const p = paneOverlayCtx(kind);
        if (!p || !p.w) return;
        const { ctx, w, h } = p;
        ctx.lineWidth = 2;
        drawFiguresList.forEach((fig) => {
            const cvt = paneToXY(kind, fig);
            if (paneOf(fig) === kind) {
                drawFigureShape(ctx, fig, w, h, false, cvt);
                return;
            }
            if (paneOf(fig) !== "main") return;
            ctx.save();
            ctx.globalAlpha = 0.5;
            drawFigureShape(ctx, fig, w, h, true, cvt);
            ctx.restore();
        });
        drawPanePreview(kind, ctx, w, h);
    }

    // tests/ind_panes.js: состояние рисования — без мыши в jsdom его не
    // выставить, а проверить предпросмотр фигуры в окне нужно.
    function drawTestState(patch) {
        if (patch) {
            if ("tool" in patch) drawTool = patch.tool;
            if ("draft" in patch) drawDraft = patch.draft;
            if ("draftPane" in patch) drawDraftPane = patch.draftPane;
            if ("hover" in patch) drawHover = patch.hover;
            if ("hoverPane" in patch) drawHoverPane = patch.hoverPane;
        }
        return { tool: drawTool, draft: drawDraft, draftPane: drawDraftPane,
                 hover: drawHover, hoverPane: drawHoverPane };
    }

    function drawPanePreview(kind, ctx, w, h) {
        if (!drawTool || drawTool === "eraser") return;
        const ownDraft = drawDraftPane === kind;      // фигура начата в этом окне
        if (drawTool === "horiz") {
            if (!drawHover || drawHoverPane !== kind) return;
            ctx.strokeStyle = drawColor;
            ctx.setLineDash([5, 4]);
            drawLineSeg(ctx, 0, drawHover.y, w, drawHover.y);
            ctx.setLineDash([]);
            return;
        }
        if (!ownDraft) {
            // Фигура начата в другом окне: показываем только её проекцию —
            // тянуть «резинку» в чужой шкале нельзя (значения окон разные).
            if (!drawDraft || drawDraftPane === "main") return;
            const a = paneToXY(kind, { pane: "main" })(drawDraft);
            if (!a) return;
            ctx.fillStyle = drawColor;
            ctx.beginPath();
            ctx.arc(a.x, a.y, 4, 0, Math.PI * 2);
            ctx.fill();
            return;
        }
        // Своя фигура окна рисуется ПО ШКАЛЕ ЭТОГО ОКНА: раньше предпросмотр
        // уходил в проекцию главного графика (значение окна как цена), из-за
        // чего линия при перетаскивании прилипала к верхней кромке окна.
        const cvt = paneToXY(kind, { pane: kind });
        if (drawDraft) {
            const a = cvt(drawDraft);
            if (a) {
                ctx.fillStyle = drawColor;
                ctx.beginPath();
                ctx.arc(a.x, a.y, 4, 0, Math.PI * 2);
                ctx.fill();
            }
        }
        if (!drawHover || drawHoverPane !== kind || !drawDraft) return;
        const a = cvt(drawDraft);
        if (!a) return;
        const tp = panePixelToTP(kind, drawHover.x, drawHover.y);
        if (!tp) return;
        drawFigureShape(ctx, { t: drawTool, c: drawColor, pane: kind,
                               p1: drawDraft, p2: tp }, w, h, true, cvt);
    }

    // время по пикселю в окне: шкала времени общая с графиком, но если
    // coordinateToTime молчит — берём ближайшую свечу (точка «липнет»
    // к свече, как и положено при рисовании по свечам)
    function paneTimeAt(x) {
        if (!chart) return null;
        try {
            const t = chart.timeScale().coordinateToTime(x);
            if (t !== null && t !== undefined && isFinite(Number(t))) return Number(t);
        } catch (e) { /* ниже — обходной путь */ }
        let best = null, bestD = Infinity;
        (state.candles || []).forEach((c) => {
            let cx = null;
            try { cx = chart.timeScale().timeToCoordinate(c.time); } catch (e) { return; }
            if (cx === null || cx === undefined || !isFinite(cx)) return;
            const d = Math.abs(cx - x);
            if (d < bestD) { bestD = d; best = Number(c.time); }
        });
        return best;
    }

    // пиксель в окне → время (общая шкала) и значение шкалы окна
    function panePixelToTP(kind, x, y) {
        const time = paneTimeAt(x);
        if (time === null || !isFinite(time)) return null;
        return { time: time, price: paneValueAt(kind, y, paneHeight(kind)) };
    }

    function drawPaneDrawings() {
        Object.keys(IND_PANES).forEach((kind) => {
            try { drawPaneFigures(kind); } catch (e) { /* окно не должно ломать график */ }
        });
    }

    // клик по окну: та же логика, что на основном графике
    function drawPointAt(tp, pane) {
        const p = pane || "main";
        if (drawTool === "horiz") {
            drawAddFigure({ t: "horiz", c: drawColor, pane: p, p1: tp, p2: null });
            return;
        }
        if (!drawDraft || drawDraftPane !== p) {
            drawDraft = tp;
            drawDraftPane = p;
            queueRedraw();
            return;
        }
        drawAddFigure({ t: drawTool, c: drawColor, pane: p, p1: drawDraft, p2: tp });
        drawDraft = null;
        drawDraftPane = null;
    }

    function paneErase(kind, x, y) {
        const h = paneHeight(kind);
        const w = (paneOverlayCtx(kind) || {}).w || 0;
        for (let i = drawFiguresList.length - 1; i >= 0; i--) {
            const fig = drawFiguresList[i];
            const own = paneOf(fig) === kind;
            if (!own && paneOf(fig) !== "main") continue;
            const cvt = paneToXY(kind, fig);
            if (drawFigureHit(fig, x, y, cvt, w, h)) {
                drawFiguresList.splice(i, 1);
                saveDrawings();
                queueRedraw();
                return true;
            }
        }
        return false;
    }

    function setupPaneDrawings() {
        Object.keys(IND_PANES).forEach((kind) => {
            const P = IND_PANES[kind];
            const canvas = $(P.draw);
            if (!canvas) return;
            canvas.addEventListener("click", (e) => {
                if (!drawTool) return;
                const x = e.offsetX, y = e.offsetY;
                if (drawTool === "eraser") { paneErase(kind, x, y); return; }
                const tp = panePixelToTP(kind, x, y);
                if (!tp) return;
                drawPointAt(tp, kind);
            });
            canvas.addEventListener("mousemove", (e) => {
                if (!drawTool) return;
                drawHover = { x: e.offsetX, y: e.offsetY };
                drawHoverPane = kind;
                queueRedraw();
            });
            canvas.addEventListener("mouseleave", () => {
                if (drawHoverPane !== kind) return;
                drawHover = null;
                drawHoverPane = null;
                queueRedraw();
            });
            canvas.addEventListener("contextmenu", (e) => {
                if (!drawTool) return;
                e.preventDefault();
                if (drawDraft) { drawDraft = null; drawDraftPane = null; queueRedraw(); }
                else setDrawTool(null);
            });
        });
    }

    function drawPreview(ctx, W, H) {
        if (!drawTool || drawTool === "eraser") return;
        if (drawHoverPane && drawHoverPane !== "main") return;
        if (drawDraft && drawDraftPane && drawDraftPane !== "main") return;
        if (drawTool === "horiz") {
            if (!drawHover) return;
            ctx.strokeStyle = drawColor;
            ctx.setLineDash([5, 4]);
            drawLineSeg(ctx, 0, drawHover.y, W, drawHover.y);
            ctx.setLineDash([]);
            return;
        }
        if (!drawDraft) return;
        const a = drawToXY(drawDraft);
        if (a) {
            ctx.fillStyle = drawColor;
            ctx.beginPath();
            ctx.arc(a.x, a.y, 4, 0, Math.PI * 2);
            ctx.fill();
        }
        if (!drawHover || !a) return;
        const tp = drawPixelToTP(drawHover.x, drawHover.y);
        if (!tp) return;
        drawFigureShape(ctx, { t: drawTool, c: drawColor, p1: drawDraft, p2: tp }, W, H, true);
    }

    function setDrawTool(tool) {
        drawTool = tool;
        drawDraft = null;
        drawDraftPane = null;
        drawHover = null;
        drawHoverPane = null;
        Object.keys(IND_PANES).forEach((kind) => {
            const cv = $(IND_PANES[kind].draw);
            if (!cv) return;
            cv.classList.toggle("armed", !!tool);
            cv.classList.toggle("erase", tool === "eraser");
        });
        if (drawToolbar) {
            drawToolbar.querySelectorAll("[data-draw-tool]").forEach((b) =>
                b.classList.toggle("active", b.getAttribute("data-draw-tool") === tool));
        }
        if (drawToggle) drawToggle.classList.toggle("active", !!tool);
        if (drawToolbar) drawToolbar.classList.toggle("hidden", !tool);
        if (tool) {
            // вход в рисование снимает закреп фигур слоёв и их окна
            pinHitKey = null;
            hoverHitKey = null;
            applyFeedHighlight(null, true);
            unpinShape();
            hideShapeModal();
            if (chartWrapper) chartWrapper.style.cursor = tool === "eraser" ? "not-allowed" : "crosshair";
        } else if (chartWrapper) {
            chartWrapper.style.cursor = "";
        }
        queueRedraw();
    }

    function drawClick(param) {
        if (!param || !param.point) return;
        if (drawTool === "eraser") {
            const W = drawCanvas ? drawCanvas.width : 0;
            const H = drawCanvas ? drawCanvas.height : 0;
            for (let i = drawFiguresList.length - 1; i >= 0; i--) {
                if (drawFigureHit(drawFiguresList[i], param.point.x, param.point.y, drawToXY, W, H)) {
                    drawFiguresList.splice(i, 1);
                    saveDrawings();
                    queueRedraw();
                    return;
                }
            }
            return;
        }
        const tp = drawClickToTP(param);
        if (!tp) return;
        drawPointAt(tp, "main");
    }

    function setupDrawToolbar() {
        if (drawToggle) {
            drawToggle.addEventListener("click", () => {
                setDrawTool(drawTool ? null : "line");
            });
        }
        if (drawToolbar) {
            drawToolbar.querySelectorAll("[data-draw-tool]").forEach((b) => {
                b.addEventListener("click", () => setDrawTool(b.getAttribute("data-draw-tool")));
            });
            drawToolbar.querySelectorAll("[data-draw-color]").forEach((b) => {
                b.addEventListener("click", () => {
                    drawColor = b.getAttribute("data-draw-color") || drawColor;
                    drawToolbar.querySelectorAll("[data-draw-color]").forEach((o) =>
                        o.classList.toggle("active", o === b));
                });
            });
            const clearBtn = $("draw-clear");
            if (clearBtn) clearBtn.addEventListener("click", drawClearAll);
            const closeBtn = $("draw-close");
            if (closeBtn) closeBtn.addEventListener("click", () => setDrawTool(null));
        }
        document.addEventListener("keydown", (e) => {
            if (!drawTool) return;
            if (e.key === "Escape") {
                if (drawDraft) { drawDraft = null; queueRedraw(); }
                else setDrawTool(null);
            }
        });
        if (chartWrapper) {
            chartWrapper.addEventListener("contextmenu", (e) => {
                if (!drawTool) return;
                e.preventDefault();
                if (drawDraft) { drawDraft = null; queueRedraw(); }
                else setDrawTool(null);
            });
        }
        // тестовый API для jsdom-гарнесса tests/drawings.js
        setupPaneDrawings();
        window.LiqScopeLiq = {
            plateGeom: liqPlateGeom,
            slotPx: plateSlotPx,
            plateGap: LIQ_PLATE_GAP,
            plateBaseH: liqPlateBaseH,
            fontMin: LIQ_PLATE_FONT_MIN,
            // для tests/liq_plates.js: что реально нарисовано и как свеча
            // отображается в пиксели при текущем масштабе
            hits: () => clusterHits.map((h) => ({ x: h.x, y: h.y, w: h.w,
                                                  h: h.h, key: h.key,
                                                  price: h.price, total: h.total,
                                                  count: h.count })),
            priceToY: (v) => candleSeries.priceToCoordinate(Number(v)),
            timeToX: (t) => chart.timeScale().timeToCoordinate(Number(t)),
            // для tests/liq_hover.js: подсветка кластера, как при наведении
            // строки ленты (в jsdom мышью не походишь)
            setHover: (key) => { hoverHitKey = key || null; queueRedraw(); },
            // Растягивание графика: в jsdom библиотека не пересчитывает
            // ширину слота, поэтому tests/liq_plates.js подменяет у шкалы
            // времени timeToCoordinate — свечи и плашки растут вместе,
            // ровно как при живом зуме.
            timeScale: () => chart.timeScale(),
            candles: () => (state.candles || []).map((c) => ({
                time: c.time, open: c.open, close: c.close,
                high: c.high, low: c.low,
            })),
            // для tests/liq_plates.js: кластеры сохранённой истории приходят
            // отдельным запросом — в тесте подставляем их руками, как если бы
            // терминал был закрыт, а сервер всё сохранил
            liqHistory: (rows, cut) => {
                state.liqHist = rows || {};
                state.liqCut = Number(cut) || 0;
                queueRedraw();
            },
            liqHistRows: () => state.liqHist,
            // для tests/liq_plates.js: история живёт не только плашками —
            // те же ряды видит индикатор, цифры и метки-киты
            liqRows: () => liqClusterRows().map((r) => ({
                key: r.key, time: r.time, level: r.level, longUsd: r.longUsd,
                shortUsd: r.shortUsd, total: r.total, count: r.count,
                price: r.total > 0 ? r.pxSum / r.total : Number(r.bar.close),
            })),
            liqSums: () => Array.from(liqCandleSums().entries()).map(([t, b]) => ({
                time: t, long: b.long, short: b.short, n: b.n })),
            liqPriceRows: () => liqPriceRows(),
            liqPaneVal: () => {
                const el = $("ind-liq-val");
                return el ? el.textContent : null;
            },
            refreshMarkers: () => { updateMarkers(); return lastMarkers.slice(); },
            // фильтр бирж и слои: тесты проверяют, что история уходит на сервер
            // и что окна не «немые»
            liqExchParam: () => liqExchParam(),
            setExchanges: (list) => { state.exchanges = list ? new Set(list) : null; },
            reloadClusters: () => loadLiqClusters(true),
            layerState: () => ({ liq: !!state.liqEnabled, cvd: !!state.cvdEnabled,
                                 oi: !!state.oiEnabled, paneLiq: !!state.paneLiq,
                                 paneCvd: !!state.paneCvd, paneOi: !!state.paneOi,
                                 profile: !!state.profileEnabled }),
            redraw: () => drawClusters(),
        };
        window.LiqScopeDraw = {
            fibPrice, distToSegment, rayFar,
            paneCanvas: (kind) => {
                const P = IND_PANES[kind];
                return P ? $(P.draw) : null;
            },
            paneToXY: (kind, tp) => paneToXY(kind, { pane: kind })(tp),
            testState: drawTestState,
            projectToXY: projectToXY,
            setPaneScale: (kind, lo, hi) => { paneScales[kind] = { lo: lo, hi: hi }; },
            paneValueAt: paneValueAt,
            panePixelToTP: panePixelToTP,
            paneTimeAt: paneTimeAt,
            clickPoint: (tp, pane) => drawPointAt(tp, pane),
            drawAt: drawPaneDrawings,
            figureHit: (fig, x, y, toXY, W, H) => drawFigureHit(fig, x, y, toXY, W, H),
            add: drawAddFigure,
            clear: drawClearAll,
            setTool: setDrawTool,
            save: saveDrawings,
            load: loadDrawings,
            getFigures: () => drawFiguresList.slice(),
            getTool: () => drawTool,
            getColor: () => drawColor,
        };
    }

    function setupClusterInteraction() {
        if (!chart || !chart.subscribeCrosshairMove || !chart.subscribeClick) return;
        try {
            // наведение (и палец на телефоне при движении по графику)
            chart.subscribeCrosshairMove((param) => {
                if (drawTool) {   // рисование: только предпросмотр, окна фигур молчат
                    drawHover = (param && param.point) ? { x: param.point.x, y: param.point.y } : null;
                    queueRedraw();
                    return;
                }
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
                } else {
                    // кластер / треугольник / шар: окно фигуры + строка в
                    // открытой ленте того же типа (liq / cvd / oi)
                    hoverShape(hit);
                    applyFeedHighlight(hit, false);
                }
            });
            // клик/тап: закрепить фигуру; повторный клик по ней или клик
            // мимо — снять закреп
            chart.subscribeClick((param) => {
                if (drawTool) { drawClick(param); return; }   // точки фигур вместо окон
                const hit = (param && param.point)
                    ? hitAt(param.point.x, param.point.y) : null;
                const pinned = hit && (
                    (hit.kind === "liq" && hit.key === pinHitKey) ||
                    (shapePin && shapePin.kind === hit.kind && shapePin.key === hit.key)
                );
                if (!hit || pinned) {
                    applyFeedHighlight(null, true);
                    unpinShape();
                    hideShapeModal();
                } else {
                    applyFeedHighlight(hit, true);
                    pinShape(hit);
                    openShapeModal(hit.kind, hit);
                }
            });
        } catch (e) { /* старая библиотека без подписок — просто без подсветки */ }
    }

    // --- Регулируемые панели: размеры сохраняются в браузере -----------------
    function clampPx(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    // Высоты блоков графика: главный тянется сам (занимает остаток стека),
    // окна LIQ/CVD/OI — своим полотном в px. Складываем всё в тот же
    // liqscope.layout, что и лента с «Лидерами».
    const IND_KINDS = ["liq", "cvd", "oi"];
    const IND_DEFAULT_CANVAS_H = 64;   // высота полотна окна по умолчанию
    const IND_MIN_CANVAS_H = 0;        // полное сужение разрешено
    const IND_HEAD_H = 26;             // шапка окна (в переменную высоты не входит)
    const SPLIT_H = 8;                 // высота разделителя
    const STACK_SLACK_H = 12;          // отступы контейнера окон + запас на округления
    const LAYOUT = {
        feedW: 430, coinsH: 170,
        indLiq: IND_DEFAULT_CANVAS_H, indCvd: IND_DEFAULT_CANVAS_H,
        indOi: IND_DEFAULT_CANVAS_H,
        sized: false,      // пользователь сам двигал высоты блоков графика
    };
    let layoutReady = false;

    function indKey(kind) {
        return "ind" + kind.charAt(0).toUpperCase() + kind.slice(1);
    }

    function loadLayout() {
        try {
            const raw = localStorage.getItem("liqscope.layout");
            if (!raw) return;
            const o = JSON.parse(raw) || {};
            LAYOUT.feedW = clampPx(Number(o.feedW) || 430, 280, 720);
            LAYOUT.coinsH = clampPx(Number(o.coinsH) || 170, 90, 460);
            IND_KINDS.forEach((k) => {
                const v = Number(o[indKey(k)]);
                LAYOUT[indKey(k)] = clampPx(isFinite(v) ? v : IND_DEFAULT_CANVAS_H, 0, 900);
            });
            LAYOUT.sized = !!o.sized;
        } catch (e) { /* ignore */ }
    }

    function saveLayout() {
        try { localStorage.setItem("liqscope.layout", JSON.stringify(LAYOUT)); } catch (e) { /* ignore */ }
    }

    function applyLayoutValues() {
        const root = document.documentElement;
        root.style.setProperty("--feed-width", LAYOUT.feedW + "px");
        root.style.setProperty("--coins-height", LAYOUT.coinsH + "px");
        IND_KINDS.forEach((k) => root.style.setProperty("--ind-h-" + k, LAYOUT[indKey(k)] + "px"));
        const stack = $("chart-stack");
        if (stack) stack.classList.toggle("sized", !!LAYOUT.sized);
    }

    function initSplitters() {
        loadLayout();
        const bind = (el, axis) => {
            if (!el) return;
            el.addEventListener("pointerdown", (e) => {
                e.preventDefault();
                el.classList.add("active");
                document.body.classList.add("split-dragging");
                if (axis === "y") document.body.classList.add("split-dragging-y");
                const start = axis === "x" ? e.clientX : e.clientY;
                const startVal = axis === "x" ? LAYOUT.feedW : LAYOUT.coinsH;
                const move = (ev) => {
                    ev.preventDefault();
                    const delta = (axis === "x" ? ev.clientX : ev.clientY) - start;
                    if (axis === "x") {
                        // лента прижата к правому краю: тянем разделитель вправо →
                        // её ширина УМЕНЬШАЕТСЯ (иначе блоки «уезжают» влево)
                        LAYOUT.feedW = clampPx(startVal - delta, 280, 720);
                    } else {
                        LAYOUT.coinsH = clampPx(startVal - delta, 90, 460);
                    }
                    applyLayoutValues();
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
        bindStackSplitters();
        layoutReady = true;
        normalizeHeights();
        applyLayoutValues();
    }

    // --- Блоки графика: главный график и окна LIQ/CVD/OI тянутся по высоте ---
    // Разделитель над окном забирает высоту у блока выше: у соседнего окна —
    // напрямую, у главного графика — просто из остатка стека. Сузить можно
    // до нуля, включая главный график; двойной клик возвращает исходное.

    // Телефон/планшет: разделители скрыты, окна держат фиксированную высоту
    // и скроллятся внутри своей области — высоты там не считаем.
    function mobileLayout() {
        return !!(window.matchMedia && window.matchMedia("(max-width: 900px)").matches);
    }

    function paneVisible(kind) {
        const P = IND_PANES[kind];
        return !!(P && state[P.skey]);
    }

    function visiblePaneKinds() { return IND_KINDS.filter(paneVisible); }

    function visibleSplitCount() {
        return document.querySelectorAll("#chart-stack .splitter:not(.hidden)").length;
    }

    // Ниже 120px стек считается «нераскладочным» (свёрнутый график, крошечное
    // окно, jsdom): тогда высоты не пересчитываем и не ужимаем окна в ноль.
    function stackHeight() {
        const stack = $("chart-stack");
        const h = (stack && stack.clientHeight) || 0;
        return h >= 120 ? h : 0;
    }

    // min-height главного графика: пока пользователь не двигал высоты, окна не
    // имеют права съесть график целиком (в «сжатом» режиме он тоже в ноль).
    function chartMinHeight() {
        if (LAYOUT.sized) return 0;
        const wrap = document.querySelector("#chart-stack > .chart-wrapper");
        if (!wrap || !window.getComputedStyle) return 0;
        let mh = NaN;
        try { mh = parseFloat(window.getComputedStyle(wrap).minHeight); } catch (e) { /* ignore */ }
        return isFinite(mh) && mh > 0 ? mh : 0;
    }

    // Сколько всего пикселей полотен есть у видимых окон: высота стека минус
    // шапки окон, разделители и то, что оставлено главному графику.
    function panesBudget() {
        const total = stackHeight();
        if (!total) return 0;
        return Math.max(0, total - chartMinHeight() - STACK_SLACK_H
            - visiblePaneKinds().length * IND_HEAD_H - visibleSplitCount() * SPLIT_H);
    }

    // Сколько максимум может занять полотно окна, чтобы блоки не вылезли
    // за стек: главный график при этом считается сжимаемым до нуля.
    function maxCanvasFor(kind) {
        if (!stackHeight() || mobileLayout()) return 900;   // раскладки нет (телефон, jsdom)
        let others = 0;
        visiblePaneKinds().forEach((k) => { if (k !== kind) others += LAYOUT[indKey(k)]; });
        return Math.max(IND_MIN_CANVAS_H, panesBudget() - others);
    }

    // Если окна в сумме больше стека (например, окно уменьшили или включили
    // ещё одно) — ужимаем их пропорционально, сохраняя соотношение высот.
    function normalizeHeights() {
        if (!layoutReady) return;
        if (!stackHeight() || mobileLayout()) return;
        const kinds = visiblePaneKinds();
        if (!kinds.length) return;
        const avail = panesBudget();
        const sum = kinds.reduce((s, k) => s + LAYOUT[indKey(k)], 0);
        if (sum <= avail || sum <= 0) return;
        const k = avail / sum;
        kinds.forEach((kind) => {
            LAYOUT[indKey(kind)] = Math.round(LAYOUT[indKey(kind)] * k);
        });
    }

    let layoutRaf = 0;
    function redrawAfterLayout() {
        if (typeof queueRedraw === "function") queueRedraw();
        if (layoutRaf) return;
        const raf = window.requestAnimationFrame || ((f) => setTimeout(f, 16));
        layoutRaf = raf(() => {
            layoutRaf = 0;
            // главный график пересчитывает размер по своему контейнеру
            window.dispatchEvent(new Event("resize"));
        });
    }

    function resetStackHeights() {
        IND_KINDS.forEach((k) => { LAYOUT[indKey(k)] = IND_DEFAULT_CANVAS_H; });
        LAYOUT.sized = false;
        applyLayoutValues();
        redrawAfterLayout();
        saveLayout();
    }

    function bindStackSplitters() {
        const list = document.querySelectorAll("#chart-stack .stack-splitter");
        Array.prototype.forEach.call(list, (el) => {
            const kind = el.getAttribute("data-pane");
            if (!kind) return;
            el.addEventListener("pointerdown", (e) => {
                e.preventDefault();
                const kinds = visiblePaneKinds();
                const i = kinds.indexOf(kind);
                if (i < 0) return;                    // окно выключено — тянуть нечего
                const prevKind = i > 0 ? kinds[i - 1] : "";   // "" — выше главный график
                const startY = e.clientY;
                const startH = LAYOUT[indKey(kind)];
                const startPrev = prevKind ? LAYOUT[indKey(prevKind)] : 0;
                el.classList.add("active");
                document.body.classList.add("split-dragging", "split-dragging-y");
                const move = (ev) => {
                    ev.preventDefault();
                    const grow = startY - ev.clientY;   // тянем вверх — окно растёт
                    let want = startH + grow;
                    if (prevKind) {
                        // высоту отдаёт соседнее окно и только до своего нуля
                        const take = clampPx(want - startH, -startH, startPrev);
                        want = startH + take;
                        LAYOUT[indKey(prevKind)] = Math.round(startPrev - take);
                    }
                    want = clampPx(want, IND_MIN_CANVAS_H, maxCanvasFor(kind));
                    LAYOUT[indKey(kind)] = Math.round(want);
                    LAYOUT.sized = true;
                    applyLayoutValues();
                    redrawAfterLayout();
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
            el.addEventListener("dblclick", resetStackHeights);
        });
        window.addEventListener("resize", () => {
            normalizeHeights();
            applyLayoutValues();
        });
    }

    // --- Топ пар: выпадающий вертикальный список -----------------------------
    function setupTopCoinsToggle() {
        const box = $("top-coins-box");
        const tgl = $("top-coins-toggle");
        if (!box || !tgl) return;
        let open = true;
        try { open = localStorage.getItem("liqscope.coinsOpen") !== "0"; } catch (e) { /* ignore */ }
        const render = () => {
            box.classList.toggle("closed", !open);
            tgl.setAttribute("aria-expanded", open ? "true" : "false");
        };
        tgl.addEventListener("click", () => {
            open = !open;
            try { localStorage.setItem("liqscope.coinsOpen", open ? "1" : "0"); } catch (e) { /* ignore */ }
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

        const items = liqPriceRows().filter((x) =>
            x.time >= range.from - pad && x.time <= range.to + pad);
        if (!items.length) return;

        const bins = [];
        for (let i = 0; i < PROFILE_BINS; i++) bins.push({ long: 0, short: 0, total: 0 });
        const step = (maxP - minP) / PROFILE_BINS;
        items.forEach((x) => {
            const idx = Math.min(Math.floor((x.price - minP) / step), PROFILE_BINS - 1);
            if (idx < 0) return;
            const b = bins[idx];
            b.long += x.long;
            b.short += x.short;
            b.total += x.total;
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
            '<td><span class="exch-badge ' + item.exchange + '">' + item.exchange +
            "</span></td>" +
            '<td><span class="badge-side ' + (isLong ? "long" : "short") + '">' +
            (isLong ? "LONG LIQ" : "SHORT LIQ") + "</span></td>" +
            '<td class="td-usd-amount ' + valClass + '">$' + fmtUsdFull(item.usd) + "</td>" +
            '<td class="td-price">' + fmtPrice(item.price) + "</td>";
        tr.addEventListener("click", () => {
            const hit = hitFromFeedItem(item);
            if (hit && hit.kind === "liq") applyFeedHighlight(hit, true);
            openModal(item);
        });
        bindFeedHover(tr, item);
        const coinBtn = tr.querySelector(".coin-link");
        if (coinBtn) {
            coinBtn.addEventListener("click", (e) => {
                e.stopPropagation();      // не открываем модалку — фильтруем ленту
                selectSymbol(decodeURIComponent(coinBtn.dataset.symbol));
            });
        }
        return tr;
    }

    /** Плашка активного фильтра ленты: клик по ней возвращает все монеты. */
    function updateFeedFilter() {
        if (!feedFilterEl) return;
        const on = state.symbol !== "ALL";
        feedFilterEl.classList.toggle("hidden", !on);
        if (!on) return;
        feedFilterEl.textContent = pretty(state.symbol) + " ✕";
        feedFilterEl.title = I18n.t("feed.chip_title");
        feedFilterEl.setAttribute("aria-label", I18n.t("feed.chip_title"));
    }

    function feedCountLabel(n) {
        return I18n.plural(n, [
            I18n.t("feed.count_one", { n: n }),
            I18n.t("feed.count_few", { n: n }),
            I18n.t("feed.count_many", { n: n }),
        ]);
    }

    function addFeedRow(item) {
        if (state.feedTab !== "liq") return;
        if (!passesFeedFilter(item)) return;
        feedEmptyEl.classList.add("hidden");
        feedTbody.insertBefore(feedRow(item), feedTbody.firstChild);
        while (feedTbody.children.length > 150) feedTbody.removeChild(feedTbody.lastChild);
        feedCountEl.textContent = feedCountLabel(feedTbody.children.length);
    }

    function paintFeedHeaders() {
        const table = $("feed-table");
        if (table) table.className = "feed-table feed-kind-" + state.feedTab;
        const usd = $("feed-th-usd");
        if (usd) {
            usd.textContent = state.feedTab === "cvd" ? I18n.t("feed.col_cvd")
                            : state.feedTab === "oi" ? I18n.t("feed.col_oi")
                            : I18n.t("feed.col_usd");
        }
        document.querySelectorAll(".feed-tab").forEach((btn) => {
            const on = btn.getAttribute("data-feed") === state.feedTab;
            btn.classList.toggle("active", on);
            btn.setAttribute("aria-selected", on ? "true" : "false");
        });
        // Порог у каждой ленты свой: подсвечиваем поле открытой вкладки,
        // чтобы пресеты и подпись кнопки было с чем сверять.
        refreshFilterButtons();
    }

    // Значение ленты CVD/OI за свечу. У OI в свече лежит уровень открытого
    // интереса (миллиарды), а изменение за свечу — в oiChg: лента показывает
    // именно дельту, как и шары на графике (столбец так и называется «OI Δ»).
    function shapeFeedValue(c, field) {
        return field === "oi" ? Number(c.oiChg) : Number(c[field]);
    }

    /** Лента «ВСЕ»: строки не по свечам графика, а по минутным потокам всех
     *  монет (сообщение flow_all). Раньше при переключении с монеты на «все»
     *  лента молча оставалась прежней — строилась только по свечам графика. */
    const isFlowFeed = (field) =>
        state.symbol === "ALL" && (field === "cvd" || field === "oi");

    function flowFeedItems(field) {
        const src = (state.flowAll && state.flowAll[field]) || [];
        const items = [];
        src.forEach((row) => {
            const raw = field === "cvd" ? row.cvd : row.oi_delta;
            const d = Number(raw);
            if (!isFinite(d) || d === 0) return;
            const ts = Number(row.ts) || Number(state.flowAll && state.flowAll.ts) || 0;
            items.push({
                _kind: field,
                flow: true,
                agg: row,
                time: ts,
                timestamp: ts,
                symbol: row.symbol,
                usd: Math.abs(d),
                delta: d,
                price: row.price,
                live: false,          // окно, а не формирующаяся свеча
            });
        });
        return items;
    }

    function shapeFeedItems(field) {
        if (isFlowFeed(field)) return flowFeedItems(field);
        const candles = state.candles;
        const items = [];
        if (!candles.length) return items;
        const lastIdx = candles.length - 1;
        const absVals = [];
        for (let i = 0; i < candles.length; i++) {
            const d = Math.abs(shapeFeedValue(candles[i], field));
            if (isFinite(d) && d > 0) absVals.push(d);
        }
        absVals.sort((a, b) => a - b);
        const p90 = absVals.length ? (absVals[Math.floor(0.9 * (absVals.length - 1))] || 0) : 0;
        const minAbs = Math.max(1000 * chartVolScale(), p90 * 0.05);
        for (let i = candles.length - 1; i >= 0 && items.length < 150; i--) {
            const d = shapeFeedValue(candles[i], field);
            if (!isFinite(d) || Math.abs(d) < minAbs) continue;
            const thr = field === "cvd" ? state.minCvd : state.minOi;
            if (thr > 0 && Math.abs(d) < thr) continue;
            items.push({
                _kind: field === "cvd" ? "cvd" : "oi",
                time: candles[i].time,
                timestamp: candles[i].time,
                symbol: chartSymbol(),
                usd: Math.abs(d),
                delta: d,
                price: candles[i].close,
                live: i === lastIdx,
                c: candles[i],
            });
        }
        return items;
    }

    function shapeFeedKey(item) {
        // в режиме «ВСЕ» строки идут по всем монетам сразу — монета в ключе,
        // иначе строки разных монет в одну минуту затирали бы друг друга
        return item._kind + "_" + (item.flow ? item.symbol + "_" : "") + item.time;
    }

    function shapeFeedType(item) {
        const isCvd = item._kind === "cvd";
        const up = item.delta >= 0;
        return {
            isCvd: isCvd,
            up: up,
            typeClass: isCvd ? (up ? "badge-cvd-buy" : "badge-cvd-sell")
                             : (up ? "badge-oi-up" : "badge-oi-down"),
            typeLabel: isCvd
                ? I18n.t(up ? "feed.cvd_buy" : "feed.cvd_sell")
                : I18n.t(up ? "feed.oi_up" : "feed.oi_down"),
            valClass: isCvd ? (up ? "cvd-buy-val" : "cvd-sell-val")
                            : (up ? "oi-up-val" : "oi-down-val"),
            sign: up ? "+" : "−",
        };
    }

    function shapeFeedTypeLabel(item) {
        const t = shapeFeedType(item);
        if (!item.flow) return t;
        // в потоке «ВСЕ» подписываем окно: строка — сумма за N минут
        const win = Number((item.agg && item.agg.window_min) ||
                           (state.flowAll && state.flowAll.window_min) || 5);
        return Object.assign({}, t, {
            typeLabel: t.typeLabel + " · " + I18n.t("flow.window_short", { n: win }),
        });
    }

    function paintShapeFeedCells(tr, item) {
        tr._feedItem = item;
        tr.dataset.feedKey = shapeFeedKey(item);
        tr.classList.toggle("feed-row-live", !!item.live);
        const t = shapeFeedTypeLabel(item);
        let usdTd = tr.querySelector(".td-usd-amount");
        if (!usdTd) {
            const openTitle = I18n.t("feed.open_chart", { sym: pretty(item.symbol) });
            const openTitleHtml = openTitle.replace(/"/g, "&quot;");
            tr.innerHTML =
                '<td class="td-time">' + I18n.time(item.timestamp) + "</td>" +
                '<td class="td-coin"><button class="coin-link" type="button" data-symbol="' +
                encodeURIComponent(item.symbol) + '" title="' + openTitleHtml +
                '" aria-label="' + openTitleHtml + '">' +
                "<strong>" + pretty(item.symbol) + "</strong><span class=\"coin-link-icon\">📈</span></button></td>" +
                "<td></td>" +
                '<td><span class="' + t.typeClass + '">' + t.typeLabel + "</span></td>" +
                '<td class="td-usd-amount ' + t.valClass + '">' + t.sign + "$" + fmtUsdFull(item.usd) + "</td>" +
                '<td class="td-price">' + fmtPrice(item.price) + "</td>";
            return;
        }
        usdTd.className = "td-usd-amount " + t.valClass;
        usdTd.textContent = t.sign + "$" + fmtUsdFull(item.usd);
        const priceTd = tr.querySelector(".td-price");
        if (priceTd) priceTd.textContent = fmtPrice(item.price);
        const badge = tr.cells[3] ? tr.cells[3].querySelector("span") : null;
        if (badge) {
            badge.className = t.typeClass;
            badge.textContent = t.typeLabel;
        }
    }

    /** Окно строки потока «ВСЕ»: на графике этой монеты нет, поэтому вместо
     *  подсветки фигуры показываем разбор строки — чем и как продавали. */
    function openFlowModal(item) {
        const a = item.agg || {};
        const isCvd = item._kind === "cvd";
        const win = Number(a.window_min || (state.flowAll && state.flowAll.window_min) || 5);
        const money = (v) => (Number(v) >= 0 ? "+" : "−") +
            "$" + fmtUsdFull(Math.abs(Number(v) || 0));
        const share = (a.cvd_share == null) ? "—"
            : (Number(a.cvd_share) >= 0 ? "+" : "") + Number(a.cvd_share) + "%";
        const rows = [
            ["flow.window", I18n.t("flow.window_val", { n: win })],
            isCvd ? ["flow.cvd", money(item.delta)] : ["flow.oi_delta", money(item.delta)],
        ];
        if (isCvd) rows.push(["flow.share", share]);
        rows.push(["flow.oi_level", a.oi_usd == null ? "—" : "$" + fmtUsdFull(a.oi_usd)]);
        rows.push(["flow.liq_long", "$" + fmtUsdFull(a.liq_long || 0)]);
        rows.push(["flow.liq_short", "$" + fmtUsdFull(a.liq_short || 0)]);
        rows.push(["flow.vol", "$" + fmtUsdFull(a.vol || 0)]);
        if (item.price) rows.push(["flow.price", fmtPrice(item.price)]);
        state.modalItem = null;
        modalTitle.textContent = pretty(item.symbol) + " · " +
            I18n.t(isCvd ? "feed.col_cvd" : "feed.col_oi");
        modalBody.innerHTML =
            rows.map((r) => "<p><strong>" + I18n.t(r[0]) + "</strong> " + r[1] + "</p>").join("") +
            '<p class="flow-note">' + I18n.t("flow.note") + "</p>";
        unpinShape();
        detailModal.classList.remove("hidden");
        detailModal.classList.remove("peek", "peek-pinned");
    }

    function shapeFeedRow(item) {
        const tr = document.createElement("tr");
        paintShapeFeedCells(tr, item);
        tr.addEventListener("click", () => {
            const it = tr._feedItem || item;
            if (it.flow) { openFlowModal(it); return; }
            const hit = hitFromFeedItem(it);
            if (hit) {
                applyFeedHighlight(hit, true);
                pinShape(hit);
                openShapeModal(hit.kind, hit);
            }
        });
        tr.addEventListener("mouseenter", () => {
            const it = tr._feedItem || item;
            if (it.flow) return;      // фигуры этой монеты на графике нет
            highlightFromFeed(it, false);
        });
        tr.addEventListener("mouseleave", () => {
            if ((tr._feedItem || item).flow) return;
            highlightFromFeed(null, false);
        });
        const coinBtn = tr.querySelector(".coin-link");
        if (coinBtn) {
            coinBtn.addEventListener("click", (e) => {
                e.stopPropagation();      // фильтр ленты + выбор монеты на графике
                selectSymbol(decodeURIComponent(coinBtn.dataset.symbol));
            });
        }
        return tr;
    }

    function finishShapeFeed(field, n) {
        feedCountEl.textContent = feedCountLabel(n);
        if (feedEmptyEl) {
            feedEmptyEl.textContent = isFlowFeed(field)
                ? I18n.t("feed.empty_flow")
                : I18n.t(field === "cvd" ? "feed.empty_cvd" : "feed.empty_oi");
            feedEmptyEl.classList.toggle("hidden", n > 0);
        }
    }

    function rebuildShapeFeed(field) {
        const rows = shapeFeedItems(field);
        feedTbody.innerHTML = "";
        const frag = document.createDocumentFragment();
        rows.forEach((item) => frag.appendChild(shapeFeedRow(item)));
        feedTbody.appendChild(frag);
        finishShapeFeed(field, rows.length);
    }

    // Тик не должен сносить DOM: иначе ховер срывается (mouseleave),
    // подсветка мигает, закреп фигуры сбрасывается. Обновляем ячейки на месте.
    function syncShapeFeed(field) {
        if (!feedTbody) return;
        const items = shapeFeedItems(field);
        if (!feedTbody.children.length) {
            rebuildShapeFeed(field);
            return;
        }
        const have = new Map();
        Array.from(feedTbody.children).forEach((tr) => {
            if (tr.dataset.feedKey) have.set(tr.dataset.feedKey, tr);
        });
        const keep = new Set();
        const ordered = [];
        for (let i = 0; i < items.length; i++) {
            const item = items[i];
            const key = shapeFeedKey(item);
            keep.add(key);
            let tr = have.get(key);
            if (!tr) tr = shapeFeedRow(item);
            else paintShapeFeedCells(tr, item);
            ordered.push(tr);
        }
        Array.from(feedTbody.children).forEach((tr) => {
            if (!keep.has(tr.dataset.feedKey)) feedTbody.removeChild(tr);
        });
        for (let i = 0; i < ordered.length; i++) {
            if (feedTbody.children[i] !== ordered[i]) {
                feedTbody.insertBefore(ordered[i], feedTbody.children[i] || null);
            }
        }
        finishShapeFeed(field, items.length);
    }

    function rebuildFeed() {
        pinHitKey = null;
        hoverHitKey = null;
        if (shapePin && (shapePin.kind === "liq" || shapePin.kind === "cvd" || shapePin.kind === "oi")) {
            if (!state.modalItem) {
                unpinShape();
                hideShapeModal();
            }
        }
        paintFeedHeaders();
        if (state.feedTab === "cvd") { rebuildShapeFeed("cvd"); return; }
        if (state.feedTab === "oi") { rebuildShapeFeed("oi"); return; }
        if (feedEmptyEl) feedEmptyEl.textContent = I18n.t("feed.empty");
        const rows = state.liquidations.filter(passesFeedFilter).slice(-150).reverse();
        feedTbody.innerHTML = "";
        const frag = document.createDocumentFragment();
        rows.forEach((item) => {
            const tr = feedRow(item);
            tr.classList.remove("feed-row-new");
            frag.appendChild(tr);
        });
        feedTbody.appendChild(frag);
        feedCountEl.textContent = feedCountLabel(rows.length);
        feedEmptyEl.classList.toggle("hidden", rows.length > 0);
    }

    function setFeedTab(tab) {
        if (tab !== "liq" && tab !== "cvd" && tab !== "oi") return;
        if (state.feedTab === tab) return;
        state.feedTab = tab;
        try { localStorage.setItem("liqscope.feedTab", tab); } catch (e) { /* ignore */ }
        rebuildFeed();
        sendFeedConfig();     // «ВСЕ» + CVD/OI → сервер начинает поток всех монет
    }

    function setupFeedTabs() {
        try {
            const v = localStorage.getItem("liqscope.feedTab");
            if (v === "liq" || v === "cvd" || v === "oi") state.feedTab = v;
        } catch (e) { /* ignore */ }
        const tabs = $("feed-tabs");
        if (!tabs) return;
        tabs.addEventListener("click", (e) => {
            const btn = e.target.closest ? e.target.closest(".feed-tab") : null;
            if (!btn) return;
            setFeedTab(btn.getAttribute("data-feed"));
        });
        paintFeedHeaders();
    }

    let shapeFeedTimer = null;
    function queueShapeFeed() {
        if (state.feedTab !== "cvd" && state.feedTab !== "oi") return;
        if (shapeFeedTimer) return;
        shapeFeedTimer = setTimeout(() => {
            shapeFeedTimer = null;
            if (state.feedTab === "cvd") syncShapeFeed("cvd");
            else if (state.feedTab === "oi") syncShapeFeed("oi");
        }, 400);
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
            "<p><strong>" + I18n.t("modal.time") + "</strong> " + I18n.dateTime(item.timestamp) + "</p>" +
            // источник события: «выведено из ленты» надо показывать честно —
            // биржа такие ликвидации не публикует, их считает терминал
            (item.kind === "tape"
                ? '<p><strong>' + I18n.t("modal.src") + "</strong> " +
                  I18n.t("modal.src_tape") +
                  (item.liq ? '<span class="liq-src"> · markPx ' +
                             fmtPrice(item.liq.markPx) +
                             (item.liq.method ? " · " + String(item.liq.method) : "") +
                             (item.liq.liquidatedUser
                                 ? ' · <span class="wallet">' +
                                   String(item.liq.liquidatedUser).slice(0, 10) + "…" +
                                   "</span>"
                                 : "") + "</span>"
                             : "") +
                  "</p>"
                : '<p><strong>' + I18n.t("modal.src") + "</strong> " +
                  I18n.t("modal.src_feed") + "</p>");
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
            // Биржи кластера: бейдж + число событий + сумма, по убыванию суммы.
            let exchRow = "";
            if (hit.exchs) {
                const parts = Object.keys(hit.exchs)
                    .map((e) => ({ e: e, n: hit.exchs[e].n, usd: hit.exchs[e].usd }))
                    .sort((a, b) => b.usd - a.usd)
                    .map((o) => '<span class="exch-badge ' + o.e.toLowerCase() + '">' +
                        o.e.charAt(0) + o.e.slice(1).toLowerCase() + "</span> ×" + o.n +
                        " $" + fmtUsdShort(o.usd));
                if (parts.length) {
                    exchRow = "<p><strong>" + I18n.t("modal.exchs") + "</strong> " +
                        parts.join(" · ") + "</p>";
                }
            }
            extraRows =
                "<p><strong>" + I18n.t("modal.level") + "</strong> ≈ " +
                fmtPrice(hit.price) + "</p>" +
                "<p><strong>" + I18n.t("modal.events") + "</strong> " + hit.count +
                " · " + hit.longN + " LONG / " + hit.shortN + " SHORT</p>" +
                "<p><strong>" + I18n.t("modal.split") + "</strong> " +
                '<span class="badge-side long">L $' + fmtUsdShort(hit.longUsd) + "</span> " +
                '<span class="badge-side short">S $' + fmtUsdShort(hit.shortUsd) + "</span></p>" +
                exchRow;
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

    // --- Панель кнопок слоёв: выезжает сверху по кнопке «☰ Слои» -------------
    // Кнопки Ликвидации/CVD/OI живут в попапе, а их цифры — в верхней плашке
    // с текстовыми подписями. Закрытие: повторный клик, клик мимо, Esc.
    function setupLayerPop() {
        const callBtn = $("layer-call"), pop = $("layer-pop");
        if (!callBtn || !pop) return;
        const setOpen = (open) => {
            pop.classList.toggle("hidden", !open);
            callBtn.classList.toggle("active", open);
        };
        callBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            setOpen(pop.classList.contains("hidden"));
        });
        // клики по кнопкам слоёв попап не закрывают — можно щёлкать несколько
        pop.addEventListener("click", (e) => e.stopPropagation());
        document.addEventListener("click", () => setOpen(false));
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") setOpen(false);
        });
        window.LiqScopeLayers = {  // тестовый API для tests/legend_layout.js
            isOpen: () => !pop.classList.contains("hidden"),
            setOpen,
        };
    }

    // --- Слои графика: ликвидации / профиль / CVD ----------------------------
    // По умолчанию при входе на график все слои выключены, а сами переключатели
    // видны только зарегистрированным пользователям (авторизация сайта).
    const LAYER_KEYS = ["profileEnabled", "liqEnabled", "cvdEnabled", "oiEnabled",
                        "paneLiq", "paneCvd", "paneOi"];

    async function applyAuthGate() {
        let user = null;
        try {
            const r = await fetch("/api/auth/me", { credentials: "same-origin" });
            const d = await r.json();
            user = (d && d.user) || null;
        } catch (e) { user = null; }
        state.userLoggedIn = !!user;
        // dev-обход для автотестов без Telegram-авторизации
        let dev = false;
        try { dev = localStorage.getItem("liqscope.devLayers") === "1"; } catch (e) { /* ignore */ }
        state.layersAllowed = state.userLoggedIn || dev;
        if (!state.layersAllowed) {
            LAYER_KEYS.forEach((k) => { state[k] = false; });
            const call = $("layer-call");
            if (call) call.classList.add("hidden");
        }
        return state.layersAllowed;
    }

    function setupLayerToggles(allowed) {
        const defs = [
            { el: profileToggle, skey: "profileEnabled", store: "liqscope.profileEnabled",
              on: "chart.profile_on", off: "chart.profile_off" },
            { el: $("liq-toggle"), skey: "liqEnabled", store: "liqscope.liqEnabled",
              on: "chart.liq_on", off: "chart.liq_off" },
            { el: $("cvd-toggle"), skey: "cvdEnabled", store: "liqscope.cvdEnabled",
              on: "chart.cvd_on", off: "chart.cvd_off" },
            { el: $("oi-toggle"), skey: "oiEnabled", store: "liqscope.oiEnabled",
              on: "chart.oi_on", off: "chart.oi_off" },
            // индикаторные окна под графиком — те же данные, что в кабинете
            { el: $("pane-liq-toggle"), skey: "paneLiq", store: "liqscope.paneLiq",
              on: "chart.pane_liq_on", off: "chart.pane_liq_off", pane: "liq" },
            { el: $("pane-cvd-toggle"), skey: "paneCvd", store: "liqscope.paneCvd",
              on: "chart.pane_cvd_on", off: "chart.pane_cvd_off", pane: "cvd" },
            { el: $("pane-oi-toggle"), skey: "paneOi", store: "liqscope.paneOi",
              on: "chart.pane_oi_on", off: "chart.pane_oi_off", pane: "oi" },
        ];
        defs.forEach((d) => {
            if (!d.el) return;
            if (allowed) {
                try {
                    const v = localStorage.getItem(d.store);
                    if (v === "0") state[d.skey] = false;
                    else if (v === "1") state[d.skey] = true;
                } catch (e) { /* ignore */ }
            } else {
                state[d.skey] = false;
            }
            const paint = () => {
                d.el.classList.toggle("active", state[d.skey]);
                d.el.title = I18n.t(state[d.skey] ? d.on : d.off);
                if (d.pane) syncPaneVisibility(d.pane);
            };
            d.el.addEventListener("click", () => {
                state[d.skey] = !state[d.skey];
                try {
                    localStorage.setItem(d.store, state[d.skey] ? "1" : "0");
                } catch (e) { /* ignore */ }
                if (d.pane) syncPaneVisibility(d.pane);
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

    // --- Индикаторные окна под графиком (LIQ / CVD / OI) --------------------
    // Те же живые данные, что в кабинете «Алерты по объёму»: короткие окна
    // с прозрачной сеткой и цифрами. Выровнены по свечам основного графика
    // (общий timeScale), листаются/зумятся вместе с ним.
    const IND_PANES = {
        liq: { canvas: "ind-canvas-liq", val: "ind-liq-val", pane: "ind-pane-liq",
               draw: "ind-draw-liq",
               skey: "paneLiq", toggle: "pane-liq-toggle", off: "chart.pane_liq_off" },
        cvd: { canvas: "ind-canvas-cvd", val: "ind-cvd-val", pane: "ind-pane-cvd",
               draw: "ind-draw-cvd",
               skey: "paneCvd", toggle: "pane-cvd-toggle", off: "chart.pane_cvd_off" },
        oi:  { canvas: "ind-canvas-oi",  val: "ind-oi-val",  pane: "ind-pane-oi",
               draw: "ind-draw-oi",
               skey: "paneOi", toggle: "pane-oi-toggle", off: "chart.pane_oi_off" },
    };

    function syncPaneVisibility(kind) {
        const P = IND_PANES[kind];
        if (!P) return;
        const el = $(P.pane);
        if (el) el.classList.toggle("hidden", !state[P.skey]);
        // разделитель показываем только у включённого окна: у выключенного
        // он висел бы пустой полосой
        const sp = $("split-" + kind + "-y");
        if (sp) sp.classList.toggle("hidden", !state[P.skey]);
        const box = $("indicator-panes");
        if (box) {
            const any = Object.keys(IND_PANES).some((k) => state[IND_PANES[k].skey]);
            box.classList.toggle("all-hidden", !any);
        }
        // высоты блоков пересчитываем: включение/выключение окна меняет остаток
        normalizeHeights();
    }

    function setupIndicatorPanes() {
        // незарегистрированным окна не положены вовсе
        if (!state.layersAllowed) {
            const box = $("indicator-panes");
            if (box) box.classList.add("all-hidden");
            // окон нет вовсе — разделители блоков тоже прячем, иначе под
            // графиком осталась бы пустая полоса из трёх полосок
            const splits = document.querySelectorAll("#chart-stack .stack-splitter");
            Array.prototype.forEach.call(splits, (el) => el.classList.add("hidden"));
            return;
        }
        Object.keys(IND_PANES).forEach((kind) => {
            const P = IND_PANES[kind];
            // крестик на окне = выключить соответствующую кнопку в «Слоях»
            const btn = $("ind-close-" + kind);
            if (btn) {
                btn.addEventListener("click", () => {
                    const tog = $(P.toggle);
                    if (tog) tog.click();
                });
            }
            syncPaneVisibility(kind);
        });
        // после синхронизации раскладываем сохранённые высоты блоков
        applyLayoutValues();
    }

    // Подписи чисел в окнах: $1.2K / $3.4M… с плюсом, если надо
    function indMoney(v, signed) {
        const n = Number(v) || 0;
        const s = signed ? (n >= 0 ? "+" : "−") : (n < 0 ? "−" : "");
        return s + "$" + fmtUsdShort(Math.abs(n));
    }

    function indBarSpacing() {
        // расстояние между свечами в px — из двух соседних видимых точек
        const ts = chart.timeScale();
        const cs = state.candles;
        for (let i = 1; i < cs.length; i++) {
            let x0 = null, x1 = null;
            try {
                x0 = ts.timeToCoordinate(cs[i - 1].time);
                x1 = ts.timeToCoordinate(cs[i].time);
            } catch (e) { return 6; }
            if (x0 != null && x1 != null && x1 !== x0) {
                return Math.max(3, Math.abs(x1 - x0));
            }
        }
        return 6;
    }

    // Сетка и подписи шкалы, как на биржевом графике
    function indGrid(ctx, w, h, lo, hi) {
        ctx.save();
        ctx.font = "9px 'JetBrains Mono', monospace";
        ctx.textBaseline = "middle";
        for (let i = 1; i <= 3; i++) {
            const y = Math.round(h * i / 4) + 0.5;
            ctx.strokeStyle = "rgba(132,147,168,0.13)";
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
            if (isFinite(lo) && isFinite(hi)) {
                const val = hi - (hi - lo) * i / 4;
                ctx.fillStyle = "rgba(132,147,168,0.75)";
                ctx.textAlign = "right";
                ctx.fillText(indMoney(val), w - 3, y - 5);
            }
        }
        ctx.restore();
    }

    function indVerticals(ctx, w, h, spacing) {
        const step = Math.max(1, Math.round(120 / spacing));
        const ts = chart.timeScale();
        const cs = state.candles;
        ctx.save();
        ctx.strokeStyle = "rgba(132,147,168,0.07)";
        for (let i = 0; i < cs.length; i += step) {
            let x = null;
            try { x = ts.timeToCoordinate(cs[i].time); } catch (e) { break; }
            if (x == null || x < 0 || x > w) continue;
            ctx.beginPath();
            ctx.moveTo(Math.round(x) + 0.5, 0);
            ctx.lineTo(Math.round(x) + 0.5, h);
            ctx.stroke();
        }
        ctx.restore();
    }

    function indPrepareCanvas(kind) {
        const P = IND_PANES[kind];
        const canvas = $(P.canvas);
        if (!canvas || !state[P.skey]) return null;
        const dpr = window.devicePixelRatio || 1;
        const w = canvas.clientWidth || (canvas.parentElement || {}).clientWidth || 0;
        const h = canvas.clientHeight || 0;
        if (w < 10 || h < 8) return null;   // окно сужено в ноль — рисовать нечего
        const W = Math.round(w * dpr), H = Math.round(h * dpr);
        if (canvas.width !== W || canvas.height !== H) {
            canvas.width = W; canvas.height = H;
        }
        const ctx = canvas.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        return { P: P, canvas: canvas, ctx: ctx, w: w, h: h };
    }

    // Полупрозрачная кривая «в моменте» — как линия OI: точки по свечам,
    // мягкая заливка под ней и точка последнего значения. Своя шкала
    // (lo…hi считается по переданным точкам), поэтому кривая накопления
    // спокойно живёт рядом со столбиками, у которых шкала своя.
    function indCurve(ctx, w, h, pts, color, fill, symmetric) {
        if (!pts || pts.length < 2) return false;
        let lo = Infinity, hi = -Infinity;
        pts.forEach((p) => { lo = Math.min(lo, p.v); hi = Math.max(hi, p.v); });
        if (!isFinite(lo) || !isFinite(hi)) return false;
        if (symmetric) {
            const m = Math.max(Math.abs(lo), Math.abs(hi));
            lo = -m; hi = m;
        }
        if (hi - lo < 1e-9) { hi = lo + 1; }
        const pad = 8;
        const yOf = (v) => h - pad - (h - 2 * pad) * (v - lo) / (hi - lo);
        ctx.save();
        ctx.beginPath();
        pts.forEach((p, i) => {
            if (!i) ctx.moveTo(p.x, yOf(p.v)); else ctx.lineTo(p.x, yOf(p.v));
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.4;
        ctx.stroke();
        // заливка под линией — «полупрозрачная», как на OI
        const first = pts[0], last = pts[pts.length - 1];
        ctx.lineTo(last.x, h); ctx.lineTo(first.x, h); ctx.closePath();
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(last.x, yOf(last.v), 2.4, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.restore();
        return true;
    }

    function indNoData(p, text) {
        p.ctx.save();
        p.ctx.fillStyle = "rgba(132,147,168,0.55)";
        p.ctx.font = "10px Inter, sans-serif";
        p.ctx.textAlign = "center";
        p.ctx.textBaseline = "middle";
        p.ctx.fillText(text || "нет данных", p.w / 2, p.h / 2);
        p.ctx.restore();
    }

    function indSetVal(kind, txt, cls, title) {
        const el = $(IND_PANES[kind].val);
        if (!el) return;
        el.textContent = txt;
        el.className = "ind-val" + (cls ? " " + cls : "");
        if (title !== undefined) el.title = title;
    }

    // ЛИКВИДАЦИИ: столбики от середины — 🔴 вниз вынесли лонг, 🟢 вверх шорт
    function drawPaneLiq() {
        const p = indPrepareCanvas("liq");
        if (!p) return;
        const { ctx, w, h } = p;
        // Столбики — по тем же рядам, что и плашки на графике: история с сервера
        // плюс живой хвост. Раньше тут была только память открытого терминала.
        const bars = liqCandleSums();
        const ts = chart.timeScale();
        const cs = state.candles;
        const spacing = indBarSpacing();
        indGrid(ctx, w, h, null, null);
        indVerticals(ctx, w, h, spacing);
        const bw = Math.max(1.5, Math.min(28, spacing - 1.5));
        let maxSide = 0, totL = 0, totS = 0, seen = 0;
        const pts = [];
        cs.forEach((c) => {
            const b = bars.get(Number(c.time));
            let x = null;
            try { x = ts.timeToCoordinate(c.time); } catch (e) { return; }
            if (x == null || x < -40 || x > w + 40) return;
            const L = b ? b.long : 0, S = b ? b.short : 0;
            pts.push({ x: x, L: L, S: S });
            maxSide = Math.max(maxSide, L, S);
            totL += L; totS += S; seen++;
        });
        // нулевая линия посередине
        const mid = Math.round(h / 2) + 0.5;
        ctx.strokeStyle = "rgba(132,147,168,0.35)";
        ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
        if (!maxSide) {
            indNoData(p);
            indSetVal("liq", "Σ $0", "");
            return;
        }
        // шкала окна — для фигур теханализа, которые тут рисуют
        paneScales.liq = { lo: -maxSide, hi: maxSide };
        const kTop = (h / 2 - 6) / maxSide;   // вверх — шорты
        const kBot = (h / 2 - 6) / maxSide;   // вниз — лонги
        pts.forEach((pt) => {
            if (pt.S > 0) {
                const hh = Math.max(1, pt.S * kTop);
                ctx.fillStyle = "rgba(0,214,255,0.85)";
                ctx.fillRect(pt.x - bw / 2, mid - hh, bw, hh);
            }
            if (pt.L > 0) {
                const hh = Math.max(1, pt.L * kBot);
                ctx.fillStyle = "rgba(255,42,95,0.85)";
                ctx.fillRect(pt.x - bw / 2, mid, bw, hh);
            }
        });
        // Накопленный перевес за видимый диапазон: шорты (+), лонги (−).
        // Полупрозрачная кривая «в моменте» — как линия OI.
        let acc = 0;
        const cum = pts.map((pt) => { acc += pt.S - pt.L; return { x: pt.x, v: acc }; });
        indCurve(ctx, w, h, cum, "rgba(255,209,102,0.85)", "rgba(255,209,102,0.08)", true);
        // подписи краёв шкалы
        ctx.font = "9px 'JetBrains Mono', monospace";
        ctx.textAlign = "right";
        ctx.fillStyle = "rgba(0,214,255,0.8)";
        ctx.fillText("▲ " + indMoney(maxSide), w - 3, 8);
        ctx.fillStyle = "rgba(255,42,95,0.8)";
        ctx.fillText("▼ " + indMoney(maxSide), w - 3, h - 8);
        indSetVal("liq", "Σ " + indMoney(totL + totS), "",
                   "Столбики: лонги " + indMoney(totL) + " / шорты " + indMoney(totS)
                   + " · кривая: накопленный перевес " + indMoney(acc, true));
    }

    // CVD: гистограмма тейкер-дельты по свечам (зелёный — покупки, красный — продажи)
    function drawPaneCvd() {
        const p = indPrepareCanvas("cvd");
        if (!p) return;
        const { ctx, w, h } = p;
        const ts = chart.timeScale();
        const cs = state.candles;
        const spacing = indBarSpacing();
        const bw = Math.max(1.5, Math.min(28, spacing - 1.5));
        const pts = [];
        let lo = 0, hi = 0, net = 0, has = false;
        cs.forEach((c) => {
            const d = Number(c.cvd);
            let x = null;
            try { x = ts.timeToCoordinate(c.time); } catch (e) { return; }
            if (x == null || x < -40 || x > w + 40) return;
            if (isFinite(d)) {
                pts.push({ x: x, d: d });
                lo = Math.min(lo, d); hi = Math.max(hi, d);
                net += d; has = true;
            }
        });
        paneScales.cvd = { lo: lo, hi: hi };
        indGrid(ctx, w, h, lo, hi);
        indVerticals(ctx, w, h, spacing);
        if (!has) { indNoData(p); indSetVal("cvd", "—", ""); return; }
        const span = Math.max(1e-9, hi - lo);
        const pad = 6;
        const yOf = (v) => h - pad - (h - 2 * pad) * (v - lo) / span;
        const y0 = Math.round(yOf(0)) + 0.5;
        ctx.strokeStyle = "rgba(132,147,168,0.35)";
        ctx.beginPath(); ctx.moveTo(0, y0); ctx.lineTo(w, y0); ctx.stroke();
        pts.forEach((pt) => {
            const y = yOf(pt.d);
            ctx.fillStyle = pt.d >= 0 ? "rgba(0,230,118,0.8)" : "rgba(255,42,95,0.8)";
            const top = Math.min(y, y0), hh = Math.max(1, Math.abs(y - y0));
            ctx.fillRect(pt.x - bw / 2, top, bw, hh);
        });
        // Накопленная дельта по видимым свечам — полупрозрачная кривая
        // «в моменте», как линия OI: видно, куда рынок перевесил.
        let acc = 0;
        const cum = pts.map((pt) => { acc += pt.d; return { x: pt.x, v: acc }; });
        indCurve(ctx, w, h, cum, "rgba(167,139,250,0.9)", "rgba(167,139,250,0.10)", true);
        indSetVal("cvd", indMoney(net, true), net >= 0 ? "pos" : "neg",
                  "Сумма тейкер-дельты по видимым свечам: " + indMoney(net, true)
                  + " · кривая — накопление по свечам");
    }

    // OI: линия открытого интереса по свечам (золото) + дельта в шапке
    function drawPaneOi() {
        const p = indPrepareCanvas("oi");
        if (!p) return;
        const { ctx, w, h } = p;
        const ts = chart.timeScale();
        const cs = state.candles;
        const spacing = indBarSpacing();
        const pts = [];
        let lo = Infinity, hi = -Infinity, first = NaN, last = NaN;
        cs.forEach((c) => {
            const v = Number(c.oi);
            let x = null;
            try { x = ts.timeToCoordinate(c.time); } catch (e) { return; }
            if (x == null || x < -40 || x > w + 40) return;
            if (!isFinite(v)) return;
            pts.push({ x: x, v: v });
            lo = Math.min(lo, v); hi = Math.max(hi, v);
            if (!isFinite(first)) first = v;
            last = v;
        });
        if (!pts.length) {
            indGrid(ctx, w, h, null, null);
            indNoData(p, "OI: нет данных по бирже");
            indSetVal("oi", "—", "");
            return;
        }
        paneScales.oi = { lo: lo, hi: hi };
        indGrid(ctx, w, h, lo, hi);
        indVerticals(ctx, w, h, spacing);
        // линия OI + мягкая подсветка под ней (та же геометрия, что у CVD/LIQ)
        indCurve(ctx, w, h, pts, "#ffd54f", "rgba(255,213,79,0.07)", false);
        const chg = last - first;
        indSetVal("oi", indMoney(chg, true), chg >= 0 ? "pos" : "neg",
                  "Изменение OI за видимый диапазон · сейчас " + indMoney(last));
    }

    function drawIndicatorPanes() {
        if (!chart || !state.candles.length) return;
        try {
            if (state.paneLiq) drawPaneLiq();
            if (state.paneCvd) drawPaneCvd();
            if (state.paneOi) drawPaneOi();
            drawPaneDrawings();   // фигуры теханализа поверх окон
        } catch (e) { /* индикаторные окна не должны ломать график */ }
    }

    // --- Сворачивание графика (нужно на телефоне) ----------------------------
    function setupChartToggle() {
        if (!chartToggle || !chartSection) return;
        try {
            if (localStorage.getItem("liqscope.chartCollapsed") === "1") {
                chartSection.classList.add("collapsed");
            }
        } catch (e) { /* ignore */ }

        chartToggle.addEventListener("click", () => {
            const collapsed = chartSection.classList.toggle("collapsed");
            try {
                localStorage.setItem("liqscope.chartCollapsed", collapsed ? "1" : "0");
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
            try { localStorage.setItem("liqscope.chartSymbol", s); } catch (e) { /* ignore */ }
        }
        const chartChanged = chartSymbol() !== prevChart;
        if (chartChanged) {
            state.tickCount = 0;
            state.lastTickAt = 0;
            state.lagMs = null;
            unpinShape();
            closeModal();
            loadDrawings();   // фигуры — свои у каждой монеты
        }
        updateSymbolTitle();
        updateFeedFilter();
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
        try { localStorage.setItem("liqscope.chartSymbol", s); } catch (e) { /* ignore */ }
        loadDrawings();   // фигуры — свои у каждой монеты
        state.tickCount = 0;
        state.lastTickAt = 0;
        state.lagMs = null;
        updateSymbolTitle();
        updateFeedFilter();
        renderSymbolButtons();
        sendConfig();
        loadCandles();
        loadHistoryFor(s);   // пузырьки новой пары сразу с полной историей
    }

    if (feedFilterEl) {
        feedFilterEl.addEventListener("click", () => selectSymbol("ALL"));
        feedFilterEl.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                selectSymbol("ALL");
            }
        });
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
            const note = sourceNote(e);
            if (note) {
                // Примечание источника из /api/health: биржа сама объясняет,
                // почему по ней может не быть событий.
                label.classList.add("check-row-warn");
                label.title = note;
                label.appendChild(cb);
                label.appendChild(name);
                const tag = document.createElement("span");
                tag.className = "check-note";
                tag.textContent = note.length > 28 ? note.slice(0, 27) + "…" : note;
                label.appendChild(tag);
            } else {
                label.appendChild(cb);
                label.appendChild(name);
            }
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

    function thresholdLabel() {
        const parts = [];
        const liq = thresholdOf("liq"), cvd = thresholdOf("cvd"), oi = thresholdOf("oi");
        if (liq > 0) {
            parts.push(I18n.t("filter.th_short_liq") + " " +
                       I18n.t("filter.th_ge", { v: I18n.number(liq) }));
        }
        if (cvd > 0) parts.push("CVD " + I18n.t("filter.th_ge", { v: I18n.number(cvd) }));
        if (oi > 0) parts.push("OI Δ " + I18n.t("filter.th_ge", { v: I18n.number(oi) }));
        return parts.length ? parts.join(" · ") + " ▾" : I18n.t("filter.all_usd");
    }

    function refreshFilterButtons() {
        if (!minUsdBtn) return;
        minUsdBtn.textContent = thresholdLabel();
        const fields = [[minUsdInput, "liq"], [minCvdInput, "cvd"], [minOiInput, "oi"]];
        fields.forEach(([el, kind]) => {
            const v = thresholdOf(kind);
            if (el) {
                el.value = v > 0 ? String(v) : "";
                // подсветим поле той ленты, что открыта: пресеты и Enter
                // работают именно с ней
                el.classList.toggle("filter-active", state.feedTab === kind);
            }
        });
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

    function setThreshold(kind, v, closePanel) {
        const cfg = THRESHOLDS[kind] || THRESHOLDS.liq;
        state[cfg.key] = Math.max(0, parseFloat(v) || 0);
        saveThreshold(kind);
        refreshFilterButtons();
        applyFiltersFull();
        if (closePanel && minUsdPanel) minUsdPanel.classList.add("hidden");
    }

    function setMinUsd(v, closePanel) {          // совместимость со старыми вызовами
        setThreshold("liq", v, closePanel);
    }

    /** OK в панели: применяем все три поля разом — ликвидации, CVD и OI Δ. */
    function applyThresholdInputs(closePanel) {
        [[minUsdInput, "liq"], [minCvdInput, "cvd"], [minOiInput, "oi"]].forEach(([el, kind]) => {
            if (el) {
                const cfg = THRESHOLDS[kind];
                state[cfg.key] = Math.max(0, parseFloat(el.value) || 0);
            }
        });
        ["liq", "cvd", "oi"].forEach(saveThreshold);
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
            minUsdApply.addEventListener("click", () => applyThresholdInputs(true));
        }
        // У каждого порога своё поле: Enter применяет именно его.
        [[minUsdInput, "liq"], [minCvdInput, "cvd"], [minOiInput, "oi"]].forEach(([el, kind]) => {
            if (!el) return;
            el.addEventListener("keydown", (e) => {
                if (e.key === "Enter") setThreshold(kind, el.value, true);
            });
        });
        // Пресеты — для той ленты, что открыта: на вкладке CVD $25K станет
        // порогом CVD, на вкладке ликвидаций — порогом ликвидаций.
        if (minUsdPresets) {
            Array.prototype.forEach.call(minUsdPresets.querySelectorAll("button"), (b) => {
                b.addEventListener("click", () => setThreshold(state.feedTab, b.dataset.v, true));
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

    // Партнёрская ссылка Gate (ru — русская страница, остальным — английская)
    const GATE_REFS = {
        ru: "https://www.gate.com/ru/signup/VLFCAVWMBW?ref_type=103",
        en: "https://www.gate.com/signup/VLFCAVWMBW?ref_type=103",
    };
    function gateRefUrl() { return GATE_REFS[I18n.lang()] || GATE_REFS.en; }

    // Примечание источника из /api/health (например, «биржа закрывается»):
    // сервер может отдать его в note — показываем прямо в фильтре.
    function sourceNote(name) {
        const k = String(name || "").toLowerCase();
        const s = state.lastHealth && state.lastHealth.sources && state.lastHealth.sources[k];
        return (s && (s.note || s.sunset_note)) || "";
    }

    function renderHealth(health) {
        if (!health || !health.sources) return;
        state.lastHealth = health;
        state.demo = !!health.demo;
        demoBadge.classList.toggle("hidden", !state.demo);
        const parts = [];
        let okN = 0, total = 0;
        Object.keys(health.sources).forEach((name) => {
            const s = health.sources[name];
            if (!s.enabled) return;
            // oxa — это транспорт для ликвидаций Hyperliquid, а не отдельная
            // площадка: её события приходят с биржей hyperliquid, и чип
            // Hyperliquid уже есть. Вторая плашка дублировала бы его.
            if (name === "oxa") return;
            const cls = s.connected ? "ok" : "bad";
            if (s.connected) okN += 1;
            total += 1;
            let label = name;
            if (name.indexOf("prices") === 0) label = I18n.t("health.prices");
            else if (name.indexOf("ticks") === 0) label = I18n.t("health.ticks");
            let title = s.connected
                ? I18n.t("health.connected", { n: s.events })
                : I18n.t("health.noconn", { err: s.last_error || "—" });
            const note = sourceNote(name);
            if (note) title += " · " + note;
            parts.push('<span class="exch-chip ' + cls + '" title="' +
                title.replace(/"/g, "&quot;") + '"><span>' + label + "</span>" +
                (s.connected ? "" : '<span>✕</span>') + "</span>");
        });
        // Партнёрская строка — в том же списке, что и биржи
        parts.push('<a class="exch-ref" href="' + gateRefUrl() +
            '" target="_blank" rel="noopener sponsored">' +
            I18n.t("health.gate_ref") + "</a>");
        const dot = (okN === total && total) ? "ok" : (okN ? "mixed" : "bad");
        const wasOpen = exchHealthEl.classList.contains("open");
        exchHealthEl.innerHTML =
            '<button type="button" class="exch-health-btn" id="exch-health-btn">' +
                '<span class="exch-health-dot ' + dot + '"></span>' +
                '<span class="exch-health-sum">' +
                    I18n.t("health.summary", { ok: okN, n: total }) + "</span>" +
                '<span class="exch-health-caret">▾</span>' +
            "</button>" +
            '<div class="exch-health-pop" id="exch-health-pop">' + parts.join("") + "</div>";
        if (wasOpen) exchHealthEl.classList.add("open");
    }

    function setupExchHealth() {
        if (!exchHealthEl) return;
        exchHealthEl.addEventListener("click", (e) => {
            const btn = e.target.closest ? e.target.closest(".exch-health-btn") : null;
            if (!btn) return;
            e.stopPropagation();
            exchHealthEl.classList.toggle("open");
        });
        document.addEventListener("click", (e) => {
            if (exchHealthEl.contains(e.target)) return;
            exchHealthEl.classList.remove("open");
        });
    }

    // --- Статистика ----------------------------------------------------------
    // Группа лидеров: подпись и карточки монет. data-symbol на карточке —
    // чтобы клик открывал монету так же, как в одиночном списке.
    function topCoinGroup(title, coins, valueOf) {
        if (!coins || !coins.length) return "";
        return '<div class="top-coins-group">' +
            '<div class="top-coins-group-title">' + title + "</div>" +
            coins.map((c) =>
                '<div class="top-coin-card" data-symbol="' + c.symbol + '"' +
                ' title="' + pretty(c.symbol) + " · $" + fmtUsdShort(c.usd) + " · " +
                I18n.t("top.count", { n: I18n.number(c.count || 0) }) + '">' +
                '<div class="top-coin-name">' + pretty(c.symbol) + "</div>" +
                '<div class="top-coin-val">' + valueOf(c) + "</div></div>").join("") +
            "</div>";
    }

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
            // Два списка лидеров: по деньгам и по числу событий. Одна монета
            // может гореть одной крупной ликвидацией, другая — сотней мелких,
            // поэтому вопросы «кто на кассе» и «кого рвало чаще» — разные.
            const byVol = data.top_coins.slice(0, 4);
            const byCnt = data.top_coins
                .filter((c) => Number(c.count) > 0)
                .sort((a, b) => Number(b.count) - Number(a.count))
                .slice(0, 4);
            topCoinsContainer.innerHTML =
                topCoinGroup(I18n.t("top.by_vol"), byVol, (c) => "$" + fmtUsdShort(c.usd)) +
                topCoinGroup(I18n.t("top.by_count"), byCnt,
                             (c) => I18n.t("top.count", { n: I18n.number(c.count) }));
            Array.prototype.forEach.call(
                topCoinsContainer.querySelectorAll(".top-coin-card"), (el) => {
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
    const TF_EXCHANGE = {
        1: { binance: "1m", bybit: "1" },
        5: { binance: "5m", bybit: "5" },
        15: { binance: "15m", bybit: "15" },
        60: { binance: "1h", bybit: "60" },
        240: { binance: "4h", bybit: "240" },
        1440: { binance: "1d", bybit: "D" },
    };

    function sameTf(a, b) {
        return Number(a) === Number(b);
    }

    function medianStep(candles) {
        if (!candles || candles.length < 4) return 0;
        const steps = [];
        for (let i = 1; i < candles.length; i++) {
            const dt = Number(candles[i].time) - Number(candles[i - 1].time);
            if (dt > 0) steps.push(dt);
        }
        if (!steps.length) return 0;
        steps.sort((a, b) => a - b);
        return steps[Math.floor(steps.length / 2)];
    }

    function candlesMatchTf(candles, tfMin) {
        const step = medianStep(candles);
        if (!step) return true;   // мало баров — шаг не на чем мерить
        const want = Number(tfMin) * 60;
        return step >= want * 0.45 && step <= want * 2.5;
    }

    async function fetchPublicKlines(symbol, tfMin) {
        const spec = TF_EXCHANGE[tfMin];
        if (!spec) return null;
        const pair = String(symbol || "").replace("_", "");
        if (!pair) return null;
        try {
            const url = "https://fapi.binance.com/fapi/v1/klines?symbol=" +
                encodeURIComponent(pair) + "&interval=" + spec.binance + "&limit=300";
            const r = await fetch(url);
            const rows = await r.json();
            if (Array.isArray(rows) && rows.length) {
                return rows.map((row) => ({
                    time: Math.floor(Number(row[0]) / 1000),
                    open: Number(row[1]), high: Number(row[2]),
                    low: Number(row[3]), close: Number(row[4]),
                    volume: Number(row[7]),
                    cvd: (row[10] != null && Number(row[7]) > 0)
                        ? (2 * Number(row[10]) - Number(row[7])) : null,
                }));
            }
        } catch (e) { /* CORS / сеть — пробуем Bybit */ }
        try {
            const url = "https://api.bybit.com/v5/market/kline?category=linear&symbol=" +
                encodeURIComponent(pair) + "&interval=" + spec.bybit + "&limit=300";
            const r = await fetch(url);
            const data = await r.json();
            const rows = (data && data.result && data.result.list) || [];
            if (!rows.length) return null;
            const out = rows.map((row) => ({
                time: Math.floor(Number(row[0]) / 1000),
                open: Number(row[1]), high: Number(row[2]),
                low: Number(row[3]), close: Number(row[4]),
                volume: Number(row[6]),
            }));
            out.sort((a, b) => a.time - b.time);
            return out;
        } catch (e) {
            return null;
        }
    }

    let candlesReqId = 0;
    async function loadCandles() {
        fetchOI();   // табло OI — за монетой графика
        const req = ++candlesReqId;
        const want = Number(state.timeframe);
        let applied = false;
        let gotWrong = false;
        try {
            const r = await fetch("/api/klines?symbol=" + encodeURIComponent(chartSymbol()) +
                "&timeframe=" + want);
            const data = await r.json();
            if (req !== candlesReqId) return;
            if (data && data.candles && data.candles.length) {
                const gotTf = data.timeframe != null ? Number(data.timeframe) : NaN;
                const tfOk = !Number.isFinite(gotTf) || gotTf === want;
                if (tfOk && candlesMatchTf(data.candles, want)) {
                    setCandles(data.candles, data.source);
                    applied = true;
                } else {
                    gotWrong = true;
                }
            }
        } catch (e) {
            console.error("klines:", e);
        }
        if (!applied && gotWrong) {
            try {
                const ext = await fetchPublicKlines(chartSymbol(), want);
                if (req !== candlesReqId) return;
                if (ext && ext.length && candlesMatchTf(ext, want)) {
                    setCandles(ext, "exchange");
                    applied = true;
                }
            } catch (e) { /* ignore */ }
        }
        if (!applied && gotWrong) {
            console.warn("klines: сервер отдал другой ТФ, дневные свечи не подставились");
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
            feed: state.feedTab,           // лента: liq | cvd | oi
            min_usd: 0,
            exchange: "ALL",               // фильтр бирж применяется на клиенте
        }));
    }

    /** Сказать серверу, какая лента открыта: в режиме «ВСЕ» для CVD/OI он
     *  начинает гнать минутные потоки по всем монетам, а не по графику. */
    function sendFeedConfig() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ action: "feed", feed: state.feedTab }));
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
                if (state.symbol !== "ALL" && state.symbols.indexOf(state.symbol) === -1 &&
                        !(state.liquidations || []).some((x) => x.symbol === state.symbol)) {
                    // монеты нет в списке сервера и событий по ней тоже нет —
                    // только тогда возвращаем ленту ко всем монетам
                    state.symbol = "ALL";
                }
                if (!state.chartSymbol) {
                    let saved = "";
                    try { saved = localStorage.getItem("liqscope.chartSymbol") || ""; } catch (e) { /* ignore */ }
                    // сохранённая пара может быть пользовательской — держим её,
                    // даже если она не попала в текущий топ (сервер найдёт свечи)
                    state.chartSymbol = (saved && /^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$/.test(saved))
                        ? saved
                        : (state.symbol !== "ALL" ? state.symbol : (state.symbols[0] || ""));
                }
                updateSymbolTitle();
                renderStats(msg.stats);
                if (msg.flow) state.flowAll = msg.flow;
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
                if (msg.symbol === chartSymbol() && sameTf(msg.tf, state.timeframe)) {
                    updateCandle(msg.candle);
                    if (msg.type === "tick") noteTick(msg.ts);
                }
                break;
            }
            case "candles": {
                if (msg.symbol === chartSymbol() && sameTf(msg.tf, state.timeframe)
                    && candlesMatchTf(msg.candles, state.timeframe)) {
                    setCandles(msg.candles, msg.source);
                }
                break;
            }
            case "flow_all": {
                state.flowAll = msg;
                // лента CVD/OI в режиме «ВСЕ» живёт этими строками: тик
                // обновляет ячейки на месте, без перерисовки DOM
                if (isFlowFeed(state.feedTab)) syncShapeFeed(state.feedTab);
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

    /** Стереть ленту и метки на графике. История при этом не теряется: она
     *  лежит на сервере и в локальном кэше, поэтому её можно вернуть кнопкой
     *  «↩️ Возобновить историю» в выпадающем списке рядом с «Очистить». */
    function clearChart() {
        state.liquidations = [];
        historyLoaded.clear();   // при след. выборе пары история подтянется заново
        feedTbody.innerHTML = "";
        feedCountEl.textContent = feedCountLabel(0);
        feedEmptyEl.classList.remove("hidden");
        applyMarkers([]);
        queueRedraw();
    }

    /** Вернуть всё, что было до очистки: события снова забираются с сервера
     *  (плюс локальный кэш), поэтому на графике появляется та же история. */
    function restoreChart() {
        const sym = chartSymbol();
        historyLoaded.clear();
        setClearMenuOpen(false);
        // что уже лежит рядом — показываем сразу, не дожидаясь сети
        Promise.all([loadCachedLiquidations(sym, MAX_HISTORY),
                     state.symbol !== "ALL" && state.symbol !== sym
                         ? loadCachedLiquidations(state.symbol, MAX_HISTORY)
                         : Promise.resolve([])]).then(([a, b]) => {
            mergeLiquidations(a);
            mergeLiquidations(b);
            rebuildFeed();
            updateMarkers();
            updateLiveStats();
            queueRedraw();
        }).catch(() => { /* кэш может быть недоступен — сервер всё равно ответит */ });
        // сервер — источник правды: тот же путь, что и при выборе пары
        loadHistoryFor(sym, true);
        if (state.symbol !== "ALL" && state.symbol !== sym) {
            loadHistoryFor(state.symbol, true);
        }
    }

    function setClearMenuOpen(open) {
        if (!clearMenu) return;
        // подсказка: что именно сделает «Возобновить историю» (с учётом языка)
        const note = $("clear-undo-note");
        if (note) note.textContent = I18n.t("clear.undone");
        clearMenu.classList.toggle("hidden", !open);
        if (clearMenuBtn) clearMenuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function runClearAction(act) {
        if (act === "restore") restoreChart();
        else clearChart();
        setClearMenuOpen(false);
    }

    clearBtn.addEventListener("click", () => {
        clearChart();
        setClearMenuOpen(false);
    });
    if (clearMenuBtn) {
        clearMenuBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            setClearMenuOpen(clearMenu ? clearMenu.classList.contains("hidden") : false);
        });
    }
    if (clearMenu) {
        // список не закрывается от клика по самому себе
        clearMenu.addEventListener("click", (e) => e.stopPropagation());
        clearMenu.addEventListener("click", (e) => {
            const btn = e.target && e.target.closest ? e.target.closest("[data-clear-act]") : null;
            if (btn) runClearAction(btn.dataset.clearAct);
        });
        document.addEventListener("click", () => setClearMenuOpen(false));
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") setClearMenuOpen(false);
        });
        setClearMenuOpen(false);      // подсказка заполнена ещё до раскрытия
    }
    window.LiqScopeClear = {      // тестовый API для tests/clear_restore.js
        clear: clearChart,
        restore: restoreChart,
        isOpen: () => !!(clearMenu && !clearMenu.classList.contains("hidden")),
        setOpen: setClearMenuOpen,
        count: () => state.liquidations.length,
    };

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
        // Слои по умолчанию выключены и доступны только зарегистрированным:
        // сначала узнаём, кто смотрит график, потом вешаем переключатели.
        applyAuthGate().then((allowed) => {
            setupLayerToggles(allowed);
            setupLayerPop();
            setupFollowToggle();     // 🎯 автоследование графика за ценой
            setupIndicatorPanes();   // окна LIQ/CVD/OI под графиком + крестики
            updateMarkers();
            queueRedraw();
        });
        setupFeedTabs();   // эфир: ликвидации / CVD / OI
        setupExchHealth(); // выпадающий список бирж в шапке
        setupChartToggle();
        setupChartExpand();
        setupDrawToolbar();   // панель рисования + тестовый API
        loadDrawings();       // фигуры текущей монеты
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

