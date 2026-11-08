import * as maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { layers, namedFlavor } from "@protomaps/basemaps";
import { SIDES, TYPES, TRUST_BANDS, TRUST_RING, trustBand, bandRange } from "./event-config.js";
import { iconSvg, symbolSvg, loadIconImages, RING_INNER } from "./icons.js";
import { OverlayManager, deepStateFront, CHANGE_WINDOWS } from "./overlays.js";
import { DateBar, ALL } from "./datebar.js";
import { TimeSlider } from "./timeslider.js";
import { h3Overlays } from "./h3grid.js";
import { seraphimLayers } from "./seraphim.js";
import { groupAreaData, installGroupLayers, setGroupAreas, kindLabel, groupColor, GROUP_AREA_WINDOW_DAYS } from "./groups.js";

const LANG = "en"; // "uk" for Ukrainian labels
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

let theme = "dark";

function buildStyle(flavor) {
  return {
    version: 8,
    glyphs: "/assets/fonts/{fontstack}/{range}.pbf",
    sprite: `${location.origin}/assets/sprites/v4/${flavor}`,
    sources: {
      protomaps: {
        type: "vector",
        url: `pmtiles://${location.origin}/data/ukraine.pmtiles`,
        attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="https://protomaps.com">Protomaps</a>',
      },
    },
    layers: layers("protomaps", namedFlavor(flavor), { lang: LANG }),
  };
}

const map = new maplibregl.Map({
  container: "map",
  style: buildStyle(theme),
  center: [31.2, 48.6],
  zoom: 5.5,
  maxBounds: [[18, 42], [44, 55]],
  hash: true,
});

// Debug overlay: open http://localhost:5173/?debug to print map errors on screen.
if (new URLSearchParams(location.search).has("debug")) {
  const box = document.createElement("pre");
  box.style.cssText = "position:fixed;left:50px;top:10px;z-index:9;max-width:800px;white-space:pre-wrap;color:#0f0;background:#000c;font-size:11px;padding:6px;margin:0";
  document.body.appendChild(box);
  const log = (m) => (box.textContent += m + "\n");
  window.map = map; // for console poking / automated tests
  log("webgl2: " + !!document.createElement("canvas").getContext("webgl2"));
  map.on("error", (e) => log("ERROR: " + (e.error?.stack || e.error?.message || e.error)));
  map.on("idle", () => log("idle; tiles loaded: " + map.areTilesLoaded() + "; layers: " + map.getStyle().layers.length));
  window.addEventListener("error", (e) => log("window: " + e.message));
  window.addEventListener("unhandledrejection", (e) => log("rejection: " + (e.reason?.stack || e.reason)));
}

map.addControl(new maplibregl.NavigationControl(), "top-left");
map.addControl(new maplibregl.ScaleControl(), "bottom-left");

// ---------------------------------------------------------------------------
// Overlays. Add new ones to this list (or later via overlays.add(...)).
// Trust bands are filter-only overlays: they have no layers of their own, they
// decide which events the "events" layer and the feed show.
// ---------------------------------------------------------------------------
const trustOverlayId = (b) => `trust-${b.id}`;
const OPS_PRESETS = { all: [], ungrouped: [] }; // filled in when groups.json loads
const overlays = new OverlayManager(
  map,
  [
    { id: "territory", label: "Territory" },
    { id: "analysis", label: "Analysis (H3)" },
    {
      id: "ops",
      label: "Operations & campaigns",
      presets: [
        { label: "All", on: OPS_PRESETS.all },
        { label: "None", on: [] },
        { label: "Ungrouped", on: OPS_PRESETS.ungrouped },
      ],
    },
    {
      id: "trust",
      label: "Events by trust-factor",
      presets: [
        { label: "All", on: TRUST_BANDS.map(trustOverlayId) },
        { label: "Trusted only", on: ["trust-trusted"] },
        { label: "Doubtful only", on: ["trust-uncertain", "trust-untrusted"] },
      ],
    },
  ],
  TRUST_BANDS.map((b) => ({
    id: trustOverlayId(b),
    group: "trust",
    label: b.label,
    detail: bandRange(b),
    swatch: b.color,
    defaultOn: true,
  })),
);
overlays.mount(document.getElementById("overlays"));

