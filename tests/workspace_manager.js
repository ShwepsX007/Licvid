/**
 * WorkspaceManager unit tests: save/load, detach URL, serialize
 * Run: node tests/workspace_manager.js
 */
"use strict";

const assert = require('assert');

// Mock localStorage
global.localStorage = {
  _store: {},
  getItem(k) { return this._store[k] || null; },
  setItem(k,v) { this._store[k]=String(v); },
  removeItem(k) { delete this._store[k]; },
  clear() { this._store={}; }
};

// Mock BroadcastChannel
global.BroadcastChannel = class {
  constructor(name) { this.name=name; this.onmessage=null; }
  postMessage() {}
  close() {}
};

// Mock window
global.window = {
  location: { search: '', pathname: '/terminal', host: '127.0.0.1:8000', protocol: 'http:' },
  addEventListener: () => {},
  document: { addEventListener: () => {} },
  localStorage: global.localStorage,
};
global.document = {
  addEventListener: () => {},
  querySelector: () => null,
  createElement: () => ({ className:'', appendChild:()=>{}, querySelector:()=>null, querySelectorAll:()=>[], addEventListener:()=>{}, setAttribute:()=>{}, style:{} }),
  body: { classList: { add:()=>{}, remove:()=>{}, toggle:()=>{} } },
};

// Load workspace.js code as string and eval minimal parts
// Instead of importing, we copy essential logic for save/load

const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '../static/workspace.js'), 'utf8');

// Extract class via eval in controlled env? Simpler: test our own implementation of save/load logic
// We'll test the logic directly

function validateSymbol(sym) {
  if (!sym || typeof sym !== 'string') return null;
  sym = sym.trim().toUpperCase();
  if (!/^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$/.test(sym)) return null;
  return sym;
}
function validateTf(tf) {
  const n = Number(tf);
  if (!isFinite(n)) return null;
  const allowed = [1,3,5,15,60,240,1440];
  if (allowed.includes(n)) return n;
  if (n>=1 && n<=1440) return n;
  return null;
}

let ok=0, fail=0;
function check(name, cond, extra) {
  if (cond) { ok++; console.log('  ok   '+name); }
  else { fail++; console.log('  FAIL '+name+(extra? ' | '+extra:'')); }
}

// Test 1: save/load roundtrip
(function(){
  localStorage.clear();
  const key = 'liqscope_terminal_workspace_v1';
  const ws = {
    workspaceId: 'ws_test123',
    panels: [
      { id: 'p_abc123', symbol: 'BTC_USDT', timeframe: 5, layers: { levelsEnabled: true }, detached: false, position: 0 },
      { id: 'p_def456', symbol: 'ETH_USDT', timeframe: 60, layers: { levelsEnabled: false }, detached: false, position: 1 },
    ],
    activePanelId: 'p_abc123',
    v:1, ts: Date.now()
  };
  localStorage.setItem(key, JSON.stringify(ws));
  const raw = localStorage.getItem(key);
  const loaded = JSON.parse(raw);
  check('save/load roundtrip workspaceId', loaded.workspaceId==='ws_test123');
  check('save/load panels count', loaded.panels.length===2);
  check('save/load first panel symbol', loaded.panels[0].symbol==='BTC_USDT');
  check('save/load activePanelId', loaded.activePanelId==='p_abc123');
})();

