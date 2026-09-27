#!/usr/bin/env node
/**
 * Rough frontend boot measurement using jsdom
 * Tries to load static files and evaluate main app logic size
 */
const fs = require('fs');
const path = require('path');

const HERE = path.join(__dirname, '../..');
const STATIC = path.join(HERE, 'static');

function sizeOf(file) {
  try {
    return fs.statSync(path.join(STATIC, file)).size;
  } catch { return 0; }
}

const files = [
  'style.css',
  'brand.css',
  'ads.css',
  'terminal_chat.css',
  'lightweight-charts.js',
  'i18n.pages.en.js',
  'i18n.js',
  'account.js',
  'app.js',
  'ads.js',
  'terminal_chat.js',
  'presence.js',
  'consent.js'
];

let total = 0;
console.log('Static files sizes:');
for (const f of files) {
  const sz = sizeOf(f);
  total += sz;
  console.log(`  ${f}: ${sz}`);
}
console.log(`Total raw: ${total} bytes`);

// Check app.js for heavy init patterns
const appJs = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf-8');
const heavyPatterns = [
  /loadHistoryFor/g,
  /liq_levels/g,
  /levelsSnapshot/g,
  /MAX_HISTORY/g,
  /ARCHIVE_HOURS/g,
  /recent_liquidations/g,
  /flow_snapshot/g
];
console.log('\nHeavy patterns in app.js:');
for (const pat of heavyPatterns) {
  const matches = appJs.match(pat);
  console.log(`  ${pat}: ${matches ? matches.length : 0}`);
}

// Check index.html size
const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf-8');
console.log(`\nindex.html size: ${html.length}`);
console.log(`i18n.pages.en.js: ${sizeOf('i18n.pages.en.js')}`);
console.log(`i18n.pages.js (combined): ${sizeOf('i18n.pages.js')}`);
