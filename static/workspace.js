/**
 * WorkspaceManager — manages multi-chart workspace.
 * Handles persistence, drag&drop reorder, detach/attach via BroadcastChannel + localStorage fallback,
 * and routes WS events to panels.
 */

class WorkspaceManager {
  constructor({ container, maxPanels = 6, storageKey = 'liqscope_terminal_workspace_v1' } = {}) {
    this.container = container; // element where workspace root will be created
    this.maxPanels = maxPanels;
    this.storageKey = storageKey;
    this.panels = new Map(); // id -> ChartPanel
    this.order = []; // array of ids in display order
    this.activePanelId = null;
    this.workspaceId = null;
    this._saveTimer = null;
    this._bc = null; // BroadcastChannel
    this._isPanelMode = false;
    this._panelModeId = null;
    this._detachedWindows = new Map(); // panelId -> window ref

    this._initBroadcast();
    this._parseUrlMode();
  }

  _parseUrlMode() {
    try {
      const params = new URLSearchParams(window.location.search);
      const mode = params.get('mode');
      if (mode === 'panel') {
        this._isPanelMode = true;
        this._panelModeId = params.get('panel') || null;
        this.workspaceId = params.get('workspace') || null;
        document.body.classList.add('panel-mode');
      }
    } catch {}
  }

  _initBroadcast() {
    try {
      if ('BroadcastChannel' in window) {
        this._bc = new BroadcastChannel('liqscope_terminal_workspace');
        this._bc.onmessage = (ev) => this._onBroadcastMessage(ev.data);
      }
    } catch {}
    // fallback storage event
    window.addEventListener('storage', (e) => {
      if (e.key === this.storageKey || e.key === 'liqscope_workspace_msg') {
        try {
          const data = JSON.parse(e.newValue || '{}');
          if (data && data.type) this._onBroadcastMessage(data);
        } catch {}
      }
    });
  }

  _broadcast(msg) {
    if (!msg || typeof msg !== 'object') return;
    msg = Object.assign({ workspaceId: this.workspaceId, ts: Date.now() }, msg);
    // validate
    if (msg.panelId && !/^[a-zA-Z0-9_-]{1,64}$/.test(msg.panelId)) return;
    try {
      if (this._bc) this._bc.postMessage(msg);
    } catch {}
    // fallback via localStorage
    try {
      localStorage.setItem('liqscope_workspace_msg', JSON.stringify(msg));
      // clear quickly to allow same message again
      setTimeout(() => {
        try { localStorage.removeItem('liqscope_workspace_msg'); } catch {}
      }, 100);
    } catch {}
  }

  _onBroadcastMessage(msg) {
    if (!msg || typeof msg !== 'object') return;
    // validate workspace
    if (msg.workspaceId && this.workspaceId && msg.workspaceId !== this.workspaceId) return;
    switch (msg.type) {
      case 'PANEL_STATE_UPDATE':
        if (this._isPanelMode) return; // detached window doesn't need to update from others? but could
        if (msg.panel && msg.panel.id) {
          const panel = this.panels.get(msg.panel.id);
          if (panel) {
            // avoid loop: if we already have same state, ignore
            if (msg.panel.symbol && msg.panel.symbol !== panel.symbol) panel.setSymbol(msg.panel.symbol);
            if (msg.panel.timeframe && Number(msg.panel.timeframe) !== Number(panel.timeframe)) panel.setTimeframe(msg.panel.timeframe);
            if (msg.panel.layers) panel.setLayers(msg.panel.layers);
          }
        }
        break;
      case 'PANEL_DETACHED':
        if (!this._isPanelMode) {
          const p = this.panels.get(msg.panelId);
          if (p) p.setDetached(true);
        }
        break;
      case 'PANEL_ATTACHED':
        if (!this._isPanelMode) {
          const p = this.panels.get(msg.panelId);
          if (p) {
            p.setDetached(false);
            // if window ref exists, try close
            const w = this._detachedWindows.get(msg.panelId);
            if (w && !w.closed) { try { w.close(); } catch {} }
            this._detachedWindows.delete(msg.panelId);
          }
        } else {
          // detached window received attach request -> close self
          if (msg.panelId === this._panelModeId) {
            try { window.close(); } catch {}
            // fallback: mark as attached in storage so main picks up
            try {
              const ws = this.loadWorkspaceRaw();
              if (ws && ws.panels) {
                ws.panels.forEach(pl => { if (pl.id===msg.panelId) pl.detached=false; });
                localStorage.setItem(this.storageKey, JSON.stringify(ws));
              }
            } catch {}
          }
        }
        break;
      case 'PANEL_CLOSED':
        if (!this._isPanelMode) {
          // if panel closed in detached window, remove from workspace
          if (msg.panelId && this.panels.has(msg.panelId)) {
            this.removePanel(msg.panelId, { broadcast: false });
          }
        }
        break;
      case 'WORKSPACE_SNAPSHOT_REQUEST':
        if (!this._isPanelMode) {
          this._broadcast({ type: 'WORKSPACE_SNAPSHOT_RESPONSE', workspace: this.serialize() });
        }
        break;
      case 'WORKSPACE_SNAPSHOT_RESPONSE':
        if (this._isPanelMode) {
          // detached window received workspace snapshot, use it to init single panel
          if (msg.workspace && Array.isArray(msg.workspace.panels)) {
            const target = msg.workspace.panels.find(p => p.id === this._panelModeId);
            if (target) {
              // if we haven't mounted yet, mount with target state
              if (!this.panels.has(target.id)) {
                this._mountSinglePanel(target);
              }
            }
          }
        }
        break;
    }
  }

