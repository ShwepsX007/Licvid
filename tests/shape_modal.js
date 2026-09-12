/** Окно фигуры CVD/OI: наведение показывает, клик закрепляет.
 *
 *  Наведению на треугольник/шарик открывает тот же попап, что у ликвидаций
 *  (заголовок, свеча, направление, сумма, сила/тир, движение цены, пояснение),
 *  уход курсора прячет. Клик закрепляет окно (димминг), повторный клик
 *  по фигуре / клик мимо / крестик — закрывает. Активная фигура — с белым кольцом.
 *  События мыши — синтетические, через настоящий вендорный LWC (mousedown+
 *  mouseup = клик); координаты фигур берём из записей канваса.
 *
 *  Запуск (сервер уже на 127.0.0.1:8000):
 *      npm install --no-save jsdom ws
 *      NODE_PATH=/tmp/smoke/node_modules node tests/shape_modal.js
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const URL_BASE = process.argv[2] || "http://127.0.0.1:8000";
const errors = [];
let ok = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " | " + extra : "")); }
}

const CVD_FILLS = ["rgba(139,92,246,0.96)", "rgba(255,145,0,0.96)"];
const OI_FILLS = ["rgba(190,242,200,0.95)", "rgba(134,239,172,0.95)",
  "rgba(34,197,94,0.95)", "rgba(0,230,118,0.95)",
  "rgba(252,200,200,0.95)", "rgba(248,113,113,0.95)",
  "rgba(239,68,68,0.95)", "rgba(255,42,95,0.95)"];
const WHITE_RING = "rgba(255,255,255,0.9)";

function makeRecorder(log) {
  const st = { fillStyle: null, strokeStyle: null, shadowColor: null, font: null, path: [] };
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "fillText") {
        return (txt) => log.push({ op: "text", style: st.fillStyle, font: st.font,
                                   txt: String(txt) });
      }
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "beginPath") return () => { st.path = []; };
      if (prop === "moveTo") return (x, y) => st.path.push(["M", x, y]);
      if (prop === "lineTo") return (x, y) => st.path.push(["L", x, y]);
      if (prop === "closePath") return noop;
      if (prop === "arc") {
        return (x, y, r) => st.path.push(["A", x, y, r]);
      }
      if (prop === "fill") {
        return () => {
          if (st.path.length === 3 && st.path[0][0] === "M" &&
              st.path[1][0] === "L" && st.path[2][0] === "L") {
            const mx = st.path[0][1], my = st.path[0][2], ly = st.path[1][2];
            const up = ly > my;
            const h = Math.abs(ly - my);
            log.push({ op: "tri", x: mx, y: up ? my + h / 2 : my - h / 2,
                       h: h, fill: st.fillStyle });
          } else if (st.path.length === 1 && st.path[0][0] === "A") {
            log.push({ op: "ball", x: st.path[0][1], y: st.path[0][2],
                       r: st.path[0][3], fill: st.fillStyle });
          }
          st.path = [];
        };
      }
      if (prop === "stroke") return () => { log.push({ op: "stroke", style: st.strokeStyle }); st.path = []; };
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "fillStyle") return st.fillStyle;
      if (prop === "strokeStyle") return st.strokeStyle;
      if (prop === "font") return st.font;
      if (prop === "shadowColor") return st.shadowColor;
      return typeof prop === "string" ? noop : undefined;
    },
    set(_t, prop, v) {
      if (prop === "fillStyle") st.fillStyle = v;
      else if (prop === "strokeStyle") st.strokeStyle = v;
      else if (prop === "shadowColor") st.shadowColor = v;
      else if (prop === "font") st.font = v;
      else if (prop === "shadowBlur") log.push({ op: "glow", color: st.shadowColor, v: v });
      return true;
    },
  });
}
function noopCtx() {
  const noop = () => {};
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "canvas") return { width: 900, height: 500 };
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop: noop });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      return typeof prop === "string" ? noop : undefined;
    },
    set() { return true; },
  });
}

const NOW = Math.floor(Date.now() / 1000);
const T2 = NOW - (NOW % 300), T1 = T2 - 3600;
const candles = [
  { time: T1, open: 50000, high: 50100, low: 49900, close: 50050,
    volume: 10, cvd: 8000, oiChg: 2e6 },
  { time: T2, open: 49000, high: 49100, low: 48900, close: 49050,
    volume: 10, cvd: -3000, oiChg: -1500 },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.message || e).slice(0, 200)));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));
  const log = [];

  const dom = await JSDOM.fromURL(URL_BASE + "/terminal", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        Object.defineProperty(win.navigator, "language", { value: "ru-RU", configurable: true });
      } catch (e) { /* ignore */ }
      win.matchMedia = () => ({ matches: false, media: "", onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {},
        removeEventListener() {}, dispatchEvent() { return false; } });
      if (!win.ResizeObserver) {
        win.ResizeObserver = function () {
          return { observe() {}, unobserve() {}, disconnect() {} };
        };
      }
      const rec = makeRecorder(log);
      const dummy = noopCtx();
      win.HTMLCanvasElement.prototype.getContext = function () {
        return this.id === "cluster-canvas" ? rec : dummy;
      };
      win.WebSocket = function () { return { readyState: 3, send() {}, close() {} }; };
      win.WebSocket.OPEN = 1;
      win.WebSocket.CLOSED = 3;
      win.fetch = async (url) => {
        const u = String(url);
        let body = {};
        if (u.indexOf("/api/klines") === 0) {
          body = { symbol: "BTC_USDT", timeframe: 5, source: "stub", candles };
        } else if (u.indexOf("/api/stats") === 0) body = {};
        else if (u.indexOf("/api/oi") === 0) body = {};
        else if (u.indexOf("/api/liquidations") === 0) body = { liquidations: [], total: 0 };
        return { ok: true, status: 200, json: async () => body };
      };
    },
  });

  await sleep(7000);
  const win = dom.window;
  const doc = win.document;
  const modal = doc.getElementById("detail-modal");
  const title = doc.getElementById("modal-title");
  const body = doc.getElementById("modal-body");

  // координаты фигур — из записей канваса (только основные заливки)
  const dedup = (pts) => {
    const seen = new Set(), out = [];
    pts.forEach((p) => {
      const k = Math.round(p.x / 15) + ":" + Math.round(p.y / 15);
      if (!seen.has(k)) { seen.add(k); out.push(p); }
    });
    return out;
  };
  const cvdPts = dedup(log.filter((e) => e.op === "tri" && CVD_FILLS.indexOf(e.fill) !== -1));
  const oiPts = dedup(log.filter((e) => e.op === "ball" && OI_FILLS.indexOf(e.fill) !== -1));

  // события — веером по контейнеру и всем потомкам: обработчики LWC
  // висят на внутренних панелях, а не на самом контейнере
  // LWC слушает мышь на канвасе панели (второй canvas в таблице);
  // синтетический даблклик он глотает, поэтому между кликами — пауза
  const pane = (() => {
    const root = doc.getElementById("tv-chart-container");
    const all = [root].concat(Array.prototype.slice.call(root.querySelectorAll("*")));
    return all.filter((n) => n.tagName === "CANVAS")[1] || root;
  })();
  function fire(type, x, y) {
    pane.dispatchEvent(new win.MouseEvent(type, { bubbles: true, cancelable: true,
      clientX: x, clientY: y, view: win }));
  }
  const move = (x, y) => { fire("mouseenter", x, y); fire("mousemove", x, y); };
  const click = async (x, y) => {
    move(x, y);
    fire("mousedown", x, y);
    fire("mouseup", x, y);
    await sleep(600);
  };
  const shown = () => !modal.classList.contains("hidden");
  const txt = () => body.textContent || "";
  const norm = (s) => String(s).replace(/\s/g, "");

  check("found cvd centers", cvdPts.length >= 2, JSON.stringify(cvdPts));
  check("found oi centers", oiPts.length >= 2, JSON.stringify(oiPts));
  if (!cvdPts.length || !oiPts.length) {
    console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
    await win.close();
    process.exit(1);
  }

  // пустая точка: далеко от всех центров
  const all = cvdPts.concat(oiPts);
  const empty = [[8, 8], [892, 8], [8, 492], [892, 492], [450, 5]].find((pt) =>
    all.every((p) => Math.abs(p.x - pt[0]) > 60 && Math.abs(p.y - pt[1]) > 60)) || [8, 8];

  // --- наведение на CVD ---
  move(cvdPts[0].x, cvdPts[0].y);
  check("cvd hover shows modal", shown());
  check("cvd hover peek (not pinned)",
    modal.classList.contains("peek") && !modal.classList.contains("peek-pinned"));
  const contentPE = () => win.getComputedStyle(body.parentElement).pointerEvents;
  check("hover modal click-through", contentPE() === "none", contentPE());
  check("cvd title", title.textContent === "CVD BTC/USDT", title.textContent);
  check("cvd direction", txt().indexOf("Покупатели перевесили") !== -1 ||
    txt().indexOf("Продавцы перевесили") !== -1, txt().slice(0, 100));
  check("cvd strength", txt().indexOf("× p90") !== -1 && txt().indexOf("топ-10%") !== -1,
    txt().slice(0, 160));
  check("cvd candle range", txt().indexOf("Свеча:") !== -1 && txt().indexOf("–") !== -1);
  check("cvd price move", txt().indexOf("Цена за свечу:") !== -1 &&
    txt().indexOf("+0.10%") !== -1, txt().slice(0, 200));
  check("cvd about", txt().indexOf("перевес агрессивных") !== -1);
  await sleep(400);
  check("cvd hover ring", log.some((e) => e.op === "stroke" && e.style === WHITE_RING),
    JSON.stringify(log.filter((e) => e.op === "stroke").map((e) => e.style)
      .filter((v, i, a) => a.indexOf(v) === i)));

  // --- уход курсора прячет ---
  move(empty[0], empty[1]);
  check("leave hides modal", !shown());

  // --- наведение на OI ---
  move(oiPts[0].x, oiPts[0].y);
  check("oi hover shows modal", shown() && title.textContent === "OI BTC/USDT",
    title.textContent);
  const oiTxt = txt(), oiNorm = norm(oiTxt);
  check("oi direction+value", oiTxt.indexOf("Интерес растёт") !== -1 &&
    oiNorm.indexOf("+$2000000") !== -1, oiTxt.slice(0, 140));
  check("oi tier", oiTxt.indexOf("T1") !== -1 && oiTxt.indexOf("$1M") !== -1,
    oiTxt.slice(0, 140));
  check("oi about", oiTxt.indexOf("открытого интереса") !== -1);
  move(empty[0], empty[1]);
  check("oi leave hides", !shown());

  // --- клик закрепляет ---
  move(oiPts[0].x, oiPts[0].y);
  await click(oiPts[0].x, oiPts[0].y);
  check("click pins modal",
    shown() && modal.classList.contains("peek-pinned") &&
    !modal.classList.contains("peek"), modal.className);
  check("pinned modal interactive", contentPE() === "auto", contentPE());
  move(empty[0], empty[1]);
  check("pinned survives leave", shown() && modal.classList.contains("peek-pinned"));

  // --- повторный клик снимает ---
  await click(oiPts[0].x, oiPts[0].y);
  check("second click unpins", !shown());

  // --- клик мимо снимает закреп ---
  move(oiPts[0].x, oiPts[0].y);
  await click(oiPts[0].x, oiPts[0].y);
  check("re-pinned", shown() && modal.classList.contains("peek-pinned"));
  await click(empty[0], empty[1]);
  check("click away unpins", !shown());

  // --- крестик закрывает закреплённое ---
  move(cvdPts[0].x, cvdPts[0].y);
  await click(cvdPts[0].x, cvdPts[0].y);
  check("cvd pinned", shown() && title.textContent === "CVD BTC/USDT");
  doc.getElementById("close-modal").dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, cancelable: true, view: win }));
  check("X closes pinned", !shown());
  move(cvdPts[0].x, cvdPts[0].y);
  check("hover works after X", shown() && modal.classList.contains("peek"));
  move(empty[0], empty[1]);

  // --- живая свеча помечена ровно у одного треугольника ---
  let liveCount = 0;
  for (const p of cvdPts) {
    move(p.x, p.y);
    if (shown() && txt().indexOf("формируется") !== -1) liveCount++;
    move(empty[0], empty[1]);
  }
  check("live badge on one tri", liveCount === 1, liveCount);

  check("no js errors", errors.length === 0, errors.slice(0, 3).join(" // "));

  await win.close();
  console.log("\nитог: " + ok + " ок, " + fail + " ошибок");
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS CRASH:", e); process.exit(2); });
