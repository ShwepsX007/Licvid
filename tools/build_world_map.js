#!/usr/bin/env node
/**
 * 🌍 Сборка карты мира для админки: контуры стран и центроиды.
 *
 * Источник — Natural Earth 110m через пакет `world-atlas` (public domain),
 * перевод кодов — `i18n-iso-countries` (MIT), разбор TopoJSON — `topojson-client`.
 * Пакеты нужны только на время сборки, в рантайме сайт обходится двумя
 * сгенерированными файлами:
 *
 *     static/world-map.js   — контуры для браузера (equirectangular, 1000×500)
 *     geo_countries.py      — ISO2 → английское имя и центроид (для сервера)
 *
 * Запуск:
 *     npm install --no-save world-atlas topojson-client i18n-iso-countries
 *     NODE_PATH=./node_modules node tools/build_world_map.js
 *
 * Что делает по дороге:
 *   * выбрасывает мелкие острова (кольца меньше порога по площади) — на карте
 *     админки в 1000 px их всё равно не видно, а вес файла они удваивают;
 *   * округляет координаты до 0,1 px и пишет неявные линии («M x,y x,y») —
 *     так путь короче почти втрое;
 *   * центроид считает по самому большому кольцу страны, а не по всем сразу:
 *     иначе Франция «уезжает» в Атлантику из-за заморских территорий.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const W = 1000;                      // ширина полотна карты в px
const H = 500;                       // высота: ровно 2:1, как у equirectangular
const MIN_RING_AREA = 12;            // кольца меньше — не рисуем (кв. px карты)
//: Антарктиду не рисуем: посетителей оттуда не бывает, а на карте она
//: занимает полосу снизу и стоит дороже всех стран вместе взятых
const SKIP_CC = { AQ: 1 };
const DECIMALS = 1;

const worldAtlas = require("world-atlas/countries-110m.json");
const { feature } = require("topojson-client");
const iso = require("i18n-iso-countries");

/** Коды, которых нет в справочнике ISO: у Natural Earth они лежат отдельно. */
const SPECIAL = {
  "-99": null,          // без кода — определяем по имени ниже
  Kosovo: "XK",
  Somaliland: "SO",
  "N. Cyprus": "CY",
  "Northern Cyprus": "CY",
};

const collection = feature(worldAtlas, worldAtlas.objects.countries);

/** Площадь кольца (в координатах проекции) со знаком: >0 — обход по часовой. */
function ringArea(ring) {
  let sum = 0;
  for (let i = 0, n = ring.length; i < n; i++) {
    const a = ring[i], b = ring[(i + 1) % n];
    sum += a[0] * b[1] - b[0] * a[1];
  }
  return sum / 2;
}

function bboxArea(ring) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of ring) {
    if (p[0] < x0) x0 = p[0];
    if (p[0] > x1) x1 = p[0];
    if (p[1] < y0) y0 = p[1];
    if (p[1] > y1) y1 = p[1];
  }
  return Math.abs((x1 - x0) * (y1 - y0));
}

/** Долгота/широта → пиксели карты (equirectangular). */
function project([lon, lat]) {
  const x = ((Number(lon) + 180) / 360) * W;
  const y = ((90 - Number(lat)) / 180) * H;
  const r = Math.pow(10, DECIMALS);
  return [Math.round(x * r) / r, Math.round(y * r) / r];
}

/** Кольцо → «x,y x,y …» с неявными линиями после M. */
function ringPath(ring) {
  const pts = ring.map(project);
  const head = pts[0];
  let d = "M" + head[0] + "," + head[1];
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i];
    if (p[0] === head[0] && p[1] === head[1] && i === pts.length - 1) continue;
    d += " " + p[0] + "," + p[1];
  }
  return d + "Z";
}

/** Полигоны страны: [{outer, holes}] из GeoJSON. */
function polygons(geom) {
  if (geom.type === "Polygon") return [geom.coordinates];
  if (geom.type === "MultiPolygon") return geom.coordinates;
  return [];
}

function codeOf(f) {
  const name = (f.properties && f.properties.name) || "";
  if (SPECIAL[name]) return SPECIAL[name];
  const cc = iso.numericToAlpha2(String(f.id));
  if (cc) return cc;
  if (f.id === "-99") return null;
  return null;
}

