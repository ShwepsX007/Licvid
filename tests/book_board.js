require("./_dom_env");
/** 📖 Кабинет: доска сервиса «Стакан: стены».
 *
 *  Гармошка «book» раскрывается → доска собрана (порог, сторона, монеты,
 *  лента), настройки чипов переключаются и улетают POST'ом на сервер.
 *  После прогона конфиг возвращаем — стенд остаётся в «человеческом» состоянии.
 *
 *  Запуск (сервер на 127.0.0.1:8000, пользователь зарегистрирован):
 *      NODE_PATH=/tmp/smoke/node_modules node tests/book_board.js [url]
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const EMAIL = process.env.LIQSCOPE_TEST_EMAIL || "live@liqscope.online";
const PASSWORD = process.env.LIQSCOPE_TEST_PASSWORD || "licvid-demo-2026";

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function login() {
  const res = await fetch(URL_BASE + "/api/auth/email/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  const setCookie = res.headers.get("set-cookie") || res.headers.getSetCookie?.() || "";
  const cookie = (Array.isArray(setCookie) ? setCookie.join(";") : String(setCookie))
    .split(/,(?=[^;]+=)/)[0].split(";")[0];
  if (!res.ok || !cookie) throw new Error("вход не удался: " + res.status);
  return cookie;
}

async function main() {
  let cookie;
  try { cookie = await login(); } catch (e) {
    console.log("  --   " + e.message);
    console.log("итог: 0 ок, 1 ошибок");
    process.exit(1);
  }
  const api = (path, opts) => fetch(URL_BASE + path,
    Object.assign({}, opts, { headers: { "content-type": "application/json", cookie } }))
    .then((r) => r.json());
  const before = await api("/api/account/book");

  const html = await (await fetch(URL_BASE + "/cabinet", { headers: { cookie } })).text();
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push(a.map(String).join(" ").slice(0, 200)));
  const apiCalls = [];

  const dom = new JSDOM(html, {
    url: URL_BASE + "/cabinet",
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true }); } catch (e) {}
      try { win.localStorage.setItem("liqscope.svc.open", "book"); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) win.ResizeObserver = function () {
        return { observe() {}, unobserve() {}, disconnect() {} };
      };
      win.fetch = async (url, opts) => {
        const u = String(url);
        apiCalls.push({ url: u, method: (opts && opts.method) || "GET", body: (opts && opts.body) || "" });
        const res = await fetch(u.startsWith("http") ? u : URL_BASE + u, Object.assign({}, opts, {
          headers: Object.assign({}, (opts && opts.headers) || {}, { cookie }),
        }));
        const body = await res.json().catch(() => ({}));
        return { ok: res.ok, status: res.status, json: async () => body };
      };
    },
  });

  await sleep(2500);
  const doc = dom.window.document;
  const $ = (id) => doc.getElementById(id);
  const q = (sel) => doc.querySelector(sel);
  const qa = (sel) => Array.prototype.slice.call(doc.querySelectorAll(sel));

  check("сервис «Стакан: стены» в списке", !!q('.svc-fold[data-fold="book"]'));
  check("доска стакана собрана", !!$("book-board") && $("book-board").innerHTML.length > 200,
        $("book-board") ? $("book-board").innerHTML.length : "нет");
  check("запрошен GET /api/account/book",
        apiCalls.some((c) => c.url.indexOf("/api/account/book") === 0));
  const chips = qa("#bk-thr .al-chip");
  check("пять чипов порога", chips.length === 5, chips.length);
  check("лента стен на месте", !!$("book-rows"));
  check("подвал статуса опроса", /опрос/.test(($("book-note") || {}).textContent || ""),
        ($("book-note") || {}).textContent);

  // --- метрики: полоса давления, почасовая лента, чипы жизни -----------------
  const press = $("book-rows").querySelector(".bk-press");
  check("полоса давления bid/ask на доске", !!press &&
        !!press.querySelector(".b") && !!press.querySelector(".a"));
  const hours = $("book-rows").querySelector(".bk-hours");
  check("нагрузка по часам: 24 ячейки", !!hours &&
        hours.querySelectorAll("i").length === 24,
        hours ? hours.querySelectorAll("i").length : "нет ленты");
  check("у часов есть bid/ask сегменты с подписями", !!hours &&
        Array.prototype.every.call(hours.querySelectorAll("i"), (c) =>
          c.querySelectorAll("b.a").length === 1 && c.querySelectorAll("b.b").length === 1 &&
          /·/.test(c.getAttribute("title") || "")));
  check("чипы жизни: медиана, сдутые, спуфы",
        /медиана жизни/.test($("book-rows").textContent) &&
        /сдутых/.test($("book-rows").textContent) &&
        /спуфов/.test($("book-rows").textContent));

  // чип порога → POST с сохранением
  const chip250 = chips.filter((c) => c.getAttribute("data-bk-thr") === "250000")[0];
  chip250.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, view: dom.window }));
  await sleep(900);
  check("клик по чипу ушёл POST'ом",
        apiCalls.some((c) => c.url.indexOf("/api/account/book") === 0 && c.method === "POST" &&
            /250000/.test(c.body)));
  const after = await api("/api/account/book");
  check("порог сохранился на сервере", Number(after.config.min_usd) === 250000,
        JSON.stringify(after.config));
  check("чип подсвечен", chip250.classList.contains("on"));

  // сторона — ask
  const askChip = qa("#bk-side .al-chip").filter((c) => c.getAttribute("data-bk-side") === "ask")[0];
  askChip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, view: dom.window }));
  await sleep(900);
  const after2 = await api("/api/account/book");
  check("сторона «ask» сохранена", after2.config.side === "ask", JSON.stringify(after2.config));

  // ввод монеты по Enter
  const add = $("bk-add");
  add.value = "SOL";
  add.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await sleep(900);
  const after3 = await api("/api/account/book");
  check("монета добавлена (SOL_USDT)", (after3.config.symbols || []).indexOf("SOL_USDT") >= 0,
        JSON.stringify(after3.config.symbols));

  // тумблеры — как у остальных сервисов (al-switch), не галочки
  const swEn = $("bk-enabled"), swNt = $("bk-notify");
  check("включение — ползунок al-switch, а не чекбокс",
        swEn && swEn.classList.contains("al-switch") && !swEn.querySelector("input"));
  check("telegram — тоже ползунок", swNt && swNt.classList.contains("al-switch"));
  const wasOn = swEn.classList.contains("on");
  swEn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, view: dom.window }));
  await sleep(900);
  const after4 = await api("/api/account/book");
  check("клик по ползунку переключил слежение и сохранил",
        after4.config.enabled === !wasOn && swEn.classList.contains("on") === !wasOn,
        JSON.stringify([wasOn, after4.config.enabled]));
  check("подпись ползунка отражает состояние",
        /ВКЛ|ВЫКЛ|ON|OFF/i.test(swEn.textContent));

  // выбор монеты из списка чипов
  const pick = qa("#bk-pick .al-chip");
  check("каталог монет для выбора отрисован", pick.length >= 3, pick.length);
  check("каталог пришёл с сервера (symbols)", Array.isArray(after4.symbols) && after4.symbols.length >= 3);
  const cand = pick.find((c) => !c.classList.contains("on") &&
                                 c.getAttribute("data-bk-pick") !== "SOL_USDT");
  if (cand) {
    const symPick = cand.getAttribute("data-bk-pick");
    cand.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, view: dom.window }));
    await sleep(900);
    const after5 = await api("/api/account/book");
    check("монета из списка добавлена: " + symPick,
          (after5.config.symbols || []).indexOf(symPick) >= 0, JSON.stringify(after5.config.symbols));
    const again = qa("#bk-pick .al-chip").find((c) => c.getAttribute("data-bk-pick") === symPick);
    check("чип в каталоге подсвечен как выбранный", again && again.classList.contains("on"));
    again.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, view: dom.window }));
    await sleep(900);
    const after6 = await api("/api/account/book");
    check("повторный клик убрал монету", (after6.config.symbols || []).indexOf(symPick) < 0);
  }
  check("счётчик монет виден (n / 8)", /\/ 8/.test(($("bk-symcount") || {}).textContent || ""));

  // возврат состояния стенда
  await api("/api/account/book", { method: "POST", body: JSON.stringify(before.config) });
  const back = await api("/api/account/book");
  check("конфиг восстановлен", Number(back.config.min_usd) === Number(before.config.min_usd));

  const real = errors.filter((e) => e.indexOf("Could not load script") === -1 &&
                                   e.indexOf("gtag") === -1);
  check("нет js-ошибок", real.length === 0, real.slice(0, 3).join(" // "));

  await dom.window.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
