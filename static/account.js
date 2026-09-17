(function (global) {
    "use strict";

    var T = {
        ru: {
            login: "Войти", cabinet: "Кабинет", admin: "Админка", logout: "Выйти",
            terminal: "Терминал",
            loginTitle: "Вход через Telegram",
            loginLead: "Кабинет и бот — один аккаунт. Нажмите кнопку, подтвердите /start в Telegram — и вернитесь сюда.",
            openBot: "Открыть бота Telegram",
            /* почта — основной вход */
            authTitleLogin: "Вход в кабинет",
            authTitleRegister: "Регистрация по почте",
            authTitleLink: "Вход по ссылке из письма",
            authLeadLogin: "Почта и пароль. Telegram можно привязать позже — для сигналов.",
            authLeadRegister: "Почта, пароль — и письмо со ссылкой для подтверждения. Без подтверждения вход закрыт.",
            authLeadLink: "Пришлём ссылку для входа — пароль помнить не нужно.",
            tabLogin: "Вход", tabRegister: "Регистрация", tabLink: "Ссылка на почту",
            labelName: "Имя (необязательно)", labelEmail: "Почта", labelPass: "Пароль",
            submitLogin: "Войти", submitRegister: "Создать аккаунт", submitLink: "Прислать ссылку",
            forgot: "Забыли пароль?", resendVerify: "Отправить письмо ещё раз",
            tgAlt: "Вход через Telegram", tgAltBadge: "запасной способ",
            tgAltLead: "Если почта недоступна — войдите тем же аккаунтом через Telegram.",
            registerSent: "Письмо отправлено на {email}. Откройте его и нажмите «Подтвердить почту».",
            linkSent: "Если адрес зарегистрирован, письмо со ссылкой уже в пути. Ссылка живёт 30 минут.",
            verifySent: "Отправили письмо ещё раз — проверьте почту и папку «Спам».",
            needEmail: "Введите почту.",
            needPass: "Введите пароль.",
            captchaLead: "Проверка",
            captchaLoading: "Проверка — загружаю пример…",
            captchaNeed: "Решите пример — так мы отсекаем роботов.",
            captchaWrong: "Неверный ответ. Пример обновили — решите новый.",
            captchaRefresh: "Другой пример",
            weakShort: "Пароль короче 8 символов.",
            weakSimple: "Такой пароль слишком простой — придумайте посложнее.",
            verifyBad: "Ссылка подтверждения не подошла — запросите новую.",
            verifyUsed: "Ссылка уже использована. Запросите новую.",
            verifyExpired: "Ссылка устарела — запросите новую.",
            verifyFirst: "Сначала подтвердите почту по ссылке из письма.",
            linkBad: "Ссылка для входа не подошла — запросите новую.",
            attachUsed: "Ссылка привязки уже использована — запросите новую в боте.",
            attachExpired: "Ссылка привязки устарела — запросите новую в боте.",
            attachBad: "Ссылка привязки не подошла — запросите новую в боте.",
            attachBlocked: "Аккаунт заблокирован — привязка не сработала.",
            tgAttached: "Telegram привязан к кабинету — сигналы алертов придут в бота.",
            verifyNoMail: "Почта не привязана. Подтвердите её в боте — она нужна для "
                + "входа и восстановления пароля.",
            openBot: "Подтвердить почту в боте",
            mailOff: "Отправка писем пока не настроена на сервере — напишите администратору.",
            emailUnverified: "подтвердите почту",
            emailVerified: "почта подтверждена",
            tgLinked: "Telegram привязан",
            tgNotLinked: "Telegram не привязан",
            tgWhy: "сигналы алертов приходят в бота, привяжите — и они не потеряются",
            tgLink: "Привязать Telegram",
            tgUnlink: "Отвязать",
            tgLinkOpened: "Откройте бота и нажмите Start — привязка подтвердится здесь сама.",
            tgLinkOk: "Telegram привязан — сигналы придут в бота.",
            tgLinkTaken: "Этот Telegram уже привязан к другому аккаунту с почтой.",
            tgUnlinkOk: "Telegram отвязан. Вход по почте и письма работают по-прежнему.",
            tgUnlinkSure: "Отвязать Telegram? Сигналы в бота перестанут приходить.",
            resetTitle: "Новый пароль",
            resetLead: "Придумайте пароль для входа в кабинет по почте.",
            resetPass2: "Пароль ещё раз",
            resetSave: "Сохранить пароль",
            resetMismatch: "Пароли не совпадают.",
            resetOk: "Пароль сохранён — открываем кабинет…",
            resetBad: "Ссылка сброса не подошла — запросите новую на странице входа.",
            emailCol: "Почта",
            svcLocked: "Сервисы включатся после подтверждения почты. Письмо со ссылкой уже отправлено — проверьте адрес и папку «Спам» (кнопка «Отправить письмо ещё раз» выше).",
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
            digestOpen: "Открыть дайджест",
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
            authTitleLogin: "Sign in",
            authTitleRegister: "Sign up with email",
            authTitleLink: "Sign in by email link",
            authLeadLogin: "Email and password. Link Telegram later to get signal alerts.",
            authLeadRegister: "Email, password, and a confirmation letter. Without it sign-in stays closed.",
            authLeadLink: "We will email you a one-time sign-in link — no password needed.",
            tabLogin: "Sign in", tabRegister: "Sign up", tabLink: "Email link",
            labelName: "Name (optional)", labelEmail: "Email", labelPass: "Password",
            submitLogin: "Sign in", submitRegister: "Create account", submitLink: "Send the link",
            forgot: "Forgot password?", resendVerify: "Resend the letter",
            tgAlt: "Sign in with Telegram", tgAltBadge: "backup way",
            tgAltLead: "If email is unavailable, use Telegram for the same account.",
            registerSent: "Letter sent to {email}. Open it and press “Confirm email”.",
            linkSent: "If the address is registered, the link is on its way. It lives 30 minutes.",
            verifySent: "Letter sent again — check your inbox and the spam folder.",
            needEmail: "Enter your email.",
            needPass: "Enter your password.",
            captchaLead: "Check",
            captchaLoading: "Check — loading the example…",
            captchaNeed: "Solve the example — that keeps robots out.",
            captchaWrong: "Wrong answer. We refreshed the example — try the new one.",
            captchaRefresh: "Another example",
            weakShort: "Password is shorter than 8 characters.",
            weakSimple: "That password is too simple — pick a stronger one.",
            verifyBad: "This confirmation link did not work — request a new one.",
            verifyUsed: "The link was already used. Request a new one.",
            verifyExpired: "The link expired — request a new one.",
            verifyFirst: "Confirm your email using the letter first.",
            linkBad: "This sign-in link did not work — request a new one.",
            attachUsed: "This attach link was already used — request a new one in the bot.",
            attachExpired: "The attach link expired — request a new one in the bot.",
            attachBad: "The attach link did not work — request a new one in the bot.",
            attachBlocked: "The account is blocked — the link did not attach anything.",
            tgAttached: "Telegram is linked to this cabinet — alert signals go to the bot.",
            verifyNoMail: "No email attached. Confirm it in the bot — it is needed for "
                + "sign-in and password recovery.",
            openBot: "Confirm email in the bot",
            mailOff: "Email sending is not configured on the server yet — ping the admin.",
            emailUnverified: "confirm your email",
            emailVerified: "email confirmed",
            tgLinked: "Telegram linked",
            tgNotLinked: "Telegram not linked",
            tgWhy: "alert signals arrive in the bot — link it so they are not lost",
            tgLink: "Link Telegram",
            tgUnlink: "Unlink",
            tgLinkOpened: "Open the bot and press Start — the link will confirm here.",
            tgLinkOk: "Telegram linked — alerts will arrive in the bot.",
            tgLinkTaken: "This Telegram is already linked to another email account.",
            tgUnlinkOk: "Telegram unlinked. Email sign-in keeps working.",
            tgUnlinkSure: "Unlink Telegram? Bot alerts will stop.",
            resetTitle: "New password",
            resetLead: "Pick a password for signing in with your email.",
            resetPass2: "Password again",
            resetSave: "Save password",
            resetMismatch: "Passwords do not match.",
            resetOk: "Password saved — opening the cabinet…",
            resetBad: "This reset link did not work — request a new one on the sign-in page.",
            emailCol: "Email",
            svcLocked: "Services unlock once your email is confirmed. The letter with the link is on its way — check the inbox and spam folder (use “Resend the letter” above).",
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
            digestOpen: "Open digest",
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
    function t(k, vars) {
        var s = (T[lang()] || T.ru)[k] || (T.en[k] || k);
        if (vars) {
            Object.keys(vars).forEach(function (name) {
                s = s.replace(new RegExp("\\{" + name + "\\}", "g"), vars[name]);
            });
        }
        return s;
    }

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
    function renderCabinet(payload, services, locked) {
        var u = (payload && payload.user) || payload || {};
        var notice = (payload && payload.site_notice) || "";
        $("cab-name") && ($("cab-name").textContent = u.display_name);
        $("cab-role") && ($("cab-role").textContent = u.is_admin ? t("roleAdmin") : t("roleUser"));
        $("cab-role") && $("cab-role").classList.toggle("badge-admin", !!u.is_admin);
        var metaParts = [];
        if (u.email) metaParts.push(u.email);
        $("cab-meta") && ($("cab-meta").textContent =
            metaParts.concat([
                u.tg_linked ? ("@" + (u.username || "telegram")) : t("tgNotLinked"),
                t("member") + " " + fmtDate(u.created_at),
            ]).join(" · "));
        fillAvatar($("cab-avatar"), u);
        var roleBadge = $("cab-role");
        if (roleBadge) roleBadge.textContent = u.is_admin ? t("roleAdmin") : t("roleUser");
        // метка подтверждения почты рядом с ролью
        var verBadge = $("cab-verified");
        if (verBadge) {
            if (!u.email) verBadge.classList.add("hidden");
            else {
                verBadge.classList.remove("hidden");
                verBadge.textContent = u.email_verified ? t("emailVerified") : t("emailUnverified");
                verBadge.className = "badge" + (u.email_verified ? "" : " badge-warn");
            }
        }
        // баннер «подтвердите почту»: письмо или бот — зависит от того,
        // привязан ли адрес вообще
        var vbar = $("verify-bar");
        var vhint = $("verify-bar-hint");
        var vresend = $("verify-resend");
        var botHref = (payload && payload.bot_link) || "";
        if (!botHref) {
            var bun = String((payload && payload.bot_username) || "LiqScopeBot").replace(/^@/, "");
            botHref = "https://t.me/" + bun;
        }
        if (vbar) {
            if (u.email && !u.email_verified) {
                vbar.classList.remove("hidden");
                if (vhint) vhint.textContent = t("verifyFirst");
                if (vresend) {
                    vresend.textContent = t("resendVerify");
                    vresend.onclick = function () {
                        api("/api/auth/email/resend", {
                            method: "POST",
                            body: JSON.stringify({ email: u.email, language: lang() }),
                        }).then(function (d) {
                            if (vresend) {
                                vresend.textContent = d.ok ? t("verifySent") : (d.error || "error");
                            }
                        });
                    };
                }
            } else if (!u.email) {
                // вошли через Telegram/бота: почты нет — напоминаем привязать её
                vbar.classList.remove("hidden");
                if (vhint) vhint.textContent = t("verifyNoMail");
                if (vresend) {
                    vresend.textContent = t("openBot");
                    vresend.onclick = function () { location.href = botHref; };
                }
            } else {
                vbar.classList.add("hidden");
            }
        }
        paintTgCard(u, payload);
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
        var box = $("svc-acc");
        if (!box) return;
        if (locked) {
            // жёсткий режим: сервисы недоступны, пока почта не подтверждена
            box.innerHTML = '<div class="notice">' + t("svcLocked") + "</div>";
            return;
        }
        var open = "";
        try { open = localStorage.getItem("liqscope.svc.open") || ""; } catch (e) { open = ""; }
        var list = services || [];
        if (!list.some(function (s) { return s.slug === "alerts"; })) {
            list = [{ slug: "alerts", title: "Алерты по объёму", icon: "🔔",
                description: "Ликвидации, CVD и OI", coming_soon: false, subscribed: true }].concat(list);
        }
        box.innerHTML = list.map(function (s) {
            var isOpen = open === s.slug;
            var soon = s.coming_soon ? '<div class="soon">⏳ ' + t("soon") + "</div>" : "";
            var label = s.coming_soon
                ? (s.subscribed ? t("waitlistOn") : t("waitlistOff"))
                : (s.subscribed ? t("subscribed") : t("subscribe"));
            var body = s.slug === "alerts"
                ? '<div class="alerts-board" id="alerts-board"></div>'
                : s.slug === "correlations"
                    ? '<div class="svc-board" id="corr-board"></div>'
                    : s.slug === "watchlist"
                        ? '<div class="svc-board" id="pump-board"></div>'
                        : "<p>" + (s.description || "") + "</p>" + soon +
                    '<div class="row-actions">' +
                    // у дайджеста есть своя страница: сразу ведём читать выпуски
                    (s.slug === "digest"
                        ? '<a class="btn btn-primary btn-small" href="/digest">📰 ' +
                          t("digestOpen") + "</a>"
                        : "") +
                    '<button class="btn btn-ghost btn-small" data-slug="' +
                    s.slug + '" data-soon="' + (s.coming_soon ? "1" : "0") +
                    '" data-on="' + (s.subscribed ? "0" : "1") + '">' + label +
                    "</button></div>";
            return '<div class="svc-fold' + (isOpen ? " open" : "") + '" data-fold="' + s.slug + '">' +
                '<button type="button" class="svc-fold-h" data-toggle="' + s.slug + '">' +
                '<span class="ic">' + (s.icon || "•") + "</span><span><div>" +
                (s.title || s.slug) + '</div><div class="svc-fold-sub">' +
                (s.description || "") + "</div></span><span class=\"chev\">▼</span></button>" +
                '<div class="svc-fold-b">' + body + "</div></div>";
        }).join("");
        box.querySelectorAll("[data-toggle]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var slug = btn.getAttribute("data-toggle");
                var fold = box.querySelector('[data-fold="' + slug + '"]');
                var was = fold && fold.classList.contains("open");
                box.querySelectorAll(".svc-fold").forEach(function (f) { f.classList.remove("open"); });
                if (!was && fold) fold.classList.add("open");
                try { localStorage.setItem("liqscope.svc.open", was ? "" : slug); } catch (e) {}
                if (!was && slug === "correlations") bootCorrelations();
                if (!was && slug === "watchlist") bootPumps();
            });
        });
        box.querySelectorAll("button[data-slug]").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                // Не перезагружаем весь кабинет: кнопка переключается на месте,
                // ответ сервера только подтверждает (или откатывает) выбор.
                var want = btn.getAttribute("data-on") === "1";
                paintSvcBtn(btn, want);
                btn.disabled = true;
                api("/api/account/services/" + btn.getAttribute("data-slug"), {
                    method: "POST",
                    body: JSON.stringify({ enabled: want }),
                }).then(function (d) {
                    btn.disabled = false;
                    if (!d || d.ok === false) paintSvcBtn(btn, !want);
                    else bootOpenBoard(btn.getAttribute("data-slug"));
                }).catch(function () {
                    btn.disabled = false;
                    paintSvcBtn(btn, !want);
                });
            });
        });
        bootAlerts();
        // доски сервисов поднимаем только для раскрытой гармошки: не дёргаем
        // сервер зря, а данные обновляются, пока сервис на экране
        if (open === "correlations") bootCorrelations();
        if (open === "watchlist") bootPumps();
    }

    var tgLinkTimer = null;

    /* ---------- Telegram в кабинете: привязка и отвязка ---------- */
    function paintSvcBtn(btn, subscribed) {
        /* Кнопка «подписаться/отписаться» без перерисовки всей гармошки:
           переключаем подпись и цель на самой кнопке. */
        var soon = btn.getAttribute("data-soon") === "1";
        btn.setAttribute("data-on", subscribed ? "0" : "1");
        btn.textContent = soon
            ? (subscribed ? t("waitlistOn") : t("waitlistOff"))
            : (subscribed ? t("subscribed") : t("subscribe"));
    }

    function bootOpenBoard(slug) {
        /* Доска раскрытого сервиса обновляется сама: перезагрузка кабинета
           на каждое нажатие только мигала бы экраном. */
        var fold = document.querySelector('.svc-fold[data-fold="' + slug + '"]');
        if (!fold || !fold.classList.contains("open")) return;
        if (slug === "correlations") bootCorrelations();
        else if (slug === "watchlist") bootPumps();
        else if (slug === "alerts") bootAlerts();
    }

    function paintTgCard(u, payload) {
        var box = $("tg-card");
        if (!box) return;
        var state = $("tg-state");
        var btn = $("tg-link");
        var off = $("tg-unlink");
        if (state) {
            state.textContent = u.tg_linked
                ? (t("tgLinked") + (u.username ? " @" + u.username : ""))
                : t("tgNotLinked") + " — " + t("tgWhy");
        }
        if (btn) {
            btn.classList.toggle("hidden", !!u.tg_linked);
            btn.disabled = !(payload && payload.bot_ready);
            btn.title = (payload && payload.bot_ready) ? "" : t("noBot");
        }
        if (off) off.classList.toggle("hidden", !u.tg_linked);
    }

    function linkTelegram() {
        var st = $("tg-state");
        api("/api/auth/telegram/link", { method: "POST" }).then(function (d) {
            if (!d.ok) {
                if (st) st.textContent = d.error === "no_bot" ? t("noBot") : (d.hint || d.error || "error");
                return;
            }
            if (st) st.textContent = t("tgLinkOpened");
            window.open(d.bot_link, "_blank", "noopener");
            var left = d.expires_in || 900;
            tgLinkTimer && clearInterval(tgLinkTimer);
            tgLinkTimer = setInterval(function () {
                left -= 3;
                api("/api/auth/telegram/link/status?nonce=" + encodeURIComponent(d.nonce))
                    .then(function (w) {
                        if (w.ok) {
                            clearInterval(tgLinkTimer);
                            if (st) st.textContent = t("tgLinkOk");
                            bootCabinet();
                            return;
                        }
                        if (w.error === "taken") {
                            clearInterval(tgLinkTimer);
                            if (st) st.textContent = t("tgLinkTaken");
                            return;
                        }
                        if (w.error === "expired" || w.error === "unknown" || left <= 0) {
                            clearInterval(tgLinkTimer);
                        }
                    });
            }, 3000);
        });
    }

    function unlinkTelegram() {
        if (!window.confirm(t("tgUnlinkSure"))) return;
        api("/api/auth/telegram/unlink", { method: "POST" }).then(function (d) {
            var st = $("tg-state");
            if (d.ok) {
                if (st) st.textContent = t("tgUnlinkOk");
                bootCabinet();
            } else if (st) {
                st.textContent = d.error || "error";
            }
        });
    }

    function bootCabinet() {
        var tgBtn = $("tg-link");
        if (tgBtn && !tgBtn._bound) { tgBtn._bound = true; tgBtn.addEventListener("click", linkTelegram); }
        var tgOff = $("tg-unlink");
        if (tgOff && !tgOff._bound) { tgOff._bound = true; tgOff.addEventListener("click", unlinkTelegram); }
        Promise.all([
            api("/api/auth/me"),
            api("/api/account/services"),
        ]).then(function (arr) {
            var me = arr[0];
            if (!me.user) { location.href = "/login?next=/cabinet"; return; }
            paintNav(me.user);
            var locked = !!(me.user.email && !me.user.email_verified);
            var services = (arr[1] && arr[1].services) || [];
            if (arr[1] && arr[1].error) services = [];   // почта не подтверждена
            renderCabinet(me, services, locked);
            // пришли по ссылке из письма о привязке Telegram
            var tgQ = new URLSearchParams(location.search).get("tg");
            if (tgQ === "attached") {
                var note = $("site-notice");
                if (note) {
                    note.textContent = t("tgAttached");
                    note.classList.remove("hidden");
                }
            }
            var cabLo = $("cab-logout");
            if (cabLo) cabLo.addEventListener("click", function () {
                api("/api/auth/logout", { method: "POST" }).then(function () {
                    location.href = "/";
                });
            });
        });
    }

    /* ---------- volume alerts ---------- */
    var alCfg = null;
    var alPresets = null;
    var alTimer = null;
    var alSaveT = null;
    var alBusy = false;
    var alSparkBuf = { liq: [], cvd: [], oi: [] };

    function alMoney(v) {
        v = Number(v) || 0;
        var sign = v < 0 ? "−" : "";
        v = Math.abs(v);
        if (v >= 1e9) return sign + "$" + (v / 1e9).toFixed(2) + "B";
        if (v >= 1e6) return sign + "$" + (v / 1e6).toFixed(2) + "M";
        if (v >= 1e3) return sign + "$" + (v / 1e3).toFixed(1) + "K";
        return sign + "$" + Math.round(v);
    }
    function alWin(m) {
        m = Number(m) || 0;
        if (m >= 60 && m % 60 === 0) return (m / 60) + "ч";
        return m + "м";
    }
    function alHas(m) {
        return alCfg && (alCfg.watch || []).indexOf(m) >= 0;
    }
    function alChip(on, attrs, label) {
        return '<button type="button" class="al-chip' + (on ? " on" : "") + '" ' + attrs + ">" +
            label + "</button>";
    }

    function bootAlerts() {
        if (!$("alerts-board")) return;
        loadAlerts(true);
        if (alTimer) clearInterval(alTimer);
        alTimer = setInterval(function () { loadAlerts(false); }, 4000);
    }

    function loadAlerts(full) {
        if (alBusy && !full) return;
        api("/api/account/alerts").then(function (d) {
            if (!d.ok) return;
            alPresets = d.presets || alPresets;
            if (full || !alCfg) {
                alCfg = d.config || alCfg;
                paintAlerts(d, true);
            } else {
                paintAlertsLive(d);
            }
        });
    }

    function saveAlerts() {
        if (!alCfg) return;
        alBusy = true;
        var st = $("al-status");
        if (st) { st.className = "al-status"; st.textContent = "сохраняю…"; }
        api("/api/account/alerts", {
            method: "POST",
            body: JSON.stringify(alCfg),
        }).then(function (d) {
            alBusy = false;
            if (d.config) alCfg = d.config;
            if (st) {
                st.className = "al-status " + (d.ok ? "ok" : "");
                st.textContent = d.ok ? "сохранено · то же в боте" : (d.error || "ошибка");
            }
            if (d.live) paintAlertsLive({ live: d.live, history: null, config: alCfg });
        }).catch(function () { alBusy = false; });
    }

    function alDebounce() {
        if (alSaveT) clearTimeout(alSaveT);
        alSaveT = setTimeout(saveAlerts, 280);
    }

    function alThrList(m) {
        var p = alPresets || {};
        if (m === "cvd" || m === "oi") return p.thresholds_flow || [10000, 100000, 1e6, 1e7, 1e8];
        return p.thresholds_liq || p.thresholds || [50000, 100000, 250000, 500000, 1e6, 5e6];
    }
    function alPushSpark(key, row) {
        var src = (row && row.spark && row.spark.length) ? row.spark : null;
        if (src) { alSparkBuf[key] = src.slice(); return; }
        var a = alSparkBuf[key] || [];
        a.push(Number(row && row.value) || 0);
        if (a.length > 36) a = a.slice(-36);
        alSparkBuf[key] = a;
    }
    function alSparkGrid(w, h) {
        // прозрачная сетка, как на биржевом графике
        var g = "";
        for (var gy = 1; gy <= 3; gy++) {
            var y = (h * gy / 4).toFixed(1);
            g += '<line x1="0" y1="' + y + '" x2="' + w + '" y2="' + y +
                '" stroke="rgba(132,147,168,0.16)" stroke-width="1" vector-effect="non-scaling-stroke"/>';
        }
        for (var gx = 1; gx <= 4; gx++) {
            var x = (w * gx / 5).toFixed(1);
            g += '<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + h +
                '" stroke="rgba(132,147,168,0.09)" stroke-width="1" vector-effect="non-scaling-stroke"/>';
        }
        return g;
    }
    function alSparkSvg(vals) {
        vals = (vals || []).map(Number).filter(function (x) { return isFinite(x); });
        var w = 120, h = 30, p = 2;
        var open = '<div class="al-spark-wrap">' +
            '<svg class="al-spark" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">';
        if (vals.length < 2) {
            return open + alSparkGrid(w, h) + "</svg></div>";
        }
        var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
        if (max === min) max = min + 1;
        var pts = vals.map(function (v, i) {
            var x = p + (w - 2 * p) * i / (vals.length - 1);
            var y = h - p - (h - 2 * p) * (v - min) / (max - min);
            return x.toFixed(1) + "," + y.toFixed(1);
        }).join(" ");
        var up = vals[vals.length - 1] >= vals[0];
        var color = up ? "#00e676" : "#ff2a5f";
        // тонкая линия (1px, не масштабируется растяжением) + шкала цифрами
        return open + alSparkGrid(w, h) +
            '<polyline fill="none" stroke="' + color +
            '" stroke-width="1" vector-effect="non-scaling-stroke" points="' + pts + '"/>' +
            "</svg>" +
            '<span class="al-spark-max">' + alMoney(max) + "</span>" +
            '<span class="al-spark-min">' + alMoney(min) + "</span>" +
            "</div>";
    }

    function paintAlerts(d, bind) {
        var board = $("alerts-board");
        if (!board || !alCfg) return;
        var live = d.live || {};
        var hist = d.history || [];
        var symbols = d.symbols || [];
        var p = alPresets || { windows: [1, 5, 15, 30, 60, 240],
            thresholds: [50000, 100000, 250000, 500000, 1000000, 5000000],
            min_event: [0, 10000, 25000, 50000, 100000],
            coins: ["ALL", "BTC_USDT", "ETH_USDT", "SOL_USDT"] };
        function chips(list, cur, attr, fmt) {
            return list.map(function (v) {
                return alChip(String(v) === String(cur), attr + '="' + v + '"', fmt ? fmt(v) : String(v));
            }).join("");
        }
        function feed(key, title) {
            var row = live[key] || {};
            alPushSpark(key, row);
            var on = alHas(key);
            var val = row.value || 0;
            var thr = (alCfg.threshold || {})[key] || 1;
            var pct = Math.max(4, Math.min(100, Math.round(100 * Math.abs(val) / thr)));
            var hot = on && Math.abs(val) >= thr;
            var cls = key === "liq" ? "gold" : (val >= 0 ? "pos" : "neg");
            var extra = "";
            if (key === "liq" && row.market) extra = "рынок " + alMoney(row.market.usd);
            else extra = (row.symbol && row.symbol !== "ALL" ? String(row.symbol).split("_")[0] : "") +
                (row.count ? " · " + row.count + " шт." : "");
            return '<div class="al-feed' + (on ? " on" : "") + (hot ? " hot" : "") +
                '" data-feed="' + key + '"><div class="al-feed-h">' +
                '<label class="al-switch' + (on ? " on" : "") + '" data-metric="' + key + '">' +
                "<i></i><span>" + title + (on ? " · ON" : " · выкл") + "</span></label></div>" +
                '<div class="al-meter" data-metric="' + key + '"><div class="v ' + cls + '">' +
                alMoney(val) + '</div><div class="s">' + extra + " / порог " + alMoney(thr) +
                '</div><div class="al-bar"><i style="width:' + pct + '%"></i></div></div>' +
                alSparkSvg(alSparkBuf[key]) +
                '<div class="al-label">Порог ' + title + "</div>" +
                '<div class="al-chips" data-thr="' + key + '">' +
                chips(alThrList(key), thr, "data-thrval", alMoney) + "</div>" +
                '<div class="al-row" style="margin-top:6px"><input data-thrin="' + key +
                '" type="number" min="0" step="1000" value="' + Math.round(thr || 0) + '"></div>' +
                '<div class="al-label">Лента ' + title + "</div>" +
                '<div class="al-tape" id="al-tape-' + key + '">' + alTape(hist, key) + "</div></div>";
        }
        var coin = alCfg.symbol || "ALL";
        var coinLabel = coin === "ALL" ? "все" : String(coin).split("_")[0];
        board.innerHTML =
            '<div class="al-head"><div><h3>Алерты по объёму</h3>' +
            '<div class="al-sub">Включайте ликвидации, CVD и OI по отдельности — можно слушать один источник или два. Порог CVD/OI крупнее, чем у ликвидаций.</div></div>' +
            '<label class="al-switch' + (alCfg.enabled ? " on" : "") + '" id="al-sw">' +
            "<i></i><span>" + (alCfg.enabled ? "СИГНАЛ ВКЛ" : "СИГНАЛ ВЫКЛ") + "</span></label></div>" +
            '<div class="al-label">Монета</div><div class="al-chips" id="al-coins">' +
            chips(p.coins || [], coin, "data-coin", function (v) {
                return v === "ALL" ? "все" : String(v).split("_")[0];
            }) + "</div>" +
            '<div class="al-row" style="margin-top:8px"><input id="al-coin-in" type="text" list="al-sym-list" placeholder="BTC_USDT или тикер" value="' +
            (coin === "ALL" ? "" : coin) + '"><datalist id="al-sym-list">' +
            (symbols || []).map(function (s) { return "<option value=\"" + s + "\">"; }).join("") +
            "</datalist></div>" +
            '<div class="al-label">Окно агрегации</div><div class="al-chips" id="al-wins">' +
            chips(p.windows || [], alCfg.window_min, "data-win", alWin) +
            '</div><div class="al-row" style="margin-top:8px"><input id="al-win-in" type="number" min="1" max="1440" placeholder="минуты" value="' +
            alCfg.window_min + '"></div>' +
            '<div class="al-feeds">' +
            feed("liq", "LIQ") + feed("cvd", "CVD") + feed("oi", "OI") +
            "</div>" +
            '<div class="al-label">Мин. удар в окне</div>' +
            ["liq", "cvd", "oi"].map(function (m) {
                var cur = (alCfg.min_event || {})[m];
                return '<div class="al-label" style="margin-top:8px">' + m.toUpperCase() + "</div>" +
                    '<div class="al-chips" data-min="' + m + '">' +
                    chips(p.min_event || [], cur, "data-minval", alMoney) +
                    '</div><div class="al-row" style="margin-top:6px"><input data-minin="' + m +
                    '" type="number" min="0" step="1000" value="' + Math.round(cur || 0) + '"></div>';
            }).join("") +
            '<div class="al-status" id="al-status">монета: ' + coinLabel +
            " · окно " + alWin(alCfg.window_min) + "</div>";
        if (bind) bindAlerts();
    }

    function alTape(hist, metric) {
        var rows = (hist || []).filter(function (h) {
            return !metric || String(h.metric || "") === metric;
        });
        if (!rows.length) return '<div class="s">пока тихо</div>';
        // ярко, как лента терминала: время · монета · сумма · порог/окно,
        // цвет по метрике/знаку, полоса накачки |значения| к порогу
        return rows.map(function (h) {
            var ts = h.ts ? new Date(Number(h.ts) * 1000) : null;
            var tm = ts ? ts.toLocaleTimeString([], {
                hour: "2-digit", minute: "2-digit", second: "2-digit",
            }) : "—";
            var sym = String(h.symbol || "").split("_")[0];
            var val = Number(h.value) || 0;
            var thr = Number(h.threshold) || 0;
            var pct = thr > 0 ? Math.min(100, Math.round(100 * Math.abs(val) / thr)) : 100;
            var hot = thr > 0 && Math.abs(val) >= thr;
            var m = String(h.metric || "liq");
            var cls = m === "liq" ? "gold" : (val >= 0 ? "pos" : "neg");
            var icon = m === "cvd" ? "🌊" : m === "oi" ? "📊" : "💥";
            return '<div class="row' + (hot ? " hot" : "") + '" style="--p:' + pct + '%">' +
                '<span class="t">' + tm + "</span>" +
                '<span class="m">' + icon + "</span>" +
                '<span class="sym">' + (sym === "ALL" ? "все" : sym) + "</span>" +
                '<span class="val ' + cls + '">' + alMoney(val) + "</span>" +
                '<span class="thr">/ ' + alMoney(thr) + " · " + alWin(h.window_min) + "</span>" +
                '<i class="tape-bar"></i></div>';
        }).join("");
    }

    function paintAlertsLive(d) {
        var live = d.live || {};
        ["liq", "cvd", "oi"].forEach(function (key) {
            var feed = document.querySelector('.al-feed[data-feed="' + key + '"]');
            var el = document.querySelector('.al-meter[data-metric="' + key + '"]');
            var row = live[key] || {};
            alPushSpark(key, row);
            var val = row.value || 0;
            var thr = (alCfg && alCfg.threshold && alCfg.threshold[key]) || 1;
            var pct = Math.max(4, Math.min(100, Math.round(100 * Math.abs(val) / thr)));
            var hot = alHas(key) && Math.abs(val) >= thr;
            if (feed) {
                feed.classList.toggle("on", alHas(key));
                feed.classList.toggle("hot", hot);
                var spark = feed.querySelector(".al-spark-wrap") || feed.querySelector(".al-spark");
                if (spark) {
                    var wrap = document.createElement("div");
                    wrap.innerHTML = alSparkSvg(alSparkBuf[key]);
                    spark.replaceWith(wrap.firstChild);
                }
            }
            if (!el) return;
            el.classList.toggle("on", alHas(key));
            el.classList.toggle("hot", hot);
            var v = el.querySelector(".v");
            if (v) {
                v.textContent = alMoney(val);
                v.className = "v " + (key === "liq" ? "gold" : (val >= 0 ? "pos" : "neg"));
            }
            var bar = el.querySelector(".al-bar i");
            if (bar) bar.style.width = pct + "%";
        });
        if (d.history) {
            ["liq", "cvd", "oi"].forEach(function (key) {
                var tape = $("al-tape-" + key);
                if (tape) tape.innerHTML = alTape(d.history, key);
            });
        }
    }

    function bindAlerts() {
        var sw = $("al-sw");
        if (sw) sw.addEventListener("click", function () {
            alCfg.enabled = !alCfg.enabled;
            sw.classList.toggle("on", alCfg.enabled);
            sw.querySelector("span").textContent = alCfg.enabled ? "СИГНАЛ ВКЛ" : "СИГНАЛ ВЫКЛ";
            alDebounce();
        });
        document.querySelectorAll(".al-switch[data-metric]").forEach(function (el) {
            el.addEventListener("click", function (e) {
                e.preventDefault();
                var m = el.getAttribute("data-metric");
                var w = alCfg.watch ? alCfg.watch.slice() : [];
                var i = w.indexOf(m);
                if (i >= 0) w.splice(i, 1);
                else w.push(m);
                alCfg.watch = w;
                el.classList.toggle("on", alHas(m));
                var sp = el.querySelector("span");
                if (sp) {
                    var title = m === "liq" ? "LIQ" : m.toUpperCase();
                    sp.textContent = title + (alHas(m) ? " · ON" : " · выкл");
                }
                var feed = document.querySelector('.al-feed[data-feed="' + m + '"]');
                if (feed) feed.classList.toggle("on", alHas(m));
                alDebounce();
            });
        });
        document.querySelectorAll("[data-coin]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                alCfg.symbol = btn.getAttribute("data-coin");
                document.querySelectorAll("[data-coin]").forEach(function (b) {
                    b.classList.toggle("on", b === btn);
                });
                var inp = $("al-coin-in");
                if (inp) inp.value = alCfg.symbol === "ALL" ? "" : alCfg.symbol;
                alDebounce();
            });
        });
        var cin = $("al-coin-in");
        if (cin) cin.addEventListener("change", function () {
            var v = (cin.value || "").trim().toUpperCase().replace("-", "_");
            alCfg.symbol = v || "ALL";
            alDebounce();
        });
        document.querySelectorAll("[data-win]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                alCfg.window_min = Number(btn.getAttribute("data-win")) || 5;
                document.querySelectorAll("[data-win]").forEach(function (b) {
                    b.classList.toggle("on", b === btn);
                });
                var inp = $("al-win-in");
                if (inp) inp.value = alCfg.window_min;
                alDebounce();
            });
        });
        var win = $("al-win-in");
        if (win) win.addEventListener("change", function () {
            var n = Math.max(1, Math.min(1440, Number(win.value) || 5));
            alCfg.window_min = n;
            alDebounce();
        });
        document.querySelectorAll("[data-thrval]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var box = btn.parentNode;
                var m = box && box.getAttribute("data-thr");
                if (!m) return;
                alCfg.threshold = alCfg.threshold || {};
                alCfg.threshold[m] = Number(btn.getAttribute("data-thrval")) || 0;
                box.querySelectorAll("[data-thrval]").forEach(function (b) {
                    b.classList.toggle("on", b === btn);
                });
                var inp = document.querySelector('[data-thrin="' + m + '"]');
                if (inp) inp.value = alCfg.threshold[m];
                alDebounce();
            });
        });
        document.querySelectorAll("[data-thrin]").forEach(function (inp) {
            inp.addEventListener("change", function () {
                var m = inp.getAttribute("data-thrin");
                alCfg.threshold = alCfg.threshold || {};
                alCfg.threshold[m] = Math.max(0, Number(inp.value) || 0);
                alDebounce();
            });
        });
        document.querySelectorAll("[data-minval]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var box = btn.parentNode;
                var m = box && box.getAttribute("data-min");
                if (!m) return;
                alCfg.min_event = alCfg.min_event || {};
                alCfg.min_event[m] = Number(btn.getAttribute("data-minval")) || 0;
                box.querySelectorAll("[data-minval]").forEach(function (b) {
                    b.classList.toggle("on", b === btn);
                });
                var inp = document.querySelector('[data-minin="' + m + '"]');
                if (inp) inp.value = alCfg.min_event[m];
                alDebounce();
            });
        });
        document.querySelectorAll("[data-minin]").forEach(function (inp) {
            inp.addEventListener("change", function () {
                var m = inp.getAttribute("data-minin");
                alCfg.min_event = alCfg.min_event || {};
                alCfg.min_event[m] = Math.max(0, Number(inp.value) || 0);
                alDebounce();
            });
        });
    }

    /* ---------- сервис «Корреляции валют»: тепловая карта и облако связей ---------- */
    /* Почему сервис раньше «туго» отвечал на нажатия: доска перерисовывалась
       целиком на каждом автообновлении (раз в 30 с) — вместе с разметкой
       умирали все обработчики, и клик по чипу уходил в пустоту. Теперь:
       1) обработчики висят на контейнере доски (делегирование) — перерисовка
          их не сносит; 2) клик сразу переключает чип и перерисовывает картину
          из уже пришедших данных, а запрос к серверу идёт фоном; 3) автообнов-
          ление обновляет только цифры (потоки, карту, график, пары, статус),
          а не всю доску. */
    var corData = null, corTimer = null, corCfg = { window: "24h", metric: "liq" };
    var corSaveT = null;
    var corAxes = { x: "liq", y: "vol" };      // оси «облака связей»
    var corPts = [];                           // точки графика: наведение и клик
    var corPinned = null;                      // пара, закреплённая кликом
    var corTip = null;                         // подсказка под курсором
    var corGeom = { w: 0, h: 0 };

    var COR_LABEL = { liq: "LIQ", vol: "📦 Объём", cvd: "🌊 CVD", oi: "📊 OI" };
    var COR_HINT = {
        liq: "по ликвидациям", vol: "по объёму",
        cvd: "по потоку CVD", oi: "по открытому интересу",
    };
    //: какая метрика логично дополняет выбранную (вторая ось графика)
    var COR_PAIR = { liq: "vol", vol: "liq", cvd: "liq", oi: "vol" };
    var COR_ORDER = ["liq", "vol", "cvd", "oi"];

    function corShort(sym) {
        return String(sym == null ? "" : sym).replace("_USDT", "");
    }

    function corLabelOf(key) {
        return COR_LABEL[key] || String(key || "");
    }

    function corMetricTitle(d, key) {
        var list = (d && d.metrics) || [];
        for (var i = 0; i < list.length; i++) {
            var m = list[i];
            var k = (typeof m === "object") ? (m.key || m.metric) : m;
            if (String(k) === String(key)) {
                return (typeof m === "object" && m.title) || corLabelOf(key);
            }
        }
        return corLabelOf(key);
    }

    function corMatrix(d, metric) {
        return ((d && d.matrices) || {})[metric] || {};
    }

    function corR(d, metric, a, b) {
        var v = (corMatrix(d, metric)[a] || {})[b];
        return (v === undefined || v === null) ? null : Number(v);
    }

    function corPairsOf(d, metric) {
        var syms = (d && d.symbols) || [];
        var out = [];
        for (var i = 0; i < syms.length; i++) {
            for (var j = i + 1; j < syms.length; j++) {
                var r = corR(d, metric, syms[i], syms[j]);
                if (r === null) continue;
                out.push({ a: syms[i], b: syms[j], r: r });
            }
        }
        return out;
    }

    function corPairWeight(d, a, b) {
        var coins = (d && d.coins) || {};
        var wa = Math.abs(Number((coins[a] || {}).liq_usd) || 0);
        var wb = Math.abs(Number((coins[b] || {}).liq_usd) || 0);
        return wa + wb;
    }

    function corChartData(d) {
        /* Точки «облака связей»: пара монет = точка, оси — корреляция по двум
           метрикам, размер — насколько крупно эта пара торговалась и горела. */
        var xk = corAxes.x, yk = corAxes.y;
        var out = { x: xk, y: yk, pts: [], maxW: 0 };
        var syms = (d && d.symbols) || [];
        for (var i = 0; i < syms.length; i++) {
            for (var j = i + 1; j < syms.length; j++) {
                var a = syms[i], b = syms[j];
                var rx = corR(d, xk, a, b);
                if (rx === null) continue;
                var ry = (xk === yk) ? rx : corR(d, yk, a, b);
                if (ry === null) continue;
                var w = corPairWeight(d, a, b);
                out.maxW = Math.max(out.maxW, w);
                out.pts.push({ a: a, b: b, x: rx, y: ry, w: w });
            }
        }
        return out;
    }

    function corChip(on, attr, label) {
        return '<button type="button" class="al-chip' + (on ? " on" : "") + '" ' +
            attr + ">" + esc(label) + "</button>";
    }

    function corWindowChips(d) {
        var wins = d.windows || [];
        var cur = d.window;
        return wins.map(function (v) {
            var key = (typeof v === "object") ? (v.key || v.window) : v;
            var label = (typeof v === "object" && (v.label || v.title)) || key;
            return corChip(String(key) === String(cur), 'data-corwin="' + key + '"', label);
        }).join("");
    }

    function corMetricChips(d, cur, attr) {
        return COR_ORDER.map(function (key) {
            return corChip(String(key) === String(cur), attr + '="' + key + '"',
                corLabelOf(key));
        }).join("");
    }

    function corFlow(title, cls, items, unit) {
        var rows = (items || []).map(function (sym) {
            var c = (corData && corData.coins && corData.coins[sym]) || {};
            var val = unit === "liq" ? c.liq_usd : unit === "cvd" ? Math.abs(c.cvd || 0)
                : unit === "oi" ? Math.abs(c.oi_delta || 0) : c.liq_usd;
            var extra = "";
            if (unit === "liq") {
                var long = Number(c.liq_long || 0), short = Number(c.liq_short || 0);
                if (long >= short) extra = Math.round(100 * long / (long + short || 1)) + "% лонги";
                else extra = Math.round(100 * short / (long + short || 1)) + "% шорты";
            } else if (unit === "cvd") {
                extra = (c.cvd_share >= 0 ? "покупатели " : "продавцы ") +
                    Math.abs(Number(c.cvd_share || 0)).toFixed(1) + "% объёма";
            } else if (unit === "oi") {
                extra = (c.oi_pct >= 0 ? "+" : "") + Number(c.oi_pct || 0).toFixed(2) + "% OI";
            }
            return '<div class="cor-flow-row"><span class="cor-flow-sym">' +
                esc(corShort(sym)) + "</span>" +
                '<span class="cor-flow-val ' + cls + '">' + alMoney(val) + "</span>" +
                '<span class="cor-flow-x">' + esc(extra) + "</span></div>";
        });
        if (!rows.length) rows = ['<div class="cor-flow-empty">пока пусто</div>'];
        return '<div class="cor-flow"><div class="cor-flow-h">' + title + "</div>" +
            rows.join("") + "</div>";
    }

    function corFlowsBox() {
        if (!corData) return "";
        var flows = corData.flows || {};
        return corFlow("🩸 Где выносило шорты", "neg", flows.liquidated_short, "liq") +
            corFlow("💥 Где выносило лонги", "gold", flows.liquidated_long, "liq") +
            corFlow("🌊 CVD на сторону продавцов", "neg", flows.cvd_sellers, "cvd") +
            corFlow("🌊 CVD на сторону покупателей", "pos", flows.cvd_buyers, "cvd") +
            corFlow("📈 OI растёт", "pos", flows.oi_up, "oi") +
            corFlow("📉 OI падает", "neg", flows.oi_down, "oi");
    }

    function corPairsList() {
        var pairs = corPairsOf(corData, (corData && corData.metric) || "liq");
        if (!pairs.length) {
            return '<div class="cor-flow-empty">связей пока нет — мало истории</div>';
        }
        pairs.sort(function (p, q) { return Math.abs(q.r) - Math.abs(p.r); });
        return pairs.slice(0, 6).map(function (p, i) {
            var r = Number(p.r) || 0;
            var w = Math.max(6, Math.min(100, Math.round(Math.abs(r) * 100)));
            var cls = r >= 0 ? "pos" : "neg";
            return '<div class="cor-pair" data-corpair="' + i + '">' +
                '<span class="cor-pair-names">' + esc(corShort(p.a)) + " ↔ " +
                esc(corShort(p.b)) + "</span>" +
                '<span class="cor-pair-bar ' + cls + '"><i style="width:' + w + '%"></i></span>' +
                '<span class="cor-pair-r ' + cls + '">' + r.toFixed(2) + "</span></div>";
        }).join("");
    }

    function corHeatColor(r) {
        var a = Math.max(0, Math.min(1, Math.abs(Number(r) || 0)));
        var alpha = (0.06 + 0.7 * a).toFixed(2);
        return Number(r) >= 0
            ? "background:rgba(0,230,118," + alpha + ")"
            : "background:rgba(255,42,95," + alpha + ")";
    }

    function corHeat(d) {
        var syms = (d.symbols || []).slice(0, 10);
        var m = corMatrix(d, d.metric);
        if (syms.length < 2) {
            return '<div class="cor-flow-empty">мало монет с историей за это окно</div>';
        }
        var html = '<div class="cor-heat-wrap"><table class="cor-heat"><thead><tr><th></th>' +
            syms.map(function (s) { return "<th>" + esc(corShort(s)) + "</th>"; }).join("") +
            "</tr></thead><tbody>";
        syms.forEach(function (a) {
            html += "<tr><th>" + esc(corShort(a)) + "</th>";
            syms.forEach(function (b) {
                var v = (m[a] || {})[b];
                var same = a === b;
                if (v === undefined || v === null) {
                    html += '<td class="cor-heat-empty">·</td>';
                    return;
                }
                var r = Number(v);
                html += '<td class="cor-heat-cell' + (same ? " diag" : "") + '" style="' +
                    (same ? "" : corHeatColor(r)) + '">' +
                    (same ? "1" : r.toFixed(2)) + "</td>";
            });
            html += "</tr>";
        });
        return html + "</tbody></table></div>";
    }

    /* ---- «облако связей»: пары монет в координатах двух метрик ------------ */
    function corQuadrant(px, py) {
        if (px >= 0 && py >= 0) return ["pos", "идут вместе"];
        if (px < 0 && py < 0) return ["neg", "вместе падают"];
        return ["mix", "метрики спорят"];
    }

    function corDotColor(px, py) {
        if (px >= 0 && py >= 0) return "rgba(0,230,118,0.85)";
        if (px < 0 && py < 0) return "rgba(255,42,95,0.85)";
        return "rgba(255,193,7,0.85)";
    }

    function drawCorChart(d) {
        var cv = $("cor-scatter");
        if (!cv || !cv.getContext) return;
        var box = cv.parentNode;
        var w = Math.max(260, Math.round((box && box.clientWidth) || cv.clientWidth || 460));
        var h = 260;
        var dpr = (global.devicePixelRatio || 1);
        if (dpr > 2) dpr = 2;
        cv.width = Math.round(w * dpr);
        cv.height = Math.round(h * dpr);
        cv.style.width = w + "px";
        cv.style.height = h + "px";
        corGeom = { w: w, h: h };
        var ctx = cv.getContext("2d");
        if (!ctx) return;
        if (ctx.setTransform) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var data = corChartData(d);
        corPts = [];
        var padL = 34, padR = 12, padT = 14, padB = 30;
        var iw = Math.max(40, w - padL - padR), ih = Math.max(40, h - padT - padB);
        function X(r) { return padL + (iw * (Number(r) + 1)) / 2; }
        function Y(r) { return padT + ih - (ih * (Number(r) + 1)) / 2; }
        ctx.clearRect(0, 0, w, h);
        // фон по квадрантам: спокойный, чтобы точки читались
        ctx.fillStyle = "rgba(0,230,118,0.05)";
        ctx.fillRect(X(0), padT, X(1) - X(0), Y(0) - padT);
        ctx.fillRect(padL, Y(0), X(0) - padL, Y(-1) - Y(0));
        ctx.fillStyle = "rgba(255,42,95,0.05)";
        ctx.fillRect(padL, padT, X(0) - padL, Y(0) - padT);
        ctx.fillRect(X(0), Y(0), X(1) - X(0), Y(-1) - Y(0));
        // сетка и подписи шкалы корреляции
        ctx.strokeStyle = "rgba(132,147,168,0.18)";
        ctx.lineWidth = 1;
        if (ctx.font) ctx.font = "10px system-ui, sans-serif";
        [-1, -0.5, 0.5, 1].forEach(function (v) {
            ctx.beginPath(); ctx.moveTo(X(v), padT); ctx.lineTo(X(v), padT + ih); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(padL, Y(v)); ctx.lineTo(padL + iw, Y(v)); ctx.stroke();
            if (ctx.fillText) {
                ctx.fillStyle = "#8d9cb4";
                ctx.fillText(String(v), X(v) - 8, padT + ih + 14);
                ctx.fillText(String(v), 6, Y(v) + 3);
            }
        });
        ctx.strokeStyle = "rgba(160,180,210,0.55)";
        ctx.beginPath(); ctx.moveTo(X(0), padT); ctx.lineTo(X(0), padT + ih); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(padL + iw, Y(0)); ctx.stroke();
        // диагональ «одинаково по обеим метрикам»
        ctx.setLineDash && ctx.setLineDash([4, 4]);
        ctx.strokeStyle = "rgba(120,140,175,0.35)";
        ctx.beginPath(); ctx.moveTo(X(-1), Y(-1)); ctx.lineTo(X(1), Y(1)); ctx.stroke();
        ctx.setLineDash && ctx.setLineDash([]);
        // точки: размер — оборот пары, цвет — знак связи по обеим метрикам
        data.pts.forEach(function (p) {
            var wgt = data.maxW > 0 ? Math.sqrt(p.w / data.maxW) : 0.4;
            var rad = 3 + 6 * wgt;
            var px = X(p.x), py = Y(p.y);
            ctx.beginPath();
            if (ctx.arc) ctx.arc(px, py, rad, 0, Math.PI * 2);
            ctx.fillStyle = corDotColor(p.x, p.y);
            ctx.fill();
            ctx.strokeStyle = "rgba(12,18,28,0.75)";
            ctx.stroke();
            p.px = px; p.py = py; p.rad = rad;
            corPts.push(p);
        });
        if (ctx.fillText) {
            ctx.fillStyle = "#9fb0c8";
            ctx.fillText("r · " + corLabelOf(data.x), padL, padT - 3);
            ctx.save && ctx.save();
            if (ctx.translate && ctx.rotate) {
                ctx.translate(10, padT + ih / 2 + 26);
                ctx.rotate(-Math.PI / 2);
                ctx.fillText("r · " + corLabelOf(data.y), 0, 0);
                ctx.restore && ctx.restore();
            }
        }
        corTip = $("cor-tip");
        if (corTip && corPinned) corPaintTip(corPinned, true);
    }

    function corPaintTip(p, pinned) {
        var tip = $("cor-tip");
        if (!tip) return;
        if (!p) { tip.classList.add("hidden"); return; }
        var q = corQuadrant(p.x, p.y);
        tip.className = "cor-tip " + q[0] + (pinned ? " pinned" : "");
        var verdict = q[0] === "pos" ? "обе метрики идут вместе"
            : q[0] === "neg" ? "обе метрики в противофазе"
                : "знаки разные — связи по метрикам спорят";
        tip.innerHTML = "<b>" + esc(corShort(p.a)) + " ↔ " + esc(corShort(p.b)) + "</b>" +
            '<span class="cor-tip-grid">' +
            "<i>" + esc(corLabelOf(corAxes.x)) + "</i><i>r = " + p.x.toFixed(2) + "</i>" +
            "<i>" + esc(corLabelOf(corAxes.y)) + "</i><i>r = " + p.y.toFixed(2) + "</i>" +
            "</span><span class=\"cor-tip-note\">" + esc(verdict) + " · " +
            esc(alMoney(p.w)) + " ликвидаций на двоих</span>";
    }

    function corPick(px, py) {
        var best = null, bestD = 14 * 14;
        corPts.forEach(function (p) {
            var dx = p.px - px, dy = p.py - py;
            var d2 = dx * dx + dy * dy;
            if (d2 <= Math.max(bestD, (p.rad + 6) * (p.rad + 6)) && (best === null || d2 < bestD)) {
                best = p; bestD = d2;
            }
        });
        return best;
    }

    function corChartBox(d) {
        var data = corChartData(d);
        var strong = data.pts.filter(function (p) { return Math.abs(p.x) >= 0.6 || Math.abs(p.y) >= 0.6; });
        var together = data.pts.filter(function (p) { return p.x >= 0.4 && p.y >= 0.4; }).length;
        var opposite = data.pts.filter(function (p) { return p.x <= -0.4 || p.y <= -0.4; }).length;
        return '<div class="cor-chart">' +
            '<div class="cor-chart-head">' +
            "<div><b>📐 Облако связей</b> <span class=\"cor-chart-sub\">каждая точка — пара монет: " +
            "по горизонтали связь «" + esc(corLabelOf(corAxes.x)) + "», по вертикали — «" +
            esc(corLabelOf(corAxes.y)) + "». Размер точки — оборот пары, цвет — знак связи. " +
            "Правее и выше — пары, которые ходят вместе; левее и ниже — в противофазе.</span></div>" +
            "</div>" +
            '<div class="cor-chart-axes"><span class="cor-axis-label">Ось X</span>' +
            '<span class="al-chips" id="cor-axis-x">' +
            corMetricChips(d, corAxes.x, "data-corx") + "</span>" +
            '<span class="cor-axis-label">Ось Y</span>' +
            '<span class="al-chips" id="cor-axis-y">' +
            corMetricChips(d, corAxes.y, "data-cory") + "</span></div>" +
            '<div class="cor-canvas-wrap"><canvas id="cor-scatter"></canvas>' +
            '<div class="cor-tip hidden" id="cor-tip"></div></div>' +
            '<div class="cor-hist-wrap"><span class="cor-hist-title">Распределение связей · ' +
            esc(corLabelOf(d.metric)) + "</span>" +
            '<div class="cor-hist" id="cor-hist">' + corHist(d.metric) + "</div></div>" +
            '<div class="cor-chart-note">пар в расчёте: ' + data.pts.length +
            " · сильных связей: " + strong.length +
            " · ходят вместе: " + together + " · в противофазе: " + opposite +
            "</div></div>";
    }

    function corHist(metric) {
        /* Сколько пар попало в каждый интервал коэффициента: сразу видно,
           рынок движется одним куском или монеты живут сами по себе. */
        var buckets = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];   // −1…−0.8 … 0.8…1
        corPairsOf(corData, metric).forEach(function (p) {
            var idx = Math.min(9, Math.max(0, Math.floor((p.r + 1) / 0.2)));
            if (p.r === 1) idx = 9;
            buckets[idx] += 1;
        });
        var max = Math.max.apply(null, buckets.concat([1]));
        return buckets.map(function (n, i) {
            var lo = -1 + i * 0.2;
            var cls = lo < 0 ? "neg" : "pos";
            var pct = Math.round(100 * n / max);
            var title = (!n ? "нет пар" : n + " пар") + " · r " +
                (lo >= 0 ? "+" : "") + lo.toFixed(1) + "…" +
                (lo + 0.2 >= 0 ? "+" : "") + (lo + 0.2).toFixed(1);
            return '<i class="' + cls + '" style="height:' + Math.max(2, pct) +
                '%" title="' + esc(title) + '"></i>';
        }).join("");
    }

    function corStatusText() {
        if (!corData) return "";
        var pairs = corPairsOf(corData, corData.metric);
        var active = pairs.filter(function (p) { return Math.abs(p.r) >= 0.4; }).length;
        var text = "монет: " + ((corData.symbols || []).length) +
            " · точек в окне: " + (corData.hours || 0) +
            " · пар: " + pairs.length + " · сильных: " + active;
        return text;
    }

    function paintCorrelations(d, bind) {
        var board = $("corr-board");
        if (!board) return;
        corData = d;
        if (d.config && d.config.metric) corCfg.metric = d.config.metric;
        if (d.config && d.config.window) corCfg.window = d.config.window;
        var winLabel = (d.window_label || d.window || "");
        var head = '<div class="al-head"><div><h3>🔗 Корреляции валют</h3>' +
            '<div class="al-sub">Кто ходит вместе за ' + esc(winLabel) +
            ", а кто в противофазе: ликвидации и объём, CVD и OI. Видно, где выносило шорты, " +
            "а где лонги, куда перекошен CVD и где растёт открытый интерес.</div></div></div>";
        board.innerHTML = head +
            '<div class="al-label">Окно</div><div class="al-chips" id="cor-wins">' +
            corWindowChips(d) + "</div>" +
            '<div class="al-label">Метрика</div><div class="al-chips" id="cor-mets">' +
            corMetricChips(d, d.metric, "data-cormet") + "</div>" +
            '<div class="cor-flows" id="cor-flows">' + corFlowsBox() + "</div>" +
            '<div class="al-label">Тепловая карта · ' + esc(corMetricTitle(d, d.metric)) +
            " · " + esc(winLabel) + "</div>" +
            '<div id="cor-heat-box">' + corHeat(d) + "</div>" +
            '<div class="al-label">График корреляций</div>' +
            '<div id="cor-chart-box">' + corChartBox(d) + "</div>" +
            '<div class="al-label">Самые сильные связи</div>' +
            '<div class="cor-pairs" id="cor-pairs">' + corPairsList() + "</div>" +
            '<div class="al-status" id="cor-status">' + esc(corStatusText()) + "</div>";
        if (bind) bindCorrelations();
        drawCorChart(d);
    }

    function paintCorrelationsLive(d) {
        /* Автообновление: только цифры. Разметку и чипы не трогаем — иначе
           обработчики кнопок умирали бы каждые 30 секунд. */
        corData = d;
        var flows = $("cor-flows");
        if (flows) flows.innerHTML = corFlowsBox();
        var heat = $("cor-heat-box");
        if (heat) heat.innerHTML = corHeat(d);
        var hist = $("cor-hist");
        if (hist) hist.innerHTML = corHist(d.metric);
        var pairs = $("cor-pairs");
        if (pairs) pairs.innerHTML = corPairsList();
        var st = $("cor-status");
        if (st && !corPinned) st.textContent = corStatusText();
        drawCorChart(d);
    }

    function corMarkChips() {
        /* Подсветка выбранного — мгновенно, не дожидаясь ответа сервера. */
        var board = $("corr-board");
        if (!board) return;
        board.querySelectorAll("#cor-wins .al-chip").forEach(function (b) {
            b.classList.toggle("on", b.getAttribute("data-corwin") === String(corCfg.window));
        });
        board.querySelectorAll("#cor-mets .al-chip").forEach(function (b) {
            b.classList.toggle("on", b.getAttribute("data-cormet") === String(corCfg.metric));
        });
        board.querySelectorAll("#cor-axis-x .al-chip").forEach(function (b) {
            b.classList.toggle("on", b.getAttribute("data-corx") === String(corAxes.x));
        });
        board.querySelectorAll("#cor-axis-y .al-chip").forEach(function (b) {
            b.classList.toggle("on", b.getAttribute("data-cory") === String(corAxes.y));
        });
    }

    function corSetStatus(text, ok) {
        var st = $("cor-status");
        if (!st) return;
        st.className = "al-status" + (ok ? " ok" : "");
        st.textContent = text || corStatusText();
    }

    function saveCorrelations() {
        if (corSaveT) clearTimeout(corSaveT);
        corSaveT = setTimeout(function () {
            api("/api/account/correlations", {
                method: "POST",
                body: JSON.stringify({ window: corCfg.window, metric: corCfg.metric }),
            }).then(function (d) {
                if (d && d.ok === false) corSetStatus(d.error || "настройка не сохранилась");
            }).catch(function () { corSetStatus("настройка не сохранилась: сеть"); });
        }, 250);
    }

    function bootCorrelations() {
        if (!$("corr-board")) return;
        bindCorrelations();
        loadCorrelations(true);
        if (corTimer) clearInterval(corTimer);
        corTimer = setInterval(function () {
            if (!document.hidden && $("corr-board") &&
                $("corr-board").offsetParent !== null &&
                !document.querySelector("#corr-board .cor-tip.pinned")) loadCorrelations(false);
        }, 30000);
    }

    function loadCorrelations(full) {
        return api("/api/account/correlations?window=" + corCfg.window + "&metric=" + corCfg.metric)
            .then(function (d) {
                if (!d || !d.ok) return d;
                corData = d;
                if (full || !$("cor-heat-box")) paintCorrelations(d, true);
                else paintCorrelationsLive(d);
                return d;
            });
    }

    function bindCorrelations() {
        var board = $("corr-board");
        if (!board || board._bound) return;
        board._bound = true;              // обработчики живут на контейнере
        board.addEventListener("click", function (e) {
            var t = e.target;
            while (t && t !== board && !(t.getAttribute && (t.getAttribute("data-corwin") ||
                t.getAttribute("data-cormet") || t.getAttribute("data-corx") ||
                t.getAttribute("data-cory") || t.getAttribute("data-corpair")))) {
                t = t.parentNode;
            }
            if (!t || t === board) return;
            var win = t.getAttribute("data-corwin");
            if (win) {
                corCfg.window = win;
                corMarkChips();
                corSetStatus("окно " + win + " · считаю…");
                loadCorrelations(true);
                saveCorrelations();
                return;
            }
            var met = t.getAttribute("data-cormet");
            if (met) {
                corCfg.metric = met;
                corMarkChips();
                if (corData) paintCorrelationsLive(corData);   // карта и графики — сразу
                corSetStatus(corStatusText());
                loadCorrelations(true);                        // цифры подтверждаем фоном
                saveCorrelations();
                return;
            }
            var ax = t.getAttribute("data-corx");
            if (ax) {
                corAxes.x = ax;
                corMarkChips();
                if (corData) { drawCorChart(corData); }
                return;
            }
            var ay = t.getAttribute("data-cory");
            if (ay) {
                corAxes.y = ay;
                corMarkChips();
                if (corData) { drawCorChart(corData); }
                return;
            }
            var pi = t.getAttribute("data-corpair");
            if (pi !== null && pi !== undefined && pi !== "") {
                var p = corPairsOf(corData, (corData && corData.metric) || "liq")
                    .sort(function (x, y) { return Math.abs(y.r) - Math.abs(x.r); })[Number(pi)];
                if (!p) return;
                var verdict = p.r >= 0 ? "движутся вместе" : "в противофазе";
                corSetStatus(corShort(p.a) + " и " + corShort(p.b) + ": r = " +
                    Number(p.r).toFixed(2) + " — " + verdict + " · " +
                    COR_HINT[(corData && corData.metric) || "liq"] + " · окно " +
                    ((corData && (corData.window_label || corData.window)) || ""), true);
            }
        });
        var cv = $("cor-scatter");
        if (cv) {
            cv.addEventListener("mousemove", function (e) {
                var rect = cv.getBoundingClientRect ? cv.getBoundingClientRect() : { left: 0, top: 0 };
                var p = corPick(e.clientX - rect.left, e.clientY - rect.top);
                corPaintTip(p, false);
            });
            cv.addEventListener("mouseleave", function () { corPaintTip(null, false); });
            cv.addEventListener("click", function (e) {
                var rect = cv.getBoundingClientRect ? cv.getBoundingClientRect() : { left: 0, top: 0 };
                var p = corPick(e.clientX - rect.left, e.clientY - rect.top);
                corPinned = p || null;
                corPaintTip(corPinned, true);
                if (p) {
                    corSetStatus("пара " + corShort(p.a) + " ↔ " + corShort(p.b) +
                        ": " + corLabelOf(corAxes.x) + " r = " + p.x.toFixed(2) + ", " +
                        corLabelOf(corAxes.y) + " r = " + p.y.toFixed(2), true);
                }
            });
        }
        if (global.addEventListener && !global._corResize) {
            global._corResize = true;
            global.addEventListener("resize", function () {
                if (corData && $("cor-scatter")) drawCorChart(corData);
            });
        }
    }

    /* ---------- сервис «Сторож монет»: гистограмма пампов/дампов ---------- */
    /* Доска сторожа жила по тому же принципу, что и корреляции: автообновление
       (раз в 12 с) перерисовывало всю разметку, обработчики кнопок исчезали, и
       чипы переставали отвечать. Теперь обработчики висят на контейнере, клик
       сразу переключает чип и пересобирает картину из уже пришедших данных, а
       автообновление меняет только цифры. */
    var pumpData = null, pumpTimer = null, pumpCfg = null, pumpSaveT = null;
    var PUMP_MODE_LABEL = { pump: "🚀 Пампы", dump: "🩸 Дампы", both: "⚖️ Сразу оба" };

    function bootPumps() {
        if (!$("pump-board")) return;
        bindPumps();
        loadPumps(true);
        if (pumpTimer) clearInterval(pumpTimer);
        pumpTimer = setInterval(function () {
            if (!document.hidden && $("pump-board") &&
                $("pump-board").offsetParent !== null) loadPumps(false);
        }, 12000);
    }

    function loadPumps(full) {
        return api("/api/account/watchlist").then(function (d) {
            if (!d || !d.ok) return d;
            pumpData = d;
            if (full || !pumpCfg || !$("pump-bars")) {
                pumpCfg = d.config || pumpCfg;
                paintPumps(d, true);
            } else {
                paintPumps(d, false);
            }
            return d;
        });
    }

    function savePumps() {
        if (!pumpCfg) return;
        api("/api/account/watchlist", {
            method: "POST",
            body: JSON.stringify(pumpCfg),
        }).then(function (d) {
            var st = $("pump-status");
            if (d && d.config) pumpCfg = d.config;
            if (d && d.movers) pumpData = Object.assign({}, pumpData || {}, { movers: d.movers, hits: d.hits || [] });
            if (d && d.hits) {
                var hits = $("pump-hits-box");
                if (hits) hits.innerHTML = pumpHitsBox(d);
            }
            if (st) {
                st.className = "al-status " + (d && d.ok ? "ok" : "");
                st.textContent = d && d.ok ? "сохранено · сигналы придут в Telegram"
                    : ((d && d.error) || "ошибка");
            }
        });
    }

    function pumpDebounce() {
        if (pumpSaveT) clearTimeout(pumpSaveT);
        pumpSaveT = setTimeout(savePumps, 300);
    }

    function pumpSpan(d) {
        // окно сигнала: период свечи × число свечей (текущая свеча считается)
        var per = (d.periods || []).filter(function (x) { return x.key === pumpCfg.period; })[0];
        var mins = (per && per.minutes) || 5;
        return mins * (Number(pumpCfg.candles) || 1);
    }

    function pumpBars(d) {
        var movers = (d.movers || []).slice(0, 12);
        if (!movers.length) {
            return '<div class="cor-flow-empty">данных ещё нет — сторож собирает минутную историю Gate</div>';
        }
        var max = movers.reduce(function (m, x) {
            return Math.max(m, Math.abs(Number(x.change_pct) || 0));
        }, 1);
        return '<div class="pump-bars">' + movers.map(function (m) {
            var pct = Number(m.change_pct) || 0;
            var w = Math.max(2, Math.round(50 * Math.abs(pct) / max));
            var up = pct >= 0;
            var thr = Math.abs(pct) >= Number((pumpCfg && pumpCfg.threshold) || 0);
            return '<div class="pump-row' + (thr ? " hot" : "") + '">' +
                '<span class="pump-sym">' + esc(String(m.symbol).replace("_USDT", "")) +
                "</span>" +
                '<span class="pump-track"><i class="' + (up ? "up" : "down") +
                '" style="' + (up ? "left:50%;" : "right:50%;") + "width:" + w + '%"></i>' +
                '<b class="pump-mid"></b></span>' +
                '<span class="pump-val ' + (up ? "pos" : "neg") + '">' +
                (pct >= 0 ? "+" : "−") + Math.abs(pct).toFixed(2) + "%</span>" +
                '<span class="pump-x">' + esc(alPrice(m.price)) + " · " +
                esc(alMoney(m.volume24h)) + "</span></div>";
        }).join("") + "</div>";
    }

    function pumpHits(d) {
        /* «Уже за порогом» считаем из тех же движений рынка: смена порога
           не должна ждать ответа сервера. */
        var thr = Math.abs(Number((pumpCfg && pumpCfg.threshold) || 0));
        var mode = (pumpCfg && pumpCfg.mode) || "both";
        var rows = (d.hits || []).slice();
        if (d.movers && d.movers.length) {
            rows = d.movers.filter(function (m) {
                var pct = Number(m.change_pct) || 0;
                if (pct >= thr && (mode === "pump" || mode === "both")) return true;
                if (pct <= -thr && (mode === "dump" || mode === "both")) return true;
                return false;
            }).map(function (m) {
                var row = Object.assign({}, m);
                row.kind = (Number(m.change_pct) || 0) >= 0 ? "pump" : "dump";
                row.threshold = thr;
                return row;
            });
        }
        return rows;
    }

    function pumpHitsBox(d) {
        var thr = Number((pumpCfg && pumpCfg.threshold) || 10);
        var hot = pumpHits(d);
        if (!hot.length) {
            return '<div class="cor-flow-empty">тихо: ни одна монета не прошла порог</div>';
        }
        return '<div class="pump-hits">' + hot.slice(0, 8).map(function (h) {
            var up = String(h.kind) === "pump";
            return '<div class="pump-hit ' + (up ? "pos" : "neg") + '">' +
                (up ? "🚀" : "🩸") + " " +
                esc(String(h.symbol).replace("_USDT", "")) + " " +
                (up ? "+" : "−") + Math.abs(Number(h.change_pct) || 0).toFixed(2) +
                "% за " + esc(alWin(h.span_min || 0)) + "</div>";
        }).join("") + "</div>";
    }

    function alPrice(v) {
        v = Number(v) || 0;
        if (v >= 1000) return "$" + v.toFixed(0);
        if (v >= 1) return "$" + v.toFixed(2);
        if (v >= 0.01) return "$" + v.toFixed(4);
        return "$" + v.toPrecision(3);
    }

    function pumpTape(d) {
        var rows = (d.signals || []).slice(0, 12);
        if (!rows.length) return '<div class="s">сигналов пока не было</div>';
        return rows.map(function (s) {
            var ts = s.ts ? new Date(Number(s.ts) * 1000) : null;
            var tm = ts ? ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
            var up = String(s.kind || "pump") === "pump";
            return '<div class="row"><span class="t">' + tm + "</span>" +
                '<span class="m">' + (up ? "🚀" : "🩸") + "</span>" +
                '<span class="sym">' + esc(String(s.symbol).replace("_USDT", "")) + "</span>" +
                '<span class="val ' + (up ? "pos" : "neg") + '">' +
                (up ? "+" : "−") + Math.abs(Number(s.change_pct) || 0).toFixed(2) + "%</span>" +
                '<span class="thr">за ' + esc(alWin(s.span_min || 0)) + "</span></div>";
        }).join("");
    }

    function pumpStatusText(d) {
        var st = (d && d.status) || {};
        return "под наблюдением монет: " + (st.coins || 0) +
            (st.age_sec != null ? " · данные " + Math.round(st.age_sec) + " с назад" : "") +
            " · сигналов за сессию: " + (st.signals_total || 0);
    }

    function pumpChips(list, cur, attr, fmt) {
        return (list || []).map(function (v) {
            var key = typeof v === "object" ? (v.key || v) : v;
            var on = String(key) === String(cur);
            return '<button type="button" class="al-chip' + (on ? " on" : "") + '" ' +
                attr + '="' + key + '">' + esc(fmt ? fmt(v, key) : key) + "</button>";
        }).join("");
    }

    function pumpShell(d) {
        var thr = Number(pumpCfg.threshold || 10);
        return '<div class="al-head"><div><h3>👁 Сторож монет</h3>' +
            '<div class="al-sub">Пампы и дампы всех монет Gate. Порог — изменение цены в % ' +
            'за выбранный период свечей; текущая, ещё формирующаяся свеча считается. ' +
            'Сигнал уходит в Telegram и предлагает посмотреть монету на Gate.</div></div>' +
            '<label class="al-switch' + (pumpCfg.enabled ? " on" : "") + '" id="pump-sw">' +
            "<i></i><span>" + (pumpCfg.enabled ? "СИГНАЛ ВКЛ" : "СИГНАЛ ВЫКЛ") +
            "</span></label></div>" +
            '<div class="al-label">Что ловим</div><div class="al-chips" id="pump-mode">' +
            pumpChips([{ key: "pump", label: "🚀 Пампы" }, { key: "dump", label: "🩸 Дампы" },
                { key: "both", label: "⚖️ Сразу оба" }], pumpCfg.mode, "data-pumpmode",
                function (v) { return v.label; }) + "</div>" +
            '<div class="al-label">Порог изменения цены</div><div class="al-chips" id="pump-thr">' +
            pumpChips(d.thresholds || [3, 5, 10, 20, 30, 50], thr, "data-pumpthr",
                function (v) { return v + "%"; }) + "</div>" +
            '<div class="al-row" style="margin-top:8px"><input id="pump-thr-in" type="number" ' +
            'min="0.5" max="500" step="0.5" value="' + thr + '">' +
            '<span class="pump-hint">свой порог, %</span></div>' +
            '<div class="al-label">Период свечей</div><div class="al-chips" id="pump-per">' +
            pumpChips(d.periods || [{ key: "1m" }, { key: "5m" }], pumpCfg.period, "data-pumpper",
                function (v) { return v.key || v; }) + "</div>" +
            '<div class="al-label">Сколько свечей</div><div class="al-chips" id="pump-cnd">' +
            pumpChips(d.candles || [1, 2, 3, 5, 10], pumpCfg.candles, "data-pumpcn",
                function (v) { return v + " свеч."; }) + "</div>" +
            '<div class="pump-window" id="pump-window">окно сигнала: ' +
            esc(alWin(pumpSpan(d))) + " (период × свечи, текущая свеча считается)</div>" +
            '<div class="al-label">Пампы и дампы сейчас</div><div id="pump-bars">' +
            pumpBars(d) + "</div>" +
            '<div class="al-label">Уже за порогом ' + thr + "%</div>" +
            '<div id="pump-hits-box">' + pumpHitsBox(d) + "</div>" +
            '<div class="al-label">Последние сигналы</div><div class="al-tape" id="pump-tape">' +
            pumpTape(d) + "</div>" +
            '<div class="al-status" id="pump-status">' + esc(pumpStatusText(d)) + "</div>";
    }

    function paintPumps(d, bind) {
        var board = $("pump-board");
        if (!board || !pumpCfg) return;
        pumpData = d;
        board.innerHTML = pumpShell(d);
        if (bind) bindPumps();
    }

    function paintPumpsLive(d) {
        /* Автообновление: цифры меняются, кнопки остаются на месте. */
        pumpData = d;
        var bars = $("pump-bars");
        if (bars) bars.innerHTML = pumpBars(d);
        var hits = $("pump-hits-box");
        if (hits) hits.innerHTML = pumpHitsBox(d);
        var tape = $("pump-tape");
        if (tape) tape.innerHTML = pumpTape(d);
        var st = $("pump-status");
        if (st) st.textContent = pumpStatusText(d);
    }

    function pumpMarkChips() {
        var board = $("pump-board");
        if (!board || !pumpCfg) return;
        board.querySelectorAll("#pump-mode .al-chip").forEach(function (b) {
            b.classList.toggle("on", b.getAttribute("data-pumpmode") === String(pumpCfg.mode));
        });
        board.querySelectorAll("#pump-thr .al-chip").forEach(function (b) {
            b.classList.toggle("on", Number(b.getAttribute("data-pumpthr")) === Number(pumpCfg.threshold));
        });
        board.querySelectorAll("#pump-per .al-chip").forEach(function (b) {
            b.classList.toggle("on", b.getAttribute("data-pumpper") === String(pumpCfg.period));
        });
        board.querySelectorAll("#pump-cnd .al-chip").forEach(function (b) {
            b.classList.toggle("on", Number(b.getAttribute("data-pumpcn")) === Number(pumpCfg.candles));
        });
        var w = $("pump-window");
        if (w) w.textContent = "окно сигнала: " + alWin(pumpSpan(pumpData || {})) +
            " (период × свечи, текущая свеча считается)";
        var sw = $("pump-sw");
        if (sw) {
            sw.classList.toggle("on", !!pumpCfg.enabled);
            var sp = sw.querySelector("span");
            if (sp) sp.textContent = pumpCfg.enabled ? "СИГНАЛ ВКЛ" : "СИГНАЛ ВЫКЛ";
        }
    }

    function pumpRepaintNow() {
        /* Мгновенная реакция на нажатие: пересобираем зависимые блоки из того,
           что уже пришло с сервера — без ожидания сети. */
        pumpMarkChips();
        var d = pumpData || {};
        var hits = $("pump-hits-box");
        if (hits) hits.innerHTML = pumpHitsBox(d);
        var label = document.querySelector("#pump-board .al-label + #pump-hits-box");
        void label;
        var bars = $("pump-bars");
        if (bars) bars.innerHTML = pumpBars(d);
        var thrs = document.querySelectorAll("#pump-board .al-label");
        thrs.forEach(function (el) {
            if (/^Уже за порогом/.test(el.textContent || "")) {
                el.textContent = "Уже за порогом " + Number(pumpCfg.threshold || 0) + "%";
            }
        });
    }

    function bindPumps() {
        var board = $("pump-board");
        if (!board || board._bound) return;
        board._bound = true;             // один раз и навсегда — перерисовка не страшна
        board.addEventListener("click", function (e) {
            var t = e.target;
            while (t && t !== board && !(t.getAttribute && (t.getAttribute("data-pumpmode") ||
                t.getAttribute("data-pumpthr") || t.getAttribute("data-pumpper") ||
                t.getAttribute("data-pumpcn") || t.id === "pump-sw"))) {
                t = t.parentNode;
            }
            if (!t || t === board) return;
            if (t.id === "pump-sw") {
                pumpCfg.enabled = !pumpCfg.enabled;
                pumpMarkChips();
                pumpDebounce();
                return;
            }
            var mode = t.getAttribute("data-pumpmode");
            if (mode) {
                pumpCfg.mode = mode;
                pumpCfg.enabled = true;
                pumpRepaintNow();          // картина меняется сразу
                pumpDebounce();
                return;
            }
            var thr = t.getAttribute("data-pumpthr");
            if (thr !== null && thr !== undefined && thr !== "") {
                pumpCfg.threshold = Number(thr);
                var inp = $("pump-thr-in");
                if (inp) inp.value = pumpCfg.threshold;
                pumpRepaintNow();
                pumpDebounce();
                return;
            }
            var per = t.getAttribute("data-pumpper");
            if (per) {
                pumpCfg.period = per;
                pumpMarkChips();
                loadPumps(true);           // окно сигнала считает сервер
                pumpDebounce();
                return;
            }
            var cnd = t.getAttribute("data-pumpcn");
            if (cnd) {
                pumpCfg.candles = Number(cnd);
                pumpMarkChips();
                loadPumps(true);
                pumpDebounce();
                return;
            }
        });
        board.addEventListener("change", function (e) {
            var inp = e.target;
            if (!inp || inp.id !== "pump-thr-in") return;
            pumpCfg.threshold = Number(inp.value) || pumpCfg.threshold;
            pumpRepaintNow();
            pumpDebounce();
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
            var role = u.is_admin ? "★" : "";
            var email = u.email
                ? (esc(u.email) + (u.email_verified ? "" : " <span class='badge badge-warn'>не подтв.</span>"))
                : "<span class='meta'>—</span>";
            var tg = u.tg_linked
                ? (u.username ? "@" + esc(u.username) : "") + " <span class='meta mono'>" + u.tg_id + "</span>"
                : "<span class='meta'>не привязан</span>";
            var banBtn = u.is_banned
                ? '<button class="btn btn-ghost btn-small" data-ban="0" data-id="' + u.id + '">' + t("unban") + "</button>"
                : '<button class="btn btn-danger btn-small" data-ban="1" data-id="' + u.id + '">' + t("ban") + "</button>";
            return "<tr class='" + (u.is_banned ? "banned" : "") + "'>" +
                "<td>" + esc(u.display_name || "") + " " + role + "</td>" +
                "<td class='mono'>" + email + "</td>" +
                "<td class='mono'>" + tg + "</td>" +
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
                var du = d.users || {};
                var dv = d.visits || {};
                $("st-users") && ($("st-users").textContent = du.total);
                $("st-new") && ($("st-new").textContent = du.new_24h);
                $("st-views") && ($("st-views").textContent = dv.today_views);
                $("st-uniq") && ($("st-uniq").textContent = dv.today_uniques);
                $("st-ws") && ($("st-ws").textContent = d.ws_clients);
                $("st-bot") && ($("st-bot").textContent = d.bot.ready ? ("@" + d.bot.username) : "—");
                var live = (d.health.live_exchanges || []).length;
                $("st-exch") && ($("st-exch").textContent = live);
                renderBars(dv.days || []);
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
        bootDigestTpl();
        bootBotAdmin();
        bootAiPrompts();
    }

    /* ---------------- 🤖 ИИ-промты: шапка поста и дневной дайджест ---------- */

    var AI_PROMPTS = null;      // ответ /api/admin/ai/prompts

    function aiRow(r, kind) {
        var mark = r.custom
            ? '<span class="ai-badge ai-badge-own">свой</span>'
            : '<span class="ai-badge">стандарт</span>';
        return '<div class="ai-row" data-kind="' + esc(kind) + '" data-lang="' + esc(r.lang) + '">' +
            '<div class="ai-row-head"><b>' + esc((AI_PROMPTS.langs || {})[r.lang] || r.lang) + "</b>" +
            mark + '<span class="meta">настройка <code>' + esc(r.setting) + "</code></span></div>" +
            '<textarea class="ai-text" rows="7" spellcheck="false">' + esc(r.text || "") + "</textarea>" +
            '<div class="row-actions">' +
            '<button class="btn btn-primary" type="button" data-ai-save="' + esc(kind) +
            '" data-ai-lang="' + esc(r.lang) + '">Сохранить</button>' +
            '<button class="btn" type="button" data-ai-reset="' + esc(kind) +
            '" data-ai-lang="' + esc(r.lang) + '">Вернуть шаблон</button>' +
            '<span class="meta ai-status"></span></div></div>';
    }

    function renderAiPrompts(d) {
        var box = $("ai-prompt-blocks");
        if (!box || !d) return;
        AI_PROMPTS = d;
        box.innerHTML = (d.blocks || []).map(function (b) {
            return '<div class="ai-block"><h4>' + esc(b.title) + "</h4>" +
                (b.langs || []).map(function (r) { return aiRow(r, b.kind); }).join("") +
                "</div>";
        }).join("");
    }

    function loadAiPrompts() {
        api("/api/admin/ai/prompts").then(function (d) {
            if (d && d.ok) renderAiPrompts(d.prompts || {});
        });
    }

    function saveAiPrompt(kind, lang, text, reset) {
        var row = document.querySelector('#ai-prompt-blocks .ai-row[data-kind="' + kind +
                                          '"][data-lang="' + lang + '"]');
        var st = row ? row.querySelector(".ai-status") : null;
        if (st) st.textContent = "Сохраняю…";
        api("/api/admin/ai/prompts", {
            method: "POST",
            body: JSON.stringify({ kind: kind, lang: lang, text: reset ? "" : text }),
        }).then(function (d) {
            if (d && d.ok) {
                renderAiPrompts(d.prompts || {});
                var again = document.querySelector('#ai-prompt-blocks .ai-row[data-kind="' + kind +
                                                    '"][data-lang="' + lang + '"] .ai-status');
                if (again) again.textContent = d.message || "Сохранено.";
            } else if (st) {
                st.textContent = (d && (d.message || d.error)) || "ошибка";
            }
        }).catch(function () {
            if (st) st.textContent = "ошибка сети";
        });
    }

    function bootAiPrompts() {
        var box = $("ai-prompt-blocks");
        if (!box) return;
        loadAiPrompts();
        // Делегированный клик: разметку перерисовывает loadAiPrompts, поэтому
        // обработчики на кнопках не выживают — слушаем контейнер.
        box.addEventListener("click", function (e) {
            var t = e.target && e.target.closest ? e.target.closest("button") : null;
            if (!t) return;
            var row = t.closest(".ai-row");
            var ta = row ? row.querySelector(".ai-text") : null;
            if (t.getAttribute("data-ai-save")) {
                saveAiPrompt(t.getAttribute("data-ai-save"),
                             t.getAttribute("data-ai-lang"), ta ? ta.value : "", false);
            } else if (t.getAttribute("data-ai-reset")) {
                saveAiPrompt(t.getAttribute("data-ai-reset"),
                             t.getAttribute("data-ai-lang"), "", true);
            }
        });
    }

    /* ---------------- админка бота на сайте ---------------- */

    function botSet(id, text) {
        var el = $(id);
        if (el) el.textContent = text || "";
    }

    function botAgo(sec) {
        if (sec === null || sec === undefined || sec === "") return "—";
        var s = Number(sec);
        if (!isFinite(s)) return "—";
        if (s < 90) return Math.round(s) + " с назад";
        if (s < 5400) return Math.round(s / 60) + " мин назад";
        return Math.round(s / 3600) + " ч назад";
    }

    function botHealthState(state) {
        if (state === "ok") return "🟢";
        if (state === "dead") return "🟠";
        return "🔴";
    }

    function botChannelLine(code, probe, ch) {
        var mark = code === "en" ? "🇬🇧" : "🇷🇺";
        var id = (ch && ch.id) || (probe && probe.id) || "";
        var title = (ch && ch.title) || (probe && probe.title) || "—";
        var rights = "";
        if (probe && probe.can_post === true) rights = " · публикация разрешена";
        else if (probe && probe.can_post === false) rights = " · публикация запрещена";
        var src = ch && ch.source_label ? " · " + ch.source_label : "";
        var note = probe && probe.note ? " · " + probe.note : "";
        return mark + " <b>" + esc(title) + "</b> <code>" + esc(id || "—") + "</code>"
            + src + rights + note;
    }

    var BOT_SNAP = null;

    function renderBotAdmin(d) {
        BOT_SNAP = d;
        var b = d.bot || {};
        botSet("bot-line", b.line || "Бот не подключён");
        var ch = d.channels || {};
        if ($("bot-ru") && document.activeElement !== $("bot-ru")) {
            $("bot-ru").value = (ch.ru || {}).id || "";
        }
        if ($("bot-en") && document.activeElement !== $("bot-en")) {
            $("bot-en").value = (ch.en || {}).id || "";
        }
        var probeBox = $("bot-ch-probe");
        if (probeBox) {
            probeBox.innerHTML = d.probes
                ? "<li>" + botChannelLine("ru", d.probes.ru, ch.ru) + "</li>" +
                  "<li>" + botChannelLine("en", d.probes.en, ch.en) + "</li>"
                : "<li>🇷🇺 <b>" + esc((ch.ru || {}).title || "—") + "</b> <code>" +
                  esc((ch.ru || {}).id || "—") + "</code>" + ((ch.ru || {}).source_label ? " · " + (ch.ru || {}).source_label : "") + "</li>" +
                  "<li>🇬🇧 <b>" + esc((ch.en || {}).title || "—") + "</b> <code>" +
                  esc((ch.en || {}).id || "—") + "</code>" + ((ch.en || {}).source_label ? " · " + (ch.en || {}).source_label : "") + "</li>";
            if (ch.warn) probeBox.innerHTML += "<li class='meta'>⚠️ " + esc(ch.warn) + "</li>";
        }
        if ($("bot-review")) $("bot-review").checked = !!d.review;
        renderPostInterval(d.interval || {});
        var h = d.health || {};
        botSet("bot-health-line", "В эфире " + (h.live === undefined ? "—" : h.live) +
            "/" + (h.total || "—") + " · событий в памяти " + (h.events_total || 0) +
            " · зрителей WS " + (h.ws_clients || 0));
        var rows = $("bot-health");
        if (rows) {
            rows.innerHTML = (h.rows || []).map(function (r) {
                var note = r.error || (r.since === null || r.since === undefined
                    ? "" : ("последнее событие " + botAgo(r.since)));
                if (r.respawns) note += " (подъёмов сторожа: " + r.respawns + ")";
                return "<tr><td>" + botHealthState(r.state) + " " + esc(r.name) + "</td>" +
                    "<td>" + (r.state === "ok" ? "в эфире" : r.state === "dead"
                        ? "слушатель не запущен" : "нет связи") + "</td>" +
                    "<td>" + (r.events || 0) + "</td><td class='meta'>" + esc(note) + "</td></tr>";
            }).join("") || "<tr><td colspan='4' class='meta'>сервер ещё собирает источники</td></tr>";
        }
        var ai = d.ai || {};
        botSet("bot-ai-line", ai.enabled
            ? ("Шапка постов: включена · " + ((ai.providers || []).map(function (p) {
                return p.name;
            }).join(" → ") || "цепочка не готова") +
               (ai.last && ai.last.provider ? " · последняя: " + ai.last.provider +
                   (ai.last.ms ? " · " + ai.last.ms + " мс" : "") : ""))
            : "Шапка постов: выключена (нет ключей) — шапки из шаблонов");
        var dl = d.daily || {};
        var sch = dl.schedule || {};
        if ($("dig-hour")) $("dig-hour").value = sch.hour === undefined ? "" : sch.hour;
        if ($("dig-min")) $("dig-min").value = sch.minute === undefined ? "" : sch.minute;
        if ($("dig-jitter")) $("dig-jitter").value = sch.jitter_min === undefined ? "" : sch.jitter_min;
        if ($("dig-enabled")) $("dig-enabled").checked = !!sch.enabled;
        var locks = Object.keys(sch.env_locked || {});
        botSet("dig-note", locks.length
            ? "Часть настроек задана на сервере переменными окружения (" +
              locks.map(function (k) { return (sch.env_locked || {})[k]; }).join(", ") +
              ") — с сайта они не меняются."
            : "Время — по Москве. Разброс: выпуск уходит в пределах ±N минут.");
        var last = dl.last || {};
        var facts = last.facts || {};
        var sent = Object.keys(last.published || {}).map(function (k) {
            return (k === "en" ? "🇬🇧 EN" : "🇷🇺 RU");
        }).join(", ");
        botSet("bot-daily-line", last.day
            ? (last.day + " · $" + usd(facts.liq_total_usd) + " · " +
               (facts.liq_count || 0) + " ликвидаций" +
               (sent ? " · отправлен: " + sent : " · ещё не отправлялся"))
            : "Выпусков пока нет — появится после первого вечернего запуска.");
    }

    function renderBotProbes(probes) {
        var box = $("bot-ch-probe");
        if (!box || !probes) return;
        var ch = (BOT_SNAP || {}).channels || {};
        box.innerHTML = "<li>" + botChannelLine("ru", probes.ru, ch.ru) + "</li>" +
            "<li>" + botChannelLine("en", probes.en, ch.en) + "</li>";
    }

    // Частота сводки: раз в N часов. Окно поста — эти же N часов, блок анализа
    // внутри поста — четверть окна. Кнопки применяются сразу: админ не должен
    // ждать ночного цикла публикации, чтобы проверить новую частоту.
    var POST_INT = { hours: 4, min: 1, max: 10, block: "" };

    function markPostIntChips(hours) {
        // Подсвечиваем выбор в тех же кнопках, что уже на странице: перерисовка
        // списка на каждый клик выглядела бы как «кнопка не нажалась».
        var box = $("post-int-btns");
        if (!box) return;
        Array.prototype.forEach.call(box.querySelectorAll("[data-pi]"), function (btn) {
            var h = Number(btn.getAttribute("data-pi")) || 0;
            var on = h === Number(hours);
            btn.className = "al-chip" + (on ? " on" : "");
            btn.textContent = (on ? "✓ " : "") + h + " ч";
        });
    }

    function renderPostInterval(info, busy) {
        POST_INT = Object.assign({}, POST_INT, info || {});
        var box = $("post-int-btns");
        if (box) {
            var n = Number(POST_INT.hours) || 4;
            var min = Number(POST_INT.min) || 1, max = Number(POST_INT.max) || 10;
            var chips = "";
            for (var h = min; h <= max; h++) {
                chips += '<button type="button" class="al-chip' + (h === n ? " on" : "") +
                    '" data-pi="' + h + '">' + (h === n ? "✓ " : "") + h + " ч</button>";
            }
            box.innerHTML = chips;
            Array.prototype.forEach.call(box.querySelectorAll("[data-pi]"), function (btn) {
                btn.addEventListener("click", function () {
                    var hours = Number(btn.getAttribute("data-pi")) || 4;
                    markPostIntChips(hours);            // сразу подсветили выбор
                    botSet("post-int-note", "Сохраняю частоту…");
                    api("/api/admin/bot/interval", {
                        method: "POST", body: JSON.stringify({ hours: hours }),
                    }).then(function (d) {
                        if (d && d.ok) {
                            renderPostInterval(d.interval || {});
                            botSet("post-int-note", d.message || "Частота применена.");
                        } else {
                            markPostIntChips(POST_INT.hours);
                            botSet("post-int-note", (d && (d.message || d.error)) || "ошибка");
                        }
                    });
                });
            });
        }
        var text = POST_INT.text
            ? ("Сейчас: " + POST_INT.text + ".")
            : ("Сейчас: раз в " + (POST_INT.hours || 4) + " ч.");
        if (!busy) botSet("post-int-note", text);
    }

    function loadBotAdmin() {
        return api("/api/admin/bot").then(function (d) {
            if (!d.ok) {
                botSet("bot-line", d.error === "no_bot" ? "Бот не подключён" : (d.error || "ошибка"));
                return d;
            }
            renderBotAdmin(d);
            return d;
        });
    }

    function bootBotAdmin() {
        if (!$("bot-admin")) return;
        loadBotAdmin();
        var refresh = $("bot-refresh");
        if (refresh) refresh.addEventListener("click", loadBotAdmin);
        var rev = $("bot-review");
        if (rev) rev.addEventListener("change", function () {
            botSet("bot-post-status", "Сохраняю…");
            api("/api/admin/bot/review", {
                method: "POST", body: JSON.stringify({ on: rev.checked }),
            }).then(function (d) {
                botSet("bot-post-status", d.ok ? (d.message || "готово") : (d.message || d.error || "ошибка"));
            });
        });
        var save = $("bot-ch-save");
        if (save) save.addEventListener("click", function () {
            botSet("bot-ch-status", "Проверяю каналы в Telegram…");
            api("/api/admin/bot/channels", {
                method: "POST",
                body: JSON.stringify({
                    ru: ($("bot-ru") || {}).value || "",
                    en: ($("bot-en") || {}).value || "",
                }),
            }).then(function (d) {
                botSet("bot-ch-status", d.ok ? "Каналы сохранены." : (d.message || d.error || "ошибка"));
                if (d.ok) loadBotAdmin().then(function () { renderBotProbes(d.probes); });
            });
        });
        var swap = $("bot-ch-swap");
        if (swap) swap.addEventListener("click", function () {
            api("/api/admin/bot/channels/swap", { method: "POST", body: "{}" }).then(function (d) {
                botSet("bot-ch-status", d.ok ? "Каналы поменялись местами." : (d.message || d.error || "ошибка"));
                if (d.ok) loadBotAdmin();
            });
        });
        var verify = $("bot-ch-verify");
        if (verify) verify.addEventListener("click", function () {
            botSet("bot-ch-status", "Сверяю с Telegram…");
            api("/api/admin/bot/channels/verify", { method: "POST", body: "{}" }).then(function (d) {
                botSet("bot-ch-status", d.ok ? (d.message || "Роли сверены.") : (d.message || d.error || "ошибка"));
                if (d.ok) loadBotAdmin().then(function () { renderBotProbes(d.probes); });
            });
        });
        function publish(kind, langs) {
            botSet("bot-post-status", kind === "daily" ? "Собираю дайджест за сутки…" : "Готовлю сводку…");
            api("/api/admin/bot/publish", {
                method: "POST", body: JSON.stringify({ kind: kind, langs: langs }),
            }).then(function (d) {
                botSet("bot-post-status", d.message || d.error || "ошибка");
            });
        }
        var post = $("bot-post");
        if (post) post.addEventListener("click", function () { publish("channel"); });
        var daily = $("bot-daily");
        if (daily) daily.addEventListener("click", function () {
            if (confirm("Собрать дневной дайджест за сутки и отправить в каналы?")) publish("daily");
        });
        var dsave = $("dig-save");
        if (dsave) dsave.addEventListener("click", function () {
            api("/api/digest/settings", {
                method: "POST",
                body: JSON.stringify({
                    hour: ($("dig-hour") || {}).value,
                    minute: ($("dig-min") || {}).value,
                    jitter_min: ($("dig-jitter") || {}).value,
                    enabled: !!($("dig-enabled") || {}).checked,
                }),
            }).then(function (d) {
                botSet("dig-status", d.ok
                    ? ("Сохранено: " + (d.schedule ? d.schedule.hour + ":" +
                        String(d.schedule.minute).padStart(2, "0") : "") +
                       (d.schedule && !d.schedule.enabled ? " (авто выключено)" : ""))
                    : (d.message || d.error || "ошибка"));
                if (d.ok) loadBotAdmin();
            });
        });
        var aiBtn = $("bot-ai-check");
        if (aiBtn) aiBtn.addEventListener("click", function () {
            botSet("bot-ai-head", "Прошу ИИ написать шапку…");
            api("/api/admin/bot/ai-check", { method: "POST", body: JSON.stringify({ lang: "ru" }) })
                .then(function (d) {
                    if (!d.ok) { botSet("bot-ai-head", d.message || d.error || "ошибка"); return; }
                    botSet("bot-ai-head", (d.note ? d.note + " — " : "") +
                        (d.head || "ИИ не ответил, шапка будет из шаблонов"));
                });
        });
    }

    function esc(s) {
        return String(s || "").replace(/[&<>"]/g, function (c) {
            return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
        });
    }

    // Фото канала по рубрикам: сводка раз в N часов ("post") и вечерний
    // дайджест ("digest"). Кнопка ⇄ переносит фото в другую рубрику — так
    // админ раскладывает картинки между постами, не перезагружая их.
    var TPL_KINDS = [
        { key: "post", box: "tpl-photos", input: "tpl-photo-in", status: "tpl-photo-status" },
        { key: "digest", box: "tpl-photos-digest", input: "tpl-photo-in-digest",
          status: "tpl-photo-status-digest" },
    ];

    function tplPhotoTile(p, kind) {
        var other = kind === "post" ? "digest" : "post";
        return '<div class="tpl-photo"><img alt="" src="' + esc(p.url) + '">' +
            '<button type="button" class="btn btn-danger btn-small tpl-del" data-pid="' + p.id +
            '" title="Удалить фото">✕</button>' +
            '<button type="button" class="btn btn-small tpl-move" data-move="' + p.id +
            '" data-to="' + other + '" title="Перенести в другую рубрику">⇄</button></div>';
    }

    function renderDigestPhotos(d) {
        var all = d.photos || [];
        var byKind = d.photos_by_kind || {};
        if (!d.photos_by_kind) {
            // старый ответ сервера: рубрик нет — всё считаем постовыми фото
            byKind = { post: all, digest: [] };
        }
        TPL_KINDS.forEach(function (k) {
            var box = $(k.box);
            if (!box) return;
            var list = (byKind[k.key] || []).filter(function (p) { return p.exists !== false; });
            var empty = k.key === "post"
                ? (d.using_default_photos
                    ? "<p class='lead'>В постах картинки из комплекта. Загрузите свои — набор заменится.</p>"
                    : "<p class='lead'>Фото нет: пост уйдёт текстом.</p>")
                : "<p class='lead'>Пусто: дайджест возьмёт фото постов.</p>";
            box.innerHTML = list.map(function (p) { return tplPhotoTile(p, k.key); }).join("") || empty;
            Array.prototype.forEach.call(box.querySelectorAll("[data-pid]"), function (btn) {
                btn.addEventListener("click", function () {
                    api("/api/admin/digest/photos/" + btn.getAttribute("data-pid") + "/delete", {
                        method: "POST", body: "{}",
                    }).then(loadDigestTpl);
                });
            });
            Array.prototype.forEach.call(box.querySelectorAll("[data-move]"), function (btn) {
                btn.addEventListener("click", function () {
                    api("/api/admin/digest/photos/" + btn.getAttribute("data-move") + "/kind", {
                        method: "POST", body: JSON.stringify({ kind: btn.getAttribute("data-to") }),
                    }).then(loadDigestTpl);
                });
            });
        });
    }

    function loadDigestTpl() {
        api("/api/admin/digest").then(function (d) {
            if (!d.ok) return;
            var ul = $("tpl-heads");
            if (ul) {
                var note = d.using_default_heads
                    ? "<li class='meta'>В постах пока дефолтные шапки. Добавьте свои — дефолт отключится.</li>"
                    : "";
                ul.innerHTML = note + (d.heads || []).map(function (h) {
                    return "<li><span>" + esc(h.text) + "</span>" +
                        '<button class="btn btn-danger btn-small" data-hid="' + h.id + '">удалить</button></li>';
                }).join("");
                ul.querySelectorAll("button[data-hid]").forEach(function (btn) {
                    btn.addEventListener("click", function () {
                        api("/api/admin/digest/heads/" + btn.getAttribute("data-hid") + "/delete", {
                            method: "POST", body: "{}",
                        }).then(loadDigestTpl);
                    });
                });
            }
            renderDigestPhotos(d);
        });
    }

    function bootDigestTpl() {
        if (!$("tpl-heads") && !$("tpl-photos") && !$("tpl-photos-digest")) return;
        loadDigestTpl();
        var add = $("tpl-head-add");
        if (add) add.addEventListener("click", function () {
            var ta = $("tpl-head-in");
            var st = $("tpl-head-status");
            api("/api/admin/digest/heads", {
                method: "POST",
                body: JSON.stringify({ text: (ta && ta.value) || "" }),
            }).then(function (d) {
                if (st) st.textContent = d.ok ? t("saved") : (d.error || "error");
                if (d.ok && ta) ta.value = "";
                loadDigestTpl();
            });
        });
        TPL_KINDS.forEach(function (k) {
            var inp = $(k.input);
            if (!inp) return;
            inp.addEventListener("change", function () {
                var f = inp.files && inp.files[0];
                var st = $(k.status);
                if (!f) return;
                if (f.size > 12 * 1000 * 1000) {
                    if (st) st.textContent = "файл больше 12 МБ";
                    inp.value = "";
                    return;
                }
                if (st) st.textContent = "загрузка…";
                uploadDigestPhoto(f, k.key).then(function (d) {
                    if (st) st.textContent = d.ok ? t("saved") : (d.hint || d.error || "ошибка загрузки");
                    inp.value = "";
                    loadDigestTpl();
                }).catch(function () {
                    if (st) st.textContent = "ошибка сети";
                    inp.value = "";
                });
            });
        });
    }

    function uploadDigestPhoto(file, kind) {
        var fd = new FormData();
        fd.append("file", file, file.name || "photo.jpg");
        return fetch("/api/admin/digest/photos?kind=" + encodeURIComponent(kind || "post"), {
            method: "POST",
            body: fd,
            credentials: "same-origin",
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (d) {
                d = d || {};
                d._status = r.status;
                if (d.ok === undefined && !r.ok) d.error = "http_" + r.status;
                return d;
            });
        });
    }

    /* ---------- login: почта как основной вход ---------- */
    var authTab = "login";

    function setStatus(el, text, kind) {
        if (!el) return;
        el.className = "login-status" + (kind ? " " + kind : "");
        el.textContent = text || "";
    }

    var captchaToken = "";

    function showCaptcha(data) {
        // Подпись и сам пример — отдельные узлы: «Другой пример» просто
        // заменяет числа, не пересобирая поле.
        var q = $("captcha-question"), label = $("captcha-label"), inp = $("in-captcha");
        if (data && data.token && data.question) {
            captchaToken = data.token;
            if (q) q.textContent = data.question;
            if (label) label.textContent = t("captchaLead");
            if (inp) inp.value = "";
            return true;
        }
        captchaToken = "";
        if (q) q.textContent = "…";
        if (label) label.textContent = t("captchaLoading");
        return false;
    }

    function loadCaptcha() {
        showCaptcha(null);
        return api("/api/auth/captcha").then(function (d) {
            showCaptcha(d && d.ok !== false ? d : null);
            return captchaToken;
        }).catch(function () { showCaptcha(null); return ""; });
    }

    function paintAuthTab(tab) {
        authTab = tab || "login";
        var isLogin = authTab === "login";
        var isReg = authTab === "register";
        var isLink = authTab === "link";
        document.querySelectorAll(".auth-tab").forEach(function (b) {
            b.classList.toggle("active", b.getAttribute("data-tab") === authTab);
        });
        var title = $("auth-title"), lead = $("auth-lead");
        if (title) title.textContent = t(isReg ? "authTitleRegister" : (isLink ? "authTitleLink" : "authTitleLogin"));
        if (lead) lead.textContent = t(isReg ? "authLeadRegister" : (isLink ? "authLeadLink" : "authLeadLogin"));
        var fName = $("field-name"), fPass = $("field-pass"), sub = $("email-submit");
        var fCap = $("field-captcha");
        if (fName) fName.classList.toggle("hidden", !isReg);
        if (fPass) fPass.classList.toggle("hidden", isLink);
        if (fCap) fCap.classList.toggle("hidden", !isReg);
        if (isReg) loadCaptcha();
        else if ($("in-captcha")) $("in-captcha").value = "";
        var pass = $("in-password");
        if (pass) pass.setAttribute("autocomplete", isReg ? "new-password" : "current-password");
        if (sub) sub.textContent = t(isReg ? "submitRegister" : (isLink ? "submitLink" : "submitLogin"));
        try { localStorage.setItem("liqscope.auth.tab", authTab); } catch (e) {}
        setStatus($("login-status"), "");
    }

    function authError(d) {
        var code = d && d.error;
        if (d && d.hint) return d.hint;
        if (code === "email_unverified") return t("verifyFirst");
        if (code === "short") return t("weakShort");
        if (code === "weak") return t("weakSimple");
        if (code === "bad_email") return t("needEmail");
        if (code === "mail_failed") return t("mailOff");
        if (code === "rate") return t("verifySent");
        return code || "error";
    }

    function submitEmailForm() {
        var email = (($("in-email") || {}).value || "").trim();
        var pass = ($("in-password") || {}).value || "";
        var name = (($("in-name") || {}).value || "").trim();
        var status = $("login-status");
        var next = new URLSearchParams(location.search).get("next") || "/cabinet";
        var answer = (($("in-captcha") || {}).value || "").trim();
        if (!email || email.indexOf("@") < 0) { setStatus(status, t("needEmail"), "err"); return; }
        if (authTab !== "link" && !pass) { setStatus(status, t("needPass"), "err"); return; }
        if (authTab === "register" && pass.length < 8) { setStatus(status, t("weakShort"), "err"); return; }
        if (authTab === "register") {
            if (!captchaToken) {
                // пример ещё не приехал — просим подождать и тянем заново
                loadCaptcha();
                setStatus(status, t("captchaNeed"), "err");
                return;
            }
            if (!answer) { setStatus(status, t("captchaNeed"), "err"); return; }
        }
        var body = { email: email, password: pass, name: name, language: lang() };
        if (authTab === "register") { body.captcha = captchaToken; body.answer = answer; }
        var path = authTab === "register" ? "/api/auth/email/register"
            : (authTab === "link" ? "/api/auth/email/link" : "/api/auth/email/login");
        setStatus(status, "…");
        api(path, { method: "POST", body: JSON.stringify(body) }).then(function (d) {
            if (d.ok && d.user) { location.href = next; return; }
            if (d.ok && d.exists) {
                // адрес уже подтверждён: отправляем на вход/восстановление
                setStatus(status, d.hint || t("verifyUsed"), "err");
                return;
            }
            if (d.ok && (authTab === "register" || authTab === "link")) {
                if (d.sent === false) {
                    setStatus(status, t("mailOff"), "err");
                    return;
                }
                setStatus(status, t(authTab === "register" ? "registerSent" : "linkSent",
                    { email: email }), "ok");
                return;
            }
            if (String(d.error || "").indexOf("captcha") === 0) {
                // Сервер уже прислал новый пример — подставляем его
                if (d.captcha && d.captcha.token) showCaptcha(d.captcha);
                else loadCaptcha();
                var capErr = d.hint || (d.error === "captcha_missing"
                    ? t("captchaNeed") : t("captchaWrong"));
                setStatus(status, capErr, "err");
                return;
            }
            setStatus(status, authError(d), "err");
        }).catch(function () { setStatus(status, "network", "err"); });
    }

    function resendVerify() {
        var email = (($("in-email") || {}).value || "").trim();
        var status = $("login-status");
        if (!email || email.indexOf("@") < 0) { setStatus(status, t("needEmail"), "err"); return; }
        api("/api/auth/email/resend", {
            method: "POST", body: JSON.stringify({ email: email, language: lang() }),
        }).then(function (d) {
            setStatus(status, d.ok ? t("verifySent") : authError(d), d.ok ? "ok" : "err");
        });
    }

    function statusFromQuery(me) {
        var q = new URLSearchParams(location.search);
        var status = $("login-status");
        var verify = q.get("verify");
        if (verify) {
            var map = { used: "verifyUsed", expired: "verifyExpired",
                        bad: "verifyBad", unknown: "verifyBad" };
            setStatus(status, t(map[verify] || "verifyBad"), "err");
            return;
        }
        if (q.get("link")) { setStatus(status, t("linkBad"), "err"); return; }
        var mail = q.get("mail");
        if (mail) {
            var mmap = { used: "attachUsed", expired: "attachExpired",
                         unknown: "attachBad", blocked: "attachBlocked" };
            setStatus(status, t(mmap[mail] || "attachBad"), "err");
            return;
        }
        if (q.get("banned")) { setStatus(status, t("banned"), "err"); return; }
        if (me && !me.mail_enabled) setStatus(status, t("mailOff"), "err");
    }

    function bootLogin() {
        paintAuthTab("login");
        // если пришли по «Забыли пароль?» или по письму — открываем нужную вкладку
        var q = new URLSearchParams(location.search);
        var saved = "";
        try { saved = localStorage.getItem("liqscope.auth.tab") || ""; } catch (e) { saved = ""; }
        if (q.get("mode") === "register" || q.get("mode") === "link") paintAuthTab(q.get("mode"));
        else if (saved === "register" || saved === "link") paintAuthTab(saved);

        document.querySelectorAll(".auth-tab").forEach(function (b) {
            b.addEventListener("click", function () { paintAuthTab(b.getAttribute("data-tab")); });
        });
        var form = $("email-form");
        if (form) form.addEventListener("submit", function (e) { e.preventDefault(); submitEmailForm(); });
        var resend = $("resend-verify");
        if (resend) resend.addEventListener("click", resendVerify);
        var capBtn = $("captcha-refresh");
        if (capBtn) {
            capBtn.textContent = t("captchaRefresh");
            capBtn.addEventListener("click", function () { loadCaptcha(); });
        }
        var forgot = $("to-reset");
        if (forgot) forgot.addEventListener("click", function () {
            var email = (($("in-email") || {}).value || "").trim();
            var status = $("login-status");
            if (!email || email.indexOf("@") < 0) { setStatus(status, t("needEmail"), "err"); return; }
            api("/api/auth/email/reset", {
                method: "POST", body: JSON.stringify({ email: email, language: lang() }),
            }).then(function (d) {
                setStatus(status, d.ok ? t("linkSent") : authError(d), d.ok ? "ok" : "err");
            });
        });

        api("/api/auth/me").then(function (me) {
            paintNav(me.user);
            if (me.user) { location.href = "/cabinet"; return; }
            statusFromQuery(me);
            // Telegram остаётся запасным входом: ссылка на бота или виджет
            var alt = $("tg-alt");
            if (alt && !me.bot_ready) alt.classList.add("hidden");
            var widget = $("tg-widget");
            if (widget && me.bot_username) {
                var sc = document.createElement("script");
                sc.async = true;
                sc.src = "https://telegram.org/js/telegram-widget.js?22";
                sc.setAttribute("data-telegram-login", me.bot_username);
                sc.setAttribute("data-size", "large");
                sc.setAttribute("data-radius", "8");
                sc.setAttribute("data-request-access", "write");
                sc.setAttribute("data-onauth", "onTelegramAuth(user)");
                widget.appendChild(sc);
            }
        });
        var btn = $("login-btn");
        if (btn) btn.addEventListener("click", startLogin);
    }

    /* ---------- reset: новый пароль по ссылке из письма ---------- */
    function bootReset() {
        var token = new URLSearchParams(location.search).get("token") || "";
        var status = $("reset-status");
        var form = $("reset-form");
        api("/api/auth/me").then(function (me) { paintNav(me.user); });
        api("/api/auth/email/token?token=" + encodeURIComponent(token)).then(function (d) {
            if (!d.ok) {
                setStatus(status, t("resetBad"), "err");
                if (form) form.classList.add("hidden");
                return;
            }
            if ($("reset-email")) $("reset-email").value = d.email || "";
        });
        if (form) form.addEventListener("submit", function (e) {
            e.preventDefault();
            var p1 = ($("reset-pass") || {}).value || "";
            var p2 = ($("reset-pass2") || {}).value || "";
            if (p1.length < 8) { setStatus(status, t("weakShort"), "err"); return; }
            if (p1 !== p2) { setStatus(status, t("resetMismatch"), "err"); return; }
            setStatus(status, "…");
            api("/api/auth/email/set-password", {
                method: "POST",
                body: JSON.stringify({ token: token, password: p1,
                                       email: ($("reset-email") || {}).value || "",
                                       language: lang() }),
            }).then(function (d) {
                if (d.ok) { setStatus(status, t("resetOk"), "ok"); location.href = "/cabinet"; return; }
                setStatus(status, authError(d), "err");
            }).catch(function () { setStatus(status, "network", "err"); });
        });
    }

    function boot() {
        var page = (document.body && document.body.getAttribute("data-page")) || "";
        if (page === "login") bootLogin();
        else if (page === "reset") bootReset();
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
