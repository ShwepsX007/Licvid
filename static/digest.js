/**
 * LiqScope — страница «Дневной дайджест».
 *
 * Слева свежие выпуски и календарь по датам, справа выпуск целиком. Тексты
 * приходят с сервера сразу в двух языках: показываем русский, если язык сайта
 * русский, иначе английский (остальные языки интерфейса читают английский).
 *
 * Архив не копится списком: в ленте видны только свежие выпуски, любой старый
 * открывается по дате. Переходы отсюда — в кабинет, в админку (если вошёл
 * админ) и в терминал, чтобы не искать их через логотип и главную.
 */
(function () {
    "use strict";

    var FRESH = 7;                  // сколько свежих выпусков показываем списком

    var I18n = window.LiqScopeI18n;
    function t(key, vars) { return I18n && I18n.t ? I18n.t(key, vars) : key; }
    function el(id) { return document.getElementById(id); }
    function lang() { return (I18n && I18n.lang && I18n.lang()) || "ru"; }
    function articleLang() { return lang() === "ru" ? "ru" : "en"; }
    function localeTag() {
        return (I18n && I18n.localeTag && I18n.localeTag()) || "ru-RU";
    }

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function money(v) {
        var n = Number(v || 0), sign = n < 0 ? "−" : "";
        n = Math.abs(n);
        if (n >= 1e9) return sign + "$" + (n / 1e9).toFixed(2) + "B";
        if (n >= 1e6) return sign + "$" + (n / 1e6).toFixed(2) + "M";
        if (n >= 1e3) return sign + "$" + (n / 1e3).toFixed(1) + "K";
        return sign + "$" + n.toFixed(0);
    }

    function api(url) {
        return fetch(url, { credentials: "same-origin" }).then(function (r) {
            return r.json().catch(function () { return {}; });
        });
    }

    var items = [];                 // свежие выпуски (полные записи)
    var days = [];                  // все даты архива (лёгкий индекс)
    var dayMap = {};                // дата -> запись индекса
    var total = 0;                  // сколько выпусков в архиве
    var selected = "";
    var month = null;               // {y, m} — открытый месяц календаря

    /* ---------- свежие выпуски ---------- */

    function paintList() {
        var box = el("dig-list");
        var more = el("dig-more");
        if (!box) return;
        if (!items.length) {
            box.innerHTML = '<div class="dig-empty">' + esc(t("dig.empty")) + "</div>";
            if (more) more.textContent = "";
            return;
        }
        box.innerHTML = items.slice(0, FRESH).map(function (it) {
            var tags = "";
            if (it.published) tags += '<span class="tag ok">' + esc(t("dig.published")) + "</span>";
            if (it.mood) tags += '<span class="tag">' + esc(it.mood) + "</span>";
            return '<button type="button" class="dig-card' +
                (String(it.day) === String(selected) ? " active" : "") +
                '" data-day="' + esc(it.day) + '">' +
                '<div class="dc-day">' + esc(it.day_label || it.day) + "</div>" +
                '<div class="dc-brief">' + esc(it.brief || "") + "</div>" +
                (tags ? '<div class="dc-tags">' + tags + "</div>" : "") +
                "</button>";
        }).join("");
        Array.prototype.forEach.call(box.querySelectorAll(".dig-card"), function (btn) {
            btn.addEventListener("click", function () { open(btn.getAttribute("data-day")); });
        });
        if (more) {
            more.textContent = total > FRESH
                ? t("dig.more", { shown: FRESH, n: total }) : "";
        }
    }

    function paintChips(item) {
        var latest = el("dig-latest"), tot = el("dig-total");
        if (latest) {
            latest.textContent = item
                ? (item.day_label || item.day) + (item.window_h ? " · " + item.window_h + "ч" : "")
                : "—";
        }
        if (tot) tot.textContent = item ? money(item.total_usd) : "—";
    }

    function paintArticle(item) {
        var box = el("dig-article");
        if (!box) return;
        if (!item) {
            box.innerHTML = '<div class="dig-empty">' + esc(t("dig.empty")) + "</div>";
            return;
        }
        var tags = "";
        if (item.published) tags += '<span class="tag ok">' + esc(t("dig.published")) + "</span>";
        else tags += '<span class="tag">' + esc(t("dig.draft")) + "</span>";
        var post = item.post
            ? '<details class="dig-post"><summary>Telegram · ' + esc(item.day_label || item.day) +
              "</summary><pre>" + esc(item.post) + "</pre></details>"
            : "";
        box.innerHTML =
            "<h2>" + esc(item.day_label || item.day) + "</h2>" +
            '<div class="art-meta">' + esc(item.brief || "") + " " + tags + "</div>" +
            (item.article || "") + post;
    }

    /* ---------- календарь по датам ---------- */

    function pad(n) { return (n < 10 ? "0" : "") + n; }

    function monthOfDay(day) {
        var p = String(day || "").split("-");
        return { y: Number(p[0]) || 0, m: (Number(p[1]) || 1) - 1 };
    }

    function monthKey(c) { return c.y * 12 + c.m; }

    function clampMonth(c) {
        if (!days.length) return c;
        var lo = monthOfDay(days[days.length - 1].day);
        var hi = monthOfDay(days[0].day);
        var k = monthKey(c);
        if (k < monthKey(lo)) return lo;
        if (k > monthKey(hi)) return hi;
        return c;
    }

    function todayKey() {
        var d = new Date();
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    function paintCalendar() {
        var grid = el("cal-grid");
        var week = el("cal-week");
        var title = el("cal-title");
        if (!grid || !title || !month) return;
        var legend = el("cal-legend");
        if (legend) {
            legend.textContent = (t("dig.has") + " · " +
                t("dig.in_archive", { n: total }));
        }
        try {
            title.textContent = new Intl.DateTimeFormat(localeTag(),
                { month: "long", year: "numeric" })
                .format(new Date(month.y, month.m, 1));
        } catch (e) {
            title.textContent = month.y + "-" + pad(month.m + 1);
        }
        if (week) {
            /* названия дней недели берём у браузера: 1 января 2024 — понедельник */
            var out = "";
            for (var i = 0; i < 7; i++) {
                var d = new Date(2024, 0, 1 + i);
                var name;
                try {
                    name = new Intl.DateTimeFormat(localeTag(), { weekday: "short" }).format(d);
                } catch (e2) { name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][i]; }
                out += "<span>" + esc(name) + "</span>";
            }
            week.innerHTML = out;
        }
        var first = new Date(month.y, month.m, 1);
        var lead = (first.getDay() + 6) % 7;              // неделя с понедельника
        var count = new Date(month.y, month.m + 1, 0).getDate();
        var cells = "";
        for (var b = 0; b < lead; b++) cells += '<span class="cal-day empty"></span>';
        for (var day = 1; day <= count; day++) {
            var key = month.y + "-" + pad(month.m + 1) + "-" + pad(day);
            var cls = "cal-day";
            if (dayMap[key]) cls += " has";
            else cls += " empty";
            if (key === selected) cls += " on";
            if (key === todayKey()) cls += " today";
            var label = dayMap[key]
                ? (dayMap[key].label || key) +
                  (dayMap[key].total_usd ? " · " + money(dayMap[key].total_usd) : "")
                : key;
            cells += dayMap[key]
                ? '<button type="button" class="' + cls + '" data-day="' + esc(key) +
                  '" title="' + esc(label) + '">' + day + "</button>"
                : '<span class="' + cls + '">' + day + "</span>";
        }
        grid.innerHTML = cells;
        Array.prototype.forEach.call(grid.querySelectorAll(".cal-day.has"), function (btn) {
            btn.addEventListener("click", function () { open(btn.getAttribute("data-day")); });
        });
        var prev = el("cal-prev"), next = el("cal-next");
        var lo = monthOfDay(days[days.length - 1].day), hi = monthOfDay(days[0].day);
        if (prev) prev.disabled = monthKey(month) <= monthKey(lo);
        if (next) next.disabled = monthKey(month) >= monthKey(hi);
    }

    function shiftMonth(delta) {
        if (!month) return;
        month = clampMonth({ y: month.y, m: month.m + delta });
        if (month.m < 0) { month.y -= 1; month.m = 11; }
        if (month.m > 11) { month.y += 1; month.m = 0; }
        month = clampMonth(month);
        paintCalendar();
    }

    /* ---------- переходы ---------- */

    function navBtn(href, label, cls) {
        return '<a class="btn ' + (cls || "btn-ghost") + ' btn-compact" href="' + href +
            '">' + esc(label) + "</a>";
    }

    function paintNav(user) {
        var box = el("nav-account");
        var jump = el("dig-jump");
        var admin = !!(user && user.is_admin);
        var name = user ? (user.display_name || user.username || t("dig.cabinet")) : "";
        if (box) {
            box.innerHTML = user
                ? navBtn("/cabinet", "👤 " + name) +
                  (admin ? navBtn("/admin", "🛠 " + t("dig.admin")) : "")
                : navBtn("/login", "🔑 " + t("dig.login"));
        }
        if (!jump) return;
        var rows = [];
        rows.push(user ? navBtn("/cabinet", "👤 " + t("dig.cabinet"))
                       : navBtn("/login", "🔑 " + t("dig.login")));
        if (admin) rows.push(navBtn("/admin", "🛠 " + t("dig.admin")));
        rows.push(navBtn("/terminal", "⚡ " + t("dig.back")));
        jump.innerHTML = rows.join("");
    }

    /* ---------- загрузка ---------- */

    function setUrl(day) {
        try {
            var url = "/digest" + (day ? "?day=" + encodeURIComponent(day) : "");
            window.history.replaceState(null, "", url);
        } catch (e) { /* ignore */ }
    }

    function open(day, push) {
        selected = day || "";
        paintList();
        if (dayMap[selected]) month = clampMonth(monthOfDay(selected));
        paintCalendar();
        return api("/api/digest/" + encodeURIComponent(day) + "?lang=" + articleLang())
            .then(function (res) {
                if (!res || !res.ok || !res.item) { paintArticle(null); return; }
                paintArticle(res.item);
                paintChips(res.item);
                if (push !== false) setUrl(day);
            })
            .catch(function () { paintArticle(null); });
    }

    function loadArchive(day) {
        return api("/api/digest?lang=" + articleLang()).then(function (res) {
            items = (res && res.items) || [];
            days = (res && res.days) || [];
            total = Number(res && res.count) || days.length;
            dayMap = {};
            days.forEach(function (d) { dayMap[String(d.day)] = d; });
            if (!items.length && !days.length) {
                paintList();
                paintCalendar();
                paintArticle(null);
                paintChips(null);
                return;
            }
            var known = function (d) { return !!dayMap[String(d)]; };
            var wanted = day && known(day) ? day
                : (items[0] && items[0].day) || (days[0] && days[0].day) || "";
            month = clampMonth(monthOfDay(wanted));
            return open(wanted, false).then(function () { setUrl(wanted); });
        }).catch(function () {
            items = [];
            days = [];
            paintList();
            paintArticle(null);
        });
    }

    function boot() {
        if (I18n && I18n.init) I18n.init();
        var sel = el("lang-select");
        if (sel) {
            sel.value = lang();
            sel.addEventListener("change", function (e) {
                if (I18n && I18n.set) I18n.set(e.target.value);
                loadArchive(selected);
            });
        }
        var prev = el("cal-prev"), next = el("cal-next");
        if (prev) prev.addEventListener("click", function () { shiftMonth(-1); });
        if (next) next.addEventListener("click", function () { shiftMonth(1); });
        var params = new URLSearchParams(window.location.search);
        var day = params.get("day") || "";
        if (params.get("lang") && I18n && I18n.set) I18n.set(params.get("lang"));
        if (I18n && I18n.onChange) I18n.onChange(function () { loadArchive(selected); });
        // кто вошёл — тому ссылки в кабинет и (если админ) в админку
        api("/api/auth/me").then(function (res) {
            paintNav(res && res.ok ? res.user : null);
        }).catch(function () { paintNav(null); });
        loadArchive(day);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