  _generateWorkspaceId() {
    return 'ws_' + Math.random().toString(36).slice(2, 10);
  }

  _generatePanelId() {
    return 'p_' + Math.random().toString(36).slice(2, 8);
  }

  loadWorkspaceRaw() {
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || typeof data !== 'object') return null;
      return data;
    } catch { return null; }
  }

  loadWorkspace() {
    const raw = this.loadWorkspaceRaw();
    if (!raw) {
      this.workspaceId = this._generateWorkspaceId();
      return { workspaceId: this.workspaceId, panels: [], activePanelId: null };
    }
    this.workspaceId = raw.workspaceId || this._generateWorkspaceId();
    return raw;
  }

  saveWorkspace() {
    // debounced
    if (this._saveTimer) clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(() => {
      try {
        const data = this.serialize();
        localStorage.setItem(this.storageKey, JSON.stringify(data));
      } catch {}
    }, 600);
  }

  serialize() {
    return {
      workspaceId: this.workspaceId,
      panels: this.order.map(id => {
        const p = this.panels.get(id);
        return p ? p.serialize() : null;
      }).filter(Boolean),
      activePanelId: this.activePanelId,
      v: 1,
      ts: Date.now(),
    };
  }

  _ensureDom() {
    if (!this.container) return null;
    let root = this.container.querySelector('.workspace-root');
    if (!root) {
      root = document.createElement('div');
      root.className = 'workspace-root';
      // toolbar
      const toolbar = document.createElement('div');
      toolbar.className = 'workspace-toolbar';
      toolbar.innerHTML = `
        <span class="ws-title">Workspace</span>
        <button class="ws-btn primary" data-action="add">+ Add chart</button>
        <button class="ws-btn" data-action="layout-1">1x</button>
        <button class="ws-btn" data-action="layout-2">2x</button>
        <button class="ws-btn" data-action="layout-4">2x2</button>
        <span class="ws-hint">Drag tabs to reorder • Detach to separate window • Per-panel layers</span>
      `;
      root.appendChild(toolbar);
      const tabs = document.createElement('div');
      tabs.className = 'workspace-tabs';
      root.appendChild(tabs);
      const grid = document.createElement('div');
      grid.className = 'workspace-grid cols-2';
      root.appendChild(grid);
      this.container.appendChild(root);

      // toolbar events
      toolbar.querySelector('[data-action="add"]').addEventListener('click', () => this.addPanel());
      toolbar.querySelector('[data-action="layout-1"]').addEventListener('click', () => this.setLayout(1));
      toolbar.querySelector('[data-action="layout-2"]').addEventListener('click', () => this.setLayout(2));
      toolbar.querySelector('[data-action="layout-4"]').addEventListener('click', () => this.setLayout(4));

      // tabs drag&drop
      tabs.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
      });
      tabs.addEventListener('drop', (e) => {
        e.preventDefault();
        const draggedId = e.dataTransfer.getData('text/plain');
        const target = e.target.closest('.ws-tab');
        if (!draggedId || !target) return;
        const targetId = target.dataset.panelId;
        if (draggedId === targetId) return;
        this.movePanel(draggedId, this.order.indexOf(targetId));
      });
    }
    return root;
  }

  setLayout(cols) {
    const root = this._ensureDom();
    if (!root) return;
    const grid = root.querySelector('.workspace-grid');
    if (!grid) return;
    grid.className = 'workspace-grid cols-' + cols;
    // save preference
    try { localStorage.setItem('liqscope_workspace_layout', String(cols)); } catch {}
  }

  _renderTabs() {
    const root = this._ensureDom();
    if (!root) return;
    const tabs = root.querySelector('.workspace-tabs');
    if (!tabs) return;
    tabs.innerHTML = '';
    this.order.forEach(id => {
      const panel = this.panels.get(id);
      if (!panel) return;
      const tab = document.createElement('div');
      tab.className = 'ws-tab' + (id===this.activePanelId ? ' active' : '');
      tab.dataset.panelId = id;
      tab.draggable = true;
      tab.innerHTML = `
        <span>${this._esc(panel.symbol.replace('_','/'))} ${panel.timeframe}m</span>
        <button class="ws-tab-close" title="Close">✕</button>
      `;
      tab.addEventListener('click', (e) => {
        if (e.target.classList.contains('ws-tab-close')) return;
        this.setActivePanel(id);
      });
      tab.querySelector('.ws-tab-close').addEventListener('click', (e) => {
        e.stopPropagation();
        this.removePanel(id);
      });
      tab.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', id);
        e.dataTransfer.effectAllowed = 'move';
        tab.classList.add('dragging');
      });
      tab.addEventListener('dragend', () => tab.classList.remove('dragging'));
      tabs.appendChild(tab);
    });
  }

  _esc(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  mount() {
    if (this._isPanelMode) {
      this._mountDetachedMode();
      return;
    }

    const root = this._ensureDom();
    if (!root) return;
    root.classList.add('active');

    const raw = this.loadWorkspace();
    let panels = raw.panels || [];
    if (!panels.length) {
      // MVP: start with 2 panels
      panels = [
        { id: this._generatePanelId(), symbol: 'BTC_USDT', timeframe: 5, layers: { levelsEnabled: false, levelsAlertEnabled: true }, position: 0 },
        { id: this._generatePanelId(), symbol: 'ETH_USDT', timeframe: 5, layers: { levelsEnabled: false, levelsAlertEnabled: true }, position: 1 },
      ];
      this.workspaceId = raw.workspaceId || this._generateWorkspaceId();
    }

    // ensure max panels
    if (panels.length > this.maxPanels) panels = panels.slice(0, this.maxPanels);

    const grid = root.querySelector('.workspace-grid');
    grid.innerHTML = '';
    this.panels.clear();
    this.order = [];

    panels.forEach((pState, idx) => {
      const id = pState.id || this._generatePanelId();
      pState.id = id;
      pState.position = idx;
      const panel = new ChartPanel({
        id,
        container: grid,
        initialState: pState,
        onEvent: (ev) => this._onPanelEvent(ev),
      });
      panel.mount();
      this.panels.set(id, panel);
      this.order.push(id);
    });

    this.activePanelId = raw.activePanelId || (this.order[0] || null);
    this._renderTabs();
    this._updateActiveStates();

    // layout from storage
    try {
      const layout = localStorage.getItem('liqscope_workspace_layout');
      if (layout) this.setLayout(Number(layout));
    } catch {}

    this.saveWorkspace();

    // hook WS
    this._hookWs();

    // hide original single chart if workspace active
    try {
      const singleChartSection = document.querySelector('.chart-section .chart-stack');
      if (singleChartSection && this.order.length>0) {
        // keep it but collapsed? For MVP, hide single chart wrapper and show workspace
        // We'll hide via CSS class
        document.body.classList.add('workspace-active');
        const wsRoot = document.querySelector('.workspace-root');
        if (wsRoot) wsRoot.classList.add('active');
      }
    } catch {}
  }

  _mountDetachedMode() {
    // detached window: show single panel
    const container = this.container || document.body;
    let root = container.querySelector('.workspace-root');
    if (!root) {
      root = document.createElement('div');
      root.className = 'workspace-root active';
      const grid = document.createElement('div');
      grid.className = 'workspace-grid cols-1';
      root.appendChild(grid);
      container.appendChild(root);
    }
    const grid = root.querySelector('.workspace-grid');
    grid.innerHTML = '';

    // try load workspace from storage
    const raw = this.loadWorkspaceRaw();
    let targetState = null;
    if (raw && Array.isArray(raw.panels)) {
      targetState = raw.panels.find(p => p.id === this._panelModeId);
    }
    if (!targetState) {
      // request snapshot from main window
      this._broadcast({ type: 'WORKSPACE_SNAPSHOT_REQUEST' });
      // fallback: create from URL params
      const params = new URLSearchParams(window.location.search);
      const sym = params.get('symbol') || 'BTC_USDT';
      const tf = params.get('tf') || '5';
      targetState = { id: this._panelModeId || this._generatePanelId(), symbol: sym, timeframe: Number(tf), layers: {}, position: 0 };
    }

    this._mountSinglePanel(targetState, grid);

    // attach back button
    const attachBtn = document.createElement('button');
    attachBtn.textContent = '↩ Attach back';
    attachBtn.className = 'ws-btn primary';
    attachBtn.style.position = 'fixed';
    attachBtn.style.top = '8px';
    attachBtn.style.right = '8px';
    attachBtn.style.zIndex = '1000';
    attachBtn.addEventListener('click', () => {
      this._broadcast({ type: 'PANEL_ATTACHED', panelId: targetState.id });
      try { window.close(); } catch {}
    });
    document.body.appendChild(attachBtn);

    // on unload, mark as attached (or closed)
    window.addEventListener('beforeunload', () => {
      this._broadcast({ type: 'PANEL_ATTACHED', panelId: targetState.id });
    });

    this._hookWs();
  }

  _mountSinglePanel(state, grid) {
    const g = grid || (this.container.querySelector('.workspace-grid'));
    if (!g) return;
    const panel = new ChartPanel({
      id: state.id,
      container: g,
      initialState: state,
      onEvent: (ev) => this._onPanelEvent(ev),
      isDetachedMode: true,
    });
    panel.mount();
    this.panels.set(state.id, panel);
    this.order = [state.id];
    this.activePanelId = state.id;
  }

  _onPanelEvent(ev) {
    const { type, panelId, data } = ev;
    switch (type) {
      case 'closeRequested':
        this.removePanel(panelId);
        break;
      case 'detachRequested':
        this.detachPanel(panelId);
        break;
      case 'activated':
        this.setActivePanel(panelId);
        break;
      case 'stateChanged':
        this.saveWorkspace();
        // broadcast state update
        if (data) {
          this._broadcast({ type: 'PANEL_STATE_UPDATE', panelId, panel: data });
        }
        this._renderTabs();
        break;
      case 'symbolChanged':
      case 'timeframeChanged':
      case 'layersChanged':
        this.saveWorkspace();
        if (type==='symbolChanged' || type==='timeframeChanged') this._renderTabs();
        // broadcast
        const p = this.panels.get(panelId);
        if (p) this._broadcast({ type: 'PANEL_STATE_UPDATE', panelId, panel: p.serialize() });
        break;
    }
  }

  addPanel(initialState) {
    if (this.panels.size >= this.maxPanels) {
      alert(`Max ${this.maxPanels} panels. Close one to add more.`);
      return null;
    }
    const root = this._ensureDom();
    const grid = root ? root.querySelector('.workspace-grid') : null;
    if (!grid) return null;

    const id = (initialState && initialState.id) || this._generatePanelId();
    const state = Object.assign({
      id,
      symbol: 'BTC_USDT',
      timeframe: 5,
      layers: { levelsEnabled: false, levelsAlertEnabled: true },
      position: this.order.length,
    }, initialState || {}, { id });

    const panel = new ChartPanel({
      id,
      container: grid,
      initialState: state,
      onEvent: (ev) => this._onPanelEvent(ev),
    });
    panel.mount();
    this.panels.set(id, panel);
    this.order.push(id);
    this.setActivePanel(id);
    this._renderTabs();
    this.saveWorkspace();
    this._broadcast({ type: 'PANEL_STATE_UPDATE', panelId: id, panel: state });
    return panel;
  }

  removePanel(id, { broadcast = true } = {}) {
    const panel = this.panels.get(id);
    if (!panel) return;
    panel.destroy();
    this.panels.delete(id);
    this.order = this.order.filter(x => x!==id);
    if (this.activePanelId===id) {
      this.activePanelId = this.order[0] || null;
    }
    this._renderTabs();
    this._updateActiveStates();
    this.saveWorkspace();
    if (broadcast) this._broadcast({ type: 'PANEL_CLOSED', panelId: id });
    // if no panels left, add one
    if (!this.order.length && !this._isPanelMode) {
      this.addPanel();
    }
  }

  movePanel(id, newIndex) {
    const oldIdx = this.order.indexOf(id);
    if (oldIdx===-1) return;
    if (newIndex<0) newIndex=0;
    if (newIndex>=this.order.length) newIndex=this.order.length-1;
    if (oldIdx===newIndex) return;
    this.order.splice(oldIdx,1);
    this.order.splice(newIndex,0,id);
    // reorder DOM
    const root = this._ensureDom();
    const grid = root ? root.querySelector('.workspace-grid') : null;
    if (grid) {
      // re-append in order
      this.order.forEach(pid => {
        const p = this.panels.get(pid);
        if (p && p.root && p.root.parentNode===grid) {
          grid.appendChild(p.root);
        }
      });
    }
    this._renderTabs();
    this.saveWorkspace();
  }

  setActivePanel(id) {
    if (!this.panels.has(id)) return;
    this.activePanelId = id;
    this._updateActiveStates();
    this._renderTabs();
    this.saveWorkspace();
  }

  _updateActiveStates() {
    this.panels.forEach((p, pid) => {
      p.setActive(pid===this.activePanelId);
    });
  }

  detachPanel(id) {
    const panel = this.panels.get(id);
    if (!panel) return;
    const state = panel.serialize();
    // mark detached
    panel.setDetached(true);
    state.detached = true;
    this.saveWorkspace();

    const url = `/terminal?mode=panel&panel=${encodeURIComponent(id)}&workspace=${encodeURIComponent(this.workspaceId)}&symbol=${encodeURIComponent(state.symbol)}&tf=${encodeURIComponent(state.timeframe)}`;
    let win = null;
    try {
      win = window.open(url, `liqscope_panel_${id}`, 'width=900,height=600,menubar=no,toolbar=no,location=no,status=no');
    } catch {}
    if (win) {
      this._detachedWindows.set(id, win);
      // poll closed
      const iv = setInterval(() => {
        if (win.closed) {
          clearInterval(iv);
          this._detachedWindows.delete(id);
          // auto attach back
          const p = this.panels.get(id);
          if (p) p.setDetached(false);
          this._broadcast({ type: 'PANEL_ATTACHED', panelId: id });
          this.saveWorkspace();
        }
      }, 1000);
    }
    this._broadcast({ type: 'PANEL_DETACHED', panelId: id, panel: state });
  }

  attachPanel(id) {
    this._broadcast({ type: 'PANEL_ATTACHED', panelId: id });
    const panel = this.panels.get(id);
    if (panel) panel.setDetached(false);
    const win = this._detachedWindows.get(id);
    if (win && !win.closed) {
      try { win.close(); } catch {}
    }
    this._detachedWindows.delete(id);
    this.saveWorkspace();
  }

  _hookWs() {
    // hook into global WS via custom event liqscope:ws or direct ws
    // For MVP, we listen to document event and also try to wrap existing ws.onmessage
    document.addEventListener('liqscope:ws', (e) => {
      const msg = e.detail;
      this._handleWsMessage(msg);
    });
    // also try to intercept global state.prices updates via polling? simpler: listen to existing app's handleMessage via monkey patch if possible
    // We also set up a direct WS if app.js hasn't created one (detached mode)
    if (this._isPanelMode) {
      // detached mode needs its own WS to get prices
      this._connectDetachedWs();
    } else {
      // in main workspace, try to hook into existing ws by checking window.ws or app's ws
      // We'll poll for price updates via state.prices if available
      setInterval(() => {
        try {
          const prices = (window.state && window.state.prices) || {};
          for (const sym in prices) {
            const price = prices[sym];
            this.panels.forEach(p => p.onPriceUpdate(sym, price));
          }
        } catch {}
      }, 1000);
    }
  }

  _connectDetachedWs() {
    if (this._detachedWs) return;
    try {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(proto + '//' + location.host + '/ws');
      this._detachedWs = ws;
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          this._handleWsMessage(msg);
        } catch {}
      };
      ws.onclose = () => {
        setTimeout(() => { this._detachedWs=null; this._connectDetachedWs(); }, 2500);
      };
    } catch {}
  }

  _handleWsMessage(msg) {
    if (!msg || !msg.type) return;
    switch (msg.type) {
      case 'prices':
        if (msg.data) {
          for (const sym in msg.data) {
            const price = msg.data[sym];
            this.panels.forEach(p => p.onPriceUpdate(sym, price));
          }
        }
        break;
      case 'tick':
      case 'candle':
        if (msg.symbol && msg.candle) {
          this.panels.forEach(p => p.onCandleUpdate(msg.symbol, msg.tf, msg.candle));
        }
        break;
      case 'candles':
        if (msg.symbol && msg.candles) {
          // for simplicity, if panel matches symbol and tf, set candles
          this.panels.forEach(p => {
            if (p.symbol===msg.symbol && Number(p.timeframe)===Number(msg.tf || msg.timeframe)) {
              // reuse load? but we have direct data
              // convert
              const candles = msg.candles.map(c => ({
                time: Number(c.time || c.t || 0) > 1e12 ? Math.floor(Number(c.time)/1000) : Number(c.time||c.t||0),
                open: Number(c.open||c.o),
                high: Number(c.high||c.h),
                low: Number(c.low||c.l),
                close: Number(c.close||c.c),
              })).filter(b=>b.time && isFinite(b.open)).sort((a,b)=>a.time-b.time);
              p.candles = candles;
              if (p.candleSeries) {
                try { p.candleSeries.setData(candles); } catch {}
              }
              if (candles.length) p._updatePriceDisplay(candles[candles.length-1].close);
            }
          });
        }
        break;
    }
  }
}

