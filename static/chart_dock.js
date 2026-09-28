/**
 * Несколько обычных сингл-графиков в сетке терминала.
 *
 * Первый график — тот, что уже на странице (все слои, следование, рисование).
 * «＋ График» добавляет рядом такой же сингл-чарт.
 * Мультиэкран — сетка ×1–×4, края между графиками тянутся.
 * Вкладки — один график на весь терминал. ⧉ отрывает окно и его
 * можно таскать по терминалу. Отдельная вкладка браузера не открывается.
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
      this.wrap = null;
      this.placeholder = null;
      this.cols = 2;
      this.view = "grid";
      this._fsId = null;
      this._embedFs = false;
      this._pops = new Map();
      this.activeId = "native";
      this._drag = null;
      this._saveTimer = null;
      this._syncTimer = null;
    }

    start() {
      // Режим встроенного окна теперь ставится самим терминалом по адресу
      // (старый workspace.js больше не грузится): дублируем класс, чтобы CSS
      // спрятал лишнее даже если app.js ещё не успел его добавить.
      try {
        const q = new URLSearchParams(location.search);
        if (q.get("embed") === "1" || q.get("mode") === "panel") {
          document.body.classList.add("chart-embed");
        }
      } catch (e) {}
      if (document.body.classList.contains("chart-embed")) {
        this._bootEmbed();
        return;
      }
      if (!this.section || !this.header || !this.stack) return;
      this._bindNativeChrome();
      this._restore();
      window.addEventListener("message", (e) => this._onMessage(e));
      if (this._needsLayout()) this.layout();
      this._syncTimer = setInterval(() => this._syncEmbedLabels(), 1500);
      this._startWsBridge();
    }

    // --- Один WebSocket на окно ---------------------------------------------
    // Дополнительные графики — iframe того же /terminal. Свой сокет они не
    // открывают: родительское окно раздаёт кадры своего единственного сокета
    // через postMessage. Итого: 1 сокет на окно при любом числе графиков.
    _startWsBridge() {
      if (this._wsBridgeOn) return;
      const api = window.LiqScopeWsBridge;
      if (!api || typeof api.listen !== "function") return;
      this._wsBridgeOn = true;
      // Только то, что нужно графикам. Лента/статистика/чат остаются в
      // родительском окне и в iframe не пересылаются.
      const ALLOW = { prices: 1, tick: 1, candle: 1, candles: 1 };
      api.listen((msg) => {
        if (msg && ALLOW[msg.type]) this._bridgeSendAll(msg);
      });
      if (typeof api.stateListen === "function") {
        api.stateListen((status, key) => {
          this._bridgeSendAll({ type: "ws-state", status: status, key: key });
        });
      }
    }

    _bridgeFrames(fn) {
      this.order.forEach((id) => {
        if (id === "native") return;
        const el = this.els.get(id);
        const frame = el && el.querySelector("iframe");
        const win = frame && frame.contentWindow;
        if (!win || win === window) return;
        try { fn(win); } catch (e) { /* фрейм мог умереть — не критично */ }
      });
    }

    _bridgeSendAll(payload) {
      const msg = { source: "liqscope-dock", type: "ws-data", payload: payload };
      this._bridgeFrames((win) => win.postMessage(msg, location.origin));
    }

    _bridgeSendTo(id, payload) {
      const el = this.els.get(id);
      const frame = el && el.querySelector("iframe");
      const win = frame && frame.contentWindow;
      if (!win || win === window) return;
      try {
        win.postMessage({ source: "liqscope-dock", type: "ws-data", payload: payload },
                        location.origin);
      } catch (e) { /* ignore */ }
    }

    _bootEmbed() {
      document.documentElement.style.height = "100%";
      document.documentElement.style.overflow = "hidden";
      const kick = () => { try { window.dispatchEvent(new Event("resize")); } catch (e) {} };
      setTimeout(kick, 60);
      setTimeout(kick, 400);
      setTimeout(kick, 1200);
      try {
        const sec = document.querySelector(".chart-section");
        if (sec && window.ResizeObserver) new ResizeObserver(kick).observe(sec);
      } catch (e) {}
      const q = new URLSearchParams(location.search);
      window.addEventListener("message", (e) => {
        if (e.origin !== location.origin) return;
        const data = e.data;
        if (!data || data.source !== "liqscope-dock" || data.type !== "fullscreen-state") return;
        this._embedFs = !!data.on;
        const expand = document.getElementById("chart-expand");
        if (expand) expand.classList.toggle("active", this._embedFs);
      });
      window.addEventListener("message", (e) => {
        if (e.origin !== location.origin) return;
        const data = e.data;
        if (!data || data.source !== "liqscope-dock" || data.type !== "chrome") return;
        document.body.classList.toggle("chart-embed-full", !!data.full);
        try { window.dispatchEvent(new Event("resize")); } catch (err) {}
      });
      if (q.get("pop") !== "1" || !window.opener) return;
      document.body.classList.add("chart-embed-pop");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chart-tear-back chart-pop-back";
      btn.textContent = "Вернуть в терминал";
      btn.addEventListener("click", () => {
        try {
          window.opener.postMessage({ source: "liqscope-dock", type: "dock", slot: q.get("slot") || "" }, location.origin);
        } catch (e) {}
        window.close();
      });
      document.body.appendChild(btn);
    }

    _onMessage(e) {
      if (e.origin !== location.origin) return;
      const data = e.data;
      if (!data || data.source !== "liqscope-dock") return;
      if (data.type === "dock") {
        if (!validId(data.slot)) return;
        this.dockBack(data.slot);
        return;
      }
      if (data.type === "fullscreen" && validId(data.slot)) {
        this.toggleSlotFullscreen(data.slot);
        return;
      }
      if (data.type === "fullscreen-exit" && this._fsId && (!data.slot || data.slot === this._fsId)) {
        this.exitSlotFullscreen();
      }
      if (data.type === "embed-sub" && validId(data.slot)) {
        // встроенный график сообщил свою пару/таймфрейм (старт или смена):
        // обновляем метку слота и сразу отдаём состояние сокета родителя
        const rec = this.meta[data.slot];
        if (rec && rec.kind === "embed") {
          const sym = validSymbol(data.symbol);
          const tf = validTf(data.tf);
          if (sym) rec.symbol = sym;
          if (tf) rec.tf = tf;
          const el = this.els.get(data.slot);
          const label = el && el.querySelector(".chart-slot-title");
          if (label) label.textContent = pretty(rec.symbol) + " · " + this._tfLabel(rec.tf);
          this._save();
        }
        const bridge = window.LiqScopeWsBridge;
        const st = bridge && bridge.currentState ? bridge.currentState() : null;
        if (st && st.status && st.key) {
          this._bridgeSendTo(data.slot, { type: "ws-state", status: st.status, key: st.key });
        }
      }
    }

    _isEmbedChild() {
      return document.body.classList.contains("chart-embed") && window.parent && window.parent !== window;
    }

    interceptFullscreen(id) {
      if (this._isEmbedChild()) {
        const q = new URLSearchParams(location.search);
        if (q.get("pop") === "1") return false;
        try {
          window.parent.postMessage({
            source: "liqscope-dock",
            type: "fullscreen",
            slot: q.get("slot") || id || "",
          }, location.origin);
        } catch (e) {}
        return true;
      }
      if (this.order.length < 2) return false;
      this.toggleSlotFullscreen(id || "native");
      return true;
    }

    isSlotFullscreen(id) {
      if (this._isEmbedChild()) return !!this._embedFs;
      return this._fsId === (id || "native");
    }

    toggleSlotFullscreen(id) {
      if (!validId(id) || !this.meta[id]) return;
      if (this._fsId === id) this.exitSlotFullscreen();
      else this._enterSlotFullscreen(id);
    }

    exitSlotFullscreen() {
      if (this._isEmbedChild()) {
        const q = new URLSearchParams(location.search);
        if (q.get("pop") === "1" || !this._embedFs) return false;
        this._embedFs = false;
        const expand = document.getElementById("chart-expand");
        if (expand) expand.classList.remove("active");
        try {
          window.parent.postMessage({
            source: "liqscope-dock",
            type: "fullscreen-exit",
            slot: q.get("slot") || "",
          }, location.origin);
        } catch (e) {}
        return true;
      }
      if (!this._fsId) return false;
      const id = this._fsId;
      this._fsId = null;
      const el = this.els.get(id);
      if (el) el.classList.remove("is-slot-fs");
      document.body.classList.remove("chart-slot-fs");
      this._restoreSlotHome(el, id);
      this._notifyFs(id, false);
      this._nudge();
      return true;
    }

    _enterSlotFullscreen(id) {
      if (this._fsId && this._fsId !== id) this.exitSlotFullscreen();
      this.setActive(id);
      this._fsId = id;
      const el = this.els.get(id);
      if (el) {
        el.classList.add("is-slot-fs");
        if (el.parentNode !== document.body) document.body.appendChild(el);
      }
      document.body.classList.add("chart-slot-fs");
      this._notifyFs(id, true);
      this._nudge();
    }

    _restoreSlotHome(el, id) {
      if (!el || !this.dock) return;
      const rec = this.meta[id];
      if (rec && rec.floating) return;
      let before = null;
      const idx = this.order.indexOf(id);
      for (let i = idx + 1; i < this.order.length; i++) {
        const sib = this.els.get(this.order[i]);
        if (sib && sib.parentNode === this.dock && !sib.classList.contains("is-slot-fs")) {
          before = sib;
          break;
        }
      }
      if (before) this.dock.insertBefore(el, before);
      else if (el.parentNode !== this.dock) this.dock.appendChild(el);
    }

    _notifyFs(id, on) {
      if (id === "native") {
        const btn = document.getElementById("chart-expand");
        if (btn) btn.classList.toggle("active", !!on);
        return;
      }
      const el = this.els.get(id);
      const frame = el && el.querySelector("iframe");
      try {
        if (frame && frame.contentWindow) {
          frame.contentWindow.postMessage({ source: "liqscope-dock", type: "fullscreen-state", on: !!on }, location.origin);
        }
      } catch (e) {}
    }

    _needsLayout() {
      if (this.order.length > 1) return true;
      const n = this.meta.native;
      return !!(n && (n.floating || n.popped));
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
        back.addEventListener("click", () => this.dockBack("native"));
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
        shareW: 1,
        shareH: 1,
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
      if (this._fsId === id) this.exitSlotFullscreen();
      const el = this.els.get(id);
      if (el) {
        const frame = el.querySelector("iframe");
        if (frame) frame.src = "about:blank";
        el.remove();
      }
      this._closePop(id);
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
      if (this._fsId === id) this.exitSlotFullscreen();
      rec.popped = false;
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
      const changed = this.activeId !== id;
      this.activeId = id;
      this.els.forEach((el, pid) => {
        el.classList.toggle("is-active", pid === id);
        el.classList.toggle("is-tab-on", pid === id);
      });
      const el = this.els.get(id);
      if (el && el.classList.contains("chart-floating")) el.style.zIndex = "78";
      this._paintTabs();
      this._paintTabChrome();
      if (changed) {
        this._save();
        this._nudge();
      }
    }

    layout() {
      if (!this._needsLayout()) {
        this._unwrap();
        return;
      }
      this._ensureDock();
      this.order.forEach((id) => this._ensureSlot(id));
      this._clearSplitChrome();
      const split = this.view !== "tabs" && this._dockedIds().length > 1;
      if (split) this._layoutSplit();
      else {
        this.order.forEach((id) => {
          const rec = this.meta[id];
          const el = this.els.get(id);
          if (!rec || !el) return;
          if (rec.floating) this._placeFloat(el, rec);
          else this._placeDock(el, rec);
          this._paintPopped(id);
        });
      }
      this._paintActive();
      this._applyView();
      this._syncPlaceholder();
      this._paintNativeBack();
      this._nudge();
    }

    _dockedIds() {
      return this.order.filter((id) => this.meta[id] && !this.meta[id].floating);
    }

    _share(rec, key) {
      const n = Number(rec && rec[key]);
      return n > 0 ? n : 1;
    }

    _splitRows() {
      const ids = this._dockedIds();
      if (this.cols === 3 && ids.length === 3) return [[ids[0], ids[1]], [ids[2]]];
      const cols = this.cols === 1 ? 1 : 2;
      const rows = [];
      for (let i = 0; i < ids.length; i += cols) rows.push(ids.slice(i, i + cols));
      return rows;
    }

    _clearSplitChrome() {
      if (!this.dock) return;
      Array.from(this.dock.querySelectorAll(".chart-slot")).forEach((s) => {
        if (s.parentNode !== this.dock) this.dock.appendChild(s);
      });
      Array.from(this.dock.children).forEach((ch) => {
        if (!ch.classList.contains("chart-slot")) ch.remove();
      });
      this.dock.classList.remove("is-split");
      this.placeholder = null;
    }

    _layoutSplit() {
      const rows = this._splitRows();
      this.order.forEach((id) => {
        const rec = this.meta[id];
        const el = this.els.get(id);
        if (!rec || !el || !rec.floating) return;
        this._placeFloat(el, rec);
        this._paintPopped(id);
      });
      rows.forEach((row, ri) => {
        if (ri > 0) {
          const bar = document.createElement("div");
          bar.className = "chart-split-h";
          bar.title = "Потяни край, чтобы сжать или растянуть графики сверху и снизу";
          bar.addEventListener("pointerdown", (e) => this._onSplitDown(e, "h", ri));
          this.dock.appendChild(bar);
        }
        const rowEl = document.createElement("div");
        rowEl.className = "chart-split-row";
        rowEl.dataset.row = String(ri);
        const h = row.reduce((m, id) => Math.max(m, this._share(this.meta[id], "shareH")), 1);
        rowEl.style.flexGrow = String(h);
        rowEl.style.flexBasis = "0px";
        row.forEach((id, ci) => {
          if (ci > 0) {
            const bar = document.createElement("div");
            bar.className = "chart-split-v";
            bar.title = "Потяни край, чтобы сжать или растянуть соседние графики";
            bar.addEventListener("pointerdown", (e) => this._onSplitDown(e, "v", row[ci - 1], id));
            rowEl.appendChild(bar);
          }
          const cell = document.createElement("div");
          cell.className = "chart-split-cell";
          cell.dataset.id = id;
          cell.style.flexGrow = String(this._share(this.meta[id], "shareW"));
          cell.style.flexBasis = "0px";
          const el = this.els.get(id);
          this._placeDock(el, this.meta[id], cell);
          this._paintPopped(id);
          rowEl.appendChild(cell);
        });
        this.dock.appendChild(rowEl);
      });
      this.dock.classList.add("is-split");
    }

    _onSplitDown(e, kind, a, b) {
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      let start = null;
      if (kind === "v") {
        const left = this.dock.querySelector('.chart-split-cell[data-id="' + a + '"]');
        const right = this.dock.querySelector('.chart-split-cell[data-id="' + b + '"]');
        if (!left || !right) return;
        start = {
          kind: "v", a: a, b: b, left: left, right: right,
          sx: e.clientX,
          aw: left.getBoundingClientRect().width,
          bw: right.getBoundingClientRect().width,
        };
      } else {
        const rows = Array.from(this.dock.querySelectorAll(".chart-split-row"));
        const top = rows[a - 1];
        const bot = rows[a];
        if (!top || !bot) return;
        start = {
          kind: "h", row: a - 1, top: top, bot: bot,
          sy: e.clientY,
          ah: top.getBoundingClientRect().height,
          bh: bot.getBoundingClientRect().height,
        };
      }
      this._split = start;
      document.body.classList.add("chart-dock-dragging");
      const move = (ev) => this._onSplitMove(ev);
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        this._onSplitEnd();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    }

    _onSplitMove(e) {
      const s = this._split;
      if (!s) return;
      const min = 120;
      if (s.kind === "v") {
        let aw = s.aw + (e.clientX - s.sx);
        let bw = s.bw - (e.clientX - s.sx);
        if (aw < min) { bw -= min - aw; aw = min; }
        if (bw < min) { aw -= min - bw; bw = min; }
        if (aw < min || bw < min) return;
        s.left.style.flexGrow = String(aw);
        s.right.style.flexGrow = String(bw);
        s.awNow = aw;
        s.bwNow = bw;
      } else {
        let ah = s.ah + (e.clientY - s.sy);
        let bh = s.bh - (e.clientY - s.sy);
        if (ah < min) { bh -= min - ah; ah = min; }
        if (bh < min) { ah -= min - bh; bh = min; }
        if (ah < min || bh < min) return;
        s.top.style.flexGrow = String(ah);
        s.bot.style.flexGrow = String(bh);
        s.ahNow = ah;
        s.bhNow = bh;
      }
      if (!s.nudged || Date.now() - s.nudged > 90) {
        s.nudged = Date.now();
        this._nudge();
      }
    }

    _onSplitEnd() {
      const s = this._split;
      this._split = null;
      document.body.classList.remove("chart-dock-dragging");
      if (!s) return;
      if (s.kind === "v" && s.awNow) {
        if (this.meta[s.a]) this.meta[s.a].shareW = s.awNow;
        if (this.meta[s.b]) this.meta[s.b].shareW = s.bwNow;
      } else if (s.kind === "h" && s.ahNow) {
        const rows = this._splitRows();
        (rows[s.row] || []).forEach((id) => { if (this.meta[id]) this.meta[id].shareH = s.ahNow; });
        (rows[s.row + 1] || []).forEach((id) => { if (this.meta[id]) this.meta[id].shareH = s.bhNow; });
      }
      this._save();
      this._nudge();
    }

    _ensureDock() {
      if (this.dock) return;
      this.wrap = document.createElement("div");
      this.wrap.className = "chart-dock-wrap";
      const tools = document.createElement("div");
      tools.className = "chart-dock-tools";
      const modes = document.createElement("div");
      modes.className = "chart-dock-modes";
      [["tabs", "Вкладки", "Один график на весь терминал. Остальные — вкладками, переключение по клику."],
        ["grid", "Мультиэкран", "Несколько графиков сразу, сеткой."]].forEach((pair) => {
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.view = pair[0];
        b.textContent = pair[1];
        b.title = pair[2];
        b.addEventListener("click", () => this.setView(pair[0]));
        modes.appendChild(b);
      });
      const gridctl = document.createElement("div");
      gridctl.className = "chart-dock-gridctl";
      const label = document.createElement("span");
      label.textContent = "Сетка";
      gridctl.appendChild(label);
      [1, 2, 3, 4].forEach((n) => {
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.cols = String(n);
        b.textContent = "×" + n;
        b.title = n === 1 ? "Столбиком" : n === 2 ? "В ряд по два" : n === 3 ? "Два сверху, один снизу" : "Сетка 2×2";
        b.addEventListener("click", () => this.setCols(n));
        gridctl.appendChild(b);
      });
      const addBtn = document.createElement("button");
      addBtn.type = "button";
      addBtn.className = "chart-dock-add";
      addBtn.textContent = "＋ График";
      addBtn.title = "Добавить такой же график";
      addBtn.addEventListener("click", () => this.addChart());
      this.tabs = document.createElement("div");
      this.tabs.className = "chart-dock-tabs";
      tools.appendChild(modes);
      tools.appendChild(gridctl);
      tools.appendChild(addBtn);
      tools.appendChild(this.tabs);
      this.tools = tools;
      this.dock = document.createElement("div");
      this.dock.id = "chart-dock";
      this.dock.className = "chart-dock cols-" + this.cols;
      this.wrap.appendChild(tools);
      this.wrap.appendChild(this.dock);
      this.section.classList.add("has-chart-dock");
      this.section.insertBefore(this.wrap, this.section.firstChild);
      this._paintCols();
    }

    setCols(n) {
      n = Number(n);
      if ([1, 2, 3, 4].indexOf(n) === -1) n = 2;
      this.cols = n;
      if (this.dock) {
        this.dock.classList.remove("cols-1", "cols-2", "cols-3", "cols-4");
        this.dock.classList.add("cols-" + n);
      }
      this._paintCols();
      if (this.dock) this.layout();
      this._save();
    }

    _paintCols() {
      if (!this.tools) return;
      this.tools.querySelectorAll("button[data-cols]").forEach((b) => {
        b.classList.toggle("active", Number(b.dataset.cols) === this.cols);
      });
    }

    setView(view) {
      this.view = view === "tabs" ? "tabs" : "grid";
      if (this._fsId) this.exitSlotFullscreen();
      if (this.dock) this.layout();
      else this._applyView();
      this._save();
    }

    _applyView() {
      if (this.wrap) {
        this.wrap.classList.toggle("view-tabs", this.view === "tabs");
        this.wrap.classList.toggle("view-grid", this.view !== "tabs");
      }
      this._paintModes();
      this._paintTabs();
      this._paintTabChrome();
    }

    _paintTabChrome() {
      const embedTab = this.view === "tabs" && this.activeId && this.activeId !== "native";
      document.body.classList.toggle("chart-tab-embed", !!embedTab);
      const tf = document.getElementById("tf-buttons");
      if (tf) tf.title = embedTab ? "Таймфрейм этого графика переключается на его собственной панели" : "";
      this._syncEmbedChrome();
    }

    _syncEmbedChrome(onlyId) {
      const full = this.view === "tabs";
      this.order.forEach((id) => {
        if (id === "native") return;
        if (onlyId && id !== onlyId) return;
        const el = this.els.get(id);
        const frame = el && el.querySelector("iframe");
        try {
          if (frame && frame.contentWindow) {
            frame.contentWindow.postMessage({ source: "liqscope-dock", type: "chrome", full: full }, location.origin);
          }
        } catch (e) {}
      });
    }

    _paintModes() {
      if (!this.tools) return;
      this.tools.querySelectorAll("button[data-view]").forEach((b) => {
        b.classList.toggle("active", b.dataset.view === this.view);
      });
    }

    _labelOf(id) {
      if (id === "native") {
        const sym = validSymbol(window.state && window.state.chartSymbol) || "BTC_USDT";
        return pretty(sym) + " · " + this._tfLabel(this._nativeTf());
      }
      const rec = this.meta[id];
      if (!rec) return id;
      return pretty(rec.symbol) + " · " + this._tfLabel(rec.tf);
    }

    _paintTabs() {
      if (!this.tabs) return;
      const existing = Array.from(this.tabs.querySelectorAll(".chart-dock-tab"));
      const same = existing.length === this.order.length && existing.every((el, i) => el.dataset.id === this.order[i]);
      if (same) {
        existing.forEach((el) => {
          const label = el.querySelector(".chart-dock-tab-label");
          if (label) label.textContent = this._labelOf(el.dataset.id);
          el.classList.toggle("is-on", el.dataset.id === this.activeId);
        });
        return;
      }
      this.tabs.textContent = "";
      this.order.forEach((id) => {
        const tab = document.createElement("button");
        tab.type = "button";
        tab.className = "chart-dock-tab" + (id === this.activeId ? " is-on" : "");
        tab.dataset.id = id;
        tab.title = "Показать " + this._labelOf(id);
        const label = document.createElement("span");
        label.className = "chart-dock-tab-label";
        label.textContent = this._labelOf(id);
        tab.appendChild(label);
        if (id !== "native") {
          const x = document.createElement("span");
          x.className = "chart-dock-tab-x";
          x.textContent = "✕";
          x.title = "Закрыть этот график";
          x.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.closeChart(id);
          });
          tab.appendChild(x);
        }
        tab.addEventListener("click", () => this.setActive(id));
        this.tabs.appendChild(tab);
      });
      const add = document.createElement("button");
      add.type = "button";
      add.className = "chart-dock-tab-add";
      add.textContent = "＋";
      add.title = "Добавить график";
      add.addEventListener("click", () => this.addChart());
      this.tabs.appendChild(add);
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
      grip.title = "Потяни, чтобы таскать окно по терминалу. Отпусти над сеткой — вернуть.";
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
      floatBtn.title = "Оторвать и двигать по терминалу";
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
      const prev = this._moveBtn(id, -1);
      const next = this._moveBtn(id, 1);
      bar.appendChild(grip);
      bar.appendChild(prev);
      bar.appendChild(title);
      bar.appendChild(tfSel);
      bar.appendChild(next);
      bar.appendChild(floatBtn);
      bar.appendChild(dockBtn);
      bar.appendChild(closeBtn);
      const wrap = document.createElement("div");
      wrap.className = "chart-slot-frame-wrap";
      const frame = document.createElement("iframe");
      frame.className = "chart-slot-frame";
      frame.title = "График " + pretty(rec.symbol);
      frame.src = this._embedUrl(rec, id);
      wrap.appendChild(frame);
      const note = this._popNote(id);
      el.appendChild(bar);
      el.appendChild(wrap);
      el.appendChild(note);
      grip.addEventListener("pointerdown", (e) => this._onGrip(id, grip, e));
      bar.addEventListener("pointerdown", (e) => {
        if (e.target.closest("select, button")) return;
        this._onGrip(id, bar, e);
      });
      tfSel.addEventListener("change", () => this._setEmbedTf(id, tfSel.value));
      floatBtn.addEventListener("click", () => this.floatChart(id));
      dockBtn.addEventListener("click", () => this.dockBack(id));
      closeBtn.addEventListener("click", () => this.closeChart(id));
      bar.addEventListener("pointerdown", () => this.setActive(id));
      frame.addEventListener("load", () => {
        let framed = false;
        try { framed = !!(frame.contentDocument && frame.contentDocument.getElementById("tv-chart-container")); } catch (err) { framed = false; }
        if (!framed && frame.src && frame.src.indexOf("embed=1") !== -1) {
          title.textContent = "График не открылся в окне";
        }
        this._hookFrame(id, frame);
        this._syncEmbedChrome(id);
      });
      this.els.set(id, el);
      return el;
    }

    _moveBtn(id, dir) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chart-slot-move";
      b.textContent = dir < 0 ? "‹" : "›";
      b.title = dir < 0 ? "Сдвинуть левее / выше" : "Сдвинуть правее / ниже";
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        this.moveChart(id, dir);
      });
      return b;
    }

    _popNote(id) {
      const note = document.createElement("div");
      note.className = "chart-slot-popnote";
      const text = document.createElement("div");
      text.textContent = "График открыт в отдельном окне. Его можно перетащить за пределы браузера.";
      const back = document.createElement("button");
      back.type = "button";
      back.className = "chart-tear-back";
      back.textContent = "Вернуть в терминал";
      back.addEventListener("click", () => this.dockBack(id));
      note.appendChild(text);
      note.appendChild(back);
      return note;
    }

    moveChart(id, dir) {
      if (!validId(id)) return;
      const i = this.order.indexOf(id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= this.order.length) return;
      const tmp = this.order[i];
      this.order[i] = this.order[j];
      this.order[j] = tmp;
      this.layout();
      this._save();
    }

    _wrapNative() {
      let el = this.els.get("native");
      if (el) return el;
      el = document.createElement("div");
      el.className = "chart-slot chart-slot-native";
      el.dataset.id = "native";
      const bar = document.createElement("div");
      bar.className = "chart-slot-bar";
      const grip = document.createElement("button");
      grip.type = "button";
      grip.className = "chart-slot-grip";
      grip.textContent = "⋮⋮";
      grip.title = "Потяни, чтобы таскать окно по терминалу. Отпусти над сеткой — вернуть.";
      const title = document.createElement("span");
      title.className = "chart-slot-title";
      title.textContent = "Основной";
      const floatBtn = document.createElement("button");
      floatBtn.type = "button";
      floatBtn.className = "chart-slot-float";
      floatBtn.textContent = "⧉";
      floatBtn.title = "Оторвать и двигать по терминалу";
      const dockBtn = document.createElement("button");
      dockBtn.type = "button";
      dockBtn.className = "chart-slot-dock";
      dockBtn.textContent = "Вернуть";
      dockBtn.hidden = true;
      bar.appendChild(grip);
      bar.appendChild(this._moveBtn("native", -1));
      bar.appendChild(title);
      bar.appendChild(this._moveBtn("native", 1));
      bar.appendChild(floatBtn);
      bar.appendChild(dockBtn);
      const body = document.createElement("div");
      body.className = "chart-slot-body";
      el.appendChild(bar);
      el.appendChild(body);
      el.appendChild(this._popNote("native"));
      body.appendChild(this.header);
      body.appendChild(this.stack);
      grip.addEventListener("pointerdown", (e) => this._onGrip("native", grip, e));
      floatBtn.addEventListener("click", () => this.floatChart("native"));
      dockBtn.addEventListener("click", () => this.dockBack("native"));
      this.els.set("native", el);
      el.addEventListener("pointerdown", () => this.setActive("native"));
      return el;
    }

    _placeDock(el, rec, parent) {
      if (el.classList.contains("is-slot-fs")) {
        if (el.parentNode !== document.body) document.body.appendChild(el);
        rec.floating = false;
        return;
      }
      el.classList.remove("chart-floating");
      el.style.left = "";
      el.style.top = "";
      el.style.width = "";
      el.style.height = "";
      el.style.zIndex = "";
      (parent || this.dock).appendChild(el);
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
      const shell = this.wrap || this.dock;
      if (shell && shell.parentNode) shell.parentNode.removeChild(shell);
      this.wrap = null;
      this.dock = null;
      this.tools = null;
      this.tabs = null;
      this.placeholder = null;
      this._fsId = null;
      document.body.classList.remove("chart-slot-fs");
      document.body.classList.remove("chart-tab-embed");
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
      const n = this.meta.native;
      back.hidden = !(n && (n.floating || n.popped));
    }

    popOut(id) {
      // Отдельная вкладка браузера больше не открывается: окно едет по терминалу.
      this._closePop(id);
      const rec = this.meta[id];
      if (rec) rec.popped = false;
      this.floatChart(id);
    }

    dockBack(id) {
      const rec = this.meta[id];
      if (!rec) return;
      this._closePop(id);
      rec.popped = false;
      rec.floating = false;
      this.activeId = id;
      this.layout();
      this._save();
      this._nudge();
    }

    _closePop(id) {
      const win = this._pops.get(id);
      this._pops.delete(id);
      if (win && !win.closed) {
        try { win.close(); } catch (e) {}
      }
    }

    _popUrl(id) {
      const rec = this.meta[id] || {};
      const symbol = id === "native"
        ? (validSymbol(window.state && window.state.chartSymbol) || "BTC_USDT")
        : (rec.symbol || "BTC_USDT");
      const tf = id === "native" ? this._nativeTf() : (rec.tf || 5);
      const u = new URL("/terminal", window.location.origin);
      u.searchParams.set("embed", "1");
      u.searchParams.set("pop", "1");
      u.searchParams.set("slot", id);
      u.searchParams.set("symbol", symbol);
      u.searchParams.set("tf", String(tf || 5));
      return u.pathname + u.search;
    }

    _paintPopped(id) {
      const el = this.els.get(id);
      const rec = this.meta[id];
      if (!el || !rec) return;
      el.classList.toggle("is-popped", !!rec.popped);
      if (id === "native") return;
      const frame = el.querySelector("iframe");
      if (!frame) return;
      if (rec.popped) {
        if (frame.getAttribute("src") && frame.getAttribute("src").indexOf("embed=1") !== -1) {
          frame.dataset.dockSrc = frame.getAttribute("src");
        }
        if (frame.getAttribute("src") !== "about:blank") frame.src = "about:blank";
      } else if (frame.dataset.dockSrc && String(frame.getAttribute("src") || "").indexOf("embed=1") === -1) {
        frame.src = frame.dataset.dockSrc;
      }
    }

    _paintActive() {
      this.els.forEach((el, id) => {
        el.classList.toggle("is-active", id === this.activeId);
        el.classList.toggle("is-tab-on", id === this.activeId);
      });
      this._paintTabs();
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
        startedFloating: !!rec.floating,
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
      const rec = this.meta[d.id];
      if (!rec) return;
      const outside = this._outsideDock(e.clientX, e.clientY);
      if (d.startedFloating || outside) {
        if (!rec.floating) {
          rec.floating = true;
          rec.popped = false;
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
        if (this.dock) this.dock.classList.toggle("is-drop", outside && !d.startedFloating);
      } else {
        if (rec.floating) {
          rec.floating = false;
          this.layout();
        }
        if (this.dock) {
          this.dock.classList.add("is-drop");
          this._markDrop(d.id, e.clientX, e.clientY);
        }
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
      document.body.classList.remove("chart-dock-pop-ready");
      if (this.dock) {
        this.dock.classList.remove("is-drop");
        this.dock.querySelectorAll(".chart-slot").forEach((s) => s.classList.remove("is-drop-target"));
      }
      if (!d || !d.moved) return;
      const rec = this.meta[d.id];
      if (!rec) return;
      if (d.startedFloating) {
        rec.floating = true;
        rec.popped = false;
        rec.x = e.clientX - d.offX;
        rec.y = e.clientY - d.offY;
        this._readFloatSize(d.id);
      } else if (this._outsideDock(e.clientX, e.clientY)) {
        rec.floating = true;
        rec.popped = false;
        rec.x = e.clientX - d.offX;
        rec.y = e.clientY - d.offY;
        this._readFloatSize(d.id);
      } else {
        rec.floating = false;
        this._reorderAt(d.id, e.clientX, e.clientY);
      }
      this.layout();
      this._save();
      this._nudge();
    }

    _outsideDock(x, y) {
      const box = this.section.getBoundingClientRect();
      return x < box.left - 12 || x > box.right + 12 || y < box.top - 12 || y > box.bottom + 12;
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
      this._paintTabs();
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
      const kick = () => {
        try { window.dispatchEvent(new Event("resize")); } catch (e) {}
        this.order.forEach((id) => {
          const el = this.els.get(id);
          const frame = el && el.querySelector("iframe");
          try {
            if (frame && frame.contentWindow) frame.contentWindow.dispatchEvent(new Event("resize"));
          } catch (err) {}
        });
      };
      setTimeout(kick, 40);
      setTimeout(kick, 280);
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
              shareW: this._share(rec, "shareW"),
              shareH: this._share(rec, "shareH"),
            };
          }).filter(Boolean);
          localStorage.setItem(KEY, JSON.stringify({ v: 3, cols: this.cols, view: this.view, activeId: this.activeId, charts: charts }));
        } catch (e) {}
      }, 180);
    }

    _restore() {
      let raw = null;
      try { raw = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { raw = null; }
      if (!raw || !Array.isArray(raw.charts)) return;
      if ([1, 2, 3, 4].indexOf(Number(raw.cols)) !== -1) this.cols = Number(raw.cols);
      if (raw.view === "tabs" || raw.view === "grid") this.view = raw.view;
      const order = [];
      const meta = {};
      raw.charts.slice(0, MAX).forEach((c) => {
        if (!c || !validId(c.id) || meta[c.id]) return;
        if (c.id === "native") {
          meta.native = {
            id: "native", kind: "native", floating: false,
            x: Number(c.x) || 48, y: Number(c.y) || 64,
            w: Number(c.w) || 760, h: Number(c.h) || 540,
            shareW: Number(c.shareW) > 0 ? Number(c.shareW) : 1,
            shareH: Number(c.shareH) > 0 ? Number(c.shareH) : 1,
          };
          order.push("native");
          return;
        }
        const sym = validSymbol(c.symbol);
        const tf = validTf(c.tf) || 5;
        if (!sym || c.kind !== "embed") return;
        meta[c.id] = {
          id: c.id, kind: "embed", symbol: sym, tf: tf, floating: false,
          x: Number(c.x) || 72, y: Number(c.y) || 72,
          w: Number(c.w) || 760, h: Number(c.h) || 540,
          shareW: Number(c.shareW) > 0 ? Number(c.shareW) : 1,
          shareH: Number(c.shareH) > 0 ? Number(c.shareH) : 1,
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
