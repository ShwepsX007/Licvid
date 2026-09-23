/**
 * Кнопка «Статьи» в шапке — в браузере (jsdom), а не в исходнике HTML.
 *
 * На лендинге, в дайджесте, сводках, статьях и на 404 ссылка стоит прямо в
 * разметке, а на страницах с шапкой кабинета (терминал, вход, сброс пароля,
 * кабинет, админка) кнопки рисует `static/account.js`. Именно поэтому мало
 * проверить HTML: «Статьи» должны стоять рядом с дайджестом и сводками и у
 * гостя, и у вошедшего пользователя — иначе опубликованную статью просто
 * не найти с этих страниц.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom
 *     node tests/nav_articles.js [http://127.0.0.1:8000]
 */

const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";

//: подпись кнопки на каждом языке: её берут из словаря, а не из кода
const LABEL = { ru: "Статьи", en: "Articles", zh: "文章", hi: "लेख", es: "Artículos" };
//: где шапку рисует скрипт — в HTML ссылок на разделы нет
const SCRIPTED = {
  guest: ["/terminal", "/login", "/reset"],
  user: ["/terminal", "/cabinet", "/admin"],
};

// Страницы админки и кабинета грузят свои данные и на заглушке могут
// споткнуться — для проверки меню это не важно, поэтому ошибку страницы
// только отмечаем в логе, а не роняем весь прогон
const quiet = (e) => console.log("  ..   ошибка на странице: " +
  String((e && e.message) || e).slice(0, 70));
process.on("uncaughtException", quiet);
process.on("unhandledRejection", quiet);

let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

/** Кто «вошёл» на странице: ответ /api/auth/me подменяем в fetch. */
function userStub(loggedIn) {
  return {
    ok: true, status: 200,
    json: async () => ({
      ok: true, services: [],
      user: loggedIn
        ? { id: 1, username: "live", first_name: "LiqScope", is_admin: true }
        : null,
    }),
  };
}

async function open(path, lang, loggedIn) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", () => {});        // gtag и переходы jsdom — не наша проверка
  vc.on("error", () => {});
  let dom;
  try {
    dom = await JSDOM.fromURL(`${URL_BASE}${path}?lang=${lang}`, {
      runScripts: "dangerously",
      resources: "usable",
      pretendToBeVisual: true,
      virtualConsole: vc,
      beforeParse(win) {
        // у jsdom нет fetch, а шапка им спрашивает пользователя: без заглушки
        // скрипт падает до отрисовки меню
        win.fetch = async () => userStub(loggedIn);
        // терминал тянет биржевые графики: в jsdom у них нет ни ResizeObserver,
        // ни наблюдателя за размерами — заглушки чтобы меню успело отрисоваться
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
    });
  } catch (e) {
    console.log("  ..   " + path + " [" + lang + "]: страница споткнулась (" +
      String((e && e.message) || e).slice(0, 60) + ")");
    return null;
  }
  await new Promise((done) => {
    if (dom.window.document.readyState === "complete") return setTimeout(done, 150);
    dom.window.addEventListener("load", () => setTimeout(done, 400));
    setTimeout(done, 3500);
  });
  return dom;
}

async function checkPage(path, lang, loggedIn) {
  const who = loggedIn ? "вошёл" : "гость";
  const dom = await open(path, lang, loggedIn);
  if (!dom) { check(`${path} [${lang}, ${who}]: страница открылась`, false); return; }
  const doc = dom.window.document;
  const link = doc.querySelector('a[href="/articles"]');
  check(`${path} [${lang}, ${who}]: кнопка «Статьи» в шапке`, !!link);
  if (link) {
    const nav = link.closest("nav") || link.parentElement;
    check(`${path} [${lang}, ${who}]: кнопка рядом с дайджестом и сводками`,
      !!(nav && nav.querySelector('a[href="/digest"]') &&
         nav.querySelector('a[href="/hourly"]')));
    const text = (link.textContent || "").trim();
    check(`${path} [${lang}, ${who}]: подпись «${LABEL[lang]}»`,
      text.indexOf(LABEL[lang]) !== -1, JSON.stringify(text));
  }
  // язык страницы: словарь ставит полную локаль (ru-RU, zh-CN…), сервер — код
  const htmlLang = doc.documentElement.getAttribute("lang") || "";
  check(`${path} [${lang}, ${who}]: страница на языке ${lang}`,
    htmlLang.toLowerCase().indexOf(lang) === 0, htmlLang);
  dom.window.close();
}

(async () => {
  for (const [who, pages] of Object.entries(SCRIPTED)) {
    for (const path of pages) {
      for (const lang of ["ru", "en"]) {
        await checkPage(path, lang, who === "user");
      }
    }
  }

  // На странице самих статей кнопка не нужна: гость уже здесь
  const articles = await open("/articles", "ru", false);
  if (articles) {
    check("/articles [ru, гость]: сам себя в меню не дублирует",
      !articles.window.document.querySelector('nav a[href="/articles"]'));
    articles.window.close();
  }

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
