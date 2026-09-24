require("./_dom_env");
/**
 * Раздел «Сводки по часам»: посты канала на сайте.
 *
 *  Требование: посты, которые ушли в канал, видны на сайте целиком — с фото и
 *  той же подписью, — а календарь открывает любой день и показывает все его
 *  сводки. Язык интерфейса переключает подписи (пять языков), а текст поста
 *  берётся на языке сайта: русский для ru, английский (канал-источник) для
 *  остальных, с ручным переключателем внутри поста.
 *
 *  Запуск (сервер уже на 127.0.0.1:8011):
 *      NODE_PATH=./node_modules node tests/hourly_posts.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8011";
const FRESH = 8;                       // столько свежих сводок показывает лента

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function pad(n) { return (n < 10 ? "0" : "") + n; }
function key(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }

/** Синтетический архив: сегодня четыре сводки, дальше — по дням назад. */
function archive() {
  const out = [];
  const start = new Date(2026, 8, 18, 21, 0, 0);       // 18 сентября 2026, 21:00
  const plan = [[0, 4], [1, 3], [2, 2], [4, 3], [9, 2], [17, 1], [33, 2]];
  plan.forEach(([back, n]) => {
    for (let i = 0; i < n; i++) {
      const ts = Math.floor((start.getTime() - back * 86400000 - i * 4 * 3600000) / 1000);
      const day = key(new Date(ts * 1000));
      out.push({
        id: day + "-" + pad(new Date(ts * 1000).getHours()) + "00",
        ts: ts, day: day,
        window_h: 4, interval_h: 4,
        total_usd: 900000000 - out.length * 1000000,
        liq_count: 8000 - out.length * 10,
        texts: {
          ru: "<b>Сводка RU " + out.length + "</b>\n💥 $215.1K · 7 ликвидаций",
          en: "<b>Summary EN " + out.length + "</b>\n💥 $215.1K · 7 fills",
        },
      });
    }
  });
  out.sort((a, b) => b.ts - a.ts);
  return out;
}

function dayIndex(posts) {
  const map = {};
  posts.forEach((p) => {
    const row = map[p.day] || (map[p.day] = { day: p.day, n: 0, total_usd: 0 });
    row.n += 1;
    row.total_usd += p.total_usd;
  });
  return Object.keys(map).sort().reverse().map((d) => map[d]);
}

function publicPost(p, lang) {
  const want = String(lang || "ru").indexOf("ru") === 0 ? "ru" : "en";
  return {
    id: p.id, ts: p.ts, day: p.day, day_label: p.day, weekday: "",
    window_h: p.window_h, interval_h: p.interval_h,
    total_usd: p.total_usd, liq_count: p.liq_count,
    langs: ["ru", "en"], sent: { ru: true, en: true },
    photo: { url: "/api/hourly/photo/" + p.id, name: "cover.jpg", source: "bundle" },
    text: p.texts[want], html: p.texts[want], texts: p.texts,
    plain: p.texts[want].replace(/<[^>]+>/g, ""),
  };
}

function makeFetch(posts) {
  return async function (url) {
    const u = String(url);
    const json = (body) => ({ ok: true, status: 200, json: async () => body });
    if (u.indexOf("/api/hourly/photo/") === 0) return json({ ok: false });
    if (u.indexOf("/api/hourly") === 0) {
      const lang = (/\blang=([a-z]+)/.exec(u) || [])[1] || "ru";
      const day = (/[?&]day=([0-9-]+)/.exec(u) || [])[1] || "";
      const items = day ? posts.filter((p) => p.day === day) : posts.slice(0, FRESH);
      return json({
        ok: true, items: items.map((p) => publicPost(p, lang)),
        days: dayIndex(posts), count: posts.length, days_count: dayIndex(posts).length,
        keep: 1200, tz_hours: 3, interval_h: 4,
        channel_url: "https://t.me/+a13HtmoE9jNhY2M6",
        stats: { count: posts.length, days: dayIndex(posts).length },
      });
    }
    if (u.indexOf("/api/auth/me") === 0) return json({ ok: true, user: null });
    if (u.indexOf("/api/health") === 0) return json({ ok: true });
    return json({ ok: false });
  };
}

async function openPage(path, posts) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    if (msg.indexOf("Could not load img") !== -1) return;
    if (msg.indexOf("Could not load link") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(ru(URL_BASE + path), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        Object.defineProperty(win.navigator, "language",
          { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages",
          { value: ["ru-RU", "ru"], configurable: true });
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      try { win.localStorage.clear(); } catch (e) { /* ignore */ }
      win.fetch = makeFetch(posts);
    },
  });
  await wait(900);
  return { win: dom.window, doc: dom.window.document };
}

function click(win, el) {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
}

