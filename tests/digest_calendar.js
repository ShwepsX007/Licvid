/**
 * Страница «Дневной дайджест»: переходы и выбор старого выпуска по дате.
 *
 *  Требование: из дайджеста видно дорогу в кабинет (и в админку — админу),
 *  а старые выпуски выбираются по календарю — списком они не копятся. В ленте
 *  видны только свежие выпуски, любой прошлый открывается кликом по дате.
 *
 *  Запуск (сервер уже на 127.0.0.1:8011):
 *      NODE_PATH=./node_modules node tests/digest_calendar.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8011";
const FRESH = 7;                       // столько свежих выпусков показывает лента
const ARCHIVE = 12;                    // выпусков в синтетическом архиве

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                   "августа", "сентября", "октября", "ноября", "декабря"];

function pad(n) { return (n < 10 ? "0" : "") + n; }
function key(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
function label(day) {
  const p = day.split("-").map(Number);
  return p[2] + " " + MONTHS_RU[p[1] - 1];
}

/** Синтетический архив: свежие дни подряд, дальше — прошлый месяц. */
function archive() {
  const days = [];
  const start = new Date(2026, 8, 18);            // 18 сентября 2026
  const offsets = [0, 1, 2, 3, 4, 6, 8, 11, 13, 16, 19, 24];
  offsets.forEach((back, i) => {
    const d = new Date(start.getTime() - back * 86400000);
    days.push({
      day: key(d), label: label(key(d)),
      published: i % 3 !== 0, total_usd: 1.5e9 - i * 1e7, liq_count: 9000 - i * 100,
    });
  });
  return days;
}

function record(info) {
  return {
    id: info.day, day: info.day, day_label: info.label, brief: "Итоги дня " + info.label,
    published: info.published, total_usd: info.total_usd, window_h: 24,
    article: "<h3>Ликвидации</h3><p>Сводка за " + info.label + "</p>",
    post: "Telegram · " + info.label,
    summary: "Рассказ за " + info.label,
  };
}

function makeFetch(days, user) {
  return async function (url) {
    const u = String(url);
    const json = (body) => ({ ok: true, status: 200, json: async () => body });
    if (u.indexOf("/api/digest/") === 0) {
      const day = decodeURIComponent(u.split("?")[0].split("/api/digest/")[1]);
      const info = days.filter((d) => d.day === day)[0];
      return info ? json({ ok: true, item: record(info) })
                  : json({ ok: false, error: "not_found" });
    }
    if (u.indexOf("/api/digest") === 0) {
      return json({
        ok: true, items: days.slice(0, 12).map(record), days: days,
        count: days.length, keep: 400,
      });
    }
    if (u.indexOf("/api/auth/me") === 0) return json({ ok: true, user: user || null });
    if (u.indexOf("/api/health") === 0) return json({ ok: true });
    // всё остальное (страницы кабинета и админки) — «нет данных»: код этих
    // разделов выходит по !ok и не тянет за собой весь бэкенд
    return json({ ok: false });
  };
}

async function openPage(path, opts) {
  const days = archive();
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    if (msg.indexOf("Could not load link") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(URL_BASE + path, {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try { Object.defineProperty(win.navigator, "language",
        { value: "ru-RU", configurable: true }); } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      try { win.localStorage.clear(); } catch (e) { /* ignore */ }
      win.fetch = makeFetch(days, opts && opts.user);
    },
  });
  await wait(900);
  return { win: dom.window, doc: dom.window.document, days };
}

function click(win, el) {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
}

