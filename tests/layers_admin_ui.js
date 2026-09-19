/**
 * ☰ Пробный доступ к слоям в админке — в jsdom, с заглушками сети.
 *
 * Проверяем карточку лимита: админка показывает, сколько минут сейчас даётся
 * гостю, откуда взялось значение (настройка сайта или переменная окружения) и
 * сколько гостей знакомится со слоями. Правка лимита уходит на сервер телом
 * {"minutes": N}, ноль выключает ограничение целиком, а кнопка «сбросить
 * таймер всем» уходит отдельным запросом.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     NODE_PATH=/tmp/smoke/node_modules node tests/layers_admin_ui.js [http://127.0.0.1:8000]
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
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const ADMIN = {
  id: 1, is_admin: true, display_name: "Админ", first_name: "Админ",
  email: "boss@liqscope.online", email_verified: true, tg_id: 1001,
};

/** Ответ настройки слоёв: как его отдаёт /api/admin/layers/settings. */
function layers({ minutes = 30, locked = false, active = 2, expired = 1,
                  total = 3 } = {}) {
  return {
    ok: true, minutes, limit_sec: minutes * 60, default_min: 30, min: 0,
    max: 1440, source: locked ? "env" : "site", locked,
    env_var: "LIQSCOPE_LAYERS_TRIAL_MIN", setting: "layers_trial_min",
    stats: { total, active, expired, limit_sec: minutes * 60 },
  };
}

function overview() {
  return {
    ok: true, users: { total: 5, new_24h: 1 },
    visits: { today_views: 10, today_uniques: 4, today_bots: 3, days: [], paths: [] },
    ws_clients: 1, bot: { ready: false, username: "" },
    health: { live_exchanges: [] },
    settings: { bot_welcome: "", site_notice: "" },
    services: [], ai: {},
  };
}

async function openAdmin(handler) {
  const calls = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
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
        const method = ((opts || {}).method || "GET").toUpperCase();
        calls.push({ url: u, method, body: (opts || {}).body || "" });
        const body = handler(u, method, opts, calls) || { ok: false };
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });
  await wait(600);
  return { win: dom.window, doc: dom.window.document, calls };
}

function click(win, el) {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
}

