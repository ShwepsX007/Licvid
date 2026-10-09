/**
 * Админка: форма системных настроек действительно сохраняет то, что ввели.
 *
 * Зачем тест. Поля рисует static/system_settings.js, а значения собирают
 * collect() и sectionValue(). Оба искали поле через
 * ``querySelector('[data-syskey="…"]')``, и первым совпадением оказывалась
 * обёртка ``div.sys-field`` (у неё в разметке стоял тот же data-syskey):
 * у div нет ``value``, поэтому в POST уходила пустая строка. Сервер трактует
 * пустое значение как «снять переопределение» — в итоге введённый ключ не
 * сохранялся, плашка оставалась «не задано», а «Проверить» проверяла пустой
 * ключ. Пострадали и Google OAuth, и SMTP, и Telegram, и ключи ИИ.
 *
 * Проверяем браузерную половину целиком, без сервера: страница admin.html,
 * настоящий static/system_settings.js и подменённый fetch.
 *
 * Запуск:
 *     node tests/admin_settings_save.js
 */
let jsdom;
try {
  jsdom = require("jsdom");
} catch (e) {
  console.log("пропуск admin_settings_save.js: нет jsdom (npm install --no-save jsdom)");
  process.exit(0);
}
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const MASK = "***";
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Поля вкладки «Безопасность»: ключи Google + список админов. */
function securitySchema(overrides) {
  return Object.assign({
    ok: true,
    mask: MASK,
    google_redirect_uri: "https://liqscope.online/api/auth/google/callback",
    settings: {
      LIQSCOPE_ADMIN_EMAILS: {
        label: "Email админов", section: "security", value: "", source: "",
        hint: "", placeholder: "owner@liqscope.online",
      },
      LIQSCOPE_GOOGLE_CLIENT_ID: {
        label: "Google OAuth Client ID", section: "security", value: "",
        source: "", hint: "",
        placeholder: "123456-abc.apps.googleusercontent.com",
      },
      LIQSCOPE_GOOGLE_CLIENT_SECRET: {
        label: "Google OAuth Client Secret", section: "security", value: "",
        source: "", secret: true, hint: "",
      },
      LIQSCOPE_SMTP_HOST: {
        label: "SMTP-сервер", section: "mail", value: "", source: "",
        hint: "", placeholder: "smtp.yandex.ru",
      },
    },
  }, overrides || {});
}

