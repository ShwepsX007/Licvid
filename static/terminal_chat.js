/* 💬 Чат LiqScope — общий + личные + сервисы + поддержка.
 *
 * Виджет живёт в /terminal и в кабинете:
 *   • панель двигается за шапку и тянется за края (размер помнится);
 *   • «Общий» — 3 дня истории, пишут зарегистрированные;
 *   • «Личные» — приватный диалог по приглашению, 3 дня;
 *   • «Сервисы» — все сигналы, что уходят в Telegram-бота (alerts, corr, pump, book);
 *     пишет только сервер, читают все;
 *   • «Поддержка» — пишут все, включая гостей (перенесено из кабинета);
 *   • звук на каждой вкладке, можно отключить отдельно (LS liqscope.tchat.mute.<tab>);
 *   • клик по нику — ответить / написать лично.
 */
(function () {
  const API = "/api/terminal/chat";
  const DM_API = "/api/chat/dm";
  const SVC_API = "/api/chat/services";
  const SUP_API = "/api/chat/support";
  const POLL_MS = 3500;
  const DM_POLL_MS = 5000;
  const SVC_POLL_MS = 8000;
  const SUP_POLL_MS = 6000;
  const BADGE_POLL_MS = 10000;
  const PING_MS = 30000;
  const LS_OPEN = "liqscope.tchat.open";
  const LS_LAST_ID = "liqscope.tchat.lastId";
  const LS_POS = "liqscope.tchat.pos";
  const LS_MUTE_PFX = "liqscope.tchat.mute.";
  const LS_SUP_NAME = "liqscope.tchat.supName";
  const LS_SUP_GUEST = "liqscope.tchat.supGuest";
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
    root.style.left = "";
    root.style.top = "";
    root.style.right = "";
    root.style.bottom = "";
    panel.style.width = "";
    panel.style.height = "";
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
      div.className = "tchat-msg" + (m.admin ? " admin" : "") +
        (o.bubbles ? (mine ? " mine" : " peer") : "");
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
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 120;
    if (nearBottom) container.scrollTop = container.scrollHeight;
  }

  function renderSvcMsgs(list, container) {
    if (!container) return;
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="tchat-empty">Пока нет сигналов от сервисов.<br><small>Сюда приходят все алерты, которые уходят в Telegram</small></div>';
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
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 120;
    if (nearBottom) container.scrollTop = container.scrollHeight;
  }

  function renderSupMsgs(list, container, me) {
    if (!container) return;
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="tchat-empty">Напишите нам — отвечаем быстро 🆘<br><small>Могут писать все, включая гостей</small></div>';
      return;
    }
    const empty = container.querySelector(".tchat-empty");
    if (empty && list.length) empty.remove();
    for (const m of list) {
      if (container.querySelector(`[data-mid="${m.id}"]`)) continue;
      const div = document.createElement("div");
      const mine = !!(me && me.id && m.user_id === me.id);
      div.className = "tchat-msg" + (m.admin ? " admin" : "") + (mine ? " mine" : "") + (m.guest ? " guest" : "");
      div.dataset.mid = m.id;
      const name = esc(m.name || (m.guest ? "Гость" : "anon"));
      const time = fmtTime(m.ts);
      const text = esc(m.text);
      const canDel = me && me.is_admin;
      const adminBadge = m.admin ? " 👑" : (m.guest ? " · гость" : "");
      const nameHtml = `<span class="tchat-name">${name}${adminBadge}</span>`;
      div.innerHTML = `<div class="tchat-meta">${nameHtml}<span class="tchat-time">${time}</span>${canDel ? `<button data-del-sup="${m.id}" title="Удалить" style="margin-left:auto;background:transparent;border:0;color:#6b7da0;cursor:pointer">✕</button>` : ""}</div><div class="tchat-text">${text}</div>`;
      container.appendChild(div);
    }
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 120;
    if (nearBottom) container.scrollTop = container.scrollHeight;
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
    const muteBtn = $("#tchat-mute");

    // на страницах без кнопки в шапке — показываем плавающую кнопку 💬
    const isTerminalPage = !!document.getElementById("tv-chart-container");
    if (toggle && !headerBtn) {
      toggle.classList.remove("hidden");
      toggle.removeAttribute("aria-hidden");
      toggle.removeAttribute("tabindex");
      // на всех страницах кроме терминала — чат внизу справа как виджет поддержки
      if (!isTerminalPage) {
        root.style.top = "auto";
        root.style.bottom = "18px";
        root.style.right = "18px";
        root.style.left = "auto";
      }
    }
    // если это не терминал — панель тоже внизу справа по умолчанию
    if (!isTerminalPage && !loadPos()) {
      root.style.top = "auto";
      root.style.bottom = "18px";
      root.style.right = "18px";
      root.style.left = "auto";
    }
    // если есть шапка-хидер — вставляем туда кнопку чата, если её нет
    (function ensureHeaderBtn(){
      if (headerBtn) return;
      try {
        const nav = document.querySelector(".site-nav, .header-controls, .top-nav, header");
        if (!nav) return;
        const btn = document.createElement("button");
        btn.id = "tchat-header-btn";
        btn.className = "tchat-header-btn";
        btn.type = "button";
        btn.title = "Чат";
        btn.innerHTML = '💬 Чат <span id="tchat-online" class="tchat-online" title="онлайн в чате">0</span> <span id="tchat-unread-hdr" class="tchat-unread hidden">0</span>';
        // вставим в конец nav
        nav.appendChild(btn);
      } catch {}
    })();
    // обновим ссылку после возможного инжекта
    headerBtn = $("#tchat-header-btn") || headerBtn;
    onlineEl = $("#tchat-online") || onlineEl;



    let me = null;
    let lastId = 0;
    try { lastId = parseInt(localStorage.getItem(LS_LAST_ID) || "0", 10) || 0; } catch { }
    let lastSvcId = 0;
    let lastSupId = 0;
    let publicUnread = 0;
    let svcUnread = 0;
    let supUnread = 0;
    let isOpen = false;
    try { isOpen = localStorage.getItem(LS_OPEN) === "1"; } catch { }

    // mute per tab
    const muteMap = { public: false, dm: false, services: false, support: false };
    function loadMute() {
      for (const k of Object.keys(muteMap)) {
        try {
          const v = localStorage.getItem(LS_MUTE_PFX + k);
          muteMap[k] = v === "1";
        } catch { }
      }
    }
    loadMute();
    function saveMute(tab) {
      try { localStorage.setItem(LS_MUTE_PFX + tab, muteMap[tab] ? "1" : "0"); } catch { }
    }

    // sound via WebAudio
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
      // don't beep if page hidden? still beep if not muted, user asked
      try {
        const ctx = ensureAudio();
        if (!ctx) return;
        if (ctx.state === "suspended") ctx.resume().catch(() => { });
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        // different pitch per tab
        const freqs = { public: 880, dm: 1200, services: 660, support: 520 };
        o.frequency.value = freqs[t] || 800;
        o.type = "sine";
        const now = ctx.currentTime;
        g.gain.setValueAtTime(0, now);
        g.gain.linearRampToValueAtTime(0.18, now + 0.02);
        g.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
        o.start(now);
        o.stop(now + 0.38);
      } catch { }
    }

    // ------- view: public | rooms | room | services | support ---------------
    let view = "public";
    let rooms = [];
    let roomId = 0;
    let roomLastId = 0;
    let roomInfo = null;
    let dmCounters = { unread: 0, invites: 0 };
    let dmTouchedAt = 0;

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

    function setView(v, arg) {
      view = v;
      dmTouchedAt = Date.now();
      const list = listEl, dm = dmViewEl(), chat = dmChatEl(), svc = svcListEl, sup = supWrap;
      if (list) list.classList.toggle("hidden", v !== "public");
      if (dm) dm.classList.toggle("hidden", v !== "rooms");
      if (chat) chat.classList.toggle("hidden", v !== "room");
      if (svc) svc.classList.toggle("hidden", v !== "services");
      if (sup) sup.classList.toggle("hidden", v !== "support");
      document.querySelectorAll("#tchat-tabs .tchat-tab").forEach((b) => {
        const tab = b.getAttribute("data-tab");
        const isActive = (tab === "public" && v === "public") ||
          (tab === "dm" && (v === "rooms" || v === "room")) ||
          (tab === tab && v === tab);
        // second condition for services/support: exact match
        if (tab === "services") b.classList.toggle("active", v === "services");
        else if (tab === "support") b.classList.toggle("active", v === "support");
        else if (tab === "public") b.classList.toggle("active", v === "public");
        else if (tab === "dm") b.classList.toggle("active", v === "rooms" || v === "room");
      });
      if (v === "public") { publicUnread = 0; setBadge(); }
      if (v === "services") { svcUnread = 0; setBadge(); if (svcListEl) svcListEl.scrollTop = svcListEl.scrollHeight; }
      if (v === "support") { supUnread = 0; setBadge(); if (supListEl) supListEl.scrollTop = supListEl.scrollHeight; }
      if (v === "room" && arg) {
        roomId = arg; roomInfo = null;
        roomLastId = 0;
        if (dmMsgsEl()) dmMsgsEl().innerHTML = '<div class="tchat-empty">загружаем…</div>';
        paintRoomHead();
        refreshRoom(true);
      }
      if (v === "rooms") refreshRooms();
      if (v === "services") refreshServices(true);
      if (v === "support") refreshSupport(true);
      updateAuthUI();
      updateMuteBtn();
    }

    function setOpen(v) {
      isOpen = !!v;
      root.classList.toggle("open", isOpen);
      document.body.classList.toggle("tchat-open", isOpen);
      if (headerBtn) headerBtn.classList.toggle("active", isOpen);
      if (panel) panel.classList.toggle("hidden", !isOpen);
      try { localStorage.setItem(LS_OPEN, isOpen ? "1" : "0"); } catch { }
      setBadge();
      if (isOpen) {
        if (view === "public") { publicUnread = 0; setBadge(); if (listEl) listEl.scrollTop = listEl.scrollHeight; }
        if (view === "services") { svcUnread = 0; setBadge(); }
        if (view === "support") { supUnread = 0; setBadge(); }
        if (view === "room") refreshRoom(false);
        else if (view === "rooms") refreshRooms();
        else if (view === "services") refreshServices(false);
        else if (view === "support") refreshSupport(false);
        ensureAudio();
      }
    }

    // ------- drag header -------------------------------------------------------
    (function bindDrag() {
      const head = root.querySelector(".tchat-head");
      if (!head) return;
      const saved = loadPos();
      if (saved) applyPos(root, panel, saved);

      let dragging = false;
      let startX = 0, startY = 0;
      let startLeft = 0, startTop = 0;
      let moved = false;

      function onDown(ev) {
        if (ev.target.closest && ev.target.closest("#tchat-close")) return;
        if (ev.target.closest && ev.target.closest(".tchat-mute")) return;
        if (ev.type === "mousedown" && ev.button !== 0) return;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        dragging = true;
        moved = false;
        startX = p.clientX;
        startY = p.clientY;
        const rect = root.getBoundingClientRect();
        startLeft = rect.left;
        startTop = rect.top;
        root.classList.add("tchat-dragging");
        ev.preventDefault();
      }

      function onMove(ev) {
        if (!dragging) return;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        const dx = p.clientX - startX;
        const dy = p.clientY - startY;
        if (!moved && Math.hypot(dx, dy) < 3) return;
        moved = true;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const rect = root.getBoundingClientRect();
        const w = rect.width;
        const h = rect.height;
        let nl = clamp(startLeft + dx, 4, Math.max(4, vw - w - 4));
        let nt = clamp(startTop + dy, 4, Math.max(4, vh - h - 4));
        root.style.left = nl + "px";
        root.style.top = nt + "px";
        root.style.right = "auto";
        root.style.bottom = "auto";
        root.classList.add("tchat-moved");
      }

      function onUp() {
        if (!dragging) return;
        dragging = false;
        root.classList.remove("tchat-dragging");
        if (moved) {
          const rect = root.getBoundingClientRect();
          savePos(rect.left, rect.top, panel.offsetWidth || rect.width,
            panel.offsetHeight || rect.height);
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
        if (ev.target.closest && (ev.target.closest("#tchat-close") ||
          ev.target.closest(".tchat-tab") || ev.target.closest(".tchat-mute"))) return;
        resetPos(root, panel);
      });

      window.addEventListener("resize", () => {
        const pos = loadPos();
        if (pos) applyPos(root, panel, pos);
      });
    })();

    // ------- resize handles ----------------------------------------------------
    (function bindResize() {
      if (!panel) return;
      const dirs = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
      for (const d of dirs) {
        const h = document.createElement("span");
        h.className = "tchat-rz tchat-rz-" + d;
        h.dataset.rz = d;
        root.appendChild(h);
      }
      let rzDir = "";
      let sx = 0, sy = 0;
      let sw = 0, sh = 0, sl = 0, st = 0;

      function rectStart() {
        const pr = panel.getBoundingClientRect();
        sw = pr.width; sh = pr.height;
        sl = root.getBoundingClientRect().left; st = root.getBoundingClientRect().top;
      }

      function down(ev) {
        const d = ev.target.closest && ev.target.closest(".tchat-rz");
        if (!d) return;
        if (ev.type === "mousedown" && ev.button !== 0) return;
        rzDir = d.dataset.rz;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        sx = p.clientX; sy = p.clientY;
        rectStart();
        panel.classList.add("tchat-resized");
        root.classList.add("tchat-dragging");
        ev.preventDefault();
        ev.stopPropagation();
      }

      function move(ev) {
        if (!rzDir) return;
        const p = ev.touches ? ev.touches[0] : ev;
        if (!p) return;
        const dx = p.clientX - sx;
        const dy = p.clientY - sy;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let w = sw, h = sh, l = sl, t = st;
        if (rzDir.indexOf("e") !== -1) w = sw + dx;
        if (rzDir.indexOf("s") !== -1) h = sh + dy;
        if (rzDir.indexOf("w") !== -1) { w = sw - dx; l = sl + dx; }
        if (rzDir.indexOf("n") !== -1) { h = sh - dy; t = st + dy; }
        w = clamp(w, MIN_W, vw - 8 - Math.max(0, l - 4));
        h = clamp(h, MIN_H, vh - 8 - Math.max(0, t - 4));
        if (rzDir.indexOf("w") !== -1) l = sl + sw - w;
        if (rzDir.indexOf("n") !== -1) t = st + sh - h;
        l = clamp(l, 4, Math.max(4, vw - w - 4));
        t = clamp(t, 4, Math.max(4, vh - h - 4));
        panel.style.width = Math.round(w) + "px";
        panel.style.height = Math.round(h) + "px";
        root.style.left = Math.round(l) + "px";
        root.style.top = Math.round(t) + "px";
        root.style.right = "auto";
        root.style.bottom = "auto";
        root.classList.add("tchat-moved");
      }

      function up() {
        if (!rzDir) return;
        rzDir = "";
        root.classList.remove("tchat-dragging");
        savePos(root.getBoundingClientRect().left, root.getBoundingClientRect().top,
          panel.offsetWidth, panel.offsetHeight);
      }

      root.addEventListener("mousedown", down);
      root.addEventListener("touchstart", down, { passive: false });
      window.addEventListener("mousemove", move);
      window.addEventListener("touchmove", move, { passive: false });
      window.addEventListener("mouseup", up);
      window.addEventListener("touchend", up);
      window.addEventListener("touchcancel", up);
    })();

    // ------- user menu ---------------------------------------------------------
    let menuEl = null;
    function closeMenu() {
      if (menuEl) { menuEl.remove(); menuEl = null; }
    }
    function openMenu(x, y, uid, uname) {
      closeMenu();
      menuEl = document.createElement("div");
      menuEl.className = "tchat-usermenu";
      const self = me && me.id === uid;
      menuEl.innerHTML = `<div class="tchat-usermenu-name">${esc(uname || "user")}</div>` +
        `<button type="button" data-act="reply">↩ Ответить</button>` +
        (self ? "" : `<button type="button" data-act="dm">✉ Написать лично</button>`);
      document.body.appendChild(menuEl);
      const vw = window.innerWidth;
      const rect = menuEl.getBoundingClientRect();
      menuEl.style.left = Math.min(x, vw - (rect.width || 180) - 10) + "px";
      menuEl.style.top = (y + 6) + "px";
      menuEl.addEventListener("click", async (ev) => {
        const b = ev.target.closest("[data-act]");
        if (!b) return;
        const act = b.dataset.act;
        closeMenu();
        if (act === "reply") {
          if (input) {
            input.value = "@" + (uname || "") + " " + (input.value || "");
            input.focus();
          }
        } else if (act === "dm" && uid) {
          await openDmWith(uid, uname);
        }
      });
      setTimeout(() => {
        const dismiss = (ev) => {
          if (menuEl && !menuEl.contains(ev.target)) {
            closeMenu();
            document.removeEventListener("mousedown", dismiss, true);
          }
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
        const nb = ev.target.closest(".tchat-namelink");
        if (!nb) return;
        ev.preventDefault();
        ev.stopPropagation();
        const uid = parseInt(nb.dataset.uid, 10) || 0;
        const uname = nb.dataset.uname || "";
        const r = nb.getBoundingClientRect();
        openMenu(r.left, r.bottom, uid, uname);
      });
    }

    async function openDmWith(peerId, peerName) {
      try {
        const j = await jget(DM_API + "/invite", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ peer_id: peerId }),
        });
        if (j && j.room) {
          setHint("✉ Личный диалог с " + (peerName || j.room.peer.name) +
            (j.room.status === "pending" ? " — приглашение отправлено, ждём ответа" : ""), false);
          setView("room", j.room.id);
        }
      } catch (e) {
        setHint(e.message === "error" ? "Не удалось открыть диалог" : (e.message || "Ошибка"), true);
      }
    }

    function setHint(t, err) {
      if (!hint) return;
      hint.textContent = t || "";
      hint.classList.toggle("error", !!err);
    }

    function updateAuthUI() {
      if (!input || !sendBtn) return;
      if (view === "services") {
        input.disabled = true; sendBtn.disabled = true;
        setHint("🔔 Только чтение — сигналы от сервисов сайта", false);
        if (supNameWrap) supNameWrap.classList.add("hidden");
        return;
      }
      if (view === "support") {
        // поддержка — пишут все
        input.disabled = false; sendBtn.disabled = false;
        input.placeholder = me ? "Написать в поддержку… (Enter)" : "Сообщение в поддержку… (Enter) — можно как гость";
        if (!me) {
          if (supNameWrap) supNameWrap.classList.remove("hidden");
          try {
            const saved = localStorage.getItem(LS_SUP_NAME);
            if (saved && supNameInput && !supNameInput.value) supNameInput.value = saved;
          } catch { }
          setHint("🆘 Поддержка — могут писать все, включая гостей", false);
        } else {
          if (supNameWrap) supNameWrap.classList.add("hidden");
          setHint(`Вы: ${me.name || "id" + me.id} • поддержка`, false);
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
        input.placeholder = "Личное сообщение… (Enter)";
      } else {
        input.placeholder = "Сообщение… (Enter)";
      }
      if (supNameWrap) supNameWrap.classList.add("hidden");
      if (!me) {
        input.placeholder = "Войдите, чтобы писать…";
        input.disabled = true;
        sendBtn.disabled = true;
        hint.innerHTML = 'Только для зарегистрированных — <a href="/login?next=' +
          encodeURIComponent(location.pathname) + '" style="color:#8ab4ff">войти</a>';
        return;
      }
      input.disabled = false;
      sendBtn.disabled = false;
      if (view === "public") {
        setHint(`Вы: ${me.name || "id" + me.id} • 3 дня истории`, false);
      } else if (roomInfo) {
        const incomingPending = roomInfo.status === "pending" &&
          roomInfo.invited_by !== me.id;
        setHint(incomingPending
          ? "💌 Входящее приглашение — ответ = принять диалог"
          : `🔒 Личный диалог с ${roomInfo.peer.name} • 3 дня истории`, false);
      }
    }

    // ------- public chat -------------------------------------------------------
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
      updateAuthUI();
      updateMuteBtn();
      const msgs = await fetchList(0, 100);
      if (msgs.length) {
        lastId = Math.max(lastId, ...msgs.map(m => m.id));
        try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch { }
      }
      if (listEl) {
        listEl.innerHTML = "";
        renderMsgs(msgs, listEl, me, {});
      }
      // preload services/support counts
      refreshServices(true).catch(() => { });
      refreshSupport(true).catch(() => { });
    }

    async function poll() {
      try {
        const msgs = await fetchList(lastId, 200);
        if (!msgs.length) return;
        let newOnes = 0;
        for (const m of msgs) {
          if (m.id > lastId) {
            lastId = m.id;
            newOnes++;
          }
        }
        if (newOnes) {
          try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch { }
          if (view === "public") renderMsgs(msgs, listEl, me, {});
          else publicUnread += newOnes;
          if (newOnes && view !== "public") playSound("public");
          else if (newOnes && !isOpen) playSound("public");
          setBadge();
        }
      } catch { }
    }

    // ------- services ----------------------------------------------------------
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
      try {
        const msgs = await fetchSvc(first ? 0 : lastSvcId, first ? 100 : 200);
        if (!msgs.length) return;
        let newOnes = 0;
        for (const m of msgs) {
          if (m.id > lastSvcId) { lastSvcId = m.id; newOnes++; }
        }
        if (first) {
          if (svcListEl) { svcListEl.innerHTML = ""; renderSvcMsgs(msgs, svcListEl); }
        } else {
          if (view === "services") renderSvcMsgs(msgs, svcListEl);
          else svcUnread += newOnes;
          if (newOnes) {
            if (view !== "services") playSound("services");
            setBadge();
          }
        }
        if (first && msgs.length) lastSvcId = Math.max(...msgs.map(m => m.id));
      } catch { }
    }

    async function pollServices() {
      if (document.hidden && !isOpen) { /* still poll but less */ }
      await refreshServices(false);
    }

    // ------- support -----------------------------------------------------------
    async function fetchSup(afterId, limit) {
      try {
        let url = SUP_API + "?limit=" + (limit || 100);
        if (afterId) url += "&after_id=" + afterId;
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
          if (first && supListEl && !supListEl.children.length) {
            renderSupMsgs([], supListEl, me);
          }
          return;
        }
        let newOnes = 0;
        for (const m of msgs) {
          if (m.id > lastSupId) { lastSupId = m.id; newOnes++; }
        }
        if (first) {
          if (supListEl) { supListEl.innerHTML = ""; renderSupMsgs(msgs, supListEl, me); }
          if (msgs.length) lastSupId = Math.max(...msgs.map(m => m.id));
        } else {
          if (view === "support") renderSupMsgs(msgs, supListEl, me);
          else supUnread += newOnes;
          if (newOnes) {
            if (view !== "support") playSound("support");
            setBadge();
          }
        }
      } catch { }
    }

    async function pollSupport() {
      await refreshSupport(false);
    }

    // ------- rooms -------------------------------------------------------------
    async function refreshRooms() {
      if (!me) return;
      try {
        const j = await jget(DM_API + "/rooms", { credentials: "same-origin" });
        rooms = (j && j.rooms) || [];
        renderRooms();
      } catch { }
    }

    function renderRooms() {
      const box = dmlistEl();
      if (!box) return;
      const top = '<div class="tchat-dm-top">' +
        '<button type="button" class="tchat-mini" id="tchat-invite-btn">＋ Пригласить</button>' +
        '<div class="tchat-invite-form hidden" id="tchat-invite-form">' +
        '<input id="tchat-invite-q" maxlength="64" placeholder="ник или @username">' +
        '<button type="button" class="tchat-mini" id="tchat-invite-go">Найти</button></div></div>';
      if (!rooms.length) {
        box.innerHTML = top + '<div class="tchat-empty">Личных диалогов пока нет.' +
          '<br><small>Нажмите на ник в общем чате → «✉ Написать лично», либо «＋ Пригласить».</small></div>';
        bindInviteUi(box);
        return;
      }
      const online = new Set(lastOnlineIds);
      const items = rooms.map((r) => {
        const incoming = r.status === "pending" && r.invited_by !== (me && me.id);
        const outgoing = r.status === "pending" && r.invited_by === (me && me.id);
        const last = r.last ? `<div class="tchat-last">${(r.last.user_id === (me && me.id) ? "Вы: " : "")}${esc((r.last.text || "").slice(0, 60))}</div>` : '<div class="tchat-last muted">— диалог начнётся с первого сообщения —</div>';
        const state = incoming ? '<span class="tchat-dm-flag inv">приглашение</span>'
          : outgoing ? '<span class="tchat-dm-flag">ждём ответа</span>' : "";
        const act = incoming ? `<div class="tchat-dm-actions">` +
          `<button type="button" class="tchat-mini ok" data-accept="${r.id}">✓ Принять</button>` +
          `<button type="button" class="tchat-mini no" data-decline="${r.id}">✕ Отклонить</button></div>` : "";
        return `<div class="tchat-dm-item${r.unread ? " unread" : ""}" data-room="${r.id}">
          <div class="tchat-peer"><span class="tchat-peer-dot ${online.has(r.peer.id) ? "on" : ""}"></span>${esc(r.peer.name)} ${state}</div>
          ${last}
          <div class="tchat-dm-meta"><span class="tchat-time">${fmtTime(r.updated_at)}</span>${r.unread ? `<span class="tchat-dm-unread">${r.unread}</span>` : ""}</div>
          ${act}
        </div>`;
      }).join("");
      box.innerHTML = top + items;
      bindInviteUi(box);
      box.querySelectorAll("[data-room]").forEach((el) => {
        el.addEventListener("click", (ev) => {
          if (ev.target.closest("[data-accept],[data-decline]")) return;
          setView("room", parseInt(el.getAttribute("data-room"), 10));
        });
      });
      box.querySelectorAll("[data-accept],[data-decline]").forEach((b) => {
        b.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const id = parseInt(b.getAttribute("data-accept") || b.getAttribute("data-decline"), 10);
          const accept = b.hasAttribute("data-accept");
          try {
            await jget(DM_API + "/" + id + "/accept", {
              method: "POST", credentials: "same-origin",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ accept }),
            });
          } catch (e) { setHint(e.message || "Ошибка", true); }
          refreshRooms();
          pollBadges();
        });
      });
    }

    function bindInviteUi(box) {
      const btn = box.querySelector("#tchat-invite-btn");
      const form = box.querySelector("#tchat-invite-form");
      const q = box.querySelector("#tchat-invite-q");
      const go = box.querySelector("#tchat-invite-go");
      if (btn && form) btn.addEventListener("click", () => {
        form.classList.toggle("hidden");
        if (!form.classList.contains("hidden") && q) q.focus();
      });
      if (!q) return;
      let t = null;
      const doSearch = async () => {
        const query = (q.value || "").trim();
        try {
          const j = await jget("/api/chat/users?q=" + encodeURIComponent(query),
            { credentials: "same-origin" });
          renderInviteResults(box, (j && j.users) || []);
        } catch (e) { }
      };
      q.addEventListener("input", () => {
        clearTimeout(t); t = setTimeout(doSearch, 350);
      });
      q.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); doSearch(); } });
      if (go) go.addEventListener("click", doSearch);
    }

    function renderInviteResults(box, users) {
      let res = box.querySelector(".tchat-invite-results");
      if (!res) {
        res = document.createElement("div");
        res.className = "tchat-invite-results";
        box.appendChild(res);
      }
      if (!users.length) {
        res.innerHTML = '<div class="tchat-empty muted">Никого не нашли — пользователь должен быть зарегистрирован.</div>';
        return;
      }
      const online = new Set(lastOnlineIds);
      res.innerHTML = users.map((u) => `<div class="tchat-inv-item" data-uid="${u.id}" data-uname="${esc(u.name)}">
        <span class="tchat-peer-dot ${online.has(u.id) ? "on" : ""}"></span>
        <span class="tchat-inv-name">${esc(u.name)}${u.admin ? " 👑" : ""}</span>
        <button type="button" class="tchat-mini">✉ Пригласить</button></div>`).join("");
      res.querySelectorAll(".tchat-inv-item").forEach((el) => {
        el.addEventListener("click", async () => {
          const uid = parseInt(el.getAttribute("data-uid"), 10);
          const uname = el.getAttribute("data-uname") || "";
          if (uid && uid !== (me && me.id)) await openDmWith(uid, uname);
        });
      });
    }

    async function refreshRoom(first) {
      if (view !== "room" || !roomId || !me) return;
      try {
        const j = await jget(DM_API + "/" + roomId + "/messages?after_id=" +
          (first ? 0 : roomLastId), { credentials: "same-origin" });
        roomInfo = j.room || roomInfo;
        const msgs = (j && j.messages) || [];
        for (const m of msgs) roomLastId = Math.max(roomLastId, m.id);
        const box = dmMsgsEl();
        if (box) {
          if (first) box.innerHTML = "";
          renderMsgs(msgs, box, me, {
            bubbles: true,
            emptyHtml: "Диалог пуст — напишите первым 🔒<br><small>Приватно • история 3 дня</small>",
          });
        }
        paintRoomHead();
        updateAuthUI();
        pollBadges();
      } catch (e) {
        if (e.status === 403 && e.body && e.body.error === "declined") {
          if (dmMsgsEl()) dmMsgsEl().innerHTML = '<div class="tchat-empty">Приглашение отклонено.</div>';
        }
      }
    }

    function paintRoomHead() {
      const head = dmHeadEl();
      if (!head) return;
      if (!roomInfo) { head.innerHTML = '<button type="button" class="tchat-mini" id="tchat-room-back">← Диалоги</button>'; return; }
      const online = new Set(lastOnlineIds);
      const st = roomInfo.status === "pending"
        ? (roomInfo.invited_by === (me && me.id) ? '<span class="tchat-dm-flag">приглашение ждёт ответа</span>'
          : '<span class="tchat-dm-flag inv">вы можете принять или ответить</span>')
        : "";
      head.innerHTML = `<button type="button" class="tchat-mini" id="tchat-room-back">←</button>
        <span class="tchat-peer tchat-peer-head"><span class="tchat-peer-dot ${online.has(roomInfo.peer.id) ? "on" : ""}"></span>${esc(roomInfo.peer.name)}</span>${st}`;
      const back = head.querySelector("#tchat-room-back");
      if (back) back.addEventListener("click", () => setView("rooms"));
    }

    // ------- badges / online ---------------------------------------------------
    let lastOnlineIds = [];
    async function pollBadges() {
      try {
        const [n, o] = await Promise.all([
          fetch(DM_API + "/notify", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null).catch(() => null),
          fetch(API + "/online", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null).catch(() => null),
        ]);
        if (n && typeof n.unread === "number") {
          dmCounters.unread = n.unread | 0;
          dmCounters.invites = (n.invites | 0);
        }
        setBadge();
        if (o && typeof o.count === "number") {
          setOnline(o.count);
          lastOnlineIds = (o.ids && Array.isArray(o.ids)) ? o.ids : lastOnlineIds;
        }
      } catch { }
    }

    async function pingPresence() {
      if (document.hidden || !me) return;
      try {
        const j = await fetch(API + "/ping", { method: "POST", credentials: "same-origin" }).then(r => r.ok ? r.json() : null);
        if (j && typeof j.online === "number") setOnline(Math.max(0, j.online - 1));
      } catch { }
    }

    // ------- send --------------------------------------------------------------
    async function doSend() {
      const txt = (input.value || "").trim();
      if (!txt) return;
      // support: allow guest, max 2000
      const isSup = view === "support";
      const limit = isSup ? 2000 : 500;
      if (txt.length > limit) { setHint(`Слишком длинно — до ${limit} символов`, true); return; }

      if (!isSup && !me) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return; }

      input.disabled = true;
      sendBtn.disabled = true;
      setHint("Отправка…", false);
      try {
        if (view === "support") {
          let guestToken = "";
          try { guestToken = localStorage.getItem(LS_SUP_GUEST) || ""; } catch { }
          if (!guestToken) {
            guestToken = Math.random().toString(36).slice(2, 10);
            try { localStorage.setItem(LS_SUP_GUEST, guestToken); } catch { }
          }
          let name = "";
          if (!me && supNameInput) {
            name = (supNameInput.value || "").trim().slice(0, 40);
            try { if (name) localStorage.setItem(LS_SUP_NAME, name); } catch { }
          }
          const j = await jget(SUP_API, {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: txt, name: name, guest_token: guestToken }),
          });
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) {
            lastSupId = Math.max(lastSupId, msg.id);
            renderSupMsgs([msg], supListEl, me);
          }
          setHint("🆘 поддержка • отправили", false);
        } else if (view === "room" && roomId) {
          const j = await jget(DM_API + "/" + roomId + "/messages", {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: txt }),
          });
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) {
            roomLastId = Math.max(roomLastId, msg.id);
            renderMsgs([msg], dmMsgsEl(), me, { bubbles: true });
          }
          setHint("🔒 приватно • 3 дня истории", false);
        } else {
          const r = await fetch(API, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: txt }),
          });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.hint || j.error || "error");
          input.value = "";
          const msg = j && j.message;
          if (msg && msg.id) {
            lastId = Math.max(lastId, msg.id);
            try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch { }
            renderMsgs([msg], listEl, me, {});
          }
          setHint(`Вы: ${me.name || "id" + me.id} • 3 дня истории`, false);
        }
      } catch (e) {
        setHint(e.message || "Ошибка", true);
      } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
      }
    }

    function toggleOpen() { setOpen(!isOpen); }
    if (toggle) toggle.addEventListener("click", toggleOpen);
    if (headerBtn) headerBtn.addEventListener("click", toggleOpen);
    if (closeBtn) closeBtn.addEventListener("click", () => setOpen(false));
    if (sendBtn) sendBtn.addEventListener("click", doSend);
    if (input) input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); doSend(); }
    });
    if (supNameInput) {
      supNameInput.addEventListener("change", () => {
        try { localStorage.setItem(LS_SUP_NAME, (supNameInput.value || "").trim()); } catch { }
      });
    }

    // mute toggle
    if (muteBtn) {
      muteBtn.addEventListener("click", () => {
        const tab = view === "rooms" || view === "room" ? "dm" : view;
        muteMap[tab] = !muteMap[tab];
        saveMute(tab);
        updateMuteBtn();
        setHint(muteMap[tab] ? `🔇 Звук выкл для вкладки «${tab}»` : `🔊 Звук вкл для вкладки «${tab}»`, false);
        if (!muteMap[tab]) playSound(tab);
      });
    }

    // tabs
    document.querySelectorAll("#tchat-tabs .tchat-tab").forEach((b) => {
      b.addEventListener("click", () => {
        const tab = b.getAttribute("data-tab");
        if (tab === "public") setView("public");
        else if (tab === "dm") setView("rooms");
        else if (tab === "services") setView("services");
        else if (tab === "support") setView("support");
      });
    });

    // delete handlers
    if (listEl) listEl.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-del]");
      if (!btn) return;
      const id = parseInt(btn.getAttribute("data-del"), 10);
      if (!id) return;
      if (!confirm("Удалить сообщение #" + id + "?")) return;
      try {
        const r = await fetch(API + "/" + id, { method: "DELETE", credentials: "same-origin" });
        if (r.ok) {
          const el = listEl.querySelector(`[data-mid="${id}"]`);
          if (el) el.remove();
        }
      } catch { }
    });
    if (supListEl) supListEl.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-del-sup]");
      if (!btn) return;
      const id = parseInt(btn.getAttribute("data-del-sup"), 10);
      if (!id) return;
      if (!confirm("Удалить сообщение поддержки #" + id + "?")) return;
      try {
        const r = await fetch(SUP_API + "/" + id, { method: "DELETE", credentials: "same-origin" });
        if (r.ok) {
          const el = supListEl.querySelector(`[data-mid="${id}"]`);
          if (el) el.remove();
        }
      } catch { }
    });

    // WS live updates
    if (!window._tchat_ws_hooked) {
      window._tchat_ws_hooked = true;
      document.addEventListener("liqscope:ws", (ev) => {
        const data = ev.detail;
        if (!data) return;
        if (data.type === "terminal_chat" && data.message) {
          const m = data.message;
          if (m.id > lastId) {
            lastId = m.id;
            try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch { }
          }
          if (view === "public") renderMsgs([m], listEl, me, {});
          else { publicUnread++; playSound("public"); setBadge(); }
        } else if (data.type === "terminal_chat_del" && data.id) {
          const el = listEl && listEl.querySelector(`[data-mid="${data.id}"]`);
          if (el) el.remove();
        } else if (data.type === "service_chat" && data.message) {
          const m = data.message;
          if (m.id > lastSvcId) lastSvcId = m.id;
          if (view === "services") renderSvcMsgs([m], svcListEl);
          else { svcUnread++; playSound("services"); setBadge(); }
        } else if (data.type === "support_chat" && data.message) {
          const m = data.message;
          if (m.id > lastSupId) lastSupId = m.id;
          if (view === "support") renderSupMsgs([m], supListEl, me);
          else { supUnread++; playSound("support"); setBadge(); }
        } else if (data.type === "support_chat_del" && data.id) {
          const el = supListEl && supListEl.querySelector(`[data-mid="${data.id}"]`);
          if (el) el.remove();
        } else if (data.type === "chat_dm" && data.message) {
          if (view === "room" && data.room_id === roomId) {
            if (data.message.id > roomLastId) refreshRoom(false);
            dmCounters.unread = 0; setBadge();
            playSound("dm");
          } else {
            pollBadges();
            if (!isOpen || view !== "rooms") playSound("dm");
            if (view === "rooms") refreshRooms();
            else { dmCounters.unread = (dmCounters.unread || 0) + 1; setBadge(); }
          }
        } else if (data.type === "chat_dm_room") {
          if (data.event === "invite") {
            setHint("💌 Вам пришли в личные сообщения — вкладка «Личные»", false);
            playSound("dm");
          } else if (data.event === "accepted") {
            setHint("✉ Приглашение принято — можно писать", false);
          }
          refreshRooms();
          pollBadges();
        }
      });
    }

    let titleTimer = null;
    function flashTitle() {
      if (titleTimer) return;
      const orig = document.title;
      let on = false;
      titleTimer = setInterval(() => {
        on = !on;
        document.title = on ? "🟢 Новый чат! " + orig : orig;
      }, 1600);
      const stop = () => {
        clearInterval(titleTimer); titleTimer = null;
        document.title = orig;
        window.removeEventListener("focus", stop);
      };
      window.addEventListener("focus", stop);
    }

    // start
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
          const t = setInterval(() => {
            if (me) {
              clearInterval(t);
              setView("room", r);
              history.replaceState(null, "", location.pathname);
            }
          }, 250);
        }
      }
    } catch { }
    bindNameClicks(root);
    if (svcListEl) bindNameClicks(svcListEl);
    if (supListEl) bindNameClicks(supListEl);
    loadInitial().then(() => {
      if (view === "rooms") renderRooms();
    });
    setInterval(poll, POLL_MS);
    setInterval(pollBadges, BADGE_POLL_MS);
    setInterval(pingPresence, PING_MS);
    setInterval(pollServices, SVC_POLL_MS);
    setInterval(pollSupport, SUP_POLL_MS);
    setInterval(() => {
      if (!isOpen) return;
      if (view === "room") refreshRoom(false);
      else if (view === "rooms") refreshRooms();
    }, DM_POLL_MS);
    pollBadges();
    pingPresence();

    window.TerminalChat = {
      onWsMessage: (msg) => {
        if (!msg) return;
        if (msg.type === "terminal_chat" || msg.type === "terminal_chat_del" ||
          msg.type === "service_chat" || msg.type === "support_chat" ||
          msg.type === "support_chat_del" ||
          msg.type === "chat_dm" || msg.type === "chat_dm_room") {
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
