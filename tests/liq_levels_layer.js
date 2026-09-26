require("./_dom_env");
/**
 * Слой «🎯 Уровни ликвидаций (оценка)» на графике и панель в кабинете.
 *
 *  Проверяем то, ради чего слой делался: кнопка в плашке слоёв подписана
 *  «оценка», легенда объясняет расчёт, при включении слой сам просит
 *  /api/liq_levels именно по монете графика, повторно — не чаще TTL, а при
 *  выключении поллер гаснет и данные чистятся. Плюс серверный контракт ответа
 *  (levels[]/magnets/cumulative/coverage/estimate) и панель кабинета:
 *  в гармошке есть сервис «Уровни ликвидаций», а доска тянет тот же расчёт.
 *
 *  Запуск (сервер уже поднят, демо-режим):
 *      NODE_PATH=./node_modules node tests/liq_levels_layer.js http://127.0.0.1:8000
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");
const path = require("path");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const ROOT = path.dirname(__dirname);

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
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

(async () => {
  // --- контракт серверного расчёта ---------------------------------------
  const api = await (await fetch(URL_BASE + "/api/liq_levels?symbol=BTC_USDT&price=60000")).json();
  check("GET /api/liq_levels отвечает ok", api.ok === true);
  check("ответ помечен как оценка", api.estimate === true && api.enabled !== undefined);
  for (const key of ["levels", "magnets", "cumulative", "coverage", "calibration"]) {
    check("в ответе есть " + key, api[key] !== undefined && api[key] !== null);
  }
  check("coverage называет источники стороны",
        typeof api.coverage.side_sources === "object");
  check("калибровка объясняет себя", typeof api.calibration.applied === "boolean");

  // --- страница терминала: кнопка, легенда, поллер -------------------------
  let levelsCalls = 0;
  const called = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String(e.message || e);
    if (msg.includes("fonts.googleapis.com")) return;   // сеть песочницы закрыта
    errors.push(msg);
  });
  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      win.WebSocket = function () { return { readyState: 3, send() {}, close() {} }; };
      win.WebSocket.OPEN = 1; win.WebSocket.CLOSED = 3;
      // настоящая сеть — до локального стенда; считаем обращения к уровням
      win.fetch = (u, opt) => {
        const url = String(u);
        if (url.includes("/api/liq_levels")) { levelsCalls++; called.push(url); }
        const abs = url.startsWith("http") ? url : URL_BASE + url;
        return fetch(abs, opt);
      };
      try {
        win.localStorage.setItem("liqscope.lang", "ru");
        win.localStorage.setItem("liqscope.devLayers", "1");
        win.localStorage.removeItem("liqscope.levelsEnabled");
        win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT");
      } catch (e) { /* ignore */ }
    },
  });
  await sleep(5000);
  const doc = dom.window.document;
  const btn = doc.getElementById("levels-toggle");
  check("кнопка слоя есть в плашке", !!btn);
  check("кнопка подписана «оценка»", !!(btn && /оценк/i.test(btn.textContent)));
  check("кнопка живёт в плашке слоёв",
        !!(btn && btn.closest("#layer-pop")));
  const legend = doc.getElementById("layer-legend");
  check("легенда объясняет уровни", !!(legend && /Уровни — оценка/.test(legend.textContent)));
  const legendTitle = legend && legend.querySelector('[data-i18n-title$="levels_est_title"]');
  check("у легенды есть пояснение title", !!legendTitle);
  check("слой по умолчанию выключен",
        !(btn && btn.classList.contains("active")));

  if (btn) {
    btn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    await sleep(1200);
    check("включение слоя просит расчёт у сервера", levelsCalls >= 1,
          "вызовов: " + levelsCalls);
    check("запрос идёт по монете графика",
          called.some((u) => u.includes("symbol=BTC_USDT")), called.slice(-3).join(" "));
    check("кнопка стала активной", btn.classList.contains("active"));
    check("слой запоминается в localStorage",
          dom.window.localStorage.getItem("liqscope.levelsEnabled") === "1");

    const before = levelsCalls;
    await sleep(900);
    check("повторный запрос не раньше TTL (45 с)", levelsCalls === before,
          "было " + before + ", стало " + levelsCalls);

    btn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    await sleep(700);
    const after = levelsCalls;
    await sleep(900);
    check("выключение гасит поллер", levelsCalls === after,
          "было " + after + ", стало " + levelsCalls);
    check("слой забыт в localStorage",
          dom.window.localStorage.getItem("liqscope.levelsEnabled") === "0");
  }

  // --- кабинет: сервис и доска --------------------------------------------
  const cab = await fetch(URL_BASE + "/cabinet");
  const cabHtml = await cab.text();
  check("кабинет отдаётся", cab.status === 200 && cabHtml.length > 500);
  const accountJs = fs.readFileSync(path.join(ROOT, "static", "account.js"), "utf8");
  check("доска уровней подключена к гармошке", accountJs.includes('id="levels-board"'));
  check("доска поднимается своим хуком",
        accountJs.includes("bootLevels") && accountJs.includes("loadLevels"));
  check("доска берёт тот же расчёт", accountJs.includes("/api/liq_levels"));
  check("в кабинете спрашивается калибровка",
        accountJs.includes("calibration") && accountJs.includes("coverage"));
  const css = fs.readFileSync(path.join(ROOT, "static", "style.css"), "utf8");
  check("у доски есть стили", css.includes(".lv-badge") && css.includes(".lv-row"));

  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  if (errors.length) console.log("JS errors: " + JSON.stringify(errors.slice(0, 5), null, 2));
  dom.window.close();
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.error("тест упал:", e);
  process.exit(1);
});