function main() {
  const countries = [];
  const centroids = {};
  const names = {};
  const skipped = [];

  for (const f of collection.features) {
    const cc = codeOf(f);
    if (!cc) { skipped.push(f.properties && f.properties.name); continue; }
    const rings = [];
    polygons(f.geometry).forEach((poly) => poly.forEach((ring, idx) => {
      rings.push({ ring, outer: idx === 0, area: Math.abs(ringArea(ring)),
                   bbox: bboxArea(ring) });
    }));
    if (!rings.length) { skipped.push(f.properties.name); continue; }
    // самый большой контур страны — и по нему центроид
    const biggest = rings.slice().sort((a, b) => b.area - a.area)[0];
    if (SKIP_CC[cc]) { skipped.push(cc); continue; }
    const keep = rings.filter((r) => r === biggest || r.bbox >= MIN_RING_AREA);
    const d = keep.map((r) => ringPath(r.ring)).join("");
    if (!d) { skipped.push(f.properties.name); continue; }

    const cx = biggest.ring.reduce((s, p) => s + p[0], 0) / biggest.ring.length;
    const cy = biggest.ring.reduce((s, p) => s + p[1], 0) / biggest.ring.length;
    centroids[cc] = [Math.round(cy * 100) / 100, Math.round(cx * 100) / 100];
    names[cc] = (f.properties && f.properties.name) || cc;
    countries.push([cc, d]);
  }

  countries.sort((a, b) => (a[0] < b[0] ? -1 : 1));

  // --- браузерный файл ----------------------------------------------------
  const js = [
    "/**",
    " * LiqScope — контуры стран для карты посещений в админке.",
    " *",
    " * Файл СГЕНЕРИРОВАН: tools/build_world_map.js (Natural Earth 110m, public domain).",
    " * Проекция — equirectangular 1000×500: x = (lon+180)/360·1000, y = (90−lat)/180·500.",
    " * Формат: [код ISO-3166 alpha-2, путь SVG]. Координаты уже в пикселях карты,",
    " * поэтому точки визитов ставятся по той же формуле и совпадают с контурами.",
    " */",
    "window.LIQSCOPE_WORLD = ",
    JSON.stringify({ w: W, h: H, countries: countries },
                   null, 0).replace(/\],\[/g, "],\n [") + ";",
    "",
  ].join("\n");
  fs.writeFileSync(path.join(ROOT, "static", "world-map.js"), js, "utf8");

  // --- серверный справочник ----------------------------------------------
  const py = [];
  py.push('"""Коды стран и их центроиды — сгенерировано tools/build_world_map.js.');
  py.push("");
  py.push("Источник — Natural Earth 110m (public domain) через world-atlas +");
  py.push("i18n-iso-countries (MIT). Правьте генератор, а не этот файл:");
  py.push("    NODE_PATH=./node_modules node tools/build_world_map.js");
  py.push('"""');
  py.push("from __future__ import annotations");
  py.push("");
  py.push("from typing import Dict, Tuple");
  py.push("");
  py.push("#: ISO 3166-1 alpha-2 → (английское имя, широта, долгота центроида)");
  py.push("COUNTRIES: Dict[str, Tuple[str, float, float]] = {");
  Object.keys(names).sort().forEach((cc) => {
    const [lat, lon] = centroids[cc];
    // в JS «%s» — не подстановка, а остаток от деления: склеиваем строками
    py.push(`    "${cc}": (${JSON.stringify(names[cc])}, ${lat}, ${lon}),`);
  });
  py.push("}");
  py.push("");
  fs.writeFileSync(path.join(ROOT, "geo_countries.py"), py.join("\n"), "utf8");

  const bytes = fs.statSync(path.join(ROOT, "static", "world-map.js")).size;
  console.log("стран:", countries.length,
              "| пропущено:", skipped.length, skipped.join(", "));
  console.log("static/world-map.js:", (bytes / 1024).toFixed(0), "КБ",
              "| geo_countries.py строк:", Object.keys(names).length);
}

main();
