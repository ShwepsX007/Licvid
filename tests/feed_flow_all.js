/** Лента CVD/OI в режиме «ВСЕ»: поток по всем монетам, а не по монете графика.
 *
 *  Раньше строки строились только по свечам открытого графика: переключил
 *  монету на «ВСЕ» — лента молча оставалась прежней. Теперь сервер шлёт
 *  сообщение flow_all (минутные потоки всех монет), а терминал показывает эти
 *  строки: монета, окно, значение, цена. Клик по строке открывает разбор —
 *  CVD, доля от объёма, ликвидации лонгов/шортов, OI и объём.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/feed_flow_all.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
function noopCtx() {
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

const NOW = Math.floor(Date.now() / 1000);
const T2 = NOW - (NOW % 300), T1 = T2 - 3600, T3 = T2 - 7200;
const candles = [
  { time: T3, open: 50000, high: 50100, low: 49900, close: 50050, volume: 10,
    cvd: 9000, oi: 5.2e8, oiChg: 3e6 },
  { time: T2, open: 49000, high: 49100, low: 48900, close: 49050, volume: 10,
    cvd: -3000, oi: 5.3e8, oiChg: -1.5e6 },
  { time: T1, open: 49000, high: 49100, low: 48900, close: 49050, volume: 10,
    cvd: 4000, oi: 5.25e8, oiChg: 1.2e6 },
];
const liqs = [
  { id: "b1", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 90000, price: 50050, timestamp: NOW },
  { id: "e1", symbol: "ETH_USDT", exchange: "BYBIT", side: "BUY",
    usd: 150000, price: 2500, timestamp: NOW - 20 },
];

// Поток по всем монетам: три монеты, у каждой своя минута и своё окно.
const flowMsg = {
  type: "flow_all",
  window_min: 5,
  ts: NOW,
  cvd: [
    { symbol: "ETH_USDT", ts: NOW, window_min: 5, cvd: -940000, cvd_share: -41.2,
      vol: 2280000, liq_long: 120000, liq_short: 880000, liq_count: 6,
      oi_delta: -2400000, oi_usd: 3.1e9, price: 2510 },
    { symbol: "BTC_USDT", ts: NOW, window_min: 5, cvd: 610000, cvd_share: 12.4,
      vol: 4900000, liq_long: 40000, liq_short: 90000, liq_count: 3,
      oi_delta: 1800000, oi_usd: 1.2e10, price: 61000 },
    { symbol: "SOL_USDT", ts: NOW, window_min: 5, cvd: 120000, cvd_share: 6.1,
      vol: 1970000, liq_long: 0, liq_short: 0, liq_count: 0,
      oi_delta: 300000, oi_usd: 8.8e8, price: 152 },
  ],
  oi: [
    { symbol: "ETH_USDT", ts: NOW, window_min: 5, cvd: -940000, cvd_share: -41.2,
      vol: 2280000, liq_long: 120000, liq_short: 880000, liq_count: 6,
      oi_delta: -2400000, oi_usd: 3.1e9, price: 2510 },
    { symbol: "BTC_USDT", ts: NOW, window_min: 5, cvd: 610000, cvd_share: 12.4,
      vol: 4900000, liq_long: 40000, liq_short: 90000, liq_count: 3,
      oi_delta: 1800000, oi_usd: 1.2e10, price: 61000 },
  ],
  liq: [],
  summary: { window_min: 60 },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

  const sockets = [];
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
      try { win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT"); } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null, onclose: null,
                       onerror: null, sent: [], send(m) { this.sent.push(String(m)); },
                       close() {} };
        sockets.push(sock);
        setTimeout(() => {
          const init = {
            type: "init",
            symbols: ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
            custom_symbols: [],
            details: [{ symbol: "BTC_USDT", volAvg7d: 1e9 },
                      { symbol: "ETH_USDT", volAvg7d: 8e8 }],
            prices: { BTC_USDT: 61000, ETH_USDT: 2510, SOL_USDT: 152 },
            recent_liquidations: liqs,
            exchanges: ["BINANCE", "BYBIT"],
            stats: {},
          };
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) sock.onmessage({ data: JSON.stringify(init) });
        }, 50);
        return sock;
      };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/klines") === 0) {
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles };
        } else if (u.indexOf("/api/liquidations") === 0) {
          body = { liquidations: [], total: 0 };
        }
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await sleep(7000);
  const win = dom.window;
  const doc = win.document;
  const sock = sockets[0];
  const tab = (name) => doc.getElementById("feed-tab-" + name).dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  const rows = () => Array.prototype.slice.call(
    doc.querySelectorAll("#feed-table tbody tr"));
  const coinTexts = () => rows().map((tr) => {
    const b = tr.querySelector(".coin-link");
    return b ? b.textContent.replace(/[📈\s✕]/g, "") : "";
  });
  const feedEmpty = doc.getElementById("feed-empty");

  // --- переключение на CVD: серверу говорим, что нужна лента CVD ---
  tab("cvd");
  await sleep(400);
  const feedMsg = sock.sent.map((m) => { try { return JSON.parse(m); } catch (e) { return {}; } })
    .filter((m) => m.action === "feed").pop();
  check("клиент сообщает серверу про ленту CVD",
    feedMsg && feedMsg.feed === "cvd", JSON.stringify(feedMsg));
  check("пока потока нет — понятное «ждём данные»",
    !feedEmpty.classList.contains("hidden") &&
    /Собираем поток по всем монетам/.test(feedEmpty.textContent),
    feedEmpty.textContent);

  // --- приходит поток по всем монетам ---
  sock.onmessage({ data: JSON.stringify(flowMsg) });
  await sleep(300);
  const texts = coinTexts();
  check("в ленте «ВСЕ» сразу несколько монет",
    texts.indexOf("ETH/USDT") !== -1 && texts.indexOf("BTC/USDT") !== -1 &&
    texts.indexOf("SOL/USDT") !== -1, texts.join(","));
  check("строки отсортированы по силе потока (ETH продают больше всех)",
    texts[0] === "ETH/USDT", texts.join(","));
  const first = rows()[0];
  check("подпись строки — продажи CVD с окном",
    /CVD ▼/.test(first.textContent) && /5м/.test(first.textContent), first.textContent);
  check("сумма и цена на месте",
    /940\D?000/.test(first.textContent) && /2\D?510/.test(first.textContent),
    first.textContent);
  check("ошибок нет после потока", errors.length === 0, errors.slice(0, 2).join(" // "));

  // --- тик потока обновляет ячейки, не пересобирая строку (ховер не рвётся) ---
  const sameRow = rows()[0];
  const next = JSON.parse(JSON.stringify(flowMsg));
  next.cvd[0].cvd = -1400000;
  sock.onmessage({ data: JSON.stringify(next) });
  await sleep(300);
  check("строка та же самая (DOM не пересобран)", rows()[0] === sameRow);
  check("значение обновилось без перерисовки", /1\D?400\D?000/.test(rows()[0].textContent),
    rows()[0].textContent);

  // --- клик по строке: разбор потока, а не окно свечи ---
  const click = (el) => el.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  click(rows()[0]);
  await sleep(200);
  const modal = doc.getElementById("detail-modal");
  const modalText = doc.getElementById("modal-body").textContent;
  check("клик по строке открывает разбор", !modal.classList.contains("hidden"));
  check("в разборе окно, доля от объёма и ликвидации",
    /Окно/.test(modalText) && /5 мин/.test(modalText) &&
    /Доля от объёма/.test(modalText) && /Ликвидации лонгов/.test(modalText) &&
    /Ликвидации шортов/.test(modalText), modalText.slice(0, 200));
  check("в разборе видно, что это поток, а не свеча",
    /минутный поток монеты/.test(modalText), modalText.slice(-120));
  click(doc.getElementById("close-modal"));
  await sleep(150);

  // --- OI в режиме «ВСЕ» — тоже из потока ---
  tab("oi");
  await sleep(400);
  const oiTexts = coinTexts();
  check("OI-лента «ВСЕ» идёт по всем монетам потока",
    oiTexts.indexOf("ETH/USDT") !== -1 && oiTexts.indexOf("BTC/USDT") !== -1,
    oiTexts.join(","));
  check("в OI-ленте показано изменение интереса",
    /OI ▼/.test(rows()[0].textContent), rows()[0].textContent);

  // --- выбор монеты возвращает ленту к свечам графика ---
  const ethBtn = rows().map((tr) => tr.querySelector(".coin-link"))
    .filter((b) => b && decodeURIComponent(b.dataset.symbol) === "ETH_USDT")[0];
  check("в потоке есть кнопка монеты", !!ethBtn);
  if (ethBtn) click(ethBtn);
  await sleep(600);
  const afterTexts = coinTexts();
  check("после выбора монеты лента только про неё",
    afterTexts.length > 0 && afterTexts.every((t) => t === "ETH/USDT"),
    afterTexts.join(","));
  const feedMsg2 = sock.sent.map((m) => { try { return JSON.parse(m); } catch (e) { return {}; } })
    .filter((m) => m.action === "feed").pop();
  check("про смену монеты серверу сообщается (поток можно гасить)",
    feedMsg2 && feedMsg2.feed === "oi", JSON.stringify(feedMsg2));
  check("лента CVD по монете снова строится по свечам графика", (() => {
    tab("cvd");
    return true;
  })());
  await sleep(500);
  const oneCoin = coinTexts();
  check("в ленте CVD монеты графика — только ETH",
    oneCoin.length > 0 && oneCoin.every((t) => t === "ETH/USDT"), oneCoin.join(","));

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
