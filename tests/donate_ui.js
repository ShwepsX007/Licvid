/**
 * ❤️ Кнопка «Донат» в шапке: список кошельков, копирование и тосты.
 *
 * Проверяем браузерную половину без сервера: настоящий static/donate.js,
 * минимальная шапка с ``#nav-account`` (как в static/*.html) и подменённый
 * fetch/clipboard. Что важно:
 *   • кнопка появляется только когда есть хотя бы один адрес;
 *   • в iframe графиков (embed=1 / mode=panel) её нет;
 *   • в списке — только непустые сети, адрес сокращён, а копируется полный;
 *   • после копирования виден тост «Адрес скопирован» (без alert);
 *   • список закрывается по Esc и по клику мимо.
 *
 * Запуск:
 *     node tests/donate_ui.js
 */
let jsdom;
try {
  jsdom = require("jsdom");
} catch (e) {
  console.log("пропуск donate_ui.js: нет jsdom (npm install --no-save jsdom)");
  process.exit(0);
}
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "donate.js"), "utf8");

const PAGE = `<!doctype html><html lang="ru"><body>
<header class="site-header">
  <a class="brand" href="/">LiqScope</a>
  <nav class="header-controls header-nav">
    <a class="nav-desk" href="/screener">Скринер</a>
    <span id="nav-account" class="acc-nav"></span>
  </nav>
</header>
</body></html>`;

const WALLETS = [
  { id: "ethereum", label: "Ethereum", short: "ETH", color: "#627EEA",
    address: "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984" },
  { id: "ton", label: "TON (GRAM)", short: "TON", color: "#0098EA",
    address: "UQ" + "A".repeat(46) },
];

