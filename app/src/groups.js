// Event groups (offensives, campaigns…): outlines around a group's recent events + labels.
import { SIDES, GROUP_KINDS } from "./event-config.js";
import { firstSymbolLayerId } from "./overlays.js";
import { shiftDay } from "./datebar.js";

// An outline covers the group's events from the last N days up to the selected date
// (so it shows where the operation is active now, not everywhere it has ever been).
export const GROUP_AREA_WINDOW_DAYS = 14;
const PAD_KM = 7; // padding around each event so 1–2 events still make a visible area

export const kindLabel = (g) => (GROUP_KINDS[g?.kind] ?? GROUP_KINDS.other).label;
export const groupColor = (g) => (SIDES[g?.side] ?? SIDES.unknown).color;
/** Lighter tint of the side colour for outlines/labels, so a red outline still reads on the red occupied area. */
const tint = (hex, amt = 0.45) => "#" + [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * (1 - amt) + 255 * amt).toString(16).padStart(2, "0")).join("");

/** Convex hull (monotone chain) of [x, y] points. */
function hull(points) {
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const pt of p) { while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), pt) <= 0) lower.pop(); lower.push(pt); }
  for (const pt of p.reverse()) { while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), pt) <= 0) upper.pop(); upper.push(pt); }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

/** Padded hull polygon around lon/lat points (works in a local equirectangular projection). */
function paddedHull(coords) {
  const lat0 = coords.reduce((s, c) => s + c[1], 0) / coords.length;
  const kx = 111.32 * Math.cos((lat0 * Math.PI) / 180), ky = 110.57;
  const ring = [];
  for (const [lon, lat] of coords)
    for (let a = 0; a < 16; a++) {
      const t = (a / 16) * 2 * Math.PI;
      ring.push([lon * kx + PAD_KM * Math.cos(t), lat * ky + PAD_KM * Math.sin(t)]);
    }
  const h = hull(ring).map(([x, y]) => [x / kx, y / ky]);
  return [...h, h[0]];
}

/**
 * Outlines for groups with `area`, from `features` (already date/trust/group filtered by the caller,
 * but spanning the whole window). `day` is "YYYY-MM-DD" or null for all dates.
 */
export function groupAreaData(groups, features, day) {
  const from = day ? shiftDay(day, -(GROUP_AREA_WINDOW_DAYS - 1)) : null;
  const areas = [], labels = [];
  for (const g of groups) {
    if (!g.area) continue;
    if (day && ((g.start && day < g.start) || (g.end && day > g.end))) continue;
    const pts = features
      .filter((f) => f.properties.group === g.id && (!day || (f.properties.day >= from && f.properties.day <= day)))
      .map((f) => f.geometry.coordinates);
    if (!pts.length) continue;
    const ring = paddedHull(pts);
    const props = { id: g.id, name: g.name, kind: kindLabel(g), color: tint(groupColor(g)), n: pts.length };
    areas.push({ type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [ring] } });
    // Label at the top of the area so it doesn't sit on the markers.
    const top = ring.reduce((a, b) => (b[1] > a[1] ? b : a));
    const cx = ring.reduce((s, c) => s + c[0], 0) / ring.length;
    labels.push({ type: "Feature", properties: props, geometry: { type: "Point", coordinates: [cx, top[1]] } });
  }
  return {
    areas: { type: "FeatureCollection", features: areas },
    labels: { type: "FeatureCollection", features: labels },
  };
}

const EMPTY = { type: "FeatureCollection", features: [] };

/** Add sources/layers for group outlines (idempotent; call after each style load). */
export function installGroupLayers(map) {
  if (map.getSource("group-areas")) return;
  map.addSource("group-areas", { type: "geojson", data: EMPTY });
  map.addSource("group-labels", { type: "geojson", data: EMPTY });
  const before = firstSymbolLayerId(map);
  map.addLayer({
    id: "group-area-fill", type: "fill", source: "group-areas",
    paint: { "fill-color": ["get", "color"], "fill-opacity": 0.1 },
  }, before);
  map.addLayer({
    id: "group-area-line", type: "line", source: "group-areas",
    layout: { "line-join": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 2, "line-dasharray": [3, 2], "line-opacity": 0.9 },
  }, before);
  map.addLayer({
    id: "group-label", type: "symbol", source: "group-labels",
    layout: {
      "text-field": ["concat", ["upcase", ["get", "name"]], "\n", ["get", "kind"], " · ", ["to-string", ["get", "n"]], " events"],
      "text-font": ["Noto Sans Medium"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 9, 13],
      "text-anchor": "bottom",
      "text-offset": [0, -0.3],
      "text-letter-spacing": 0.05,
      "text-max-width": 14,
    },
    paint: { "text-color": ["get", "color"], "text-halo-color": "#000000", "text-halo-width": 1.5, "text-halo-blur": 0.5 },
  });
}

export function setGroupAreas(map, data) {
  map.getSource("group-areas")?.setData(data.areas);
  map.getSource("group-labels")?.setData(data.labels);
}