async function main() {
  console.log("страница дайджестов: переходы и календарь выпусков");

  const admin = { id: 1, username: "boss", display_name: "Boss", is_admin: true };
  const page = await openPage("/digest", { user: admin });
  const doc = page.doc, win = page.win;
  const $ = (s) => doc.querySelector(s);
  const $$ = (s) => Array.prototype.slice.call(doc.querySelectorAll(s));
  const days = page.days;

  // --- лента свежих выпусков ------------------------------------------------
  const cards = $$("#dig-list .dig-card");
  check("свежие выпуски отрисованы", cards.length > 0, cards.length);
  check("в ленте не больше " + FRESH + " выпусков — архив не копится списком",
    cards.length === FRESH, cards.length);
  check("под лентой сказано, сколько всего выпусков",
    /12/.test($("#dig-more").textContent), $("#dig-more").textContent);
  check("самый свежий выпуск открыт сразу",
    $("#dig-article h2") && $("#dig-article h2").textContent.indexOf(days[0].label) !== -1,
    $("#dig-article h2") && $("#dig-article h2").textContent);

  // --- календарь ------------------------------------------------------------
  check("календарь нарисован", $$("#cal-grid .cal-day").length > 0);
  check("у дней недели есть подписи", $$("#cal-week span").length === 7);
  const title = $("#cal-title").textContent;
  check("в шапке календаря месяц и год выбранного выпуска",
    /сентябр/i.test(title) && /2026/.test(title), title);
  const has = $$("#cal-grid .cal-day.has");
  const sepDays = days.filter((d) => d.day.slice(0, 7) === "2026-09").length;
  check("кнопками стали ровно те дни сентября, за которые есть выпуск",
    has.length === sepDays, has.length + " из " + sepDays);
  check("день без выпуска кнопкой не стал",
    $$("#cal-grid .cal-day.empty").length > 0);
  const on = $$("#cal-grid .cal-day.on");
  check("подсвечен ровно один — открытый выпуск", on.length === 1 && on[0].textContent === "18",
    on.map((b) => b.textContent).join("/"));
  const titlesOk = has.every((b) => {
    const d = days.filter((x) => x.day === b.getAttribute("data-day"))[0];
    return d && b.getAttribute("title").indexOf(d.label) === 0;
  });
  check("в подсказке каждой даты — её день и сумма",
    titlesOk, has.map((b) => b.getAttribute("title")).slice(0, 3).join(" | "));

  // --- старый выпуск по дате ------------------------------------------------
  click(win, $("#cal-prev"));
  await wait(30);
  check("стрелка ‹ уводит в прошлый месяц",
    /август/i.test($("#cal-title").textContent), $("#cal-title").textContent);
  const aug = days.filter((d) => d.day.slice(0, 7) === "2026-08");
  check("в прошлом месяце видны свои выпуски",
    $$("#cal-grid .cal-day.has").length === aug.length,
    $$("#cal-grid .cal-day.has").length + " из " + aug.length);
  const oldBtn = $$("#cal-grid .cal-day.has").filter(
    (b) => b.getAttribute("data-day") === aug[0].day)[0];
  check("старый день кликается", !!oldBtn, aug[0] && aug[0].day);
  click(win, oldBtn);
  await wait(120);
  check("старый выпуск открылся в статье",
    $("#dig-article h2").textContent.indexOf(aug[0].label) !== -1,
    $("#dig-article h2").textContent);
  check("в ссылке страницы появилась дата выпуска",
    win.location.search.indexOf(aug[0].day) !== -1, win.location.search);
  check("календарь переехал к открытому выпуску и подсветил его",
    $$("#cal-grid .cal-day.on").length === 1 &&
    $$("#cal-grid .cal-day.on")[0].getAttribute("data-day") === aug[0].day,
    $$("#cal-grid .cal-day.on").map((b) => b.getAttribute("data-day")).join("/"));
  check("кнопки ‹/› не выходят за пределы архива",
    $("#cal-next").disabled === false && $("#cal-prev").disabled === true,
    [$("#cal-prev").disabled, $("#cal-next").disabled].join("/"));

  // --- переходы -------------------------------------------------------------
  const jump = $("#dig-jump");
  const hrefs = $$("#dig-jump a").map((a) => a.getAttribute("href"));
  check("из дайджеста есть переход в кабинет",
    hrefs.indexOf("/cabinet") !== -1, hrefs.join(" "));
  check("и в админку — админу",
    hrefs.indexOf("/admin") !== -1, hrefs.join(" "));
  check("и в терминал", hrefs.indexOf("/terminal") !== -1, hrefs.join(" "));
  check("в шапке тоже кабинет и админка",
    $$("#nav-account a").map((a) => a.getAttribute("href")).indexOf("/cabinet") !== -1 &&
    $$("#nav-account a").map((a) => a.getAttribute("href")).indexOf("/admin") !== -1,
    $$("#nav-account a").map((a) => a.getAttribute("href")).join(" "));
  check("ссылки подписаны по-русски",
    /Кабинет/.test(jump.textContent) && /Админка/.test(jump.textContent),
    jump.textContent);

  // --- гость без входа ------------------------------------------------------
  const guest = await openPage("/digest", { user: null });
  const gHrefs = Array.prototype.slice.call(guest.doc.querySelectorAll("#dig-jump a"))
    .map((a) => a.getAttribute("href"));
  check("гостю предлагают войти, а не пустую ссылку",
    gHrefs.indexOf("/login") !== -1 && gHrefs.indexOf("/admin") === -1, gHrefs.join(" "));

  // --- переход из кабинета --------------------------------------------------
  const cab = await openPage("/cabinet", { user: admin });
  const cabLinks = Array.prototype.slice.call(cab.doc.querySelectorAll("#nav-account a"))
    .map((a) => a.getAttribute("href"));
  check("из кабинета видно дорогу в дайджесты",
    cabLinks.indexOf("/digest") !== -1, cabLinks.join(" "));
  const adm = await openPage("/admin", { user: admin });
  const admLinks = Array.prototype.slice.call(adm.doc.querySelectorAll("#nav-account a"))
    .map((a) => a.getAttribute("href"));
  check("из админки тоже", admLinks.indexOf("/digest") !== -1, admLinks.join(" "));

  check("ошибок страниц нет", errors.length === 0, errors.slice(0, 3).join(" | "));
}

main().then(() => {
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}).catch((e) => {
  console.log("  --   " + String(e && e.stack || e).slice(0, 400));
  console.log("итог: " + ok + " ок, " + (fail + 1) + " ошибок");
  process.exit(1);
});
