/**
 * 📣 Реклама: баннер на главной и панель управления в админке.
 *
 * Запуск (сервер уже на 127.0.0.1:8011):
 *     NODE_PATH=./node_modules node tests/ads_banner.js [http://127.0.0.1:8011]
 *
 * Проверяем то, что просил админ: баннер всплывает под кнопками «Открыть
 * терминал» и «Что умеет», показывает текст с фото и закрывается насовсем;
 * в панели видно, куда и когда уйдёт пост, а отправка уважает выбранные
 * источники, время и срок автоудаления.
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8011";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const AD = {
  id: 7,
  text: "Скидка 10% на комиссию Gate\nПодробнее: https://liqscope.online/promo",
  html: "Скидка 10% на комиссию Gate<br>Подробнее: " +
        '<a target="_blank" rel="noopener" href="https://liqscope.online/promo">' +
        "https://liqscope.online/promo</a>",
  photo: "/api/ads/7/photo",
  has_photo: true,
  expires_at: 1789060000,
  sent_at: 1789000000,
};

/** Главная страница с подставленным ответом /api/ads. */
async function openLanding({ items = [AD], stored = null } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    if (msg.indexOf("Could not load link") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));

  const dom = await JSDOM.fromURL(ru(URL_BASE + "/"), {
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
      if (stored) { try { win.localStorage.setItem("liqscope.ads.hidden", stored); } catch (e) {} }
      win.WebSocket = require("ws");
      win.open = () => ({ closed: false });
      win.fetch = async (url) => {
        const u = String(url);
        if (u.indexOf("/api/ads") === 0) {
          return { ok: true, status: 200, json: async () => ({ ok: true, items: items }) };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      };
    },
  });
  await wait(900);
  return { win: dom.window, doc: dom.window.document };
}

/** Админка: /api/admin/ads отдаёт каналы и очередь, всё остальное — заглушка. */
async function openAdmin(ads) {
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

  const dom = await JSDOM.fromURL(ru(URL_BASE + "/admin"), {
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
      win.confirm = () => true;
      win.WebSocket = require("ws");
      win.open = () => ({ closed: false });
      win.fetch = async (url, opts) => {
        const u = String(url);
        const method = ((opts || {}).method || "GET").toUpperCase();
        let body = null;
        try { body = (opts || {}).body ? JSON.parse((opts || {}).body) : null; } catch (e) { body = (opts || {}).body; }
        reqs.push({ url: u, method: method, body: body });
        const send = (data) => ({ ok: true, status: 200, json: async () => data });
        if (u.indexOf("/api/auth/me") === 0) {
          return send({ ok: true, user: { id: 1, is_admin: true, first_name: "Босс" }, verified: true });
        }
        if (u.indexOf("/api/admin/ads") === 0) {
          if (method === "GET") return send(ads.snapshot);
          const id = (u.match(/\/api\/admin\/ads\/(\d+)/) || [])[1];
          if (id && u.indexOf("/send") > 0) {
            ads.snapshot.items[0].status = "sent";
            ads.snapshot.items[0].line = "бот: 3/3; главная: баннер";
            return send({ ok: true, item: ads.snapshot.items[0], published: true });
          }
          if (id && u.indexOf("/stop") > 0) {
            ads.snapshot.items[0].status = "expired";
            return send({ ok: true, item: ads.snapshot.items[0] });
          }
          if (id && u.indexOf("/delete") > 0) {
            ads.snapshot.items = [];
            return send({ ok: true, id: Number(id) });
          }
          if (id) return send({ ok: true, item: Object.assign({}, ads.snapshot.items[0], body) });
          return send({ ok: true, item: { id: 12, status: "sent", send_at: 1789000000,
                                          line: "бот: 3/3; главная: баннер" } });
        }
        if (u.indexOf("/api/admin/overview") === 0) {
          return send({ ok: true, users: { total: 5, new_24h: 1 },
                        visits: { today_views: 10, today_uniques: 4, today_bots: 0, days: [] },
                        ws_clients: 2, bot: { ready: true, username: "LiqScopeBot" },
                        health: { live_exchanges: ["binance"] }, services: [], settings: {} });
        }
        if (u.indexOf("/api/admin/stats") === 0) return send({ ok: true, stats: {} });
        return send({ ok: false });      // остальные панели гасим: тест не про них
      };
    },
  });
  await wait(1200);
  return { win: dom.window, doc: dom.window.document, reqs: reqs };
}

