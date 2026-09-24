/* 💬 Чат LiqScope v10 — per-user + collapsible + persistent threads + delete thread support threads + TG reminders
 *
 *  • «Общий» — 3 дня, пишут зарегистрированные
 *  • «Личные» — 30 дней, можно удалить, TG-напоминание через chat_dm_tg_delay_min
 *  • «Сервисы» — личные сигналы по настройкам (book/pump/alerts/corr), 30 дней
 *  • «Поддержка» — персональный тред per-user, гость с временным ником (guest_token+name)
 *    админ видит все треды, треды сворачиваются, TG-напоминание админам через тот же delay
 */

(function () {
  const API = "/api/terminal/chat";
  const DM_API = "/api/chat/dm";
  const SVC_API = "/api/chat/services";
  const SUP_API = "/api/chat/support";
  const SUP_THREADS_API = "/api/chat/support/threads";
  const POLL_MS = 3500;
  const DM_POLL_MS = 5000;
  const SVC_POLL_MS = 8000;
  const SUP_POLL_MS = 5000;
  const BADGE_POLL_MS = 10000;
  const PING_MS = 30000;
  const LS_OPEN = "liqscope.tchat.open";
  const LS_LAST_ID = "liqscope.tchat.lastId";
  const LS_POS = "liqscope.tchat.pos";
  const LS_MUTE_PFX = "liqscope.tchat.mute.";
  const LS_SUP_NAME = "liqscope.tchat.supName";
  const LS_SUP_GUEST = "liqscope.tchat.supGuest";
  const LS_SUP_THREADS_COLLAPSED = "liqscope.tchat.supThreadsCollapsed";
  const MIN_W = 280, MIN_H = 260;

  const $ = (s, r) => (r || document).querySelector(s);

  function fmtTime(ts) {
    try {
      const d = new Date(ts * 1000);
      const now = new Date();
      const sameDay = d.toDateString() === now.toDateString();
      const hh = String(d.getHours()).padStart(2, "0");
      const mm = String(d.getMinutes()).padStart(2, "0");
      if (sameDay) return `${hh}:${mm}`;
      const dd = String(d.getDate()).padStart(2, "0");
      const mo = String(d.getMonth() + 1).padStart(2, "0");
      return `${dd}.${mo} ${hh}:${mm}`;
    } catch { return ""; }
  }
  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  /* =====================================================================
   * Языки сайта
   *
   * Все подписи чата живут в словарях сайта ключами ``chat.*`` (пять языков:
   * ru, en, zh, hi, es) — чат едет за переключателем в шапке, как кабинет и
   * терминал. Второй аргумент ``T()`` — русский текст: он нужен, если движок
   * словарей не подключён, и заодно виден в коде рядом с ключом.
   * ===================================================================== */
  function T(key, ru, vars) {
    let s = "";
    try {
      const I = window.LiqScopeI18n;
      if (I && I.t) { const v = I.t(key, vars); if (v && v !== key) s = v; }
    } catch (e) { /* без словаря — русский */ }
    if (!s && ru) {
      s = ru;
      if (vars) for (const k in vars) {
        if (Object.prototype.hasOwnProperty.call(vars, k)) s = s.split("{" + k + "}").join(String(vars[k]));
      }
    }
    return s || key;
  }

  /** Дата и время в поясе языка сайта — как в кабинете. */
  function num(v, digits) {
    const n = Number(v) || 0;
    try {
      const I = window.LiqScopeI18n;
      if (I && I.number) return I.number(digits == null ? n : +n.toFixed(digits));
    } catch (e) { /* ignore */ }
    return digits == null ? String(Math.round(n)) : n.toFixed(digits);
  }

  /** Деньги как в сигналах: $1.20M — одинаково во всех языках. */
  function money(v) {
    const n = Math.abs(Number(v) || 0);
    const sign = Number(v) < 0 ? "−" : "";
    if (n >= 1e9) return sign + "$" + (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return sign + "$" + (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return sign + "$" + (n / 1e3).toFixed(1) + "K";
    return sign + "$" + n.toFixed(0);
  }
  function pct(v) {
    const n = Number(v) || 0;
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(2) + "%";
  }
  function coin(sym) {
    const s = String(sym || "");
    if (!s || s.toUpperCase() === "ALL") return T("chat.coin.all", "все монеты");
    return (s.split("_")[0] || s).toUpperCase();
  }
  /** Окно сигнала: 5м / 2ч / 1д — единицы берём из словаря. */
  function winLabel(minutes) {
    const m = Math.max(0, parseInt(minutes, 10) || 0);
    if (m >= 1440 && m % 1440 === 0) return T("chat.win.d", "{n}д", { n: m / 1440 });
    if (m >= 60 && m % 60 === 0) return T("chat.win.h", "{n}ч", { n: m / 60 });
    return T("chat.win.m", "{n}м", { n: m });
  }
  /** Окно корреляций приходит ключом («4h», «24h») — переводим в минуты. */
  function winLabelKey(key) {
    const m = /^(\d+)([mhd])$/.exec(String(key || ""));
    if (!m) return String(key || "");
    const mult = m[2] === "m" ? 1 : m[2] === "h" ? 60 : 1440;
    return winLabel(parseInt(m[1], 10) * mult);
  }
  function priceStr(p) {
    const v = Number(p) || 0;
    if (v <= 0) return "—";
    const digits = v < 0.01 ? 6 : v < 1 ? 4 : 2;
    return "$" + num(v, digits);
  }
  function kindLabel(kind) {
    const k = String(kind || "alert");
    const known = {
      book: ["chat.kind.book", "📖 СТАКАН"],
      pump: ["chat.kind.pump", "💥 ПАМП"],
      alert: ["chat.kind.alert", "🔔 АЛЕРТ"],
      corr: ["chat.kind.corr", "🔗 КОРР"],
    };
    const one = known[k];
    return one ? T(one[0], one[1]) : esc(k.toUpperCase());
  }

  /** Текст сигнала сервиса на языке посетителя.
   *
   * В базе сообщение лежит одним русским текстом, а рядом — ``meta.parts``
   * с числами и ключами (см. ``chat_meta`` в alerts/correlations/pump_scan/
   * book_feed). Кабинет собирает строку из частей сам, поэтому сигнал в
   * Telegram, в ленте кабинета и на английском сайте читается одинаково.
   */
  function svcText(m) {
    const meta = (m && m.meta) || {};
    const parts = meta.parts;
    if (!parts) return esc((m && m.text) || "");
    const k = String((m && m.kind) || "alert");
    try {
      if (k === "alert") return svcAlert(parts);
      if (k === "corr") return svcCorr(parts);
      if (k === "pump") return svcPump(parts);
      if (k === "book") return svcBook(parts);
    } catch (e) { /* части битые — показываем текст из базы */ }
    return esc((m && m.text) || "");
  }
  function svcAlert(p) {
    const metric = T("chat.metric." + String(p.metric || "liq"),
                     String(p.metric || ""));
    const win = parseInt(p.window_min, 10) || 5;
    const span = parseInt(p.span_min, 10) || win;
    const tail = span < win
      ? T("chat.sig.period_span", "за {s} · окно {w}", { s: winLabel(span), w: winLabel(win) })
      : T("chat.sig.period", "за {w}", { w: winLabel(win) });
    const lines = [
      T("chat.sig.alert", "🔔 {metric}: {symbol} {value} {period}",
        { metric, symbol: coin(p.symbol), value: money(p.value), period: tail }),
      T("chat.sig.threshold", "порог {t}", { t: money(p.threshold) }),
    ];
    if (String(p.metric) === "liq") {
      lines.push(T("chat.sig.hits", "{n} ударов · 🔴 {l}  🟢 {sh}",
        { n: num(p.count), l: money(p.longs), sh: money(p.shorts) }));
    } else if (String(p.metric) === "cvd") {
      lines.push(Number(p.value) >= 0 ? T("chat.sig.buys", "покупки") : T("chat.sig.sells", "продажи"));
    } else if (String(p.metric) === "oi") {
      const arrow = Number(p.value) >= 0 ? "↑" : "↓";
      const extra = p.pct ? " (" + pct(p.pct) + ")" : "";
      lines.push(T("chat.sig.oi", "изменение OI {arrow}{pct}", { arrow, pct: extra }));
    }
    const peers = (p.peers || []).map((x) => coin(x.symbol) + " " + money(x.value)).join(", ");
    if (peers) lines.push(T("chat.sig.peers", "ещё в волне: {list}", { list: peers }));
    return esc(lines.join("\n"));
  }
  function svcCorr(p) {
    const metric = T("chat.metric." + String(p.metric || ""), String(p.metric || ""));
    const opp = String(p.kind) === "opp";
    const win = winLabelKey(p.window);
    const lines = [
      T("chat.sig.corr_head", "🔗 корреляции · {metric}", { metric }),
      T("chat.sig.corr_pair", "{a} ↔ {b}  r = {r}", { a: coin(p.a), b: coin(p.b), r: (Number(p.r) || 0).toFixed(2) }),
      opp ? T("chat.sig.corr_opp", "в противофазе · порог {t} · окно {w}",
              { t: (Number(p.threshold) || 0).toFixed(2), w: win })
          : T("chat.sig.corr_same", "в одну сторону · порог {t} · окно {w}",
              { t: (Number(p.threshold) || 0).toFixed(2), w: win }),
    ];
    const peers = (p.peers || []).map((x) => coin(x.a) + " ↔ " + coin(x.b) + " " + (Number(x.r) || 0).toFixed(2)).join(", ");
    if (peers) {
      lines.push(opp ? T("chat.sig.corr_peers_opp", "ещё в противофазе: {list}", { list: peers })
                     : T("chat.sig.corr_peers_same", "ещё в одну сторону: {list}", { list: peers }));
    }
    return esc(lines.join("\n"));
  }
  function svcPump(p) {
    const up = String(p.direction) === "pump";
    const lines = [
      up ? T("chat.sig.pump_head", "🚀 Памп · {coin}", { coin: coin(p.symbol) })
         : T("chat.sig.dump_head", "🩸 Дамп · {coin}", { coin: coin(p.symbol) }),
      T("chat.sig.pump_move", "{pct} за {span} · {candles} × {period}",
        { pct: pct(p.change_pct), span: winLabel(p.span_min), candles: num(p.candles), period: p.period }),
      up ? T("chat.sig.pump_price_up", "цена {now} → {from}", { now: priceStr(p.price), from: priceStr(p.price_from) })
         : T("chat.sig.pump_price_down", "цена {now} ← {from}", { now: priceStr(p.price), from: priceStr(p.price_from) }),
      T("chat.sig.pump_volume", "оборот 24ч {v}", { v: money(p.volume24h) }),
    ];
    return esc(lines.join("\n"));
  }
  function svcBook(p) {
    const side = String(p.side) === "bid" ? "Bid" : "Ask";
    const lines = [
      T("chat.sig.book_head", "📖 Стена на {sym} — {side} {money}",
        { sym: p.sym, side, money: money(p.usdt) }),
      T("chat.sig.book_prices", "Цены {lo}–{hi}", { lo: num(p.lo), hi: num(p.hi) }),
    ];
    if ((p.exchs || []).length) {
      lines.push(T("chat.sig.book_exchs", "Видна на: {list}", { list: p.exchs.join(", ") }));
    }
    return esc(lines.join("\n"));
  }

  // «Перемотка к последним записям». Контейнер, который только что открыли или
  // впервые наполнили, всегда прыгает вниз: раньше при входе в чат человек
  // листал всю историю руками. Если он сам ушёл вверх читать — не дёргаем.
  const NEEDS_JUMP = new WeakSet();
  function jumpToLast(container) { if (container) NEEDS_JUMP.add(container); }
  function stickToBottom(container) {
    if (!container) return;
    const firstFill = container.dataset.tchatFilled !== "1";
    container.dataset.tchatFilled = "1";
    const gap = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (NEEDS_JUMP.has(container) || firstFill || gap < 120) {
      NEEDS_JUMP.delete(container);
      container.scrollTop = container.scrollHeight;
    }
  }

  function loadPos() {
    try {
      const raw = localStorage.getItem(LS_POS);
      if (!raw) return null;
      const p = JSON.parse(raw);
      if (p && typeof p.left === "number" && typeof p.top === "number") return p;
    } catch { }
    return null;
  }
  function savePos(left, top, width, height) {
    try { localStorage.setItem(LS_POS, JSON.stringify({ left, top, width, height })); } catch { }
  }
  function clearPos() {
    try { localStorage.removeItem(LS_POS); } catch { }
  }
  function applyPos(root, panel, pos) {
    if (!root || !pos) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (typeof pos.width === "number" && pos.width >= MIN_W) {
      panel.style.width = clamp(pos.width, MIN_W, Math.max(MIN_W, vw - 8)) + "px";
      panel.classList.add("tchat-resized");
    }
    if (typeof pos.height === "number" && pos.height >= MIN_H) {
      panel.style.height = clamp(pos.height, MIN_H, Math.max(MIN_H, vh - 12)) + "px";
      panel.classList.add("tchat-resized");
    }
    const rect = root.getBoundingClientRect();
    const w = rect.width || 340;
    const h = rect.height || 420;
    let l = clamp(pos.left, 4, Math.max(4, vw - w - 4));
    let t = clamp(pos.top, 4, Math.max(4, vh - h - 4));
    root.style.left = l + "px";
    root.style.top = t + "px";
    root.style.right = "auto";
    root.style.bottom = "auto";
    root.classList.add("tchat-moved");
  }
  function resetPos(root, panel) {
    if (!root) return;
    root.style.left = ""; root.style.top = ""; root.style.right = ""; root.style.bottom = "";
    panel.style.width = ""; panel.style.height = "";
    panel.classList.remove("tchat-resized");
    root.classList.remove("tchat-moved");
    clearPos();
  }

  async function jget(url, opts) {
    const r = await fetch(url, opts);
    let j = null;
    try { j = await r.json(); } catch { }
    if (!r.ok) {
      const err = new Error((j && (j.hint || j.error)) || "error");
      err.status = r.status; err.body = j;
      throw err;
    }
    return j || {};
  }

  function getGuestToken() {
    try {
      let t = localStorage.getItem(LS_SUP_GUEST) || "";
      if (!t || t.length < 4) {
        t = Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 6);
        localStorage.setItem(LS_SUP_GUEST, t);
      }
      return t;
    } catch {
      return Math.random().toString(36).slice(2, 10);
    }
  }
  function getGuestName() {
    try { return (localStorage.getItem(LS_SUP_NAME) || "").trim().slice(0, 40); } catch { return ""; }
  }
  function setGuestName(n) {
    try { if (n) localStorage.setItem(LS_SUP_NAME, n); } catch { }
  }

  function renderMsgs(list, container, me, opts) {
    if (!container) return;
    const o = opts || {};
    container.__tchatLast = { render: "msgs", list, me, opts: o };   // перерисовка при смене языка
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="tchat-empty">' + (o.emptyHtml ||
        T("chat.empty.public", "Пока тихо — напишите первым! 💬") + '<br><small>' +
        T("chat.empty.public_note", "История хранится 3 дня") + '</small>') + '</div>';
      return;
    }
    const empty = container.querySelector(".tchat-empty");
    if (empty && list.length) empty.remove();
    for (const m of list) {
      if (container.querySelector(`[data-mid="${m.id}"]`)) continue;
      const div = document.createElement("div");
      const mine = !!(me && me.id && m.user_id === me.id);
      div.className = "tchat-msg" + (m.admin ? " admin" : "") + (o.bubbles ? (mine ? " mine" : " peer") : "");
      div.dataset.mid = m.id;
      const name = esc(m.name || "anon");
      const time = fmtTime(m.ts);
      const text = esc(m.text);
      const canDel = me && me.is_admin && !o.bubbles;
      const nameHtml = (o.bubbles && mine) ? "" :
        `<button class="tchat-name tchat-namelink" type="button" data-i18n-skip data-uid="${m.user_id || 0}" data-uname="${esc(m.name || "")}">${name}${m.admin ? " 👑" : ""}</button>`;
      div.innerHTML = `<div class="tchat-meta">${nameHtml}<span class="tchat-time">${time}</span>${canDel ? `<button data-del="${m.id}" title="${esc(T("chat.btn.delete", "Удалить"))}" style="margin-left:auto;background:transparent;border:0;color:#6b7da0;cursor:pointer">✕</button>` : ""}</div><div class="tchat-text" data-i18n-skip>${text}</div>`;
      container.appendChild(div);
    }
    stickToBottom(container);
  }

  function renderSvcMsgs(list, container) {
    if (!container) return;
    container.__tchatLast = { render: "svc", list };
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="tchat-empty">' +
        T("chat.empty.services", "Пока нет личных сигналов.") + '<br><small>' +
        T("chat.empty.services_note",
          "Сюда приходят только ваши сигналы по настройкам кабинета: стакан, пампы, алерты, корреляции. 30 дней истории.") +
        '</small></div>';
      return;
    }
    const empty = container.querySelector(".tchat-empty");
    if (empty && list.length) empty.remove();
    for (const m of list) {
      if (container.querySelector(`[data-mid="${m.id}"]`)) continue;
      const div = document.createElement("div");
      const kind = String(m.kind || "alert");
      div.className = "tchat-msg svc " + esc(kind);
      div.dataset.mid = m.id;
      const time = fmtTime(m.ts);
      const text = svcText(m);                       // сигнал на языке посетителя
      const meta = m.meta || {};
      const sym = meta.symbol ? esc(coin(meta.symbol)) : "";
      const label = kindLabel(kind);
      div.innerHTML = `<div class="tchat-meta"><span class="tchat-svc-kind">${label}${sym ? " · " + sym : ""}</span><span class="tchat-time">${time}</span></div><div class="tchat-text" data-i18n-skip>${text}</div>`;
      container.appendChild(div);
    }
    stickToBottom(container);
  }

  function renderSupMsgs(list, container, me) {
    if (!container) return;
    if (!list.length && !container.children.length) {
      const isGuest = !me;
      container.__tchatLast = { render: "sup", list, me, opts: {} };
      container.innerHTML = '<div class="tchat-empty">' + (isGuest ?
        T("chat.empty.support_guest", "Напишите нам — отвечаем быстро 🆘") + '<br><small>' +
        T("chat.empty.support_guest_note", "Вы как гость, введите временный ник ниже. Админ ответит в этот же тред. 30 дней истории.") + '</small>' :
        T("chat.empty.support_user", "Напишите в поддержку — отвечаем быстро 🆘") + '<br><small>' +
        T("chat.empty.support_user_note", "Ваш личный чат с поддержкой, 30 дней истории") + '</small>') + '</div>';
      return;
    }
    container.__tchatLast = { render: "msgs", list, me, opts: {} };
    const empty = container.querySelector(".tchat-empty");
    if (empty && list.length) empty.remove();
    for (const m of list) {
      if (container.querySelector(`[data-mid="${m.id}"]`)) continue;
      const div = document.createElement("div");
      const mine = !!(me && me.id && m.user_id === me.id);
      const isAdminMsg = !!m.admin;
      div.className = "tchat-msg" + (isAdminMsg ? " admin" : "") + (mine ? " mine" : "") + (m.guest ? " guest" : "");
      div.dataset.mid = m.id;
      const name = esc(m.name || (m.guest ? T("chat.label.guest_name", "Гость") : "anon"));
      const time = fmtTime(m.ts);
      const text = esc(m.text);
      const canDel = me && me.is_admin;
      const adminBadge = m.admin ? " 👑 " + T("chat.label.admin", "админ")
        : (m.guest ? " · " + T("chat.label.guest", "гость") : "");
      const nameHtml = `<span class="tchat-name" data-i18n-skip>${name}${adminBadge}</span>`;
      div.innerHTML = `<div class="tchat-meta">${nameHtml}<span class="tchat-time">${time}</span>${canDel ? `<button data-del-sup="${m.id}" title="${esc(T("chat.btn.delete", "Удалить"))}" style="margin-left:auto;background:transparent;border:0;color:#6b7da0;cursor:pointer">✕</button>` : ""}</div><div class="tchat-text" data-i18n-skip>${text}</div>`;
      container.appendChild(div);
    }
    stickToBottom(container);
  }

  async function fetchMe() {
    try {
      const r = await fetch(API + "/me", { credentials: "same-origin" });
      if (!r.ok) return null;
      const j = await r.json();
      return j && j.user ? { id: j.user.id, name: j.user.name, is_admin: j.user.is_admin } : null;
    } catch { return null; }
  }

  function init() {
    const root = $("#terminal-chat");
    if (!root) return;
    const toggle = $("#tchat-toggle");
    let headerBtn = $("#tchat-header-btn");
    const panel = $("#tchat-panel");
    const closeBtn = $("#tchat-close");
    const listEl = $("#tchat-list");
    const input = $("#tchat-input");
    const sendBtn = $("#tchat-send");
    const hint = $("#tchat-hint");
    let onlineEl = $("#tchat-online");
    const svcListEl = $("#tchat-services");
    const supListEl = $("#tchat-support-list");
    const supWrap = $("#tchat-support");
    const supNameWrap = $("#tchat-support-name-wrap");
    const supNameInput = $("#tchat-support-name");
    const supThreadsEl = $("#tchat-support-threads");
    const muteBtn = $("#tchat-mute");

    const isTerminalPage = !!document.getElementById("tv-chart-container");
    if (toggle && !headerBtn) {
      toggle.classList.remove("hidden");
      toggle.removeAttribute("aria-hidden");
      toggle.removeAttribute("tabindex");
      if (!isTerminalPage) {
        root.style.top = "auto"; root.style.bottom = "18px"; root.style.right = "18px"; root.style.left = "auto";
      }
    }
    if (!isTerminalPage && !loadPos()) {
      root.style.top = "auto"; root.style.bottom = "18px"; root.style.right = "18px"; root.style.left = "auto";
    }
    (function ensureHeaderBtn(){
      if (headerBtn) return;
      try {
        const nav = document.querySelector(".site-nav, .header-controls, .top-nav, header");
        if (!nav) return;
        const btn = document.createElement("button");
        btn.id = "tchat-header-btn"; btn.className = "tchat-header-btn"; btn.type = "button";
        btn.title = T("chat.toggle", "💬 Чат");
        btn.innerHTML = esc(T("chat.toggle", "💬 Чат")) +
          ' <span id="tchat-online" class="tchat-online" title="' + esc(T("chat.online_title", "онлайн в чате")) +
          '">0</span> <span id="tchat-unread-hdr" class="tchat-unread hidden">0</span>';
        nav.appendChild(btn);
      } catch {}
    })();
    headerBtn = $("#tchat-header-btn") || headerBtn;
    onlineEl = $("#tchat-online") || onlineEl;

    let me = null;
    let lastId = 0;
    try { lastId = parseInt(localStorage.getItem(LS_LAST_ID) || "0", 10) || 0; } catch { }
    let lastSvcId = 0;
    let lastSupId = 0;
    let publicUnread = 0, svcUnread = 0, supUnread = 0;
    let isOpen = false;
    try { isOpen = localStorage.getItem(LS_OPEN) === "1"; } catch { }

    const muteMap = { public: false, dm: false, services: false, support: false };
    function loadMute() {
      for (const k of Object.keys(muteMap)) {
        try { const v = localStorage.getItem(LS_MUTE_PFX + k); muteMap[k] = v === "1"; } catch { }
      }
    }
    loadMute();
    function saveMute(tab) {
      try { localStorage.setItem(LS_MUTE_PFX + tab, muteMap[tab] ? "1" : "0"); } catch { }
    }
    let audioCtx = null;
    function ensureAudio() {
      if (audioCtx) return audioCtx;
      try {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        audioCtx = new AC();
      } catch { return null; }
      return audioCtx;
    }
    function playSound(tab) {
      const t = tab || view;
      if (muteMap[t]) return;
      try {
        const ctx = ensureAudio();
        if (!ctx) return;
        if (ctx.state === "suspended") ctx.resume().catch(() => {});
        const o = ctx.createOscillator(); const g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        const freqs = { public: 880, dm: 1200, services: 660, support: 520 };
        o.frequency.value = freqs[t] || 800; o.type = "sine";
        const now = ctx.currentTime;
        g.gain.setValueAtTime(0, now);
        g.gain.linearRampToValueAtTime(0.18, now + 0.02);
        g.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
        o.start(now); o.stop(now + 0.38);
      } catch {}
    }

    let view = "public";
    let rooms = [];
    let roomId = 0, roomLastId = 0, roomInfo = null;
    let dmCounters = { unread: 0, invites: 0 };
    let supThreads = [];
    let supCurrentThreadKey = "";
    let supThreadsCollapsed = false;
    try { supThreadsCollapsed = localStorage.getItem(LS_SUP_THREADS_COLLAPSED) === "1"; } catch {}
    let supGuestToken = getGuestToken();

    function dmlistEl() { return $("#tchat-dm-list"); }
    function dmViewEl() { return $("#tchat-dm"); }
    function dmChatEl() { return $("#tchat-dm-chat"); }
    function dmMsgsEl() { return $("#tchat-dm-msgs"); }
    function dmHeadEl() { return $("#tchat-dm-head"); }

    /** Перерисовать ленту на новом языке: рендеры помнят, что показывают
     *  (``__tchatLast``), поэтому смена языка не требует новых запросов.
     *  Живые подсказки (что висит под полем ввода) обновляет ``updateAuthUI``;
     *  короткие сообщения вроде «Отправка…» остаются как были — их сменяет
     *  следующее же действие. */
    function relabelLists() {
      const boxes = [listEl, svcListEl, supListEl, dmMsgsEl()];
      for (const el of boxes) {
        const last = el && el.__tchatLast;
        if (!last) continue;
        jumpToLast(el);                       // после перерисовки — снова к последним
        el.innerHTML = "";
        if (last.render === "svc") renderSvcMsgs(last.list, el);
        else if (last.render === "sup") renderSupMsgs(last.list, el, last.me);
        else renderMsgs(last.list, el, last.me, last.opts);
      }
    }
    function relabelAll() {
      relabelLists();
      updateMuteBtn(); updateAuthUI(); setBadge();
      renderSupThreads();
      renderRooms();
    }
    try {
      if (window.LiqScopeI18n && window.LiqScopeI18n.onChange) {
        window.LiqScopeI18n.onChange(relabelAll);
      }
    } catch (e) { /* без движка словарей чат остаётся на русском */ }

    function setBadge() {
      const total = publicUnread + dmCounters.unread + (dmCounters.invites || 0) + svcUnread + supUnread;
      const notify = total > 0;
      [$("#tchat-unread"), $("#tchat-unread-hdr")].forEach((el) => {
        if (!el) return;
        el.textContent = total > 99 ? "99+" : String(total);
        el.classList.toggle("hidden", total <= 0);
      });
      const dmBadge = $("#tchat-tab-dm-badge");
      if (dmBadge) {
        const s = dmCounters.unread + (dmCounters.invites || 0);
        dmBadge.textContent = s > 99 ? "99+" : String(s);
        dmBadge.classList.toggle("hidden", s <= 0);
      }
      const svcBadge = $("#tchat-tab-svc-badge");
      if (svcBadge) {
        svcBadge.textContent = svcUnread > 99 ? "99+" : String(svcUnread);
        svcBadge.classList.toggle("hidden", svcUnread <= 0);
      }
      const supBadge = $("#tchat-tab-sup-badge");
      if (supBadge) {
        supBadge.textContent = supUnread > 99 ? "99+" : String(supUnread);
        supBadge.classList.toggle("hidden", supUnread <= 0);
      }
      root.classList.toggle("tchat-notify", !!notify && !isOpen);
      if (panel) panel.classList.toggle("tchat-notify", !!notify);
      if (headerBtn) headerBtn.classList.toggle("tchat-notify", !!notify && !isOpen);
      if (toggle) toggle.classList.toggle("tchat-notify", !!notify && !isOpen);
    }
    function setOnline(count) {
      if (!onlineEl) return;
      const n = Math.max(0, Number(count) || 0);
      onlineEl.textContent = String(n);
      onlineEl.classList.toggle("on", n > 0);
      onlineEl.title = n > 0
        ? T("chat.online.some", "{n} онлайн в чате", { n: num(n) })
        : T("chat.online.none", "никто не онлайн");
    }
    function updateMuteBtn() {
      if (!muteBtn) return;
      const tab = view === "rooms" || view === "room" ? "dm" : view;
      const muted = !!muteMap[tab];
      muteBtn.textContent = muted ? "🔇" : "🔊";
      muteBtn.classList.toggle("muted", muted);
      muteBtn.title = muted ? T("chat.mute.off_title", "Звук выкл — нажать чтобы включить")
                            : T("chat.mute.on_title", "Звук вкл — нажать чтобы выключить");
    }

    function updateTabsForAuth() {
      const tabs = document.querySelectorAll("#tchat-tabs .tchat-tab");
      tabs.forEach((b) => {
        const tab = b.getAttribute("data-tab");
        if (!me) {
          if (tab !== "support") b.classList.add("hidden");
          else b.classList.remove("hidden");
        } else {
          b.classList.remove("hidden");
        }
      });
      if (!me && view !== "support") {
        setView("support");
      }
    }

    function setView(v, arg) {
      if (!me && v !== "support") v = "support";
      view = v;
      const list = listEl, dm = dmViewEl(), chat = dmChatEl(), svc = svcListEl, sup = supWrap;
      if (list) list.classList.toggle("hidden", v !== "public");
      if (dm) dm.classList.toggle("hidden", v !== "rooms");
      if (chat) chat.classList.toggle("hidden", v !== "room");
      if (svc) svc.classList.toggle("hidden", v !== "services");
      if (sup) sup.classList.toggle("hidden", v !== "support");
      document.querySelectorAll("#tchat-tabs .tchat-tab").forEach((b) => {
        const tab = b.getAttribute("data-tab");
        if (tab === "services") b.classList.toggle("active", v === "services");
        else if (tab === "support") b.classList.toggle("active", v === "support");
        else if (tab === "public") b.classList.toggle("active", v === "public");
        else if (tab === "dm") b.classList.toggle("active", v === "rooms" || v === "room");
      });
      // Вход в раздел = «покажи последние записи»: метим контейнер, и первый же
      // рендер после загрузки прыгнет вниз
      if (v === "public") { publicUnread = 0; setBadge(); jumpToLast(listEl); }
      if (v === "services") { svcUnread = 0; setBadge(); jumpToLast(svcListEl); }
      if (v === "support") { supUnread = 0; setBadge(); jumpToLast(supListEl); }
      if (v === "room") jumpToLast(dmMsgsEl());
      if (v === "room" && arg) {
        roomId = arg; roomInfo = null; roomLastId = 0;
        if (dmMsgsEl()) dmMsgsEl().innerHTML = '<div class="tchat-empty">' + T("chat.empty.loading", "загружаем…") + '</div>';
        paintRoomHead(); refreshRoom(true);
      }
      if (v === "rooms") refreshRooms();
      if (v === "services") refreshServices(true);
      if (v === "support") { refreshSupport(true); if (me && me.is_admin) refreshSupThreads(); }
      updateAuthUI(); updateMuteBtn();
    }

    function setOpen(v) {
      isOpen = !!v;
      root.classList.toggle("open", isOpen);
      document.body.classList.toggle("tchat-open", isOpen);
      if (headerBtn) headerBtn.classList.toggle("active", isOpen);
      if (panel) panel.classList.toggle("hidden", !isOpen);
      try { localStorage.setItem(LS_OPEN, isOpen ? "1" : "0"); } catch {}
      setBadge();
      if (isOpen) {
        // Панель открыли — снова к последним записям
        if (view === "public") { publicUnread = 0; setBadge(); jumpToLast(listEl); }
        if (view === "services") { svcUnread = 0; jumpToLast(svcListEl); }
        if (view === "support") { supUnread = 0; jumpToLast(supListEl); }
        if (view === "room") jumpToLast(dmMsgsEl());
        if (view === "room") refreshRoom(false);
        else if (view === "rooms") refreshRooms();
        else if (view === "services") refreshServices(false);
        else if (view === "support") { refreshSupport(false); if (me && me.is_admin) refreshSupThreads(); }
        ensureAudio();
      }
    }

    (function bindDrag() {
      const head = root.querySelector(".tchat-head");
      if (!head) return;
      const saved = loadPos();
      if (saved) applyPos(root, panel, saved);
      let dragging = false, startX = 0, startY = 0, startLeft = 0, startTop = 0, moved = false;
      function onDown(ev) {
        if (ev.target.closest && ev.target.closest("#tchat-close")) return;
        if (ev.target.closest && ev.target.closest(".tchat-mute")) return;
        if (ev.type === "mousedown" && ev.button !== 0) return;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        dragging = true; moved = false; startX = p.clientX; startY = p.clientY;
        const rect = root.getBoundingClientRect(); startLeft = rect.left; startTop = rect.top;
        root.classList.add("tchat-dragging"); ev.preventDefault();
      }
      function onMove(ev) {
        if (!dragging) return;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        const dx = p.clientX - startX, dy = p.clientY - startY;
        if (!moved && Math.hypot(dx, dy) < 3) return;
        moved = true;
        const vw = window.innerWidth, vh = window.innerHeight;
        const rect = root.getBoundingClientRect(); const w = rect.width, h = rect.height;
        let nl = clamp(startLeft + dx, 4, Math.max(4, vw - w - 4));
        let nt = clamp(startTop + dy, 4, Math.max(4, vh - h - 4));
        root.style.left = nl + "px"; root.style.top = nt + "px";
        root.style.right = "auto"; root.style.bottom = "auto";
        root.classList.add("tchat-moved");
      }
      function onUp() {
        if (!dragging) return;
        dragging = false; root.classList.remove("tchat-dragging");
        if (moved) {
          const rect = root.getBoundingClientRect();
          savePos(rect.left, rect.top, panel.offsetWidth || rect.width, panel.offsetHeight || rect.height);
        }
      }
      head.addEventListener("mousedown", onDown);
      head.addEventListener("touchstart", onDown, { passive: false });
      window.addEventListener("mousemove", onMove);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("mouseup", onUp);
      window.addEventListener("touchend", onUp);
      window.addEventListener("touchcancel", onUp);
      head.addEventListener("dblclick", (ev) => {
        if (ev.target.closest && (ev.target.closest("#tchat-close") || ev.target.closest(".tchat-tab") || ev.target.closest(".tchat-mute"))) return;
        resetPos(root, panel);
      });
      window.addEventListener("resize", () => { const pos = loadPos(); if (pos) applyPos(root, panel, pos); });
    })();

    (function bindResize() {
      if (!panel) return;
      const dirs = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
      for (const d of dirs) {
        const h = document.createElement("span"); h.className = "tchat-rz tchat-rz-" + d; h.dataset.rz = d; root.appendChild(h);
      }
      let rzDir = "", sx = 0, sy = 0, sw = 0, sh = 0, sl = 0, st = 0;
      function rectStart() {
        const pr = panel.getBoundingClientRect(); sw = pr.width; sh = pr.height;
        sl = root.getBoundingClientRect().left; st = root.getBoundingClientRect().top;
      }
      function down(ev) {
        const d = ev.target.closest && ev.target.closest(".tchat-rz");
        if (!d) return;
        if (ev.type === "mousedown" && ev.button !== 0) return;
        rzDir = d.dataset.rz;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        sx = p.clientX; sy = p.clientY; rectStart();
        panel.classList.add("tchat-resized"); root.classList.add("tchat-dragging");
        ev.preventDefault(); ev.stopPropagation();
      }
      function move(ev) {
        if (!rzDir) return;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        const dx = p.clientX - sx, dy = p.clientY - sy;
        const vw = window.innerWidth, vh = window.innerHeight;
        let w = sw, h = sh, l = sl, t = st;
        if (rzDir.indexOf("e") !== -1) w = sw + dx;
        if (rzDir.indexOf("s") !== -1) h = sh + dy;
        if (rzDir.indexOf("w") !== -1) { w = sw - dx; l = sl + dx; }
        if (rzDir.indexOf("n") !== -1) { h = sh - dy; t = st + dy; }
        w = clamp(w, MIN_W, vw - 8 - Math.max(0, l - 4));
        h = clamp(h, MIN_H, vh - 8 - Math.max(0, t - 4));
        if (rzDir.indexOf("w") !== -1) l = sl + sw - w;
        if (rzDir.indexOf("n") !== -1) t = st + sh - h;
        l = clamp(l, 4, Math.max(4, vw - w - 4)); t = clamp(t, 4, Math.max(4, vh - h - 4));
        panel.style.width = Math.round(w) + "px"; panel.style.height = Math.round(h) + "px";
        root.style.left = Math.round(l) + "px"; root.style.top = Math.round(t) + "px";
        root.style.right = "auto"; root.style.bottom = "auto"; root.classList.add("tchat-moved");
      }
      function up() {
        if (!rzDir) return; rzDir = ""; root.classList.remove("tchat-dragging");
        savePos(root.getBoundingClientRect().left, root.getBoundingClientRect().top, panel.offsetWidth, panel.offsetHeight);
      }
      root.addEventListener("mousedown", down);
      root.addEventListener("touchstart", down, { passive: false });
      window.addEventListener("mousemove", move);
      window.addEventListener("touchmove", move, { passive: false });
      window.addEventListener("mouseup", up);
      window.addEventListener("touchend", up);
      window.addEventListener("touchcancel", up);
    })();

    let menuEl = null;
    function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
    function openMenu(x, y, uid, uname) {
      closeMenu();
      menuEl = document.createElement("div"); menuEl.className = "tchat-usermenu";
      const self = me && me.id === uid;
      menuEl.innerHTML = `<div class="tchat-usermenu-name" data-i18n-skip>${esc(uname || "user")}</div>` +
        `<button type="button" data-act="reply">${esc(T("chat.btn.reply", "↩ Ответить"))}</button>` +
        (self ? "" : `<button type="button" data-act="dm">${esc(T("chat.btn.dm", "✉ Написать лично"))}</button>`);
      document.body.appendChild(menuEl);
      const vw = window.innerWidth; const rect = menuEl.getBoundingClientRect();
      menuEl.style.left = Math.min(x, vw - (rect.width || 180) - 10) + "px";
      menuEl.style.top = (y + 6) + "px";
      menuEl.addEventListener("click", async (ev) => {
        const b = ev.target.closest("[data-act]"); if (!b) return;
        const act = b.dataset.act; closeMenu();
        if (act === "reply") {
          if (input) { input.value = "@" + (uname || "") + " " + (input.value || ""); input.focus(); }
        } else if (act === "dm" && uid) { await openDmWith(uid, uname); }
      });
      setTimeout(() => {
        const dismiss = (ev) => {
          if (menuEl && !menuEl.contains(ev.target)) { closeMenu(); document.removeEventListener("mousedown", dismiss, true); }
        };
        document.addEventListener("mousedown", dismiss, true);
      }, 0);
    }
    window.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeMenu(); });
    window.addEventListener("scroll", closeMenu, true);

    function bindNameClicks(container) {
      if (!container || container.dataset.namelink) return;
      container.dataset.namelink = "1";
      container.addEventListener("click", (ev) => {
        const nb = ev.target.closest(".tchat-namelink"); if (!nb) return;
        ev.preventDefault(); ev.stopPropagation();
        const uid = parseInt(nb.dataset.uid, 10) || 0; const uname = nb.dataset.uname || "";
        const r = nb.getBoundingClientRect(); openMenu(r.left, r.bottom, uid, uname);
      });
    }

    async function openDmWith(peerId, peerName) {
      if (!me) { setHint(T("chat.hint.dm_need_login", "Войдите чтобы писать личные"), true); return; }
      try {
        const j = await jget(DM_API + "/invite", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ peer_id: peerId }),
        });
        if (j && j.room) {
          setHint(T("chat.hint.dm_opened", "✉ Личный диалог с {name}", { name: peerName || j.room.peer.name }) +
            (j.room.status === "pending" ? T("chat.hint.dm_invited", " — приглашение отправлено") : ""), false);
          setView("room", j.room.id);
        }
      } catch (e) { setHint(e.message === "error"
        ? T("chat.hint.dm_open_fail", "Не удалось открыть диалог")
        : (e.message || T("chat.hint.error", "Ошибка")), true); }
    }

    function setHint(t, err) {
      if (!hint) return; hint.textContent = t || ""; hint.classList.toggle("error", !!err);
    }

    function updateAuthUI() {
      if (!input || !sendBtn) return;
      if (view === "services") {
        if (!me) { input.disabled = true; sendBtn.disabled = true;
          setHint(T("chat.hint.services_guest", "🔔 Только для зарегистрированных — войдите чтобы видеть личные сигналы"), true); return; }
        input.disabled = true; sendBtn.disabled = true;
        setHint(T("chat.hint.services_readonly", "🔔 Только чтение — ваши личные сигналы по настройкам кабинета (30 дней)"), false);
        if (supNameWrap) supNameWrap.classList.add("hidden");
        return;
      }
      if (view === "support") {
        input.disabled = false; sendBtn.disabled = false;
        if (!me) {
          if (supNameWrap) supNameWrap.classList.remove("hidden");
          try {
            const saved = getGuestName();
            if (saved && supNameInput && !supNameInput.value) supNameInput.value = saved;
          } catch {}
          const hasName = !!(supNameInput && supNameInput.value.trim()) || !!getGuestName();
          input.placeholder = hasName
            ? T("chat.ph.support", "Сообщение в поддержку… (Enter)")
            : T("chat.ph.support_name", "Сначала введите временный ник ниже…");
          setHint(hasName
            ? T("chat.hint.support_guest", "🆘 Поддержка — ваш личный тред (гость, 30 дней)")
            : T("chat.hint.support_need_name", "🆘 Введите временный ник чтобы писать — вас найдут по нему"), false);
        } else {
          if (supNameWrap) supNameWrap.classList.add("hidden");
          if (me.is_admin && supCurrentThreadKey) {
            input.placeholder = T("chat.ph.support_thread", "Ответ в тред {tk}… (Enter)", { tk: supCurrentThreadKey });
            setHint(T("chat.hint.support_admin_thread",
              "Вы админ • отвечаете в {tk} • клик по треду снова — свернуть", { tk: supCurrentThreadKey }), false);
          } else if (me.is_admin) {
            input.placeholder = T("chat.ph.support_pick", "Выберите тред выше или ждите сообщений…");
            setHint(T("chat.hint.support_admin_pick",
              "Вы админ • выберите тред для ответа, новые сообщения приходят в бот и с задержкой"), false);
          } else {
            input.placeholder = T("chat.ph.support_write", "Написать в поддержку… (Enter)");
            setHint(T("chat.hint.support_personal", "Вы: {name} • личный чат поддержки 30 дней",
              { name: me.name || "id" + me.id }), false);
          }
        }
        return;
      }
      if (view === "rooms") {
        input.disabled = true; sendBtn.disabled = true;
        setHint(T("chat.hint.pick_room", "Выберите диалог слева или пригласите пользователя"), false);
        if (supNameWrap) supNameWrap.classList.add("hidden");
        return;
      }
      if (view === "room" && roomInfo && roomInfo.status === "pending") {
        input.placeholder = T("chat.ph.accept", "Ответ = принять приглашение… (Enter)");
      } else if (view === "room") {
        input.placeholder = T("chat.ph.dm", "Личное сообщение… (Enter) — 30 дней");
      } else {
        input.placeholder = T("chat.ph.public", "Сообщение… (Enter)");
      }
      if (supNameWrap) supNameWrap.classList.add("hidden");
      if (!me) {
        input.placeholder = T("chat.ph.guest", "Только поддержка доступна гостям — войдите для остальных чатов");
        input.disabled = true; sendBtn.disabled = true;
        hint.innerHTML = esc(T("chat.hint.guests_only_support", "Гостям только поддержка —")) +
          ' <a href="/login?next=' + encodeURIComponent(location.pathname) + '" style="color:#8ab4ff">' +
          esc(T("chat.hint.sign_in", "войти")) + '</a> ' +
          esc(T("chat.hint.for_public_and_dm", "для общего и личных"));
        return;
      }
      input.disabled = false; sendBtn.disabled = false;
      if (view === "public") setHint(T("chat.hint.public_history", "Вы: {name} • 3 дня истории",
        { name: me.name || "id" + me.id }), false);
      else if (roomInfo) {
        const incomingPending = roomInfo.status === "pending" && roomInfo.invited_by !== me.id;
        setHint(incomingPending
          ? T("chat.hint.dm_incoming", "💌 Входящее приглашение — ответ = принять диалог")
          : T("chat.hint.dm_room", "🔒 Личный диалог с {name} • 30 дней • можно удалить",
              { name: roomInfo.peer.name }), false);
      }
    }

    async function fetchList(afterId, limit) {
      try {
        let url = API + "?limit=" + (limit || 100);
        if (afterId) url += "&after_id=" + afterId;
        const r = await fetch(url, { credentials: "same-origin" });
        if (!r.ok) return [];
        const j = await r.json();
        return (j && j.messages) ? j.messages : [];
      } catch { return []; }
    }
    async function loadInitial() {
      me = await fetchMe();
      updateTabsForAuth();
      updateAuthUI(); updateMuteBtn();
      if (me) {
        const msgs = await fetchList(0, 100);
        if (msgs.length) {
          lastId = Math.max(lastId, ...msgs.map(m => m.id));
          try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {}
        }
        if (listEl) { listEl.innerHTML = ""; renderMsgs(msgs, listEl, me, {}); }
        refreshServices(true).catch(() => {});
      } else {
        if (listEl) listEl.innerHTML = '<div class="tchat-empty">' +
          T("chat.empty.guest_public", "Гостям доступен только таб «Поддержка» — войдите чтобы видеть общий чат.") +
          '<br><small>' + T("chat.empty.guest_public_note", "Поддержка 30 дней, личный тред") + '</small></div>';
      }
      refreshSupport(true).catch(() => {});
      if (me && me.is_admin) refreshSupThreads().catch(() => {});
    }
    async function poll() {
      if (!me) return;
      try {
        const msgs = await fetchList(lastId, 200);
        if (!msgs.length) return;
        let newOnes = 0;
        for (const m of msgs) { if (m.id > lastId) { lastId = m.id; newOnes++; } }
        if (newOnes) {
          try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {}
          if (view === "public") renderMsgs(msgs, listEl, me, {});
          else publicUnread += newOnes;
          if (newOnes && view !== "public") playSound("public");
          else if (newOnes && !isOpen) playSound("public");
          setBadge();
        }
      } catch {}
    }

    async function fetchSvc(afterId, limit) {
      try {
        let url = SVC_API + "?limit=" + (limit || 100);
        if (afterId) url += "&after_id=" + afterId;
        const r = await fetch(url, { credentials: "same-origin" });
        if (!r.ok) return [];
        const j = await r.json();
        return (j && j.messages) ? j.messages : [];
      } catch { return []; }
    }
    async function refreshServices(first) {
      if (!me) return;
      try {
        const msgs = await fetchSvc(first ? 0 : lastSvcId, first ? 100 : 200);
        if (!msgs.length) {
          if (first && svcListEl && !svcListEl.children.length) renderSvcMsgs([], svcListEl);
          return;
        }
        let newOnes = 0;
        for (const m of msgs) { if (m.id > lastSvcId) { lastSvcId = m.id; newOnes++; } }
        if (first) {
          if (svcListEl) { svcListEl.innerHTML = ""; renderSvcMsgs(msgs, svcListEl); }
          if (msgs.length) lastSvcId = Math.max(...msgs.map(m => m.id));
        } else {
          if (view === "services") renderSvcMsgs(msgs, svcListEl);
          else svcUnread += newOnes;
          if (newOnes) { if (view !== "services") playSound("services"); setBadge(); }
        }
      } catch {}
    }
    async function pollServices() { if (me) await refreshServices(false); }

    async function fetchSup(afterId, limit) {
      try {
        let url = SUP_API + "?limit=" + (limit || 100);
        if (afterId) url += "&after_id=" + afterId;
        if (!me) {
          url += "&guest_token=" + encodeURIComponent(supGuestToken);
        } else if (me.is_admin && supCurrentThreadKey) {
          url += "&thread_key=" + encodeURIComponent(supCurrentThreadKey);
        }
        const r = await fetch(url, { credentials: "same-origin" });
        if (!r.ok) return [];
        const j = await r.json();
        return (j && j.messages) ? j.messages : [];
      } catch { return []; }
    }
    async function refreshSupport(first) {
      try {
        const msgs = await fetchSup(first ? 0 : lastSupId, first ? 100 : 200);
        if (!msgs.length) {
          if (first && supListEl && !supListEl.children.length) renderSupMsgs([], supListEl, me);
          return;
        }
        let newOnes = 0;
        for (const m of msgs) { if (m.id > lastSupId) { lastSupId = m.id; newOnes++; } }
        if (first) {
          if (supListEl) { supListEl.innerHTML = ""; renderSupMsgs(msgs, supListEl, me); }
          if (msgs.length) lastSupId = Math.max(...msgs.map(m => m.id));
        } else {
          if (view === "support") renderSupMsgs(msgs, supListEl, me);
          else supUnread += newOnes;
          if (newOnes) { if (view !== "support") playSound("support"); setBadge(); }
        }
      } catch {}
    }
    async function pollSupport() { await refreshSupport(false); }

    async function fetchSupThreads() {
      if (!me || !me.is_admin) return [];
      try {
        const j = await jget(SUP_THREADS_API + "?limit=100", { credentials: "same-origin" });
        return (j && j.threads) ? j.threads : [];
      } catch { return []; }
    }
    async function refreshSupThreads() {
      if (!me || !me.is_admin) return;
      const threads = await fetchSupThreads();
      supThreads = threads;
      renderSupThreads();
    }
    function renderSupThreads() {
      if (!supThreadsEl) return;
      if (!me || !me.is_admin) { supThreadsEl.innerHTML = ""; supThreadsEl.classList.add("hidden"); return; }
      supThreadsEl.classList.remove("hidden");
      const collapsed = !!supThreadsCollapsed;
      const cur = supCurrentThreadKey;
      const header = `<div class="tchat-threads-head" id="tchat-sup-threads-toggle" style="display:flex;align-items:center;justify-content:space-between;padding:6px 8px;border-bottom:1px solid #1e2e55;background:#0e182d;cursor:pointer"><span>${esc(T("chat.threads", "💬 Треды поддержки ({n})", { n: num(supThreads.length) }))}${cur ? ` · <code style="font-size:11px">${esc(cur)}</code>` : ""}</span><button type="button" class="tchat-mini">${collapsed ? esc(T("chat.btn.expand", "▼ Развернуть")) : esc(T("chat.btn.collapse", "▲ Свернуть"))}</button></div>`;
      if (!supThreads.length) {
        supThreadsEl.innerHTML = header + '<div class="tchat-empty muted" style="padding:8px">' +
          T("chat.empty.no_threads", "Нет обращений в поддержку пока") + '</div>';
        const h = supThreadsEl.querySelector("#tchat-sup-threads-toggle");
        if (h) h.addEventListener("click", () => { supThreadsCollapsed = !supThreadsCollapsed; try { localStorage.setItem(LS_SUP_THREADS_COLLAPSED, supThreadsCollapsed ? "1" : "0"); } catch {}; renderSupThreads(); });
        return;
      }
      let body = "";
      if (!collapsed) {
        body = supThreads.map((t) => {
          const tk = esc(t.thread_key || "");
          const last = esc((t.last_text || "").slice(0, 60));
          const name = esc(t.display_name || (t.user_id ? "id" + t.user_id : T("chat.label.guest", "гость")));
          const total = t.total || 0;
          const isCur = tk === cur;
          return `<div class="tchat-dm-item${isCur ? " active" : ""}" data-tkey="${tk}" style="cursor:pointer">
            <div class="tchat-peer"><span data-i18n-skip>${name}</span> <span class="tchat-time" style="margin-left:6px">${fmtTime(t.last_at || 0)}</span><button type="button" class="tchat-mini" data-del-thread="${tk}" style="margin-left:auto" title="${esc(T("chat.btn.delete_thread_title", "Удалить весь тред"))}">🗑</button></div>
            <div class="tchat-last" data-i18n-skip>${last || "—"}</div>
            <div class="tchat-dm-meta"><span class="tchat-time">${tk}</span><span class="tchat-dm-unread">${total}</span></div>
          </div>`;
        }).join("");
      }
      supThreadsEl.innerHTML = header + body;
      const h = supThreadsEl.querySelector("#tchat-sup-threads-toggle");
      if (h) h.addEventListener("click", () => { supThreadsCollapsed = !supThreadsCollapsed; try { localStorage.setItem(LS_SUP_THREADS_COLLAPSED, supThreadsCollapsed ? "1" : "0"); } catch {}; renderSupThreads(); });
      if (!collapsed) {
        supThreadsEl.querySelectorAll("[data-del-thread]").forEach((btn) => {
          btn.addEventListener("click", async (ev) => {
            ev.stopPropagation();
            const tk = btn.getAttribute("data-del-thread") || "";
            if (!tk) return;
            if (!confirm(T("chat.confirm.delete_thread", "Удалить весь тред поддержки {tk}? Все сообщения удалятся.", { tk }))) return;
            try {
              await jget(SUP_API + "/thread/" + encodeURIComponent(tk), { method: "DELETE", credentials: "same-origin" });
              if (supCurrentThreadKey === tk) {
                supCurrentThreadKey = "";
                lastSupId = 0;
                if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">' + T("chat.empty.thread_deleted", "Тред удалён") + '</div>';
              }
              refreshSupThreads();
              updateAuthUI();
              setHint(T("chat.hint.thread_deleted", "🗑 Тред {tk} удалён", { tk }), false);
            } catch (e) {
              setHint(e.message || T("chat.hint.thread_delete_fail", "Не удалось удалить тред"), true);
            }
          });
        });
        supThreadsEl.querySelectorAll("[data-tkey]").forEach((el) => {
          el.addEventListener("click", async (ev) => {
            if (ev.target.closest("[data-del-thread]")) return;
            ev.stopPropagation();
            const tk = el.getAttribute("data-tkey") || "";
            if (supCurrentThreadKey === tk) {
              // сворачиваем — повторный клик по активному треду
              supCurrentThreadKey = "";
              lastSupId = 0;
              if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">' +
                T("chat.empty.thread_closed", "Выберите тред выше для просмотра — тред свернут") + '</div>';
              updateAuthUI();
              renderSupThreads();
              return;
            }
            supCurrentThreadKey = tk;
            lastSupId = 0;
            if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">' + T("chat.empty.loading", "загружаем…") + '</div>';
            jumpToLast(supListEl);          // открыли тред — сразу к последним сообщениям
            await refreshSupport(true);
            updateAuthUI();
            renderSupThreads();
          });
        });
      }
    }

    async function refreshRooms() {
      if (!me) return;
      try {
        const j = await jget(DM_API + "/rooms", { credentials: "same-origin" });
        rooms = (j && j.rooms) || [];
        renderRooms();
      } catch {}
    }
    function renderRooms() {
      const box = dmlistEl(); if (!box) return;
      const top = '<div class="tchat-dm-top"><button type="button" class="tchat-mini" id="tchat-invite-btn">' +
        esc(T("chat.btn.invite", "＋ Пригласить")) + '</button><div class="tchat-invite-form hidden" id="tchat-invite-form">' +
        '<input id="tchat-invite-q" maxlength="64" placeholder="' + esc(T("chat.invite_ph", "ник или @username")) + '">' +
        '<button type="button" class="tchat-mini" id="tchat-invite-go">' + esc(T("chat.btn.find", "Найти")) + '</button></div></div>';
      if (!rooms.length) {
        box.innerHTML = top + '<div class="tchat-empty">' +
          T("chat.empty.no_dm", "Личных диалогов пока нет.") + '<br><small>' +
          T("chat.empty.no_dm_note", "Нажмите на ник в общем чате → «✉ Написать лично», либо «＋ Пригласить». 30 дней истории, можно удалить.") +
          '</small></div>';
        bindInviteUi(box); return;
      }
      const online = new Set(lastOnlineIds);
      const items = rooms.map((r) => {
        const incoming = r.status === "pending" && r.invited_by !== (me && me.id);
        const outgoing = r.status === "pending" && r.invited_by === (me && me.id);
        const last = r.last ? `<div class="tchat-last" data-i18n-skip>${(r.last.user_id === (me && me.id) ? esc(T("chat.label.you", "Вы: ")) : "")}${esc((r.last.text || "").slice(0, 60))}</div>` : '<div class="tchat-last muted">' + T("chat.empty.dm_start", "— диалог начнётся с первого сообщения —") + '</div>';
        const state = incoming
          ? '<span class="tchat-dm-flag inv">' + esc(T("chat.flag.invite", "приглашение")) + '</span>'
          : outgoing ? '<span class="tchat-dm-flag">' + esc(T("chat.flag.waiting", "ждём ответа")) + '</span>' : "";
        const act = incoming ? `<div class="tchat-dm-actions"><button type="button" class="tchat-mini ok" data-accept="${r.id}">${esc(T("chat.btn.accept", "✓ Принять"))}</button><button type="button" class="tchat-mini no" data-decline="${r.id}">${esc(T("chat.btn.decline", "✕ Отклонить"))}</button></div>` : "";
        return `<div class="tchat-dm-item${r.unread ? " unread" : ""}" data-room="${r.id}">
          <div class="tchat-peer"><span class="tchat-peer-dot ${online.has(r.peer.id) ? "on" : ""}"></span><span data-i18n-skip>${esc(r.peer.name)}</span> ${state}<button type="button" class="tchat-mini" data-del-room="${r.id}" style="margin-left:auto" title="${esc(T("chat.btn.delete_room_title", "Удалить чат"))}">🗑</button></div>
          ${last}
          <div class="tchat-dm-meta"><span class="tchat-time">${fmtTime(r.updated_at)}</span>${r.unread ? `<span class="tchat-dm-unread">${r.unread}</span>` : ""}</div>
          ${act}
        </div>`;
      }).join("");
      box.innerHTML = top + items;
      bindInviteUi(box);
      box.querySelectorAll("[data-room]").forEach((el) => {
        el.addEventListener("click", (ev) => {
          if (ev.target.closest("[data-accept],[data-decline],[data-del-room]")) return;
          setView("room", parseInt(el.getAttribute("data-room"), 10));
        });
      });
      box.querySelectorAll("[data-accept],[data-decline]").forEach((b) => {
        b.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const id = parseInt(b.getAttribute("data-accept") || b.getAttribute("data-decline"), 10);
          const accept = b.hasAttribute("data-accept");
          try {
            await jget(DM_API + "/" + id + "/accept", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accept }), });
          } catch (e) { setHint(e.message || T("chat.hint.error", "Ошибка"), true); }
          refreshRooms(); pollBadges();
        });
      });
      box.querySelectorAll("[data-del-room]").forEach((b) => {
        b.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const id = parseInt(b.getAttribute("data-del-room"), 10);
          if (!id) return;
          if (!confirm(T("chat.confirm.delete_room", "Удалить личный чат #{n}? История удалится у обоих.", { n: id }))) return;
          try {
            await jget(DM_API + "/" + id, { method: "DELETE", credentials: "same-origin" });
            if (roomId === id) { setView("rooms"); }
            refreshRooms(); pollBadges();
            setHint(T("chat.hint.room_deleted", "🗑 Чат удалён"), false);
          } catch (e) { setHint(e.message || T("chat.hint.delete_fail", "Не удалось удалить"), true); }
        });
      });
    }
    function bindInviteUi(box) {
      const btn = box.querySelector("#tchat-invite-btn");
      const form = box.querySelector("#tchat-invite-form");
      const q = box.querySelector("#tchat-invite-q");
      const go = box.querySelector("#tchat-invite-go");
      if (btn && form) btn.addEventListener("click", () => { form.classList.toggle("hidden"); if (!form.classList.contains("hidden") && q) q.focus(); });
      if (!q) return;
      let t = null;
      const doSearch = async () => {
        const query = (q.value || "").trim();
        try {
          const j = await jget("/api/chat/users?q=" + encodeURIComponent(query), { credentials: "same-origin" });
          renderInviteResults(box, (j && j.users) || []);
        } catch {}
      };
      q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(doSearch, 350); });
      q.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); doSearch(); } });
      if (go) go.addEventListener("click", doSearch);
    }
    function renderInviteResults(box, users) {
      let res = box.querySelector(".tchat-invite-results");
      if (!res) { res = document.createElement("div"); res.className = "tchat-invite-results"; box.appendChild(res); }
      if (!users.length) { res.innerHTML = '<div class="tchat-empty muted">' +
        T("chat.empty.user_not_found", "Никого не нашли — пользователь должен быть зарегистрирован.") + '</div>'; return; }
      const online = new Set(lastOnlineIds);
      res.innerHTML = users.map((u) => `<div class="tchat-inv-item" data-uid="${u.id}" data-uname="${esc(u.name)}"><span class="tchat-peer-dot ${online.has(u.id) ? "on" : ""}"></span><span class="tchat-inv-name" data-i18n-skip>${esc(u.name)}${u.admin ? " 👑" : ""}</span><button type="button" class="tchat-mini">${esc(T("chat.btn.invite_user", "✉ Пригласить"))}</button></div>`).join("");
      res.querySelectorAll(".tchat-inv-item").forEach((el) => {
        el.addEventListener("click", async () => {
          const uid = parseInt(el.getAttribute("data-uid"), 10); const uname = el.getAttribute("data-uname") || "";
          if (uid && uid !== (me && me.id)) await openDmWith(uid, uname);
        });
      });
    }
    async function refreshRoom(first) {
      if (view !== "room" || !roomId || !me) return;
      try {
        const j = await jget(DM_API + "/" + roomId + "/messages?after_id=" + (first ? 0 : roomLastId), { credentials: "same-origin" });
        roomInfo = j.room || roomInfo;
        const msgs = (j && j.messages) || [];
        for (const m of msgs) roomLastId = Math.max(roomLastId, m.id);
        const box = dmMsgsEl();
        if (box) {
          if (first) box.innerHTML = "";
          renderMsgs(msgs, box, me, { bubbles: true, emptyHtml:
            T("chat.empty.dm_private", "Диалог пуст — напишите первым 🔒") + '<br><small>' +
            T("chat.empty.dm_private_note", "Приватно • 30 дней • можно удалить") + '</small>' });
        }
        paintRoomHead(); updateAuthUI(); pollBadges();
      } catch (e) {
        if (e.status === 403 && e.body && e.body.error === "declined") {
          if (dmMsgsEl()) dmMsgsEl().innerHTML = '<div class="tchat-empty">' + T("chat.empty.dm_declined", "Приглашение отклонено.") + '</div>';
        }
      }
    }
    function paintRoomHead() {
      const head = dmHeadEl(); if (!head) return;
      if (!roomInfo) { head.innerHTML = '<button type="button" class="tchat-mini" id="tchat-room-back">' +
        esc(T("chat.btn.back", "← Диалоги")) + '</button>'; return; }
      const online = new Set(lastOnlineIds);
      const st = roomInfo.status === "pending"
        ? (roomInfo.invited_by === (me && me.id)
            ? '<span class="tchat-dm-flag">' + esc(T("chat.flag.pending_mine", "приглашение ждёт ответа")) + '</span>'
            : '<span class="tchat-dm-flag inv">' + esc(T("chat.flag.pending_other", "вы можете принять или ответить")) + '</span>')
        : "";
      head.innerHTML = `<button type="button" class="tchat-mini" id="tchat-room-back">←</button><span class="tchat-peer tchat-peer-head" data-i18n-skip><span class="tchat-peer-dot ${online.has(roomInfo.peer.id) ? "on" : ""}"></span>${esc(roomInfo.peer.name)}</span>${st}<button type="button" class="tchat-mini" id="tchat-room-del" data-del-room="${roomInfo.id}" style="margin-left:auto" title="${esc(T("chat.btn.delete_room_title", "Удалить чат"))}">${esc(T("chat.btn.delete_room", "🗑 Удалить"))}</button>`;
      const back = head.querySelector("#tchat-room-back");
      if (back) back.addEventListener("click", () => setView("rooms"));
      const del = head.querySelector("#tchat-room-del");
      if (del) del.addEventListener("click", async () => {
        const id = parseInt(del.getAttribute("data-del-room"), 10);
        if (!id) return;
        if (!confirm(T("chat.confirm.delete_room_short", "Удалить личный чат?"))) return;
        try { await jget(DM_API + "/" + id, { method: "DELETE", credentials: "same-origin" }); setView("rooms"); refreshRooms(); } catch (e) { setHint(e.message || T("chat.hint.error", "Ошибка"), true); }
      });
    }

    let lastOnlineIds = [];
    async function pollBadges() {
      try {
        const [n, o] = await Promise.all([
          me ? fetch(DM_API + "/notify", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null).catch(() => null) : null,
          fetch(API + "/online", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null).catch(() => null),
        ]);
        if (n && typeof n.unread === "number") { dmCounters.unread = n.unread | 0; dmCounters.invites = (n.invites | 0); }
        setBadge();
        if (o && typeof o.count === "number") { setOnline(o.count); lastOnlineIds = (o.ids && Array.isArray(o.ids)) ? o.ids : lastOnlineIds; }
      } catch {}
    }
    async function pingPresence() {
      if (document.hidden || !me) return;
      try { const j = await fetch(API + "/ping", { method: "POST", credentials: "same-origin" }).then(r => r.ok ? r.json() : null); if (j && typeof j.online === "number") setOnline(Math.max(0, j.online - 1)); } catch {}
    }

    async function doSend() {
      const txt = (input.value || "").trim(); if (!txt) return;
      const isSup = view === "support";
      const limit = isSup ? 2000 : 500;
      if (txt.length > limit) { setHint(T("chat.hint.too_long", "Слишком длинно — до {n} символов", { n: num(limit) }), true); return; }
      if (!isSup && !me) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return; }
      if (isSup && !me) {
        const name = (supNameInput ? supNameInput.value.trim() : "") || getGuestName();
        if (!name) { setHint(T("chat.hint.need_name", "Введите временный ник для поддержки"), true); if (supNameInput) supNameInput.focus(); return; }
      }
      input.disabled = true; sendBtn.disabled = true; setHint(T("chat.hint.sending", "Отправка…"), false);
      try {
        if (view === "support") {
          supGuestToken = getGuestToken();
          let name = "";
          if (!me && supNameInput) {
            name = (supNameInput.value || "").trim().slice(0, 40) || getGuestName();
            if (name) setGuestName(name);
          }
          const body = { text: txt, name: name, guest_token: supGuestToken };
          if (me && me.is_admin && supCurrentThreadKey) body.thread_key = supCurrentThreadKey;
          const j = await jget(SUP_API, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), });
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) { lastSupId = Math.max(lastSupId, msg.id); renderSupMsgs([msg], supListEl, me); if (j.guest_token) { supGuestToken = j.guest_token; try { localStorage.setItem(LS_SUP_GUEST, supGuestToken); } catch {} } }
          setHint(me && me.is_admin
            ? T("chat.hint.sent_support_admin", "🆘 ответ в поддержку отправлен")
            : T("chat.hint.sent_support", "🆘 поддержка • отправили (30 дней)"), false);
          if (me && me.is_admin) refreshSupThreads();
        } else if (view === "room" && roomId) {
          const j = await jget(DM_API + "/" + roomId + "/messages", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: txt }), });
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) { roomLastId = Math.max(roomLastId, msg.id); renderMsgs([msg], dmMsgsEl(), me, { bubbles: true }); }
          setHint(T("chat.hint.sent_dm", "🔒 приватно • 30 дней"), false);
        } else {
          const r = await fetch(API, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: txt }), });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.hint || j.error || "error");
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) { lastId = Math.max(lastId, msg.id); try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {} renderMsgs([msg], listEl, me, {}); }
          setHint(T("chat.hint.public_history", "Вы: {name} • 3 дня истории",
            { name: me.name || "id" + me.id }), false);
        }
      } catch (e) { setHint(e.message || T("chat.hint.error", "Ошибка"), true); } finally { input.disabled = false; sendBtn.disabled = false; input.focus(); }
    }

    function toggleOpen() { setOpen(!isOpen); }
    if (toggle) toggle.addEventListener("click", toggleOpen);
    if (headerBtn) headerBtn.addEventListener("click", toggleOpen);
    if (closeBtn) closeBtn.addEventListener("click", () => setOpen(false));
    if (sendBtn) sendBtn.addEventListener("click", doSend);
    if (input) input.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); doSend(); } });
    if (supNameInput) {
      supNameInput.addEventListener("change", () => {
        const v = (supNameInput.value || "").trim().slice(0, 40);
        if (v) setGuestName(v);
        updateAuthUI();
      });
      supNameInput.addEventListener("input", () => { updateAuthUI(); });
    }
    if (muteBtn) {
      muteBtn.addEventListener("click", () => {
        const tab = view === "rooms" || view === "room" ? "dm" : view;
        muteMap[tab] = !muteMap[tab]; saveMute(tab); updateMuteBtn();
        setHint(muteMap[tab]
          ? T("chat.hint.mute_off", "🔇 Звук выкл для «{tab}»", { tab })
          : T("chat.hint.mute_on", "🔊 Звук вкл для «{tab}»", { tab }), false);
        if (!muteMap[tab]) playSound(tab);
      });
    }
    document.querySelectorAll("#tchat-tabs .tchat-tab").forEach((b) => {
      b.addEventListener("click", () => {
        const tab = b.getAttribute("data-tab");
        if (tab === "public") setView("public");
        else if (tab === "dm") setView("rooms");
        else if (tab === "services") setView("services");
        else if (tab === "support") setView("support");
      });
    });
    if (listEl) listEl.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-del]"); if (!btn) return;
      const id = parseInt(btn.getAttribute("data-del"), 10); if (!id) return;
      if (!confirm(T("chat.confirm.delete_msg", "Удалить сообщение #{n}?", { n: id }))) return;
      try { const r = await fetch(API + "/" + id, { method: "DELETE", credentials: "same-origin" }); if (r.ok) { const el = listEl.querySelector(`[data-mid="${id}"]`); if (el) el.remove(); } } catch {}
    });
    if (supListEl) supListEl.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-del-sup]"); if (!btn) return;
      const id = parseInt(btn.getAttribute("data-del-sup"), 10); if (!id) return;
      if (!confirm(T("chat.confirm.delete_sup_msg", "Удалить сообщение поддержки #{n}?", { n: id }))) return;
      try { const r = await fetch(SUP_API + "/" + id, { method: "DELETE", credentials: "same-origin" }); if (r.ok) { const el = supListEl.querySelector(`[data-mid="${id}"]`); if (el) el.remove(); } } catch {}
    });

    if (!window._tchat_ws_hooked) {
      window._tchat_ws_hooked = true;
      document.addEventListener("liqscope:ws", (ev) => {
        const data = ev.detail; if (!data) return;
        if (data.type === "terminal_chat" && data.message) {
          if (!me) return;
          const m = data.message;
          if (m.id > lastId) { lastId = m.id; try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {} }
          if (view === "public") renderMsgs([m], listEl, me, {}); else { publicUnread++; playSound("public"); setBadge(); }
        } else if (data.type === "terminal_chat_del" && data.id) {
          const el = listEl && listEl.querySelector(`[data-mid="${data.id}"]`); if (el) el.remove();
        } else if (data.type === "service_chat" && data.message) {
          if (!me) return;
          const m = data.message;
          if (m.id > lastSvcId) lastSvcId = m.id;
          if (view === "services") renderSvcMsgs([m], svcListEl); else { svcUnread++; playSound("services"); setBadge(); }
        } else if (data.type === "support_chat" && data.message) {
          const m = data.message;
          const tk = data.thread_key || m.thread_key || "";
          if (!me) {
            if (m.guest_token && m.guest_token !== supGuestToken) return;
          } else if (!me.is_admin) {
            if (m.user_id && m.user_id !== me.id && !m.admin) {
              if (tk !== `u:${me.id}`) return;
            }
            if (tk && tk !== `u:${me.id}` && !tk.startsWith(`u:${me.id}`)) {
              if (tk !== `u:${me.id}`) {
                if (m.user_id !== me.id && !m.admin) return;
              }
            }
          } else {
            if (supCurrentThreadKey && tk && tk !== supCurrentThreadKey) {
              supUnread++; playSound("support"); setBadge(); refreshSupThreads();
              return;
            }
          }
          if (m.id > lastSupId) lastSupId = m.id;
          if (view === "support") renderSupMsgs([m], supListEl, me); else { supUnread++; playSound("support"); setBadge(); }
          if (me && me.is_admin) refreshSupThreads();
        } else if (data.type === "support_chat_del" && data.id) {
          const el = supListEl && supListEl.querySelector(`[data-mid="${data.id}"]`); if (el) el.remove();
        } else if (data.type === "support_thread_del" && data.thread_key) {
          if (supCurrentThreadKey === data.thread_key) {
            supCurrentThreadKey = "";
            lastSupId = 0;
            if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">' +
              T("chat.empty.thread_deleted_admin", "Тред удалён админом") + '</div>';
          }
          refreshSupThreads();
        } else if (data.type === "chat_dm" && data.message) {
          if (!me) return;
          if (view === "room" && data.room_id === roomId) {
            if (data.message.id > roomLastId) refreshRoom(false);
            dmCounters.unread = 0; setBadge(); playSound("dm");
          } else {
            pollBadges(); if (!isOpen || view !== "rooms") playSound("dm");
            if (view === "rooms") refreshRooms(); else { dmCounters.unread = (dmCounters.unread || 0) + 1; setBadge(); }
          }
        } else if (data.type === "chat_dm_room") {
          if (!me) return;
          if (data.event === "invite") { setHint(T("chat.hint.dm_invite_notice", "💌 Вам пришли в личные сообщения — вкладка «Личные»"), false); playSound("dm"); }
          else if (data.event === "accepted") setHint(T("chat.hint.dm_accepted", "✉ Приглашение принято — можно писать"), false);
          else if (data.event === "deleted") { if (roomId === data.room_id) setView("rooms"); }
          refreshRooms(); pollBadges();
        }
      });
    }

    setOpen(isOpen);
    try {
      const q = new URLSearchParams(location.search);
      if (q.get("chat") === "1") {
        setOpen(true);
        const r = parseInt(q.get("room") || "0", 10);
        const tab = q.get("tab") || "";
        if (tab === "services") setView("services");
        else if (tab === "support") setView("support");
        else setView(r ? "rooms" : "public");
        if (r) {
          const t = setInterval(() => { if (me) { clearInterval(t); setView("room", r); history.replaceState(null, "", location.pathname); } }, 250);
        }
      }
    } catch {}
    bindNameClicks(root);
    if (svcListEl) bindNameClicks(svcListEl);
    if (supListEl) bindNameClicks(supListEl);
    loadInitial().then(() => { if (view === "rooms") renderRooms(); });
    setInterval(poll, POLL_MS);
    setInterval(pollBadges, BADGE_POLL_MS);
    setInterval(pingPresence, PING_MS);
    setInterval(pollServices, SVC_POLL_MS);
    setInterval(pollSupport, SUP_POLL_MS);
    setInterval(() => { if (!isOpen) return; if (view === "room") refreshRoom(false); else if (view === "rooms") refreshRooms(); else if (me && me.is_admin && view === "support") refreshSupThreads(); }, DM_POLL_MS);
    pollBadges(); pingPresence();

    window.TerminalChat = {
      onWsMessage: (msg) => {
        if (!msg) return;
        if (msg.type === "terminal_chat" || msg.type === "terminal_chat_del" || msg.type === "service_chat" || msg.type === "support_chat" || msg.type === "support_chat_del" || msg.type === "support_thread_del" || msg.type === "chat_dm" || msg.type === "chat_dm_room") {
          document.dispatchEvent(new CustomEvent("liqscope:ws", { detail: msg }));
        }
      },
      open: (opts) => {
        setOpen(true);
        if (opts && opts.tab) {
          if (opts.tab === "services") setView("services");
          else if (opts.tab === "support") setView("support");
        }
        if (opts && opts.room) setView("room", opts.room);
      },
    };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
