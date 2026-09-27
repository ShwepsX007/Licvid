/**
 * ChartPanel — isolated chart instance for multi-chart workspace.
 * Robust init: waits for visible size, retries, shows errors.
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

    this.chart = null;
    this.candleSeries = null;
    this.volumeSeries = null;
    this.clusterCanvas = null;
    this.drawCanvas = null;
    this.candles = [];
    this.levelsData = null;
    this.levelsAt = 0;
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
        <button class="panel-btn panel-expand-btn" title="Expand to fullscreen">⛶</button>
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
      <button class="panel-layer-btn" data-layer="levelsEnabled">🎯 Уровни (оценка)</button>
      <button class="panel-layer-btn" data-layer="levelsAlertEnabled">🔔 Сигнал уровней</button>
      <button class="panel-layer-btn" data-layer="liqEnabled">⚡ Ликвидации</button>
      <button class="panel-layer-btn" data-layer="cvdEnabled">🎯 CVD</button>
      <button class="panel-layer-btn" data-layer="oiEnabled">● OI</button>
      <button class="panel-layer-btn" data-layer="bookEnabled">📖 Стакан</button>
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
    if (this.layers.levelsEnabled) this.loadLevels();
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
        try { this.chart.timeScale().fitContent(); } catch {}
        this._clearError();
      } catch {}
    }

    this._clearError();
    this.resize();
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
      const bars = candles.map(c => {
        let t = Number(c.time || c.t || c.timestamp || 0);
        if (t > 1e12) t = Math.floor(t/1000);
        return {
          time: t,
          open: Number(c.open || c.o),
          high: Number(c.high || c.h),
          low: Number(c.low || c.l),
          close: Number(c.close || c.c),
          volume: Number(c.volume || c.v || 0),
        };
      }).filter(b => b.time && isFinite(b.open) && isFinite(b.close)).sort((a,b)=>a.time-b.time);

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
          try { this.chart.timeScale().fitContent(); } catch {}
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
    this._drawOverlays();
  }

  async loadLevels() {
    if (!this.layers.levelsEnabled && !this.layers.liqEnabled) return;
    if (this.symbol === 'ALL') return;
    const sym = this.symbol;
    try {
      const price = this.price || (this.candles.length ? this.candles[this.candles.length-1].close : 0);
      const url = `/api/liq_levels?symbol=${encodeURIComponent(sym)}${price?`&price=${price}`:''}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error('levels fetch failed');
      const data = await r.json();
      this.levelsData = data;
      this.levelsAt = Date.now();
      this._drawOverlays();
    } catch (e) {
      // ignore
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

  _drawOverlays() {
    if (!this.clusterCanvas || !this.chart || !this.candleSeries) return;
    const canvas = this.clusterCanvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0,0,W,H);
    if ((!this.layers.levelsEnabled && !this.layers.liqEnabled) || !this.levelsData) return;
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
  }

  _checkDashedTriggers(price) {
    if ((!this.layers.levelsEnabled && !this.layers.liqEnabled) || !this.layers.levelsAlertEnabled) return;
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
    this._levelHighlights = {};
    if (v!=='ALL') {
      this.loadCandles();
      if (this.layers.levelsEnabled) this.loadLevels();
    } else {
      if (this.priceEl) this.priceEl.textContent = 'ALL';
      if (this.clusterCanvas) {
        const ctx = this.clusterCanvas.getContext('2d');
        if (ctx) ctx.clearRect(0,0,this.clusterCanvas.width,this.clusterCanvas.height);
      }
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
    if (this.symbol!=='ALL') this.loadCandles();
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
      if (patch.levelsEnabled && !this.levelsData) this.loadLevels();
      if (!this.layers.levelsEnabled) {
        if (this.clusterCanvas) {
          const ctx = this.clusterCanvas.getContext('2d');
          if (ctx) ctx.clearRect(0,0,this.clusterCanvas.width,this.clusterCanvas.height);
        }
      } else {
        this._drawOverlays();
      }
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
      detached: !!this.detached,
      position: this.position || 0,
    };
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
  }

  onCandleUpdate(symbol, tf, candle) {
    if (this.symbol==='ALL') return;
    if (symbol!==this.symbol) return;
    if (Number(tf)!==Number(this.timeframe)) return;
    if (!candle) return;
    let t = Number(candle.time || candle.t || 0);
    if (t>1e12) t = Math.floor(t/1000);
    const bar = {
      time: t,
      open: Number(candle.open || candle.o),
      high: Number(candle.high || candle.h),
      low: Number(candle.low || candle.l),
      close: Number(candle.close || candle.c),
    };
    if (!bar.time || !isFinite(bar.open)) return;
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
  }

  unmount() {
    this._destroyed = true;
    if (this._ro) { try { this._ro.disconnect(); } catch {} }
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