let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Страница + donate.js: fetch и clipboard подменены, ничего не уходит в сеть. */
function page(opts) {
  const o = opts || {};
  const dom = new JSDOM(PAGE, {
    url: o.url || "https://liqscope.online/digest",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const w = dom.window;
  const copies = [];
  const packs = {
    ru: { "donate.btn": "Донат", "donate.title": "Поддержать проект",
          "donate.hint": "Нажмите на адрес — он скопируется",
          "donate.copied": "Адрес скопирован", "donate.copy": "Скопировать адрес" },
    en: { "donate.btn": "Donate", "donate.title": "Support the project",
          "donate.hint": "Tap an address to copy it",
          "donate.copied": "Address copied", "donate.copy": "Copy address" },
  };
  const lang = o.lang || "ru";
  w.LiqScopeI18n = {
    lang: () => lang,
    t: (k) => packs[lang][k] || k,
    onChange: () => {},
  };
  let calls = 0;
  w.fetch = (url, init) => {
    calls++;
    return Promise.resolve({
      json: () => Promise.resolve(o.fail ? { ok: false }
                                         : { ok: true, wallets: o.wallets || [] }),
    });
  };
  Object.defineProperty(w.navigator, "clipboard", {
    configurable: true,
    value: { writeText: (s) => { copies.push(s); return Promise.resolve(); } },
  });
  w.eval(SRC);
  return { dom, w, copies, calls: () => calls };
}

(async function main() {
  console.log("1) две сети: кнопка, список, копирование, тост");
  {
    const p = page({ wallets: WALLETS });
    await sleep(0);
    const w = p.w;
    const btn = w.document.getElementById("nav-donate-btn");
    check("кнопка появилась в шапке", !!btn);
    check("подпись кнопки — «Донат»", btn && btn.textContent.indexOf("Донат") !== -1,
          btn && btn.textContent);
    check("кнопка стоит внутри той же шапки",
          btn && btn.closest("nav.header-nav") !== null);
    check("кнопка — первая в меню, левее «Скринера»",
          !!btn && btn.parentElement === btn.closest("nav").firstElementChild, 
          btn && btn.parentElement.previousElementSibling &&
              btn.parentElement.previousElementSibling.textContent);
    check("шапку с аккаунтом не трогаем",
          w.document.getElementById("nav-account").children.length === 0);

    const pop = w.document.querySelector(".donate-pop");
    check("список закрыт до клика", pop && pop.hidden);
    btn.dispatchEvent(new w.Event("click", { bubbles: true }));
    check("после клика список открыт", pop && !pop.hidden);
    check("кнопка помечена раскрытой", btn.getAttribute("aria-expanded") === "true");

    const rows = pop.querySelectorAll(".donate-row");
    check("в списке две строки", rows.length === 2, rows.length);
    check("подписи сетей на месте",
          rows[0].textContent.indexOf("Ethereum") !== -1 &&
          rows[1].textContent.indexOf("TON") !== -1,
          rows[0].textContent + " / " + rows[1].textContent);
    check("адрес показан сокращённым",
          rows[0].textContent.indexOf("0x1f98…F984") !== -1, rows[0].textContent);

    rows[0].dispatchEvent(new w.Event("click", { bubbles: true }));
    await sleep(0);
    check("скопирован полный адрес", p.copies[0] === WALLETS[0].address, p.copies[0]);
    const toast = w.document.querySelector(".donate-toast");
    check("тост «Адрес скопирован»", toast && toast.textContent === "Адрес скопирован",
          toast && toast.textContent);
    check("тост виден", toast && toast.classList.contains("is-on"));
    check("список закрылся после копирования", pop.hidden);

    btn.dispatchEvent(new w.Event("click", { bubbles: true }));
    w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    check("Esc закрывает список", pop.hidden);

    btn.dispatchEvent(new w.Event("click", { bubbles: true }));
    w.document.body.dispatchEvent(new w.Event("click", { bubbles: true }));
    check("клик мимо закрывает список", pop.hidden);
    p.dom.window.close();
  }

  console.log("2) нет адресов — нет кнопки");
  {
    const p = page({ wallets: [] });
    await sleep(0);
    check("кнопки нет", !p.w.document.getElementById("nav-donate"));
    check("пустого списка тоже нет", !p.w.document.querySelector(".donate-pop"));
    check("ручку всё же спросили", p.calls() === 1, p.calls());
    p.dom.window.close();
  }

  console.log("3) iframe графиков (embed=1 / mode=panel) — кнопки нет");
  for (const q of ["embed=1", "mode=panel"]) {
    const p = page({ wallets: WALLETS, url: "https://liqscope.online/?chart=btc&" + q });
    await sleep(0);
    check(q + ": кнопки нет", !p.w.document.getElementById("nav-donate"));
    check(q + ": ручку не дёргали", p.calls() === 0, p.calls());
    p.dom.window.close();
  }

  console.log("4) английский язык");
  {
    const p = page({ wallets: WALLETS, lang: "en" });
    await sleep(0);
    const btn = p.w.document.getElementById("nav-donate-btn");
    check("подпись кнопки — «Donate»", btn && btn.textContent.indexOf("Donate") !== -1,
          btn && btn.textContent);
    btn.dispatchEvent(new p.w.Event("click", { bubbles: true }));
    p.w.document.querySelector(".donate-row")
      .dispatchEvent(new p.w.Event("click", { bubbles: true }));
    await sleep(0);
    const toast = p.w.document.querySelector(".donate-toast");
    check("тост — «Address copied»", toast && toast.textContent === "Address copied",
          toast && toast.textContent);
    check("в разметке нет alert()", SRC.indexOf("alert(") === -1);
    p.dom.window.close();
  }

  console.log("5) настоящая разметка страницы (static/digest.html)");
  {
    const real = fs.readFileSync(path.join(ROOT, "static", "digest.html"), "utf8");
    const dom = new JSDOM(real, {
      url: "https://liqscope.online/digest",
      runScripts: "outside-only",
      pretendToBeVisual: true,
    });
    const w = dom.window;
    w.LiqScopeI18n = { lang: () => "ru", t: (k) => k, onChange: () => {} };
    w.fetch = () => Promise.resolve({
      json: () => Promise.resolve({ ok: true, wallets: WALLETS }),
    });
    Object.defineProperty(w.navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.resolve() },
    });
    w.eval(SRC);
    await sleep(0);
    const btn = w.document.getElementById("nav-donate-btn");
    check("кнопка нашла шапку в разметке страницы", !!btn);
    check("шапка с меню не задета",
          w.document.querySelectorAll("#nav-donate").length === 1 &&
          w.document.getElementById("nav-account").children.length === 0);
    dom.window.close();
  }

  console.log(fail ? `\n${ok} ok, ${fail} FAIL` : `\nвсе ${ok} проверок прошли`);
  process.exit(fail ? 1 : 0);
})();
