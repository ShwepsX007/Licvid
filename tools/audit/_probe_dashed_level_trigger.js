/**
 * Probe: dashed level trigger must not fire 100x on price series lingering near level.
 *
 * Simulates price approaching a level, staying in zone, then leaving, re-entering after cooldown.
 * Checks that trigger count is reasonable (not 100x) and cooldown respected.
 *
 * Run: node tools/audit/_probe_dashed_level_trigger.js
 */

"use strict";

const DASHED_TRIGGER_PCT = 0.0001;
const DASHED_COOLDOWN_MS = 15000;

function shouldTrigger(price, levelPrice, state, nowMs) {
  if (!isFinite(price) || !isFinite(levelPrice) || levelPrice === 0) return false;
  const rel = Math.abs(price - levelPrice) / Math.abs(levelPrice);
  const inZone = rel <= DASHED_TRIGGER_PCT;
  if (!inZone) {
    if (state) state.inZone = false;
    return false;
  }
  if (state && state.inZone) return false;
  if (state && state.lastTrigger && (nowMs - state.lastTrigger) < DASHED_COOLDOWN_MS) {
    if (state) state.inZone = true;
    return false;
  }
  if (state) {
    state.inZone = true;
    state.lastTrigger = nowMs;
  }
  return true;
}

let ok=0, fail=0;
function check(name, cond, extra){
  if(cond){ ok++; console.log("  ok   "+name); }
  else { fail++; console.log("  FAIL "+name+(extra? " | "+extra:"")); }
}

(function(){
  const level = 60000;
  const state = {lastTrigger:0, inZone:false};
  let triggers=0;
  let now=0;
  const stepMs=100; // 100ms per price tick

  // series: price starts 0.05% away, approaches to level, lingers 10s in zone (100 ticks), leaves, returns after 20s
  const prices=[];
  // approach
  for(let i=0;i<50;i++){
    const rel = 0.0005 - i*0.00001; // 0.05% -> 0%
    prices.push(level*(1+rel));
  }
  // linger in zone 100 ticks = 10s
  for(let i=0;i<100;i++){
    prices.push(level*(1+0.00002)); // 0.002% inside
  }
  // leave
  for(let i=0;i<20;i++){
    prices.push(level*(1+0.001 + i*0.0001));
  }
  // wait 200 ticks = 20s away
  for(let i=0;i<200;i++){
    prices.push(level*1.02);
  }
  // re-enter
  for(let i=0;i<10;i++){
    prices.push(level*(1+0.00003));
  }
  // linger again
  for(let i=0;i<100;i++){
    prices.push(level*(1-0.00001));
  }

  for(const p of prices){
    now+=stepMs;
    if(shouldTrigger(p, level, state, now)) triggers++;
  }

  console.log(`triggers count: ${triggers} (expected 2)`);
  check("not triggering 100x (should be 2)", triggers===2, `got ${triggers}`);
  check("no flood during linger", triggers<=3, `got ${triggers}`);

  // second probe: price oscillating at boundary should not spam
  const s2={lastTrigger:0, inZone:false};
  let trig2=0;
  now=0;
  for(let i=0;i<1000;i++){
    now+=10; // 10ms
    const p = (i%2===0) ? level*(1+0.00005) : level*(1+0.00006); // always inside
    if(shouldTrigger(p, level, s2, now)) trig2++;
  }
  console.log(`oscillating inside zone triggers: ${trig2} (expected 1)`);
  check("oscillation does not spam", trig2===1, `got ${trig2}`);

  // third: many levels, O(N) check
  const levels=[59000,59500,60000,60500,61000];
  const states={};
  levels.forEach(l=>states[l]={lastTrigger:0,inZone:false});
  let multi=0;
  now=0;
  const series=[59990,60001,60000,59999,60002,61001,61000,59001];
  for(const p of series){
    now+=1000;
    for(const lv of levels){
      if(shouldTrigger(p, lv, states[lv], now)) multi++;
    }
  }
  console.log(`multi-level triggers: ${multi}`);
  check("multi-level works", multi>=2 && multi<=5, `got ${multi}`);
})();

console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
process.exit(fail?1:0);
