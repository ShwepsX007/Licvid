/**
 * LiqScope — 📰 «Статьи»: список материалов и страница одной статьи.
 *
 * Карточки и текст статьи приходят с сервера уже на нужном языке (русский —
 * на русском сайте, английский — на остальных языках интерфейса, как в
 * дайджесте и сводках по часам): страница читается даже с выключенным JS, а
 * скрипт доделывает мелочи — даты в поясе гостя, «показать ещё» и счётчики.
 *
 * Язык текста статьи выбирает сервер, поэтому переключение языка в шапке
 * просто перезагружает страницу: адрес статьи (slug) от языка не зависит.
 */
(function () {
    "use strict";

    var I18n = window.LiqScopeI18n;
    var PAGE = 9;                       // сколько карточек показываем сразу

    function t(key, vars) { return I18n && I18n.t ? I18n.t(key, vars) : key; }
    function el(id) { return document.getElementById(id); }
    function localeTag() {
        return (I18n && I18n.localeTag && I18n.localeTag()) || "ru-RU";
    }

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function fmtDate(ts, withTime) {
        if (!ts) return "";
        try {
            var opts = { day: "numeric", month: "long", year: "numeric" };
            if (withTime) { opts.hour = "2-digit"; opts.minute = "2-digit"; }
            return new Intl.DateTimeFormat(localeTag(), opts).format(new Date(ts * 1000));
        } catch (e) {
            return "";
        }
    }

    /* Дата в карточке: сервер печатает в поясе выпуска, браузер — в поясе гостя */
    function stampDates(root) {
        var nodes = (root || document).querySelectorAll("[data-ts]");
        for (var i = 0; i < nodes.length; i++) {
            var ts = Number(nodes[i].getAttribute("data-ts") || 0);
            var label = fmtDate(ts, nodes[i].getAttribute("data-time") === "1");
            if (label) nodes[i].textContent = label;
        }
    }

    function api(url) {
        return fetch(url, { credentials: "same-origin" }).then(function (r) {
            return r.json().catch(function () { return {}; });
        });
    }

    /* ---------- список статей ---------- */

    function bootList() {
        var list = el("art-list");
        var pager = el("art-pager");
        if (!list) return;
        // Показанные карточки прячем за «показать ещё»: в списке они идут
        // подряд, но читателю приятнее видеть свежее без длинной прокрутки
        var cards = list.querySelectorAll(".art-card");
        for (var i = PAGE; i < cards.length; i++) cards[i].classList.add("hidden");
        if (pager && cards.length > PAGE) {
            pager.classList.remove("hidden");
            var more = el("art-more");
            if (more) {
                more.addEventListener("click", function () {
                    var hidden = list.querySelectorAll(".art-card.hidden");
                    for (var k = 0; k < PAGE && k < hidden.length; k++) {
                        hidden[k].classList.remove("hidden");
                    }
                    if (!list.querySelectorAll(".art-card.hidden").length) {
                        pager.classList.add("hidden");
                    }
                });
            }
        }
        stampDates(list);
        if (I18n && I18n.onChange) I18n.onChange(function () { stampDates(list); });

        api("/api/articles?limit=1").then(function (d) {
            if (!d || !d.ok) return;
            var total = el("art-total");
            if (total) total.textContent = String(d.count || 0);
            var fresh = el("art-fresh");
            if (fresh && d.items && d.items.length) {
                fresh.textContent = fmtDate(d.items[0].published_at || 0);
            } else if (fresh) {
                fresh.textContent = "—";
            }
        });
    }

    /* ---------- страница статьи ---------- */

    function slug() {
        try {
            var parts = (location.pathname || "").split("/").filter(Boolean);
            if (parts.length > 1) return decodeURIComponent(parts[parts.length - 1]);
        } catch (e) {}
        return "";
    }

    /* ---------- язык статьи: RU/EN на месте, без перезагрузки ------------- */

    /** Какой язык статьи показать сейчас: адрес (?lang=), потом язык сайта. */
    function wantedArticleLang(versions) {
        var want = "";
        try {
            want = new URLSearchParams(location.search).get("lang") || "";
        } catch (e) { /* старый браузер — смотрим только язык сайта */ }
        if (!want) want = (I18n && I18n.lang && I18n.lang()) || "ru";
        want = want === "ru" ? "ru" : "en";
        if (versions[want]) return want;
        return Object.keys(versions)[0] || want;
    }

    /** Отметить в разметке, на каком языке текст — так же, как это делает сервер. */
    function markLang(node, code, pageLang) {
        if (!node) return;
        if (!code || code === pageLang) node.removeAttribute("lang");
        else node.setAttribute("lang", code);
    }

    function bootLangSwitch() {
        var box = el("art-langs");
        var data = el("art-versions");
        if (!box || !data) return;
        var versions = {};
        try {
            versions = JSON.parse(data.textContent || "{}") || {};
        } catch (e) { return; }
        var codes = Object.keys(versions);
        if (codes.length < 2) return;                  // одна версия — переключать нечего
        var title = el("art-title"), body = el("art-body");

        function paint(code) {
            var v = versions[code] || {};
            // Язык текста в разметке сверяем с языком сайта: русская статья на
            // английской странице помечается lang="ru" — иначе скринридер
            // читает её как английскую. Сайт переключатель не меняет.
            var pageLang = ((I18n && I18n.lang && I18n.lang()) || "ru") === "ru" ? "ru" : "en";
            if (title && v.title) title.textContent = v.title;
            if (body) body.innerHTML = v.html || "";
            markLang(title, code, pageLang);
            markLang(body, code, pageLang);
            var links = box.querySelectorAll("a[data-lang]");
            for (var i = 0; i < links.length; i++) {
                links[i].classList.toggle("on", links[i].getAttribute("data-lang") === code);
            }
            try {
                var url = new URL(location.href);
                url.searchParams.set("lang", code);
                history.replaceState(null, "", url.pathname + url.search + url.hash);
            } catch (e) { /* адрес не меняем — текст всё равно переключился */ }
        }

        box.addEventListener("click", function (ev) {
            var link = ev.target.closest ? ev.target.closest("a[data-lang]") : null;
            if (!link) return;
            ev.preventDefault();                       // без перезагрузки
            paint(link.getAttribute("data-lang"));
        });
        paint(wantedArticleLang(versions));
        if (I18n && I18n.onChange) {
            I18n.onChange(function () { paint(wantedArticleLang(versions)); });
        }
    }

    function bootArticle() {
        var title = el("art-title");
        var body = el("art-body");
        if (!title || !body) return;
        var id = slug();
        if (!id) return;
        // Даты печатаем в поясе гостя: сервер их уже отдал, но в поясе выпуска
        stampDates(document);
        if (I18n && I18n.onChange) I18n.onChange(function () { stampDates(document); });
        bootLangSwitch();
        api("/api/articles/" + encodeURIComponent(id)).then(function (d) {
            if (d && d.ok && d.item) return;
            // Статью сняли с сайта или адрес устарел: пустой страницы быть не
            // должно — говорим словами и уводим в список
            title.textContent = t("art.gone");
            body.innerHTML = '<p><a href="/articles">' + esc(t("art.all")) + "</a></p>";
            var cover = el("art-cover");
            if (cover) cover.classList.add("hidden");
            var meta = el("art-meta");
            if (meta) meta.textContent = "";
            var langs = el("art-langs");
            if (langs) langs.innerHTML = "";
        });
    }

    function boot() {
        if (I18n && I18n.init) I18n.init();
        var page = (document.body && document.body.getAttribute("data-page")) || "";
        if (page === "article") bootArticle();
        else bootList();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
