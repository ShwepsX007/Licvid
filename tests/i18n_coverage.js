/**
 * Полнота переводов: не осталось ли русского текста в интерфейсе.
 *
 * Скрипт берёт все строки, которые сайт показывает человеку (кабинет,
 * доски сервисов, вход и регистрация, сброс пароля, дайджест, админка,
 * cookie-плашка, баннер) и прогоняет их настоящим сопоставителем из
 * static/i18n.js — тем самым, что работает в браузере. Печатается то,
 * что осталось на русском.
 *
 * Разметка с data-i18n переводится ключами, поэтому такие строки не
 * считаются пропущенными: они уже в словарях.
 *
 * Приём тот же, что у бота (tests/test_bot_i18n.py): забытая фраза видна
 * сразу, язык не разъезжается со временем.
 *
 * Запуск:
 *     node tests/i18n_coverage.js            # все языки, только сводка
 *     node tests/i18n_coverage.js zh         # один язык, список фраз
 *     node tests/i18n_coverage.js en --all   # показать и строки с ключами
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const LANGS = ["en", "zh", "hi", "es"];

const CYR = /[А-Яа-яЁё]/;
// «🇷🇺 Русский» в переключателе языка переводить не нужно: язык зовётся
// на самом себе — иначе испанец не найдёт свою строку в списке
const ENDONYM = /^(?:[\u{1F1E6}-\u{1F1FF}]{2}\s*)?(?:Русский|English|中文|हिन्दी|Español)$/u;
const SOURCES = [
  "static/account.js",
  "static/digest.js",
  "static/consent.js",
  "static/ads.js",
  "static/login.html",
  "static/cabinet.html",
  "static/reset.html",
  "static/admin.html",
  "static/geo_map.js",
  "static/presence.js",
  "static/digest.html",
  "static/index.html",
  "static/landing.html",
];

/** Песочница с настоящими словарями сайта. */
function sandbox() {
  const ctx = {
    window: {},
    navigator: { language: "ru", languages: ["ru"] },
    localStorage: null,
    console: console,
  };
  ctx.window = ctx;                       // window === global, как в браузере
  vm.createContext(ctx);
  for (const file of ["static/i18n.pages.js", "static/i18n.js"]) {
    const code = fs.readFileSync(path.join(ROOT, file), "utf8");
    vm.runInContext(code, ctx, { filename: file });
  }
  return ctx.LiqScopeI18n;
}

/** Строковые литералы файла без комментариев (как tools/i18n_scan.py). */
function literals(rel) {
  const src = fs.readFileSync(path.join(ROOT, rel), "utf8");
  if (rel.endsWith(".html")) return htmlLiterals(src);
  const out = [];
  src.split("\n").forEach((line) => {
    const st = line.trim();
    if (st.startsWith("//") || st.startsWith("*") || st.startsWith("/*")) return;
    const code = line.replace(/(?<!:)\/\/.*$/, "");
    const re = /"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'/g;
    let m;
    while ((m = re.exec(code))) {
      // «"cookie.sid": "…"» — это ключ объекта, а не текст на странице
      const rest = code.slice(re.lastIndex);
      if (/^\s*:/.test(rest)) continue;
      out.push({ text: m[1] !== undefined ? m[1] : m[2], keyed: isKeyedLine(code) });
    }
  });
  return out;
}

