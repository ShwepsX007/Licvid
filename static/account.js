(function (global) {
    "use strict";

    var T = {
        ru: {
            login: "Войти", cabinet: "Кабинет", admin: "Админка", logout: "Выйти",
            terminal: "Терминал",
            loginTitle: "Вход через Telegram",
            loginLead: "Кабинет и бот — один аккаунт. Нажмите кнопку, подтвердите /start в Telegram — и вернитесь сюда.",
            openBot: "Открыть бота Telegram",
            waiting: "Ждём подтверждение в боте…",
            ok: "Готово, открываем кабинет",
            noBot: "Бот ещё не настроен. Администратор задаёт LIQSCOPE_BOT_TOKEN.",
            expired: "Код устарел — нажмите кнопку ещё раз.",
            banned: "Доступ закрыт.",
            member: "с нами с",
            roleUser: "пользователь", roleAdmin: "администратор",
            services: "Сервисы",
            servicesLead: "Подписка сохранится: когда сервис заработает, он появится и в кабинете, и в боте.",
            soon: "скоро",
            waitlistOn: "В листе ожидания",
            waitlistOff: "В лист ожидания",
            subscribed: "Подключено",
            subscribe: "Подключить",
            noticeEmpty: "Объявлений нет.",
            users: "Пользователи",
            visits: "Визиты",
            broadcast: "Рассылка в Telegram",
            send: "Отправить",
            saved: "Сохранено",
            health: "Биржи в эфире",
            settings: "Настройки",
            welcome: "Приветствие бота",
            siteNotice: "Объявление на сайте",
            ban: "Бан", unban: "Разбан",
            views: "просмотров", uniques: "уник.",
        },
        en: {
            login: "Sign in", cabinet: "Cabinet", admin: "Admin", logout: "Log out",
            terminal: "Terminal",
            loginTitle: "Sign in with Telegram",
            loginLead: "Cabinet and bot share one account. Open Telegram, press Start, then come back.",
            openBot: "Open Telegram bot",
            waiting: "Waiting for confirmation in the bot…",
            ok: "Done — opening the cabinet",
            noBot: "Bot is not configured. Set LIQSCOPE_BOT_TOKEN.",
            expired: "Code expired — press the button again.",
            banned: "Access denied.",
            member: "member since",
            roleUser: "user", roleAdmin: "admin",
            services: "Services",
            servicesLead: "Your subscription is kept: when a service ships, it appears in the cabinet and the bot.",
            soon: "soon",
            waitlistOn: "On the waitlist",
            waitlistOff: "Join waitlist",
            subscribed: "On",
            subscribe: "Enable",
            noticeEmpty: "No announcements.",
            users: "Users", visits: "Visits",
            broadcast: "Telegram broadcast", send: "Send", saved: "Saved",
            health: "Live exchanges", settings: "Settings",
            welcome: "Bot welcome text", siteNotice: "Site notice",
            ban: "Ban", unban: "Unban",
            views: "views", uniques: "unique",
        },
    };

    function lang() {
        try {
            var c = (window.LiqScopeI18n && LiqScopeI18n.lang()) || "ru";
            return T[c] ? c : "ru";
        } catch (e) { return "ru"; }
    }
    function t(k) { return (T[lang()] || T.ru)[k] || (T.en[k] || k); }

    async function api(path, opts) {
        var r = await fetch(path, Object.assign({
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
        }, opts || {}));
        var data = {};
        try { data = await r.json(); } catch (e) { data = {}; }
        data._status = r.status;
        return data;
    }

    function $(id) { return document.getElementById(id); }

    function fmtDate(ts) {
        if (!ts) return "—";
        try {
            return new Date(Number(ts) * 1000).toLocaleDateString();
        } catch (e) { return "—"; }
    }

    function initials(u) {
        var n = (u && (u.first_name || u.username || "?")) || "?";
        return String(n).slice(0, 1).toUpperCase();
    }

    function paintNav(user) {
        var box = $("nav-account");
        if (!box) return;
        var path = location.pathname || "";
        if (!user) {
            if (path === "/login") { box.innerHTML = ""; return; }
            box.innerHTML = '<a class="btn btn-primary btn-compact" href="/login">' + t("login") + "</a>";
            return;
        }
        var html = "";
        if (path !== "/cabinet") {
            html += '<a class="btn btn-ghost btn-compact" href="/cabinet">' + t("cabinet") + "</a>";
        }
        if (user.is_admin && path !== "/admin") {
            html += '<a class="btn btn-ghost btn-compact" href="/admin">' + t("admin") + "</a>";
        }
        html += '<button type="button" class="btn btn-ghost btn-compact" id="acc-logout">' + t("logout") + "</button>";
        box.innerHTML = html;
        var lo = $("acc-logout");
        if (lo) lo.addEventListener("click", function (e) {
            e.preventDefault();
            api("/api/auth/logout", { method: "POST" }).then(function () {
                location.href = "/";
            });
        });
    }

    function fillAvatar(el, user) {
        if (!el || !user) return;
        if (user.photo_url) {
            el.innerHTML = '<img alt="" src="' + user.photo_url.replace(/"/g, "") + '">';
        } else {
            el.textContent = initials(user);
        }
    }

    /* ---------- login ---------- */
    var waitTimer = null;
    function startLogin() {
        var status = $("login-status");
        var btn = $("login-btn");
        if (status) { status.className = "login-status"; status.textContent = t("waiting"); }
        if (btn) btn.disabled = true;
        api("/api/auth/telegram/start", { method: "POST" }).then(function (d) {
            if (!d.ok) {
                if (status) {
                    status.className = "login-status err";
                    status.textContent = d.error === "no_bot" ? t("noBot") : (d.hint || d.error || t("noBot"));
                }
                if (btn) btn.disabled = false;
                return;
            }
            window.open(d.bot_link, "_blank", "noopener");
            var left = d.expires_in || 300;
            waitTimer && clearInterval(waitTimer);
            waitTimer = setInterval(function () {
                left -= 1;
                api("/api/auth/telegram/wait?nonce=" + encodeURIComponent(d.nonce)).then(function (w) {
                    if (w.ok && w.user) {
                        clearInterval(waitTimer);
                        if (status) { status.className = "login-status ok"; status.textContent = t("ok"); }
                        var next = new URLSearchParams(location.search).get("next") || "/cabinet";
                        location.href = next;
                        return;
                    }
                    if (w.error === "expired" || w.error === "unknown" || left <= 0) {
                        clearInterval(waitTimer);
                        if (status) { status.className = "login-status err"; status.textContent = t("expired"); }
                        if (btn) btn.disabled = false;
                    }
                });
            }, 1000);
        }).catch(function () {
            if (status) { status.className = "login-status err"; status.textContent = t("noBot"); }
            if (btn) btn.disabled = false;
        });
    }

    global.onTelegramAuth = function (user) {
        api("/api/auth/telegram/widget", { method: "POST", body: JSON.stringify(user) }).then(function (d) {
            if (d.ok) {
                location.href = new URLSearchParams(location.search).get("next") || "/cabinet";
            } else {
                var status = $("login-status");
                if (status) {
                    status.className = "login-status err";
                    status.textContent = d.error === "banned" ? t("banned") : (d.error || "error");
                }
            }
        });
    };

    /* ---------- cabinet ---------- */
    function renderCabinet(payload, services) {
        var u = (payload && payload.user) || payload || {};
        var notice = (payload && payload.site_notice) || "";
        $("cab-name") && ($("cab-name").textContent = u.display_name);
        $("cab-role") && ($("cab-role").textContent = u.is_admin ? t("roleAdmin") : t("roleUser"));
        $("cab-role") && $("cab-role").classList.toggle("badge-admin", !!u.is_admin);
        $("cab-meta") && ($("cab-meta").textContent =
            (u.username ? "@" + u.username + " · " : "") +
            "id " + u.tg_id + " · " + t("member") + " " + fmtDate(u.created_at));
        fillAvatar($("cab-avatar"), u);
        var botLink = $("cab-bot-link");
        if (botLink) {
            var href = (payload && payload.bot_link) || "";
            if (!href) {
                var un = String((payload && payload.bot_username) || "LiqScopeBot").replace(/^@/, "");
                href = "https://t.me/" + un;
            }
            botLink.href = href;
            botLink.setAttribute("target", "_blank");
            botLink.setAttribute("rel", "noopener");
        }
        var n = $("site-notice");
        if (n) {
            if (notice) { n.textContent = notice; n.classList.remove("hidden"); }
            else n.classList.add("hidden");
        }
        var box = $("svc-grid");
        if (!box) return;
        box.innerHTML = (services || []).map(function (s) {
            var soon = s.coming_soon ? '<div class="soon">⏳ ' + t("soon") + "</div>" : "";
            var label = s.coming_soon
                ? (s.subscribed ? t("waitlistOn") : t("waitlistOff"))
                : (s.subscribed ? t("subscribed") : t("subscribe"));
            return '<div class="card svc' + (s.coming_soon ? " locked" : "") + '">' +
                '<div class="icon">' + (s.icon || "•") + "</div>" +
                "<h3>" + (s.title || s.slug) + "</h3>" +
                "<p>" + (s.description || "") + "</p>" +
                soon +
                '<div class="row-actions"><button class="btn btn-ghost btn-small" data-slug="' +
                s.slug + '" data-on="' + (s.subscribed ? "0" : "1") + '">' + label +
                "</button></div></div>";
        }).join("") || "<p class='lead'>—</p>";
        box.querySelectorAll("button[data-slug]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                api("/api/account/services/" + btn.getAttribute("data-slug"), {
                    method: "POST",
                    body: JSON.stringify({ enabled: btn.getAttribute("data-on") === "1" }),
                }).then(function () { bootCabinet(); });
            });
        });
    }

    function bootCabinet() {
        Promise.all([
            api("/api/auth/me"),
            api("/api/account/services"),
        ]).then(function (arr) {
            var me = arr[0];
            if (!me.user) { location.href = "/login?next=/cabinet"; return; }
            paintNav(me.user);
            renderCabinet(me, (arr[1] && arr[1].services) || []);
            var cabLo = $("cab-logout");
            if (cabLo) cabLo.addEventListener("click", function () {
                api("/api/auth/logout", { method: "POST" }).then(function () {
                    location.href = "/";
                });
            });
        });
    }

    /* ---------- admin ---------- */
    function usd(v) {
        v = Number(v) || 0;
        if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
        if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
        if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
        return String(Math.round(v));
    }

    function renderBars(days) {
        var el = $("visit-bars");
        if (!el) return;
        var max = 1;
        (days || []).forEach(function (d) { if (d.views > max) max = d.views; });
        el.innerHTML = (days || []).map(function (d) {
            var h = Math.max(6, Math.round(100 * d.views / max));
            var label = String(d.day || "").slice(5);
            return '<div class="bar" style="height:' + h + 'px" title="' +
                d.day + ": " + d.views + " / " + d.uniques + '"><span>' + label + "</span></div>";
        }).join("");
    }

    function renderUsers(list) {
        var tb = $("users-body");
        if (!tb) return;
        tb.innerHTML = (list || []).map(function (u) {
            var un = u.username ? "@" + u.username : "—";
            var role = u.is_admin ? "★" : "";
            var banBtn = u.is_banned
                ? '<button class="btn btn-ghost btn-small" data-ban="0" data-id="' + u.id + '">' + t("unban") + "</button>"
                : '<button class="btn btn-danger btn-small" data-ban="1" data-id="' + u.id + '">' + t("ban") + "</button>";
            return "<tr class='" + (u.is_banned ? "banned" : "") + "'>" +
                "<td>" + (u.display_name || "") + " " + role + "</td>" +
                "<td class='mono'>" + un + "</td>" +
                "<td class='mono'>" + u.tg_id + "</td>" +
                "<td>" + fmtDate(u.last_seen) + "</td>" +
                "<td>" + banBtn + "</td></tr>";
        }).join("") || "<tr><td colspan='5'>—</td></tr>";
        tb.querySelectorAll("button[data-id]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                api("/api/admin/users/" + btn.getAttribute("data-id") + "/ban", {
                    method: "POST",
                    body: JSON.stringify({ banned: btn.getAttribute("data-ban") === "1" }),
                }).then(function () { loadUsers($("user-q") && $("user-q").value); });
            });
        });
    }

    function loadUsers(q) {
        api("/api/admin/users?q=" + encodeURIComponent(q || "")).then(function (d) {
            if (d.ok) renderUsers(d.users);
        });
    }

    function bootAdmin() {
        api("/api/auth/me").then(function (me) {
            if (!me.user) { location.href = "/login?next=/admin"; return; }
            if (!me.user.is_admin) { location.href = "/cabinet"; return; }
            paintNav(me.user);
            api("/api/admin/overview").then(function (d) {
                if (!d.ok) return;
                $("st-users") && ($("st-users").textContent = d.users.total);
                $("st-new") && ($("st-new").textContent = d.users.new_24h);
                $("st-views") && ($("st-views").textContent = d.visits.today_views);
                $("st-uniq") && ($("st-uniq").textContent = d.visits.today_uniques);
                $("st-ws") && ($("st-ws").textContent = d.ws_clients);
                $("st-bot") && ($("st-bot").textContent = d.bot.ready ? ("@" + d.bot.username) : "—");
                var live = (d.health.live_exchanges || []).length;
                $("st-exch") && ($("st-exch").textContent = live);
                renderBars(d.visits.days);
                if ($("bot-welcome")) $("bot-welcome").value = (d.settings && d.settings.bot_welcome) || "";
                if ($("site-notice-in")) $("site-notice-in").value = (d.settings && d.settings.site_notice) || "";
                var svc = $("admin-svc");
                if (svc) {
                    svc.innerHTML = (d.services || []).map(function (s) {
                        return '<label style="display:flex;gap:10px;align-items:center;margin:8px 0">' +
                            "<input type='checkbox' data-svc='" + s.slug + "' data-field='coming_soon' " +
                            (s.coming_soon ? "" : "checked") + "> " +
                            (s.icon || "") + " <b>" + s.title + "</b> — " +
                            "<span style='color:var(--muted);font-size:0.8rem'>включён для пользователей</span>" +
                            "</label>";
                    }).join("");
                    /* checkbox ON = not coming_soon (available) */
                    svc.querySelectorAll("input[data-svc]").forEach(function (inp) {
                        inp.addEventListener("change", function () {
                            api("/api/admin/services/" + inp.getAttribute("data-svc"), {
                                method: "POST",
                                body: JSON.stringify({ coming_soon: !inp.checked }),
                            });
                        });
                    });
                }
            });
            loadUsers("");
            api("/api/admin/stats").then(function (d) {
                if (!d.ok || !d.stats) return;
                $("m-24h") && ($("m-24h").textContent = "$" + usd(d.stats.total_usd_24h));
                $("m-1h") && ($("m-1h").textContent = "$" + usd(d.stats.total_usd_1h));
            });
        });
        var q = $("user-q");
        if (q) q.addEventListener("input", function () { loadUsers(q.value); });
        var bsend = $("broadcast-send");
        if (bsend) bsend.addEventListener("click", function () {
            var text = ($("broadcast-text") || {}).value || "";
            var st = $("broadcast-status");
            api("/api/admin/broadcast", { method: "POST", body: JSON.stringify({ text: text }) }).then(function (d) {
                if (st) st.textContent = d.ok ? ("ok " + d.ok + "/" + d.total) : (d.error || "error");
            });
        });
        var save = $("settings-save");
        if (save) save.addEventListener("click", function () {
            api("/api/admin/settings", {
                method: "POST",
                body: JSON.stringify({
                    bot_welcome: ($("bot-welcome") || {}).value || "",
                    site_notice: ($("site-notice-in") || {}).value || "",
                }),
            }).then(function (d) {
                var st = $("settings-status");
                if (st) st.textContent = d.ok ? t("saved") : (d.error || "error");
            });
        });
    }

    function bootLogin() {
        api("/api/auth/me").then(function (me) {
            paintNav(me.user);
            if (me.user) { location.href = "/cabinet"; return; }
            var widget = $("tg-widget");
            if (widget && me.bot_username) {
                var s = document.createElement("script");
                s.async = true;
                s.src = "https://telegram.org/js/telegram-widget.js?22";
                s.setAttribute("data-telegram-login", me.bot_username);
                s.setAttribute("data-size", "large");
                s.setAttribute("data-radius", "8");
                s.setAttribute("data-request-access", "write");
                s.setAttribute("data-onauth", "onTelegramAuth(user)");
                widget.appendChild(s);
            }
        });
        var btn = $("login-btn");
        if (btn) btn.addEventListener("click", startLogin);
    }

    function boot() {
        var page = (document.body && document.body.getAttribute("data-page")) || "";
        if (page === "login") bootLogin();
        else if (page === "cabinet") bootCabinet();
        else if (page === "admin") bootAdmin();
        else {
            api("/api/auth/me").then(function (me) { paintNav(me.user); });
        }
    }

    global.LiqScopeAccount = { boot: boot, api: api, t: t, paintNav: paintNav };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else boot();
})(window);
