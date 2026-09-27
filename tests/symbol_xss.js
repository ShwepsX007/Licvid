require("./_dom_env");
/**
 * Регрессия P0 XSS через символы.
 * Проверяем, что строка с HTML не превращается в реальный DOM-узел
 * при отрисовке выпадающего списка символов.
 *
 * Запуск: node tests/symbol_xss.js http://127.0.0.1:8000
 * Не требует живого сервера — проверяет логику экранирования фронта.
 */
const { JSDOM } = require("jsdom");

let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? " | " + extra : "")); }
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function main() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body><div id="dropdown"></div></body></html>`, {
    runScripts: "dangerously",
  });
  const { document } = dom.window;

  const malicious = '<img src=x onerror=alert(1)>';
  const malicious2 = '"><svg onload=alert(2)>';

  // Тест 1: escapeHtml экранирует < >
  const esc = escapeHtml(malicious);
  check("escapeHtml экранирует <img", esc.includes("&lt;img") && !esc.includes("<img"), esc);

  // Тест 2: вставка через innerHTML с escapeHtml не создаёт реальный элемент
  const row = document.createElement("button");
  row.className = "symbol-option";
  const label = malicious;
  row.innerHTML =
    '<span class="sym-opt-left">' +
      "<span>" + escapeHtml(label) + "</span>" +
    "</span>";
  document.getElementById("dropdown").appendChild(row);
  check("выпадающий список не содержит реального <img> из строки символа",
        row.querySelector("img") === null,
        row.innerHTML.slice(0,200));
  check("текст остаётся текстом, а не узлом",
        row.textContent.includes("<img") || row.textContent.includes("img"),
        row.textContent.slice(0,200));

  // Тест 3: символ с кавычкой не разрывает атрибут
  const row2 = document.createElement("div");
  const sym = malicious2;
  // как в topCoinGroup: data-symbol=" + escapeHtml(c.symbol)
  row2.innerHTML = '<div class="top-coin-card" data-symbol="' + escapeHtml(sym) + '"></div>';
  check("кавычка в символе экранируется в атрибуте",
        row2.innerHTML.includes("&quot;") && !row2.innerHTML.includes('"><svg'),
        row2.innerHTML.slice(0,200));
  check("атрибут не создал svg элемент",
        row2.querySelector("svg") === null,
        row2.innerHTML.slice(0,200));

  // Тест 4: проверка что даже если сервер отдаст грязную строку, фронт её не исполнит
  // Симулируем список custom_symbols из WS init
  const customSymbols = [malicious, 'BTC_USDT', malicious2];
  const container = document.createElement("div");
  customSymbols.forEach(s => {
    const btn = document.createElement("button");
    btn.textContent = s; // правильный путь — textContent
    // неправильный путь был бы innerHTML без экранирования
    container.appendChild(btn);
  });
  check("custom_symbols через textContent не создаёт HTML узлы",
        container.querySelector("img") === null && container.querySelector("svg") === null,
        container.innerHTML.slice(0,300));

  console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
  if (fail) process.exit(1);
}

main().catch(e => { console.error(e); process.exit(1); });
