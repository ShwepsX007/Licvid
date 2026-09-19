/**
 * LiqScope — страница «Сводки по часам».
 *
 * Показывает посты канала так, как они вышли в Telegram: то же фото и та же
 * подпись (разметка канала уже переведена сервером в безопасный HTML).
 * Слева — свежие сводки и календарь по дням, справа — все посты выбранного
 * дня. Тексты приходят в двух языках: русский показываем на русском сайте,
 * остальным языкам интерфейса отдаём английскую версию (посты раздела выходят
 * в английском канале), внутри поста можно переключить язык вручную.
 *
 * Время поста считает браузер — в поясе посетителя и на языке страницы, а дни
 * и календарь идут по поясу выпусков (МСК): так же, как в архиве дайджеста.
 */
(function () {
    "use strict";

    var FRESH = 8;                  // сколько свежих постов показываем списком

    var I18n = window.LiqScopeI18n;
    function t(key, vars) { return I18n && I18n.t ? I18n.t(key, vars) : key; }
    function el(id) { return document.getElementById(id); }
    function lang() { return (I18n && I18n.lang && I18n.lang()) || "ru"; }
    function postLang() { return lang() === "ru" ? "ru" : "en"; }
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

    /* ---------- время и даты: всегда в поясе посетителя ---------- */

    function timeLabel(ts) {
        try {
            return new Intl.DateTimeFormat(localeTag(),
                { hour: "2-digit", minute: "2-digit" }).format(new Date(ts * 1000));
        } catch (e) {
            return "";
        }
    }

    function dayLabel(day) {
        var p = String(day || "").split("-");
        if (p.length !== 3) return String(day || "");
        try {
            return new Intl.DateTimeFormat(localeTag(),
                { day: "numeric", month: "long", year: "numeric" })
                .format(new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])));
        } catch (e) {
            return String(day || "");
        }
    }

    function pad(n) { return (n < 10 ? "0" : "") + n; }

    function todayKey() {
        var d = new Date();
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    /* ---------- состояние ---------- */

    var items = [];                 // посты того, что показано справа
    var days = [];                  // индекс дней для календаря
    var dayMap = {};
    var total = 0;
    var selected = "";
    var picked = {};                // id поста → язык, выбранный вручную
    var month = null;
    var jumpTo = "";
    var tzHours = 3;
    var freshShown = FRESH;

    /* ---------- свежие сводки ---------- */

    function paintFresh() {
        var box = el("hour-list");
        var more = el("hour-more");
        if (!box) return;
        var list = freshItems;
        if (!list.length) {
            box.innerHTML = '<div class="hour-empty">' + esc(t("hour.empty")) + "</div>";
            if (more) more.textContent = "";
            return;
        }
        box.innerHTML = list.slice(0, freshShown).map(function (it) {
            var sum = [];
            if (it.total_usd) sum.push(money(it.total_usd));
            if (it.liq_count) sum.push(t("hour.events", { n: it.liq_count }));
            return '<button type="button" class="hour-card' +
                (it.day === selected ? " active" : "") + '" data-day="' + esc(it.day) +
                '" data-post="' + esc(it.id) + '">' +
                '<div class="hc-line">🕘 ' + esc(timeLabel(it.ts)) +
                (it.window_h ? ' <span class="hc-day">' + esc(t("hour.window", { h: it.window_h })) + "</span>" : "") +
                "</div>" +
                '<div class="hc-day">' + esc(dayLabel(it.day)) + "</div>" +
                (sum.length ? '<div class="hc-sum">' + esc(sum.join(" · ")) + "</div>" : "") +
                "</button>";
        }).join("");
        Array.prototype.forEach.call(box.querySelectorAll(".hour-card"), function (btn) {
            btn.addEventListener("click", function () {
                jumpTo = btn.getAttribute("data-post") || "";
                open(btn.getAttribute("data-day"));
            });
        });
        if (more) {
            more.textContent = total > freshShown
                ? t("hour.more", { shown: Math.min(freshShown, list.length), n: total }) : "";
        }
    }

    /* ---------- посты дня ---------- */

    function postText(it) {
        var want = picked[it.id] || postLang();
        var texts = it.texts || {};
        if (texts[want]) return texts[want];
        return texts[postLang()] || texts.ru || texts.en || esc(it.text || "");
    }

    function paintPosts(res) {
        var box = el("hour-posts");
        if (!box) return;
        items = (res && res.items) || [];
        if (!items.length) {
            box.innerHTML = '<div class="hour-empty">' + esc(t("hour.empty_day")) + "</div>";
            return;
        }
        var head = '<div class="hour-day-head"><h2>' + esc(dayLabel(selected)) + "</h2>" +
            '<span class="hd-n">' + esc(t("hour.n_posts", { n: items.length })) + "</span></div>";
        box.innerHTML = head + items.map(function (it) {
            var langs = it.langs || [];
            var cur = picked[it.id] || postLang();
            var hasLangs = langs.indexOf("ru") !== -1 && langs.indexOf("en") !== -1;
            var toggle = hasLangs
                ? '<span class="tg-langs">' +
                  langs.map(function (code) {
                      return '<button type="button" data-post="' + esc(it.id) +
                          '" data-lang="' + code + '" class="' + (code === cur ? "on" : "") +
                          '">' + (code === "ru" ? "🇷🇺 RU" : "🇬🇧 EN") + "</button>";
                  }).join("") + "</span>"
                : "";
            var photo = it.photo && it.photo.url
                ? '<img class="tg-photo" src="' + esc(it.photo.url) + '" alt="' +
                  esc(t("hour.photo_alt")) + '" loading="lazy">'
                : "";
            var foot = [];
            if (it.total_usd) foot.push(money(it.total_usd));
            if (it.liq_count) foot.push(t("hour.events", { n: it.liq_count }));
            if (it.window_h) foot.push(t("hour.window", { h: it.window_h }));
            return '<article class="tg-card' + (jumpTo === it.id ? " hit" : "") +
                '" id="post-' + esc(it.id) + '">' +
                '<div class="tg-head"><span class="tg-time">🕘 ' + esc(timeLabel(it.ts)) +
                '</span><span class="tg-day">' + esc(dayLabel(it.day)) + "</span>" + toggle + "</div>" +
                photo + '<div class="tg-text">' + postText(it) + "</div>" +
                (foot.length ? '<div class="tg-foot">' + esc(foot.join(" · ")) +
                    '<span class="sep">·</span><a href="/hourly?post=' + esc(it.id) +
                    '">' + esc(t("hour.link")) + "</a></div>" : "") +
                "</article>";
        }).join("");
        Array.prototype.forEach.call(box.querySelectorAll(".tg-langs button"), function (btn) {
            btn.addEventListener("click", function () {
                picked[btn.getAttribute("data-post")] = btn.getAttribute("data-lang");
                paintPosts({ items: items });
            });
        });
    }

    /* ---------- календарь по дням ---------- */

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

    function paintCalendar() {
        var grid = el("cal-grid");
        var week = el("cal-week");
        var title = el("cal-title");
        if (!grid || !title || !month) return;
        var legend = el("cal-legend");
        if (legend) {
            legend.textContent = t("hour.has") + " · " + t("hour.in_archive", { n: total });
        }
        try {
            title.textContent = new Intl.DateTimeFormat(localeTag(),
                { month: "long", year: "numeric" }).format(new Date(month.y, month.m, 1));
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
            var row = dayMap[key];
            var cls = "cal-day" + (row ? " has" : " empty");
            if (key === selected) cls += " on";
            if (key === todayKey()) cls += " today";
            var hint = row
                ? dayLabel(key) + " · " + t("hour.n_posts", { n: row.n })
                : key;
            cells += row
                ? '<button type="button" class="' + cls + '" data-day="' + esc(key) +
                  '" title="' + esc(hint) + '">' + day + "</button>"
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

    /* ---------- шапка и переходы ---------- */

    function takeMeta(res) {
        /* цифры шапки приходят и со списком, и с днём: берём что есть */
        if (!res) return;
        var interval = Number(res.interval_h);
        if (interval > 0) state.interval = interval;
        var tz = Number(res.tz_hours);
        if (!isNaN(tz)) state.tz = tz;
        if (res.channel_url) state.channel = String(res.channel_url);
    }

    function paintChips() {
        var every = el("hour-every"), tot = el("hour-total"), daysEl = el("hour-days");
        var tz = el("hour-tz"), ch = el("hour-channel");
        tzHours = state.tz;
        if (every) every.textContent = "🕘 " + t("hour.every", { h: state.interval });
        if (tot) tot.textContent = String(total);
        if (daysEl) daysEl.textContent = String(days.length);
        if (tz) tz.textContent = t("hour.tz", { tz: tzLabel() });
        if (ch) {
            var url = state.channel;
            if (url) {
                ch.setAttribute("href", url);
                ch.classList.remove("hidden");
            } else {
                ch.classList.add("hidden");
            }
        }
    }

    function tzLabel() {
        var sign = tzHours < 0 ? "−" : "+";
        var abs = Math.abs(tzHours);
        var hours = Math.floor(abs);
        var minutes = Math.round((abs - hours) * 60);
        return "UTC" + sign + hours + (minutes ? ":" + pad(minutes) : "");
    }

    function navBtn(href, label, cls) {
        return '<a class="btn ' + (cls || "btn-ghost") + ' btn-compact" href="' + href +
            '">' + esc(label) + "</a>";
    }

    function paintNav(user) {
        var box = el("nav-account");
        var jump = el("hour-jump");
        var admin = !!(user && user.is_admin);
        var name = user ? (user.display_name || user.username || t("hour.cabinet")) : "";
        if (box) {
            box.innerHTML = user
                ? navBtn("/cabinet", "👤 " + name) +
                  (admin ? navBtn("/admin", "🛠 " + t("hour.admin")) : "")
                : navBtn("/login", "🔑 " + t("hour.login"));
        }
        if (!jump) return;
        var rows = [];
        rows.push(user ? navBtn("/cabinet", "👤 " + t("hour.cabinet"))
                       : navBtn("/login", "🔑 " + t("hour.login")));
        if (admin) rows.push(navBtn("/admin", "🛠 " + t("hour.admin")));
        rows.push(navBtn("/digest", "📰 " + t("hour.digest")));
        rows.push(navBtn("/terminal", "⚡ " + t("hour.to_terminal")));
        jump.innerHTML = rows.join("");
    }

    /* ---------- загрузка ---------- */

    function setUrl(day, postId) {
        try {
            var url = "/hourly";
            var q = [];
            if (day) q.push("day=" + encodeURIComponent(day));
            if (postId) q.push("post=" + encodeURIComponent(postId));
            if (q.length) url += "?" + q.join("&");
            window.history.replaceState(null, "", url);
        } catch (e) { /* ignore */ }
    }

    function open(day, push) {
        selected = day || "";
        if (!selected) selected = (days[0] && days[0].day) || "";
        if (dayMap[selected]) month = clampMonth(monthOfDay(selected));
        paintCalendar();
        return api("/api/hourly?lang=" + encodeURIComponent(postLang()) +
                   "&day=" + encodeURIComponent(selected))
            .then(function (res) {
                takeMeta(res);
                total = Number(res && res.count) || total;
                paintFresh();
                paintPosts(res);
                paintChips();
                if (push !== false) setUrl(selected, jumpTo);
                if (jumpTo) {
                    var card = el("post-" + jumpTo);
                    if (card && card.scrollIntoView) {
                        card.scrollIntoView({ block: "start" });
                    }
                    jumpTo = "";
                }
            })
            .catch(function () { paintPosts(null); });
    }

    var freshItems = [];            // свежие посты для левой колонки
    var state = { interval: 4, tz: 3, channel: "" };

    function loadArchive(day, post) {
        return api("/api/hourly?lang=" + encodeURIComponent(postLang()) +
                   "&limit=" + FRESH).then(function (res) {
            freshItems = (res && res.items) || [];
            days = (res && res.days) || [];
            total = Number(res && res.count) || 0;
            takeMeta(res);
            dayMap = {};
            days.forEach(function (d) { dayMap[String(d.day)] = d; });
            paintChips();
            paintFresh();
            if (!days.length) {
                paintCalendar();
                paintPosts(null);
                return;
            }
            // Какой день открыть: из ссылки (?post= или ?day=) или самый свежий
            var wanted = "";
            if (post) {
                var hit = null;
                freshItems.some(function (it) {
                    if (String(it.id) === String(post)) { hit = it; return true; }
                    return false;
                });
                if (hit) {
                    wanted = hit.day;
                    jumpTo = hit.id;
                    month = clampMonth(monthOfDay(wanted));
                } else {
                    wanted = String(post).slice(0, 10);
                }
            }
            if (!wanted && day && dayMap[String(day)]) wanted = day;
            if (!wanted || !dayMap[wanted]) {
                wanted = (days[0] && days[0].day) || "";
            }
            selected = wanted;
            if (!month) month = clampMonth(monthOfDay(wanted));
            return open(wanted, false).then(function () { setUrl(wanted, jumpTo); });
        }).catch(function () {
            days = [];
            freshItems = [];
            paintFresh();
            paintPosts(null);
        });
    }

    function boot() {
        if (I18n && I18n.init) I18n.init();
        var sel = el("lang-select");
        if (sel) {
            sel.value = lang();
            sel.addEventListener("change", function (e) {
                if (I18n && I18n.set) I18n.set(e.target.value);
                picked = {};
                loadArchive(selected, "");
            });
        }
        var prev = el("cal-prev"), next = el("cal-next");
        if (prev) prev.addEventListener("click", function () { shiftMonth(-1); });
        if (next) next.addEventListener("click", function () { shiftMonth(1); });
        var params = new URLSearchParams(window.location.search);
        var day = params.get("day") || "";
        var post = params.get("post") || "";
        if (params.get("lang") && I18n && I18n.set) I18n.set(params.get("lang"));
        if (I18n && I18n.onChange) {
            I18n.onChange(function () {
                // язык страницы сменился: подписи, календарь и посты
                // перерисовываются, а ручной выбор языка внутри поста остаётся
                paintChips();
                paintFresh();
                paintCalendar();
                open(selected, false);
            });
        }
        api("/api/auth/me").then(function (res) {
            paintNav(res && res.ok ? res.user : null);
        }).catch(function () { paintNav(null); });
        loadArchive(day, post);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
