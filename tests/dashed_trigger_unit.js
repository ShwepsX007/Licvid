/**
 * Unit for dashed level proximity trigger ±0.01% + cooldown
 * Checks shouldTrigger(price, level, state, now)
 *
 * Run: node tests/dashed_trigger_unit.js
 */
"use strict";

const DASHED_TRIGGER_PCT = 0.0001;
const DASHED_COOLDOWN_MS = 15000;

// copy of app.js pure function
function shouldTrigger(price, levelPrice, state, nowMs) {
  if (!isFinite(price) || !isFinite(levelPrice) || levelPrice === 0) return false;
  const rel = Math.abs(price - levelPrice) / Math.abs(levelPrice);
  const inZone = rel <= DASHED_TRIGGER_PCT;
  if (!inZone) {
    // leaving zone
    if (state) state.inZone = false;
    return false;
  }
  // in zone
  if (state && state.inZone) return false; // already in zone, no retrigger
  if (state && state.lastTrigger && (nowMs - state.lastTrigger) < DASHED_COOLDOWN_MS) {
    // still in cooldown, but mark inZone to avoid flood
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
  // boundary
  let s={};
  const level=60000;
  const now=Date.now();
  // exactly 0.01%
  const p_in = level * (1 + 0.0001); // +0.01%
  check("boundary +0.01% triggers", shouldTrigger(p_in, level, s, now)===true);
  // just outside 0.01001%
  s={}; // reset
  const p_out = level * (1 + 0.0001001);
  check("just outside +0.01001% not trigger", shouldTrigger(p_out, level, s, now)===false);

  // 0.009% inside
  s={};
  const p_09 = level * (1 + 0.00009);
  check("0.009% inside triggers", shouldTrigger(p_09, level, s, now)===true);

  // cooldown
  let s2={lastTrigger: now, inZone:false};
  check("cooldown blocks", shouldTrigger(p_in, level, s2, now+1000)===false);
  s2={lastTrigger: now-16000, inZone:false};
  check("after cooldown triggers again", shouldTrigger(p_in, level, s2, now)===true);

  // exit-and-reenter
  let s3={lastTrigger: now-20000, inZone:true};
  // still in zone, already marked inZone -> no trigger
  check("already inZone no retrigger", shouldTrigger(p_in, level, s3, now)===false);
  // leave zone
  const p_far = level * 1.01;
  check("leaving zone returns false", shouldTrigger(p_far, level, s3, now)===false);
  check("inZone cleared after leave", s3.inZone===false);
  // re-enter after cooldown
  s3.lastTrigger = now-20000;
  check("re-enter after leave triggers", shouldTrigger(p_in, level, s3, now+100)===true);

  // negative side
  let s4={};
  const p_below = level * (1 - 0.00005);
  check("below level -0.005% triggers", shouldTrigger(p_below, level, s4, now)===true);

  // zero level not trigger
  check("zero level no crash", shouldTrigger(100, 0, {}, now)===false);

  // price NaN
  check("NaN price no trigger", shouldTrigger(NaN, level, {}, now)===false);
})();

console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
process.exit(fail?1:0);
