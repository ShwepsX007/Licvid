/**
 * Архив старых выпусков: удаление дайджестов и сводок по часам.
 *
 * Снимает запись со страниц /digest и /hourly, а если в канале есть id
 * сообщения — убирает и вышедший пост в Telegram. Telegram-посты, ушедшие
 * до этой версии, удалить нельзя: тогда id сообщений не сохранялись. Об этом
 * честно сказано в подписи раздела.
 */
(function () {
    "use strict";

    var TG_KEY = "liqscope_archive_tg";
    var state = { digests: [], digestsAll: 0, hourly: [], hourlyAll: 0, days: [], tz: 0 };

    function el(id) {
        return document.getElementById(id);
    }

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function say(text, bad) {
        var box = el("arc-status");
        if (!box) return;
        box.textContent = text || "";
        box.classList.toggle("bad", !!bad);
    }

    function tgOn() {
        var box = el("arc-tg");
        return !box || !!box.checked;
    }

    function money(v) {
        var n = Number(v) || 0;
        if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
        if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
        if (n >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
        return "$" + n.toFixed(0);
    }

    function hhmm(ts, tz) {
        var n = Number(ts) || 0;
        if (!n) return "";
        var d = new Date((n + (Number(tz) || 0) * 3600) * 1000);
        var pad = function (x) { return (x < 10 ? "0" : "") + x; };
        return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
    }

    function getJson(url) {
        return fetch(url, { credentials: "same-origin" }).then(function (r) {
            return r.json().catch(function () { return {}; });
        });
    }

    function postJson(url, body) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                data = data || {};
                if (!data.message) data.message = data.error || ("HTTP " + r.status);
                data.ok = !!data.ok && r.ok;
                return data;
            });
        });
    }

    function tgSummary(res) {
        // Что вышло в Telegram: сколько сообщений снесли и где не получилось
        var tg = res && res.tg;
        if (!tg) return tgOn() ? "в Telegram ничего не убирали" : "";
        var parts = [];
        Object.keys(tg).forEach(function (lang) {
            var row = tg[lang] || {};
            var name = lang.toUpperCase();
            if (row.deleted) parts.push(name + ": удалено " + row.deleted);
            if (row.err) parts.push(name + ": " + row.err);
        });
        return parts.join("; ");
    }

    /* ---------- выпуски дня ---------- */

    function digestRow(it) {
        var tg = (it.tg || []).map(function (x) { return x.toUpperCase(); }).join("+");
        var langs = (it.langs || []).map(function (x) { return x.toUpperCase(); }).join("+");
        var sub = (it.window_h || 24) + "ч · " + money(it.total_usd) + " · "
            + (it.liq_count || 0) + " ликвидаций" + (langs ? " · вышел: " + langs : " · в канал не уходил");
        return '<div class="art-row arc-row" data-id="' + esc(it.day) + '">'
            + '<span class="ar-title">' + esc(it.label || it.day) + "</span>"
            + '<span class="art-badge pub">' + esc(it.day) + "</span>"
            + (tg ? '<span class="art-badge tg">в TG: ' + esc(tg) + "</span>"
                  : '<span class="art-badge">в TG нет id</span>')
            + (it.cover ? '<span class="art-badge">с фото</span>' : "")
            + '<span class="ar-sub">' + esc(sub) + "</span>"
            + '<button class="btn btn-small arc-del" type="button" data-act="del" data-day="'
            + esc(it.day) + '">🗑 Удалить</button>'
            + "</div>";
    }

    function renderDigests(data) {
        var box = el("arc-digests");
        if (!box) return;
        var items = (data && data.items) || [];
        state.digests = items;
        state.digestsAll = (data && data.count) || items.length;
        if (!items.length) {
            box.innerHTML = '<p class="meta">Выпусков нет — архив пуст.</p>';
            return;
        }
        box.innerHTML = items.map(digestRow).join("")
            + '<p class="meta">Всего в архиве: ' + state.digestsAll
            + (state.digestsAll > items.length ? " (показаны первые " + items.length + ")" : "")
            + ". Удаление убирает выпуск со страницы /digest.</p>";
    }

    /* ---------- сводки по часам ---------- */

    function hourlyRow(it) {
        var tg = (it.tg || []).map(function (x) { return x.toUpperCase(); }).join("+");
        var sub = (it.day || "") + " · " + (it.window_h || 4) + "ч · " + money(it.total_usd)
            + " · " + (it.liq_count || 0) + " ликвидаций"
            + (it.langs && it.langs.length > 1 ? " · RU+EN" : it.langs && it.langs.length ? " · " + String(it.langs[0]).toUpperCase() : "");
        return '<div class="art-row arc-row" data-id="' + esc(it.id) + '">'
            + '<span class="ar-title">' + esc(hhmm(it.ts, state.tz) || it.day) + "</span>"
            + (tg ? '<span class="art-badge tg">в TG: ' + esc(tg) + "</span>"
                  : '<span class="art-badge">в TG нет id</span>')
            + (it.cover ? '<span class="art-badge">с фото</span>' : "")
            + '<span class="ar-sub">' + esc(sub) + "</span>"
            + '<button class="btn btn-small arc-del" type="button" data-act="del" data-id="'
            + esc(it.id) + '">🗑 Удалить</button>'
            + "</div>";
    }

    function renderHourly(data) {
        var box = el("arc-hourly");
        if (!box) return;
        var items = (data && data.items) || [];
        state.hourly = items;
        state.hourlyAll = (data && data.count) || items.length;
        state.days = (data && data.days) || [];
        state.tz = (data && data.tz_hours) || 0;
        if (!items.length) {
            box.innerHTML = '<p class="meta">Сводок нет — архив пуст. Их наполняет бот каждой '
                + "публикацией в канал, а кнопка «Собрать сводку сейчас» кладёт пост без Telegram.</p>";
            return;
        }
        var html = '<div id="arc-day-btns" class="row-actions" style="margin-bottom:8px">';
        state.days.forEach(function (d) {
            html += '<button class="btn btn-small arc-day" type="button" data-act="day" data-day="'
                + esc(d.day) + '">🗑 Весь день ' + esc(d.day) + " (" + (d.n || 0) + ")</button>";
        });
        html += "</div>" + items.map(hourlyRow).join("")
            + '<p class="meta">Всего в архиве: ' + state.hourlyAll
            + (state.hourlyAll > items.length ? " (показаны первые " + items.length + ")" : "")
            + ". Кнопка «Весь день» снимает все сводки за сутки.</p>";
        box.innerHTML = html;
    }

    /* ---------- действия ---------- */

    function reload() {
        say("Обновляю архив…");
        return Promise.all([
            getJson("/api/admin/digest/archive?limit=200"),
            getJson("/api/admin/hourly/archive?limit=200"),
        ]).then(function (both) {
            var d = both[0] || {}, h = both[1] || {};
            if (!d.ok && !h.ok) {
                say("Архив не открылся: " + esc(d.message || h.message || "нет доступа"), true);
                return state;
            }
            renderDigests(d);
            renderHourly(h);
            var tgHint = "";
            var old = (d.items || []).filter(function (x) { return !x.tg_ready; }).length
                + (h.items || []).filter(function (x) { return !x.tg_ready; }).length;
            if (old) {
                tgHint = " У " + old + " записей нет id из Telegram — их в канале не удалить "
                    + "(id начали сохраняться только с этой версии).";
            }
            say("В архиве: " + (d.count || 0) + " выпусков, " + (h.count || 0)
                + " сводок." + tgHint);
            return state;
        }).catch(function (e) {
            say("Ошибка связи: " + e.message, true);
            return state;
        });
    }

    function removeDigest(day) {
        if (!window.confirm("Удалить выпуск за " + day + " со страницы /digest"
                + (tgOn() ? " и вышедший пост из Telegram" : "") + "?")) return Promise.resolve(false);
        say("Удаляю выпуск за " + day + "…");
        return postJson("/api/admin/digest/" + encodeURIComponent(day) + "/delete",
                        { tg: tgOn() }).then(function (res) {
            if (!res.ok) {
                say("Не удалилось: " + (res.message || res.error), true);
                return false;
            }
            var extra = tgSummary(res);
            say("Выпуск за " + day + " удалён" + (extra ? ". " + extra : "") + ".");
            return reload().then(function () { return true; });
        });
    }

    function removePost(id) {
        if (!window.confirm("Удалить эту сводку со страницы /hourly"
                + (tgOn() ? " и её пост из Telegram" : "") + "?")) return Promise.resolve(false);
        say("Удаляю сводку…");
        return postJson("/api/admin/hourly/" + encodeURIComponent(id) + "/delete",
                        { tg: tgOn() }).then(function (res) {
            if (!res.ok) {
                say("Не удалилось: " + (res.message || res.error), true);
                return false;
            }
            var extra = tgSummary(res);
            say("Сводка удалена" + (extra ? ". " + extra : "") + ".");
            return reload().then(function () { return true; });
        });
    }

    function removeDay(day, n) {
        if (!window.confirm("Удалить все сводки за " + day + " (" + n + " шт.) со страницы /hourly"
                + (tgOn() ? " и их посты из Telegram" : "") + "?")) return Promise.resolve(false);
        say("Удаляю сводки за " + day + "…");
        return postJson("/api/admin/hourly/day/" + encodeURIComponent(day) + "/delete",
                        { tg: tgOn() }).then(function (res) {
            if (!res.ok) {
                say("Не удалилось: " + (res.message || res.error), true);
                return false;
            }
            var extra = tgSummary(res);
            say("Сводки за " + day + " удалены: " + (res.deleted || 0) + " шт."
                + (extra ? ". " + extra : "") + ".");
            return reload().then(function () { return true; });
        });
    }

    /* ---------- запуск ---------- */

    function onClick(ev) {
        var btn = ev.target && ev.target.closest ? ev.target.closest("button[data-act]") : null;
        if (!btn) return;
        var act = btn.getAttribute("data-act");
        if (act === "del" && btn.hasAttribute("data-day")) return void removeDigest(btn.getAttribute("data-day"));
        if (act === "del") return void removePost(btn.getAttribute("data-id"));
        if (act === "day") {
            var day = btn.getAttribute("data-day");
            var row = state.days.filter(function (d) { return d.day === day; })[0] || {};
            return void removeDay(day, row.n || 0);
        }
    }

    function bind() {
        var card = el("archive-admin");
        if (!card || card.getAttribute("data-bound") === "1") return;
        card.setAttribute("data-bound", "1");
        var box = el("arc-tg");
        if (box) {
            try {
                var saved = window.localStorage && window.localStorage.getItem(TG_KEY);
                if (saved === "0") box.checked = false;
            } catch (e) { /* приватный режим — просто без памяти */ }
            box.addEventListener("change", function () {
                try {
                    window.localStorage.setItem(TG_KEY, box.checked ? "1" : "0");
                } catch (e) { /* ок */ }
            });
        }
        var reloadBtn = el("arc-reload");
        if (reloadBtn) reloadBtn.addEventListener("click", function () { reload(); });
        card.addEventListener("click", onClick);
        reload();
    }

    window.ArchiveAdmin = {
        load: reload,
        removeDigest: removeDigest,
        removePost: removePost,
        removeDay: removeDay,
        state: function () { return state; },
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bind);
    } else {
        bind();
    }
})();
