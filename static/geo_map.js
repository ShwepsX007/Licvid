/**
 * LiqScope — 🌍 карта посещений в админке.
 *
 * Рисует карточку «Откуда приходят и как долго смотрят»: карту мира с
 * контурами стран, точки гостей за выбранный период и живые точки тех, кто на
 * сайте сейчас (они пульсируют), таблицы по странам, источникам переходов и
 * временем на сайте.
 *
 * Данные — ``GET /api/admin/geo?period=24h|7d|30d`` (см. web_geo.py), контуры
 * стран — ``static/world-map.js``. Проекция у обоих одна и та же
 * (equirectangular 1000×500), поэтому точки ложатся точно на контуры:
 *     x = (lon + 180) / 360 · 1000,   y = (90 − lat) / 180 · 500
 *
 * Подписи берутся ключами словаря (data-i18n и ``I18n.t``), поэтому карточка
 * говорит на том же языке, что и вся страница. При смене языка список
 * перерисовывается.
 */
(function (global) {
    "use strict";

    var REFRESH_MS = 20000;         // как часто обновляем картину
    var MAP_W = 1000, MAP_H = 500;  // размеры полотна карты (см. world-map.js)
    var VIEW_TOP = 12, VIEW_H = 398;   // видимая часть: без Антарктиды
    var MAX_BUBBLE = 26;

    var I18n = global.LiqScopeI18n || { t: function (k) { return k; },
                                        number: function (n) { return String(n); },
                                        plural: function (n, f) { return f[2] || f[0]; },
                                        onChange: function () {} };

    var panel = null;
    var period = "24h";
    var timer = null;
    var last = null;
    var req = 0;

    function t(key, vars) {
        try { return I18n.t(key, vars); } catch (e) { return key; }
    }

    function num(n) {
        try { return I18n.number(Number(n) || 0); } catch (e) { return String(n); }
    }

    /** Склонение по числу: «1 гость», «2 гостя», «5 гостей».
     *
     * Формы живут в словаре (по три на язык), сам выбор делает i18n: в
     * русском правила сложнее, чем «один/много». */
    function unit(key, n) {
        var forms = [t(key + "_one"), t(key + "_few"), t(key + "_many")];
        try { return I18n.plural(Number(n) || 0, forms); } catch (e) { return forms[2]; }
    }

    /** «3 гостя» — число вместе со словом (для подсказок на карте). */
    function visitors(n) {
        return num(n) + " " + unit("geo.u_visitors", n);
    }

    function esc(text) {
        return String(text === null || text === undefined ? "" : text)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    /** Длительность короткой подписью: 12 с, 4 мин, 1 ч 20 мин. */
    function fmtSec(sec) {
        var s = Math.max(0, Math.round(Number(sec) || 0));
        if (s < 60) return num(s) + " " + t("geo.u.s");
        var m = Math.round(s / 60);
        if (m < 60) return num(m) + " " + t("geo.u.m");
        var h = Math.floor(m / 60), rest = m % 60;
        return num(h) + " " + t("geo.u.h") +
            (rest ? " " + num(rest) + " " + t("geo.u.m") : "");
    }

    function fmtWhen(ts) {
        var n = Number(ts) || 0;
        if (!n) return t("geo.empty");
        var diff = Math.max(0, (Date.now() / 1000) - n);
        if (diff < 90) return t("geo.now");
        try { return I18n.time(n) || t("geo.empty"); } catch (e) { return t("geo.empty"); }
    }

    function x(lon) { return ((Number(lon) + 180) / 360) * MAP_W; }
    function y(lat) { return ((90 - Number(lat)) / 180) * MAP_H; }

    function kindLabel(kind) {
        var known = ["direct", "search", "social", "referral", "internal",
                     "campaign", "app"];
        var key = "geo.kind." + (known.indexOf(kind) >= 0 ? kind : "direct");
        return t(key);
    }

    /** Источник перехода: хост, а для прямых заходов — прочерк.
     *
     * Вид перехода стоит отдельной колонкой («прямой заход», «поиск»), так
     * что в первой колонке дублировать его не нужно. */
    function sourceLabel(row) {
        if (row && row.source) return esc(row.source);
        return esc(t("geo.empty"));
    }

    /** Имя страны на языке страницы: его умеет собирать сам браузер.
     *
     * Справочник ``geo_countries.py`` знает только английское имя (оно нужно
     * серверу), а ``Intl.DisplayNames`` даёт «Финляндия»/「芬兰」/「Finlandia」
     * без таблиц на нашей стороне. Нет поддержки — остаётся имя из ответа.
     */
    var regions = null, regionsLang = "";

    function displayName(cc, fallback) {
        var code = String(cc || "").toUpperCase();
        if (!code) return fallback || "";
        var lang = I18n.lang ? I18n.lang() : "ru";
        try {
            if (global.Intl && global.Intl.DisplayNames) {
                if (!regions || regionsLang !== lang) {
                    regions = new global.Intl.DisplayNames([lang], { type: "region" });
                    regionsLang = lang;
                }
                var name = regions.of(code);
                if (name && name !== code) return name;
            }
        } catch (e) { /* нет Intl — показываем как пришло */ }
        return fallback || code;
    }

    function countryName(row) {
        if (row && row.country) return esc(displayName(row.country, row.name));
        return esc(t("geo.unknown"));
    }

    function guestLabel(row) {
        if (row && row.user && row.user.name) return esc(row.user.name);
        var vid = String((row && row.vid) || "");
        return t("geo.guest") + (vid ? " #" + esc(vid.slice(0, 6)) : "");
    }

    /** Контуры стран: рисуем один раз, дальше только точки. */
    function landPaths() {
        var world = global.LIQSCOPE_WORLD;
        if (!world || !world.countries) return "";
        return world.countries.map(function (row) {
            return '<path class="geo-land" d="' + row[1] + '"></path>';
        }).join("");
    }

    function pointTitle(p) {
        var bits = [p.country ? displayName(p.country, p.name) : t("geo.unknown"),
                    fmtSec(p.sec)];
        if (p.path) bits.push(p.path);
        if (p.source) bits.push(p.source);
        else bits.push(kindLabel(p.source_kind || "direct"));
        return bits.join(" · ");
    }

    function mapSvg(data) {
        var bubbles = (data.countries || []).filter(function (c) {
            return c.lat !== null && c.lon !== null && c.visitors > 0;
        }).map(function (c) {
            var r = Math.min(MAX_BUBBLE, 3 + Math.sqrt(c.visitors) * 2.2);
            var online = Number(c.online) || 0;
            var title = displayName(c.country, c.name) + " — " + visitors(c.visitors) +
                (online ? " · " + t("geo.chip_online") : "");
            return '<circle class="geo-bubble' + (online ? " geo-bubble-on" : "") +
                '" data-cc="' + esc(c.country) + '" cx="' + x(c.lon).toFixed(1) +
                '" cy="' + y(c.lat).toFixed(1) + '" r="' + r.toFixed(1) + '">' +
                "<title>" + esc(title) + "</title></circle>";
        }).join("");

        var dots = (data.points || []).filter(function (p) {
            return p.lat !== null && p.lon !== null && !p.online;
        }).map(function (p) {
            return '<circle class="geo-dot" cx="' + x(p.lon).toFixed(1) +
                '" cy="' + y(p.lat).toFixed(1) + '" r="1.8"><title>' +
                esc(pointTitle(p)) + "</title></circle>";
        }).join("");

        // живые: пульсирующий ореол + точка; на карте видно «дышит»
        var live = (data.online || []).filter(function (p) {
            return p.lat !== null && p.lon !== null;
        }).map(function (p) {
            var cx = x(p.lon).toFixed(1), cy = y(p.lat).toFixed(1);
            return '<g class="geo-live-dot"><circle class="geo-pulse" cx="' + cx +
                '" cy="' + cy + '" r="9"></circle><circle class="geo-on" cx="' + cx +
                '" cy="' + cy + '" r="3.2"><title>' + esc(pointTitle(p)) +
                "</title></circle></g>";
        }).join("");

        var empty = (!bubbles && !dots && !live)
            ? '<text class="geo-map-empty" x="500" y="200" text-anchor="middle">' +
              esc(t("geo.map_empty")) + "</text>"
            : "";
        return '<svg class="geo-map" id="geo-map" viewBox="0 0 ' + MAP_W + " " +
            VIEW_TOP + " " + MAP_W + " " + VIEW_H + '" role="img" ' +
            'aria-label="' + esc(t("geo.map_alt")) + '">' +
            '<g class="geo-lands">' + landPaths() + "</g>" +
            '<g class="geo-bubbles">' + bubbles + "</g>" +
            '<g class="geo-dots">' + dots + "</g>" +
            '<g class="geo-live">' + live + "</g>" + empty + "</svg>";
    }

    function chips(data) {
        var tot = data.totals || {};
        var online = tot.online || 0, guests = tot.visitors || 0;
        var views = tot.views || 0, ccs = tot.countries || 0;
        var rows = [
            [online, t("geo.chip_online"), "geo-chip-on"],
            [guests, unit("geo.u_visitors", guests), ""],
            [views, unit("geo.u_views", views), ""],
            [ccs, unit("geo.u_countries", ccs), ""],
            [fmtSec(tot.avg_sec || 0), t("geo.chip_avg"), ""],
        ];
        return rows.map(function (row) {
            return '<div class="geo-chip ' + row[2] + '">' +
                "<b>" + (typeof row[0] === "number" ? num(row[0]) : row[0]) +
                "</b><span>" + esc(row[1]) + "</span></div>";
        }).join("");
    }

    function legend(data) {
        var bits = ['<span class="geo-key"><i class="geo-i-live"></i>' +
                    esc(t("geo.legend_online")) + "</span>",
                    '<span class="geo-key"><i class="geo-i-dot"></i>' +
                    esc(t("geo.legend_visits")) + "</span>",
                    '<span class="geo-key"><i class="geo-i-bubble"></i>' +
                    esc(t("geo.legend_bubble")) + "</span>"];
        var geo = data.geo || {};
        bits.push('<span class="geo-key geo-note">' + esc(geo.edge
            ? t("geo.src_edge") : t("geo.src_provider",
                                    { p: geo.provider || t("geo.src_service") })) +
            "</span>");
        return bits.join("");
    }

    function table(id, heads, rows, empty) {
        var head = heads.map(function (h) { return "<th>" + esc(t(h)) + "</th>"; }).join("");
        var body = (rows && rows.length)
            ? rows.join("")
            : '<tr><td colspan="' + heads.length + '" class="geo-empty">' +
              esc(t(empty || "geo.empty")) + "</td></tr>";
        return '<table class="geo-table" id="' + id + '"><thead><tr>' + head +
            "</tr></thead><tbody>" + body + "</tbody></table>";
    }

    /** Тело карточки: шапка с кнопками периода остаётся, меняется только оно. */
    function body() {
        var box = panel && panel.querySelector("#geo-body");
        return box || panel;
    }

    function render(data) {
        last = data;
        if (!panel) return;
        panel = panel;
        var countries = (data.countries || []).slice(0, 12).map(function (c) {
            return "<tr><td>" + countryName(c) + "</td>" +
                '<td class="geo-num' + ((c.online ? " geo-hot" : "")) + '">' +
                (c.online ? num(c.online) : "—") + "</td>" +
                '<td class="geo-num">' + num(c.visitors) + "</td>" +
                '<td class="geo-num">' + num(c.views) + "</td>" +
                '<td class="geo-num">' + fmtSec(c.avg_sec) + "</td>" +
                '<td class="geo-num">' + fmtWhen(c.last) + "</td></tr>";
        });
        var sources = (data.sources || []).slice(0, 12).map(function (s) {
            return "<tr><td>" + sourceLabel(s) + "</td><td>" + esc(kindLabel(s.kind)) +
                '</td><td class="geo-num">' + num(s.visitors) + "</td>" +
                '<td class="geo-num">' + num(s.views) + "</td></tr>";
        });
        var live = (data.online || []).map(function (p) {
            return "<tr><td>" + guestLabel(p) + "</td><td>" + countryName(p) +
                "</td><td>" + esc(p.path || "—") + "</td><td>" + sourceLabel(p) +
                '</td><td class="geo-num">' + fmtSec(p.sec) + "</td></tr>";
        });
        var long = (data.long || []).slice(0, 8).map(function (p) {
            return "<tr><td>" + guestLabel(p) + "</td><td>" + countryName(p) +
                '</td><td class="geo-num">' + fmtSec(p.sec) + "</td><td>" +
                esc(p.path || "—") + "</td><td>" + sourceLabel(p) + "</td></tr>";
        });
        var paths = (data.paths || []).slice(0, 8).map(function (p) {
            return "<tr><td>" + esc(p.path || "/") + '</td><td class="geo-num">' +
                num(p.n) + "</td></tr>";
        });

        body().innerHTML =
            '<div class="geo-chips">' + chips(data) + "</div>" +
            '<div class="geo-map-wrap">' + mapSvg(data) +
            '<div class="geo-legend">' + legend(data) + "</div></div>" +
            '<div class="geo-cols">' +
            '<div class="geo-col">' + '<h4>' + esc(t("geo.countries")) + "</h4>" +
            table("geo-countries", ["geo.col_country", "geo.col_online",
                                    "geo.col_visitors", "geo.col_views",
                                    "geo.col_avg", "geo.col_last"],
                  countries, "geo.no_guests") + "</div>" +
            '<div class="geo-col">' + '<h4>' + esc(t("geo.sources")) + "</h4>" +
            table("geo-sources", ["geo.col_source", "geo.col_kind",
                                  "geo.col_visitors", "geo.col_views"],
                  sources, "geo.no_guests") + "</div>" +
            "</div>" +
            "<h4>" + esc(t("geo.live")) + " · " + num((data.totals || {}).online || 0) +
            "</h4>" + table("geo-live", ["geo.col_guest", "geo.col_country",
                                         "geo.col_path", "geo.col_source",
                                         "geo.col_time"], live, "geo.no_online") +
            '<div class="geo-cols">' +
            '<div class="geo-col">' + "<h4>" + esc(t("geo.long_visits")) + "</h4>" +
            table("geo-long", ["geo.col_guest", "geo.col_country", "geo.col_time",
                               "geo.col_path", "geo.col_source"], long,
                  "geo.no_long") + "</div>" +
            '<div class="geo-col">' + "<h4>" + esc(t("geo.paths")) + "</h4>" +
            table("geo-paths", ["geo.col_path", "geo.col_views"], paths,
                  "geo.empty") + "</div>" +
            "</div>";
    }

    function load() {
        if (!panel) return;
        if (global.document && global.document.hidden) return;
        var my = ++req;
        try {
            global.fetch("/api/admin/geo?period=" + encodeURIComponent(period), {
                credentials: "same-origin",
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (my !== req || !data || !data.ok) return;
                render(data);
            }).catch(function () { /* нет связи — оставим прошлую картину */ });
        } catch (e) { /* старый браузер — просто нет карточки */ }
    }

    function periodButtons(data) {
        var list = (data && data.periods) || ["24h", "7d", "30d"];
        return list.map(function (key) {
            return '<button type="button" class="geo-p' +
                (key === period ? " on" : "") + '" data-period="' + esc(key) + '" ' +
                'data-i18n="geo.p' + esc(key) + '">' + esc(t("geo.p" + key)) +
                "</button>";
        }).join("");
    }

    function shell() {
        return '<div class="geo-head"><div class="geo-periods" id="geo-periods">' +
            periodButtons() + "</div>" +
            '<p class="geo-lead" data-i18n="geo.lead">' + esc(t("geo.lead")) +
            "</p></div>" + '<div id="geo-body" class="geo-body"></div>';
    }

    function onClick(event) {
        var btn = event.target && event.target.closest
            ? event.target.closest("[data-period]") : null;
        if (!btn) return;
        period = btn.getAttribute("data-period") || "24h";
        var box = global.document.getElementById("geo-periods");
        if (box) {
            Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
                b.classList.toggle("on", b === btn);
            });
        }
        load();
    }

    function mount(root) {
        panel = root || global.document.getElementById("geo-panel");
        if (!panel) return null;
        panel.innerHTML = shell();
        panel.addEventListener("click", onClick);
        load();
        if (!timer) {
            timer = global.setInterval(load, REFRESH_MS);
            if (global.document && global.document.addEventListener) {
                global.document.addEventListener("visibilitychange", function () {
                    if (!global.document.hidden) load();
                });
            }
            // смена языка перерисовывает таблицы: подписи берутся ключами
            if (I18n.onChange) I18n.onChange(function () { render(last || {}); });
        }
        return panel;
    }

    global.LiqScopeGeo = { mount: mount, load: load, render: render,
                           get period() { return period; }, fmtSec: fmtSec,
                           displayName: displayName, REFRESH_MS: REFRESH_MS };
})(window);
