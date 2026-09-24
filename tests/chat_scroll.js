/**
 * 💬 Чат: вход в раздел всегда показывает последние записи.
 *
 * Раньше прокрутка ставилась до ответа сервера — по пустому списку, поэтому
 * при входе в чат человек листал всю историю руками. Теперь контейнер,
 * который только что открыли или впервые наполнили, прыгает вниз, а если
 * человек сам ушёл вверх читать — новые сообщения его не дёргают.
 *
 * Страница берётся с живого сервера, API чата подменён моками; высоты
 * контейнеров задаются заглушками, потому что в jsdom нет раскладки.
 *
 * Запуск (сервер на 127.0.0.1:8000):
 *     NODE_PATH=./node_modules node tests/chat_scroll.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const HISTORY = 40;                     // длинная история, которую надо «промотать»

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function history() {
  const now = Math.floor(Date.now() / 1000);
  const out = [];
  for (let i = HISTORY; i >= 1; i--) {
    out.push({
      id: i, user_id: 1, name: "Tester", guest: false, admin: false,
      text: "сообщение " + i, ts: now - i * 60,
    });
  }
  return out;
}

function mockFetch(win, calls) {
  const msgs = history();
  win.fetch = async (url, opts) => {
    const u = String(url);
    const m = (opts && opts.method) || "GET";
    calls.push(u);
    const json = (obj) => ({ ok: true, status: 200, json: async () => obj });
    if (u.indexOf("/api/terminal/chat/me") === 0)
      return json({ ok: true, user: { id: 1, name: "Tester", is_admin: false }, can_write: true });
    if (u.indexOf("/api/terminal/chat/ping") === 0)
      return json({ ok: true, online: 1 });
    if (u.indexOf("/api/terminal/chat/online") === 0)
      return json({ ok: true, count: 1, ids: [1] });
    if (u.indexOf("/api/terminal/chat/participants") === 0)
      return json({ ok: true, participants: [], now: 0 });
    if (u.indexOf("/api/terminal/chat?") === 0 || u === "/api/terminal/chat") {
      if (m === "POST") return json({ ok: true, message: msgs[msgs.length - 1] });
      if (u.indexOf("after_id=") !== -1) return json({ ok: true, messages: [], now: 0 });
      return json({ ok: true, messages: [], now: 0, keep_days: 3 });   // «Общий» пуст
    }
    if (u.indexOf("/api/chat/support/threads") === 0)
      return json({ ok: true, threads: [], now: 0 });
    if (u.indexOf("/api/chat/support") === 0) {
      if (m === "POST") return json({ ok: true, message: msgs[msgs.length - 1] });
      if (u.indexOf("after_id=") !== -1) return json({ ok: true, messages: [], now: 0 });
      return json({ ok: true, messages: msgs, now: 0 });
    }
    if (u.indexOf("/api/chat/services") === 0)
      return json({ ok: true, messages: [], now: 0 });
    if (u.indexOf("/api/chat/dm/notify") === 0)
      return json({ ok: true, unread: 0, invites: 0, auth: true });
    if (u.indexOf("/api/chat/dm/rooms") === 0)
      return json({ ok: true, rooms: [], now: 0 });
    if (u.indexOf("/api/health") === 0) return json({ ok: true });
    return json({ ok: true });
  };
}

/** Числа вместо раскладки: jsdom не считает высоты, поэтому задаём их сами. */
function stubHeights(win, height, client) {
  Object.defineProperty(win.HTMLElement.prototype, "scrollHeight", {
    configurable: true, get() { return height; },
  });
  Object.defineProperty(win.HTMLElement.prototype, "clientHeight", {
    configurable: true, get() { return client; },
  });
  Object.defineProperty(win.HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get() { return this.__top || 0; },
    set(v) { this.__top = Number(v) || 0; },
  });
}

async function openChat() {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(URL_BASE + "/cabinet?chat=1&tab=support", {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true }); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      win.WebSocket = function FakeWs() {
        this.send = () => {}; this.close = () => {};
        this.addEventListener = () => {};
      };
      try { win.localStorage.clear(); } catch (e) {}
      mockFetch(win, []);
      stubHeights(win, 1200, 300);       // 900px «прокручиваемой» истории
    },
  });
  await sleep(900);
  return dom;
}

(async () => {
  console.log("💬 перемотка к последним записям");
  const dom = await openChat();
  const win = dom.window, doc = win.document;
  const list = doc.getElementById("tchat-support-list");

  check("панель чата открыта по ссылке ?chat=1&tab=support",
        !!doc.getElementById("tchat-panel") &&
        !doc.getElementById("tchat-panel").classList.contains("hidden"));
  check("история раздела отрисована", !!list && list.children.length >= HISTORY,
        list ? list.children.length : "нет списка");
  check("вход в раздел перематывает к последним записям",
        !!list && list.scrollTop === list.scrollHeight, list && list.scrollTop);

  // Человек читает историю: ушёл вверх — новые сообщения не дёргают ленту
  list.scrollTop = 0;
  win.TerminalChat.onWsMessage({
    type: "support_chat", thread_key: "u:1",
    message: { id: 999, user_id: 1, name: "Tester", guest: false, admin: true,
               text: "новое сверху", ts: Math.floor(Date.now() / 1000) },
  });
  await sleep(60);
  check("пока читаешь историю, лента не прыгает вниз", list.scrollTop === 0, list.scrollTop);
  check("новое сообщение всё равно добавлено", !!list.querySelector('[data-mid="999"]'));

  // Снова открыли панель (закрыли/открыли) — опять к последним записям
  win.TerminalChat.open({ tab: "support" });
  await sleep(120);
  check("повторное открытие снова показывает последние записи",
        list.scrollTop === list.scrollHeight, list.scrollTop);

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  errors.slice(0, 5).forEach((e) => console.log("  ! " + e));
  win.close();
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error("Тест упал:", e.message); process.exit(1); });
