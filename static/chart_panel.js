/**
 * ChartPanel — isolated chart instance for multi-chart workspace.
 * Robust init: waits for visible size, retries, shows errors.
 * Слои (ликвидации, CVD, OI, стакан, профиль, уровни) живут на этом
 * инстансе и не завязаны на единственный chart из app.js.
 */

class ChartPanel {
  constructor({ id, container, initialState, onEvent, isDetachedMode = false }) {
    this.id = id || ('panel_' + Math.random().toString(36).slice(2, 8));
    this.container = container;
    this.onEvent = onEvent || (() => {});
    this.isDetachedMode = !!isDetachedMode;

    const def = initialState || {};
    this.symbol = this._validateSymbol(def.symbol) || 'BTC_USDT';
    this.timeframe = this._validateTf(def.timeframe) || 5;
    this.layers = Object.assign({
      levelsEnabled: true,
      levelsAlertEnabled: true,
      liqEnabled: true,
      cvdEnabled: false,
      oiEnabled: false,
      bookEnabled: false,
      profileEnabled: false,
    }, def.layers || {});
    this.detached = !!def.detached;
    this.position = def.position || 0;
    this.follow = def.follow != null ? !!def.follow : this._defaultFollow();
    this._followHold = false;
    this._followTimer = null;
    this._followRelease = null;
    this._followBound = false;
    this._markersApi = null;

    this.chart = null;
    this.candleSeries = null;
    this.volumeSeries = null;
    this.clusterCanvas = null;
    this.drawCanvas = null;
    this.candles = [];
    this.levelsData = null;
    this.levelsAt = 0;
    this.liqHist = {};
    this.liqHistAt = 0;
    this._liqHistKey = '';
    this._liveLiqs = [];
    this.bookData = null;
    this.bookHist = [];
    this._bookTimer = null;
    this._bookHistSym = '';
    this._bookHistAt = 0;
    this._flowReload = false;
    this._drawing = false;
    this.price = null;
    this._levelHighlights = {};
    this._destroyed = false;
    this._boundResize = () => this.resize();
    this._resizeAttempts = 0;
    this._initAttempts = 0;

    this.root = null;
    this.chartEl = null;
    this.headerEl = null;
    this.priceEl = null;
    this.changeEl = null;
    this.symbolSelect = null;
    this.tfSelect = null;
    this.layersPop = null;
    this._ro = null;
  }

