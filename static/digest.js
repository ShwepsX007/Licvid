/**
 * LiqScope — страница «Дневной дайджест».
 *
 * Слева архив выпусков, справа выпуск целиком. Тексты приходят с сервера
 * сразу в двух языках: показываем русский, если язык сайта русский, иначе
 * английский (остальные языки интерфейса читают английский текст).
 */
(function () {
    "use strict";

    var I18n = window.LiqScopeI18n;
    function t(key) { return I18n && I18n.t ? I18n.t(key) : key; }
    function el(id) { return document.getElementById(id); }
    function lang() { return (I18n && I18n.lang && I18n.lang()) || "ru"; }
    function articleLang() { return lang() === "ru" ? "ru" : "en"; }

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

    var items = [];
    var selected = "";

    function paintList() {
        var box = el("dig-list");
        if (!box) return;
        if (!items.length) {
            box.innerHTML = '<div class="dig-empty">' + esc(t("dig.empty")) + "</div>";
            return;
        }
        box.innerHTML = items.map(function (it) {
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
    }

    function paintChips(item) {
        var latest = el("dig-latest"), total = el("dig-total");
        if (latest) {
            latest.textContent = item
                ? (item.day_label || item.day) + (item.window_h ? " · " + item.window_h + "ч" : "")
                : "—";
        }
        if (total) total.textContent = item ? money(item.total_usd) : "—";
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

    function setUrl(day) {
        try {
            var url = "/digest" + (day ? "?day=" + encodeURIComponent(day) : "");
            window.history.replaceState(null, "", url);
        } catch (e) { /* ignore */ }
    }

    function open(day, push) {
        selected = day || "";
        paintList();
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
            if (!items.length) {
                paintList();
                paintArticle(null);
                paintChips(null);
                return;
            }
            var wanted = day && items.some(function (x) { return String(x.day) === String(day); })
                ? day : items[0].day;
            return open(wanted, false).then(function () { setUrl(wanted); });
        }).catch(function () {
            items = [];
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
                var day = selected;
                loadArchive(day);
            });
        }
        var params = new URLSearchParams(window.location.search);
        var day = params.get("day") || "";
        if (params.get("lang") && I18n && I18n.set) I18n.set(params.get("lang"));
        if (I18n && I18n.onChange) I18n.onChange(function () { loadArchive(selected); });
        loadArchive(day);
        // Выход в кабинет или на страницу входа
        api("/api/auth/me").then(function (res) {
            var box = el("nav-account");
            if (!box) return;
            if (res && res.ok && res.user) {
                box.innerHTML = '<a class="btn btn-ghost btn-compact" href="/cabinet">👤 ' +
                    esc(res.user.display_name || res.user.username || t("dig.cabinet")) + "</a>";
            } else {
                box.innerHTML = '<a class="btn btn-primary btn-compact" href="/login">' +
                    esc(t("dig.login")) + "</a>";
            }
        }).catch(function () {
            var box = el("nav-account");
            if (box) box.innerHTML = '<a class="btn btn-primary btn-compact" href="/login">' + esc(t("dig.login")) + "</a>";
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
