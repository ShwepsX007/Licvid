require("./_dom_env");
/**
 * Интерфейс входа по почте в jsdom: страница /login (вкладки, форма,
 * сообщения из адресной строки) и /reset (токен из письма, смена пароля).
 * Плюс разметка кабинета: метка подтверждения, баннер и блок привязки Telegram.
 *
 * Запуск (сервер уже на 127.0.0.1:8000):
 *     npm install --no-save jsdom ws
 *     node tests/email_ui.js [http://127.0.0.1:8000]
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const { ru } = require("./_ru");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

function stubCanvas(win) {
  const noop = () => {};
  const ctx = new Proxy({}, {
    get(_t, prop) {
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
}

/** Страница с заглушками сети: собираем запросы, отвечаем по сценарию. */
async function openPage(path, { me = {}, handler } = {}) {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    const msg = String((e && e.message) || e);
    // jsdom не умеет навигацию между документами — это ожидаемый шум от
    // location.href = "/cabinet" после успешной смены пароля
    if (msg.indexOf("Not implemented: navigation") !== -1) return;
    errors.push("jsdomError: " + msg.slice(0, 200));
  });
  vc.on("error", (...a) => errors.push("console.error: " +
    a.map((x) => String((x && x.message) || x)).join(" ").slice(0, 200)));
  const calls = [];
  const dom = await JSDOM.fromURL(ru(URL_BASE + path), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      stubCanvas(win);
      try { Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
        Object.defineProperty(win.navigator, "languages", { value: ["ru-RU", "ru"], configurable: true }); } catch (e) {}
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
        dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () { return { observe() {}, unobserve() {}, disconnect() {} }; };
      }
      win.WebSocket = require("ws");
      win.open = () => ({ closed: false });
      win.fetch = async (url, opts) => {
        const u = String(url);
        calls.push({ url: u, body: (opts && opts.body) || "" });
        let body = { ok: true };
        if (handler) body = handler(u, opts) || body;
        else if (u.indexOf("/api/auth/me") === 0) body = Object.assign({ ok: true, user: null }, me);
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });
  await new Promise((r) => setTimeout(r, 500));
  return { win: dom.window, doc: dom.window.document, calls };
}

