/**
 * ChartPanel — isolated chart instance for multi-chart workspace.
 * Each panel is like a separate terminal.
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
      levelsEnabled: false,
      levelsAlertEnabled: true,
      liqEnabled: true,
      cvdEnabled: false,
      oiEnabled: false,
      bookEnabled: false,
      profileEnabled: false,
    }, def.layers || {});
    this.detached = !!def.detached;
    this.position = def.position || 0;

    // runtime
    this.chart = null;
    this.candleSeries = null;
    this.volumeSeries = null;
    this.clusterCanvas = null;
    this.drawCanvas = null;
    this.candles = [];
    this.prices = {};
    this.levelsData = null;
    this.levelsAt = 0;
    this.levelsError = '';
    this.price = null;
    this.changePct = null;
    this._levelHighlights = {};
    this._raf = null;
    this._destroyed = false;
    this._boundResize = () => this.resize();
    this._resizeAttempts = 0;

    // DOM refs
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
        <button class="panel-btn panel-expand-btn" title="Expand to single view">⛶</button>
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

    // events
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
    this._initChart();

    // retry init if container was 0 width
    setTimeout(() => this._ensureChartSize(), 200);
    setTimeout(() => this._ensureChartSize(), 800);
    setTimeout(() => this._ensureChartSize(), 2000);

    this.loadCandles();
    if (this.layers.levelsEnabled) this.loadLevels();

    window.addEventListener('resize', this._boundResize);
    // click to activate and set feed
    root.addEventListener('mousedown', () => {
      this._emit('activated', { id: this.id });
    });
    root.addEventListener('click', () => {
      // clicking panel sets feed to its symbol, unless ALL
      this._emit('feedRequested', { symbol: this.symbol });
    });
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
      symbols = ['BTC_USDT','ETH_USDT','SOL_USDT','XRP_USDT','DOGE_USDT','ADA_USDT','AVAX_USDT','LINK_USDT','LTC_USDT','BCH_USDT'];
    }
    // add ALL option for feed
    const allOpt = document.createElement('option');
    allOpt.value = 'ALL';
    allOpt.textContent = 'ALL';
    sel.appendChild(allOpt);

    if (!symbols.includes(this.symbol) && this.symbol !== 'ALL') symbols.unshift(this.symbol);
    symbols.slice(0, 150).forEach(sym => {
      if (sym === 'ALL') return;
      const opt = document.createElement('option');
      opt.value = sym;
      opt.textContent = sym.replace('_','/');
      sel.appendChild(opt);
    });
  }

  _ensureChartSize() {
    if (!this.chartEl) return;
    const w = this.chartEl.clientWidth;
    const h = this.chartEl.clientHeight;
    if (w < 50 || h < 50) {
      if (this._resizeAttempts < 10) {
        this._resizeAttempts++;
        setTimeout(() => this._ensureChartSize(), 300);
      }
      return;
    }
    if (!this.chart) {
      this._initChart();
    } else {
      this.resize();
    }
  }

  _initChart() {
    if (this._destroyed) return;
    const container = this.chartEl;
    if (!container) return;
    // if already has chart, remove
    if (this.chart) {
      try { this.chart.remove(); } catch {}
      this.chart = null;
    }
    // keep error overlay if exists
    const existingError = container.querySelector('.panel-error');
    container.innerHTML = '';
    if (existingError) container.appendChild(existingError);

    if (typeof window.LightweightCharts === 'undefined') {
      container.innerHTML = '<div style="padding:20px;color:#6a7a8e">Chart lib not loaded</div>';
      return;
    }
    const width = container.clientWidth || 400;
    const height = container.clientHeight || 320;
    if (width < 10 || height < 10) {
      setTimeout(() => this._ensureChartSize(), 300);
      return;
    }

    let chart;
    try {
      chart = window.LightweightCharts.createChart(container, {
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
        crosshair: { mode: window.LightweightCharts.CrosshairMode ? window.LightweightCharts.CrosshairMode.Normal : 0 },
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
      container.innerHTML = `<div style="padding:12px;color:#ff6a7a;font-size:11px">Chart init failed: ${this._esc(e.message||e)}</div>`;
      return;
    }
    this.chart = chart;

    let candleSeries = null;
    try {
      const helper = window.LiqScopeApp && window.LiqScopeApp.createCandleSeries;
      if (helper) {
        candleSeries = helper(chart, {
          upColor: "#00e676",
          downColor: "#ff2a5f",
          borderVisible: false,
          wickUpColor: "#00e676",
          wickDownColor: "#ff2a5f",
        });
      } else if (window.LightweightCharts && window.LightweightCharts.CandlestickSeries && chart.addSeries) {
        candleSeries = chart.addSeries(window.LightweightCharts.CandlestickSeries, {
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
    }
    this.candleSeries = candleSeries;

    try {
      const vHelper = window.LiqScopeApp && window.LiqScopeApp.createVolumeSeries;
      if (vHelper && candleSeries) {
        this.volumeSeries = vHelper(chart, {
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

    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(container);
    }

    if (this.candles && this.candles.length && this.candleSeries) {
      try { this.candleSeries.setData(this.candles); } catch {}
    }

    setTimeout(() => this.resize(), 100);
  }

  resize() {
    if (!this.chart || !this.chartEl) return;
    try {
      const w = this.chartEl.clientWidth || 400;
      const h = this.chartEl.clientHeight || 320;
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
      const r = await fetch(`/api/klines?symbol=${encodeURIComponent(sym)}&timeframe=${tf}`);
      if (!r.ok) throw new Error('klines fetch failed ' + r.status);
      const data = await r.json();
      let candles = data.candles || data || [];
      if (!Array.isArray(candles)) {
        if (data.candles && typeof data.candles === 'object') {
          candles = Object.values(data.candles);
        } else {
          return;
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

      this.candles = bars;
      if (this.candleSeries && bars.length) {
        try {
          this.candleSeries.setData(bars);
          if (this.chart && this.chart.timeScale) {
            try { this.chart.timeScale().fitContent(); } catch {}
          }
        } catch (e) {
          console.debug('setData failed', e);
        }
      }
      if (bars.length) {
        const last = bars[bars.length-1];
        this._updatePriceDisplay(last.close);
      }
      this._drawOverlays();
      // clear error
      if (this.chartEl) {
        const err = this.chartEl.querySelector('.panel-error');
        if (err) err.remove();
      }
    } catch (e) {
      console.debug('panel loadCandles failed', sym, e);
      if (this.chartEl && !this.candles.length) {
        let errEl = this.chartEl.querySelector('.panel-error');
        if (!errEl) {
          errEl = document.createElement('div');
          errEl.className = 'panel-error';
          errEl.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#6a7a8e;font-size:12px;z-index:10;background:rgba(9,12,16,0.8)';
          this.chartEl.appendChild(errEl);
        }
        errEl.textContent = `Failed to load ${sym}: ${String(e.message||e)}`;
        setTimeout(() => this._initChart(), 2000);
      }
    }
  }

  async loadLevels() {
    if (!this.layers.levelsEnabled) return;
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
      this.levelsError = String(e);
    }
  }

  _esc(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
    if (!this.layers.levelsEnabled || !this.levelsData) return;
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
    this._levelHighlights = {};
    if (v!=='ALL') {
      this.loadCandles();
      if (this.layers.levelsEnabled) this.loadLevels();
    } else {
      // keep previous candles but show ALL in price
      if (this.priceEl) this.priceEl.textContent = 'ALL';
      // clear overlays
      if (this.clusterCanvas) {
        const ctx = this.clusterCanvas.getContext('2d');
        if (ctx) ctx.clearRect(0,0,this.clusterCanvas.width,this.clusterCanvas.height);
      }
      // restore candles from previous? keep empty for now, but not destroy chart
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
