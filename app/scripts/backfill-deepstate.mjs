// Backfills DeepStateMap snapshots from before the cyterat archive (which starts 8 Jul 2024), using
// DeepStateMap's own version history: https://deepstatemap.live/api/history/public lists every published
// map version since April 2022, and /api/history/<id>/geojson returns that version.
//
// Conversion matches the cyterat files: Russian-held = polygons filled #a52714 ("Occupied") or #880e4f
// (Crimea, the 2014-occupied parts of Donetsk/Luhansk, Tuzla), merged into one MultiPolygon. Versions up to
// mid-2022 differ: Crimea/ORDLO/Tuzla are #000000, Transnistria is #880e4f (excluded by name), and some
// areas are GeometryCollections (flattened, keeping the feature's fill). The file for
// day D is the last version published on day D-1 (UTC), as cyterat downloads around 03:00 UTC on day D.
// Checked against cyterat's 2024-07-08 file: 4 km² differ out of ~110,000 km².
//
// Writes public/data/frontline/YYYY-MM-DD.geojson only for days that have a new version and no file yet,
// so existing (cyterat) days are never overwritten. Days without a new version are left out; the map
// shows the latest earlier snapshot for them. Requests are sequential with a pause, to go easy on the site.
//
// Raw versions are cached in .cache/deepstate-raw/<id>.json, so a change to the rules (or --force, which
// rewrites existing backfilled days) doesn't need to download anything again.
//
// Usage: node scripts/backfill-deepstate.mjs [--from 2022-04-01] [--to 2024-07-07] [--delay 800] [--force]
import { writeFile, readFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import polygonClipping from "polygon-clipping";
import { FRONTLINE_DIR, writeIndex } from "./fetch-frontline.mjs";

const API = "https://deepstatemap.live/api/history";
const UA = { "User-Agent": "htb-map-project/1.0 (local event map)" };
const HELD_FILLS = new Set(["#a52714", "#880e4f", "#000000"]);
const NOT_UKRAINE = /придністров|transnistr/i;
const RAW_DIR = join(FRONTLINE_DIR, "..", "..", "..", ".cache", "deepstate-raw");

const arg = (name, def) => { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] : def; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const nextDay = (day) => { const d = new Date(day + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + 1); return d.toISOString().slice(0, 10); };
const strip = (c) => (typeof c[0] === "number" ? c.slice(0, 2) : c.map(strip)); // drop z values

async function getJson(url, tries = 3) {
  for (let i = 1; ; i++) {
    try {
      const r = await fetch(url, { headers: UA });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      if (i >= tries) throw new Error(`${url}: ${e.message}`);
      await sleep(5000 * i);
    }
  }
}

function convert(geo, day, id) {
  const polys = geo.features
    .filter((f) => HELD_FILLS.has(String(f.properties?.fill).toLowerCase()) && !NOT_UKRAINE.test(f.properties?.name ?? ""))
    .flatMap((f) => (f.geometry?.type === "GeometryCollection" ? f.geometry.geometries : [f.geometry]))
    .filter((g) => /Polygon$/.test(g?.type ?? ""))
    .flatMap((g) => (g.type === "Polygon" ? [strip(g.coordinates)] : strip(g.coordinates)));
  if (!polys.length) throw new Error("no occupied polygons");
  const merged = polygonClipping.union(...polys.map((p) => [p]));
  return {
    type: "FeatureCollection",
    features: [{ type: "Feature", properties: { date: day, source: "DeepStateMap", version: id }, geometry: { type: "MultiPolygon", coordinates: merged } }],
    metadata: { date: day, source: `${API}/${id}/geojson` },
  };
}

/** A version's GeoJSON, from the cache or (politely) from the API. */
async function version(id) {
  const file = join(RAW_DIR, `${id}.json`);
  if (existsSync(file)) return { geo: JSON.parse(await readFile(file, "utf8")), cached: true };
  const geo = await getJson(`${API}/${id}/geojson`);
  await writeFile(file, JSON.stringify(geo));
  return { geo, cached: false };
}

/** Was this day's file written by the backfill (not cyterat)? */
async function isBackfilled(day) {
  try { return JSON.parse(await readFile(join(FRONTLINE_DIR, `${day}.geojson`), "utf8")).features?.[0]?.properties?.version != null; }
  catch { return false; }
}

const from = arg("--from", "2022-04-01"), to = arg("--to", "2024-07-07"), delay = Number(arg("--delay", 800));
const force = process.argv.includes("--force");
await mkdir(FRONTLINE_DIR, { recursive: true });
await mkdir(RAW_DIR, { recursive: true });
const versions = (await getJson(`${API}/public`))
  .map((v) => ({ id: v.id, created: v.createdAt }))
  .sort((a, b) => a.created.localeCompare(b.created));

// Last version per publication day -> file for the following day.
const byDay = new Map();
for (const v of versions) byDay.set(nextDay(v.created.slice(0, 10)), v);
const todo = [];
for (const [day, v] of byDay) {
  if (day < from || day > to) continue;
  const exists = existsSync(join(FRONTLINE_DIR, `${day}.geojson`));
  if (!exists || (force && (await isBackfilled(day)))) todo.push([day, v]); // never touch cyterat's files
}
console.log(`[backfill] ${versions.length} versions; ${todo.length} day(s) to fetch between ${from} and ${to}`);

let saved = 0, failed = 0;
for (const [i, [day, v]] of todo.entries()) {
  try {
    const { geo, cached } = await version(v.id);
    await writeFile(join(FRONTLINE_DIR, `${day}.geojson`), JSON.stringify(convert(geo, day, v.id)));
    saved++;
    if (!cached) await sleep(delay);
  } catch (e) {
    failed++;
    console.warn(`[backfill] ${day} (version ${v.id}) failed: ${e.message}`);
  }
  if ((i + 1) % 50 === 0) { console.log(`[backfill] ${i + 1}/${todo.length}`); await writeIndex(); }
}
const all = await writeIndex();
console.log(`[backfill] saved ${saved}, failed ${failed}; ${all.length} snapshots (${all[0]} … ${all.at(-1)})`);
