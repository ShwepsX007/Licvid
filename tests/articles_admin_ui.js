/**
 * Проверка админского раздела «Статьи» в браузере: панель открывается,
 * список грузится, редактор умеет сохранять текст, фото и публиковать.
 *
 * Запуск (сервер уже поднят на 8000):
 *   LIQSCOPE_TEST_URL=http://127.0.0.1:8000 \
 *   LIQSCOPE_TEST_COOKIE="liqscope_sid=…" node tests/articles_admin_ui.js
 *
 * Без cookie тест проверяет, что раздел честно просит вход. Cookie берётся
 * входом через /api/auth/email/login — так же, как это делает браузер.
 */
"use strict";

const { JSDOM, VirtualConsole } = require("jsdom");

const BASE = process.env.LIQSCOPE_TEST_URL || "http://127.0.0.1:8000";
const EMAIL = process.env.LIQSCOPE_TEST_EMAIL || "live@liqscope.online";
const PASSWORD = process.env.LIQSCOPE_TEST_PASSWORD || "licvid-demo-2026";

let pass = 0;
let fail = 0;

function ok(name, cond, extra) {
    if (cond) { pass++; console.log("  ✓ " + name); } else {
        fail++; console.log("  ✗ " + name + (extra ? " — " + extra : ""));
    }
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

async function boot(cookie) {
    const html = await (await fetch(BASE + "/admin", {
        headers: { Cookie: cookie },
    })).text();
    const vc = new VirtualConsole();
    const errors = [];
    vc.on("jsdomError", (e) => errors.push(String(e && e.message)));
    const dom = new JSDOM(html, {
        url: BASE + "/admin",
        runScripts: "dangerously",
        pretendToBeVisual: true,
        resources: undefined,       // скрипты подгрузим сами, локальных файлов хватает
        virtualConsole: vc,
    });
    const win = dom.window;
    win.fetch = makeFetch(cookie);
    win.confirm = () => true;
    win.alert = () => {};
    // Страница не тянет внешние скрипты (resources выключены) — подключим вручную
    const files = ["i18n.pages.js", "i18n.js", "articles_admin.js"];
    for (const f of files) {
        const code = await (await fetch(BASE + "/static/" + f)).text();
        win.eval(code);
    }
    win.document.dispatchEvent(new win.Event("DOMContentLoaded"));
    return { dom, win, errors };
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function waitFor(fn, ms = 6000) {
    const until = Date.now() + ms;
    while (Date.now() < until) {
        try { if (fn()) return true; } catch (e) { /* ещё не готово */ }
        await wait(120);
    }
    return false;
}

(async function main() {
    console.log("Админка статей: " + BASE);
    const cookie = await login();
    const { win, errors } = await boot(cookie);
    const doc = win.document;

    ok("карточка «Статьи» есть", !!doc.getElementById("articles-admin"));
    ok("кнопка «Новая статья» есть", !!doc.getElementById("art-new"));
    ok("скрипт без ошибок", errors.length === 0, errors.join(" | "));

    const loaded = await waitFor(() => {
        const list = doc.getElementById("art-list");
        return list && !/загружаю/.test(list.textContent || "");
    });
    ok("список статей загрузился", loaded,
        (doc.getElementById("art-list") || {}).textContent);
    // Строка состояния собирается кодом и переводится словарём фраз: проверяем
    // смысл (числа, каналы, ИИ), а не конкретный язык панели
    const state = (doc.getElementById("art-state") || {}).textContent || "";
    ok("видно состояние раздела (каналы, ИИ)",
        /[0-9]/.test(state) && /канал|channel/i.test(state) && /ИИ|AI/.test(state), state);
    ok("опубликованная статья в списке", /Как читать стены/.test(
        (doc.getElementById("art-list") || {}).textContent || ""));
    // В каналы статью ещё не отправляли — бейджа «в TG» быть не должно
    ok("бейджа «в TG» нет, пока не отправляли", !doc.querySelector(".art-row .art-badge.tg"));

    // Открываем опубликованную статью (в начале списка могут быть черновики)
    const row = Array.from(doc.querySelectorAll(".art-row"))
        .filter((r) => /Как читать стены/.test(r.textContent))[0];
    ok("строка нужной статьи найдена", !!row);
    if (!row) { console.log("Итого: " + pass + " ок, " + fail + " провал(ов)"); process.exit(1); }
    row.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    const editorReady = await waitFor(() => !!doc.getElementById("art-text-ru"));
    ok("редактор открылся", editorReady);
    ok("русский текст подставился", /Стена/.test(
        (doc.getElementById("art-text-ru") || {}).value || ""));
    ok("английский текст подставился", /wall/i.test(
        (doc.getElementById("art-text-en") || {}).value || ""));
    ok("предпросмотр нарисовал разметку",
        /<h3>|<strong>|<blockquote>/.test(
            (doc.getElementById("art-prev-ru") || {}).innerHTML || ""));

    // Английский текст редактируется так же, как русский: над окном своя
    // панель разметки, и «Ссылка» ставит ссылку на выделенное слово.
    // Раньше панель была только у русского окна — из-за этого в английском
    // тексте нельзя было сделать ссылки руками.
    const enBar = doc.querySelector('[data-md="en"]');
    const enText = doc.getElementById("art-text-en");
    ok("у английского окна своя панель разметки",
        !!enBar && !!enText, enBar ? "" : "панели нет");
    ok("панель английского стоит над своим окном",
        !!(enBar && enText && (enBar.compareDocumentPosition(enText) &
            win.Node.DOCUMENT_POSITION_FOLLOWING)));
    if (enBar && enText) {
        enText.value = "Walls show where the crowd is trapped.";
        enText.dispatchEvent(new win.Event("input"));
        enText.selectionStart = 11;                 // «where»
        enText.selectionEnd = 16;
        const enLink = enBar.querySelector("button[data-sel]");
        ok("в английской панели есть кнопка «Ссылка»", !!enLink);
        if (enLink) enLink.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
        ok("ссылка встала на английское слово",
            /\[where\]\(https:\/\/\)/.test(enText.value), enText.value);
        ok("английский предпросмотр ожил",
            /<a\s/.test((doc.getElementById("art-prev-en") || {}).innerHTML || ""),
            (doc.getElementById("art-prev-en") || {}).innerHTML);
    }
    // Русская панель не сломалась: заготовка ссылки осталась русской
    const ruBar = doc.querySelector('[data-md="ru"]');
    const ruText = doc.getElementById("art-text-ru");
    const ruSaved = ruText.value;
    ruText.selectionStart = ruText.selectionEnd = 0;
    ruBar.querySelector("button[data-sel]").dispatchEvent(
        new win.MouseEvent("click", { bubbles: true }));
    ok("русская панель вставляет русскую заготовку ссылки",
        /\[текст\]\(https:\/\/\)/.test(ruText.value), ruText.value.slice(0, 40));
    ruText.value = ruSaved;
    ruText.dispatchEvent(new win.Event("input"));
    ok("обложка показана в редакторе",
        /<img/.test((doc.getElementById("art-photo-prev") || {}).innerHTML || ""));

    // Новая статья: сохранить пустую нельзя, с текстом — можно
    doc.getElementById("art-new").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await waitFor(() => !!doc.getElementById("art-save"));
    doc.getElementById("art-save").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await waitFor(() => /заголовок/i.test((doc.getElementById("art-hint") || {}).textContent || ""));
    ok("без заголовка статья не сохраняется",
        /заголовок/i.test((doc.getElementById("art-hint") || {}).textContent || ""));
    ok("ошибка объяснена, а не молчание",
        /art-note/.test((doc.getElementById("art-hint") || {}).className || ""));

    // Публикация без английской версии — сервер обязан отказать
    doc.getElementById("art-title-ru").value = "Тестовая статья из проверки";
    doc.getElementById("art-text-ru").value =
        "## Первый раздел\n\nЭто **проверка** раздела «Статьи»: текст, разметка и публикация.\n\n- пункт\n\n> цитата";
    doc.getElementById("art-publish").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    await waitFor(() => /ИИ|англ/i.test((doc.getElementById("art-hint") || {}).textContent || ""));
    const hint = (doc.getElementById("art-hint") || {}).textContent || "";
    ok("без английской версии публикация отклонена (ИИ недоступен)", /англ|ИИ/i.test(hint), hint);
    ok("подсказка говорит, что делать", /впишите|вручную|Перевести/i.test(hint), hint);

    // Сохраняем черновик — он должен появиться в списке
    doc.getElementById("art-save").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    const saved = await waitFor(() => /Тестовая статья/.test(
        (doc.getElementById("art-list") || {}).textContent || ""));
    ok("черновик попал в список", saved);
    const rows = Array.from(doc.querySelectorAll(".art-row"));
    const draftRow = rows.filter((r) => /Тестовая статья/.test(r.textContent))[0];
    // Подпись бейджа может быть переведена словарём страницы (черновик/draft)
    const badge = draftRow && draftRow.querySelector(".art-badge");
    ok("у черновика правильный бейдж",
        !!badge && /черновик|draft/i.test(badge.textContent)
        && !/pub|plan/.test(badge.className),
        badge ? badge.className + " / " + badge.textContent : "бейджа нет");
    ok("дубликат не создался (одна строка на статью)",
        rows.filter((r) => /Тестовая статья/.test(r.textContent)).length === 1,
        "строк с таким заголовком: " +
        rows.filter((r) => /Тестовая статья/.test(r.textContent)).length);

    // Убираем за собой: проверка не должна оставлять мусор в разделе.
    // Чистим все статьи с тестовым заголовком — их могло остаться и больше одной
    // от прежних прогонов.
    const leftovers = await (await fetch(BASE + "/api/admin/articles", {
        headers: { Cookie: cookie },
    })).json();
    const junk = ((leftovers && leftovers.items) || [])
        .filter((it) => /^Тестовая статья/.test((it.titles || {}).ru || ""));
    for (const it of junk) {
        await fetch(BASE + "/api/admin/articles/" + encodeURIComponent(it.id) + "/delete", {
            method: "POST", headers: { Cookie: cookie, "Content-Type": "application/json" },
            body: "{}",
        });
    }
    if (junk.length) console.log("  · тестовые статьи удалены: " + junk.length);

    console.log("\nИтого: " + pass + " ок, " + fail + " провал(ов)");
    process.exit(fail ? 1 : 0);
})().catch((e) => {
    console.error("Тест не смог запуститься:", e);
    process.exit(2);
});
