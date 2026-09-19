/**
 * 🌍 Карта посещений в админке: страны, источники и время на сайте.
 *
 * Проверяем готовую страницу `/admin` в jsdom: карточка геометрии рисует карту
 * мира контурами из `static/world-map.js`, точки гостей ложатся по той же
 * проекции, что и контуры, живые гости пульсируют, таблицы считают страны и
 * источники, а кнопки периода переключают окно. Сеть подменена заглушкой:
 * данные приходят из фикстуры, поэтому проверка не зависит от того, кто
 * заходил на демо-стенд.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     NODE_PATH=/tmp/smoke/node_modules node tests/geo_map.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const NOW = Math.floor(Date.now() / 1000);
const FIXTURE = {
  ok: true, period: "24h", periods: ["24h", "7d", "30d"], hours: 24, online_sec: 300,
  totals: { online: 2, visitors: 5, views: 26, countries: 3, avg_sec: 154,
            anon_views: 1, bots: 12, long_60: 2, total_sec: 770 },
  online: [
    { vid: "aa11bb", country: "FI", name: "Finland", lat: 65.48, lon: 25.77,
      sec: 240, views: 4, path: "/terminal", source: "google.com",
      source_kind: "search", online: true, first_ts: NOW - 240, last_ts: NOW,
      user: { id: 1, name: "Вася" } },
    { vid: "cc22dd", country: "DE", name: "Germany", lat: 51.11, lon: 10.42,
      sec: 65, views: 2, path: "/cabinet", source: "", source_kind: "direct",
      online: true, first_ts: NOW - 65, last_ts: NOW, user: null },
  ],
  points: [
    { vid: "aa11bb", country: "FI", name: "Finland", lat: 65.48, lon: 25.77,
      sec: 240, views: 4, path: "/terminal", source: "google.com",
      source_kind: "search", online: true },
    { vid: "cc22dd", country: "DE", name: "Germany", lat: 51.11, lon: 10.42,
      sec: 65, views: 2, path: "/cabinet", source: "", source_kind: "direct",
      online: true },
    { vid: "ee33ff", country: "US", name: "United States", lat: 39.83, lon: -98.58,
      sec: 30, views: 1, path: "/", source: "t.me", source_kind: "social",
      online: false },
  ],
  countries: [
    { country: "FI", name: "Finland", lat: 65.48, lon: 25.77, visitors: 3,
      views: 12, online: 1, avg_sec: 200, last: NOW },
    { country: "DE", name: "Germany", lat: 51.11, lon: 10.42, visitors: 1,
      views: 2, online: 1, avg_sec: 65, last: NOW - 60 },
    { country: "US", name: "United States", lat: 39.83, lon: -98.58, visitors: 1,
      views: 1, online: 0, avg_sec: 30, last: NOW - 900 },
    { country: "", name: "", lat: null, lon: null, visitors: 1, views: 1,
      online: 0, avg_sec: 0, last: NOW - 1200 },
  ],
  sources: [
    { source: "google.com", kind: "search", visitors: 2, views: 10, online: 1,
      last: NOW },
    { source: "", kind: "direct", visitors: 3, views: 16, online: 1,
      last: NOW - 60 },
  ],
  long: [
    { vid: "aa11bb", country: "FI", name: "Finland", lat: 65.48, lon: 25.77,
      sec: 240, views: 4, path: "/terminal", source: "google.com",
      source_kind: "search", online: true },
  ],
  paths: [{ path: "/terminal", n: 12 }, { path: "/", n: 8 }],
  geo: { edge: true, enabled: true, provider: "country.is", cache_days: 30 },
  now: NOW,
};

/** Страница админки с подменённой сетью. */
async function openAdmin(routes) {
  const reqs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented") !== -1) return;
    if (msg.indexOf("Could not load") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(URL_BASE + "/admin", {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true }); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      try { win.localStorage.clear(); } catch (e) {}
      win.WebSocket = require("ws");
      win.fetch = async (url, opts) => {
        const u = String(url);
        const method = ((opts || {}).method || "GET").toUpperCase();
        reqs.push({ url: u, method });
        const data = routes(u, method, opts);
        return { ok: true, status: 200, json: async () => data };
      };
    },
  });
  await wait(1800);
  return { win: dom.window, doc: dom.window.document, reqs };
}

const ADMIN = { id: 1, first_name: "Босс", is_admin: true, email: "boss@liqscope.online" };

function adminRoutes(geo) {
  return (u) => {
    if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN, verified: true };
    if (u.indexOf("/api/admin/geo") === 0) return geo;
    if (u.indexOf("/api/visit/ping") === 0) return { ok: true, counted: true };
    if (u.indexOf("/api/feedback") === 0) return { ok: true, unread: 0, messages: [] };
    return { ok: false };
  };
}

