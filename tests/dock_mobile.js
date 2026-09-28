require("./_dom_env");
/**
 * Мобильный док графиков: схлопывание, подписи кнопок и дубли текста.
 *
 * Тест без браузера и без сервера — читает сами файлы, потому что ломался
 * именно CSS/разметка, а не рантайм:
 *   1. На телефоне (≤900px) каждый слот дока держит высоту, сетка x2–x4
 *      идёт одной колонкой, iframe занимает слот целиком, а страница
 *      скроллится вниз ко второму графику. Десктоп (>900px) не тронут.
 *   2. Кнопки режимов («Вкладки»/«Сетка») подписываются по языку страницы
 *      через textContent, панель дока помечена translate="no" — машинный
 *      перевод браузера не дублирует и не корежит подписи.
 *   3. app.js не рисует график в свёрнутый контейнер (h < 100 → 400).
 *
 * Запуск: node tests/dock_mobile.js
 */

const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..");

let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

/** Все блоки @media (max-width: 900px) из таблицы CSS, склеенные в одну. */
function mobileBlocks(css) {
  const out = [];
  const re = /@media[^{]*max-width:\s*900px[^{]*\{/g;
  let m;
  while ((m = re.exec(css))) {
    let depth = 1, j = m.index + m[0].length;
    for (; j < css.length && depth > 0; j++) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}") depth--;
    }
    out.push(css.slice(m.index + m[0].length, j - 1));
  }
  return out.join("\n");
}

/** CSS без мобильных блоков — то, что видит десктоп. */
function desktopCss(css) {
  let out = "", last = 0;
  const re = /@media[^{]*max-width:\s*900px[^{]*\{/g;
  let m;
  while ((m = re.exec(css))) {
    let depth = 1, j = m.index + m[0].length;
    for (; j < css.length && depth > 0; j++) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}") depth--;
    }
    out += css.slice(last, m.index);
    last = j;
  }
  return out + css.slice(last);
}

/** Правило по селектору: первое вхождение { ... } после него. */
function rule(css, sel) {
  const re = new RegExp(sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{([^}]*)\\}");
  const m = css.match(re);
  return m ? m[1] : "";
}

/** Первое число min-height в правиле, либо null. */
function minHeightOf(ruleText) {
  const m = ruleText.match(/min-height:\s*(?:max\([^,]+,\s*)?(\d+)px/);
  return m ? Number(m[1]) : null;
}

/** Тело функции/стрелки от маркера до парной закрывающей скобки. */
function bodyOf(src, marker) {
  const i = src.indexOf(marker);
  if (i < 0) return "";
  let depth = 0, j = src.indexOf("{", i);
  const start = j;
  for (; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) break; }
  }
  return src.slice(start, j + 1);
}

/* ===================== 1. CSS: мобильная вёрстка ===================== */

const css = read("static/workspace.css");
const mob = mobileBlocks(css);
const desk = desktopCss(css);

check("CSS: мобильный блок @media (max-width: 900px) есть", mob.length > 0);

// сетка x2–x4 на телефоне — одна колонка, графики идут друг под другом
check("CSS: на телефоне сетка x2–x4 сворачивается в одну колонку",
  /\.chart-dock\.cols-2,\s*\.chart-dock\.cols-3,\s*\.chart-dock\.cols-4\s*\{\s*grid-template-columns:\s*1fr\s*;/.test(mob),
  (mob.match(/\.chart-dock\.cols-2[^{]*\{[^}]*\}/) || [""])[0]);
check("CSS: десктопная сетка x2 не тронута",
  /\.chart-dock\.cols-2\s*\{\s*grid-template-columns:\s*1fr 1fr/.test(desk));

// высота слотов: контейнеры не сжимаются в ноль
const slotRule = mob.match(/\.chart-dock[^{]*\.chart-slot:not\([^)]*\)[^{]*\{([^}]*)\}/);
check("CSS: на телефоне слоту задана высота", !!slotRule, slotRule && slotRule[1]);
const slotMin = slotRule ? minHeightOf(slotRule[1]) : null;
check("CSS: слот держит минимум 400px", slotMin !== null && slotMin >= 400, String(slotMin));

// сплит (×2/×3/×4 с тянущимися краями) — колонкой, а не сжатием
const splitRow = rule(mob, ".chart-dock.is-split .chart-split-row");
const splitCell = rule(mob, ".chart-dock.is-split .chart-split-cell");
check("CSS: сплит на телефоне идёт колонкой", /flex-direction:\s*column/.test(splitRow), splitRow);
const cellMin = minHeightOf(splitCell);
check("CSS: ячейка сплита держит высоту (≥400px)", cellMin !== null && cellMin >= 400, splitCell);
check("CSS: ячейка сплита на всю ширину", /width:\s*100%/.test(splitCell), splitCell);

// iframe занимает слот целиком и не имеет права схлопнуться
const frameRule = rule(css, ".chart-slot-frame");
check("CSS: iframe слота занимает его целиком",
  /position:\s*absolute/.test(frameRule) && /height:\s*100%/.test(frameRule) && /width:\s*100%/.test(frameRule),
  frameRule);
const wrapMin = minHeightOf(rule(mob, ".chart-slot-frame-wrap"));
check("CSS: рамка iframe держит высоту на телефоне", wrapMin !== null && wrapMin >= 300, String(wrapMin));
const tabOnMin = minHeightOf(rule(mob, ".chart-dock-wrap .chart-slot.is-tab-on"));
check("CSS: видимый слот во «Вкладках» держит высоту", tabOnMin !== null && tabOnMin >= 400, String(tabOnMin));

// страница скроллится: у секции/дока нет фиксированной высоты
check("CSS: на телефоне секция графика не держит 100% высоты",
  /\.chart-section\.has-chart-dock\s*\{\s*height:\s*auto/.test(mob),
  (mob.match(/\.chart-section\.has-chart-dock\s*\{[^}]*\}/) || [""])[0]);
check("CSS: на телефоне обёртка и сетка дока не держат 100% высоты",
  /\.chart-dock-wrap\s*\{\s*height:\s*auto/.test(mob) && /\.chart-dock\s*\{\s*height:\s*auto/.test(mob));
check("CSS: десктопный док по-прежнему занимает высоту секции",
  /\.chart-section\.has-chart-dock\s*\{\s*display:\s*flex/.test(desk) &&
  /\.chart-dock\s*\{[^}]*height:\s*100%/.test(desk));

/* ===================== 2. Подписи дока и translate="no" ============== */

const dock = read("static/chart_dock.js");

check("JS: в исходниках дока нет опечатки «epy»", !/epy/i.test(dock),
  (dock.match(/.{0,40}epy.{0,40}/i) || [""])[0]);
check("JS: панель инструментов не отдаётся машинному переводу",
  /tools\.setAttribute\(\s*"translate",\s*"no"\s*\)/.test(dock));
check("JS: подписи режимов ставятся через textContent",
  /this\._modeBtns\.tabs\.textContent\s*=\s*T\.tabs\[0\]/.test(dock) &&
  /this\._modeBtns\.grid\.textContent\s*=\s*T\.grid\[0\]/.test(dock));
check("JS: кнопки режимов создаются без зашитого текста",
  /\["tabs",\s*"grid"\]\.forEach/.test(dock) &&
  !/\["tabs",\s*"grid"\]\.forEach\(\(view\)\s*=>\s*\{[\s\S]{0,400}?textContent/.test(dock));
check("JS: у режимов есть русские подписи (Вкладки/Мультиэкран/Сетка)",
  /tabs:\s*\["Вкладки"/.test(dock) && /grid:\s*\["Мультиэкран"/.test(dock) && /net:\s*"Сетка"/.test(dock));
check("JS: у режимов есть английские подписи (Tabs/Multiscreen/Grid)",
  /tabs:\s*\["Tabs"/.test(dock) && /grid:\s*\["Multiscreen"/.test(dock) && /net:\s*"Grid"/.test(dock));
check("JS: язык берётся со страницы, а не из константы",
  /document\.documentElement\.lang/.test(dock));
check("JS: подписи перерисовываются при смене языка",
  /i18n\.onChange\(\(\)\s*=>\s*this\._paintToolLabels\(\)\)/.test(dock));
check("JS: язык берётся из модуля страницы (LiqScopeI18n), а не из пустого window.I18n",
  /window\.LiqScopeI18n\s*\|\|\s*window\.I18n/.test(dock) && !/window\.I18n\.onChange/.test(dock),
  (dock.match(/i18n\s*=\s*window[^;]*/) || [""])[0]);
check("JS: подпись слота тоже заменяется, а не дописывается",
  /label\.textContent\s*=\s*pretty\(rec\.symbol\)/.test(dock));

/* ===================== 3. Живые секции без перевода ================== */

const html = read("static/index.html");
check("HTML: controls-bar помечен translate=\"no\"",
  /<section class="controls-bar" translate="no">/.test(html));
check("HTML: chart-section помечена translate=\"no\"",
  /<section class="chart-section" translate="no">/.test(html));
check("HTML: feed-section помечена translate=\"no\"",
  /<aside class="feed-section" translate="no">/.test(html));

/* ============ 4. i18n: подпись заменяется, а не дублируется ========== */

const i18n = read("static/i18n.js");
check("JS: setLabel пишет перевод в первый текстовый узел",
  /kids\[i\]\.nodeValue\s*=\s*text/.test(i18n));
check("JS: setLabel гасит остальные текстовые узлы (анти-дубль)",
  /kids\[i\]\.nodeValue\s*=\s*""/.test(i18n));

/* ===================== 5. Безопасный ресайз ========================== */

const app = read("static/app.js");
check("JS: у высоты графика есть страховка от схлопнутого контейнера",
  /const CHART_MIN_H\s*=\s*100/.test(app) && /const CHART_FALLBACK_H\s*=\s*400/.test(app));
const resizeBody = bodyOf(app, "const handleResize = () => {");
check("JS: handleResize подставляет высоту по умолчанию",
  /if\s*\(\s*h\s*<\s*CHART_MIN_H\s*\)\s*h\s*=\s*CHART_FALLBACK_H/.test(resizeBody), resizeBody.slice(0, 120));
check("JS: initChart не рисует график в свёрнутый контейнер",
  /if\s*\(\s*height\s*<\s*CHART_MIN_H\s*\)\s*height\s*=\s*CHART_FALLBACK_H/.test(app));
check("JS: ResizeObserver следит и за контейнером, и за обёрткой",
  /ro\.observe\(container\)/.test(app) && /ro\.observe\(chartWrapper\)/.test(app));

console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
process.exit(fail ? 1 : 0);
