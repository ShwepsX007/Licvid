/**
 * Админка бота на сайте в jsdom: страница /admin собирает панель управления
 * ботом и шлёт те же команды, что раньше жили только в кнопках Telegram.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom
 *     node tests/bot_admin_ui.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");

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

const BOT_SNAP = {
  ok: true,
  bot: { present: true, enabled: true, running: true, task_alive: true,
         username: "LiqScopeBot", line: "🤖 Бот: опрос ок (3 с) · апдейт 1 с назад" },
  channels: {
    ru: { id: "-100111", title: "LiqScopeRUS", source: "bot",
          source_label: "выбрано в боте или на сайте" },
    en: { id: "-100222", title: "LiqScopeEng", source: "bot",
          source_label: "выбрано в боте или на сайте" },
    route: "🇷🇺 LiqScopeRUS\n🇬🇧 LiqScopeEng", warn: "",
  },
  probes: {
    ru: { id: "-100111", title: "LiqScopeRUS", status: "administrator", can_post: true, note: "" },
    en: { id: "-100222", title: "LiqScopeEng", status: "administrator", can_post: false,
          note: "бот админ, но «Публикация сообщений» выключена" },
  },
  review: true,
  templates: { heads: 3, photos: 2, using_default_heads: false, using_default_photos: false },
  ai: { enabled: true, providers: [{ name: "gemini" }, { name: "groq" }],
        last: { ok: true, provider: "gemini", ms: 812 } },
  health: {
    live: 1, total: 3, events_total: 120, ws_clients: 7,
    bot_line: "🤖 Бот: опрос ок (3 с) · апдейт 1 с назад",
    rows: [
      { name: "binance", state: "ok", events: 120, since: 4, error: "", attempts: 1, respawns: 0 },
      { name: "bybit", state: "down", events: 0, since: null, error: "timeout", attempts: 3, respawns: 0 },
      { name: "okx", state: "dead", events: 0, since: null, error: "", attempts: 9, respawns: 2 },
    ],
  },
  daily: {
    last: { day: "2026-09-16", created: 1.0,
            facts: { liq_total_usd: 1234567, liq_count: 640 },
            published: { ru: { ok: true }, en: { ok: true } } },
    schedule: { enabled: true, hour: 22, minute: 0, jitter_min: 10, tz: 10800,
                tz_hours: 3, env_locked: {} },
    store_error: "",
  },
};

/** Страница с заглушками сети: собираем запросы, отвечаем по сценарию. */
async function openPage(path, { me = {}, handler, env = {} } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));
  const calls = [];
  const dom = await JSDOM.fromURL(URL_BASE + path, {
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
      win.confirm = () => true;
      win.open = () => ({ closed: false });
      win.fetch = async (url, opts) => {
        const u = String(url);
        const body = (opts && opts.body) || "";
        calls.push({ url: u, method: (opts && opts.method) || "GET", body: body });
        if (handler) return { ok: true, status: 200, json: async () => handler(u, opts, calls) };
        if (u.indexOf("/api/auth/me") === 0) {
          return { ok: true, status: 200, json: async () => Object.assign({ ok: true, user: ADMIN }, me) };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      };
    },
  });
  await new Promise((r) => setTimeout(r, 400));
  return { win: dom.window, doc: dom.window.document, calls };
}

