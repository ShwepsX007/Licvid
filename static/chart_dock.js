/**
 * Несколько обычных сингл-графиков в сетке терминала.
 *
 * Первый график — тот, что уже на странице (все слои, следование, рисование).
 * «＋ График» добавляет рядом такой же сингл-чарт. Плашка ⋮⋮ переставляет
 * окна внутри сетки и отрывает их от терминала: отпущенное снаружи окно висит
 * поверх страницы, отпущенное над сеткой — встаёт обратно.
 */
(function () {
  const MAX = 6;
  const KEY = "liqscope_chart_dock_v1";
  const NEXT = ["ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT", "DOGE_USDT", "BTC_USDT"];
  const TFS = [1, 3, 5, 15, 60, 240, 1440];

  function validSymbol(sym) {
    if (!sym || typeof sym !== "string") return null;
    sym = sym.trim().toUpperCase();
    return /^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$/.test(sym) ? sym : null;
  }
  function validTf(tf) {
    const n = Number(tf);
    return TFS.indexOf(n) !== -1 ? n : null;
  }
  function validId(id) {
    return id === "native" || (typeof id === "string" && /^c_[a-z0-9]{6,12}$/.test(id));
  }
  function clamp(n, a, b) {
    n = Number(n);
    if (!isFinite(n)) return a;
    return Math.max(a, Math.min(b, n));
  }
  function pretty(sym) {
    return String(sym || "").replace("_", "/");
  }

  class ChartDock {
    constructor() {
      this.order = ["native"];
      this.meta = {
        native: { id: "native", kind: "native", floating: false, x: 48, y: 64, w: 760, h: 540 },
      };
      this.els = new Map();
      this.section = document.querySelector(".chart-section");
      this.header = document.querySelector(".chart-section > .chart-header");
      this.stack = document.getElementById("chart-stack");
      this.host = document.getElementById("workspace-host");
      this.dock = null;
      this.placeholder = null;
      this.activeId = "native";
      this._drag = null;
      this._saveTimer = null;
      this._syncTimer = null;
    }

    start() {
      if (!this.section || !this.header || !this.stack) return;
      if (document.body.classList.contains("chart-embed")) return;
      this._bindNativeChrome();
      this._restore();
      if (this._needsLayout()) this.layout();
      this._syncTimer = setInterval(() => this._syncEmbedLabels(), 1500);
    }

    _needsLayout() {
      if (this.order.length > 1) return true;
      const n = this.meta.native;
      return !!(n && n.floating);
    }

    _bindNativeChrome() {
      const add = document.getElementById("add-chart-btn");
      if (add && !add._dockBound) {
        add._dockBound = true;
        add.addEventListener("click", () => this.addChart());
      }
      const grip = document.getElementById("chart-tear");
      if (grip && !grip._dockBound) {
        grip._dockBound = true;
        grip.addEventListener("pointerdown", (e) => this._onGrip("native", grip, e));
      }
      const back = document.getElementById("chart-tear-back");
      if (back && !back._dockBound) {
        back._dockBound = true;
        back.addEventListener("click", () => this.dockChart("native"));
      }
    }

    addChart() {
      if (this.order.length >= MAX) {
        window.alert("Максимум " + MAX + " графиков.");
        return null;
      }
      const id = "c_" + Math.random().toString(36).slice(2, 10);
      const rec = {
        id: id,
        kind: "embed",
        symbol: this._nextSymbol(),
        tf: this._nativeTf(),
        floating: false,
        x: 72 + this.order.length * 18,
        y: 72 + this.order.length * 18,
        w: 760,
        h: 540,
      };
      this.meta[id] = rec;
      this.order.push(id);
      this.activeId = id;
      this.layout();
      this._save();
      return id;
    }

    closeChart(id) {
      if (id === "native" || !this.meta[id]) return;
      const el = this.els.get(id);
      if (el) {
        const frame = el.querySelector("iframe");
        if (frame) frame.src = "about:blank";
        el.remove();
      }
      this.els.delete(id);
      delete this.meta[id];
      this.order = this.order.filter((x) => x !== id);
      if (this.activeId === id) this.activeId = "native";
      this.layout();
      this._save();
    }

    dockChart(id) {
      const rec = this.meta[id];
      if (!rec) return;
      rec.floating = false;
      this.activeId = id;
      this.layout();
      this._save();
      this._nudge();
    }

    floatChart(id, x, y) {
      const rec = this.meta[id];
      if (!rec) return;
      rec.floating = true;
      if (isFinite(x)) rec.x = x;
      if (isFinite(y)) rec.y = y;
      this._clampFloat(rec);
      this.activeId = id;
      this.layout();
      this._save();
      this._nudge();
    }

    setActive(id) {
      if (!this.meta[id]) return;
      this.activeId = id;
      this.els.forEach((el, pid) => el.classList.toggle("is-active", pid === id));
      const el = this.els.get(id);
      if (el && el.classList.contains("chart-floating")) el.style.zIndex = "78";
    }

    layout() {
      if (!this._needsLayout()) {
        this._unwrap();
        return;
      }
      this._ensureDock();
      this.order.forEach((id) => this._ensureSlot(id));
      this.order.forEach((id) => {
        const rec = this.meta[id];
        const el = this.els.get(id);
        if (!rec || !el) return;
        if (rec.floating) this._placeFloat(el, rec);
        else this._placeDock(el, rec);
      });
      this._paintActive();
      this._syncPlaceholder();
      this._paintNativeBack();
      this._nudge();
    }

    _ensureDock() {
      if (this.dock) return;
      this.dock = document.createElement("div");
      this.dock.id = "chart-dock";
      this.dock.className = "chart-dock";
      this.section.classList.add("has-chart-dock");
      this.section.insertBefore(this.dock, this.section.firstChild);
    }

    _ensureSlot(id) {
      if (this.els.get(id)) return this.els.get(id);
      const rec = this.meta[id];
      if (!rec) return null;
      if (id === "native") return this._wrapNative();
      const el = document.createElement("div");
      el.className = "chart-slot chart-slot-embed";
      el.dataset.id = id;
      const bar = document.createElement("div");
      bar.className = "chart-slot-bar";
      const grip = document.createElement("button");
      grip.type = "button";
      grip.className = "chart-slot-grip";
      grip.textContent = "⋮⋮";
      grip.title = "Потяни наружу, чтобы оторвать. Отпусти над сеткой — вернуть.";
      const title = document.createElement("span");
      title.className = "chart-slot-title";
      title.textContent = pretty(rec.symbol);
      const tfSel = document.createElement("select");
      tfSel.className = "chart-slot-tf";
      tfSel.title = "Таймфрейм этого графика";
      [[1, "1м"], [3, "3м"], [5, "5м"], [15, "15м"], [60, "1ч"], [240, "4ч"], [1440, "1д"]].forEach((pair) => {
        const opt = document.createElement("option");
        opt.value = String(pair[0]);
        opt.textContent = pair[1];
        if (pair[0] === rec.tf) opt.selected = true;
        tfSel.appendChild(opt);
      });
      const floatBtn = document.createElement("button");
      floatBtn.type = "button";
      floatBtn.className = "chart-slot-float";
      floatBtn.textContent = "⧉";
      floatBtn.title = "Оторвать от терминала";
      const dockBtn = document.createElement("button");
      dockBtn.type = "button";
      dockBtn.className = "chart-slot-dock";
      dockBtn.textContent = "Вернуть";
      dockBtn.title = "Вставить обратно в терминал";
      dockBtn.hidden = true;
      const closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "chart-slot-close";
      closeBtn.textContent = "✕";
      closeBtn.title = "Закрыть этот график";
      bar.appendChild(grip);
      bar.appendChild(title);
      bar.appendChild(tfSel);
      bar.appendChild(floatBtn);
      bar.appendChild(dockBtn);
      bar.appendChild(closeBtn);
      const frame = document.createElement("iframe");
      frame.className = "chart-slot-frame";
      frame.title = "График " + pretty(rec.symbol);
      frame.src = this._embedUrl(rec, id);
      el.appendChild(bar);
      el.appendChild(frame);
      grip.addEventListener("pointerdown", (e) => this._onGrip(id, grip, e));
      tfSel.addEventListener("change", () => this._setEmbedTf(id, tfSel.value));
      floatBtn.addEventListener("click", () => this.floatChart(id));
      dockBtn.addEventListener("click", () => this.dockChart(id));
      closeBtn.addEventListener("click", () => this.closeChart(id));
      bar.addEventListener("pointerdown", () => this.setActive(id));
      frame.addEventListener("load", () => {
        let framed = false;
        try { framed = !!(frame.contentDocument && frame.contentDocument.getElementById("tv-chart-container")); } catch (err) { framed = false; }
        if (!framed && frame.src && frame.src.indexOf("embed=1") !== -1) {
          title.textContent = "График не открылся в окне";
        }
        this._hookFrame(id, frame);
      });
      this.els.set(id, el);
      return el;
    }

    _wrapNative() {
      let el = this.els.get("native");
      if (el) return el;
      el = document.createElement("div");
      el.className = "chart-slot chart-slot-native";
      el.dataset.id = "native";
      const body = document.createElement("div");
      body.className = "chart-slot-body";
      el.appendChild(body);
      body.appendChild(this.header);
      body.appendChild(this.stack);
      this.els.set("native", el);
      el.addEventListener("pointerdown", () => this.setActive("native"));
      return el;
    }

    _placeDock(el, rec) {
      el.classList.remove("chart-floating");
      el.style.left = "";
      el.style.top = "";
      el.style.width = "";
      el.style.height = "";
      el.style.zIndex = "";
      if (el.parentNode !== this.dock) this.dock.appendChild(el);
      const floatBtn = el.querySelector(".chart-slot-float");
      const dockBtn = el.querySelector(".chart-slot-dock");
      if (floatBtn) floatBtn.hidden = false;
      if (dockBtn) dockBtn.hidden = true;
      rec.floating = false;
    }

    _placeFloat(el, rec) {
      this._clampFloat(rec);
      if (el.parentNode !== document.body) document.body.appendChild(el);
      el.classList.add("chart-floating");
      el.style.left = rec.x + "px";
      el.style.top = rec.y + "px";
      el.style.width = rec.w + "px";
      el.style.height = rec.h + "px";
      el.style.zIndex = "74";
      const floatBtn = el.querySelector(".chart-slot-float");
      const dockBtn = el.querySelector(".chart-slot-dock");
      if (floatBtn) floatBtn.hidden = true;
      if (dockBtn) dockBtn.hidden = false;
      rec.floating = true;
    }

    _unwrap() {
      if (this.header.parentNode !== this.section) {
        if (this.host && this.host.parentNode === this.section) this.section.insertBefore(this.header, this.host);
        else this.section.insertBefore(this.header, this.section.firstChild);
      }
      if (this.stack.parentNode !== this.section) {
        if (this.host && this.host.parentNode === this.section) this.host.insertAdjacentElement("afterend", this.stack);
        else this.section.appendChild(this.stack);
      }
      this.els.delete("native");
      if (this.dock && this.dock.parentNode) this.dock.parentNode.removeChild(this.dock);
      this.dock = null;
      this.placeholder = null;
      this.section.classList.remove("has-chart-dock");
      this._paintNativeBack();
      this._nudge();
    }

    _syncPlaceholder() {
      if (!this.dock) return;
      const docked = this.order.some((id) => this.meta[id] && !this.meta[id].floating);
      if (docked) {
        if (this.placeholder) this.placeholder.remove();
        this.placeholder = null;
        return;
      }
      if (this.placeholder) return;
      const box = document.createElement("div");
      box.className = "chart-dock-placeholder";
      const p = document.createElement("p");
      p.textContent = "График откреплён и висит поверх терминала. Его можно двигать за ⋮⋮.";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chart-tear-back";
      btn.textContent = "Вернуть в терминал";
      btn.addEventListener("click", () => {
        this.order.forEach((id) => { if (this.meta[id]) this.meta[id].floating = false; });
        this.layout();
        this._save();
      });
      box.appendChild(p);
      box.appendChild(btn);
      this.dock.appendChild(box);
      this.placeholder = box;
    }

    _paintNativeBack() {
      const back = document.getElementById("chart-tear-back");
      if (!back) return;
      const floating = !!(this.meta.native && this.meta.native.floating);
      back.hidden = !floating;
    }

    _paintActive() {
      this.els.forEach((el, id) => el.classList.toggle("is-active", id === this.activeId));
    }

    _onGrip(id, grip, e) {
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      this.setActive(id);
      const rec = this.meta[id];
      if (!rec) return;
      this._drag = {
        id: id,
        grip: grip,
        sx: e.clientX,
        sy: e.clientY,
        moved: false,
        offX: 28,
        offY: 16,
      };
      const el = this.els.get(id);
      if (el) {
        const r = el.getBoundingClientRect();
        this._drag.offX = e.clientX - r.left;
        this._drag.offY = e.clientY - r.top;
      }
      document.body.classList.add("chart-dock-dragging");
      const move = (ev) => this._onDragMove(ev);
      const up = (ev) => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        this._onDragEnd(ev);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    }

    _setEmbedTf(id, tf) {
      const rec = this.meta[id];
      const n = validTf(tf);
      if (!rec || !n) return;
      rec.tf = n;
      const el = this.els.get(id);
      const frame = el && el.querySelector("iframe");
      let clicked = false;
      try {
        const btn = frame && frame.contentDocument && frame.contentDocument.querySelector('.btn-tf[data-tf="' + n + '"]');
        if (btn) { btn.click(); clicked = true; }
      } catch (e) {}
      if (!clicked && frame) frame.src = this._embedUrl(rec, id);
      const label = el && el.querySelector(".chart-slot-title");
      if (label) label.textContent = pretty(rec.symbol) + " · " + this._tfLabel(n);
      this._save();
    }

    _onDragMove(e) {
      const d = this._drag;
      if (!d) return;
      if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < 6) return;
      d.moved = true;
      const outside = this._outsideDock(e.clientX, e.clientY);
      const rec = this.meta[d.id];
      if (!rec) return;
      if (outside) {
        if (!rec.floating) {
          rec.floating = true;
          rec.x = e.clientX - d.offX;
          rec.y = e.clientY - d.offY;
          this.layout();
        } else {
          rec.x = e.clientX - d.offX;
          rec.y = e.clientY - d.offY;
          this._clampFloat(rec);
          const el = this.els.get(d.id);
          if (el) {
            el.style.left = rec.x + "px";
            el.style.top = rec.y + "px";
          }
        }
        if (this.dock) this.dock.classList.add("is-drop");
      } else if (this.dock) {
        this.dock.classList.add("is-drop");
        this._markDrop(d.id, e.clientX, e.clientY);
      }
    }

    _markDrop(id, x, y) {
      if (!this.dock) return;
      this.dock.querySelectorAll(".chart-slot").forEach((s) => s.classList.remove("is-drop-target"));
      const slots = Array.from(this.dock.querySelectorAll(".chart-slot"));
      for (let i = 0; i < slots.length; i++) {
        if (slots[i].dataset.id === id) continue;
        const r = slots[i].getBoundingClientRect();
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
          slots[i].classList.add("is-drop-target");
          break;
        }
      }
    }

    _onDragEnd(e) {
      const d = this._drag;
      this._drag = null;
      document.body.classList.remove("chart-dock-dragging");
      if (this.dock) {
        this.dock.classList.remove("is-drop");
        this.dock.querySelectorAll(".chart-slot").forEach((s) => s.classList.remove("is-drop-target"));
      }
      if (!d || !d.moved) return;
      const rec = this.meta[d.id];
      if (!rec) return;
      const outside = this._outsideDock(e.clientX, e.clientY);
      if (outside) {
        rec.floating = true;
        rec.x = e.clientX - d.offX;
        rec.y = e.clientY - d.offY;
        this._readFloatSize(d.id);
        this.layout();
      } else {
        rec.floating = false;
        this._reorderAt(d.id, e.clientX, e.clientY);
        this.layout();
      }
      this._save();
      this._nudge();
    }

    _outsideDock(x, y) {
      const box = (this.dock || this.section).getBoundingClientRect();
      return x < box.left + 8 || x > box.right - 8 || y < box.top + 8 || y > box.bottom - 8;
    }

    _reorderAt(id, x, y) {
      if (!this.dock) return;
      const slots = Array.from(this.dock.querySelectorAll(".chart-slot"));
      let hit = null;
      for (let i = 0; i < slots.length; i++) {
        const s = slots[i];
        if (s.dataset.id === id) continue;
        const r = s.getBoundingClientRect();
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
          hit = { id: s.dataset.id, after: x > r.left + r.width / 2 };
          break;
        }
      }
      if (!hit) return;
      const rest = this.order.filter((xId) => xId !== id);
      let at = rest.indexOf(hit.id);
      if (at === -1) rest.push(id);
      else {
        if (hit.after) at += 1;
        rest.splice(at, 0, id);
      }
      this.order = rest;
    }

    _hookFrame(id, frame) {
      try {
        const doc = frame.contentDocument;
        if (!doc || doc._dockHooked) return;
        doc._dockHooked = true;
        doc.addEventListener("pointerdown", () => this.setActive(id), true);
      } catch (e) {}
      this._syncEmbedLabels();
    }

    _syncEmbedLabels() {
      this.order.forEach((id) => {
        if (id === "native") return;
        const el = this.els.get(id);
        const rec = this.meta[id];
        if (!el || !rec) return;
        const frame = el.querySelector("iframe");
        let doc = null;
        try { doc = frame && frame.contentDocument; } catch (e) { doc = null; }
        if (!doc) return;
        const titleEl = doc.getElementById("current-symbol-title");
        const text = titleEl ? titleEl.textContent : "";
        const m = String(text || "").toUpperCase().match(/[A-Z0-9]{2,20}\/[A-Z0-9]{2,6}/);
        if (m) {
          const sym = validSymbol(m[0].replace("/", "_"));
          if (sym) rec.symbol = sym;
        }
        const tfBtn = doc.querySelector(".btn-tf.active");
        const tf = tfBtn ? validTf(tfBtn.dataset.tf) : null;
        if (tf) rec.tf = tf;
        const label = el.querySelector(".chart-slot-title");
        if (label) label.textContent = pretty(rec.symbol) + " · " + this._tfLabel(rec.tf);
        const tfSel = el.querySelector(".chart-slot-tf");
        if (tfSel && document.activeElement !== tfSel) tfSel.value = String(rec.tf || 5);
      });
    }

    _embedUrl(rec, id) {
      const u = new URL("/terminal", window.location.origin);
      u.searchParams.set("embed", "1");
      u.searchParams.set("slot", id);
      u.searchParams.set("symbol", rec.symbol);
      u.searchParams.set("tf", String(rec.tf || 5));
      return u.pathname + u.search;
    }

    _nextSymbol() {
      const used = new Set();
      const native = validSymbol(window.state && window.state.chartSymbol) || "BTC_USDT";
      used.add(native);
      this.order.forEach((id) => {
        const rec = this.meta[id];
        if (rec && rec.symbol) used.add(rec.symbol);
      });
      for (let i = 0; i < NEXT.length; i++) {
        if (!used.has(NEXT[i])) return NEXT[i];
      }
      return "ETH_USDT";
    }

    _nativeTf() {
      const fromState = validTf(window.state && window.state.timeframe);
      if (fromState) return fromState;
      const btn = document.querySelector(".btn-tf.active");
      return validTf(btn && btn.dataset.tf) || 5;
    }

    _tfLabel(tf) {
      if (tf === 60) return "1ч";
      if (tf === 240) return "4ч";
      if (tf === 1440) return "1д";
      return String(tf || 5) + "м";
    }

    _clampFloat(rec) {
      const w = clamp(rec.w || 760, 380, Math.max(380, window.innerWidth - 16));
      const h = clamp(rec.h || 540, 280, Math.max(280, window.innerHeight - 16));
      rec.w = w;
      rec.h = h;
      rec.x = clamp(rec.x, 8, Math.max(8, window.innerWidth - w - 8));
      rec.y = clamp(rec.y, 8, Math.max(8, window.innerHeight - 80));
    }

    _readFloatSize(id) {
      const el = this.els.get(id);
      const rec = this.meta[id];
      if (!el || !rec || !rec.floating) return;
      const r = el.getBoundingClientRect();
      if (r.width > 200 && r.height > 160) {
        rec.w = Math.round(r.width);
        rec.h = Math.round(r.height);
      }
    }

    _nudge() {
      setTimeout(() => { try { window.dispatchEvent(new Event("resize")); } catch (e) {} }, 40);
      setTimeout(() => { try { window.dispatchEvent(new Event("resize")); } catch (e) {} }, 240);
    }

    _save() {
      if (this._saveTimer) clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(() => {
        this._saveTimer = null;
        try {
          const charts = this.order.map((id) => {
            const rec = this.meta[id];
            if (!rec) return null;
            return {
              id: rec.id,
              kind: rec.kind,
              symbol: rec.symbol || "",
              tf: rec.tf || 5,
              floating: !!rec.floating,
              x: Math.round(rec.x || 0),
              y: Math.round(rec.y || 0),
              w: Math.round(rec.w || 760),
              h: Math.round(rec.h || 540),
            };
          }).filter(Boolean);
          localStorage.setItem(KEY, JSON.stringify({ v: 1, activeId: this.activeId, charts: charts }));
        } catch (e) {}
      }, 180);
    }

    _restore() {
      let raw = null;
      try { raw = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { raw = null; }
      if (!raw || !Array.isArray(raw.charts)) return;
      const order = [];
      const meta = {};
      raw.charts.slice(0, MAX).forEach((c) => {
        if (!c || !validId(c.id) || meta[c.id]) return;
        if (c.id === "native") {
          meta.native = {
            id: "native", kind: "native", floating: !!c.floating,
            x: Number(c.x) || 48, y: Number(c.y) || 64,
            w: Number(c.w) || 760, h: Number(c.h) || 540,
          };
          order.push("native");
          return;
        }
        const sym = validSymbol(c.symbol);
        const tf = validTf(c.tf) || 5;
        if (!sym || c.kind !== "embed") return;
        meta[c.id] = {
          id: c.id, kind: "embed", symbol: sym, tf: tf, floating: !!c.floating,
          x: Number(c.x) || 72, y: Number(c.y) || 72,
          w: Number(c.w) || 760, h: Number(c.h) || 540,
        };
        order.push(c.id);
      });
      if (order.indexOf("native") === -1) {
        order.unshift("native");
        meta.native = this.meta.native;
      }
      this.order = order;
      this.meta = meta;
      if (validId(raw.activeId) && meta[raw.activeId]) this.activeId = raw.activeId;
    }
  }

  function boot() {
    try {
      const dock = new ChartDock();
      window.LiqScopeDock = dock;
      dock.start();
    } catch (e) {
      console.warn("chart dock failed", e);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  window.LiqScopeDockValidate = { symbol: validSymbol, tf: validTf, id: validId };
})();