async function main() {
  console.log("раздел «Сводки по часам»: посты канала на сайте");

  const posts = archive();
  const days = dayIndex(posts);
  const page = await openPage("/hourly", posts);
  const doc = page.doc, win = page.win;
  const $ = (s) => doc.querySelector(s);
  const $$ = (s) => Array.prototype.slice.call(doc.querySelectorAll(s));

  // --- лента свежих сводок --------------------------------------------------
  const cards = $$("#hour-list .hour-card");
  check("свежие сводки отрисованы", cards.length > 0, cards.length);
  check("в ленте не больше " + FRESH + " сводок — архив не копится списком",
    cards.length === FRESH, cards.length);
  check("под лентой сказано, сколько всего сводок",
    $("#hour-more").textContent.indexOf(String(posts.length)) !== -1,
    $("#hour-more").textContent);
  check("в карточке свежей сводки видно её день",
    cards[0] && /сентябр/i.test(cards[0].textContent), cards[0] && cards[0].textContent);

  // --- шапка раздела --------------------------------------------------------
  check("в шапке видно, как часто выходят сводки",
    /каждые 4/.test($("#hour-every").textContent), $("#hour-every").textContent);
  check("в шапке число сводок архива",
    $("#hour-total").textContent === String(posts.length), $("#hour-total").textContent);
  check("в шапке число дней архива",
    $("#hour-days").textContent === String(days.length), $("#hour-days").textContent);
  check("в шапке сказано, по какому поясу считаются дни",
    /UTC\+3/.test($("#hour-tz").textContent), $("#hour-tz").textContent);
  const chan = $("#hour-channel");
  check("есть ссылка на канал, откуда посты",
    chan && chan.getAttribute("href") === "https://t.me/+a13HtmoE9jNhY2M6" &&
    !chan.classList.contains("hidden"), chan && chan.getAttribute("href"));

  // --- календарь по дням ----------------------------------------------------
  check("календарь нарисован", $$("#cal-grid .cal-day").length > 0);
  check("у дней недели есть подписи", $$("#cal-week span").length === 7);
  const title = $("#cal-title").textContent;
  check("в шапке календаря месяц и год",
    /сентябр/i.test(title) && /2026/.test(title), title);
  const has = $$("#cal-grid .cal-day.has");
  const sepDays = days.filter((d) => d.day.slice(0, 7) === "2026-09").length;
  check("кнопками стали ровно те дни, за которые есть сводки",
    has.length === sepDays, has.length + " из " + sepDays);
  check("день без сводок кнопкой не стал", $$("#cal-grid .cal-day.empty").length > 0);
  const on = $$("#cal-grid .cal-day.on");
  check("подсвечен ровно один день — открытый",
    on.length === 1 && on[0].getAttribute("data-day") === days[0].day,
    on.map((b) => b.getAttribute("data-day")).join("/"));
  check("в подсказке дня — сколько в нём сводок",
    has[0] && /сводок: \d+/.test(has[0].getAttribute("title")),
    has[0] && has[0].getAttribute("title"));

  // --- посты дня: как в канале ---------------------------------------------
  const openPosts = posts.filter((p) => p.day === days[0].day);
  const tgs = $$("#hour-posts .tg-card");
  check("за день показаны все его сводки",
    tgs.length === openPosts.length, tgs.length + " из " + openPosts.length);
  check("в заголовке дня — дата и число сводок",
    /сентябр/i.test($(".hour-day-head h2").textContent) &&
    /сводок: \d+/.test($(".hour-day-head .hd-n").textContent),
    $(".hour-day-head").textContent);
  const first = tgs[0];
  check("свежая сводка дня идёт первой",
    first && first.id === "post-" + openPosts[0].id, first && first.id);
  check("у поста есть фото — то же, что ушло в канал",
    first && first.querySelector(".tg-photo") &&
    first.querySelector(".tg-photo").getAttribute("src") ===
      "/api/hourly/photo/" + openPosts[0].id,
    first && first.querySelector(".tg-photo") &&
      first.querySelector(".tg-photo").getAttribute("src"));
  check("фото поста грузится лениво",
    first.querySelector(".tg-photo").getAttribute("loading") === "lazy");
  check("у фото есть подпись для незрячих",
    first.querySelector(".tg-photo").getAttribute("alt") === "Фото сводки",
    first.querySelector(".tg-photo").getAttribute("alt"));
  check("подпись поста — разметка канала, а не текст с тегами",
    !!first.querySelector(".tg-text b") &&
    first.querySelector(".tg-text").textContent.indexOf("<b>") === -1,
    first.querySelector(".tg-text").textContent.slice(0, 60));
  check("на русском сайте пост показан по-русски",
    /Сводка RU/.test(first.querySelector(".tg-text").textContent),
    first.querySelector(".tg-text").textContent.slice(0, 40));
  check("в шапке поста — время сводки", /\d{2}:\d{2}/.test(
    first.querySelector(".tg-time").textContent),
    first.querySelector(".tg-time").textContent);
  const foot = first.querySelector(".tg-foot");
  check("под постом — цифры окна и ссылка на сводку",
    foot && /\$/.test(foot.textContent) && foot.querySelector("a") &&
    foot.querySelector("a").getAttribute("href") === "/hourly?post=" + openPosts[0].id,
    foot && foot.textContent);

  // --- язык внутри поста ----------------------------------------------------
  const langBtns = first.querySelectorAll(".tg-langs button");
  check("у поста есть переключатель RU/EN", langBtns.length === 2, langBtns.length);
  const enBtn = Array.prototype.filter.call(langBtns,
    (b) => b.getAttribute("data-lang") === "en")[0];
  if (enBtn) click(win, enBtn);
  await wait(60);
  const afterEn = $$("#hour-posts .tg-card")[0];
  check("переключатель EN показывает английскую версию поста",
    /Summary EN/.test(afterEn.querySelector(".tg-text").textContent),
    afterEn.querySelector(".tg-text").textContent.slice(0, 40));
  check("активной стала кнопка EN",
    !!afterEn.querySelector('.tg-langs button[data-lang="en"].on'));

  // --- календарь переключает день ------------------------------------------
  const other = has[1];
  if (other) click(win, other);
  await wait(700);
  const otherDay = other.getAttribute("data-day");
  const otherPosts = posts.filter((p) => p.day === otherDay);
  check("клик по дню календаря открывает его сводки",
    $$("#hour-posts .tg-card").length === otherPosts.length,
    $$("#hour-posts .tg-card").length + " из " + otherPosts.length);
  check("подсветка календаря переехала на выбранный день",
    $$("#cal-grid .cal-day.on")[0].getAttribute("data-day") === otherDay);

  // --- английский интерфейс -------------------------------------------------
  win.LiqScopeI18n.set("en");
  await wait(700);
  check("на английском подписи раздела переводятся",
    /Hourly market summaries/.test(doc.querySelector("h1").textContent),
    doc.querySelector("h1").textContent);
  check("на английском пост показывается по-английски",
    /Summary EN/.test($$("#hour-posts .tg-text")[0].textContent),
    $$("#hour-posts .tg-text")[0].textContent.slice(0, 40));
  check("на английском лента свежих сводок тоже переведена",
    /Latest summaries/.test($$(".hour-list-col h2")[0].textContent),
    $$(".hour-list-col h2")[0].textContent);

  // --- кто вошёл: ссылки в шапке -------------------------------------------
  const nav = $("#nav-account");
  check("гостю в шапке предложен вход",
    nav && /\/login/.test(nav.innerHTML) && !/\/admin/.test(nav.innerHTML),
    nav && nav.textContent);
  const jump = $("#hour-jump").innerHTML;
  check("из раздела видно дорогу в терминал и дайджест",
    /\/terminal/.test(jump) && /\/digest/.test(jump), jump.replace(/<[^>]+>/g, " ").slice(0, 80));
  check("в разделе нет ошибок в консоли", errors.length === 0, errors.slice(0, 2).join(" | "));
  await win.close();

  // --- ссылка на конкретный пост: ?post= -----------------------------------
  const target = posts[5];
  const deep = await openPage("/hourly?post=" + target.id, posts);
  const ddoc = deep.doc;
  check("ссылка ?post= открывает день этого поста",
    !!ddoc.querySelector(".hour-day-head h2") &&
    /сентябр/i.test(ddoc.querySelector(".hour-day-head h2").textContent),
    ddoc.querySelector(".hour-day-head h2") &&
      ddoc.querySelector(".hour-day-head h2").textContent);
  const hit = ddoc.querySelector(".tg-card.hit");
  check("нужный пост подсвечен", !!hit && hit.id === "post-" + target.id,
    hit && hit.id);
  await deep.win.close();

  // --- пустой архив: раздел честно говорит об этом --------------------------
  const empty = await openPage("/hourly", []);
  check("пустой архив: сказано, что сводок пока нет",
    /Сводок пока нет/.test(empty.doc.querySelector("#hour-list").textContent),
    empty.doc.querySelector("#hour-list").textContent);
  check("пустой архив: место справа тоже объясняет",
    /этот день/.test(empty.doc.querySelector("#hour-posts").textContent),
    empty.doc.querySelector("#hour-posts").textContent);
  await empty.win.close();

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => {
  console.log("  FAIL исключение: " + (e && e.stack ? e.stack : e));
  process.exit(1);
});
