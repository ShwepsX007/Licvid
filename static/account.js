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
            digest: "Дайджест",
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
            digest: "Digest",
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
        // Дайджест — из любой страницы в шапке: раньше в него можно было
        // попасть только кнопкой внутри сервиса или через логотип и главную
        if (path !== "/digest") {
            html += '<a class="btn btn-ghost btn-compact" href="/digest">📰 ' +
                t("digest") + "</a>";
        }
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
        bootFeedbackUser();
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
    var alHist = [];            // сигналы метрик: их присылает сервер

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
    function alWinOf(m) {
        var w = alCfg && alCfg.windows;
        var v = w && w[m];
        if (!v) v = (alCfg && alCfg.window_min) || 5;
        return Number(v) || 5;
    }
    function alCoinOf(m) {
        /* Монета метрики: у ликвидаций, CVD и OI она своя. Старое поле
           alCfg.symbol читаем как «одна монета на все метрики». */
        var coins = alCfg && alCfg.coins;
        var v = coins && coins[m];
        if (!v) v = (alCfg && alCfg.symbol) || "ALL";
        return String(v);
    }
    function alCoinLabel(v) {
        return String(v) === "ALL" ? "все" : String(v).split("_")[0];
    }
    function alCoinInput(raw) {
        /* «btc» → «BTC_USDT»: пишем так, как хранит сервер, чтобы подпись и
           чипы совпадали с ответом и человек видел итог сразу. */
        var v = String(raw == null ? "" : raw).trim().toUpperCase()
            .replace(/\s+/g, "").replace("-", "_").replace("/", "_");
        if (!v) return "ALL";
        if (v === "ALL" || v === "*" || v === "ВСЕ") return "ALL";
        return v.indexOf("_") === -1 ? v + "_USDT" : v;
    }
    function alCoinChips(key, list) {
        list = (list || []).slice();
        var cur = alCoinOf(key);
        if (!list.some(function (v) { return String(v) === cur; })) list.unshift(cur);
        return list.map(function (v) {
            return alChip(String(v) === cur,
                'data-coinval="' + v + '" data-coinmetric="' + key + '"', alCoinLabel(v));
        }).join("");
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
            if (d.history) alHist = d.history;
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
                st.textContent = d.ok ? ("сохранено · монеты: " +
                    alCoinLabel(alCoinOf("liq")) + " / " + alCoinLabel(alCoinOf("cvd")) +
                    " / " + alCoinLabel(alCoinOf("oi"))) : (d.error || "ошибка");
            }
            if (d.live) paintAlertsLive({ live: d.live, history: alHist, config: alCfg });
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
        /* Микрографик метрики. Первый кадр берём у сервера (накопительная
           кривая окна), дальше ведём свой буфер: на каждом опросе дописываем
           итог окна. Так линия двигается даже между редкими бакетами CVD/OI —
           раньше серверная «гребёнка» не менялась, и график стоял картинкой. */
        var buf = alSparkBuf[key] || [];
        var src = (row && row.spark && row.spark.length) ? row.spark : null;
        if (!buf.length && src) { alSparkBuf[key] = src.slice(-36); return; }
        var v = Number(row && (row.total !== undefined ? row.total : row.value)) || 0;
        buf.push(v);
        if (buf.length > 36) buf = buf.slice(-36);
        alSparkBuf[key] = buf;
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
        var hist = d.history || alHist || [];
        alHist = hist;
        var symbols = d.symbols || [];
        var p = alPresets || { windows: [1, 5, 15, 30, 60, 240],
            thresholds: [50000, 100000, 250000, 500000, 1000000, 5000000],
            min_event: [0, 10000, 25000, 50000, 100000],
            coins: ["ALL", "BTC_USDT", "ETH_USDT", "SOL_USDT"] };
        function chips(list, cur, attr, fmt) {
            list = (list || []).slice();
            // текущее значение показываем всегда: у ликвидаций окно 5м, а
            // среди пресетов её нет — иначе ни один чип не подсвечен
            if (cur !== undefined && cur !== null && cur !== "" &&
                !list.some(function (v) { return String(v) === String(cur); })) {
                list.unshift(cur);
            }
            return list.map(function (v) {
                return alChip(String(v) === String(cur), attr + '="' + v + '"', fmt ? fmt(v) : String(v));
            }).join("");
        }
        /* Метрика — горизонтальная строка во всю ширину, как у корреляций:
           слева значение с микрографиком, посередине настройки сигнала
           (окно, порог, монета), справа живая лента потока. */
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
            return '<section class="al-feed' + (on ? " on" : "") + (hot ? " hot" : "") +
                '" data-feed="' + key + '">' +
                '<div class="al-feed-h">' +
                '<label class="al-switch' + (on ? " on" : "") + '" data-metric="' + key + '">' +
                "<i></i><span>" + title + (on ? " · ON" : " · выкл") + "</span></label>" +
                '<span class="al-feed-meta" data-feedmeta="' + key + '">' +
                esc(alFeedMeta(key, row, extra, thr)) + "</span></div>" +
                '<div class="al-feed-grid">' +
                '<div class="al-feed-col al-col-now">' +
                alSparkSvg(alSparkBuf[key]) +
                '<div class="al-meter' + (hot ? " hot" : "") + '" data-metric="' + key + '">' +
                '<div class="v ' + cls + '">' + alMoney(val) + '</div>' +
                '<div class="s">' + extra + " / порог " + alMoney(thr) +
                '</div><div class="al-bar"><i style="width:' + pct + '%"></i></div></div>' +
                "</div>" +
                '<div class="al-feed-col al-col-set">' +
                '<div class="al-label" data-winlabel="' + key + '">Окно · ' + alWin(alWinOf(key)) +
                '</div><div class="al-chips" data-winbox="' + key + '">' +
                chips(p.windows || [], alWinOf(key), "data-win", alWin) + "</div>" +
                '<div class="al-row"><input data-winin="' + key +
                '" type="number" min="1" max="1440" placeholder="минуты" value="' +
                alWinOf(key) + '"></div>' +
                '<div class="al-label">Порог</div>' +
                '<div class="al-chips" data-thr="' + key + '">' +
                chips(alThrList(key), thr, "data-thrval", alMoney) + "</div>" +
                '<div class="al-row"><input data-thrin="' + key +
                '" type="number" min="0" step="1000" value="' + Math.round(thr || 0) + '"></div>' +
                '<div class="al-label">Монета · <span data-coinlabel="' +
                key + '">' + esc(alCoinLabel(alCoinOf(key))) + "</span></div>" +
                '<div class="al-chips" data-coinbox="' + key + '">' +
                alCoinChips(key, p.coins || []) + "</div>" +
                '<div class="al-row"><input data-coinin="' + key +
                '" type="text" list="al-sym-list" placeholder="BTC_USDT или тикер" value="' +
                (alCoinOf(key) === "ALL" ? "" : alCoinOf(key)) + '"></div>' +
                "</div>" +
                '<div class="al-feed-col al-col-tape">' +
                '<div class="al-label">Лента · <span data-flowmeta="' + key + '">' +
                esc(alFlowLabel(row)) + '</span></div>' +
                '<div class="al-tape" id="al-tape-' + key + '">' +
                alTape(hist, row.flow, key) + "</div>" +
                "</div></div></section>";
        }
        board.innerHTML =
            '<div class="al-head"><div><h3>Алерты по объёму</h3>' +
            '<div class="al-sub">Включайте ликвидации, CVD и OI по отдельности — можно слушать один источник или два. Порог CVD/OI крупнее, чем у ликвидаций, и окно у каждой метрики своё. После сигнала окно начинается заново: в следующее сообщение попадут только новые данные.</div></div>' +
            '<label class="al-switch' + (alCfg.enabled ? " on" : "") + '" id="al-sw">' +
            "<i></i><span>" + (alCfg.enabled ? "СИГНАЛ ВКЛ" : "СИГНАЛ ВЫКЛ") + "</span></label></div>" +
            '<datalist id="al-sym-list">' +
            (symbols || []).map(function (s) { return "<option value=\"" + s + "\">"; }).join("") +
            "</datalist>" +
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
            '<div class="al-status" id="al-status">монеты: LIQ ' + alCoinLabel(alCoinOf("liq")) +
            " · CVD " + alCoinLabel(alCoinOf("cvd")) +
            " · OI " + alCoinLabel(alCoinOf("oi")) +
            " · окна: LIQ " + alWin(alWinOf("liq")) +
            " · CVD " + alWin(alWinOf("cvd")) +
            " · OI " + alWin(alWinOf("oi")) + "</div>";
        if (bind) bindAlerts();
    }

    /* Сводка строки в заголовке: значение метрики, её порог, окно и монета. */
    function alFeedMeta(key, row, extra, thr) {
        var title = key.toUpperCase();
        var val = alMoney((row && row.value) || 0);
        return title + " " + val +
            (extra ? " · " + extra : "") +
            " · порог " + alMoney(thr) +
            " · окно " + alWin(alWinOf(key)) +
            " · монета " + alCoinLabel(alCoinOf(key));
    }

    /* Подпись ленты: сколько событий потока видно в окне. */
    function alFlowLabel(row) {
        var n = ((row && row.flow) || []).length;
        return n ? "поток окна · " + n + " событий" : "поток окна";
    }

    function alFlowRow(f) {
        /* Строка живого потока: время · монета · сумма · обстоятельства.
           Это не сигнал, а то, что происходит в окне прямо сейчас, — иначе
           между редкими сигналами лента выглядела мёртвой. */
        var ts = f.ts ? new Date(Number(f.ts) * 1000) : null;
        var tm = ts ? ts.toLocaleTimeString([], {
            hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
        var sym = String(f.symbol || "").split("_")[0];
        var val = Number(f.value) || 0;
        var cls = val >= 0 ? "pos" : "neg";
        var side = String(f.side || "");
        var what = side === "LONG" ? "лонг" : side === "SHORT" ? "шорт" : "";
        var bmin = Number(f.bucket_min) || 0;
        var bsec = Number(f.bucket_sec) || 0;
        var span = bsec ? "за " + bsec + "с" : (bmin ? "за " + alWin(bmin) : "");
        var extra = [what, span,
                     f.exchange ? String(f.exchange) : ""].filter(Boolean).join(" · ");
        return '<div class="row flow"><span class="t">' + tm + "</span>" +
            '<span class="m">' + (cls === "pos" ? "🟢" : "🔴") + "</span>" +
            '<span class="sym">' + esc(sym || "все") + "</span>" +
            '<span class="val ' + cls + '">' + alMoney(Math.abs(val)) + "</span>" +
            '<span class="thr">' + esc(extra) + "</span></div>";
    }

    function alTape(hist, flow, metric) {
        var rows = (hist || []).filter(function (h) {
            return !metric || String(h.metric || "") === metric;
        });
        // ярко, как лента терминала: время · монета · сумма · порог/окно,
        // цвет по метрике/знаку, полоса накачки |значения| к порогу
        var sig = rows.map(function (h) {
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
                '<span class="thr">/ ' + alMoney(thr) + " · " + alWin(h.window_min) +
                (h.detail && h.detail.span_min ? " · новых " + alWin(h.detail.span_min) : "") +
                "</span>" +
                '<i class="tape-bar"></i></div>';
        }).join("");
        // Ниже сигналов — живой поток метрики: сигналы редкие, а поток идёт,
        // и лента должна это показывать (иначе «пока тихо» на всех метриках).
        var fl = (flow || []).map(alFlowRow).join("");
        if (!sig && !fl) return '<div class="s">пока тихо</div>';
        return (sig ? '<div class="tape-sig">🔔 сигналы</div>' + sig : "") +
            (fl ? '<div class="tape-sig">поток окна</div>' + fl : "");
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
            var extra = "";
            if (key === "liq" && row.market) extra = "рынок " + alMoney(row.market.usd);
            else extra = (row.symbol && row.symbol !== "ALL"
                ? String(row.symbol).split("_")[0] : "") +
                (row.count ? " · " + row.count + " шт." : "");
            if (feed) {
                feed.classList.toggle("on", alHas(key));
                feed.classList.toggle("hot", hot);
                var spark = feed.querySelector(".al-spark-wrap") || feed.querySelector(".al-spark");
                if (spark) {
                    var wrap = document.createElement("div");
                    wrap.innerHTML = alSparkSvg(alSparkBuf[key]);
                    spark.replaceWith(wrap.firstChild);
                }
                var meta = feed.querySelector('[data-feedmeta="' + key + '"]');
                if (meta) meta.textContent = alFeedMeta(key, row, extra, thr);
                var flowMeta = feed.querySelector('[data-flowmeta="' + key + '"]');
                if (flowMeta) flowMeta.textContent = alFlowLabel(row);
            }
            if (el) {
                el.classList.toggle("hot", hot);
                var v = el.querySelector(".v");
                if (v) {
                    v.textContent = alMoney(val);
                    v.className = "v " + (key === "liq" ? "gold" : (val >= 0 ? "pos" : "neg"));
                }
                var s2 = el.querySelector(".s");
                if (s2) s2.textContent = extra + " / порог " + alMoney(thr);
                var bar = el.querySelector(".al-bar i");
                if (bar) bar.style.width = pct + "%";
            }
            // лента обновляется на каждом опросе: поток — это живые данные,
            // а не только редкие сигналы
            var tape = $("al-tape-" + key);
            if (tape) tape.innerHTML = alTape(d.history || alHist, row.flow, key);
        });
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
        document.querySelectorAll("[data-coinval]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var m = btn.getAttribute("data-coinmetric");
                alCfg.coins = alCfg.coins || {};
                alCfg.coins[m] = btn.getAttribute("data-coinval");
                document.querySelectorAll('[data-coinbox="' + m + '"] [data-coinval]')
                    .forEach(function (b) { b.classList.toggle("on", b === btn); });
                var inp = document.querySelector('[data-coinin="' + m + '"]');
                if (inp) inp.value = alCfg.coins[m] === "ALL" ? "" : alCfg.coins[m];
                var lab = document.querySelector('[data-coinlabel="' + m + '"]');
                if (lab) lab.textContent = alCoinLabel(alCoinOf(m));
                var st = $("al-status");
                if (st) {
                    st.textContent = "монеты: LIQ " + alCoinLabel(alCoinOf("liq")) +
                        " · CVD " + alCoinLabel(alCoinOf("cvd")) +
                        " · OI " + alCoinLabel(alCoinOf("oi")) +
                        " · окна: LIQ " + alWin(alWinOf("liq")) +
                        " · CVD " + alWin(alWinOf("cvd")) +
                        " · OI " + alWin(alWinOf("oi"));
                }
                alDebounce();
            });
        });
        document.querySelectorAll("[data-coinin]").forEach(function (cin) {
            cin.addEventListener("change", function () {
                var m = cin.getAttribute("data-coinin");
                alCfg.coins = alCfg.coins || {};
                alCfg.coins[m] = alCoinInput(cin.value);
                cin.value = alCfg.coins[m] === "ALL" ? "" : alCfg.coins[m];
                var lab = document.querySelector('[data-coinlabel="' + m + '"]');
                if (lab) lab.textContent = alCoinLabel(alCfg.coins[m]);
                alDebounce();
            });
        });
        function alSetWin(m, n) {
            n = Math.max(1, Math.min(1440, Number(n) || 5));
            alCfg.windows = alCfg.windows || {};
            alCfg.windows[m] = n;
            if (alCfg.watch && alCfg.watch.indexOf(m) >= 0) alCfg.window_min = n;
            var box = document.querySelector('[data-winbox="' + m + '"]');
            if (box) {
                box.parentNode.querySelectorAll("[data-win]").forEach(function (b) {
                    b.classList.toggle("on",
                        Number(b.getAttribute("data-win")) === n);
                });
            }
            var inp = document.querySelector('[data-winin="' + m + '"]');
            if (inp) inp.value = n;
            var lab = document.querySelector('[data-winlabel="' + m + '"]');
            if (lab) {
                var titles = { liq: "LIQ", cvd: "CVD", oi: "OI" };
                lab.textContent = "Окно " + (titles[m] || m) + " · " + alWin(n);
            }
            alDebounce();
        }
        document.querySelectorAll("[data-win]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var box = btn.parentNode;
                var m = box && box.getAttribute("data-winbox");
                if (!m) return;
                alSetWin(m, btn.getAttribute("data-win"));
            });
        });
        document.querySelectorAll("[data-winin]").forEach(function (inp) {
            inp.addEventListener("change", function () {
                var m = inp.getAttribute("data-winin");
                if (!m) return;
                alSetWin(m, inp.value);
            });
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

    /* ---------- сервис «Корреляции валют»: тепловая карта ---------- */
    /* Почему сервис раньше «туго» отвечал на нажатия: доска перерисовывалась
       целиком на каждом автообновлении (раз в 30 с) — вместе с разметкой
       умирали все обработчики, и клик по чипу уходил в пустоту. Теперь:
       1) обработчики висят на контейнере доски (делегирование) — перерисовка
          их не сносит; 2) клик сразу переключает чип и перерисовывает картину
          из уже пришедших данных, а запрос к серверу идёт фоном; 3) автообнов-
          ление обновляет только цифры (потоки, карту, гистограмму, пары,
          статус), а не всю доску. */
    var corData = null, corTimer = null, corCfg = { window: "24h", metric: "liq" };
    var corSaveT = null;
    var COR_LABEL = { liq: "LIQ", vol: "📦 Объём", cvd: "🌊 CVD", oi: "📊 OI" };
    var COR_HINT = {
        liq: "по ликвидациям", vol: "по объёму",
        cvd: "по потоку CVD", oi: "по открытому интересу",
    };
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

    function corPairExplanation(a, b, r, metric) {
        metric = metric || (corData && corData.metric) || corCfg.metric;
        var what = corMetricHint(metric);
        var ar = Math.abs(r), side = r >= 0 ? "в одну сторону" : "в противофазе";
        var verdict = ar >= 0.6 ? "сильная связь" : ar >= 0.4 ? "заметная связь"
            : ar >= 0.2 ? "слабая связь" : "связи почти нет";
        return corShort(a) + " ↔ " + corShort(b) + ": r = " + r.toFixed(2) +
            " · " + verdict + " (" + side + "). В клетке — как вела себя метрика" +
            " «" + what + "» у этих двух монет за " + corWinLabel() +
            ". " + COR_COEF_TEXT;
    }

    function corStatusText() {
        /* Статус по всей доске: у каждой переменной своя карта, поэтому
           считаем пары и сильные связи по всем метрикам сразу. */
        if (!corData) return "";
        var pairs = [], strong = 0;
        COR_ORDER.forEach(function (m) {
            corPairsOf(corData, m).forEach(function (p) {
                pairs.push(p);
                if (Math.abs(p.r) >= 0.4) strong += 1;
            });
        });
        return "монет: " + ((corData.symbols || []).length) +
            " · точек в окне: " + (corData.hours || 0) +
            " · пар: " + pairs.length + " · сильных: " + strong +
            " · карт: " + COR_ORDER.length;
    }

    /* Настройки алертов по корреляции — свои у каждой переменной: окно,
       порог «в противофазе» (минус) и порог «в одну сторону» (плюс). */
    function corAlertOf(metric) {
        var alerts = corCfg.alerts || (corData && corData.alerts) || {};
        var row = alerts[metric] || {};
        return {
            enabled: !!row.enabled,
            window: row.window || (corData && corData.window) || corCfg.window || "24h",
            opp: (row.opp === undefined || row.opp === null) ? -0.6 : Number(row.opp),
            same: (row.same === undefined || row.same === null) ? 0.6 : Number(row.same),
        };
    }

    function corAlertSave(metric, patch) {
        var alerts = Object.assign({}, corCfg.alerts || {});
        alerts[metric] = Object.assign({}, alerts[metric] || {}, patch);
        corCfg.alerts = alerts;
        saveCorrelations();
    }

    function corAlertNum(v) {
        var n = Number(v) || 0;
        return (n < 0 ? "−" : "+") + Math.abs(n).toFixed(2);
    }

    function corAlertBlock(d, metric) {
        var cfg = corAlertOf(metric);
        var wins = (d.windows || []).map(function (v) {
            var key = (typeof v === "object") ? (v.key || v.window) : v;
            var label = (typeof v === "object" && (v.label || v.title)) || key;
            var on = String(key) === String(cfg.window);
            return '<button type="button" class="al-chip' + (on ? " on" : "") +
                '" data-corawin="' + esc(metric + "|" + key) + '">' + esc(label) + "</button>";
        }).join("");
        return '<div class="cor-alert" data-coralert="' + esc(metric) + '">' +
            '<div class="cor-alert-h"><label class="al-switch' + (cfg.enabled ? " on" : "") +
            '" data-coralon="' + esc(metric) + '"><i></i><span>' +
            (cfg.enabled ? "СИГНАЛ ВКЛ" : "СИГНАЛ ВЫКЛ") + "</span></label>" +
            '<span class="cor-alert-now">в противофазе ' + corAlertNum(cfg.opp) +
            " · в одну сторону " + corAlertNum(cfg.same) + "</span></div>" +
            '<div class="al-label">Окно сигнала</div><div class="al-chips">' + wins + "</div>" +
            '<div class="al-row cor-alert-nums">' +
            '<label class="cor-num">в противофазе <input data-coraopp="' + esc(metric) +
            '" type="number" min="-1" max="0" step="0.05" value="' + cfg.opp.toFixed(2) +
            '"></label>' +
            '<label class="cor-num">в одну сторону <input data-corasame="' + esc(metric) +
            '" type="number" min="0" max="1" step="0.05" value="' + cfg.same.toFixed(2) +
            '"></label></div>' +
            '<div class="cor-alert-hint">Сигнал придёт, когда пара монет перешагнёт порог: ' +
            "«в противофазе» — связь со знаком минус, «в одну сторону» — со знаком плюс " +
            "(порог 0.5 значит «коэффициент 0.5 и выше»).</div></div>";
    }

    /* Иконка переменной — для заголовка строки: «💥 Ликвидации». */
    var COR_ICON = { liq: "💥", vol: "📦", cvd: "🌊", oi: "📊" };

    /* Короткая сводка в заголовке строки: окно, сколько монет и связей и
       включён ли сигнал по этой переменной. */
    function corMapMeta(d, metric) {
        var pairs = corPairsOf(d, metric) || [];
        var strong = 0;
        pairs.forEach(function (p) {
            if (Math.abs(Number(p.r)) >= 0.4) strong += 1;
        });
        var cfg = corAlertOf(metric);
        return "окно " + corWinLabel(d) +
            " · монет " + ((d && d.symbols) || []).length +
            " · связей " + pairs.length + " · сильных " + strong +
            " · сигнал: " + (cfg.enabled ? "вкл" : "выкл");
    }

    /* Одна переменная — одна горизонтальная строка во всю ширину доски.
       Внутри строка делится на три части: тепловая карта со своим пояснением и
       распределением связей, список сильнейших связей и настройки сигнала.
       Раньше карты стояли столбиками рядом и каждая была узкой колонкой. */
    function corMetricBlock(d, metric) {
        var note = "Что это: " + String(corMetricTitle(d, metric)).toLowerCase() +
            " — " + corMetricHint(metric) +
            ". Клетка — пара монет, число — коэффициент за " + corWinLabel(d) +
            " (зелёный: вместе, красный: наоборот). Наведите на клетку или нажмите её: " +
            "покажу, что именно показывает эта цифра.";
        return '<section class="cor-map" data-cormap="' + esc(metric) + '">' +
            '<div class="cor-map-h">' +
            '<span class="cor-map-title">' + (COR_ICON[metric] || "🎨") + " " +
            esc(corMetricTitle(d, metric)) + "</span>" +
            '<span class="cor-map-meta" data-cormeta="' + esc(metric) + '">' +
            esc(corMapMeta(d, metric)) + "</span></div>" +
            '<div class="cor-map-grid">' +
            '<div class="cor-map-col cor-col-heat">' +
            '<div class="cor-heat-note">' + esc(note) + "</div>" +
            '<div class="cor-heat-box" data-corheat="' + esc(metric) + '">' +
            corHeat(d, metric) + "</div>" +
            '<div class="cor-hist-wrap"><span class="cor-hist-title">Распределение связей · ' +
            esc(COR_LABEL[metric] || metric) + '</span><div class="cor-hist" data-corhist="' +
            esc(metric) + '">' + corHist(metric) + "</div></div>" +
            "</div>" +
            '<div class="cor-map-col cor-col-links">' +
            '<div class="al-label">Самые сильные связи · ' + esc(COR_LABEL[metric] || metric) +
            '</div><div class="cor-pairs" data-corpairs="' + esc(metric) + '">' +
            corPairsList(metric) + "</div></div>" +
            '<div class="cor-map-col cor-col-alert">' +
            '<div class="al-label">🔔 Сигнал по переменной</div>' +
            corAlertBlock(d, metric) + "</div>" +
            "</div></section>";
    }

    function paintCorrelations(d, bind) {
        var board = $("corr-board");
        if (!board) return;
        corData = d;
        if (d.config && d.config.metric) corCfg.metric = d.config.metric;
        if (d.config && d.config.window) corCfg.window = d.config.window;
        if (d.alerts) corCfg.alerts = d.alerts;
        var winLabel = (d.window_label || d.window || "");
        var head = '<div class="al-head"><div><h3>🔗 Корреляции валют</h3>' +
            '<div class="al-sub">Кто ходит вместе за ' + esc(winLabel) +
            ", а кто в противофазе: ликвидации и объём, CVD и OI. У каждой переменной " +
            "своя тепловая карта — выбирать метрику не нужно. Видно, где выносило шорты, " +
            "а где лонги, куда перекошен CVD и где растёт открытый интерес.</div></div></div>";
        board.innerHTML = head +
            '<div class="al-label">Окно</div><div class="al-chips" id="cor-wins">' +
            corWindowChips(d) + "</div>" +
            '<div class="cor-flows" id="cor-flows">' + corFlowsBox() + "</div>" +
            '<div class="cor-maps" id="cor-maps">' +
            COR_ORDER.map(function (m) { return corMetricBlock(d, m); }).join("") +
            "</div>" +
            '<div class="al-status" id="cor-status">' + esc(corStatusText()) + "</div>";
        if (bind) bindCorrelations();
    }

    function paintCorrelationsLive(d) {
        /* Автообновление: только цифры. Разметку и чипы не трогаем — иначе
           обработчики кнопок умирали бы каждые 30 секунд. */
        corData = d;
        var flows = $("cor-flows");
        if (flows) flows.innerHTML = corFlowsBox();
        COR_ORDER.forEach(function (m) {
            var heat = board_query('[data-corheat="' + m + '"]');
            if (heat) heat.innerHTML = corHeat(d, m);
            var hist = board_query('[data-corhist="' + m + '"]');
            if (hist) hist.innerHTML = corHist(m);
            var pairs = board_query('[data-corpairs="' + m + '"]');
            if (pairs) pairs.innerHTML = corPairsList(m);
            var meta = board_query('[data-cormeta="' + m + '"]');
            if (meta) meta.textContent = corMapMeta(d, m);
        });
        var st = $("cor-status");
        if (st) st.textContent = corStatusText();
    }

    function board_query(sel) {
        var board = $("corr-board");
        return board ? board.querySelector(sel) : null;
    }

    function corMarkChips() {
        /* Подсветка выбранного окна — мгновенно, не дожидаясь ответа сервера. */
        var board = $("corr-board");
        if (!board) return;
        board.querySelectorAll("#cor-wins .al-chip").forEach(function (b) {
            b.classList.toggle("on", b.getAttribute("data-corwin") === String(corCfg.window));
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
                body: JSON.stringify({ window: corCfg.window, metric: corCfg.metric,
                                       alerts: corCfg.alerts || {} }),
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
                $("corr-board").offsetParent !== null) loadCorrelations(false);
        }, 30000);
    }

    function loadCorrelations(full) {
        return api("/api/account/correlations?window=" + corCfg.window + "&metric=" + corCfg.metric)
            .then(function (d) {
                if (!d || !d.ok) return d;
                corData = d;
                if (d.alerts) corCfg.alerts = d.alerts;
                if (full || !board_query('[data-corheat="liq"]')) paintCorrelations(d, true);
                else paintCorrelationsLive(d);
                return d;
            });
    }

    function corAttr(node, board, attr) {
        var t = node;
        while (t && t !== board) {
            if (t.getAttribute && t.getAttribute(attr)) return t;
            t = t.parentNode;
        }
        return null;
    }

    function bindCorrelations() {
        var board = $("corr-board");
        if (!board || board._bound) return;
        board._bound = true;              // обработчики живут на контейнере
        board.addEventListener("click", function (e) {
            var t = e.target;
            var cell = corAttr(t, board, "data-corcell");
            if (cell) {
                // Клик по клетке тепловой карты: то же объяснение, что и в
                // подсказке, но ещё и текстом под картой — на телефоне
                // наведения нет, а вопрос «что это за цифра» остаётся.
                var parts = cell.getAttribute("data-corcell").split("|");
                var line = parts[3]
                    ? corPairExplanation(parts[1] + "_USDT", parts[2] + "_USDT",
                                         Number(parts[3]), parts[0])
                    : parts[1] + " — сама с собой: r = 1, диагональ карты";
                corSetStatus(line, true);
                return;
            }
            var win = corAttr(t, board, "data-corwin");
            if (win) {
                corCfg.window = win.getAttribute("data-corwin");
                corMarkChips();
                corSetStatus("окно " + corCfg.window + " · считаю…");
                loadCorrelations(true);
                saveCorrelations();
                return;
            }
            var awin = corAttr(t, board, "data-corawin");
            if (awin) {
                var wp = awin.getAttribute("data-corawin").split("|");
                var metric = wp[0];
                corAlertSave(metric, { window: wp[1] });
                var box = board_query('[data-coralert="' + metric + '"]');
                if (box) {
                    box.querySelectorAll(".al-chips .al-chip").forEach(function (b) {
                        b.classList.toggle("on", b === awin);
                    });
                }
                corSetStatus("сигнал " + metric.toUpperCase() + " · окно " + wp[1]);
                return;
            }
            var aon = corAttr(t, board, "data-coralon");
            if (aon) {
                var am = aon.getAttribute("data-coralon");
                var acfg = corAlertOf(am);
                acfg.enabled = !acfg.enabled;
                corAlertSave(am, { enabled: acfg.enabled });
                aon.classList.toggle("on", acfg.enabled);
                var sp = aon.querySelector("span");
                if (sp) sp.textContent = acfg.enabled ? "СИГНАЛ ВКЛ" : "СИГНАЛ ВЫКЛ";
                corSetStatus("сигнал " + am.toUpperCase() +
                    (acfg.enabled ? " включён" : " выключен"));
                return;
            }
            var pair = corAttr(t, board, "data-corpair");
            if (pair) {
                var map = corAttr(t, board, "data-cormap");
                var pm = map ? map.getAttribute("data-cormap") : corCfg.metric;
                var pi = Number(pair.getAttribute("data-corpair"));
                var p = corPairsOf(corData, pm)
                    .sort(function (x, y) { return Math.abs(y.r) - Math.abs(x.r); })[pi];
                if (!p) return;
                var verdict = p.r >= 0 ? "движутся вместе" : "в противофазе";
                corSetStatus(corShort(p.a) + " и " + corShort(p.b) + ": r = " +
                    Number(p.r).toFixed(2) + " — " + verdict + " · " +
                    COR_HINT[pm] + " · окно " + corWinLabel(), true);
            }
        });
        board.addEventListener("change", function (e) {
            var opp = corAttr(e.target, board, "data-coraopp");
            var same = corAttr(e.target, board, "data-corasame");
            if (!opp && !same) return;
            var node = opp || same;
            var metric = node.getAttribute(opp ? "data-coraopp" : "data-corasame");
            var raw = Number(node.value);
            if (!isFinite(raw)) return;
            var patch = {};
            if (opp) patch.opp = Math.max(-1, Math.min(0, -Math.abs(raw)));
            else patch.same = Math.max(0, Math.min(1, Math.abs(raw)));
            patch.enabled = true;              // поставил порог — ждём сигнал
            corAlertSave(metric, patch);
            var box = board_query('[data-coralert="' + metric + '"]');
            if (box) {
                var sw = box.querySelector("[data-coralon]");
                if (sw && !sw.classList.contains("on")) {
                    sw.classList.add("on");
                    var sp2 = sw.querySelector("span");
                    if (sp2) sp2.textContent = "СИГНАЛ ВКЛ";
                }
                var now = box.querySelector(".cor-alert-now");
                var cfg2 = corAlertOf(metric);
                if (now) now.textContent = "в противофазе " + corAlertNum(cfg2.opp) +
                    " · в одну сторону " + corAlertNum(cfg2.same);
            }
            corSetStatus("сигнал " + metric.toUpperCase() + " включён");
        });
    }

    // Что означает цвет клетки и что в неё смотреть: собирается из подписи
    // метрики, знака коэффициента и самих монет. Раньше клетки карты были
    // просто цветными квадратами без единого слова — по наведению ничего
    // не объяснялось.
    var COR_COEF_TEXT = "Коэффициент Пирсона по часовым точкам: +1 — метрика " +
        "движется синхронно, 0 — связи нет, −1 — в противофазе.";

    function corMetricHint(metric) {
        var list = (corData && corData.metrics) || [];
        for (var i = 0; i < list.length; i++) {
            var m = list[i];
            var k = (typeof m === "object") ? (m.key || m.metric) : m;
            if (String(k) === String(metric)) {
                return (typeof m === "object" && m.hint) || corLabelOf(metric);
            }
        }
        return COR_HINT[String(metric)] || corLabelOf(metric);
    }

    function corWinLabel(d) {
        d = d || corData;
        if (d && (d.window_label || d.window)) {
            return d.window_label || d.window;
        }
        return String(corCfg.window || "");
    }

    function corHeatColor(r) {
        var a = Math.max(0, Math.min(1, Math.abs(Number(r) || 0)));
        var alpha = (0.06 + 0.7 * a).toFixed(2);
        return Number(r) >= 0
            ? "background:rgba(0,230,118," + alpha + ")"
            : "background:rgba(255,42,95," + alpha + ")";
    }

    function corHeat(d, metric) {
        metric = metric || (d && d.metric) || "liq";
        var syms = (d && d.symbols || []).slice(0, 10);
        var m = corMatrix(d, metric);
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
                var tip = same
                    ? corShort(a) + " — сама с собой: r = 1, диагональ карты"
                    : corPairExplanation(a, b, r, metric);
                html += '<td class="cor-heat-cell' + (same ? " diag" : "") +
                    '" data-corcell="' + esc(metric + "|" + corShort(a) + "|" +
                        corShort(b) + "|" + (same ? "" : r.toFixed(2))) +
                    '" title="' + esc(tip) + '" style="' +
                    (same ? "" : corHeatColor(r)) + '">' +
                    (same ? "1" : r.toFixed(2)) + "</td>";
            });
            html += "</tr>";
        });
        return html + "</tbody></table></div>";
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

    function corPairsList(metric) {
        var pairs = corPairsOf(corData, metric || (corData && corData.metric) || "liq");
        if (!pairs.length) {
            return '<div class="cor-flow-empty">связей пока нет — мало истории</div>';
        }
        pairs.sort(function (p, q) { return Math.abs(q.r) - Math.abs(p.r); });
        // Строка переменной широкая: в колонке связей помещается больше пар,
        // чем помещалось в узкой карте-столбике.
        return pairs.slice(0, 10).map(function (p, i) {
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

    /* ---------- сервис «Сторож монет»: гистограмма пампов/дампов ---------- */
    /* Доска сторожа жила по тому же принципу, что и корреляции: автообновление
       (раз в 12 с) перерисовывало всю разметку, обработчики кнопок исчезали, и
       чипы переставали отвечать. Теперь обработчики висят на контейнере, клик
       сразу переключает чип и пересобирает картину из уже пришедших данных, а
       автообновление меняет только цифры. */
    var pumpData = null, pumpTimer = null, pumpCfg = null, pumpSaveT = null;
    /* Нажатые, но ещё не подтверждённые сервером настройки. Раньше клик по
       «Период свечей» / «Сколько свечей» тут же просил свежий снимок у сервера,
       снимок приходил со старым значением, доска перерисовывалась — и чип
       отскакивал назад, а сохранение уходило со старым числом («скачет и
       остаётся на месте»). Теперь нажатие — источник правды, пока сервер его
       не подтвердил, а снимки только дополняют картину. */
    var pumpPending = {};
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

    function pumpPendingAny() {
        for (var k in pumpPending) {
            if (Object.prototype.hasOwnProperty.call(pumpPending, k)) return true;
        }
        return false;
    }

    /** Локальный выбор важнее снимка с сервера, пока сервер его не подтвердил. */
    function pumpMergeCfg(cfg) {
        var out = Object.assign({}, cfg || {});
        Object.keys(pumpPending).forEach(function (k) { out[k] = pumpPending[k]; });
        return out;
    }

    /** Настройка, выбранная в панели: сразу видна и защищена от старых снимков. */
    function pumpSet(key, value) {
        if (!pumpCfg) pumpCfg = {};
        pumpCfg[key] = value;
        pumpPending[key] = value;
    }

    function loadPumps(full) {
        return api("/api/account/watchlist").then(function (d) {
            if (!d || !d.ok) return d;
            var first = !pumpCfg || !$("pump-bars");
            pumpData = d;
            if (d.config) pumpCfg = pumpMergeCfg(d.config);
            /* Пока настройка не подтверждена сервером, доску целиком не
               пересобираем: перерисовка снесла бы чипы, которые только что
               нажали. Обновляем цифры и подсветку — этого достаточно. */
            if ((full || first) && !pumpPendingAny()) {
                paintPumps(d, true);
            } else {
                pumpMarkChips();
                paintPumpsLive(d);
            }
            return d;
        });
    }

    function savePumps() {
        if (!pumpCfg) return;
        pumpSaveT = null;
        var sent = Object.assign({}, pumpCfg);       // что именно уйдёт на сервер
        api("/api/account/watchlist", {
            method: "POST",
            body: JSON.stringify(sent),
        }).then(function (d) {
            var st = $("pump-status");
            // подтверждение пришло: «ожидание» снимаем только с тех полей,
            // которые не успели измениться, пока запрос был в пути
            Object.keys(pumpPending).forEach(function (k) {
                if (String(sent[k]) === String(pumpCfg[k])) delete pumpPending[k];
            });
            if (d && d.config) pumpCfg = pumpMergeCfg(d.config);
            if (d && d.movers) pumpData = Object.assign({}, pumpData || {}, { movers: d.movers, hits: d.hits || [] });
            if (d && d.hits) {
                var hits = $("pump-hits-box");
                if (hits) hits.innerHTML = pumpHitsBox(d);
            }
            pumpMarkChips();                         // окно сигнала — по факту
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
                pumpSet("enabled", !pumpCfg.enabled);
                pumpMarkChips();
                pumpDebounce();
                return;
            }
            var mode = t.getAttribute("data-pumpmode");
            if (mode) {
                pumpSet("mode", mode);
                pumpSet("enabled", true);
                pumpRepaintNow();          // картина меняется сразу
                pumpDebounce();
                return;
            }
            var thr = t.getAttribute("data-pumpthr");
            if (thr !== null && thr !== undefined && thr !== "") {
                pumpSet("threshold", Number(thr));
                var inp = $("pump-thr-in");
                if (inp) inp.value = pumpCfg.threshold;
                pumpRepaintNow();
                pumpDebounce();
                return;
            }
            var per = t.getAttribute("data-pumpper");
            if (per) {
                // период и число свечей считаются на клиенте: окно сигнала —
                // период × свечи, минуты периода уже пришли со снимком
                pumpSet("period", per);
                pumpMarkChips();
                pumpDebounce();
                return;
            }
            var cnd = t.getAttribute("data-pumpcn");
            if (cnd) {
                pumpSet("candles", Number(cnd));
                pumpMarkChips();
                pumpDebounce();
                return;
            }
        });
        board.addEventListener("change", function (e) {
            var inp = e.target;
            if (!inp || inp.id !== "pump-thr-in") return;
            pumpSet("threshold", Number(inp.value) || pumpCfg.threshold);
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
        (days || []).forEach(function (d) {
            if (d.uniques > max) max = d.uniques;
            if (d.views > max) max = d.views;
        });
        el.innerHTML = (days || []).map(function (d) {
            var hv = Math.max(6, Math.round(100 * d.views / max));
            var hu = Math.max(4, Math.round(100 * (d.uniques || 0) / max));
            var label = String(d.day || "").slice(5);
            var title = d.day + ": " + d.views + " переходов, " +
                d.uniques + " посетителей";
            if (d.bots) title += ", служебных отсеяно " + d.bots;
            return '<div class="bar" style="height:' + hv + 'px" title="' + title +
                '"><i class="bar-u" style="height:' + hu + '%"></i><span>' + label +
                "</span></div>";
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
                $("st-bots") && ($("st-bots").textContent = dv.today_bots || 0);
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
        bootFolds();            // разделы сворачиваются — до остальных панелей, им нужны id
        bootDigestTpl();
        bootBotAdmin();
        bootAiPrompts();
        bootAds();
        bootFeedbackAdmin();
    }

    /* ---------------- 🗂 Сворачиваемые разделы админки ---------------------
     *
     * Админка растёт — каждая карточка превращается в <details>: заголовок
     * кликается, содержимое прячется. Состояние разделов живёт в localStorage,
     * поэтому страница открывается такой, какой её оставил админ. Сверху —
     * кнопки «Развернуть всё» и «Свернуть всё».
     */

    var FOLDS_KEY = "liqscope.admin.folds";

    function foldState() {
        try {
            var raw = localStorage.getItem(FOLDS_KEY) || "{}";
            var obj = JSON.parse(raw);
            return obj && typeof obj === "object" ? obj : {};
        } catch (e) { return {}; }
    }

    function foldSave(id, open) {
        var st = foldState();
        st[id] = !!open;
        try { localStorage.setItem(FOLDS_KEY, JSON.stringify(st)); } catch (e) {}
    }

    function foldHead(card) {
        for (var i = 0; i < card.children.length; i++) {
            if (card.children[i].tagName === "H3") return card.children[i];
        }
        return null;
    }

    /** Карточка → сворачиваемый блок. Возвращает <details> или null. */
    function toFold(card, index) {
        if (!card || card.tagName === "DETAILS") return null;
        var head = foldHead(card);
        if (!head) return null;
        var id = card.id || ("fold-" + index);
        var det = document.createElement("details");
        det.className = (card.className || "") + " fold";
        det.id = id;
        if (card.getAttribute("style")) det.setAttribute("style", card.getAttribute("style"));
        var sum = document.createElement("summary");
        sum.appendChild(head);
        var hint = card.getAttribute("data-hint") || "";
        if (hint) {
            var hintEl = document.createElement("span");
            hintEl.className = "fold-hint";
            hintEl.textContent = hint;
            sum.appendChild(hintEl);
        }
        det.appendChild(sum);
        var body = document.createElement("div");
        body.className = "fold-body";
        while (card.firstChild) body.appendChild(card.firstChild);
        det.appendChild(body);
        card.parentNode.replaceChild(det, card);
        det.foldId = id;
        // По умолчанию разделы свёрнуты: админка должна открываться компактной,
        // а раскрытым остаётся то, что админ раскрыл сам.
        var st = foldState();
        det.open = st[id] === true;
        det.addEventListener("toggle", function () { foldSave(id, det.open); });
        return det;
    }

    function foldAll(folds, open) {
        folds.forEach(function (d) {
            d.open = !!open;
            foldSave(d.foldId || d.id, !!open);
        });
    }

    function bootFolds() {
        var page = (document.body && document.body.getAttribute("data-page")) || "";
        if (page !== "admin") return [];
        var wrap = document.querySelector("main.wrap");
        if (!wrap) return [];
        var cards = Array.prototype.slice.call(wrap.querySelectorAll(".card"));
        var folds = [];
        cards.forEach(function (card, i) {
            var det = toFold(card, i);
            if (det) folds.push(det);
        });
        if (folds.length && !wrap.querySelector(".fold-tools")) {
            var lead = wrap.querySelector("p.lead");
            var tools = document.createElement("div");
            tools.className = "fold-tools";
            tools.innerHTML =
                '<button class="btn btn-small" type="button" id="folds-open">⤢ Развернуть всё</button>' +
                '<button class="btn btn-small" type="button" id="folds-close">⤡ Свернуть всё</button>' +
                '<span class="meta">Разделы сворачиваются в блоки — админка открывается ' +
                "с теми, что были раскрыты в прошлый раз</span>";
            if (lead && lead.parentNode) lead.parentNode.insertBefore(tools, lead.nextSibling);
            else wrap.insertBefore(tools, wrap.firstChild);
            var open = tools.querySelector("#folds-open"), close = tools.querySelector("#folds-close");
            if (open) open.addEventListener("click", function () { foldAll(folds, true); });
            if (close) close.addEventListener("click", function () { foldAll(folds, false); });
        }
        return folds;
    }

    /* ---------------- 💬 Обратная связь: «по всем вопросам» ---------------- */

    function fbTime(ts) {
        if (!ts) return "";
        var d = new Date(Number(ts) * 1000);
        return adPad(d.getDate()) + "." + adPad(d.getMonth() + 1) + " " +
            adPad(d.getHours()) + ":" + adPad(d.getMinutes());
    }

    function fbBubble(m) {
        var mine = !!m.mine;
        var who = mine ? "вы" : (m.admin ? "админ" : "пользователь");
        return '<div class="fb-msg' + (mine ? " mine" : "") + '"><div class="fb-bubble">' +
            '<span class="fb-who">' + esc(who) + "</span>" + esc(m.text) +
            '<span class="fb-time">' + fbTime(m.at) + "</span></div></div>";
    }

    function fbPaint(threadId, messages) {
        var box = $(threadId);
        if (!box) return;
        box.innerHTML = (messages || []).map(fbBubble).join("") ||
            '<p class="meta">Переписки пока нет — напишите первым.</p>';
        box.scrollTop = box.scrollHeight;
    }

    function fbBadge(n) {
        [ $("fb-dot"), $("fb-dot-2"), $("fb-admin-badge") ].forEach(function (el) {
            if (!el) return;
            el.textContent = n ? String(n) : "";
            el.classList.toggle("hidden", !n);
            el.style.display = n ? "" : "none";
        });
        var box = $("fb-admin-badge");
        if (box) box.style.display = n ? "" : "none";
    }

    /** Диалог пользователя в кабинете. */
    function bootFeedbackUser() {
        var card = $("fb-card");
        if (!card || !$("fb-thread")) return;
        function load() {
            return api("/api/feedback").then(function (d) {
                if (!d.ok) return;
                fbPaint("fb-thread", d.messages);
                fbBadge(d.unread);
                var jump = $("fb-jump");
                if (jump) jump.setAttribute("data-unread", d.unread || 0);
            });
        }
        load();
        var send = $("fb-send"), ta = $("fb-text");
        function submit() {
            var text = (ta && ta.value || "").trim();
            var st = $("fb-status");
            if (!text) { if (st) st.textContent = "напишите сообщение"; return; }
            if (st) st.textContent = "отправляю…";
            api("/api/feedback", { method: "POST", body: JSON.stringify({ text: text }) })
                .then(function (d) {
                    if (!d.ok) { if (st) st.textContent = d.hint || d.error || "ошибка"; return; }
                    if (st) st.textContent = "отправлено — ответ придёт сюда";
                    if (ta) ta.value = "";
                    load();
                })
                .catch(function () { if (st) st.textContent = "ошибка сети"; });
        }
        if (send) send.addEventListener("click", submit);
        if (ta) ta.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
        });
        // новые ответы подтягиваем не перезагружая страницу
        setInterval(function () {
            if (document.hidden) return;
            api("/api/feedback/unread").then(function (d) {
                if (d.ok) fbBadge(d.unread);
            });
        }, 60000);
    }

    /** Список диалогов у админа. */
    function bootFeedbackAdmin() {
        if (!$("fb-admin")) return;
        var current = 0;
        function paintList(d) {
            var box = $("fb-list");
            if (!box) return;
            var threads = d.threads || [];
            fbBadge(d.unread);
            if (!threads.length) {
                box.innerHTML = "<p class='meta'>Пока никто не писал. Здесь появятся диалоги " +
                    "из кабинета — с историей и возможностью ответить.</p>";
                return;
            }
            box.innerHTML = threads.map(function (t) {
                return '<div class="fb-item' + (t.unread ? " unread" : "") +
                    (String(t.id) === String(current) ? " on" : "") + '" data-thread="' + t.id + '">' +
                    '<div class="fb-item-name">' + esc(t.name) +
                    (t.unread ? ' <span class="fb-dot">' + t.unread + "</span>" : "") + "</div>" +
                    '<div class="fb-item-last">' + (t.last_admin ? "вы: " : "") +
                    esc(String(t.last_text || "").slice(0, 90)) + "</div>" +
                    '<div class="meta">' + fbTime(t.updated_at) + " · сообщений: " + t.total +
                    (t.link ? " · " + esc(t.link) : "") +
                    (t.email ? " · " + esc(t.email) : "") + "</div></div>";
            }).join("");
            box.querySelectorAll("[data-thread]").forEach(function (el) {
                el.addEventListener("click", function () {
                    openThread(el.getAttribute("data-thread"));
                });
            });
        }
        function openThread(id) {
            current = Number(id) || 0;
            api("/api/admin/feedback/" + current).then(function (d) {
                if (!d.ok) return;
                var t = d.thread || {};
                var head = $("fb-conv-head");
                if (head) {
                    head.innerHTML = esc(t.name || ("#" + current)) +
                        (t.username ? ' <span class="meta">@' + esc(t.username) + "</span>" : "") +
                        (t.email ? ' <span class="meta">' + esc(t.email) + "</span>" : "") +
                        ' <span class="meta">переписка за ' + fbTime(t.created_at) + "</span>";
                }
                fbPaint("fb-admin-thread",
                        (d.messages || []).map(function (m) {
                            return Object.assign({}, m, { mine: m.admin });
                        }));
                fbBadge(d.unread);
                load();
            });
        }
        function load() {
            api("/api/admin/feedback").then(function (d) {
                if (d.ok) paintList(d);
            });
        }
        load();
        var send = $("fb-admin-send"), ta = $("fb-admin-text");
        if (send) send.addEventListener("click", function () {
            var text = (ta && ta.value || "").trim();
            var st = $("fb-admin-status");
            if (!current) { if (st) st.textContent = "выберите диалог слева"; return; }
            if (!text) { if (st) st.textContent = "напишите ответ"; return; }
            if (st) st.textContent = "отправляю…";
            api("/api/admin/feedback/" + current, {
                method: "POST", body: JSON.stringify({ text: text }),
            }).then(function (d) {
                if (!d.ok) { if (st) st.textContent = d.hint || d.error || "ошибка"; return; }
                if (st) st.textContent = "ответ ушёл (и в Telegram, если связан)";
                if (ta) ta.value = "";
                openThread(current);
            }).catch(function () { if (st) st.textContent = "ошибка сети"; });
        });
        if (ta) ta.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && send) send.click();
        });
        var card = $("fb-admin");
        if (card) card.addEventListener("toggle", function () {
            if (card.open) load();          // раскрыли — показать свежие диалоги
        });
        setInterval(function () {
            if (!document.hidden) load();
        }, 45000);
    }

    /* ---------------- 📣 Рекламные посты: бот, каналы и баннер на главной --- */
    //
    // Панель рекламы: текст, фото, источники (бот / каналы / главная сайта),
    // время отправки и срок автоудаления. Отправка идёт через /api/admin/ads,
    // а показ на главной — публичным /api/ads, который отдаёт только живые
    // объявления: срок вышел — баннер исчезает сам, без действий админа.

    var ADS = {
        id: 0,
        photo: "",              // data-URL только что выбранного фото
        photoName: "",
        channels: [],
        limits: { plain: 4000, photo: 1024 },
    };

    var AD_CHIPS = {
        draft: ["черновик", "#8493a8"],
        scheduled: ["к отправке", "#ffd54f"],
        sending: ["отправляется", "#ffd54f"],
        sent: ["отправлен", "#00e676"],
        expired: ["срок вышел", "#8493a8"],
        cancelled: ["снят", "#ff2a5f"],
        failed: ["не ушло", "#ff2a5f"],
    };

    function adPad(n) { return String(n).padStart(2, "0"); }

    function adTime(ts) {
        if (!ts) return "—";
        var d = new Date(Number(ts) * 1000);
        return adPad(d.getDate()) + "." + adPad(d.getMonth() + 1) + " " +
            adPad(d.getHours()) + ":" + adPad(d.getMinutes());
    }

    function adLeft(ts) {
        // «осталось 2 ч» — админу проще, чем пересчитывать время самому
        var sec = Math.round(Number(ts) - Date.now() / 1000);
        if (sec <= 0) return "вот-вот снимется";
        if (sec < 3600) return "ещё " + Math.max(1, Math.round(sec / 60)) + " мин";
        if (sec < 86400) return "ещё " + Math.round(sec / 3600) + " ч";
        return "ещё " + Math.round(sec / 86400) + " сут";
    }

    function adChip(st) {
        var c = AD_CHIPS[st] || [st || "—", "#8493a8"];
        return '<span class="ad-chip" style="border-color:' + c[1] + ";color:" + c[1] + '">' +
            esc(c[0]) + "</span>";
    }

    function adTargetRow(key, label, note) {
        return '<label class="bot-check" style="display:flex;gap:8px;align-items:flex-start;margin:6px 0">' +
            '<input type="checkbox" data-ad-target="' + esc(key) + '"> <span><b>' + label +
            "</b>" + (note ? ' <span class="meta">' + note + "</span>" : "") + "</span></label>";
    }

    function adLimit() {
        return ADS.photo ? (ADS.limits.photo || 1024) : (ADS.limits.plain || 4000);
    }

    function adCount() {
        var ta = $("ad-text"), el = $("ad-len");
        if (!ta || !el) return;
        var n = ta.value.length, lim = adLimit();
        el.textContent = n + " / " + lim + " знаков" +
            (ADS.photo ? " (с фото)" : " (без фото)") +
            (n > lim ? " — Telegram не примет" : "");
        el.style.color = n > lim ? "var(--red)" : "";
    }

    function adShowPhoto() {
        var box = $("ad-photo-prev");
        if (box) {
            box.innerHTML = ADS.photo
                ? '<div class="tpl-photo"><img alt="" src="' + ADS.photo + '"></div>'
                : "";
        }
        adCount();
    }

    function renderAdTargets(d) {
        var box = $("ad-targets");
        if (!box) return;
        ADS.channels = d.channels || [];
        var html = adTargetRow("bot", "🤖 Telegram-бот",
                               "— в личку всем, кто запускал бота")
            + adTargetRow("site", "🌐 Главная сайта",
                          "— баннер под кнопками «Терминал» и «Что умеет»");
        (d.channels || []).forEach(function (c) {
            html += adTargetRow("ch:" + c.id, esc(c.label || "📣 Канал"),
                                "— id " + esc(c.id));
        });
        if (!(d.channels || []).length) {
            html += '<p class="meta">Каналы в боте не привязаны: впишите id или @имя ниже ' +
                "либо отправьте пост только в бота и на главную.</p>";
        }
        box.innerHTML = html;
    }

    function adCheckboxes() {
        return Array.prototype.slice.call(
            document.querySelectorAll("#ad-targets input[data-ad-target]"));
    }

    function renderAdList(d) {
        var box = $("ad-list");
        if (!box) return;
        var items = d.items || [];
        if (!items.length) {
            box.innerHTML = "<p class='meta'>Пока пусто. Заполните форму выше — пост появится здесь " +
                "и уйдёт сам в выбранное время.</p>";
            return;
        }
        box.innerHTML = items.map(function (a) {
            var photo = a.has_photo
                ? '<img class="ad-thumb" alt="" src="/api/admin/ads/' + a.id + '/photo">'
                : '<span class="ad-thumb ad-thumb-empty">без фото</span>';
            var when = a.status === "sent"
                ? "отправлен " + adTime(a.sent_at)
                : (a.send_at ? (a.status === "draft" ? "черновик · время " : "уйдёт ") + adTime(a.send_at)
                             : "черновик");
            var life = a.expires_at
                ? "снимется " + adTime(a.expires_at) +
                  (a.status === "sent" ? " (" + adLeft(a.expires_at) + ")" : "")
                : "без автоудаления";
            var acts = [];
            if (a.status !== "sent" && a.status !== "expired") {
                acts.push('<button class="btn btn-small" data-ad-act="send" data-ad-id="' + a.id + '">⚡ Сейчас</button>');
            }
            acts.push('<button class="btn btn-small" data-ad-act="edit" data-ad-id="' + a.id + '">✎ Править</button>');
            if (a.status === "sent" || a.status === "scheduled") {
                acts.push('<button class="btn btn-small" data-ad-act="stop" data-ad-id="' + a.id + '">⏹ Снять</button>');
            }
            acts.push('<button class="btn btn-danger btn-small" data-ad-act="del" data-ad-id="' + a.id + '">🗑 Удалить</button>');
            return '<div class="ad-item">' + photo +
                '<div class="ad-item-body"><div class="ad-item-head">' + adChip(a.status) +
                '<span class="meta">' + esc(when) + " · " + esc(life) + "</span></div>" +
                '<div class="ad-item-text">' + (esc(a.text).slice(0, 240) || "<i>без текста</i>") + "</div>" +
                '<div class="meta">Куда: ' + esc(a.human_targets || "—") + "</div>" +
                '<div class="meta">' + esc(a.line || "") + "</div>" +
                '<div class="row-actions">' + acts.join("") + "</div></div></div>";
        }).join("");
        box.querySelectorAll("button[data-ad-act]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var id = btn.getAttribute("data-ad-id");
                var act = btn.getAttribute("data-ad-act");
                if (act === "edit") { adEdit(id); return; }
                if (act === "del" && !confirm("Удалить запись? Посты в каналах, если они вышли, тоже удалятся.")) return;
                if (act === "stop" && !confirm("Снять объявление сейчас? Баннер уберётся, посты в каналах удалятся.")) return;
                var path = act === "send" ? "/send" : (act === "stop" ? "/stop" : "/delete");
                var st = $("ad-status");
                if (st) st.textContent = "выполняю…";
                api("/api/admin/ads/" + id + path, { method: "POST", body: "{}" }).then(function (r) {
                    if (st) {
                        st.textContent = r.ok
                            ? (act === "send" ? ("отправлено: " + ((r.item || {}).line || "ok"))
                                              : (r.hint || "готово"))
                            : (r.hint || r.error || "ошибка");
                    }
                    loadAds();
                });
            });
        });
    }

    function adEdit(id) {
        var d = (ADS.last || { items: [] });
        var a = (d.items || []).filter(function (x) { return String(x.id) === String(id); })[0];
        if (!a) return;
        ADS.id = a.id;
        ADS.photo = "";
        ADS.photoName = "";
        var ta = $("ad-text");
        if (ta) ta.value = a.text || "";
        var extra = $("ad-extra");
        var known = (d.channels || []).map(function (c) { return String(c.id); });
        if (extra) {
            extra.value = ((a.targets || {}).channels || []).filter(function (c) {
                return known.indexOf(String(c)) < 0;
            }).join(", ");
        }
        adCheckboxes().forEach(function (inp) {
            var key = inp.getAttribute("data-ad-target");
            var t = a.targets || {};
            inp.checked = key === "bot" ? !!t.bot
                : (key === "site" ? !!t.site
                    : ((t.channels || []).map(String).indexOf(key.slice(3)) >= 0));
        });
        var mode = $("ad-when-mode"), when = $("ad-when");
        if (mode && when && a.send_at && a.send_at > Date.now() / 1000 + 5) {
            mode.value = "later";
            when.disabled = false;
            var dt = new Date(a.send_at * 1000);
            when.value = dt.getFullYear() + "-" + adPad(dt.getMonth() + 1) + "-" + adPad(dt.getDate()) +
                "T" + adPad(dt.getHours()) + ":" + adPad(dt.getMinutes());
        }
        var ttl = $("ad-ttl");
        if (ttl) {
            var mins = a.send_at && a.expires_at
                ? Math.max(0, Math.round((a.expires_at - a.send_at) / 60)) : 0;
            var have = Array.prototype.some.call(ttl.options, function (o) {
                return Number(o.value) === mins;
            });
            ttl.value = have ? String(mins) : "0";
        }
        adShowPhoto();
        var st = $("ad-status");
        if (st) st.textContent = "правим #" + a.id + " — «Отправить» перезапишет запись";
        if (ta && ta.scrollIntoView) ta.scrollIntoView({ block: "center", behavior: "smooth" });
    }

    function adCollect(draft) {
        var targets = { bot: false, site: false, channels: [] };
        adCheckboxes().forEach(function (inp) {
            if (!inp.checked) return;
            var key = inp.getAttribute("data-ad-target");
            if (key === "bot") targets.bot = true;
            else if (key === "site") targets.site = true;
            else if (key.indexOf("ch:") === 0) targets.channels.push(key.slice(3));
        });
        ((($("ad-extra") || {}).value) || "").split(/[\s,;]+/).forEach(function (x) {
            if (x && x !== "@" && targets.channels.indexOf(x) < 0) targets.channels.push(x);
        });
        var payload = {
            text: (($("ad-text") || {}).value || "").trim(),
            targets: targets,
        };
        if (ADS.photo) {
            payload.photo = ADS.photo;
            payload.photo_name = ADS.photoName || "ad.jpg";
        }
        if (!draft) {
            var mode = ($("ad-when-mode") || {}).value || "now";
            if (mode === "later") {
                var raw = (($("ad-when") || {}).value) || "";
                var ts = raw ? Math.round(new Date(raw).getTime() / 1000) : 0;
                payload.send_at = ts || Math.round(Date.now() / 1000);
            } else {
                payload.send_at = Math.round(Date.now() / 1000);
            }
            payload.ttl_min = Number((($("ad-ttl") || {}).value) || 0);
        } else {
            payload.draft = true;
        }
        return payload;
    }

    function saveAd(draft) {
        var st = $("ad-status");
        var payload = adCollect(draft);
        if (draft && !payload.text && !ADS.photo) {
            if (st) st.textContent = "пусто: нужен текст или фотография";
            return;
        }
        if (st) st.textContent = draft ? "сохраняю черновик…" : "отправляю…";
        var path = ADS.id ? "/api/admin/ads/" + ADS.id : "/api/admin/ads";
        api(path, { method: "POST", body: JSON.stringify(payload) }).then(function (d) {
            if (!d.ok) {
                if (st) st.textContent = d.hint || d.error || "ошибка";
                if (d.id) loadAds();
                return;
            }
            var item = d.item || {};
            var now = !draft && item.status !== "scheduled" && item.status !== "draft";
            if (draft) {
                if (st) st.textContent = "черновик сохранён";
                loadAds();
            } else if (ADS.id && ($("ad-when-mode") || {}).value === "now") {
                api("/api/admin/ads/" + ADS.id + "/send", { method: "POST", body: "{}" })
                    .then(function (r) {
                        if (st) st.textContent = r.ok ? ("ушло: " + ((r.item || {}).line || "ok"))
                                                      : (r.hint || r.error || "не ушло");
                        loadAds();
                    });
            } else {
                if (st) st.textContent = now
                    ? ("ушло: " + (item.line || "ok"))
                    : ("запланировано на " + adTime(item.send_at));
                loadAds();
            }
            if (now) adResetForm(false);
        }).catch(function () {
            if (st) st.textContent = "ошибка сети";
        });
    }

    function adResetForm(full) {
        ADS.id = 0;
        ADS.photo = "";
        ADS.photoName = "";
        var ta = $("ad-text");
        if (ta) ta.value = "";
        var extra = $("ad-extra");
        if (extra) extra.value = "";
        adCheckboxes().forEach(function (i) { i.checked = false; });
        var mode = $("ad-when-mode"), when = $("ad-when"), ttl = $("ad-ttl");
        if (mode) mode.value = "now";
        if (when) { when.disabled = true; when.value = ""; }
        if (ttl) ttl.value = "0";
        var inp = $("ad-photo-in");
        if (inp) inp.value = "";
        adShowPhoto();
        if (full) {
            var st = $("ad-status");
            if (st) st.textContent = "";
        }
    }

    function loadAds() {
        api("/api/admin/ads").then(function (d) {
            if (!d.ok) return;
            ADS.last = d;
            ADS.limits = d.limits || ADS.limits;
            renderAdTargets(d);
            renderAdList(d);
            var svc = d.service || {};
            var line = "Планировщик: " + (svc.bot ? "бот на связи" : "бот не подключён") +
                " · отправлено за сессию: " + (svc.sent_total || 0) +
                " · снято по сроку: " + (svc.expired_total || 0);
            if (svc.error) line += " · ошибка: " + svc.error;
            if ((svc.log || []).length) line += "\n" + svc.log.slice(-4).join("\n");
            var el = $("ad-service");
            if (el) { el.textContent = line; el.style.whiteSpace = "pre-line"; }
            var note = $("ad-note");
            if (note) {
                note.textContent = "Баннер на главной: " + (d.site || "") +
                    " · сейчас на сервере " + adTime(d.now);
            }
            adCount();
        });
    }

    function bootAds() {
        if (!$("ads-panel")) return;
        loadAds();
        var ta = $("ad-text");
        if (ta) ta.addEventListener("input", adCount);
        var mode = $("ad-when-mode"), when = $("ad-when");
        if (mode && when) {
            mode.addEventListener("change", function () {
                var later = mode.value === "later";
                when.disabled = !later;
                if (later && !when.value) {
                    var d = new Date(Date.now() + 3600 * 1000);
                    when.value = d.getFullYear() + "-" + adPad(d.getMonth() + 1) + "-" +
                        adPad(d.getDate()) + "T" + adPad(d.getHours()) + ":" + adPad(d.getMinutes());
                }
            });
        }
        var all = $("ad-all"), none = $("ad-none");
        if (all) all.addEventListener("click", function () {
            adCheckboxes().forEach(function (i) { i.checked = true; });
        });
        if (none) none.addEventListener("click", function () {
            adCheckboxes().forEach(function (i) { i.checked = false; });
        });
        var inp = $("ad-photo-in");
        if (inp) inp.addEventListener("change", function () {
            var f = inp.files && inp.files[0];
            var st = $("ad-photo-status");
            if (!f) return;
            if (f.size > 12 * 1000 * 1000) {
                if (st) st.textContent = "файл больше 12 МБ";
                inp.value = "";
                return;
            }
            var reader = new FileReader();
            reader.onload = function () {
                ADS.photo = String(reader.result || "");
                ADS.photoName = f.name || "ad.jpg";
                if (st) st.textContent = "фото выбрано: " + ADS.photoName + " — уйдёт вместе с постом";
                adShowPhoto();
            };
            reader.readAsDataURL(f);
        });
        var clr = $("ad-photo-clear");
        if (clr) clr.addEventListener("click", function () {
            ADS.photo = "";
            ADS.photoName = "";
            if (inp) inp.value = "";
            var st = $("ad-photo-status");
            if (st) st.textContent = "фото убрано";
            adShowPhoto();
        });
        var save = $("ad-save"), draft = $("ad-draft"), reset = $("ad-reset");
        if (save) save.addEventListener("click", function () { saveAd(false); });
        if (draft) draft.addEventListener("click", function () { saveAd(true); });
        if (reset) reset.addEventListener("click", function () { adResetForm(true); });
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
                (b.kind === "digest" ? ("<p class=\"meta\">Этот рассказ идёт на страницу дайджеста " +
                    "на сайте — целиком, без обрезки. В канал уходит небольшой пост: шапка, " +
                    "первые предложения рассказа, цифры дня и ссылка на полный разбор. Так он " +
                    "влезает в <b>подпись под фото</b> (Telegram разрешает до 1024 знаков), " +
                    "поэтому фотография прикрепляется к каждому выпуску. Под постом — кнопка " +
                    "«📖 Полный разбор дня». Промт можно не укорачивать: длина рассказа " +
                    "влияет только на статью на сайте.</p>") : "") +
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
                // Ключей может быть несколько: видно, на каком работаем и
                // сколько их у сервиса — при лимите запрос уходит на следующий.
                var n = Number(p.keys || 1);
                return p.name + (n > 1 ? " (" + n + " кл., №" + (p.key_index || 1) + ")" : "");
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