async function main() {
  console.log("🌍 карта посещений в админке");
  const page = await openAdmin(adminRoutes(FIXTURE));
  const { doc, win, reqs } = page;

  // --- карточка и карта ---------------------------------------------------
  check("карточка географии есть в админке", !!doc.getElementById("geo-card"));
  check("панель отрисована", !!doc.querySelector("#geo-panel .geo-body"));
  const land = doc.querySelectorAll("#geo-map .geo-land");
  check("контуры стран на карте (" + land.length + ")", land.length > 150, land.length);
  check("карта — SVG с вьюбоксом без Антарктиды",
        !!doc.getElementById("geo-map") &&
        doc.getElementById("geo-map").getAttribute("viewBox") === "0 0 1000 12 1000 398",
        doc.getElementById("geo-map") && doc.getElementById("geo-map").getAttribute("viewBox"));

  // --- точки: онлайн пульсируют, гости за период — мелкими точками --------
  const live = doc.querySelectorAll("#geo-map .geo-live-dot .geo-on");
  check("живые точки — по числу онлайн-гостей", live.length === 2, live.length);
  check("у живых точек есть пульсирующий ореол",
        doc.querySelectorAll("#geo-map .geo-pulse").length === live.length);
  const dots = doc.querySelectorAll("#geo-map .geo-dot");
  check("остальные гости за период — отдельными точками", dots.length === 1, dots.length);
  check("роботы и гости без страны точку не получают",
        doc.querySelectorAll("#geo-map .geo-bubble").length === 3,
        doc.querySelectorAll("#geo-map .geo-bubble").length);

  // страна FI: точка обязана лечь туда же, куда показывает формула проекции
  const bubble = doc.querySelector('#geo-map .geo-bubble[data-cc="FI"]');
  const ex = ((25.77 + 180) / 360) * 1000, ey = ((90 - 65.48) / 180) * 500;
  check("точка страны ложится по проекции карты",
        !!bubble && Math.abs(Number(bubble.getAttribute("cx")) - ex) < 1 &&
        Math.abs(Number(bubble.getAttribute("cy")) - ey) < 1,
        bubble && [bubble.getAttribute("cx"), bubble.getAttribute("cy"), ex.toFixed(1), ey.toFixed(1)].join(" "));
  check("подсказка круга: страна, число гостей и кто онлайн",
        !!bubble && /Финляндия/.test(bubble.innerHTML) &&
        /3 гостя/.test(bubble.innerHTML) && /сейчас на сайте/.test(bubble.innerHTML),
        bubble && bubble.innerHTML.slice(0, 160));
  const usDot = doc.querySelector("#geo-map .geo-dot");
  check("подсказка точки: страна (по-русски), время, страница и источник",
        !!usDot && /Соединенные Штаты/.test(usDot.innerHTML) &&
        /30 с/.test(usDot.innerHTML) && /t\.me/.test(usDot.innerHTML),
        usDot && usDot.innerHTML.slice(0, 200));
  check("в легенде есть живые точки, визиты и круги",
        doc.querySelectorAll(".geo-legend .geo-key").length >= 3,
        doc.querySelectorAll(".geo-legend .geo-key").length);
  check("видно, откуда берётся страна (заголовок CDN)",
        /CDN|заголовк/i.test(doc.querySelector(".geo-legend").textContent),
        doc.querySelector(".geo-legend").textContent.slice(0, 80));

  // --- сводка -------------------------------------------------------------
  const chips = Array.prototype.slice.call(doc.querySelectorAll(".geo-chip"));
  check("сводка: онлайн, гости, визиты, страны, среднее время", chips.length === 5, chips.length);
  check("в сводке двое онлайн", /(^|\D)2(\D|$)/.test(chips[0].querySelector("b").textContent),
        chips[0].textContent);
  check("число гостей склоняется по-русски (5 гостей)",
        chips[1].querySelector("span").textContent.trim() === "гостей",
        chips[1].textContent);
  check("среднее время показано минутами",
        /мин/.test(chips[4].querySelector("b").textContent),
        chips[4].textContent);

  // --- таблицы ------------------------------------------------------------
  const crows = doc.querySelectorAll("#geo-countries tbody tr");
  check("таблица стран: строки по странам", crows.length === 4, crows.length);
  check("Финляндия названа по-русски",
        /Финляндия/.test(crows[0].textContent), crows[0].textContent);
  check("в строке страны видно онлайн и визиты",
        /3/.test(crows[0].textContent) && /12/.test(crows[0].textContent),
        crows[0].textContent);
  check("гость без страны подписан, а не спрятан",
        doc.querySelector("#geo-countries tbody tr:last-child").textContent.indexOf("неизвестна") >= 0,
        doc.querySelector("#geo-countries tbody tr:last-child").textContent);
  const srows = doc.querySelectorAll("#geo-sources tbody tr");
  check("таблица источников: поиск и прямой заход", srows.length === 2, srows.length);
  check("источник-поисковик назван", /google\.com/.test(srows[0].textContent), srows[0].textContent);
  check("прямой заход переведён на язык страницы",
        /прямой заход/.test(srows[1].textContent), srows[1].textContent);
  check("у прямого захода в колонке источника прочерк, а не дубль вида",
        srows[1].querySelector("td").textContent.trim() === "—",
        srows[1].querySelector("td").textContent);
  const lrows = doc.querySelectorAll("#geo-live tbody tr");
  check("онлайн-таблица: строка на гостя", lrows.length === 2, lrows.length);
  check("у вошедшего гостя видно имя аккаунта",
        /Вася/.test(lrows[0].textContent), lrows[0].textContent);
  check("гость без аккаунта — «гость #…»",
        /гость/.test(lrows[1].textContent), lrows[1].textContent);
  check("долгие визиты и страницы рядом",
        doc.querySelectorAll("#geo-long tbody tr").length === 1 &&
        doc.querySelectorAll("#geo-paths tbody tr").length === 2);

  // --- период -------------------------------------------------------------
  const geoReqs = () => reqs.filter((r) => r.url.indexOf("/api/admin/geo") === 0);
  check("данные запрошены с периодом по умолчанию",
        geoReqs().length === 1 && geoReqs()[0].url.indexOf("period=24h") > 0,
        JSON.stringify(geoReqs()));
  const btn7 = doc.querySelector('[data-period="7d"]');
  check("кнопки периода: 24 часа, 7 дней, 30 дней",
        doc.querySelectorAll("#geo-periods button").length === 3 && !!btn7);
  check("текущий период подсвечен",
        doc.querySelector('[data-period="24h"]').classList.contains("on"));
  btn7.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  await wait(300);
  check("нажатие «7 дней» запрашивает другое окно",
        geoReqs().length === 2 && geoReqs()[1].url.indexOf("period=7d") > 0,
        JSON.stringify(geoReqs()));
  check("подсветка перешла на выбранный период",
        btn7.classList.contains("on") &&
        !doc.querySelector('[data-period="24h"]').classList.contains("on"));

  // --- смена языка --------------------------------------------------------
  check("смена языка есть в API", typeof win.LiqScopeI18n.set === "function");
  win.LiqScopeI18n.set("en");
  await wait(200);
  const headEn = doc.querySelector("#geo-countries thead").textContent;
  check("заголовки таблиц переключились на английский",
        /Country/.test(headEn) && !/Страна/.test(headEn), headEn);
  check("имя страны тоже английское",
        /Finland/.test(doc.querySelector("#geo-countries tbody tr").textContent),
        doc.querySelector("#geo-countries tbody tr").textContent);

  // --- интервал обновления ------------------------------------------------
  check("картина обновляется сама (20 секунд)",
        win.LiqScopeGeo && win.LiqScopeGeo.REFRESH_MS === 20000,
        win.LiqScopeGeo && win.LiqScopeGeo.REFRESH_MS);

  // --- пустая картина -----------------------------------------------------
  const empty = await openAdmin(adminRoutes({
    ok: true, period: "24h", periods: ["24h", "7d", "30d"], hours: 24, online_sec: 300,
    totals: { online: 0, visitors: 0, views: 0, countries: 0, avg_sec: 0 },
    online: [], points: [], countries: [], sources: [], long: [], paths: [],
    geo: { edge: false, enabled: true, provider: "country.is", cache_days: 30 },
    now: NOW,
  }));
  const edoc = empty.doc;
  check("пусто: на карте подсказка вместо точек",
        !!edoc.querySelector(".geo-map-empty") &&
        edoc.querySelectorAll(".geo-live-dot, .geo-dot, .geo-bubble").length === 0,
        edoc.querySelector(".geo-map-empty") && edoc.querySelector(".geo-map-empty").textContent);
  check("пусто: в таблицах объяснение, а не пустое место",
        /Гостей за период нет/.test(edoc.querySelector("#geo-countries").textContent) &&
        /Сейчас на сайте никого/.test(edoc.querySelector("#geo-live").textContent),
        edoc.querySelector("#geo-live").textContent);
  check("пусто: видно, что страна берётся у внешнего сервиса",
        /country\.is/.test(edoc.querySelector(".geo-legend").textContent),
        edoc.querySelector(".geo-legend").textContent);

  console.log("\nошибок в консоли: " + errors.length);
  errors.slice(0, 6).forEach((e) => console.log("   ! " + e));
  console.log("ИТОГ: " + ok + " ок, " + fail + " провал(ов)");
  process.exit(fail || errors.length ? 1 : 0);
}

main().catch((e) => { console.error("Тест упал:", e.stack || e.message); process.exit(1); });
