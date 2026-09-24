require("./_dom_env");
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
const { ru } = require("./_ru");
const http = require("http");

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
      online: true, first_ts: NOW - 65, last_ts: NOW, user: null,
      trial: { who: "v:cc22dd", left_sec: 900, hits: 3, expired: false,
               minutes: 30 } },
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
  dots_after: 0, dots_shown: 3, dots_hidden: 0,
  layers: { minutes: 30, locked: false, enabled: true },
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

  const dom = await JSDOM.fromURL(ru(URL_BASE + "/admin"), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true }); } catch (e) {}
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

        reqs.push({ url: u, method, body: (opts || {}).body || "" });
        const data = routes(u, method, opts);
        return { ok: true, status: 200, json: async () => data };
      };
    },
  });
  await wait(1800);
  return { win: dom.window, doc: dom.window.document, reqs };
}

const ADMIN = { id: 1, first_name: "Босс", is_admin: true, email: "boss@liqscope.online" };

const ALL_POINTS = FIXTURE.points.slice();

function adminRoutes(geo) {
  return (u, method) => {
    if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN, verified: true };
    if (u.indexOf("/api/admin/geo/dots/clear") === 0) {
      // как сервер: старые точки прячем, живые гости остаются на карте
      const all = geo.points || [];
      geo.points = all.filter((p) => p.online);
      geo.dots_after = NOW;
      geo.dots_shown = geo.points.length;
      geo.dots_hidden = all.length - geo.points.length;
      return { ok: true, dots_after: NOW, keep_sec: 300 };
    }
    if (u.indexOf("/api/admin/geo/dots/reset") === 0) {
      geo.points = ALL_POINTS.slice();
      geo.dots_after = 0; geo.dots_shown = 3; geo.dots_hidden = 0;
      return { ok: true, dots_after: 0 };
    }
    if (u.indexOf("/api/admin/geo") === 0) return geo;
    if (u.indexOf("/api/admin/layers/reset") === 0) return { ok: true, reset: 1 };
    if (u.indexOf("/api/visit/ping") === 0) return { ok: true, counted: true };
    if (u.indexOf("/api/feedback") === 0) return { ok: true, unread: 0, messages: [] };
    return { ok: false };
  };
}

/** Стили админки текстом: карта вписана в карточку именно правилами CSS. */
function fetchCss() {
  return new Promise((resolve) => {
    const u = new URL(URL_BASE + "/static/account.css");
    http.get({ hostname: u.hostname, port: u.port, path: u.pathname },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (c) => { body += c; });
        res.on("end", () => resolve(body));
      }).on("error", () => resolve(""));
  });
}

