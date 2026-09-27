/**
 * WorkspaceManager — multi-chart workspace with single/grid modes and feed sync.
 */

class WorkspaceManager {
  constructor({ container, maxPanels = 6, storageKey = 'liqscope_terminal_workspace_v1' } = {}) {
    this.container = container;
    this.maxPanels = maxPanels;
    this.storageKey = storageKey;
    this.panels = new Map();
    this.order = [];
    this.activePanelId = null;
    this.workspaceId = null;
    this._saveTimer = null;
    this._bc = null;
    this._isPanelMode = false;
    this._panelModeId = null;
    this._detachedWindows = new Map();
    this._singleMode = false;

    this._initBroadcast();
    this._parseUrlMode();
    try {
      const sm = localStorage.getItem('liqscope_workspace_single');
      if (sm === '1') this._singleMode = true;
    } catch {}
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
    if (msg.panelId && !/^[a-zA-Z0-9_-]{1,64}$/.test(msg.panelId)) return;
    try { if (this._bc) this._bc.postMessage(msg); } catch {}
    try {
      localStorage.setItem('liqscope_workspace_msg', JSON.stringify(msg));
      setTimeout(() => { try { localStorage.removeItem('liqscope_workspace_msg'); } catch {} }, 100);
    } catch {}
  }

  _onBroadcastMessage(msg) {
    if (!msg || typeof msg !== 'object') return;
    if (msg.workspaceId && this.workspaceId && msg.workspaceId !== this.workspaceId) return;
    switch (msg.type) {
      case 'PANEL_STATE_UPDATE':
        if (this._isPanelMode) return;
        if (msg.panel && msg.panel.id) {
          const panel = this.panels.get(msg.panel.id);
          if (panel) {
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
            const w = this._detachedWindows.get(msg.panelId);
            if (w && !w.closed) { try { w.close(); } catch {} }
            this._detachedWindows.delete(msg.panelId);
          }
        } else {
          if (msg.panelId === this._panelModeId) {
            try { window.close(); } catch {}
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
        if (!this._isPanelMode && msg.panelId && this.panels.has(msg.panelId)) {
          this.removePanel(msg.panelId, { broadcast: false });
        }
        break;
      case 'WORKSPACE_SNAPSHOT_REQUEST':
        if (!this._isPanelMode) {
          this._broadcast({ type: 'WORKSPACE_SNAPSHOT_RESPONSE', workspace: this.serialize() });
        }
        break;
      case 'WORKSPACE_SNAPSHOT_RESPONSE':
        if (this._isPanelMode && msg.workspace && Array.isArray(msg.workspace.panels)) {
          const target = msg.workspace.panels.find(p => p.id === this._panelModeId);
          if (target && !this.panels.has(target.id)) {
            this._mountSinglePanel(target);
          }
        }
        break;
    }
  }

  _generateWorkspaceId() { return 'ws_' + Math.random().toString(36).slice(2, 10); }
  _generatePanelId() { return 'p_' + Math.random().toString(36).slice(2, 8); }

  loadWorkspaceRaw() {
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) return null;
      return JSON.parse(raw);
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
      singleMode: this._singleMode,
      v: 1,
      ts: Date.now(),
    };
  }

  _ensurePersistentToggle() {
    try {
      const chartSection = document.querySelector('.chart-section');
      if (!chartSection) return;
      let btn = document.getElementById('workspace-global-toggle');
      if (!btn) {
        btn = document.createElement('button');
        btn.id = 'workspace-global-toggle';
        btn.className = 'ws-global-toggle';
        btn.title = 'Toggle Multi-chart workspace';
        btn.style.cssText = 'position:absolute;top:6px;right:80px;z-index:50;background:#151d2b;border:1px solid #233044;color:#a8bdd6;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;';
        chartSection.appendChild(btn);
        btn.addEventListener('click', () => {
          const isNowMulti = !document.body.classList.contains('workspace-active');
          document.body.classList.toggle('workspace-active', isNowMulti);
          try { localStorage.setItem('liqscope_workspace_active', isNowMulti ? '1' : '0'); } catch {}
          try {
            const cs = document.querySelector('.chart-section');
            if (!isNowMulti && cs) {
              cs.classList.remove('collapsed');
              try { localStorage.setItem('liqscope.chartCollapsed', '0'); } catch {}
            }
          } catch {}
          setTimeout(() => {
            try { window.dispatchEvent(new Event('resize')); } catch {}
            this.panels.forEach(p => p.resize());
          }, 100);
          setTimeout(() => {
            try { window.dispatchEvent(new Event('resize')); } catch {}
            this.panels.forEach(p => p.resize());
          }, 400);
          this._updateGlobalToggle();
        });
      }
      this._updateGlobalToggle();
    } catch {}
  }

