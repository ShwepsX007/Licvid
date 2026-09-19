/**
 * Кабинет на других языках: сервисы целиком — заголовок и описание внутри.
 *
 * Самый важный тест перевода: кабинет рисуется кодом (account.js) и данными
 * сервера, поэтому проверяем не словари, а готовую страницу — раскрываем
 * каждую гармошку сервиса и ищем русский текст в видимых узлах. Текст
 * пользователя (переписка, поля ввода) из проверки исключён: он помечен
 * атрибутом data-i18n-skip.
 *
 * Запуск (сервер уже на 127.0.0.1:8000, вход — подтверждённая почта):
 *     LIQSCOPE_TEST_EMAIL=... LIQSCOPE_TEST_PASSWORD=... \
 *         NODE_PATH=/tmp/smoke/node_modules node tests/i18n_cabinet.js
 *
 * Без переменных окружения тест берёт демо-доступ из LIQSCOPE_TEST_* и,
 * если входа нет, честно сообщает об этом (стенд может быть пустым).
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const EMAIL = process.env.LIQSCOPE_TEST_EMAIL || "";
const PASSWORD = process.env.LIQSCOPE_TEST_PASSWORD || "";

const LANGS = ["en", "zh", "hi", "es"];
const CYR = /[А-Яа-яЁё]/;

let ok = 0, fail = 0;
const errors = [];
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function noopCtx() {
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

async function login() {
  const res = await fetch(URL_BASE + "/api/auth/email/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  const raw = res.headers.get("set-cookie") || res.headers.getSetCookie?.() || "";
  const cookie = (Array.isArray(raw) ? raw.join(";") : String(raw))
    .split(/,(?=[^;]+=)/)[0].split(";")[0];
  if (!res.ok || !cookie) throw new Error("вход не удался: " + res.status);
  return cookie;
}

/** Все видимые русские куски страницы, кроме текста пользователя. */
function russianBits(doc) {
  const out = [];
  const walk = (node) => {
    if (node.nodeType === 3) {
      const text = node.nodeValue || "";
      if (CYR.test(text) && !isSkipped(node.parentNode)) out.push(text.trim().slice(0, 90));
      return;
    }
    if (node.nodeType !== 1) return;
    if (isSkipped(node)) return;
    const tag = node.tagName;
    if (tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA" || tag === "INPUT") return;
    // свитчер языка: названия языков пишутся на самих себе
    if (tag === "SELECT" && node.hasAttribute("data-lang-select")) return;
    if (tag === "OPTION" && node.parentNode && node.parentNode.id === "lang-select") return;
    if (node.getAttribute && node.hasAttribute("data-i18n-skip")) return;
    for (let i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i]);
  };
  const isSkipped = (node) => {
    while (node && node.nodeType === 1) {
      if (node.hasAttribute && node.hasAttribute("data-i18n-skip")) return true;
      node = node.parentNode;
    }
    return false;
  };
  walk(doc.body);
  return out;
}

async function openCabinet(lang, cookie, path) {
  path = path || "/cabinet";
  const html = await (await fetch(URL_BASE + path + "?lang=" + lang, { headers: { cookie } })).text();
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 160));
  });
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 160)));
  const dom = new JSDOM(html, {
    url: URL_BASE + path,
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
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.fetch = async (url, opts) => {
        const u = String(url);
        const headers = Object.assign({}, (opts && opts.headers) || {}, { cookie });
        const res = await fetch(u.startsWith("http") ? u : URL_BASE + u,
                                Object.assign({}, opts, { headers }));
        const body = await res.json().catch(() => ({}));
        return { ok: res.ok, status: res.status, json: async () => body };
      };
    },
  });
  await sleep(2200);
  return dom.window;
}

async function main() {
  console.log("кабинет: сервисы на других языках");
  if (!EMAIL || !PASSWORD) {
    console.log("  --   нет LIQSCOPE_TEST_EMAIL/LIQSCOPE_TEST_PASSWORD — тест пропущен");
    process.exit(0);
  }
  let cookie;
  try {
    cookie = await login();
  } catch (e) {
    console.log("  --   " + e.message + " — тест пропущен");
    process.exit(0);
  }

  for (const lang of LANGS) {
    const win = await openCabinet(lang, cookie);
    const doc = win.document;
    const folds = Array.prototype.slice.call(doc.querySelectorAll(".svc-fold"));
    check(`[${lang}] сервисы в кабинете отрисованы (${folds.length})`, folds.length >= 4, folds.length);

    // раскрываем все сервисы: заголовок и содержимое панели
    for (const f of folds) {
      const btn = f.querySelector("[data-toggle]");
      if (btn && !f.classList.contains("open")) {
        btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
      }
    }
    await sleep(1400);

    const titles = folds.map((f) => {
      const t = f.querySelector(".svc-title, h3, summary");
      return t ? t.textContent.trim() : "";
    });
    check(`[${lang}] заголовки сервисов без русского`, !titles.some((t) => CYR.test(t)),
      titles.filter((t) => CYR.test(t)).join(" | "));

    const boardText = ["alerts-board", "corr-board", "pump-board"]
      .map((id) => doc.getElementById(id))
      .filter(Boolean)
      .map((el) => el.textContent)
      .join(" ");
    check(`[${lang}] доски сервисов без русского`, !CYR.test(boardText),
      (boardText.match(/[\u0410-\u044f\u0401\u0451][^.!?]{0,60}/) || [""])[0]);

    const left = russianBits(doc);
    check(`[${lang}] на странице не осталось русского текста`, left.length === 0,
      left.slice(0, 6).join(" ¶ "));
    win.close();
  }

  // админка — тот же набор панелей, что у сервисов: если вход админский,
  // проверяем и её (внутренняя страница, но перевод нужен и там)
  const winA = await openCabinet("en", cookie, "/admin");
  const docA = winA.document;
  if (docA.querySelector("#admin-svc")) {
    check("[admin] панели отрисованы", !!docA.querySelector("#bot-admin"));
    const leftA = russianBits(docA).filter((t) => t.length > 1);
    check("[admin] на странице не осталось русского текста", leftA.length === 0,
      leftA.slice(0, 5).join(" ¶ "));
  }
  winA.close();

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  errors.slice(0, 6).forEach((e) => console.log("  ! " + e));
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("Тест упал:", e.message); process.exit(1); });