async function main() {
  console.log("🌍 карта посещений в админке");
  const page = await openAdmin(adminRoutes(FIXTURE));
  const { doc, win, reqs } = page;
  const styles = await fetchCss();

  // --- карточка и карта ---------------------------------------------------
  check("карточка географии есть в админке", !!doc.getElementById("geo-card"));
  check("панель отрисована", !!doc.querySelector("#geo-panel .geo-body"));
  const land = doc.querySelectorAll("#geo-map .geo-land");
  check("контуры стран на карте (" + land.length + ")", land.length > 150, land.length);
  // вьюбокс — РОВНО четыре числа: с шестью браузер его игнорирует, и карта
  // рисуется в натуральную величину, обрезанной по краям карточки
  const svg = doc.getElementById("geo-map");
  const vb = (svg && svg.getAttribute("viewBox") || "").trim().split(/\s+/);
  check("карта — SVG с вьюбоксом без Антарктиды",
        !!svg && vb.length === 4 && vb[0] === "0" && vb[1] === "12" &&
        vb[2] === "1000" && vb[3] === "398", vb.join(" "));
  check("карта не шире карточки: ширина задана в процентах",
        /width:\s*100%/.test(styles), "нет width:100% у .geo-map");
  check("высота карты идёт от её пропорций, а не от содержимого",
        /aspect-ratio:\s*1000\s*\/\s*398/.test(styles), "нет aspect-ratio у .geo-map");
  check("под картой сказано, что её можно тянуть и приближать",
        /тянуть|приближ/i.test(doc.querySelector(".geo-legend").textContent),
        doc.querySelector(".geo-legend").textContent.slice(-90));

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

  // возвращаем окно на сутки: дальше проверки ждут период по умолчанию
  doc.querySelector('[data-period="24h"]')
     .dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  await wait(60);

  // --- приближение и перетаскивание карты ---------------------------------
  // карта после каждой перерисовки — новый SVG, поэтому берём его заново
  const svgEl = () => doc.getElementById("geo-map");
  const vpEl = () => doc.querySelector("#geo-map .geo-viewport");
  const tf = () => (vpEl() && vpEl().getAttribute("transform")) || "";
  const centre = () => {
    const m = /translate\((-?[\d.]+) (-?[\d.]+)\)/.exec(tf());
    return m ? { x: Number(m[1]), y: Number(m[2]) } : null;
  };
  check("контуры и точки лежат в одном слое-окне (его и двигаем)",
        vpEl().querySelectorAll(".geo-land").length > 150 &&
        vpEl().querySelectorAll(".geo-dot, .geo-on").length >= 3,
        vpEl().querySelectorAll(".geo-land").length);
  const zbtn = (kind) => doc.querySelector('#geo-map-tools [data-zoom="' + kind + '"]');
  const zclick = (kind) => zbtn(kind).dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  check("у карты есть кнопки приблизить/отдалить/вписать",
        !!zbtn("in") && !!zbtn("out") && !!zbtn("fit"));
  check("на целой карте «отдалить» и «вписать» выключены",
        zbtn("out").disabled === true && zbtn("fit").disabled === true,
        zbtn("out").disabled + "/" + zbtn("fit").disabled);
  check("карта вписана целиком: масштаб 1 и сдвига нет",
        /scale\(1(\.0+)?\)/.test(tf()) && centre() && !centre().x && !centre().y, tf());

  zclick("in");
  check("кнопка ＋ приближает карту", /scale\(1\.5/.test(tf()), tf());
  check("после приближения «отдалить» включилась", zbtn("out").disabled === false);
  check("жесты уходят карте только при приближении",
        svgEl().style.touchAction === "none", svgEl().style.touchAction);

  // масштаб держит край карты: утащить её в пустоту нельзя
  const onDot = () => doc.querySelector("#geo-map .geo-on");
  const cxBefore = Number(onDot().getAttribute("cx"));
  const beforeDrag = centre();
  svgEl().dispatchEvent(new win.MouseEvent("mousedown",
    { bubbles: true, cancelable: true, clientX: 100, clientY: 100 }));
  win.dispatchEvent(new win.MouseEvent("mousemove",
    { bubbles: true, clientX: 130, clientY: 140 }));
  win.dispatchEvent(new win.MouseEvent("mouseup", { bubbles: true }));
  const afterDrag = centre();
  check("карту можно утащить мышью",
        !!beforeDrag && !!afterDrag && (afterDrag.x !== beforeDrag.x ||
                                        afterDrag.y !== beforeDrag.y),
        JSON.stringify([beforeDrag, afterDrag]));
  check("тянем вправо-вниз — карта едет вправо-вниз",
        afterDrag.x > beforeDrag.x && afterDrag.y > beforeDrag.y,
        JSON.stringify([beforeDrag, afterDrag]));
  check("сдвиг не сбивает точку с проекции (меняется только transform)",
        Number(onDot().getAttribute("cx")) === cxBefore, cxBefore);
  check("значки не растут вместе с картой (радиус поделён на масштаб)",
        Math.abs(Number(onDot().getAttribute("r")) - 3.2 / 1.5) < 0.05,
        onDot().getAttribute("r"));

  // у края карта упирается: дальше вести некуда
  svgEl().dispatchEvent(new win.MouseEvent("mousedown",
    { bubbles: true, cancelable: true, clientX: 100, clientY: 100 }));
  win.dispatchEvent(new win.MouseEvent("mousemove",
    { bubbles: true, clientX: 2000, clientY: 2000 }));
  win.dispatchEvent(new win.MouseEvent("mouseup", { bubbles: true }));
  check("карта упирается в свой край, пустоты за ней нет",
        centre().x <= 0.01 && centre().y <= 12.01,
        JSON.stringify(centre()));

  zclick("out");
  zclick("fit");
  check("кнопка ⤢ возвращает всю карту",
        /scale\(1(\.0+)?\)/.test(tf()) && centre() && !centre().x && !centre().y, tf());
  check("кнопка − отдаляет обратно до целой карты", zbtn("out").disabled === true);

  // колесо приближает карту к курсору
  svgEl().dispatchEvent(new win.WheelEvent("wheel",
    { bubbles: true, cancelable: true, deltaY: -120, clientX: 400, clientY: 200 }));
  check("колесо приближает карту", /scale\(1\.25/.test(tf()), tf());
  check("приближение к курсору сдвигает карту (точка под курсором на месте)",
        !!centre() && (centre().x !== 0 || centre().y !== 0), tf());

  // смена периода перерисовывает карту — вид при этом сохраняется
  const beforeRedraw = tf();
  doc.querySelector('[data-period="7d"]').dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  await wait(60);
  check("после смены периода карта перерисована, но вид сохранён",
        tf() === beforeRedraw, tf() + " ≠ " + beforeRedraw);
  // и обратно на сутки: дальше проверки ждут период по умолчанию
  zclick("fit");
  doc.querySelector('[data-period="24h"]').dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  await wait(60);

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

  // --- пробник слоёв у живого гостя ---------------------------------------
  win.LiqScopeI18n.set("ru");      // дальше проверяем русские подписи
  await wait(120);
  const trialHead = Array.prototype.map.call(
    doc.querySelectorAll("#geo-live thead th"),
    (th) => th.textContent).join(" ");
  check("в таблице «сейчас на сайте» есть столбец пробника слоёв",
        /Пробник слоёв/.test(trialHead), trialHead);
  const liveRows = doc.querySelectorAll("#geo-live tbody tr");
  const trialBtn = liveRows[1] && liveRows[1].querySelector("[data-trial]");
  check("гостю видно остаток пробника (15 мин из 30)",
        !!trialBtn && /15 мин/.test(liveRows[1].textContent),
        liveRows[1] && liveRows[1].textContent);
  check("у гостя без пробника — прочерк и нет кнопки",
        !!liveRows[0] && !liveRows[0].querySelector("[data-trial]") &&
        /—/.test(liveRows[0].textContent), liveRows[0] && liveRows[0].textContent);
  check("кнопка «дать ещё» знает, кому сбрасывать таймер",
        !!trialBtn && trialBtn.getAttribute("data-trial") === "v:cc22dd",
        trialBtn && trialBtn.getAttribute("data-trial"));
  if (trialBtn) {
    trialBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await wait(120);
  }
  const trialReq = reqs.filter((r) => r.url.indexOf("/api/admin/layers/reset") === 0).pop();
  check("сброс таймера уходит на сервер с ключом гостя",
        !!trialReq && trialReq.method === "POST" &&
        /"who":"v:cc22dd"/.test(String(trialReq.body || "")),
        trialReq && trialReq.method + " " + trialReq.body);

  // --- кнопка «убрать старые точки» ---------------------------------------
  const dotsBtn = doc.getElementById("geo-dots");
  check("кнопка уборки точек стоит рядом с картой",
        !!dotsBtn && /Убрать старые точки/.test(dotsBtn.textContent),
        dotsBtn && dotsBtn.textContent);
  check("пока точек не прятали — обещаем убрать, а не вернуть",
        !!dotsBtn && dotsBtn.getAttribute("data-dots") === "clear");
  const dotCount = () => doc.querySelectorAll(".geo-dot").length;
  const markCount = () => doc.querySelectorAll(".geo-dot, .geo-live-dot").length;
  check("до уборки на карте и старая точка, и живые гости",
        dotCount() === 1 && markCount() === 3,
        dotCount() + " точек, " + markCount() + " меток");
  if (dotsBtn) {
    dotsBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await wait(60);
  }
  check("первый клик только переспрашивает",
        !!dotsBtn && /Точно убрать/.test(dotsBtn.textContent) &&
        !reqs.some((r) => r.url.indexOf("dots/clear") === 0),
        dotsBtn && dotsBtn.textContent);
  if (dotsBtn) {
    dotsBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await wait(160);
  }
  const clearReq = reqs.filter((r) => r.url.indexOf("/api/admin/geo/dots/clear") === 0).pop();
  check("второй клик убирает старые точки и оставляет тех, кто онлайн",
        !!clearReq && clearReq.method === "POST" && /"keep_sec":300/.test(String(clearReq.body)),
        clearReq && clearReq.method + " " + clearReq.body);
  check("после уборки карточка говорит, сколько точек скрыто",
        /скрыто 1/.test((doc.getElementById("geo-dots-note") || {}).textContent || ""),
        (doc.getElementById("geo-dots-note") || {}).textContent);
  check("старая точка исчезла с карты, живые остались",
        dotCount() === 0 && markCount() === 2,
        dotCount() + " точек, " + markCount() + " меток");
  check("кнопка превращается в «вернуть точки»",
        !!dotsBtn && dotsBtn.getAttribute("data-dots") === "restore" &&
        /Вернуть точки/.test(dotsBtn.textContent), dotsBtn && dotsBtn.textContent);
  if (dotsBtn) {
    dotsBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await wait(160);
  }
  const resetReq = reqs.filter((r) => r.url.indexOf("/api/admin/geo/dots/reset") === 0).pop();
  check("возврат точек — один клик и без переспроса",
        !!resetReq && resetReq.method === "POST",
        resetReq && resetReq.method);
  check("точки вернулись на карту", dotCount() === 1 && markCount() === 3,
        dotCount() + " точек, " + markCount() + " меток");

  console.log("\nошибок в консоли: " + errors.length);
  errors.slice(0, 6).forEach((e) => console.log("   ! " + e));
  console.log("ИТОГ: " + ok + " ок, " + fail + " провал(ов)");
  process.exit(fail || errors.length ? 1 : 0);
}

main().catch((e) => { console.error("Тест упал:", e.stack || e.message); process.exit(1); });
