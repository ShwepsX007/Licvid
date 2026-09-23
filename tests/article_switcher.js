/**
 * Переключатель языка на странице статьи — в браузере (jsdom).
 *
 * Английская версия статьи раньше была на сайте, но найти её было нечем:
 * адрес менялся только руками (``?lang=en``). Теперь над заголовком стоят
 * чипсы RU/EN, как у сводок по часам и дайджеста, и нажатие меняет заголовок
 * и текст **без перезагрузки** — а обычная ссылка ``?lang=xx`` остаётся
 * запасным путём для гостя без JS.
 *
 * Здесь проверяется именно поведение в браузере: подпись и порядок ссылок,
 * подмена заголовка, текста, отметки текущего языка и адреса страницы.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom
 *     node tests/article_switcher.js [http://127.0.0.1:8000]
 */

const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const SLUG = process.env.LIQSCOPE_TEST_SLUG || "kak-chitat-steny-v-stakane-5-pravil";

// Страница тянет gtag и живёт с чужими скриптами: чужая ошибка не должна
// ронять проверку переключателя.
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

async function open(path) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", () => {});          // gtag и прочий внешний шум
  vc.on("error", () => {});
  let dom;
  try {
    dom = await JSDOM.fromURL(`${URL_BASE}${path}`, {
      runScripts: "dangerously",
      resources: "usable",
      pretendToBeVisual: true,
      virtualConsole: vc,
      beforeParse(win) {
        // у jsdom нет fetch, а страница им спрашивает статью и комментарии
        win.fetch = (url, opts) => fetch(
          String(url).startsWith("http") ? String(url) : URL_BASE + String(url),
          opts);
        win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
        if (!win.matchMedia) {
          win.matchMedia = () => ({ matches: false, addEventListener() {},
            removeEventListener() {}, addListener() {}, removeListener() {} });
        }
      },
    });
  } catch (e) {
    console.log("  ..   " + path + ": страница не открылась (" +
      String((e && e.message) || e).slice(0, 70) + ")");
    return null;
  }
  await new Promise((done) => {
    if (dom.window.document.readyState === "complete") return setTimeout(done, 200);
    dom.window.addEventListener("load", () => setTimeout(done, 500));
    setTimeout(done, 4000);
  });
  return dom;
}

/** Чипсы переключателя: [{code, text, on}] по порядку. */
function chips(doc) {
  return Array.from(doc.querySelectorAll("#art-langs a[data-lang]")).map((a) => ({
    code: a.getAttribute("data-lang"),
    text: (a.textContent || "").trim(),
    on: a.classList.contains("on"),
    href: a.getAttribute("href") || "",
  }));
}

async function main() {
  console.log("Переключатель языка статьи: " + URL_BASE);

  // --- русская страница: жмём EN -----------------------------------------
  const ru = await open(`/articles/${SLUG}?lang=ru`);
  if (!ru) { check("русская страница открылась", false); }
  else {
    const win = ru.window, doc = win.document;
    const box = doc.getElementById("art-langs");
    const title = doc.getElementById("art-title");
    const body = doc.getElementById("art-body");
    const vers = JSON.parse((doc.getElementById("art-versions") || {}).textContent || "{}");
    check("русская страница открылась", !!box && !!title && !!body);
    const list = chips(doc);
    check("видны обе версии: RU и EN", list.length === 2, JSON.stringify(list));
    check("подписи на чипсах — RU и EN",
      list[0] && list[0].text === "RU" && list[1] && list[1].text === "EN");
    check("сейчас отмечен RU", !!(list[0] && list[0].on) && !list[1].on);
    check("ссылки ведут на языковые адреса",
      !!(list[0] && list[0].href.indexOf("lang=ru") !== -1 &&
         list[1] && list[1].href.indexOf("lang=en") !== -1));
    check("русский заголовок на странице", /Как читать стены/.test(title.textContent),
      title.textContent);

    const ruTitle = title.textContent;
    win.__noReload = true;                       // перезагрузка стёрла бы метку
    list[1] && doc.querySelector('#art-langs a[data-lang="en"]')
      .dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await wait(200);

    const after = chips(doc);
    check("нажатие EN не перезагрузило страницу", win.__noReload === true);
    check("текст статьи стал английским",
      !!vers.en && title.textContent === vers.en.title, title.textContent);
    check("тело статьи — английская версия",
      !!vers.en && body.innerHTML === vers.en.html);
    check("отметка переехала на EN",
      !!(after[1] && after[1].on) && !(after[0] && after[0].on));
    check("адрес страницы помнит язык", /lang=en/.test(win.location.search),
      win.location.search);
    check("английский текст на русской странице помечен lang=\"en\"",
      // язык сайта — русский, а показан английский текст: его и помечаем
      title.getAttribute("lang") === "en", String(title.getAttribute("lang")));

    // --- возвращаемся на русский ------------------------------------------
    doc.querySelector('#art-langs a[data-lang="ru"]')
      .dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await wait(200);
    check("возврат на RU вернул заголовок", title.textContent === ruTitle, title.textContent);
    check("на русской странице пометки языка у текста нет",
      title.getAttribute("lang") === null && body.getAttribute("lang") === null,
      String(title.getAttribute("lang")) + "/" + String(body.getAttribute("lang")));
    check("адрес вернулся на RU", /lang=ru/.test(win.location.search), win.location.search);
    win.close();
  }

  // --- английская страница: чипсы и текст на месте ------------------------
  const en = await open(`/articles/${SLUG}?lang=en`);
  if (!en) { check("английская страница открылась", false); }
  else {
    const win = en.window, doc = win.document;
    const list = chips(doc);
    check("английская страница открылась с чипсами", list.length === 2);
    check("на английской странице отмечен EN",
      !!(list[1] && list[1].on) && !(list[0] && list[0].on));
    check("заголовок английский",
      /How to read order book walls/.test(doc.getElementById("art-title").textContent),
      doc.getElementById("art-title").textContent);
    win.close();
  }

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
}

main();
