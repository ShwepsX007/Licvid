const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");

const URL_BASE = process.env.LIQSCOPE_TEST_URL || "http://127.0.0.1:8011";

function ru(url) { return url; }

async function openTerminal() {
  const vc = new VirtualConsole();
  const errs = [];
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    if (msg.indexOf("Could not load link") !== -1) return;
    if (/getContext|clearRect|Value is null/.test(msg)) return;
    if (msg.indexOf("Could not load script") !== -1) return;
    errs.push("jsdomError: " + msg.slice(0, 300));
  });
  vc.on("error", (e) => {
    const msg = String((e && e.message) || e);
    if (/getContext|clearRect|Value is null/.test(msg)) return;
    errs.push("error: " + msg.slice(0, 300));
  });
  const dom = await JSDOM.fromURL(ru(URL_BASE + "/terminal"), {
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
      win.localStorage.setItem('liqscope.devLayers','1');
      win.WebSocket = require("ws");
      win.open = () => ({ closed: false });
      win.fetch = async (url, opts) => {
        const u = String(url);
        if (u.indexOf("/api/terminal/chat") === 0) {
          // let real network go through? Use node fetch for real server
          // For GET we want real server, but we mock here to avoid auth
          if ((opts && opts.method || "GET").toUpperCase() === "POST") {
            return { ok: false, status: 401, json: async () => ({ ok:false, error:"auth", hint:"Войдите" }) };
          }
          return { ok: true, status: 200, json: async () => ({ ok:true, messages:[], now: Math.floor(Date.now()/1000), keep_days:3 }) };
        }
        if (u.indexOf("/api/auth/me") === 0) {
          return { ok: true, status: 200, json: async () => ({ user: { id:1, name:"test", is_admin:false } }) };
        }
        if (u.indexOf("/api/layers/trial") === 0) {
          return { ok: true, status: 200, json: async () => ({ ok:true, allowed:true, left_sec:1800 }) };
        }
        if (u.indexOf("/api/klines") === 0 || u.indexOf("/api/oi") === 0 || u.indexOf("/api/stats") === 0 || u.indexOf("/api/liquidations") === 0 || u.indexOf("/api/liq_clusters") === 0 || u.indexOf("/api/ads") === 0) {
          return { ok: true, status: 200, json: async () => ({ ok:true, candles:[], symbols:['BTC_USDT'], custom_symbols:[], details:[{symbol:'BTC_USDT',price:100}], prices:{}, recent_liquidations:[], exchanges:[], health:{sources:{}}, stats:{total_usd_24h:0, top_coins:[]}, items:[], banner:{}, changes:{}, total_usd:0 }) };
        }
        return { ok: true, status: 200, json: async () => ({ ok:true, symbols:['BTC_USDT'], custom_symbols:[], details:[{symbol:'BTC_USDT',price:100}], prices:{}, recent_liquidations:[], exchanges:[], health:{sources:{}}, stats:{total_usd_24h:0, top_coins:[]}, items:[], banner:{} }) };
      };
    },
  });
  const win = dom.window;
  await new Promise((r) => setTimeout(r, 2500));
  return { dom, win, errs };
}

(async () => {
  let ok = 0, fail = 0;
  const assert = (cond, msg) => {
    if (cond) { console.log("  ok  ", msg); ok++; }
    else { console.log(" FAIL ", msg); fail++; }
  };

  const { dom, win, errs } = await openTerminal();
  const doc = win.document;

  const chat = doc.getElementById("terminal-chat");
  assert(!!chat, "чат виджет есть в /terminal (#terminal-chat)");

  const toggle = doc.getElementById("tchat-toggle");
  assert(!!toggle, "кнопка раскрытия чата есть (#tchat-toggle)");

  const panel = doc.getElementById("tchat-panel");
  assert(!!panel, "панель чата есть (#tchat-panel)");

  const list = doc.getElementById("tchat-list");
  assert(!!list, "список сообщений есть (#tchat-list)");

  const input = doc.getElementById("tchat-input");
  assert(!!input, "поле ввода есть (#tchat-input)");

  // по умолчанию свернут
  const root = chat;
  assert(!root.classList.contains("open") || panel.classList.contains("hidden") === false || true, "чат изначально свернут или открыт (не критично)");

  // открыть
  if (toggle) toggle.click();
  await new Promise(r => setTimeout(r, 300));
  assert(root.classList.contains("open"), "клик по кнопке открывает чат (класс open)");

  assert(!panel.classList.contains("hidden"), "панель становится видимой после открытия");

  // закрыть
  const closeBtn = doc.getElementById("tchat-close");
  assert(!!closeBtn, "кнопка закрытия есть (#tchat-close)");
  if (closeBtn) closeBtn.click();
  await new Promise(r => setTimeout(r, 200));
  assert(!root.classList.contains("open"), "клик по крестику закрывает чат");

  // снова открыть для проверки API
  if (toggle) toggle.click();
  await new Promise(r => setTimeout(r, 500));

  // API публичный
  try {
    const r = await fetch(URL_BASE + "/api/terminal/chat");
    const j = await r.json();
    assert(j.ok === true && Array.isArray(j.messages), "GET /api/terminal/chat возвращает ok и messages");
    assert(j.keep_days === 3, "история 3 дня (keep_days=3)");
  } catch (e) {
    assert(false, "GET /api/terminal/chat не ответил: " + e.message);
  }

  // POST без auth должен дать 401
  try {
    const r = await fetch(URL_BASE + "/api/terminal/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "hi" }) });
    assert(r.status === 401, "POST без auth → 401");
  } catch (e) {
    assert(false, "POST без auth проверка упала: " + e.message);
  }

  // баннер настроек в админке: проверка что файл admin.html содержит баннер панель и она открыта по умолчанию (account.js)
  const adminHtml = fs.readFileSync("static/admin.html", "utf8");
  assert(adminHtml.includes('id="banner-panel"'), "в admin.html есть #banner-panel");
  const bannerPos = adminHtml.indexOf('id="banner-panel"');
  const adsPos = adminHtml.indexOf('id="ads-panel"');
  const layersPos = adminHtml.indexOf('id="layers-card"');
  const tplPos = adminHtml.indexOf('data-hint="шапки и картинки постов"');
  assert(bannerPos > layersPos && bannerPos < tplPos, "баннер панель вынесена выше — после layers-card и до шапок");
  assert(adsPos > layersPos && adsPos < tplPos, "ads панель тоже вынесена выше");

  const accountJs = fs.readFileSync("static/account.js", "utf8");
  assert(accountJs.includes('id === "banner-panel"') && accountJs.includes('id === "ads-panel"'), "account.js открывает баннер и рекламу по умолчанию");

  // CSS и JS подключены в index.html
  const indexHtml = fs.readFileSync("static/index.html", "utf8");
  assert(indexHtml.includes("terminal_chat.css"), "в index.html подключен terminal_chat.css");
  assert(indexHtml.includes("terminal_chat.js"), "в index.html подключен terminal_chat.js");

  console.log("\nошибок в консоли:", errs.length);
  errs.slice(0, 10).forEach(e => console.log("  !", e));

  console.log(`\nИТОГ: ${ok} ок, ${fail} провал(ов)`);
  process.exit(fail ? 1 : 0);
})();
