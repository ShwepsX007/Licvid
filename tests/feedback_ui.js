/**
 * 💬 Обратная связь в кабинете и админке + 🗂 сворачиваемые разделы админки.
 *
 * Запуск (сервер уже на 127.0.0.1:8011):
 *     NODE_PATH=./node_modules node tests/feedback_ui.js [http://127.0.0.1:8011]
 *
 * Сеть подменена заглушками, поэтому проверка не зависит от того, что лежит в
 * базе демо-стенда: важно, что кнопка «По всем вопросам» (маленькая, у правого
 * края строки действий) раскрывает диалог — по умолчанию он свёрнут; сообщение
 * уходит, ответ админа виден в кабинете, а разделы админки сворачиваются и
 * помнят своё состояние.
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8011";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/** Общая обвязка страницы: свои ответы API и запись запросов. */
async function openPage(path, routes, { stored = null } = {}) {
  const reqs = [];
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
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true }); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
        dispatchEvent() { return false; } });
      try { win.localStorage.clear(); } catch (e) {}
      if (stored) {
        try { win.localStorage.setItem("liqscope.admin.folds", JSON.stringify(stored)); } catch (e) {}
      }
      win.confirm = () => true;
      win.WebSocket = require("ws");
      win.open = () => ({ closed: false });
      win.fetch = async (url, opts) => {
        const u = String(url);
        const method = ((opts || {}).method || "GET").toUpperCase();
        let body = null;
        try { body = (opts || {}).body ? JSON.parse((opts || {}).body) : null; } catch (e) { body = null; }
        reqs.push({ url: u, method, body });
        const data = routes(u, method, body);
        return { ok: true, status: 200, json: async () => data };
      };
    },
  });
  await wait(1200);
  return { win: dom.window, doc: dom.window.document, reqs };
}

const ME = { id: 1, first_name: "Вася", username: "vasya", email: "vasya@example.com" };
const ADMIN = { id: 2, first_name: "Босс", is_admin: true, email: "boss@liqscope.online" };

/** Кабинет: диалог с админом. */
function cabinetRoutes(state) {
  return (u, method, body) => {
    if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ME, verified: true };
    if (u.indexOf("/api/feedback/unread") === 0) return { ok: true, unread: state.unread };
    if (u.indexOf("/api/feedback") === 0) {
      if (method === "POST") {
        state.messages.push({ id: state.messages.length + 1, text: body.text, at: 1789000000,
                              mine: true, admin: false });
        state.lastPost = body.text;
        return { ok: true, message: state.messages[state.messages.length - 1], unread: state.unread };
      }
      return { ok: true, me: ME, messages: state.messages, unread: state.unread, max_len: 2000 };
    }
    if (u.indexOf("/api/account/services") === 0) return { ok: true, services: [] };
    return { ok: false };
  };
}

/** Админка: список диалогов и переписка. */
function adminRoutes(state) {
  return (u, method) => {
    if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: ADMIN, verified: true };
    if (u.indexOf("/api/admin/feedback/") === 0) {
      const id = Number(u.split("/api/admin/feedback/")[1]);
      const t = state.threads.filter((x) => x.id === id)[0];
      return { ok: true, thread: t, messages: state.messages[id] || [], unread: 0 };
    }
    if (u.indexOf("/api/admin/feedback") === 0) {
      return { ok: true, threads: state.threads, unread: 2, me: ADMIN, max_len: 2000 };
    }
    if (u.indexOf("/api/admin/overview") === 0) {
      return { ok: true, users: { total: 3, new_24h: 0 },
               visits: { today_views: 1, today_uniques: 1, today_bots: 0, days: [] },
               ws_clients: 0, bot: { ready: false, username: "" },
               health: { live_exchanges: [] }, services: [], settings: {} };
    }
    if (u.indexOf("/api/admin/users") === 0) return { ok: true, users: [], total: 0 };
    if (u.indexOf("/api/admin/stats") === 0) return { ok: true, stats: {} };
    return { ok: false };
  };
}

