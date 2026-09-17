/**
 * Раскладка блоков графика в настоящем Chrome: главный график и окна
 * LIQ/CVD/OI регулируются по высоте разделителями, как лента и «Лидеры».
 *
 * В jsdom раскладки нет (все высоты нулевые), поэтому здесь мы гоняем
 * настоящий браузер и меряем живую геометрию:
 *   • перетаскивание разделителя над окном меняет его высоту ровно на drag;
 *   • разделитель между окнами меняет высоты соседей, не трогая график;
 *   • окно и главный график сужаются в ноль (и график пересчитывает канвы);
 *   • после F5 высоты восстанавливаются из localStorage;
 *   • двойной клик по разделителю возвращает исходные 64px.
 *
 * Запуск (сервер должен быть уже запущен на 127.0.0.1:8000):
 *     npm install --no-save puppeteer
 *     npx puppeteer browsers install chrome   # если Chrome ещё не скачан
 *     node tests/stack_layout.js [http://127.0.0.1:8000]
 *
 * Без установленного Chrome тест мягко пропускается — в CI-контейнерах
 * без браузера раскладку проверяют части 1 и 2b tests/ind_panes.js.
 */

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

async function main() {
  let puppeteer;
  try {
    puppeteer = require("puppeteer");
  } catch (e) {
    console.log("  --   puppeteer не установлен (npm install --no-save puppeteer) — пропуск");
    return;
  }

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,900"],
    });
  } catch (e) {
    console.log("  --   Chrome не найден (" + String(e.message || e).split("\n")[0].slice(0, 120) +
                ") — пропуск, нужен `npx puppeteer browsers install chrome`");
    return;
  }
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e.message || e).slice(0, 200)));
  page.on("console", (m) => { if (m.type() === "error") pageErrors.push("console: " + m.text().slice(0, 200)); });

  // окна индикаторов видны только зарегистрированным — включаем dev-обход
  await page.evaluateOnNewDocument(() => {
    try {
      localStorage.setItem("liqscope.devLayers", "1");
      ["liq", "cvd", "oi"].forEach((k) => localStorage.setItem("liqscope.pane" + k[0].toUpperCase() + k.slice(1), "1"));
      localStorage.removeItem("liqscope.layout");
    } catch (e) { /* ignore */ }
  });

  const metrics = () => page.evaluate(() => {
    const h = (sel) => {
      const el = document.querySelector(sel);
      return el ? Math.round(el.getBoundingClientRect().height) : -1;
    };
    const v = (n) => document.documentElement.style.getPropertyValue(n).trim();
    const cv = document.querySelector("#tv-chart-container canvas");
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem("liqscope.layout")) || {}; } catch (e) { /* ignore */ }
    return {
      chart: h(".chart-wrapper"), stack: h("#chart-stack"),
      liq: h("#ind-pane-liq .ind-canvas-wrap"),
      cvd: h("#ind-pane-cvd .ind-canvas-wrap"),
      oi: h("#ind-pane-oi .ind-canvas-wrap"),
      canvasPx: cv ? Math.round(cv.getBoundingClientRect().height) : 0,
      sized: !!document.querySelector("#chart-stack").classList.contains("sized"),
      liqVar: v("--ind-h-liq"), cvdVar: v("--ind-h-cvd"), oiVar: v("--ind-h-oi"),
      saved,
    };
  });

  /** Перетаскивание разделителя: dy > 0 — вниз, dy < 0 — вверх. */
  async function dragSplit(sel, dy) {
    const box = await page.$eval(sel, (el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    });
    await page.mouse.move(box.x, box.y);
    await page.mouse.down();
    await page.mouse.move(box.x, box.y + dy, { steps: 10 });
    await page.mouse.up();
    await new Promise((r) => setTimeout(r, 150));
    return box;
  }

  try {
    console.log("\nраскладка блоков графика в Chrome: " + URL_BASE);
    await page.goto(URL_BASE + "/terminal", { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForFunction(() => {
      const p = document.querySelector("#ind-pane-oi");
      return !!p && !p.classList.contains("hidden") && !!document.querySelector("#split-liq-y");
    }, { timeout: 20000 });
    await new Promise((r) => setTimeout(r, 1200));   // график/канвы успевают отрисоваться

    const m0 = await metrics();
    check("главный график отрисован", m0.chart > 200, JSON.stringify(m0));
    check("окна по умолчанию 64px", m0.liq === 64 && m0.cvd === 64 && m0.oi === 64,
          [m0.liq, m0.cvd, m0.oi].join("/"));
    check("канва графика совпадает с блоком", Math.abs(m0.canvasPx - m0.chart) <= 2,
          m0.canvasPx + " против " + m0.chart);

    // 1) разделитель над LIQ: окно растёт ровно на величину перетаскивания,
    //    высоту отдаёт главный график
    await dragSplit("#split-liq-y", -60);
    const m1 = await metrics();
    check("окно LIQ выросло на 60px", m1.liq === 124, m1.liq);
    check("главный график отдал эти 60px", Math.abs((m0.chart - m1.chart) - 60) <= 3,
          m0.chart + " → " + m1.chart);
    check("канва графика пересчитана", Math.abs(m1.canvasPx - m1.chart) <= 2,
          m1.canvasPx + " против " + m1.chart);
    check("режим растяжки включён", m1.sized === true);
    check("высота сохранена в localStorage", m1.saved.indLiq === 124, JSON.stringify(m1.saved));

    // 2) разделитель между окнами: соседи обмениваются высотой, график не тронут
    await dragSplit("#split-cvd-y", -40);
    const m2 = await metrics();
    check("окно CVD выросло на 40px", m2.cvd === 104, m2.cvd);
    check("высоту отдало соседнее окно LIQ (124 → 84)", m2.liq === 84, m2.liq);
    check("главный график при этом не изменился", m2.chart === m1.chart,
          m1.chart + " → " + m2.chart);

    // 3) окно сужается в ноль рывком вниз: 84px отдаются главному графику
    await dragSplit("#split-liq-y", 4000);
    const m3 = await metrics();
    check("окно LIQ сузилось полностью", m3.liq === 0, m3.liq);
    check("нулевая высота сохранена", m3.saved.indLiq === 0, JSON.stringify(m3.saved.indLiq));
    check("освободившиеся 84px забрал главный график",
          Math.abs((m3.chart - m2.chart) - 84) <= 3, m2.chart + " → " + m3.chart);

    // 4) главный график сужается в ноль: разделитель над LIQ тянем вверх
    await dragSplit("#split-liq-y", -4000);
    const m4 = await metrics();
    check("главный график сузился в ноль", m4.chart <= 2, m4.chart);
    check("окно LIQ заняло всю высоту стека", m4.liq > 300, m4.liq);
    check("стек не переполнен: график + окна = стек",
          Math.abs((m4.chart + m4.liq + m4.cvd + m4.oi + 3 * 8 + 3 * 26) - m4.stack) <= 4,
          [m4.chart, m4.liq, m4.cvd, m4.oi, m4.stack].join(" / "));
    check("канва графика не разъехалась при нулевой высоте", m4.canvasPx <= 3, m4.canvasPx);

    // 5) двойной клик по разделителю — исходные высоты
    const box = await page.$eval("#split-cvd-y", (el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    });
    await page.mouse.click(box.x, box.y, { count: 2 });
    await new Promise((r) => setTimeout(r, 200));
    const m5 = await metrics();
    check("двойной клик вернул высоты окон",
          m5.liq === 64 && m5.cvd === 64 && m5.oi === 64, [m5.liq, m5.cvd, m5.oi].join("/"));
    check("график снова получил свою высоту", m5.chart > 250, m5.chart);
    check("сброс сохранён без режима растяжки",
          m5.saved.indCvd === 64 && !m5.saved.sized, JSON.stringify(m5.saved));

    // 6) раскладка переживает F5
    await dragSplit("#split-oi-y", -50);
    const m6 = await metrics();
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => {
      const p = document.querySelector("#ind-pane-oi");
      return !!p && !p.classList.contains("hidden");
    }, { timeout: 20000 });
    await new Promise((r) => setTimeout(r, 1200));
    const m7 = await metrics();
    check("после F5 высота окна восстановлена", m7.oi === m6.oi && m7.oi === 114,
          m6.oi + " → " + m7.oi);
    check("после F5 главный график на месте", m7.chart > 200, m7.chart);

    check("ошибок страницы нет", pageErrors.length === 0, pageErrors.join(" | "));
  } catch (e) {
    fail++;
    console.log("  FAIL исключение: " + String(e.message || e).slice(0, 300));
  } finally {
    await browser.close();
  }
}

(async () => {
  await main();
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
