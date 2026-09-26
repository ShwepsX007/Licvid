require("./_dom_env");
/**
 * 📣 Реклама: баннер на главной и панель управления в админке.
 *
 * Запуск (сервер уже на 127.0.0.1:8011):
 *     NODE_PATH=./node_modules node tests/ads_banner.js [http://127.0.0.1:8011]
 *
 * Проверяем то, что просил админ:
 *
 *   * баннер на главной стоит под кнопками «Терминал» и «Что умеет», фото
 *     видно целиком (object-fit: contain), подпись — ПОД фото;
 *   * ссылка объявления делает баннер кликабельным, без ссылки слайд не
 *     притворяется кнопкой;
 *   * закрыть баннер посетителю нечем: крестика нет, а старый ключ
 *     liqscope.ads.hidden стирается при первом же показе;
 *   * слайды сменяются по настройке из /api/ads (banner), сеткой можно
 *     показать все акции сразу, срок показа убирает баннер без перезагрузки;
 *   * в терминале — тот же блок под основным графиком;
 *   * в панели видно, куда и когда уйдёт пост, добавлено поле ссылки и
 *     настройка смены/плавности, отправка уважает выбранные источники.
 */
const fs = require("fs");
const path = require("path");
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

// Таймеры в jsdom дрейфуют, когда рядом крутятся другие проверки: ждём нужное
// состояние опросом, а не одним сном «на глаз».
async function until(what, fn, timeoutMs) {
  const stop = Date.now() + (timeoutMs || 4000);
  while (Date.now() < stop) {
    try { if (fn()) return true; } catch (e) { /* ещё не отрисовано */ }
    await wait(100);
  }
  return false;
}

// jsdom не считает layout, поэтому «фото целиком» и «подпись под фото»
// проверяем по факту в файле стилей, а не по нарисованным пикселям.
const AD_CSS = fs.readFileSync(path.join(__dirname, "..", "static", "ads.css"), "utf8");

// срок показа — «через час от теперешнего времени»: фикстура не должна
// протухать на другой машине/дате, иначе клиент сам снимает баннер по expires_at
const LATER = Math.floor(Date.now() / 1000) + 3600;

const AD = {
  id: 7,
  link: "https://liqscope.online/promo",
  text: "Скидка 10% на комиссию Gate\nПодробнее: https://liqscope.online/promo",
  html: "Скидка 10% на комиссию Gate<br>Подробнее: " +
        '<a target="_blank" rel="noopener" href="https://liqscope.online/promo">' +
        "https://liqscope.online/promo</a>",
  photo: "/api/ads/7/photo",
  has_photo: true,
  expires_at: LATER,
  sent_at: 1789000000,
};
const AD_PLAIN = {              // подпись без своих ссылок: оборачиваем целиком
  id: 11, link: "https://gate.com/promo", text: "Скидка на комиссию",
  html: "Скидка на комиссию", photo: "/api/ads/11/photo", has_photo: true,
  expires_at: LATER, sent_at: 1789000000,
};
const BANNER = { rotate_sec: 4, fade_ms: 300, layout: "carousel", max: 4,
                 fit: "contain", caption: "below" };

/** Страница с подставленным ответом /api/ads (главная или терминал). */
async function openLanding({ items = [AD], banner = BANNER, stored = null,
                             page = "/" } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    if (msg.indexOf("Could not load link") !== -1) return;
    if (msg.indexOf("googletagmanager") !== -1) return;
    if (msg.indexOf("gtag") !== -1) return;
    // Страница терминала рисует график; без npm-пакета canvas jsdom падает в
    // getContext() — к баннеру это отношения не имеет и в проверке не участвует.
    if (/getContext|clearRect|Value is null/.test(msg)) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => {
    const msg = a.map((x) => String((x && x.message) || x)).join(" ");
    if (/getContext|clearRect|Value is null/.test(msg)) return;
    errors.push("console.error: " + msg.slice(0, 200));
  });

  const dom = await JSDOM.fromURL(ru(URL_BASE + page), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true }); } catch (e) {}
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
          var pl = "landing";
          if (u.indexOf("place=terminal") >= 0) pl = "terminal";
          else if (u.indexOf("place=digest") >= 0) pl = "digest";
          else if (u.indexOf("place=hourly") >= 0) pl = "hourly";
          return {
            ok: true, status: 200,
            json: async () => ({
              ok: true, items: items, banner: banner,
              place: pl,
            }),
          };
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
    if (msg.indexOf("googletagmanager") !== -1) return;
    if (msg.indexOf("gtag") !== -1) return;
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
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true }); } catch (e) {}
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
        if (u.indexOf("/api/admin/ads/banner") === 0) {
          const b = Object.assign({}, ads.snapshot.banner, body || {});
          // сервер значения не принимает на веру: рамки те же, что в ads.py
          b.rotate_sec = Math.min(120, Math.max(2, Number(b.rotate_sec) || 8));
          b.fade_ms = Math.min(2000, Math.max(0, Math.round(Number(b.fade_ms) || 0)));
          b.max = Math.min(8, Math.max(1, Number(b.max) || 4));
          if (["carousel", "grid"].indexOf(b.layout) < 0) b.layout = "carousel";
          ads.snapshot.banner = b;
          return send({ ok: true, banner: b });
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
    banner: { rotate_sec: 8, fade_ms: 500, layout: "carousel", max: 4,
              fit: "contain", caption: "below" },
    items: [{
      id: 5, text: "Скидка на комиссию Gate", status: "scheduled",
      link: "https://gate.com/promo",
      targets: { bot: true, site: true, terminal: true, channels: ["-1001112223334"] },
      human_targets: "бот, LiqScope RU, главная сайта, терминал",
      send_at: 1789100000, expires_at: 1789103600, sent_at: 0,
      has_photo: true, line: "ещё не отправлялось",
    }],
  };
}

