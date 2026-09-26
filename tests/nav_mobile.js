require("./_dom_env");
/**
 * Шапка на телефоне: с любой страницы виден каждый раздел.
 *
 * Главная держит ссылки разделов в разметке как `nav-desk` — CSS прячет их на
 * телефоне, а кнопки для телефона рисует `static/account.js`. Пока шапка
 * считала такие ссылки «уже есть» и ничего не рисовала, на телефоне у гостя
 * оставались только «Войти» и «Терминал»: ни сводок, ни дайджеста, ни статей.
 *
 * Здесь это проверяется так, как видит гость: для каждой ширины экрана берём
 * стили страницы и смотрим, какие ссылки меню на самом деле видны (jsdom сам
 * медиа-запросы не применяет — разбор в `tests/_mobile.js`).
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom
 *     node tests/nav_mobile.js [http://127.0.0.1:8000]
 */

const { JSDOM, VirtualConsole } = require("jsdom");
const { visibleLinks, navOf, MOBILE_MAX } = require("./_mobile");

const URL_BASE = process.env.LIQSCOPE_TEST_URL || process.argv[2] || "http://127.0.0.1:8000";

//: разделы сайта: каждый должен быть доступен с каждой страницы
const SECTIONS = ["/terminal", "/digest", "/hourly", "/articles"];
//: страницы сайта, включая 404
const PAGES = ["/", "/terminal", "/digest", "/hourly", "/articles", "/net-takoy-stranicy"];
//: ширины: узкий телефон, телефон, планшет, порог вёрстки и широкий экран
const WIDTHS = [360, 380, MOBILE_MAX, MOBILE_MAX + 1, 1280];

const quiet = (e) => console.log("  ..   ошибка на странице: " +
  String((e && e.message) || e).slice(0, 70));
process.on("uncaughtException", quiet);
process.on("unhandledRejection", quiet);

let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

function apiStub(user) {
  const body = {
    ok: true, services: [], user, bot_ready: false, is_admin: !!(user && user.is_admin),
    items: [], posts: [], item: null, rooms: [], messages: [], days: [], count: 0,
  };
  return async () => ({ ok: true, status: 200, json: async () => body });
}

/** Сессия настоящего админа: шапка при входе рисует кабинет и админку. */
async function realCookie() {
  try {
    const res = await fetch(URL_BASE + "/api/auth/email/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: process.env.LIQSCOPE_TEST_EMAIL || "live@liqscope.online",
        password: process.env.LIQSCOPE_TEST_PASSWORD || "licvid-demo-2026",
      }),
    });
    const list = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
    return list.map((c) => c.split(";")[0]).join("; ");
  } catch (e) { return ""; }
}

async function open(path, lang, state, cookie) {
  const url = `${URL_BASE}${path}?lang=${lang}`;
  const headers = state.cookie && cookie ? { Cookie: cookie } : {};
  const vc = new VirtualConsole();
  vc.on("jsdomError", () => {});        // gtag и внешние скрипты — не наша проверка
  vc.on("error", () => {});
  const opts = {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.fetch = state.cookie
        ? (u, o) => fetch(String(u).startsWith("http") ? String(u) : URL_BASE + String(u),
            Object.assign({}, o, { headers: Object.assign({}, (o && o.headers) || {}, headers) }))
        : apiStub(state.user);
      win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      if (!win.matchMedia) {
        win.matchMedia = () => ({ matches: false, addEventListener() {},
          removeEventListener() {}, addListener() {}, removeListener() {} });
      }
      win.requestIdleCallback = (fn) => setTimeout(fn, 0);
      win.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, p) => (p === "measureText" ? () => ({ width: 10 }) : () => {}),
        set: () => true,
      });
    },
  };
  let dom;
  try {
    let html = await (await fetch(url, { headers })).text();
    dom = new JSDOM(html, Object.assign({ url }, opts));
  } catch (e) {
    console.log("  ..   " + path + " [" + state.who + "]: страница споткнулась (" +
      String((e && e.message) || e).slice(0, 60) + ")");
    return null;
  }
  await new Promise((done) => setTimeout(done, 1200));
  return dom;
}

/** Раздел, в котором стоит страница: на саму себя она не ссылается. */
function ownOf(path) {
  return SECTIONS.filter((s) => path === s || path.indexOf(s + "/") === 0);
}

async function checkPage(path, state, width, cookie) {
  const name = `${path} @${width} [${state.who}]`;
  const dom = await open(path, "ru", state, cookie);
  if (!dom) { check(`${name}: страница открылась`, false); return; }
  const win = dom.window;
  const { box, nav } = navOf(win);
  const until = Date.now() + 5000;
  while (Date.now() < until && box && box.children.length === 0) await wait(120);
  const own = ownOf(path);

  const lost = [];
  const twice = [];
  SECTIONS.forEach((s) => {
    if (own.indexOf(s) !== -1) return;                 // страница сама себе не ссылка
    const seen = visibleLinks(win, nav, s, width).length;
    if (seen === 0) lost.push(s);
    if (seen > 1) twice.push(s + "×" + seen);
  });
  check(`${name}: видны все разделы, кроме своего`, lost.length === 0,
    lost.length ? "нет доступа к " + lost.join(", ") : "");
  check(`${name}: раздел показан один раз`, twice.length === 0,
    twice.length ? "дубли: " + twice.join(", ") : "");

  if (state.cookie || state.user) {
    const cab = path === "/cabinet" ? 1 : visibleLinks(win, nav, "/cabinet", width).length;
    check(`${name}: кабинет доступен`, cab === 1, "ссылок: " + cab);
    const adm = state.admin
      ? (path === "/admin" ? 1 : visibleLinks(win, nav, "/admin", width).length)
      : 0;
    check(`${name}: админка ${state.admin ? "доступна" : "не показана"}`,
      state.admin ? adm === 1 : adm === 0, "ссылок: " + adm);
  } else {
    const login = path === "/login" ? 1 : visibleLinks(win, nav, "/login", width).length;
    check(`${name}: гостю есть куда войти`, login === 1, "ссылок: " + login);
  }
  win.close();
}

(async () => {
  console.log("Шапка на телефоне: " + URL_BASE);
  const cookie = await realCookie();
  const states = [{ who: "гость", user: null }];
  if (cookie) states.push({ who: "админ", user: { id: 1, username: "live", is_admin: true },
                            cookie: true, admin: true });
  else console.log("  ..   без входа: состояние админа не проверяем");

  for (const state of states) {
    for (const width of WIDTHS) {
      for (const path of PAGES) {
        await checkPage(path, state, width, cookie);
      }
    }
  }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
