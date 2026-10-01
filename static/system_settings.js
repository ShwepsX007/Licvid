/**
 * ⚙️ Менеджер системных настроек админки.
 *
 * Конфигурация сервиса (SMTP, токен Telegram-бота, админские доступы, лимиты)
 * хранится в базе (таблица system_settings) и меняется из админки без
 * перезапуска сервера. Секреты сервер отдаёт строкой «***»; пустое поле
 * секрета при сохранении означает «не менять».
 *
 * Ручки (см. web_account.py):
 *   GET  /api/admin/settings               — все настройки (секреты маской)
 *   POST /api/admin/settings               — сохранить изменённые ключи
 *   POST /api/admin/settings/test-smtp     — live-проверка SMTP (ошибка → 400)
 *   POST /api/admin/settings/test-tg       — live-проверка токена (ошибка → 400)
 *
 * Ошибки проверок выводятся красным прямо под формой карточки.
 */
(function () {
    "use strict";
    if (!document.body || document.body.getAttribute("data-page") !== "admin") return;

    var MASK = "***";

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

    function setStatus(id, text, cls) {
        var el = $(id);
        if (!el) return;
        el.textContent = text || "";
        el.className = cls ? "meta " + cls : "meta";
    }

    function setErr(id, text) {
        var el = $(id);
        if (!el) return;
        el.textContent = text || "";
        el.hidden = !text;
    }

    function setWarn(id, text) {
        var el = $(id);
        if (!el) return;
        el.textContent = text || "";
        el.hidden = !text;
    }

    /* ----- состояние с сервера --------------------------------------- */
    var state = {};   // key -> {value, source, secret, restart, section}
    var maskChar = MASK;

    function loadSettings() {
        api("/api/admin/settings").then(function (d) {
            if (!d || !d.ok) return;
            if (d.mask) maskChar = d.mask;
            state = d.settings || {};
            paint();
        });
    }

    function paint() {
        document.querySelectorAll("[data-syskey]").forEach(function (inp) {
            var key = inp.getAttribute("data-syskey");
            var st = state[key];
            if (!st) return;
            var src = $("src-" + key);
            if (src) {
                src.textContent = st.source === "db" ? "· из базы"
                    : st.source === "env" ? "· из окружения" : "· не задано";
            }
            if (st.secret) {
                // секрет никогда не подставляется в поле: пусто = «не менять»
                inp.value = "";
                inp.placeholder = st.value ? maskChar + " — задан, оставьте пустым, чтобы не менять"
                                           : "не задан";
            } else {
                inp.value = st.value || "";
            }
        });
    }

    /* ----- сбор и сохранение секции ----------------------------------- */
    function sectionKeys(box) {
        var keys = [];
        box.querySelectorAll("[data-syskey]").forEach(function (inp) {
            keys.push(inp.getAttribute("data-syskey"));
        });
        return keys;
    }

    function collect(box) {
        var payload = {};
        box.querySelectorAll("[data-syskey]").forEach(function (inp) {
            var key = inp.getAttribute("data-syskey");
            var st = state[key] || {};
            var val = inp.value == null ? "" : String(inp.value).trim();
            if (st.secret) {
                // пустой ввод или маска = «не менять» — ключ не отправляем
                if (!val || val.indexOf(maskChar) === 0) return;
            }
            payload[key] = val;
        });
        return payload;
    }

    function saveSection(box, statusId, errId, warnId) {
        var payload = collect(box);
        setErr(errId, "");
        setWarn(warnId, "");
        setStatus(statusId, "сохраняю…");
        api("/api/admin/settings", {
            method: "POST",
            body: JSON.stringify(payload),
        }).then(function (d) {
            if (!d || !d.ok) {
                setStatus(statusId, "");
                setErr(errId, (d && (d.error || d.hint)) || "Ошибка сохранения");
                return;
            }
            var savedCount = Object.keys(d.saved || {}).length;
            setStatus(statusId, savedCount ? "Сохранено ✓" : "Изменений нет",
                      savedCount ? "sys-ok" : "");
            if (d.warnings && d.warnings.length) {
                setWarn(warnId, d.warnings.join("\n"));
            }
            loadSettings();  // обновить маски/источники
        });
    }

    /* ----- проверки подключения ---------------------------------------- */
    function testSmtp(box) {
        var body = {};
        function val(key) {
            var inp = box.querySelector('[data-syskey="' + key + '"]');
            return inp ? String(inp.value || "").trim() : "";
        }
        body.host = val("LIQSCOPE_SMTP_HOST");
        body.port = val("LIQSCOPE_SMTP_PORT");
        body.user = val("LIQSCOPE_SMTP_USER");
        body.tls = val("LIQSCOPE_SMTP_TLS");
        body.from = val("LIQSCOPE_SMTP_FROM");
        var pass = val("LIQSCOPE_SMTP_PASSWORD");
        if (pass && pass.indexOf(maskChar) !== 0) body.password = pass;
        setErr("sys-smtp-err", "");
        setStatus("sys-smtp-status", "проверяю подключение…");
        api("/api/admin/settings/test-smtp", {
            method: "POST",
            body: JSON.stringify(body),
        }).then(function (d) {
            if (d && d.ok) {
                setStatus("sys-smtp-status", "Подключение работает ✓", "sys-ok");
                setErr("sys-smtp-err", "");
            } else {
                setStatus("sys-smtp-status", "");
                setErr("sys-smtp-err", (d && d.error) || "Ошибка SMTP: нет ответа сервера");
            }
        });
    }

    function testTg() {
        var inp = $("sys-tg-token");
        var body = {};
        var tok = inp ? String(inp.value || "").trim() : "";
        if (tok && tok.indexOf(maskChar) !== 0) body.token = tok;
        setErr("sys-tg-err", "");
        setWarn("sys-tg-warn", "");
        setStatus("sys-tg-status", "проверяю токен…");
        api("/api/admin/settings/test-tg", {
            method: "POST",
            body: JSON.stringify(body),
        }).then(function (d) {
            if (d && d.ok) {
                var name = d.username ? " (@" + esc(d.username) + ")" : "";
                setStatus("sys-tg-status", "Токен действителен ✓" + name, "sys-ok");
            } else {
                setStatus("sys-tg-status", "");
                setErr("sys-tg-err", (d && d.error) || "Invalid Telegram Token");
            }
        });
    }

    /* ----- привязка ------------------------------------------------------
     * Каждая кнопка «Сохранить» берёт поля своей карточки (.sys-card) и
     * шлёт только их; кнопки проверки вызывают живые тесты подключения. */
    [["sys-smtp-save", "sys-smtp-status", "sys-smtp-err", null],
     ["sys-tg-save", "sys-tg-status", "sys-tg-err", "sys-tg-warn"],
     ["sys-acc-save", "sys-acc-status", "sys-acc-err", null],
     ["sys-lim-save", "sys-lim-status", "sys-lim-err", "sys-lim-warn"]
    ].forEach(function (cfg) {
        var btn = $(cfg[0]);
        if (!btn) return;
        btn.addEventListener("click", function () {
            var card = btn.closest(".sys-card");
            if (!card) return;
            saveSection(card, cfg[1], cfg[2], cfg[3]);
        });
    });

    var smtpTest = $("sys-smtp-test");
    if (smtpTest) smtpTest.addEventListener("click", function () {
        var card = smtpTest.closest(".sys-card");
        if (card) testSmtp(card);
    });
    var tgTest = $("sys-tg-test");
    if (tgTest) tgTest.addEventListener("click", testTg);

    loadSettings();
})();