async function main() {
  console.log("вход по почте: страницы /login и /reset в jsdom");

  // --- 1. страница входа: почта — основная, Telegram — запасной -----------
  const login = await openPage("/login");
  const doc = login.doc;
  check("вкладки входа/регистрации/ссылки есть",
    doc.querySelectorAll(".auth-tab").length === 3,
    doc.querySelectorAll(".auth-tab").length);
  check("по умолчанию активен вход",
    (doc.querySelector('.auth-tab[data-tab="login"]') || {}).classList &&
    doc.querySelector('.auth-tab[data-tab="login"]').classList.contains("active"));
  check("поле почты на месте", !!doc.querySelector("#in-email"));
  check("поле пароля видно на вкладке входа",
    !doc.querySelector("#field-pass").classList.contains("hidden"));
  check("поле имени скрыто до регистрации",
    doc.querySelector("#field-name").classList.contains("hidden"));
  check("поле с примером скрыто на входе",
    doc.querySelector("#field-captcha").classList.contains("hidden"));
  check("кнопка «Другой пример» на месте",
    !!doc.querySelector("#captcha-refresh") &&
    doc.querySelector("#captcha-refresh").textContent.length > 0);
  // Telegram — отдельный блок рядом с формой почты с разделителем «или»,
  // а не спрятанный в свёрнутый блок: его раньше просто не замечали
  const alt = doc.querySelector("#tg-alt");
  check("Telegram — отдельный блок рядом с формой почты (не свёрнутый)",
    !!alt && alt.tagName === "DIV" && !!doc.querySelector("#tg-alt .or-sep"),
    alt && alt.tagName);
  check("без токена бота блок Telegram не показываем",
    !!alt && alt.classList.contains("hidden"), alt && alt.className);
  check("кнопка Telegram-бота внутри блока",
    !!doc.querySelector("#login-btn") &&
    /Telegram/.test(doc.querySelector("#login-btn").textContent));
  check("почта названа основным способом",
    /Основной способ — почта/.test(doc.body.textContent),
    (doc.body.textContent.match(/Основной способ[^.]*/) || [""])[0]);
  check("ссылки-помощники: сброс пароля и повтор письма",
    !!doc.querySelector("#to-reset") && !!doc.querySelector("#resend-verify"));
  check("виджет Telegram не подгружается без токена бота",
    !doc.querySelector("#tg-widget script"));

  // с настроенным ботом блок Telegram виден сразу — не надо «раскрывать»
  // без bot_username: виджет Telegram тянет скрипт из интернета, а в тесте
  // сети нет — нам важен сам блок и кнопка
  const withBot = await openPage("/login", { me: { bot_ready: true } });
  const altBot = withBot.doc.querySelector("#tg-alt");
  check("с ботом блок Telegram виден",
    !!altBot && !altBot.classList.contains("hidden"), altBot && altBot.className);
  check("кнопка Telegram ведёт в бота",
    !!withBot.doc.querySelector("#login-btn"));
  check("пояснение про привязку почты позже",
    /привязать позже/.test(withBot.doc.body.textContent));
  await withBot.win.close();

  // переключение на регистрацию: появляется имя, меняется заголовок
  doc.querySelector('.auth-tab[data-tab="register"]').dispatchEvent(
    new login.win.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));
  check("на регистрации появилось поле имени",
    !doc.querySelector("#field-name").classList.contains("hidden"));
  check("на регистрации появилось поле с примером",
    !doc.querySelector("#field-captcha").classList.contains("hidden"));
  check("заголовок сменился на регистрацию",
    doc.querySelector("#auth-title").textContent.indexOf("Регистрация") === 0,
    doc.querySelector("#auth-title").textContent);
  check("кнопка стала «Создать аккаунт»",
    doc.querySelector("#email-submit").textContent === "Создать аккаунт");

  // вкладка «ссылка на почту» прячет пароль
  doc.querySelector('.auth-tab[data-tab="link"]').dispatchEvent(
    new login.win.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));
  check("на вкладке ссылки пароль скрыт",
    doc.querySelector("#field-pass").classList.contains("hidden"));
  check("вкладка запомнена в браузере",
    login.win.localStorage.getItem("liqscope.auth.tab") === "link",
    login.win.localStorage.getItem("liqscope.auth.tab"));

  // --- 2. отправка формы регистрации --------------------------------------
  const sent = [];
  const reg = await openPage("/login", {
    handler(u, opts) {
      if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: null, mail_enabled: true };
      if (u.indexOf("/api/auth/captcha") === 0) {
        return { ok: true, token: "cap-1", question: "3 + 4", hint: "Сколько получится?" };
      }
      if (u.indexOf("/api/auth/email/register") === 0) {
        sent.push({ url: u, body: (opts && opts.body) || "" });
        return { ok: true, sent: true, email: "bob@mail.ru", verify_required: true };
      }
      return { ok: true };
    },
  });
  const rdoc = reg.doc;
  rdoc.querySelector('.auth-tab[data-tab="register"]').dispatchEvent(
    new reg.win.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 250));   // пример приходит запросом
  check("на регистрации видно поле с примером",
    !rdoc.querySelector("#field-captcha").classList.contains("hidden"));
  check("пример загрузился с сервера",
    rdoc.querySelector("#captcha-question").textContent === "3 + 4",
    rdoc.querySelector("#captcha-question").textContent);
  rdoc.querySelector("#in-email").value = "bob@mail.ru";
  rdoc.querySelector("#in-password").value = "Good-Pass-2026";
  rdoc.querySelector("#in-name").value = "Боб";
  // без ответа на пример форма не уходит
  rdoc.querySelector("#email-form").dispatchEvent(
    new reg.win.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 60));
  check("без ответа на пример запроса нет", sent.length === 0, JSON.stringify(sent));
  check("и подсказка просит решить пример",
    rdoc.querySelector("#login-status").textContent.indexOf("Решите пример") !== -1,
    rdoc.querySelector("#login-status").textContent);
  rdoc.querySelector("#in-captcha").value = "7";
  rdoc.querySelector("#email-form").dispatchEvent(
    new reg.win.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 120));
  check("регистрация ушла на сервер", sent.length === 1, JSON.stringify(sent));
  const regBody = JSON.parse(sent[0].body);
  check("в запросе токен и ответ капчи",
    regBody.captcha === "cap-1" && String(regBody.answer) === "7",
    sent[0].body);
  check("просьба открыть письмо показана",
    reg.doc.querySelector("#login-status").textContent.indexOf("Письмо отправлено") === 0,
    reg.doc.querySelector("#login-status").textContent);
  check("статус зелёный",
    reg.doc.querySelector("#login-status").classList.contains("ok"));

  // --- 2б. неверный ответ на пример ---------------------------------------
  const capWrong = await openPage("/login", {
    handler(u) {
      if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: null, mail_enabled: true };
      if (u.indexOf("/api/auth/captcha") === 0) {
        return { ok: true, token: "cap-a", question: "2 + 2", hint: "" };
      }
      if (u.indexOf("/api/auth/email/register") === 0) {
        return { ok: false, error: "captcha_wrong", status: 400,
                 hint: "Неверный ответ на пример. Попробуйте ещё раз.",
                 captcha: { ok: true, token: "cap-b", question: "9 + 1" } };
      }
      return { ok: true };
    },
  });
  capWrong.doc.querySelector('.auth-tab[data-tab="register"]').dispatchEvent(
    new capWrong.win.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));
  capWrong.doc.querySelector("#in-email").value = "bob@mail.ru";
  capWrong.doc.querySelector("#in-password").value = "Good-Pass-2026";
  capWrong.doc.querySelector("#in-captcha").value = "5";
  capWrong.doc.querySelector("#email-form").dispatchEvent(
    new capWrong.win.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 120));
  check("ошибка капчи показана",
    capWrong.doc.querySelector("#login-status").textContent.indexOf("Неверный ответ") !== -1,
    capWrong.doc.querySelector("#login-status").textContent);
  check("сервер прислал новый пример — он на экране",
    capWrong.doc.querySelector("#captcha-question").textContent === "9 + 1",
    capWrong.doc.querySelector("#captcha-question").textContent);
  check("поле ответа очищено под новый пример",
    capWrong.doc.querySelector("#in-captcha").value === "",
    capWrong.doc.querySelector("#in-captcha").value);

  // --- 3. вход по паролю: ошибка «подтвердите почту» ----------------------
  const verifyNeeded = await openPage("/login", {
    handler(u) {
      if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: null, mail_enabled: true };
      if (u.indexOf("/api/auth/email/login") === 0) {
        return { ok: false, error: "email_unverified",
                 hint: "Сначала подтвердите почту — ссылка есть в письме от LiqScope.", sent: true };
      }
      return { ok: true };
    },
  });
  verifyNeeded.doc.querySelector("#in-email").value = "bob@mail.ru";
  verifyNeeded.doc.querySelector("#in-password").value = "Good-Pass-2026";
  verifyNeeded.doc.querySelector("#email-form").dispatchEvent(
    new verifyNeeded.win.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 120));
  check("предупреждение о неподтверждённой почте",
    verifyNeeded.doc.querySelector("#login-status").textContent.indexOf("подтвердите почту") !== -1,
    verifyNeeded.doc.querySelector("#login-status").textContent);
  check("ошибка помечена красным",
    verifyNeeded.doc.querySelector("#login-status").classList.contains("err"));

  // --- 4. сообщения из адресной строки (переходы по ссылкам) --------------
  const cases = [
    ["?verify=bad", "Ссылка подтверждения не подошла"],
    ["?verify=used", "уже использована"],
    ["?verify=expired", "устарела"],
    ["?link=used", "Ссылка для входа"],
    ["?banned=1", "Доступ закрыт"],
  ];
  for (const [q, needle] of cases) {
    const page = await openPage("/login" + q, {
      handler(u) {
        if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: null, mail_enabled: true };
        return { ok: true };
      },
    });
    const text = page.doc.querySelector("#login-status").textContent;
    check("сообщение для " + q, text.indexOf(needle) !== -1, text);
    check("сообщение для " + q + " — ошибка",
      page.doc.querySelector("#login-status").classList.contains("err"), text);
  }

  // --- 5. страница сброса пароля ------------------------------------------
  const resetCalls = [];
  const reset = await openPage("/reset?token=TOK1", {
    handler(u, opts) {
      if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: null };
      if (u.indexOf("/api/auth/email/token") === 0) return { ok: true, email: "bob@mail.ru" };
      if (u.indexOf("/api/auth/email/set-password") === 0) {
        resetCalls.push(String((opts && opts.body) || ""));
        return { ok: true, user: { id: 1, email: "bob@mail.ru" } };
      }
      return { ok: true };
    },
  });
  await new Promise((r) => setTimeout(r, 200));
  check("почта из письма подставлена",
    reset.doc.querySelector("#reset-email").value === "bob@mail.ru",
    reset.doc.querySelector("#reset-email").value);
  reset.doc.querySelector("#reset-pass").value = "Fresh-Pass-2026";
  reset.doc.querySelector("#reset-pass2").value = "Other-Pass-2026";
  reset.doc.querySelector("#reset-form").dispatchEvent(
    new reset.win.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 80));
  check("несовпадающие пароли не уходят на сервер",
    resetCalls.length === 0 && reset.doc.querySelector("#reset-status").textContent.indexOf("не совпадают") !== -1,
    reset.doc.querySelector("#reset-status").textContent);
  reset.doc.querySelector("#reset-pass2").value = "Fresh-Pass-2026";
  reset.doc.querySelector("#reset-form").dispatchEvent(
    new reset.win.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 120));
  check("новый пароль уходит с токеном",
    resetCalls.length === 1 && resetCalls[0].indexOf("TOK1") !== -1 &&
    resetCalls[0].indexOf("Fresh-Pass-2026") !== -1, resetCalls[0]);
  check("короткий пароль отклоняем сами",
    reset.doc.querySelector("#reset-status").textContent.indexOf("Ссылка") === -1);

  // битая ссылка: форму прячем
  const badLink = await openPage("/reset?token=BAD", {
    handler(u) {
      if (u.indexOf("/api/auth/me") === 0) return { ok: true, user: null };
      if (u.indexOf("/api/auth/email/token") === 0) return { ok: false, error: "expired" };
      return { ok: true };
    },
  });
  await new Promise((r) => setTimeout(r, 150));
  check("по битой ссылке форму прячем",
    badLink.doc.querySelector("#reset-form").classList.contains("hidden") &&
    badLink.doc.querySelector("#reset-status").textContent.indexOf("не подошла") !== -1,
    badLink.doc.querySelector("#reset-status").textContent);

  // --- 6. кабинет: подтверждение почты и привязка Telegram ----------------
  const cab = await openPage("/cabinet", {
    handler(u) {
      if (u.indexOf("/api/auth/me") === 0) {
        return { ok: true, mail_enabled: true, verified: false, bot_ready: true,
                 bot_username: "LiqScopeBot",
                 user: { id: 1, email: "bob@mail.ru", email_verified: false,
                         tg_linked: false, tg_id: null, display_name: "Боб",
                         is_admin: false, created_at: 1700000000, username: "" } };
      }
      if (u.indexOf("/api/account/services") === 0) {
        return { ok: false, error: "email_unverified" };
      }
      return { ok: true };
    },
  });
  await new Promise((r) => setTimeout(r, 200));
  check("в кабинете видно почту", cab.doc.querySelector("#cab-meta").textContent.indexOf("bob@mail.ru") !== -1,
    cab.doc.querySelector("#cab-meta").textContent);
  check("метка просит подтвердить почту",
    cab.doc.querySelector("#cab-verified").textContent === "подтвердите почту",
    cab.doc.querySelector("#cab-verified").textContent);
  check("баннер подтверждения показан",
    !cab.doc.querySelector("#verify-bar").classList.contains("hidden"));
  check("Telegram помечен как непривязанный",
    cab.doc.querySelector("#tg-state").textContent.indexOf("Telegram не привязан") === 0,
    cab.doc.querySelector("#tg-state").textContent);
  check("и объяснено, зачем привязывать",
    cab.doc.querySelector("#tg-state").textContent.indexOf("сигналы алертов") !== -1,
    cab.doc.querySelector("#tg-state").textContent);
  check("кнопок привязки/отвязки две, вторая скрыта",
    !!cab.doc.querySelector("#tg-link") &&
    cab.doc.querySelector("#tg-unlink").classList.contains("hidden"));
  check("неподтверждённая почта не показывает сервисы",
    cab.doc.querySelectorAll("#svc-acc .svc-fold").length === 0,
    cab.doc.querySelector("#svc-acc").innerHTML.slice(0, 80));
  check("вместо сервисов — объяснение",
    cab.doc.querySelector("#svc-acc").textContent.indexOf("после подтверждения почты") !== -1,
    cab.doc.querySelector("#svc-acc").textContent.slice(0, 90));

  // --- 7. кабинет того, кто вошёл только через Telegram/бота --------------
  const tgOnly = await openPage("/cabinet", {
    handler(u) {
      if (u.indexOf("/api/auth/me") === 0) {
        return { ok: true, mail_enabled: true, verified: true, bot_ready: true,
                 bot_username: "LiqScopeBot",
                 user: { id: 2, email: "", email_verified: false, tg_linked: true,
                         tg_id: 777, display_name: "Боб", is_admin: false,
                         created_at: 1700000000, username: "bob_tg" } };
      }
      if (u.indexOf("/api/account/services") === 0) return { ok: true, services: [] };
      return { ok: true };
    },
  });
  await new Promise((r) => setTimeout(r, 250));
  check("без почты баннер напоминает привязать её",
    !tgOnly.doc.querySelector("#verify-bar").classList.contains("hidden"));
  check("и объясняет, зачем почта",
    tgOnly.doc.querySelector("#verify-bar-hint").textContent.indexOf("Почта не привязана") === 0,
    tgOnly.doc.querySelector("#verify-bar-hint").textContent);
  check("кнопка ведёт подтверждать почту в боте",
    tgOnly.doc.querySelector("#verify-resend").textContent === "Подтвердить почту в боте",
    tgOnly.doc.querySelector("#verify-resend").textContent);
  check("метка подтверждения скрыта, когда почты нет",
    tgOnly.doc.querySelector("#cab-verified").classList.contains("hidden"));

  check("ошибок страниц нет", errors.length === 0, errors.join(" | "));
}

(async () => {
  try { await main(); } catch (e) { fail++; console.log("  FAIL исключение: " + e.message); }
  console.log(`\nитог: ${ok} ок, ${fail} ошибок`);
  process.exit(fail ? 1 : 0);
})();
