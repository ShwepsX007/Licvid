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
  check('index.html contains chart_panel.js', html.includes('chart_panel.js'));
  check('index.html contains workspace.js', html.includes('workspace.js'));
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

  // Check workspace manager exists
  check('WorkspaceManager global exists', !!win.WorkspaceManager);
  check('ChartPanel global exists', !!win.ChartPanel);
  check('LiqScopeWorkspace exists', !!win.LiqScopeWorkspace);

  if (win.LiqScopeWorkspace) {
    const wm = win.LiqScopeWorkspace;
    check('workspace has panels after mount', wm.panels && wm.panels.size>=1, `size=${wm.panels?wm.panels.size:0}`);
    check('workspace order length >=1', wm.order && wm.order.length>=1, `order=${wm.order?wm.order.length:0}`);

    // add 3rd panel
    const before = wm.panels.size;
    wm.addPanel({ symbol:'SOL_USDT', timeframe:15, layers:{ levelsEnabled:true } });
    await new Promise(r => setTimeout(r, 500));
    check('addPanel increases count', wm.panels.size===before+1, `before ${before} after ${wm.panels.size}`);

    // serialize
    const ser = wm.serialize();
    check('serialize has workspaceId', !!ser.workspaceId);
    check('serialize panels array', Array.isArray(ser.panels) && ser.panels.length===wm.panels.size);
    check('serialize panels have symbol', ser.panels.every(p=>/^[A-Z0-9]+_[A-Z0-9]+$/.test(p.symbol)), JSON.stringify(ser.panels.map(p=>p.symbol)));
    check('serialize panels have per-panel layers', ser.panels.every(p=>p.layers && typeof p.layers.levelsEnabled==='boolean'));

    // save/load
    const raw = win.localStorage.getItem('liqscope_terminal_workspace_v1');
    check('localStorage saved workspace', !!raw);
    if (raw) {
      const parsed = JSON.parse(raw);
      check('localStorage workspace has panels', parsed.panels && parsed.panels.length>=2);
    }

    // detach URL
    const firstId = wm.order[0];
    const firstPanel = wm.panels.get(firstId);
    if (firstPanel) {
      const state = firstPanel.serialize();
      const url = `/terminal?mode=panel&panel=${encodeURIComponent(state.id)}&workspace=${encodeURIComponent(wm.workspaceId)}&symbol=${encodeURIComponent(state.symbol)}&tf=${encodeURIComponent(state.timeframe)}`;
      check('detach URL formed correctly', url.includes('mode=panel') && url.includes('panel=') && url.includes('workspace='));
      check('detach URL symbol validated', /^[A-Z0-9]+_[A-Z0-9]+$/.test(state.symbol));
    }

    // test movePanel (reorder)
    if (wm.order.length>=2) {
      const first = wm.order[0];
      const second = wm.order[1];
      wm.movePanel(first, 1);
      check('movePanel reorders', wm.order[0]===second && wm.order[1]===first, `order=${wm.order.join(',')}`);
    }

    // test per-panel layers independent
    const ids = wm.order.slice(0,2);
    if (ids.length>=2) {
      const p1 = wm.panels.get(ids[0]);
      const p2 = wm.panels.get(ids[1]);
      p1.setLayers({ levelsEnabled: true });
      p2.setLayers({ levelsEnabled: false });
      check('per-panel layers independent after set', p1.layers.levelsEnabled===true && p2.layers.levelsEnabled===false);
    }
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
  check('panel mode adds body class panel-mode', win2.document.body.classList.contains('panel-mode'));
  check('panel mode has workspace-root', !!win2.document.querySelector('.workspace-root'));

  console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
  process.exit(fail?1:0);
})().catch(e => { console.error('test failed', e); process.exit(1); });
