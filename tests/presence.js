require("./_dom_env");
/**
 * ⏱ «Я ещё здесь»: страница сама говорит серверу, что гость на месте.
 *
 * Проверяем `static/presence.js`: отметка уходит вскоре после загрузки страницы
 * и дальше по интервалу, скрытая вкладка молчит (свернутый браузер — не
 * «человек у экрана»), возвращение и уход отправляют отметку сразу (уход — через
 * `sendBeacon`, чтобы запрос дожил до закрытия вкладки). Сеть подменена: ни
 * одного настоящего запроса, только записи о вызовах.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     NODE_PATH=/tmp/smoke/node_modules node tests/presence.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const http = require("http");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const TICK = 150;                     // «минута» в тесте — 150 мс
const FIRST = 40;                     // первая отметка — почти сразу
const PAGES = ["/", "/terminal", "/login", "/cabinet", "/admin", "/reset", "/digest"];

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (fn()) return Date.now() - t0;
    await wait(10);
  }
  return -1;
}

function get(path) {
  return new Promise((resolve, reject) => {
    const u = new URL(URL_BASE + path);
    http.get({ hostname: u.hostname, port: u.port, path: u.pathname + u.search,
               headers: { "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126" } },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (c) => { body += c; });
        res.on("end", () => resolve({ status: res.statusCode, body }));
      }).on("error", reject);
  });
}

async function open(path) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (/Not implemented|Could not load|canvas|clearRect/i.test(msg)) return;
    errors.push("jsdomError: " + msg.slice(0, 160));
  });
  vc.on("error", (...a) => {
    const msg = a.map((x) => String((x && x.message) || x)).join(" ");
    if (/clearRect|canvas/i.test(msg)) return;
    errors.push("console.error: " + msg.slice(0, 160));
  });

  const pings = [];
  const beacons = [];
  const dom = await JSDOM.fromURL(URL_BASE + path, {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.LIQSCOPE_PRESENCE_MS = TICK;
      win.LIQSCOPE_PRESENCE_FIRST_MS = FIRST;
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
        // страница сама ходит в /api/auth/me и /api/feedback — считаем только
        // отметки присутствия, иначе проверка ловила бы чужие запросы
        if (u.indexOf("/api/visit/ping") >= 0) pings.push({ at: Date.now(), url: u, opts: opts || {} });
        return { ok: true, status: 200,
                 json: async () => ({ ok: true, counted: true, cc: "FI" }) };
      };
      win.navigator.sendBeacon = (url, body) => {
        const entry = { url: String(url), text: "", blob: null };
        // путь приходит Blob'ом (в браузере его понимает сам sendBeacon),
        // поэтому читаем его асинхронно — как это сделал бы браузер
        if (body && typeof body.text === "function") {
          entry.blob = body;
          body.text().then((t) => { entry.text = String(t); }).catch(() => {});
        } else {
          entry.text = String(body);
        }
        beacons.push(entry);
        return true;
      };
    },
  });
  return { win: dom.window, doc: dom.window.document, pings, beacons, at: Date.now() };
}

/** Спрятать или показать вкладку: jsdom всегда считает её видимой. */
function setHidden(win, hidden) {
  Object.defineProperty(win.document, "hidden", { value: hidden, configurable: true });
  Object.defineProperty(win.document, "visibilityState",
                        { value: hidden ? "hidden" : "visible", configurable: true });
}