  _updateGlobalToggle() {
    try {
      const btn = document.getElementById('workspace-global-toggle');
      if (!btn) return;
      const isMulti = document.body.classList.contains('workspace-active');
      btn.textContent = isMulti ? 'Single chart' : 'Multi chart';
      btn.style.background = isMulti ? '#1e3a5f' : '#151d2b';
      btn.style.borderColor = isMulti ? '#2a5a9a' : '#233044';
      btn.style.color = isMulti ? '#cfe3ff' : '#a8bdd6';
      // When entering multi, ensure chart-section not collapsed to avoid small height
      if (isMulti) {
        try {
          const cs = document.querySelector('.chart-section');
          if (cs && cs.classList.contains('collapsed')) {
            cs.classList.remove('collapsed');
            try { localStorage.setItem('liqscope.chartCollapsed', '0'); } catch {}
          }
        } catch {}
      }
    } catch {}
  }

  _ensureDom() {
    if (!this.container) return null;
    this._ensurePersistentToggle();
    let root = this.container.querySelector('.workspace-root');
    if (!root) {
      root = document.createElement('div');
      root.className = 'workspace-root';
      const toolbar = document.createElement('div');
      toolbar.className = 'workspace-toolbar';
      toolbar.innerHTML = `
        <span class="ws-title">Workspace</span>
        <button class="ws-btn primary" data-action="add">+ Add chart</button>
        <button class="ws-btn" data-action="single" title="Toggle single/grid mode">⛶ Single</button>
        <button class="ws-btn" data-action="layout-1">1x</button>
        <button class="ws-btn" data-action="layout-2">2x</button>
        <button class="ws-btn" data-action="layout-4">2x2</button>
        <button class="ws-btn" data-action="toggle-single-chart" title="Show/hide original single chart">Single chart</button>
        <span class="ws-hint">Click chart → feed • Drag tabs • Detach • Per-panel layers</span>
      `;
      root.appendChild(toolbar);
      const tabs = document.createElement('div');
      tabs.className = 'workspace-tabs';
      root.appendChild(tabs);
      const grid = document.createElement('div');
      grid.className = 'workspace-grid cols-2';
      root.appendChild(grid);
      this.container.appendChild(root);

      toolbar.querySelector('[data-action="add"]').addEventListener('click', () => this.addPanel());
      toolbar.querySelector('[data-action="single"]').addEventListener('click', () => this.toggleSingleMode());
      toolbar.querySelector('[data-action="layout-1"]').addEventListener('click', () => this.setLayout(1));
      toolbar.querySelector('[data-action="layout-2"]').addEventListener('click', () => this.setLayout(2));
      toolbar.querySelector('[data-action="layout-4"]').addEventListener('click', () => this.setLayout(4));
      toolbar.querySelector('[data-action="toggle-single-chart"]').addEventListener('click', () => {
        const isNowMulti = !document.body.classList.contains('workspace-active');
        document.body.classList.toggle('workspace-active', isNowMulti);
        const btn = toolbar.querySelector('[data-action="toggle-single-chart"]');
        btn.textContent = isNowMulti ? 'Single chart' : 'Multi chart';
        try { localStorage.setItem('liqscope_workspace_active', isNowMulti ? '1' : '0'); } catch {}
        // Ensure main chart not collapsed when switching back to single
        try {
          const chartSection = document.querySelector('.chart-section');
          if (!isNowMulti && chartSection) {
            chartSection.classList.remove('collapsed');
            try { localStorage.setItem('liqscope.chartCollapsed', '0'); } catch {}
          }
        } catch {}
        // Trigger resize for both modes
        setTimeout(() => {
          try { window.dispatchEvent(new Event('resize')); } catch {}
          this.panels.forEach(p => p.resize());
          // Also try to resize main chart if exists
          try {
            if (window.LiqScopeApp && window.LiqScopeApp.resize) window.LiqScopeApp.resize();
          } catch {}
        }, 100);
        setTimeout(() => {
          try { window.dispatchEvent(new Event('resize')); } catch {}
          this.panels.forEach(p => p.resize());
        }, 400);
      });

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
    if (!this._singleMode) {
      // keep cols class for styling, but flex layout handles actual sizing
      grid.className = 'workspace-grid cols-' + cols;
      // trigger resize for all panels
      setTimeout(() => this.panels.forEach(p => p.resize()), 150);
    }
    try { localStorage.setItem('liqscope_workspace_layout', String(cols)); } catch {}
  }

  toggleSingleMode() {
    this._singleMode = !this._singleMode;
    document.body.classList.toggle('workspace-single-mode', this._singleMode);
    const root = this._ensureDom();
    if (root) {
      const grid = root.querySelector('.workspace-grid');
      if (grid) {
        if (this._singleMode) {
          grid.classList.add('single-mode');
          // hide ad in single mode to have full terminal like before
          document.body.classList.add('workspace-single-mode');
        } else {
          grid.classList.remove('single-mode');
          document.body.classList.remove('workspace-single-mode');
          try {
            const layout = localStorage.getItem('liqscope_workspace_layout') || '2';
            grid.className = 'workspace-grid cols-' + layout;
          } catch {}
        }
      }
    }
    this._applySingleModeVisibility();
    try { localStorage.setItem('liqscope_workspace_single', this._singleMode ? '1' : '0'); } catch {}
    this.saveWorkspace();
    const btn = root ? root.querySelector('[data-action="single"]') : null;
    if (btn) btn.textContent = this._singleMode ? '⊞ Grid' : '⛶ Single';
    // Resize after layout change — important for filling without gaps
    setTimeout(() => {
      this.panels.forEach(p => p.resize());
      const active = this.panels.get(this.activePanelId);
      if (active) active.resize();
    }, 100);
    setTimeout(() => this.panels.forEach(p => p.resize()), 400);
  }

  _applySingleModeVisibility() {
    const root = this._ensureDom();
    if (!root) return;
    if (this._singleMode) {
      this.panels.forEach((p, id) => {
        if (p.root) {
          p.root.style.display = (id === this.activePanelId) ? 'flex' : 'none';
          if (id === this.activePanelId) {
            p.root.style.flex = '1 1 100%';
            p.root.style.width = '100%';
            p.root.style.height = '100%';
            p.root.style.minHeight = '0';
            p.root.style.resize = 'none';
          }
        }
      });
      const active = this.panels.get(this.activePanelId);
      if (active && active.root) {
        setTimeout(() => active.resize(), 100);
        setTimeout(() => active.resize(), 400);
      }
    } else {
      this.panels.forEach(p => {
        if (p.root) {
          p.root.style.display = 'flex';
          p.root.style.flex = '';
          p.root.style.width = '';
          p.root.style.height = '';
          p.root.style.minHeight = '';
          p.root.style.resize = '';
        }
      });
      setTimeout(() => this.panels.forEach(p => p.resize()), 150);
      setTimeout(() => this.panels.forEach(p => p.resize()), 500);
    }
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
      const symText = panel.symbol === 'ALL' ? 'ALL' : panel.symbol.replace('_','/');
      tab.innerHTML = `
        <span>${this._esc(symText)} ${panel.timeframe}m</span>
        <button class="ws-tab-close" title="Close">✕</button>
      `;
      tab.addEventListener('click', (e) => {
        if (e.target.classList.contains('ws-tab-close')) return;
        this.setActivePanel(id);
        this._setFeedForPanel(id);
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

  _setFeedForPanel(panelId) {
    const panel = this.panels.get(panelId);
    if (!panel) return;
    const sym = panel.symbol;
    try {
      if (window.LiqScopeApp && window.LiqScopeApp.selectSymbol) {
        window.LiqScopeApp.selectSymbol(sym);
      }
    } catch {}
  }

  mount() {
    if (this._isPanelMode) {
      this._mountDetachedMode();
      return;
    }

    const root = this._ensureDom();
    if (!root) return;
    root.classList.add('active');

    try {
      const activePref = localStorage.getItem('liqscope_workspace_active');
      if (activePref === '0') {
        document.body.classList.remove('workspace-active');
      } else {
        document.body.classList.add('workspace-active');
      }
    } catch {
      document.body.classList.add('workspace-active');
    }

    const raw = this.loadWorkspace();
    let panels = raw.panels || [];
    if (!panels.length) {
      panels = [
        { id: this._generatePanelId(), symbol: 'BTC_USDT', timeframe: 5, layers: { levelsEnabled: false, levelsAlertEnabled: true, liqEnabled: true }, position: 0 },
        { id: this._generatePanelId(), symbol: 'ETH_USDT', timeframe: 5, layers: { levelsEnabled: false, levelsAlertEnabled: true, liqEnabled: true }, position: 1 },
      ];
      this.workspaceId = raw.workspaceId || this._generateWorkspaceId();
    }

    if (panels.length > this.maxPanels) panels = panels.slice(0, this.maxPanels);

    const grid = root.querySelector('.workspace-grid');
    grid.innerHTML = '';
    this.panels.clear();
    this.order = [];

    if (raw.singleMode) {
      this._singleMode = !!raw.singleMode;
      document.body.classList.toggle('workspace-single-mode', this._singleMode);
    }

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
    this._applySingleModeVisibility();

    try {
      const layout = localStorage.getItem('liqscope_workspace_layout');
      if (layout && !this._singleMode) this.setLayout(Number(layout));
    } catch {}

    try {
      const btn = root.querySelector('[data-action="toggle-single-chart"]');
      if (btn) btn.textContent = document.body.classList.contains('workspace-active') ? 'Single chart' : 'Multi chart';
      const singleBtn = root.querySelector('[data-action="single"]');
      if (singleBtn) singleBtn.textContent = this._singleMode ? '⊞ Grid' : '⛶ Single';
    } catch {}

    this.saveWorkspace();
    this._hookWs();

    // ensure resize after layout settled
    setTimeout(() => this.panels.forEach(p => p.resize()), 500);
    setTimeout(() => this.panels.forEach(p => p.resize()), 1500);
  }

  _mountDetachedMode() {
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

    const raw = this.loadWorkspaceRaw();
    let targetState = null;
    if (raw && Array.isArray(raw.panels)) {
      targetState = raw.panels.find(p => p.id === this._panelModeId);
    }
    if (!targetState) {
      this._broadcast({ type: 'WORKSPACE_SNAPSHOT_REQUEST' });
      const params = new URLSearchParams(window.location.search);
      const sym = params.get('symbol') || 'BTC_USDT';
      const tf = params.get('tf') || '5';
      targetState = { id: this._panelModeId || this._generatePanelId(), symbol: sym, timeframe: Number(tf), layers: {}, position: 0 };
    }

    this._mountSinglePanel(targetState, grid);

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
      case 'expandRequested':
        this.setActivePanel(panelId);
        if (!this._singleMode) this.toggleSingleMode();
        else this._applySingleModeVisibility();
        break;
      case 'activated':
        this.setActivePanel(panelId);
        break;
      case 'feedRequested':
        this._setFeedForPanel(panelId);
        if (data && data.symbol === 'ALL') {
          try {
            if (window.LiqScopeApp && window.LiqScopeApp.selectSymbol) {
              window.LiqScopeApp.selectSymbol('ALL');
            }
          } catch {}
        }
        break;
      case 'stateChanged':
        this.saveWorkspace();
        if (data) this._broadcast({ type: 'PANEL_STATE_UPDATE', panelId, panel: data });
        this._renderTabs();
        break;
      case 'symbolChanged':
      case 'timeframeChanged':
      case 'layersChanged':
        this.saveWorkspace();
        if (type==='symbolChanged' || type==='timeframeChanged') this._renderTabs();
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
      layers: { levelsEnabled: false, levelsAlertEnabled: true, liqEnabled: true },
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
    this._applySingleModeVisibility();
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
    this._applySingleModeVisibility();
    this.saveWorkspace();
    if (broadcast) this._broadcast({ type: 'PANEL_CLOSED', panelId: id });
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
    const root = this._ensureDom();
    const grid = root ? root.querySelector('.workspace-grid') : null;
    if (grid) {
      this.order.forEach(pid => {
        const p = this.panels.get(pid);
        if (p && p.root && p.root.parentNode===grid) {
          grid.appendChild(p.root);
        }
      });
    }
    this._renderTabs();
    this._applySingleModeVisibility();
    this.saveWorkspace();
  }

  setActivePanel(id) {
    if (!this.panels.has(id)) return;
    this.activePanelId = id;
    this._updateActiveStates();
    this._renderTabs();
    this._applySingleModeVisibility();
    this.saveWorkspace();
    this._setFeedForPanel(id);
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
      const iv = setInterval(() => {
        if (win.closed) {
          clearInterval(iv);
          this._detachedWindows.delete(id);
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
    document.addEventListener('liqscope:ws', (e) => {
      const msg = e.detail;
      this._handleWsMessage(msg);
    });
    if (this._isPanelMode) {
      this._connectDetachedWs();
    } else {
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
          this.panels.forEach(p => {
            if (p.symbol===msg.symbol && Number(p.timeframe)===Number(msg.tf || msg.timeframe)) {
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

(function(){
  function initWorkspace() {
    try {
      const host = document.getElementById('workspace-host');
      if (!host) return;
      if (!window.location.pathname.includes('/terminal')) return;

      if (!window.LiqScopeApp) window.LiqScopeApp = {};
      window.LiqScopeApp.getSymbols = function() {
        try {
          if (window.state && Array.isArray(window.state.symbols)) return window.state.symbols;
        } catch {}
        return [];
      };

      const wm = new WorkspaceManager({ container: host, maxPanels: 6 });
      window.LiqScopeWorkspace = wm;
      setTimeout(() => wm.mount(), 800);

      const params = new URLSearchParams(window.location.search);
      if (params.get('mode')==='panel') {
        document.body.classList.add('panel-mode');
        wm.mount();
      }
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
