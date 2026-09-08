/**
 * Licvid Terminal - Cluster Footprint Chart & Stream App
 */

document.addEventListener("DOMContentLoaded", () => {
    let currentSymbol = "BTC_USDT";
    let currentTimeframe = 5;
    let minUsdFilter = 5000;
    let exchangeFilter = "ALL";
    let soundEnabled = true;

    let ws = null;
    let chart = null;
    let candlestickSeries = null;
    let volumeSeries = null;

    let candlesData = [];
    let liquidationHistory = [];
    let audioCtx = null;

    // DOM Elements
    const connStatusEl = document.getElementById("conn-status");
    const symbolTitleEl = document.getElementById("current-symbol-title");
    const livePriceEl = document.getElementById("current-price");
    const priceChangeEl = document.getElementById("price-change-pct");
    const feedTbody = document.getElementById("feed-tbody");
    const feedCountEl = document.getElementById("feed-count");
    const minUsdSelect = document.getElementById("min-usd-filter");
    const exchangeSelect = document.getElementById("exchange-filter");
    const soundToggleBtn = document.getElementById("sound-toggle-btn");
    const soundIcon = document.getElementById("sound-icon");
    const clearClustersBtn = document.getElementById("clear-clusters-btn");
    const topCoinsContainer = document.getElementById("top-coins-list");
    const profileBarsContainer = document.getElementById("profile-bars-container");
    const chartWrapper = document.getElementById("chart-wrapper");
    const clusterCanvas = document.getElementById("cluster-canvas");

    const stat24hTotalEl = document.getElementById("stat-24h-total");
    const stat1hTotalEl = document.getElementById("stat-1h-total");
    const longPctEl = document.getElementById("long-pct");
    const shortPctEl = document.getElementById("short-pct");
    const longRatioBar = document.getElementById("long-ratio-bar");

    const detailModal = document.getElementById("detail-modal");
    const closeModalBtn = document.getElementById("close-modal");
    const modalTitle = document.getElementById("modal-title");
    const modalBody = document.getElementById("modal-body");

    function initAudio() {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
    }

    function playLiquidationSound(usd, side) {
        if (!soundEnabled) return;
        try {
            initAudio();
            if (audioCtx.state === 'suspended') audioCtx.resume();
            
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.connect(gain);
            gain.connect(audioCtx.destination);

            if (usd >= 100000) {
                osc.type = 'sine';
                osc.frequency.setValueAtTime(150, audioCtx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(45, audioCtx.currentTime + 0.5);
                gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.5);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.5);
            } else {
                const freq = side === 'BUY' ? 800 : 420;
                osc.type = 'triangle';
                osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
                gain.gain.setValueAtTime(0.04, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.12);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.12);
            }
        } catch (e) {}
    }

    // --- Init Chart ---
    function initChart() {
        const container = document.getElementById("tv-chart-container");
        container.innerHTML = "";

        const width = container.clientWidth || chartWrapper.clientWidth || 800;
        const height = container.clientHeight || chartWrapper.clientHeight || 500;

        if (typeof window.LightweightCharts === 'undefined') {
            console.error("LightweightCharts library missing!");
            container.innerHTML = `<div style="padding:20px; color:#ff2a5f;">Ошибка загрузки графика TradingView.</div>`;
            return;
        }

        chart = LightweightCharts.createChart(container, {
            width: width,
            height: height,
            layout: {
                background: { type: 'solid', color: '#090c10' },
                textColor: '#8493a8',
                fontSize: 12,
                fontFamily: 'Inter, sans-serif'
            },
            grid: {
                vertLines: { color: '#18202c' },
                horzLines: { color: '#18202c' }
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: '#212938',
                scaleMargins: { top: 0.05, bottom: 0.22 }
            },
            timeScale: {
                borderColor: '#212938',
                timeVisible: true,
                secondsVisible: false,
            }
        });

        candlestickSeries = chart.addCandlestickSeries({
            upColor: '#00e676',
            downColor: '#ff2a5f',
            borderVisible: false,
            wickUpColor: '#00e676',
            wickDownColor: '#ff2a5f',
        });

        volumeSeries = chart.addHistogramSeries({
            color: '#26a69a',
            priceFormat: { type: 'volume' },
            priceScaleId: '',
            scaleMargins: { top: 0.82, bottom: 0.01 }
        });

        const handleResize = () => {
            if (chart && container) {
                const w = container.clientWidth || chartWrapper.clientWidth || 800;
                const h = container.clientHeight || chartWrapper.clientHeight || 500;
                chart.applyOptions({ width: w, height: h });
                if (clusterCanvas) {
                    clusterCanvas.width = w;
                    clusterCanvas.height = h;
                }
                drawClustersOnCanvas();
            }
        };

        if (window.ResizeObserver) {
            const ro = new ResizeObserver(() => handleResize());
            ro.observe(container);
            ro.observe(chartWrapper);
        }
        window.addEventListener('resize', handleResize);
        setTimeout(handleResize, 100);

        chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
            drawClustersOnCanvas();
        });
    }

    // --- Load Historical Candles ---
    async function loadCandles() {
        try {
            const resp = await fetch(`/api/klines?symbol=${currentSymbol}&timeframe=${currentTimeframe}`);
            const data = await resp.json();
            if (data && data.candles) {
                candlesData = data.candles;
                
                const formattedCandles = candlesData.map(c => ({
                    time: c.time,
                    open: c.open,
                    high: c.high,
                    low: c.low,
                    close: c.close
                }));
                const formattedVol = candlesData.map(c => ({
                    time: c.time,
                    value: c.volume,
                    color: c.close >= c.open ? 'rgba(0, 230, 118, 0.35)' : 'rgba(255, 42, 95, 0.35)'
                }));

                if (candlestickSeries) {
                    candlestickSeries.setData(formattedCandles);
                    volumeSeries.setData(formattedVol);
                }

                if (formattedCandles.length > 0) {
                    const last = formattedCandles[formattedCandles.length - 1];
                    updatePriceDisplay(last.close, formattedCandles[0].open);
                }
                
                updateChartMarkers();
                drawClustersOnCanvas();
            }
        } catch (e) {
            console.error("Failed to fetch klines:", e);
        }
    }

    function updatePriceDisplay(price, firstOpenPrice) {
        livePriceEl.innerText = formatPrice(price, currentSymbol);
        if (firstOpenPrice) {
            const diff = price - firstOpenPrice;
            const pct = (diff / firstOpenPrice) * 100;
            const sign = pct >= 0 ? "+" : "";
            priceChangeEl.innerText = `${sign}${pct.toFixed(2)}%`;
            priceChangeEl.className = `price-change ${pct >= 0 ? 'positive' : 'negative'}`;
        }
    }

    // --- TradingView Standard Markers Fallback ---
    function updateChartMarkers() {
        if (!candlestickSeries) return;

        const filtered = liquidationHistory.filter(item => {
            if (item.symbol !== currentSymbol) return false;
            if (exchangeFilter !== "ALL" && item.exchange !== exchangeFilter) return false;
            if (item.usd < minUsdFilter) return false;
            return true;
        });

        const tfSec = currentTimeframe * 60;
        const markers = [];

        filtered.slice(-60).forEach(item => {
            const candleTime = Math.floor(item.timestamp / tfSec) * tfSec;
            const isLong = item.side === "SELL";
            const isWhale = item.usd >= 100000;

            let color = isLong ? '#ff2a5f' : '#00e676';
            let shape = isLong ? 'arrowDown' : 'arrowUp';
            let text = `$${formatUsdShort(item.usd)}`;

            if (isWhale) {
                color = '#ffc107';
                shape = 'circle';
                text = `🔥 $${formatUsdShort(item.usd)}`;
            }

            markers.push({
                time: candleTime,
                position: isLong ? 'aboveBar' : 'belowBar',
                color: color,
                shape: shape,
                text: text,
                id: item.id
            });
        });

        candlestickSeries.setMarkers(markers);
    }

    // --- Canvas Footprint Clusters INSIDE Candle Body ---
    function drawClustersOnCanvas() {
        if (!clusterCanvas || !chart || !candlestickSeries) return;
        const ctx = clusterCanvas.getContext("2d");
        ctx.clearRect(0, 0, clusterCanvas.width, clusterCanvas.height);

        const tfSec = currentTimeframe * 60;

        const filtered = liquidationHistory.filter(item => {
            if (item.symbol !== currentSymbol) return false;
            if (exchangeFilter !== "ALL" && item.exchange !== exchangeFilter) return false;
            if (item.usd < minUsdFilter) return false;
            return true;
        });

        const clusters = {};

        filtered.forEach(item => {
            const candleTime = Math.floor(item.timestamp / tfSec) * tfSec;
            const key = `${candleTime}_${item.price.toFixed(priceDecimals(currentSymbol))}`;
            if (!clusters[key]) {
                clusters[key] = {
                    time: candleTime,
                    price: item.price,
                    side: item.side,
                    longUsd: 0,
                    shortUsd: 0,
                    totalUsd: 0,
                    count: 0
                };
            }
            if (item.side === "SELL") {
                clusters[key].longUsd += item.usd;
            } else {
                clusters[key].shortUsd += item.usd;
            }
            clusters[key].totalUsd += item.usd;
            clusters[key].count += 1;
        });

        Object.values(clusters).forEach(c => {
            const x = chart.timeScale().timeToCoordinate(c.time);
            const y = candlestickSeries.priceToCoordinate(c.price);

            if (x !== null && y !== null && x >= 0 && x <= clusterCanvas.width && y >= 0 && y <= clusterCanvas.height) {
                const isWhale = c.totalUsd >= 100000;
                const isLong = c.longUsd >= c.shortUsd;
                
                let radius = Math.min(Math.max(Math.log10(c.totalUsd) * 3.5, 8), 22);

                ctx.save();
                ctx.beginPath();
                ctx.arc(x, y, radius, 0, 2 * Math.PI);

                if (isWhale) {
                    ctx.fillStyle = "rgba(255, 193, 7, 0.85)";
                    ctx.strokeStyle = "#ffffff";
                    ctx.lineWidth = 2;
                    ctx.shadowColor = "rgba(255, 193, 7, 0.9)";
                    ctx.shadowBlur = 10;
                } else if (isLong) {
                    ctx.fillStyle = "rgba(255, 42, 95, 0.8)";
                    ctx.strokeStyle = "rgba(255, 42, 95, 1)";
                    ctx.lineWidth = 1;
                } else {
                    ctx.fillStyle = "rgba(0, 230, 118, 0.8)";
                    ctx.strokeStyle = "rgba(0, 230, 118, 1)";
                    ctx.lineWidth = 1;
                }

                ctx.fill();
                ctx.stroke();
                ctx.shadowBlur = 0;

                ctx.fillStyle = "#ffffff";
                ctx.font = "bold 9px 'JetBrains Mono', monospace";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                const labelText = `$${formatUsdShort(c.totalUsd)}`;
                ctx.fillText(labelText, x, y);

                ctx.restore();
            }
        });
    }

    // --- Profile Sidebar ---
    function updateProfileSidebar() {
        const filtered = liquidationHistory.filter(x => x.symbol === currentSymbol);
        if (filtered.length === 0) {
            profileBarsContainer.innerHTML = `<div style="text-align:center; color:#8493a8; font-size:0.68rem; padding:8px;">Нет данных</div>`;
            return;
        }

        const prices = filtered.map(x => x.price);
        const minP = Math.min(...prices);
        const maxP = Math.max(...prices);
        if (minP === maxP) return;

        const binCount = 8;
        const step = (maxP - minP) / binCount;
        const bins = [];

        for (let i = 0; i < binCount; i++) {
            bins.push({
                low: minP + i * step,
                high: minP + (i + 1) * step,
                longUsd: 0,
                shortUsd: 0,
                totalUsd: 0
            });
        }

        filtered.forEach(x => {
            const bIndex = Math.min(Math.floor((x.price - minP) / step), binCount - 1);
            if (bIndex >= 0 && bIndex < binCount) {
                if (x.side === "SELL") bins[bIndex].longUsd += x.usd;
                else bins[bIndex].shortUsd += x.usd;
                bins[bIndex].totalUsd += x.usd;
            }
        });

        const maxBinUsd = Math.max(...bins.map(b => b.totalUsd), 1);

        let html = "";
        for (let i = binCount - 1; i >= 0; i--) {
            const b = bins[i];
            const midP = (b.low + b.high) / 2;
            const longPct = (b.longUsd / maxBinUsd) * 100;
            const shortPct = (b.shortUsd / maxBinUsd) * 100;

            html += `
                <div class="profile-row">
                    <span class="profile-price">${formatPrice(midP, currentSymbol)}</span>
                    <div class="profile-bar-wrapper">
                        <div class="profile-bar-long" style="width: ${longPct}%"></div>
                        <div class="profile-bar-short" style="width: ${shortPct}%"></div>
                    </div>
                    <span class="profile-vol">$${formatUsdShort(b.totalUsd)}</span>
                </div>
            `;
        }
        profileBarsContainer.innerHTML = html;
    }

    // --- Live Stream Table Feed ---
    function addFeedRow(item, animate = true) {
        if (currentSymbol !== "ALL" && item.symbol !== currentSymbol) return;
        if (exchangeFilter !== "ALL" && item.exchange !== exchangeFilter) return;
        if (item.usd < minUsdFilter) return;

        const isLong = item.side === "SELL";
        const isWhale = item.usd >= 100000;
        const sideText = isLong ? "LONG LIQ" : "SHORT LIQ";
        const sideClass = isLong ? "long" : "short";

        const tr = document.createElement("tr");
        if (animate) {
            tr.className = isWhale ? "feed-row-whale" : "feed-row-new";
        }

        const date = new Date(item.timestamp * 1000);
        const timeStr = date.toLocaleTimeString();

        let valClass = isLong ? "long-val" : "short-val";
        if (isWhale) valClass = "whale-val";

        tr.innerHTML = `
            <td style="color: var(--text-muted);">${timeStr}</td>
            <td><strong>${item.symbol.replace('_', '/')}</strong></td>
            <td><span class="exch-badge">${item.exchange}</span></td>
            <td><span class="badge-side ${sideClass}">${sideText}</span></td>
            <td class="td-usd-amount ${valClass}">
                $${formatUsdNumber(item.usd)}
            </td>
            <td style="font-weight: 600;">${formatPrice(item.price, item.symbol)}</td>
        `;

        tr.addEventListener("click", () => openDetailModal(item));

        feedTbody.insertBefore(tr, feedTbody.firstChild);

        while (feedTbody.children.length > 100) {
            feedTbody.removeChild(feedTbody.lastChild);
        }

        feedCountEl.innerText = `${feedTbody.children.length} событий`;
    }

    // --- WebSockets ---
    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        connStatusEl.innerHTML = `<span class="dot pulse yellow"></span> ПОДКЛЮЧЕНИЕ...`;
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            connStatusEl.innerHTML = `<span class="dot pulse green"></span> LIVE WS`;
            connStatusEl.style.color = "var(--color-green)";
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleWsMessage(msg);
            } catch (err) {
                console.error("WS parse err:", err);
            }
        };

        ws.onclose = () => {
            connStatusEl.innerHTML = `<span class="dot red"></span> ПЕРЕПОДКЛЮЧЕНИЕ`;
            connStatusEl.style.color = "var(--color-red)";
            setTimeout(connectWebSocket, 3000);
        };
    }

    function handleWsMessage(msg) {
        if (msg.type === "init") {
            liquidationHistory = msg.recent_liquidations || [];
            setupSymbolButtons(msg.symbols);
            refreshAllData();
            fetchStats();
        } else if (msg.type === "liquidation") {
            const item = msg.data;
            liquidationHistory.push(item);
            if (liquidationHistory.length > 2000) liquidationHistory.shift();

            if (item.symbol === currentSymbol && item.usd >= minUsdFilter) {
                playLiquidationSound(item.usd, item.side);
            }

            addFeedRow(item, true);
            updateChartMarkers();
            drawClustersOnCanvas();
            updateProfileSidebar();
            fetchStats();

            if (msg.candle_ticks && msg.candle_ticks.length > 0) {
                msg.candle_ticks.forEach(ct => {
                    if (ct.timeframe === currentTimeframe && ct.candle && candlestickSeries) {
                        candlestickSeries.update({
                            time: ct.candle.time,
                            open: ct.candle.open,
                            high: ct.candle.high,
                            low: ct.candle.low,
                            close: ct.candle.close
                        });
                        if (volumeSeries) {
                            volumeSeries.update({
                                time: ct.candle.time,
                                value: ct.candle.volume,
                                color: ct.candle.close >= ct.candle.open ? 'rgba(0, 230, 118, 0.35)' : 'rgba(255, 42, 95, 0.35)'
                            });
                        }
                        updatePriceDisplay(ct.candle.close, candlesData[0]?.open);
                    }
                });
            }
        } else if (msg.type === "price_tick") {
            if (msg.symbol === currentSymbol) {
                updatePriceDisplay(msg.price, candlesData[0]?.open);
                if (msg.candle_ticks) {
                    msg.candle_ticks.forEach(ct => {
                        if (ct.timeframe === currentTimeframe && ct.candle && candlestickSeries) {
                            candlestickSeries.update({
                                time: ct.candle.time,
                                open: ct.candle.open,
                                high: ct.candle.high,
                                low: ct.candle.low,
                                close: ct.candle.close
                            });
                        }
                    });
                }
            }
        }
    }

    async function fetchStats() {
        try {
            const resp = await fetch(`/api/stats?symbol=${currentSymbol}`);
            const data = await resp.json();
            if (data) {
                stat24hTotalEl.innerText = `$${formatUsdShort(data.total_usd_24h)}`;
                stat1hTotalEl.innerText = `$${formatUsdShort(data.total_usd_1h)}`;

                const total24h = data.total_usd_24h || 1;
                const longPct = Math.round((data.longs_usd_24h / total24h) * 100);
                const shortPct = 100 - longPct;

                longPctEl.innerText = `${longPct}%`;
                shortPctEl.innerText = `${shortPct}%`;
                longRatioBar.style.width = `${longPct}%`;

                if (data.top_coins) {
                    let html = "";
                    data.top_coins.slice(0, 6).forEach(c => {
                        html += `
                            <div class="top-coin-card">
                                <div class="top-coin-name">${c.symbol.replace('_', '/')}</div>
                                <div class="top-coin-val">$${formatUsdShort(c.usd)}</div>
                            </div>
                        `;
                    });
                    topCoinsContainer.innerHTML = html;
                }
            }
        } catch (e) {}
    }

    function setupSymbolButtons(symbols) {
        const container = document.getElementById("symbol-buttons");
        container.innerHTML = "";
        symbols.forEach(s => {
            const btn = document.createElement("button");
            btn.className = `btn-symbol ${s === currentSymbol ? 'active' : ''}`;
            btn.innerText = s.split('_')[0];
            btn.addEventListener("click", () => {
                document.querySelectorAll(".btn-symbol").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                currentSymbol = s;
                symbolTitleEl.innerText = s;
                refreshAllData();
            });
            container.appendChild(btn);
        });
    }

    document.querySelectorAll(".btn-tf").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".btn-tf").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentTimeframe = parseInt(btn.dataset.tf);
            loadCandles().then(() => {
                updateChartMarkers();
                drawClustersOnCanvas();
            });
        });
    });

    minUsdSelect.addEventListener("change", (e) => {
        minUsdFilter = parseFloat(e.target.value);
        refreshFeedTable();
        updateChartMarkers();
        drawClustersOnCanvas();
    });

    exchangeSelect.addEventListener("change", (e) => {
        exchangeFilter = e.target.value;
        refreshFeedTable();
        updateChartMarkers();
        drawClustersOnCanvas();
    });

    soundToggleBtn.addEventListener("click", () => {
        soundEnabled = !soundEnabled;
        soundIcon.innerText = soundEnabled ? "🔊" : "🔇";
        soundToggleBtn.classList.toggle("btn-outline", soundEnabled);
    });

    clearClustersBtn.addEventListener("click", () => {
        liquidationHistory = [];
        feedTbody.innerHTML = "";
        profileBarsContainer.innerHTML = "";
        if (candlestickSeries) candlestickSeries.setMarkers([]);
        drawClustersOnCanvas();
    });

    closeModalBtn.addEventListener("click", () => {
        detailModal.classList.add("hidden");
    });

    function openDetailModal(item) {
        modalTitle.innerText = `Ликвидация ${item.symbol.replace('_', '/')}`;
        const isLong = item.side === "SELL";
        modalBody.innerHTML = `
            <p><strong>Биржа:</strong> <span style="text-transform:capitalize">${item.exchange}</span></p>
            <p><strong>Тип:</strong> ${isLong ? '<span class="badge-side long">LONG (Forced Sell)</span>' : '<span class="badge-side short">SHORT (Forced Buy)</span>'}</p>
            <p><strong>Цена:</strong> ${formatPrice(item.price, item.symbol)}</p>
            <p><strong>Сумма ($):</strong> <span style="font-size:1.1rem; font-weight:800; color:var(--color-gold);">$${formatUsdNumber(item.usd)}</span></p>
            <p><strong>Количество:</strong> ${item.qty.toFixed(4)}</p>
            <p><strong>Время:</strong> ${new Date(item.timestamp * 1000).toLocaleString()}</p>
        `;
        detailModal.classList.remove("hidden");
    }

    function refreshAllData() {
        loadCandles().then(() => {
            updateChartMarkers();
            drawClustersOnCanvas();
            updateProfileSidebar();
            refreshFeedTable();
            fetchStats();
        });
    }

    function refreshFeedTable() {
        feedTbody.innerHTML = "";
        const filtered = liquidationHistory.filter(item => {
            if (currentSymbol !== "ALL" && item.symbol !== currentSymbol) return false;
            if (exchangeFilter !== "ALL" && item.exchange !== exchangeFilter) return false;
            if (item.usd < minUsdFilter) return false;
            return true;
        });
        filtered.slice(-100).reverse().forEach(item => addFeedRow(item, false));
    }

    function priceDecimals(symbol) {
        if (symbol.includes("DOGE") || symbol.includes("XRP")) return 4;
        return 2;
    }

    function formatPrice(price, symbol) {
        if (!price) return "$0.00";
        if (price < 1) return `$${price.toFixed(4)}`;
        if (price < 10) return `$${price.toFixed(3)}`;
        return `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    function formatUsdNumber(val) {
        return Math.round(val).toLocaleString();
    }

    function formatUsdShort(val) {
        if (!val) return "0";
        if (val >= 1000000) return (val / 1000000).toFixed(2) + "M";
        if (val >= 1000) return (val / 1000).toFixed(1) + "K";
        return Math.round(val).toString();
    }

    initChart();
    loadCandles();
    connectWebSocket();
});
