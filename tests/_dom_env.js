/**
 * Общая среда jsdom-тестов.
 *
 * 1. gtag.js с googletagmanager в песочнице не грузится и сыпет jsdomError.
 *    Заглушка отдаёт пустой скрипт и глушит это событие — чужая сеть не
 *    должна красить прогон.
 * 2. stubNavigator ставит и language, и languages. Детектор сайта сначала
 *    смотрит languages; в jsdom там остаётся en-US, даже если language уже ru.
 *
 * Подключается первой строкой теста или через NODE_OPTIONS=--require.
 */
"use strict";

const { EventEmitter } = require("events");

const GTAG = /googletagmanager|google-analytics|gtag\/js|doubleclick\.net/i;

if (!EventEmitter.prototype.__liqGtag) {
  const origEmit = EventEmitter.prototype.emit;
  EventEmitter.prototype.emit = function (event, ...args) {
    if (event === "jsdomError" || event === "error") {
      const msg = args.map((x) => String((x && x.message) || x)).join(" ");
      if (GTAG.test(msg)) return false;
    }
    return origEmit.call(this, event, ...args);
  };
  EventEmitter.prototype.__liqGtag = true;
}

let jsdom = null;
try { jsdom = require("jsdom"); } catch (e) { jsdom = null; }

if (jsdom && jsdom.ResourceLoader && !jsdom.ResourceLoader.prototype.__liqGtag) {
  const origFetch = jsdom.ResourceLoader.prototype.fetch;
  jsdom.ResourceLoader.prototype.fetch = function (url, options) {
    const u = String(url || "");
    if (GTAG.test(u)) return Promise.resolve(Buffer.from("/* gtag stub */"));
    return origFetch.apply(this, arguments);
  };
  jsdom.ResourceLoader.prototype.__liqGtag = true;
}

/** ru-RU → language и languages, иначе детект сайта остаётся на en-US. */
function stubNavigator(win, tag) {
  const value = tag || "ru-RU";
  const base = String(value).split("-")[0];
  const langs = value === base ? [value] : [value, base];
  try {
    Object.defineProperty(win.navigator, "language", { value, configurable: true });
  } catch (e) { /* ignore */ }
  try {
    Object.defineProperty(win.navigator, "languages", { value: langs, configurable: true });
  } catch (e) { /* ignore */ }
}

module.exports = { stubNavigator };
