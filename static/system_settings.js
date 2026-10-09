/**
 * ⚙️ Менеджер системных настроек админки — универсальный пульт.
 *
 * Поля не зашиты в разметку: сервер отдаёт схему каждого ключа
 * (имя, подсказка, раздел, секретность, «нужен ли рестарт») в
 *   GET /api/admin/settings,
 * а этот скрипт рисует из неё вкладки и формы. Поэтому новая настройка в
 * ``app_settings.MANAGED_SETTINGS`` появляется в админке сама, без правок
 * вёрстки.
 *
 * Ручки (см. web_account.py):
 *   GET  /api/admin/settings              — схема + значения (секреты маской)
 *   POST /api/admin/settings              — сохранить изменённые ключи
 *   POST /api/admin/settings/test-smtp    — проверка почты (ошибка → 400)
 *   POST /api/admin/settings/test-tg      — проверка токена бота (ошибка → 400)
 *   POST /api/admin/settings/test-llm     — проверка ключа ИИ (ошибка → 400)
 *   POST /api/admin/settings/test-captcha — само-тест капчи
 *
 * Ошибки тестов и сохранения показываются красной плашкой прямо под
 * вкладкой — никаких скрытых консольных ошибок и alert().
 */
(function () {
    "use strict";
    if (!document.body || document.body.getAttribute("data-page") !== "admin") return;

    var MASK = "***";

    /* Порядок и подписи вкладок. Ключи = значения section из схемы сервера. */
    var TABS = [
        { id: "mail",     title: "✉️ Почта и рассылки" },
        { id: "ai",       title: "🤖 ИИ и нейросети" },
        { id: "security", title: "🛡️ Безопасность и доступы" },
        { id: "telegram", title: "💬 Telegram" },
        { id: "tuning",   title: "⚙️ Тюнинг и производительность" },
        { id: "donate",   title: "❤️ Донат" },
    ];

    function $(id) { return document.getElementById(id); }

    function api(path, opts) {
        return fetch(path, Object.assign({
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
        }, opts || {})).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                data._status = r.status;
                return data;
            });
        }).catch(function () {
            return { ok: false, error: "сеть недоступна", _status: 0 };
        });
    }

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
        });
    }

    /* ----- индикатор занятости на кнопке -------------------------------- */
    function busy(btn, on, label) {
        if (!btn) return;
        if (on) {
            btn.dataset.orig = btn.textContent;
            btn.classList.add("busy");
            btn.disabled = true;
            btn.textContent = label || "⏳ проверяю…";
        } else {
            btn.classList.remove("busy");
            btn.disabled = false;
            if (btn.dataset.orig) btn.textContent = btn.dataset.orig;
        }
    }

    /* ----- состояние с сервера ------------------------------------------- */
    var schema = {};     // key -> {value, source, secret, restart, section, label, hint, placeholder}
    var maskChar = MASK;
    var googleRedirectUri = "";
    var activeTab = (function () {
        try { return localStorage.getItem("liqscope.settings.tab") || "mail"; }
        catch (e) { return "mail"; }
    })();

    function loadSchema() {
        api("/api/admin/settings").then(function (d) {
            if (!d || !d.ok) {
                $("sys-tabs").innerHTML =
                    '<span class="sys-err">Не удалось загрузить настройки: ' +
                    esc((d && d.error) || "ошибка") + "</span>";
                return;
            }
            if (d.mask) maskChar = d.mask;
            googleRedirectUri = d.google_redirect_uri || (location.origin + "/api/auth/google/callback");
            schema = d.settings || {};
            render();
        });
    }

    /* ----- рендер вкладок и полей ---------------------------------------- */
    function keysInSection(sec) {
        var out = [];
        for (var k in schema) if (schema[k].section === sec) out.push(k);
        return out;
    }

    function sourceBadge(st) {
        if (st.source === "db") return '<span class="sys-src">· из базы</span>';
        if (st.source === "env") return '<span class="sys-src">· из окружения</span>';
        return '<span class="sys-src">· не задано</span>';
    }

    function buildField(key) {
        var st = schema[key];
        var wrap = document.createElement("div");
        wrap.className = "sys-field";
        // data-syskey ставим только на input: на обёртке он ломал поиск поля
        // (querySelector находил div вместо input).
        wrap.dataset.field = key;

        var label = document.createElement("div");
        label.className = "sys-label";
        label.innerHTML = esc(st.label || key) + sourceBadge(st) +
            (st.secret ? '<span class="sys-flag" title="секрет">🔒</span>' : "") +
            (st.restart ? '<span class="sys-flag" title="нужен перезапуск">↻</span>' : "");
        wrap.appendChild(label);

        var row = document.createElement("div");
        row.className = "sys-inrow";

        var inp = document.createElement("input");
        inp.className = "search";
        inp.dataset.syskey = key;
        inp.autocomplete = "off";
        if (st.placeholder) inp.placeholder = st.placeholder;
        if (st.secret) {
            inp.type = "password";
            // секрет не подставляется в поле: пусто = «не менять»
            inp.value = "";
            if (st.value) inp.placeholder = maskChar + " — задан, пустое поле = не менять";
        } else {
            inp.type = "text";
            inp.value = st.value || "";
        }
        row.appendChild(inp);

        if (st.secret) {
            var eye = document.createElement("button");
            eye.type = "button";
            eye.className = "sys-eye";
            eye.textContent = "👁";
            eye.title = "Показать/скрыть";
            eye.addEventListener("click", function () {
                inp.type = inp.type === "password" ? "text" : "password";
            });
            row.appendChild(eye);
        }

        // Кнопка проверки — для ключей ИИ и для отдельных сервисов
        var testBtn = aiTestButton(key);
        if (testBtn) row.appendChild(testBtn);

        wrap.appendChild(row);

        if (st.hint) {
            var hint = document.createElement("div");
            hint.className = "sys-hint";
            hint.textContent = st.hint;
            wrap.appendChild(hint);
        }
        return wrap;
    }

    /* Кнопка «Проверить» для ключей ИИ (один ключ на провайдера). */
    function aiTestButton(key) {
        var map = {
            "LIQSCOPE_AI_GEMINI_KEY": "gemini",
            "LIQSCOPE_AI_GROQ_KEY": "groq",
            "LIQSCOPE_AI_OPENROUTER_KEY": "openrouter",
            "LIQSCOPE_AI_DEEPSEEK_KEY": "deepseek",
            "LIQSCOPE_AI_KEY": "custom",
        };
        var provider = map[key];
        if (!provider) return null;
        var b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-small";
        b.textContent = "🔎 Проверить";
        b.dataset.llmProvider = provider;
        b.dataset.llmKey = key;
        return b;
    }

    function panelFor(sec) {
        var panel = document.createElement("div");
        panel.className = "sys-panel";
        panel.id = "sys-panel-" + sec;

        keysInSection(sec).forEach(function (key) {
            panel.appendChild(buildField(key));
        });

        /* Служебные блоки вкладки: тесты подключения и примечания. */
        if (sec === "mail") {
            var note = document.createElement("p");
            note.className = "sys-note";
            note.textContent = "«Проверить подключение» входит на SMTP-сервер и, " +
                "если указан адрес отправителя, шлёт тестовое письмо. Сервисы " +
                "рассылок (Resend/SendPulse) используются, когда заполнен " +
                "«Сервис рассылок».";
            panel.appendChild(note);
        }
        if (sec === "donate") {
            // Сети и адреса рисуются из схемы сервера — здесь только рамка
            // раздела: заголовок, пояснение и напоминание про пустое поле.
            var dTitle = document.createElement("div");
            dTitle.className = "sys-label";
            dTitle.style.marginBottom = "2px";
            dTitle.textContent = "Кошельки для донатов";
            panel.insertBefore(dTitle, panel.firstChild);

            var dLead = document.createElement("p");
            dLead.className = "sys-note";
            dLead.style.marginTop = "0";
            dLead.textContent = "Адреса показываются гостям в кнопке «Донат» в шапке: " +
                "сеть с пустым полем в этой кнопке не появляется. Адрес проверяется " +
                "по формату сети — опечатку сохранить нельзя, текст ошибки будет ниже.";
            panel.insertBefore(dLead, dTitle.nextSibling);
        }
        if (sec === "security") {
            var rWrap = document.createElement("div");
            rWrap.className = "sys-field";
            var rLabel = document.createElement("div");
            rLabel.className = "sys-label";
            rLabel.innerHTML = 'Google OAuth Redirect URI <span class="sys-src">· только чтение</span>';
            rWrap.appendChild(rLabel);
            var rRow = document.createElement("div");
            rRow.className = "sys-inrow";
            var rInp = document.createElement("input");
            rInp.type = "text";
            rInp.className = "search mono";
            rInp.readOnly = true;
            rInp.id = "sys-google-redirect-uri";
            rInp.value = googleRedirectUri || (location.origin + "/api/auth/google/callback");
            rRow.appendChild(rInp);
            rWrap.appendChild(rRow);
            var rHint = document.createElement("div");
            rHint.className = "sys-hint";
            rHint.textContent = "OAuth consent screen → Web client → Authorized redirect URIs (скопируйте этот адрес в Google Cloud Console).";
            rWrap.appendChild(rHint);
            panel.appendChild(rWrap);

            var cap = document.createElement("div");
            cap.className = "sys-actions";
            var gTestBtn = document.createElement("button");
            gTestBtn.type = "button";
            gTestBtn.className = "btn";
            gTestBtn.id = "sys-google-test";
            gTestBtn.textContent = "🔎 Проверить Google OAuth";
            cap.appendChild(gTestBtn);
            var capBtn = document.createElement("button");
            capBtn.type = "button";
            capBtn.className = "btn";
            capBtn.id = "sys-captcha-test";
            capBtn.textContent = "🧮 Проверить капчу";
            cap.appendChild(capBtn);
            panel.appendChild(cap);
            var capNote = document.createElement("p");
            capNote.className = "sys-note";
            capNote.textContent = "Капча в проекте — встроенная математическая " +
                "(своя, без внешних сервисов), поэтому внешних ключей " +
                "Turnstile/reCAPTCHA здесь нет. Кнопка выполняет само-тест.";
            panel.appendChild(capNote);
        }
        if (sec === "telegram") {
            var tgNote = document.createElement("p");
            tgNote.className = "sys-note";
            tgNote.textContent = "Опрос бота запускается при старте процесса, " +
                "поэтому новый токен подхватится после перезапуска " +
                "(systemctl restart licvid).";
            panel.appendChild(tgNote);
        }
        if (sec === "tuning") {
            var tNote = document.createElement("p");
            tNote.className = "sys-note";
            tNote.textContent = "Значения с отметкой ↻ читаются один раз при " +
                "старте процесса и подхватятся после перезапуска. Пустое поле " +
                "при сохранении возвращает значение из окружения.";
            panel.appendChild(tNote);
        }

        /* Кнопки: проверка подключения (где есть) + сохранение вкладки. */
        var actions = document.createElement("div");
        actions.className = "sys-actions";

        if (sec === "mail") {
            var smtpTest = document.createElement("button");
            smtpTest.type = "button";
            smtpTest.className = "btn";
            smtpTest.id = "sys-smtp-test";
            smtpTest.textContent = "🔌 Проверить подключение";
            actions.appendChild(smtpTest);
        }
        if (sec === "telegram") {
            var tgTest = document.createElement("button");
            tgTest.type = "button";
            tgTest.className = "btn";
            tgTest.id = "sys-tg-test";
            tgTest.textContent = "🔎 Проверить токен";
            actions.appendChild(tgTest);
        }

        var save = document.createElement("button");
        save.type = "button";
        save.className = "btn btn-primary";
        save.dataset.save = sec;
        save.textContent = "💾 Сохранить";
        actions.appendChild(save);

        var status = document.createElement("span");
        status.className = "meta";
        status.id = "sys-status-" + sec;
        status.setAttribute("role", "status");
        actions.appendChild(status);

        panel.appendChild(actions);

        /* Плашки результата — под формой, как требует задача. */
        var warn = document.createElement("p");
        warn.className = "sys-warn";
        warn.id = "sys-warn-" + sec;
        warn.hidden = true;
        panel.appendChild(warn);

        var err = document.createElement("p");
        err.className = "sys-err";
        err.id = "sys-err-" + sec;
        err.hidden = true;
        panel.appendChild(err);

        return panel;
    }

    function render() {
        var tabsBox = $("sys-tabs");
        var panelsBox = $("sys-panels");
        tabsBox.innerHTML = "";
        panelsBox.innerHTML = "";

        TABS.forEach(function (tab) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "sys-tab";
            b.textContent = tab.title;
            b.dataset.tab = tab.id;
            if (tab.id === activeTab) b.classList.add("active");
            b.addEventListener("click", function () {
                activeTab = tab.id;
                try { localStorage.setItem("liqscope.settings.tab", tab.id); } catch (e) {}
                render();
            });
            tabsBox.appendChild(b);
            panelsBox.appendChild(panelFor(tab.id));
            if (tab.id === activeTab) {
                panelsBox.lastChild.classList.add("active");
            }
        });
        bindActions();
    }

    /* ----- сбор и сохранение вкладки ------------------------------------- */
    function setStatus(sec, text, cls) {
        var el = $("sys-status-" + sec);
        if (!el) return;
        el.textContent = text || "";
        el.className = cls ? "meta " + cls : "meta";
    }
    function setErr(sec, text) {
        var el = $("sys-err-" + sec);
        if (!el) return;
        el.textContent = text || "";
        el.hidden = !text;
    }
    function setWarn(sec, text) {
        var el = $("sys-warn-" + sec);
        if (!el) return;
        el.textContent = text || "";
        el.hidden = !text;
    }

    function collect(sec) {
        var payload = {};
        // Селектор обязан целиться в input: у обёртки поля тоже есть
        // data-syskey, и она идёт в разметке раньше — querySelector возвращал
        // div, у него нет value, и введённое значение уходило пустой строкой
        // (сервер трактует пусто как «снять переопределение»), а секрет —
        // вовсе не отправлялся.
        var inputs = document.querySelectorAll(
            '#sys-panel-' + sec + ' input[data-syskey]');
        for (var i = 0; i < inputs.length; i++) {
            var key = inputs[i].dataset.syskey;
            var st = schema[key] || {};
            var val = inputs[i].value == null ? "" : String(inputs[i].value).trim();
            if (st.secret) {
                // пусто или маска = «не менять» — ключ не отправляем
                if (!val || val.indexOf(maskChar) !== -1) continue;
            }
            payload[key] = val;
        }
        return payload;
    }

    function saveSection(sec, btn) {
        var payload = collect(sec);
        setErr(sec, "");
        setWarn(sec, "");
        setStatus(sec, "сохраняю…");
        busy(btn, true, "💾 сохраняю…");
        api("/api/admin/settings", {
            method: "POST",
            body: JSON.stringify(payload),
        }).then(function (d) {
            busy(btn, false);
            if (!d || !d.ok) {
                setStatus(sec, "");
                setErr(sec, (d && (d.error || d.hint)) || "Ошибка сохранения");
                return;
            }
            var savedCount = Object.keys(d.saved || {}).length;
            setStatus(sec, savedCount ? "Сохранено ✓" : "Изменений нет",
                      savedCount ? "sys-ok-plate" : "");
            if (d.warnings && d.warnings.length) setWarn(sec, d.warnings.join("\n"));
            loadSchema();   // обновить маски/источники
        });
    }

    /* ----- проверки подключения ------------------------------------------ */
    function sectionValue(sec, key) {
        // Именно input: у обёртки поля тоже есть data-syskey (см. collect).
        var inp = document.querySelector(
            '#sys-panel-' + sec + ' input[data-syskey="' + key + '"]');
        return inp ? String(inp.value || "").trim() : "";
    }

    function testSmtp(btn) {
        var body = {
            host: sectionValue("mail", "LIQSCOPE_SMTP_HOST"),
            port: sectionValue("mail", "LIQSCOPE_SMTP_PORT"),
            user: sectionValue("mail", "LIQSCOPE_SMTP_USER"),
            tls: sectionValue("mail", "LIQSCOPE_SMTP_TLS"),
            from: sectionValue("mail", "LIQSCOPE_SMTP_FROM"),
        };
        var pass = sectionValue("mail", "LIQSCOPE_SMTP_PASSWORD");
        if (pass && pass.indexOf(maskChar) === -1) body.password = pass;
        setErr("mail", "");
        setStatus("mail", "");
        busy(btn, true, "🔌 проверяю…");
        api("/api/admin/settings/test-smtp", { method: "POST", body: JSON.stringify(body) })
            .then(function (d) {
                busy(btn, false);
                if (d && d.ok) setStatus("mail", "Подключение работает ✓", "sys-ok-plate");
                else setErr("mail", (d && d.error) || "Ошибка SMTP: нет ответа сервера");
            });
    }

    function testTg(btn) {
        var body = {};
        var tok = sectionValue("telegram", "LIQSCOPE_BOT_TOKEN");
        if (tok && tok.indexOf(maskChar) === -1) body.token = tok;
        setErr("telegram", "");
        setStatus("telegram", "");
        busy(btn, true, "🔎 проверяю…");
        api("/api/admin/settings/test-tg", { method: "POST", body: JSON.stringify(body) })
            .then(function (d) {
                busy(btn, false);
                if (d && d.ok) {
                    var name = d.username ? " (@" + d.username + ")" : "";
                    setStatus("telegram", "Токен действителен ✓" + name, "sys-ok-plate");
                } else setErr("telegram", (d && d.error) || "Invalid Telegram Token");
            });
    }

    function testLlm(provider, keyField, btn) {
        var body = { provider: provider };
        var val = sectionValue("ai", keyField);
        if (val && val.indexOf(maskChar) === -1) body.key = val;
        // модель и свой URL — из соседних полей вкладки ИИ
        var modelKey = keyField.replace(/_KEY$/, "_MODEL");
        var urlKey = keyField.replace(/_KEY$/, "_URL");
        if (schema[modelKey]) body.model = sectionValue("ai", modelKey);
        if (schema[urlKey]) body.url = sectionValue("ai", urlKey);
        setErr("ai", "");
        setStatus("ai", "");
        busy(btn, true, "🔎 проверяю…");
        api("/api/admin/settings/test-llm", { method: "POST", body: JSON.stringify(body) })
            .then(function (d) {
                busy(btn, false);
                if (d && d.ok) {
                    setStatus("ai", "«" + provider + "» работает ✓" +
                              (d.model ? " (" + d.model + ")" : ""), "sys-ok-plate");
                } else setErr("ai", (d && d.error) || "Ошибка ИИ: нет ответа");
            });
    }

    function testCaptcha(btn) {
        setErr("security", "");
        setStatus("security", "");
        busy(btn, true, "🧮 проверяю…");
        api("/api/admin/settings/test-captcha", { method: "POST", body: "{}" })
            .then(function (d) {
                busy(btn, false);
                if (d && d.ok) setStatus("security", d.message || "Капча работает ✓", "sys-ok-plate");
                else setErr("security", (d && d.error) || "Ошибка капчи");
            });
    }

    function testGoogle(btn) {
        var body = {
            client_id: sectionValue("security", "LIQSCOPE_GOOGLE_CLIENT_ID"),
        };
        var sec = sectionValue("security", "LIQSCOPE_GOOGLE_CLIENT_SECRET");
        if (sec && sec.indexOf(maskChar) === -1) body.client_secret = sec;
        setErr("security", "");
        setStatus("security", "");
        busy(btn, true, "🔎 проверяю…");
        api("/api/admin/settings/test-google", { method: "POST", body: JSON.stringify(body) })
            .then(function (d) {
                busy(btn, false);
                if (d && d.ok) setStatus("security", d.message || "Google OAuth настроен ✓", "sys-ok-plate");
                else setErr("security", (d && d.error) || "Ошибка проверки Google OAuth");
            });
    }

    /* ----- привязка обработчиков после рендера --------------------------- */
    function bindActions() {
        TABS.forEach(function (tab) {
            var save = document.querySelector('[data-save="' + tab.id + '"]');
            if (save) save.addEventListener("click", function () { saveSection(tab.id, save); });
        });
        var smtp = $("sys-smtp-test");
        if (smtp) smtp.addEventListener("click", function () { testSmtp(smtp); });
        var tg = $("sys-tg-test");
        if (tg) tg.addEventListener("click", function () { testTg(tg); });
        var cap = $("sys-captcha-test");
        if (cap) cap.addEventListener("click", function () { testCaptcha(cap); });
        var gTest = $("sys-google-test");
        if (gTest) gTest.addEventListener("click", function () { testGoogle(gTest); });
        document.querySelectorAll("[data-llm-provider]").forEach(function (b) {
            b.addEventListener("click", function () {
                testLlm(b.dataset.llmProvider, b.dataset.llmKey, b);
            });
        });
    }

    loadSchema();
})();
