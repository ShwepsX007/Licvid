/**
 * LiqScope — 📣 рекламный баннер: главная, терминал, дайджест, сводка по часам,
 * статьи.
 *
 * Баннер собирается из активных объявлений (/api/ads?place=) и живёт в блоке
 * ``#ad-host``: на главной — под кнопками героя, в терминале — под графиком,
 * в дайджесте и сводке — над таблицей. Что показывать и как крутить, решает
 * админка: у каждого объявления свои фото, ссылка, срок и подпись, поэтому
 * несколько акций живут одновременно — каруселью или сеткой.
 *
 * Правила, которые здесь важны:
 *
 *   * баннер **нельзя закрыть** — крестика нет, старый ключ
 *     ``liqscope.ads.hidden`` стираем;
 *   * фото видно **целиком** (contain; cover — по выбору админа), подпись под фото;
 *   * клик по фото и по подписи (если в ней нет своих ссылок) ведёт по ссылке;
 *   * текст не обязателен: баннер из одной картинки — норма;
 *   * HTML-баннер: админ может выбрать тип — картинка+ссылка или произвольный
 *     HTML-код, который вставляется в слот баннера как есть;
 *   * смена и плавность берутся из ответа сервера (banner), админ меняет без перезапуска;
 *   * срок переживаем на лету: раз в минуту переспрашиваем /api/ads;
 *   * в терминале баннер скрывается для зарегистрированных (ad-free), на остальных
 *     страницах (landing, digest, hourly) виден всем.
 *
 * Стили — в ``static/ads.css``; странице нужен только контейнер ``#ad-host``
 * с ``data-ad-place`` (landing | terminal | digest | hourly | articles).
 */
