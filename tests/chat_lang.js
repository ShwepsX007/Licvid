/**
 * 💬 Чат на языках сайта: ru, en, zh, hi, es.
 *
 * Chat — часть сайта, а сайт говорит на пяти языках: кнопки, вкладки,
 * подсказки и сигналы сервисов должны ехать за переключателем в шапке, а не
 * оставаться русскими. Проверяем это в браузере (jsdom): открываем кабинет и
 * терминал на каждом языке и смотрим, осталась ли кириллица в панели чата.
 *
 * Кириллица допустима только там, где это текст пользователя: переписка,
 * ники, сообщения сигналов из прошлых версий. Такие места помечены
 * ``data-i18n-skip`` — их пропускаем.
 *
 * Запуск (сервер на 127.0.0.1:8000):
 *     NODE_PATH=./node_modules node tests/chat_lang.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";

// Что должно быть видно в каждом сигнале на этом языке — проверяем не только
// «нет кириллицы», но и что строка действительно переведена.
const SIG_WORDS = {
  ru: { pump: "Памп", corr: "корреляции", book: "Стена", alert: "АЛЕРТ" },
  en: { pump: "Pump ·", corr: "correlations", book: "Wall on", alert: "ALERT" },
  zh: { pump: "拉升", corr: "相关性", book: "挂单墙", alert: "提醒" },
  hi: { pump: "पंप", corr: "कोरिलेशन", book: "वॉल", alert: "अलर्ट" },
  es: { pump: "Pump ·", corr: "correlaciones", book: "Muro en", alert: "ALERTA" },
};
const LANGS = ["ru", "en", "zh", "hi", "es"];

let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function mockFetch(win) {
  const now = Math.floor(Date.now() / 1000);
  win.fetch = async (url, opts) => {
    const u = String(url);
    const m = (opts && opts.method) || "GET";
    const json = (obj) => ({ ok: true, status: 200, json: async () => obj });
    if (u.indexOf("/api/terminal/chat/me") === 0)
      return json({ ok: true, user: { id: 1, name: "Tester", is_admin: false }, can_write: true });
    if (u.indexOf("/api/terminal/chat/ping") === 0) return json({ ok: true, online: 2 });
    if (u.indexOf("/api/terminal/chat/online") === 0) return json({ ok: true, count: 2, ids: [1, 9] });
    if (u.indexOf("/api/terminal/chat/participants") === 0) return json({ ok: true, participants: [], now: 0 });
    if (u.indexOf("/api/terminal/chat") === 0) {
      if (m === "POST") return json({ ok: true, message: { id: 7, user_id: 1, name: "Tester", text: "ok", ts: now } });
      return json({ ok: true, messages: [{ id: 3, user_id: 1, name: "Tester", text: "привет", ts: now, admin: false }], now, keep_days: 3 });
    }
    // Сигналы сервисов: текст русский (как в базе), рядом — части для перевода
    if (u.indexOf("/api/chat/services") === 0)
      return json({ ok: true, messages: [
        { id: 11, kind: "alert", ts: now, text: "💥 ликвидации: BTC $1.20M за 5м",
          meta: { symbol: "BTC_USDT", metric: "liq", parts: { metric: "liq", symbol: "BTC_USDT",
            value: 1200000, threshold: 500000, window_min: 5, span_min: 5, count: 3,
            longs: 900000, shorts: 300000, peers: [{ symbol: "ETH_USDT", value: 200000 }] } } },
        { id: 12, kind: "pump", ts: now, text: "🚀 Памп · SOL +8.42% за 5 мин",
          meta: { symbol: "SOL_USDT", parts: { direction: "pump", symbol: "SOL_USDT",
            change_pct: 8.42, span_min: 5, candles: 5, period: "5m",
            price: 210.5, price_from: 194.2, volume24h: 1230000000 } } },
        { id: 13, kind: "corr", ts: now, text: "🔗 корреляции · CVD / SOL ↔ XRP r = -0.58",
          meta: { symbol: "SOL_USDT|XRP_USDT", metric: "cvd", parts: { metric: "cvd",
            kind: "opp", a: "SOL_USDT", b: "XRP_USDT", r: -0.58, threshold: -0.5,
            window: "24h", peers: [{ a: "BTC_USDT", b: "ETH_USDT", r: -0.55 }] } } },
        { id: 14, kind: "book", ts: now, text: "📖 Стена на BTC_USDT — Bid $5.40M",
          meta: { symbol: "BTC_USDT", parts: { sym: "BTC_USDT", side: "bid", lo: 64000,
            hi: 64100, usdt: 5400000, exchs: ["binance", "okx"] } } },
      ], now });
    if (u.indexOf("/api/chat/support") === 0) return json({ ok: true, messages: [], now });
    if (u.indexOf("/api/chat/dm/notify") === 0) return json({ ok: true, unread: 0, invites: 0, auth: true });
    if (u.indexOf("/api/chat/dm/rooms") === 0) return json({ ok: true, rooms: [], now });
    return json({ ok: true });
  };
}

/** Видимый текст панели чата без мест, помеченных data-i18n-skip. */
function chatText(doc) {
  const panel = doc.getElementById("tchat-panel");
  if (!panel) return "";
  const parts = [];
  const walk = (node) => {
    if (node.nodeType === 3) {
      parts.push(node.nodeValue || "");
      return;
    }
    if (node.nodeType !== 1) return;
    if (node.hasAttribute && node.hasAttribute("data-i18n-skip")) return;
    const tag = node.tagName;
    if (tag === "SCRIPT" || tag === "STYLE") return;
    for (const ch of node.childNodes) walk(ch);
    for (const attr of ["placeholder", "title", "aria-label"]) {
      const v = node.getAttribute && node.getAttribute(attr);
      if (v) parts.push(v);
    }
  };
  walk(panel);
  return parts.join(" ");
}

