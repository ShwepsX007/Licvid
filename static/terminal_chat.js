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
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="tchat-empty">' + (o.emptyHtml ||
        'Пока тихо — напишите первым! 💬<br><small>История хранится 3 дня</small>') + '</div>';
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
        `<button class="tchat-name tchat-namelink" type="button" data-uid="${m.user_id || 0}" data-uname="${esc(m.name || "")}">${name}${m.admin ? " 👑" : ""}</button>`;
      div.innerHTML = `<div class="tchat-meta">${nameHtml}<span class="tchat-time">${time}</span>${canDel ? `<button data-del="${m.id}" title="Удалить" style="margin-left:auto;background:transparent;border:0;color:#6b7da0;cursor:pointer">✕</button>` : ""}</div><div class="tchat-text">${text}</div>`;
      container.appendChild(div);
    }
    stickToBottom(container);
  }

  function renderSvcMsgs(list, container) {
    if (!container) return;
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="tchat-empty">Пока нет личных сигналов.<br><small>Сюда приходят только ваши сигналы по настройкам кабинета: стакан, пампы, алерты, корреляции. 30 дней истории.</small></div>';
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
      const text = esc(m.text);
      const meta = m.meta || {};
      const sym = meta.symbol ? esc(meta.symbol) : "";
      const kindLabel = { book: "📖 СТАКАН", pump: "💥 ПАМП", alert: "🔔 АЛЕРТ", corr: "🔗 КОРР" }[kind] || esc(kind.toUpperCase());
      div.innerHTML = `<div class="tchat-meta"><span class="tchat-svc-kind">${kindLabel}${sym ? " · " + sym : ""}</span><span class="tchat-time">${time}</span></div><div class="tchat-text">${text}</div>`;
      container.appendChild(div);
    }
    stickToBottom(container);
  }

  function renderSupMsgs(list, container, me) {
    if (!container) return;
    if (!list.length && !container.children.length) {
      const isGuest = !me;
      container.innerHTML = '<div class="tchat-empty">' + (isGuest ?
        'Напишите нам — отвечаем быстро 🆘<br><small>Вы как гость, введите временный ник ниже. Админ ответит в этот же тред. 30 дней истории.</small>' :
        'Напишите в поддержку — отвечаем быстро 🆘<br><small>Ваш личный чат с поддержкой, 30 дней истории</small>') + '</div>';
      return;
    }
    const empty = container.querySelector(".tchat-empty");
    if (empty && list.length) empty.remove();
    for (const m of list) {
      if (container.querySelector(`[data-mid="${m.id}"]`)) continue;
      const div = document.createElement("div");
      const mine = !!(me && me.id && m.user_id === me.id);
      const isAdminMsg = !!m.admin;
      div.className = "tchat-msg" + (isAdminMsg ? " admin" : "") + (mine ? " mine" : "") + (m.guest ? " guest" : "");
      div.dataset.mid = m.id;
      const name = esc(m.name || (m.guest ? "Гость" : "anon"));
      const time = fmtTime(m.ts);
      const text = esc(m.text);
      const canDel = me && me.is_admin;
      const adminBadge = m.admin ? " 👑 админ" : (m.guest ? " · гость" : "");
      const nameHtml = `<span class="tchat-name">${name}${adminBadge}</span>`;
      div.innerHTML = `<div class="tchat-meta">${nameHtml}<span class="tchat-time">${time}</span>${canDel ? `<button data-del-sup="${m.id}" title="Удалить" style="margin-left:auto;background:transparent;border:0;color:#6b7da0;cursor:pointer">✕</button>` : ""}</div><div class="tchat-text">${text}</div>`;
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
        btn.id = "tchat-header-btn"; btn.className = "tchat-header-btn"; btn.type = "button"; btn.title = "Чат";
        btn.innerHTML = '💬 Чат <span id="tchat-online" class="tchat-online" title="онлайн в чате">0</span> <span id="tchat-unread-hdr" class="tchat-unread hidden">0</span>';
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
      onlineEl.title = n > 0 ? n + " онлайн в чате" : "никто не онлайн";
    }
    function updateMuteBtn() {
      if (!muteBtn) return;
      const tab = view === "rooms" || view === "room" ? "dm" : view;
      const muted = !!muteMap[tab];
      muteBtn.textContent = muted ? "🔇" : "🔊";
      muteBtn.classList.toggle("muted", muted);
      muteBtn.title = muted ? "Звук выкл — нажать чтобы включить" : "Звук вкл — нажать чтобы выключить";
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
        if (dmMsgsEl()) dmMsgsEl().innerHTML = '<div class="tchat-empty">загружаем…</div>';
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
      menuEl.innerHTML = `<div class="tchat-usermenu-name">${esc(uname || "user")}</div>` +
        `<button type="button" data-act="reply">↩ Ответить</button>` +
        (self ? "" : `<button type="button" data-act="dm">✉ Написать лично</button>`);
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
      if (!me) { setHint("Войдите чтобы писать личные", true); return; }
      try {
        const j = await jget(DM_API + "/invite", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ peer_id: peerId }),
        });
        if (j && j.room) {
          setHint("✉ Личный диалог с " + (peerName || j.room.peer.name) + (j.room.status === "pending" ? " — приглашение отправлено" : ""), false);
          setView("room", j.room.id);
        }
      } catch (e) { setHint(e.message === "error" ? "Не удалось открыть диалог" : (e.message || "Ошибка"), true); }
    }

    function setHint(t, err) {
      if (!hint) return; hint.textContent = t || ""; hint.classList.toggle("error", !!err);
    }

    function updateAuthUI() {
      if (!input || !sendBtn) return;
      if (view === "services") {
        if (!me) { input.disabled = true; sendBtn.disabled = true; setHint("🔔 Только для зарегистрированных — войдите чтобы видеть личные сигналы", true); return; }
        input.disabled = true; sendBtn.disabled = true;
        setHint("🔔 Только чтение — ваши личные сигналы по настройкам кабинета (30 дней)", false);
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
          input.placeholder = hasName ? "Сообщение в поддержку… (Enter)" : "Сначала введите временный ник ниже…";
          setHint(hasName ? "🆘 Поддержка — ваш личный тред (гость, 30 дней)" : "🆘 Введите временный ник чтобы писать — вас найдут по нему", false);
        } else {
          if (supNameWrap) supNameWrap.classList.add("hidden");
          if (me.is_admin && supCurrentThreadKey) {
            input.placeholder = `Ответ в тред ${supCurrentThreadKey}… (Enter)`;
            setHint(`Вы админ • отвечаете в ${supCurrentThreadKey} • клик по треду снова — свернуть`, false);
          } else if (me.is_admin) {
            input.placeholder = "Выберите тред выше или ждите сообщений…";
            setHint("Вы админ • выберите тред для ответа, новые сообщения приходят в бот и с задержкой", false);
          } else {
            input.placeholder = "Написать в поддержку… (Enter)";
            setHint(`Вы: ${me.name || "id" + me.id} • личный чат поддержки 30 дней`, false);
          }
        }
        return;
      }
      if (view === "rooms") {
        input.disabled = true; sendBtn.disabled = true;
        setHint("Выберите диалог слева или пригласите пользователя", false);
        if (supNameWrap) supNameWrap.classList.add("hidden");
        return;
      }
      if (view === "room" && roomInfo && roomInfo.status === "pending") {
        input.placeholder = "Ответ = принять приглашение… (Enter)";
      } else if (view === "room") {
        input.placeholder = "Личное сообщение… (Enter) — 30 дней";
      } else {
        input.placeholder = "Сообщение… (Enter)";
      }
      if (supNameWrap) supNameWrap.classList.add("hidden");
      if (!me) {
        input.placeholder = "Только поддержка доступна гостям — войдите для остальных чатов";
        input.disabled = true; sendBtn.disabled = true;
        hint.innerHTML = 'Гостям только поддержка — <a href="/login?next=' + encodeURIComponent(location.pathname) + '" style="color:#8ab4ff">войти</a> для общего и личных';
        return;
      }
      input.disabled = false; sendBtn.disabled = false;
      if (view === "public") setHint(`Вы: ${me.name || "id" + me.id} • 3 дня истории`, false);
      else if (roomInfo) {
        const incomingPending = roomInfo.status === "pending" && roomInfo.invited_by !== me.id;
        setHint(incomingPending ? "💌 Входящее приглашение — ответ = принять диалог" : `🔒 Личный диалог с ${roomInfo.peer.name} • 30 дней • можно удалить`, false);
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
        if (listEl) listEl.innerHTML = '<div class="tchat-empty">Гостям доступен только таб «Поддержка» — войдите чтобы видеть общий чат.<br><small>Поддержка 30 дней, личный тред</small></div>';
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
      const header = `<div class="tchat-threads-head" id="tchat-sup-threads-toggle" style="display:flex;align-items:center;justify-content:space-between;padding:6px 8px;border-bottom:1px solid #1e2e55;background:#0e182d;cursor:pointer"><span>💬 Треды поддержки (${supThreads.length})${cur ? ` · <code style="font-size:11px">${esc(cur)}</code>` : ""}</span><button type="button" class="tchat-mini">${collapsed ? "▼ Развернуть" : "▲ Свернуть"}</button></div>`;
      if (!supThreads.length) {
        supThreadsEl.innerHTML = header + '<div class="tchat-empty muted" style="padding:8px">Нет обращений в поддержку пока</div>';
        const h = supThreadsEl.querySelector("#tchat-sup-threads-toggle");
        if (h) h.addEventListener("click", () => { supThreadsCollapsed = !supThreadsCollapsed; try { localStorage.setItem(LS_SUP_THREADS_COLLAPSED, supThreadsCollapsed ? "1" : "0"); } catch {}; renderSupThreads(); });
        return;
      }
      let body = "";
      if (!collapsed) {
        body = supThreads.map((t) => {
          const tk = esc(t.thread_key || "");
          const last = esc((t.last_text || "").slice(0, 60));
          const name = esc(t.display_name || (t.user_id ? "id" + t.user_id : "гость"));
          const total = t.total || 0;
          const isCur = tk === cur;
          return `<div class="tchat-dm-item${isCur ? " active" : ""}" data-tkey="${tk}" style="cursor:pointer">
            <div class="tchat-peer">${name} <span class="tchat-time" style="margin-left:6px">${fmtTime(t.last_at || 0)}</span><button type="button" class="tchat-mini" data-del-thread="${tk}" style="margin-left:auto" title="Удалить весь тред">🗑</button></div>
            <div class="tchat-last">${last || "—"}</div>
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
            if (!confirm("Удалить весь тред поддержки " + tk + "? Все сообщения удалятся.")) return;
            try {
              await jget(SUP_API + "/thread/" + encodeURIComponent(tk), { method: "DELETE", credentials: "same-origin" });
              if (supCurrentThreadKey === tk) {
                supCurrentThreadKey = "";
                lastSupId = 0;
                if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">Тред удалён</div>';
              }
              refreshSupThreads();
              updateAuthUI();
              setHint("🗑 Тред " + tk + " удалён", false);
            } catch (e) {
              setHint(e.message || "Не удалось удалить тред", true);
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
              if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">Выберите тред выше для просмотра — тред свернут</div>';
              updateAuthUI();
              renderSupThreads();
              return;
            }
            supCurrentThreadKey = tk;
            lastSupId = 0;
            if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">загружаем…</div>';
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
      const top = '<div class="tchat-dm-top"><button type="button" class="tchat-mini" id="tchat-invite-btn">＋ Пригласить</button><div class="tchat-invite-form hidden" id="tchat-invite-form"><input id="tchat-invite-q" maxlength="64" placeholder="ник или @username"><button type="button" class="tchat-mini" id="tchat-invite-go">Найти</button></div></div>';
      if (!rooms.length) {
        box.innerHTML = top + '<div class="tchat-empty">Личных диалогов пока нет.<br><small>Нажмите на ник в общем чате → «✉ Написать лично», либо «＋ Пригласить». 30 дней истории, можно удалить.</small></div>';
        bindInviteUi(box); return;
      }
      const online = new Set(lastOnlineIds);
      const items = rooms.map((r) => {
        const incoming = r.status === "pending" && r.invited_by !== (me && me.id);
        const outgoing = r.status === "pending" && r.invited_by === (me && me.id);
        const last = r.last ? `<div class="tchat-last">${(r.last.user_id === (me && me.id) ? "Вы: " : "")}${esc((r.last.text || "").slice(0, 60))}</div>` : '<div class="tchat-last muted">— диалог начнётся с первого сообщения —</div>';
        const state = incoming ? '<span class="tchat-dm-flag inv">приглашение</span>' : outgoing ? '<span class="tchat-dm-flag">ждём ответа</span>' : "";
        const act = incoming ? `<div class="tchat-dm-actions"><button type="button" class="tchat-mini ok" data-accept="${r.id}">✓ Принять</button><button type="button" class="tchat-mini no" data-decline="${r.id}">✕ Отклонить</button></div>` : "";
        return `<div class="tchat-dm-item${r.unread ? " unread" : ""}" data-room="${r.id}">
          <div class="tchat-peer"><span class="tchat-peer-dot ${online.has(r.peer.id) ? "on" : ""}"></span>${esc(r.peer.name)} ${state}<button type="button" class="tchat-mini" data-del-room="${r.id}" style="margin-left:auto" title="Удалить чат">🗑</button></div>
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
          } catch (e) { setHint(e.message || "Ошибка", true); }
          refreshRooms(); pollBadges();
        });
      });
      box.querySelectorAll("[data-del-room]").forEach((b) => {
        b.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const id = parseInt(b.getAttribute("data-del-room"), 10);
          if (!id) return;
          if (!confirm("Удалить личный чат #" + id + "? История удалится у обоих.")) return;
          try {
            await jget(DM_API + "/" + id, { method: "DELETE", credentials: "same-origin" });
            if (roomId === id) { setView("rooms"); }
            refreshRooms(); pollBadges();
            setHint("🗑 Чат удалён", false);
          } catch (e) { setHint(e.message || "Не удалось удалить", true); }
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
      if (!users.length) { res.innerHTML = '<div class="tchat-empty muted">Никого не нашли — пользователь должен быть зарегистрирован.</div>'; return; }
      const online = new Set(lastOnlineIds);
      res.innerHTML = users.map((u) => `<div class="tchat-inv-item" data-uid="${u.id}" data-uname="${esc(u.name)}"><span class="tchat-peer-dot ${online.has(u.id) ? "on" : ""}"></span><span class="tchat-inv-name">${esc(u.name)}${u.admin ? " 👑" : ""}</span><button type="button" class="tchat-mini">✉ Пригласить</button></div>`).join("");
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
          renderMsgs(msgs, box, me, { bubbles: true, emptyHtml: "Диалог пуст — напишите первым 🔒<br><small>Приватно • 30 дней • можно удалить</small>" });
        }
        paintRoomHead(); updateAuthUI(); pollBadges();
      } catch (e) {
        if (e.status === 403 && e.body && e.body.error === "declined") {
          if (dmMsgsEl()) dmMsgsEl().innerHTML = '<div class="tchat-empty">Приглашение отклонено.</div>';
        }
      }
    }
    function paintRoomHead() {
      const head = dmHeadEl(); if (!head) return;
      if (!roomInfo) { head.innerHTML = '<button type="button" class="tchat-mini" id="tchat-room-back">← Диалоги</button>'; return; }
      const online = new Set(lastOnlineIds);
      const st = roomInfo.status === "pending" ? (roomInfo.invited_by === (me && me.id) ? '<span class="tchat-dm-flag">приглашение ждёт ответа</span>' : '<span class="tchat-dm-flag inv">вы можете принять или ответить</span>') : "";
      head.innerHTML = `<button type="button" class="tchat-mini" id="tchat-room-back">←</button><span class="tchat-peer tchat-peer-head"><span class="tchat-peer-dot ${online.has(roomInfo.peer.id) ? "on" : ""}"></span>${esc(roomInfo.peer.name)}</span>${st}<button type="button" class="tchat-mini" id="tchat-room-del" data-del-room="${roomInfo.id}" style="margin-left:auto" title="Удалить чат">🗑 Удалить</button>`;
      const back = head.querySelector("#tchat-room-back");
      if (back) back.addEventListener("click", () => setView("rooms"));
      const del = head.querySelector("#tchat-room-del");
      if (del) del.addEventListener("click", async () => {
        const id = parseInt(del.getAttribute("data-del-room"), 10);
        if (!id) return;
        if (!confirm("Удалить личный чат?")) return;
        try { await jget(DM_API + "/" + id, { method: "DELETE", credentials: "same-origin" }); setView("rooms"); refreshRooms(); } catch (e) { setHint(e.message || "Ошибка", true); }
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
      if (txt.length > limit) { setHint(`Слишком длинно — до ${limit} символов`, true); return; }
      if (!isSup && !me) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return; }
      if (isSup && !me) {
        const name = (supNameInput ? supNameInput.value.trim() : "") || getGuestName();
        if (!name) { setHint("Введите временный ник для поддержки", true); if (supNameInput) supNameInput.focus(); return; }
      }
      input.disabled = true; sendBtn.disabled = true; setHint("Отправка…", false);
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
          setHint(me && me.is_admin ? "🆘 ответ в поддержку отправлен" : "🆘 поддержка • отправили (30 дней)", false);
          if (me && me.is_admin) refreshSupThreads();
        } else if (view === "room" && roomId) {
          const j = await jget(DM_API + "/" + roomId + "/messages", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: txt }), });
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) { roomLastId = Math.max(roomLastId, msg.id); renderMsgs([msg], dmMsgsEl(), me, { bubbles: true }); }
          setHint("🔒 приватно • 30 дней", false);
        } else {
          const r = await fetch(API, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: txt }), });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.hint || j.error || "error");
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) { lastId = Math.max(lastId, msg.id); try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {} renderMsgs([msg], listEl, me, {}); }
          setHint(`Вы: ${me.name || "id" + me.id} • 3 дня истории`, false);
        }
      } catch (e) { setHint(e.message || "Ошибка", true); } finally { input.disabled = false; sendBtn.disabled = false; input.focus(); }
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
        setHint(muteMap[tab] ? `🔇 Звук выкл для «${tab}»` : `🔊 Звук вкл для «${tab}»`, false);
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
      if (!confirm("Удалить сообщение #" + id + "?")) return;
      try { const r = await fetch(API + "/" + id, { method: "DELETE", credentials: "same-origin" }); if (r.ok) { const el = listEl.querySelector(`[data-mid="${id}"]`); if (el) el.remove(); } } catch {}
    });
    if (supListEl) supListEl.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-del-sup]"); if (!btn) return;
      const id = parseInt(btn.getAttribute("data-del-sup"), 10); if (!id) return;
      if (!confirm("Удалить сообщение поддержки #" + id + "?")) return;
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
            if (supListEl) supListEl.innerHTML = '<div class="tchat-empty">Тред удалён админом</div>';
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
          if (data.event === "invite") { setHint("💌 Вам пришли в личные сообщения — вкладка «Личные»", false); playSound("dm"); }
          else if (data.event === "accepted") setHint("✉ Приглашение принято — можно писать", false);
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