(async () => {
  console.log("☰ пробный доступ к слоям в админке");

  // --- обычная настройка: лимит из админки --------------------------------
  let state = layers({ minutes: 30 });
  const page = await openAdmin((u, method, opts) => {
    if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN };
    if (u.indexOf("/api/admin/overview") === 0) return overview();
    if (u.indexOf("/api/admin/users") === 0) return { ok: true, users: [], total: 0 };
    if (u.indexOf("/api/admin/stats") === 0) return { ok: true, stats: {} };
    if (u.indexOf("/api/admin/bot") === 0) return { ok: false };
    if (u.indexOf("/api/admin/geo") === 0) return { ok: true, periods: [] };
    if (u.indexOf("/api/admin/layers/settings") === 0) {
      if (method === "POST") {
        const body = JSON.parse((opts && opts.body) || "{}");
        const minutes = Number(body.minutes || 0);
        state = layers({ minutes: minutes, active: minutes ? 2 : 0, expired: 0 });
        state.saved = minutes;
        state.note = minutes ? "Гостям выдаётся " + minutes + " мин." : "выключено";
      }
      return state;
    }
    if (u.indexOf("/api/admin/layers/reset") === 0) {
      state = layers({ minutes: state.minutes, active: 0, expired: 0, total: 0 });
      state.reset = 3;
      return state;
    }
    return { ok: false };
  });
  const { doc, win, calls } = page;

  const card = doc.getElementById("layers-card");
  check("карточка лимита есть в админке", !!card);
  const head = card ? card.querySelector("h3").textContent : "";
  check("заголовок карточки — про пробный доступ к слоям",
        /Пробный доступ к слоям/.test(head), head);
  check("карточка объясняет, что такое 0",
        /0 — слои открыты всем/.test(card ? card.textContent : ""),
        card && card.querySelector(".lead").textContent);
  const input = doc.getElementById("layers-min");
  check("в поле стоит текущий лимит (30 мин)",
        !!input && Number(input.value) === 30, input && input.value);
  check("админка запросила настройку у сервера",
        calls.some((c) => c.url.indexOf("/api/admin/layers/settings") === 0 &&
                          c.method === "GET"));
  const stats = doc.getElementById("layers-stats");
  check("видно воронку: сколько знакомятся и сколько упёрлись",
        /2/.test(stats.textContent) && /1/.test(stats.textContent) &&
        /3/.test(stats.textContent), stats.textContent);
  check("админу сказано, что правка действует сразу",
        /Действует сразу/.test(doc.getElementById("layers-note").textContent),
        doc.getElementById("layers-note").textContent);
  check("поле не заблокировано, когда значение правит сайт", !input.disabled);

  // --- сохраняем новый лимит ----------------------------------------------
  input.value = "45";
  click(win, doc.getElementById("layers-save"));
  await wait(200);
  const saveCall = calls.filter((c) => c.url.indexOf("/api/admin/layers/settings") === 0 &&
                                        c.method === "POST").pop();
  check("правка лимита уходит на сервер числом минут",
        !!saveCall && /"minutes":45/.test(String(saveCall.body)),
        saveCall && saveCall.body);
  check("статус подтверждает сохранение",
        /45 мин/.test(doc.getElementById("layers-status").textContent),
        doc.getElementById("layers-status").textContent);
  check("в поле остался новый лимит", Number(input.value) === 45, input.value);

  // --- ноль выключает ограничение -----------------------------------------
  input.value = "0";
  click(win, doc.getElementById("layers-save"));
  await wait(200);
  check("ноль — это «ограничение выключено», а не «0 минут»",
        /выключ/i.test(doc.getElementById("layers-status").textContent),
        doc.getElementById("layers-status").textContent);
  check("в поле видно 0", Number(input.value) === 0, input.value);

  // --- сброс таймера всем --------------------------------------------------
  click(win, doc.getElementById("layers-reset"));
  await wait(200);
  const resetCall = calls.filter((c) => c.url.indexOf("/api/admin/layers/reset") === 0).pop();
  check("сброс таймера уходит отдельным запросом на всех гостей",
        !!resetCall && resetCall.method === "POST" &&
        /"all":true/.test(String(resetCall.body)),
        resetCall && resetCall.body);
  check("статус говорит, скольким гостям сбросили таймер",
        /3/.test(doc.getElementById("layers-status").textContent),
        doc.getElementById("layers-status").textContent);

  // --- лимит задан переменной окружения ------------------------------------
  const locked = await openAdmin((u, method) => {
    if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN };
    if (u.indexOf("/api/admin/overview") === 0) return overview();
    if (u.indexOf("/api/admin/users") === 0) return { ok: true, users: [], total: 0 };
    if (u.indexOf("/api/admin/stats") === 0) return { ok: true, stats: {} };
    if (u.indexOf("/api/admin/bot") === 0) return { ok: false };
    if (u.indexOf("/api/admin/geo") === 0) return { ok: true, periods: [] };
    if (u.indexOf("/api/admin/layers/settings") === 0) {
      return layers({ minutes: 15, locked: true, active: 0, expired: 0, total: 0 });
    }
    return { ok: false };
  });
  const ldoc = locked.doc;
  check("при переменной окружения поле и кнопка выключены",
        ldoc.getElementById("layers-min").disabled &&
        ldoc.getElementById("layers-save").disabled);
  check("и написано, какой переменной значение задано",
        /LIQSCOPE_LAYERS_TRIAL_MIN/.test(ldoc.getElementById("layers-note").textContent),
        ldoc.getElementById("layers-note").textContent);
  check("значение из окружения показано (15 мин)",
        Number(ldoc.getElementById("layers-min").value) === 15,
        ldoc.getElementById("layers-min").value);

  console.log("\nошибок в консоли: " + errors.length);
  errors.slice(0, 6).forEach((e) => console.log("   ! " + e));
  console.log("ИТОГ: " + ok + " ок, " + fail + " провал(ов)");
  process.exit(fail || errors.length ? 1 : 0);
})().catch((e) => { console.error("Тест упал:", e.stack || e.message); process.exit(1); });
