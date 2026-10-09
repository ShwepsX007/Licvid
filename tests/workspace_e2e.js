/**
 * E2E-lite jsdom smoke for multi-chart workspace
 * - create workspace DOM, add 2 panels, serialize, check persistence
 * Run: node tests/workspace_e2e.js http://127.0.0.1:8000
 */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');

const URL_BASE = process.argv[2] || 'http://127.0.0.1:8000';
const ROOT = path.dirname(__dirname);

let ok=0, fail=0;
function check(name, cond, extra){
  if(cond){ ok++; console.log('  ok   '+name); }
  else { fail++; console.log('  FAIL '+name+(extra? ' | '+extra:'')); }
}

(async () => {
  const html = await (await fetch(URL_BASE + '/terminal')).text();
  // check that our new files are included
  check('index.html contains workspace.css', html.includes('workspace.css'));
  check('index.html contains chart_dock.js', html.includes('chart_dock.js'));
  check('index.html contains add-chart button', html.includes('id="add-chart-btn"'));
  check('index.html has no multi-chart toggle', !html.includes('id="ws-mode-toggle"'));
  check('index.html contains workspace-host', html.includes('workspace-host'));

  // Load jsdom with our scripts
  const dom = new JSDOM(html, {
    url: URL_BASE + '/terminal',
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    beforeParse(win) {
      win.matchMedia = () => ({ matches: false, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}, dispatchEvent(){return false;} });
      if (!win.ResizeObserver) win.ResizeObserver = function(){ return { observe(){}, unobserve(){}, disconnect(){} }; };
      win.HTMLCanvasElement.prototype.getContext = () => {
        return {
          clearRect:()=>{}, fillRect:()=>{}, strokeRect:()=>{}, beginPath:()=>{}, moveTo:()=>{}, lineTo:()=>{}, stroke:()=>{}, fill:()=>{}, clip:()=>{}, rect:()=>{}, save:()=>{}, restore:()=>{}, setLineDash:()=>{}, measureText:()=>({width:10}),
          createLinearGradient:()=>({addColorStop:()=>{}}),
        };
      };
      win.WebSocket = function(){ return { readyState:3, send(){}, close(){} }; };
      win.WebSocket.OPEN=1; win.WebSocket.CLOSED=3;
      // fetch polyfill for app.js
      win.fetch = (u, opt) => {
        const url = String(u);
        const abs = url.startsWith('http') ? url : URL_BASE + url;
        return fetch(abs, opt);
      };
      // mock LightweightCharts
      win.LightweightCharts = {
        createChart: (container, opts) => {
          return {
            applyOptions:()=>{},
            priceScale:()=>({ width:()=>68, applyOptions:()=>{} }),
            timeScale:()=>({ subscribeVisibleLogicalRangeChange:()=>{}, applyOptions:()=>{} }),
            remove:()=>{},
            addCandlestickSeries:()=>({ setData:()=>{}, update:()=>{}, priceToCoordinate:()=>100 }),
          };
        },
        CrosshairMode: { Normal:0 }
      };
      try {
        win.localStorage.clear();
      } catch {}
    }
  });

  // wait for scripts to load
  await new Promise(r => setTimeout(r, 4000));

  const win = dom.window;
  const doc = win.document;

  check('single chart is not hidden by multi mode', !doc.body.classList.contains('workspace-active'));
  check('ChartDock exists', !!win.LiqScopeDock);
  if (win.LiqScopeDock) {
    const dock = win.LiqScopeDock;
    check('dock starts with the native chart', dock.order && dock.order[0]==='native');
    const before = dock.order.length;
    const id = dock.addChart();
    check('addChart adds a single-chart slot', !!id && dock.order.length===before+1, `order=${dock.order && dock.order.join(',')}`);
    const slot = dock.els && dock.els.get(id);
    const frame = slot && slot.querySelector('iframe');
    const src = frame ? frame.src : '';
    check('added chart is an embedded single chart', src.includes('embed=1') && src.includes('symbol='), src);
    dock.floatChart(id, 40, 40);
    check('float tears the chart off the grid', !!(dock.meta[id] && dock.meta[id].floating) && slot.classList.contains('chart-floating'));
    dock.dockChart(id);
    check('dock puts the chart back', dock.meta[id] && dock.meta[id].floating===false && slot.parentNode===dock.dock);
    dock.closeChart(id);
    check('closing the extra chart leaves the native one', dock.order.length===1 && dock.order[0]==='native');
  }

  // Check panel mode URL handling
  const dom2 = new JSDOM(html, {
    url: URL_BASE + '/terminal?mode=panel&panel=p_test123&workspace=ws_test&symbol=BTC_USDT&tf=5',
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    beforeParse(win) {
      win.matchMedia = () => ({ matches: false, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}, dispatchEvent(){return false;} });
      if (!win.ResizeObserver) win.ResizeObserver = function(){ return { observe(){}, unobserve(){}, disconnect(){} }; };
      win.HTMLCanvasElement.prototype.getContext = () => ({
        clearRect:()=>{}, fillRect:()=>{}, strokeRect:()=>{}, beginPath:()=>{}, moveTo:()=>{}, lineTo:()=>{}, stroke:()=>{}, fill:()=>{}, clip:()=>{}, rect:()=>{}, save:()=>{}, restore:()=>{}, setLineDash:()=>{}, measureText:()=>({width:10}),
      });
      win.WebSocket = function(){ return { readyState:3, send(){}, close(){} }; };
      win.WebSocket.OPEN=1; win.WebSocket.CLOSED=3;
      win.fetch = (u, opt) => {
        const url = String(u);
        const abs = url.startsWith('http') ? url : URL_BASE + url;
        return fetch(abs, opt);
      };
      win.LightweightCharts = {
        createChart: () => ({
          applyOptions:()=>{},
          priceScale:()=>({ width:()=>68, applyOptions:()=>{} }),
          timeScale:()=>({ subscribeVisibleLogicalRangeChange:()=>{}, applyOptions:()=>{} }),
          remove:()=>{},
          addCandlestickSeries:()=>({ setData:()=>{}, update:()=>{}, priceToCoordinate:()=>100 }),
        }),
        CrosshairMode: { Normal:0 }
      };
      try { win.localStorage.setItem('liqscope_terminal_workspace_v1', JSON.stringify({
        workspaceId:'ws_test',
        panels:[{ id:'p_test123', symbol:'BTC_USDT', timeframe:5, layers:{ levelsEnabled:true }, position:0 }],
        activePanelId:'p_test123'
      })); } catch {}
    }
  });
  await new Promise(r => setTimeout(r, 3000));
  const win2 = dom2.window;
  check('old panel url uses the real single chart', win2.document.body.classList.contains('chart-embed'));
  check('old panel url does not mount multi-chart', !win2.document.querySelector('.workspace-root'));

  console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
  process.exit(fail?1:0);
})().catch(e => { console.error('test failed', e); process.exit(1); });