function snapshot() {
  return {
    ok: true,
    channels: [
      { id: "-1001112223334", code: "ru", label: "🇷🇺 Русский канал", title: "LiqScope RU" },
      { id: "-1005556667778", code: "en", label: "🇬🇧 Английский канал", title: "LiqScope EN" },
    ],
    bot: true,
    bot_ready: true,
    limits: { plain: 4000, photo: 1024, caption: 1024 },
    site: "https://liqscope.online/",
    now: 1789000000,
    tz_hours: 3,
    service: { bot: true, sent_total: 2, expired_total: 1, log: ["12:00 #7 отправлено"] },
    items: [{
      id: 5, text: "Скидка на комиссию Gate", status: "scheduled",
      targets: { bot: true, site: true, channels: ["-1001112223334"] },
      human_targets: "бот, LiqScope RU, главная сайта",
      send_at: 1789100000, expires_at: 1789103600, sent_at: 0,
      has_photo: true, line: "ещё не отправлялось",
    }],
  };
}

async function main() {
  console.log("📣 реклама: баннер на главной");
  const first = await openLanding();
  const host = first.doc.querySelector("#ad-host");
  const banner = first.doc.querySelector(".ad-banner");
  check("баннер появился на главной", !!banner);
  check("он стоит сразу под кнопками героя", !!host && host.previousElementSibling &&
        host.previousElementSibling.className.indexOf("hero-actions") >= 0);
  check("есть метка «Реклама»", (first.doc.querySelector(".ad-tag") || {}).textContent === "Реклама");
  check("фото объявления на месте",
        (first.doc.querySelector(".ad-media img") || {}).getAttribute("src") === "/api/ads/7/photo");
  check("текст с ссылкой отрисован как HTML",
        first.doc.querySelector(".ad-text a").getAttribute("href") === "https://liqscope.online/promo");
  check("виден срок показа", /⏳\s\d{2}:\d{2}/.test(
        (first.doc.querySelector(".ad-until") || {}).textContent || ""));
  check("у баннера есть кнопка закрытия", !!first.doc.querySelector(".ad-close"));

  const close = first.doc.querySelector(".ad-close");
  close.dispatchEvent(new first.win.MouseEvent("click", { bubbles: true }));
  await wait(60);
  check("после закрытия баннер исчез", first.doc.querySelector("#ad-host").hidden === true);
  check("закрытие запомнено",
        String(first.win.localStorage.getItem("liqscope.ads.hidden") || "").indexOf("7") >= 0);

  // тот же гость заходит снова — реклама не возвращается
  const again = await openLanding({ stored: "[7]" });
  check("закрытое объявление больше не показывается",
        again.doc.querySelector("#ad-host").hidden === true);

  // без фотографии баннер тоже работает
  const noPhoto = await openLanding({
    items: [Object.assign({}, AD, { id: 8, photo: "", has_photo: false })],
  });
  check("пост без фото: баннер без картинки",
        !!noPhoto.doc.querySelector(".ad-banner.no-photo") &&
        !noPhoto.doc.querySelector(".ad-media"));

  // несколько объявлений — точки переключения
  const many = await openLanding({
    items: [AD, Object.assign({}, AD, { id: 9, text: "Второе", html: "Второе" })],
  });
  const dots = many.doc.querySelectorAll(".ad-dots i");
  check("несколько объявлений: есть переключатель", dots.length === 2);
  dots[1].dispatchEvent(new many.win.MouseEvent("click", { bubbles: true }));
  await wait(50);
  check("переключение показывает второе объявление",
        (many.doc.querySelector(".ad-text") || {}).textContent.indexOf("Второе") >= 0);

  const none = await openLanding({ items: [] });
  check("нет объявлений — на главной пусто", none.doc.querySelector("#ad-host").hidden === true);

  const terminal = await JSDOM.fromURL(ru(URL_BASE + "/terminal"), {
    runScripts: "outside-only", resources: "usable", pretendToBeVisual: true,
  });
  check("в терминале баннера нет", !terminal.window.document.querySelector("#ad-host"));

  console.log("\n📣 реклама: панель в админке");
  const ads = { snapshot: snapshot() };
  const admin = await openAdmin(ads);
  const doc = admin.doc;

  check("панель рекламы есть в админке", !!doc.querySelector("#ads-panel"));
  const targetKeys = Array.from(doc.querySelectorAll("#ad-targets input[data-ad-target]"))
    .map((i) => i.getAttribute("data-ad-target"));
  check("источники перечислены: бот, главная и каналы",
        targetKeys.join(",") === "bot,site,ch:-1001112223334,ch:-1005556667778", targetKeys.join(","));
  check("подписи каналов пришли из бота",
        doc.querySelector("#ad-targets").textContent.indexOf("Русский канал") >= 0);
  check("очередь показывает отправленный ранее пост",
        doc.querySelector("#ad-list").textContent.indexOf("Скидка на комиссию Gate") >= 0);
  check("в очереди видно, куда уйдёт и когда",
        doc.querySelector("#ad-list").textContent.indexOf("бот, LiqScope RU, главная сайта") >= 0);
  check("состояние планировщика подписано",
        (doc.querySelector("#ad-service") || {}).textContent.indexOf("снято по сроку") >= 0);

  // --- отправка: все источники, сразу -----------------------------------
  const ta = doc.querySelector("#ad-text");
  ta.value = "Скидка на комиссию Gate https://liqscope.online/promo";
  ta.dispatchEvent(new admin.win.Event("input", { bubbles: true }));
  check("счётчик знаков считает по лимиту без фото", /\/ 4000 знаков \(без фото\)/.test(
        doc.querySelector("#ad-len").textContent || ""), doc.querySelector("#ad-len").textContent);

  // фото выбирается в форме и сразу ограничивает текст 1024 знаками
  const fileInput = doc.querySelector("#ad-photo-in");
  const pngBytes = new admin.win.Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
                                             0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
  const file = new admin.win.File([pngBytes], "ad.png", { type: "image/png" });
  Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
  fileInput.dispatchEvent(new admin.win.Event("change", { bubbles: true }));
  await wait(120);
  check("выбранное фото показывает превью", !!doc.querySelector("#ad-photo-prev img"));
  check("с фото лимит текста — 1024 знака", /\/ 1024 знаков \(с фото\)/.test(
        doc.querySelector("#ad-len").textContent || ""), doc.querySelector("#ad-len").textContent);
  doc.querySelector("#ad-all").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  doc.querySelector("#ad-extra").value = "@myads, -1009998887776";
  doc.querySelector("#ad-ttl").value = "360";
  doc.querySelector("#ad-save").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  await wait(200);

  const post = admin.reqs.filter((r) => r.method === "POST" &&
    r.url.indexOf("/api/admin/ads") === 0).pop();
  check("форма отправила пост в API", !!post, JSON.stringify(admin.reqs.map((r) => r.url)));
  check("выбраны все источники", post && post.body.targets.bot === true &&
        post.body.targets.site === true && post.body.targets.channels.length === 4,
        post && JSON.stringify(post.body.targets));
  check("свои каналы из строки попали в список",
        post && post.body.targets.channels.indexOf("@myads") >= 0 &&
        post.body.targets.channels.indexOf("-1009998887776") >= 0);
  check("срок автоудаления ушёл как 6 часов", post && post.body.ttl_min === 360);
  check("фото ушло вместе с постом (data-URL)",
        post && String(post.body.photo || "").indexOf("data:image/png;base64,") === 0,
        post && String(post.body.photo || "").slice(0, 30));
  check("время отправки — «сейчас»", post && Math.abs(post.body.send_at - Date.now() / 1000) < 60);
  check("панель отчиталась об отправке",
        doc.querySelector("#ad-status").textContent.indexOf("ушло") >= 0,
        doc.querySelector("#ad-status").textContent);

  // --- отложенная отправка в выбранное время -----------------------------
  doc.querySelector("#ad-when-mode").value = "later";
  doc.querySelector("#ad-when-mode").dispatchEvent(new admin.win.Event("change", { bubbles: true }));
  check("поле времени включается вместе с режимом",
        doc.querySelector("#ad-when").disabled === false);
  const local = "2026-09-20T21:30";
  doc.querySelector("#ad-when").value = local;
  doc.querySelector("#ad-none").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  doc.querySelectorAll("#ad-targets input[data-ad-target]")[0].checked = true;
  doc.querySelector("#ad-save").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  await wait(160);
  const second = admin.reqs.filter((r) => r.method === "POST" &&
    r.url.indexOf("/api/admin/ads") === 0).pop();
  check("отложенный пост уходит на выбранное время",
        second && Math.abs(second.body.send_at - Math.round(new Date(local).getTime() / 1000)) === 0,
        second && second.body.send_at + " vs " + Math.round(new Date(local).getTime() / 1000));
  check("при отправке в будущее каналы не сбрасываются",
        second && second.body.targets.bot === true && second.body.targets.channels.length === 0);

  // --- действия в очереди ------------------------------------------------
  const sendBtn = doc.querySelector('#ad-list button[data-ad-act="send"]');
  check("в очереди есть кнопка «Сейчас»", !!sendBtn);
  if (sendBtn) {
    sendBtn.dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
    await wait(160);
    check("кнопка «Сейчас» зовёт /send",
          admin.reqs.some((r) => r.url.indexOf("/send") > 0 && r.method === "POST"));
    check("панель показала, кому ушло",
          doc.querySelector("#ad-status").textContent.indexOf("бот: 3/3") >= 0,
          doc.querySelector("#ad-status").textContent);
  }
  const stopBtn = doc.querySelector('#ad-list button[data-ad-act="stop"]');
  if (stopBtn) {
    stopBtn.dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
    await wait(160);
    check("кнопка «Снять» зовёт /stop",
          admin.reqs.some((r) => r.url.indexOf("/stop") > 0 && r.method === "POST"));
  }

  console.log("\nошибок в консоли: " + errors.length);
  errors.slice(0, 4).forEach((e) => console.log("  ! " + e));
  console.log(`ИТОГ: ${ok} ок, ${fail} провал(ов)`);
  process.exit(fail || errors.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
