require("./_dom_env");
/**
 * Перелинковка разделов — в браузере (jsdom), а не в исходнике HTML.
 *
 * Правило простое: с любой страницы сайта можно уйти в любой раздел. Часть
 * кнопок стоит прямо в разметке (их видит и гость без скриптов, и поисковик),
 * часть рисует `static/account.js` — терминал, вход, сброс, кабинет, админка и
 * 404 держат в шапке только язык и логотип. Если страница не подключит этот
 * скрипт или он не нарисует меню без ответа сервера, раздел становится
 * тупиком: из «Статей» нельзя было попасть в кабинет, а из кабинета — в статьи.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom
 *     node tests/nav_articles.js [http://127.0.0.1:8000]
 */

const { JSDOM, VirtualConsole } = require("jsdom");
// Видимость ссылок считаем с учётом мобильных правил CSS: на главной часть
// разделов приходит кнопками `nav-mob`, которые прячутся на широком экране —
// это «одна ссылка то на телефоне, то на компьютере», а не дубль.
const { visibleLinks } = require("./_mobile");

const URL_BASE = process.env.LIQSCOPE_TEST_URL || process.argv[2] || "http://127.0.0.1:8000";

//: подпись раздела «Статьи» на каждом языке — её берут из словаря, не из кода
const LABEL = { ru: "Статьи", en: "Articles", zh: "文章", hi: "लेख", es: "Artículos" };
//: разделы сайта: с любой страницы должен быть путь в каждый
const SECTIONS = ["/terminal", "/digest", "/hourly", "/articles"];
//: эта проверка смотрит на широкий экран: телефон — в tests/nav_mobile.js
const DESKTOP = 1280;
//: страницы сайта: публичные, служебные и 404
const PAGES = ["/", "/terminal", "/digest", "/hourly", "/articles", "/net-takoy-stranicy"];
//: кто смотрит страницу
const STATES = [
  { who: "гость", user: null },
  { who: "вошедший", user: { id: 2, username: "user", first_name: "User", is_admin: false } },
  { who: "админ", user: { id: 1, username: "live", first_name: "LiqScope", is_admin: true } },
];

// Страницы кабинета и админки грузят свои данные и на заглушке могут
// споткнуться — для проверки меню это не важно, ошибку только отмечаем
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

/** Заглушка API: про пользователя отвечаем честно, остальное — пустое «ок». */
function apiStub(user) {
  const body = {
    ok: true, services: [], user, bot_ready: false, is_admin: !!(user && user.is_admin),
    items: [], posts: [], item: null, rooms: [], messages: [], days: [], count: 0,
  };
  return async () => ({ ok: true, status: 200, json: async () => body });
}

async function open(path, lang, user) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", () => {});        // gtag и внешние скрипты — не наша проверка
  vc.on("error", () => {});
  const url = `${URL_BASE}${path}?lang=${lang}`;
  const opts = {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.fetch = apiStub(user);
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
    dom = await JSDOM.fromURL(url, opts);
  } catch (e) {
    // 404 отдаёт страницу со статусом 404, а fromURL такую не берёт: забираем
    // разметку сами и отдаём jsdom с тем же адресом — страница та же самая
    try {
      const html = await (await fetch(url)).text();
      dom = new JSDOM(html, Object.assign({ url }, opts));
    } catch (e2) {
      console.log("  ..   " + path + " [" + lang + "]: страница споткнулась (" +
        String((e2 && e2.message) || e2).slice(0, 60) + ")");
      return null;
    }
  }
  await new Promise((done) => {
    if (dom.window.document.readyState === "complete") return setTimeout(done, 150);
    dom.window.addEventListener("load", () => setTimeout(done, 400));
    setTimeout(done, 3500);
  });
  return dom;
}

/** Меню страницы: кнопки разделов, вход, кабинет, админка. */
function menu(doc) {
  const box = doc.getElementById("nav-account");
  const nav = (box && (box.closest("nav") || box.parentNode)) || doc;
  const links = Array.from(nav.querySelectorAll("a[href]"));
  return { box, nav, links };
}

