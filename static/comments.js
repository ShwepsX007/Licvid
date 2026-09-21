/* 💬 Комментарии к дайджесту и сводке по часам — читают все, пишут только зарегистрированные */
(function () {
  "use strict";

  const API = "/api/comments";
  const ME = "/api/comments/me";
  const MAX = 1000;

  const I18n = window.LiqScopeI18n;
  function t(key, vars) { return I18n && I18n.t ? I18n.t(key, vars) : key; }
  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

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

  async function fetchMe() {
    try {
      const r = await fetch(ME, { credentials: "same-origin" });
      if (!r.ok) return null;
      const j = await r.json();
      return j && j.user ? { id: j.user.id, name: j.user.name, is_admin: j.user.is_admin } : null;
    } catch { return null; }
  }

  async function fetchList(kind, item, afterId, limit) {
    try {
      let url = `${API}?kind=${encodeURIComponent(kind)}&item=${encodeURIComponent(item)}&limit=${limit || 100}`;
      if (afterId) url += `&after_id=${afterId}`;
      const r = await fetch(url, { credentials: "same-origin" });
      if (!r.ok) return [];
      const j = await r.json();
      return (j && j.messages) ? j.messages : [];
    } catch { return []; }
  }

  async function postComment(kind, item, text) {
    const r = await fetch(API, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, item, text }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.hint || j.error || t("cc.error"));
    return j.message;
  }

  function renderList(list, container, me) {
    if (!container) return;
    if (!list.length && !container.children.length) {
      container.innerHTML = '<div class="cc-empty">' + esc(t("cc.empty")) + '</div>';
      return;
    }
    const empty = container.querySelector(".cc-empty");
    if (empty && list.length) empty.remove();

    for (const m of list) {
      if (container.querySelector(`[data-cid="${m.id}"]`)) continue;
      const div = document.createElement("div");
      div.className = "cc-msg" + (m.admin ? " admin" : "");
      div.dataset.cid = m.id;
      const name = esc(m.name || "anon");
      const time = fmtTime(m.ts);
      const text = esc(m.text);
      const canDel = me && me.is_admin;
      div.innerHTML = `<div class="cc-meta"><span class="cc-name">${name}${m.admin ? " 👑" : ""}</span><span class="cc-time">${time}</span>${canDel ? `<button data-del="${m.id}" title="Delete" style="margin-left:auto;background:transparent;border:0;color:#6b7da0;cursor:pointer;font-size:14px">✕</button>` : ""}</div><div class="cc-text">${text}</div>`;
      container.appendChild(div);
    }
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 160;
    if (nearBottom) container.scrollTop = container.scrollHeight;
  }

  function initForPage(kind, getItem) {
    const host = document.getElementById("comments-host");
    if (!host) return;
    const listEl = document.getElementById("cc-list");
    const input = document.getElementById("cc-input");
    const sendBtn = document.getElementById("cc-send");
    const hint = document.getElementById("cc-hint");
    const countEl = document.getElementById("cc-count");
    const titleEl = host.querySelector("h3");
    const subEl = host.querySelector(".cc-sub");
    if (!listEl || !input || !sendBtn) return;

    // translate static parts
    function translateStatic() {
      try {
        if (titleEl) {
          // keep count span
          const cnt = titleEl.querySelector("#cc-count");
          titleEl.childNodes[0].textContent = "💬 " + t("cc.title") + " ";
          if (cnt && !titleEl.contains(cnt)) titleEl.appendChild(cnt);
        }
        if (subEl) {
          subEl.textContent = t(kind === "digest" ? "cc.sub_digest" : "cc.sub_hourly");
        }
        if (sendBtn) sendBtn.textContent = t("cc.send");
        if (input) {
          if (input.disabled) input.placeholder = t("cc.ph_login");
          else input.placeholder = t("cc.ph_write");
        }
      } catch(e){}
    }
    translateStatic();
    if (I18n && I18n.onChange) I18n.onChange(translateStatic);

    let me = null;
    let lastId = 0;
    let currentItem = "";

    function setHint(ttext, err, html) {
      if (!hint) return;
      if (html) hint.innerHTML = ttext;
      else hint.textContent = ttext || "";
      hint.classList.toggle("error", !!err);
    }

    function setCount(n) {
      if (!countEl) return;
      countEl.textContent = n > 0 ? String(n) : "0";
    }

    function updateAuth() {
      if (!me) {
        input.placeholder = t("cc.ph_login");
        input.disabled = true;
        sendBtn.disabled = true;
        const loginUrl = "/login?next=" + encodeURIComponent(location.pathname + location.search);
        const loginLink = '<a href="' + loginUrl + '">' + esc(t("cc.login")) + '</a>';
        setHint(t("cc.login_prompt", { login: loginLink }), false, true);
      } else {
        input.placeholder = t("cc.ph_write");
        input.disabled = false;
        sendBtn.disabled = false;
        setHint(t("cc.you", { name: me.name || ("id"+me.id), max: MAX }), false, false);
      }
    }

    async function load() {
      currentItem = getItem() || "";
      if (!currentItem) {
        listEl.innerHTML = '<div class="cc-empty">' + esc(t("cc.open_first")) + '</div>';
        setCount(0);
        return;
      }
      listEl.innerHTML = "";
      lastId = 0;
      const msgs = await fetchList(kind, currentItem, 0, 100);
      if (msgs.length) {
        lastId = Math.max(...msgs.map(m => m.id));
      }
      setCount(msgs.length);
      renderList(msgs, listEl, me);
    }

    async function doSend() {
      if (!me) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
        return;
      }
      const it = getItem() || currentItem;
      if (!it) { setHint(t("cc.open_issue"), true); return; }
      const txt = (input.value || "").trim();
      if (!txt) return;
      if (txt.length > MAX) { setHint(t("cc.too_long", { max: MAX }), true); return; }
      input.disabled = true;
      sendBtn.disabled = true;
      setHint(t("cc.sending"), false);
      try {
        const msg = await postComment(kind, it, txt);
        input.value = "";
        if (msg && msg.id) {
          lastId = Math.max(lastId, msg.id);
          renderList([msg], listEl, me);
          const cur = listEl.querySelectorAll(".cc-msg").length;
          setCount(cur);
        }
        setHint(t("cc.you", { name: me.name || ("id"+me.id), max: MAX }), false);
      } catch (e) {
        setHint(e.message || t("cc.error"), true);
      } finally {
        input.disabled = !me;
        sendBtn.disabled = !me;
        try{ input.focus(); }catch(e){}
      }
    }

    sendBtn.addEventListener("click", doSend);
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); doSend(); }
    });

    listEl.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-del]");
      if (!btn) return;
      const id = parseInt(btn.getAttribute("data-del"), 10);
      if (!id) return;
      if (!confirm(t("cc.delete_confirm", { id }))) return;
      try {
        const r = await fetch(`${API}/${id}`, { method: "DELETE", credentials: "same-origin" });
        if (r.ok) {
          const el = listEl.querySelector(`[data-cid="${id}"]`);
          if (el) el.remove();
          setCount(listEl.querySelectorAll(".cc-msg").length);
          if (!listEl.children.length) {
            listEl.innerHTML = '<div class="cc-empty">' + esc(t("cc.empty")) + '</div>';
          }
        }
      } catch {}
    });

    setInterval(async () => {
      const it = getItem() || currentItem;
      if (!it) return;
      if (it !== currentItem) { await load(); return; }
      try {
        const msgs = await fetchList(kind, it, lastId, 100);
        if (!msgs.length) return;
        for (const m of msgs) if (m.id > lastId) lastId = m.id;
        renderList(msgs, listEl, me);
        setCount(listEl.querySelectorAll(".cc-msg").length);
      } catch {}
    }, 4000);

    window.LiqScopeComments = window.LiqScopeComments || {};
    window.LiqScopeComments.reload = load;
    window.LiqScopeComments.kind = kind;

    fetchMe().then((u) => { me = u; updateAuth(); translateStatic(); }).then(load);

    let lastSeen = getItem() || "";
    setInterval(() => {
      const cur = getItem() || "";
      if (cur && cur !== lastSeen) {
        lastSeen = cur;
        load();
      }
    }, 1200);
  }

  function boot() {
    if (I18n && I18n.init) I18n.init();
    const path = location.pathname || "";
    if (path.indexOf("/digest") === 0) {
      initForPage("digest", () => {
        try {
          const sel = document.querySelector(".dig-card.active");
          if (sel && sel.getAttribute("data-day")) return sel.getAttribute("data-day");
        } catch {}
        try {
          const urlDay = new URLSearchParams(location.search).get("day");
          if (urlDay) return urlDay;
        } catch {}
        if (window._dig_selected) return window._dig_selected;
        return "";
      });
    } else if (path.indexOf("/hourly") === 0) {
      initForPage("hourly", () => {
        try {
          const urlDay = new URLSearchParams(location.search).get("day");
          if (urlDay) return urlDay;
        } catch {}
        try {
          const sel = document.querySelector(".hour-card.active");
          if (sel && sel.getAttribute("data-day")) return sel.getAttribute("data-day");
        } catch {}
        if (window._hour_selected) return window._hour_selected;
        return "";
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