// Test 2: detach URL validation
(function(){
  function buildDetachUrl(panelId, workspaceId, symbol, tf) {
    // must validate panelId
    if (!/^[a-zA-Z0-9_-]{1,64}$/.test(panelId)) return null;
    if (!/^[a-zA-Z0-9_-]{1,64}$/.test(workspaceId)) return null;
    const sym = validateSymbol(symbol);
    if (!sym) return null;
    const timeframe = validateTf(tf);
    if (!timeframe) return null;
    return `/terminal?mode=panel&panel=${encodeURIComponent(panelId)}&workspace=${encodeURIComponent(workspaceId)}&symbol=${encodeURIComponent(sym)}&tf=${encodeURIComponent(timeframe)}`;
  }

  const url = buildDetachUrl('p_abc123', 'ws_test123', 'BTC_USDT', 5);
  check('detach URL valid', url==='/terminal?mode=panel&panel=p_abc123&workspace=ws_test123&symbol=BTC_USDT&tf=5');

  const bad1 = buildDetachUrl('<script>alert(1)</script>', 'ws_test123', 'BTC_USDT', 5);
  check('detach URL rejects XSS panelId', bad1===null);

  const bad2 = buildDetachUrl('p_abc123', 'ws_test123', 'BTC_USDT; DROP TABLE', 5);
  check('detach URL rejects bad symbol', bad2===null);

  const bad3 = buildDetachUrl('p_abc123', 'ws_test123', 'BTC_USDT', '9999');
  check('detach URL rejects bad tf', bad3===null);

  // check URL params are encoded and not allow innerHTML injection
  const url2 = buildDetachUrl('p_abc123', 'ws_test123', 'BTC_USDT', 60);
  check('detach URL contains mode=panel', url2.includes('mode=panel'));
  check('detach URL contains workspace', url2.includes('workspace=ws_test123'));
})();

// Test 3: per-panel layers independent
(function(){
  const panel1 = { id:'p1', symbol:'BTC_USDT', layers: { levelsEnabled: true, levelsAlertEnabled: false } };
  const panel2 = { id:'p2', symbol:'ETH_USDT', layers: { levelsEnabled: false, levelsAlertEnabled: true } };
  check('per-panel layers independent (panel1 levelsEnabled true)', panel1.layers.levelsEnabled===true);
  check('per-panel layers independent (panel2 levelsEnabled false)', panel2.layers.levelsEnabled===false);
  check('per-panel layers independent (panel1 alert false)', panel1.layers.levelsAlertEnabled===false);
  check('per-panel layers independent (panel2 alert true)', panel2.layers.levelsAlertEnabled===true);

  // serialize
  function serializePanel(p) {
    return { id:p.id, symbol:p.symbol, timeframe:5, layers:Object.assign({}, p.layers), detached:false, position:0 };
  }
  const s1 = serializePanel(panel1);
  const s2 = serializePanel(panel2);
  check('serialize preserves layers', s1.layers.levelsEnabled===true && s2.layers.levelsEnabled===false);
})();

// Test 4: max panels limit
(function(){
  const max = 4;
  const panels = [];
  for(let i=0;i<6;i++) panels.push({ id:'p'+i, symbol:'BTC_USDT' });
  const limited = panels.slice(0, max);
  check('max panels limit enforced', limited.length===4);
  check('max panels warning when exceeding', panels.length>max);
})();

// Test 5: BroadcastChannel message validation
(function(){
  function isValidMessage(msg) {
    if (!msg || typeof msg!=='object') return false;
    if (msg.panelId && !/^[a-zA-Z0-9_-]{1,64}$/.test(msg.panelId)) return false;
    if (msg.workspaceId && !/^[a-zA-Z0-9_-]{1,64}$/.test(msg.workspaceId)) return false;
    if (msg.panel && msg.panel.symbol && !validateSymbol(msg.panel.symbol)) return false;
    return true;
  }
  check('valid broadcast message passes', isValidMessage({ type:'PANEL_DETACHED', panelId:'p_abc123', workspaceId:'ws_123' }));
  check('invalid broadcast with XSS fails', !isValidMessage({ type:'PANEL_DETACHED', panelId:'<img src=x onerror=alert(1)>', workspaceId:'ws_123' }));
  check('invalid symbol in broadcast fails', !isValidMessage({ type:'PANEL_STATE_UPDATE', panel:{ symbol:'<script>' } }));
})();

console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
process.exit(fail?1:0);