async function openCabinet(lang) {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(URL_BASE + "/cabinet?lang=" + lang, {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(win) {
      try { Object.defineProperty(win.navigator, "language", { value: lang === "ru" ? "ru-RU" : lang, configurable: true }); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
        dispatchEvent() { return false; } });
      if (!win.ResizeObserver) win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      win.WebSocket = function FakeWs() { this.send = () => {}; this.close = () => {}; this.addEventListener = () => {}; };
      try { win.localStorage.clear(); } catch (e) {}
      mockFetch(win);
    },
  });
  await sleep(800);
  return dom;
}

(async () => {
  console.log("💬 чат на языках сайта");
  const CYR = /[А-Яа-яЁё]/;
  for (const lang of LANGS) {
    const dom = await openCabinet(lang);
    const win = dom.window, doc = win.document;
    // панель открыта по умолчанию? — откроем сами
    if (doc.getElementById("tchat-panel").classList.contains("hidden")) {
      win.TerminalChat.open({ tab: "services" });
      await sleep(400);
    }
    const panel = doc.getElementById("tchat-panel");
    check(`[${lang}] панель чата отрисована`, !!panel && !panel.classList.contains("hidden"));

    const tabs = Array.from(doc.querySelectorAll("#tchat-tabs .tchat-tab")).map((b) => b.textContent.trim());
    check(`[${lang}] вкладки переведены`, lang === "ru" ? true : !CYR.test(tabs.join(" ")), tabs.join(" | "));

    // вкладка «Сервисы»: сигнал собран из частей на языке посетителя
    win.TerminalChat.open({ tab: "services" });
    await sleep(500);
    const svc = doc.getElementById("tchat-services");
    const svcText = svc ? svc.textContent : "";
    check(`[${lang}] сигнал сервиса виден`, /BTC/.test(svcText), svcText.slice(0, 60));
    if (lang !== "ru") {
      check(`[${lang}] сигнал сервиса переведён`, !CYR.test(svcText), svcText.slice(0, 90));
    }
    // каждый сервис — сигнал стакан/памп/корреляции/алерты — на языке сайта
    const words = SIG_WORDS[lang];
    for (const [kind, word] of Object.entries(words)) {
      const node = svc && svc.querySelector(`[data-mid].${kind}`);
      const body = node ? node.textContent : "";
      check(`[${lang}] сигнал «${kind}» переведён`, !!node && body.indexOf(word) >= 0,
            node ? body.replace(/\n/g, " / ").slice(0, 90) : "нет узла " + kind);
    }

    const text = chatText(doc);
    if (lang === "ru") {
      check("[ru] русские подписи на месте", CYR.test(text));
    } else {
      const left = (text.match(/[А-Яа-яЁё][^ ]*/g) || []).slice(0, 6);
      check(`[${lang}] в панели не осталось русского`, !CYR.test(text), left.join(", "));
    }

    // смена языка на живой странице: панель перерисовывается без перезагрузки
    if (lang === "en") {
      win.LiqScopeI18n.set("zh");
      await sleep(300);
      const after = chatText(doc) + " " + (doc.getElementById("tchat-services") || {}).textContent;
      check("[en→zh] смена языка перерисовала чат", !CYR.test(after), after.slice(0, 90));
      check("[en→zh] сигналы пересобраны по-китайски",
            after.indexOf("挂单墙") >= 0 && after.indexOf("相关性") >= 0 && after.indexOf("拉升") >= 0,
            (doc.getElementById("tchat-services") || {}).textContent.slice(0, 120));
      const hint = doc.getElementById("tchat-hint");
      check("[en→zh] подсказка под полем тоже переведена", !CYR.test(hint ? hint.textContent : ""), hint && hint.textContent);
    }
    win.close();
  }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error("Тест упал:", e && e.message); process.exit(1); });
