/**
 * Кабинет сайта: доска «Алерты по объёму» и её окна по метрикам.
 *
 *  Требование: у ликвидаций, CVD и OI окно агрегации своё — и в боте, и в
 *  кабинете. Плюс главное правило против повторов: после сигнала окно метрики
 *  начинается заново, в следующее сообщение попадают только новые данные.
 *
 *  Проверяем связку целиком: API отдаёт windows по метрикам и превью,
 *  посчитанное от прошлого сигнала; на доске — по чипсам окон в каждой
 *  карточке метрики; клик по чипсу сохраняет окно ТОЛЬКО этой метрики;
 *  старые настройки (одно число window_min) читаются и раскладываются.
 *
 *  Запуск (сервер уже на 127.0.0.1:8011, демо-режим):
 *      NODE_PATH=/tmp/smoke/node_modules node tests/alerts_board.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8011";
const EMAIL = process.env.LIQSCOPE_TEST_EMAIL || "live@liqscope.online";
const PASSWORD = process.env.LIQSCOPE_TEST_PASSWORD || "licvid-demo-2026";
const DEFAULTS = { liq: 5, cvd: 15, oi: 60 };

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
  const setCookie = res.headers.get("set-cookie") || res.headers.getSetCookie?.() || "";
  const cookie = (Array.isArray(setCookie) ? setCookie.join(";") : String(setCookie))
    .split(/,(?=[^;]+=)/)[0].split(";")[0];
  if (!res.ok || !cookie) {
    const body = await res.text();
    throw new Error("вход в кабинет не удался: " + res.status + " " + body.slice(0, 160));
  }
  return cookie;
}

async function api(cookie, body) {
  const res = await fetch(URL_BASE + "/api/account/alerts", {
    method: "POST",
    headers: { "content-type": "application/json", cookie },
    body: JSON.stringify(body),
  });
  return await res.json();
}

async function main() {
  let cookie;
  try {
    cookie = await login();
  } catch (e) {
    console.log("  --   " + e.message);
    console.log("итог: 0 ок, 1 ошибок");
    process.exit(1);
  }

  // --- API: окна по метрикам ------------------------------------------------
  const base = await api(cookie, {});
  check("настройки алертов сохраняются и возвращаются",
        base.ok === true, JSON.stringify(base).slice(0, 120));
  check("окна по умолчанию — у каждой метрики своё",
        base.config && JSON.stringify(base.config.windows) === JSON.stringify(DEFAULTS),
        base.config && JSON.stringify(base.config.windows));
  check("старое поле window_min равно окну ликвидаций",
        base.config && base.config.window_min === DEFAULTS.liq, base.config && base.config.window_min);

  const got = await (await fetch(URL_BASE + "/api/account/alerts", { headers: { cookie } })).json();
  check("GET отдаёт те же окна", got.config && JSON.stringify(got.config.windows) ===
        JSON.stringify(DEFAULTS), JSON.stringify(got.config && got.config.windows));
  check("пресеты окон — пятёрка (кнопки в боте и в кабинете совпадают)",
        got.presets && JSON.stringify(got.presets.windows) === JSON.stringify([1, 15, 30, 60, 240]),
        got.presets && JSON.stringify(got.presets.windows));
  check("превью по каждой метрике со своим окном и остатком окна",
        got.live && got.live.liq && got.live.liq.window_min === 5 &&
        got.live.cvd && got.live.cvd.window_min === 15 &&
        got.live.oi && got.live.oi.window_min === 60 &&
        typeof got.live.liq.span_min === "number",
        got.live ? [got.live.liq.window_min, got.live.cvd.window_min, got.live.oi.window_min,
                    got.live.liq.span_min].join("/") : "нет live");

  const per = await api(cookie, { windows: { liq: 1, cvd: 30, oi: 240 }, enabled: true });
  check("окна сохраняются по отдельности",
        per.config && per.config.windows.liq === 1 && per.config.windows.cvd === 30 &&
        per.config.windows.oi === 240, JSON.stringify(per.config && per.config.windows));
  const global = await api(cookie, { window_min: 15, enabled: true });
  check("старая настройка «одно окно на всё» раскладывается по метрикам",
        global.config && global.config.windows.liq === 15 &&
        global.config.windows.cvd === 15 && global.config.windows.oi === 15,
        JSON.stringify(global.config && global.config.windows));
  const restore = await api(cookie, {});           // назад к умолчаниям
  check("после сброса окна снова по умолчанию",
        restore.config && restore.config.windows.cvd === DEFAULTS.cvd,
        JSON.stringify(restore.config && restore.config.windows));

  // --- доска в кабинете -----------------------------------------------------
  const html = await (await fetch(URL_BASE + "/cabinet", { headers: { cookie } })).text();
  const apiCalls = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));

  const dom = new JSDOM(html, {
    url: URL_BASE + "/cabinet",
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try { win.localStorage.setItem("liqscope.svc.open", "alerts"); } catch (e) { /* ignore */ }
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
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
        apiCalls.push({ url: u, method: (opts && opts.method) || "GET",
                        body: (opts && opts.body) || "" });
        const headers = Object.assign({}, (opts && opts.headers) || {}, { cookie });
        const res = await fetch(u.startsWith("http") ? u : URL_BASE + u,
                                Object.assign({}, opts, { headers }));
        const body = await res.json().catch(() => ({}));
        return { ok: res.ok, status: res.status, json: async () => body };
      };
    },
  });

  await sleep(2500);
  const win = dom.window;
  const doc = win.document;
  const $ = (id) => doc.getElementById(id);
  const q = (sel) => doc.querySelector(sel);
  const qa = (sel) => Array.prototype.slice.call(doc.querySelectorAll(sel));
  const click = (el) => el.dispatchEvent(new win.MouseEvent("click",
    { bubbles: true, cancelable: true, view: win }));

  const board = $("alerts-board");
  check("доска алертов отрисована", !!board && board.innerHTML.length > 500,
        board ? board.innerHTML.length : "нет");
  check("доска запросила настройки у сервера",
        apiCalls.some((c) => c.url.indexOf("/api/account/alerts") === 0),
        apiCalls.map((c) => c.url).filter((u) => u.indexOf("alerts") === 0).join(" "));

  const groups = { liq: '[data-winbox="liq"] [data-win]', cvd: '[data-winbox="cvd"] [data-win]',
                   oi: '[data-winbox="oi"] [data-win]' };
  const counts = Object.keys(groups).map((k) => qa(groups[k]).length);
  check("у каждой метрики свои чипсы окон",
        counts.every((n) => n >= 5), counts.join("/"));
  const inputs = qa("[data-winin]").map((i) => i.getAttribute("data-winin")).sort();
  check("у каждой метрики своё поле «минуты»",
        JSON.stringify(inputs) === JSON.stringify(["cvd", "liq", "oi"]), inputs.join("/"));
  check("общего блока «окно агрегации» больше нет (окно живёт в карточке метрики)",
        !$("al-win-in") && !$("al-wins"));
  const marked = ["liq", "cvd", "oi"].map((m) =>
    (qa(groups[m]).filter((b) => b.classList.contains("on"))[0] || {}).textContent);
  check("подсвечены текущие окна метрик: 5м / 15м / 1ч",
        JSON.stringify(marked) === JSON.stringify(["5м", "15м", "1ч"]), marked.join("/"));
  const lit = ["liq", "cvd", "oi"].map((m) =>
    qa(groups[m]).filter((b) => b.classList.contains("on")).length);
  check("подсвечен ровно один чип в каждой метрике",
        lit.every((n) => n === 1), lit.join("/"));
  const labels = ["liq", "cvd", "oi"].map((m) =>
    (q('[data-winlabel="' + m + '"]') || {}).textContent);
  check("в карточках подписаны текущие окна метрик",
        JSON.stringify(labels) === JSON.stringify(["Окно · 5м", "Окно · 15м", "Окно · 1ч"]),
        labels.join(" | "));

  /* --- вид доски: каждая метрика — горизонтальная строка ------------------ */
  const css2 = (el, prop) => win.getComputedStyle(el)[prop];
  const feeds = qa("#alerts-board .al-feed");
  check("метрик ровно три строки", feeds.length === 3, feeds.length + " строк");
  const feedsBox = q("#alerts-board .al-feeds");
  check("строки метрик идут друг под другом (одна колонка у доски)",
        !!feedsBox && css2(feedsBox, "gridTemplateColumns") === "1fr",
        feedsBox ? css2(feedsBox, "gridTemplateColumns") : "нет сетки");
  const rowGrids = qa("#alerts-board .al-feed > .al-feed-grid");
  check("внутри строки — горизонтальная раскладка в несколько колонок",
        rowGrids.length === 3 && rowGrids.every((g) =>
          (css2(g, "gridTemplateColumns").match(/minmax/g) || []).length >= 3),
        rowGrids.length + " строк: " +
        (rowGrids[0] ? css2(rowGrids[0], "gridTemplateColumns") : "нет"));
  check("значение, настройки и лента стоят в одной строке, а не столбиком",
        rowGrids.length === 3 && rowGrids.every((g) =>
          !!g.querySelector(":scope > .al-col-now [data-metric]") &&
          !!g.querySelector(":scope > .al-col-set [data-winbox]") &&
          !!g.querySelector(":scope > .al-col-tape .al-tape")),
        rowGrids.length + " строк");
  const metas = qa("#alerts-board [data-feedmeta]");
  check("в заголовке строки — сводка метрики (значение, порог, окно, монета)",
        metas.length === 3 && metas.every((m) =>
          /^(LIQ|CVD|OI) /.test(m.textContent) && /порог /.test(m.textContent) &&
          /окно /.test(m.textContent) && /монета /.test(m.textContent)),
        metas.length + " сводок: " + (metas[0] ? metas[0].textContent : "нет"));

  /* --- ленты живые: сигналы отдельно, поток окна отдельно ---------------- */
  const tapes = qa("#alerts-board .al-tape");
  check("у каждой метрики своя лента", tapes.length === 3, tapes.length + " лент");
  const flowRows = qa("#alerts-board .al-tape .row.flow");
  check("в лентах видно поток окна, а не только сигналы",
        flowRows.length >= 3,
        flowRows.length + " строк потока: " +
        (flowRows[0] ? flowRows[0].textContent.replace(/\s+/g, " ") : "нет"));
  const flowMarks = qa("#alerts-board .al-tape .tape-sig");
  check("у ленты есть разделы «сигналы» и «поток окна»",
        flowMarks.some((m) => /поток окна/.test(m.textContent)),
        flowMarks.map((m) => m.textContent).slice(0, 3).join(" | "));
  const flowMeta = qa("#alerts-board [data-flowmeta]");
  check("в подписи ленты — сколько событий в окне",
        flowMeta.length === 3 && flowMeta.every((m) => /поток окна/.test(m.textContent)),
        flowMeta.map((m) => m.textContent).join(" | "));
  const sparks = qa("#alerts-board .al-spark");
  check("микрографик есть у каждой метрики (включая OI)",
        sparks.length === 3, sparks.length + " графиков");
  const oiPts = qa('#alerts-board [data-feed="oi"] .al-spark polyline');
  const cvdPts = qa('#alerts-board [data-feed="cvd"] .al-spark polyline');
  check("у OI и CVD микрографик нарисован ломаной, а не пустой сеткой",
        oiPts.length === 1 && cvdPts.length === 1,
        "oi: " + oiPts.length + ", cvd: " + cvdPts.length);
  check("в статусе доски перечислены окна всех трёх метрик",
        /окна: LIQ 5м · CVD 15м · OI 1ч/.test($("al-status") ? $("al-status").textContent : ""),
        $("al-status") && $("al-status").textContent);
  check("под заголовком объяснено, что после сигнала окно начинается заново",
        /окно начинается заново/.test(board.textContent),
        (board.textContent.match(/окно начинается заново/) || [""])[0]);

  // клик по чипсу меняет окно ТОЛЬКО своей метрики
  const cvdChip = qa(groups.cvd).filter((b) => b.textContent.trim() === "30м")[0];
  check("чип «30м» у CVD есть", !!cvdChip);
  if (cvdChip) {
    click(cvdChip);
    await sleep(900);
    const posts = apiCalls.filter((c) => c.method === "POST" &&
      c.url.indexOf("/api/account/alerts") === 0);
    const last = posts[posts.length - 1];
    let body = {};
    try { body = JSON.parse((last && last.body) || "{}"); } catch (e) { body = {}; }
    check("клик по чипсу сохранил окно CVD = 30",
          body.windows && body.windows.cvd === 30, JSON.stringify(body.windows || null));
    check("окна других метрик не тронуты",
          body.windows && body.windows.liq === 5 && body.windows.oi === 60,
          JSON.stringify(body.windows || null));
    check("чип сразу подсветился", cvdChip.classList.contains("on"), cvdChip.className);
    const cvdLabel = q('[data-winlabel="cvd"]') || {};
    check("подпись окна CVD обновилась сразу",
          /Окно CVD · 30м/.test(String(cvdLabel.textContent)), String(cvdLabel.textContent));
    check("в статусе видно, что окно сохранилось",
          /сохранено|сохраняю/.test($("al-status") ? $("al-status").textContent : ""),
          $("al-status") && $("al-status").textContent);
  }

  // поле «минуты» тоже про свою метрику
  const oiInp = qa("[data-winin]").filter((i) => i.getAttribute("data-winin") === "oi")[0];
  if (oiInp) {
    oiInp.value = "600";
    oiInp.dispatchEvent(new win.Event("change", { bubbles: true }));
    await sleep(900);
    const posts = apiCalls.filter((c) => c.method === "POST" &&
      c.url.indexOf("/api/account/alerts") === 0);
    const last = posts[posts.length - 1];
    let body = {};
    try { body = JSON.parse((last && last.body) || "{}"); } catch (e) { body = {}; }
    check("поле минут сохранило окно OI = 600 и не тронуло остальные",
          body.windows && body.windows.oi === 600 && body.windows.liq === 5,
          JSON.stringify(body.windows || null));
  }

  // Монета — своя у каждой метрики: ликвидации могут смотреть BTC, а CVD — ETH,
  // и сигналы идут независимо. Общий чип «монета на всё» из доски убран.
  const coinBoxes = qa("[data-coinbox]");
  check("монета настраивается у каждой метрики (LIQ/CVD/OI)",
        coinBoxes.length === 3 &&
        ["liq", "cvd", "oi"].every((m) => !!q('[data-coinbox="' + m + '"]')),
        coinBoxes.length + " блоков");
  check("общего блока «монета на все метрики» больше нет",
        !q("[data-coin]") && !$("al-coin-in"), "старый блок остался");
  const liqCoins = qa('[data-coinbox="liq"] [data-coinval]');
  const ethChip = liqCoins.filter((b) => b.textContent.trim() === "ETH")[0];
  check("в монетах ликвидаций есть ETH", !!ethChip, liqCoins.map((b) => b.textContent.trim()).join(","));
  if (ethChip) {
    click(ethChip);
    await sleep(900);
    const posts = apiCalls.filter((c) => c.method === "POST" &&
      c.url.indexOf("/api/account/alerts") === 0);
    const last = posts[posts.length - 1];
    let body = {};
    try { body = JSON.parse((last && last.body) || "{}"); } catch (e) { body = {}; }
    check("монета сохранилась только ликвидациям",
          body.coins && body.coins.liq === "ETH_USDT" &&
          body.coins.cvd !== "ETH_USDT" && body.coins.oi !== "ETH_USDT",
          JSON.stringify(body.coins || null));
    check("чип ETH подсветился сразу", ethChip.classList.contains("on"), ethChip.className);
    check("в статусе видно монеты метрик",
          /(монеты: LIQ ETH|сохранено · монеты: ETH)/.test(
            $("al-status") ? $("al-status").textContent : ""),
          $("al-status") && $("al-status").textContent);
    const liqCoinLabel = q('[data-coinlabel="liq"]') || {};
    check("в карточке ликвидаций подписана монета ETH",
          String(liqCoinLabel.textContent).trim() === "ETH", String(liqCoinLabel.textContent));
  }
  // своя монета для CVD — своё поле ввода
  const cvdCoinIn = q('[data-coinin="cvd"]');
  check("у CVD есть своё поле ввода монеты", !!cvdCoinIn);
  if (cvdCoinIn) {
    cvdCoinIn.value = "sol";
    cvdCoinIn.dispatchEvent(new win.Event("change", { bubbles: true }));
    await sleep(900);
    const posts = apiCalls.filter((c) => c.method === "POST" &&
      c.url.indexOf("/api/account/alerts") === 0);
    let body = {};
    try { body = JSON.parse((posts[posts.length - 1] && posts[posts.length - 1].body) || "{}"); }
    catch (e) { body = {}; }
    check("тикер без пары понят: CVD смотрит SOL_USDT",
          body.coins && body.coins.cvd === "SOL_USDT" && body.coins.liq === "ETH_USDT",
          JSON.stringify(body.coins || null));
  }

  check("ошибок страницы нет", errors.length === 0, errors.slice(0, 2).join(" | ") || "ok");

  // вернуть демо-аккаунту умолчания, чтобы повторный прогон был чистым
  await api(cookie, { windows: DEFAULTS, window_min: DEFAULTS.liq, enabled: true,
                      coins: { liq: "ALL", cvd: "ALL", oi: "ALL" } });

  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  dom.window.close();
  process.exit(fail ? 1 : 0);
}

main().catch((e) => {
  console.log("  FAIL исключение: " + (e && e.stack ? e.stack.split("\n")[0] : e));
  console.log("итог: " + ok + " ок, " + (fail + 1) + " ошибок");
  process.exit(1);
});
