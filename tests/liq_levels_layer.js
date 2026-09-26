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

  // --- доска кабинета: настоящий рендер, а не поиск строк в исходнике ------
  // Регресс: в шапке таблицы стоял вызов T() — в account.js это словарь, не
  // функция. Рендер падал, и в сервисе не было ни одной строки уровней.
  const EMAIL = process.env.LIQSCOPE_TEST_EMAIL || "";
  const PASSWORD = process.env.LIQSCOPE_TEST_PASSWORD || "";

  async function loginCookie() {
    const res = await fetch(URL_BASE + "/api/auth/email/login", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    });
    const raw = res.headers.get("set-cookie") || res.headers.getSetCookie?.() || "";
    const cookie = (Array.isArray(raw) ? raw.join(";") : String(raw))
      .split(/,(?=[^;]+=)/)[0].split(";")[0];
    return res.ok && cookie ? cookie : "";
  }

  /** Кабинет в jsdom: живой расчёт или «сервер без уровней» (режим bad). */
  async function openCabinet(cookie, opts) {
    opts = opts || {};
    const html = await (await fetch(URL_BASE + "/cabinet", { headers: { cookie } })).text();
    const seen = [];
    const errors2 = [];
    const vc2 = new VirtualConsole();
    vc2.on("jsdomError", (e) => {
      const msg = String((e && e.message) || e);
      if (msg.includes("fonts.googleapis.com") || msg.includes("Not implemented")) return;
      errors2.push(msg);
    });
    vc2.on("error", (...a) => errors2.push(a.map(String).join(" ").slice(0, 160)));
    const dom2 = new JSDOM(html, {
      url: URL_BASE + "/cabinet", runScripts: "dangerously", resources: "usable",
      pretendToBeVisual: true, virtualConsole: vc2,
      beforeParse(win) {
        win.matchMedia = () => ({ matches: false, media: "", onchange: null,
          addListener() {}, removeListener() {}, addEventListener() {},
          removeEventListener() {}, dispatchEvent() { return false; } });
        if (!win.ResizeObserver) {
          win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
        }
        const dummy = noopCtx();
        win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
        try { win.localStorage.setItem("liqscope.svc.open", "levels"); } catch (e) { /* ignore */ }
        win.fetch = async (url, o) => {
          const u = String(url);
          const headers = Object.assign({}, (o && o.headers) || {}, { cookie });
          if (u.includes("/api/liq_levels")) seen.push(u);
          if (u.includes("/api/liq_levels") && opts.failLevels) {
            return { ok: false, status: 404, json: async () => ({ ok: false, error: "not found" }),
                     text: async () => "not found" };
          }
          const res = await fetch(u.startsWith("http") ? u : URL_BASE + u,
                                  Object.assign({}, o, { headers }));
          const body = await res.json().catch(() => ({}));
          if (opts.hideService && u.includes("/api/account/services") && Array.isArray(body.services)) {
            body.services = body.services.filter((x) => x.slug !== "levels");
          }
          return { ok: res.ok, status: res.status, json: async () => body };
        };
      },
    });
    await sleep(3000);
    return { dom: dom2, doc: dom2.window.document, seen, errors: errors2 };
  }

  if (!EMAIL || !PASSWORD) {
    console.log("  --   нет LIQSCOPE_TEST_EMAIL/LIQSCOPE_TEST_PASSWORD — доска кабинета не проверена");
  } else {
    const cookie = await loginCookie();
    check("вход в кабинет для проверки доски", !!cookie);
    if (cookie) {
      const live = await openCabinet(cookie, {});
      const board = live.doc.getElementById("levels-board");
      const rows = board ? board.querySelectorAll(".lv-row").length : 0;
      const head = board ? board.querySelector(".lv-row.lv-head") : null;
      check("доска уровней отрисовала таблицу", rows > 1, "строк: " + rows);
      check("в шапке таблицы шесть колонок",
            !!(head && head.querySelectorAll("span").length === 6));
      const first = board && board.querySelector(".lv-row:not(.lv-head)");
      const firstTxt = first ? first.textContent.replace(/\s+/g, " ") : "";
      check("в строке есть цена, масса и плечо",
            /\$[\d.,]+/.test(firstTxt) && /x/.test(firstTxt), firstTxt.slice(0, 60));
      const status = live.doc.getElementById("lv-status");
      check("статус доски рассказывает про цену и уровни",
            !!(status && /\$/.test(status.textContent) && status.textContent.length > 5),
            status ? status.textContent.slice(0, 60) : "-");
      check("оговорки на доске есть",
            !!board && board.querySelectorAll(".lv-note").length > 0);
      check("запрос доски ушёл по монете",
            live.seen.some((u) => u.includes("symbol=")), live.seen.slice(0, 2).join(" "));
      live.dom.window.close();

      // «Старый сервер»: в списке сервисов нет строки levels, расчёт — 404
      const stale = await openCabinet(cookie, { hideService: true, failLevels: true });
      const fold = stale.doc.querySelector('.svc-fold[data-fold="levels"]');
      check("карточка уровней есть даже без строки в базе", !!fold);
      const b2 = stale.doc.getElementById("levels-board");
      const t2 = b2 ? (b2.textContent || "").replace(/\s+/g, " ") : "";
      check("доска объясняет, что сервер не отдал расчёт",
            /404/.test(t2) && /(перезапу|restart|重启|रीस्टार्ट|reinici)/i.test(t2), t2.slice(0, 120));
      stale.dom.window.close();
    }
  }

  // --- подсказка над графиком, когда рисовать нечего ----------------------
  async function openTerminal(failLevels, emptyLevels) {
    const html = await (await fetch(URL_BASE + "/terminal")).text();
    const notes = [];
    const vc3 = new VirtualConsole();
    vc3.on("jsdomError", (e) => {
      const msg = String((e && e.message) || e);
      if (msg.includes("fonts.googleapis.com") || msg.includes("Not implemented")) return;
      notes.push(msg);
    });
    const dom3 = new JSDOM(html, {
      url: URL_BASE + "/terminal", runScripts: "dangerously", resources: "usable",
      pretendToBeVisual: true, virtualConsole: vc3,
      beforeParse(win) {
        win.matchMedia = () => ({ matches: false, media: "", onchange: null,
          addListener() {}, removeListener() {}, addEventListener() {},
          removeEventListener() {}, dispatchEvent() { return false; } });
        if (!win.ResizeObserver) {
          win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
        }
        const dummy = noopCtx();
        win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
        win.WebSocket = function () { return { readyState: 3, send() {}, close() {} }; };
        win.WebSocket.OPEN = 1; win.WebSocket.CLOSED = 3;
        try {
          win.localStorage.setItem("liqscope.lang", "ru");
          win.localStorage.setItem("liqscope.devLayers", "1");
          win.localStorage.removeItem("liqscope.levelsEnabled");
          win.localStorage.setItem("liqscope.chartSymbol", "BTC_USDT");
        } catch (e) { /* ignore */ }
        win.fetch = async (url, o) => {
          const u = String(url);
          if (u.includes("/api/liq_levels") && failLevels) {
            return { ok: false, status: 404, json: async () => ({ ok: false, error: "not found" }),
                     text: async () => "not found" };
          }
          if (u.includes("/api/liq_levels") && emptyLevels) {
            return { ok: true, status: 200, text: async () => "{}",
                     json: async () => ({ ok: true, enabled: true, estimate: true, price: null,
                       levels: [], magnets: { up: null, down: null }, magnets_list: [],
                       cumulative: { up: [], down: [] }, total_usd: 0, long_usd: 0, short_usd: 0,
                       totals: {}, applied: {}, coverage: { rows: 0, oi_points: 0, events: 0 },
                       calibration: { applied: false }, notes: ["нет цены для расчёта"] }) };
          }
          const res = await fetch(u.startsWith("http") ? u : URL_BASE + u, o);
          return { ok: res.ok, status: res.status, json: async () => res.json().catch(() => ({})),
                   text: async () => "" };
        };
      },
    });
    await sleep(4500);
    const doc3 = dom3.window.document;
    const btn = doc3.getElementById("levels-toggle");
    if (btn) btn.dispatchEvent(new dom3.window.MouseEvent("click", { bubbles: true }));
    await sleep(2500);
    const note = doc3.getElementById("levels-note");
    const out = {
      dom: dom3.window,
      text: note ? (note.textContent || "") : "",
      hidden: note ? note.classList.contains("hidden") : true,
    };
    dom3.window.close();
    return out;
  }

  const broken = await openTerminal(true, false);
  check("при 404 на графике появляется подсказка слоя", !broken.hidden && !!broken.text,
        JSON.stringify(broken.text.slice(0, 80)));
  check("подсказка называет ошибку сервера",
        /404/.test(broken.text) && /(перезапу|restart)/i.test(broken.text),
        broken.text.slice(0, 120));

  const nodata = await openTerminal(false, true);
  check("при пустом расчёте подсказка объясняет накопление данных",
        !nodata.hidden && /(нет данных|no data|暂无数据)/i.test(nodata.text),
        nodata.text.slice(0, 120));

  const healthy = await openTerminal(false, false);
  check("при живом расчёте подсказка скрыта", healthy.hidden === true,
        JSON.stringify(healthy.text.slice(0, 60)));

  // --- админка: карточка настроек расчёта ----------------------------------
  if (EMAIL && PASSWORD) {
    const cookie = await loginCookie();
    const html = await (await fetch(URL_BASE + "/admin", { headers: { cookie } })).text();
    const saved = [];
    const vc4 = new VirtualConsole();
    vc4.on("jsdomError", (e) => {
      const msg = String((e && e.message) || e);
      if (msg.includes("fonts.googleapis.com") || msg.includes("Not implemented")) return;
      errors.push(msg);
    });
    const dom4 = new JSDOM(html, {
      url: URL_BASE + "/admin", runScripts: "dangerously", resources: "usable",
      pretendToBeVisual: true, virtualConsole: vc4,
      beforeParse(win) {
        win.matchMedia = () => ({ matches: false, media: "", onchange: null,
          addListener() {}, removeListener() {}, addEventListener() {},
          removeEventListener() {}, dispatchEvent() { return false; } });
        if (!win.ResizeObserver) {
          win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
        }
        const dummy = noopCtx();
        win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
        win.fetch = async (url, o) => {
          const u = String(url);
          const headers = Object.assign({}, (o && o.headers) || {}, { cookie });
          const res = await fetch(u.startsWith("http") ? u : URL_BASE + u,
                                  Object.assign({}, o, { headers }));
          const body = await res.json().catch(() => ({}));
          if (u.includes("/api/admin/liq_levels/settings") && o && o.method === "POST") {
            saved.push(String(o.body || ""));
          }
          return { ok: res.ok, status: res.status, json: async () => body, text: async () => "" };
        };
      },
    });
    await sleep(3000);
    const doc4 = dom4.window.document;
    const card = doc4.getElementById("levels-card");
    check("в админке есть карточка расчёта уровней", !!card);
    const fields = card ? card.querySelectorAll("[data-lv-f]") : [];
    check("форма настроек построена из ответа сервера", fields.length >= 10,
          "полей: " + fields.length);
    const winEl = card && card.querySelector('[data-lv-f="window_hours"]');
    check("в поле окна стоит текущее значение",
          !!(winEl && Number(winEl.value) > 0), winEl ? winEl.value : "-");
    const distEl = card && card.querySelector('[data-lv-f="lev_dist"]');
    check("распределение плеч показано списком пар",
          !!(distEl && /\[/.test(distEl.value)), distEl ? distEl.value.slice(0, 40) : "-");
    const noteEl = doc4.getElementById("levels-note");
    check("под карточкой видно состояние движка",
          !!(noteEl && noteEl.textContent.length > 10), noteEl ? noteEl.textContent.slice(0, 60) : "-");
    const saveBtn = doc4.getElementById("levels-save");
    if (saveBtn && winEl) {
      saveBtn.click();                      // сохраняем те же значения — стенд не меняем
      await sleep(2000);
      check("кнопка сохраняет настройки на сервер", saved.length >= 1,
            "POST-ов: " + saved.length);
      check("в теле запроса уходит окно расчёта",
            saved.some((b) => /window_hours/.test(b)), saved.slice(0, 1).join("").slice(0, 80));
      const st4 = doc4.getElementById("levels-status");
      check("админка подтверждает сохранение",
            !!(st4 && st4.textContent.length > 3), st4 ? st4.textContent.slice(0, 60) : "-");
    }
    dom4.window.close();
  }

  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  if (errors.length) console.log("JS errors: " + JSON.stringify(errors.slice(0, 5), null, 2));
  dom.window.close();
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.error("тест упал:", e);
  process.exit(1);
});