/** Разбор HTML по тегам: атрибут с data-i18n переводит и текст рядом с ним. */
function htmlLiterals(src) {
  const out = [];
  // код и стили страницы текстом не показываются — их не проверяем
  src = src.replace(/<script[\s\S]*?<\/script>/gi, "\n").replace(/<style[\s\S]*?<\/style>/gi, "\n");
  const tagRe = /<[a-zA-Z][^>]*>/gs;
  const tags = [];
  let m;
  while ((m = tagRe.exec(src))) tags.push({ at: m.index, end: tagRe.lastIndex, tag: m[0] });
  const push = (text, keyed) => {
    const t = String(text || "").trim();
    if (t) out.push({ text: t, keyed: !!keyed });
  };
  tags.forEach((t, i) => {
    const keyed = /data-i18n(-placeholder|-title|-meta)?\s*=/.test(t.tag);
    (t.tag.match(/"([^"]*)"/g) || []).forEach((q) => push(q.slice(1, -1), keyed));
    // текст между этим тегом и следующим
    const next = tags[i + 1];
    const body = src.slice(t.end, next ? next.at : src.length);
    if (body.trim()) push(body, keyed);
  });
  return out;
}

/** Строка размечает перевод ключом: текст подставит словарь. */
function isKeyedLine(line) {
  return /data-i18n(-placeholder|-title|-meta)?\s*=/.test(line);
}

/** Куски текста из строки: без разметки, но с подписями полей. */
function textUnits(raw) {
  const units = [];
  const attr = /(?:placeholder|title|aria-label|alt)="([^"]*)"/g;
  let m;
  while ((m = attr.exec(raw))) {
    if (CYR.test(m[1])) units.push(m[1].trim());
  }
  const text = raw
    .replace(/<[^>]*>/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"');
  text.split(/\n|(?<=[.!?])\s+/).forEach((chunk) => {
    let c = chunk.trim();
    // обрубки разметки (" type=..., '>Снять) текстом на странице не бывают
    if (c.indexOf("=") >= 0) c = c.replace(/^[^"']*["']\s*/, "");
    if (/["'<>=]/.test(c)) return;
    if (c.length > 1 && CYR.test(c)) units.push(c);
  });
  return units;
}

function main() {
  const args = process.argv.slice(2);
  const showAll = args.indexOf("--all") >= 0;
  const only = args.filter((a) => !a.startsWith("--"))[0];
  const langs = only ? [only] : LANGS;

  const i18n = sandbox();
  const ruKeys = new Set(
    Object.values((i18n.messages().ru) || {}).filter((v) => typeof v === "string")
  );

  const units = new Map();               // фраза → файл
  const keyed = new Map();               // фразы, переводимые ключом
  SOURCES.forEach((rel) => {
    if (!fs.existsSync(path.join(ROOT, rel))) return;
    literals(rel).forEach(({ text, keyed: isKeyed }) => {
      textUnits(text).forEach((u) => {
        if (isKeyed || ruKeys.has(u)) keyed.set(u, rel);
        else units.set(u, rel);
      });
    });
  });

  let bad = 0;
  langs.forEach((lang) => {
    const table = Object.keys(
      ((i18n.messages && i18n.messages().ru) || {})
    );
    const done = (t) => !CYR.test(i18n.phrase(t, lang));
    // фраза переведена, если её переводит собственная подстановка ИЛИ она
    // сидит внутри более длинной фразы, у которой перевод есть: движок ищет
    // самый длинный известный кусок в тексте узла и найдёт его
    const left = [];
    units.forEach((rel, text) => {
      let ok = done(text);
      if (!ok) {
        for (const k of table) {
          if (k.length > text.length && k.indexOf(text) >= 0 && done(k)) { ok = true; break; }
        }
      }
      if (!ok && ENDONYM.test(text)) ok = true;  // самоназвание языка — так и надо
      if (!ok) left.push([rel, text, i18n.phrase(text, lang)]);
    });
    console.log(`\n=== ${lang}: без перевода ${left.length} фраз` +
      (showAll ? "" : ` (в словарях ключами: ${keyed.size})`));
    if (left.length) {
      bad += left.length;
      left
        .sort((a, b) => a[1].length - b[1].length)
        .forEach(([rel, text]) => console.log(`   ? [${rel}] ${text}`));
    }
  });
  console.log(`\nфраз в проверке: ${units.size}, переведено ключами: ${keyed.size}`);
  return bad ? 1 : 0;
}

process.exit(main());
