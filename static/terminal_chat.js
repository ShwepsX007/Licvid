/* 💬 Мини-чат терминала — виджет в /terminal, 3 дня истории, пишет любой auth юзер */
(function () {
  const API = "/api/terminal/chat";
  const POLL_MS = 3500;
  const LS_OPEN = "liqscope.tchat.open";
  const LS_LAST_ID = "liqscope.tchat.lastId";
  const LS_POS = "liqscope.tchat.pos";


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
    } catch {}
    return null;
  }

  function savePos(left, top) {
    try { localStorage.setItem(LS_POS, JSON.stringify({ left, top })); } catch {}
  }

  function clearPos() {
    try { localStorage.removeItem(LS_POS); } catch {}
  }

  function applyPos(root, pos) {
    if (!root || !pos) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const rect = root.getBoundingClientRect();
    const w = rect.width || 340;
    const h = rect.height || 420;
    let l = clamp(pos.left, 4, vw - w - 4);
    let t = clamp(pos.top, 4, vh - h - 4);
    root.style.left = l + "px";
    root.style.top = t + "px";
    root.style.right = "auto";
    root.style.bottom = "auto";
    root.classList.add("tchat-moved");
  }

  function resetPos(root) {
    if (!root) return;
    root.style.left = "";
    root.style.top = "";
    root.style.right = "";
    root.style.bottom = "";
    root.classList.remove("tchat-moved");
    clearPos();
  }


  function renderMsgs(list, container, me) {
    if (!container) return;
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="tchat-empty">Пока тихо — напишите первым! 💬<br><small>История хранится 3 дня</small></div>';
      return;
    }
    // remove empty placeholder if present
    const empty = container.querySelector(".tchat-empty");
    if (empty && list.length) empty.remove();

    for (const m of list) {
      if (container.querySelector(`[data-mid="${m.id}"]`)) continue;
      const div = document.createElement("div");
      div.className = "tchat-msg" + (m.admin ? " admin" : "");
      div.dataset.mid = m.id;
      const name = esc(m.name || "anon");
      const time = fmtTime(m.ts);
      const text = esc(m.text);
      const canDel = me && me.is_admin;
      div.innerHTML = `<div class="tchat-meta"><span class="tchat-name">${name}${m.admin ? " 👑" : ""}</span><span class="tchat-time">${time}</span>${canDel ? `<button data-del="${m.id}" title="Удалить" style="margin-left:auto;background:transparent;border:0;color:#6b7da0;cursor:pointer">✕</button>` : ""}</div><div class="tchat-text">${text}</div>`;
      container.appendChild(div);
    }
    // autoscroll if near bottom
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

  async function postMsg(text) {
    const r = await fetch(API, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.hint || j.error || "error");
    return j.message;
  }

  function init() {
    // only on /terminal
    if (!/\/terminal/.test(location.pathname)) return;
    const root = $("#terminal-chat");
    if (!root) return;
    const toggle = $("#tchat-toggle");
    const headerBtn = $("#tchat-header-btn");
    const panel = $("#tchat-panel");
    const closeBtn = $("#tchat-close");
    const listEl = $("#tchat-list");
    const input = $("#tchat-input");
    const sendBtn = $("#tchat-send");
    const hint = $("#tchat-hint");
    const unreadEls = [];
    try {
      document.querySelectorAll("#tchat-unread, #tchat-unread-hdr").forEach((el) => unreadEls.push(el));
    } catch {}

    let me = null;
    let lastId = 0;
    try { lastId = parseInt(localStorage.getItem(LS_LAST_ID) || "0", 10) || 0; } catch {}
    let unread = 0;
    let isOpen = false;
    try { isOpen = localStorage.getItem(LS_OPEN) === "1"; } catch {}

    function setUnread(n) {
      unreadEls.forEach((el) => {
        if (!el) return;
        el.textContent = n > 99 ? "99+" : String(n);
        el.classList.toggle("hidden", n <= 0);
      });
    }

    function setOpen(v) {
      isOpen = !!v;
      root.classList.toggle("open", isOpen);
      document.body.classList.toggle("tchat-open", isOpen);
      if (headerBtn) headerBtn.classList.toggle("active", isOpen);
      if (panel) panel.classList.toggle("hidden", !isOpen);
      try { localStorage.setItem(LS_OPEN, isOpen ? "1" : "0"); } catch {}
      if (isOpen) {
        unread = 0;
        setUnread(0);
        if (listEl) listEl.scrollTop = listEl.scrollHeight;
      }
    }

    // --- draggable across whole screen ---------------------------------
    (function bindDrag() {
      const head = root.querySelector(".tchat-head");
      if (!head) return;
      // apply saved position on start
      const saved = loadPos();
      if (saved) applyPos(root, saved);

      let dragging = false;
      let startX = 0, startY = 0;
      let startLeft = 0, startTop = 0;
      let moved = false;

      function onDown(ev) {
        // ignore clicks on close button
        if (ev.target.closest && ev.target.closest("#tchat-close")) return;
        // only left mouse or touch
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
        // prevent text selection
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
        let nl = clamp(startLeft + dx, 4, vw - w - 4);
        let nt = clamp(startTop + dy, 4, vh - h - 4);
        root.style.left = nl + "px";
        root.style.top = nt + "px";
        root.style.right = "auto";
        root.style.bottom = "auto";
        root.classList.add("tchat-moved");
      }

      function onUp(ev) {
        if (!dragging) return;
        dragging = false;
        root.classList.remove("tchat-dragging");
        if (moved) {
          const rect = root.getBoundingClientRect();
          savePos(rect.left, rect.top);
        }
      }

      head.addEventListener("mousedown", onDown);
      head.addEventListener("touchstart", onDown, { passive: false });
      window.addEventListener("mousemove", onMove);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("mouseup", onUp);
      window.addEventListener("touchend", onUp);
      window.addEventListener("touchcancel", onUp);

      // double-click header to reset position
      head.addEventListener("dblclick", (ev) => {
        if (ev.target.closest && ev.target.closest("#tchat-close")) return;
        resetPos(root);
      });

      // keep inside viewport on resize
      window.addEventListener("resize", () => {
        const pos = loadPos();
        if (pos) applyPos(root, pos);
      });
    })();


    function setHint(t, err) {
      if (!hint) return;
      hint.textContent = t || "";
      hint.classList.toggle("error", !!err);
    }

    function updateAuthUI() {
      if (!input || !sendBtn) return;
      if (!me) {
        input.placeholder = "Войдите, чтобы писать…";
        input.disabled = true;
        sendBtn.disabled = true;
        setHint('Только для зарегистрированных — <a href="/login?next=/terminal" style="color:#8ab4ff">войти</a>', false);
        if (hint) hint.innerHTML = 'Только для зарегистрированных — <a href="/login?next=/terminal" style="color:#8ab4ff">войти</a>';
      } else {
        input.placeholder = "Сообщение… (Enter)";
        input.disabled = false;
        sendBtn.disabled = false;
        setHint(`Вы: ${me.name || "id" + me.id} • 3 дня истории`, false);
      }
    }

    async function loadInitial() {
      me = await fetchMe();
      updateAuthUI();
      const msgs = await fetchList(0, 100);
      if (msgs.length) {
        lastId = Math.max(lastId, ...msgs.map(m => m.id));
        try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {}
      }
      if (listEl) {
        listEl.innerHTML = "";
        renderMsgs(msgs, listEl, me);
      }
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
          try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {}
          renderMsgs(msgs, listEl, me);
          if (!isOpen) {
            unread += newOnes;
            setUnread(unread);
          }
        }
      } catch {}
    }

    async function doSend() {
      if (!me) { location.href = "/login?next=/terminal"; return; }
      const txt = (input.value || "").trim();
      if (!txt) return;
      if (txt.length > 500) { setHint("Слишком длинно — до 500 символов", true); return; }
      input.disabled = true;
      sendBtn.disabled = true;
      setHint("Отправка…", false);
      try {
        const msg = await postMsg(txt);
        input.value = "";
        if (msg && msg.id) {
          lastId = Math.max(lastId, msg.id);
          try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {}
          renderMsgs([msg], listEl, me);
        }
        setHint(`Вы: ${me.name || "id" + me.id} • 3 дня истории`, false);
      } catch (e) {
        setHint(e.message || "Ошибка", true);
      } finally {
        input.disabled = !me;
        sendBtn.disabled = !me;
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

    // делегирование удаления (админ)
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
      } catch {}
    });

    // WS live updates (если есть основной WS)
    try {
      const origOnMessage = window._tchat_ws_hooked;
      if (!origOnMessage) {
        window._tchat_ws_hooked = true;
        // перехватываем сообщения основного WS через событие
        document.addEventListener("liqscope:ws", (ev) => {
          const data = ev.detail;
          if (!data) return;
          if (data.type === "terminal_chat" && data.message) {
            const m = data.message;
            if (m.id > lastId) {
              lastId = m.id;
              try { localStorage.setItem(LS_LAST_ID, String(lastId)); } catch {}
            }
            if (!isOpen) {
              unread++;
              setUnread(unread);
            }
            renderMsgs([m], listEl, me);
          } else if (data.type === "terminal_chat_del" && data.id) {
            const el = listEl && listEl.querySelector(`[data-mid="${data.id}"]`);
            if (el) el.remove();
          }
        });
      }
    } catch {}

    // start
    setOpen(isOpen);
    loadInitial();
    setInterval(poll, POLL_MS);

    // expose for app.js to forward WS messages
    window.TerminalChat = {
      onWsMessage: (msg) => {
        if (!msg) return;
        if (msg.type === "terminal_chat" || msg.type === "terminal_chat_del") {
          document.dispatchEvent(new CustomEvent("liqscope:ws", { detail: msg }));
        }
      }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