function adminHandler(u, opts, calls) {
  if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN };
  if (u.indexOf("/api/admin/overview") === 0) {
    return { ok: true, users: { total: 5, new_24h: 1, active_24h: 3 },
             visits: { today_views: 10, today_uniques: 4, days: [] },
             ws_clients: 7, bot: { ready: true, username: "LiqScopeBot" },
             health: { live_exchanges: ["binance"] },
             settings: { bot_welcome: "привет", site_notice: "" },
             services: [], ai: {} };
  }
  if (u.indexOf("/api/admin/users") === 0) return { ok: true, users: [], total: 0 };
  if (u.indexOf("/api/admin/stats") === 0) return { ok: true, stats: {} };
  if (u.indexOf("/api/admin/digest") === 0) {
    return { ok: true, heads: [], photos: [], using_default_heads: true,
             using_default_photos: true };
  }
  if (u.indexOf("/api/admin/bot") === 0) {
    const last = calls[calls.length - 1];
    const body = last && last.body ? JSON.parse(last.body) : {};
    if (u.indexOf("/api/admin/bot/review") === 0) {
      return { ok: true, review: body.on, message: body.on ? "Контроль включён" : "Контроль выключен" };
    }
    if (u.indexOf("/api/admin/bot/publish") === 0) {
      return { ok: true, kind: body.kind, draft: false,
               message: body.kind === "daily"
                 ? "Дневной дайджест за 2026-09-17 отправлен: 🇷🇺 RU, 🇬🇧 EN"
                 : "Сводка ушла в каналы. 🇷🇺 LiqScopeRUS · 🇬🇧 LiqScopeEng" };
    }
    if (u.indexOf("/api/admin/bot/channels") === 0) {
      return { ok: true, channels: BOT_SNAP.channels, probes: BOT_SNAP.probes,
               note: "Роли каналов сверены по названиям." };
    }
    if (u.indexOf("/api/admin/bot/ai-check") === 0) {
      return { ok: true, head: "Ночь на рынке выдалась горячей — снесло $120M",
               note: "ИИ: gemini · 812 мс", lang: "ru" };
    }
    return BOT_SNAP;
  }
  if (u.indexOf("/api/digest/settings") === 0) {
    const last = calls[calls.length - 1];
    const body = last && last.body ? JSON.parse(last.body) : {};
    return { ok: true, saved: body, note: "Время выпуска сохранено.",
             schedule: { hour: Number(body.hour), minute: Number(body.minute),
                         jitter_min: Number(body.jitter_min), enabled: !!body.enabled,
                         env_locked: {} } };
  }
  return { ok: true };
}

function click(win, el) {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
}

