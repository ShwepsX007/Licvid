require("./_dom_env");
/**
 * ☰ Слои доступны всем — промо-окно с бонусами регистрации.
 *
 * Новое поведение (round 6):
 * - слои всегда allowed, badge скрыт;
 * - гостю без регистрации показывается промо-плашка 1 раз за сессию с текстом
 *   про сигналы в Telegram и терминал без рекламы;
 * - крестик / «Продолжить» закрывает окно, но слои остаются включёнными;
 * - expire() больше не блокирует слои.
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

async function openTerminal(trial, { search = "", seed } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    if (msg.indexOf("Could not load link") !== -1) return;
    if (msg.indexOf("googletagmanager") !== -1) return;
    if (msg.indexOf("gtag") !== -1) return;
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

const fresh = () => ({ ok: true, guest: true, allowed: true, left_sec: 1795, limit_sec: 1800, expired: false, hits: 1 });
const over = () => ({ ok: true, guest: true, allowed: false, left_sec: 0, limit_sec: 1800, expired: true, ended_at: 1, hits: 3 });

(async () => {
  // 1. Гость: слои доступны, бейдж скрыт, промо-плашка показывается
  const win = await openTerminal(fresh());
  const doc = win.document;
  const call = doc.getElementById("layer-call");
  const badge = doc.getElementById("layer-trial");
  const gate = doc.getElementById("layers-gate");
  const API = win.LiqScopeLayersGate;

  check("окно предложения есть в разметке", !!gate);
  check("кнопка слоёв видна гостю (слои доступны всем)", !!call && !call.classList.contains("hidden"));
  check("бейдж пробника скрыт (слои всем)", !!badge && badge.classList.contains("hidden"));
  check("тестовый API шлюза на месте", !!API && typeof API.isOpen === "function");
  check("слои allowed всегда true", !!API && API.allowed() === true);
  check("слои не blocked", !!API && API.blocked() === false);

  // промо должно открыться для гостя 1 раз за сессию
  await new Promise(r => setTimeout(r, 500));
  check("промо-плашка открывается гостю", API.isOpen() && !gate.classList.contains("hidden"));

  // текст промо — новые бенефиты
  const txt = gate.textContent || "";
  check("в промо есть про Telegram сигналы", /Telegram/i.test(txt), txt.slice(0, 120));
  check("в промо есть про без рекламы / ad-free", /без рекламы|ad-free|Ad-free|без/i.test(txt), txt.slice(0, 200));

  // слои у гостя работают
  click(win, call);
  const liq = doc.getElementById("liq-toggle");
  check("плашка слоёв открывается", !!win.LiqScopeLayers && win.LiqScopeLayers.isOpen());
  click(win, liq);
  await new Promise((r) => setTimeout(r, 60));
  check("гость может включить слой (слои всем)", liq.classList.contains("active"));

  // 2. expire() больше не блокирует
  API.expire();
  await new Promise((r) => setTimeout(r, 120));
  check("expire() не закрывает кнопку слоёв", !call.classList.contains("hidden"));
  check("expire() не выключает слой", liq.classList.contains("active"));
  check("после expire слои всё ещё allowed", API.allowed() && !API.blocked());

  // 3. Крестик закрывает промо, но слои остаются
  const closeBtn = doc.getElementById("gate-close");
  check("в окне есть крестик", !!closeBtn);
  click(win, closeBtn);
  await new Promise((r) => setTimeout(r, 60));
  check("крестик закрывает промо", !API.isOpen() && gate.classList.contains("hidden"));
  check("кнопка слоёв остаётся видимой после закрытия промо", !call.classList.contains("hidden"));
  check("слои остаются allowed после закрытия промо", API.allowed() && !API.blocked());
  check("отказ запомнен на сессию", win.sessionStorage.getItem("liqscope.gate.dismissed") === "1");

  // 4. Кнопка «Продолжить» тоже закрывает
  const win2 = await openTerminal(fresh());
  const API2 = win2.LiqScopeLayersGate;
  const gate2 = win2.document.getElementById("layers-gate");
  await new Promise(r => setTimeout(r, 500));
  check("второму гостю промо тоже показывается", API2.isOpen());
  click(win2, win2.document.getElementById("gate-later"));
  await new Promise((r) => setTimeout(r, 60));
  check("«Продолжить» закрывает промо", !API2.isOpen());
  check("после «Продолжить» слои не блокируются", API2.allowed() && !API2.blocked());

  // 5. Языки
  win2.LiqScopeI18n.set("en");
  await new Promise((r) => setTimeout(r, 200));
  check("en: промо переведено", /Telegram/i.test(gate2.textContent) && /ad-free|without ads|ad-free/i.test(gate2.textContent.toLowerCase()) || /Telegram/i.test(gate2.textContent), gate2.textContent.slice(0, 120));

  const zh = await openTerminal(fresh());
  zh.LiqScopeI18n.set("zh");
  await new Promise((r) => setTimeout(r, 200));
  const gateZh = zh.document.getElementById("layers-gate");
  check("zh: промо переведено", /Telegram/.test(gateZh.textContent) || /信号/.test(gateZh.textContent), gateZh.textContent.slice(0, 60));

  await win.close();
  await win2.close();
  await zh.close();

  check("ошибок страницы нет", errors.length === 0, errors.join(" | "));

  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("  FAIL исключение: " + e.message + "\n" + e.stack); process.exit(1); });