(function () {
    "use strict";

    var HOST_ID = "ad-host";
    var HIDDEN_KEY = "liqscope.ads.hidden";       // наследие крестика
    var REFRESH_MS = 60000;
    var SWIPE_MIN = 24;                           // px до «это свайп»
    var DEFAULTS = {
        rotate_sec: 8, fade_ms: 500, layout: "carousel",
        max: 4, fit: "contain", caption: "below"
    };

    var cfg = copy(DEFAULTS);
    var items = [];
    var index = 0;
    var timer = 0;
    var expiry = 0;
    var refresh = 0;
    var paused = false;

    function copy(src) {
        var out = {};
        for (var k in src) {
            if (Object.prototype.hasOwnProperty.call(src, k)) out[k] = src[k];
        }
        return out;
    }

    function t(key, fallback) {
        try {
            if (window.LiqScopeI18n && window.LiqScopeI18n.t) {
                var v = window.LiqScopeI18n.t(key);
                if (v && v !== key) return v;
            }
        } catch (e) { /* словарь — не повод не показать баннер */ }
        return fallback || "";
    }

    function esc(text) {
        return String(text == null ? "" : text).replace(/[&<>\"]/g, function (ch) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
        });
    }

    function host() { return document.getElementById(HOST_ID); }

    function place() {
        var box = host();
        var val = box ? (box.getAttribute("data-ad-place") || "") : "";
        val = String(val || "").trim().toLowerCase();
        if (val === "terminal" || val === "digest" || val === "hourly"
                || val === "landing" || val === "articles") return val;
        return "landing";
    }

    function num(val, min, max, fallback) {
        var n = Math.round(Number(val));
        if (!isFinite(n)) n = Math.round(Number(fallback));
        if (!isFinite(n)) return min;
        return Math.max(min, Math.min(max, n));
    }

    /** «Убери анимацию» в системе — тогда слайды меняются мгновенно. */
    function calm() {
        try {
            return !!(window.matchMedia &&
                      window.matchMedia("(prefers-reduced-motion: reduce)").matches);
        } catch (e) { return false; }
    }

    function forgetOldHide() {
        try { window.localStorage.removeItem(HIDDEN_KEY); } catch (e) { /* приватный режим */ }
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

    /** Голый текст объявления: для alt, aria-подписей и «слайд без всего». */
    function plain(ad) {
        return String((ad && ad.text) || "").replace(/\s+/g, " ").trim();
    }

    function hasHtmlBanner(ad) {
        var raw = ad && (ad.banner_html || ad.html_banner || ad.html_code || "");
        if (raw == null) return false;
        return !!String(raw).trim();
    }

    function getHtmlBanner(ad) {
        return String((ad && (ad.banner_html || ad.html_banner || ad.html_code)) || "");
    }

    /**
     * Один слайд. Ссылка объявляет <a>, а не вешается на клик: так работают
     * средняя кнопка, «открыть в новой вкладке» и клавиатура. Разметка
     * Telegram-текста может содержать свои <a> — вложить их в наш нельзя,
     * поэтому подпису оборачиваем в ссылку только когда в ней ссылать нечего.
     *
     * HTML-баннер: если есть banner_html — вставляем как есть в слот.
     */
    function slideHtml(ad, i) {
        var link = String(ad.link || "");
        var text = plain(ad);
        var html = ad.html || esc(text);
        var hasLinks = /<a\s/i.test(String(ad.html || ""));
        var rawBanner = getHtmlBanner(ad);
        var isHtml = hasHtmlBanner(ad);
        var flag = '<span class="ad-tag ad-flag">' + esc(t("ad.tag", "Реклама")) + "</span>";

        if (isHtml) {
            var capHtml = "";
            if (text || ad.expires_at) {
                capHtml = '<div class="ad-cap">' + flag +
                    (text ? '<span class="ad-text">' + html + "</span>" : "") +
                    (ad.expires_at ? '<span class="ad-until">' + untilLabel(ad.expires_at) + "</span>" : "") +
                    "</div>";
                if (link && !hasLinks) {
                    capHtml = '<a class="ad-hit ad-hit-cap" href="' + esc(link) +
                        '" target="_blank" rel="noopener sponsored" tabindex="-1"' +
                        ' aria-hidden="true">' + capHtml + "</a>";
                }
            }
            // HTML-баннер: raw HTML вставляется без экранирования — админ доверенный
            return '<div class="ad-slide ad-html' + (link ? " ad-linked" : "") +
                '" data-ad-index="' + i + '">' +
                '<div class="ad-html-banner">' + rawBanner + "</div>" +
                capHtml + "</div>";
        }

        var media = '<div class="ad-media">' +
            (ad.photo
                ? '<img src="' + esc(ad.photo) + '" alt="' + esc(text.slice(0, 120)) +
                  '" loading="lazy">'
                : "") + "</div>";
        // Подпись нужна, когда есть что читать или когда срок на исходе. Голый
        // «Реклама»-блок под одной картинкой — только дыра в макете, поэтому
        // для такого слайда метку ставим флажком в углу фото.
        var cap = "";
        if (text || ad.expires_at) {
            cap = '<div class="ad-cap">' + flag +
                (text ? '<span class="ad-text">' + html + "</span>" : "") +
                (ad.expires_at ? '<span class="ad-until">' + untilLabel(ad.expires_at) + "</span>" : "") +
                "</div>";
        } else if (ad.photo) {
            media = '<div class="ad-media">' + flag +
                '<img src="' + esc(ad.photo) + '" alt="' + esc(t("ad.tag", "Реклама")) +
                '" loading="lazy"></div>';
        }
        if (link) {
            var label = t("ad.open", "Открыть") +
                (text ? ": " + text.slice(0, 80) : "");
            media = '<a class="ad-hit" href="' + esc(link) + '" target="_blank"' +
                ' rel="noopener sponsored" aria-label="' + esc(label) + '">' +
                media + "</a>";
            if (!hasLinks) {
                cap = '<a class="ad-hit ad-hit-cap" href="' + esc(link) +
                    '" target="_blank" rel="noopener sponsored" tabindex="-1"' +
                    ' aria-hidden="true">' + cap + "</a>";
            }
        }
        return '<div class="ad-slide' + (link ? " ad-linked" : "") +
            (ad.photo ? "" : " ad-no-photo") + '" data-ad-index="' + i + '">' +
            media + cap + "</div>";
    }

    /** На главной карусель не крутим: она залезала в карточку и сдвигала блоки. */
    function quietLanding() {
        return place() === "landing" && cfg.layout !== "grid";
    }

    function pagerHtml() {
        if (cfg.layout === "grid" || quietLanding() || items.length < 2) return "";
        var dots = items.map(function (a, i) {
            return '<button type="button" class="ad-dot" data-ad-dot="' + i +
                '" aria-label="' + esc(t("ad.tag", "Реклама") + " " + (i + 1) + "/" + items.length) +
                '"></button>';
        }).join("");
        return '<div class="ad-pager">' +
            '<button type="button" class="ad-nav" data-ad-nav="-1" aria-label="' +
            esc(t("ad.prev", "Предыдущее")) + '">‹</button>' +
            '<div class="ad-dots">' + dots + "</div>" +
            '<button type="button" class="ad-nav" data-ad-nav="1" aria-label="' +
            esc(t("ad.next", "Следующее")) + '">›</button>' +
            "</div>";
    }

    function paintFlags(box) {
        var fade = calm() ? 0 : num(cfg.fade_ms, 0, 4000, 0);
        box.setAttribute("data-ad-layout", cfg.layout);
        box.setAttribute("data-ad-fit", cfg.fit);
        box.setAttribute("data-ad-caption", cfg.caption);
        box.setAttribute("data-ad-count", String(items.length));
        box.setAttribute("data-ad-rotate", String(num(cfg.rotate_sec, 2, 120, 8)));
        box.setAttribute("data-ad-fade", String(fade));
        box.style.setProperty("--ad-fade", fade + "ms");
    }

    function mark() {
        var box = host();
        if (!box) return;
        var grid = cfg.layout === "grid" || quietLanding();
        Array.prototype.forEach.call(box.querySelectorAll(".ad-slide"), function (el) {
            if (grid) { el.classList.add("on"); return; }
            el.classList.toggle("on", Number(el.getAttribute("data-ad-index")) === index);
        });
        Array.prototype.forEach.call(box.querySelectorAll(".ad-dot"), function (dot) {
            dot.classList.toggle("on", Number(dot.getAttribute("data-ad-dot")) === index);
        });
    }

    function show(i) {
        var n = items.length || 1;
        index = ((Math.round(Number(i) || 0) % n) + n) % n;
        mark();
    }

    function bind(box) {
        Array.prototype.forEach.call(box.querySelectorAll("[data-ad-dot]"), function (dot) {
            dot.addEventListener("click", function () {
                show(Number(dot.getAttribute("data-ad-dot")) || 0);
                arm();
            });
        });
        Array.prototype.forEach.call(box.querySelectorAll("[data-ad-nav]"), function (btn) {
            btn.addEventListener("click", function () {
                show(index + (Number(btn.getAttribute("data-ad-nav")) || 1));
                arm();
            });
        });
        var stage = box.querySelector(".ad-stage");
        if (!stage || quietLanding()) return;
        ["mouseenter", "focusin", "touchstart"].forEach(function (ev) {
            stage.addEventListener(ev, function () { paused = true; });
        });
        ["mouseleave", "focusout", "touchend"].forEach(function (ev) {
            stage.addEventListener(ev, function () { paused = false; });
        });
        var x0 = 0, y0 = 0;
        stage.addEventListener("touchstart", function (e) {
            var p = (e.touches || [])[0] || {};
            x0 = Number(p.clientX) || 0;
            y0 = Number(p.clientY) || 0;
        }, { passive: true });
        stage.addEventListener("touchend", function (e) {
            var p = (e.changedTouches || [])[0] || {};
            var dx = (Number(p.clientX) || 0) - x0;
            var dy = (Number(p.clientY) || 0) - y0;
            if (Math.abs(dx) < SWIPE_MIN || Math.abs(dx) < Math.abs(dy)) return;
            show(index + (dx < 0 ? 1 : -1));
            arm();
        }, { passive: true });
    }

    function step() {
        if (cfg.layout === "grid" || quietLanding() || paused || items.length < 2) return;
        show(index + 1);
    }

    function arm() {
        if (timer) { clearInterval(timer); timer = 0; }
        if (cfg.layout === "grid" || quietLanding() || items.length < 2) return;
        timer = setInterval(step, Math.max(2, cfg.rotate_sec) * 1000);
    }

    function alive(ad) {
        var ts = Number(ad && ad.expires_at) || 0;
        return !ts || ts > Date.now() / 1000;
    }

    function nextExpiry() {
        var now = Date.now() / 1000, soon = 0;
        items.forEach(function (ad) {
            var ts = Number(ad.expires_at) || 0;
            if (ts > now && (!soon || ts < soon)) soon = ts;
        });
        return soon;
    }

    function prune() {
        expiry = 0;
        var before = items.length;
        items = items.filter(alive);
        if (!items.length) { render(); return; }
        if (items.length !== before) {
            if (index >= items.length) index = 0;
            render();
        } else {
            armExpiry();
        }
    }

    function armExpiry() {
        if (expiry) { clearTimeout(expiry); expiry = 0; }
        var soon = nextExpiry();
        if (!soon) return;
        expiry = setTimeout(prune, Math.max(300, (soon - Date.now() / 1000) * 1000));
    }

    function render() {
        var box = host();
        if (!box) return;
        if (!items.length) {
            box.hidden = true;
            box.innerHTML = "";
            if (timer) { clearInterval(timer); timer = 0; }
            if (expiry) { clearTimeout(expiry); expiry = 0; }
            return;
        }
        if (index >= items.length || index < 0) index = 0;
        box.innerHTML = '<div class="ad-banner"><div class="ad-stage">' +
            items.map(slideHtml).join("") + "</div>" + pagerHtml() + "</div>";
        paintFlags(box);
        mark();
        bind(box);
        box.hidden = false;
        arm();
        armExpiry();
    }

    function applyCfg(incoming) {
        var src = incoming || {};
        cfg = {
            rotate_sec: num(src.rotate_sec, 2, 120, DEFAULTS.rotate_sec),
            fade_ms: calm() ? 0 : num(src.fade_ms, 0, 2000, DEFAULTS.fade_ms),
            layout: src.layout === "grid" ? "grid" : "carousel",
            max: num(src.max, 1, 8, DEFAULTS.max),
            fit: src.fit === "cover" ? "cover" : "contain",
            caption: src.caption === "side" ? "side" : "below"
        };
    }

    function fetchAuth() {
        try {
            return window.fetch("/api/auth/me", { credentials: "same-origin" })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) { return (d && d.user) ? d.user : null; })
                .catch(function () { return null; });
        } catch (e) { return Promise.resolve(null); }
    }

    function loadAdsData() {
        return window.fetch("/api/ads?place=" + encodeURIComponent(place()),
                     { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .catch(function () { return null; });
    }

    function load() {
        var box = host();
        if (!box) return;
        var pl = place();
        if (pl === "terminal") {
            fetchAuth().then(function (user) {
                if (user) {
                    items = [];
                    var b = host();
                    if (b) { b.hidden = true; b.innerHTML = ""; }
                    if (timer) { clearInterval(timer); timer = 0; }
                    if (expiry) { clearTimeout(expiry); expiry = 0; }
                    return;
                }
                loadAdsData().then(function (d) {
                    applyCfg(d && d.banner);
                    items = (((d && d.items) || []).filter(function (a) {
                        return a && (a.photo || plain(a) || hasHtmlBanner(a)) && alive(a);
                    })).slice(0, cfg.max);
                    render();
                }).catch(function () {
                    items = [];
                    render();
                });
            });
            return;
        }
        loadAdsData().then(function (d) {
            applyCfg(d && d.banner);
            items = (((d && d.items) || []).filter(function (a) {
                return a && (a.photo || plain(a) || hasHtmlBanner(a)) && alive(a);
            })).slice(0, cfg.max);
            render();
        }).catch(function () {
            items = [];
            render();
        });
    }

    function boot() {
        var box = host();
        if (!box) return;
        forgetOldHide();
        load();
        if (!refresh) {
            refresh = setInterval(function () {
                if (document.hidden) return;
                load();
            }, REFRESH_MS);
        }
        try {
            if (window.LiqScopeI18n && window.LiqScopeI18n.onChange) {
                window.LiqScopeI18n.onChange(render);
            }
        } catch (e) { }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
