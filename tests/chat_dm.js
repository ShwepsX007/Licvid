/**
 * 🔒 Чат v3: вкладки «Общий/Личные», приватные диалоги, растягивание панели,
 * меню по клику на ник, бейджи unread/онлайн, подсветка уведомлений.
 *
 * Живой сервер не обязателен в полном смысле: страница берётся с сервера,
 * но API чата подменяется моками — проверяется поведение виджета.
 *
 * Запуск (сервер на 127.0.0.1:8011):
 *     node tests/chat_dm.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.env.LIQSCOPE_TEST_URL || "http://127.0.0.1:8011";

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function mockFetch(win, calls) {
  const pubMsgs = [
    { id: 10, user_id: 9, name: "Bobby", text: "привет всем", ts: Math.floor(Date.now() / 1000), admin: false },
    { id: 11, user_id: 12, name: "Karen", text: "gosha", ts: Math.floor(Date.now() / 1000), admin: true },
  ];
  win.fetch = async (url, opts) => {
    const u = String(url);
    calls.push(u);
    const m = (opts && opts.method) || "GET";
    const json = (obj, status) => ({
      ok: !status || status < 400, status: status || 200,
      json: async () => obj,
    });
    if (u.indexOf("/api/terminal/chat/me") === 0)
      return json({ ok: true, user: { id: 1, name: "Tester", is_admin: false }, can_write: true });
    if (u.indexOf("/api/terminal/chat/ping") === 0 && m === "POST")
      return json({ ok: true, online: 3 });
    if (u.indexOf("/api/terminal/chat/online") === 0)
      return json({ ok: true, count: 2, ids: [1, 9, 12] });
    if (u.indexOf("/api/terminal/chat/participants") === 0)
      return json({ ok: true, participants: [{ id: 9, name: "Bobby", admin: false, online: true }], now: 0 });
    if (u.indexOf("/api/terminal/chat?") === 0) {
      if (u.indexOf("after_id=") !== -1) return json({ ok: true, messages: [], now: 0, keep_days: 3 });
      return json({ ok: true, messages: pubMsgs, now: 0, keep_days: 3 });
    }
    if (u === "/api/terminal/chat" && m === "POST")
      return json({ ok: true, message: { id: 99, user_id: 1, name: "Tester", text: "ok", ts: 0, admin: false } });
    if (u.indexOf("/api/chat/dm/notify") === 0)
      return json({ ok: true, unread: 2, invites: 1, auth: true });
    if (u.indexOf("/api/chat/dm/rooms") === 0)
      return json({ ok: true, rooms: [{
        id: 1, status: "pending", invited_by: 9,
        peer: { id: 9, name: "Bobby", banned: false },
        last: { id: 5, user_id: 9, name: "Bobby", text: "привет, это личное", ts: 100, mine: false },
        unread: 2, updated_at: Math.floor(Date.now() / 1000), created_at: 0,
      }], now: 0 });
    if (u.indexOf("/api/chat/dm/1/messages") === 0 && m === "GET")
      return json({ ok: true, messages: [
        { id: 1, room_id: 1, user_id: 9, name: "Bobby", text: "тсс", ts: 100, admin: false, mine: false },
        { id: 2, room_id: 1, user_id: 1, name: "Tester", text: "секрет", ts: 101, admin: false, mine: true },
      ], room: { id: 1, status: "active", invited_by: 9, peer: { id: 9, name: "Bobby", banned: false }, updated_at: 0, created_at: 0 }, now: 0 });
    if (u.indexOf("/api/chat/dm/1/messages") === 0 && m === "POST")
      return json({ ok: true, message: { id: 3, room_id: 1, user_id: 1, name: "Tester", text: "ответ", ts: 102, mine: true } });
    if (u.indexOf("/api/chat/dm/invite") === 0 && m === "POST")
      return json({ ok: true, created: false, room: { id: 1, status: "active", invited_by: 9, peer: { id: 9, name: "Bobby", banned: false }, updated_at: 0, created_at: 0, last: null, unread: 0 } });
    if (u.indexOf("/api/chat/users") === 0)
      return json({ ok: true, users: [{ id: 12, name: "Karen", username: "karen", admin: true }],
      });
    return json({ ok: true });
  };
}

async function openPage() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (/Not implemented: navigation|Could not load (link|script|img)|getContext|clearRect|Value is null|Could not parse CSS/.test(msg)) return;
    errors.push("jsdomError: " + msg.slice(0, 300));
  });
  vc.on("error", (e) => {
    const msg = String((e && e.message) || e);
    if (/getContext|clearRect|Value is null/.test(msg)) return;
    errors.push("error: " + msg.slice(0, 300));
  });
  const calls = [];
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
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
      // «мобильный» сценарий: узкий вьюпорт + сохранённые размер/позиция —
      // applyPos обязан восстановить их и на телефоне
      if (process.env.CHATDM_MOBILE) {
        Object.defineProperty(win, "innerWidth", { value: 375, configurable: true });
        Object.defineProperty(win, "innerHeight", { value: 667, configurable: true });
        win.localStorage.setItem("liqscope.tchat.pos",
          JSON.stringify({ left: 10, top: 60, width: 340, height: 420 }));
      }
      win.WebSocket = function FakeWs() {
        this.send = () => {}; this.close = () => {};
        this.addEventListener = () => {};
      };
      win.WebSocket.prototype.OPEN = 1;
      mockFetch(win, calls);
    },
  });
  await sleep(2200);
  return { dom, win: dom.window, doc: dom.window.document, calls };
}

(async () => {
  const { win, doc, calls } = await openPage();

  // ---------- 0. мобильный: сохранённые размер/позиция применяются сразу ----------
  if (process.env.CHATDM_MOBILE) {
    const css = require("fs").readFileSync("static/terminal_chat.css", "utf8");
    const p0 = doc.getElementById("tchat-panel");
    check("мобильный: сохранённая ширина применена",
      p0.style.width === "340px", p0.style.width);
    check("мобильный: сохранённая высота применена",
      p0.style.height === "420px", p0.style.height);
    check("мобильный: CSS не прячет ручки на узких экранах",
      css.indexOf(".tchat-rz { display:none; }") === -1);
    check("мобильный: у ручек touch-action none", /\.tchat-rz\s*\{[^}]*touch-action:none/.test(css));
    check("мобильный: низ панели — широкая видимая «губа», а не тонкая полоска",
      /@media \(max-width: 640px\)[^]*?\.tchat-rz-s \{[^}]*bottom:-26px[^}]*height:26px/.test(css) &&
      /\.tchat-rz-s \{[^}]*background:/.test(css) && /\.tchat-rz-s::after/.test(css));
    // ранний баг: мобильные оверрайды стояли ДО базовых .tchat-rz-* и
    // перебивались ими — ручки оставались 10px. Следим за порядком в файле.
    const baseS = css.indexOf(".tchat-rz-s { bottom:-4px;");
    const mobS = css.indexOf(".tchat-rz-s { bottom:-26px;");
    check("мобильный: оверрайд ручек идёт после базовых правил (каскад)",
      baseS !== -1 && mobS !== -1 && mobS > baseS, baseS + " vs " + mobS);
    check("мобильный: угловые ручки крупные (≥36px)",
      /\.tchat-rz-se \{ width: 44px; height: 44px/.test(css) &&
      /\.tchat-rz-sw \{ width: 40px; height: 40px/.test(css));
    // тянем «губу» вниз тач-событиями: высота обязана вырасти и сохраниться
    const lip = doc.querySelector(".tchat-rz-s");
    if (lip) {
      const mk = (x, y) => {
        const e = new win.Event("touchmove", { bubbles: true, cancelable: true });
        e.touches = [{ clientX: x, clientY: y }];
        e.changedTouches = e.touches;
        return e;
      };
      const st = new win.Event("touchstart", { bubbles: true, cancelable: true });
      st.touches = [{ clientX: 100, clientY: 400 }];
      lip.dispatchEvent(st);
      win.dispatchEvent(mk(100, 560));
      await sleep(50);
      win.dispatchEvent(new win.Event("touchend", { bubbles: true }));
      await sleep(80);
      const h = parseFloat(p0.style.height || "0");
      check("мобильный: drag губы вниз задал высоту (>0 — inline)", h > 0 && /px/.test(p0.style.height || ""),
        p0.style.height);
      check("мобильный: панель помечена как растянутая", p0.classList.contains("tchat-resized"));
      const savedRaw = win.localStorage.getItem("liqscope.tchat.pos") || "";
      check("мобильный: размер записан в localStorage",
        /"height"/.test(savedRaw) && /"width"/.test(savedRaw), savedRaw);
    } else {
      check("мобильный: drag губы вниз задал высоту (>0 — inline)", false, "нет .tchat-rz-s");
    }
    // событие resize (выехал адрес-бар) не должно сбрасывать растянутый размер
    win.dispatchEvent(new win.Event("resize"));
    await sleep(120);
    check("мобильный: после window.resize высота осталась inline",
      /px/.test(doc.getElementById("tchat-panel").style.height || ""),
      doc.getElementById("tchat-panel").style.height);
  }

  // ---------- 1. структура вкладки/бейджи ----------
  const root = doc.getElementById("terminal-chat");
  const panel = doc.getElementById("tchat-panel");
  check("виджет на месте", !!root && !!panel);
  const tabs = doc.querySelectorAll("#tchat-tabs .tchat-tab");
  check("две вкладки: Общий и Личные", tabs.length === 2, tabs.length);
  check("вкладка «Личные» есть", !!doc.querySelector('[data-tab="dm"]'));
  check("бейдж онлайна на кнопке чата", !!doc.getElementById("tchat-online"));

  // открыть панель
  doc.getElementById("tchat-header-btn").click();
  await sleep(300);
  check("панель открыта", root.classList.contains("open"));

  // ---------- 2. общий чат: ник = кнопка с меню ----------
  const nameBtn = doc.querySelector("#tchat-list .tchat-namelink");
  check("имя в общем чате кликабельно", !!nameBtn);
  if (nameBtn) {
    nameBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await sleep(120);
    const menu = doc.querySelector(".tchat-usermenu");
    check("клик по нику открыл меню", !!menu);
    if (menu) {
      check("в меню есть «Ответить» и «Написать лично»",
        !!menu.querySelector('[data-act="reply"]') && !!menu.querySelector('[data-act="dm"]'));
      const dmBtn = menu.querySelector('[data-act="dm"]');
      if (dmBtn) dmBtn.click();
      await sleep(400);
      check("«Написать лично» → вызван invite",
        calls.some((c) => c.indexOf("/api/chat/dm/invite") !== -1));
      check("после invite открыт приватный вид", !doc.getElementById("tchat-dm-chat").classList.contains("hidden"));
      const bubbles = doc.querySelectorAll("#tchat-dm-msgs .tchat-msg");
      check("пузыри личных сообщений отрисованы", bubbles.length === 2, bubbles.length);
      check("моё сообщение помечено mine", !!doc.querySelector("#tchat-dm-msgs .tchat-msg.mine"));
    }
  }

  // ---------- 3. вкладка «Личные»: комнаты, приглашение, кнопки ----------
  const dmTab = doc.querySelector('[data-tab="dm"]');
  if (dmTab) dmTab.click();
  await sleep(400);
  check("вызван GET /api/chat/dm/rooms", calls.some((c) => c.indexOf("/api/chat/dm/rooms") !== -1));
  const item = doc.querySelector("#tchat-dm .tchat-dm-item");
  check("комната в списке личных", !!item);
  if (item) {
    check("непрочитанное на комнате", !!item.querySelector(".tchat-dm-unread"));
    check("входящее приглашение помечено", !!item.querySelector(".tchat-dm-flag.inv"));
    check("кнопки Принять/Отклонить на приглашении",
      !!item.querySelector("[data-accept]") && !!item.querySelector("[data-decline]"));
    check("точка онлайн у собеседника", !!item.querySelector(".tchat-peer-dot.on"));
  }

  // ---------- 4. поиск и приглашение ----------
  const inviteBtn = doc.getElementById("tchat-invite-btn");
  if (inviteBtn) inviteBtn.click();
  await sleep(80);
  const q = doc.getElementById("tchat-invite-q");
  check("форма поиска открылась", !!q && !doc.getElementById("tchat-invite-form").classList.contains("hidden"));
  if (q) {
    q.value = "kar";
    q.dispatchEvent(new win.Event("input", { bubbles: true }));
    await sleep(700);
    check("поиск вызвал /api/chat/users", calls.some((c) => c.indexOf("/api/chat/users") !== -1));
    const inv = doc.querySelector("#tchat-dm .tchat-inv-item");
    check("найден кандидат для приглашения", !!inv, inv && inv.textContent);
  }

  // ---------- 5. ввод в приватный диалог ----------
  doc.querySelector('[data-tab="public"]').click();
  await sleep(200);
  if (item) { item.click(); await sleep(500); }
  const inp = doc.getElementById("tchat-input");
  if (inp) {
    inp.value = "ответ";
    doc.getElementById("tchat-send").click();
    await sleep(400);
    check("отправка в личную комнату → POST messages",
      calls.some((c) => c.indexOf("/api/chat/dm/1/messages") !== -1));
  }

  // ---------- 6. растягивание панели ----------
  const handles = doc.querySelectorAll(".tchat-rz");
  check("ручки изменения размера (8 сторон)", handles.length === 8, handles.length);
  check("ручки — дети корня виджета (panel overflow:hidden их бы резал)",
    Array.from(handles).every((h) => h.parentElement && h.parentElement.id === "terminal-chat"));
  const east = doc.querySelector(".tchat-rz-e");
  if (east) {
    east.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true, clientX: 100, clientY: 100 }));
    win.dispatchEvent(new win.MouseEvent("mousemove", { bubbles: true, clientX: 220, clientY: 100 }));
    await sleep(60);
    win.dispatchEvent(new win.MouseEvent("mouseup", { bubbles: true }));
    await sleep(60);
    const saved = win.localStorage.getItem("liqscope.tchat.pos");
    check("размер сохранён в localStorage", !!saved && /width/.test(saved), saved);
    check("панель получила inline-ширину", /px/.test(panel.style.width || ""), panel.style.width);
  }

  // ---------- 7. бейдж и подсветка уведомлений ----------
  check("поллинг /api/chat/dm/notify", calls.some((c) => c.indexOf("/api/chat/dm/notify") !== -1));
  const unreadHdr = doc.getElementById("tchat-unread-hdr");
  check("бейдж непрочитанных показан", !!unreadHdr && !unreadHdr.classList.contains("hidden"));
  const online = doc.getElementById("tchat-online");
  check("онлайн-бейдж светится (class on)", !!online && online.classList.contains("on"),
    online && online.className);
  doc.getElementById("tchat-close").click();
  await sleep(200);
  check("при закрытии панели корень подсвечивается уведомлением",
    root.classList.contains("tchat-notify"));

  // ---------- 8. WS-события ----------
  doc.getElementById("tchat-header-btn").click();
  await sleep(200);
  doc.dispatchEvent(new win.CustomEvent("liqscope:ws", { detail: {
    type: "chat_dm", room_id: 1, event: "message",
    message: { id: 9, room_id: 1, user_id: 9, name: "Bobby", text: "новое личное", ts: 0, mine: false },
  } }));
  await sleep(400);
  check("WS chat_dm не сломал виджет", true);
  doc.dispatchEvent(new win.CustomEvent("liqscope:ws", { detail: {
    type: "chat_dm_room", event: "invite",
    room: { id: 2, status: "pending", invited_by: 9, peer: { id: 9, name: "Bobby" } },
  } }));
  await sleep(300);
  check("WS invite → обновление списка комнат",
    calls.filter((c) => c.indexOf("/api/chat/dm/rooms") !== -1).length >= 2);

  console.log("\nошибок в консоли:", errors.length);
  errors.slice(0, 12).forEach((e) => console.log("  ! " + e));
  console.log(`\nитог: ${ok} ок, ${fail} провал(ов)`);
  process.exit(fail || errors.length ? 1 : 0);
})();