async function main() {
  console.log("⏱ «я ещё здесь» на страницах");

  // --- скрипт подключён ко всем страницам сайта ---------------------------
  let served = 0, withScript = 0;
  for (const path of PAGES) {
    const res = await get(path);
    if (res.status !== 200) { check("страница " + path + " отдаётся", false, res.status); continue; }
    served++;
    if (/\/static\/presence\.js\?v=[\w.]+/.test(res.body)) withScript++;
    else check("на " + path + " подключён presence.js", false);
  }
  check("presence.js подключён на всех страницах (" + withScript + "/" + served + ")",
        served === PAGES.length && withScript === served);
  const asset = await get("/static/presence.js");
  check("сам скрипт отдаётся", asset.status === 200 && asset.body.indexOf("LiqScopePresence") > 0,
        asset.status);

  // --- кабинет: отметки идут, интервал слушается --------------------------
  const page = await open("/cabinet");
  const { pings, beacons, win, doc } = page;
  const appeared = await until(() => typeof win.LiqScopePresence === "object", 4000);
  check("скрипт выполнился на странице", appeared >= 0, appeared);

  // «минута» и первая отметка берутся из подмены — иначе тест ждал бы минуту
  check("интервал отметок настраивается тестом",
        win.LiqScopePresence.GAP === TICK && win.LiqScopePresence.FIRST === FIRST,
        win.LiqScopePresence.GAP + "/" + win.LiqScopePresence.FIRST);

  const firstAt = await until(() => pings.length >= 1, 2000);
  check("первая отметка ушла сразу после загрузки", firstAt >= 0 && firstAt < 1000, firstAt);
  check("отметка идёт на /api/visit/ping",
        pings.length > 0 && pings[0].url.indexOf("/api/visit/ping") >= 0,
        pings[0] && pings[0].url);
  check("отметка отправляется POST'ом с cookie",
        pings.length > 0 && pings[0].opts.method === "POST" &&
        pings[0].opts.credentials === "same-origin",
        pings[0] && JSON.stringify(pings[0].opts));
  check("в теле — путь текущей страницы",
        pings.length > 0 && JSON.parse(pings[0].opts.body).path === "/cabinet",
        pings[0] && pings[0].opts.body);

  await until(() => pings.length >= 3, 2000);
  check("отметки повторяются по интервалу", pings.length >= 3, pings.length);
  const gaps = pings.slice(1).map((p, i) => p.at - pings[i].at);
  check("пауза между отметками — как настроено (" + gaps.join(", ") + " мс)",
        gaps.every((g) => g >= TICK - 60 && g <= TICK + 400), gaps.join(","));

  // --- стоп и снова старт -------------------------------------------------
  const stopped = pings.length;
  win.LiqScopePresence.stop();
  await wait(TICK * 2.5);
  check("stop() выключает отметки", pings.length === stopped, pings.length - stopped);
  win.LiqScopePresence.start();
  await until(() => pings.length > stopped, 1000);
  check("start() включает отметки обратно", pings.length > stopped, pings.length - stopped);

  // --- скрытая вкладка молчит --------------------------------------------
  setHidden(win, true);
  doc.dispatchEvent(new win.Event("visibilitychange", { bubbles: true }));
  const before = pings.length;
  await wait(TICK * 3);
  check("скрытая вкладка не пингует", pings.length === before, pings.length - before);
  check("уход со страницы отмечен сразу (sendBeacon)", beacons.length >= 1, beacons.length);
  await until(() => beacons.length > 0 && beacons[0].text.length > 0, 500);
  check("маячок ушёл на тот же адрес и с тем же путём",
        beacons.length > 0 && beacons[0].url.indexOf("/api/visit/ping") >= 0 &&
        JSON.parse(beacons[0].text || "{}").path === "/cabinet",
        beacons.length && (beacons[0].url + " " + beacons[0].text));
  check("маячок — это Blob (sendBeacon сам доставит его при закрытии вкладки)",
        beacons.length > 0 && beacons[0].blob !== null, beacons.length);

  // --- вернулись: отметка сразу ------------------------------------------
  const wasPings = pings.length;
  setHidden(win, false);
  doc.dispatchEvent(new win.Event("visibilitychange", { bubbles: true }));
  const backAt = await until(() => pings.length > wasPings, 500);
  check("вернулись во вкладку — отметка сразу", backAt >= 0 && backAt < 200, backAt);

  // --- другие страницы ----------------------------------------------------
  const login = await open("/login");
  await until(() => login.pings.length >= 2, 3000);
  check("на /login отметки тоже идут",
        login.pings.length >= 2 &&
        login.pings.every((p) => JSON.parse(p.opts.body).path === "/login"),
        login.pings.length);
  check("ошибок в консоли нет", errors.length === 0, errors.slice(0, 3).join(" | "));

  console.log("\nИТОГ: " + ok + " ок, " + fail + " провал(ов)");
  process.exit(fail || errors.length ? 1 : 0);
}

main().catch((e) => { console.error("Тест упал:", e.stack || e.message); process.exit(1); });