async function checkPage(path, lang, state) {
  const name = `${path} [${lang}, ${state.who}]`;
  const dom = await open(path, lang, state.user);
  if (!dom) { check(`${name}: страница открылась`, false); return; }
  const win = dom.window;
  const doc = win.document;
  const { box, nav, links } = menu(doc);
  if (!box) {
    check(`${name}: страница открылась`, false, "нет шапки #nav-account");
    dom.window.close();
    return;
  }
  // Ждём, пока скрипт нарисует кнопки (на публичных страницах часть ссылок
  // уже стоит в разметке — тогда ждём кнопку входа/кабинета)
  const until = Date.now() + 5000;
  while (Date.now() < until && box.children.length === 0) await wait(120);

  const own = SECTIONS.filter((s) => path === s || path.indexOf(s + "/") === 0);
  const seen = (href) => visibleLinks(win, nav, href, DESKTOP).length;
  const missing = SECTIONS.filter((s) => own.indexOf(s) === -1 && !seen(s));
  check(`${name}: все разделы, кроме своего`, missing.length === 0,
    missing.length ? "нет ссылок на " + missing.join(", ") : "");

  // Дубли мешают: на публичных страницах ссылка стоит в HTML и скрипт её
  // рисовать не должен
  const dupes = SECTIONS.filter((s) => seen(s) > 1);
  check(`${name}: разделы не задвоены`, dupes.length === 0, dupes.join(", "));

  const has = (href) => seen(href) > 0;
  if (!state.user) {
    check(`${name}: гость может войти`, path === "/login" || has("/login"));
    check(`${name}: админки у гостя нет`, !has("/admin"));
  } else {
    check(`${name}: из раздела видно кабинет`, path === "/cabinet" || has("/cabinet"));
    const expectAdmin = !!state.user.is_admin && path !== "/admin";
    check(`${name}: админка ${expectAdmin ? "есть" : "не нужна"}`,
      has("/admin") === expectAdmin);
    check(`${name}: выход есть`, !!doc.getElementById("acc-logout"));
  }

  const art = visibleLinks(win, nav, "/articles", DESKTOP)[0];
  if (art && path !== "/articles") {
    check(`${name}: подпись «${LABEL[lang]}»`, (art.textContent || "").indexOf(LABEL[lang]) !== -1,
      JSON.stringify((art.textContent || "").trim()));
  }
  dom.window.close();
}

/* ---------- настоящая сессия админа: сервер отвечает сам ---------------- */

async function realCookie() {
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
}

/** Открыть страницу с настоящей сессией: fetch идёт на сервер с cookie. */
async function openAs(path, lang, cookie) {
  const url = `${URL_BASE}${path}?lang=${lang}`;
  const vc = new VirtualConsole();
  vc.on("jsdomError", () => {});
  vc.on("error", () => {});
  let dom;
  try {
    dom = new JSDOM(await (await fetch(url, { headers: { Cookie: cookie } })).text(), {
      url,
      runScripts: "dangerously",
      resources: "usable",
      pretendToBeVisual: true,
      virtualConsole: vc,
      beforeParse(win) {
        win.fetch = (u, o) => fetch(
          String(u).startsWith("http") ? String(u) : URL_BASE + String(u),
          Object.assign({}, o, { headers: Object.assign({}, (o && o.headers) || {},
            { Cookie: cookie }) }));
        win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
        if (!win.matchMedia) {
          win.matchMedia = () => ({ matches: false, addEventListener() {},
            removeEventListener() {}, addListener() {}, removeListener() {} });
        }
        win.requestIdleCallback = (fn) => setTimeout(fn, 0);
      },
    });
  } catch (e) {
    console.log("  ..   " + path + " (сессия): не открылась (" +
      String((e && e.message) || e).slice(0, 60) + ")");
    return null;
  }
  await new Promise((done) => setTimeout(done, 1500));
  return dom;
}

async function checkLiveAdmin(slug) {
  let cookie = "";
  try { cookie = await realCookie(); } catch (e) { cookie = ""; }
  if (!cookie) { console.log("  ..   без входа: живую сессию админа не проверить"); return; }
  const pages = ["/articles", "/terminal", "/cabinet"];
  if (slug) pages.push(`/articles/${slug}`);
  for (const path of pages) {
    const name = `${path} [ru, админ с сервера]`;
    const dom = await openAs(path, "ru", cookie);
    if (!dom) { check(`${name}: страница открылась`, false); continue; }
    const doc = dom.window.document;
    const { box, nav } = menu(doc);
    const until = Date.now() + 5000;
    while (Date.now() < until && box && !box.querySelector("a")) await wait(120);
    const has = (href) => visibleLinks(dom.window, nav, href, DESKTOP).length > 0;
    // Себя страница не показывает: на «Кабинете» кнопки кабинета нет, на
    // «Статьях» — ссылки на сами статьи
    const own = SECTIONS.filter((s) => path === s || path.indexOf(s + "/") === 0);
    const want = (path === "/cabinet" ? [] : ["/cabinet"]).concat(
      path === "/admin" ? [] : ["/admin"]);
    const noAcc = want.filter((h) => !has(h));
    check(`${name}: кабинет и админка в шапке`, noAcc.length === 0,
      "нет " + noAcc.join(", "));
    const lost = SECTIONS.filter((s) => own.indexOf(s) === -1 && !has(s));
    check(`${name}: разделы на месте`, lost.length === 0, lost.join(", "));
    dom.window.close();
  }
}

(async () => {
  console.log("Перелинковка разделов: " + URL_BASE);
  for (const state of STATES) {
    for (const path of PAGES) {
      for (const lang of ["ru", "en"]) {
        await checkPage(path, lang, state);
      }
      // прочие языки — на дайджесте: там ссылка на «Статьи» стоит прямо в
      // разметке и подпись ей ставит словарь страницы
      if (path === "/digest") {
        for (const lang of ["zh", "hi", "es"]) await checkPage("/digest", lang, state);
      }
    }
  }
  // последняя статья из списка: на её странице тоже должна быть шапка
  let slug = "";
  try {
    const d = await (await fetch(URL_BASE + "/api/articles?lang=ru")).json();
    slug = ((d.items || [])[0] || {}).id || "";
  } catch (e) { slug = ""; }
  await checkLiveAdmin(slug);

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
