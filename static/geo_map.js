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
 * Карта вписывается в карточку целиком (``viewBox`` показывает полосу без
 * Антарктиды), но её можно приблизить и тащить: на телефоне карта шириной в
 * пол-экрана мелкая, а на большом мониторе хочется разглядеть страну. Тянуть —
 * мышью или пальцем, приближать — колесом, кнопками ＋/− или клавишами со
 * стрелками, вернуться к целой карте — кнопкой ⤢ или клавишей 0. Масштаб и
 * сдвиг живут между перерисовками: карточка обновляется сама раз в 20 секунд,
 * и вид при этом не должен сбрасываться.
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
    var MAX_K = 8;                  // предел приближения: 1× — вся карта, 8× — страна
    var WHEEL_STEP = 1.25;          // один щелчок колеса
    var BUTTON_STEP = 1.5;          // одно нажатие кнопки ＋ / −

    //: Текущий вид карты: масштаб и сдвиг в координатах полотна. Живёт вне
    //: разметки: карточка перерисовывается сама, и вид не должен «прыгать».
    var view = { k: 1, x: 0, y: 0 };

    var I18n = global.LiqScopeI18n || { t: function (k) { return k; },
                                        number: function (n) { return String(n); },
                                        plural: function (n, f) { return f[2] || f[0]; },
                                        onChange: function () {} };

    var panel = null;
    var period = "24h";
    var timer = null;
    var last = null;
    var req = 0;
    var onlineSec = 300;            // окно «онлайн»: столько оставляем точкам при уборке
    var askTimer = null;            // переспрос кнопки «убрать старые точки»
    var dotsBusy = false;           // запрос уборки уже в пути

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
     * серверу), а ``Intl.DisplayNames`` даёт «Финляндия»/「芬兰」/「Finlandia»
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

    /** Контуры стран: рисуем один раз, дальше только точки.
     *
     * ``vector-effect`` держит береговую линию волоском на любом масштабе:
     * без него на 8× контуры превращаются в толстые ленты.
     */
    function landPaths() {
        var world = global.LIQSCOPE_WORLD;
        if (!world || !world.countries) return "";
        return world.countries.map(function (row) {
            return '<path class="geo-land" d="' + row[1] +
                '" vector-effect="non-scaling-stroke"></path>';
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
                '" cy="' + y(c.lat).toFixed(1) + '" r="' + r.toFixed(1) +
                '" data-r="' + r.toFixed(1) + '" data-sw="0.6">' +
                "<title>" + esc(title) + "</title></circle>";
        }).join("");

        var dots = (data.points || []).filter(function (p) {
            return p.lat !== null && p.lon !== null && !p.online;
        }).map(function (p) {
            return '<circle class="geo-dot" cx="' + x(p.lon).toFixed(1) +
                '" cy="' + y(p.lat).toFixed(1) + '" r="1.8" data-r="1.8"><title>' +
                esc(pointTitle(p)) + "</title></circle>";
        }).join("");

        // живые: пульсирующий ореол + точка; на карте видно «дышит»
        var live = (data.online || []).filter(function (p) {
            return p.lat !== null && p.lon !== null;
        }).map(function (p) {
            var cx = x(p.lon).toFixed(1), cy = y(p.lat).toFixed(1);
            return '<g class="geo-live-dot"><circle class="geo-pulse" cx="' + cx +
                '" cy="' + cy + '" r="9" data-r="9"></circle><circle class="geo-on" cx="' +
                cx + '" cy="' + cy + '" r="3.2" data-r="3.2" data-sw="0.8"><title>' +
                esc(pointTitle(p)) + "</title></circle></g>";
        }).join("");

        var empty = (!bubbles && !dots && !live)
            ? '<text class="geo-map-empty" x="500" y="200" text-anchor="middle">' +
              esc(t("geo.map_empty")) + "</text>"
            : "";
        // viewBox — четыре числа: левый верхний угол и размер видимой полосы
        // (Антарктида снизу не нужна). Всё содержимое — внутри .geo-viewport:
        // его transform и есть перетаскивание с масштабом.
        return '<svg class="geo-map" id="geo-map" viewBox="0 ' + VIEW_TOP + " " +
            MAP_W + " " + VIEW_H + '" role="img" tabindex="0" ' +
            'aria-label="' + esc(t("geo.map_alt")) + '">' +
            '<g class="geo-viewport">' +
            '<g class="geo-lands">' + landPaths() + "</g>" +
            '<g class="geo-bubbles">' + bubbles + "</g>" +
            '<g class="geo-dots">' + dots + "</g>" +
            '<g class="geo-live">' + live + "</g>" +
            "</g>" + empty + "</svg>";
    }

    /** Кнопки карты: приблизить, отдалить, вписать в окно. */
    function mapTools() {
        var defs = [["in", "＋", "geo.zoom_in"], ["out", "−", "geo.zoom_out"],
                    ["fit", "⤢", "geo.zoom_fit"]];
        return '<div class="geo-map-tools" id="geo-map-tools">' + defs.map(function (d) {
            return '<button type="button" class="geo-zoom" data-zoom="' + d[0] +
                '" title="' + esc(t(d[2])) + '" aria-label="' + esc(t(d[2])) +
                '">' + d[1] + "</button>";
        }).join("") + "</div>";
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
                    esc(t("geo.legend_bubble")) + "</span>",
                    '<span class="geo-key geo-note">' + esc(t("geo.map_hint")) +
                    "</span>"];
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

    /** Сколько пробного доступа к слоям осталось гостю и кнопка «дать ещё».
     *
     * Пробник считается только у гостей без регистрации: вошедшему в кабинет
     * слои доступны без ограничений, поэтому у него в колонке прочерк.
     * Сброс — не «бессрочно», а «заново»: таймер начинается с текущей минуты.
     */
    function trialCell(p) {
        var tr = p.trial;
        if (!tr) return '<td class="geo-num geo-trial">—</td>';
        var left = tr.expired ? t("geo.trial_over") : fmtSec(tr.left_sec);
        return '<td class="geo-num geo-trial"><span class="' +
            (tr.expired ? "geo-hot" : "") + '">' + esc(left) + "</span>" +
            '<button type="button" class="geo-mini" data-trial="' + esc(tr.who || "") +
            '" title="' + esc(t("geo.trial_more", { min: tr.minutes || 0 })) +
            '" aria-label="' + esc(t("geo.trial_more", { min: tr.minutes || 0 })) +
            '">↺</button></td>';
    }

    /** Тело карточки: шапка с кнопками периода остаётся, меняется только оно. */
    function body() {
        var box = panel && panel.querySelector("#geo-body");
        return box || panel;
    }

    function render(data) {
        last = data;
        if (!panel) return;
        onlineSec = Number(data.online_sec || onlineSec) || onlineSec;
        paintDots(data);
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
                '</td><td class="geo-num">' + fmtSec(p.sec) + "</td>" +
                trialCell(p) + "</tr>";
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
            '<div class="geo-map-wrap">' + mapTools() + mapSvg(data) +
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
                                         "geo.col_time", "geo.col_trial"],
                            live, "geo.no_online") +
            '<div class="geo-cols">' +
            '<div class="geo-col">' + "<h4>" + esc(t("geo.long_visits")) + "</h4>" +
            table("geo-long", ["geo.col_guest", "geo.col_country", "geo.col_time",
                               "geo.col_path", "geo.col_source"], long,
                  "geo.no_long") + "</div>" +
            '<div class="geo-col">' + "<h4>" + esc(t("geo.paths")) + "</h4>" +
            table("geo-paths", ["geo.col_path", "geo.col_views"], paths,
                  "geo.empty") + "</div>" +
            "</div>";

        bindMap();      // разметка новая — обработчики карты тоже
    }

    // ----- вид карты: вписать, приблизить, подвинуть ----------------------
    function svgEl() {
        return (panel && panel.querySelector("#geo-map")) || null;
    }

    function viewportEl() {
        return (panel && panel.querySelector("#geo-map .geo-viewport")) || null;
    }

    /** Держим карту в кадре: при масштабе 1 видно весь мир, а при
     * приближении карту нельзя утащить за край окна (как в картах). */
    function clampView() {
        view.k = Math.max(1, Math.min(MAX_K, Number(view.k) || 1));
        var minX = MAP_W * (1 - view.k);
        var minY = (VIEW_TOP + VIEW_H) * (1 - view.k);
        var maxY = VIEW_TOP * (1 - view.k);
        view.x = Math.min(0, Math.max(minX, view.x));
        view.y = Math.min(maxY, Math.max(minY, view.y));
    }

    /** Значки не растут вместе с картой: иначе на 8× точка-гость становится
     * размером со страну и закрывает то, что под ней. */
    function scaleMarks() {
        var svg = svgEl();
        if (!svg) return;
        var f = 1 / view.k;
        Array.prototype.forEach.call(svg.querySelectorAll("[data-r]"), function (el) {
            el.setAttribute("r", (Number(el.getAttribute("data-r")) * f).toFixed(2));
        });
        // обводку ставим стилем: правило из таблицы стилей сильнее атрибута
        Array.prototype.forEach.call(svg.querySelectorAll("[data-sw]"), function (el) {
            el.style.setProperty("stroke-width",
                (Number(el.getAttribute("data-sw")) * f).toFixed(2));
        });
    }

    function applyView() {
        clampView();
        var g = viewportEl();
        if (g) {
            g.setAttribute("transform", "translate(" + view.x.toFixed(2) + " " +
                view.y.toFixed(2) + ") scale(" + view.k.toFixed(3) + ")");
        }
        var svg = svgEl();
        if (svg) {
            // На масштабе 1 карта и так видна целиком — не отбираем у страницы
            // прокрутку пальцем. Приблизил — жесты уходят карте.
            svg.style.touchAction = view.k > 1 ? "none" : "pan-y";
        }
        scaleMarks();
        var tools = panel && panel.querySelector("#geo-map-tools");
        if (tools) {
            var out = tools.querySelector('[data-zoom="out"]');
            var fitBtn = tools.querySelector('[data-zoom="fit"]');
            var whole = view.k <= 1.001 && !view.x && !view.y;
            if (out) out.disabled = view.k <= 1.001;
            if (fitBtn) fitBtn.disabled = whole;
        }
    }

    function fit() { view.k = 1; view.x = 0; view.y = 0; applyView(); }

    /** Точка экрана → координаты полотна карты (viewBox). */
    function viewBoxPoint(svg, clientX, clientY) {
        var px = Number(clientX) || 0, py = Number(clientY) || 0;
        if (svg.createSVGPoint && svg.getScreenCTM) {
            try {
                var pt = svg.createSVGPoint();
                pt.x = px;
                pt.y = py;
                var m = svg.getScreenCTM();
                if (m && m.inverse) return pt.matrixTransform(m.inverse());
            } catch (e) { /* считаем по рамке ниже */ }
        }
        var box = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
        var w = (box && box.width) || MAP_W, h = (box && box.height) || VIEW_H;
        return { x: (px - (box ? box.left : 0)) * (MAP_W / w),
                 y: VIEW_TOP + (py - (box ? box.top : 0)) * (VIEW_H / h) };
    }

    /** Приблизить/отдалить так, чтобы точка под курсором осталась на месте. */
    function zoomAt(px, py, factor) {
        var k0 = view.k;
        var k = Math.max(1, Math.min(MAX_K, k0 * factor));
        if (Math.abs(k - k0) < 1e-6) { applyView(); return; }
        // (p − t)/k — координата точки на полотне: она и должна сохраниться
        var ux = (px - view.x) / k0, uy = (py - view.y) / k0;
        view.k = k;
        view.x = px - ux * k;
        view.y = py - uy * k;
        applyView();
    }

    function zoomCenter(factor) {
        var p = { x: MAP_W / 2, y: VIEW_TOP + VIEW_H / 2 };
        zoomAt(p.x, p.y, factor);
    }

    function onWheel(ev) {
        var svg = svgEl();
        if (!svg) return;
        if (ev.preventDefault) ev.preventDefault();
        var p = viewBoxPoint(svg, ev.clientX, ev.clientY);
        zoomAt(p.x, p.y, Number(ev.deltaY) < 0 ? WHEEL_STEP : 1 / WHEEL_STEP);
    }

    //: Перетаскивание: запоминаем, где нажали, и сдвигаем карту от этой точки.
    //: Клик по кнопкам карты и выделение текста не должны её двигать.
    var drag = null;
    var winBound = false;

    function onDragStart(ev) {
        var svg = svgEl();
        if (!svg || drag) return;
        if (ev.button !== undefined && ev.button !== null && ev.button !== 0) return;
        if (ev.target && ev.target.closest && ev.target.closest("button")) return;
        drag = { sx: Number(ev.clientX) || 0, sy: Number(ev.clientY) || 0,
                 x0: view.x, y0: view.y };
        svg.classList.add("geo-grabbing");
        if (ev.pointerId !== undefined && svg.setPointerCapture) {
            try { svg.setPointerCapture(ev.pointerId); } catch (e) { /* не беда */ }
        }
        if (ev.preventDefault && ev.cancelable !== false) ev.preventDefault();
    }

    function onDragMove(ev) {
        var svg = svgEl();
        if (!drag || !svg) return;
        // пиксели тоже переводим в координаты полотна: на телефоне и на большом
        // мониторе один и тот же жест двигает карту одинаково
        var box = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
        var w = (box && box.width) || MAP_W, h = (box && box.height) || VIEW_H;
        view.x = drag.x0 + (Number(ev.clientX) - drag.sx) * (MAP_W / w);
        view.y = drag.y0 + (Number(ev.clientY) - drag.sy) * (VIEW_H / h);
        applyView();
    }

    function onDragEnd() {
        if (!drag) return;
        drag = null;
        var svg = svgEl();
        if (svg) svg.classList.remove("geo-grabbing");
    }

    function onKey(ev) {
        var key = ev.key || "";
        var step = 40 / view.k;          // стрелка — ~10% видимой части карты
        if (key === "ArrowLeft") view.x += step * 2.5;
        else if (key === "ArrowRight") view.x -= step * 2.5;
        else if (key === "ArrowUp") view.y += step;
        else if (key === "ArrowDown") view.y -= step;
        else if (key === "+" || key === "=" || key === "Add") zoomCenter(BUTTON_STEP);
        else if (key === "-" || key === "_" || key === "Subtract") zoomCenter(1 / BUTTON_STEP);
        else if (key === "0" || key === "Home") { fit(); ev.preventDefault(); return; }
        else return;
        ev.preventDefault();
        applyView();
    }

    function onToolClick(ev) {
        var btn = ev.target && ev.target.closest
            ? ev.target.closest("[data-zoom]") : null;
        if (!btn || btn.disabled) return;
        var kind = btn.getAttribute("data-zoom");
        if (kind === "in") zoomCenter(BUTTON_STEP);
        else if (kind === "out") zoomCenter(1 / BUTTON_STEP);
        else fit();
    }

    /** Вешаем обработчики на свежую разметку карты (её перерисовывает render).
     *
     * Слушатели окна — один раз за всю жизнь карточки: события мыши и касания
     * продолжают приходить, даже когда палец ушёл за пределы карты. */
    function bindWindow() {
        if (winBound || !global.addEventListener) return;
        winBound = true;
        ["pointermove", "mousemove"].forEach(function (name) {
            global.addEventListener(name, onDragMove);
        });
        ["pointerup", "pointercancel", "mouseup", "blur"].forEach(function (name) {
            global.addEventListener(name, onDragEnd);
        });
    }

    function bindMap() {
        var svg = svgEl();
        if (!svg) return;
        bindWindow();
        svg.addEventListener("pointerdown", onDragStart);
        svg.addEventListener("mousedown", onDragStart);
        svg.addEventListener("wheel", onWheel, { passive: false });
        svg.addEventListener("keydown", onKey);
        svg.addEventListener("dblclick", function (ev) {
            if (ev.preventDefault) ev.preventDefault();
            var p = viewBoxPoint(svg, ev.clientX, ev.clientY);
            zoomAt(p.x, p.y, BUTTON_STEP);
        });
        var tools = panel && panel.querySelector("#geo-map-tools");
        if (tools) tools.addEventListener("click", onToolClick);
        applyView();
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

    /** Кнопка уборки старых точек и подпись к ней.
     *
     * Точки копятся за весь период: за 30 дней карта превращается в кашу, и
     * по ней уже не видно, кто приходит сейчас. Уборка ставит отметку времени
     * (её хранит сервер, ``geo_dots_after``): карта показывает только гостей
     * после неё. Статистика, страны и таблицы остаются целыми, поэтому уборка
     * обратима — та же кнопка возвращает все точки назад.
     */
    function dotsBar(data) {
        return '<div class="geo-dots-row">' +
            '<button type="button" class="btn btn-small" id="geo-dots" ' +
            'data-i18n="geo.dots_clear">' + esc(t("geo.dots_clear")) + "</button>" +
            '<span class="meta" id="geo-dots-note" role="status"></span></div>';
    }

    function paintDots(data) {
        var btn = panel && panel.querySelector("#geo-dots");
        var note = panel && panel.querySelector("#geo-dots-note");
        if (!btn) return;
        var after = Number((data || {}).dots_after || 0);
        var hidden = Number((data || {}).dots_hidden || 0);
        btn.setAttribute("data-dots", after ? "restore" : "clear");
        btn.setAttribute("data-i18n", after ? "geo.dots_restore" : "geo.dots_clear");
        if (!askTimer) btn.textContent = after ? t("geo.dots_restore") : t("geo.dots_clear");
        if (note) {
            note.textContent = after
                ? t("geo.dots_note", { n: num(hidden), time: fmtWhen(after) })
                : "";
        }
    }

    /** Запрос уборки точек: ``clear`` — спрятать старые, ``restore`` — вернуть. */
    function dotsRequest(act, btn) {
        if (dotsBusy) return;
        dotsBusy = true;
        var note = panel && panel.querySelector("#geo-dots-note");
        if (note) note.textContent = t("cab.loading");
        var url = act === "restore" ? "/api/admin/geo/dots/reset"
                                    : "/api/admin/geo/dots/clear";
        var body = act === "restore"
            ? "{}" : JSON.stringify({ keep_sec: onlineSec });
        try {
            global.fetch(url, {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" }, body: body,
            }).then(function (r) { return r.json(); }).then(function (d) {
                dotsBusy = false;
                if (btn) btn.classList.remove("ask");
                if (!d || !d.ok) {
                    if (note) note.textContent = t("geo.dots_fail");
                    return;
                }
                load();
            }).catch(function () {
                dotsBusy = false;
                if (note) note.textContent = t("geo.dots_fail");
            });
        } catch (e) {
            dotsBusy = false;
            if (note) note.textContent = t("geo.dots_fail");
        }
    }

    /** Первый клик только переспрашивает: кнопка рядом с картой, промахнуться легко. */
    function onDots(btn) {
        var act = btn.getAttribute("data-dots") || "clear";
        if (act === "restore") {
            dotsRequest("restore", btn);
            return;
        }
        if (btn.classList.contains("ask")) {
            if (askTimer) { global.clearTimeout(askTimer); askTimer = null; }
            btn.classList.remove("ask");
            dotsRequest("clear", btn);
            return;
        }
        btn.classList.add("ask");
        btn.textContent = t("geo.dots_confirm");
        if (askTimer) global.clearTimeout(askTimer);
        askTimer = global.setTimeout(function () {
            askTimer = null;
            if (btn.isConnected === false) return;
            btn.classList.remove("ask");
            btn.textContent = t("geo.dots_clear");
        }, 6000);
    }

    /** Дать гостю пробный доступ заново: сброс таймера слоёв (web_layers). */
    function resetTrial(who, btn) {
        if (!who) return;
        if (btn) btn.disabled = true;
        try {
            global.fetch("/api/admin/layers/reset", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ who: who }),
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (btn) btn.disabled = false;
                if (!d || !d.ok) return;
                load();
            }).catch(function () { if (btn) btn.disabled = false; });
        } catch (e) { if (btn) btn.disabled = false; }
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
            dotsBar() +
            '<p class="geo-lead" data-i18n="geo.lead">' + esc(t("geo.lead")) +
            "</p></div>" + '<div id="geo-body" class="geo-body"></div>';
    }

    function onClick(event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var trial = target.closest("[data-trial]");
        if (trial) {
            resetTrial(trial.getAttribute("data-trial") || "", trial);
            return;
        }
        var dots = target.closest("[data-dots]");
        if (dots) {
            onDots(dots);
            return;
        }
        var btn = target.closest("[data-period]");
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
                           displayName: displayName, REFRESH_MS: REFRESH_MS,
                           MAX_K: MAX_K, view: view, fit: fit,
                           element: svgEl };
})(window);
