// Шаг A: что сервер реально рассылает всем клиентам в init-сообщении WS.
const WebSocket = require("ws");
const ws = new WebSocket("ws://127.0.0.1:8000/ws");
const t0 = Date.now();
ws.on("message", (raw) => {
  let m;
  try { m = JSON.parse(String(raw)); } catch (e) { return; }
  if (m.type !== "init") return;
  const syms = m.symbols || [];
  const bad = syms.filter((s) => /[<>"']/.test(s));
  console.log("тип:", m.type, "| символов:", syms.length, "| с опасными символами:", bad.length);
  console.log("payload-символ присутствует:", syms.includes("<IMG SRC=X ONERROR=ALERT(1)>_USDT"));
  console.log("первые 3:", syms.slice(0, 3));
  console.log("custom_symbols из WS:", JSON.stringify(m.custom_symbols));
  ws.close();
  process.exit(0);
});
ws.on("error", (e) => { console.log("ошибка WS:", e.message); process.exit(1); });
setTimeout(() => { console.log("таймаут, сообщений нет"); process.exit(2); }, 15000);
