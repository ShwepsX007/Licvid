require("./_dom_env");
/**
 * Время на графике — местное, как в ленте.
 *
 * Библиотека графика по умолчанию подписывает ось временем Гринвича: низ
 * графика жил в UTC, а лента показывала пояс пользователя — отсюда
 * «рассинхронизация». Тест проверяет, что в график ушли наши форматеры и что
 * подписи считаются в местном поясе (и на языке интерфейса).
 *
 * Запуск (сервер уже поднят):
 *     npm install --no-save jsdom ws
 *     TZ=Europe/Moscow node tests/chart_time.js [http://127.0.0.1:8000]
 *
 * Пояс задаётся здесь же (Europe/Moscow = UTC+3), чтобы тест не зависел от
 * машины: если пояс нулевой, проверка «не UTC» ничего бы не значила.
 */
process.env.TZ = process.env.LIQSCOPE_TEST_TZ || "Europe/Moscow";

const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

/** Canvas в jsdom не реализован — подсовываем заглушку 2d-контекста. */
function stubCanvas(win) {
  const noop = () => {};
  const ctx = new Proxy({}, {
    get(_t, prop) {
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") {
        return () => ({ data: new Uint8ClampedArray(4) });
      }
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
}

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 300)));
  vc.on("error", (...a) =>
    errors.push("console.error: " + a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 300)));

  const dom = await JSDOM.fromURL(ru(URL_BASE + "/terminal"), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      stubCanvas(win);
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true });
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({
        matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {},
        dispatchEvent() { return false; },
      });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      win.WebSocket = require("ws");
      win.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
    },
  });

  const win = dom.window;
  await new Promise((r) => setTimeout(r, 5000));

  const T = win.LiQScopeChartTime;
  check("тестовый API времени доступен", !!T && typeof T.tick === "function" &&
    typeof T.crosshair === "function");

  const tag = win.LiqScopeI18n.localeTag();
  // 18.09.2026 14:30 UTC — фиксированная точка, чтобы тест не зависел от даты
  const ts = Math.floor(Date.UTC(2026, 8, 18, 14, 30) / 1000);
  const local = new Date(ts * 1000);
  const wantHM = local.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit" });
  const utcHM = local.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  check("тест запущен не в UTC (иначе проверка ниже бессмысленна)",
    wantHM !== utcHM, `местное ${wantHM} / UTC ${utcHM}`);

  const label = T.tick(ts, 3);              // TickMarkType.Time
  check("деление оси — местное время, а не UTC", label === wantHM,
    `${label} (ждали ${wantHM}, UTC был бы ${utcHM})`);
  check("метка не совпадает с UTC-подписью", label !== utcHM);

  const dayLabel = T.tick(ts, 2);            // DayOfMonth
  check("на дневных делениях — число и месяц",
    dayLabel.indexOf(String(local.getDate())) !== -1, dayLabel);
  check("на годовых делениях — год",
    T.tick(ts, 0) === String(local.getFullYear()), T.tick(ts, 0));

  const cross = T.crosshair(ts);
  check("подпись перекрестия — дата и местное время",
    cross.indexOf(wantHM) !== -1 && cross.indexOf(String(local.getFullYear())) !== -1, cross);

  // Главное: эти же форматеры реально ушли в график
  const opts = T.options();
  check("график знает форматер делений оси",
    !!(opts && opts.timeScale && typeof opts.timeScale.tickMarkFormatter === "function"));
  check("график знает форматер перекрестия",
    !!(opts && opts.localization && typeof opts.localization.timeFormatter === "function"));
  check("ось рисуется нашим форматером (местное время)",
    opts && opts.timeScale.tickMarkFormatter(ts, 3) === wantHM,
    opts && opts.timeScale.tickMarkFormatter(ts, 3));
  check("локаль графика — язык интерфейса",
    !!(opts && opts.localization && opts.localization.locale),
    opts && opts.localization && opts.localization.locale);

  // Лента и ось должны показывать один и тот же час
  const feedHour = win.LiqScopeI18n.time(ts);
  check("подпись оси и время в ленте совпадают",
    feedHour.indexOf(wantHM) !== -1, `${feedHour} / ${label}`);

  // Смена языка перерисовывает ось (числа — в формате языка), но пояс не меняет
  win.LiqScopeI18n.set("en");
  await new Promise((r) => setTimeout(r, 200));
  const enTag = win.LiqScopeI18n.localeTag();
  const enWant = local.toLocaleTimeString(enTag, { hour: "2-digit", minute: "2-digit" });
  const enUtc = local.toLocaleTimeString(enTag, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  const enLabel = T.tick(ts, 3);
  check("после смены языка ось остаётся местной",
    enLabel === enWant && enLabel !== enUtc, `${enLabel} (местное ${enWant}, UTC ${enUtc})`);
  win.LiqScopeI18n.set("ru");
  await new Promise((r) => setTimeout(r, 200));

  check("ошибок страницы нет", errors.length === 0, errors.slice(0, 3).join(" // "));
  win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
