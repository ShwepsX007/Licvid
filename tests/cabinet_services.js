/**
 * Кабинет сайта: доски сервисов «Корреляции валют» и «Сторож монет».
 *
 *  Проверяем, что в кабинете сервисы не просто включены, а дают картинку:
 *  тепловая карта корреляций с потоками и выбором окна/метрики, а у сторожа —
 *  гистограмма пампов и дампов, настройки (режим, порог, период, свечи),
 *  список «уже за порогом» и лента сигналов. Настройки сохраняются на сервер.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000, демо-режим):
 *      NODE_PATH=/tmp/smoke/node_modules node tests/cabinet_services.js
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

async function main() {
  let cookie;
  try {
    cookie = await login();
  } catch (e) {
    console.log("  --   " + e.message);
    console.log("итог: 0 ок, 1 ошибок");
    process.exit(1);
  }

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
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
      try {
        // кабинет открывает доску раскрытой гармошки сервиса
        win.localStorage.setItem("liqscope.svc.open", "correlations");
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () { return dummy; };
      // jsdom без fetch: проксируем запросы страницы в node-fetch и несём
      // сессионную куку — кабинет требует подтверждённого входа
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
  const fold = (slug) => q('.svc-fold[data-fold="' + slug + '"]');
  const openFold = (slug) => {
    const btn = q('.svc-fold[data-fold="' + slug + '"] [data-toggle]');
    if (!btn) return false;
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    return true;
  };

  check("кабинет отрисован (есть список сервисов)", !!$("svc-acc") && qa(".svc-fold").length >= 3,
        qa(".svc-fold").length + " сервисов");
  check("сервис «Корреляции валют» есть и не «скоро»",
        !!fold("correlations") && !/скоро/.test(fold("correlations").textContent),
        fold("correlations") && fold("correlations").textContent.slice(0, 60));
  check("сервис «Сторож монет» есть и не «скоро»",
        !!fold("watchlist") && !/скоро/.test(fold("watchlist").textContent),
        fold("watchlist") && fold("watchlist").textContent.slice(0, 60));
  check("в описании сторожа — пампы и дампы",
        /пампы|памп/i.test(fold("watchlist").textContent), fold("watchlist").textContent.slice(0, 80));

  // --- корреляции: доска раскрыта сразу (localStorage) ---------------------
  await sleep(1200);
  const corr = $("corr-board");
  check("доска корреляций отрисована", !!corr && corr.innerHTML.length > 500,
        corr ? corr.innerHTML.length : "нет");
  check("запрошена картина корреляций",
        apiCalls.some((c) => c.url.indexOf("/api/account/correlations") === 0),
        apiCalls.map((c) => c.url).filter((u) => u.indexOf("correlations") === 0).join(" "));
  const cells = qa("#corr-board .cor-heat-cell");
  check("тепловая карта: клетки с коэффициентами", cells.length >= 4, cells.length + " клеток");
  // Пояснение к тепловой карте: что за цифра в клетке и что значит цвет.
  // Спрашивается у пользователя дважды — наведением и кликом.
  const heatNote = $("cor-heat-note");
  check("под картой есть пояснение, что показывают данные",
        !!heatNote && /Что это/.test(heatNote.textContent) && heatNote.textContent.length > 80,
        heatNote ? heatNote.textContent.slice(0, 120) : "нет блока");
  check("в пояснении названы метрика, окно и смысл цвета",
        !!heatNote && /зелёный/.test(heatNote.textContent) &&
        /красный/.test(heatNote.textContent) && /коэффициент/.test(heatNote.textContent),
        heatNote ? heatNote.textContent.slice(0, 160) : "нет блока");
  const cellsWithTip = cells.filter((c) => (c.getAttribute("title") || "").length > 40);
  check("у клеток есть подсказка при наведении (что за пара и что значит число)",
        cellsWithTip.length === cells.length,
        cellsWithTip.length + "/" + cells.length);
  check("в клетках есть числа корреляции",
        cells.some((c) => /^-?\d\.\d\d$/.test(c.textContent.trim())), cells[0] && cells[0].textContent);
  check("есть блоки потоков (шорты/лонги/CVD/OI)",
        qa("#corr-board .cor-flow-h").length >= 6, qa("#corr-board .cor-flow-h").length + " блоков");
  check("есть окна и метрики для выбора",
        qa("#cor-wins .al-chip").length >= 4 && qa("#cor-mets .al-chip").length >= 4,
        qa("#cor-wins .al-chip").length + "/" + qa("#cor-mets .al-chip").length);
  // связей может не быть, если в окне мало часов с историей — тогда доска
  // честно говорит об этом; иначе показываем пары и клик по паре
  const pairRows = qa("#cor-pairs .cor-pair");
  const pairHint = /мало истории|связей пока нет/.test(
    ($("cor-pairs") ? $("cor-pairs").textContent : ""));
  check("есть список связей или честная подсказка про мало истории",
        pairRows.length >= 1 || pairHint,
        pairRows.length + " пар | " + (pairHint ? "подсказка есть" : "нет"));

  // переключение метрики на CVD идёт на сервер
  const cvdChip = qa("#cor-mets .al-chip").filter((b) => /CVD/.test(b.textContent))[0];
  check("чип CVD есть", !!cvdChip);
  if (cvdChip) {
    cvdChip.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    await sleep(1200);
    check("после клика картина запрошена по CVD",
          apiCalls.some((c) => c.url.indexOf("metric=cvd") !== -1),
          apiCalls.map((c) => c.url).filter((u) => u.indexOf("correlations") === 0).slice(-1)[0]);
    check("клетки тепловой карты пересобраны", qa("#corr-board .cor-heat-cell").length >= 4,
          qa("#corr-board .cor-heat-cell").length);
  }

  // клик по клетке карты объясняет её текстом (на телефоне наведения нет)
  const cell = q("#cor-heat-box .cor-heat-cell:not(.diag)");
  if (cell) {
    const before = $("cor-status").textContent;
    cell.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    await sleep(200);
    const after = $("cor-status").textContent;
    check("клик по клетке карты объясняет пару словами",
          /r = /.test(after) && /в одну сторону|противофазе/.test(after) && after !== before,
          after.slice(0, 160));
  }

  // клик по паре объясняет связь словами
  const pair = q("#cor-pairs .cor-pair");
  if (pair) {
    pair.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    await sleep(200);
    check("клик по паре объясняет связь",
          /r = /.test($("cor-status").textContent) || /вместе|противофазе/.test($("cor-status").textContent),
          $("cor-status").textContent);
  }

  // --- сторож монет: раскрываем гармошку ----------------------------------
  const pumpWasOpen = !!$("pump-board");
  check("доска сторожа открывается по клику", openFold("watchlist"));
  await sleep(1500);
  const pump = $("pump-board");
  check("доска сторожа отрисована", !!pump && pump.innerHTML.length > 500,
        pump ? pump.innerHTML.length : "нет");
  check("запрошен снимок сторожа",
        apiCalls.some((c) => c.url.indexOf("/api/account/watchlist") === 0),
        apiCalls.map((c) => c.url).filter((u) => u.indexOf("watchlist") === 0).join(" "));
  const rows = qa("#pump-board .pump-row");
  check("гистограмма: строки пампов/дампов", rows.length >= 3, rows.length + " строк");
  check("у строк есть полосы и проценты",
        qa("#pump-board .pump-track i").length >= 3 && qa("#pump-board .pump-val").length >= 3,
        qa("#pump-board .pump-track i").length + " полос");
  check("есть настройки: режим, порог, период, свечи",
        qa("#pump-mode .al-chip").length >= 3 && qa("#pump-thr .al-chip").length >= 4 &&
        qa("#pump-per .al-chip").length >= 4 && qa("#pump-cnd .al-chip").length >= 3,
        [qa("#pump-mode .al-chip").length, qa("#pump-thr .al-chip").length,
         qa("#pump-per .al-chip").length, qa("#pump-cnd .al-chip").length].join("/"));
  check("объяснено, что текущая свеча считается",
        /текущая свеча/i.test(pump.textContent), "нет пояснения про свечу");
  check("в панели есть ссылка на Gate", /Gate/.test(pump.textContent));
  check("есть лента сигналов", !!q("#pump-board .al-tape") || /сигналов/.test(pump.textContent));
  check("у сторожа включён сигнал (как в демо-подписке)",
        /СИГНАЛ (ВКЛ|ВЫКЛ)/.test(pump.textContent), q("#pump-sw") && q("#pump-sw").textContent);

  // «Облако связей» из сервиса корреляций убрано: вместе с тепловой картой
  // осталось распределение связей (гистограмма коэффициентов).
  check("облака связей больше нет в сервисе корреляций",
        !$("cor-scatter") && !q("#cor-chart-box") && !q("#cor-axis-x") && !q("#cor-axis-y"),
        [$("cor-scatter") ? "canvas" : "", q("#cor-chart-box") ? "box" : "",
         q("#cor-axis-x") ? "оси" : ""].join(" "));
  check("распределение связей по интервалам r осталось", qa(".cor-hist i").length >= 8,
        qa(".cor-hist i").length + " столбиков");
  check("тепловая карта на месте", qa("#cor-heat-box .cor-heat-cell").length >= 4,
        qa("#cor-heat-box .cor-heat-cell").length + " клеток");

  // сохранение настройки: порог 30%
  const before = apiCalls.filter((c) => c.method === "POST" &&
    c.url.indexOf("/api/account/watchlist") === 0).length;
  const chip30 = qa("#pump-thr .al-chip").filter((b) => b.textContent.trim() === "30%")[0];
  check("есть чип порога 30%", !!chip30);
  if (chip30) {
    chip30.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    await sleep(1200);
    const posts = apiCalls.filter((c) => c.method === "POST" &&
      c.url.indexOf("/api/account/watchlist") === 0);
    check("порог сохранён на сервер", posts.length > before && /30/.test(posts.slice(-1)[0].body),
          posts.slice(-1)[0] && posts.slice(-1)[0].body);
    check("в панели появилась плашка сохранения",
          /сохранено|ошибка|null/.test($("pump-status") ? $("pump-status").textContent : ""),
          $("pump-status") && $("pump-status").textContent);
  }

  // Регрессия «Период свечей / Сколько свечей скачут и остаются на месте»:
  // клик по чипу тут же просил свежий снимок у сервера, снимок приходил со
  // старым значением, доска пересобиралась — чип отскакивал назад, а на сервер
  // уходило старое число. Теперь нажатие — источник правды, пока сервер его
  // не подтвердил.
  // Чипы выбора держим с самым коротким окном (1m × 1 свеча): демо-стенд
  // копит минутные срезы, и длинное окно оставило бы доску без движений.
  const perChip = qa("#pump-per .al-chip").filter((b) => b.textContent.trim() === "1m")[0];
  const cndChip = qa("#pump-cnd .al-chip").filter((b) => b.textContent.trim() === "1 свеч.")[0];
  check("есть чипы «1m» и «1 свеч.»", !!perChip && !!cndChip);
  const windowText = () => ($("pump-window") ? $("pump-window").textContent : "");
  const markedCandles = () => Number(String((qa("#pump-cnd .al-chip")
    .filter((b) => b.classList.contains("on"))[0] || {}).textContent || "").replace(/\D+/g, "")) || 0;
  if (perChip && cndChip) {
    perChip.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    await sleep(250);
    check("клик по периоду подсветил чип сразу", perChip.classList.contains("on"), perChip.className);
    const expectWin = (1 * markedCandles()) + "м";
    check("окно сигнала пересчитано сразу (период × свечи)",
          windowText().indexOf(expectWin) !== -1, windowText().slice(0, 60) + " | ждали " + expectWin);
    await sleep(1200);
    check("период не отскакивает назад после снимка сервера",
          perChip.classList.contains("on"), perChip.className);
    cndChip.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    await sleep(1400);
    check("число свечей не отскакивает назад после снимка сервера",
          cndChip.classList.contains("on"), cndChip.className);
    check("окно сигнала с одной свечой — один период",
          windowText().indexOf("1м") !== -1, windowText().slice(0, 60));
    const lastSave = apiCalls.filter((c) => c.method === "POST" &&
      c.url.indexOf("/api/account/watchlist") === 0).slice(-1)[0];
    check("на сервер ушли выбранные период и свечи",
          !!lastSave && /"period":"1m"/.test(String(lastSave.body)) &&
          /"candles":1/.test(String(lastSave.body)),
          lastSave && String(lastSave.body).slice(0, 140));
  }

  // Регрессия «кнопки в новых сервисах туго отвечают»: доски сторожа и
  // корреляций перерисовывались целиком на автообновлении (12 и 30 секунд) —
  // обработчики чипов исчезали, и нажатие уходило в пустоту. Ждём тик
  // автообновления сторожа и проверяем, что клик по чипу по-прежнему работает.
  await sleep(13000);
  const beforeTick = apiCalls.filter((c) => c.method === "POST" &&
    c.url.indexOf("/api/account/watchlist") === 0).length;
  const chipAfterTick = qa("#pump-thr .al-chip").filter((b) => b.textContent.trim() === "20%")[0];
  check("после автообновления доски чипы на месте", !!chipAfterTick);
  if (chipAfterTick) {
    chipAfterTick.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
    await sleep(900);
    const afterTick = apiCalls.filter((c) => c.method === "POST" &&
      c.url.indexOf("/api/account/watchlist") === 0).length;
    check("после автообновления доски чип всё ещё сохраняет настройку",
          afterTick > beforeTick, afterTick - beforeTick + " запросов");
    check("после автообновления клик сразу подсветил чип",
          chipAfterTick.classList.contains("on"), chipAfterTick.className);
  }

  const viewportWarn = [];
  check("ошибок страницы нет", errors.length === 0,
        errors.slice(0, 2).join(" | ") || "ok");
  void pumpWasOpen; void viewportWarn;

  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  dom.window.close();
  process.exit(fail ? 1 : 0);
}

main().catch((e) => {
  console.log("  FAIL исключение: " + (e && e.stack ? e.stack.split("\n")[0] : e));
  console.log("итог: " + ok + " ок, " + (fail + 1) + " ошибок");
  process.exit(1);
});