// ---------------------------------------------------------------------------
// Front line (DeepStateMap only): occupied territory on the selected day, plus what changed hands over
// the previous `windowDays` (Russian gains brighter red, Ukrainian recaptures blue).
// ---------------------------------------------------------------------------
let frontline = null; // deepStateFront controller, once the snapshot index has loaded
const frontControls = document.getElementById("front-controls");
const fmtKm2 = (x) => (x >= 100 ? Math.round(x).toLocaleString() : x.toFixed(1));
const WINDOW_KEY = "front-window-days";
const savedWindow = (() => { try { return Number(localStorage.getItem(WINDOW_KEY)); } catch { return 0; } })();

function renderFrontStats(st) {
  const sel = `<select id="front-window" aria-label="Recent changes window">${CHANGE_WINDOWS.map((n) =>
    `<option value="${n}" ${n === frontline.front.windowDays ? "selected" : ""}>${n} days</option>`).join("")}</select>`;
  let stats = "";
  if (st === undefined) stats = `<span class="fc-muted">computing…</span>`;
  else if (st === null) stats = "";
  else if (!st.before) stats = `<span class="fc-muted">no earlier snapshot to compare</span>`;
  else if (st.error) stats = `<span class="fc-muted">couldn't compute changes</span>`;
  else stats = `<span class="fc-gain">+${fmtKm2(st.gainKm2)} km² Russian</span> · <span class="fc-loss">${fmtKm2(st.lossKm2)} km² recaptured</span>
    <span class="fc-muted">(${fmtDay(st.before)} → ${fmtDay(st.day)})</span>`;
  frontControls.innerHTML = `<label>“Recent” = the past ${sel}</label><div class="fc-stats">${stats}</div>`;
}
frontControls.addEventListener("change", (e) => {
  if (e.target.id !== "front-window" || !frontline) return;
  const n = Number(e.target.value);
  try { localStorage.setItem(WINDOW_KEY, String(n)); } catch { /* private mode */ }
  renderFrontStats(undefined);
  frontline.front.setWindow(map, n);
});

const frontlineReady = fetch("/data/frontline/index.json", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : { dates: [] }))
  .then(({ dates = [] }) => {
    if (!dates.length) throw new Error("no snapshots");
    frontline = deepStateFront(dates, {
      windowDays: CHANGE_WINDOWS.includes(savedWindow) ? savedWindow : 7,
      onDetail: (d) => overlays.setDetail("front-held", d),
      onStats: (st) => {
        renderFrontStats(st);
        overlays.setDetail("front-gains", st?.before && !st.error ? `+${fmtKm2(st.gainKm2)} km²` : "");
        overlays.setDetail("front-losses", st?.before && !st.error ? `${fmtKm2(st.lossKm2)} km²` : "");
      },
    });
    for (const o of frontline.overlays) overlays.add(o);
    renderFrontStats(undefined);
  })
  .catch((e) => console.warn("Front line unavailable (run `npm run frontline`):", e));

/** Show the front line for a day. */
function showFront(day) {
  if (!frontline) return;
  frontline.front.showDate(map, day);
}

// ---------------------------------------------------------------------------
// Date selection ("YYYY-MM-DD" in UTC, or ALL). Kept in the URL as ?date=… so views can be shared.
// ---------------------------------------------------------------------------
let selectedDay = ALL;
let sera = null; // Seraphim layers (damage, Polymarket), created below
const dayOf = (iso) => iso.slice(0, 10); // event times are UTC ISO strings
const fmtDay = (day) => new Date(day + "T00:00:00Z").toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" });

