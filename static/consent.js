/**
 * LiqScope — плашка о cookie для новых посетителей.
 *
 * Показывается один раз: пока гость не нажал «Понятно», при каждом заходе
 * внизу висит аккуратная полоска с объяснением, зачем сайту cookie, и
 * кнопкой согласия. После нажатия выбор запоминается в localStorage, и
 * плашка больше не появляется (в «Подробнее» перечислены сами cookie).
 *
 * Подключается на всех страницах: /terminal, лендинг, кабинет, админка,
 * вход/сброс пароля и дайджест.
 */
(function (global) {
    "use strict";

    var KEY = "liqscope.cookie.ok";
    var VERSION = "1";
    var RU = {
        "cookie.title": "Сайт использует cookie",
        "cookie.text": "Это стандартная практика: cookie держат вас в аккаунте, помнят выбранный язык, а ещё помогают считать посещаемость — без них мы не отличаем живых гостей от служебных запросов.",
        "cookie.ok": "Понятно",
        "cookie.more": "Что именно хранится",
        "cookie.sid": "вход в аккаунт — пока вы не выйдете сами",
        "cookie.vid": "номер гостя — только для статистики посещений",
        "cookie.pref": "язык, тема и настройки графиков — в вашем браузере",
        "cookie.note": "Продолжая пользоваться сайтом, вы соглашаетесь с этим."
    };

    function t(key, fallback) {
        try {
            if (global.LiqScopeI18n && global.LiqScopeI18n.t) {
                var s = global.LiqScopeI18n.t(key);
                if (s && s !== key) return s;
            }
        } catch (e) { /* нет словаря — берём русский */ }
        return RU[key] || fallback || key;
    }

    function accepted() {
        try {
            return global.localStorage.getItem(KEY) === VERSION;
        } catch (e) {
            return false;            // приватный режим — показываем каждый раз
        }
    }

    function remember() {
        try {
            global.localStorage.setItem(KEY, VERSION);
        } catch (e) { /* не сохранилось — не беда */ }
    }

    function paint(bar) {
        bar.querySelectorAll("[data-consent]").forEach(function (el) {
            var key = el.getAttribute("data-consent");
            var txt = t(key);
            if (el.tagName === "BUTTON") el.textContent = txt;
            else el.innerHTML = txt;
        });
    }

    function build() {
        var bar = document.createElement("div");
        bar.className = "cookie-bar";
        bar.id = "cookie-bar";
        bar.setAttribute("role", "dialog");
        bar.setAttribute("aria-label", t("cookie.title"));
        bar.innerHTML =
            '<div class="cookie-in">' +
                '<span class="cookie-ico" aria-hidden="true">🍪</span>' +
                '<div class="cookie-txt">' +
                    '<b data-consent="cookie.title"></b> ' +
                    '<span data-consent="cookie.text"></span> ' +
                    '<span class="cookie-note" data-consent="cookie.note"></span>' +
                '</div>' +
                '<div class="cookie-acts">' +
                    '<button type="button" class="cookie-more-btn" data-consent="cookie.more"></button>' +
                    '<button type="button" class="cookie-ok" data-consent="cookie.ok"></button>' +
                '</div>' +
            '</div>' +
            '<ul class="cookie-list" hidden>' +
                '<li>🔐 <code>liqscope_sid</code> — <span data-consent="cookie.sid"></span></li>' +
                '<li>👥 <code>liqscope_vid</code> — <span data-consent="cookie.vid"></span></li>' +
                '<li>⚙️ <span data-consent="cookie.pref"></span></li>' +
            '</ul>';
        return bar;
    }

    function show() {
        if (document.getElementById("cookie-bar")) return;
        var bar = build();
        paint(bar);
        document.body.appendChild(bar);
        requestAnimationFrame(function () { bar.classList.add("on"); });

        var list = bar.querySelector(".cookie-list");
        bar.querySelector(".cookie-more-btn").addEventListener("click", function () {
            list.hidden = !list.hidden;
        });
        bar.querySelector(".cookie-ok").addEventListener("click", function () {
            remember();
            bar.classList.remove("on");
            setTimeout(function () { bar.remove(); }, 260);
        });
        if (global.LiqScopeI18n && global.LiqScopeI18n.onChange) {
            global.LiqScopeI18n.onChange(function () { paint(bar); });
        }
    }

    function boot() {
        if (accepted()) return;
        if (!document.body) {
            document.addEventListener("DOMContentLoaded", boot);
            return;
        }
        setTimeout(show, 600);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }

    global.LiqScopeConsent = { show: show, accepted: accepted, reset: function () {
        try { global.localStorage.removeItem(KEY); } catch (e) { /* пусто */ }
    } };
})(typeof window !== "undefined" ? window : this);
