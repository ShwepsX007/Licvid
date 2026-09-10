/**
 * Смоук-тест интерфейса без браузера: поднимает страницу терминала в jsdom,
 * подключается к работающему серверу и проверяет, что всё отрисовалось —
 * график, кнопки монет, лента, статистика, индикатор тиков, переключатель
 * профиля ликвидаций.
 *
 * Отдельно проверяет сценарий «выбрал монету → нажал ВСЕ»: график обязан
 * остаться на выбранной монете, а лента — показать все монеты. Плюс лендинг:
 * на / должна быть посадочная страница со ссылкой в терминал.
 *
 * Запуск (сервер должен быть уже запущен на 127.0.0.1:8000):
 *     npm install --no-save jsdom ws
 *     node tests/ui_smoke.js [http://127.0.0.1:8000]
 */

const { JSDOM, VirtualConsole } = require("jsdom");
const http = require("http");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];

function httpGet(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => resolve({ status: res.statusCode, body }));
    }).on("error", reject);
  });
}

/** Canvas в jsdom не реализован — подсовываем заглушку 2d-контекста. */
function stubCanvas(win) {
  const noop = () => {};
  const ctx = new Proxy({}, {
    get(_t, prop) {
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") {
        return () => ({ data: new Uint8ClampedArray(4) });
      }
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
}

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 300)));
  vc.on("error", (...a) =>
    errors.push("console.error: " + a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 300)));

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      stubCanvas(win);
      // jsdom всегда отдаёт en-US; принудительно ru, как у большинства наших юзеров
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({
        matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {},
        dispatchEvent() { return false; },
      });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      // проверка регулируемых панелей: задаём сохранённую раскладку заранее
      try {
        win.localStorage.setItem("licvid.layout", JSON.stringify({ feedW: 520, coinsH: 220 }));
      } catch (e) { /* ignore */ }
      win.WebSocket = require("ws");
      win.fetchCalls = [];
      // В jsdom нет fetch — подсовываем заглушку REST (каталог с GRAM)
      win.fetch = async (url) => {
        const u = String(url);
        win.fetchCalls.push(u);
        let body = {};
        if (u.indexOf("/api/symbols/search") === 0) {
          body = {
            query: "GRAM",
            results: [{ symbol: "GRAM_USDT", base: "GRAM", price: 0.42,
                        volume24h: 1.2e8, change24h: 12.0,
                        exchanges: ["binance", "gate"], liq24h: 0 }],
            total: 1, source: "stub", index_size: 1,
          };
        } else if (u.indexOf("/api/symbols/add") === 0) {
          body = { added: true, found: true, symbol: "GRAM_USDT",
                   details: { symbol: "GRAM_USDT", base: "GRAM", price: 0.42,
                              volume24h: 1.2e8, change24h: 12.0,
                              exchanges: ["binance", "gate"], liq24h: 0 } };
        } else if (u.indexOf("/api/klines") === 0) {
          body = { symbol: "BTC_USDT", timeframe: 5, source: "demo", candles: [] };
        } else if (u.indexOf("/api/stats") === 0) {
          body = { total_usd_24h: 0, longs_usd_24h: 0, shorts_usd_24h: 0,
                   total_usd_1h: 0, total_usd_5m: 0, top_coins: [] };
        } else if (u.indexOf("/api/liquidations") === 0) {
          // терминал догружает историю пузырьков после F5 — отдаём стаб
          body = { liquidations: [], total: 0 };
        }
        return { ok: true, status: 200, json: async () => body };
      };
      win.AudioContext = function () {
        return {
          state: "running", resume() {}, currentTime: 0,
          createOscillator: () => ({ connect() {}, start() {}, stop() {}, frequency: { value: 0 }, type: "" }),
          createGain: () => ({ connect() {}, gain: { value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} } }),
          destination: {},
        };
      };
    },
  });

  const win = dom.window;
  const doc = win.document;
  await new Promise((r) => setTimeout(r, 6000));   // даём накопиться данным

  const q = (id) => doc.getElementById(id);
  const text = (id) => (q(id) ? q(id).textContent.trim() : null);

  // Сценарий: выпадающий список монет — открываем, выбираем монету, затем «ВСЕ»
  const symCurrent = q("symbol-current");
  const symDrop = q("symbol-dropdown");
  const symOpts = () => Array.from(doc.querySelectorAll("#symbol-dropdown .symbol-option"));
  const clickOpt = (el) => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  };
  // список отрисован при загрузке (скрыт); для «выбора» откроем дропдаун
  if (symCurrent && !symDrop.classList.contains("hidden")) {
    clickOpt(symCurrent);   // закрыть, чтобы проверить открытие заново
  }
  if (symCurrent) clickOpt(symCurrent);   // открыть
  const symDropdownShown = !!symDrop && !symDrop.classList.contains("hidden");
  const symOptsList = symOpts();
  const coinBtn = symOptsList[3];
  const coinName = coinBtn ? coinBtn.dataset.symbol.replace(/_/g, "/") : "";
  if (coinBtn) clickOpt(coinBtn);
  await new Promise((r) => setTimeout(r, 800));
  const titleAfterCoin = text("current-symbol-title");
  const symCurrentLabelAfterCoin = text("symbol-current-label");
  const symDropdownClosedAfterPick = !!symDrop && symDrop.classList.contains("hidden");
  // снова открыть и выбрать «ВСЕ» (первый пункт)
  if (symCurrent) clickOpt(symCurrent);
  const allOpt = symOpts()[0];
  if (allOpt) clickOpt(allOpt);
  await new Promise((r) => setTimeout(r, 800));
  const titleAfterAll = text("current-symbol-title");

  // --- Новая функциональность: поиск, фильтры, кликабельная монета -----------
  // 1) Поиск пары: вводим GRAM → появляется результат → добавляем на график
  const searchInput = q("symbol-search");
  if (searchInput) {
    searchInput.value = "GRAM";
    searchInput.dispatchEvent(new win.Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 700));
  }
  const searchPanelShown = !!q("symbol-search-results") &&
    !q("symbol-search-results").classList.contains("hidden");
  const searchHasGram = !!doc.querySelector(
    "#symbol-search-results .search-result[data-symbol='GRAM_USDT']");
  let titleAfterAdd = null;
  const addBtn = doc.querySelector(".search-result-add");
  if (addBtn) {
    addBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 500));
    titleAfterAdd = text("current-symbol-title");
  }
  const gramChip = symOpts().some((b) => b.textContent.indexOf("GRAM") !== -1);

  // 2) Мин. объём: ввод порога вручную
  const minUsdInput = q("min-usd-input");
  if (minUsdInput) {
    minUsdInput.value = "500000";
    const applyBtn = q("min-usd-apply");
    if (applyBtn) applyBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 150));
  }
  const minUsdLabel = text("min-usd-btn");

  // 3) Фильтр бирж: выключить все → лента пустая, включить все → снова непустая
  const exchNone = q("exch-none-check");
  const exchAll = q("exch-all-check");
  let feedRowsAfterNone = -1;
  if (exchNone && exchAll) {
    exchNone.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 300));
    feedRowsAfterNone = doc.querySelectorAll("#feed-tbody tr").length;
    exchAll.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 300));
  }
  const exchCheckboxCount = doc.querySelectorAll("#exch-list input[type=checkbox]").length;
  const exchLabel = text("exchange-btn");

  // 4) Кликабельная колонка «Монета»
  const firstCoinLink = doc.querySelector("#feed-tbody .coin-link");
  let titleAfterCoinClick = null;
  if (firstCoinLink) {
    const sym = decodeURIComponent(firstCoinLink.dataset.symbol);
    firstCoinLink.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 300));
    titleAfterCoinClick = text("current-symbol-title");
  }

  // 5) Переключатель профиля: есть кнопка, по клику меняет активное состояние
  const profileToggleBtn = q("profile-toggle");
  let profileToggleActive = null;
  if (profileToggleBtn) {
    profileToggleActive = profileToggleBtn.classList.contains("active");
    profileToggleBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 100));
  }
  const profileToggleAfter = profileToggleBtn
    ? profileToggleBtn.classList.contains("active") : null;

  // 6) Лендинг: на / посадочная страница со ссылкой в терминал
  let landing = null;
  try {
    landing = await httpGet(URL_BASE + "/");
  } catch (e) { /* ignore */ }
  const landingOk = !!landing && landing.status === 200 &&
    landing.body.indexOf("Открыть терминал") !== -1 &&
    landing.body.indexOf("/terminal") !== -1;
  const landingBrandOk = !!landing && landing.body.indexOf("LICVIDATION") !== -1;
  const landingLangOk = !!landing && landing.body.indexOf('id="lang-select"') !== -1;
  // Название в шапке терминала — ссылка на главную
  const brandLink = doc.querySelector(".brand-link");
  const brandLinkOk = !!brandLink && brandLink.getAttribute("href") === "/" &&
    brandLink.textContent.indexOf("LICVIDATION") !== -1;

  // 8) Регулируемые панели + привязка строк ленты к шарикам
  const splitX = q("split-feed-x");
  const splitY = q("split-coins-y");
  const hasSplitX = !!splitX;
  const hasSplitY = !!splitY;
  const layoutFeedW = win.document.documentElement.style.getPropertyValue("--feed-width").trim();
  const layoutCoinsH = win.document.documentElement.style.getPropertyValue("--coins-height").trim();
  // имитация перетаскивания разделителя (pointerdown → move на window → up)
  let layoutFeedAfterDrag = null;
  let layoutSaved = null;
  if (splitX) {
    // тянем разделитель ВПРАВО: лента прижата к правому краю, поэтому её
    // ширина должна УМЕНЬШИТЬСЯ (520 → 420), а не вырасти (инверсия №1)
    splitX.dispatchEvent(new win.MouseEvent("pointerdown", { bubbles: true, clientX: 520 }));
    win.dispatchEvent(new win.MouseEvent("pointermove", { bubbles: true, clientX: 620 }));
    win.dispatchEvent(new win.MouseEvent("pointerup", { bubbles: true, clientX: 620 }));
    layoutFeedAfterDrag = win.document.documentElement.style.getPropertyValue("--feed-width").trim();
    try {
      const raw = win.localStorage.getItem("licvid.layout");
      if (raw) layoutSaved = JSON.parse(raw).feedW;
    } catch (e) { /* ignore */ }
  }
  // топ-пары: вертикальный список + сворачивание заголовком
  const topCoinsBox = q("top-coins-box");
  const topCoinsToggle = q("top-coins-toggle");
  let topCoinsClosed = false;
  if (topCoinsToggle) {
    topCoinsToggle.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    topCoinsClosed = topCoinsBox.classList.contains("closed");
  }
  // строки ленты должны знать id своего события (для подсветки по шарику)
  const rowsHaveLiqId = !!doc.querySelector("#feed-tbody tr[data-liq-id]");

  // 7) Язык: по умолчанию ru (jsdom-navigator подменён на ru-RU),
  //    переключаем на EN и проверяем, что интерфейс перевёлся.
  const langSelect = q("lang-select");
  const langDefault = langSelect ? langSelect.value : null;
  let langEn = null;
  if (langSelect) {
    langSelect.value = "en";
    langSelect.dispatchEvent(new win.Event("change", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 500));
    langEn = {
      select: langSelect.value,
      htmlLang: win.document.documentElement.lang,
      feedTitle: doc.querySelector(".feed-title").textContent.trim(),
      metricLabel: doc.querySelector(".metric-label").textContent.trim(),
      title: win.document.title,
    };
  }

  const out = {
    errors,
    conn: text("conn-status"),
    symbolOptions: symOptsList.length,
    symDropdownShown,
    symCurrentLabelAfterCoin,
    symDropdownClosedAfterPick,
    symCurrentLabelNow: text("symbol-current-label"),
    pickedCoin: coinName,
    titleAfterCoin,
    titleAfterAll,
    feedRows: doc.querySelectorAll("#feed-tbody tr").length,
    feedCount: text("feed-count"),
    price: text("current-price"),
    stat24: text("stat-24h"),
    dataSource: text("data-source"),
    legend: doc.querySelector(".chart-legend").textContent.replace(/\s+/g, " ").trim(),
    health: text("exch-health"),
    hasProfileToggle: !!profileToggleBtn,
    profileToggleActive,
    profileToggleAfter,
    hasChartToggle: !!q("chart-toggle"),
    hasProfileSidebar: !!q("profile-sidebar"),
    chartCanvases: doc.querySelectorAll("#tv-chart-container canvas").length,
    hasSearchInput: !!searchInput,
    searchPanelShown,
    searchHasGram,
    titleAfterAdd,
    gramChip,
    hasMinUsdInput: !!minUsdInput,
    minUsdLabel,
    exchCheckboxCount,
    exchLabel,
    feedRowsAfterNone,
    coinLinkCount: doc.querySelectorAll("#feed-tbody .coin-link").length,
    titleAfterCoinClick,
    landingOk,
    terminalOk: landingBrandOk,
    brandLinkOk,
    langDefault,
    langEn,
    liqHistoryFetches: win.fetchCalls.filter((u) => u.indexOf("/api/liquidations") === 0).length,
    hasSplitX,
    hasSplitY,
    layoutFeedW,
    layoutCoinsH,
    layoutFeedAfterDrag,
    layoutSaved,
    rowsHaveLiqId,
    topCoinsBoxVertical: !!topCoinsBox,
    topCoinsToggleExists: !!topCoinsToggle,
    topCoinsIsList: !!doc.querySelector(".top-coins-list"),
    topCoinsCardsCount: doc.querySelectorAll(".top-coin-card").length,
    topCoinsClosed,
  };

  const normLabel = String(out.minUsdLabel || "").replace(/\s/g, " ");

  const ok =
    out.chartCanvases > 0 &&
    out.symbolOptions > 5 &&
    out.symDropdownShown &&                 // клик по триггеру открывает список
    out.symCurrentLabelAfterCoin.indexOf(out.pickedCoin) !== -1 && // видна выбранная монета
    out.symDropdownClosedAfterPick &&       // после выбора список закрывается
    out.hasProfileToggle &&
    out.profileToggleActive === true &&
    out.profileToggleAfter === false &&
    !out.hasProfileSidebar &&
    out.hasChartToggle &&
    !!out.titleAfterCoin &&
    out.titleAfterAll.indexOf(out.pickedCoin) === 0 &&      // график не ушёл на BTC
    out.titleAfterAll.indexOf("лента: все монеты") !== -1 &&
    out.hasSearchInput &&
    out.searchPanelShown &&
    out.searchHasGram &&
    !!out.titleAfterAdd &&
    out.titleAfterAdd.indexOf("GRAM/USDT") === 0 &&
    out.gramChip &&
    out.hasMinUsdInput &&
    normLabel.indexOf("500 000") !== -1 &&
    out.exchCheckboxCount >= 4 &&
    out.feedRowsAfterNone === 0 &&
    out.exchLabel.indexOf("Все биржи") === 0 &&
    out.coinLinkCount > 0 &&
    !!out.titleAfterCoinClick &&
    out.landingOk &&
    out.terminalOk &&
    out.brandLinkOk &&
    out.langDefault === "ru" &&
    out.langEn &&
    out.langEn.select === "en" &&
    out.langEn.htmlLang === "en-US" &&
    out.langEn.feedTitle.indexOf("LIVE LIQUIDATION FEED") !== -1 &&
    out.langEn.metricLabel.indexOf("Liquidations") !== -1 &&
    out.langEn.title.indexOf("Licvidation Terminal") !== -1 &&
    out.liqHistoryFetches > 0 &&   // после F5 клиент догружает историю пузырьков
    out.hasSplitX &&
    out.hasSplitY &&
    out.layoutFeedW === "520px" &&        // раскладка восстановлена из localStorage
    out.layoutCoinsH === "220px" &&
    out.layoutFeedAfterDrag === "420px" && // тянем вправо → лента сужается (без инверсии)
    out.layoutSaved === 420 &&            // и сохраняется
    out.rowsHaveLiqId &&                  // строки связаны с событиями шариков
    out.topCoinsBoxVertical &&
    out.topCoinsToggleExists &&
    out.topCoinsIsList &&
    out.topCoinsCardsCount > 0 &&
    out.topCoinsClosed;                   // клик по заголовку сворачивает список

  console.log(JSON.stringify(out, null, 2));
  console.log(ok ? "\nИТОГ: ок" : "\nИТОГ: ЕСТЬ ПРОБЛЕМЫ");
  dom.window.close();
  process.exit(ok ? 0 : 1);
}

main().catch((e) => {
  console.error("Тест упал:", String(e.message || e).slice(0, 300));
  process.exit(1);
});
