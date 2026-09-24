/**
 * Проверка админского раздела «Архив — удаление старых выпусков».
 *
 * Раздел открывается на /admin, показывает, что лежит в архиве дайджестов и
 * сводок по часам, и умеет снимать старые записи: со страниц /digest и /hourly,
 * а с галочкой «и из Telegram» — и вышедшие посты в каналах.
 *
 * Запуск (сервер уже поднят на 8000):
 *   LIQSCOPE_TEST_URL=http://127.0.0.1:8000 node tests/archive_delete_ui.js
 *
 * Тест сам наполняет архив сводкой (POST /api/hourly/collect) и удаляет её
 * через интерфейс, так что после прогона архив остаётся таким, каким был.
 * Выпуск дня не удаляем: проверяем, что кнопка спрашивает подтверждение и
 * называет день, а сама запись остаётся на месте.
 */
"use strict";

const { JSDOM, VirtualConsole } = require("jsdom");

const BASE = process.env.LIQSCOPE_TEST_URL || "http://127.0.0.1:8000";
const EMAIL = process.env.LIQSCOPE_TEST_EMAIL || "live@liqscope.online";
const PASSWORD = process.env.LIQSCOPE_TEST_PASSWORD || "licvid-demo-2026";

let pass = 0;
let fail = 0;

function ok(name, cond, extra) {
    if (cond) {
        pass++;
        console.log("  ✓ " + name);
    } else {
        fail++;
        console.log("  ✗ " + name + (extra ? " — " + extra : ""));
    }
}