function setDay(day) {
  const url = new URL(location.href);
  url.searchParams.set("date", day);
  history.replaceState(null, "", url);
  selectedDay = day;
  // Front line for the chosen day; with "All dates" show the newest snapshot.
  if (frontline) showFront(day === ALL ? frontline.front.dates.at(-1) : day);
  sera?.setDay(day);
  applyEventFilter();
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
let events = { type: "FeatureCollection", features: [] };
let groups = []; // from /groups.json
const groupById = new Map();
const iconsReady = loadIconImages();

const TRUST = ["coalesce", ["get", "trust"], -1]; // -1 = unrated

function bandExpr(b, t = TRUST) {
  if (b.unrated) return ["==", t, -1];
  const e = ["all", [">=", t, b.min]];
  if (b.max !== Infinity) e.push(["<", t, b.max]);
  return e;
}

const enabledBands = () => TRUST_BANDS.filter((b) => overlays.isOn(trustOverlayId(b)));

// Group filter overlays ("group-<id>", plus "group-none" for ungrouped events).
const NO_GROUP = "none";
const groupOverlayId = (id) => `group-${id ?? NO_GROUP}`;
// Until the groups have loaded (or for a group without an overlay) don't hide anything.
const groupOn = (id) => (overlays.get(groupOverlayId(id)) ? overlays.isOn(groupOverlayId(id)) : true);

// ---------------------------------------------------------------------------
// Filtering happens here (not as layer filters) because clusters are built from the
// source data: a cluster's count must only include events that pass every filter.
// ---------------------------------------------------------------------------
/** Events passing the trust-band and group filters (all dates). */
function filteredEvents() {
  const bands = new Set(enabledBands());
  return events.features.filter((f) => bands.has(trustBand(f.properties.trust)) && groupOn(f.properties.group));
}

function applyEventFilter() {
  const filtered = filteredEvents();
  const visible = selectedDay === ALL ? filtered : filtered.filter((f) => f.properties.day === selectedDay);
  map.getSource("events")?.setData({ type: "FeatureCollection", features: visible });
  setGroupAreas(map, groupAreaData(groups, filtered, selectedDay === ALL ? null : selectedDay));
  renderFeed(visible);
}

// Icon scale by zoom; the ring layer uses the same stops so it hugs the disc.
const ICON_SIZE_STOPS = [[4, 0.75], [10, 1.1]];
const zoomInterp = (f) => ["interpolate", ["linear"], ["zoom"], ...ICON_SIZE_STOPS.flatMap(([z, s]) => [z, f(s)])];

// Ring colour from the trust band, width grows with trust (see TRUST_RING in event-config.js).
// `t` is a trust expression (an event's trust, or a cluster's average trust; -1 = unrated).
const ringColor = (t) => ["case", ...TRUST_BANDS.filter((b) => !b.unrated).flatMap((b) => [bandExpr(b, t), b.color]),
  TRUST_BANDS.find((b) => b.unrated).color];
const ringWidth = (t) => ["case", ["==", t, -1], TRUST_RING.unratedWidth,
  ["+", TRUST_RING.minWidth, ["*", TRUST_RING.maxWidth - TRUST_RING.minWidth, t]]];

// Clusters: per-side counts + trust sum, so a cluster can be coloured by its dominant side
// and ringed by its average trust.
const IS_NUM = ["==", ["typeof", ["get", "trust"]], "number"];
const CLUSTER_PROPS = {
  ...Object.fromEntries(Object.keys(SIDES).map((s) => [`n_${s}`, ["+", ["case", ["==", ["get", "side"], s], 1, 0]]])),
  trustSum: ["+", ["case", IS_NUM, ["get", "trust"], 0]],
  rated: ["+", ["case", IS_NUM, 1, 0]],
};
const CLUSTER_TRUST = ["case", [">", ["get", "rated"], 0], ["/", ["get", "trustSum"], ["get", "rated"]], -1];
const maxSide = ["max", ...Object.keys(SIDES).map((s) => ["get", `n_${s}`])];
const dominantSide = (pick) => ["case", ...Object.entries(SIDES).flatMap(([k, s]) => [["==", ["get", `n_${k}`], maxSide], pick(s)]), pick(SIDES.unknown)];
const clusterRadius = ["step", ["get", "point_count"], 13, 5, 16, 15, 20, 40, 25];
const CLUSTER_MAX_ZOOM = 8; // from zoom 9 every event is shown individually

async function addEventLayers() {
  const images = await iconsReady;
  // setStyle() wipes images and custom layers, so (re)add them after every style load.
  for (const [key, img] of images) if (!map.hasImage(key)) map.addImage(key, img, { pixelRatio: 2 });
  installGroupLayers(map);
  if (map.getSource("events")) return;
  map.addSource("events", {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
    cluster: true,
    clusterRadius: 45,
    clusterMaxZoom: CLUSTER_MAX_ZOOM,
    clusterProperties: CLUSTER_PROPS,
  });
  const single = ["!", ["has", "point_count"]];
  const cluster = ["has", "point_count"];

  // Trust ring: an invisible circle the size of the icon disc whose stroke (drawn outside
  // circle-radius) forms the ring. Sits directly under the icon layer.
  map.addLayer({
    id: "events-ring", type: "circle", source: "events", filter: single,
    layout: { "circle-sort-key": ["-", 0, ["to-number", ["get", "ts"]]] },
    paint: {
      "circle-radius": zoomInterp((s) => RING_INNER * s), // icon is 32px at size 1
      "circle-opacity": 0,
      "circle-stroke-color": ringColor(TRUST),
      "circle-stroke-width": ringWidth(TRUST),
    },
  });
  map.addLayer({
    id: "events", type: "symbol", source: "events", filter: single,
    layout: {
      "icon-image": ["concat", "ev-", ["get", "side"], "-", ["get", "type"]],
      "icon-size": zoomInterp((s) => s),
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "symbol-sort-key": ["-", 0, ["to-number", ["get", "ts"]]], // newest on top
    },
    paint: {
      // Less trustworthy events are drawn fainter.
      "icon-opacity": ["case", ["==", TRUST, -1], 0.9, ["+", 0.35, ["*", 0.65, TRUST]]],
    },
  });

  // Clusters: disc in the dominant side's colour, white separator, average-trust ring, count.
  map.addLayer({
    id: "clusters-ring", type: "circle", source: "events", filter: cluster,
    paint: {
      "circle-radius": ["+", clusterRadius, 0.75],
      "circle-opacity": 0,
      "circle-stroke-color": ringColor(CLUSTER_TRUST),
      "circle-stroke-width": ringWidth(CLUSTER_TRUST),
    },
  });
  map.addLayer({
    id: "clusters", type: "circle", source: "events", filter: cluster,
    paint: {
      "circle-radius": clusterRadius,
      "circle-color": dominantSide((s) => s.color),
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.5,
    },
  });
  map.addLayer({
    id: "cluster-count", type: "symbol", source: "events", filter: cluster,
    layout: {
      "text-field": ["get", "point_count_abbreviated"],
      "text-font": ["Noto Sans Medium"],
      "text-size": ["step", ["get", "point_count"], 12, 15, 13, 40, 14],
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: { "text-color": dominantSide((s) => s.fg) },
  });
  applyEventFilter();
}

const fmtTime = (t) => new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const safeUrl = (u) => (/^https?:\/\//i.test(u ?? "") ? u : "");
const paragraphs = (text) =>
  String(text ?? "").split(/\n\s*\n/).filter(Boolean).map((p) => `<p>${esc(p.trim())}</p>`).join("");

function trustBadge(trust) {
  const b = trustBand(trust);
  const text = trust == null ? "unrated" : `trust ${Number(trust.toFixed(2))}`;
  return `<span class="trust" style="--c:${b.color}" title="${esc(b.label)} (${esc(bandRange(b))})">${esc(text)}</span>`;
}

/** Group chip; clicking it shows only that group (see the feed/popup click handler). */
function groupChip(id) {
  const g = groupById.get(id);
  if (!g) return "";
  return `<button type="button" class="group-chip" data-group="${esc(g.id)}" style="--c:${groupColor(g)}"
    title="${esc(kindLabel(g))} — click to show only this group">${esc(g.name)}</button>`;
}

function popupHtml(p) {
  const s = SIDES[p.side] ?? SIDES.unknown;
  const t = TYPES[p.type] ?? TYPES.other;
  const src = safeUrl(p.source) ? `<a href="${esc(p.source)}" target="_blank" rel="noopener">Source ↗</a>` : "";
  return `
    <div class="pop-head">${iconSvg(p.side, p.type, 28, p.trust ?? null)}<div>
      <div class="ev-time">${esc(fmtTime(p.time))}${p.location ? " · " + esc(p.location) : ""}</div>
      <strong>${esc(p.name)}</strong></div></div>
    <div class="pop-tags"><span style="background:${s.color};color:${s.fg}">${esc(s.label)}</span><span>${esc(t.label)}</span>${trustBadge(p.trust ?? null)}</div>
    ${p.group ? `<div class="pop-group">${groupChip(p.group)}</div>` : ""}
    <div class="pop-body">${paragraphs(p.description)}</div>${src}`;
}

function groupPopupHtml(id) {
  const g = groupById.get(id);
  if (!g) return "";
  const range = [g.start ?? g.firstDay, g.end ?? (g.lastDay ? `${g.lastDay}` : "")].filter(Boolean).map(fmtDay).join(" – ");
  return `
    <div class="ev-time">${esc(kindLabel(g))} · ${esc((SIDES[g.side] ?? SIDES.unknown).label)}</div>
    <strong>${esc(g.name)}</strong>
    <div class="pop-body">${paragraphs(g.description)}</div>
    <div class="ev-time">${g.count} events${range ? " · " + esc(range) : ""}</div>
    ${g.area ? `<div class="ev-time">Outline: events from the ${GROUP_AREA_WINDOW_DAYS} days up to the selected date.</div>` : ""}
    <div class="pop-group">${groupChip(g.id)}</div>`;
}

function popupAt(lngLat, html) {
  new maplibregl.Popup({ offset: 14, maxWidth: "300px" }).setLngLat(lngLat).setHTML(html).addTo(map);
}
const showPopup = (feature) => popupAt(feature.geometry.coordinates, popupHtml(feature.properties));

/** Show only one group (all other group overlays off, incl. "Ungrouped"). */
function soloGroup(id) {
  overlays.set(Object.fromEntries(overlays.overlays.filter((o) => o.group === "ops").map((o) => [o.id, o.id === groupOverlayId(id)])));
}
document.addEventListener("click", (e) => {
  const chip = e.target.closest?.(".group-chip");
  if (!chip) return;
  e.stopPropagation();
  soloGroup(chip.dataset.group);
}, true);

function renderFeed(visible) {
  const list = document.getElementById("feed-list");
  const count = document.getElementById("feed-count");
  const onDay = selectedDay === ALL ? events.features : events.features.filter((f) => f.properties.day === selectedDay);

  // Counts for the selected date next to each trust band and group toggle.
  for (const b of TRUST_BANDS) {
    const n = onDay.filter((f) => trustBand(f.properties.trust) === b).length;
    overlays.setDetail(trustOverlayId(b), `${bandRange(b)} · ${n}`);
  }
  for (const g of [...groups, { id: null }]) {
    const n = onDay.filter((f) => (f.properties.group ?? null) === g.id).length;
    overlays.setDetail(groupOverlayId(g.id), g.id ? `${kindLabel(g)} · ${n}` : `${n}`);
  }

  const when = selectedDay === ALL ? "across all dates" : `on ${fmtDay(selectedDay)}`;
  count.textContent = `Showing ${visible.length} of ${onDay.length} events ${when}`;
  list.innerHTML = "";
  if (!events.features.length) {
    list.innerHTML = `<li class="empty">No events loaded yet.</li>`;
    return;
  }
  if (!onDay.length) {
    list.innerHTML = `<li class="empty">No events recorded on this date.</li>`;
    return;
  }
  if (!visible.length) {
    list.innerHTML = `<li class="empty">No events match the selected filters.</li>`;
    return;
  }
  for (const f of [...visible].sort((a, b) => b.properties.ts - a.properties.ts)) {
    const p = f.properties;
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="ev-icon">${iconSvg(p.side, p.type, 26, p.trust)}</div>
      <div>
        <div class="ev-time">${esc(fmtTime(p.time))}${p.location ? " · " + esc(p.location) : ""}</div>
        <div class="ev-title">${esc(p.name)}</div>
        <div class="ev-tags">${trustBadge(p.trust)}${p.group ? groupChip(p.group) : ""}</div>
      </div>`;
    li.onclick = () => {
      // Zoom past the cluster threshold so the event is shown on its own.
      map.flyTo({ center: f.geometry.coordinates, zoom: Math.max(map.getZoom(), CLUSTER_MAX_ZOOM + 2) });
      showPopup(f);
    };
    list.appendChild(li);
  }
}

function renderLegend() {
  document.getElementById("legend-sides").innerHTML = Object.values(SIDES)
    .map((s) => `<li><span class="swatch" style="background:${s.color}"></span>${esc(s.label)}</li>`).join("");
  document.getElementById("legend-types").innerHTML = Object.entries(TYPES)
    .map(([k, t]) => `<li>${symbolSvg(k, 15)}${esc(t.label)} <code>${esc(k)}</code></li>`).join("");
  const samples = [[0.9, "0.9"], [0.55, "0.55"], [0.15, "0.15"], [null, "unrated"]];
  document.getElementById("legend-trust").innerHTML = samples
    .map(([v, txt]) => `<li>${iconSvg("unknown", "other", 18, v)}${esc(trustBand(v).label)} <code>${esc(txt)}</code></li>`).join("");
}

// One click handler with priority by what's on top: event > cluster > group label/area.
// ---------------------------------------------------------------------------
// H3 hexagons: a grid overlay, plus model capture probabilities once /data/h3/predictions.json exists.
// ---------------------------------------------------------------------------
sera = seraphimLayers(map, ALL);
const h3Controls = document.getElementById("h3-controls");
const h3 = h3Overlays({
  onDetail: (id, text) => overlays.setDetail(id, text),
  onControls: () => { h3Controls.innerHTML = h3.controlsHtml(); },
});
// Registered after the front line so the hexagons draw above the territory layers.
frontlineReady.finally(async () => {
  overlays.add(h3.grid);
  if (await h3.predReady) overlays.add(h3.pred);
  for (const o of sera.overlays) overlays.add(o);
  sera.setDay(selectedDay);
});
h3Controls.addEventListener("change", (e) => { if (h3.handleControl(e.target)) h3Controls.innerHTML = h3.controlsHtml(); });

const CLICKABLE = ["events", "clusters", "group-label", "group-area-fill", ...h3.clickLayers];
map.on("click", async (e) => {
  const layers = CLICKABLE.filter((id) => map.getLayer(id));
  const [hit] = map.queryRenderedFeatures(e.point, { layers });
  if (!hit) return;
  const layer = hit.layer.id;
  if (layer === "events") showPopup(hit);
  else if (layer === "clusters") {
    const zoom = await map.getSource("events").getClusterExpansionZoom(hit.properties.cluster_id);
    map.easeTo({ center: hit.geometry.coordinates, zoom });
  } else if (h3.clickLayers.includes(layer)) popupAt(e.lngLat, h3.popupHtml(hit));
  else popupAt(e.lngLat, groupPopupHtml(hit.properties.id));
});
map.on("mousemove", (e) => {
  const layers = ["events", "clusters", "group-label"].filter((id) => map.getLayer(id));
  map.getCanvas().style.cursor = map.queryRenderedFeatures(e.point, { layers }).length ? "pointer" : "";
});

overlays.onChange(applyEventFilter);
map.on("style.load", addEventLayers);

document.getElementById("theme-toggle").onclick = () => {
  theme = theme === "dark" ? "light" : "dark";
  // diff:false forces a full reload so style.load fires and overlays/events are re-added.
  map.setStyle(buildStyle(theme), { diff: false });
};

renderLegend();
const eventsReady = fetch("/events.geojson", { cache: "no-store" })
  .then((r) => r.json())
  .then((data) => {
    for (const f of data.features) {
      f.properties.ts = Date.parse(f.properties.time);
      f.properties.day = dayOf(f.properties.time);
    }
    events = data;
  });

// Groups -> one filter toggle each (area operations first), plus "Ungrouped".
const groupsReady = fetch("/groups.json", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : []))
  .then((list) => {
    groups = list.sort((a, b) => b.area - a.area || a.name.localeCompare(b.name));
    for (const g of groups) groupById.set(g.id, g);
    const ids = [...groups.map((g) => groupOverlayId(g.id)), groupOverlayId(null)];
    for (const g of groups)
      overlays.add({ id: groupOverlayId(g.id), group: "ops", label: g.name, detail: kindLabel(g), swatch: groupColor(g), defaultOn: true });
    overlays.add({ id: groupOverlayId(null), group: "ops", label: "Ungrouped events", swatch: "#666", defaultOn: true });
    OPS_PRESETS.all.push(...ids);
    OPS_PRESETS.ungrouped.push(groupOverlayId(null));
  })
  .catch((e) => console.warn("groups.json unavailable:", e));

// Once events, groups and front-line index are in, the selectable range spans events + snapshots.
Promise.allSettled([eventsReady, groupsReady, frontlineReady]).then(() => {
  const days = [...events.features.map((f) => f.properties.day), ...(frontline?.front.dates ?? [])].sort();
  const today = new Date().toISOString().slice(0, 10);
  const min = days[0] ?? today, max = days.at(-1) ?? today;
  const fromUrl = new URLSearchParams(location.search).get("date");
  const initial = fromUrl === ALL || /^\d{4}-\d{2}-\d{2}$/.test(fromUrl ?? "") ? fromUrl : max;
  const bar = new DateBar(document.getElementById("datebar"), { min, max, value: initial, onChange: setDay });
  setDay(bar.value);
  setupTimeSlider(bar);
});

// ---------------------------------------------------------------------------
// Time slider mode: animation of the front line over every snapshot (see src/timeslider.js). The date bar
// is hidden meanwhile; on exit it takes over the slider's date. ?mode=timeslider opens it directly.
// ---------------------------------------------------------------------------
function setupTimeSlider(bar) {
  const btn = document.getElementById("timeslider-toggle");
  if (!frontline) return;
  fetch("/data/frontline/changes.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then(({ days }) => {
      if (!days?.length) throw new Error("empty");
      const setMode = (on) => {
        const url = new URL(location.href);
        on ? url.searchParams.set("mode", "timeslider") : url.searchParams.delete("mode");
        history.replaceState(null, "", url);
        btn.classList.toggle("active", on);
      };
      const slider = new TimeSlider(document.getElementById("timeslider"), {
        days,
        map,
        onDay: setDay,
        prefetch: (d) => frontline.front.prefetch(d),
        onExit: (day) => { setMode(false); bar.set(day); },
      });
      btn.disabled = false;
      btn.onclick = () => {
        if (slider.isOpen) slider.close();
        else { slider.open(selectedDay); setMode(true); }
      };
      if (new URLSearchParams(location.search).get("mode") === "timeslider") { slider.open(selectedDay); setMode(true); }
    })
    .catch((e) => { btn.title = "Time slider unavailable: run `npm run frontline:changes`"; console.warn("changes.json unavailable:", e); });
}
