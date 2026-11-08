// Downloads daily DeepStateMap occupied-territory snapshots from
// https://github.com/cyterat/deepstate-map-data (updated daily ~03:00 UTC, GPL-3.0)
// into public/data/frontline/YYYY-MM-DD.geojson, plus public/data/frontline/index.json
// listing the available dates. Days already downloaded are not fetched again.
//
// Usage:
//   node scripts/fetch-frontline.mjs                 last 180 days (fills any gaps)
//   node scripts/fetch-frontline.mjs --days 900      full archive since 8 Jul 2024
//   node scripts/fetch-frontline.mjs --days 90       last 90 days
//   node scripts/fetch-frontline.mjs 20260301        one specific day
// Also runs automatically under `npm run dev` / `npm run build` when the index is >12h old.
import { readdir, writeFile, stat, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const FRONTLINE_DIR = join(ROOT, "public", "data", "frontline");
const INDEX_FILE = join(FRONTLINE_DIR, "index.json");
const RAW = "https://raw.githubusercontent.com/cyterat/deepstate-map-data/main/data";
const DEFAULT_DAYS = 180; // days kept up to date on every run (older days already downloaded stay; `--days 900` fetches the full archive, which starts 8 Jul 2024)
const MAX_AGE_MS = 12 * 3600 * 1000;
const PARALLEL = 6;

const isoDay = (d) => d.toISOString().slice(0, 10);

/** Sanity-check a downloaded snapshot so a bad file never gets saved. */
function validate(geo) {
  if (geo?.type !== "FeatureCollection" || !Array.isArray(geo.features) || !geo.features.length)
    throw new Error("not a non-empty FeatureCollection");
  for (const f of geo.features) {
    if (!["Polygon", "MultiPolygon"].includes(f?.geometry?.type)) throw new Error(`unexpected geometry ${f?.geometry?.type}`);
    const [x, y] = JSON.stringify(f.geometry.coordinates).match(/-?\d+\.?\d*/g).slice(0, 2).map(Number);
    if (x < 20 || x > 45 || y < 40 || y > 55) throw new Error("coordinates outside Ukraine region");
  }
}

/** Download one day (YYYY-MM-DD). Returns true if saved, false if no snapshot exists for that day. */
async function fetchDay(day) {
  const url = `${RAW}/deepstatemap_data_${day.replace(/-/g, "")}.geojson`;
  const res = await fetch(url);
  if (res.status === 404) return false;
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  const geo = await res.json();
  validate(geo);
  delete geo.crs; // CRS84 = plain lon/lat, the GeoJSON default
  for (const f of geo.features) f.properties = { ...f.properties, date: day, source: "DeepStateMap" };
  geo.metadata = { date: day, source: url };
  await writeFile(join(FRONTLINE_DIR, `${day}.geojson`), JSON.stringify(geo));
  return true;
}

export async function writeIndex() {
  const dates = (await readdir(FRONTLINE_DIR))
    .filter((f) => /^\d{4}-\d{2}-\d{2}\.geojson$/.test(f))
    .map((f) => f.slice(0, 10))
    .sort();
  await writeFile(INDEX_FILE, JSON.stringify({ dates, updated: new Date().toISOString() }, null, 1));
  return dates;
}

/**
 * @param {{ days?: number, dates?: string[], force?: boolean, log?: {info: Function, warn: Function} }} opts
 *   days: how many days back from today to make sure are present; dates: explicit YYYY-MM-DD list.
 */
export async function fetchFrontline({ days = DEFAULT_DAYS, dates, force = false, log = console } = {}) {
  await mkdir(FRONTLINE_DIR, { recursive: true });
  if (!dates && !force && existsSync(INDEX_FILE) && Date.now() - (await stat(INDEX_FILE)).mtimeMs < MAX_AGE_MS)
    return { skipped: true };

  const wanted = dates ?? Array.from({ length: days }, (_, i) => isoDay(new Date(Date.now() - i * 86400000)));
  const todo = wanted.filter((d) => !existsSync(join(FRONTLINE_DIR, `${d}.geojson`)));
  let saved = 0, missing = 0;
  for (let i = 0; i < todo.length; i += PARALLEL) {
    const results = await Promise.all(todo.slice(i, i + PARALLEL).map(fetchDay));
    saved += results.filter(Boolean).length;
    missing += results.filter((r) => !r).length;
  }
  const all = await writeIndex();
  log.info(`[frontline] ${saved} new snapshot(s)${missing ? `, ${missing} day(s) not published yet/missing` : ""}; ` +
    `${all.length} available (${all[0] ?? "-"} … ${all.at(-1) ?? "-"})`);
  return { saved, missing, dates: all };
}

/** Like fetchFrontline but never throws: keeps existing snapshots on failure. */
export async function refreshFrontline(log = console) {
  try {
    return await fetchFrontline({ log });
  } catch (e) {
    log.warn(`[frontline] update failed (${e.message}); keeping existing snapshots`);
    if (existsSync(FRONTLINE_DIR)) await writeIndex().catch(() => {});
    return { error: e };
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const args = process.argv.slice(2);
  const day = args.find((a) => /^\d{8}$/.test(a));
  const daysArg = args.indexOf("--days");
  await fetchFrontline({
    force: true,
    dates: day ? [`${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)}`] : undefined,
    days: daysArg >= 0 ? Number(args[daysArg + 1]) : DEFAULT_DAYS,
  });
}
