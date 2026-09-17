/** «Очистить» и «Возобновить историю» в терминале.
 *
 *  Раньше «🧹 Очистить» стирало ленту и метки без пути назад: промахнулся —
 *  и история пропала до перезагрузки страницы. Теперь рядом с кнопкой есть
 *  «▾» — выпадающий список из двух пунктов: «🧹 Очистить график» и
 *  «↩️ Возобновить историю». Возврат берёт историю заново с сервера (плюс
 *  локальный кэш), поэтому возвращается та же картина, что была до очистки.
 *
 *  Ещё проверяем, что подсказка «Ликв. лонгов … CVD ▼» уехала из шапки графика
 *  в плашку слоёв: в строке она занимала место, а в плашке идёт столбиком
 *  над кнопками.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/clear_restore.js
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
const candles = [
  { time: NOW - 600, open: 50000, high: 50100, low: 49900, close: 50050, volume: 10,
    cvd: 9000, oi: 5.2e8, oiChg: 3e6 },
  { time: NOW - 300, open: 50050, high: 50200, low: 50000, close: 50150, volume: 11,
    cvd: -7000, oi: 5.25e8, oiChg: -2e6 },
];
const liqs = [
  { id: "b1", symbol: "BTC_USDT", exchange: "BINANCE", side: "SELL",
    usd: 90000, price: 50050, timestamp: NOW - 30 },
  { id: "b2", symbol: "BTC_USDT", exchange: "BYBIT", side: "BUY",
    usd: 120000, price: 49900, timestamp: NOW - 90 },
  { id: "b3", symbol: "BTC_USDT", exchange: "BINANCE", side: "BUY",
    usd: 210000, price: 49950, timestamp: NOW - 150 },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));
  const restCalls = [];

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
      try {
        win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT");
        win.localStorage.setItem("liqscope.devLayers", "1");
        ["liqscope.liqEnabled", "liqscope.cvdEnabled", "liqscope.oiEnabled"].forEach((k) =>
          win.localStorage.setItem(k, "1"));
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.WebSocket = function () {
        const sock = { readyState: 1, onopen: null, onmessage: null,
                       onclose: null, onerror: null, send() {}, close() {} };
        setTimeout(() => {
          const init = {
            type: "init",
            symbols: ["BTC_USDT"],
            custom_symbols: [],
            details: [{ symbol: "BTC_USDT", volAvg7d: 1e9 }],
            prices: { BTC_USDT: 50050 },
            recent_liquidations: liqs,
            exchanges: ["BINANCE", "BYBIT"],
            stats: {},
            flow: { type: "flow_all", window_min: 5, ts: NOW, cvd: [], oi: [], liq: [],
                    summary: { window_min: 60 } },
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
        } else if (u.indexOf("/api/stats") === 0) body = {};
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) {
          // история «с сервера»: тот же набор, что пришёл в init — по ней и
          // восстанавливается очищенный график
          restCalls.push(u);
          body = { liquidations: liqs, total: liqs.length };
        }
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await sleep(7000);
  const win = dom.window;
  const doc = win.document;
  const $ = (id) => doc.getElementById(id);
  const q = (sel) => doc.querySelector(sel);
  const qa = (sel) => Array.prototype.slice.call(doc.querySelectorAll(sel));
  const click = (el) => el && el.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  const rows = () => qa("#feed-tbody tr, #feed-table tbody tr");
  const menuOpen = () => $("clear-menu") && !$("clear-menu").classList.contains("hidden");

  // --- подсказка уехала из шапки графика в плашку слоёв ---
  const legendItems = qa(".legend-item");
  check("подсказка больше не занимает строку шапки",
        qa(".chart-legend .legend-item").length === 0,
        qa(".chart-legend .legend-item").length + " пунктов в строке");
  check("пять пунктов подсказки живут в плашке слоёв",
        legendItems.length === 5 && legendItems.every((el) => el.closest("#layer-pop")),
        legendItems.length + " пунктов");
  check("подсказка идёт над кнопками слоёв",
        $("layer-pop").firstElementChild === $("layer-legend"),
        $("layer-pop") && $("layer-pop").firstElementChild.className);
  check("пункты подсказки — столбиком, а не в ряд",
        win.getComputedStyle($("layer-legend")).flexDirection === "column",
        win.getComputedStyle($("layer-legend")).flexDirection);
  check("расшифровка на месте по тексту",
        /Ликв\. лонгов/.test($("layer-legend").textContent) &&
        /CVD ▼/.test($("layer-legend").textContent),
        $("layer-legend").textContent.replace(/\s+/g, " ").trim().slice(0, 80));

  // --- меню очистки: два пункта и возврат истории ---
  check("кнопка «Очистить» на месте", !!$("clear-clusters-btn"));
  check("рядом кнопка выпадающего списка", !!$("clear-menu-btn"));
  check("список скрыт по умолчанию", !!$("clear-menu") && !menuOpen());
  check("в списке два пункта", qa("#clear-menu [data-clear-act]").length === 2,
        qa("#clear-menu [data-clear-act]").map((b) => b.dataset.clearAct).join(","));
  check("есть пункт «Очистить график»",
        !!q("#clear-menu [data-clear-act='clear']") &&
        /Очистить/.test(q("#clear-menu [data-clear-act='clear']").textContent));
  const undoItem = q("#clear-menu [data-clear-act='restore']");
  check("есть пункт «Возобновить историю»", !!undoItem && /Возобновить историю/.test(undoItem.textContent),
        undoItem && undoItem.textContent.trim());
  check("пункт возврата объясняет, что история вернётся",
        /история вернётся/i.test(undoItem ? undoItem.textContent : ""),
        undoItem && undoItem.textContent.replace(/\s+/g, " ").trim());

  click($("clear-menu-btn"));
  check("клик по «▾» раскрывает список", menuOpen() &&
        $("clear-menu-btn").getAttribute("aria-expanded") === "true",
        $("clear-menu-btn").getAttribute("aria-expanded"));

  // --- очистка ---
  const before = rows().length;
  check("до очистки история в ленте есть", before >= 3, before + " строк");
  click($("clear-clusters-btn"));
  await sleep(300);
  check("«Очистить» убирает ленту", rows().length === 0, rows().length + " строк");
  check("после очистки список закрыт", !menuOpen());
  check("подсказка о счётчике пуста", /^0|пусто/i.test($("feed-count").textContent),
        $("feed-count").textContent.trim().slice(0, 40));

  // --- возврат истории ---
  const restBefore = restCalls.length;
  click($("clear-menu-btn"));
  await sleep(150);
  click(q("#clear-menu [data-clear-act='restore']"));
  await sleep(1200);
  check("«Возобновить историю» вернула события в ленту", rows().length >= 3,
        rows().length + " строк");
  check("история снова запрошена у сервера", restCalls.length > restBefore,
        restCalls.length - restBefore + " запросов");
  check("после возврата список закрыт", !menuOpen());
  check("счётчик снова показывает события", !/^0\b/.test($("feed-count").textContent.trim()),
        $("feed-count").textContent.trim().slice(0, 40));

  // --- меню закрывается по Esc и по клику мимо ---
  click($("clear-menu-btn"));
  await sleep(100);
  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("Esc закрывает список", !menuOpen());
  click($("clear-menu-btn"));
  await sleep(100);
  doc.body.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  check("клик мимо закрывает список", !menuOpen());

  check("без ошибок в консоли", errors.length === 0, errors.slice(0, 2).join(" | "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  if (fail) process.exitCode = 1;
}

main().catch((e) => {
  console.error("тест упал:", (e && e.stack) || e);
  process.exitCode = 1;
});
