/**
 * ❤️ Кнопка «Донат» в шапке: адреса крипто-кошельков и копирование в буфер.
 *
 * Платёжек нет — показываем адреса, которые администратор заполнил в админке
 * (Системные настройки → «❤️ Донат»). Публичный ответ — GET /api/donate/wallets:
 *
 *     {"ok": true, "wallets": [{"id": "ethereum", "label": "Ethereum",
 *                               "short": "ETH", "color": "#627EEA",
 *                               "address": "0x…"}, …]}
 *
 * Что важно:
 *   • адреса не зашиты в JS — только из ручки, и подставляются через
 *     textContent (innerHTML с адресом — это XSS из админки);
 *   • нет ни одного заполненного адреса — кнопки нет вовсе (не занимаем
 *     шапку заглушкой);
 *   • в iframe графиков (embed=1 / mode=panel) кнопки нет;
 *   • список кэшируется на клиенте 5 минут, чтобы шапка не дёргала ручку
 *     на каждой странице.
 */
(function (global) {
    "use strict";

    var q = new URLSearchParams(global.location.search);
    if (q.get("embed") === "1" || q.get("mode") === "panel") return;

    var API = "/api/donate/wallets";
    var CACHE_KEY = "liqscope.donate.wallets";
    var CACHE_TTL = 5 * 60 * 1000;          // 5 минут — как в задаче
    var TOAST_MS = 1800;                    // «Адрес скопирован» на 1.8 с

    //: Запасной текст, если словари ещё не догрузились.
    var FALLBACK = {
        ru: { btn: "Донат", title: "Поддержать проект", hint: "Нажмите на адрес — он скопируется",
              copied: "Адрес скопирован", copy: "Скопировать адрес", empty: "Донаты временно не настроены" },
        en: { btn: "Donate", title: "Support the project", hint: "Tap an address to copy it",
              copied: "Address copied", copy: "Copy address", empty: "Donations are not set up yet" },
    };
    var KEYS = { btn: "donate.btn", title: "donate.title", hint: "donate.hint",
                 copied: "donate.copied", copy: "donate.copy", empty: "donate.empty" };

    var wallets = null;          // список с сервера
    var pop = null;              // выпадающий список
    var wrap = null;             // обёртка кнопки в шапке
    var btn = null;              // сама кнопка
    var toast = null;
    var toastTimer = null;

    function t(name) {
        var key = KEYS[name] || name;
        if (global.LiqScopeI18n && LiqScopeI18n.t) {
            var s = LiqScopeI18n.t(key);
            if (s && s !== key) return s;
        }
        var lang = (global.LiqScopeI18n && LiqScopeI18n.lang && LiqScopeI18n.lang()) || "ru";
        var pack = FALLBACK[lang] || FALLBACK.en;
        return pack[name] || "";
    }

    /* ---------- данные ---------- */

    function fromCache() {
        try {
            var raw = sessionStorage.getItem(CACHE_KEY);
            if (!raw) return null;
            var box = JSON.parse(raw);
            if (!box || Date.now() - Number(box.at || 0) > CACHE_TTL) return null;
            return Array.isArray(box.wallets) ? box.wallets : null;
        } catch (e) { return null; }
    }

    function toCache(list) {
        try {
            sessionStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), wallets: list }));
        } catch (e) { /* приватный режим — переживём */ }
    }

    function fetchWallets() {
        var cached = fromCache();
        if (cached) return Promise.resolve(cached);
        return fetch(API, { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var list = (d && d.ok && Array.isArray(d.wallets)) ? d.wallets : [];
                toCache(list);
                return list;
            })
            .catch(function () { return []; });
    }

    /* ---------- вид ---------- */

    function shorten(addr) {
        var s = String(addr || "");
        if (s.length <= 14) return s;
        return s.slice(0, 6) + "…" + s.slice(-4);
    }

    /** Скопировать текст: Clipboard API, иначе старый способ через textarea. */
    function copyText(text) {
        if (global.navigator && navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text).catch(function () {
                return legacyCopy(text);
            });
        }
        return Promise.resolve(legacyCopy(text));
    }

    function legacyCopy(text) {
        try {
            var area = document.createElement("textarea");
            area.value = text;
            area.setAttribute("readonly", "");
            area.style.position = "fixed";
            area.style.top = "-1000px";
            area.style.opacity = "0";
            document.body.appendChild(area);
            area.select();
            var ok = document.execCommand && document.execCommand("copy");
            document.body.removeChild(area);
            return !!ok;
        } catch (e) { return false; }
    }

    function showToast() {
        if (!toast) {
            toast = document.createElement("div");
            toast.className = "donate-toast";
            toast.setAttribute("role", "status");
            toast.setAttribute("aria-live", "polite");
            document.body.appendChild(toast);
        }
        toast.textContent = t("copied");
        toast.classList.add("is-on");
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function () {
            toast.classList.remove("is-on");
        }, TOAST_MS);
    }

    /** Строка списка: сеть, сокращённый адрес и значок копирования. */
    function rowFor(wallet) {
        var row = document.createElement("button");
        row.type = "button";
        row.className = "donate-row";
        row.setAttribute("role", "menuitem");
        row.setAttribute("data-net", String(wallet.id || ""));
        row.setAttribute("title", t("copy"));

        var net = document.createElement("span");
        net.className = "donate-net";
        var dot = document.createElement("i");
        dot.className = "donate-dot";
        // Цвет сети приходит из нашего же списка сетей, но подставляем
        // через style + проверку: значение из ответа не должно ломать вёрстку.
        var color = String(wallet.color || "");
        if (/^#[0-9a-fA-F]{3,8}$/.test(color)) dot.style.background = color;
        net.appendChild(dot);
        var label = document.createElement("span");
        label.className = "donate-net-label";
        label.textContent = String(wallet.label || wallet.id || "");
        net.appendChild(label);
        row.appendChild(net);

        var addr = document.createElement("span");
        addr.className = "donate-addr";
        // Только textContent: адрес приходит из админки, innerHTML тут нельзя.
        addr.textContent = shorten(wallet.address);
        row.appendChild(addr);

        var icon = document.createElement("span");
        icon.className = "donate-copy";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "⧉";
        row.appendChild(icon);

        row.addEventListener("click", function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            copyText(String(wallet.address || "")).then(function () {
                showToast();
                closePop();
            });
        });
        return row;
    }

    function buildPop() {
        pop = document.createElement("div");
        pop.className = "donate-pop";
        pop.setAttribute("role", "menu");
        pop.hidden = true;

        var head = document.createElement("div");
        head.className = "donate-pop-head";
        head.textContent = t("title");
        pop.appendChild(head);

        var box = document.createElement("div");
        box.className = "donate-rows";
        wallets.forEach(function (w) { box.appendChild(rowFor(w)); });
        pop.appendChild(box);

        var hint = document.createElement("div");
        hint.className = "donate-hint";
        hint.textContent = t("hint");
        pop.appendChild(hint);

        document.body.appendChild(pop);
    }

    function placePop() {
        if (!pop || !btn) return;
        var r = btn.getBoundingClientRect();
        var width = pop.offsetWidth || 300;
        var left = Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8));
        var top = r.bottom + 8;
        // Не помещается вниз — раскрываем вверх.
        if (top + pop.offsetHeight > window.innerHeight - 8) {
            top = Math.max(8, r.top - pop.offsetHeight - 8);
        }
        pop.style.left = Math.round(left) + "px";
        pop.style.top = Math.round(top) + "px";
    }

    function openPop() {
        if (!pop || !wallets || !wallets.length) return;
        pop.hidden = false;
        placePop();
        btn.setAttribute("aria-expanded", "true");
        wrap.classList.add("is-open");
    }

    function closePop() {
        if (!pop || pop.hidden) return;
        pop.hidden = true;
        if (btn) btn.setAttribute("aria-expanded", "false");
        if (wrap) wrap.classList.remove("is-open");
    }

    function togglePop(ev) {
        if (ev) ev.preventDefault();
        if (pop && !pop.hidden) closePop();
        else openPop();
    }

    function onDocClick(ev) {
        if (!pop || pop.hidden) return;
        var node = ev.target;
        if (pop.contains(node) || (wrap && wrap.contains(node))) return;
        closePop();
    }

    function onKey(ev) {
        if (ev.key === "Escape" || ev.key === "Esc") closePop();
    }

    /* ---------- установка в шапку ---------- */

    function mount() {
        var nav = document.querySelector("header nav");
        if (!nav || document.getElementById("nav-donate")) return;

        wrap = document.createElement("span");
        wrap.className = "nav-donate";
        wrap.id = "nav-donate";

        btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn nav-donate-btn";
        btn.id = "nav-donate-btn";
        btn.setAttribute("aria-haspopup", "true");
        btn.setAttribute("aria-expanded", "false");
        btn.setAttribute("title", t("title"));
        btn.appendChild(document.createTextNode("❤ "));
        var label = document.createElement("span");
        label.className = "nav-donate-label";
        label.textContent = t("btn");
        btn.appendChild(label);
        btn.addEventListener("click", togglePop);

        wrap.appendChild(btn);
        // В конец шапки: рядом с «Кабинет»/«Войти», но вне блока, который
        // перерисовывает account.js при смене входа.
        nav.appendChild(wrap);

        buildPop();
        document.addEventListener("click", onDocClick);
        document.addEventListener("keydown", onKey);
        window.addEventListener("resize", closePop);
        window.addEventListener("scroll", closePop, true);
    }

    function paint() {
        if (!btn) return;
        var label = btn.querySelector(".nav-donate-label");
        if (label) label.textContent = t("btn");
        btn.setAttribute("title", t("title"));
        if (pop) {
            var head = pop.querySelector(".donate-pop-head");
            if (head) head.textContent = t("title");
            var hint = pop.querySelector(".donate-hint");
            if (hint) hint.textContent = t("hint");
        }
    }

    function render(list) {
        wallets = Array.isArray(list) ? list.filter(function (w) {
            return w && w.address;
        }) : [];
        if (!wallets.length) return;           // нет адресов — нет кнопки
        mount();
        if (global.LiqScopeI18n && LiqScopeI18n.onChange) {
            LiqScopeI18n.onChange(function () {
                paint();
                // Тексты строк — только подписи сетей и адреса, их не переводим,
                // но шапку списка и подсказку обновляем.
            });
        }
    }

    function boot() {
        fetchWallets().then(render);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }

    //: Для тестов: отдать загруженный список и перерисовать кнопку.
    global.LiqScopeDonate = {
        render: render,
        wallets: function () { return wallets || []; },
        close: closePop,
    };
})(window);