window.WorkspaceManager = WorkspaceManager;

// Auto-init on terminal page
(function(){
  function initWorkspace() {
    try {
      const host = document.getElementById('workspace-host');
      if (!host) return;
      // check if we are in terminal
      if (!window.location.pathname.includes('/terminal')) return;

      // expose symbols getter for ChartPanel
      if (!window.LiqScopeApp) window.LiqScopeApp = {};
      window.LiqScopeApp.getSymbols = function() {
        try {
          // try to get from global state if app.js has set it
          if (window.state && Array.isArray(window.state.symbols)) return window.state.symbols;
          // fallback: fetch from API
        } catch {}
        return [];
      };

      const wm = new WorkspaceManager({ container: host, maxPanels: 6 });
      window.LiqScopeWorkspace = wm;
      // mount after a short delay to allow app.js to init symbols
      setTimeout(() => wm.mount(), 800);

      // For panel mode, mount immediately
      const params = new URLSearchParams(window.location.search);
      if (params.get('mode')==='panel') {
        // in panel mode, hide other UI quickly
        document.body.classList.add('panel-mode');
        wm.mount();
      }

      // Add CSS for workspace-active: hide single chart-stack when workspace has panels, but allow toggle
      // We keep both visible for now, but workspace takes precedence
      // Add button to toggle single vs multi?
    } catch(e) {
      console.warn('workspace init failed', e);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWorkspace);
  } else {
    initWorkspace();
  }
})();