  _validateSymbol(sym) {
    if (!sym || typeof sym !== 'string') return null;
    sym = sym.trim().toUpperCase();
    if (sym === 'ALL') return 'ALL';
    if (!/^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$/.test(sym)) return null;
    return sym;
  }
  _validateTf(tf) {
    const n = Number(tf);
    if (!isFinite(n)) return null;
    const allowed = [1, 3, 5, 15, 60, 240, 1440];
    if (allowed.includes(n)) return n;
    if (n >= 1 && n <= 1440) return n;
    return null;
  }
  _emit(type, data) {
    try { this.onEvent({ type, panelId: this.id, data }); } catch {}
  }
  _esc(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  mount() {
    if (this.root) return;
    const root = document.createElement('div');
    root.className = 'chart-panel';
    root.dataset.panelId = this.id;
    if (this.detached) root.classList.add('detached');

    const header = document.createElement('div');
    header.className = 'panel-header';
    header.innerHTML = `
      <span class="panel-drag" draggable="true" title="Drag to reorder">⋮⋮</span>
      <span class="panel-symbol">
        <select class="panel-symbol-select" title="Symbol"></select>
        <select class="panel-tf-select" title="Timeframe">
          <option value="1">1m</option>
          <option value="3">3m</option>
          <option value="5">5m</option>
          <option value="15">15m</option>
          <option value="60">1h</option>
          <option value="240">4h</option>
          <option value="1440">1D</option>
        </select>
      </span>
      <span class="panel-price">—</span>
      <span class="panel-change"></span>
      <span class="panel-actions">
        <button class="panel-btn panel-layers-btn" title="Layers">☰</button>
        <button class="panel-btn panel-follow-btn" type="button" title="Следить за ценой">🎯</button>
        <button class="panel-btn panel-collapse-btn" type="button" title="Свернуть в сетку">▾</button>
        <button class="panel-btn panel-expand-btn" title="Развернуть график">⛶</button>
        <button class="panel-btn panel-detach-btn" title="Detach to new window">⧉</button>
        <button class="panel-btn close panel-close-btn" title="Close">✕</button>
      </span>
    `;
    root.appendChild(header);

    const wrap = document.createElement('div');
    wrap.className = 'panel-chart-wrap';
    const chartEl = document.createElement('div');
    chartEl.className = 'panel-chart';
    chartEl.id = `panel-chart-${this.id}`;
    wrap.appendChild(chartEl);
    const clusterCanvas = document.createElement('canvas');
    clusterCanvas.className = 'panel-canvas panel-cluster-canvas';
    wrap.appendChild(clusterCanvas);
    const drawCanvas = document.createElement('canvas');
    drawCanvas.className = 'panel-canvas panel-draw-canvas';
    wrap.appendChild(drawCanvas);

    const layersPop = document.createElement('div');
    layersPop.className = 'panel-layers-pop hidden';
    layersPop.innerHTML = `
      <button class="panel-layer-btn" data-layer="liqEnabled">⚡ Ликвидации</button>
      <button class="panel-layer-btn" data-layer="profileEnabled">📊 Профиль</button>
      <button class="panel-layer-btn" data-layer="cvdEnabled">🎯 CVD</button>
      <button class="panel-layer-btn" data-layer="oiEnabled">● OI</button>
      <button class="panel-layer-btn" data-layer="bookEnabled">📖 Стакан</button>
      <button class="panel-layer-btn" data-layer="levelsEnabled">🎯 Уровни (оценка)</button>
      <button class="panel-layer-btn" data-layer="levelsAlertEnabled">🔔 Сигнал уровней</button>
    `;
    wrap.appendChild(layersPop);
    root.appendChild(wrap);
    this.container.appendChild(root);

    this.root = root;
    this.headerEl = header;
    this.chartEl = chartEl;
    this.clusterCanvas = clusterCanvas;
    this.drawCanvas = drawCanvas;
    this.layersPop = layersPop;
    this.priceEl = header.querySelector('.panel-price');
    this.changeEl = header.querySelector('.panel-change');
    this.symbolSelect = header.querySelector('.panel-symbol-select');
    this.tfSelect = header.querySelector('.panel-tf-select');

    this._populateSymbolSelect();
    this.symbolSelect.value = this.symbol;
    this.tfSelect.value = String(this.timeframe);

    this.symbolSelect.addEventListener('change', () => {
      const v = this._validateSymbol(this.symbolSelect.value);
      if (v) this.setSymbol(v);
    });
    this.tfSelect.addEventListener('change', () => {
      const tf = this._validateTf(this.tfSelect.value);
      if (tf) this.setTimeframe(tf);
    });
    header.querySelector('.panel-layers-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      layersPop.classList.toggle('hidden');
      this._paintLayers();
    });
    layersPop.querySelectorAll('.panel-layer-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.layer;
        if (key) {
          this.setLayers({ [key]: !this.layers[key] });
          this._paintLayers();
        }
      });
    });
    document.addEventListener('click', (e) => {
      if (!root.contains(e.target)) layersPop.classList.add('hidden');
    });
    header.querySelector('.panel-detach-btn').addEventListener('click', () => {
      this._emit('detachRequested', { id: this.id });
    });
    header.querySelector('.panel-close-btn').addEventListener('click', () => {
      this._emit('closeRequested', { id: this.id });
    });
    header.querySelector('.panel-expand-btn').addEventListener('click', () => {
      this._emit('expandRequested', { id: this.id });
    });
    header.querySelector('.panel-collapse-btn').addEventListener('click', () => {
      this._emit('collapseRequested', { id: this.id });
    });
    header.querySelector('.panel-follow-btn').addEventListener('click', () => {
      this.setFollow(!this.follow);
    });
    this._paintFollow();

    const dragHandle = header.querySelector('.panel-drag');
    dragHandle.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/plain', this.id);
      e.dataTransfer.effectAllowed = 'move';
      this.root.classList.add('dragging');
    });
    dragHandle.addEventListener('dragend', () => {
      this.root.classList.remove('dragging');
    });

    this._paintLayers();

    // Defer chart init to next frame to ensure layout computed
    requestAnimationFrame(() => {
      this._initChartWithRetry();
    });

    window.addEventListener('resize', this._boundResize);
    root.addEventListener('mousedown', () => {
      this._emit('activated', { id: this.id });
    });
    root.addEventListener('click', () => {
      this._emit('feedRequested', { symbol: this.symbol });
    });

    // If panel is resized via CSS resize handle, trigger chart resize
    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(root);
      this._ro.observe(wrap);
    }

    // Periodic refresh of levels (like main chart)
    this._levelsTimer = setInterval(() => {
      if (this._destroyed || this.symbol === 'ALL') return;
      if (this.layers.levelsEnabled) this.loadLevels();
      if (this.layers.liqEnabled || this.layers.profileEnabled) this.loadLiqClusters(true);
    }, 30000);
  }

  _populateSymbolSelect() {
    const sel = this.symbolSelect;
    sel.innerHTML = '';
    let symbols = [];
    try {
      if (window.LiqScopeApp && window.LiqScopeApp.getSymbols) {
        symbols = window.LiqScopeApp.getSymbols() || [];
      } else if (window.state && window.state.symbols) {
        symbols = window.state.symbols;
      }
    } catch {}
    if (!symbols.length) {
      symbols = ['BTC_USDT','ETH_USDT','SOL_USDT','XRP_USDT','DOGE_USDT','ADA_USDT','AVAX_USDT','LINK_USDT','LTC_USDT','BCH_USDT','ZEC_USDT','ENA_USDT','WLD_USDT'];
    }
    const allOpt = document.createElement('option');
    allOpt.value = 'ALL';
    allOpt.textContent = 'ALL';
    sel.appendChild(allOpt);
    if (!symbols.includes(this.symbol) && this.symbol !== 'ALL') symbols.unshift(this.symbol);
    symbols.slice(0, 200).forEach(sym => {
      if (sym === 'ALL') return;
      const opt = document.createElement('option');
      opt.value = sym;
      opt.textContent = sym.replace('_','/');
      sel.appendChild(opt);
    });
  }

  _initChartWithRetry() {
    if (this._destroyed) return;
    if (!this.chartEl) return;
    const rect = this.chartEl.getBoundingClientRect();
    const w = rect.width || this.chartEl.clientWidth;
    const h = rect.height || this.chartEl.clientHeight;
    // If not visible yet (0 size or hidden), retry
    if (w < 50 || h < 50 || !this.chartEl.offsetParent) {
      this._initAttempts++;
      if (this._initAttempts < 30) {
        setTimeout(() => this._initChartWithRetry(), 200);
      } else {
        this._showError(`Chart container no size (${Math.round(w)}x${Math.round(h)}) - check CSS`);
      }
      return;
    }
    this._initChart();
    // After init, ensure resize after a bit
    setTimeout(() => this.resize(), 100);
    setTimeout(() => this.resize(), 500);
    setTimeout(() => this.resize(), 1500);
    // Load data after chart ready
    this.loadCandles();
    this._syncLayerFeeds();
  }

  _showError(msg) {
    if (!this.chartEl) return;
    let errEl = this.chartEl.querySelector('.panel-error');
    if (!errEl) {
      errEl = document.createElement('div');
      errEl.className = 'panel-error';
      this.chartEl.appendChild(errEl);
    }
    errEl.innerHTML = `<div>${this._esc(msg)}<br><button class="panel-retry-btn" style="margin-top:8px;padding:4px 8px;background:#1e3a5f;border:1px solid #2a5a9a;color:#cfe3ff;border-radius:4px;cursor:pointer">Retry</button></div>`;
    const btn = errEl.querySelector('.panel-retry-btn');
    if (btn) btn.addEventListener('click', () => {
      errEl.remove();
      this._initAttempts = 0;
      this._initChartWithRetry();
    });
  }
  _clearError() {
    if (!this.chartEl) return;
    const err = this.chartEl.querySelector('.panel-error');
    if (err) err.remove();
  }

  _initChart() {
    if (this._destroyed) return;
    const container = this.chartEl;
    if (!container) return;
    if (this.chart) {
      try { this.chart.remove(); } catch {}
      this.chart = null;
      this.candleSeries = null;
    }
    const existingError = container.querySelector('.panel-error');
    // Don't clear error if we are retrying? Keep but remove chart canvases
    const canvases = container.querySelectorAll('canvas');
    canvases.forEach(c => c.remove());
    // If error exists, keep it but ensure container has size
    if (existingError) {
      // keep error, but ensure we still create chart behind
    } else {
      // clear any leftover text
      // container.innerHTML = ''; // avoid destroying error
      // Instead, remove only non-error children
      Array.from(container.childNodes).forEach(n => {
        if (n.nodeType === 3 || (n.classList && !n.classList.contains('panel-error'))) {
          // text or non-error div
          if (n !== existingError) {
            try { n.remove(); } catch {}
          }
        }
      });
    }

    if (typeof window.LightweightCharts === 'undefined') {
      this._showError('Chart lib not loaded');
      return;
    }
    const width = container.clientWidth || container.getBoundingClientRect().width || 400;
    const height = container.clientHeight || container.getBoundingClientRect().height || 360;
    if (width < 10 || height < 10) {
      setTimeout(() => this._initChartWithRetry(), 300);
      return;
    }

    let chart;
    try {
      const LWC = window.LightweightCharts;
      chart = LWC.createChart(container, {
        width, height,
        autoSize: false,
        layout: {
          background: { color: "#090c10" },
          textColor: "#8493a8",
          fontSize: 11,
          fontFamily: "Inter, system-ui, sans-serif",
          attributionLogo: false,
        },
        grid: {
          vertLines: { color: "#18202c" },
          horzLines: { color: "#18202c" },
        },
        crosshair: { mode: LWC.CrosshairMode ? LWC.CrosshairMode.Normal : 0 },
        rightPriceScale: { borderColor: "#212938", scaleMargins: { top: 0.06, bottom: 0.24 } },
        timeScale: {
          borderColor: "#212938",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 6,
        },
      });
    } catch (e) {
      console.warn('panel createChart failed', this.id, e);
      this._showError(`Chart init failed: ${this._esc(e.message||e)}`);
      return;
    }
    this.chart = chart;
    try {
      if (!this._onRange) this._onRange = () => this._drawOverlays();
      chart.timeScale().subscribeVisibleLogicalRangeChange(this._onRange);
    } catch {}

    let candleSeries = null;
    try {
      const LWC = window.LightweightCharts;
      // Try helper from main app first
      if (window.LiqScopeApp && window.LiqScopeApp.createCandleSeries) {
        candleSeries = window.LiqScopeApp.createCandleSeries(chart, {
          upColor: "#00e676",
          downColor: "#ff2a5f",
          borderVisible: false,
          wickUpColor: "#00e676",
          wickDownColor: "#ff2a5f",
        });
      } else if (LWC && LWC.CandlestickSeries && chart.addSeries) {
        candleSeries = chart.addSeries(LWC.CandlestickSeries, {
          upColor: "#00e676",
          downColor: "#ff2a5f",
          borderVisible: false,
          wickUpColor: "#00e676",
          wickDownColor: "#ff2a5f",
        });
      } else if (chart.addCandlestickSeries) {
        candleSeries = chart.addCandlestickSeries({
          upColor: "#00e676",
          downColor: "#ff2a5f",
          borderVisible: false,
          wickUpColor: "#00e676",
          wickDownColor: "#ff2a5f",
        });
      }
    } catch (e) {
      console.warn('candle series failed', e);
      this._showError(`Candle series failed: ${this._esc(e.message||e)}`);
    }
    this.candleSeries = candleSeries;

    try {
      if (window.LiqScopeApp && window.LiqScopeApp.createVolumeSeries && candleSeries) {
        this.volumeSeries = window.LiqScopeApp.createVolumeSeries(chart, {
          priceFormat: { type: "volume" },
          priceScaleId: "volume",
          color: "#26a69a",
        });
        try {
          chart.priceScale("volume").applyOptions({
            scaleMargins: { top: 0.84, bottom: 0 },
            borderVisible: false,
          });
        } catch {}
      }
    } catch {}

    if (this.candles && this.candles.length && this.candleSeries) {
      try {
        this.candleSeries.setData(this.candles);
        if (this.follow) this._applyFollowMode();
        else { try { this.chart.timeScale().fitContent(); } catch {} }
        this._clearError();
      } catch {}
    }

    this._clearError();
    this._markersApi = null;
    this.resize();
    this._startFollow();
  }

  resize() {
    if (!this.chart || !this.chartEl) return;
    try {
      const rect = this.chartEl.getBoundingClientRect();
      const w = Math.round(rect.width) || this.chartEl.clientWidth || 400;
      const h = Math.round(rect.height) || this.chartEl.clientHeight || 360;
      if (w < 10 || h < 10) return;
      this.chart.applyOptions({ width: w, height: h });
      if (this.clusterCanvas) {
        this.clusterCanvas.width = w;
        this.clusterCanvas.height = h;
        this.clusterCanvas.style.width = w + 'px';
        this.clusterCanvas.style.height = h + 'px';
      }
      if (this.drawCanvas) {
        this.drawCanvas.width = w;
        this.drawCanvas.height = h;
        this.drawCanvas.style.width = w + 'px';
        this.drawCanvas.style.height = h + 'px';
      }
      this._drawOverlays();
    } catch {}
  }

  async loadCandles() {
    if (this._destroyed) return;
    const sym = this.symbol;
    if (sym === 'ALL') return;
    const tf = this.timeframe;
    try {
      const r = await fetch(`/api/klines?symbol=${encodeURIComponent(sym)}&timeframe=${tf}`, { cache: 'no-store' });
      if (!r.ok) throw new Error('klines ' + r.status);
      const data = await r.json();
      let candles = data.candles || data || [];
      if (!Array.isArray(candles)) {
        if (data.candles && typeof data.candles === 'object') {
          candles = Object.values(data.candles);
        } else {
          throw new Error('bad candles format');
        }
      }
      const bars = candles.map(c => this._normalizeBar(c)).filter(b => b && b.time && isFinite(b.open) && isFinite(b.close)).sort((a,b)=>a.time-b.time);

      if (!bars.length) {
        // Try fallback from main state if same symbol
        const fallback = this._getFallbackCandles(sym, tf);
        if (fallback && fallback.length) {
          this._applyCandles(fallback);
          return;
        }
        throw new Error('no candles');
      }

      this._applyCandles(bars);
    } catch (e) {
      console.debug('panel loadCandles failed', sym, e);
      // fallback
      const fallback = this._getFallbackCandles(sym, tf);
      if (fallback && fallback.length) {
        this._applyCandles(fallback);
        return;
      }
      if (this.chartEl && !this.candles.length) {
        this._showError(`Failed to load ${sym}: ${this._esc(e.message||e)}`);
        setTimeout(() => this.loadCandles(), 3000);
      }
    }
  }

  _getFallbackCandles(sym, tf) {
    try {
      // If main app has candles for this symbol and timeframe, use them
      if (window.state && window.state.candles && Array.isArray(window.state.candles)) {
        const s = window.state;
        // state.candles is for current chartSymbol, check if matches
        if (s.chartSymbol === sym || s.symbol === sym) {
          // s.candles may be already formatted
          if (s.candles.length && s.candles[0].time) {
            return s.candles;
          }
        }
      }
    } catch {}
    return null;
  }

  _applyCandles(bars) {
    this.candles = bars;
    if (this.candleSeries && bars.length) {
      try {
        this.candleSeries.setData(bars);
        if (this.chart && this.chart.timeScale) {
          if (this.follow) this._applyFollowMode();
          else { try { this.chart.timeScale().fitContent(); } catch {} }
        }
        this._clearError();
      } catch (e) {
        console.debug('setData failed', e);
        this._showError(`setData failed: ${this._esc(e.message||e)}`);
        // Try to re-init chart
        setTimeout(() => this._initChartWithRetry(), 500);
        return;
      }
    } else if (!this.candleSeries) {
      // chart not ready, retry init
      this._initChartWithRetry();
      // keep bars for later
      setTimeout(() => {
        if (this.candleSeries) {
          try { this.candleSeries.setData(bars); this._clearError(); } catch {}
        }
      }, 500);
    }
    if (bars.length) {
      const last = bars[bars.length-1];
      this._updatePriceDisplay(last.close);
    }
    this._syncLayerFeeds();
    if (this.follow) this._anchorFollow();
  }

  async loadLevels() {
    if (!this.layers.levelsEnabled) return;
    if (this.symbol === 'ALL') return;
    const sym = this.symbol;
    try {
      const price = this.price || (this.candles.length ? this.candles[this.candles.length-1].close : 0);
      const url = `/api/liq_levels?symbol=${encodeURIComponent(sym)}${price?`&price=${price}`:''}`;
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) throw new Error('levels fetch failed ' + r.status);
      const data = await r.json();
      if (this.symbol !== sym || !this.layers.levelsEnabled) return;
      if (!data || data.ok === false) {
        // empty or off
        this.levelsData = data;
        this._drawOverlays();
        return;
      }
      this.levelsData = data;
      this.levelsAt = Date.now();
      // Ensure canvas size before drawing
      this.resize();
      this._drawOverlays();
      // Retry drawing after a bit in case priceToCoordinate not ready
      setTimeout(() => this._drawOverlays(), 500);
      setTimeout(() => this._drawOverlays(), 1500);
    } catch (e) {
      console.debug('panel loadLevels failed', sym, e);
    }
  }

  _updatePriceDisplay(price) {
    if (!isFinite(price)) return;
    this.price = Number(price);
    if (this.priceEl) this.priceEl.textContent = this._fmtPrice(price);
    if (this.candles.length) {
      const open = this.candles[0].open;
      if (open) {
        const pct = (price - open)/open*100;
        if (this.changeEl) {
          this.changeEl.textContent = (pct>=0?'+':'')+pct.toFixed(2)+'%';
          this.changeEl.className = 'panel-change '+(pct>=0?'positive':'negative');
        }
      }
    }
    this._checkDashedTriggers(price);
  }

  _fmtPrice(p) {
    const n = Number(p);
    if (!isFinite(n)) return '—';
    if (n >= 1000) return n.toFixed(2);
    if (n >= 1) return n.toFixed(4);
    if (n >= 0.01) return n.toFixed(6);
    return n.toFixed(8);
  }

  _normalizeBar(c, prev) {
    if (!c) return null;
    let t = Number(c.time || c.t || c.timestamp || 0);
    if (t > 1e12) t = Math.floor(t / 1000);
    const bar = {
      time: t,
      open: Number(c.open || c.o),
      high: Number(c.high || c.h),
      low: Number(c.low || c.l),
      close: Number(c.close || c.c),
      volume: Number(c.volume || c.v || 0),
    };
    if (c.cvd != null && c.cvd !== '') bar.cvd = Number(c.cvd);
    else if (prev && prev.cvd != null) bar.cvd = prev.cvd;
    if (c.oiChg != null && c.oiChg !== '') bar.oiChg = Number(c.oiChg);
    else if (c.oi_chg != null && c.oi_chg !== '') bar.oiChg = Number(c.oi_chg);
    else if (prev && prev.oiChg != null) bar.oiChg = prev.oiChg;
    return bar;
  }

  _fmtCompact(v) {
    const n = Math.abs(Number(v) || 0);
    const sign = Number(v) < 0 ? '-' : '';
    const trim = (x) => String(x).replace(/\.0$/, '');
    if (n >= 1e9) return sign + '$' + trim((n / 1e9).toFixed(n >= 1e10 ? 0 : 1)) + 'B';
    if (n >= 1e6) return sign + '$' + trim((n / 1e6).toFixed(n >= 1e7 ? 0 : 1)) + 'M';
    if (n >= 1e3) return sign + '$' + trim((n / 1e3).toFixed(n >= 1e4 ? 0 : 1)) + 'K';
    return sign + '$' + Math.round(n);
  }

  _volScale() {
    try {
      const s = window.state;
      if (!s || !s.details) return 1;
      const volOf = (d) => Number(d && (d.volAvg7d || d.volume24h)) || 0;
      const anchor = volOf(s.details.BTC_USDT) || 0;
      const v = volOf(s.details[this.symbol]) || 0;
      if (!(anchor > 0) || !(v > 0)) return 1;
      return Math.min(2, Math.max(0.001, v / anchor));
    } catch { return 1; }
  }

  _heatRGB(usd, k) {
    const stops = [[1e5,255,206,0],[3e5,255,168,0],[5e5,255,128,0],[1e6,255,72,0],[2e6,255,30,8],[5e6,255,16,48]];
    k = k || 1;
    const u = Number(usd);
    if (!(u > stops[0][0] * k)) return stops[0].slice(1);
    for (let i = 1; i < stops.length; i++) {
      if (u <= stops[i][0] * k) {
        const a = stops[i - 1], b = stops[i];
        const t = (u - a[0] * k) / ((b[0] - a[0]) * k);
        return [0, 1, 2].map(j => Math.round(a[j + 1] + (b[j + 1] - a[j + 1]) * t));
      }
    }
    return stops[stops.length - 1].slice(1);
  }

  _heatTheme(usd, k) {
    const c = this._heatRGB(usd, k);
    const lum = (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255;
    const mx = (v) => Math.round(v + (255 - v) * 0.55);
    return {
      fill: 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',0.95)',
      ring: 'rgb(' + mx(c[0]) + ',' + mx(c[1]) + ',' + mx(c[2]) + ')',
      text: lum > 0.5 ? '#1a1200' : '#ffffff',
    };
  }

  _syncWhaleMarkers(rows) {
    if (!this.candleSeries || !window.LightweightCharts || !window.LightweightCharts.createSeriesMarkers) return;
    const k = this._volScale();
    const byTime = new Map();
    (rows || []).forEach(r => {
      [[r.longUsd, 'SELL'], [r.shortUsd, 'BUY']].forEach(([usd, side]) => {
        if (!(usd >= 100000 * k)) return;
        const key = r.time + '_' + side;
        const cur = byTime.get(key) || { time: r.time, side, usd: 0 };
        cur.usd += usd;
        byTime.set(key, cur);
      });
    });
    const markers = Array.from(byTime.values())
      .sort((a, b) => a.usd - b.usd)
      .slice(-24)
      .map(m => {
        const c = this._heatRGB(m.usd, k);
        return {
          time: m.time,
          position: m.side === 'SELL' ? 'aboveBar' : 'belowBar',
          color: 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')',
          shape: 'circle',
          text: '🔥 ' + this._fmtCompact(m.usd),
        };
      })
      .sort((a, b) => a.time - b.time);
    try {
      if (!this._markersApi) this._markersApi = window.LightweightCharts.createSeriesMarkers(this.candleSeries, markers);
      else this._markersApi.setMarkers(markers);
    } catch {}
  }

  _paintFittedLabel(ctx, text, x, y, bw, bh, color) {
    const sizes = [9, 8, 7];
    const alts = [text];
    if (text && text.charAt(0) === '$') alts.push(text.slice(1));
    let shown = '', font = 0;
    for (let i = 0; i < alts.length && !font; i++) {
      for (let s = 0; s < sizes.length; s++) {
        ctx.font = "bold " + sizes[s] + "px 'JetBrains Mono', monospace";
        const tw = ctx.measureText ? ctx.measureText(alts[i]).width : 999;
        if (tw <= bw - 4 && sizes[s] + 2 <= bh) { font = sizes[s]; shown = alts[i]; break; }
      }
    }
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    if (font) {
      ctx.font = "bold " + font + "px 'JetBrains Mono', monospace";
      ctx.fillStyle = color;
      ctx.fillText(shown, x, y + 0.5);
      return;
    }
    ctx.font = "bold 8px 'JetBrains Mono', monospace";
    const tw = ctx.measureText ? ctx.measureText(text).width : 24;
    const ly = y - bh / 2 - 14;
    ctx.fillStyle = 'rgba(5,8,14,0.88)';
    ctx.fillRect(x - tw / 2 - 2, ly, tw + 4, 11);
    ctx.fillStyle = '#f4f7fb';
    ctx.fillText(text, x, y - bh / 2 - 8);
  }

  _slotPx(W) {
    let visible = 0;
    try {
      const r = this.chart && this.chart.timeScale && this.chart.timeScale().getVisibleLogicalRange();
      if (r && isFinite(r.from) && isFinite(r.to)) visible = Math.ceil(r.to - r.from);
    } catch {}
    if (!visible) visible = Math.min((this.candles && this.candles.length) || 80, 80) || 80;
    return W > 0 ? W / visible : 8;
  }

  _clearPriceLines() {
    try {
      if (this._priceLines && this.candleSeries) {
        this._priceLines.forEach(pl => {
          try { this.candleSeries.removePriceLine(pl); } catch {}
        });
      }
      this._priceLines = [];
    } catch {}
  }

  _overlayOn() {
    const L = this.layers || {};
    return !!(L.levelsEnabled || L.liqEnabled || L.cvdEnabled || L.oiEnabled || L.bookEnabled || L.profileEnabled);
  }

  _syncLayerFeeds() {
    if (this._destroyed) return;
    if (this.symbol === 'ALL') {
      this._stopBookPoll();
      this._drawOverlays();
      return;
    }
    if (this.layers.levelsEnabled) {
      if (!this.levelsData || Date.now() - this.levelsAt > 45000) this.loadLevels();
    } else {
      this._clearPriceLines();
    }
    if (this.layers.liqEnabled || this.layers.profileEnabled) {
      const key = this.symbol + '|' + this.timeframe;
      if (this._liqHistKey !== key || !this.liqHistAt || Date.now() - this.liqHistAt > 60000) {
        this.loadLiqClusters();
      }
    }
    if (this.layers.bookEnabled) this._startBookPoll();
    else this._stopBookPoll();
    if ((this.layers.cvdEnabled || this.layers.oiEnabled) && this.candles.length && !this._flowReload) {
      const missing = this.candles.every(c => c.cvd == null && c.oiChg == null);
      if (missing) {
        this._flowReload = true;
        this.loadCandles();
      }
    }
    this._drawOverlays();
  }

  _startBookPoll() {
    this._loadBook();
    if (this._bookTimer) return;
    this._bookTimer = setInterval(() => this._loadBook(), 4000);
  }

  _stopBookPoll() {
    if (this._bookTimer) {
      try { clearInterval(this._bookTimer); } catch {}
      this._bookTimer = null;
    }
    this.bookData = null;
    this.bookHist = [];
    this._bookHistAt = 0;
  }

  async _loadBook() {
    if (this._destroyed || !this.layers.bookEnabled || this.symbol === 'ALL') return;
    const sym = this.symbol;
    try {
      const r = await fetch('/api/book/snapshot?symbol=' + encodeURIComponent(sym), { credentials: 'same-origin', cache: 'no-store' });
      const d = await r.json();
      if (d && d.ok && this.symbol === sym) {
        this.bookData = d;
        this._drawOverlays();
      }
    } catch {}
    if (this._bookHistSym !== sym || Date.now() - (this._bookHistAt || 0) > 60000) {
      this._bookHistSym = sym;
      this._bookHistAt = Date.now();
      try {
        const r = await fetch('/api/book/walls?symbol=' + encodeURIComponent(sym) + '&hours=48', { credentials: 'same-origin', cache: 'no-store' });
        const d = await r.json();
        if (d && d.ok && this.symbol === sym) {
          this.bookHist = d.walls || [];
          this._drawOverlays();
        }
      } catch {}
    }
  }

  async loadLiqClusters(force) {
    if (this._destroyed) return;
    if (!this.layers.liqEnabled && !this.layers.profileEnabled) return;
    if (this.symbol === 'ALL') return;
    const sym = this.symbol;
    const tf = this.timeframe;
    const key = sym + '|' + tf;
    if (!force && this._liqHistKey === key && Date.now() - this.liqHistAt < 60000) return;
    if (this._liqPending === key) return;
    this._liqPending = key;
    try {
      const url = '/api/liq_clusters?symbol=' + encodeURIComponent(sym) +
        '&timeframe=' + tf + '&min_usd=0&exchanges=';
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) return;
      const data = await r.json();
      if (this.symbol !== sym || Number(this.timeframe) !== Number(tf)) return;
      this.liqHist = (data && data.candles) || {};
      this.liqCut = Number(data && data.cut) || 0;
      this._liqHistKey = key;
      this.liqHistAt = Date.now();
      this._drawOverlays();
    } catch (e) {
      console.debug('panel liq clusters', sym, e);
    } finally {
      if (this._liqPending === key) this._liqPending = null;
    }
  }

  _histAt(t) {
    const h = this.liqHist || {};
    return h[t] || h[String(t)] || null;
  }

  _liqRows() {
    const tfSec = Number(this.timeframe) * 60 || 300;
    const rows = new Map();
    const rowOf = (t, bar, lo, hi, level) => {
      const key = 'b' + t + '_' + level;
      let r = rows.get(key);
      if (!r) {
        r = { key, time: t, bar, lo, hi, level, longUsd: 0, shortUsd: 0, total: 0, count: 0, pxSum: 0 };
        rows.set(key, r);
      }
      return r;
    };
    const addHistory = (r, row) => {
      const longUsd = Number(row[1]) || 0;
      const shortUsd = Number(row[2]) || 0;
      const usd = longUsd + shortUsd;
      if (usd <= 0) return;
      r.longUsd += longUsd;
      r.shortUsd += shortUsd;
      r.total += usd;
      r.count += (Number(row[3]) || 0) + (Number(row[4]) || 0);
      r.pxSum += (Number(row[5]) || 0) * usd;
    };
    const live = new Map();
    (this._liveLiqs || []).forEach(item => {
      const ts = Number(item.timestamp);
      if (!(ts > 0)) return;
      const t = Math.floor(ts / tfSec) * tfSec;
      const served = this._histAt(t);
      if (served && served.t != null && ts <= Number(served.t)) return;
      const arr = live.get(t);
      if (arr) arr.push(item); else live.set(t, [item]);
    });
    (this.candles || []).forEach(bar => {
      const t = Number(bar.time);
      const lo = Math.min(Number(bar.low), Number(bar.high));
      const hi = Math.max(Number(bar.low), Number(bar.high));
      const served = this._histAt(t);
      if (served && served.l && served.l.length) {
        served.l.forEach(row => addHistory(rowOf(t, bar, lo, hi, Number(row[0]) || 0), row));
      }
      const items = live.get(t);
      if (!items) return;
      items.forEach(item => {
        let price = Number(item.price);
        if (!isFinite(price)) price = Number(bar.close);
        if (hi >= lo) price = Math.min(Math.max(price, lo), hi);
        const span = hi - lo;
        const level = span > 0 ? Math.round(((price - lo) / span) * 5) : 0;
        const r = rowOf(t, bar, lo, hi, level);
        const usd = Number(item.usd) || 0;
        if (!(usd > 0)) return;
        if (item.side === 'SELL') r.longUsd += usd; else r.shortUsd += usd;
        r.total += usd;
        r.count += 1;
        r.pxSum += price * usd;
      });
    });
    return Array.from(rows.values());
  }

  _drawOverlays() {
    if (this._drawing) return;
    this._drawing = true;
    try {
      if (!this.clusterCanvas || !this.chart || !this.candleSeries) return;
      const canvas = this.clusterCanvas;
      const rect = this.chartEl ? this.chartEl.getBoundingClientRect() : null;
      let W = canvas.width, H = canvas.height;
      if (!W || !H || W < 10 || H < 10) {
        if (rect && rect.width > 10 && rect.height > 10) {
          canvas.width = Math.round(rect.width);
          canvas.height = Math.round(rect.height);
          W = canvas.width; H = canvas.height;
        } else {
          return;
        }
      }
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, W, H);
      if (!this.layers.levelsEnabled) this._clearPriceLines();
      const draw = (fn) => { try { fn.call(this, ctx, W, H); } catch (e) { console.debug('panel overlay', e); } };
      if (this.layers.profileEnabled) draw(this._drawProfile);
      if (this.layers.bookEnabled) draw(this._drawBook);
      if (this.layers.oiEnabled) draw(this._drawOi);
      if (this.layers.cvdEnabled) draw(this._drawCvd);
      if (this.layers.liqEnabled) draw(this._drawLiqPlates);
      else this._syncWhaleMarkers([]);
      if (this.layers.levelsEnabled) draw(this._drawLevels);
    } finally {
      this._drawing = false;
    }
  }

  _drawProfile(ctx, W, H) {
    const rows = this._liqRows();
    if (!rows.length || !this.candles.length) return;
    let minP = Infinity, maxP = -Infinity;
    this.candles.forEach(c => {
      minP = Math.min(minP, Number(c.low), Number(c.high));
      maxP = Math.max(maxP, Number(c.low), Number(c.high));
    });
    if (!(maxP > minP)) return;
    const BINS = 34;
    const bins = [];
    for (let i = 0; i < BINS; i++) bins.push({ long: 0, short: 0, total: 0 });
    const step = (maxP - minP) / BINS;
    rows.forEach(r => {
      const price = r.total > 0 ? r.pxSum / r.total : Number(r.bar && r.bar.close);
      if (!isFinite(price)) return;
      let idx = Math.floor((price - minP) / step);
      if (idx < 0 || idx >= BINS) return;
      bins[idx].long += r.longUsd;
      bins[idx].short += r.shortUsd;
      bins[idx].total += r.total;
    });
    let maxTotal = 0;
    bins.forEach(b => { if (b.total > maxTotal) maxTotal = b.total; });
    if (!(maxTotal > 0)) return;
    const maxBar = Math.max(W * 0.34, 60);
    ctx.save();
    bins.forEach((b, i) => {
      if (!b.total) return;
      const price = maxP - (i + 0.5) * step;
      let y = null;
      try { y = this.candleSeries.priceToCoordinate(price); } catch {}
      if (y == null || y < -20 || y > H + 20) return;
      const rowW = Math.max((b.total / maxTotal) * maxBar, 4);
      const longW = b.total > 0 ? rowW * (b.long / b.total) : 0;
      const shortW = rowW - longW;
      const barH = 6;
      const top = y - barH / 2;
      ctx.globalAlpha = 0.42;
      if (longW > 0) { ctx.fillStyle = 'rgba(255,45,149,0.92)'; ctx.fillRect(2, top, longW, barH); }
      if (shortW > 0) { ctx.fillStyle = 'rgba(0,214,255,0.92)'; ctx.fillRect(2 + longW, top, shortW, barH); }
      if (b.total >= maxTotal * 0.55 && rowW >= 48) {
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = '#d7e6f5';
        ctx.font = "bold 8px 'JetBrains Mono', monospace";
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(this._fmtCompact(b.total), 6 + rowW, y);
      }
    });
    ctx.restore();
  }

  _bookWalls() {
    const byId = new Map();
    (this.bookHist || []).forEach(w => { if (w && w.id != null) byId.set(w.id, w); });
    ((this.bookData && this.bookData.walls) || []).forEach(w => {
      if (w && w.id != null) byId.set(w.id, w);
    });
    return Array.from(byId.values()).filter(w => Math.max(Number(w.usdt) || 0, Number(w.peak) || 0) > 0);
  }

  _drawBook(ctx, W, H) {
    const walls = this._bookWalls();
    if (!walls.length) return;
    const tfSec = Number(this.timeframe) * 60 || 300;
    const ts = this.chart.timeScale();
    const bw = Math.max(3, Math.min(96, Math.round(this._slotPx(W) * 0.86)));
    ctx.save();
    walls.forEach(w => {
      const t0 = Math.floor(Number(w.opened) / tfSec) * tfSec;
      if (!isFinite(t0)) return;
      let x = null, y1 = null, y2 = null;
      try {
        x = ts.timeToCoordinate(t0);
        y1 = this.candleSeries.priceToCoordinate(Number(w.hi));
        y2 = this.candleSeries.priceToCoordinate(Number(w.lo));
      } catch { return; }
      if (x == null || y1 == null || y2 == null || !isFinite(x)) return;
      if (x < -bw || x > W + bw) return;
      let top = Math.min(y1, y2), bot = Math.max(y1, y2);
      if (bot - top < 6) { const cy = (top + bot) / 2; top = cy - 3; bot = cy + 3; }
      if (bot < -20 || top > H + 20) return;
      const bid = w.side === 'bid';
      ctx.globalAlpha = w.live ? 0.45 : 0.16;
      ctx.fillStyle = bid ? '#67e8f9' : '#a78bfa';
      ctx.fillRect(Math.round(x - bw / 2), Math.round(top), bw, Math.max(6, Math.round(bot - top)));
      ctx.globalAlpha = w.live ? 0.85 : 0.35;
      ctx.strokeStyle = bid ? '#22d3ee' : '#8b5cf6';
      ctx.lineWidth = 1;
      ctx.strokeRect(Math.round(x - bw / 2) + 0.5, Math.round(top) + 0.5, Math.max(1, bw - 1), Math.max(5, Math.round(bot - top) - 1));
      if (w.live && (bot - top) >= 14 && bw >= 22) {
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = '#041018';
        ctx.font = "bold 8px 'JetBrains Mono', monospace";
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(this._fmtCompact(Math.max(Number(w.usdt) || 0, Number(w.peak) || 0)), x, (top + bot) / 2);
      }
    });
    ctx.restore();
  }

  _drawOi(ctx, W, H) {
    const candles = this.candles || [];
    const absVals = [];
    candles.forEach(c => {
      const d = Math.abs(Number(c.oiChg));
      if (isFinite(d) && d > 0) absVals.push(d);
    });
    if (!absVals.length) return;
    absVals.sort((a, b) => a - b);
    const p90 = absVals[Math.floor(0.9 * (absVals.length - 1))] || absVals[absVals.length - 1];
    if (!(p90 > 0)) return;
    const thr = Math.max(1000, p90 * 0.05);
    const cand = [];
    candles.forEach(c => {
      const d = Number(c.oiChg);
      if (isFinite(d) && Math.abs(d) >= thr) cand.push({ c, d });
    });
    cand.sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
    const ts = this.chart.timeScale();
    let drawn = 0;
    ctx.save();
    for (let i = 0; i < cand.length && drawn < 60; i++) {
      const item = cand[i];
      const up = item.d > 0;
      const ref = up ? Number(item.c.high) : Number(item.c.low);
      if (!isFinite(ref)) continue;
      let x = null, y = null;
      try {
        x = ts.timeToCoordinate(item.c.time);
        y = this.candleSeries.priceToCoordinate(ref);
      } catch { continue; }
      if (x == null || y == null) continue;
      const abs = Math.abs(item.d);
      const kvol = this._volScale();
      const tier = abs >= 2e7 * kvol ? 3 : abs >= 5e6 * kvol ? 2 : abs >= 1e6 * kvol ? 1 : 0;
      const shades = up
        ? [[190,242,200],[134,239,172],[34,197,94],[0,230,118]]
        : [[252,200,200],[248,113,113],[239,68,68],[255,42,95]];
      const col = shades[tier];
      let r = (7 + 8 * Math.min(abs / p90, 1.2)) * (tier > 0 ? 1.15 : 1);
      if (tier > 0) r = Math.max(r, 12);
      const cy = up ? y - r - 2 : y + r + 2;
      if (x < -r || x > W + r || cy < -r || cy > H + r) continue;
      ctx.beginPath();
      ctx.fillStyle = 'rgba(' + col[0] + ',' + col[1] + ',' + col[2] + ',0.95)';
      ctx.arc(x, cy, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = up ? '#d8ffe6' : '#ffe3e6';
      ctx.stroke();
      if (tier > 0) {
        const val = this._fmtCompact(abs);
        ctx.font = "bold 8px 'JetBrains Mono', monospace";
        if (!ctx.measureText || ctx.measureText(val).width <= r * 1.7) {
          ctx.fillStyle = up ? '#04160b' : '#1c060d';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(val, x, cy);
        }
      }
      drawn++;
    }
    ctx.restore();
  }

  _drawCvd(ctx, W, H) {
    const candles = this.candles || [];
    if (!candles.length) return;
    const absVals = [];
    candles.forEach(c => {
      const d = Math.abs(Number(c.cvd));
      if (isFinite(d) && d > 0) absVals.push(d);
    });
    if (!absVals.length) return;
    absVals.sort((a, b) => a - b);
    const p90 = absVals[Math.floor(0.9 * (absVals.length - 1))] || absVals[absVals.length - 1];
    if (!(p90 > 0)) return;
    const thr = Math.max(1000, p90 * 0.05);
    const cand = [];
    candles.forEach((c, i) => {
      const d = Number(c.cvd);
      if (isFinite(d) && Math.abs(d) >= thr) cand.push({ c, d, live: i === candles.length - 1 });
    });
    cand.sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
    const ts = this.chart.timeScale();
    const placed = [];
    let drawn = 0;
    ctx.save();
    for (let i = 0; i < cand.length && drawn < 40; i++) {
      const item = cand[i];
      const mid = (Number(item.c.open) + Number(item.c.close)) / 2;
      let x = null, y = null;
      try {
        x = ts.timeToCoordinate(item.c.time);
        y = this.candleSeries.priceToCoordinate(mid);
      } catch { continue; }
      if (x == null || y == null || !isFinite(x) || !isFinite(y)) continue;
      const kk = Math.min(Math.sqrt(Math.abs(item.d) / p90), 1.2);
      const halfW = 9 + 12 * kk;
      const h = halfW * 1.5;
      if (x < -halfW - 8 || x > W + halfW + 8 || y < -h || y > H + h) continue;
      let clash = false;
      for (let j = 0; j < placed.length; j++) {
        const q = placed[j];
        const dx = x - q.x, dy = y - q.y;
        if (dx * dx + dy * dy <= (halfW + q.r) * (halfW + q.r)) { clash = true; break; }
      }
      if (clash) continue;
      placed.push({ x, y, r: halfW });
      const buy = item.d > 0;
      const apexY = buy ? y - h / 2 : y + h / 2;
      ctx.beginPath();
      if (buy) {
        ctx.moveTo(x, apexY);
        ctx.lineTo(x + halfW, apexY + h);
        ctx.lineTo(x - halfW, apexY + h);
      } else {
        ctx.moveTo(x, apexY);
        ctx.lineTo(x + halfW, apexY - h);
        ctx.lineTo(x - halfW, apexY - h);
      }
      ctx.closePath();
      ctx.fillStyle = buy ? 'rgba(139,92,246,0.96)' : 'rgba(255,145,0,0.96)';
      ctx.fill();
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = buy ? '#e6d9ff' : '#ffe3b8';
      ctx.stroke();
      const val = this._fmtCompact(Math.abs(item.d));
      const cy = buy ? apexY + h * 0.68 : apexY - h * 0.68;
      let vFont = 0;
      for (const fs of [8, 7, 6.5]) {
        ctx.font = "bold " + fs + "px 'JetBrains Mono', monospace";
        if (ctx.measureText(val).width <= halfW * 1.3) { vFont = fs; break; }
      }
      if (vFont) {
        ctx.fillStyle = buy ? '#0d0618' : '#1c0d00';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(val, x, cy + 0.5);
      }
      drawn++;
    }
    ctx.restore();
  }

  _drawLiqPlates(ctx, W, H) {
    const rows = this._liqRows();
    this._syncWhaleMarkers(rows);
    if (!rows.length) return;
    const ts = this.chart.timeScale();
    const slot = Math.max(3, Math.min(36, Math.round(this._slotPx(W) * 0.86)));
    const kvol = this._volScale();
    const drawn = [];
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    rows.sort((a, b) => a.total - b.total);
    rows.forEach(c => {
      const price = c.total > 0 ? c.pxSum / c.total : Number(c.bar && c.bar.close);
      let x = null, y = null;
      try {
        x = ts.timeToCoordinate(c.time);
        y = this.candleSeries.priceToCoordinate(price);
      } catch { return; }
      if (x == null || y == null || !isFinite(x) || !isFinite(y)) return;
      if (x < -40 || x > W + 40 || y < -20 || y > H + 20) return;
      const whale = c.total >= 100000 * kvol;
      const isLong = c.longUsd >= c.shortUsd;
      const theme = whale
        ? this._heatTheme(c.total, kvol)
        : (isLong
          ? { fill: 'rgba(255,45,149,0.92)', ring: '#ffd7ec', text: '#04070d' }
          : { fill: 'rgba(0,214,255,0.92)', ring: '#ccf6ff', text: '#04070d' });
      const wantLabel = whale || c.total >= 2000 * kvol;
      const h = wantLabel ? (whale ? 18 : 15) : 11;
      let bw = slot;
      if (wantLabel) {
        ctx.font = "bold 8px 'JetBrains Mono', monospace";
        const tw = ctx.measureText(this._fmtCompact(c.total)).width + 8;
        bw = Math.max(slot, Math.min(52, Math.ceil(tw)));
      }
      const bx = Math.round(x - bw / 2);
      const by = Math.round(y - h / 2);
      for (let j = 0; j < drawn.length; j++) {
        const d = drawn[j];
        if (bx < d.x + d.w && bx + bw > d.x && by < d.y + d.h && by + h > d.y) return;
      }
      drawn.push({ x: bx, y: by, w: bw, h });
      ctx.globalAlpha = 1;
      ctx.fillStyle = 'rgba(5,8,14,0.85)';
      ctx.fillRect(bx - 1, by - 1, bw + 2, h + 2);
      ctx.shadowColor = theme.fill;
      ctx.shadowBlur = whale ? 8 : 0;
      ctx.fillStyle = theme.fill;
      ctx.fillRect(bx, by, bw, h);
      ctx.shadowBlur = 0;
      ctx.strokeStyle = theme.ring;
      ctx.lineWidth = whale ? 1.4 : 1;
      ctx.strokeRect(bx + 0.5, by + 0.5, Math.max(1, bw - 1), Math.max(1, h - 1));
      if (wantLabel) this._paintFittedLabel(ctx, this._fmtCompact(c.total), x, y, bw, h, theme.text);
    });
    ctx.restore();
  }

  _drawLevels(ctx, W, H) {
    if (!this.layers.levelsEnabled || !this.levelsData) return;
    // Also clear old price lines
    try {
      if (this._priceLines) {
        this._priceLines.forEach(pl => {
          try { this.candleSeries.removePriceLine(pl); } catch {}
        });
      }
      this._priceLines = [];
    } catch {}
    const data = this.levelsData;
    const rows = (data.levels || []).filter(r => Number(r.usd)>0);
    if (!rows.length) return;

    let priceAxisW = 68;
    try {
      const ps = this.chart.priceScale ? this.chart.priceScale('right') : null;
      if (ps && ps.width) priceAxisW = ps.width() || 68;
    } catch {}
    const plotRight = W - priceAxisW;
    const plotBottom = H - 20;
    const colW = Math.min(116, Math.max(68, plotRight*0.15));
    const xL = plotRight - colW;

    const visible = [];
    rows.slice(0, 40).forEach(row => {
      let y = null;
      try { y = this.candleSeries.priceToCoordinate(Number(row.price)); } catch {}
      if (y==null || y<20 || y>plotBottom) return;
      visible.push({ row, y });
    });
    if (!visible.length) return;

    let maxUsd = 0;
    visible.forEach(v => { const usd = Number(v.row.usd)||0; if (usd>maxUsd) maxUsd=usd; });
    if (!(maxUsd>0)) return;

    const barH = 5;
    const maxBar = Math.max(20, colW-8);
    const LIQ_COLORS = {
      long: { fill: "rgba(255,45,149,0.92)", ring: "#ffd7ec" },
      short: { fill: "rgba(0,214,255,0.92)", ring: "#ccf6ff" },
    };

    ctx.save();
    ctx.beginPath();
    ctx.rect(0,0,plotRight,plotBottom);
    ctx.clip();

    visible.forEach(v => {
      const row = v.row;
      const y = v.y;
      const isLong = (row.side==='long' || (row.distance_pct!=null && row.distance_pct<0));
      const col = isLong ? LIQ_COLORS.long : LIQ_COLORS.short;
      const usd = Number(row.usd)||0;
      const w = Math.max(2, Math.min(maxBar, usd/maxUsd*maxBar));
      const x0 = Math.round(plotRight - w);

      const magnets = data.magnets_list || [];
      const isMagnet = magnets.some(m => Number(m.price)===Number(row.price));

      if (isMagnet) {
        const hl = this._levelHighlights[row.price];
        const now = Date.now();
        let isHl=false, hlAlpha=0;
        if (hl && hl.highlightUntil && now < hl.highlightUntil) {
          isHl=true;
          hlAlpha = Math.max(0, Math.min(1, (hl.highlightUntil-now)/3000));
        }
        ctx.save();
        if (isHl) {
          ctx.globalAlpha = 0.6*hlAlpha+0.2;
          ctx.strokeStyle = "#ff2a5f";
          ctx.lineWidth = 2;
          ctx.shadowColor = "rgba(255,42,95,0.8)";
          ctx.shadowBlur = 8*hlAlpha;
          ctx.setLineDash([5,3]);
          ctx.beginPath(); ctx.moveTo(6, y+0.5); ctx.lineTo(xL, y+0.5); ctx.stroke();
          ctx.setLineDash([]); ctx.shadowBlur=0;
        } else {
          ctx.globalAlpha = 0.55;
          ctx.strokeStyle = col.ring;
          ctx.lineWidth = 1;
          ctx.setLineDash([5,3]);
          ctx.beginPath(); ctx.moveTo(6, y+0.5); ctx.lineTo(xL, y+0.5); ctx.stroke();
          ctx.setLineDash([]);
        }
        ctx.restore();
      }

      const hlBar = this._levelHighlights[row.price];
      const nowBar = Date.now();
      let hlActive=false, hlAlpha=0;
      if (hlBar && hlBar.highlightUntil && nowBar < hlBar.highlightUntil) {
        hlActive=true;
        hlAlpha = Math.max(0, Math.min(1, (hlBar.highlightUntil-nowBar)/3000));
      }
      if (hlActive) {
        ctx.globalAlpha = 0.35*hlAlpha+0.12;
        ctx.fillStyle = "#ff2a5f";
        ctx.fillRect(x0, y-barH/2, Math.round(w), barH);
        ctx.globalAlpha = 0.7*hlAlpha+0.2;
        ctx.strokeStyle = "#ff2a5f";
        ctx.lineWidth = 1;
        ctx.shadowColor = "rgba(255,42,95,0.9)";
        ctx.shadowBlur = 10*hlAlpha;
        ctx.strokeRect(x0+0.5, y-barH/2+0.5, Math.max(1, Math.round(w)-1), Math.max(1, barH-1));
        ctx.shadowBlur=0;
      } else {
        ctx.globalAlpha = isMagnet ? 0.18 : 0.12;
        ctx.fillStyle = col.fill;
        ctx.fillRect(x0, y-barH/2, Math.round(w), barH);
        ctx.globalAlpha = isMagnet ? 0.35 : 0.22;
        ctx.strokeStyle = col.fill;
        ctx.lineWidth = 1;
        ctx.strokeRect(x0+0.5, y-barH/2+0.5, Math.max(1, Math.round(w)-1), Math.max(1, barH-1));
      }
    });

    ctx.restore();

    // Also draw price lines for magnets as fallback (visible even if canvas clipped)
    try {
      const magnets = data.magnets_list || [];
      const maxLines = 20;
      let drawn = 0;
      for (const m of magnets) {
        if (drawn >= maxLines) break;
        const lv = Number(m && m.price);
        if (!(lv>0)) continue;
        // Check if already visible in canvas (we already have visible check)
        // Create price line
        try {
          const isLong = (m.side === 'long' || (m.distance_pct!=null && m.distance_pct<0));
          const color = isLong ? "#ff2d95" : "#00d6ff";
          const line = this.candleSeries.createPriceLine({
            price: lv,
            color: color,
            lineWidth: 1,
            lineStyle: 2, // dashed
            axisLabelVisible: true,
            title: '',
          });
          this._priceLines.push(line);
          drawn++;
        } catch {}
      }
    } catch {}
  }

  _checkDashedTriggers(price) {
    if (!this.layers.levelsEnabled || !this.layers.levelsAlertEnabled) return;
    const data = this.levelsData;
    if (!data) return;
    const magnets = data.magnets_list || [];
    if (!magnets.length) return;
    const now = Date.now();
    const p = Number(price);
    if (!(p>0)) return;
    const PCT = 0.0001;
    const COOLDOWN = 15000;
    const FADE = 3000;
    for (const m of magnets) {
      const lv = Number(m && m.price);
      if (!(lv>0)) continue;
      const key = String(lv);
      let st = this._levelHighlights[key];
      if (!st) st = this._levelHighlights[key] = { lastTrigger:0, inZone:false, highlightUntil:0 };
      const dist = Math.abs(p-lv)/lv;
      if (dist > PCT) { st.inZone=false; continue; }
      if (st.inZone) continue;
      if (now - st.lastTrigger < COOLDOWN) continue;
      st.lastTrigger = now;
      st.inZone = true;
      st.highlightUntil = now + FADE;
      try {
        if (window.AudioContext || window.webkitAudioContext) {
          const AudioCtx = window.AudioContext || window.webkitAudioContext;
          if (!this._audioCtx) this._audioCtx = new AudioCtx();
          const ac = this._audioCtx;
          if (ac.state==='suspended') ac.resume();
          const osc = ac.createOscillator();
          const gain = ac.createGain();
          osc.connect(gain); gain.connect(ac.destination);
          osc.type='sine';
          osc.frequency.setValueAtTime(880, ac.currentTime);
          osc.frequency.exponentialRampToValueAtTime(440, ac.currentTime+0.25);
          gain.gain.setValueAtTime(0.12, ac.currentTime);
          gain.gain.exponentialRampToValueAtTime(0.001, ac.currentTime+0.35);
          osc.start(); osc.stop(ac.currentTime+0.38);
        }
      } catch {}
      this._drawOverlays();
    }
  }

  _paintLayers() {
    if (!this.layersPop) return;
    this.layersPop.querySelectorAll('.panel-layer-btn').forEach(btn => {
      const key = btn.dataset.layer;
      if (!key) return;
      btn.classList.toggle('active', !!this.layers[key]);
    });
  }

  setSymbol(sym) {
    const v = this._validateSymbol(sym);
    if (!v) return false;
    if (v===this.symbol) {
      if (v==='ALL') this._emit('feedRequested', { symbol: v });
      return true;
    }
    const prev = this.symbol;
    this.symbol = v;
    if (this.symbolSelect) this.symbolSelect.value = v;
    this.candles = [];
    this.levelsData = null;
    this.levelsAt = 0;
    this.liqHist = {};
    this.liqHistAt = 0;
    this._liqHistKey = '';
    this._liqPending = null;
    this._liveLiqs = [];
    this._flowReload = false;
    this._bookHistSym = '';
    this.bookData = null;
    this.bookHist = [];
    this._levelHighlights = {};
    this._clearPriceLines();
    if (v!=='ALL') {
      this.loadCandles();
      this._syncLayerFeeds();
    } else {
      if (this.priceEl) this.priceEl.textContent = 'ALL';
      this._syncLayerFeeds();
    }
    this._emit('symbolChanged', { symbol: v, prev });
    this._emit('stateChanged', this.serialize());
    this._emit('feedRequested', { symbol: v });
    return true;
  }

  setTimeframe(tf) {
    const v = this._validateTf(tf);
    if (!v) return false;
    if (v===this.timeframe) return true;
    this.timeframe = v;
    if (this.tfSelect) this.tfSelect.value = String(v);
    this.candles = [];
    this.liqHist = {};
    this.liqHistAt = 0;
    this._liqHistKey = '';
    this._liqPending = null;
    this._flowReload = false;
    if (this.symbol!=='ALL') {
      this.loadCandles();
      this._syncLayerFeeds();
    }
    this._emit('timeframeChanged', { timeframe: v });
    this._emit('stateChanged', this.serialize());
    return true;
  }

  setLayers(patch) {
    let changed=false;
    for (const k in patch) {
      if (k in this.layers && this.layers[k]!==!!patch[k]) {
        this.layers[k]=!!patch[k];
        changed=true;
      }
    }
    if (changed) {
      this._paintLayers();
      if (!this.layers.levelsEnabled) {
        this.levelsData = null;
        this.levelsAt = 0;
        this._clearPriceLines();
      }
      if (!this.layers.liqEnabled && !this.layers.profileEnabled) {
        this.liqHist = {};
        this.liqHistAt = 0;
        this._liqHistKey = '';
      }
      this._syncLayerFeeds();
      this._emit('layersChanged', { layers: Object.assign({}, this.layers) });
      this._emit('stateChanged', this.serialize());
    }
    return changed;
  }

  serialize() {
    return {
      id: this.id,
      symbol: this.symbol,
      timeframe: this.timeframe,
      layers: Object.assign({}, this.layers),
      follow: !!this.follow,
      detached: !!this.detached,
      position: this.position || 0,
    };
  }

  setChrome(singleMode) {
    if (!this.root) return;
    const col = this.root.querySelector('.panel-collapse-btn');
    const exp = this.root.querySelector('.panel-expand-btn');
    if (col) col.classList.toggle('active', !!singleMode);
    if (exp) {
      exp.classList.toggle('active', !!singleMode);
      exp.title = singleMode ? 'График развёрнут' : 'Развернуть график';
    }
  }

  _defaultFollow() {
    try {
      const v = localStorage.getItem('liqscope.chartFollow');
      if (v === '0') return false;
      if (v === '1') return true;
    } catch {}
    try {
      if (window.state && typeof window.state.chartFollow === 'boolean') return window.state.chartFollow;
    } catch {}
    return true;
  }

  _paintFollow() {
    const btn = this.root && this.root.querySelector('.panel-follow-btn');
    if (!btn) return;
    btn.classList.toggle('active', !!this.follow);
    btn.title = this.follow ? 'Слежение за ценой включено' : 'Следить за ценой';
    btn.setAttribute('aria-pressed', this.follow ? 'true' : 'false');
  }

  setFollow(on) {
    this.follow = !!on;
    this._paintFollow();
    this._applyFollowMode();
    this._setFollowTicker();
    if (this.follow) this._anchorFollow();
    this._emit('stateChanged', this.serialize());
  }

  _followConsts() {
    const api = window.LiQScopeFollow || null;
    return {
      margin: api && api.margin != null ? api.margin : 0.15,
      edge: api && api.edgePct != null ? api.edgePct : 0.03,
      drift: api && api.driftPct != null ? api.driftPct : 0.005,
      hyst: api && api.hyst != null ? api.hyst : 0.01,
    };
  }

  _rightScale() {
    try {
      if (this.candleSeries && this.candleSeries.priceScale) {
        const s = this.candleSeries.priceScale();
        if (s && s.applyOptions) return s;
      }
    } catch {}
    try {
      const s = this.chart && this.chart.priceScale ? this.chart.priceScale('right') : null;
      if (s && s.applyOptions) return s;
    } catch {}
    return null;
  }

  _applyFollowMode() {
    if (!this.chart) return;
    const on = !!this.follow;
    const api = window.LiQScopeFollow || null;
    const opts = api && api.priceOptions
      ? api.priceOptions(on)
      : (on
        ? { autoScale: false, scaleMargins: { top: 0.15, bottom: 0.15 } }
        : { autoScale: true, scaleMargins: { top: 0.06, bottom: 0.24 } });
    const scale = this._rightScale();
    if (scale && scale.applyOptions) {
      try { scale.applyOptions(opts); } catch {}
    }
    try {
      this.chart.timeScale().applyOptions({
        rightOffset: on ? 0 : 6,
        shiftVisibleRangeOnNewBar: false,
      });
    } catch {}
  }

  _followRange(lr, last) {
    const c = this._followConsts();
    const api = window.LiQScopeFollow || null;
    if (api && api.range) return api.range(lr, last, c.edge, c.drift);
    if (!lr || !isFinite(lr.from) || !isFinite(lr.to) || last < 0) return null;
    const span = Number(lr.to) - Number(lr.from);
    if (!(span > 0)) return null;
    const keepN = Math.round(span * c.edge);
    const keep = keepN > 1 ? keepN : 1;
    const drift = Math.max(0, span * c.drift);
    const edge = Number(lr.to) - Number(last);
    if (edge <= keep + 0.05 && edge >= drift) return null;
    const to = Number(last) + keep;
    return { from: to - span, to };
  }

  _followPriceShift(cur, price, hyst) {
    const c = this._followConsts();
    if (!cur) return null;
    const from = Number(cur.from), to = Number(cur.to), p = Number(price);
    if (!isFinite(from) || !isFinite(to) || !isFinite(p) || !(to > from)) return null;
    const span = to - from;
    let m = Number(c.margin);
    if (!isFinite(m) || m < 0) m = 0;
    m = Math.min(m, 0.4);
    const pad = span * m;
    let hy = Number(hyst);
    if (!isFinite(hy) || hy < 0) hy = 0;
    const eps = span * hy;
    if (p > to - pad + eps) {
      const nTo = p + pad;
      return { from: nTo - span, to: nTo };
    }
    if (p < from + pad - eps) {
      const nFrom = p - pad;
      return { from: nFrom, to: nFrom + span };
    }
    return null;
  }

  _followPriceFit(band, price) {
    const c = this._followConsts();
    const p = Number(price);
    const hasPrice = price !== null && price !== undefined && isFinite(p);
    if (!band && !hasPrice) return null;
    let low = band ? Number(band.low) : p;
    let high = band ? Number(band.high) : p;
    if (hasPrice) { low = Math.min(low, p); high = Math.max(high, p); }
    if (!isFinite(low) || !isFinite(high)) return null;
    if (high <= low) {
      const pad0 = Math.max(Math.abs(high) * 1e-4, 1e-9);
      low -= pad0; high += pad0;
    }
    let m = Number(c.margin);
    if (!isFinite(m) || m < 0) m = 0;
    m = Math.min(m, 0.4);
    const need = (high - low) / (1 - 2 * m);
    const mid = (low + high) / 2;
    return { from: mid - need / 2, to: mid + need / 2 };
  }

  _visibleBand() {
    const candles = this.candles || [];
    if (!candles.length || !this.chart) return null;
    let from = 0, to = candles.length - 1;
    try {
      const lr = this.chart.timeScale().getVisibleLogicalRange();
      if (lr && isFinite(lr.from) && isFinite(lr.to)) {
        from = Math.max(0, Math.floor(lr.from));
        to = Math.min(candles.length - 1, Math.ceil(lr.to));
      }
    } catch {}
    if (to < from) return null;
    let low = Infinity, high = -Infinity;
    for (let i = from; i <= to; i++) {
      const bar = candles[i];
      if (!bar) continue;
      const lo = Number(bar.low), hi = Number(bar.high);
      if (isFinite(lo) && lo < low) low = lo;
      if (isFinite(hi) && hi > high) high = hi;
    }
    if (!isFinite(low) || !isFinite(high)) return null;
    return { low, high };
  }

  _lastPrice() {
    if (this.price != null && isFinite(Number(this.price))) return Number(this.price);
    const last = this.candles && this.candles[this.candles.length - 1];
    const p = last ? Number(last.close) : NaN;
    return isFinite(p) ? p : null;
  }

  _followPrice(hyst) {
    if (!this.follow || !this.chart) return false;
    const scale = this._rightScale();
    if (!scale || !scale.setVisibleRange) return false;
    const price = this._lastPrice();
    if (price === null) return false;
    let cur = null;
    try { cur = scale.getVisibleRange ? scale.getVisibleRange() : null; } catch { cur = null; }
    const ok = !!cur && isFinite(cur.from) && isFinite(cur.to) && Number(cur.to) > Number(cur.from);
    let next = null;
    if (ok) {
      const span = Number(cur.to) - Number(cur.from);
      const far = price < Number(cur.from) - span || price > Number(cur.to) + span;
      next = far ? this._followPriceFit(this._visibleBand(), price) : this._followPriceShift(cur, price, hyst);
    } else {
      next = this._followPriceFit(this._visibleBand(), price);
    }
    if (!next) return false;
    try { scale.setVisibleRange(next); } catch { return false; }
    return true;
  }

  _followStep() {
    if (!this.follow || this._followHold || !this.chart || !this.candles.length) return false;
    let lr = null;
    try { lr = this.chart.timeScale().getVisibleLogicalRange(); } catch { return false; }
    const next = this._followRange(lr, this.candles.length - 1);
    let moved = false;
    if (next) {
      try { this.chart.timeScale().setVisibleLogicalRange(next); moved = true; } catch {}
    }
    return this._followPrice(this._followConsts().hyst) || moved;
  }

  _anchorFollow() {
    if (!this.follow || !this.chart || !this.candles.length) return false;
    let lr = null;
    try { lr = this.chart.timeScale().getVisibleLogicalRange(); } catch { lr = null; }
    const span = lr && lr.to > lr.from ? (lr.to - lr.from) : 80;
    const c = this._followConsts();
    const keepN = Math.round(span * c.edge);
    const keep = keepN > 1 ? keepN : 1;
    const to = this.candles.length - 1 + keep;
    let moved = false;
    try { this.chart.timeScale().setVisibleLogicalRange({ from: to - span, to }); moved = true; } catch {}
    return this._followPrice(0) || moved;
  }

  _setFollowTicker() {
    if (this._followTimer) { clearInterval(this._followTimer); this._followTimer = null; }
    if (this.follow && !this._destroyed) {
      this._followTimer = setInterval(() => {
        if (this.follow && !this._followHold && !this._destroyed) this._followStep();
      }, 500);
    }
  }

  _bindFollowGestures() {
    if (this._followBound || !this.chartEl) return;
    this._followBound = true;
    const el = this.chartEl;
    this._onFollowHold = () => {
      this._followHold = true;
      if (this._followRelease) { clearTimeout(this._followRelease); this._followRelease = null; }
    };
    this._onFollowRelease = () => {
      if (!this._followHold) return;
      this._followHold = false;
      if (this._followRelease) clearTimeout(this._followRelease);
      if (!this.follow) return;
      this._followRelease = setTimeout(() => {
        this._followRelease = null;
        this._anchorFollow();
      }, 350);
    };
    this._onFollowWheel = () => {
      this._followHold = true;
      if (this._followRelease) clearTimeout(this._followRelease);
      this._followRelease = setTimeout(() => {
        this._followRelease = null;
        this._followHold = false;
        if (this.follow) this._anchorFollow();
      }, 350);
    };
    el.addEventListener('pointerdown', this._onFollowHold, true);
    el.addEventListener('wheel', this._onFollowWheel, { passive: true, capture: true });
    window.addEventListener('pointerup', this._onFollowRelease);
    window.addEventListener('pointercancel', this._onFollowRelease);
  }

  _startFollow() {
    this._paintFollow();
    this._bindFollowGestures();
    this._applyFollowMode();
    this._setFollowTicker();
  }

  setDetached(detached) {
    this.detached = !!detached;
    if (this.root) this.root.classList.toggle('detached', this.detached);
  }

  setActive(active) {
    if (this.root) this.root.classList.toggle('active-panel', !!active);
  }

  onPriceUpdate(symbol, price) {
    if (this.symbol==='ALL') return;
    if (symbol!==this.symbol) return;
    this._updatePriceDisplay(price);
    if (this.candleSeries && this.candles.length) {
      const last = this.candles[this.candles.length-1];
      if (last) {
        const newClose = Number(price);
        if (isFinite(newClose)) {
          const updated = Object.assign({}, last, { close: newClose });
          this.candles[this.candles.length-1] = updated;
          try { this.candleSeries.update(updated); } catch {}
        }
      }
    }
    if (this.follow && !this._followHold) this._followStep();
  }

  onCandleUpdate(symbol, tf, candle) {
    if (this.symbol==='ALL') return;
    if (symbol!==this.symbol) return;
    if (Number(tf)!==Number(this.timeframe)) return;
    if (!candle) return;
    const prevIdx = this.candles.findIndex(c => {
      const raw = Number(candle.time || candle.t || 0);
      const t = raw > 1e12 ? Math.floor(raw / 1000) : raw;
      return c.time === t;
    });
    const bar = this._normalizeBar(candle, prevIdx >= 0 ? this.candles[prevIdx] : null);
    if (!bar || !bar.time || !isFinite(bar.open)) return;
    const idx = this.candles.findIndex(c => c.time===bar.time);
    if (idx>=0) {
      this.candles[idx]=bar;
      try { this.candleSeries.update(bar); } catch {}
    } else {
      this.candles.push(bar);
      this.candles.sort((a,b)=>a.time-b.time);
      try { this.candleSeries.setData(this.candles); } catch {}
    }
    this._updatePriceDisplay(bar.close);
    if (this.follow && !this._followHold) this._followStep();
    if (this.layers.cvdEnabled || this.layers.oiEnabled || this.layers.liqEnabled || this.layers.profileEnabled) {
      this._drawOverlays();
    }
  }

  onLiquidation(item) {
    if (!item || this.symbol === 'ALL' || item.symbol !== this.symbol) return;
    if (!this._liveLiqs) this._liveLiqs = [];
    this._liveLiqs.push(item);
    if (this._liveLiqs.length > 500) this._liveLiqs.splice(0, this._liveLiqs.length - 500);
    if (this.layers.liqEnabled || this.layers.profileEnabled) this._drawOverlays();
  }

  unmount() {
    this._destroyed = true;
    if (this._followTimer) { try { clearInterval(this._followTimer); } catch {} this._followTimer = null; }
    if (this._followRelease) { try { clearTimeout(this._followRelease); } catch {} this._followRelease = null; }
    if (this._followBound) {
      try { this.chartEl && this.chartEl.removeEventListener('pointerdown', this._onFollowHold, true); } catch {}
      try { this.chartEl && this.chartEl.removeEventListener('wheel', this._onFollowWheel, true); } catch {}
      try { window.removeEventListener('pointerup', this._onFollowRelease); } catch {}
      try { window.removeEventListener('pointercancel', this._onFollowRelease); } catch {}
    }
    if (this._ro) { try { this._ro.disconnect(); } catch {} }
    if (this._levelsTimer) { try { clearInterval(this._levelsTimer); } catch {} }
    this._stopBookPoll();
    window.removeEventListener('resize', this._boundResize);
    if (this.chart) {
      try { this.chart.remove(); } catch {}
      this.chart=null;
    }
    if (this.root && this.root.parentNode) {
      this.root.parentNode.removeChild(this.root);
    }
    this.root=null;
  }

  destroy() { this.unmount(); }
}

window.ChartPanel = ChartPanel;
