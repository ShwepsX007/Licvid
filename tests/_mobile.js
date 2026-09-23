/**
 * Видно ли элемент на такой ширине экрана — для jsdom-тестов шапки.
 *
 * jsdom не считает разметку и не применяет медиа-запросы: `matchMedia` всегда
 * отвечает «нет», а `getComputedStyle` возвращает правила без учёта ширины.
 * Поэтому «телефон» мы проверяем по самим стилям: собираем из таблиц CSS
 * правила `display: none` вместе с их медиа-условиями и смотрим, попадает ли
 * ширина окна под условие. Элемент считается скрытым, если его прячет своё
 * правило или правило родителя.
 *
 * Так ловится поломка, из-за которой на телефоне с главной пропали кнопки
 * разделов: ссылки страницы помечены классом `nav-desk` (CSS прячет их на
 * телефоне), а кнопки шапки — классом `nav-mob` (наоборот, видны только на
 * телефоне). Проверяем не классы, а то, что видит гость: на каждой ширине у
 * каждого раздела должна остаться ровно одна работающая ссылка.
 *
 *     const { visibleLinks } = require("./_mobile");
 *     visibleLinks(win, nav, "/articles", 380).length === 1
 */

"use strict";

/** Порог мобильной вёрстки сайта: он же в account.css и landing.html. */
const MOBILE_MAX = 760;

/** Правила со `display`, обычные и внутри блоков `@media` с шириной. */
function widthRules(win) {
  const out = [];
  let sheets = [];
  try { sheets = Array.from(win.document.styleSheets || []); } catch (e) { return out; }
  sheets.forEach(function (sheet) {
    let list = [];
    try { list = Array.from(sheet.cssRules || []); } catch (e) { return; }
    list.forEach(function (rule) {
      if (rule.type === 4 && rule.cssRules) {              // @media
        const media = (rule.media && rule.media.mediaText) || "";
        if (!/width:/.test(media)) return;                 // print, motion и прочее не про ширину
        Array.from(rule.cssRules).forEach(function (inner) {
          if (inner.style) out.push({ sel: inner.selectorText, css: inner.style, media: media });
        });
      } else if (rule.style && rule.selectorText) {
        out.push({ sel: rule.selectorText, css: rule.style, media: "" });
      }
    });
  });
  return out;
}

/** Подходит ли медиа-условие под ширину окна. */
function mediaOk(text, width) {
  if (!text) return true;
  const parts = String(text).split(",").map((s) => s.trim()).filter(Boolean);
  if (!parts.length) return true;
  return parts.some(function (part) {
    if (!/width:/.test(part)) return false;
    const mins = (part.match(/min-width:\s*(\d+)px/g) || []).map((m) => Number(/(\d+)/.exec(m)[1]));
    const maxs = (part.match(/max-width:\s*(\d+)px/g) || []).map((m) => Number(/(\d+)/.exec(m)[1]));
    return mins.every((n) => width >= n) && maxs.every((n) => width <= n);
  });
}

/** Что именно прячет элемент на такой ширине (для сообщения об ошибке). */
function hiddenBy(win, el, width, rules) {
  const list = rules || widthRules(win);
  for (let n = el; n && n.nodeType === 1 && n.tagName !== "BODY"; n = n.parentNode) {
    for (let i = 0; i < list.length; i++) {
      const rule = list[i];
      if (!rule.sel || rule.css.display !== "none" || !mediaOk(rule.media, width)) continue;
      try {
        if (n.matches(rule.sel)) return rule.sel + (rule.media ? " @" + rule.media : "");
      } catch (e) { /* селектор jsdom не понял — пропускаем правило */ }
    }
  }
  return "";
}

function hiddenAt(win, el, width, rules) {
  return !!hiddenBy(win, el, width, rules);
}

/** Ссылки на адрес внутри меню, которые на этой ширине действительно видны. */
function visibleLinks(win, root, href, width) {
  if (!root || !root.querySelectorAll) return [];
  const rules = widthRules(win);
  return Array.from(root.querySelectorAll('a[href="' + href + '"]'))
    .filter(function (a) { return !hiddenAt(win, a, width, rules); });
}

/** Шапка страницы: сам блок меню и место, куда шапка дорисовывает кнопки. */
function navOf(win) {
  const doc = win.document;
  const box = doc.getElementById("nav-account");
  const nav = (box && (box.closest("nav") || box.parentNode)) || doc;
  return { box: box, nav: nav };
}

module.exports = {
  MOBILE_MAX: MOBILE_MAX,
  widthRules: widthRules,
  mediaOk: mediaOk,
  hiddenBy: hiddenBy,
  hiddenAt: hiddenAt,
  visibleLinks: visibleLinks,
  navOf: navOf,
};
