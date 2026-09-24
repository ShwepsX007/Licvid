require("./_dom_env");
/**
 * Кнопка «стереть статистику» в админке — в jsdom, с заглушками сети.
 *
 * Проверяем то, ради чего она и делалась: случайный клик ничего не стирает,
 * подтверждение открывается отдельно, отмена его закрывает, а настоящий
 * запрос уходит только со словом подтверждения и галочкой кэша. После
 * стирания админка перезагружает цифры и честно говорит, что график пуст.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     node tests/admin_wipe_ui.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const ADMIN = {
  id: 1, is_admin: true, display_name: "Админ", first_name: "Админ",
  email: "boss@liqscope.online", email_verified: true, tg_id: 1001,
};

/** Сводка админки: до стирания — с визитами, после — пустая. */
function overview(wiped) {
  return {
    ok: true, users: { total: 5, new_24h: 1 },
    visits: wiped
      ? { today_views: 0, today_uniques: 0, today_bots: 0, days: [], paths: [] }
      : { today_views: 10, today_uniques: 4, today_bots: 3,
          days: [{ day: "2026-09-17", views: 10, uniques: 4, bots: 3 },
                 { day: "2026-09-18", views: 6, uniques: 3, bots: 1 }] },
    ws_clients: 7, bot: { ready: true, username: "LiqScopeBot" },
    health: { live_exchanges: ["binance"] },
    settings: { bot_welcome: "привет", site_notice: "" },
    services: [], ai: {},
  };
}

async function openPage(path, { handler } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));
  const calls = [];
  const dom = await JSDOM.fromURL(ru(URL_BASE + path), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      win.WebSocket = function () {
        return { close() {}, send() {}, addEventListener() {}, readyState: 0 };
      };
      win.fetch = async (url, opts) => {
        const u = String(url);
        calls.push({ url: u, method: (opts && opts.method) || "GET",
                     body: (opts && opts.body) || "" });
        const body = handler ? handler(u, opts, calls) : { ok: true };
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });
  await new Promise((r) => setTimeout(r, 400));
  return { win: dom.window, doc: dom.window.document, calls };
}

function click(win, el) {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
}

(async () => {
  // --- обычная админка: визиты есть, кнопка ждёт подтверждения ------------
  let wiped = false;
  const calls = [];
  const page = await openPage("/admin", {
    handler: (u) => {
      if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN };
      if (u.indexOf("/api/admin/overview") === 0) return overview(wiped);
      if (u.indexOf("/api/admin/users") === 0) return { ok: true, users: [], total: 0 };
      if (u.indexOf("/api/admin/stats") === 0) return { ok: true, stats: {} };
      if (u.indexOf("/api/admin/visits/clear") === 0) {
        calls.push(u);
        wiped = true;
        return { ok: true, deleted: { visits: 10, presence: 4, geo_cache: 3 },
                 before: { visits: 10, presence: 4, geo_cache: 3 }, cache: true,
                 now: { visits: 0, presence: 0, geo_cache: 0 } };
      }
      return { ok: true };
    },
  });
  const { win, doc } = page;
  const btn = doc.getElementById("visits-clear");
  const box = doc.getElementById("visits-wipe");
  const go = doc.getElementById("visits-wipe-go");
  const cancel = doc.getElementById("visits-wipe-cancel");
  const status = doc.getElementById("visits-clear-status");

  check("кнопка стирания есть на странице", !!btn);
  check("подтверждение по умолчанию скрыто", !!box && box.hidden === true);
  check("график визитов нарисован", /bars|bar/.test(doc.getElementById("visit-bars").innerHTML));

  if (!btn || !box || !go) {
    console.log(`\nитог: ${ok} ок, ${fail + 1} ошибок`);
    process.exit(1);
  }

  click(win, btn);
  await new Promise((r) => setTimeout(r, 60));
  check("первый клик открывает подтверждение, а не стирает", box.hidden === false);
  check("до подтверждения запроса на стирание нет",
    !page.calls.some((c) => c.url.indexOf("/api/admin/visits/clear") === 0),
    JSON.stringify(page.calls.map((c) => c.url)));

  click(win, cancel);
  await new Promise((r) => setTimeout(r, 40));
  check("отмена закрывает подтверждение", box.hidden === true);
  check("после отмены галочка кэша снята",
    doc.getElementById("visits-wipe-cache").checked === false);

  click(win, btn);
  await new Promise((r) => setTimeout(r, 40));
  doc.getElementById("visits-wipe-cache").checked = true;
  click(win, go);
  await new Promise((r) => setTimeout(r, 250));

  const call = page.calls.filter((c) => c.url.indexOf("/api/admin/visits/clear") === 0).pop();
  check("подтверждение уходит POST-ом на /api/admin/visits/clear",
    !!call && call.method === "POST", call && call.method);
  let payload = {};
  try { payload = JSON.parse((call && call.body) || "{}"); } catch (e) { /* пусто */ }
  check("в запросе слово подтверждения и галочка кэша",
    payload.confirm === "clear" && payload.cache === true, call && call.body);
  check("статус рассказывает, сколько стёрли",
    /10/.test(status.textContent) && /4/.test(status.textContent) &&
    /3/.test(status.textContent), status.textContent);
  check("после стирания подтверждение закрылось", box.hidden === true);
  const reloads = page.calls.filter((c) => c.url.indexOf("/api/admin/overview") === 0).length;
  check("цифры перезагружены с сервера (сводка запрошена дважды)", reloads >= 2, reloads);
  check("пустой график объясняет, что визитов нет",
    /переходов за это окно нет|Пока пусто/.test(doc.getElementById("visit-bars").textContent),
    doc.getElementById("visit-bars").textContent);

  // --- английский язык: та же кнопка, те же пояснения ---------------------
  win.LiqScopeI18n.set("en");
  await new Promise((r) => setTimeout(r, 120));
  check("на английском пустой график тоже объясняется",
    /no page views/i.test(doc.getElementById("visit-bars").textContent),
    doc.getElementById("visit-bars").textContent);
  check("на английском подпись кнопки переведена",
    /Clear statistics/.test(btn.textContent), btn.textContent);
  click(win, btn);
  await new Promise((r) => setTimeout(r, 60));
  check("на английском подтверждение открывается и переведено",
    box.hidden === false && /Clear all visit statistics/i.test(box.textContent),
    box.textContent.slice(0, 120));
  check("ошибок страницы нет", errors.length === 0, errors.join(" | "));
  await win.close();

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("  FAIL исключение: " + e.message); process.exit(1); });
