/**
 * ☰ Пробный доступ к слоям и окно «зарегистрируйтесь бесплатно».
 *
 * Гость без регистрации видит кнопку слоёв 30 минут: рядом с надписью тикает
 * остаток, а переключатели работают. Когда время вышло (сервер говорит
 * allowed:false) кнопка закрывается, включённые слои гаснут, а на экране
 * появляется окно с предложением бесплатной регистрации. Крестик в углу
 * закрывает окно — терминал работает дальше, но слои остаются выключенными.
 *
 * Проверяем и то, что окно не пристаёт при каждом обновлении страницы: гость
 * закрыл его — значит, выбор сделан (на сессию).
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     node tests/layers_gate.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const TRIAL_LIMIT = 1800;

/** Канва графика в jsdom не рисуется — подменяем контекст заглушкой. */
function stubCanvas(win) {
  const noop = () => {};
  const ctx = new Proxy({}, {
    get(_t, prop) {
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
}

/** Открыть /terminal с подменённым шлюзом (сервер не нужен живым). */
async function openTerminal(trial, { search = "", seed } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("console", () => {});
  const dom = await JSDOM.fromURL(ru(URL_BASE + "/terminal" + search), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      stubCanvas(win);
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      win.WebSocket = function () {
        return { close() {}, send() {}, addEventListener() {}, readyState: 0 };
      };
      // Заглушка сети: /api/auth/me — гость, /api/layers/trial — что скажем
      win.fetch = async (url) => {
        const u = String(url);
        if (u.indexOf("/api/layers/trial") === 0) {
          return { ok: true, status: 200, json: async () => trial };
        }
        if (u.indexOf("/api/auth/me") === 0) {
          return { ok: true, status: 200, json: async () => ({ ok: true, user: null }) };
        }
        if (u.indexOf("/api/klines") === 0) {
          return { ok: true, status: 200, json: async () => ({ ok: true, candles: [] }) };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      };
      if (seed) seed(win);
    },
  });
  await new Promise((r) => setTimeout(r, 3500));
  return dom.window;
}

const click = (win, el) => el.dispatchEvent(
  new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));

const fresh = () => ({ ok: true, guest: true, allowed: true, left_sec: 1795,
                       limit_sec: TRIAL_LIMIT, expired: false, hits: 1 });
const over = () => ({ ok: true, guest: true, allowed: false, left_sec: 0,
                      limit_sec: TRIAL_LIMIT, expired: true, ended_at: 1, hits: 3 });

(async () => {
  // --- 1. Пробник идёт: кнопка на месте, рядом тикает остаток ------------
  const win = await openTerminal(fresh());
  const doc = win.document;
  const call = doc.getElementById("layer-call");
  const badge = doc.getElementById("layer-trial");
  const gate = doc.getElementById("layers-gate");
  const API = win.LiqScopeLayersGate;

  check("окно предложения есть в разметке", !!gate);
  check("окно по умолчанию закрыто", !!gate && gate.classList.contains("hidden"));
  check("кнопка слоёв видна гостю в пробные минуты",
    !!call && !call.classList.contains("hidden"));
  check(" рядом с кнопкой — остаток пробника", !!badge && !badge.classList.contains("hidden"),
    badge && badge.textContent);
  check("остаток показан минутами", !!badge && /30|29|м/.test(badge.textContent),
    badge && badge.textContent);
  check("в подсказке кнопки — сколько осталось",
    !!call && /мин|min/.test(call.title || ""), call && call.title);
  check("тестовый API шлюза на месте",
    !!API && typeof API.expire === "function" && typeof API.isOpen === "function");
  check("счётчик пробника знает остаток",
    !!API && Math.abs(API.left() - 1795) < 1, API && API.left());

  // слои у гостя работают: включаем «Ликвидации» из плашки
  click(win, call);
  const liq = doc.getElementById("liq-toggle");
  check("плашка слоёв открывается", !!win.LiqScopeLayers && win.LiqScopeLayers.isOpen());
  click(win, liq);
  await new Promise((r) => setTimeout(r, 60));
  check("гость может включить слой в пробные минуты", liq.classList.contains("active"));

  // --- 2. Время вышло: кнопка закрывается, слои гаснут, окно открывается --
  API.expire();
  await new Promise((r) => setTimeout(r, 120));
  check("кнопка слоёв закрылась", call.classList.contains("hidden"));
  check("включённый слой выключился", !liq.classList.contains("active"));
  check("окно предложения открылось", API.isOpen() && !gate.classList.contains("hidden"));
  check("шлюз помнит, что доступ закрыт", API.blocked() && !API.allowed());
  check("в окне — большая кнопка регистрации", !!doc.getElementById("gate-cta"));
  check("в окне есть крестик закрытия", !!doc.getElementById("gate-close"));

  const cta = doc.getElementById("gate-cta");
  check("кнопка ведёт на регистрацию и возвращает в терминал",
    /\/login/.test(cta.getAttribute("href")) &&
    /mode=register/.test(cta.getAttribute("href")) &&
    /next=%2Fterminal|\/terminal/.test(cta.getAttribute("href")),
    cta.getAttribute("href"));
  check("регистрация подписана как бесплатная",
    /бесплат/i.test(cta.textContent), cta.textContent);

  // --- 3. Крестик: продолжаем без слоёв ---------------------------------
  click(win, doc.getElementById("gate-close"));
  await new Promise((r) => setTimeout(r, 60));
  check("крестик закрывает окно", !API.isOpen() && gate.classList.contains("hidden"));
  check("кнопка слоёв остаётся закрытой", call.classList.contains("hidden"));
  check("терминал продолжает работать без слоёв", API.blocked());
  // повторное обновление страницы в той же сессии окно не возвращает
  check("отказ запомнен на сессию (окно не пристаёт)",
    win.sessionStorage.getItem("liqscope.gate.dismissed") === "1");

  // --- 4. Вторая ссылка — «Продолжить без слоёв» ------------------------
  win.LiqScopeI18n.set("en");
  await new Promise((r) => setTimeout(r, 120));
  check("на английском кнопка регистрации переведена",
    /Register for free/i.test(cta.textContent), cta.textContent);
  check("на английском текст окна переведён",
    /free minutes/i.test(gate.textContent), gate.textContent.slice(0, 120));

  await win.close();

  // --- 5. Пробник уже был истрачен: окно показывается сразу -------------
  const win2 = await openTerminal(over());
  const doc2 = win2.document;
  const API2 = win2.LiqScopeLayersGate;
  check("вернувшемуся гостю кнопка слоёв не показывается",
    doc2.getElementById("layer-call").classList.contains("hidden"));
  check("вернувшемуся гостю сразу предлагают регистрацию",
    !!API2 && API2.isOpen() && API2.blocked());
  // «Продолжить без слоёв» работает так же, как крестик
  click(win2, doc2.getElementById("gate-later"));
  await new Promise((r) => setTimeout(r, 60));
  check("«продолжить без слоёв» закрывает окно", !API2.isOpen());
  check("после отказа слои не включаются",
    !doc2.getElementById("liq-toggle").classList.contains("active"));
  check("и кнопка слоёв так и не появляется",
    doc2.getElementById("layer-call").classList.contains("hidden"));
  await win2.close();

  // --- 6. Языки: окно переведено на все пять ---------------------------
  const zh = await openTerminal(fresh());
  zh.LiqScopeI18n.set("zh");
  await new Promise((r) => setTimeout(r, 200));
  const gateZh = zh.document.getElementById("layers-gate");
  const ctaZh = zh.document.getElementById("gate-cta");
  check("китайский: заголовок окна переведён", /注册/.test(gateZh.textContent),
    gateZh.textContent.slice(0, 60));
  check("китайский: кнопка регистрации переведена", /注册/.test(ctaZh.textContent),
    ctaZh.textContent);
  check("китайский: остаток пробника переведён",
    /分/.test((zh.document.getElementById("layer-trial") || {}).textContent || ""),
    (zh.document.getElementById("layer-trial") || {}).textContent);
  await zh.close();

  check("ошибок страницы нет", errors.length === 0, errors.join(" | "));

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("  FAIL исключение: " + e.message); process.exit(1); });