async function main() {
  console.log("📣 реклама: баннер на главной");
  const one = await openLanding({ items: [AD] });
  const host = one.doc.querySelector("#ad-host");
  check("баннер на главной есть", !!host);
  check("страница объявлена — стили и тексты берутся под неё",
        !!host && host.getAttribute("data-ad-place") === "landing");
  check("баннер показан", !!host && host.hidden === false);

  const slide = host.querySelector(".ad-slide");
  const media = host.querySelector(".ad-media img");
  const cap = host.querySelector(".ad-cap");
  check("слайд один и он активен",
        host.querySelectorAll(".ad-slide").length === 1 &&
        host.querySelectorAll(".ad-slide.on").length === 1);
  check("фотография показана", !!media && media.getAttribute("src") === "/api/ads/7/photo",
        media && media.getAttribute("src"));
  check("у картинки есть внятное имя", !!media && !!media.getAttribute("alt"));
  check("подпись под фотографией, а не сбоку",
        !!slide && !!media && !!cap &&
        Array.prototype.indexOf.call(slide.children, cap) >
        Array.prototype.indexOf.call(slide.children, media.closest(".ad-hit")),
        slide && Array.prototype.map.call(slide.children, (n) => n.className).join("|"));
  check("режим — вписывать целиком (object-fit: contain)",
        host.getAttribute("data-ad-fit") === "contain" &&
        /object-fit:\s*contain/.test(AD_CSS) &&
        /data-ad-fit="cover"[\s\S]{0,120}object-fit:\s*cover/.test(AD_CSS));
  check("высота блока — по самому высокому слайду, без прыжков",
        /grid-area:\s*1 \/ 1/.test(AD_CSS));

  const hit = media && media.closest("a");
  check("баннер кликабельный по ссылке объявления",
        !!hit && hit.getAttribute("href") === "https://liqscope.online/promo" &&
        hit.getAttribute("target") === "_blank" &&
        /sponsored/.test(hit.getAttribute("rel") || ""), hit && hit.getAttribute("href"));
  check("в href не протаскивает чужой код", !/href="[^"]*javascript/i.test(host.innerHTML));
  check("текст с собственными ссылками не оборачиваем повторно",
        !!host.querySelector('.ad-text a[href="https://liqscope.online/promo"]') &&
        !host.querySelector(".ad-hit-cap"));
  check("срок показа подписан", /\u23f3\s?\d\d:\d\d/.test(cap.textContent), cap.textContent);

  // крестика нет ни в разметке, ни в стилях — закрыть баннер нечем
  check("закрыть баннер нечем", !host.querySelector(".ad-close") && !/\.ad-close/.test(AD_CSS));
  check("старый «не показывать» забывается",
        one.win.localStorage.getItem("liqscope.ads.hidden") === null);

  // плавность и смена — из настроек, а не из головы скрипта
  check("частота смены берётся из настройки", host.getAttribute("data-ad-rotate") === "4",
        host.getAttribute("data-ad-rotate"));
  check("плавность перехода берётся из настройки", host.getAttribute("data-ad-fade") === "300",
        host.getAttribute("data-ad-fade"));
  check("режим карусели прописан на контейнере", host.getAttribute("data-ad-layout") === "carousel");

  const plain = await openLanding({ items: [AD_PLAIN] });
  const plainCap = plain.doc.querySelector(".ad-hit-cap");
  check("подпись без своих ссылок кликабельна целиком",
        !!plainCap && plainCap.getAttribute("href") === "https://gate.com/promo");
  check("двойной таб-стоп убран: ссылка на подписи вне фокуса",
        !!plainCap && plainCap.getAttribute("tabindex") === "-1" &&
        plainCap.getAttribute("aria-hidden") === "true");

  const noLink = await openLanding({ items: [Object.assign({}, AD, { link: "" })] });
  check("без ссылки баннер не притворяется кнопкой", !noLink.doc.querySelector(".ad-hit"));

  const photoOnly = await openLanding({
    items: [Object.assign({}, AD, { text: "", html: "", expires_at: 0 })],
  });
  check("фото без текста — баннер из одной картинки",
        !!photoOnly.doc.querySelector(".ad-media img") && !photoOnly.doc.querySelector(".ad-cap"));
  check("на голом фото всё равно стоит метка «Реклама»",
        !!photoOnly.doc.querySelector(".ad-media .ad-flag"),
        photoOnly.doc.querySelector(".ad-slide").innerHTML.slice(0, 120));
  const poHit = photoOnly.doc.querySelector(".ad-hit");
  check("у такого баннера есть доступная подпись для читалки",
        !!poHit && !!poHit.getAttribute("aria-label"), poHit && poHit.getAttribute("aria-label"));

  // --- карусель: несколько ссылок крутятся по очереди ---------------------
  const AD2 = Object.assign({}, AD, { id: 9, text: "Второе", html: "Второе", link: "" });
  const many = await openLanding({ items: [AD, AD2],
                                   banner: Object.assign({}, BANNER, { rotate_sec: 2 }) });
  const manyHost = many.doc.querySelector("#ad-host");
  const slides = manyHost.querySelectorAll(".ad-slide");
  const dots = manyHost.querySelectorAll(".ad-dot");
  check("кнопок прокрутки не видно ни на одной странице",
        /\.ad-pager\s*\{[^}]*display:\s*none/.test(AD_CSS));
  check("из двух слайдов показан один", manyHost.querySelectorAll(".ad-slide.on").length === 1);
  dots[1].dispatchEvent(new many.win.MouseEvent("click", { bubbles: true }));
  await wait(80);
  check("клик по точке включает вторую акцию",
        slides[1].classList.contains("on") && !slides[0].classList.contains("on"));
  const next = manyHost.querySelector('.ad-nav[data-ad-nav="1"]');
  check("стрелки на месте, но спрятаны стилем, а не вырезаны из логики",
        !!next && !!next.getAttribute("aria-label") && dots.length === 2);
  if (next) {
    next.dispatchEvent(new many.win.MouseEvent("click", { bubbles: true }));
    await wait(60);
    check("стрелка вперёд на последнем слайде возвращается к первой",
          slides[0].classList.contains("on"));
  }
  const rotated = await until("смена слайда по таймеру",
                              () => slides[1].classList.contains("on"), 6000);
  check("слайды меняются сами, по таймеру из настройки", rotated,
        Array.prototype.map.call(slides, (n) => n.className).join("|"));
  check("пока баннер просто висит, посетителю ничего не сохраняют",
        many.win.localStorage.length === 0, many.win.localStorage.length);

  // --- сетка: одновременно несколько ссылок и картинок --------------------
  const grid = await openLanding({ items: [AD, AD2, Object.assign({}, AD, { id: 12 })],
                                   banner: Object.assign({}, BANNER, { layout: "grid" }) });
  const gridHost = grid.doc.querySelector("#ad-host");
  check("сетка: все объявления в разметке сразу",
        gridHost.getAttribute("data-ad-layout") === "grid" &&
        gridHost.querySelectorAll(".ad-slide").length === 3);
  check("сетка не прячет слайды за прозрачностью",
        /\[data-ad-layout="grid"\][\s\S]{0,200}visibility:\s*visible/.test(AD_CSS) &&
        !gridHost.querySelector(".ad-pager"));

  const soon = await openLanding({
    items: [Object.assign({}, AD, { expires_at: Math.floor(Date.now() / 1000) + 3 })],
  });
  check("баннер показан, пока срок не вышел", soon.doc.querySelector("#ad-host").hidden === false);
  const gone = await until("баннер снят по сроку",
                           () => soon.doc.querySelector("#ad-host").hidden === true, 6000);
  check("срок вышел — баннер исчез сам, без перезагрузки", gone);

  const none = await openLanding({ items: [] });
  check("нет объявлений — на главной пусто", none.doc.querySelector("#ad-host").hidden === true);

  // --- терминал: тот же баннер под основным графиком ---------------------
  const term = await openLanding({ items: [AD], page: "/terminal" });
  const tHost = term.doc.querySelector("#ad-host");
  check("в терминале баннер есть", !!tHost);
  check("терминал сам просит свою выдачу",
        !!tHost && tHost.getAttribute("data-ad-place") === "terminal");
  const tStack = term.doc.querySelector("#chart-stack");
  const tSection = tHost && tHost.closest(".chart-section");
  check("баннер стоит под графиком, а не внутри него",
        !!tSection && !!tStack && tHost.parentElement === tSection &&
        (tStack.compareDocumentPosition(tHost) & 4) === 4 &&
        !tHost.querySelector(".chart-wrapper"),
        "section=" + !!tSection + " stack=" + !!tStack +
        " parent=" + (tHost && tHost.parentElement.className) +
        " pos=" + (tStack && tHost ? tStack.compareDocumentPosition(tHost) : "—"));
  check("в терминале картинка поменьше, чтобы график не съезжал",
        /\[data-ad-place="terminal"\][\s\S]{0,200}--ad-img-max/.test(AD_CSS));
  check("на весь экран и в свёрнутый график реклама не лезет",
        /\.chart-section\.fullscreen \.ad-host,[^{}]*\{[^}]*display:\s*none/.test(AD_CSS));
  check("тот же файл стилей подключён и в терминале",
        !!term.doc.querySelector('link[href^="/static/ads.css"]') &&
        !!term.doc.querySelector('script[src^="/static/ads.js"]'));

  console.log("\n📣 реклама: панель в админке");
  const ads = { snapshot: snapshot() };
  const admin = await openAdmin(ads);
  const doc = admin.doc;

  check("панель рекламы есть в админке", !!doc.querySelector("#ads-panel"));
  const targetKeys = Array.from(doc.querySelectorAll("#ad-targets input[data-ad-target]"))
    .map((i) => i.getAttribute("data-ad-target"));
  check("источники перечислены: бот, главная, терминал, дайджест, сводка, статьи и каналы",
        targetKeys.join(",") ===
          "bot,site,terminal,digest,hourly,articles,ch:-1001112223334,ch:-1005556667778",
        targetKeys.join(","));
  check("поле ссылки по клику есть в форме", !!doc.querySelector("#ad-link"));
  check("настройка баннера в админке есть", !!doc.querySelector("#banner-panel"));
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
  doc.querySelector("#ad-link").value = "https://gate.com/promo";
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
  check("ссылка по клику ушла вместе с постом",
        post && post.body.link === "https://gate.com/promo", post && post.body.link);
  check("терминал выбирается так же, как главная",
        post && post.body.targets.terminal === true, post && JSON.stringify(post.body.targets));
  check("в очереди видно, куда ведёт клик",
        doc.querySelector("#ad-list").textContent.indexOf("gate.com/promo") >= 0);
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

  // --- правка сохранённого: ссылка должна возвращаться в форму ----------
  const editBtn = doc.querySelector('#ad-list button[data-ad-act="edit"]');
  if (editBtn) editBtn.dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  await wait(80);
  check("при правке объявления ссылка возвращается в форму",
        doc.querySelector("#ad-link").value === "https://gate.com/promo",
        doc.querySelector("#ad-link").value);
  check("в терминале отметка стоит так же, как на главной",
        Array.prototype.some.call(doc.querySelectorAll("#ad-targets input[data-ad-target]"),
                                 (i) => i.getAttribute("data-ad-target") === "terminal" && i.checked));
  doc.querySelector("#ad-reset").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));

  // --- настройка смены и плавности ----------------------------------------
  const banRotate = doc.querySelector("#ban-rotate");
  check("настройка баннера предзаполнена со значениями сервера",
        banRotate.value === "8", banRotate.value);
  check("режим и плавность тоже читаются из ответа",
        doc.querySelector("#ban-fade").value === "500" &&
        doc.querySelector("#ban-layout").value === "carousel" &&
        doc.querySelector("#ban-max").value === "4" &&
        doc.querySelector("#ban-fit").value === "contain" &&
        doc.querySelector("#ban-caption").value === "below");
  banRotate.value = "999";
  banRotate.dispatchEvent(new admin.win.Event("change", { bubbles: true }));
  check("несохранённое изменение помечено",
        (doc.querySelector("#ban-status").textContent || "").indexOf("не сохранено") >= 0,
        doc.querySelector("#ban-status").textContent);
  doc.querySelector("#ban-layout").value = "grid";
  doc.querySelector("#ban-max").value = "6";
  doc.querySelector("#ban-save").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  await wait(200);
  const banReq = admin.reqs.filter((r) => r.method === "POST" &&
    r.url.indexOf("/api/admin/ads/banner") === 0).pop();
  check("настройка уходит отдельным запросом, не вместе с постом",
        !!banReq && banReq.body.rotate_sec === 999 && banReq.body.layout === "grid" &&
        banReq.body.max === 6, banReq && JSON.stringify(banReq.body));
  check("поле показало то, что разрешил сервер",
        doc.querySelector("#ban-rotate").value === "120" &&
        doc.querySelector("#ban-max").value === "6",
        doc.querySelector("#ban-rotate").value + "/" + doc.querySelector("#ban-max").value);
  check("под полем написано, что именно сохранилось",
        /rotate_sec=120 сек/.test(doc.querySelector("#ban-status").textContent || ""),
        doc.querySelector("#ban-status").textContent);

  // --- дайджест и сводка по часам: тот же адаптивный баннер --------------
  console.log("\n📣 реклама: баннер на дайджесте и сводке по часам");
  const dig = await openLanding({ items: [AD], page: "/digest" });
  const dHost = dig.doc.querySelector("#ad-host");
  check("в дайджесте баннер есть", !!dHost);
  check("дайджест сам просит свою выдачу",
        !!dHost && dHost.getAttribute("data-ad-place") === "digest",
        dHost && dHost.getAttribute("data-ad-place"));
  check("баннер на дайджесте показан", !!dHost && dHost.hidden === false);
  check("тот же файл стилей подключён и в дайджесте",
        !!dig.doc.querySelector('link[href^="/static/ads.css"]') &&
        !!dig.doc.querySelector('script[src^="/static/ads.js"]'));
  check("в дайджесте картинка не обрезается — тот же contain",
        /\[data-ad-place="digest"\][\s\S]{0,200}--ad-img-max/.test(AD_CSS) ||
        /\.ad-media img[\s\S]{0,200}object-fit:\s*contain/.test(AD_CSS));

  const hour = await openLanding({ items: [AD], page: "/hourly" });
  const hHost = hour.doc.querySelector("#ad-host");
  check("в сводке по часам баннер есть", !!hHost);
  check("сводка сама просит свою выдачу",
        !!hHost && hHost.getAttribute("data-ad-place") === "hourly",
        hHost && hHost.getAttribute("data-ad-place"));
  check("баннер в сводке показан", !!hHost && hHost.hidden === false);
  check("тот же файл стилей подключён и в сводке",
        !!hour.doc.querySelector('link[href^="/static/ads.css"]') &&
        !!hour.doc.querySelector('script[src^="/static/ads.js"]'));

  // --- админка: новые места (digest/hourly) тоже выбираются --------------
  // в уже открытой админке чекбоксы должны быть, а отправка должна нести новые ключи
  const allTargets = Array.from(doc.querySelectorAll("#ad-targets input[data-ad-target]"))
    .map((i) => i.getAttribute("data-ad-target"));
  check("в админке есть таргетинг на дайджест", allTargets.indexOf("digest") >= 0, allTargets.join(","));
  check("в админке есть таргетинг на сводку", allTargets.indexOf("hourly") >= 0, allTargets.join(","));

  // отметим только дайджест и отправим — проверим что уходит именно он
  doc.querySelector("#ad-none").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  var digChk = doc.querySelector('input[data-ad-target="digest"]');
  var hourChk = doc.querySelector('input[data-ad-target="hourly"]');
  if (digChk) digChk.checked = true;
  if (hourChk) hourChk.checked = true;
  doc.querySelector("#ad-save").dispatchEvent(new admin.win.MouseEvent("click", { bubbles: true }));
  await wait(200);
  var last = admin.reqs.filter((r) => r.method === "POST" && r.url.indexOf("/api/admin/ads") === 0).pop();
  check("админка отправляет таргетинг на дайджест", last && last.body.targets.digest === true,
        last && JSON.stringify(last.body.targets));
  check("админка отправляет таргетинг на сводку", last && last.body.targets.hourly === true,
        last && JSON.stringify(last.body.targets));

  console.log("\nошибок в консоли: " + errors.length);
  errors.slice(0, 4).forEach((e) => console.log("  ! " + e));
  console.log(`ИТОГ: ${ok} ок, ${fail} провал(ов)`);
  process.exit(fail || errors.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
