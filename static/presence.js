/**
 * LiqScope — «я ещё здесь».
 *
 * Маленький скрипт на всех страницах: раз в минуту говорит серверу, что гость
 * всё ещё на сайте, и уходит вместе с ним (``sendBeacon`` на уходе). Из этих
 * отметок админка собирает две вещи, которых не видно по одним визитам:
 * сколько времени человек провёл на сайте и кто сейчас онлайн.
 *
 * Ничего не отправляем, пока вкладка скрыта: свернутый браузер — это не
 * «человек у экрана», а лишние запросы. Вернулся — отправляем сразу.
 *
 * Скрипт без зависимостей и без разметки: если он не загрузился, сайт
 * работает как обычно, просто без времени и точки «онлайн».
 */
(function (global) {
    "use strict";

    // Раз в минуту. LIQSCOPE_PRESENCE_MS подменяет интервал: в тестах ждать
    // минуту не нужно, а в неспешных деплоях можно и пореже.
    var GAP = Number(global.LIQSCOPE_PRESENCE_MS) > 0
        ? Number(global.LIQSCOPE_PRESENCE_MS) : 60000;
    var FIRST = Number(global.LIQSCOPE_PRESENCE_FIRST_MS) >= 0
        ? Number(global.LIQSCOPE_PRESENCE_FIRST_MS) : 1500;   // страница уже ожила
    var timer = null;
    var started = false;

    function endpoint() {
        return "/api/visit/ping";
    }

    function payload() {
        return { path: (global.location && global.location.pathname) || "/" };
    }

    /** Отметка через fetch: тихо, без ожидания и без шума в консоли. */
    function ping() {
        if (!global.fetch || (global.document && global.document.hidden)) return;
        try {
            var p = global.fetch(endpoint(), {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload()),
            });
            if (p && p.catch) p.catch(function () { /* гость просто ушёл */ });
        } catch (e) { /* нет сети — не беда */ }
    }

    /** Уход со страницы: sendBeacon доживёт, даже если вкладку закрывают. */
    function bye() {
        if (!global.navigator || !global.navigator.sendBeacon) return;
        try {
            var body = JSON.stringify(payload());
            var blob = global.Blob ? new global.Blob([body], { type: "application/json" })
                                   : body;
            global.navigator.sendBeacon(endpoint(), blob);
        } catch (e) { /* не получилось — не беда */ }
    }

    function start() {
        if (started) return;
        started = true;
        global.setTimeout(ping, FIRST);
        timer = global.setInterval(ping, GAP);
        if (global.document && global.document.addEventListener) {
            global.document.addEventListener("visibilitychange", function () {
                if (global.document.hidden) bye();
                else ping();
            });
            global.addEventListener("pagehide", bye);
        }
    }

    function stop() {
        started = false;
        if (timer) global.clearInterval(timer);
        timer = null;
    }

    global.LiqScopePresence = { ping: ping, bye: bye, start: start, stop: stop,
                                GAP: GAP, FIRST: FIRST };

    // Страницы подключают скрипт до </body>: к этому моменту DOM уже есть.
    if (global.document && global.document.readyState === "loading") {
        global.document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})(window);
