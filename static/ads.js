/**
 * LiqScope — 📣 рекламный баннер на главной.
 *
 * Баннер всплывает под кнопками «⚡ Открыть терминал» и «Что умеет» — сразу
 * после блока .hero-actions, — и показывает рекламный пост из /api/ads.
 * Что показывать, решает админка: страница только спрашивает список живых
 * объявлений, поэтому истёкший пост исчезает сам, без перезагрузки и без
 * действий с чьей-то стороны.
 *
 * Разметка и стили — в landing.html (.ad-banner). Скрипт подключается только
 * на главной: в терминале и кабинете баннера нет.
 */
(function () {
    "use strict";

    var HOST_ID = "ad-host";
    var STORE_KEY = "liqscope.ads.hidden";     // id, которые посетитель закрыл
    var ROTATE_MS = 15000;                      // если постов несколько — листаем

    var items = [];
    var index = 0;
    var timer = 0;

    function t(key, fallback) {
        try {
            if (window.LiqScopeI18n && window.LiqScopeI18n.t) {
                var v = window.LiqScopeI18n.t(key);
                if (v && v !== key) return v;
            }
        } catch (e) { /* словарь — не повод не показать баннер */ }
        return fallback || "";
    }

    function host() { return document.getElementById(HOST_ID); }

    function hiddenIds() {
        try {
            var raw = window.localStorage.getItem(STORE_KEY) || "[]";
            var arr = JSON.parse(raw);
            return Object.prototype.toString.call(arr) === "[object Array]" ? arr : [];
        } catch (e) {
            return [];
        }
    }

    function hideForever(id) {
        try {
            var arr = hiddenIds();
            if (arr.indexOf(id) < 0) arr.push(id);
            window.localStorage.setItem(STORE_KEY, JSON.stringify(arr.slice(-50)));
        } catch (e) { /* приватный режим — просто прячем сейчас */ }
    }

    function two(n) { return (n < 10 ? "0" : "") + n; }

    function untilLabel(ts) {
        // Время в часовом поясе посетителя, как и все даты на сайте.
        try {
            var d = new Date(Number(ts) * 1000);
            if (!ts || isNaN(d.getTime())) return "";
            return "⏳ " + two(d.getHours()) + ":" + two(d.getMinutes());
        } catch (e) { return ""; }
    }

    function bannerHtml(ad) {
        var body =
            '<div class="ad-head"><span class="ad-tag">' + t("ad.tag", "Реклама") + "</span>" +
            (ad.expires_at ? '<span class="ad-until">' + untilLabel(ad.expires_at) + "</span>" : "") +
            "</div>" +
            '<div class="ad-text">' + (ad.html || "") + "</div>";
        var media = ad.photo
            ? '<div class="ad-media"><img src="' + ad.photo + '" alt="" loading="lazy"></div>'
            : "";
        return '<div class="ad-banner' + (ad.photo ? "" : " no-photo") + '">' +
            '<button class="ad-close" type="button" data-ad-close="' + ad.id + '" title="' +
            t("ad.hide", "Больше не показывать") + '" aria-label="' +
            t("ad.close", "Закрыть") + '">✕</button>' +
            media + '<div class="ad-body">' + body + "</div></div>" +
            (items.length > 1
                ? '<div class="ad-dots">' + items.map(function (a, i) {
                    return '<i data-ad-dot="' + i + '" class="' + (i === index ? "on" : "") + '"></i>';
                }).join("") + "</div>"
                : "");
    }

    function render() {
        var box = host();
        if (!box) return;
        if (!items.length) {
            box.hidden = true;
            box.innerHTML = "";
            return;
        }
        if (index >= items.length) index = 0;
        box.innerHTML = bannerHtml(items[index]);
        box.hidden = false;
        var close = box.querySelector("[data-ad-close]");
        if (close) {
            close.addEventListener("click", function () {
                var id = Number(close.getAttribute("data-ad-close"));
                hideForever(id);
                items = items.filter(function (a) { return Number(a.id) !== id; });
                index = 0;
                render();
            });
        }
        box.querySelectorAll("[data-ad-dot]").forEach(function (dot) {
            dot.addEventListener("click", function () {
                index = Number(dot.getAttribute("data-ad-dot")) || 0;
                render();
            });
        });
    }

    function rotate() {
        if (items.length < 2) return;
        index = (index + 1) % items.length;
        render();
    }

    function load() {
        var box = host();
        if (!box) return;
        var skip = hiddenIds();
        window.fetch("/api/ads", { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                items = ((d && d.items) || []).filter(function (a) {
                    return a && skip.indexOf(a.id) < 0;
                });
                render();
                if (timer) clearInterval(timer);
                if (items.length > 1) timer = setInterval(rotate, ROTATE_MS);
            })
            .catch(function () {
                box.hidden = true;      // реклама — не повод ломать главную
            });
    }

    function boot() {
        load();
        try {
            if (window.LiqScopeI18n && window.LiqScopeI18n.onChange) {
                window.LiqScopeI18n.onChange(render);   // смена языка — новая подпись
            }
        } catch (e) { /* без i18n останутся русские подписи */ }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
