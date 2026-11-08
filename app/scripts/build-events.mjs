// Reads events/*.md (one file per event) and groups/*.md (one file per group, e.g. an offensive)
// and writes public/events.geojson + public/groups.json.
// Place names are geocoded via OpenStreetMap Nominatim and cached in events/.geocode-cache.json.
//
// Usage: node scripts/build-events.mjs   (also runs automatically under `npm run dev` / `npm run build`)
import { readdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { SIDES, TYPES, SIDE_ALIASES, GROUP_KINDS } from "../src/event-config.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const EVENTS_DIR = join(ROOT, "events");
export const GROUPS_DIR = join(ROOT, "groups");
const OUT_FILE = join(ROOT, "public", "events.geojson");
const GROUPS_OUT = join(ROOT, "public", "groups.json");
const CACHE_FILE = join(EVENTS_DIR, ".geocode-cache.json");

const NOMINATIM = "https://nominatim.openstreetmap.org/search";
const USER_AGENT = "htb-map-project/1.0 (local event map)";
const VIEWBOX = "22.0,52.5,40.3,44.0"; // prefer results in/around Ukraine (not exclusive)

// Field name (lower-cased, spaces/underscores -> '-') -> canonical field.
const FIELD_ALIASES = {
  "event-name": "name", name: "name", title: "name",
  time: "time", date: "time", datetime: "time",
  location: "location", place: "location",
  coordinates: "coordinates", coords: "coordinates", latlng: "coordinates",
  side: "side", actor: "side",
  type: "type", category: "type",
  source: "source", url: "source", link: "source",
  group: "group", operation: "group", campaign: "group",
  kind: "kind", start: "start", end: "end", area: "area",
  "trust-factor": "trust", trustfactor: "trust", trust: "trust", "trust-score": "trust", confidence: "trust",
};

/** Parse a simple `---` frontmatter block of `key: value` lines plus a markdown body. */
function parseMarkdown(text) {
  const m = text.match(/^﻿?---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)([\s\S]*)$/);
  if (!m) return null;
  const fields = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = line.match(/^\s*([^:#][^:]*?)\s*:\s*(.*?)\s*$/);
    if (!kv) continue;
    const key = FIELD_ALIASES[kv[1].toLowerCase().replace(/[\s_]+/g, "-")];
    if (!key) continue;
    // Strip trailing " # comment" (a '#' inside a URL has no space before it, so it's kept).
    fields[key] = kv[2].replace(/\s+#.*$/, "").replace(/^(["'])(.*)\1$/, "$2");
  }
  return { fields, body: m[2].trim() };
}

function normaliseSide(v) {
  const k = String(v ?? "").trim().toLowerCase();
  return SIDES[k] ? k : SIDE_ALIASES[k] ?? null;
}
function normaliseType(v) {
  const k = String(v ?? "").trim().toLowerCase().replace(/[\s_]+/g, "-");
  return TYPES[k] ? k : null;
}
export const slugify = (s) => String(s ?? "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const parseDay = (v) => (/^\d{4}-\d{2}-\d{2}/.test(v ?? "") && !isNaN(new Date(v)) ? String(v).slice(0, 10) : null);
const parseBool = (v) => (/^(yes|true|1|on)$/i.test(v ?? "") ? true : /^(no|false|0|off)$/i.test(v ?? "") ? false : null);

/** Read groups/*.md -> Map(id -> group). */
async function loadGroups(warn) {
  const groups = new Map();
  if (!existsSync(GROUPS_DIR)) return groups;
  for (const file of (await readdir(GROUPS_DIR)).filter((f) => f.toLowerCase().endsWith(".md") && !f.startsWith("_")).sort()) {
    const parsed = parseMarkdown(await readFile(join(GROUPS_DIR, file), "utf8"));
    if (!parsed) { warn(`groups/${file}`, "no '---' frontmatter block, skipped"); continue; }
    const { fields: f, body } = parsed;
    const id = slugify(basename(file, ".md"));
    const kind = GROUP_KINDS[slugify(f.kind)] ? slugify(f.kind) : "other";
    if (f.kind && kind === "other" && slugify(f.kind) !== "other")
      warn(`groups/${file}`, `unknown kind '${f.kind}' -> other (valid: ${Object.keys(GROUP_KINDS).join(", ")})`);
    const side = normaliseSide(f.side) ?? "unknown";
    if (f.start && !parseDay(f.start)) warn(`groups/${file}`, `bad start '${f.start}' (YYYY-MM-DD)`);
    if (f.end && !parseDay(f.end)) warn(`groups/${file}`, `bad end '${f.end}' (YYYY-MM-DD)`);
    groups.set(id, {
      id, name: f.name ?? id, kind, side,
      start: parseDay(f.start), end: parseDay(f.end),
      area: parseBool(f.area) ?? GROUP_KINDS[kind].area,
      description: body,
    });
  }
  return groups;
}

function parseCoords(v) {
  const m = String(v ?? "").match(/^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (!m) return null;
  const lat = Number(m[1]), lng = Number(m[2]);
  return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? [lng, lat] : null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let lastRequest = 0;

async function nominatim(place, extra = "") {
  // Nominatim usage policy: max 1 request/second, identify the client.
  const wait = lastRequest + 1100 - Date.now();
  if (wait > 0) await sleep(wait);
  lastRequest = Date.now();
  const url = `${NOMINATIM}?format=jsonv2&limit=1&accept-language=en&viewbox=${VIEWBOX}${extra}&q=${encodeURIComponent(place)}`;
  const res = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
  if (!res.ok) throw new Error(`Nominatim HTTP ${res.status}`);
  return (await res.json())[0];
}

async function geocode(place, cache, log) {
  const key = place.trim().toLowerCase();
  if (key in cache) return cache[key];
  // Prefer cities/towns/villages ("Dnipro" the city not the river, "Poltava" the city not the oblast);
  // fall back to any feature (oblasts, rivers, landmarks) if no settlement matches.
  const hit = (await nominatim(place, "&featureType=city")) ?? (await nominatim(place));
  cache[key] = hit ? { coords: [Number(hit.lon), Number(hit.lat)], display: hit.display_name } : null;
  if (hit) log.info(`[events] geocoded '${place}' -> ${hit.display_name}  (wrong? add 'coordinates:' or edit events/.geocode-cache.json)`);
  return cache[key];
}

let running = null;
/** Build events.geojson. Concurrent calls share one run. Returns { count, warnings }. */
export function buildEvents({ log = console } = {}) {
  running ??= doBuild(log).finally(() => (running = null));
  return running;
}

async function doBuild(log) {
  const files = existsSync(EVENTS_DIR)
    ? (await readdir(EVENTS_DIR)).filter((f) => f.toLowerCase().endsWith(".md") && !f.startsWith("_")).sort()
    : [];
  const cache = existsSync(CACHE_FILE) ? JSON.parse(await readFile(CACHE_FILE, "utf8")) : {};
  const cacheBefore = JSON.stringify(cache);
  const features = [];
  const warnings = [];
  const warn = (file, msg) => warnings.push(`${file}: ${msg}`);
  const groups = await loadGroups(warn);
  // Events may reference a group by file id or by its name.
  const groupByKey = new Map();
  for (const g of groups.values()) { groupByKey.set(g.id, g); groupByKey.set(slugify(g.name), g); }

  for (const file of files) {
    const parsed = parseMarkdown(await readFile(join(EVENTS_DIR, file), "utf8"));
    if (!parsed) { warn(file, "no '---' frontmatter block, skipped"); continue; }
    const { fields: f, body } = parsed;

    if (!f.name) warn(file, "missing 'event name'");
    const side = normaliseSide(f.side);
    if (!side) warn(file, `unknown side '${f.side ?? ""}' -> shown as unknown (valid: ${Object.keys(SIDES).join(", ")})`);
    const type = normaliseType(f.type);
    if (!type) warn(file, `unknown type '${f.type ?? ""}' -> shown as other (valid: ${Object.keys(TYPES).join(", ")})`);

    let trust = null;
    if (f.trust != null && f.trust !== "") {
      const n = Number(f.trust);
      if (Number.isFinite(n) && n >= 0 && n <= 1) trust = n;
      else warn(file, `trust-factor '${f.trust}' must be a number from 0 to 1 -> treated as unrated`);
    }

    const time = new Date(f.time);
    if (!f.time || isNaN(time)) { warn(file, `missing/invalid 'time' (${f.time ?? ""}), skipped`); continue; }

    let coords = parseCoords(f.coordinates) ?? parseCoords(f.location);
    if (f.coordinates && !parseCoords(f.coordinates)) warn(file, `bad 'coordinates' (${f.coordinates}); expected 'lat, lng'`);
    if (!coords && f.location) {
      try {
        const hit = await geocode(f.location, cache, log);
        if (hit) coords = hit.coords;
        else warn(file, `location '${f.location}' not found; add 'coordinates: lat, lng'`);
      } catch (e) {
        warn(file, `geocoding '${f.location}' failed (${e.message}); add 'coordinates: lat, lng'`);
      }
    }
    if (!coords) { warn(file, "no position (needs 'location' or 'coordinates'), skipped"); continue; }

    let group = null;
    if (f.group) {
      group = groupByKey.get(slugify(f.group));
      if (!group) {
        // Undefined group: create it on the fly so tagging still works, and say how to describe it.
        const id = slugify(f.group);
        group = { id, name: f.group, kind: "other", side: side ?? "unknown", start: null, end: null, area: false, description: "", auto: true };
        groups.set(id, group); groupByKey.set(id, group);
        warn(file, `group '${f.group}' has no groups/${id}.md; created a plain group (add that file to set kind/side/area)`);
      }
    }

    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: coords },
      properties: {
        id: basename(file, ".md"),
        name: f.name ?? basename(file, ".md"),
        time: time.toISOString(),
        location: f.location ?? "",
        side: side ?? "unknown",
        type: type ?? "other",
        trust,
        group: group?.id ?? null,
        source: f.source ?? "",
        description: body,
      },
    });
  }

  features.sort((a, b) => b.properties.time.localeCompare(a.properties.time));
  await writeFile(OUT_FILE, JSON.stringify({ type: "FeatureCollection", features }, null, 2) + "\n");
  // Groups: fill in first/last event day and count; drop the 'auto' marker.
  const out = [...groups.values()].map(({ auto, ...g }) => {
    const days = features.filter((e) => e.properties.group === g.id).map((e) => e.properties.time.slice(0, 10)).sort();
    return { ...g, count: days.length, firstDay: days[0] ?? null, lastDay: days.at(-1) ?? null };
  });
  await writeFile(GROUPS_OUT, JSON.stringify(out, null, 2) + "\n");
  if (JSON.stringify(cache) !== cacheBefore) await writeFile(CACHE_FILE, JSON.stringify(cache, null, 2) + "\n");

  for (const w of warnings) log.warn(`[events] ${w}`);
  log.info(`[events] ${features.length}/${files.length} events -> public/events.geojson; ${out.length} groups -> public/groups.json`);
  return { count: features.length, warnings };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  await buildEvents();
}