/** Страница админки с заглушкой сети; возвращает окно и перехваченные запросы. */
async function openAdmin(schema) {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    errors.push(msg);
  });
  const html = fs.readFileSync(path.join(ROOT, "static/admin.html"), "utf8");
  const dom = new JSDOM(html, {
    runScripts: "outside-only",
    url: "https://liqscope.online/admin",
    pretendToBeVisual: true,
    virtualConsole: vc,
  });
  const win = dom.window;
  const calls = [];
  win.fetch = async (url, opts) => {
    const u = String(url);
    calls.push({
      url: u,
      method: (opts && opts.method) || "GET",
      body: (opts && opts.body) || "",
    });
    if (u.indexOf("/api/admin/settings") === 0) {
      return { ok: true, status: 200, json: async () => schema };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  win.eval(fs.readFileSync(path.join(ROOT, "static/system_settings.js"), "utf8"));
  await sleep(50);
  return { win, doc: win.document, calls, errors };
}

function postsTo(calls, pathPart) {
  return calls.filter((c) => c.method === "POST" && c.url.indexOf(pathPart) === 0);
}

function typeIn(doc, key, value) {
  const inp = doc.querySelector(`#sys-panel-security input[data-syskey="${key}"]`)
    || doc.querySelector(`input[data-syskey="${key}"]`);
  if (!inp) throw new Error("нет поля " + key);
  inp.value = value;
  return inp;
}

function clickSave(doc, section) {
  const btn = doc.querySelector(`[data-save="${section}"]`);
  if (!btn) throw new Error("нет кнопки сохранения " + section);
  btn.dispatchEvent(new doc.defaultView.MouseEvent("click", { bubbles: true }));
}

async function main() {
  /* 1. Введённые ключи Google уходят на сервер как есть. */
  {
    const { doc, calls } = await openAdmin(securitySchema());
    typeIn(doc, "LIQSCOPE_GOOGLE_CLIENT_ID", "1234567890-abc.apps.googleusercontent.com");
    typeIn(doc, "LIQSCOPE_GOOGLE_CLIENT_SECRET", "GOCSPX-SuperSecret");
    clickSave(doc, "security");
    await sleep(50);
    const posts = postsTo(calls, "/api/admin/settings");
    check("сохранение вкладки уходит на сервер", posts.length === 1, posts.length);
    const body = posts.length ? JSON.parse(posts[0].body) : {};
    check("Client ID сохранён из поля, а не пустой строкой",
      body.LIQSCOPE_GOOGLE_CLIENT_ID === "1234567890-abc.apps.googleusercontent.com",
      JSON.stringify(body));
    check("Client Secret сохранён из поля",
      body.LIQSCOPE_GOOGLE_CLIENT_SECRET === "GOCSPX-SuperSecret",
      JSON.stringify(body));
  }

  /* 2. Пустой секрет и маска = «не менять»: ключ не отправляем. */
  {
    const { doc, calls } = await openAdmin(securitySchema());
    clickSave(doc, "security");
    await sleep(50);
    let body = JSON.parse(postsTo(calls, "/api/admin/settings")[0].body);
    check("пустой секрет не отправляется", !("LIQSCOPE_GOOGLE_CLIENT_SECRET" in body),
      JSON.stringify(body));
    typeIn(doc, "LIQSCOPE_GOOGLE_CLIENT_SECRET", MASK + " — задан, пустое поле = не менять");
    clickSave(doc, "security");
    await sleep(50);
    const posts = postsTo(calls, "/api/admin/settings");
    body = JSON.parse(posts[posts.length - 1].body);
    check("маска вместо секрета не отправляется",
      !("LIQSCOPE_GOOGLE_CLIENT_SECRET" in body), JSON.stringify(body));
  }

  /* 3. «Проверить Google OAuth» проверяет то, что введено. */
  {
    const { doc, calls } = await openAdmin(securitySchema());
    typeIn(doc, "LIQSCOPE_GOOGLE_CLIENT_ID", "999-xyz.apps.googleusercontent.com");
    typeIn(doc, "LIQSCOPE_GOOGLE_CLIENT_SECRET", "GOCSPX-TestSecret");
    doc.getElementById("sys-google-test")
      .dispatchEvent(new doc.defaultView.MouseEvent("click", { bubbles: true }));
    await sleep(50);
    const posts = postsTo(calls, "/api/admin/settings/test-google");
    check("проверка Google уходит на сервер", posts.length === 1, posts.length);
    const body = posts.length ? JSON.parse(posts[0].body) : {};
    check("проверка берёт Client ID из поля",
      body.client_id === "999-xyz.apps.googleusercontent.com", JSON.stringify(body));
    check("проверка берёт Client Secret из поля",
      body.client_secret === "GOCSPX-TestSecret", JSON.stringify(body));
  }

  /* 4. sectionValue читает поле, а не обёртку — на примере SMTP. */
  {
    const { doc, calls } = await openAdmin(securitySchema());
    const host = doc.querySelector('input[data-syskey="LIQSCOPE_SMTP_HOST"]');
    host.value = "smtp.example.com";
    doc.getElementById("sys-smtp-test")
      .dispatchEvent(new doc.defaultView.MouseEvent("click", { bubbles: true }));
    await sleep(50);
    const posts = postsTo(calls, "/api/admin/settings/test-smtp");
    const body = posts.length ? JSON.parse(posts[0].body) : {};
    check("проверка SMTP берёт хост из поля",
      body.host === "smtp.example.com", JSON.stringify(body));
  }

  /* 5. После сохранения форма перезагружает схему (плашки «из базы»). */
  {
    const { doc, calls } = await openAdmin(securitySchema());
    typeIn(doc, "LIQSCOPE_GOOGLE_CLIENT_ID", "1-abc.apps.googleusercontent.com");
    const before = calls.filter((c) => c.url.indexOf("/api/admin/settings") === 0).length;
    clickSave(doc, "security");
    await sleep(50);
    const after = calls.filter((c) => c.url.indexOf("/api/admin/settings") === 0).length;
    check("после сохранения схема перечитывается", after > before, before + " -> " + after);
  }

  /* 6. Секрет не подставляется обратно в поле даже маской значения. */
  {
    const schema = securitySchema();
    schema.settings.LIQSCOPE_GOOGLE_CLIENT_SECRET =
      { label: "Google OAuth Client Secret", section: "security", value: MASK,
        source: "db", secret: true, hint: "" };
    const { doc } = await openAdmin(schema);
    const inp = doc.querySelector('input[data-syskey="LIQSCOPE_GOOGLE_CLIENT_SECRET"]');
    check("секрет не подставлен значением", inp.value === "", JSON.stringify(inp.value));
    check("секрет показан подсказкой", (inp.placeholder || "").indexOf(MASK) !== -1,
      inp.placeholder);
  }

  console.log(`\n${ok} ok, ${fail} fail`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => {
  console.log("FAIL исключение: " + (e && e.stack ? e.stack : e));
  process.exit(1);
});