(async () => {
  console.log("админка бота на сайте: /admin в jsdom");
  const page = await openPage("/admin", { handler: adminHandler });
  const { win, doc, calls } = page;
  await new Promise((r) => setTimeout(r, 250));

  check("карточка управления ботом есть", !!doc.querySelector("#bot-admin"));
  check("строка состояния бота заполнена",
    /опрос ок/.test(doc.querySelector("#bot-line").textContent),
    doc.querySelector("#bot-line").textContent);

  check("id русского канала подставлен",
    doc.querySelector("#bot-ru").value === "-100111",
    doc.querySelector("#bot-ru").value);
  check("id английского канала подставлен",
    doc.querySelector("#bot-en").value === "-100222",
    doc.querySelector("#bot-en").value);
  const probes = doc.querySelector("#bot-ch-probe").textContent;
  check("по каналам показаны права бота", /публикация разрешена/.test(probes), probes);
  check("и предупреждение, где посты не уйдут",
    /Публикация сообщений/.test(probes), probes);

  check("контроль постов показан включённым",
    doc.querySelector("#bot-review").checked === true);
  check("таблица бирж заполнена",
    doc.querySelectorAll("#bot-health tr").length === 3,
    doc.querySelectorAll("#bot-health tr").length);
  const health = doc.querySelector("#bot-health").textContent;
  check("мёртвый слушатель помечен", /слушатель не запущен/.test(health), health);
  check("сводка по биржам с WS", /В эфире 1\/3/.test(doc.querySelector("#bot-health-line").textContent),
    doc.querySelector("#bot-health-line").textContent);
  check("ИИ показан с провайдером",
    /gemini/.test(doc.querySelector("#bot-ai-line").textContent),
    doc.querySelector("#bot-ai-line").textContent);
  check("последний дайджест виден",
    /2026-09-16/.test(doc.querySelector("#bot-daily-line").textContent) &&
    /🇷🇺 RU/.test(doc.querySelector("#bot-daily-line").textContent) &&
    /🇬🇧 EN/.test(doc.querySelector("#bot-daily-line").textContent),
    doc.querySelector("#bot-daily-line").textContent);

  check("час выпуска подставлен", doc.querySelector("#dig-hour").value === "22",
    doc.querySelector("#dig-hour").value);
  check("минуты выпуска подставлены", doc.querySelector("#dig-min").value === "0",
    doc.querySelector("#dig-min").value);
  check("замороженных настроек нет — подсказка обычная",
    /Разброс/.test(doc.querySelector("#dig-note").textContent),
    doc.querySelector("#dig-note").textContent);

  // --- действия ---------------------------------------------------------
  const before = calls.length;
  click(win, doc.querySelector("#bot-post"));
  await new Promise((r) => setTimeout(r, 120));
  const publish = calls.slice(before).find((c) => c.url.indexOf("/api/admin/bot/publish") === 0);
  check("кнопка «Сводка в канал» шлёт публикацию",
    !!publish && JSON.parse(publish.body).kind === "channel",
    publish && publish.body);
  check("и показывает ответ",
    /Сводка ушла/.test(doc.querySelector("#bot-post-status").textContent),
    doc.querySelector("#bot-post-status").textContent);

  const beforeDaily = calls.length;
  click(win, doc.querySelector("#bot-daily"));
  await new Promise((r) => setTimeout(r, 120));
  const daily = calls.slice(beforeDaily).find((c) => c.url.indexOf("/api/admin/bot/publish") === 0);
  check("кнопка «Дайджест за сутки» шлёт сборку выпуска",
    !!daily && JSON.parse(daily.body).kind === "daily", daily && daily.body);
  check("и отчитывается, куда ушёл",
    /RU, 🇬🇧 EN/.test(doc.querySelector("#bot-post-status").textContent),
    doc.querySelector("#bot-post-status").textContent);

  const beforeAi = calls.length;
  click(win, doc.querySelector("#bot-ai-check"));
  await new Promise((r) => setTimeout(r, 120));
  check("проверка ИИ дёргает сервер",
    calls.slice(beforeAi).some((c) => c.url.indexOf("/api/admin/bot/ai-check") === 0));
  check("и печатает шапку от ИИ",
    /120M/.test(doc.querySelector("#bot-ai-head").textContent),
    doc.querySelector("#bot-ai-head").textContent);

  doc.querySelector("#dig-hour").value = "21";
  doc.querySelector("#dig-min").value = "45";
  doc.querySelector("#dig-jitter").value = "5";
  doc.querySelector("#dig-enabled").checked = false;
  const beforeSave = calls.length;
  click(win, doc.querySelector("#dig-save"));
  await new Promise((r) => setTimeout(r, 120));
  const saved = calls.slice(beforeSave).find((c) => c.url.indexOf("/api/digest/settings") === 0);
  const savedBody = saved ? JSON.parse(saved.body) : {};
  check("сохранение времени уходит с новыми значениями",
    savedBody.hour === "21" && savedBody.minute === "45" &&
    savedBody.jitter_min === "5" && savedBody.enabled === false,
    saved && saved.body);
  check("и подтверждается на странице",
    /21:45/.test(doc.querySelector("#dig-status").textContent),
    doc.querySelector("#dig-status").textContent);

  const beforeVerify = calls.length;
  click(win, doc.querySelector("#bot-ch-verify"));
  await new Promise((r) => setTimeout(r, 150));
  check("сверка ролей зовёт сервер",
    calls.slice(beforeVerify).some((c) => c.url.indexOf("/api/admin/bot/channels/verify") === 0));
  check("после сверки виден список каналов с правами",
    /публикация разрешена/.test(doc.querySelector("#bot-ch-probe").textContent),
    doc.querySelector("#bot-ch-probe").textContent);

  check("ошибок страницы нет", errors.length === 0, errors.join(" | "));
  await win.close();

  // --- бот выключен: панель объясняет, а не молчит -----------------------
  const off = await openPage("/admin", {
    handler: (u) => {
      if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN };
      if (u.indexOf("/api/admin/overview") === 0) {
        return { ok: true, users: { total: 1 }, visits: { days: [] }, bot: {},
                 health: { live_exchanges: [] }, settings: {}, services: [] };
      }
      if (u.indexOf("/api/admin/users") === 0) return { ok: true, users: [] };
      if (u.indexOf("/api/admin/stats") === 0) return { ok: true, stats: {} };
      if (u.indexOf("/api/admin/digest") === 0) return { ok: true, heads: [], photos: [] };
      if (u.indexOf("/api/admin/bot") === 0) {
        return { ok: true,
                 bot: { present: false, enabled: false, running: false, username: "",
                        line: "Бот не подключён" },
                 channels: { ru: {}, en: {}, route: "", warn: "" },
                 review: false, templates: {}, ai: {}, health: { rows: [] },
                 daily: { schedule: { hour: 22, minute: 0, jitter_min: 10,
                                      enabled: false, env_locked: { enabled: "LIQSCOPE_DIGEST_SCHED" } } } };
      }
      return { ok: true };
    },
  });
  await new Promise((r) => setTimeout(r, 250));
  check("без бота панель честно пишет, что он не подключён",
    /не подключён/i.test(off.doc.querySelector("#bot-line").textContent),
    off.doc.querySelector("#bot-line").textContent);
  check("заморозка окружением объяснена",
    /LIQSCOPE_DIGEST_SCHED/.test(off.doc.querySelector("#dig-note").textContent),
    off.doc.querySelector("#dig-note").textContent);
  await off.win.close();

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("  FAIL исключение: " + e.message); process.exit(1); });