async function main() {
  console.log("💬 обратная связь в кабинете");
  const cabState = {
    unread: 1,
    lastPost: "",
    messages: [
      { id: 1, text: "Не приходят алерты по CVD", at: 1789000000, mine: true, admin: false },
      { id: 2, text: "Посмотрел, починил — проверьте", at: 1789000600, mine: false, admin: true },
    ],
  };
  const cab = await openPage("/cabinet", cabinetRoutes(cabState));
  const cdoc = cab.doc;
  const jump = cdoc.querySelector("#fb-jump");
  check("в кабинете есть кнопка «По всем вопросам»",
        !!jump && jump.textContent.indexOf("По всем вопросам") >= 0,
        jump && jump.textContent);
  check("кнопка ведёт к диалогу", jump && jump.getAttribute("href") === "#fb-card");
  const card = cdoc.querySelector("#fb-card");
  check("карточка диалога есть в кабинете", !!card);
  // Диалог не мозолит глаза: свёрнут, пока его не открыли кнопкой.
  check("диалог по умолчанию свёрнут", !!card && card.open === false,
        card && card.open);
  check("кнопка «По всем вопросам» — маленькая",
        !!jump && String(jump.className).indexOf("btn-small") >= 0,
        jump && jump.className);
  const acts = cdoc.querySelectorAll(".row-actions")[0];
  check("кнопка стоит последней в строке действий (правый край)",
        !!acts && acts.lastElementChild === jump,
        acts && acts.lastElementChild && acts.lastElementChild.id);
  const cssTxt = await new Promise((res, rej) => {
    require("http").get(URL_BASE + "/static/account.css", (r) => {
      let b = ""; r.on("data", (c) => (b += c)); r.on("end", () => res(b));
    }).on("error", rej);
  });
  check("CSS: кнопка прижата к правому краю строки",
        /#fb-jump\s*\{[^}]*margin-left:\s*auto/.test(cssTxt));

  jump.dispatchEvent(new cab.win.MouseEvent("click", { bubbles: true }));
  await wait(80);
  check("нажатие на кнопку раскрывает диалог", !!card && card.open === true,
        card && card.open);
  check("кнопка не уводит со страницы (href остался)", 
        jump.getAttribute("href") === "#fb-card");

  const bubbles = cdoc.querySelectorAll("#fb-thread .fb-bubble");
  check("переписка показана обоими голосами", bubbles.length === 2, bubbles.length);
  check("ответ админа отличается от своего сообщения",
        cdoc.querySelectorAll("#fb-thread .fb-msg.mine").length === 1 &&
        cdoc.querySelector("#fb-thread").textContent.indexOf("Посмотрел, починил") >= 0);
  check("непрочитанный ответ виден счётчиком",
        (cdoc.querySelector("#fb-dot") || {}).textContent === "1",
        (cdoc.querySelector("#fb-dot") || {}).textContent);

  const ta = cdoc.querySelector("#fb-text");
  ta.value = "И ещё: не хватает фильтра по биржам";
  cdoc.querySelector("#fb-send").dispatchEvent(new cab.win.MouseEvent("click", { bubbles: true }));
  await wait(200);
  check("сообщение ушло в API",
        cabState.lastPost === "И ещё: не хватает фильтра по биржам", cabState.lastPost);
  check("после отправки поле очищено", ta.value === "");
  check("панель подтвердила отправку",
        cdoc.querySelector("#fb-status").textContent.indexOf("отправлено") >= 0,
        cdoc.querySelector("#fb-status").textContent);
  check("новое сообщение появилось в переписке",
        cdoc.querySelectorAll("#fb-thread .fb-msg").length === 3);

  console.log("\n🗂 сворачиваемые разделы админки");
  const state = { threads: [], messages: {} };
  const admin1 = await openPage("/admin", adminRoutes(state));
  const adoc = admin1.doc;
  const folds = Array.prototype.slice.call(adoc.querySelectorAll("details.fold"));
  check("карточки админки стали сворачиваемыми блоками", folds.length >= 6, folds.length);
  check("по умолчанию разделы свёрнуты — админка компактная",
        folds.every((d) => !d.open));
  check("заголовок раздела кликается (summary с h3)",
        folds.every((d) => d.querySelector("summary > h3")));
  check("у разделов есть подсказки под заголовком",
        adoc.querySelectorAll("details.fold .fold-hint").length >= 6);
  check("цифры сверху остались на виду", !!adoc.querySelector("#st-users"));

  adoc.querySelector("#folds-open").dispatchEvent(new admin1.win.MouseEvent("click", { bubbles: true }));
  await wait(80);
  check("«Развернуть всё» раскрывает все разделы",
        Array.prototype.every.call(adoc.querySelectorAll("details.fold"), (d) => d.open));
  adoc.querySelector("#folds-close").dispatchEvent(new admin1.win.MouseEvent("click", { bubbles: true }));
  await wait(80);
  check("«Свернуть всё» прячет содержимое",
        Array.prototype.every.call(adoc.querySelectorAll("details.fold"), (d) => !d.open));

  const saved = JSON.parse(admin1.win.localStorage.getItem("liqscope.admin.folds") || "{}");
  check("состояние разделов запоминается", Object.keys(saved).length >= 6, JSON.stringify(saved));

  // Админ раскрыл «Рекламные посты» — после перезагрузки они раскрыты
  const again = await openPage("/admin", adminRoutes({ threads: [], messages: {} }),
                               { stored: { "ads-panel": true } });
  const adsFold = again.doc.querySelector("#ads-panel");
  check("раздел, раскрытый в прошлый раз, остаётся раскрытым", adsFold && adsFold.open === true);
  check("остальные разделы при этом свёрнуты",
        Array.prototype.filter.call(again.doc.querySelectorAll("details.fold"), (d) => d.open).length === 1);

  console.log("\n💬 диалоги в админке");
  const talk = {
    threads: [{ id: 5, name: "Вася", username: "vasya", email: "vasya@example.com",
                unread: 2, total: 2, last_text: "Не приходят алерты", last_admin: false,
                updated_at: 1789000600, created_at: 1789000000, user_id: 1, status: "open" }],
    messages: {},
  };
  talk.messages[5] = [
    { id: 1, text: "Не приходят алерты по CVD", at: 1789000000, mine: false, admin: false },
    { id: 2, text: "И по OI тоже пусто", at: 1789000300, mine: false, admin: false },
  ];
  const adm = await openPage("/admin", adminRoutes(talk));
  const doc = adm.doc;
  const panel = doc.querySelector("#fb-admin");
  check("раздел обратной связи есть в админке", !!panel);
  check("в заголовке видно, сколько ждёт ответа",
        (doc.querySelector("#fb-admin-badge") || {}).textContent === "2",
        (doc.querySelector("#fb-admin-badge") || {}).textContent);
  const items = doc.querySelectorAll("#fb-list .fb-item");
  check("список диалогов показан", items.length === 1, items.length);
  check("непрочитанный диалог помечен",
        !!doc.querySelector("#fb-list .fb-item.unread") &&
        doc.querySelector("#fb-list").textContent.indexOf("Вася") >= 0);

  items[0].dispatchEvent(new adm.win.MouseEvent("click", { bubbles: true }));
  await wait(200);
  check("открылась переписка выбранного диалога",
        doc.querySelectorAll("#fb-admin-thread .fb-bubble").length === 2);
  check("видно, кто пишет: имя, @имя и почта",
        doc.querySelector("#fb-conv-head").textContent.indexOf("Вася") >= 0 &&
        doc.querySelector("#fb-conv-head").textContent.indexOf("vasya@example.com") >= 0);

  const reply = doc.querySelector("#fb-admin-text");
  reply.value = "Посмотрел, починил — проверьте";
  doc.querySelector("#fb-admin-send").dispatchEvent(new adm.win.MouseEvent("click", { bubbles: true }));
  await wait(200);
  const posted = adm.reqs.filter((r) => r.method === "POST" &&
    r.url.indexOf("/api/admin/feedback/5") === 0).pop();
  check("ответ уходит в API диалога", !!posted && posted.body.text.indexOf("починил") >= 0,
        posted && JSON.stringify(posted.body));
  check("панель подтвердила ответ",
        doc.querySelector("#fb-admin-status").textContent.indexOf("ушёл") >= 0,
        doc.querySelector("#fb-admin-status").textContent);

  console.log("\nошибок в консоли: " + errors.length);
  errors.slice(0, 4).forEach((e) => console.log("  ! " + e));
  console.log(`ИТОГ: ${ok} ок, ${fail} провал(ов)`);
  process.exit(fail || errors.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
