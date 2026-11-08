// Per-day territorial change summary for the time slider: public/data/frontline/changes.json
//
// For each snapshot day D (compared with the previous snapshot P):
//   { d: D, p: P, area: km² held on D, g: km² gained by Russia since P, l: km² recaptured by Ukraine since P,
//     f: [w, s, e, n] bbox of the largest changed piece (for "follow the action"), fk: "g" | "l" }
// Pieces under 0.1 km² are ignored (DeepState redrawing the line, not movement), as in the map.
// Incremental: rows whose (d, p) pair is unchanged are kept, so only new days are computed (~0.3 s each).
//
// Usage: node scripts/build-changes.mjs [--force]   (also runs after each front-line refresh)
import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import polygonClipping from "polygon-clipping";
import { FRONTLINE_DIR } from "./fetch-frontline.mjs";

const OUT = join(FRONTLINE_DIR, "changes.json");
const MIN_KM2 = 0.1;

const km2 = (poly) => poly.reduce((t, ring, k) => {
  const c = Math.cos((ring[0][1] * Math.PI) / 180);
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  a = (Math.abs(a) / 2) * 111.32 * 110.57 * c;
  return t + (k ? -a : a);
}, 0);
const bbox = (poly) => {
  let w = 180, s = 90, e = -180, n = -90;
  for (const [x, y] of poly[0]) { if (x < w) w = x; if (x > e) e = x; if (y < s) s = y; if (y > n) n = y; }
  return [w, s, e, n].map((v) => Math.round(v * 1000) / 1000);
};
const round1 = (x) => Math.round(x * 10) / 10;

async function loadHeld(day) {
  const geo = JSON.parse(await readFile(join(FRONTLINE_DIR, `${day}.geojson`), "utf8"));
  const polys = geo.features.flatMap((f) => (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates));
  return polygonClipping.union(...polys.map((p) => [p])); // snapshots can contain overlapping parts
}

export async function buildChanges({ force = false, log = console } = {}) {
  const { dates = [] } = JSON.parse(await readFile(join(FRONTLINE_DIR, "index.json"), "utf8"));
  const old = !force && existsSync(OUT) ? JSON.parse(await readFile(OUT, "utf8")).days ?? [] : [];
  const have = new Map(old.map((r) => [`${r.d}|${r.p ?? ""}`, r]));
  const days = [];
  let computed = 0, prevHeld = null, prevDay = null;
  const t0 = Date.now();
  for (const [i, d] of dates.entries()) {
    const p = i ? dates[i - 1] : null;
    const cached = have.get(`${d}|${p ?? ""}`);
    if (cached) { days.push(cached); prevHeld = null; prevDay = null; continue; }
    const held = await loadHeld(d);
    const before = p ? (prevDay === p ? prevHeld : await loadHeld(p)) : null;
    const row = { d, p, area: Math.round(held.reduce((s, q) => s + km2(q), 0)), g: 0, l: 0 };
    if (before) {
      let best = null;
      for (const [kind, pieces] of [["g", polygonClipping.difference(held, before)], ["l", polygonClipping.difference(before, held)]])
        for (const q of pieces) {
          const a = km2(q);
          if (a < MIN_KM2) continue;
          row[kind] += a;
          if (!best || a > best.a) best = { a, kind, q };
        }
      row.g = round1(row.g);
      row.l = round1(row.l);
      if (best) { row.f = bbox(best.q); row.fk = best.kind; }
    }
    days.push(row);
    prevHeld = held;
    prevDay = d;
    if (++computed % 100 === 0) log.info(`[changes] ${computed} day(s) computed…`);
  }
  await writeFile(OUT, JSON.stringify({ updated: new Date().toISOString(), days }));
  if (computed) log.info(`[changes] ${computed} new day(s) in ${((Date.now() - t0) / 1000).toFixed(0)} s; ${days.length} days in changes.json`);
  return days;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1])
  await buildChanges({ force: process.argv.includes("--force") });