function wait(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

async function waitFor(fn, ms = 25000) {
    const until = Date.now() + ms;
    while (Date.now() < until) {
        try {
            if (fn()) return true;
        } catch (e) { /* ещё не готово */ }
        await wait(150);
    }
    return false;
}

async function login() {
    if (process.env.LIQSCOPE_TEST_COOKIE) return process.env.LIQSCOPE_TEST_COOKIE;
    const res = await fetch(BASE + "/api/auth/email/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    });
    const cookie = (res.headers.getSetCookie ? res.headers.getSetCookie() : [])
        .map((c) => c.split(";")[0]).join("; ");
    if (!cookie) throw new Error("не вошли: " + res.status);
    return cookie;
}

function makeFetch(cookie) {
    return function (url, opts) {
        const full = String(url).startsWith("http") ? url : BASE + url;
        const headers = Object.assign({}, (opts && opts.headers) || {}, { Cookie: cookie });
        return fetch(full, Object.assign({}, opts, { headers, redirect: "follow" }));
    };
}

async function api(cookie, path, body) {
    const res = await fetch(BASE + path, {
        method: body === undefined ? "GET" : "POST",
        headers: { Cookie: cookie, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    return res.json().catch(() => ({}));
}

/** Собрать сводку сейчас: она попадёт в архив раздела, без Telegram. */
async function seedPost(cookie) {
    const data = await api(cookie, "/api/hourly/collect", {});
    return (data.item || {}).id || "";
}

async function boot(cookie) {
    const html = await (await fetch(BASE + "/admin", {
        headers: { Cookie: cookie },
    })).text();
    const vc = new VirtualConsole();
    const errors = [];
    vc.on("jsdomError", (e) => errors.push(String((e && e.message) || e)));
    const dom = new JSDOM(html, {
        url: BASE + "/admin",
        runScripts: "dangerously",
        pretendToBeVisual: true,
        resources: undefined,
        virtualConsole: vc,
    });
    const win = dom.window;
    win.fetch = makeFetch(cookie);
    win.alert = () => {};
    win.confirm = () => true;
    for (const f of ["i18n.pages.js", "i18n.js", "archive_admin.js"]) {
        const code = await (await fetch(BASE + "/static/" + f)).text();
        win.eval(code);
    }
    win.document.dispatchEvent(new win.Event("DOMContentLoaded"));
    return { dom, win, errors };
}

process.on("uncaughtException", (e) => {
    console.error("Непойманная ошибка:", e);
    process.exit(2);
});

(async function main() {
    console.log("Архив — удаление выпусков: " + BASE);
    const cookie = await login();

    const { win, errors } = await boot(cookie);
    const doc = win.document;

    ok("раздел «Архив» есть на странице админки", !!doc.getElementById("archive-admin"));
    ok("есть кнопка «Обновить архив»", !!doc.getElementById("arc-reload"));
    const box = doc.getElementById("arc-tg");
    ok("галочка «и из Telegram» включена по умолчанию", !!box && !!box.checked);
    ok("обе колонки на месте",
        !!doc.getElementById("arc-digests") && !!doc.getElementById("arc-hourly"));
    ok("скрипт без ошибок", errors.length === 0, errors.join(" | "));

    const digests = await api(cookie, "/api/admin/digest/archive");
    ok("выпуски дня загрузились в интерфейс",
        await waitFor(() => {
            const list = doc.getElementById("arc-digests");
            return list && !/загружаю/.test(list.textContent || "")
                && list.querySelectorAll(".arc-row").length === (digests.items || []).length;
        }),
        (doc.getElementById("arc-digests") || {}).textContent);
    ok("у выпуска видна дата и кнопка удаления",
        /\d{4}-\d{2}-\d{2}/.test((doc.getElementById("arc-digests") || {}).textContent || "")
        && !!doc.querySelector("#arc-digests .arc-del"));
    ok("у выпуска без id из Telegram честный бейдж",
        /нет id/.test((doc.getElementById("arc-digests") || {}).textContent || ""));

    // Кнопка удаления выпуска спрашивает подтверждение и не сносит запись,
    // если админ отказался (иначе один промах уносил бы день из архива).
    let asked = "";
    win.confirm = (msg) => { asked = String(msg || ""); return false; };
    const before = (await api(cookie, "/api/admin/digest/archive")).count;
    const digestBtn = doc.querySelector("#arc-digests .arc-del");
    if (digestBtn) {
        digestBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
        await wait(400);
    }
    const after = (await api(cookie, "/api/admin/digest/archive")).count;
    ok("перед удалением выпуска спрашивают подтверждение",
        /Удалить выпуск/.test(asked) && /\d{4}-\d{2}-\d{2}/.test(asked), asked);
    ok("отказ оставляет выпуск на месте", before === after && before > 0,
        "было " + before + ", стало " + after);

    // Сводки по часам: наполняем архив, обновляем список и удаляем через интерфейс
    const pid = await seedPost(cookie);
    ok("сводка собрана для проверки", !!pid, "id не пришёл");
    ok("кнопка «Обновить архив» показала новую сводку",
        await waitFor(() => /архив|сводк/i.test(
            (doc.getElementById("arc-status") || {}).textContent || ""))
        && await (async function () {
            const btn = doc.getElementById("arc-reload");
            if (btn) btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
            return await waitFor(() =>
                !!doc.querySelector("#arc-hourly .arc-row[data-id='" + pid + "']"));
        })());
    const hourRow = doc.querySelector("#arc-hourly .arc-row[data-id='" + pid + "']");
    ok("сводка попала в список архива", !!hourRow,
        (doc.getElementById("arc-hourly") || {}).textContent);
    ok("у сводки есть кнопка «Удалить»", !!(hourRow && hourRow.querySelector(".arc-del")));
    ok("есть кнопка «Весь день»",
        !!doc.querySelector("#arc-hourly .arc-day, .arc-day"));

    win.confirm = () => true;
    if (hourRow) {
        hourRow.querySelector(".arc-del")
            .dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    }
    const gone = await waitFor(() =>
        !doc.querySelector("#arc-hourly .arc-row[data-id='" + pid + "']"));
    ok("сводка исчезла из списка после удаления", gone);
    const left = await api(cookie, "/api/admin/hourly/archive");
    ok("в архиве на сервере её тоже нет",
        !(left.items || []).some((x) => x.id === pid), "осталось: " + (left.count || 0));
    ok("в подписи сказано, что удалено",
        /удал/i.test((doc.getElementById("arc-status") || {}).textContent || ""),
        (doc.getElementById("arc-status") || {}).textContent);

    // Убираем за собой: если сводка не удалилась из интерфейса, снимаем через API
    if (!gone && pid) {
        await api(cookie, "/api/admin/hourly/" + encodeURIComponent(pid) + "/delete",
                  { tg: false });
        console.log("  · сводка удалена напрямую (интерфейс не сработал)");
    }

    console.log("\nИтого: " + pass + " ок, " + fail + " провал(ов)");
    process.exit(fail ? 1 : 0);
})().catch((e) => {
    console.error("Тест не смог запуститься:", e);
    process.exit(2);
});
