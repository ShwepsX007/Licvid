/**
 * Unit for dashed level proximity trigger ±0.01% + cooldown + hysteresis
 * Checks shouldTrigger(price, level, state, now) — копия чистой функции app.js
 *
 * Новая («успокоенная») семантика:
 *   - касание: |Δ|/level ≤ 0.01%
 *   - выход из зоны только при отходе на 0.05% (гистерезис: микрошум у
 *     уровня больше не дёргает сигнал каждые 15 секунд)
 *   - cooldown 45s на уровень (глобальный тихий период живёт в
 *     checkDashedLevelTriggers и здесь не проверяется)
 *
 * Run: node tests/dashed_trigger_unit.js
 */
"use strict";

const DASHED_TRIGGER_PCT = 0.0001;
const DASHED_EXIT_PCT = 0.0005;
const DASHED_COOLDOWN_MS = 45000;

// copy of app.js pure function
function shouldTrigger(price, levelPrice, state, nowMs) {
  const p = Number(price), lv = Number(levelPrice);
  if (!(p > 0) || !(lv > 0)) return false;
  const rel = Math.abs(p - lv) / lv;
  if (rel > DASHED_EXIT_PCT) {
    if (state) state.inZone = false;          // отошли далеко — взводим
    return false;
  }
  if (rel > DASHED_TRIGGER_PCT) return false; // гистерезис: мёртвая зона
  if (state && state.inZone) return false;    // уже в зоне касания
  if (state && (state.lastTrigger || 0) &&
      (nowMs - state.lastTrigger) < DASHED_COOLDOWN_MS) return false;
  return true;
}

let ok=0, fail=0;
function check(name, cond, extra){
  if(cond){ ok++; console.log("  ok   "+name); }
  else { fail++; console.log("  FAIL "+name+(extra? " | "+extra:"")); }
}

(function(){
  const level = 60000;
  const now = Date.now();
  const p_in = level * (1 + 0.0001);        // ровно +0.01%
  const p_dead = level * (1 + 0.0002);      // +0.02% — мёртвая зона гистерезиса
  const p_exit = level * (1 + 0.0006);      // +0.06% — уже за порогом выхода

  // базовое касание
  check("boundary +0.01% triggers", shouldTrigger(p_in, level, {}, now)===true);
  check("just outside +0.01001% not trigger",
        shouldTrigger(level * (1 + 0.0001001), level, {}, now)===false);

  // гистерезис: между касанием и выходом — тишина, но inZone не сбрасывается
  let s = {lastTrigger: now - 100000, inZone: true};
  check("dead zone 0.02% no trigger", shouldTrigger(p_dead, level, s, now)===false);
  check("dead zone keeps inZone", s.inZone===true);
  check("exit 0.06% clears inZone",
        shouldTrigger(p_exit, level, s, now)===false);
  check("inZone cleared after exit", s.inZone===false);
  check("re-enter after exit triggers",
        shouldTrigger(p_in, level, s, now + 100)===true);

  // микрошум вокруг уровня: выход «чуть-чуть» не взводит повторно
  let s2 = {lastTrigger: now, inZone: true};
  check("jitter out-in stays quiet",
        shouldTrigger(p_dead, level, s2, now)===false &&
        shouldTrigger(p_in, level, s2, now + 60000)===false);

  // cooldown
  let s3 = {lastTrigger: now, inZone: false};
  check("cooldown blocks", shouldTrigger(p_in, level, s3, now + 1000)===false);
  s3 = {lastTrigger: now - 46000, inZone: false};
  check("after cooldown triggers again",
        shouldTrigger(p_in, level, s3, now)===true);

  // нижняя сторона
  check("below level -0.005% triggers",
        shouldTrigger(level * (1 - 0.00005), level, {}, now)===true);
  check("below dead zone quiet",
        shouldTrigger(level * (1 - 0.0002), level, {}, now)===false);

  // мусор на входе
  check("zero level no crash", shouldTrigger(100, 0, {}, now)===false);
  check("NaN price no trigger", shouldTrigger(NaN, level, {}, now)===false);
  check("negative price no trigger", shouldTrigger(-5, level, {}, now)===false);
})();

console.log(`\nитог: ${ok} ok, ${fail} ошибок`);
process.exit(fail?1:0);
