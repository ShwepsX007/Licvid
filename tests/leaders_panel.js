require("./_dom_env");
/** Лидеры по ликвидациям: два списка — по объёму и по количеству.
 *
 *  Раньше панель «🔥 Лидеры по ликвидациям (24ч)» показывала один список и
 *  только деньги. Теперь внутри две группы: «💥 По объёму» и «🔢 По
 *  количеству» — одна монета горит одной крупной ликвидацией, другая сотней
 *  мелких, и вопросы «кто на кассе» и «кого рвало чаще» — разные.
 *
 *  Запуск (сервер уже на 127.0.0.1:8011):
 *      npm install --no-save jsdom ws
 *      node tests/leaders_panel.js http://127.0.0.1:8011
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8011";
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
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

// Монеты подобраны так, что порядки не совпадают: по деньгам впереди BTC,
// а по числу событий — ETH (одна крупная ликвидация против сотни мелких).
const TOP_COINS = [
  { symbol: "BTC_USDT", usd: 61_970_000, count: 1100 },
  { symbol: "ETH_USDT", usd: 41_000_000, count: 12_146 },
  { symbol: "SOL_USDT", usd: 22_500_000, count: 8_900 },
  { symbol: "XRP_USDT", usd: 9_400_000, count: 5_400 },
  { symbol: "DOGE_USDT", usd: 7_100_000, count: 4_100 },
  { symbol: "ZEC_USDT", usd: 3_200_000, count: 150 },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(ru(URL_BASE + "/terminal"), {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true });
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
          if (sock.onopen) sock.onopen();
          if (sock.onmessage) {
            sock.onmessage({ data: JSON.stringify({
              type: "init", symbols: TOP_COINS.map((c) => c.symbol), custom_symbols: [],
              details: [], prices: {}, recent_liquidations: [],
              exchanges: ["BINANCE"], stats: {},
            }) });
          }
        }, 50);
        return sock;
      };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/stats") === 0) {
          body = { total_usd_24h: 145_000_000, longs_usd_24h: 90_000_000,
                   shorts_usd_24h: 55_000_000, total_usd_1h: 0, total_usd_5m: 0,
                   top_coins: TOP_COINS };
        } else if (u.indexOf("/api/klines") === 0) {
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles: [] };
        } else if (u.indexOf("/api/liquidations") === 0) {
          body = { liquidations: [], total: 0 };
        }
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await sleep(4000);
  const win = dom.window;
  const doc = win.document;
  const list = doc.getElementById("top-coins-list");
  const groups = list ? list.querySelectorAll(".top-coins-group") : [];
  const titles = Array.from(list ? list.querySelectorAll(".top-coins-group-title") : [])
    .map((el) => el.textContent.trim());
  const cardsOf = (i) => Array.from(groups[i] ? groups[i].querySelectorAll(".top-coin-card") : []);
  const valueOf = (el) => el.querySelector(".top-coin-val").textContent.trim();
  const nameOf = (el) => el.querySelector(".top-coin-name").textContent.trim();

  check("панель лидеров на месте", !!list);
  check("две группы лидеров", groups.length === 2, groups.length);
  check("заголовок группы объёма", titles[0] === "💥 По объёму", titles[0]);
  check("заголовок группы количества", titles[1] === "🔢 По количеству", titles[1]);

  const vol = cardsOf(0);
  check("по объёму — четыре монеты", vol.length === 4, vol.length);
  check("по объёму первый BTC/USDT",
    vol[0] && nameOf(vol[0]) === "BTC/USDT", vol[0] && nameOf(vol[0]));
  check("по объёму убывание",
    vol.length === 4 && nameOf(vol[0]) === "BTC/USDT" && nameOf(vol[1]) === "ETH/USDT" &&
    nameOf(vol[2]) === "SOL/USDT" && nameOf(vol[3]) === "XRP/USDT",
    vol.map(nameOf).join(","));
  check("по объёму суммы в $", vol.every((el) => /\$\d/.test(valueOf(el))),
    vol.map(valueOf).join(" | "));

  const cnt = cardsOf(1);
  check("по количеству — четыре монеты", cnt.length === 4, cnt.length);
  check("по количеству первый ETH/USDT (не BTC)",
    cnt[0] && nameOf(cnt[0]) === "ETH/USDT", cnt[0] && nameOf(cnt[0]));
  check("по количеству убывание",
    cnt.map(nameOf).join(",") === "ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT",
    cnt.map(nameOf).join(","));
  check("по количеству значения — счётчики", cnt.every((el) => /\d[\d\u00a0\u202f ]*\s*ликв\./.test(valueOf(el))),
    cnt.map(valueOf).join(" | "));
  check("счётчик ETH с разделителем тысяч",
    cnt[0] && valueOf(cnt[0]).replace(/[\u00a0\u202f ]/g, "").indexOf("12146") !== -1,
    cnt[0] && valueOf(cnt[0]));

  const first = cnt[1] || cnt[0];
  check("карточка знает обе цифры",
    !!first && /\$/.test(first.getAttribute("title") || "") &&
    /ликв\./.test(first.getAttribute("title") || ""),
    first && first.getAttribute("title"));

  // клик по карточке из группы количества выбирает монету так же, как раньше
  const target = cnt[1] || cnt[0];
  const wantSym = target && target.dataset.symbol;
  target.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  await sleep(300);
  const label = doc.getElementById("symbol-current-label");
  check("клик по карточке выбирает монету",
    !!label && label.textContent.toUpperCase().indexOf(String(wantSym).split("_")[0]) !== -1,
    wantSym + " -> " + (label && label.textContent));

  check("без ошибок в консоли", errors.length === 0, errors.slice(0, 3).join(" ; "));

  console.log("\n" + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.log("падение: " + (e && e.stack || e)); process.exit(2); });
